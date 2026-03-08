"""
SAGA Wohnungs-Monitor
=====================
Überwacht https://www.saga.hamburg/immobiliensuche?Kategorie=APARTMENT
alle 5 Minuten und sendet eine Telegram-Nachricht bei neuen Wohnungen.

Token & Chat-ID als Umgebungsvariablen in Railway hinterlegen:
  TELEGRAM_TOKEN
  TELEGRAM_CHAT_ID
"""

import os
import re
import time
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

URL            = "https://www.saga.hamburg/immobiliensuche?Kategorie=APARTMENT"
CHECK_INTERVAL = 300  # 5 Minuten

# Verschiedene User-Agent Strings zum Rotieren
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 OPR/127.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

ua_index = 0

def make_session() -> requests.Session:
    """Baut eine neue Session auf mit rotierendem User-Agent."""
    global ua_index
    session = requests.Session()
    ua = USER_AGENTS[ua_index % len(USER_AGENTS)]
    ua_index += 1

    session.headers.update({
        "User-Agent":                ua,
        "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language":           "de,de-DE;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding":           "gzip, deflate, br",
        "sec-ch-ua-mobile":          "?0",
        "sec-fetch-dest":            "document",
        "sec-fetch-mode":            "navigate",
        "sec-fetch-site":            "none",
        "upgrade-insecure-requests": "1",
        "cache-control":             "no-cache",
    })

    # Mehrere Seiten besuchen wie echter Nutzer
    pages = [
        "https://www.saga.hamburg/",
        "https://www.saga.hamburg/immobiliensuche",
        "https://www.saga.hamburg/immobiliensuche?Kategorie=APARTMENT",
    ]
    for i, page in enumerate(pages):
        try:
            r = session.get(page, timeout=15)
            log.info(f"Seite {i+1}/3: {r.status_code}, Cookies: {dict(session.cookies)}")
            time.sleep(2 + i)  # 2s, 3s, 4s
        except Exception as e:
            log.warning(f"Fehler bei {page}: {e}")

    return session


def parse_card(card_html: str, card_id: str) -> dict:
    """Extrahiert alle Details aus einem Wohnungs-Card HTML-Block."""
    soup = BeautifulSoup(card_html, "html.parser")

    h3 = soup.find("h3")
    titel = h3.get_text(strip=True) if h3 else ""

    link = soup.find("a", href=lambda h: h and "immo-detail" in h)
    href = link["href"] if link else ""
    objekt_id = href.split("/immo-detail/")[1].split("/")[0] if "/immo-detail/" in href else card_id

    adresse_tag = soup.find("p", class_=lambda c: c and "pb-3" in c)
    adresse = adresse_tag.get_text(strip=True) if adresse_tag else ""

    zimmer  = re.search(r'data-rooms="([^"]+)"', card_html)
    groesse = re.search(r'data-livingSpace="([^"]+)"', card_html)
    miete   = re.search(r'data-fullCosts="([^"]+)"', card_html)
    avail   = re.search(r'data-availableAt="(\d{4}-\d{2}-\d{2})', card_html)

    verfuegbar = ""
    if avail:
        try:
            d = datetime.strptime(avail.group(1), "%Y-%m-%d")
            verfuegbar = d.strftime("%d.%m.%Y")
        except Exception:
            verfuegbar = avail.group(1)

    return {
        "objekt_id":  objekt_id,
        "titel":      titel,
        "adresse":    adresse,
        "zimmer":     zimmer.group(1)  if zimmer  else "",
        "groesse":    groesse.group(1) if groesse else "",
        "miete":      miete.group(1)   if miete   else "",
        "verfuegbar": verfuegbar,
        "url":        f"https://www.saga.hamburg{href}",
    }


