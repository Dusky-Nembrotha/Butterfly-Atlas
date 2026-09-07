#!/usr/bin/env python3
"""
Butterfly Atlas — locality re-geocoder
======================================

Re-geocodes the localities in data/butterflies.json *in place*, without
re-scraping Flickr. Only unique localities are looked up (a few hundred
rather than a few thousand photos), so a full pass takes minutes, not hours.

Why the original geocoding failed on things like
"Yanachaga Chemillen NP, Pasco, Peru":

  1. Abbreviations. Nominatim indexes "National Park", not "NP". A query
     containing "NP" frequently matches nothing at all.
  2. Free-form only. One long comma string has to match as a single place.
     Nominatim's *structured* query (city / county / country as separate
     fields) is far more forgiving.
  3. One provider. If Nominatim has no entry (or was rate-limiting during
     the original run) there was no second opinion.
  4. Accents. "Chemillen" vs "Chemillén" can be the difference between a
     hit and a miss, so both are tried.

This script addresses all four: it expands abbreviations, tries structured
then free-form Nominatim, then falls back to Photon (also free, also
OpenStreetMap-based, no API key, and much better at fuzzy/partial names),
and only then starts dropping detail.

Usage:
    python3 fix_geocode.py                 # re-do only approximate/missing ones
    python3 fix_geocode.py --all           # re-do every locality from scratch
    python3 fix_geocode.py --self-test     # offline checks, no network
    python3 fix_geocode.py --dry-run       # report what would change, write nothing
"""

import argparse
import json
import os
import re
import sys
import time
import unicodedata

try:
    import requests
except ImportError:
    requests = None

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data", "butterflies.json")
CACHE = os.path.join(HERE, "geocache2.json")
UA = "ButterflyAtlas/1.0 (locality re-geocoder; contact via github)"

MAX_ATTEMPTS = 6
NOMINATIM = "https://nominatim.openstreetmap.org/search"
PHOTON = "https://photon.komoot.io/api"

# ---------------------------------------------------------------------------
# Query normalisation (pure functions — covered by --self-test)
# ---------------------------------------------------------------------------

# Abbreviations that appear in this collection's locality strings. Nominatim
# and Photon both index the expanded forms, so expanding these is the single
# biggest win available.
ABBREV = [
    (r"\bN\.?\s?P\.?\b", "National Park"),
    (r"\bN\.?\s?R\.?\b", "Nature Reserve"),
    (r"\bN\.?N\.?R\.?\b", "National Nature Reserve"),
    (r"\bW\.?\s?R\.?\b", "Wildlife Reserve"),
    (r"\bG\.?\s?R\.?\b", "Game Reserve"),
    (r"\bF\.?\s?R\.?\b", "Forest Reserve"),
    (r"\bP\.?\s?N\.?\b", "Parque Nacional"),
    (r"\bR\.?\s?N\.?\b", "Reserva Nacional"),
    (r"\bMt\.?\b", "Mount"),
    (r"\bMts\.?\b", "Mountains"),
    (r"\bSt\.?\b", "Saint"),
    (r"\bIs\.?\b", "Island"),
    (r"\bProv\.?\b", "Province"),
    (r"\bDept\.?\b", "Department"),
    (r"\bNr\.?\b", "near"),
]

# Words that describe a position rather than name a place. Keeping them in a
# query usually guarantees a miss ("East slopes of Andes near Satipo").
TAXON_PREFIX_RE = re.compile(
    r"^(?:"
    r"[A-Z][a-z]+\s+sp\.?"          # "Acraea sp" / "Euphaedra sp."
    r"|[A-Z][a-z]+(?:idae|inae)\s+sp\.?"   # "Nymphalidae sp"
    r"|[A-Z]\.\s*[a-z\-]+"          # "P. tringa"
    r"|[A-Z][a-z]+\s+spp\.?"
    r"|eggs?|larva[e]?|caterpillars?"
    r")$", re.IGNORECASE)

