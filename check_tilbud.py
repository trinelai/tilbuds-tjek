"""
Dansk supermarked tilbuds-checker
Bruger eTilbudsavis.dk som datakilde (Next.js __NEXT_DATA__).
Kører hver søndag via GitHub Actions og sender en mail
hvis dine ønskede produkter er på tilbud.
"""

import smtplib
import os
import json
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from bs4 import BeautifulSoup

# ─── Produkter vi leder efter ────────────────────────────────────────────────
PRODUCTS = [
    {"søgeord": "schulstad gilleleje",          "navn": "Schulstad Gilleleje Havn rugbrød"},
    {"søgeord": "spidskål",                      "navn": "Spidskål"},
    {"søgeord": "smør",                          "navn": "Smør"},
    {"søgeord": "kims peanuts",                  "navn": "Kims saltede peanuts 1 kg"},
    {"søgeord": "den grønne slagter rullepølse", "navn": "Den Grønne Slagter rullepølse"},
]

# ─── Butikker vi vil tjekke ───────────────────────────────────────────────────
BUTIKKER = ["REMA 1000", "MENY", "365discount", "Coop 365"]

# ─── Email-opsætning ─────────────────────────────────────────────────────────
EMAIL_SENDER   = os.environ["EMAIL_SENDER"]
EMAIL_PASSWORD = os.environ["EMAIL_PASSWORD"]
EMAIL_RECEIVER = os.environ["EMAIL_RECEIVER"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "da-DK,da;q=0.9",
}


# ─── Udtræk data fra Next.js __NEXT_DATA__ ───────────────────────────────────
def udtræk_next_data(html: str) -> dict:
    """Find og parse <script id="__NEXT_DATA__"> blokken."""
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", {"id": "__NEXT_DATA__"})
    if not tag:
        print("  __NEXT_DATA__ ikke fundet – prøver alternativ metode")
        return {}
    try:
        return json.loads(tag.string)
    except Exception as e:
        print(f"  JSON-fejl i __NEXT_DATA__: {e}")
        return {}


def find_tilbud_i_data(data: dict, dybde: int = 0) -> list:
    """Rekursivt søg efter lister af tilbuds-objekter i Next.js data-træet."""
    resultater = []
    if dybde > 10:
        return resultater

    if isinstance(data, list):
        # Tjek om det er en liste af tilbuds-objekter
        if data and isinstance(data[0], dict) and any(
            k in data[0] for k in ["validUntil", "price", "dealer", "business"]
        ):
            return data
        for item in data:
            resultater.extend(find_tilbud_i_data(item, dybde + 1))

    elif isinstance(data, dict):
        for v in data.values():
            resultater.extend(find_tilbud_i_data(v, dybde + 1))

    return resultater


# ─── Søg på eTilbudsavis ─────────────────────────────────────────────────────
def søg_etilbudsavis(søgeord: str) -> list[dict]:
    url = f"https://etilbudsavis.dk/soeg/{requests.utils.quote(søgeord)}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        print(f"  HTTP {r.status_code} – side er {len(r.text)} tegn lang")

        next_data = udtræk_next_data(r.text)
        if not next_data:
            return []

        # Debug: print øverste nøgler
        props = next_data.get("props", {}).get("pageProps", {})
        print(f"  pageProps nøgler: {list(props.keys())[:10]}")

        resultater = find_tilbud_i_data(props)
        print(f"  Fandt {len(resultater)} tilbuds-objekter")
        return resultater

    except Exception as e:
        print(f"  Fejl: {e}")
        return []


# ─── Filtrer kun relevante butikker og aktive tilbud ─────────────────────────
def filtrer_tilbud(resultater: list[dict], produkt_navn: str) -> list[dict]:
    nu = datetime.now(timezone.utc)
    fundne = []

    for item in resultater:
        # Butiksnavn kan ligge forskellige steder
        butik = (
            item.get("business", {}).get("name", "") or
            item.get("dealer", {}).get("name", "") or
            item.get("store", {}).get("name", "")
        )

        if not any(b.lower() in butik.lower() for b in BUTIKKER):
            continue

        # Tjek om tilbuddet er aktivt
        try:
            til_str = (item.get("validUntil") or item.get("run_till") or "").replace("+0000", "+00:00")
            fra_str = (item.get("validFrom") or item.get("run_from") or "").replace("+0000", "+00:00")
            if til_str and fra_str:
                til = datetime.fromisoformat(til_str)
                fra = datetime.fromisoformat(fra_str)
                if nu < fra or nu > til:
                    continue
        except Exception:
            pass

        pris = item.get("price") or item.get("pricing", {}).get("price")
        beskrivelse = item.get("description") or item.get("heading", "")

        fundne.append({
            "butik":       butik,
            "produkt":     produkt_navn,
            "tilbudsnavn": item.get("name") or item.get("heading", produkt_navn),
            "pris":        f"{pris} kr." if pris else "Se avis",
            "beskrivelse": (beskrivelse[:80] + "…") if beskrivelse and len(beskrivelse) > 80 else (beskrivelse or ""),
            "url":         f"https://etilbudsavis.dk/soeg/{requests.utils.quote(produkt_navn)}",
        })

    return fundne


