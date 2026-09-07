#!/usr/bin/env python3
"""
Butterfly Atlas — taxonomy re-resolver
======================================

Re-resolves family / genus / order for every species in data/butterflies.json
using GBIF properly, and reports names that look mis-parsed.

WHY THE ORIGINAL RUN GOT THINGS WRONG
-------------------------------------
Nothing here looks at photographs. The pipeline reads the species name from
the Flickr *title text*, then asks GBIF about that string. GBIF is reliable;
the queries were not:

  1. Unconstrained fuzzy matching. /species/match will return the nearest
     match anywhere in the tree of life. Fed a garbled title fragment it
     answers with something confident and wrong (this is how genera like
     "Cycnus" and "Elkalyce" got in) instead of admitting no match.

  2. Trusting the species-level family. For a shaky species match the family
     that comes back is shaky too. Asking GBIF about the GENUS directly is
     far more dependable — "Emesis" as a genus unambiguously returns
     Riodinidae, whereas a poor species match had it under Lycaenidae.

This script therefore:
  * constrains every query to Lepidoptera within Insecta/Arthropoda/Animalia
  * records GBIF's matchType and confidence, and rejects weak matches
  * verifies/derives the family from a genus-level lookup, not the species one
  * refuses any family outside the recognised butterfly families
  * lists every unresolved name so the underlying Flickr title can be checked

Usage:
    python3 fix_taxonomy.py --self-test    # offline checks, no network
    python3 fix_taxonomy.py --dry-run      # report only, write nothing
    python3 fix_taxonomy.py                # apply and write
    python3 fix_taxonomy.py --report suspects.txt   # save the review list
"""

import argparse
import json
import os
import re
import sys
import time

try:
    import requests
except ImportError:
    requests = None

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "data", "butterflies.json")
CACHE = os.path.join(HERE, "taxocache2.json")
UA = "ButterflyAtlas/1.0 (taxonomy re-resolver)"
MATCH = "https://api.gbif.org/v1/species/match"
SEARCH = "https://api.gbif.org/v1/species/search"

# The recognised true-butterfly families. A result outside this set means the
# match went wrong, not that a moth or fly wandered into the collection.
BUTTERFLY_FAMILIES = {
    "Papilionidae", "Pieridae", "Nymphalidae",
    "Lycaenidae", "Riodinidae", "Hesperiidae",
}

# GBIF returns matchType EXACT / FUZZY / HIGHERRANK / NONE. HIGHERRANK means
# it could only resolve to something above species level, which for our
# purposes is a miss. Fuzzy matches are accepted only when confident.
MIN_FUZZY_CONFIDENCE = 92

# When GBIF's backbone lists a genus under more than one butterfly family,
# prefer the modern placement. Riodinidae was long treated as the subfamily
# Riodininae inside Lycaenidae, so legacy Lycaenidae entries persist for
# genera such as Emesis, Melanis and Baeotis — Riodinidae is the current
# accepted family and should win.
FAMILY_PREFERENCE = ["Riodinidae", "Hesperiidae", "Papilionidae",
                     "Pieridae", "Nymphalidae", "Lycaenidae"]


def preferred_family(families):
    """Pick the best family from a set, by modern-placement preference."""
    for fam in FAMILY_PREFERENCE:
        if fam in families:
            return fam
    return None

LEPIDOPTERA_SCOPE = {
    "kingdom": "Animalia",
    "phylum": "Arthropoda",
    "class": "Insecta",
    "order": "Lepidoptera",
}


# ---------------------------------------------------------------------------
# Pure helpers (covered by --self-test)
# ---------------------------------------------------------------------------

BINOMIAL_RE = re.compile(r"^[A-Z][a-z]+(?:\s+[a-z][a-z\-]+){1,2}$")


PLACEHOLDER_RE = re.compile(r"^(unknown|unidentified|indet|sp\.?|unnamed)\b", re.IGNORECASE)


def is_placeholder(species):
    """'Unknown species' / 'Unidentified species' are intentional, not errors."""
    return bool(PLACEHOLDER_RE.match((species or "").strip()))


def genus_of(species):
    """First word of a binomial."""
    return (species or "").strip().split(" ")[0] if species else ""


def looks_like_binomial(species):
    """Cheap sanity check: 'Emesis mandana' yes, 'Peneleos Acraea' no."""
    return bool(BINOMIAL_RE.match((species or "").strip()))


