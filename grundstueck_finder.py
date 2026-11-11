#!/usr/bin/env python3
"""
Grundstücks-Finder Niedersachsen
================================

Findet Flurstücke (und deren Lagebezeichnung / Adresse) in Niedersachsen anhand
der amtlichen Fläche in m² und der Gemeinde.

Datenquelle: ALKIS® vereinfacht, WFS des LGLN (OpenGeoData.NI)
  https://opendata.lgln.niedersachsen.de/doorman/noauth/alkis_wfs_einfach
  Lizenz: Datenlizenz Deutschland – Namensnennung – Version 2.0
  Quellenvermerk: © LGLN (Landesamt für Geoinformation und Landesvermessung Niedersachsen)

Nur Python-Standardbibliothek, keine Installation nötig.

Beispiele:
  python grundstueck_finder.py 1190 Wedemark
  python grundstueck_finder.py 850 Neuenkirchen --kreis Heidekreis
  python grundstueck_finder.py 612 "Hannover" --toleranz 2 --nutzung Wohnbau
  python grundstueck_finder.py 1500 --kreis "Rotenburg" --nur-adressen --csv treffer.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from xml.sax.saxutils import escape as xml_escape

WFS_URL = "https://opendata.lgln.niedersachsen.de/doorman/noauth/alkis_wfs_einfach"
NS = {
    "wfs": "http://www.opengis.net/wfs/2.0",
    "gml": "http://www.opengis.net/gml/3.2",
    "ave": "http://repository.gdi-de.org/schemas/adv/produkt/alkis-vereinfacht/2.0",
    "ows": "http://www.opengis.net/ows/1.1",
}
FILTER_NS = (
    'xmlns:fes="http://www.opengis.net/fes/2.0" '
    'xmlns:ave="http://repository.gdi-de.org/schemas/adv/produkt/alkis-vereinfacht/2.0" '
    'xmlns:gml="http://www.opengis.net/gml/3.2"'
)
CRS = "urn:ogc:def:crs:EPSG::25832"  # ETRS89 / UTM 32N (Standard des Dienstes)
PAGE_SIZE = 1000
USER_AGENT = "grundstueck-finder/1.0 (Python urllib)"


# --------------------------------------------------------------------------- #
# HTTP / WFS
# --------------------------------------------------------------------------- #

def wfs_get_feature(typename: str, fes_filter: str, start: int = 0, count: int = PAGE_SIZE,
                    retries: int = 3) -> ET.Element:
    params = {
        "SERVICE": "WFS",
        "VERSION": "2.0.0",
        "REQUEST": "GetFeature",
        "TYPENAMES": typename,
        "SRSNAME": CRS,
        "COUNT": str(count),
        "STARTINDEX": str(start),
        "FILTER": fes_filter,
    }
    url = WFS_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = resp.read()
            root = ET.fromstring(data)
            if root.tag.endswith("ExceptionReport"):
                txt = " ".join(t.strip() for t in root.itertext() if t.strip())
                raise RuntimeError(f"WFS-Fehler: {txt}")
            return root
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"WFS nicht erreichbar: {last_err}")


def wfs_get_all(typename: str, fes_filter: str, limit: int | None = None) -> list[ET.Element]:
    """Holt alle Features seitenweise (Result Paging)."""
    members: list[ET.Element] = []
    start = 0
    while True:
        root = wfs_get_feature(typename, fes_filter, start=start)
        page = [m[0] for m in root.findall("wfs:member", NS) if len(m)]
        members.extend(page)
        if len(page) < PAGE_SIZE or (limit and len(members) >= limit):
            break
        start += PAGE_SIZE
    return members


def lit(value) -> str:
    return f"<fes:Literal>{xml_escape(str(value))}</fes:Literal>"


def like_escape(text: str) -> str:
    # Wildcard '*', Einzelzeichen '?', Escape '!'
    return re.sub(r"([*?!])", r"!\1", text)


def f_equal(prop: str, value) -> str:
    return (f"<fes:PropertyIsEqualTo><fes:ValueReference>{prop}</fes:ValueReference>"
            f"{lit(value)}</fes:PropertyIsEqualTo>")


def f_like(prop: str, text: str) -> str:
    return (f'<fes:PropertyIsLike wildCard="*" singleChar="?" escapeChar="!" matchCase="false">'
            f"<fes:ValueReference>{prop}</fes:ValueReference>{lit('*' + like_escape(text) + '*')}"
            f"</fes:PropertyIsLike>")


def f_between(prop: str, lo, hi) -> str:
    return (f"<fes:PropertyIsBetween><fes:ValueReference>{prop}</fes:ValueReference>"
            f"<fes:LowerBoundary>{lit(lo)}</fes:LowerBoundary>"
            f"<fes:UpperBoundary>{lit(hi)}</fes:UpperBoundary></fes:PropertyIsBetween>")


def f_or(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else "<fes:Or>" + "".join(parts) + "</fes:Or>"


def f_and(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else "<fes:And>" + "".join(parts) + "</fes:And>"


def f_bbox(xmin, ymin, xmax, ymax) -> str:
    return (f"<fes:BBOX><fes:ValueReference>ave:geometrie</fes:ValueReference>"
            f'<gml:Envelope srsName="{CRS}">'
            f"<gml:lowerCorner>{xmin:.3f} {ymin:.3f}</gml:lowerCorner>"
            f"<gml:upperCorner>{xmax:.3f} {ymax:.3f}</gml:upperCorner>"
            f"</gml:Envelope></fes:BBOX>")


def wrap(inner: str) -> str:
    return f"<fes:Filter {FILTER_NS}>{inner}</fes:Filter>"


def text(el: ET.Element, tag: str) -> str:
    child = el.find(f"ave:{tag}", NS)
    return (child.text or "").strip() if child is not None else ""


# --------------------------------------------------------------------------- #
# Geometrie: Schwerpunkt + UTM32 -> WGS84
# --------------------------------------------------------------------------- #

def parse_rings(feature: ET.Element) -> list[list[tuple[float, float]]]:
    rings = []
    for poslist in feature.iterfind(".//gml:exterior//gml:posList", NS):
        vals = [float(v) for v in (poslist.text or "").split()]
        rings.append(list(zip(vals[0::2], vals[1::2])))
    return rings


def ring_centroid(ring: list[tuple[float, float]]) -> tuple[float, float, float]:
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:] + ring[:1]):
        c = x0 * y1 - x1 * y0
        a += c
        cx += (x0 + x1) * c
        cy += (y0 + y1) * c
    if abs(a) < 1e-9:
        xs, ys = zip(*ring)
        return sum(xs) / len(xs), sum(ys) / len(ys), 0.0
    return cx / (3 * a), cy / (3 * a), abs(a) / 2


def utm32_to_wgs84(e: float, n: float) -> tuple[float, float]:
    """ETRS89/UTM32N -> geographische Koordinaten (Krüger-Reihen, GRS80)."""
    a, f, k0, lon0 = 6378137.0, 1 / 298.257222101, 0.9996, math.radians(9.0)
    nn = f / (2 - f)
    A = a / (1 + nn) * (1 + nn ** 2 / 4 + nn ** 4 / 64)
    beta = [nn / 2 - 2 * nn ** 2 / 3 + 37 * nn ** 3 / 96, nn ** 2 / 48 + nn ** 3 / 15, 17 * nn ** 3 / 480]
    delta = [2 * nn - 2 * nn ** 2 / 3 - 2 * nn ** 3, 7 * nn ** 2 / 3 - 8 * nn ** 3 / 5, 56 * nn ** 3 / 15]
    xi = n / (k0 * A)
    eta = (e - 500000.0) / (k0 * A)
    xi_p = xi - sum(b * math.sin(2 * j * xi) * math.cosh(2 * j * eta) for j, b in enumerate(beta, 1))
    eta_p = eta - sum(b * math.cos(2 * j * xi) * math.sinh(2 * j * eta) for j, b in enumerate(beta, 1))
    chi = math.asin(math.sin(xi_p) / math.cosh(eta_p))
    lat = chi + sum(d * math.sin(2 * j * chi) for j, d in enumerate(delta, 1))
    lon = lon0 + math.atan2(math.sinh(eta_p), math.cos(xi_p))
    return math.degrees(lat), math.degrees(lon)


# --------------------------------------------------------------------------- #
# Fachlogik
# --------------------------------------------------------------------------- #

@dataclass
class Gemeinde:
    name: str
    ags: str
    kreis: str


@dataclass
class Treffer:
    flaeche: float
    gemeinde: str
    kreis: str
    gemarkung: str
    flur: str
    flurstueck: str
    kennzeichen: str
    lage: str
    nutzung: str
    lat: float | None
    lon: float | None
    hat_hausnummer: bool
    nachbar_adressen: list[str] = field(default_factory=list)
    bbox: tuple[float, float, float, float] | None = None

    @property
    def osm_link(self) -> str:
        if self.lat is None:
            return ""
        return (f"https://www.openstreetmap.org/?mlat={self.lat:.6f}&mlon={self.lon:.6f}"
                f"#map=19/{self.lat:.6f}/{self.lon:.6f}")

    @property
    def gmaps_link(self) -> str:
        if self.lat is None:
            return ""
        return f"https://www.google.com/maps?q={self.lat:.6f},{self.lon:.6f}"


HAUSNR_RE = re.compile(r"\d")


def finde_gemeinden(name: str, kreis: str | None) -> list[Gemeinde]:
    """Löst einen Gemeindenamen über den Layer VerwaltungsEinheit in AGS auf."""
    flt = wrap(f_and([f_equal("ave:art", "Gemeinde"),
                      f_like("ave:VerwaltungsEinheit/ave:name", name)]))
    found = []
    for el in wfs_get_all("ave:VerwaltungsEinheit", flt):
        found.append(Gemeinde(text(el, "name"), text(el, "ags"), text(el, "ueboname")))
    if kreis:
        found = [g for g in found if kreis.casefold() in g.kreis.casefold()]

    q = name.casefold().strip()

    def kurz(n: str) -> str:  # "Celle, Stadt" -> "celle"
        return n.split(",")[0].strip().casefold()

    exakt = [g for g in found if g.name.casefold() == q or kurz(g.name) == q]
    if exakt:
        return exakt
    beginnt = [g for g in found if kurz(g.name).startswith(q)]
    return beginnt or found


def suche_flurstuecke(flaeche: float, toleranz: float, gemeinden: list[Gemeinde] | None,
                      kreis: str | None, gemarkung: str | None) -> list[Treffer]:
    parts = [f_between("ave:flaeche", flaeche - toleranz, flaeche + toleranz)]
    if gemeinden:
        parts.append(f_or([f_equal("ave:gmdschl", g.ags) for g in gemeinden]))
    elif kreis:
        parts.append(f_like("ave:kreis", kreis))
    if gemarkung:
        parts.append(f_like("ave:gemarkung", gemarkung))
    features = wfs_get_all("ave:Flurstueck", wrap(f_and(parts)))
    return [feature_to_treffer(f) for f in features]


def format_nutzung(tntxt: str) -> str:
    """'Wald(vegetationsmerkmal:Laubholz,nutzung:null);903|Weg;24' -> 'Wald (Laubholz) 903 m², Weg 24 m²'"""
    teile = []
    for seg in filter(None, tntxt.split("|")):
        art, _, qm = seg.rpartition(";")
        if not art:
            art, qm = seg, ""
        m = re.match(r"^(.*?)\((.*)\)$", art)
        if m:
            werte = [kv.split(":", 1)[-1] for kv in m.group(2).split(",")]
            werte = [w for w in werte if w and w != "null"]
            art = m.group(1) + (f" ({', '.join(werte)})" if werte else "")
        teile.append(f"{art} {qm} m²" if qm else art)
    return ", ".join(teile)


def feature_to_treffer(el: ET.Element) -> Treffer:
    rings = parse_rings(el)
    lat = lon = None
    bbox = None
    if rings:
        cx, cy, _ = max((ring_centroid(r) for r in rings), key=lambda t: t[2])
        lat, lon = utm32_to_wgs84(cx, cy)
        xs = [p[0] for r in rings for p in r]
        ys = [p[1] for r in rings for p in r]
        bbox = (min(xs), min(ys), max(xs), max(ys))
    zae, nen = text(el, "flstnrzae"), text(el, "flstnrnen")
    lage = text(el, "lagebeztxt")
    nutzung = format_nutzung(text(el, "tntxt"))
    return Treffer(
        flaeche=float(text(el, "flaeche") or "nan"),
        gemeinde=text(el, "gemeinde"),
        kreis=text(el, "kreis"),
        gemarkung=text(el, "gemarkung"),
        flur=text(el, "flur").lstrip("0") or text(el, "flur"),
        flurstueck=f"{zae}/{nen}" if nen else zae,
        kennzeichen=text(el, "flstkennz"),
        lage=lage,
        nutzung=nutzung,
        lat=lat,
        lon=lon,
        hat_hausnummer=bool(HAUSNR_RE.search(lage)),
        bbox=bbox,
    )


def ergaenze_nachbarn(t: Treffer, puffer: float = 3.0, max_anzahl: int = 6) -> None:
    """Sucht Adressen benachbarter Flurstücke (z. B. für Garten-/Hinterland-Flurstücke)."""
    if not t.bbox:
        return
    x0, y0, x1, y1 = t.bbox
    flt = wrap(f_bbox(x0 - puffer, y0 - puffer, x1 + puffer, y1 + puffer))
    adressen: list[str] = []
    for el in wfs_get_all("ave:Flurstueck", flt, limit=200):
        if text(el, "flstkennz") == t.kennzeichen:
            continue
        lage = text(el, "lagebeztxt")
        if HAUSNR_RE.search(lage) and lage not in adressen:
            adressen.append(lage)
        if len(adressen) >= max_anzahl:
            break
    t.nachbar_adressen = adressen


# --------------------------------------------------------------------------- #
# Ausgabe
# --------------------------------------------------------------------------- #

def fmt_flaeche(v: float) -> str:
    return f"{v:,.0f}".replace(",", ".") + " m²"


def ausgabe_text(treffer: list[Treffer], ziel: float) -> None:
    for i, t in enumerate(treffer, 1):
        diff = t.flaeche - ziel
        diff_txt = "" if abs(diff) < 0.5 else f"  (Δ {diff:+.0f} m²)"
        print(f"[{i}] {t.lage or '(keine Lagebezeichnung)'}")
        print(f"    Gemeinde:   {t.gemeinde} ({t.kreis})")
        print(f"    Flurstück:  Gemarkung {t.gemarkung}, Flur {t.flur}, Flurstück {t.flurstueck}")
        print(f"    Fläche:     {fmt_flaeche(t.flaeche)}{diff_txt}")
        if t.nutzung:
            print(f"    Nutzung:    {t.nutzung}")
        if t.nachbar_adressen:
            print(f"    Nachbarn:   {' | '.join(t.nachbar_adressen)}")
        if t.lat is not None:
            print(f"    Karte:      {t.osm_link}")
        print()


def schreibe_csv(pfad: str, treffer: list[Treffer]) -> None:
    with open(pfad, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(["Lage", "Fläche_m2", "Gemeinde", "Kreis", "Gemarkung", "Flur", "Flurstück",
                    "Kennzeichen", "Nutzung", "Hausnummer", "Nachbar_Adressen", "Breite", "Länge",
                    "OSM", "Google_Maps"])
        for t in treffer:
            w.writerow([t.lage, f"{t.flaeche:.0f}", t.gemeinde, t.kreis, t.gemarkung, t.flur,
                        t.flurstueck, t.kennzeichen, t.nutzung, "ja" if t.hat_hausnummer else "nein",
                        " | ".join(t.nachbar_adressen),
                        f"{t.lat:.6f}" if t.lat is not None else "",
                        f"{t.lon:.6f}" if t.lon is not None else "", t.osm_link, t.gmaps_link])


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:  # bei Umleitung in Datei/Pipe UTF-8 statt cp1252 schreiben
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass

    p = argparse.ArgumentParser(
        description="Findet Flurstücke/Adressen in Niedersachsen anhand Fläche (m²) und Gemeinde "
                    "(Quelle: ALKIS vereinfacht, LGLN OpenGeoData.NI, dl-de/by-2-0).")
    p.add_argument("flaeche", type=float, help="amtliche Fläche in m²")
    p.add_argument("gemeinde", nargs="?", help="Gemeindename, z. B. 'Wedemark' oder 'Celle' "
                   "(keine Samtgemeinde – dann Mitgliedsgemeinde oder --kreis angeben)")
    p.add_argument("-t", "--toleranz", type=float, default=0,
                   help="erlaubte Abweichung in m² (Standard 0 = exakt)")
    p.add_argument("-k", "--kreis", help="Landkreis (zur Eindeutigkeit, oder ohne Gemeinde: "
                   "ganzen Kreis durchsuchen)")
    p.add_argument("-g", "--gemarkung", help="nur diese Gemarkung (Teilstring)")
    p.add_argument("--ags", help="amtlicher Gemeindeschlüssel (8-stellig) statt Gemeindename")
    p.add_argument("-n", "--nutzung", help="Filter auf tatsächliche Nutzung, z. B. 'Wohnbau', "
                   "'Landwirtschaft', 'Wald' (Teilstring)")
    p.add_argument("-a", "--nur-adressen", action="store_true",
                   help="nur Flurstücke mit Hausnummer in der Lagebezeichnung")
    p.add_argument("--nachbarn", action="store_true",
                   help="für Flurstücke ohne Hausnummer die Adressen der Nachbarflurstücke ermitteln")
    p.add_argument("--csv", metavar="DATEI", help="Ergebnisse zusätzlich als CSV speichern")
    p.add_argument("--json", action="store_true", help="Ausgabe als JSON")
    p.add_argument("--max", type=int, default=50, help="max. angezeigte Treffer (Standard 50)")
    args = p.parse_args(argv)

    if not (args.gemeinde or args.ags or args.kreis):
        p.error("Gemeinde, --ags oder --kreis angeben.")

    log = (lambda *a: print(*a, file=sys.stderr))

    gemeinden: list[Gemeinde] | None = None
    if args.ags:
        gemeinden = [Gemeinde(args.ags, args.ags, "")]
    elif args.gemeinde:
        name = args.gemeinde.strip()
        praefix = re.match(r"^(Samtgemeinde|SG|Stadt|Hansestadt|Gemeinde|Flecken)\s+", name, re.I)
        if praefix:
            name = name[praefix.end():]
            if praefix.group(1).casefold() in ("samtgemeinde", "sg"):
                log(f"Hinweis: Samtgemeinden sind im ALKIS-Dienst nicht geführt – es wird nur die "
                    f"Gemeinde '{name}' durchsucht. Für alle Mitgliedsgemeinden: --kreis verwenden.")
        gemeinden = finde_gemeinden(name, args.kreis)
        if not gemeinden:
            log(f"Keine Gemeinde '{args.gemeinde}' gefunden"
                + (f" im Kreis '{args.kreis}'" if args.kreis else "") + ".")
            log("Hinweis: Samtgemeinden sind im Dienst nicht als Gemeinde geführt – nutze den Namen "
                "der Mitgliedsgemeinde oder durchsuche mit --kreis den ganzen Landkreis.")
            return 2
        if len(gemeinden) > 10:
            log(f"'{args.gemeinde}' ist mehrdeutig ({len(gemeinden)} Gemeinden), bitte genauer:")
            for g in gemeinden:
                log(f"  {g.name}  [{g.ags}]  – {g.kreis}")
            return 2
        log("Gemeinde(n): " + "; ".join(f"{g.name} [{g.ags}, {g.kreis}]" for g in gemeinden))
    else:
        log(f"Durchsuche Landkreis: {args.kreis}")

    log(f"Suche Flurstücke mit {fmt_flaeche(args.flaeche)}"
        + (f" ± {args.toleranz:g} m²" if args.toleranz else "") + " …")
    treffer = suche_flurstuecke(args.flaeche, args.toleranz, gemeinden,
                                args.kreis if not gemeinden else None, args.gemarkung)

    if args.nutzung:
        treffer = [t for t in treffer if args.nutzung.casefold() in t.nutzung.casefold()]
    if args.nur_adressen:
        treffer = [t for t in treffer if t.hat_hausnummer]

    # Sortierung: exakte Fläche zuerst, dann Flurstücke mit Hausnummer
    treffer.sort(key=lambda t: (abs(t.flaeche - args.flaeche), not t.hat_hausnummer,
                                t.gemeinde, t.gemarkung, t.lage))

    gesamt = len(treffer)
    log(f"{gesamt} Treffer"
        + (f" (davon {sum(t.hat_hausnummer for t in treffer)} mit Hausnummer)" if gesamt else "")
        + ".\n")

    if args.nachbarn:
        ohne = [t for t in treffer[:args.max] if not t.hat_hausnummer]
        if ohne:
            log(f"Ermittle Nachbaradressen für {len(ohne)} Flurstück(e) ohne Hausnummer …\n")
        for t in ohne:
            ergaenze_nachbarn(t)

    if args.csv:
        schreibe_csv(args.csv, treffer)
        log(f"CSV gespeichert: {args.csv}\n")

    anzeigen = treffer[:args.max]
    if args.json:
        out = []
        for t in anzeigen:
            d = asdict(t)
            d.pop("bbox", None)
            d["osm_link"] = t.osm_link
            out.append(d)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        sys.stdout.flush()
    else:
        ausgabe_text(anzeigen, args.flaeche)
        sys.stdout.flush()
        if gesamt > args.max:
            log(f"… {gesamt - args.max} weitere Treffer nicht angezeigt (--max erhöhen oder --csv nutzen).")

    log("Datenquelle: © LGLN, ALKIS vereinfacht (OpenGeoData.NI), Lizenz dl-de/by-2-0")
    return 0 if gesamt else 1


if __name__ == "__main__":
    sys.exit(main())
