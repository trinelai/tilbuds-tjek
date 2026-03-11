"""
Dansk supermarked tilbuds-checker
- eTilbudsavis.dk  → REMA 1000 og MENY
- tilbudsugen.dk   → 365discount
Kører hver søndag via GitHub Actions.
"""

import smtplib
import os
import re
import json
from html import unescape
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


# ══════════════════════════════════════════════════════════════════════════════
# KILDE 1: eTilbudsavis.dk  →  REMA 1000 og MENY
# ══════════════════════════════════════════════════════════════════════════════

ETILBUD_BUTIKKER = ["REMA 1000", "MENY"]

def udtræk_json_etilbud(html: str) -> list[dict]:
    decoded = unescape(html)
    start = decoded.find('{"data":[{"publicId"')
    if start == -1:
        start = decoded.find('{"data":[{')
    if start == -1:
        return []
    depth, end = 0, start
    for i, ch in enumerate(decoded[start:], start):
        if ch == '{':   depth += 1
        elif ch == '}': depth -= 1
        if depth == 0:
            end = i + 1
            break
    try:
        return json.loads(decoded[start:end]).get("data", [])
    except Exception as e:
        print(f"  JSON-fejl: {e}")
        return []

def søg_etilbudsavis(søgeord: str) -> list[dict]:
    url = f"https://etilbudsavis.dk/soeg/{requests.utils.quote(søgeord)}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        print(f"  [eTilbudsavis] HTTP {r.status_code} – {len(r.text)} tegn")
        resultater = udtræk_json_etilbud(r.text)
        print(f"  [eTilbudsavis] Fandt {len(resultater)} objekter i JSON")
        return resultater
    except Exception as e:
        print(f"  [eTilbudsavis] Fejl: {e}")
        return []

def filtrer_etilbud(resultater: list[dict], produkt_navn: str) -> list[dict]:
    nu = datetime.now(timezone.utc)
    fundne = []
    for item in resultater:
        butik = item.get("business", {}).get("name", "")
        if not any(b.lower() in butik.lower() for b in ETILBUD_BUTIKKER):
            continue
        try:
            til = datetime.fromisoformat(item.get("validUntil", "").replace("+0000", "+00:00"))
            fra = datetime.fromisoformat(item.get("validFrom", "").replace("+0000", "+00:00"))
            if nu < fra or nu > til:
                continue
        except Exception:
            pass
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


# ══════════════════════════════════════════════════════════════════════════════
# KILDE 2: tilbudsugen.dk  →  365discount
# ══════════════════════════════════════════════════════════════════════════════

def søg_tilbudsugen_365(søgeord: str, produkt_navn: str) -> list[dict]:
    """Søg på tilbudsugen.dk og returnér kun 365discount-resultater."""
    url = f"https://www.tilbudsugen.dk/offer/{requests.utils.quote(søgeord)}"
    fundne = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        print(f"  [tilbudsugen]  HTTP {r.status_code} – {len(r.text)} tegn")

        soup = BeautifulSoup(r.text, "html.parser")

        # Hvert tilbud er et <a>-kort med billede, butikslogo og tekst
        kort = soup.find_all("a", href=re.compile(r"/single/\d+"))

        antal = 0
        for k in kort:
            tekst = k.get_text(" ", strip=True).lower()

            # Tjek at det er 365discount
            logo = k.find("img")
            logo_src = (logo.get("src", "") + logo.get("alt", "")).lower() if logo else ""
            if "365" not in logo_src and "coop365" not in logo_src and "coop 365" not in tekst:
                continue

            # Tjek at søgeordet findes i kortets tekst
            if not all(ord.lower() in tekst for ord in søgeord.split()):
                continue

            # Udtræk produktnavn (første linje af tekst, før datoer og pris)
            linjer = [l.strip() for l in k.get_text("\n", strip=True).split("\n") if l.strip()]
            tilbudsnavn = linjer[0] if linjer else produkt_navn

            # Find pris – leder efter mønster som "14,-" eller "29,95,-"
            pris_match = re.search(r"(\d+(?:,\d+)?),?-", tekst)
            pris = pris_match.group(0).rstrip("-").rstrip(",") + " kr." if pris_match else "Se avis"

            # Find datoer – mønster "05.03 - 11.03"
            dato_match = re.search(r"(\d{2}\.\d{2})\s*-\s*(\d{2}\.\d{2})", k.get_text())
            datoer = dato_match.group(0) if dato_match else ""

            fundne.append({
                "butik":       "365discount",
                "produkt":     produkt_navn,
                "tilbudsnavn": tilbudsnavn,
                "pris":        pris,
                "beskrivelse": datoer,
                "url":         "https://www.tilbudsugen.dk" + k.get("href", ""),
            })
            antal += 1

        print(f"  [tilbudsugen]  Fandt {antal} 365discount-tilbud")
    except Exception as e:
        print(f"  [tilbudsugen]  Fejl: {e}")

    return fundne


# ══════════════════════════════════════════════════════════════════════════════
# EMAIL
# ══════════════════════════════════════════════════════════════════════════════

def send_email(tilbud: list[dict]) -> None:
    uge = datetime.now().strftime("%-d. %B %Y")
    subject = (
        f"🛒 {len(tilbud)} tilbud fundet – {uge}"
        if tilbud else
        f"🛒 Ingen tilbud denne uge – {uge}"
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
          <p style="color:#555;">Tjekket hos REMA 1000, MENY og 365discount:</p>
          <table width="100%" cellspacing="0" style="border-collapse:collapse;margin-top:16px;">
            <tr style="background:#f5f5f5;">
              <th style="padding:10px;text-align:left;">Butik</th>
              <th style="padding:10px;text-align:left;">Produkt</th>
              <th style="padding:10px;text-align:left;">Tilbudsnavn</th>
              <th style="padding:10px;text-align:left;">Pris</th>
              <th style="padding:10px;text-align:left;">Periode</th>
              <th style="padding:10px;text-align:left;">Link</th>
            </tr>
            {rows}
          </table>
          <p style="color:#aaa;font-size:0.8em;margin-top:24px;">Kører hver søndag automatisk ✓</p>
        </body></html>
        """
    else:
        ingen_rows = "".join(f"<li>{p['navn']}</li>" for p in PRODUCTS)
        body = f"""
        <html><body style="font-family:Arial,sans-serif;max-width:600px;margin:auto;padding:20px;">
          <h2>🛒 Ingen tilbud denne uge</h2>
          <p>Ingen af følgende produkter var på tilbud i REMA 1000, MENY eller 365discount:</p>
          <ul>{ingen_rows}</ul>
          <p style="color:#aaa;font-size:0.8em;">Kører hver søndag automatisk ✓</p>
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


# ══════════════════════════════════════════════════════════════════════════════
# KØR
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("Tjekker tilbud...\n")
    alle_tilbud = []

    for produkt in PRODUCTS:
        print(f"Søger efter: {produkt['navn']}...")

        # REMA + MENY via eTilbudsavis
        resultater = søg_etilbudsavis(produkt["søgeord"])
        fundne = filtrer_etilbud(resultater, produkt["navn"])
        print(f"  → {len(fundne)} tilbud fra REMA/MENY")
        alle_tilbud.extend(fundne)

        # 365discount via tilbudsugen
        fundne_365 = søg_tilbudsugen_365(produkt["søgeord"], produkt["navn"])
        print(f"  → {len(fundne_365)} tilbud fra 365discount")
        alle_tilbud.extend(fundne_365)

        print()

    print(f"Total: {len(alle_tilbud)} tilbud fundet.")
    send_email(alle_tilbud)
