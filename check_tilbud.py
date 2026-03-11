"""
Dansk supermarked tilbuds-checker
Bruger eTilbudsavis.dk som datakilde.
Kører hver søndag via GitHub Actions og sender en mail
hvis dine ønskede produkter er på tilbud.
"""

import smtplib
import os
import re
import json
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests

# ─── Produkter vi leder efter ────────────────────────────────────────────────
PRODUCTS = [
    {"søgeord": "schulstad gilleleje",          "navn": "Schulstad Gilleleje Havn rugbrød"},
    {"søgeord": "spidskål",                      "navn": "Spidskål"},
    {"søgeord": "smør",                          "navn": "Smør"},
    {"søgeord": "kims peanuts",                  "navn": "Kims saltede peanuts 1 kg"},
    {"søgeord": "den grønne slagter rullepølse", "navn": "Den Grønne Slagter rullepølse"},
    {"søgeord": "æg",                            "navn": "Æg"},
]

# ─── Butikker vi vil tjekke ───────────────────────────────────────────────────
BUTIKKER = ["REMA 1000", "MENY", "365discount", "Coop 365"]

# ─── Email-opsætning (sættes som GitHub Secrets) ─────────────────────────────
EMAIL_SENDER   = os.environ["EMAIL_SENDER"]
EMAIL_PASSWORD = os.environ["EMAIL_PASSWORD"]
EMAIL_RECEIVER = os.environ["EMAIL_RECEIVER"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "da-DK,da;q=0.9",
}


# ─── Udtræk JSON fra HTML-side ────────────────────────────────────────────────
def udtræk_json(html: str) -> list[dict]:
    """Find og parse {"data":[...]} blokken i HTML-siden."""
    # Find startposition af JSON
    start = html.find('{"data":[')
    if start == -1:
        return []

    # Tæl krøllede parenteser for at finde korrekt slutning
    depth = 0
    end = start
    for i, ch in enumerate(html[start:], start):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    raw = html[start:end]
    try:
        data = json.loads(raw)
        return data.get("data", [])
    except json.JSONDecodeError as e:
        print(f"  JSON-fejl: {e}")
        print(f"  Første 200 tegn af rådata: {raw[:200]}")
        return []


# ─── Søg på eTilbudsavis ─────────────────────────────────────────────────────
def søg_etilbudsavis(søgeord: str) -> list[dict]:
    url = f"https://etilbudsavis.dk/soeg/{requests.utils.quote(søgeord)}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()

        print(f"  HTTP {r.status_code} – side er {len(r.text)} tegn lang")

        resultater = udtræk_json(r.text)
        print(f"  Fandt {len(resultater)} resultater i JSON")
        return resultater

    except Exception as e:
        print(f"  Fejl ved søgning på '{søgeord}': {e}")
        return []


# ─── Filtrer kun relevante butikker og aktive tilbud ─────────────────────────
def filtrer_tilbud(resultater: list[dict], produkt_navn: str) -> list[dict]:
    nu = datetime.now(timezone.utc)
    fundne = []

    for item in resultater:
        butik = item.get("business", {}).get("name", "")

        if not any(b.lower() in butik.lower() for b in BUTIKKER):
            continue

        # Tjek om tilbuddet er aktivt
        try:
            til_str = item.get("validUntil", "").replace("+0000", "+00:00")
            fra_str = item.get("validFrom", "").replace("+0000", "+00:00")
            til = datetime.fromisoformat(til_str)
            fra = datetime.fromisoformat(fra_str)
            if nu < fra or nu > til:
                continue
        except Exception:
            pass  # Hvis datoer ikke kan parses, inkludér alligevel

        pris = item.get("price")
        beskrivelse = item.get("description", "")

        fundne.append({
            "butik":       butik,
            "produkt":     produkt_navn,
            "tilbudsnavn": item.get("name", produkt_navn),
            "pris":        f"{pris} kr." if pris else "Se avis",
            "beskrivelse": (beskrivelse[:80] + "…") if len(beskrivelse) > 80 else beskrivelse,
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
          <p style="color:#aaa;font-size:0.8em;margin-top:24px;">
            Automatisk tjekket via eTilbudsavis.dk · Kører hver søndag ✓
          </p>
        </body></html>
        """
    else:
        ingen_rows = "".join(f"<li>{p['navn']}</li>" for p in PRODUCTS)
        body = f"""
        <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:20px;">
          <h2>🛒 Ingen tilbud denne uge</h2>
          <p>Ingen af følgende produkter var på tilbud i REMA 1000, MENY eller 365discount:</p>
          <ul>{ingen_rows}</ul>
          <p>Tjek selv på <a href="https://etilbudsavis.dk">etilbudsavis.dk</a> for at være sikker.</p>
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