def acceptable(match, expect_genus=None):
    """Decide whether a GBIF /species/match response is trustworthy.

    Returns (ok, reason).

    NOTE ON SYNONYMS: GBIF answers with its *accepted* name, which is often a
    different genus from the one written on the photo — Adelpha resolves to
    Limenitis, Altinote and Telchinia to Acraea, Muschampia to Syrichtus, and
    so on. Those are correct answers, not errors, so a differing genus is NOT
    grounds for rejection. What matters is that the result lands in a real
    butterfly family. (The name as written is kept for display; only the
    family is taken from GBIF.)
    """
    if not match:
        return False, "no response"
    mtype = (match.get("matchType") or "NONE").upper()
    if mtype == "NONE":
        return False, "no match"
    if mtype == "HIGHERRANK":
        return False, "only matched above species level"
    conf = match.get("confidence") or 0
    if mtype == "FUZZY" and conf < MIN_FUZZY_CONFIDENCE:
        return False, "low-confidence fuzzy match (%d)" % conf
    fam = match.get("family")
    if not fam:
        return False, "no family returned"
    if fam not in BUTTERFLY_FAMILIES:
        return False, "family '%s' is not a butterfly family" % fam
    return True, "ok"


# ---------------------------------------------------------------------------
# GBIF
# ---------------------------------------------------------------------------

