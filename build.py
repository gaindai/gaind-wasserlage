#!/usr/bin/env python3
"""Wasserlage Raum Koblenz.

Holt täglich die amtliche Niedrigwasser-Einstufung der NIWIS-Messstellen im Umkreis
von Koblenz, legt einen Tagesschnappschuss ab und rendert eine statische Seite.

Quelle: NIWIS (Bundesanstalt für Gewässerkunde), Daten je Messstelle unter CC BY 4.0.
Aufruf:  python3 build.py            (abrufen und rendern)
         python3 build.py --nur-rendern
"""

import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

BASIS = "https://niwis-online.de"
KOBLENZ = (50.3569, 7.5886)
RADIUS_KM = 60

# Karten-Thema -> (Endpunkt der dokumentierten Daten-API, Anzeigename)
THEMEN = [
    ("ABFLUSS", "abfluss", "Abfluss"),
    ("GRUNDWASSER", "grundwasserstand", "Grundwasser"),
    ("QUELLSCHUETTUNG", "quellschuettung", "Quellschüttung"),
]

# NIWIS markiert fehlende Werte mit -777 und dem Flag BfGAdded.
FEHLWERT_FLAG = "BfGAdded"
FEHLWERT_ZAHL = -700.0

KLASSEN = {
    "KEIN_NIEDRIGWASSER": ("kein Niedrigwasser", "gruen"),
    "NIEDRIG": ("niedrig", "gelb"),
    "SEHR_NIEDRIG": ("sehr niedrig", "orange"),
    "EXTREM_NIEDRIG": ("extrem niedrig", "rot"),
    "KEINE_DATEN": ("keine Daten", "grau"),
}
KLASSEN_REIHE = ["EXTREM_NIEDRIG", "SEHR_NIEDRIG", "NIEDRIG", "KEIN_NIEDRIGWASSER", "KEINE_DATEN"]

TRENDS = {
    "FALLEND": ("fallend", "↓"),
    "STEIGEND": ("steigend", "↑"),
    "GLEICHBLEIBEND": ("gleichbleibend", "→"),
    "KEINE_DATEN": ("unbekannt", "·"),
}

WURZEL = Path(__file__).resolve().parent
DATEN = WURZEL / "data"
SEITE = WURZEL / "docs"


def hole(pfad, versuche=3):
    """GET auf die NIWIS-API. Gibt None zurueck, statt den Lauf abzubrechen."""
    for versuch in range(versuche):
        try:
            anfrage = urllib.request.Request(
                BASIS + pfad, headers={"User-Agent": "gaind-wasserlage/1.0 (+https://gaind.ai)"}
            )
            with urllib.request.urlopen(anfrage, timeout=30) as antwort:
                return json.load(antwort)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as fehler:
            if versuch == versuche - 1:
                print(f"  Abruf fehlgeschlagen: {pfad} ({fehler})", file=sys.stderr)
                return None
            time.sleep(2 * (versuch + 1))
    return None


def entfernung_km(a, b):
    """Grobe Entfernung in Kilometern, ausreichend für einen Umkreisfilter."""
    bogen = math.pi / 180
    x = (b[1] - a[1]) * bogen * math.cos((a[0] + b[0]) * bogen / 2)
    y = (b[0] - a[0]) * bogen
    return 6371 * math.sqrt(x * x + y * y)


def letzte_messung(nummer, endpunkt):
    """Juengster echter Messwert einer Station. Grundwasser darf bis 31 Tage alt sein."""
    heute = datetime.now(timezone.utc).date()
    frage = urllib.parse.urlencode(
        {
            "messstelleNr": nummer,
            "von": (heute - timedelta(days=40)).isoformat(),
            "bis": heute.isoformat(),
        }
    )
    reihe = hole(f"/api/daten/{endpunkt}?{frage}")
    if not reihe:
        return None, None, None
    for satz in reihe:  # API liefert absteigend, neuester zuerst
        wert = satz.get("messwert")
        if wert is None or satz.get("flag") == FEHLWERT_FLAG or wert <= FEHLWERT_ZAHL:
            continue
        return satz.get("datum"), wert, satz.get("einheit")
    return None, None, None


