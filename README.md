# Wasserlage Raum Koblenz

Täglich aktualisierte Übersicht der amtlichen Niedrigwasser-Einstufung für alle
NIWIS-Messstellen im Umkreis von 60 km um Koblenz. Statische Seite, kein Server, keine
Datenbank, keine laufenden Kosten.

## Was es tut

1. Holt je Messgröße die Karten-Übersicht aus NIWIS (Einstufung, Entwicklung, Koordinate)
2. Filtert auf den 60-km-Umkreis um Koblenz
3. Holt je Station den jüngsten echten Messwert samt **Messdatum** aus der dokumentierten API
4. Schreibt einen Tagesschnappschuss nach `data/JJJJ-MM-TT.json` und `data/latest.json`
5. Rendert `docs/index.html`

Die **Git-Historie ist das Archiv**: ein Commit pro Tag, datiert und nachvollziehbar. Genau
das fehlt allen bestehenden Wasserampeln in RLP (0 von 5 zeigen überhaupt ein Stand-Datum).

## Ausführen

```bash
python3 build.py              # abrufen und rendern
python3 build.py --nur-rendern  # nur die Seite neu bauen, ohne Abruf
```

Keine Abhängigkeiten, reine Standardbibliothek. Laufzeit rund 4 Sekunden bei 25 Stationen.

## Datenquellen

| Zweck | Endpunkt |
|---|---|
| Einstufung, Entwicklung, Koordinate | `/api/karte/messstelle/{ABFLUSS\|GRUNDWASSER\|QUELLSCHUETTUNG}?klassifikationsart=DYNAMISCH` |
| Messwert und Messdatum | `/api/daten/{abfluss\|grundwasserstand\|quellschuettung}?messstelleNr=&von=&bis=` |

Basis: `https://niwis-online.de`, ohne Authentifizierung.

**Wichtig zur Stabilität:** Der zweite Endpunkt gehört zur offiziell dokumentierten
öffentlichen API (Portal, Infomenü, „Öffentliche API"). Der erste ist der interne Endpunkt
des Portals, er liefert Einstufung und Trend in einem einzigen Aufruf statt in einem pro
Station. Er kann sich ohne Ankündigung ändern. Beide Aufrufe stecken deshalb in `hole()`,
ein Wechsel auf `berechneEinzelwertKategorie` je Station ist an einer Stelle möglich.

## Fallstricke, die schon getreten wurden

- **Fehlwerte sind `-777.0` mit `flag: "BfGAdded"`.** Ungefiltert verseuchen sie jeden
  Mittelwert und jeden Trend. Wird in `letzte_messung()` aussortiert.
- **Grundwasser ist nicht tagesaktuell.** NIWIS lässt Werte bis 31 Tage alt zu, RLP misst
  sein Netz überwiegend wöchentlich von Hand. Deshalb steht an jeder Zeile das echte
  Messdatum, und ab 7 Tagen Abstand ein sichtbarer Alters-Hinweis.
- **NIWIS berechnet für Grundwasser keinen Trend.** Das Feld ist dort immer `KEINE_DATEN`,
  die Seite zeigt ehrlich „unbekannt“ statt etwas zu erfinden.
- Stationen mit Einstufung `KEINE_DATEN` oder ohne Messwert dürfen den Lauf nicht abbrechen.
  Abgedeckt, siehe Rheindiebach (Gailsbach).

## Lizenz und Namensnennung

Die Messdaten stammen von Bund und Ländern über NIWIS (Bundesanstalt für Gewässerkunde,
Koblenz) und stehen je Messstelle unter **CC BY 4.0**. Die Namensnennung steht im Fuß der
Seite und muss dort bleiben. Die NIWIS-Nutzungsbedingungen erlauben ausdrücklich die Nutzung
„für private, wissenschaftliche und gewerbliche Zwecke".

## Veröffentlichung

GitHub Pages aus dem Ordner `docs/` auf `main`. Der Workflow
`.github/workflows/taeglich.yml` läuft täglich um 05:20 UTC, committet den Tagesstand und
pusht. Bei einem öffentlichen Repo sind Actions-Minuten und Pages kostenlos.

## Git-Identität

Repo-lokal auf gaind gepinnt, damit nichts unter der Particulate-Identität landet:

```bash
git config user.email   # julian@gaind.ai
git config credential.helper  # gh auth token -u gaindai
```
