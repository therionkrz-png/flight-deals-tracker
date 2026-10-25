
import csv
import html
import json
import os
import smtplib
import urllib.request
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from flight_deals.providers.ryanair import RyanairProvider

ORIGIN = "KRK"
MAX_PRICE_EUR = 70.0
TOP_N = 25

DNI_TYGODNIA = [
    "poniedziałek",
    "wtorek",
    "środa",
    "czwartek",
    "piątek",
    "sobota",
    "niedziela",
]


def add_months(d, months):
    import calendar

    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def load_city_names():
    with open("data/destinations.json", encoding="utf-8") as f:
        data = json.load(f)

    cities = {
        item["iata"].strip().upper(): item["city"]
        for item in data.get("airports", [])
        if item.get("iata") and item.get("city")
    }

    countries = {}

    # Pobierz nazwy miast i kody krajów dla lotnisk.
    url = (
        "https://raw.githubusercontent.com/"
        "davidmegginson/ourairports-data/main/airports.csv"
    )

    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "flight-deals-tracker"},
        )

        with urllib.request.urlopen(request, timeout=20) as response:
            rows = csv.DictReader(
                response.read().decode("utf-8").splitlines()
            )

            for row in rows:
                iata = (row.get("iata_code") or "").strip().upper()
                city = (row.get("municipality") or "").strip()
                country = (row.get("iso_country") or "").strip().upper()

                if not iata:
                    continue

                if city and iata not in cities:
                    cities[iata] = city

                if country:
                    countries[iata] = country

    except Exception as exc:
        print(f"Nie udało się pobrać danych OurAirports: {exc}")
        print("Lista Włoch i Hiszpanii może być niepełna.")

    return cities, countries


def is_weekend_trip(out_date, return_date):
    nights = (return_date - out_date).days

    # Sobota–niedziela: 1 noc.
    # Piątek–niedziela: 2 noce.
    return (
        (
            out_date.weekday() == 5
            and nights == 1
            and return_date.weekday() == 6
        )
        or (
            out_date.weekday() == 4
            and nights == 2
            and return_date.weekday() == 6
        )
    )


def data_z_dniem(d):
    return f"{DNI_TYGODNIA[d.weekday()]}, {d:%d.%m.%Y}"


def opis_pobytu(nights):
    if nights == 1:
        return "1 noc"
    return f"{nights} noce" if nights in (2, 3, 4) else f"{nights} nocy"


def fare_details(fare, cities):
    destination = fare.destination.upper()
    city = cities.get(destination, f"Nieznane miasto ({destination})")
    out_date = date.fromisoformat(fare.out_date)
    return_date = date.fromisoformat(fare.return_date)
    nights = (return_date - out_date).days

    out_dep = fare.outbound.departure_time or "brak godz."
    out_arr = fare.outbound.arrival_time or "brak godz."
    in_dep = fare.inbound.departure_time or "brak godz."
    in_arr = fare.inbound.arrival_time or "brak godz."

    return {
        "city": city,
        "price": fare.total_price_eur,
        "out_date": out_date,
        "return_date": return_date,
        "nights": nights,
        "out_dep": out_dep,
        "out_arr": out_arr,
        "in_dep": in_dep,
        "in_arr": in_arr,
        "out_origin": fare.outbound.origin,
        "out_destination": fare.outbound.destination,
        "out_flight": fare.outbound.flight_number or "brak danych",
        "in_origin": fare.inbound.origin,
        "in_destination": fare.inbound.destination,
        "in_flight": fare.inbound.flight_number or "brak danych",
    }


def text_offer(number, fare, cities):
    d = fare_details(fare, cities)

    return (
        f"{number}. {d['city']} | "
        f"{d['price']:.2f} EUR/os. w obie strony\n"
        f"   Wylot: {data_z_dniem(d['out_date'])}, "
        f"{d['out_dep']}–{d['out_arr']} | "
        f"{d['out_origin']} → {d['out_destination']} | "
        f"lot {d['out_flight']}\n"
        f"   Powrót: {data_z_dniem(d['return_date'])}, "
        f"{d['in_dep']}–{d['in_arr']} | "
        f"{d['in_origin']} → {d['in_destination']} | "
        f"lot {d['in_flight']}\n"
        f"   Pobyt: {opis_pobytu(d['nights'])}"
    )


def html_offer(number, fare, cities):
    d = fare_details(fare, cities)

    city = html.escape(d["city"])
    out_origin = html.escape(d["out_origin"])
    out_destination = html.escape(d["out_destination"])
    in_origin = html.escape(d["in_origin"])
    in_destination = html.escape(d["in_destination"])
    out_flight = html.escape(d["out_flight"])
    in_flight = html.escape(d["in_flight"])

    return (
        f'<p><strong>{number}. {city}</strong><br>'
        f'<span style="color:#d00000;font-weight:bold;">'
        f'{d["price"]:.2f} EUR/os. w obie strony'
        f'</span><br>'
        f'Wylot: <strong>{data_z_dniem(d["out_date"])}</strong><br>'
        f'<span style="color:#173a70;font-weight:bold;">'
        f'{html.escape(d["out_dep"])}–{html.escape(d["out_arr"])} | '
        f'{out_origin} → {out_destination} | lot {out_flight}'
        f'</span><br>'
        f'Powrót: <strong>{data_z_dniem(d["return_date"])}</strong><br>'
        f'<span style="color:#173a70;font-weight:bold;">'
        f'{html.escape(d["in_dep"])}–{html.escape(d["in_arr"])} | '
        f'{in_origin} → {in_destination} | lot {in_flight}'
        f'</span><br>'
        f'<span style="color:#555;">'
        f'Pobyt: {opis_pobytu(d["nights"])}'
        f'</span></p><hr>'
    )


