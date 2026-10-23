"""
Dansk supermarked tilbuds-checker
- eTilbudsavis.dk  → REMA 1000, MENY, Netto og SuperBrugsen
- tilbudsugen.dk   → 365discount, SuperBrugsen og Dagli'Brugsen
Læser produktliste fra produkter.json i samme mappe.
Udløses af cron-job.org via GitHub Actions (workflow_dispatch).
"""

import smtplib, os, re, json, time
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
EMAIL_SENDER   = os.environ.get("EMAIL_SENDER", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER", "")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "da-DK,da;q=0.9",
}

MÅNEDER = ["januar", "februar", "marts", "april", "maj", "juni", "juli",
           "august", "september", "oktober", "november", "december"]


def hent(url: str, kilde: str):
    """Henter en side. Prøver én gang til efter 5 sek. ved fejl.
    Returnerer teksten, eller None hvis begge forsøg fejler."""
    for forsøg in (1, 2):
        try:
            r = requests.get(url, headers=HEADERS, timeout=20)
            r.raise_for_status()
            print(f"  [{kilde}] HTTP {r.status_code} – {len(r.text)} tegn")
            return r.text
        except Exception as e:
            print(f"  [{kilde}] Fejl (forsøg {forsøg}): {e}")
            if forsøg == 1:
                time.sleep(5)
    return None


def indeholder_søgeord(tekst: str, søgeord: str) -> bool:
    """Alle ord i søgeordet skal findes i teksten (små bogstaver).
    Delstreng-match, så 'æg' også rammer 'frilandsæg'."""
    tekst = tekst.lower()
    return all(w.lower() in tekst for w in søgeord.split())


# ══════════════════════════════════════════════════════════════════════════════
# KILDE 1: eTilbudsavis.dk  →  REMA 1000, MENY, Netto, SuperBrugsen
# ══════════════════════════════════════════════════════════════════════════════

ETILBUD_BUTIKKER = ["REMA 1000", "MENY", "Netto", "SuperBrugsen"]

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

def søg_etilbudsavis(søgeord: str):
    """Returnerer (resultater, ok). ok=False hvis siden ikke kunne hentes."""
    url = f"https://etilbudsavis.dk/soeg/{requests.utils.quote(søgeord)}"
    html = hent(url, "eTilbudsavis")
    if html is None:
        return [], False
    resultater = udtræk_json_etilbud(html)
    print(f"  [eTilbudsavis] Fandt {len(resultater)} objekter")
    return resultater, True

def filtrer_etilbud(resultater: list, søgeord: str, produkt_navn: str) -> list:
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
        navn = item.get("name", "") or ""
        beskrivelse = item.get("description", "") or ""
        # eTilbudsavis' søgning er upræcis – tjek selv at søgeordet passer
        if not indeholder_søgeord(f"{navn} {beskrivelse}", søgeord):
            continue
        pris = item.get("price")
        fundne.append({
            "butik":       butik,
            "produkt":     produkt_navn,
            "tilbudsnavn": navn or produkt_navn,
            "pris":        f"{pris} kr." if pris else "Se avis",
            "beskrivelse": (beskrivelse[:80] + "…") if len(beskrivelse) > 80 else beskrivelse,
            "url":         f"https://etilbudsavis.dk/soeg/{requests.utils.quote(søgeord)}",
        })
    return fundne


# ══════════════════════════════════════════════════════════════════════════════
# KILDE 2: tilbudsugen.dk  →  365discount, SuperBrugsen og Dagli'Brugsen
# ══════════════════════════════════════════════════════════════════════════════

TILBUDSUGEN_BUTIKKER = {
    "coop365":      "365discount",
    "365discount":  "365discount",
    "superbrugsen": "SuperBrugsen",
    "daglibrugsen": "Dagli'Brugsen",
}

def søg_tilbudsugen(søgeord: str, produkt_navn: str):
    """Returnerer (fundne, ok, antal_kort). ok=False hvis siden ikke kunne hentes.
    antal_kort = antal tilbudskort på siden før filtrering (bruges til at
    opdage, hvis siden svarer uden data)."""
    url = f"https://www.tilbudsugen.dk/offer/{requests.utils.quote(søgeord)}"
    html = hent(url, "tilbudsugen")
    if html is None:
        return [], False, 0

    fundne = []
    soup = BeautifulSoup(html, "html.parser")
    kort = soup.find_all("a", href=re.compile(r"/single/\d+"))

    set_fundne_hrefs = set()
    for k in kort:
        href = k.get("href", "")
        if href in set_fundne_hrefs:
            continue
        if not k.find("img"):
            continue

        forælder       = k.parent
        forælder_tekst = forælder.get_text(" ", strip=True).lower()
        forælder_html  = str(forælder).lower()

        butik_navn = None
        for nøgle, navn in TILBUDSUGEN_BUTIKKER.items():
            if nøgle in forælder_html:
                butik_navn = navn
                break
        if not butik_navn:
            continue

        if not indeholder_søgeord(forælder_tekst, søgeord):
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
            "url":         href if href.startswith("http") else "https://www.tilbudsugen.dk" + href,
        })

    print(f"  [tilbudsugen]  {len(kort)} kort på siden, {len(fundne)} matchende tilbud")
    return fundne, True, len(kort)