def get_wohnungen(session: requests.Session) -> dict:
    """Lädt die Seite und gibt alle Wohnungen als Dict zurück."""
    # Referer setzen (wie echter Browser der von der Übersichtsseite kommt)
    session.headers.update({
        "Referer":        "https://www.saga.hamburg/immobiliensuche",
        "sec-fetch-site": "same-origin",
    })

    response = session.get(URL, timeout=15)
    response.raise_for_status()
    html = response.text

    log.info(f"Seite geladen: {response.status_code}, {len(html)} Zeichen")

    if "Sicherheitsprüfung" in html and "0 Ergebnisse" in html:
        raise ValueError("Sicherheitspruefung aktiv")

    soup = BeautifulSoup(html, "html.parser")
    cards = [div for div in soup.find_all("div") if "immo-item" in div.get("class", [])]

    if len(cards) == 0:
        # Debug: zeige was die Seite zurückgibt
        snippet = html[html.find("<main"):html.find("<main")+500] if "<main" in html else html[:500]
        log.warning(f"Keine Cards. HTML-Snippet: {snippet[:300]}")
        raise ValueError("Keine Wohnungs-Cards gefunden")

    results = {}
    for card in cards:
        info = parse_card(str(card), card.get("id", ""))
        objekt_id = info.pop("objekt_id")
        results[objekt_id] = info

    return results


def send_telegram(message: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id":                  TELEGRAM_CHAT_ID,
        "text":                     message,
        "parse_mode":               "HTML",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        log.error(f"Telegram-Fehler: {e}")


def format_message(info: dict) -> str:
    zimmer  = f"🛏 {info['zimmer']} Zimmer\n"        if info.get("zimmer")     else ""
    groesse = f"📐 {info['groesse']} m²\n"            if info.get("groesse")    else ""
    miete   = f"💶 {info['miete']} € Gesamtmiete\n"  if info.get("miete")      else ""
    verfueg = f"📅 Verfügbar ab: {info['verfuegbar']}\n" if info.get("verfuegbar") else ""
    return (
        f"🏠 <b>Neue SAGA Wohnung!</b>\n\n"
        f"📍 <b>{info['titel']}</b>\n"
        f"🏘 {info['adresse']}\n"
        f"{zimmer}{groesse}{miete}{verfueg}\n"
        f"🔗 <a href='{info['url']}'>Zur Anzeige</a>"
    )


def main():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("FEHLER: Umgebungsvariablen fehlen!")
        return

    log.info("SAGA Wohnungs-Monitor gestartet")
    send_telegram(
        "✅ <b>SAGA Monitor läuft!</b>\n"
        f"Ich prüfe alle {CHECK_INTERVAL // 60} Minuten auf neue Wohnungen."
    )

    session = make_session()

    known = {}
    retries = 0
    while not known:
        try:
            known = get_wohnungen(session)
            log.info(f"Basis geladen: {len(known)} Wohnungen")
        except ValueError as e:
            retries += 1
            log.warning(f"Versuch {retries}: {e}")
            if retries % 3 == 0:
                log.info("Neue Session...")
                session = make_session()
            time.sleep(60)
        except Exception as e:
            log.error(f"Fehler: {e} – Retry in 60s")
            time.sleep(60)

    consecutive_errors = 0
    while True:
        time.sleep(CHECK_INTERVAL)
        try:
            current = get_wohnungen(session)
            consecutive_errors = 0
        except ValueError as e:
            consecutive_errors += 1
            log.warning(f"Abruf: {e}")
            if consecutive_errors >= 3:
                log.info("Neue Session...")
                session = make_session()
                consecutive_errors = 0
            continue
        except Exception as e:
            log.warning(f"Fehler: {e}")
            continue

        new_ids = set(current.keys()) - set(known.keys())
        if new_ids:
            log.info(f"NEU: {len(new_ids)} neue Wohnung(en)!")
            for oid in new_ids:
                send_telegram(format_message(current[oid]))
                log.info(f"  Gemeldet: {oid} – {current[oid]['adresse']}")
        else:
            log.info(f"Keine Neuen. ({len(current)} Wohnungen, {datetime.now().strftime('%H:%M')})")

        known = current


if __name__ == "__main__":
    main()