def schnappschuss():
    """Alle Messstellen im Umkreis mit Klasse, Trend, Messwert und echtem Messdatum."""
    stationen = []
    for thema, endpunkt, bezeichnung in THEMEN:
        karte = hole(f"/api/karte/messstelle/{thema}?klassifikationsart=DYNAMISCH")
        if karte is None:
            print(f"  Thema {thema} nicht abrufbar, wird übersprungen.", file=sys.stderr)
            continue
        for eintrag in karte:
            koordinate = eintrag.get("koordinate") or {}
            if "y" not in koordinate or "x" not in koordinate:
                continue
            weite = entfernung_km(KOBLENZ, (koordinate["y"], koordinate["x"]))
            if weite > RADIUS_KM:
                continue
            gemessen_am, wert, einheit = letzte_messung(eintrag["nummer"], endpunkt)
            stationen.append(
                {
                    "nummer": eintrag["nummer"],
                    "name": eintrag.get("anzeigeName") or eintrag["nummer"],
                    "messgroesse": bezeichnung,
                    "entfernung_km": round(weite),
                    "klasse": eintrag.get("niedrigwasserKlasse") or "KEINE_DATEN",
                    "trend": eintrag.get("entwicklung") or "KEINE_DATEN",
                    "messwert": wert,
                    "einheit": einheit,
                    "gemessen_am": gemessen_am,
                }
            )
        print(f"  {bezeichnung}: {sum(1 for s in stationen if s['messgroesse'] == bezeichnung)} Stationen im Umkreis")

    stationen.sort(key=lambda s: (KLASSEN_REIHE.index(s["klasse"]), s["entfernung_km"]))
    return {
        "abgerufen_am": datetime.now(timezone.utc).date().isoformat(),
        "umkreis_km": RADIUS_KM,
        "mittelpunkt": "Koblenz",
        "stationen": stationen,
        "verteilung": {k: sum(1 for s in stationen if s["klasse"] == k) for k in KLASSEN_REIHE},
    }


def speichern(stand):
    DATEN.mkdir(exist_ok=True)
    tagesdatei = DATEN / f"{stand['abgerufen_am']}.json"
    text = json.dumps(stand, ensure_ascii=False, indent=2) + "\n"
    tagesdatei.write_text(text, encoding="utf-8")
    (DATEN / "latest.json").write_text(text, encoding="utf-8")
    return tagesdatei