# ─── Send email ───────────────────────────────────────────────────────────────
def send_email(tilbud: list[dict]) -> None:
    uge = datetime.now().strftime("%-d. %B %Y")
    subject = (
        f"🛒 {len(tilbud)} tilbud fundet – {uge}"
        if tilbud
        else f"🛒 Ingen tilbud denne uge – {uge}"
    )

    if tilbud:
        rows = ""
        for t in tilbud:
            rows += f"""
            <tr>
              <td style="padding:10px;border-bottom:1px solid #eee;"><b>{t['butik']}</b></td>
              <td style="padding:10px;border-bottom:1px solid #eee;">{t['produkt']}</td>
              <td style="padding:10px;border-bottom:1px solid #eee;">{t['tilbudsnavn']}</td>
              <td style="padding:10px;border-bottom:1px solid #eee;color:#2d7a2d;font-weight:bold;">{t['pris']}</td>
              <td style="padding:10px;border-bottom:1px solid #eee;font-size:0.85em;color:#666;">{t['beskrivelse']}</td>
              <td style="padding:10px;border-bottom:1px solid #eee;">
                <a href="{t['url']}" style="color:#2d7a2d;">Se tilbud →</a>
              </td>
            </tr>"""
        body = f"""
        <html><body style="font-family:Arial,sans-serif;max-width:800px;margin:auto;padding:20px;">
          <h2 style="color:#2d7a2d;">🛒 Ugentlige tilbud på dine produkter</h2>
          <p style="color:#555;">Her er denne uges tilbud fra REMA 1000, MENY og 365discount:</p>
          <table width="100%" cellspacing="0" style="border-collapse:collapse;margin-top:16px;">
            <tr style="background:#f5f5f5;">
              <th style="padding:10px;text-align:left;">Butik</th>
              <th style="padding:10px;text-align:left;">Produkt</th>
              <th style="padding:10px;text-align:left;">Tilbudsnavn</th>
              <th style="padding:10px;text-align:left;">Pris</th>
              <th style="padding:10px;text-align:left;">Info</th>
              <th style="padding:10px;text-align:left;">Link</th>
            </tr>
            {rows}
          </table>
          <p style="color:#aaa;font-size:0.8em;margin-top:24px;">Automatisk tjekket via eTilbudsavis.dk · Kører hver søndag ✓</p>
        </body></html>
        """
    else:
        ingen_rows = "".join(f"<li>{p['navn']}</li>" for p in PRODUCTS)
        body = f"""
        <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:20px;">
          <h2>🛒 Ingen tilbud denne uge</h2>
          <p>Ingen af følgende produkter var på tilbud i REMA 1000, MENY eller 365discount:</p>
          <ul>{ingen_rows}</ul>
          <p>Tjek selv på <a href="https://etilbudsavis.dk">etilbudsavis.dk</a>.</p>
          <p style="color:#aaa;font-size:0.8em;">Automatisk tjekket · Kører hver søndag ✓</p>
        </body></html>
        """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = EMAIL_SENDER
    msg["To"]      = EMAIL_RECEIVER
    msg.attach(MIMEText(body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECEIVER, msg.as_string())

    print(f"✓ Mail sendt: {subject}")


# ─── Kør ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Tjekker tilbud på eTilbudsavis.dk...")
    alle_tilbud = []

    for produkt in PRODUCTS:
        print(f"\nSøger efter: {produkt['navn']}...")
        resultater = søg_etilbudsavis(produkt["søgeord"])
        fundne = filtrer_tilbud(resultater, produkt["navn"])
        print(f"  → {len(fundne)} relevante tilbud i REMA/MENY/365discount")
        alle_tilbud.extend(fundne)

    print(f"\nTotal: {len(alle_tilbud)} tilbud fundet.")
    send_email(alle_tilbud)