class Taxonomy:
    def __init__(self, session, cache):
        self.s = session
        self.cache = cache
        self.stats = {"species_ok": 0, "genus_rescue": 0, "probe_rescue": 0, "search_rescue": 0, "unresolved": 0, "cached": 0}

    def _match(self, params):
        for attempt in range(3):
            try:
                r = self.s.get(MATCH, params=params, headers={"User-Agent": UA}, timeout=25)
                if r.status_code == 200:
                    return r.json()
                time.sleep(1.5 * (attempt + 1))
            except Exception:
                time.sleep(1.5)
        return None

    def species_lookup(self, species):
        p = dict(LEPIDOPTERA_SCOPE)
        p["name"] = species
        p["rank"] = "SPECIES"
        return self._match(p)

    def genus_lookup(self, genus, family_hint=None):
        p = dict(LEPIDOPTERA_SCOPE)
        p["name"] = genus
        p["rank"] = "GENUS"
        if family_hint:
            p["family"] = family_hint
        return self._match(p)

    def genus_family_counts(self, genus):
        """How many backbone entries place this genus in each family.

        GBIF marks essentially every entry ACCEPTED, so status cannot
        separate a real placement from a stray record. The weight of entries
        can: Emesis is ~14 Riodinidae vs 4 Lycaenidae, whereas Zizula is 19
        Lycaenidae vs a single Riodinidae outlier. A majority vote gets both
        right where "does a Riodinidae entry exist?" got Zizula wrong."""
        counts = {}
        try:
            r = self.s.get(SEARCH, params={"q": genus, "rank": "GENUS", "limit": 50},
                           headers={"User-Agent": UA}, timeout=25)
            if r.status_code != 200:
                return counts
            for item in (r.json().get("results") or []):
                if (item.get("genus") or "").lower() != genus.lower():
                    continue
                fam = (item.get("family") or "").strip().title()  # "LYCAENIDAE" -> "Lycaenidae"
                if fam in BUTTERFLY_FAMILIES:
                    counts[fam] = counts.get(fam, 0) + 1
        except Exception:
            pass
        return counts

    def genus_families_from_search(self, genus):
        """Every butterfly family GBIF's backbone associates with this genus.

        The backbone contains legacy entries: many riodinid genera also appear
        under Lycaenidae because Riodinidae used to be treated as the subfamily
        Riodininae within it. Returning the full set lets the caller choose."""
        fams = set()
        try:
            r = self.s.get(SEARCH, params={"q": genus, "rank": "GENUS", "limit": 20},
                           headers={"User-Agent": UA}, timeout=25)
            if r.status_code != 200:
                return fams
            for item in (r.json().get("results") or []):
                if (item.get("genus") or "").lower() != genus.lower():
                    continue          # a different genus entirely (Baeotis vs Baeotus)
                fam = item.get("family")
                if fam in BUTTERFLY_FAMILIES:
                    fams.add(fam)
        except Exception:
            pass
        return fams

    def genus_by_search(self, genus):
        """Last resort: GBIF's search endpoint.

        /species/match frequently returns no family at all for many valid
        Riodinidae genera (Ancyluris, Siseme, Mesosemia, Nymphidium...).
        /species/search returns full classifications, so scanning its results
        for a butterfly family resolves them.
        """
        try:
            r = self.s.get(SEARCH,
                           params={"q": genus, "rank": "GENUS", "limit": 20},
                           headers={"User-Agent": UA}, timeout=25)
            if r.status_code != 200:
                return None
            for item in (r.json().get("results") or []):
                if (item.get("genus") or "").lower() != genus.lower():
                    continue
                fam = item.get("family")
                if fam in BUTTERFLY_FAMILIES:
                    return {"family": fam, "genus": item.get("genus") or genus,
                            "order": item.get("order") or "Lepidoptera"}
        except Exception:
            pass
        return None

    def genus_by_family_probe(self, genus):
        """Disambiguate homonyms.

        Several riodinid genera share a name with a moth genus — Caria also
        exists in Limacodidae, Lemonias in Brahmaeidae, Lasaia in
        Acrolophidae. Scoping to Lepidoptera cannot separate them because
        moths are Lepidoptera too. Asking GBIF once per butterfly family,
        with that family as an explicit hint, resolves it cleanly.
        """
        for fam in ("Riodinidae", "Lycaenidae", "Nymphalidae",
                    "Hesperiidae", "Papilionidae", "Pieridae"):
            m = self.genus_lookup(genus, family_hint=fam)
            # The probe must ALSO confirm the genus name came back unchanged.
            # Without this, "Baeotis" fuzzy-matches to the nymphalid "Baeotus"
            # and the Nymphalidae hint then appears to confirm it.
            if m and (m.get("genus") or "").lower() != genus.lower():
                continue
            if m and m.get("family") == fam:
                mtype = (m.get("matchType") or "NONE").upper()
                conf = m.get("confidence") or 0
                if mtype == "EXACT" or (mtype == "FUZZY" and conf >= MIN_FUZZY_CONFIDENCE):
                    return m
        return None

    def resolve(self, species):
        """Return dict(family, genus, order, source) or None, plus a reason."""
        if species in self.cache:
            self.stats["cached"] += 1
            c = self.cache[species]
            return c.get("result"), c.get("reason", "cached")

        genus = genus_of(species)
        reason = ""

        # Deliberately-unidentified records aren't errors; leave them be.
        if is_placeholder(species):
            self.cache[species] = {"result": None, "reason": "deliberately unidentified"}
            return None, "deliberately unidentified"

        # 1) GENUS FIRST. The family of a genus is a far more dependable fact
        #    than whatever a species-level fuzzy match happens to return: a
        #    weak species match can come back with a plausible-but-wrong
        #    butterfly family (Emesis, Melanis and Baeotis are all Riodinidae
        #    but were being filed under Lycaenidae/Nymphalidae this way).
        #    Genus placement is stable, so it decides the family.
        if genus:
            gm = self.genus_lookup(genus)
            # A genus lookup that answers with a DIFFERENT genus name is a
            # mis-match, not a synonym — Baeotis fuzzy-matched to Baeotus.
            if gm and (gm.get("genus") or "").lower() != genus.lower():
                gm = None
            gok, gwhy = acceptable(gm)
            if gok:
                fam = gm["family"]
                source = "genus"
                # ONLY Lycaenidae answers are second-guessed. Riodinidae was
                # long treated as the subfamily Riodininae inside Lycaenidae,
                # so the backbone still returns Lycaenidae with full
                # confidence for genera like Emesis and Melanis. Every other
                # family is taken at face value — applying this check more
                # widely wrongly reclassified Danaus, Eurytides and others.
                if fam == "Lycaenidae":
                    counts = self.genus_family_counts(genus)
                    rio = counts.get("Riodinidae", 0)
                    lyc = counts.get("Lycaenidae", 0)
                    # Only override on a clear majority. A lone Riodinidae
                    # record among many Lycaenidae ones (Zizula, Panthiades)
                    # is an outlier, not the modern placement.
                    if rio > lyc:
                        fam = "Riodinidae"
                        source = "genus+riodinid-correction (%d riodinid vs %d lycaenid entries)" % (rio, lyc)
                        self.stats["riodinid_fix"] = self.stats.get("riodinid_fix", 0) + 1
                result = {"family": fam, "genus": genus,
                          "order": gm.get("order") or "Lepidoptera", "source": source}
                self.stats["genus_rescue"] += 1
                self.cache[species] = {"result": result, "reason": source}
                return result, source
            reason = "genus lookup: %s" % gwhy

            # 1b) homonym probe — ask family by family (Caria, Lasaia, Lemonias)
            pm = self.genus_by_family_probe(genus)
            if pm:
                result = {"family": pm["family"], "genus": genus,
                          "order": pm.get("order") or "Lepidoptera", "source": "family-probe"}
                self.stats["probe_rescue"] = self.stats.get("probe_rescue", 0) + 1
                self.cache[species] = {"result": result, "reason": "resolved by family probe"}
                return result, "resolved by family probe"

            # 1c) search endpoint (catches most remaining Riodinidae genera)
            sm = self.genus_by_search(genus)
            if sm:
                result = {"family": sm["family"], "genus": genus,
                          "order": sm.get("order") or "Lepidoptera", "source": "search"}
                self.stats["search_rescue"] = self.stats.get("search_rescue", 0) + 1
                self.cache[species] = {"result": result, "reason": "resolved via species search"}
                return result, "resolved via species search"

        # 2) only if the genus cannot be placed at all, fall back to the
        #    species-level match.
        m = self.species_lookup(species)
        ok, why = acceptable(m)
        if ok:
            result = {"family": m["family"], "genus": genus or m.get("genus"),
                      "order": m.get("order") or "Lepidoptera", "source": "species"}
            self.stats["species_ok"] += 1
            self.cache[species] = {"result": result, "reason": "species match (genus unplaceable)"}
            return result, "species match"
        reason = "%s; species lookup: %s" % (reason, why)


        self.stats["unresolved"] += 1
        self.cache[species] = {"result": None, "reason": reason}
        return None, reason