# ══════════════════════════════════════════════════════════════════════════════
# FJERN DUBLETTER
# ══════════════════════════════════════════════════════════════════════════════

def fjern_dubletter(tilbud: list) -> list:
    """Samme butik + samme tilbudsnavn + samme pris vises kun én gang."""
    sete = set()
    unikke = []
    for t in tilbud:
        nøgle = (
            t["butik"].lower(),
            re.sub(r"\s+", " ", t["tilbudsnavn"].lower()).strip(),
            t["pris"],
        )
        if nøgle in sete:
            continue
        sete.add(nøgle)
        unikke.append(t)
    return unikke


# ══════════════════════════════════════════════════════════════════════════════
# EMAIL
# ══════════════════════════════════════════════════════════════════════════════

def send_email(tilbud: list, advarsler: list) -> None:
    nu = datetime.now()
    dato = f"{nu.day}. {MÅNEDER[nu.month - 1]} {nu.year}"
    subject = (
        f"🛒 {len(tilbud)} tilbud fundet – {dato}"
        if tilbud else
        f"🛒 Ingen tilbud fundet – {dato}"
    )
    if advarsler:
        subject += " (ufuldstændigt tjek)"

    advarsel_html = ""
    if advarsler:
        punkter = "".join(f"<li>{a}</li>" for a in advarsler)
        advarsel_html = f"""
          <div style="background:#fff4e5;border:1px solid #f0c36d;padding:12px 16px;margin:16px 0;border-radius:4px;">
            <b>⚠️ Tjekket er ufuldstændigt</b> – nogle opslag fejlede, så der kan mangle tilbud:
            <ul style="margin:8px 0 0 0;">{punkter}</ul>
          </div>"""

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
        indhold = f"""
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
          </table>"""
    else:
        ingen_rows = "".join(f"<li>{p['navn']}</li>" for p in PRODUCTS)
        indhold = f"""
          <p>Ingen af følgende produkter var på tilbud:</p>
          <ul>{ingen_rows}</ul>"""

    body = f"""
        <html><body style="font-family:Arial,sans-serif;max-width:800px;margin:auto;padding:20px;">
          <h2 style="color:#2d7a2d;">🛒 Tilbud på dine produkter</h2>
          <p style="color:#555;">Tjekket hos REMA 1000, MENY, Netto, 365discount, SuperBrugsen og Dagli'Brugsen:</p>
          {advarsel_html}
          {indhold}
          <p style="color:#aaa;font-size:0.8em;margin-top:24px;">Kører automatisk søndag, torsdag, fredag og lørdag ✓</p>
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
    fejl_etilbud, fejl_tilbudsugen = [], []
    tilbudsugen_kort_i_alt = 0
    tilbudsugen_ok = 0

    for produkt in PRODUCTS:
        søgeord, navn = produkt["søgeord"], produkt["navn"]
        print(f"Søger efter: {navn} (søgeord: '{søgeord}')...")

        resultater, ok = søg_etilbudsavis(søgeord)
        if not ok:
            fejl_etilbud.append(navn)
        fundne = filtrer_etilbud(resultater, søgeord, navn)
        print(f"  → {len(fundne)} tilbud fra REMA/MENY/Netto/SuperBrugsen")
        alle_tilbud.extend(fundne)

        fundne2, ok2, antal_kort = søg_tilbudsugen(søgeord, navn)
        if ok2:
            tilbudsugen_ok += 1
            tilbudsugen_kort_i_alt += antal_kort
        else:
            fejl_tilbudsugen.append(navn)
        print(f"  → {len(fundne2)} tilbud fra 365discount/SuperBrugsen/Dagli'Brugsen")
        alle_tilbud.extend(fundne2)
        print()

    advarsler = []
    if fejl_etilbud:
        advarsler.append("eTilbudsavis (REMA 1000, MENY, Netto) kunne ikke hentes for: "
                         + ", ".join(fejl_etilbud))
    if fejl_tilbudsugen:
        advarsler.append("tilbudsugen.dk (365discount, SuperBrugsen, Dagli'Brugsen) kunne ikke hentes for: "
                         + ", ".join(fejl_tilbudsugen))
    if tilbudsugen_ok > 0 and tilbudsugen_kort_i_alt == 0:
        advarsler.append("tilbudsugen.dk svarede, men viste ingen tilbud på nogen af søgningerne "
                         "– siden var muligvis ved at blive opdateret.")

    før = len(alle_tilbud)
    alle_tilbud = fjern_dubletter(alle_tilbud)
    print(f"Total: {len(alle_tilbud)} tilbud fundet ({før - len(alle_tilbud)} dubletter fjernet).")
    for a in advarsler:
        print(f"ADVARSEL: {a}")
    send_email(alle_tilbud, advarsler)
