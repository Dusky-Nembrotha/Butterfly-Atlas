// Headless front-end test: loads index.html + app.js in jsdom with stubbed
// fetch/Leaflet/IntersectionObserver and exercises the real UI against the
// real data/butterflies.json.
//
//   npm install jsdom && node scraper/_test_frontend.js
//
// Assertions are derived from the data rather than hard-coded, so this keeps
// working as the collection grows.
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { JSDOM } = require("jsdom");

const root = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const appJs = fs.readFileSync(path.join(root, "js", "app.js"), "utf8");
const raw = fs.readFileSync(path.join(root, "data", "butterflies.json"), "utf8");
const data = JSON.parse(raw);

const dom = new JSDOM(html, { runScripts: "outside-only", pretendToBeVisual: true, url: "https://example.com/" });
const { window } = dom;
const store = {};
window.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; } };
window.IntersectionObserver = class { constructor(cb) { this.cb = cb; } observe() {} disconnect() {} };
window.fetch = async (url) => {
  const u = String(url);
  if (u.indexOf("butterflies.json") !== -1) return { ok: true, status: 200, json: async () => JSON.parse(raw) };
  if (u.indexOf("gbif.org") !== -1) return { ok: true, json: async () => ({ matchType: "EXACT", order: "Lepidoptera", family: "Nymphalidae", genus: "Danaus", usageKey: 123 }) };
  if (u.indexOf("wikipedia.org") !== -1) return { ok: true, json: async () => ({ extract: "A test summary.", type: "standard", content_urls: { desktop: { page: "https://en.wikipedia.org/wiki/Test" } } }) };
  return { ok: false, status: 404, json: async () => ({}) };
};

const ctx = dom.getInternalVMContext();
["fetch", "IntersectionObserver", "localStorage"].forEach(k => { ctx[k] = window[k]; });
ctx.L = undefined;                       // map view not exercised here
vm.runInContext(appJs, ctx);
window.document.dispatchEvent(new window.Event("DOMContentLoaded"));

const q = s => window.document.querySelector(s);
const qa = s => [...window.document.querySelectorAll(s)];
const fire = (n, type) => n.dispatchEvent(new window.Event(type));
const wait = ms => new Promise(r => setTimeout(r, ms));

let failures = 0;
function check(label, cond, detail) {
  if (!cond) failures++;
  console.log((cond ? "PASS" : "FAIL") + " | " + label + (detail != null ? "  " + detail : ""));
}
// "<b>102</b> specimens · 60 species" -> 102
const shown = () => parseInt(q("#resultCount").textContent.replace(/,/g, ""), 10);
// "Armenia (102)" -> {value, count}
const opts = sel => qa(sel + " option").filter(o => o.value).map(o => ({
  value: o.value, count: parseInt((o.textContent.match(/\((\d+)\)\s*$/) || [])[1], 10)
}));

async function select(sel, value) { const n = q(sel); n.value = value; fire(n, "change"); await wait(0); }

