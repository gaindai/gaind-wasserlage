#!/usr/bin/env python3
"""Wasserlage Raum Koblenz.

Holt täglich die amtliche Niedrigwasser-Einstufung der NIWIS-Messstellen rund um
Koblenz, legt einen Tagesschnappschuss ab und rendert eine statische Seite.

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
REIHE_TAGE = 40

THEMEN = [
    ("ABFLUSS", "abfluss", "Abfluss"),
    ("GRUNDWASSER", "grundwasserstand", "Grundwasser"),
    ("QUELLSCHUETTUNG", "quellschuettung", "Quellschüttung"),
]

# NIWIS markiert fehlende Werte mit -777 und dem Flag BfGAdded.
FEHLWERT_FLAG = "BfGAdded"
FEHLWERT_ZAHL = -700.0

# Gewässerkennzahl (LAWA), längster Treffer gewinnt. Ordnet jede Messstelle
# ihrem Flussgebiet zu, statt sie nur nach Luftlinie einzusammeln.
GKZ_RAEUME = [
    ("25874", "Lahn"), ("2589", "Lahn"), ("258", "Lahn"),
    ("2692", "Mosel"), ("2678", "Mosel"), ("2656", "Mosel"), ("26", "Mosel"),
    ("2718", "Ahr"), ("2716", "Wied"),
    ("2726", "Sieg"), ("272", "Sieg"),
    ("2572", "Mittelrhein"), ("256", "Mittelrhein"),
    ("254", "Nahe"), ("252", "Selz"),
    ("2", "Rhein"),
]
# Diese Flussgebiete münden im Raum Koblenz zusammen. Sieg (Bonn) und Selz
# (Rheinhessen) liegen im Umkreis, gehören hydrologisch aber nicht dazu.
KERNRAEUME = {"Rhein", "Mosel", "Lahn", "Ahr", "Wied", "Mittelrhein"}
GRUNDWASSER_KERN_KM = 45

KLASSEN = {
    "KEIN_NIEDRIGWASSER": ("kein Niedrigwasser", "gruen"),
    "NIEDRIG": ("niedrig", "gelb"),
    "SEHR_NIEDRIG": ("sehr niedrig", "orange"),
    "EXTREM_NIEDRIG": ("extrem niedrig", "rot"),
    "KEINE_DATEN": ("keine Daten", "grau"),
}
KLASSEN_REIHE = ["EXTREM_NIEDRIG", "SEHR_NIEDRIG", "NIEDRIG", "KEIN_NIEDRIGWASSER", "KEINE_DATEN"]

# Amtliche Definition laut NIWIS, "Grundlagen und Zusammenhänge".
KLASSEN_ERKLAERUNG = {
    "NIEDRIG": ("Q75", "Ein Wert, der normalerweise an einem Viertel aller Tage unterschritten wird. "
                       "Im Sommer nichts Ungewöhnliches."),
    "SEHR_NIEDRIG": ("Q95 bzw. Q90", "Ein Wert, der normalerweise nur an 5 Prozent der Tage unterschritten wird, "
                                     "also an rund 18 Tagen im Jahr."),
    "EXTREM_NIEDRIG": ("Q99", "Ein Wert, der normalerweise nur an 1 Prozent der Tage unterschritten wird, "
                              "also an drei bis vier Tagen im Jahr."),
    "KEIN_NIEDRIGWASSER": ("über Q75", "Im üblichen Bereich für diese Jahreszeit."),
    "KEINE_DATEN": ("", "Für diese Messstelle liegt derzeit keine Einstufung vor."),
}

TRENDS = {
    "FALLEND": ("fallend", "↓"),
    "STEIGEND": ("steigend", "↑"),
    "GLEICHBLEIBEND": ("gleichbleibend", "→"),
    "KEINE_DATEN": ("ohne Angabe", "·"),
}

WURZEL = Path(__file__).resolve().parent
DATEN = WURZEL / "data"
SEITE = WURZEL / "docs"


# ---------------------------------------------------------------- Abruf

def hole(pfad, versuche=3):
    """GET auf die NIWIS-API. Gibt None zurück, statt den Lauf abzubrechen."""
    for versuch in range(versuche):
        try:
            anfrage = urllib.request.Request(
                BASIS + pfad, headers={"User-Agent": "gaind-wasserlage/2.0 (+https://gaind.ai)"}
            )
            with urllib.request.urlopen(anfrage, timeout=30) as antwort:
                return json.load(antwort)
        except urllib.error.HTTPError as fehler:
            if fehler.code == 404:
                return None
            if versuch == versuche - 1:
                print(f"  Abruf fehlgeschlagen: {pfad} ({fehler})", file=sys.stderr)
                return None
            time.sleep(2 * (versuch + 1))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as fehler:
            if versuch == versuche - 1:
                print(f"  Abruf fehlgeschlagen: {pfad} ({fehler})", file=sys.stderr)
                return None
            time.sleep(2 * (versuch + 1))
    return None


def entfernung_km(a, b):
    bogen = math.pi / 180
    x = (b[1] - a[1]) * bogen * math.cos((a[0] + b[0]) * bogen / 2)
    y = (b[0] - a[0]) * bogen
    return 6371 * math.sqrt(x * x + y * y)


def raum(stammdaten, messgroesse, weite):
    """Flussgebiet aus der Gewässerkennzahl, Grundwasser über den hydrogeologischen Raum."""
    if messgroesse == "Grundwasser":
        name = (stammdaten or {}).get("teilraumName") or "Grundwasser"
        kern = weite <= GRUNDWASSER_KERN_KM and (stammdaten or {}).get("landcode") == "DERP"
        return name, kern
    gkz = str((stammdaten or {}).get("gkz") or "")
    for anfang, name in GKZ_RAEUME:
        if gkz.startswith(anfang):
            return name, name in KERNRAEUME
    return "ohne Zuordnung", False


def messreihe(nummer, endpunkt):
    """Zeitreihe der letzten Wochen, Fehlwerte aussortiert, älteste zuerst."""
    heute = datetime.now(timezone.utc).date()
    frage = urllib.parse.urlencode(
        {
            "messstelleNr": nummer,
            "von": (heute - timedelta(days=REIHE_TAGE)).isoformat(),
            "bis": heute.isoformat(),
        }
    )
    roh = hole(f"/api/daten/{endpunkt}?{frage}") or []
    sauber = [
        {"datum": s["datum"], "wert": s["messwert"], "einheit": s.get("einheit")}
        for s in roh
        if s.get("messwert") is not None
        and s.get("flag") != FEHLWERT_FLAG
        and s["messwert"] > FEHLWERT_ZAHL
    ]
    sauber.reverse()
    return sauber


def eigener_trend(werte):
    """Grobe Tendenz aus der eigenen Messreihe.

    NIWIS berechnet für Grundwasser keine Entwicklung, das Feld ist dort immer leer.
    Für einen Wasserversorger ist die Richtung aber die eigentliche Frage, deshalb
    diese bewusst einfache Näherung: Mittel der letzten drei Tage gegen das Mittel
    rund eine Woche davor, gemessen an der Schwankungsbreite des Zeitraums.
    """
    if len(werte) < 10:
        return None
    jung = sum(werte[-3:]) / 3
    alt = sum(werte[-10:-7]) / 3
    spanne = max(werte) - min(werte)
    if spanne == 0:
        return "GLEICHBLEIBEND"
    anteil = (jung - alt) / spanne
    if anteil <= -0.15:
        return "FALLEND"
    if anteil >= 0.15:
        return "STEIGEND"
    return "GLEICHBLEIBEND"


def schnappschuss():
    stationen = []
    for thema, endpunkt, bezeichnung in THEMEN:
        karte = hole(f"/api/karte/messstelle/{thema}?klassifikationsart=DYNAMISCH")
        if karte is None:
            print(f"  Thema {thema} nicht abrufbar, wird übersprungen.", file=sys.stderr)
            continue
        gefunden = 0
        for eintrag in karte:
            koordinate = eintrag.get("koordinate") or {}
            if "y" not in koordinate or "x" not in koordinate:
                continue
            weite = entfernung_km(KOBLENZ, (koordinate["y"], koordinate["x"]))
            if weite > RADIUS_KM:
                continue

            nummer = eintrag["nummer"]
            stamm = hole(f"/api/daten/stammdaten?messstelleNr={urllib.parse.quote(nummer)}") or {}
            reihe = messreihe(nummer, endpunkt)
            gebiet, kern = raum(stamm, bezeichnung, weite)
            letzter = reihe[-1] if reihe else None

            # NIWIS stellt Grundwasser in m ü. NHN dar. Für Laien ist der Abstand zur
            # Geländeoberkante verständlicher, deshalb selbst gerechnet und so gekennzeichnet.
            tiefe = None
            gok = stamm.get("hoeheGok")
            if bezeichnung == "Grundwasser" and gok and letzter:
                tiefe = round(gok - letzter["wert"], 2)

            stationen.append(
                {
                    "nummer": nummer,
                    "name": eintrag.get("anzeigeName") or nummer,
                    "messgroesse": bezeichnung,
                    "gewaesser": stamm.get("gewaesser"),
                    "raum": gebiet,
                    "kernraum": bool(kern),
                    "entfernung_km": round(weite),
                    "klasse": eintrag.get("niedrigwasserKlasse") or "KEINE_DATEN",
                    "trend": eintrag.get("entwicklung") or "KEINE_DATEN",
                    "trend_eigen": eigener_trend([r["wert"] for r in reihe])
                    if (eintrag.get("entwicklung") or "KEINE_DATEN") == "KEINE_DATEN" else None,
                    "messwert": letzter["wert"] if letzter else None,
                    "einheit": letzter["einheit"] if letzter else None,
                    "gemessen_am": letzter["datum"] if letzter else None,
                    "tiefe_unter_gelaende": tiefe,
                    "ezg_km2": stamm.get("ezgGroesse"),
                    "rekord_wert": stamm.get("nnq") if stamm.get("nnq") is not None else stamm.get("nnw"),
                    "rekord_art": "Abfluss" if stamm.get("nnq") is not None else ("Wasserstand" if stamm.get("nnw") is not None else None),
                    "rekord_datum": stamm.get("nnqDatum") or stamm.get("nnwDatum"),
                    "betreiber": stamm.get("institution") or stamm.get("betreiber"),
                    "url": stamm.get("urlMessstelle") or stamm.get("urlInstitution"),
                    "reihe": [r["wert"] for r in reihe][-30:],
                }
            )
            gefunden += 1
        print(f"  {bezeichnung}: {gefunden} Messstellen im Umkreis")

    stationen.sort(key=lambda s: (not s["kernraum"], KLASSEN_REIHE.index(s["klasse"]), s["entfernung_km"]))
    kern = [s for s in stationen if s["kernraum"]]
    return {
        "abgerufen_am": datetime.now(timezone.utc).date().isoformat(),
        "umkreis_km": RADIUS_KM,
        "mittelpunkt": "Koblenz",
        "stationen": stationen,
        "verteilung": {k: sum(1 for s in kern if s["klasse"] == k) for k in KLASSEN_REIHE},
        "verteilung_alle": {k: sum(1 for s in stationen if s["klasse"] == k) for k in KLASSEN_REIHE},
    }


def speichern(stand):
    DATEN.mkdir(exist_ok=True)
    tagesdatei = DATEN / f"{stand['abgerufen_am']}.json"
    text = json.dumps(stand, ensure_ascii=False, indent=2) + "\n"
    tagesdatei.write_text(text, encoding="utf-8")
    (DATEN / "latest.json").write_text(text, encoding="utf-8")
    return tagesdatei


def historie(grenze=21):
    eintraege = []
    for datei in sorted(DATEN.glob("20*.json"), reverse=True)[:grenze]:
        try:
            stand = json.loads(datei.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        eintraege.append({"datum": stand["abgerufen_am"], "verteilung": stand.get("verteilung", {})})
    return eintraege


# ---------------------------------------------------------------- Darstellung

def de_datum(iso, kurz=False):
    if not iso:
        return "ohne Datum"
    try:
        d = datetime.strptime(iso, "%Y-%m-%d")
    except ValueError:
        return iso
    return d.strftime("%d.%m.") if kurz else d.strftime("%d.%m.%Y")


def zahl(wert, einheit=None):
    """Deutsche Schreibweise mit sinnvoller Genauigkeit.

    Feste zwei Nachkommastellen wären falsch: 0,002 m3/s würde als 0 erscheinen und
    ein fast trockenes Gewässer von einem trockenen nicht mehr unterscheidbar machen.
    """
    if wert is None:
        return "–"
    if wert == 0:
        return "0"
    if einheit and "NHN" in einheit:
        stellen = 2
    else:
        betrag = abs(wert)
        stellen = 0 if betrag >= 100 else 1 if betrag >= 10 else 2 if betrag >= 1 else 3
    text = f"{wert:,.{stellen}f}"
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def sparkline(werte, breite=104, hoehe=26):
    if len(werte) < 3:
        return ""
    tief, hoch = min(werte), max(werte)
    spanne = (hoch - tief) or 1
    schritt = breite / (len(werte) - 1)
    punkte = " ".join(
        f"{i * schritt:.1f},{hoehe - 3 - (w - tief) / spanne * (hoehe - 6):.1f}"
        for i, w in enumerate(werte)
    )
    return (
        f'<svg class="spark" viewBox="0 0 {breite} {hoehe}" width="{breite}" height="{hoehe}" '
        f'aria-hidden="true" focusable="false"><polyline points="{punkte}"/></svg>'
    )


def vergleich_rekord(s):
    """Ordnet den heutigen Wert gegenüber dem niedrigsten je gemessenen ein."""
    rekord, wert = s.get("rekord_wert"), s.get("messwert")
    if rekord is None or wert is None or s["messgroesse"] != "Abfluss" or s.get("rekord_art") != "Abfluss":
        return ""
    datum = de_datum(s.get("rekord_datum"))
    if wert <= rekord:
        return (f'<p class="einordnung warnung">Der heutige Wert liegt unter dem niedrigsten Abfluss, '
                f'den die amtliche Zeitreihe für diese Messstelle führt ({zahl(rekord)} m³/s am {datum}).</p>')
    faktor = wert / rekord if rekord else 0
    if faktor < 2:
        return (f'<p class="einordnung">Niedrigster bisher erfasster Abfluss hier: {zahl(rekord)} m³/s '
                f'am {datum}. Der heutige Wert liegt nur knapp darüber.</p>')
    return (f'<p class="einordnung">Niedrigster bisher erfasster Abfluss hier: {zahl(rekord)} m³/s am {datum}.</p>')


def stationskarte(s, bezug):
    beschriftung, farbe = KLASSEN.get(s["klasse"], ("unbekannt", "grau"))
    trend_text, trend_zeichen = TRENDS.get(s["trend"], TRENDS["KEINE_DATEN"])
    if s["trend"] == "KEINE_DATEN" and s.get("trend_eigen"):
        trend_text, trend_zeichen = TRENDS[s["trend_eigen"]]
        trend_text += ' <span class="zusatz">eigene Rechnung</span>'
    gewaesser = s.get("gewaesser") or s["messgroesse"]

    alter = ""
    if s.get("gemessen_am"):
        try:
            tage = (date.fromisoformat(bezug) - date.fromisoformat(s["gemessen_am"])).days
            if tage >= 7:
                alter = f'<span class="alt">Messwert {tage} Tage alt</span>'
        except ValueError:
            pass

    wert = f'{zahl(s["messwert"], s.get("einheit"))} {s["einheit"]}' if s["messwert"] is not None else "kein Wert"
    if s.get("tiefe_unter_gelaende") is not None:
        wert += (f' <span class="zusatz" title="Eigene Rechnung: Geländeoberkante minus Wasserstand">'
                 f'rechnerisch {zahl(s["tiefe_unter_gelaende"])} m unter Gelände</span>')

    fakten = []
    if s.get("ezg_km2"):
        fakten.append(f"Einzugsgebiet {zahl(s['ezg_km2'])} km²")
    if s.get("betreiber"):
        fakten.append(f"Messstelle betrieben von {s['betreiber']}")
    fakten.append(f"Luftlinie {s['entfernung_km']} km bis Koblenz")
    if s.get("url"):
        fakten.append(f'<a href="{s["url"]}" rel="noopener">Amtliche Messstellenseite</a>')

    return f"""      <article class="karte" data-klasse="{s['klasse']}" data-groesse="{s['messgroesse']}" data-kern="{str(s['kernraum']).lower()}">
        <div class="streifen {farbe}"></div>
        <div class="kopf">
          <div>
            <h3>{s['name']}</h3>
            <p class="ort">{gewaesser} &middot; {s['raum']} &middot; {s['entfernung_km']} km</p>
          </div>
          <span class="badge {farbe}">{beschriftung}</span>
        </div>
        <div class="werte">
          <div class="wert">{wert}</div>
          {sparkline(s.get('reihe') or [])}
          <div class="trendfeld"><span class="pfeil">{trend_zeichen}</span> {trend_text}</div>
          <div class="stand">gemessen {de_datum(s.get('gemessen_am'))}{alter}</div>
        </div>
        <details>
          <summary>Was heißt das?</summary>
          <p>{KLASSEN_ERKLAERUNG.get(s['klasse'], ('', ''))[1]}</p>
          {vergleich_rekord(s)}
          <ul>{''.join(f'<li>{f}</li>' for f in fakten)}</ul>
        </details>
      </article>"""


def rendern(stand, verlauf):
    kern = [s for s in stand["stationen"] if s["kernraum"]]
    umfeld = [s for s in stand["stationen"] if not s["kernraum"]]
    v = stand["verteilung"]
    gesamt = len(kern)
    auffaellig = v.get("EXTREM_NIEDRIG", 0) + v.get("SEHR_NIEDRIG", 0)
    unter_rekord = sum(
        1 for s in kern
        if s.get("rekord_art") == "Abfluss" and s.get("rekord_wert") is not None
        and s.get("messwert") is not None and s["messwert"] <= s["rekord_wert"]
    )

    balken = "".join(
        f'<div class="teil {KLASSEN[k][1]}" style="flex:{v.get(k,0)}" title="{KLASSEN[k][0]}: {v.get(k,0)}"></div>'
        for k in KLASSEN_REIHE if v.get(k, 0)
    )
    legende = "".join(
        f'<button class="legende" data-filter="{k}"><i class="punkt {KLASSEN[k][1]}"></i>'
        f'{KLASSEN[k][0]} <b>{v.get(k,0)}</b></button>'
        for k in KLASSEN_REIHE
    )
    erklaerzeilen = "".join(
        f'<tr><td><span class="badge {KLASSEN[k][1]}">{KLASSEN[k][0]}</span></td>'
        f'<td class="schwelle">{KLASSEN_ERKLAERUNG[k][0]}</td><td>{KLASSEN_ERKLAERUNG[k][1]}</td></tr>'
        for k in ["KEIN_NIEDRIGWASSER", "NIEDRIG", "SEHR_NIEDRIG", "EXTREM_NIEDRIG"]
    )
    verlaufszeilen = "".join(
        f"<tr><td>{de_datum(e['datum'])}</td>"
        + "".join(f'<td class="z">{e["verteilung"].get(k, 0)}</td>' for k in KLASSEN_REIHE)
        + "</tr>"
        for e in verlauf
    )
    verlaufskopf = "".join(f"<th>{KLASSEN[k][0]}</th>" for k in KLASSEN_REIHE)

    rekordsatz = ""
    if unter_rekord:
        rekordsatz = (f' Bei <strong>{unter_rekord}</strong> davon '
                      f'{"liegt" if unter_rekord == 1 else "liegen"} der heutige Abfluss sogar unter dem '
                      f'niedrigsten Wert, den die amtliche Zeitreihe dort bisher führt.')

    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Wasserlage Raum Koblenz</title>
<meta name="description" content="Täglich aktualisierte Niedrigwasser-Lage an Rhein, Mosel und Lahn im Raum Koblenz, auf Basis der amtlichen NIWIS-Daten der Bundesanstalt für Gewässerkunde.">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Ccircle cx='16' cy='16' r='14' fill='%23b32d2d'/%3E%3C/svg%3E">
<style>
  :root {{
    --grund:#fff; --karte:#fff; --text:#15171b; --leise:#5b6470; --linie:#e4e7ec; --feld:#f5f7f9;
    --gruen:#2f7d4f; --gelb:#a8820f; --orange:#c1631b; --rot:#b32d2d; --grau:#8d949e; --akzent:#15171b;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --grund:#101216; --karte:#171a1f; --text:#eceef1; --leise:#98a0ab; --linie:#272c33; --feld:#1a1e24;
      --gruen:#4da372; --gelb:#cfa227; --orange:#e08036; --rot:#de5a5a; --grau:#79808a; --akzent:#eceef1;
    }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--grund); color:var(--text);
    font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    -webkit-font-smoothing:antialiased; }}
  .huelle {{ max-width:960px; margin:0 auto; padding:40px 20px 72px; }}
  h1 {{ font-size:clamp(1.6rem,4.5vw,2.2rem); line-height:1.15; margin:0 0 8px; letter-spacing:-0.02em; }}
  h2 {{ font-size:1.1rem; margin:44px 0 14px; letter-spacing:-0.01em; }}
  h3 {{ font-size:1rem; margin:0 0 3px; letter-spacing:-0.01em; }}
  .unterzeile {{ color:var(--leise); margin:0 0 28px; }}

  .lage {{ background:var(--feld); border:1px solid var(--linie); border-radius:14px; padding:22px; }}
  .lage .satz {{ margin:0 0 16px; font-size:clamp(1rem,2.6vw,1.18rem); line-height:1.45; }}
  .balken {{ display:flex; height:14px; border-radius:7px; overflow:hidden; gap:2px; }}
  .teil {{ min-width:4px; }}
  .legenden {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; }}
  .legende {{ display:inline-flex; align-items:center; gap:7px; white-space:nowrap; cursor:pointer;
    font:inherit; font-size:0.83rem; color:var(--leise); background:var(--karte);
    border:1px solid var(--linie); border-radius:999px; padding:5px 12px; }}
  .legende:hover {{ border-color:var(--leise); }}
  .legende[aria-pressed="true"] {{ border-color:var(--akzent); color:var(--text); }}
  .legende b {{ font-variant-numeric:tabular-nums; }}
  .punkt {{ width:9px; height:9px; border-radius:50%; display:inline-block; flex:none; }}

  .werkzeuge {{ display:flex; flex-wrap:wrap; gap:8px; margin:0 0 18px; }}
  .chip {{ font:inherit; font-size:0.85rem; padding:7px 14px; border-radius:999px; cursor:pointer;
    background:var(--karte); color:var(--leise); border:1px solid var(--linie); }}
  .chip[aria-pressed="true"] {{ background:var(--akzent); color:var(--grund); border-color:var(--akzent); }}

  .liste {{ display:grid; gap:12px; }}
  .karte {{ position:relative; background:var(--karte); border:1px solid var(--linie);
    border-radius:14px; padding:16px 18px 14px 22px; overflow:hidden; }}
  .karte[hidden] {{ display:none; }}
  .streifen {{ position:absolute; left:0; top:0; bottom:0; width:5px; }}
  .kopf {{ display:flex; justify-content:space-between; align-items:flex-start; gap:12px; }}
  .ort {{ margin:0; color:var(--leise); font-size:0.85rem; }}
  .badge {{ display:inline-block; padding:3px 11px; border-radius:999px; font-size:0.76rem;
    font-weight:600; color:#fff; white-space:nowrap; flex:none; }}
  .werte {{ display:flex; align-items:center; flex-wrap:wrap; gap:10px 20px; margin-top:12px; }}
  .wert {{ font-size:1.22rem; font-weight:600; font-variant-numeric:tabular-nums; letter-spacing:-0.01em; }}
  .zusatz {{ font-size:0.83rem; font-weight:400; color:var(--leise); }}
  .spark {{ overflow:visible; }}
  .spark polyline {{ fill:none; stroke:var(--leise); stroke-width:1.6; stroke-linejoin:round; stroke-linecap:round; }}
  .trendfeld, .stand {{ color:var(--leise); font-size:0.86rem; }}
  .pfeil {{ font-size:1rem; }}
  .alt {{ color:var(--orange); display:block; font-size:0.8rem; }}
  details {{ margin-top:12px; border-top:1px solid var(--linie); padding-top:10px; }}
  summary {{ cursor:pointer; color:var(--leise); font-size:0.85rem; list-style:none; }}
  summary::-webkit-details-marker {{ display:none; }}
  summary::before {{ content:"+ "; }}
  details[open] summary::before {{ content:"– "; }}
  details p {{ margin:10px 0 0; font-size:0.9rem; }}
  details ul {{ margin:10px 0 0; padding-left:18px; color:var(--leise); font-size:0.86rem; }}
  .einordnung.warnung {{ color:var(--rot); font-weight:500; }}

  .tabellenrahmen {{ overflow-x:auto; -webkit-overflow-scrolling:touch; }}
  table {{ width:100%; border-collapse:collapse; font-size:0.9rem; }}
  th {{ text-align:left; font-size:0.74rem; text-transform:uppercase; letter-spacing:0.04em;
    color:var(--leise); font-weight:600; padding:0 12px 8px 0; white-space:nowrap; }}
  td {{ padding:10px 12px 10px 0; border-top:1px solid var(--linie); vertical-align:top; }}
  .schwelle {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; color:var(--leise); white-space:nowrap; }}
  .z {{ text-align:right; font-variant-numeric:tabular-nums; padding-right:16px; }}
  .gruen{{background:var(--gruen);}} .gelb{{background:var(--gelb);}} .orange{{background:var(--orange);}}
  .rot{{background:var(--rot);}} .grau{{background:var(--grau);}}
  @media (max-width:600px) {{
    .erklaerung tr {{ display:block; border-top:1px solid var(--linie); padding:12px 0 4px; }}
    .erklaerung td {{ display:block; border:0; padding:0 0 6px; }}
    .erklaerung td:first-child {{ margin-bottom:4px; }}
    .erklaerung + thead, thead:has(+ .erklaerung) th:not(:first-child) {{ display:none; }}
  }}
  .hinweis {{ background:var(--feld); border:1px solid var(--linie); padding:16px 18px;
    border-radius:12px; color:var(--leise); font-size:0.89rem; }}
  .hinweis p {{ margin:0 0 10px; }} .hinweis p:last-child {{ margin:0; }}
  footer {{ margin-top:48px; padding-top:20px; border-top:1px solid var(--linie);
    color:var(--leise); font-size:0.83rem; }}
  a {{ color:inherit; }}
  .leer {{ color:var(--leise); font-size:0.9rem; padding:18px 0; }}
</style>
</head>
<body>
<div class="huelle">
  <h1>Wasserlage Raum Koblenz</h1>
  <p class="unterzeile">Rhein, Mosel, Lahn und die Zuflüsse ringsum &middot; Stand {de_datum(stand['abgerufen_am'])}</p>

  <div class="lage">
    <p class="satz"><strong>{auffaellig} von {gesamt} Messstellen</strong> im Raum Koblenz stehen auf
       &bdquo;sehr niedrig&ldquo; oder &bdquo;extrem niedrig&ldquo;.{rekordsatz}</p>
    <div class="balken">{balken}</div>
    <div class="legenden">{legende}</div>
  </div>

  <h2>Was die Einstufungen bedeuten</h2>
  <div class="tabellenrahmen">
    <table>
      <thead><tr><th>Einstufung</th><th>Schwelle</th><th>Bedeutung</th></tr></thead>
      <tbody class="erklaerung">{erklaerzeilen}</tbody>
    </table>
  </div>
  <p class="unterzeile" style="margin-top:12px;font-size:0.86rem">
    Die Schwellen stammen aus NIWIS und werden je Messstelle aus dem Zeitraum 1991 bis 2020 berechnet.
    Sie sind damit zwischen Messstellen vergleichbar, auch wenn ein kleiner Bach und der Rhein völlig
    unterschiedliche Mengen führen.</p>

  <h2>Messstellen</h2>
  <div class="werkzeuge">
    <button class="chip" data-bereich="kern" aria-pressed="true">Raum Koblenz ({len(kern)})</button>
    <button class="chip" data-bereich="alle" aria-pressed="false">auch weiteres Umfeld ({len(stand['stationen'])})</button>
    <button class="chip" data-groesse="Abfluss" aria-pressed="false">nur Flüsse</button>
    <button class="chip" data-groesse="Grundwasser" aria-pressed="false">nur Grundwasser</button>
  </div>
  <div class="liste" id="liste">
{chr(10).join(stationskarte(s, stand['abgerufen_am']) for s in stand['stationen'])}
  </div>
  <p class="leer" id="leer" hidden>Keine Messstelle passt zu dieser Auswahl.</p>

  <h2>Verlauf</h2>
  <p class="unterzeile" style="font-size:0.88rem">Anzahl Messstellen im Raum Koblenz je Einstufung, ein Eintrag pro Abruftag.</p>
  <div class="tabellenrahmen">
    <table>
      <thead><tr><th>Tag</th>{verlaufskopf}</tr></thead>
      <tbody>{verlaufszeilen}</tbody>
    </table>
  </div>

  <h2>Was diese Seite ist, und was nicht</h2>
  <div class="hinweis">
    <p>Sie gibt die amtliche Einstufung der Messstellen wieder und ordnet sie ein. Sie ist eine
       Orientierung, <strong>keine amtliche Warnung</strong> und keine Aussage über die
       Trinkwasserversorgung einer bestimmten Gemeinde. Dafür sind die Betriebsdaten des jeweiligen
       Wasserversorgers maßgeblich, die hier nicht enthalten sind.</p>
    <p>Als &bdquo;Raum Koblenz&ldquo; gelten die Messstellen an Rhein, Mosel, Lahn, Ahr, Wied und den
       Mittelrhein-Zuflüssen sowie Grundwassermessstellen in Rheinland-Pfalz bis 45 km. Messstellen im
       Umkreis, die hydrologisch woanders hingehören, etwa an der Sieg oder an der Selz, stehen unter
       &bdquo;weiteres Umfeld&ldquo;.</p>
    <p>Grundwasserstände können mehrere Wochen alt sein, das Messdatum steht deshalb an jeder
       Messstelle. Aktuelle Werte sind vorläufig und noch nicht abschließend geprüft.</p>
  </div>

  <footer>
    Datenquelle: NIWIS, Bundesanstalt für Gewässerkunde, Koblenz
    (<a href="https://niwis-online.de">niwis-online.de</a>). Messdaten der Länder und des Bundes,
    je Messstelle unter CC BY 4.0. Einstufung und Entwicklung stammen unverändert aus NIWIS.<br>
    Zusammenstellung: <a href="https://gaind.ai">gaind</a>. Automatischer Abruf, täglich.
  </footer>
</div>
<script>
(function () {{
  var zustand = {{ bereich: "kern", groesse: null, klasse: null }};
  var karten = Array.prototype.slice.call(document.querySelectorAll(".karte"));
  var leer = document.getElementById("leer");

  function anwenden() {{
    var sichtbar = 0;
    karten.forEach(function (k) {{
      var passt =
        (zustand.bereich === "alle" || k.dataset.kern === "true") &&
        (!zustand.groesse || k.dataset.groesse === zustand.groesse) &&
        (!zustand.klasse || k.dataset.klasse === zustand.klasse);
      k.hidden = !passt;
      if (passt) sichtbar++;
    }});
    leer.hidden = sichtbar > 0;
  }}

  document.querySelectorAll(".chip").forEach(function (chip) {{
    chip.addEventListener("click", function () {{
      if (chip.dataset.bereich) {{
        zustand.bereich = chip.dataset.bereich;
        document.querySelectorAll("[data-bereich]").forEach(function (c) {{
          c.setAttribute("aria-pressed", String(c === chip));
        }});
      }} else {{
        var an = chip.getAttribute("aria-pressed") === "true";
        zustand.groesse = an ? null : chip.dataset.groesse;
        document.querySelectorAll("[data-groesse]").forEach(function (c) {{
          c.setAttribute("aria-pressed", String(!an && c === chip));
        }});
      }}
      anwenden();
    }});
  }});

  document.querySelectorAll(".legende").forEach(function (b) {{
    b.setAttribute("aria-pressed", "false");
    b.addEventListener("click", function () {{
      var an = b.getAttribute("aria-pressed") === "true";
      zustand.klasse = an ? null : b.dataset.filter;
      document.querySelectorAll(".legende").forEach(function (c) {{
        c.setAttribute("aria-pressed", String(!an && c === b));
      }});
      anwenden();
    }});
  }});

  anwenden();
}})();
</script>
</body>
</html>
"""


def main():
    if "--nur-rendern" in sys.argv:
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
    kern = sum(1 for s in stand["stationen"] if s["kernraum"])
    print(f"Seite gerendert: {kern} im Raum Koblenz, {len(stand['stationen'])} gesamt, Stand {stand['abgerufen_am']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
