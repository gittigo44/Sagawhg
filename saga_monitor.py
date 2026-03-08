"""
SAGA Wohnungs-Monitor
=====================
Überwacht https://www.saga.hamburg/immobiliensuche?Kategorie=APARTMENT
alle 5 Minuten und sendet eine Telegram-Nachricht bei neuen Wohnungen.

Funktioniert mit Session-Management um den Anti-Bot-Check zu umgehen.

Token & Chat-ID als Umgebungsvariablen in Railway hinterlegen:
  TELEGRAM_TOKEN
  TELEGRAM_CHAT_ID
"""

import os
import time
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

URL            = "https://www.saga.hamburg/immobiliensuche?Kategorie=APARTMENT"
CHECK_INTERVAL = 300  # 5 Minuten
HEADERS        = {
    "User-Agent":                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 OPR/127.0.0.0",
    "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language":           "de,de-DE;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding":           "gzip, deflate, br",
    "Referer":                   "https://www.saga.hamburg/immobiliensuche?Kategorie=APARTMENT",
    "sec-ch-ua":                 '"Opera";v="127", "Chromium";v="143", "Not A(Brand";v="24"',
    "sec-ch-ua-mobile":          "?0",
    "sec-ch-ua-platform":        '"Windows"',
    "sec-fetch-dest":            "document",
    "sec-fetch-mode":            "navigate",
    "sec-fetch-site":            "same-origin",
    "upgrade-insecure-requests": "1",
    "cache-control":             "max-age=0",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def make_session() -> requests.Session:
    """
    Erstellt eine neue Session mit PHPSESSID.
    Besucht die Seite zuerst ohne Parameter um einen gültigen Cookie zu bekommen,
    wartet kurz (wie ein echter Browser) und ruft dann die Wohnungsseite ab.
    """
    session = requests.Session()
    session.headers.update(HEADERS)

    # Erster Besuch — Session/Cookie aufbauen
    try:
        session.get("https://www.saga.hamburg/", timeout=15)
        time.sleep(2)  # kurze Pause wie echter Nutzer
    except Exception:
        pass

    return session


def get_wohnungen(session: requests.Session) -> dict:
    """
    Lädt die Seite und gibt ein Dict zurück:
      { objekt_id: { titel, adresse, miete, verfuegbar, url } }
    """
    response = session.get(URL, timeout=15)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # Prüfen ob Sicherheitscheck noch aktiv
    if "Sicherheitsprüfung" in response.text and "0 Ergebnisse" in response.text:
        raise ValueError("Sicherheitspruefung aktiv – noch keine Daten")

    results = {}

    # Alle Links zu Wohnungs-Detailseiten finden
    for link in soup.find_all("a", href=True):
        href = link["href"]
        # SAGA Wohnungs-Links haben das Muster /immobiliensuche/.../details/...
        # oder enthalten eine eindeutige ID
        if "/immobiliensuche" not in href:
            continue
        if "details" not in href and "apartment" not in href.lower():
            continue

        # ID aus URL extrahieren (letztes Segment)
        objekt_id = href.rstrip("/").split("/")[-1]
        if not objekt_id or objekt_id in results:
            continue

        # Text aus dem umgebenden Block
        block = link.find_parent()
        raw_lines = []
        if block:
            raw_lines = [
                l.strip()
                for l in block.get_text("\n", strip=True).splitlines()
                if l.strip()
            ]

        titel      = raw_lines[0] if len(raw_lines) > 0 else "Wohnung"
        adresse    = next((l for l in raw_lines if any(x in l for x in ["Hamburg", "Str.", "straße", "weg", "Weg", "Ring", "Allee"])), "")
        miete      = next((l for l in raw_lines if "EUR" in l or "€" in l), "")
        verfuegbar = next((l for l in raw_lines if "frei" in l.lower() or "ab" in l.lower()), "")

        full_url = f"https://www.saga.hamburg{href}" if href.startswith("/") else href

        results[objekt_id] = {
            "titel":      titel,
            "adresse":    adresse,
            "miete":      miete,
            "verfuegbar": verfuegbar,
            "url":        full_url,
        }

    # Fallback: Anzahl Ergebnisse aus HTML lesen als Sanity-Check
    ergebnisse_text = soup.find(string=lambda t: t and "Ergebnisse" in t)
    if ergebnisse_text and "0 Ergebnisse" in ergebnisse_text:
        raise ValueError("Seite zeigt 0 Ergebnisse – moeglicherweise blockiert")

    if len(results) == 0:
        raise ValueError("Keine Wohnungen gefunden – HTML-Struktur evtl. geaendert oder blockiert")

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
    return (
        f"🏠 <b>Neue SAGA Wohnung!</b>\n\n"
        f"📍 <b>{info['titel']}</b>\n"
        f"🏘 {info['adresse']}\n"
        f"💶 {info['miete']}\n"
        f"📅 {info['verfuegbar']}\n\n"
        f"🔗 <a href='{info['url']}'>Zur Anzeige</a>"
    )


def main():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.error("FEHLER: Umgebungsvariablen TELEGRAM_TOKEN und TELEGRAM_CHAT_ID fehlen!")
        return

    log.info("SAGA Wohnungs-Monitor gestartet")
    send_telegram(
        "✅ <b>SAGA Monitor läuft!</b>\n"
        f"Ich prüfe alle {CHECK_INTERVAL // 60} Minuten auf neue Wohnungen."
    )

    # Session erstellen
    session = make_session()

    # Ersten Stand einlesen
    known = {}
    retries = 0
    while not known:
        try:
            known = get_wohnungen(session)
            log.info(f"Basis geladen: {len(known)} Wohnungen")
        except ValueError as e:
            retries += 1
            log.warning(f"Versuch {retries}: {e}")
            if retries % 5 == 0:
                # Nach 5 Fehlversuchen neue Session aufbauen
                log.info("Neue Session wird aufgebaut...")
                session = make_session()
            time.sleep(60)
        except Exception as e:
            log.error(f"Fehler: {e} – Retry in 60s")
            time.sleep(60)

    # Haupt-Loop
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
                # Nach 3 Fehlern neue Session aufbauen
                log.info("Zu viele Fehler – neue Session wird aufgebaut...")
                session = make_session()
                consecutive_errors = 0
            continue
        except Exception as e:
            log.warning(f"Abruf fehlgeschlagen: {e}")
            continue

        new_ids = set(current.keys()) - set(known.keys())

        if new_ids:
            log.info(f"NEU: {len(new_ids)} neue Wohnung(en) gefunden!")
            for oid in new_ids:
                msg = format_message(current[oid])
                send_telegram(msg)
                log.info(f"  Gemeldet: {oid} – {current[oid]['adresse']}")
        else:
            log.info(f"Keine Neuen. ({len(current)} Wohnungen, {datetime.now().strftime('%H:%M')})")

        known = current


if __name__ == "__main__":
    main()
