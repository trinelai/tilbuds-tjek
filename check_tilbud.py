"""
Dansk supermarked tilbuds-checker
Kører hver søndag via GitHub Actions og sender en mail
hvis dine ønskede produkter er på tilbud.
"""

import smtplib
import os
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from bs4 import BeautifulSoup

# ─── Produkter vi leder efter ────────────────────────────────────────────────
PRODUCTS = [
    "schulstad gilleleje",
    "spidskål",
    "smør",
    "kims peanuts",
    "den grønne slagter rullepølse",
]

# ─── Email-opsætning (sættes som GitHub Secrets) ─────────────────────────────
EMAIL_SENDER   = os.environ["EMAIL_SENDER"]    # din Gmail-adresse
EMAIL_PASSWORD = os.environ["EMAIL_PASSWORD"]  # Gmail app-adgangskode
EMAIL_RECEIVER = os.environ["EMAIL_RECEIVER"]  # modtager (kan være dig selv)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# ─── Hjælpefunktion: søg efter produkt i tekst ───────────────────────────────
def product_found(text: str, product: str) -> bool:
    """Returnerer True hvis alle ord i produktnavnet findes i teksten."""
    text_lower = text.lower()
    return all(word in text_lower for word in product.lower().split())


# ─── Scraper: REMA 1000 ───────────────────────────────────────────────────────
def check_rema() -> list[dict]:
    found = []
    try:
        r = requests.get("https://rema1000.dk/avis", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        page_text = soup.get_text(" ", strip=True)

        # Find produkt-blokke (tekst-baseret søgning)
        for product in PRODUCTS:
            if product_found(page_text, product):
                # Prøv at udtrække pris-kontekst
                pattern = re.compile(re.escape(product.split()[0]), re.IGNORECASE)
                match = pattern.search(page_text)
                context = page_text[max(0, match.start()-30):match.start()+80] if match else product
                found.append({
                    "butik": "REMA 1000",
                    "produkt": product,
                    "kontekst": context.strip(),
                    "url": "https://rema1000.dk/avis"
                })
    except Exception as e:
        print(f"REMA 1000 fejl: {e}")
    return found


# ─── Scraper: MENY ────────────────────────────────────────────────────────────
def check_meny() -> list[dict]:
    found = []
    try:
        r = requests.get("https://meny.dk/ugensavis", headers=HEADERS, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        page_text = soup.get_text(" ", strip=True)

        for product in PRODUCTS:
            if product_found(page_text, product):
                pattern = re.compile(re.escape(product.split()[0]), re.IGNORECASE)
                match = pattern.search(page_text)
                context = page_text[max(0, match.start()-30):match.start()+80] if match else product
                found.append({
                    "butik": "MENY",
                    "produkt": product,
                    "kontekst": context.strip(),
                    "url": "https://meny.dk/ugensavis"
                })
    except Exception as e:
        print(f"MENY fejl: {e}")
    return found


# ─── Scraper: 365discount (Coop) ─────────────────────────────────────────────
def check_365() -> list[dict]:
    found = []
    try:
        r = requests.get(
            "https://365discount.coop.dk/365avis/",
            headers=HEADERS,
            timeout=15
        )
        soup = BeautifulSoup(r.text, "html.parser")
        page_text = soup.get_text(" ", strip=True)

        for product in PRODUCTS:
            if product_found(page_text, product):
                pattern = re.compile(re.escape(product.split()[0]), re.IGNORECASE)
                match = pattern.search(page_text)
                context = page_text[max(0, match.start()-30):match.start()+80] if match else product
                found.append({
                    "butik": "365discount",
                    "produkt": product,
                    "kontekst": context.strip(),
                    "url": "https://365discount.coop.dk/365avis/"
                })
    except Exception as e:
        print(f"365discount fejl: {e}")
    return found


# ─── Send email ───────────────────────────────────────────────────────────────
def send_email(tilbud: list[dict]) -> None:
    subject = (
        f"🛒 {len(tilbud)} tilbud fundet denne uge!"
        if tilbud
        else "🛒 Ingen tilbud fundet denne uge"
    )

    # HTML-indhold
    if tilbud:
        rows = ""
        for t in tilbud:
            rows += f"""
            <tr>
              <td style="padding:8px;border-bottom:1px solid #eee;"><b>{t['butik']}</b></td>
              <td style="padding:8px;border-bottom:1px solid #eee;">{t['produkt'].title()}</td>
              <td style="padding:8px;border-bottom:1px solid #eee;font-size:0.9em;color:#555;">{t['kontekst']}</td>
              <td style="padding:8px;border-bottom:1px solid #eee;">
                <a href="{t['url']}">Se avis →</a>
              </td>
            </tr>"""
        body = f"""
        <html><body style="font-family:Arial,sans-serif;max-width:700px;margin:auto;">
          <h2 style="color:#2d7a2d;">🛒 Ugentlige tilbud på dine produkter</h2>
          <table width="100%" cellspacing="0" style="border-collapse:collapse;">
            <tr style="background:#f0f0f0;">
              <th style="padding:8px;text-align:left;">Butik</th>
              <th style="padding:8px;text-align:left;">Produkt</th>
              <th style="padding:8px;text-align:left;">Kontekst</th>
              <th style="padding:8px;text-align:left;">Link</th>
            </tr>
            {rows}
          </table>
          <p style="color:#888;font-size:0.85em;margin-top:24px;">
            Tjekket automatisk hver søndag ✓
          </p>
        </body></html>
        """
    else:
        body = """
        <html><body style="font-family:Arial,sans-serif;">
          <h2>🛒 Ingen tilbud denne uge</h2>
          <p>Ingen af dine produkter var på tilbud i REMA 1000, MENY eller 365discount denne uge.</p>
          <p style="color:#888;font-size:0.85em;">Tjekket automatisk hver søndag ✓</p>
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

    print(f"Mail sendt: {subject}")


# ─── Kør ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Tjekker tilbud...")
    alle_tilbud = check_rema() + check_meny() + check_365()
    print(f"Fandt {len(alle_tilbud)} tilbud.")
    send_email(alle_tilbud)