# ---------------------------------------------------------------------------

def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_json(p, o):
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(o, f, ensure_ascii=False, indent=1)
    os.replace(tmp, p)


def run(dry_run=False, report_path=None):
    if requests is None:
        raise SystemExit("Install requests first:  pip install requests")
    if not os.path.exists(DATA):
        raise SystemExit("Can't find %s" % DATA)

    data = load_json(DATA)
    species_tbl = data.get("species", {})
    photos = data.get("photos", [])
    names = sorted([s for s in species_tbl.keys() if s])
    print("Loaded %d photos, %d distinct species names.\n" % (len(photos), len(names)))

    cache = {}
    if os.path.exists(CACHE):
        try:
            cache = load_json(CACHE)
            print("Re-using %d cached lookups.\n" % len(cache))
        except Exception:
            cache = {}

    session = requests.Session()
    tax = Taxonomy(session, cache)

    changed, unresolved = [], []
    for i, sp in enumerate(names, 1):
        before = species_tbl.get(sp, {}).get("family")
        result, reason = tax.resolve(sp)
        if result:
            if before and before != result["family"]:
                changed.append((sp, before, result["family"], result["source"]))
            entry = species_tbl.setdefault(sp, {})
            entry["family"] = result["family"]
            entry["genus"] = result["genus"]
            entry["order"] = result["order"]
        else:
            unresolved.append((sp, reason, species_tbl.get(sp, {}).get("count", 0)))
        if i % 25 == 0:
            print("  ...%d/%d" % (i, len(names)))
            if not dry_run:
                save_json(CACHE, cache)

    # push corrected values down onto the photo records
    for p in photos:
        e = species_tbl.get(p.get("species"))
        if e and e.get("family"):
            p["family"] = e["family"]
            p["genus"] = e.get("genus") or p.get("genus")
            p["order"] = e.get("order") or p.get("order")

    print("\n--- CORRECTED FAMILIES (%d) ---" % len(changed))
    for sp, old, new, src in changed[:60]:
        print("  %-38s %-14s -> %-14s (via %s)" % (sp[:38], old, new, src))
    if len(changed) > 60:
        print("  ...and %d more" % (len(changed) - 60))

    print("\n--- UNRESOLVED / LIKELY MIS-PARSED TITLES (%d) ---" % len(unresolved))
    print("These could not be matched to a butterfly family via GBIF.")
    print("Note: a name appearing here does NOT mean it is wrong — GBIF's")
    print("backbone is simply incomplete for some genera. Check these only")
    print("if the name itself looks garbled:\n")
    for sp, reason, count in sorted(unresolved, key=lambda x: -x[2])[:60]:
        print("  %-38s (%s photo%s)  %s" % (sp[:38], count, "" if count == 1 else "s", reason[:60]))
    if len(unresolved) > 60:
        print("  ...and %d more" % (len(unresolved) - 60))

    if report_path:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("UNRESOLVED SPECIES NAMES — check these Flickr titles\n")
            f.write("=" * 55 + "\n\n")
            for sp, reason, count in sorted(unresolved, key=lambda x: -x[2]):
                f.write("%s\t%s photo(s)\t%s\n" % (sp, count, reason))
            f.write("\n\nCORRECTED FAMILIES\n" + "=" * 55 + "\n\n")
            for sp, old, new, src in changed:
                f.write("%s\t%s -> %s\t(via %s)\n" % (sp, old, new, src))
        print("\nFull report written to %s" % report_path)

    if not dry_run:
        save_json(CACHE, cache)
        save_json(DATA, data)
        print("\nWritten to %s" % DATA)
        print("\nDeploy with:")
        print("  cd ~/Butterfly-Atlas && git add -A && "
              "git commit -m 'Re-resolve taxonomy via GBIF' && git push")
    else:
        print("\nDRY RUN — nothing written.")

    if tax.stats.get("riodinid_fix"):
        print("  %d genera corrected from legacy Lycaenidae to Riodinidae"
              % tax.stats["riodinid_fix"])
    print("\n  %d at species level, %d via genus, %d via family probe, %d via search, %d unresolved"
          % (tax.stats["species_ok"], tax.stats["genus_rescue"],
             tax.stats.get("probe_rescue", 0), tax.stats.get("search_rescue", 0),
             tax.stats["unresolved"]))


