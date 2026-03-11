"""
Dansk supermarked tilbuds-checker
- eTilbudsavis.dk  → REMA 1000, MENY og SuperBrugsen
- tilbudsugen.dk   → 365discount og SuperBrugsen
Læser produktliste fra produkter.json i samme mappe.
Kører hver søndag via GitHub Actions.
"""

import smtplib, os, re, json
from html import unescape
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from bs4 import BeautifulSoup

# ─── Læs produktliste fra produkter.json ─────────────────────────────────────
with open("produkter.json", "r", encoding="utf-8") as f:
    PRODUCTS = json.load(f)

print(f"Indlæste {len(PRODUCTS)} produkter fra produkter.json")

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
# KILDE 1: eTilbudsavis.dk  →  REMA 1000, MENY, SuperBrugsen
# ══════════════════════════════════════════════════════════════════════════════

ETILBUD_BUTIKKER = ["REMA 1000", "MENY", "SuperBrugsen"]

def udtræk_json_etilbud(html: str) -> list:
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

def søg_etilbudsavis(søgeord: str) -> list:
    url = f"https://etilbudsavis.dk/soeg/{requests.utils.quote(søgeord)}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        print(f"  [eTilbudsavis] HTTP {r.status_code} – {len(r.text)} tegn")
        resultater = udtræk_json_etilbud(r.text)
        print(f"  [eTilbudsavis] Fandt {len(resultater)} objekter")
        return resultater
    except Exception as e:
        print(f"  [eTilbudsavis] Fejl: {e}")
        return []

def filtrer_etilbud(resultater: list, produkt_navn: str) -> list:
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
# KILDE 2: tilbudsugen.dk  →  365discount og SuperBrugsen
# ══════════════════════════════════════════════════════════════════════════════

TILBUDSUGEN_BUTIKKER = {
    "coop365":      "365discount",
    "365discount":  "365discount",
    "superbrugsen": "SuperBrugsen",
}

def søg_tilbudsugen(søgeord: str, produkt_navn: str) -> list:
    url = f"https://www.tilbudsugen.dk/offer/{requests.utils.quote(søgeord)}"
    fundne = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        print(f"  [tilbudsugen]  HTTP {r.status_code} – {len(r.text)} tegn")

        soup = BeautifulSoup(r.text, "html.parser")
        kort = soup.find_all("a", href=re.compile(r"/single/\d+"))

        set_fundne_hrefs = set()
        antal = 0
        for k in kort:
            href = k.get("href", "")
            if href in set_fundne_hrefs:
                continue
            if not k.find("img"):
                continue

            forælder       = k.parent
            forælder_tekst = forælder.get_text(" ", strip=True).lower()
            forælder_html  = str(forælder).lower()

            # Find hvilken butik det er
            butik_navn = None
            for nøgle, navn in TILBUDSUGEN_BUTIKKER.items():
                if nøgle in forælder_html:
                    butik_navn = navn
                    break
            if not butik_navn:
                continue

            # Tjek at alle søgeord findes
            if not all(w.lower() in forælder_tekst for w in søgeord.split()):
                continue

            set_fundne_hrefs.add(href)

            linjer = [l.strip() for l in forælder.get_text("\n", strip=True).split("\n") if l.strip()]
            tilbudsnavn = linjer[0] if linjer else produkt_navn

            pris_match = re.search(r"(\d+(?:,\d+)?),?-", forælder_tekst)
            pris = pris_match.group(0).rstrip("-").rstrip(",") + " kr." if pris_match else "Se avis"

            dato_match = re.search(r"(\d{2}\.\d{2})\s*-\s*(\d{2}\.\d{2})", forælder.get_text())
            datoer = dato_match.group(0) if dato_match else ""

            fundne.append({
                "butik":       butik_navn,
                "produkt":     produkt_navn,
                "tilbudsnavn": tilbudsnavn,
                "pris":        pris,
                "beskrivelse": datoer,
                "url":         "https://www.tilbudsugen.dk" + href,
            })
            antal += 1

        print(f"  [tilbudsugen]  Fandt {antal} tilbud (365discount + SuperBrugsen)")
    except Exception as e:
        print(f"  [tilbudsugen]  Fejl: {e}")
    return fundne


# ══════════════════════════════════════════════════════════════════════════════
# EMAIL
# ══════════════════════════════════════════════════════════════════════════════

def send_email(tilbud: list) -> None:
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
          <p style="color:#555;">Tjekket hos REMA 1000, MENY, 365discount og SuperBrugsen:</p>
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
          <p>Ingen af følgende produkter var på tilbud:</p>
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
        resultater = søg_etilbudsavis(produkt["søgeord"])
        fundne = filtrer_etilbud(resultater, produkt["navn"])
        print(f"  → {len(fundne)} tilbud fra REMA/MENY/SuperBrugsen")
        alle_tilbud.extend(fundne)

        fundne2 = søg_tilbudsugen(produkt["søgeord"], produkt["navn"])
        print(f"  → {len(fundne2)} tilbud fra 365discount/SuperBrugsen")
        alle_tilbud.extend(fundne2)
        print()

    print(f"Total: {len(alle_tilbud)} tilbud fundet.")
    send_email(alle_tilbud)