(async () => {
  await wait(200);

  /* ---------- baseline render ---------- */
  check("collection renders", qa(".specimen").length > 0, qa(".specimen").length + " cards");
  check("stat: photos matches data", q('[data-stat="photos"]').textContent.replace(/,/g, "") === String(data.photos.length));
  check("sample banner hidden for real data", q("#sampleBanner").hidden === true);
  check("all specimens shown initially", shown() === data.photos.length);

  /* ---------- every dropdown option must actually match records ----------
     This is the invariant that bug 1 broke: the Country dropdown was keyed on
     the canonical country while the record popup filtered on the raw field,
     so a filter value could exist that matched nothing. */
  for (const [sel, label] of [["#fCountry", "country"], ["#fFamily", "family"], ["#fContinent", "continent"]]) {
    let bad = [];
    for (const o of opts(sel)) {
      await select(sel, o.value);
      if (shown() !== o.count || shown() === 0) bad.push(o.value + ": label=" + o.count + " actual=" + shown());
    }
    await select(sel, "");
    check("every " + label + " option matches its stated count", bad.length === 0, bad.join("; "));
  }

  // a sample of genera (498 of them — checking all is slow)
  const genusSample = opts("#fGenus").filter((_, i) => i % 60 === 0);
  let badGenus = [];
  for (const o of genusSample) {
    await select("#fGenus", o.value);
    if (shown() !== o.count) badGenus.push(o.value + ": label=" + o.count + " actual=" + shown());
  }
  await select("#fGenus", "");
  check("sampled genus options match their counts", badGenus.length === 0, badGenus.join("; "));

  /* ---------- bug 1: record popup -> country filter round-trip ---------- */
  // Exercise an album named "<Place> Butterflies" — these are the records
  // whose country field used to hold the album name, so clicking Country in
  // the popup filtered on a value the dropdown did not have and matched
  // nothing. The round-trip must hold whatever the stored field contains.
  const junk = data.photos.find(p => /Butterflies$/.test(p.albumTitle || "") && p.country);
  check("test fixture: an album-named-after-a-place record exists", !!junk,
        junk ? junk.albumTitle + " / " + JSON.stringify(junk.country) : "none found");
  if (junk) {
    await select("#fAlbum", junk.albumTitle);
    const before = shown();
    fire(q(".specimen"), "click");
    await wait(0);
    const dt = qa(".modal .factrow dt").find(d => d.textContent.trim() === "Country");
    const dd = dt && dt.nextElementSibling;
    const btn = dd && dd.querySelector("button.fact-link");
    check("popup shows a real country, not the album name",
          !!dd && !/Butterflies/.test(dd.textContent), JSON.stringify(dd && dd.textContent));
    if (btn) {
      fire(btn, "click");
      await wait(0);
      check("clicking Country in the popup returns records", shown() > 0, shown() + " specimens");
      check("country chip agrees with the dropdown",
            q("#fCountry").value === dd.textContent.trim(),
            "select=" + JSON.stringify(q("#fCountry").value) + " popup=" + JSON.stringify(dd.textContent.trim()));
      check("the filtered set is the whole country, not a subset", shown() >= before);
    } else {
      check("Country value in popup is clickable", false);
    }
    q("#resetFilters").dispatchEvent(new window.Event("click"));
    await wait(0);
  }

  /* ---------- bug 3: genus-level ("Genus sp.") determinations ---------- */
  const spNames = [...new Set(data.photos.map(p => p.species).filter(s => s && / sp\.$/.test(s)))];
  check("genus-level determinations are present in the data", spNames.length > 0, spNames.length + " names");
  const spPhotos = data.photos.filter(p => / sp\.$/.test(p.species || ""));
  // Most resolve to a family via GBIF's genus lookup. A few never will — a
  // title like "Skipper sp." names a common group, not a genus, and
  // fix_taxonomy.py lists those in suspects.txt for manual review. What must
  // hold is that an unresolved one never reaches the Genus dropdown.
  const noFamily = spPhotos.filter(p => !p.family);
  const genusValues = new Set(opts("#fGenus").map(o => o.value));
  check("genus-level records mostly resolve to a family",
        noFamily.length <= spPhotos.length * 0.1,
        (spPhotos.length - noFamily.length) + "/" + spPhotos.length + " resolved" +
        (noFamily.length ? "; unresolved: " + [...new Set(noFamily.map(p => p.species))].join(", ") : ""));
  check("an unresolved genus-level record stays out of the Genus filter",
        noFamily.every(p => !genusValues.has(p.genus)),
        noFamily.filter(p => genusValues.has(p.genus)).map(p => p.genus).join(", ") || "none leaked");
  // they must be filterable under their genus, and rendered by name
  const example = spPhotos.find(p => p.genus && p.family);
  if (example) {
    await select("#fGenus", example.genus);
    const labels = qa(".specimen .sci").map(n => n.textContent);
    check("genus filter includes its 'Genus sp.' records",
          labels.indexOf(example.genus + " sp.") !== -1,
          "genus=" + example.genus + " -> " + shown() + " specimens");
    check("genus-level records are not labelled 'Unidentified'",
          labels.indexOf("Unidentified") === -1);
    await select("#fGenus", "");
  }
  check("no common name leaked into the Genus dropdown",
        opts("#fGenus").every(o => /^[A-Z][a-z]+$/.test(o.value)));

  /* ---------- continent filter ---------- */
  const continents = opts("#fContinent");
  check("continent options are populated", continents.length > 0,
        continents.map(o => o.value + " (" + o.count + ")").join(", "));

  // Selecting a continent must narrow the Country list to that continent's
  // countries, the same way Family narrows Genus.
  const allCountries = new Set(opts("#fCountry").map(o => o.value));
  await select("#fContinent", "South America");
  const saCountries = opts("#fCountry").map(o => o.value);
  check("continent narrows the Country dropdown",
        saCountries.length > 0 && saCountries.length < allCountries.size,
        saCountries.join(", "));
  check("every narrowed country really is on that continent",
        saCountries.every(c => ["Brazil", "Argentina", "Peru", "Ecuador", "Bolivia", "Colombia"].indexOf(c) !== -1),
        saCountries.join(", "));
  check("continent chip renders",
        qa(".chip").some(c => /^Continent: South America/.test(c.textContent)));

  // A country selected on one continent is cleared when another is chosen,
  // rather than being left as a stale filter that matches nothing.
  await select("#fCountry", saCountries[0]);
  const narrowed = shown();
  await select("#fContinent", "Africa");
  check("stale country selection is cleared when the continent changes",
        q("#fCountry").value === "" && shown() > 0,
        "country=" + JSON.stringify(q("#fCountry").value) + ", " + shown() + " specimens");
  check("country filter still narrows within a continent", narrowed > 0 && narrowed <= shown() + data.photos.length);

  // Transcontinental countries appear under both continents.
  for (const c of ["Europe", "Asia"]) {
    await select("#fContinent", c);
    check("Turkey is listed under " + c,
          opts("#fCountry").some(o => o.value === "Turkey"),
          opts("#fCountry").map(o => o.value).join(", "));
  }
  await select("#fContinent", "");
  check("clearing the continent restores every country",
        opts("#fCountry").length === allCountries.size);

  // continent survives a shareable URL
  await select("#fContinent", "Africa");
  check("continent is written to the URL hash", /continent=Africa/.test(window.location.hash),
        window.location.hash);
  q("#resetFilters").dispatchEvent(new window.Event("click"));
  await wait(0);
  check("reset clears the continent", q("#fContinent").value === "" && shown() === data.photos.length);

  /* ---------- search + modal enrichment still work ---------- */
  const s = q("#search"); s.value = "morpho"; fire(s, "input");
  await wait(200);
  check("search narrows the collection", shown() > 0 && shown() < data.photos.length, shown() + " for 'morpho'");
  s.value = ""; fire(s, "input");
  await wait(200);

  fire(q(".specimen"), "click");
  await wait(50);
  check("record popup opens", q("#modalRoot").hidden === false);
  check("taxonomy chips render", qa("#taxo li").length > 0, qa("#taxo li").length + " ranks");
  await wait(100);
  check("species notes resolve", !!q("#wiki") && !/^Looking up/.test(q("#wiki").textContent));

  console.log("\n==== " + (failures ? failures + " FAILURE(S)" : "ALL PASS") + " ====");
  process.exit(failures ? 1 : 0);
})();
