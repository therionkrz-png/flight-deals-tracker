
import json
import os
import smtplib
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from flight_deals.providers.ryanair import RyanairProvider

ORIGIN = "KRK"
MAX_PRICE_EUR = 70.0
TOP_N = 10


def add_months(d, months):
    import calendar
    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def load_city_names():
    import csv
    import urllib.request

    with open("data/destinations.json", encoding="utf-8") as f:
        data = json.load(f)

    cities = {
        item["iata"].upper(): item["city"]
        for item in data.get("airports", [])
        if item.get("iata") and item.get("city")
    }

    # Uzupełnij brakujące lotniska danymi OurAirports.
    url = (
        "https://raw.githubusercontent.com/"
        "davidmegginson/ourairports-data/main/airports.csv"
    )
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "flight-deals-tracker"}
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            rows = csv.DictReader(
                response.read().decode("utf-8").splitlines()
            )
            for row in rows:
                iata = (row.get("iata_code") or "").strip().upper()
                city = (row.get("municipality") or "").strip()
                if iata and city and iata not in cities:
                    cities[iata] = city
    except Exception as exc:
        print(f"Nie udało się uzupełnić bazy miast: {exc}")

    return cities

def is_weekend_trip(out_date, return_date):
    nights = (return_date - out_date).days
    # Sobota–niedziela: 1 noc; piątek–niedziela: 2;
    # piątek–poniedziałek: 3.
    return (
        (out_date.weekday() == 5 and nights == 1 and return_date.weekday() == 6)
        or (out_date.weekday() == 4 and nights == 2 and return_date.weekday() == 6)
        or (out_date.weekday() == 4 and nights == 3 and return_date.weekday() == 0)
    )


def main():
    today = datetime.now(ZoneInfo("Europe/Warsaw")).date()
    end_date = add_months(today, 3)
    cities = load_city_names()
    provider = RyanairProvider(currency="EUR", use_cache=False)

    print(f"Wyszukiwanie: {ORIGIN}, {today} – {end_date}")
    print("Przewoźnik: Ryanair | Limit: 70 EUR/os.")
    print("Zakres: tygodniowe fragmenty, pobyty weekendowe 1–3 noce")

    found = {}
    failures = []

    chunk_start = today
    while chunk_start <= end_date:
        chunk_end = min(chunk_start + timedelta(days=6), end_date)
        print(f"Pobieram {chunk_start} – {chunk_end} ...")

        try:
            fares = provider.roundtrip_fares(
                ORIGIN,
                dest=None,
                out_from=chunk_start.isoformat(),
                out_to=chunk_end.isoformat(),
                duration_from=1,
                duration_to=3,
                use_cache=False,
            )

            for fare in fares:
                out_date = date.fromisoformat(fare.out_date)
                return_date = date.fromisoformat(fare.return_date)

                if not (today <= out_date <= end_date):
                    continue
                if return_date > end_date:
                    continue
                if not is_weekend_trip(out_date, return_date):
                    continue
                if fare.total_price_eur > MAX_PRICE_EUR:
                    continue

                key = (
                    fare.destination.upper(),
                    fare.out_date,
                    fare.return_date,
                )
                old = found.get(key)
                if old is None or fare.total_price_eur < old.total_price_eur:
                    found[key] = fare

        except Exception as exc:
            message = f"{chunk_start}–{chunk_end}: {type(exc).__name__}: {exc}"
            failures.append(message)
            print("BŁĄD:", message)

        chunk_start = chunk_end + timedelta(days=1)

    offers = sorted(found.values(), key=lambda x: x.total_price_eur)[:TOP_N]

    lines = [
        f"Weekendowe loty Ryanair z Krakowa ({ORIGIN})",
        f"Okres: {today} – {end_date}",
        f"Limit: {MAX_PRICE_EUR:.0f} EUR za osobę, lot w obie strony.",
        "",
    ]

    if offers:
        for i, fare in enumerate(offers, 1):
            destination = fare.destination.upper()
            city = cities.get(destination, f"Nieznane miasto ({destination})")
            out_date = date.fromisoformat(fare.out_date)
            return_date = date.fromisoformat(fare.return_date)
            nights = (return_date - out_date).days
            lines.append(
                f"{i}. {city} | "
                f"{out_date:%d.%m.%Y} – {return_date:%d.%m.%Y} | "
                f"{nights} {'noc' if nights == 1 else 'noce' if nights in (2, 3, 4) else 'nocy'} | "
                f"{fare.total_price_eur:.2f} EUR/os."
            )
    else:
        lines.append("Nie znaleziono ofert spełniających kryteria.")

    if failures:
        lines += [
            "",
            f"UWAGA: nie udało się pobrać danych z {len(failures)} fragmentów.",
            "Wyniki mogą być niepełne.",
            *failures,
        ]

    report = "\n".join(lines)
    print("\n" + report)

    # E-mail zostanie wysłany dopiero po ustawieniu zmiennych środowiskowych.
    email_to = os.getenv("EMAIL_TO")
    email_from = os.getenv("EMAIL_FROM") or os.getenv("SMTP_USER")
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")

    if not all([email_to, email_from, smtp_user, smtp_password]):
        print("\nTryb testowy: nie skonfigurowano e-maila, więc nic nie wysłano.")
        return

    msg = EmailMessage()
    msg["Subject"] = f"Loty weekendowe z KRK — {today:%d.%m.%Y}"
    msg["From"] = email_from
    msg["To"] = email_to
    msg.set_content(report)

    with smtplib.SMTP(os.getenv("SMTP_HOST", "smtp.gmail.com"),
                      int(os.getenv("SMTP_PORT", "587")), timeout=30) as smtp:
        smtp.starttls()
        smtp.login(smtp_user, smtp_password)
        smtp.send_message(msg)

    print(f"\nWiadomość wysłana do: {email_to}")


if __name__ == "__main__":
    main()