def historie(grenze=21):
    """Tagesverteilungen der letzten Läufe, jüngster zuerst."""
    eintraege = []
    for datei in sorted(DATEN.glob("20*.json"), reverse=True)[:grenze]:
        try:
            stand = json.loads(datei.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        eintraege.append({"datum": stand["abgerufen_am"], "verteilung": stand.get("verteilung", {})})
    return eintraege


def de_datum(iso):
    if not iso:
        return "kein Wert"
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return iso


def alter_hinweis(iso, bezug):
    """Kennzeichnet veraltete Messwerte, damit die Seite nicht aktueller wirkt als sie ist."""
    if not iso:
        return ' <span class="alt">ohne Messwert</span>'
    try:
        tage = (date.fromisoformat(bezug) - date.fromisoformat(iso)).days
    except ValueError:
        return ""
    if tage >= 7:
        return f' <span class="alt">{tage} Tage alt</span>'
    return ""


def zahl(wert):
    if wert is None:
        return "–"
    return f"{wert:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".").rstrip("0").rstrip(",")


def rendern(stand, verlauf):
    v = stand["verteilung"]
    gesamt = sum(v.values())
    auffaellig = v.get("EXTREM_NIEDRIG", 0) + v.get("SEHR_NIEDRIG", 0)

    balken = "".join(
        f'<div class="teil {KLASSEN[k][1]}" style="flex:{v.get(k,0)}" '
        f'title="{KLASSEN[k][0]}: {v.get(k,0)}"></div>'
        for k in KLASSEN_REIHE
        if v.get(k, 0)
    )

    legende = "".join(
        f'<span class="legende"><i class="punkt {KLASSEN[k][1]}"></i>{KLASSEN[k][0]} ({v.get(k,0)})</span>'
        for k in KLASSEN_REIHE
    )

    zeilen = []
    for s in stand["stationen"]:
        beschriftung, farbe = KLASSEN.get(s["klasse"], ("unbekannt", "grau"))
        trend_text, trend_zeichen = TRENDS.get(s["trend"], TRENDS["KEINE_DATEN"])
        wert = (
            f'{zahl(s["messwert"])} {s["einheit"]}' if s["messwert"] is not None else "–"
        )
        zeilen.append(
            f"""      <tr>
        <td class="ort"><strong>{s['name']}</strong><span class="meta">{s['messgroesse']} · {s['entfernung_km']} km</span></td>
        <td><span class="badge {farbe}">{beschriftung}</span></td>
        <td class="trend" title="{trend_text}">{trend_zeichen} {trend_text}</td>
        <td class="wert">{wert}</td>
        <td class="stand">{de_datum(s['gemessen_am'])}{alter_hinweis(s['gemessen_am'], stand['abgerufen_am'])}</td>
      </tr>"""
        )

    verlaufszeilen = "".join(
        f"""      <tr><td>{de_datum(e['datum'])}</td>"""
        + "".join(f'<td class="z">{e["verteilung"].get(k, 0)}</td>' for k in KLASSEN_REIHE)
        + "</tr>"
        for e in verlauf
    )
    verlaufskopf = "".join(f"<th>{KLASSEN[k][0]}</th>" for k in KLASSEN_REIHE)

    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wasserlage Raum Koblenz</title>
<meta name="description" content="Täglich aktualisierte Niedrigwasser-Lage im Umkreis von {RADIUS_KM} km um Koblenz, auf Basis der amtlichen NIWIS-Daten der Bundesanstalt für Gewässerkunde.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='16' cy='16' r='14' fill='%23b32d2d'/%3E%3C/svg%3E">
<style>
  :root {{
    --grund: #ffffff; --text: #16181c; --leise: #5c6470; --linie: #e3e6ea; --feld: #f6f7f9;
    --gruen: #2f7d4f; --gelb: #a8820f; --orange: #c1631b; --rot: #b32d2d; --grau: #8d949e;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --grund: #14161a; --text: #eceef1; --leise: #9aa2ad; --linie: #2a2e35; --feld: #1c1f25;
      --gruen: #4da372; --gelb: #cfa227; --orange: #e08036; --rot: #de5a5a; --grau: #79808a;
    }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--grund); color: var(--text);
    font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  }}
  .huelle {{ max-width: 900px; margin: 0 auto; padding: 32px 20px 64px; }}
  h1 {{ font-size: 1.7rem; line-height: 1.2; margin: 0 0 6px; letter-spacing: -0.01em; }}
  h2 {{ font-size: 1.05rem; margin: 40px 0 12px; }}
  .unterzeile {{ color: var(--leise); margin: 0 0 28px; }}
  .lage {{ background: var(--feld); border: 1px solid var(--linie); border-radius: 10px; padding: 18px 18px 14px; }}
  .lage p {{ margin: 0 0 14px; font-size: 1.05rem; }}
  .balken {{ display: flex; height: 12px; border-radius: 6px; overflow: hidden; gap: 2px; }}
  .teil {{ min-width: 3px; }}
  .legenden {{ display: flex; flex-wrap: wrap; gap: 8px 16px; margin-top: 12px; font-size: 0.84rem; color: var(--leise); }}
  .legende {{ display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; }}
  .punkt {{ width: 9px; height: 9px; border-radius: 50%; display: inline-block; }}
  .tabellenrahmen {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.92rem; }}
  th {{ text-align: left; font-size: 0.76rem; text-transform: uppercase; letter-spacing: 0.04em;
        color: var(--leise); font-weight: 600; padding: 0 10px 8px 0; white-space: nowrap; }}
  td {{ padding: 11px 10px 11px 0; border-top: 1px solid var(--linie); vertical-align: top; }}
  .ort {{ min-width: 190px; }}
  .ort strong {{ font-weight: 600; }}
  .meta {{ display: block; color: var(--leise); font-size: 0.8rem; white-space: nowrap; }}
  .badge {{ display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 0.78rem;
            font-weight: 600; color: #fff; white-space: nowrap; }}
  .trend, .wert, .stand {{ color: var(--leise); white-space: nowrap; }}
  .wert {{ font-variant-numeric: tabular-nums; }}
  .alt {{ color: var(--orange); font-size: 0.78rem; display: block; }}
  .z {{ text-align: right; font-variant-numeric: tabular-nums; padding-right: 14px; }}
  .gruen {{ background: var(--gruen); }} .gelb {{ background: var(--gelb); }}
  .orange {{ background: var(--orange); }} .rot {{ background: var(--rot); }}
  .grau {{ background: var(--grau); }}
  .hinweis {{ background: var(--feld); border-left: 3px solid var(--linie); padding: 12px 14px;
              border-radius: 0 8px 8px 0; color: var(--leise); font-size: 0.88rem; }}
  footer {{ margin-top: 44px; padding-top: 18px; border-top: 1px solid var(--linie);
            color: var(--leise); font-size: 0.84rem; }}
  a {{ color: inherit; }}
</style>
</head>
<body>
<div class="huelle">
  <h1>Wasserlage Raum Koblenz</h1>
  <p class="unterzeile">{gesamt} amtliche Messstellen im Umkreis von {RADIUS_KM} km &middot;
     Stand {de_datum(stand['abgerufen_am'])}</p>

  <div class="lage">
    <p><strong>{auffaellig} von {gesamt} Messstellen</strong> stehen auf &bdquo;sehr niedrig&ldquo; oder
       &bdquo;extrem niedrig&ldquo;.</p>
    <div class="balken">{balken}</div>
    <div class="legenden">{legende}</div>
  </div>

  <h2>Messstellen im Einzelnen</h2>
  <div class="tabellenrahmen">
    <table>
      <thead><tr><th>Messstelle</th><th>Einstufung</th><th>Entwicklung</th><th>Messwert</th><th>gemessen am</th></tr></thead>
      <tbody>
{chr(10).join(zeilen)}
      </tbody>
    </table>
  </div>

  <h2>Verlauf</h2>
  <p class="unterzeile">Anzahl Messstellen je Einstufung, ein Eintrag pro Abruftag.</p>
  <div class="tabellenrahmen">
    <table>
      <thead><tr><th>Tag</th>{verlaufskopf}</tr></thead>
      <tbody>{verlaufszeilen}</tbody>
    </table>
  </div>

  <h2>Was diese Seite ist, und was nicht</h2>
  <div class="hinweis">
    Diese Seite gibt die amtliche Einstufung der Messstellen wieder. Sie ist eine
    Orientierung, keine amtliche Warnung und keine Aussage über die Trinkwasserversorgung
    einer bestimmten Gemeinde. Dafür sind die Betriebsdaten des jeweiligen Wasserversorgers
    maßgeblich, die hier nicht enthalten sind. Grundwasserwerte können mehrere Wochen
    alt sein, das jeweilige Messdatum steht deshalb an jeder Zeile.
  </div>

  <footer>
    Datenquelle: NIWIS, Bundesanstalt für Gewässerkunde, Koblenz
    (<a href="https://niwis-online.de">niwis-online.de</a>). Messdaten der Länder und des Bundes,
    je Messstelle unter CC BY 4.0. Einstufung und Entwicklung stammen unverändert aus NIWIS.<br>
    Zusammenstellung: <a href="https://gaind.ai">gaind</a>. Automatischer Abruf, täglich.
  </footer>
</div>
</body>
</html>
"""


def main():
    nur_rendern = "--nur-rendern" in sys.argv
    if nur_rendern:
        stand = json.loads((DATEN / "latest.json").read_text(encoding="utf-8"))
    else:
        print("Abruf NIWIS ...")
        stand = schnappschuss()
        if not stand["stationen"]:
            print("Keine Messstellen abrufbar, Lauf wird abgebrochen.", file=sys.stderr)
            return 1
        ziel = speichern(stand)
        print(f"Tagesstand gespeichert: {ziel.name}")

    SEITE.mkdir(exist_ok=True)
    (SEITE / "index.html").write_text(rendern(stand, historie()), encoding="utf-8")
    (SEITE / ".nojekyll").write_text("", encoding="utf-8")
    print(f"Seite gerendert: {len(stand['stationen'])} Messstellen, Stand {stand['abgerufen_am']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