def main():
    today = datetime.now(ZoneInfo("Europe/Warsaw")).date()
    end_date = add_months(today, 3)

    cities, countries = load_city_names()
    provider = RyanairProvider(currency="EUR", use_cache=False)

    print(f"Wyszukiwanie: {ORIGIN}, {today} – {end_date}")
    print(f"Przewoźnik: Ryanair | Limit: {MAX_PRICE_EUR:.0f} EUR/os.")
    print("Pobyty weekendowe: sobota–niedziela lub piątek–niedziela")

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
            message = (
                f"{chunk_start}–{chunk_end}: "
                f"{type(exc).__name__}: {exc}"
            )
            failures.append(message)
            print("BŁĄD:", message)

        chunk_start = chunk_end + timedelta(days=1)

    all_offers = sorted(
        found.values(),
        key=lambda fare: fare.total_price_eur,
    )

    italy_spain_offers = [
        fare
        for fare in all_offers
        if countries.get(fare.destination.upper()) in {"IT", "ES"}
    ]

    offers = all_offers[:TOP_N]

    # Raport tekstowy — używany także jako awaryjna wersja e-maila.
    lines = [
        f"Weekendowe loty Ryanair z Krakowa ({ORIGIN})",
        f"Okres: {today:%d.%m.%Y} – {end_date:%d.%m.%Y}",
        f"Limit: {MAX_PRICE_EUR:.0f} EUR/os. w obie strony.",
        "",
        "WSZYSTKIE LOTY DO WŁOCH I HISZPANII",
        "",
    ]

    if italy_spain_offers:
        for i, fare in enumerate(italy_spain_offers, 1):
            lines.append(text_offer(i, fare, cities))
            lines.append("")
    else:
        lines.append(
            "Nie znaleziono żadnych lotów do Włoch i Hiszpanii."
        )
        lines.append("")

    lines.extend([
        "=" * 45,
        "",
        f"{TOP_N} NAJTAŃSZYCH LOTÓW ZE WSZYSTKICH KIERUNKÓW",
        "",
    ])

    if offers:
        for i, fare in enumerate(offers, 1):
            lines.append(text_offer(i, fare, cities))
            lines.append("")
    else:
        lines.append("Nie znaleziono ofert spełniających kryteria.")
        lines.append("")

    if failures:
        lines.extend([
            "",
            f"UWAGA: nie udało się pobrać danych z {len(failures)} fragmentów.",
            "Wyniki mogą być niepełne.",
            *failures,
        ])

    report = "\n".join(lines)
    print("\n" + report)

    # Konfiguracja poczty — korzysta z istniejących sekretów GitHuba.
    email_to = os.getenv("EMAIL_TO")
    email_from = os.getenv("EMAIL_FROM") or os.getenv("SMTP_USER")
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")

    if not all([email_to, email_from, smtp_user, smtp_password]):
        print("\nTryb testowy: brak konfiguracji e-maila. Nic nie wysłano.")
        return

    html_lines = [
        '<html><body style="font-family:Arial,sans-serif;color:#222;">',
        f"<h2>Weekendowe loty Ryanair z Krakowa ({ORIGIN})</h2>",
        f"<p>Okres: {today:%d.%m.%Y} – {end_date:%d.%m.%Y}<br>",
        f"Limit: {MAX_PRICE_EUR:.0f} EUR/os. w obie strony.</p>",
        "<hr>",
        "<h2>Wszystkie loty do Włoch i Hiszpanii</h2>",
    ]

    if italy_spain_offers:
        for i, fare in enumerate(italy_spain_offers, 1):
            html_lines.append(html_offer(i, fare, cities))
    else:
        html_lines.append(
            "<p>Nie znaleziono żadnych lotów do Włoch i Hiszpanii.</p>"
        )

    html_lines.extend([
        "<h2>25 najtańszych lotów ze wszystkich kierunków</h2>",
    ])

    if offers:
        for i, fare in enumerate(offers, 1):
            html_lines.append(html_offer(i, fare, cities))
    else:
        html_lines.append(
            "<p>Nie znaleziono ofert spełniających kryteria.</p>"
        )

    if failures:
        html_lines.append(
            f'<p style="color:#a00;"><strong>Uwaga:</strong> '
            f'nie udało się pobrać danych z {len(failures)} fragmentów. '
            f'Wyniki mogą być niepełne.</p>'
        )
        html_lines.extend(
            f'<p>{html.escape(failure)}</p>' for failure in failures
        )

    html_lines.append("</body></html>")
    html_report = "\n".join(html_lines)

    msg = EmailMessage()
    msg["Subject"] = f"Loty weekendowe z KRK — {today:%d.%m.%Y}"
    msg["From"] = email_from
    msg["To"] = email_to
    msg.set_content(report)
    msg.add_alternative(html_report, subtype="html")

    with smtplib.SMTP(
        os.getenv("SMTP_HOST", "smtp.gmail.com"),
        int(os.getenv("SMTP_PORT", "587")),
        timeout=30,
    ) as smtp:
        smtp.starttls()
        smtp.login(smtp_user, smtp_password)
        smtp.send_message(msg)

    print(f"\nWiadomość wysłana do: {email_to}")


if __name__ == "__main__":
    main()
