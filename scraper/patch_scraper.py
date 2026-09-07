#!/usr/bin/env python3
"""
Patch scraper/fetch_flickr.py so it stops putting butterfly names into the
location field.
=====================================================================

THE BUG
-------
The collector's parse_title() finds the scientific name in a Flickr title,
then treats everything after it as the locality. For titles where the common
name or a "sp." qualifier trails the binomial, that text ends up inside the
location, producing entries such as:

    "(Abadima Acraea, Kakum NP, Ghana"
    "(African Beak) Murchison Falls NP, Uganda"
    "Acraea sp, Ankasa NP, Ghana"
    "P. tringa, Yanachaga Chemillen NP, Pasco, Peru"
    "& C. myrmidone, Apuseni Hills, Cluj, Romania"

Nothing can geocode those, which is why so many pins were wrong or missing,
and it is the same root cause behind the mangled species/family values.

THE FIX
-------
This adds the locality-cleaning logic already proven in fix_geocode.py and
wraps parse_title so every locality it returns is cleaned before use. The
wrapper approach means the original function is left untouched — nothing
about species parsing changes, only the locality it hands back.

Idempotent: running it twice is harmless.

Usage:
    python3 patch_scraper.py            # patch, then verify
    python3 patch_scraper.py --check    # report status only, change nothing
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "fetch_flickr.py")
MARKER = "# --- locality cleaning (added by patch_scraper.py) ---"

PATCH = '''

''' + MARKER + '''
# Strips butterfly names out of the locality that parse_title() returns.
# See patch_scraper.py for why this is needed.

TAXON_PREFIX_RE = re.compile(
    r"^(?:"
    r"[A-Z][a-z]+\\s+sp\\.?"                  # "Acraea sp" / "Euphaedra sp."
    r"|[A-Z][a-z]+(?:idae|inae)\\s+sp\\.?"     # "Nymphalidae sp"
    r"|[A-Z]\\.\\s*[a-z\\-]+"                  # "P. tringa"
    r"|[A-Z][a-z]+\\s+spp\\.?"
    r"|eggs?|larva[e]?|caterpillars?"
    r")$", re.IGNORECASE)


def clean_locality(text):
    """Remove common-name / species-name pollution from a locality string.

        "(Abadima Acraea, Kakum NP, Ghana"  -> "Kakum NP, Ghana"
        "(African Beak) Murchison Falls NP" -> "Murchison Falls NP"
        "Acraea sp, Ankasa NP, Ghana"       -> "Ankasa NP, Ghana"
        "Ham Wall, Somerset"                -> unchanged
    """
    s = (text or "").strip()
    if ")" in s:                       # "(Common name) Real Place"
        s = s[s.rindex(")") + 1:]
    elif s.startswith("(") or s.startswith("&"):
        parts = [p.strip() for p in s.split(",")]
        if len(parts) > 1:
            s = ", ".join(parts[1:])

    parts = [p.strip() for p in s.split(",")]
    while len(parts) > 1 and TAXON_PREFIX_RE.match(parts[0]):
        parts = parts[1:]
    s = ", ".join(parts)

    s = re.sub(r"\\s+[-\\u2013\\u2014]\\s+", ", ", s)          # "Coroico - Sol y Luna"
    s = ", ".join(                                          # "Collard Hill NT"
        re.sub(r"\\s+\\b(NT|CP|RDA|LNR)\\b\\s*$", "", t, flags=re.IGNORECASE).strip()
        for t in s.split(",")
    )
    s = re.sub(r"\\(.*$", "", s)                             # "Bwindi (high altitude"
    s = re.sub(r"\\b\\d{1,2}\\.\\d{1,2}\\.\\d{4}\\b", "", s)    # stray dates
    s = re.sub(r"^\\s*[A-Z][a-z]+\\s+Butterflies\\s*$", "", s)  # album name as locality
    return re.sub(r"\\s+", " ", s).strip(" ,-\\u2013\\u2014")


# Wrap the original parser so every locality it returns is cleaned. Species
# parsing is untouched — only the locality is affected.
_unpatched_parse_title = parse_title


def parse_title(title, album_title=""):
    species, subspecies, location, country, common = _unpatched_parse_title(
        title, album_title)
    return species, subspecies, clean_locality(location), country, common
'''


def status():
    if not os.path.exists(TARGET):
        return "missing"
    with open(TARGET, encoding="utf-8") as f:
        return "patched" if MARKER in f.read() else "unpatched"


def apply_patch():
    with open(TARGET, encoding="utf-8") as f:
        src = f.read()

    if MARKER in src:
        print("Already patched — nothing to do.")
        return True

    # Insert before the __main__ guard so the wrapper is defined at import time.
    guard = '\nif __name__ == "__main__":'
    if guard not in src:
        print("ERROR: couldn't find the __main__ block in fetch_flickr.py.")
        print("The file may have been edited; patch not applied.")
        return False

    src = src.replace(guard, PATCH + guard, 1)
    backup = TARGET + ".bak"
    if not os.path.exists(backup):
        with open(backup, "w", encoding="utf-8") as f:
            with open(TARGET, encoding="utf-8") as orig:
                f.write(orig.read())
        print("Backup written to %s" % backup)

    with open(TARGET, "w", encoding="utf-8") as f:
        f.write(src)
    print("Patch applied to %s" % TARGET)
    return True


def verify():
    """Import the patched module and check real corrupted titles now parse."""
    sys.path.insert(0, HERE)
    for mod in ("fetch_flickr",):
        if mod in sys.modules:
            del sys.modules[mod]
    try:
        import fetch_flickr as ff
    except Exception as e:
        print("Could not import fetch_flickr.py: %s" % e)
        return False

    cases = [
        # (title, expected species, locality must NOT contain)
        ("Abadima Acraea (Acraea abadima), Kakum NP, Ghana 12/05/2018",
         "Acraea abadima", "Abadima"),
        ("African Beak (Libythea labdaca) Murchison Falls NP, Uganda 03/02/2019",
         "Libythea labdaca", "Beak"),
        ("Acraea sp, Ankasa NP, Ghana 01/01/2020", "", "sp,"),
        ("Peacock (Aglais io), Sandhurst, Berkshire, UK 21/05/2011",
         "Aglais io", "Peacock"),
    ]
    ok = True
    print("\nVerifying against real title shapes:\n")
    for title, want_species, must_not_contain in cases:
        sp, ss, loc, ctry, common = ff.parse_title(title, "")
        clean = must_not_contain.lower() not in (loc or "").lower()
        good = clean and (not want_species or sp == want_species)
        ok = ok and good
        print(("PASS" if good else "FAIL"))
        print("   title   : %s" % title[:66])
        print("   species : %r" % sp)
        print("   locality: %r" % loc)
        if not good:
            print("   PROBLEM : locality still contains %r or species mismatch"
                  % must_not_contain)
        print()

    # already-clean localities must survive untouched
    for raw, expect in [("Ham Wall, Somerset, UK", "Ham Wall, Somerset, UK"),
                        ("Kakum NP, Ghana", "Kakum NP, Ghana")]:
        got = ff.clean_locality(raw)
        good = got == expect
        ok = ok and good
        print(("PASS" if good else "FAIL"), "| clean locality untouched: %r -> %r" % (raw, got))

    print("\n==== %s ====" % ("PATCH VERIFIED" if ok else "VERIFICATION FAILED"))
    return ok


def main():
    if "--check" in sys.argv:
        print("fetch_flickr.py is: %s" % status())
        return
    st = status()
    if st == "missing":
        raise SystemExit("Can't find %s" % TARGET)
    if not apply_patch():
        raise SystemExit(1)
    if not verify():
        print("\nThe patch was applied but verification failed.")
        print("Restore the original with:  mv %s.bak %s" % (TARGET, TARGET))
        raise SystemExit(1)
    print("\nNext: re-scrape, then re-run the two fix scripts:")
    print("  python scraper/fetch_flickr.py --user robertgodden")
    print("  python scraper/fix_geocode.py --all")
    print("  python scraper/fix_taxonomy.py")


if __name__ == "__main__":
    main()