NOISE = [
    r"\beast slopes? of\b", r"\bwest slopes? of\b", r"\bnorth slopes? of\b",
    r"\bsouth slopes? of\b", r"\bslopes? of\b", r"\bnear\b", r"\babove\b",
    r"\bbelow\b", r"\bbetween\b", r"\broad to\b", r"\bkm from\b", r"\btrack to\b",
    r"\bvalley of\b", r"\boutskirts of\b",
]


def expand_abbreviations(text):
    """NP -> National Park, Mt -> Mount, etc."""
    out = text or ""
    for pattern, replacement in ABBREV:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", out).strip()


def strip_accents(text):
    """Chemillén -> Chemillen (tried as an alternate spelling)."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def strip_noise(text):
    """Remove positional phrasing that stops a locality matching."""
    out = text or ""
    for pattern in NOISE:
        out = re.sub(pattern, " ", out, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", out).strip(" ,-")


def clean_locality(text):
    """Strip common-name pollution that leaked in from title parsing.

    The collector mis-split some Flickr titles, leaving the butterfly's
    COMMON NAME at the front of the locality field, e.g.:
        "(Abadima Acraea, Kakum NP, Ghana"   -> "Kakum NP, Ghana"
        "(African Beak) Murchison Falls NP"  -> "Murchison Falls NP"
        "& C. myrmidone, Apuseni Hills"      -> "Apuseni Hills"
    Geocoding the common name as if it were a place is why so many of these
    failed. Strings that are already clean pass through unchanged.
    """
    s = (text or "").strip()
    if ")" in s:                       # "(Common name) Real Place"
        s = s[s.rindex(")") + 1:]
    elif s.startswith("(") or s.startswith("&"):
        parts = [p.strip() for p in s.split(",")]
        if len(parts) > 1:
            s = ", ".join(parts[1:])

    # Drop a leading term that is really a TAXON, not a place. Seen as
    # "Acraea sp, Ankasa NP", "Euphaedra sp., Kyabobo NP", "P. tringa,
    # Yanachaga...", "Nymphalidae sp, Regua". Also drops stray prefixes
    # like "eggs," that came from the title rather than the locality.
    parts = [p.strip() for p in s.split(",")]
    while len(parts) > 1 and TAXON_PREFIX_RE.match(parts[0]):
        parts = parts[1:]
    s = ", ".join(parts)

    # "Coroico - Sol y Luna" behaves like a comma-separated pair
    s = re.sub(r"\s+[-\u2013\u2014]\s+", ", ", s)
    # trailing designations that stop an exact match ("Collard Hill NT")
    s = ", ".join(
        re.sub(r"\s+\b(NT|CP|RDA|LNR)\b\s*$", "", t, flags=re.IGNORECASE).strip()
        for t in s.split(",")
    )
    # unbalanced junk: "Bwindi (high altitude", "Kibale 25.11.2017 (1"
    s = re.sub(r"\(.*$", "", s)
    s = re.sub(r"\b\d{1,2}\.\d{1,2}\.\d{4}\b", "", s)
    return re.sub(r"\s+", " ", s).strip(" ,-\u2013\u2014")


def prefer_after_near(text):
    """"East slopes of Andes near Satipo" -> "Satipo".

    When a locality is described relative to somewhere else, the place AFTER
    "near" is the identifiable one. This affects the single largest group in
    the collection (177 photos), so it is worth handling explicitly."""
    m = re.split(r"\bnear\b", text or "", flags=re.IGNORECASE)
    if len(m) == 2 and m[1].strip():
        return m[1].strip(" ,-")
    return None


def route_endpoints(term):
    """"Yendi to Kyabobo NP" -> ["Yendi to Kyabobo NP", "Kyabobo NP", "Yendi"].

    Several localities describe a journey rather than a point. Neither end is
    wrong, so both are offered as fallbacks after the full string."""
    out = [term]
    m = re.split(r"\s+to\s+", term, flags=re.IGNORECASE)
    if len(m) == 2:
        dest = re.sub(r"\b(road|track|trail)\b", "", m[1], flags=re.IGNORECASE).strip(" ,-")
        start = m[0].strip(" ,-")
        for part in (dest, start):
            if part and part not in out:
                out.append(part)
    return out


def locality_terms(location):
    """Split a locality string into its comma-separated parts, cleaned."""
    parts = [p.strip() for p in (location or "").split(",")]
    return [p for p in parts if p]


def build_queries(location, country):
    """Ordered list of (kind, payload) attempts for one locality.

    kind is 'structured' (dict of Nominatim fields), or 'free' (a string).
    Ordered most-specific first; the caller stops at the first hit and marks
    anything after the first entry as approximate.
    """
    attempts = []
    loc_raw = clean_locality(location)   # strip common-name pollution first
    ctry = (country or "").strip()

    expanded = expand_abbreviations(loc_raw)
    cleaned = strip_noise(expanded)
    variants = []
    for v in (expanded, cleaned):
        if v and v not in variants:
            variants.append(v)
        plain = strip_accents(v)
        if plain and plain not in variants:
            variants.append(plain)

    for v in variants:
        terms = locality_terms(v)
        if not terms:
            continue
        # structured: first term is the place, last is usually the region
        structured = {"country": ctry} if ctry else {}
        # Only pass a 'state' when the last term is genuinely a region — if it
        # merely repeats the country, sending it guarantees a miss.
        if len(terms) >= 2 and terms[-1].strip().lower() != ctry.strip().lower():
            attempts.append(("structured", dict(structured, city=terms[0], state=terms[-1])))
        attempts.append(("structured", dict(structured, city=terms[0])))
        # free-form: whole thing, then progressively drop the trailing region
        full = ", ".join(terms + ([ctry] if ctry and ctry not in terms else []))
        attempts.append(("free", full))
        if len(terms) > 1:
            attempts.append(("free", ", ".join([terms[0]] + ([ctry] if ctry else []))))

    # journey-style localities: try each endpoint too
    first_terms = locality_terms(variants[0]) if variants else []
    if first_terms:
        for alt in route_endpoints(first_terms[0])[1:]:
            attempts.append(("free", ", ".join([alt] + ([ctry] if ctry else []))))

    if ctry:
        attempts.append(("free", ctry))

    # de-duplicate, preserving order
    seen, uniq = set(), []
    for kind, payload in attempts:
        key = (kind, json.dumps(payload, sort_keys=True) if isinstance(payload, dict) else payload)
        if key not in seen:
            seen.add(key)
            uniq.append((kind, payload))
    # Cap the cascade: beyond this the queries are too vague to be useful, and
    # 443 localities x 1.1s per request adds up fast. The country-only entry is
    # always kept as the final fallback, even when the cap trims the middle.
    country_only = ("free", ctry) if ctry else None
    if country_only and country_only in uniq:
        uniq = [a for a in uniq if a != country_only]
        return uniq[:MAX_ATTEMPTS - 1] + [country_only]
    return uniq[:MAX_ATTEMPTS]


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

class Geocoder:
    def __init__(self, session, cache, delay=1.1):
        self.s = session
        self.cache = cache
        self.delay = delay
        self.stats = {"nominatim": 0, "photon": 0, "miss": 0, "throttled": 0, "cached": 0}

    def _get(self, url, params):
        for attempt in range(3):
            try:
                r = self.s.get(url, params=params, headers={"User-Agent": UA}, timeout=25)
                time.sleep(self.delay)
                if r.status_code == 200:
                    return r.json()
                if r.status_code in (429, 403):
                    self.stats["throttled"] += 1
                    time.sleep(5 * (attempt + 1))
                    continue
                return None
            except Exception:
                time.sleep(2)
        return None

    def nominatim(self, kind, payload):
        params = {"format": "json", "limit": 1}
        if kind == "structured":
            params.update({k: v for k, v in payload.items() if v})
        else:
            params["q"] = payload
        data = self._get(NOMINATIM, params)
        if isinstance(data, list) and data:
            self.stats["nominatim"] += 1
            return {"lat": float(data[0]["lat"]), "lon": float(data[0]["lon"])}
        return None

    def photon(self, text, country):
        """Photon is OSM-based, keyless, and far more tolerant of partial or
        slightly-off names than Nominatim — the safety net for places like
        national parks that Nominatim won't match as a whole string."""
        q = text if not country or country in text else (text + ", " + country)
        data = self._get(PHOTON, {"q": q, "limit": 1})
        try:
            feats = (data or {}).get("features") or []
            if feats:
                lon, lat = feats[0]["geometry"]["coordinates"][:2]
                self.stats["photon"] += 1
                return {"lat": float(lat), "lon": float(lon)}
        except Exception:
            pass
        return None

    def resolve(self, location, country):
        """Return (result_or_None, is_approx)."""
        ckey = (location or "") + "|" + (country or "")
        if ckey in self.cache:
            self.stats["cached"] += 1
            c = self.cache[ckey]
            return (c.get("hit"), c.get("approx", False)) if c else (None, False)

        attempts = build_queries(location, country)
        # The primary place name (first term of the cleaned locality). A result
        # is only "approximate" if the query that succeeded no longer contained
        # it — merely rephrasing the SAME place (structured vs free-form,
        # accents stripped, abbreviations expanded) is still an exact location.
        cleaned = clean_locality(location)
        after_near = prefer_after_near(cleaned)
        base = after_near or strip_noise(expand_abbreviations(cleaned))
        primary = (locality_terms(expand_abbreviations(base)) or [""])[0]

        # Split attempts into those that still name the actual place, and the
        # vaguer ones. Everything precise is tried FIRST (Nominatim, then
        # Photon); only if all of that fails do we accept a vague fallback.
        def is_precise(payload):
            text = json.dumps(payload) if isinstance(payload, dict) else payload
            return bool(primary) and primary.lower() in text.lower()

        precise = [(k, p) for k, p in attempts if is_precise(p)]
        vague = [(k, p) for k, p in attempts if not is_precise(p)]

        for kind, payload in precise:
            hit = self.nominatim(kind, payload)
            if hit:
                self.cache[ckey] = {"hit": hit, "approx": False}
                return hit, False

        # Photon before falling back — it is far better at reserves, hills and
        # parks that Nominatim will not match as a whole string.
        for text in [t for t in (primary, strip_accents(primary)) if t]:
            hit = self.photon(text, country)
            if hit:
                self.cache[ckey] = {"hit": hit, "approx": False}
                return hit, False

        for kind, payload in vague:
            hit = self.nominatim(kind, payload)
            if hit:
                self.cache[ckey] = {"hit": hit, "approx": True}
                return hit, True

        # Nominatim exhausted — try Photon on the cleanest form of the name
        expanded = strip_noise(expand_abbreviations(location or ""))
        for text in [t for t in (expanded, strip_accents(expanded)) if t]:
            hit = self.photon(text, country)
            if hit:
                self.cache[ckey] = {"hit": hit, "approx": False}
                return hit, False

        self.stats["miss"] += 1
        self.cache[ckey] = {"hit": None, "approx": False}
        return None, False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def run(redo_all=False, dry_run=False):
    if requests is None:
        raise SystemExit("Install requests first:  pip install requests")
    if not os.path.exists(DATA):
        raise SystemExit("Can't find %s" % DATA)

    data = load_json(DATA)
    photos = data.get("photos", [])
    print("Loaded %d photos." % len(photos))

    cache = {}
    if os.path.exists(CACHE):
        try:
            cache = load_json(CACHE)
            print("Re-using %d cached lookups." % len(cache))
        except Exception:
            cache = {}

    # Which localities actually need work?
    targets = {}
    for p in photos:
        needs = redo_all or p.get("geoApprox") or p.get("lat") is None
        if not needs:
            continue
        # Group by the CLEANED locality so the same real place isn't looked up
        # once per differing common-name prefix (that was 443 "unique"
        # localities for far fewer actual places).
        # Some records have no country at all (several Armenian sites), which
        # leaves the query unconstrained and ambiguous. The album title names
        # the place in this collection, so use it as a fallback.
        ctry = (p.get("country") or "").strip()
        if not ctry:
            ctry = re.sub(r"\b\d{4}\b|\bButterflies\b", "", p.get("albumTitle") or "",
                          flags=re.IGNORECASE).strip(" ,-")
        key = (clean_locality(p.get("location") or ""), ctry)
        targets.setdefault(key, []).append(p)

    print("%d unique localities to resolve (covering %d photos).\n"
          % (len(targets), sum(len(v) for v in targets.values())))
    if not targets:
        print("Nothing to do.")
        return

    session = requests.Session()
    geo = Geocoder(session, cache)

    fixed = still_approx = failed = 0
    for i, ((loc, ctry), recs) in enumerate(sorted(targets.items()), 1):
        hit, approx = geo.resolve(loc, ctry)
        label = (loc or ctry or "?")[:52]
        if hit:
            status = "approx" if approx else "EXACT"
            if approx:
                still_approx += 1
            else:
                fixed += 1
            for p in recs:
                p["lat"], p["lon"] = hit["lat"], hit["lon"]
                if approx:
                    p["geoApprox"] = True
                else:
                    p.pop("geoApprox", None)
        else:
            failed += 1
            status = "MISS "
        print("[%3d/%3d] %-6s %-52s (%d photo%s)"
              % (i, len(targets), status, label, len(recs), "" if len(recs) == 1 else "s"))
        if i % 25 == 0 and not dry_run:
            save_json(CACHE, cache)

    if not dry_run:
        save_json(CACHE, cache)
        save_json(DATA, data)

    print("\n%s" % ("DRY RUN — nothing written." if dry_run else "Written to %s" % DATA))
    print("  exact matches now: %d localities" % fixed)
    print("  still approximate: %d" % still_approx)
    print("  no match at all  : %d" % failed)
    print("  provider: %d via Nominatim, %d via Photon, %d cached, %d throttled"
          % (geo.stats["nominatim"], geo.stats["photon"], geo.stats["cached"], geo.stats["throttled"]))
    if not dry_run:
        print("\nNow deploy the updated data file:")
        print("  cd ~/Butterfly-Atlas && git add data/butterflies.json && "
              "git commit -m 'Re-geocode localities' && git push")


