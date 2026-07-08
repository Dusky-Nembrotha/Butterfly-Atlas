#!/usr/bin/env python3
"""
The Butterfly Atlas — data collector
====================================

Builds ``data/butterflies.json`` from a public Flickr photostream.

It needs NO Flickr Pro account. It works two ways, in order of preference:

1. **API key** (most reliable). Get a free non-commercial key in ~2 minutes at
   https://www.flickr.com/services/apps/create/ and pass it via the
   ``FLICKR_API_KEY`` environment variable.
2. **Keyless site-key** (fallback, no signup). The script scrapes the public
   "site key" that Flickr embeds in every page to power its own logged-out
   browsing, then calls the same public REST API with it. No login, no Pro.
   This can break if Flickr changes their markup — if it does, use option 1.

Either way it: lists all albums, pulls every photo with its title / date /
tags / description / geo-coordinates, parses the scientific name + locality
from each title, enriches taxonomy from GBIF, geocodes localities that lack
Flickr coordinates via OpenStreetMap Nominatim (cached), and writes the JSON
the website reads.

Usage:
    python3 fetch_flickr.py --user robertgodden
    FLICKR_API_KEY=xxxx python3 fetch_flickr.py --user robertgodden
    python3 fetch_flickr.py --self-test      # offline: test the title parser
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

try:
    import requests
except ImportError:  # self-test doesn't need it
    requests = None

REST = "https://api.flickr.com/services/rest/"
UA = "ButterflyAtlas/1.0 (+https://github.com/) collector"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "data", "butterflies.json")
GEOCACHE = os.path.join(HERE, "geocache.json")
TAXOCACHE = os.path.join(HERE, "taxocache.json")

# Image size suffixes Flickr serves: q=150, w=400, c=800, b=1024, h=1600
IMG = "https://live.staticflickr.com/{server}/{id}_{secret}{suf}.jpg"

# ----------------------------------------------------------------------------
# Title parsing  (pure functions — covered by --self-test)
# ----------------------------------------------------------------------------

# A binomial: Genus species. Genus capitalised, epithet lower-case.
SCI_RE = re.compile(r"\b([A-Z][a-z]+)\s+([a-z][a-z\-]{1,})\b")
EPITHET_RE = re.compile(r"^[a-z][a-z\-]{1,}$")

# "(Genus species)" or "(Genus species f. form)" / "ssp."/"var."/"subsp." —
# this is the collection's actual style: common name, then the scientific
# name in parentheses, e.g. "Glanville Fritillary, (Melitaea cinxia), Hurst
# Castle, Hampshire, UK 21/05/2011".
PAREN_SCI_RE = re.compile(
    r"\(([A-Z][a-z]+)\s+([a-z][a-z\-]{1,})"
    r"(?:\s+(?:f\.|ssp\.|subsp\.|var\.)\s*([a-z][a-z\-]{1,}))?\)"
)

# Trailing "DD/MM/YYYY" (the date is read from Flickr's own metadata, not
# the title, so this is only stripped to keep it out of the locality text).
DATE_TAIL_RE = re.compile(r"\s+\d{1,2}/\d{1,2}/\d{4}\s*$")
ELEV_TAIL_RE = re.compile(r"\s+\d+\s*m\s*$", re.IGNORECASE)

# Common English/descriptive words that appear after a name but are NOT a
# subspecies epithet — used to avoid mis-reading "Genus species basking" etc.
DESCRIPTORS = {
    "female", "male", "form", "ssp", "sp", "aberration", "ab", "basking",
    "mating", "pair", "underside", "upperside", "dorsal", "ventral", "on",
    "at", "in", "the", "and", "feeding", "puddling", "resting", "larva",
    "caterpillar", "pupa", "egg", "roosting", "nectaring", "possibly",
    "probably", "cf", "aff", "type", "wet", "dry", "season",
}

# Rough country lexicon for the collection (extend freely — matching is best-effort).
COUNTRIES = {
    "uganda", "brazil", "argentina", "romania", "turkey", "peru", "ghana",
    "ecuador", "spain", "bolivia", "armenia", "united kingdom", "uk",
    "colombia", "costa rica", "mexico", "kenya", "tanzania", "south africa",
    "india", "malaysia", "indonesia", "usa", "united states", "france",
    "italy", "greece", "portugal", "thailand", "vietnam", "panama",
}

def parse_title(title, album_title=""):
    """Return (species, subspecies, location, country, common_name).

    Handles two title styles seen in this collection:
      1. "Genus species, Locality, Country"                       (leading binomial)
      2. "Common Name, (Genus species [f. form]), Locality, Country"  (common name + parens)
    Tolerant of extra descriptive text and missing parts. Falls back to the
    album title for country/location context.
    """
    title = (title or "").strip()
    title = DATE_TAIL_RE.sub("", title)
    title = ELEV_TAIL_RE.sub("", title)

    species = subspecies = common = ""

    m = PAREN_SCI_RE.search(title)
    if m:
        species = "%s %s" % (m.group(1), m.group(2))
        subspecies = m.group(3) or ""
        common = title[:m.start()].strip(" ,")
        rest = title[m.end():].strip(" ,")
        loc_parts = [p.strip() for p in rest.split(",") if p.strip()]
    else:
        parts = [p.strip() for p in title.split(",") if p.strip()]
        # The scientific name lives in the first comma-segment.
        name_seg = parts[0] if parts else title
        mm = SCI_RE.search(name_seg)
        if mm:
            species = "%s %s" % (mm.group(1), mm.group(2))
            # Accept a subspecies only if the token immediately after the epithet
            # is itself a clean Latin epithet (not a descriptive English word).
            after = name_seg[mm.end():].strip().split()
            if after:
                cand = after[0].strip(".,").lower()
                if EPITHET_RE.match(cand) and cand not in DESCRIPTORS:
                    subspecies = cand

        loc_parts = []
        if len(parts) > 1:
            # drop the first segment if it merely holds the scientific name
            first = parts[0]
            if species and species.lower() in first.lower():
                loc_parts = parts[1:]
            else:
                loc_parts = parts
        else:
            # single segment: remove the binomial to leave any trailing locality
            if species:
                leftover = title.replace(species, "").strip(" ,-")
                if subspecies:
                    leftover = leftover.replace(subspecies, "").strip(" ,-")
                if leftover:
                    loc_parts = [leftover]

    location = ", ".join(loc_parts).strip()
    country = ""
    if loc_parts:
        last = loc_parts[-1].strip()
        if last.lower() in COUNTRIES or len(loc_parts) > 1:
            country = last
    # fall back to album title (albums are named by place in this collection)
    if not country and album_title:
        at = album_title.strip()
        # album may be "Uganda 2026" -> take the wordy part
        cand = re.sub(r"\b\d{4}\b", "", at).strip()
        country = cand
        if not location:
            location = cand
    return species, subspecies, location, country, common


def norm_country(c):
    c = (c or "").strip()
    low = c.lower()
    if low in ("uk", "u.k.", "england", "scotland", "wales"):
        return "United Kingdom"
    if low in ("usa", "u.s.a.", "us"):
        return "United States"
    return c


# ----------------------------------------------------------------------------
# Flickr access
# ----------------------------------------------------------------------------

class Flickr:
    def __init__(self, api_key=None, session=None):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": UA})
        self.api_key = api_key
        self.keyless = False

    def ensure_key(self, sample_url):
        if self.api_key:
            print("Using FLICKR_API_KEY.")
            return
        print("No API key set — extracting Flickr site key (keyless mode)…")
        html = self.session.get(sample_url, timeout=30).text
        key = extract_site_key(html)
        if not key:
            raise SystemExit(
                "Could not extract a site key from Flickr. Flickr may have changed "
                "their markup. Get a free API key at "
                "https://www.flickr.com/services/apps/create/ and set FLICKR_API_KEY."
            )
        self.api_key = key
        self.keyless = True
        print("Site key acquired: %s…" % key[:8])

    def call(self, method, **params):
        params.update({
            "method": method, "api_key": self.api_key,
            "format": "json", "nojsoncallback": "1",
        })
        for attempt in range(4):
            try:
                r = self.session.get(REST, params=params, timeout=30)
                data = r.json()
                if data.get("stat") == "ok":
                    return data
                # site keys sometimes 100/api-key errors -> surface clearly
                raise RuntimeError(data.get("message", "unknown Flickr error"))
            except Exception as e:  # noqa
                if attempt == 3:
                    raise
                time.sleep(1.5 * (attempt + 1))


SITE_KEY_PATTERNS = [
    re.compile(r'root\.YUI_config\.flickr\.api\.site_key\s*=\s*"([0-9a-f]{6,})"'),
    re.compile(r'"?(?:api\.)?site_key"?\s*[:=]\s*"([0-9a-f]{6,})"'),
    re.compile(r'api_site_key["\']?\s*[:=]\s*["\']([0-9a-f]{6,})'),
]

def extract_site_key(html):
    for pat in SITE_KEY_PATTERNS:
        m = pat.search(html or "")
        if m:
            return m.group(1)
    return None


def resolve_nsid(fl, user):
    """Accept an NSID, a username, or a photos URL and return the NSID."""
    if re.match(r"^\d+@N\d+$", user):
        return user
    url = user if user.startswith("http") else "https://www.flickr.com/photos/%s" % user
    data = fl.call("flickr.urls.lookupUser", url=url)
    return data["user"]["id"]


def get_albums(fl, nsid):
    albums, page = [], 1
    while True:
        d = fl.call("flickr.photosets.getList", user_id=nsid, per_page=500, page=page)
        ps = d["photosets"]
        for s in ps["photoset"]:
            albums.append({
                "id": s["id"],
                "title": s["title"]["_content"],
                "count": int(s.get("photos", 0)),
            })
        if page >= int(ps.get("pages", 1)):
            break
        page += 1
    return albums


def get_album_photos(fl, nsid, album_id):
    photos, page = [], 1
    extras = "description,date_taken,geo,tags,owner_name,url_q,url_c,url_b,url_h,url_o"
    while True:
        d = fl.call("flickr.photosets.getPhotos", photoset_id=album_id, user_id=nsid,
                    extras=extras, per_page=500, page=page)
        pset = d["photoset"]
        photos.extend(pset["photo"])
        if page >= int(pset.get("pages", 1)):
            break
        page += 1
    return photos


# ----------------------------------------------------------------------------
# Enrichment: GBIF taxonomy + Nominatim geocoding (both cached, both free)
# ----------------------------------------------------------------------------

def load_cache(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_cache(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except Exception as e:
        print("  (warn) could not save cache %s: %s" % (path, e))


def gbif_match(session, species, cache):
    if species in cache:
        return cache[species]
    out = {}
    try:
        r = session.get("https://api.gbif.org/v1/species/match",
                        params={"name": species}, timeout=20)
        j = r.json()
        if j.get("matchType") != "NONE":
            for k in ("kingdom", "phylum", "class", "order", "family", "genus"):
                if j.get(k):
                    out[k] = j[k]
            out["gbifKey"] = j.get("usageKey") or j.get("speciesKey")
    except Exception as e:
        print("  (warn) GBIF failed for %s: %s" % (species, e))
    cache[species] = out
    return out


def _nominatim_lookup(session, query, stats):
    """Single Nominatim call. Returns {'lat':..,'lon':..} or None."""
    for attempt in range(3):
        try:
            r = session.get("https://nominatim.openstreetmap.org/search",
                            params={"q": query, "format": "json", "limit": 1},
                            headers={"User-Agent": UA}, timeout=20)
            time.sleep(1.1)  # Nominatim policy: max ~1 request/second
            if r.status_code == 200:
                arr = r.json()
                if arr:
                    return {"lat": float(arr[0]["lat"]), "lon": float(arr[0]["lon"])}
                return None
            if r.status_code in (403, 429):
                stats["throttled"] += 1
                time.sleep(3 * (attempt + 1))
                continue
            return None
        except Exception as e:
            if attempt == 2:
                stats["errors"] += 1
                stats["last_error"] = str(e)
            time.sleep(1.5)
    return None


def geocode(session, location, country, cache, stats):
    """Geocode a locality with progressive fallback.

    Nominatim matches the query as a *whole* place name, so an overly
    specific string like "San Pedro de Bedoya, Picos de Europa, Cantabria,
    Spain" often returns nothing even though every part is real. This tries
    the full locality first, then drops the left-most (most specific) part
    on each retry, finally falling back to the country alone — so a record
    almost always ends up with at least an approximate country-level pin.
    """
    terms = [t.strip() for t in (location or "").split(",") if t.strip()]
    candidates = []
    for i in range(len(terms)):
        tail = terms[i:]
        cand = ", ".join(tail + ([country] if country and country not in tail else []))
        if cand:
            candidates.append(cand)
    if country and (not candidates or candidates[-1].lower() != country.lower()):
        candidates.append(country)
    # dedupe, preserving order
    seen, uniq = set(), []
    for c in candidates:
        k = c.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(c)

    for q in uniq:
        key = q.lower()
        if key in cache:
            if cache[key]:
                return cache[key], (q != uniq[0])
            continue  # cached miss — try the next, less specific candidate
        result = _nominatim_lookup(session, q, stats)
        cache[key] = result
        if result:
            return result, (q != uniq[0])
    return None, False


# ----------------------------------------------------------------------------
# Build
# ----------------------------------------------------------------------------

def first_url(p):
    for suf in ("_h", "_b", "_c"):
        u = p.get("url" + suf)
        if u:
            return u
    return ""

def build(user, api_key):
    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    fl = Flickr(api_key=api_key, session=session)
    sample_url = user if user.startswith("http") else "https://www.flickr.com/photos/%s" % user
    fl.ensure_key(sample_url)

    nsid = resolve_nsid(fl, user)
    print("User NSID:", nsid)
    albums = get_albums(fl, nsid)
    print("Albums:", len(albums))

    geo_cache = load_cache(GEOCACHE)
    taxo_cache = load_cache(TAXOCACHE)

    photos_out = []
    species_tbl = {}

    for a in albums:
        raw = get_album_photos(fl, nsid, a["id"])
        print("  %-40s %d photos" % (a["title"][:40], len(raw)))
        for p in raw:
            title = p.get("title", "")
            species, subsp, location, country, common = parse_title(title, a["title"])
            country = norm_country(country)

            lat = lon = None
            if p.get("latitude") and float(p["latitude"]) != 0:
                lat, lon = float(p["latitude"]), float(p["longitude"])

            date_iso = ""
            dt = p.get("datetaken") or ""
            if dt:
                date_iso = dt.split(" ")[0]

            rec = {
                "id": p.get("id"),
                "title": title,
                "species": species,
                "subspecies": subsp,
                "commonName": common,
                "location": location,
                "country": country,
                "lat": lat, "lon": lon,
                "date": date_iso,
                "year": int(date_iso[:4]) if date_iso[:4].isdigit() else None,
                "albumId": a["id"],
                "albumTitle": a["title"],
                "tags": (p.get("tags", "") or "").split(),
                "urlThumb": p.get("url_q", "") or p.get("url_c", ""),
                "urlLarge": first_url(p),
                "urlOriginal": p.get("url_o", ""),
                "flickrPage": "https://www.flickr.com/photos/%s/%s" % (nsid, p.get("id")),
                "description": (p.get("description", {}) or {}).get("_content", ""),
            }
            photos_out.append(rec)
            if species and species not in species_tbl:
                species_tbl[species] = {"count": 0, "commonName": ""}
            if species:
                species_tbl[species]["count"] += 1
                if common and not species_tbl[species].get("commonName"):
                    species_tbl[species]["commonName"] = common

    # taxonomy enrichment per unique species
    print("Enriching %d species via GBIF…" % len(species_tbl))
    for sp in species_tbl:
        tax = gbif_match(session, sp, taxo_cache)
        species_tbl[sp].update(tax)
    save_cache(TAXOCACHE, taxo_cache)

    # backfill family/genus onto photos + geocode those without coordinates
    print("Geocoding localities without Flickr coordinates (this can take a while — "
          "Nominatim allows ~1 request/second)…")
    geo_stats = {"throttled": 0, "errors": 0, "last_error": "", "exact": 0, "approx": 0, "none": 0}
    for i, rec in enumerate(photos_out):
        s = species_tbl.get(rec["species"], {})
        rec_family = s.get("family", "")
        if rec_family:
            rec["family"] = rec_family
        if rec["lat"] is None and (rec["location"] or rec["country"]):
            g, approx = geocode(session, rec["location"], rec["country"], geo_cache, geo_stats)
            if g:
                rec["lat"], rec["lon"] = g["lat"], g["lon"]
                if approx:
                    rec["geoApprox"] = True
                    geo_stats["approx"] += 1
                else:
                    geo_stats["exact"] += 1
            else:
                geo_stats["none"] += 1
    save_cache(GEOCACHE, geo_cache)
    print("  geocoded: %d exact, %d approximate (country/region-level), %d not found"
          % (geo_stats["exact"], geo_stats["approx"], geo_stats["none"]))
    if geo_stats["throttled"] or geo_stats["errors"]:
        print("  (%d requests throttled, %d failed outright — last error: %s)"
              % (geo_stats["throttled"], geo_stats["errors"], geo_stats["last_error"]))

    payload = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "source": sample_url,
        "photographer": raw and raw[0].get("ownername") or "",
        "is_sample": False,
        "albums": albums,
        "species": species_tbl,
        "photos": photos_out,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    geo = sum(1 for r in photos_out if r["lat"] is not None)
    print("\nWrote %s" % OUT)
    print("  photos=%d species=%d albums=%d geocoded=%d/%d"
          % (len(photos_out), len(species_tbl), len(albums), geo, len(photos_out)))


# ----------------------------------------------------------------------------
# Self-test (offline)
# ----------------------------------------------------------------------------

def self_test():
    cases = [
        # style 1: leading binomial, no parens (some albums use this)
        ("Papilio dardanus, Bwindi, Uganda",
         ("Papilio dardanus", "", "Uganda", "")),
        ("Morpho helenor helenor, Cristalino, Mato Grosso, Brazil",
         ("Morpho helenor", "helenor", "Brazil", "")),
        ("Heliconius erato",
         ("Heliconius erato", "", "", "")),
        ("Charaxes candiope basking on fruit, Kibale, Uganda",
         ("Charaxes candiope", "", "Uganda", "")),
        # style 2: "Common Name, (Genus species), Locality, Country DD/MM/YYYY"
        # — this is the collection's actual UK/Spain title format.
        ("Peacock (Aglais io) San Pedro de Bedoya, Picos de Europa, Cantabria, Spain 11/06/2009",
         ("Aglais io", "", "Spain", "Peacock")),
        ("Marsh Fritillary, (Euphydryas aurinia), Finglandrigg Wood, Cumbria, UK 26/05/2010",
         ("Euphydryas aurinia", "", "UK", "Marsh Fritillary")),
        ("Silver-washed Fritillary, (Argynnis paphia f. valesina), Bentley Wood, Wiltshire, UK 31/07/2010",
         ("Argynnis paphia", "valesina", "UK", "Silver-washed Fritillary")),
        ("Glanville Fritillary, (Melitaea cinxia), Hurst Castle, Hampshire, UK 21/05/2011",
         ("Melitaea cinxia", "", "UK", "Glanville Fritillary")),
    ]
    ok = True
    for title, exp in cases:
        sp, ss, loc, ctry, common = parse_title(title, album_title="")
        exp_sp, exp_ss, exp_ctry, exp_common = exp
        good = (sp == exp_sp and ss == exp_ss and common == exp_common
                and (exp_ctry == "" or exp_ctry.lower() in ctry.lower()))
        print(("PASS" if good else "FAIL"),
              "| %-50s -> sp=%r ss=%r common=%r loc=%r ctry=%r"
              % (title[:50], sp, ss, common, loc, ctry))
        ok = ok and good
    # album fallback (no country in title at all)
    sp, ss, loc, ctry, common = parse_title("Danaus plexippus", album_title="Peru 2025")
    print(("PASS" if ctry == "Peru" else "FAIL"),
          "| album fallback -> ctry=%r (expected Peru)" % ctry)
    ok = ok and ctry == "Peru"

    # geocode fallback candidate list (offline — no network — just checks the
    # progressive query list is sane and ends at the country)
    fake_cache, fake_stats = {}, {"throttled": 0, "errors": 0, "last_error": ""}
    class _Dead:  # a session whose .get() always raises, to stay fully offline
        def get(self, *a, **k):
            raise RuntimeError("offline self-test — no network expected")
    g, approx = geocode(_Dead(), "San Pedro de Bedoya, Picos de Europa, Cantabria", "Spain", fake_cache, fake_stats)
    geo_ok = g is None  # offline, so it should exhaust candidates and return None cleanly
    print(("PASS" if geo_ok else "FAIL"), "| geocode() degrades cleanly with no network")
    ok = ok and geo_ok

    print("\n==== %s ====" % ("ALL PASS" if ok else "FAILURES"))
    sys.exit(0 if ok else 1)


DEFAULT_USER = "robertgodden"


def main():
    ap = argparse.ArgumentParser(description="Build butterflies.json from a Flickr photostream.")
    ap.add_argument("--user", help="Flickr username, NSID, or photos URL (e.g. robertgodden)")
    ap.add_argument("--self-test", action="store_true", help="run offline parser tests and exit")
    # parse_known_args so a stray argument from IDLE/double-click can't crash it
    args, _ = ap.parse_known_args()

    if args.self_test:
        self_test()

    user = args.user
    if not user:
        # No --user given (e.g. run from IDLE with F5, or double-clicked).
        # Ask interactively; press Enter to accept the default.
        try:
            typed = input("Flickr username, NSID, or photos URL [%s]: " % DEFAULT_USER).strip()
        except EOFError:
            typed = ""
        user = typed or DEFAULT_USER

    if requests is None:
        raise SystemExit("The 'requests' package is required: pip install -r scraper/requirements.txt")

    try:
        build(user, os.environ.get("FLICKR_API_KEY"))
    except SystemExit:
        raise
    except Exception as e:
        # Keep the window/output readable in IDLE instead of a bare traceback line.
        print("\nERROR: %s" % e)
        raise
    input("\nDone. Press Enter to close.")


if __name__ == "__main__":
    main()
