# Grundstücks-Finder Niedersachsen

Findet Flurstücke in Niedersachsen anhand der **amtlichen Fläche (m²)** und der **Gemeinde**
und gibt die **Lagebezeichnung (Straße + Hausnummer)**, die Flurstücksangaben, die Nutzung
und einen Kartenlink aus. Das Skript arbeitet vollautomatisch, nutzt nur die
Python-Standardbibliothek (ab Python 3.9) und braucht weder Registrierung noch API-Key.

```bash
python grundstueck_finder.py 1190 Wedemark
```

```
[1] Zum Hundshop 14
    Gemeinde:   Wedemark (Region Hannover)
    Flurstück:  Gemarkung Abbensen, Flur 1, Flurstück 61/2
    Fläche:     1.190 m²
    Nutzung:    Wohnbaufläche 1190 m²
    Karte:      https://www.openstreetmap.org/?mlat=52.568438&mlon=9.609039#map=19/52.568438/9.609039
```

## Optionen

| Option | Zweck |
|---|---|
| `-t 2` / `--toleranz 2` | ±2 m² Abweichung zulassen (z. B. wenn die Angabe gerundet ist) |
| `-k "Heidekreis"` / `--kreis` | mehrdeutige Gemeindenamen eingrenzen (es gibt 5× „Neuenkirchen“) **oder** ohne Gemeinde den ganzen Landkreis durchsuchen |
| `-g Bissendorf` / `--gemarkung` | nur eine Gemarkung (Ortsteil) |
| `--ags 03241019` | amtlicher Gemeindeschlüssel statt Name |
| `-n Wohnbau` / `--nutzung` | nach tatsächlicher Nutzung filtern (`Wohnbau`, `Landwirtschaft`, `Wald`, `Gewerbe` …) |
| `-a` / `--nur-adressen` | nur Flurstücke mit Hausnummer |
| `--nachbarn` | für Flurstücke ohne Hausnummer (Garten, Acker, Hinterland) die Adressen der angrenzenden Flurstücke anzeigen |
| `--csv datei.csv` | alle Treffer als CSV (Excel-kompatibel, `;`-getrennt) |
| `--json` | JSON-Ausgabe |
| `--max 100` | mehr Treffer anzeigen (Standard 50) |

Beispiele:

```bash
python grundstueck_finder.py 850 Neuenkirchen --kreis Heidekreis
python grundstueck_finder.py 612 Hannover -t 1 -n Wohnbau --csv hannover_612.csv
python grundstueck_finder.py 1503 --kreis Rotenburg --nur-adressen
python grundstueck_finder.py 4200 Wedemark -n Landwirtschaft --nachbarn
```

## Datenquelle

**ALKIS® vereinfacht – WFS des LGLN** (Landesamt für Geoinformation und Landesvermessung
Niedersachsen), Teil von **OpenGeoData.NI**. Die Daten sind seit der Open-Data-Freigabe
des LGLN entgeltfrei und unter der Lizenz **Datenlizenz Deutschland – Namensnennung 2.0**
(dl-de/by-2-0) nutzbar. Quellenvermerk: „© LGLN“.