def self_test():
    ok = True

    def check(name, got, want):
        nonlocal ok
        good = got == want
        ok = ok and good
        print(("PASS" if good else "FAIL"), "|", name)
        if not good:
            print("      got :", got)
            print("      want:", want)

    check("NP expands to National Park",
          expand_abbreviations("Yanachaga Chemillen NP"), "Yanachaga Chemillen National Park")
    check("Mt expands to Mount", expand_abbreviations("Mt Kenya"), "Mount Kenya")
    check("PN expands to Parque Nacional",
          expand_abbreviations("PN Manu"), "Parque Nacional Manu")
    check("accents stripped", strip_accents("Chemillén"), "Chemillen")
    check("positional noise removed",
          strip_noise("East slopes of Andes near Satipo"), "Andes Satipo")

    qs = build_queries("Yanachaga Chemillén NP, Pasco", "Peru")
    kinds = [k for k, _ in qs]
    frees = [p for k, p in qs if k == "free"]
    print("\n  first 6 attempts for the reported failure:")
    for k, p in qs[:6]:
        print("    %-11s %s" % (k, p))
    check("structured attempted before free-form", kinds[0], "structured")
    expanded_present = any("National Park" in f for f in frees)
    check("an expanded 'National Park' query is attempted", expanded_present, True)
    country_last = qs[-1] == ("free", "Peru")
    check("country-only is the final fallback", country_last, True)

    print("\n==== %s ====" % ("ALL PASS" if ok else "FAILURES"))
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser(description="Re-geocode localities in butterflies.json")
    ap.add_argument("--all", action="store_true", help="re-do every locality, not just approximate ones")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--self-test", action="store_true", help="offline checks, no network")
    args, _ = ap.parse_known_args()

    if args.self_test:
        self_test()
    try:
        run(redo_all=args.all, dry_run=args.dry_run)
    except KeyboardInterrupt:
        print("\nInterrupted — cache saved, safe to re-run (it resumes).")


if __name__ == "__main__":
    main()