def self_test():
    ok = True

    def check(name, got, want):
        nonlocal ok
        good = got == want
        ok = ok and good
        print(("PASS" if good else "FAIL"), "|", name)
        if not good:
            print("      got :", got, "\n      want:", want)

    check("genus extracted", genus_of("Emesis mandana"), "Emesis")
    check("binomial recognised", looks_like_binomial("Emesis mandana"), True)
    check("garbled name rejected as binomial", looks_like_binomial("Peneleos Acraea"), False)

    # a good species match
    good = {"matchType": "EXACT", "confidence": 99, "family": "Riodinidae", "genus": "Emesis"}
    check("exact butterfly match accepted", acceptable(good, "Emesis")[0], True)

    # A differing genus is a SYNONYM resolution, not an error: GBIF answers
    # with its accepted name (Adelpha -> Limenitis). Must be accepted.
    synonym = {"matchType": "EXACT", "confidence": 99, "family": "Nymphalidae", "genus": "Limenitis"}
    check("synonym resolution accepted (Adelpha->Limenitis)", acceptable(synonym, "Adelpha")[0], True)
    check("placeholder names detected", is_placeholder("Unknown species"), True)
    check("real species not treated as placeholder", is_placeholder("Emesis mandana"), False)

    # a non-butterfly family (fly/fungus) must never be accepted
    fly = {"matchType": "EXACT", "confidence": 99, "family": "Tachinidae", "genus": "Tachina"}
    check("non-butterfly family rejected", acceptable(fly)[0], False)

    # low-confidence fuzzy guesses are how junk genera got in
    weak = {"matchType": "FUZZY", "confidence": 70, "family": "Nymphalidae", "genus": "Elkalyce"}
    check("low-confidence fuzzy rejected", acceptable(weak)[0], False)

    higher = {"matchType": "HIGHERRANK", "confidence": 99, "family": "Nymphalidae"}
    check("higher-rank-only match rejected", acceptable(higher)[0], False)

    check("no match rejected", acceptable({"matchType": "NONE"})[0], False)
    check("queries are scoped to Lepidoptera", LEPIDOPTERA_SCOPE["order"], "Lepidoptera")

    # The real-world cases: GBIF lists these genera under BOTH families
    # (legacy Lycaenidae + modern Riodinidae). Riodinidae must win.
    check("Emesis: Riodinidae beats legacy Lycaenidae",
          preferred_family({"Lycaenidae", "Nymphalidae", "Riodinidae"}), "Riodinidae")
    check("Melanis: Riodinidae beats legacy Lycaenidae",
          preferred_family({"Lycaenidae", "Riodinidae"}), "Riodinidae")
    check("a genuine lycaenid stays Lycaenidae",
          preferred_family({"Lycaenidae"}), "Lycaenidae")
    check("a genuine nymphalid stays Nymphalidae",
          preferred_family({"Nymphalidae"}), "Nymphalidae")
    check("no butterfly family -> None", preferred_family({"Tachinidae"}), None)

    print("\n==== %s ====" % ("ALL PASS" if ok else "FAILURES"))
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser(description="Re-resolve taxonomy in butterflies.json via GBIF")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--self-test", action="store_true", help="offline checks, no network")
    ap.add_argument("--report", metavar="FILE", help="write the full review list to a file")
    args, _ = ap.parse_known_args()

    if args.self_test:
        self_test()
    try:
        run(dry_run=args.dry_run, report_path=args.report)
    except KeyboardInterrupt:
        print("\nInterrupted — cache saved, safe to re-run (it resumes).")


if __name__ == "__main__":
    main()