- Endpunkt: `https://opendata.lgln.niedersachsen.de/doorman/noauth/alkis_wfs_einfach`
  ([GetCapabilities](https://opendata.lgln.niedersachsen.de/doorman/noauth/alkis_wfs_einfach?SERVICE=WFS&REQUEST=GetCapabilities))
- Layer `ave:Flurstueck` mit den Feldern `flaeche` (amtliche Fläche), `gemeinde`, `gmdschl`,
  `gemarkung`, `flur`, `flstnrzae/flstnrnen`, **`lagebeztxt`** (Lagebezeichnung = Adresse)
  und `tntxt` (tatsächliche Nutzung)
- Layer `ave:VerwaltungsEinheit` zur Auflösung Gemeindename → Gemeindeschlüssel
- Das Skript filtert auf dem Server (OGC Filter Encoding: `PropertyIsBetween` auf `flaeche`
  plus Gemeindeschlüssel). Pro Suche wird also nur eine Handvoll Datensätze übertragen,
  eine Abfrage dauert etwa 3–5 Sekunden.

Weitere Dienste des LGLN, falls du die Daten anders auswerten willst:

| Dienst | URL | Nutzen |
|---|---|---|
| ALKIS WFS vereinfacht | `…/doorman/noauth/alkis_wfs_einfach` | **vom Skript genutzt** |
| ALKIS WFS Simple Features | `…/doorman/noauth/alkis_wfs_sf` | feinere Objektarten |
| ALKIS WFS NAS | `…/doorman/noauth/alkis_wfs_nas` | volles AAA-Datenmodell (AX_Flurstueck …) |
| Download (GeoPackage je Landkreis) | [OpenGeoData.NI](https://ni-lgln-opengeodata.hub.arcgis.com/) · [opengeodata.lgln.niedersachsen.de](https://opengeodata.lgln.niedersachsen.de/) | offline, z. B. mit QGIS oder `sqlite3` filtern |
| Kartenviewer | [geobasis.niedersachsen.de](https://www.geobasis.niedersachsen.de/) · [Geoportal Niedersachsen](https://www.geodaten.niedersachsen.de/) | Treffer manuell auf der Liegenschaftskarte prüfen |

Wenn der WFS einmal nicht erreichbar ist, kannst du das GeoPackage „Flurstücke“ des
Landkreises herunterladen und in QGIS nach `flaeche = X` filtern. Die Feldnamen sind dort
die gleichen.

## Bestehende Webseiten

Ich habe keine öffentliche Seite gefunden, die rückwärts **nach Fläche** sucht. Seiten wie
[flurcheck.de](https://www.flurcheck.de/) oder die Länder-Geoportale arbeiten in die andere
Richtung: Adresse oder Klick auf die Karte, dann Fläche und Flurstücksnummer. flurcheck.de
verweist auf die ALKIS-Open-Data der Länder (dl-de/by-2-0), also auf dieselbe Datenbasis,
die dieses Skript nutzt.

## Grenzen / Hinweise

- **Grundstück ≠ Flurstück:** Ein Grundstück im Grundbuchsinn kann aus mehreren Flurstücken
  bestehen. Die Fläche in einer Anzeige (z. B. Zwangsversteigerung im
  [ZVG-Portal](https://www.zvg-portal.de/)) ist dann die **Summe**, und die Suche nach einem
  einzelnen Flurstück findet nichts. In dem Fall mit `--toleranz` und `--nachbarn` arbeiten
  oder nach dem größten Teilflurstück suchen. Oft stehen Gemarkung, Flur und Flurstück aber
  ohnehin in der Bekanntmachung, dann reicht `--gemarkung` mit einer kleinen Toleranz.
- **Samtgemeinden** führt der Dienst nicht als eigene Einheit, sondern nur deren
  Mitgliedsgemeinden. Gib dann die Mitgliedsgemeinde an oder suche mit `--kreis` im ganzen
  Landkreis.
- **Mehrere Treffer** sind normal, vor allem bei runden Flächen in großen Städten.
  Grenze dann mit `--gemarkung`, `--nutzung` oder `--nur-adressen` ein.
- `lagebeztxt` enthält bei unbebauten Flurstücken oft nur Straße oder Gewann (z. B.
  „Kartoffelacker“). `--nachbarn` liefert dafür die Adressen der Nachbarflurstücke.
- Eigentümerdaten sind **nicht** enthalten. Die gibt es nur mit berechtigtem Interesse beim
  Katasteramt bzw. Grundbuchamt.
- Koordinaten: Der Dienst liefert ETRS89/UTM32. Das Skript rechnet den Flächenschwerpunkt
  selbst nach WGS84 um (Genauigkeit im cm-Bereich).
