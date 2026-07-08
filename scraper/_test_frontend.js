// Headless smoke test: loads index.html + app.js in jsdom with stubbed
// fetch/Leaflet/IntersectionObserver and checks the collection renders.
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const root = path.join(__dirname, "..");
const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
const appJs = fs.readFileSync(path.join(root, "js", "app.js"), "utf8");
const data = fs.readFileSync(path.join(root, "data", "butterflies.json"), "utf8");

const dom = new JSDOM(html, { runScripts: "outside-only", pretendToBeVisual: true, url: "https://example.com/" });
const { window } = dom;
global.window = window; global.document = window.document;

// stub localStorage
const store = {};
window.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: k => { delete store[k]; } };
// stub IntersectionObserver
window.IntersectionObserver = class { constructor(cb){this.cb=cb;} observe(){} disconnect(){} };
// stub Leaflet (map view not exercised here)
window.L = undefined;
// stub fetch -> local files / fake APIs
window.fetch = async (url) => {
  if (String(url).indexOf("butterflies.json") !== -1) return { ok: true, status: 200, json: async () => JSON.parse(data) };
  if (String(url).indexOf("gbif.org") !== -1) return { ok: true, json: async () => ({ matchType: "EXACT", order: "Lepidoptera", family: "Nymphalidae", genus: "Danaus", usageKey: 123 }) };
  if (String(url).indexOf("wikipedia.org") !== -1) return { ok: true, json: async () => ({ extract: "A test summary.", type: "standard", content_urls: { desktop: { page: "https://en.wikipedia.org/wiki/Test" } } }) };
  return { ok: false, status: 404, json: async () => ({}) };
};

// run app.js in the window context
const vm = require("vm");
const ctx = dom.getInternalVMContext();
["fetch","IntersectionObserver","L","localStorage"].forEach(k => { ctx[k] = window[k]; });
vm.runInContext(appJs, ctx);

// fire DOMContentLoaded
window.document.dispatchEvent(new window.Event("DOMContentLoaded"));

setTimeout(() => {
  const q = s => window.document.querySelector(s);
  const cards = window.document.querySelectorAll(".specimen").length;
  const photos = q('[data-stat="photos"]').textContent;
  const species = q('[data-stat="species"]').textContent;
  const families = q('[data-stat="families"]').textContent;
  const countries = q('[data-stat="countries"]').textContent;
  const famOpts = window.document.querySelectorAll("#fFamily option").length;
  const countryOpts = window.document.querySelectorAll("#fCountry option").length;
  const count = q("#resultCount").textContent;
  const banner = q("#sampleBanner").hidden;

  console.log("cards rendered   :", cards);
  console.log("stat photos      :", photos);
  console.log("stat species     :", species);
  console.log("stat families    :", families);
  console.log("stat countries   :", countries);
  console.log("family options   :", famOpts, "(incl 'All')");
  console.log("country options  :", countryOpts, "(incl 'All')");
  console.log("result count text:", count);
  console.log("sample banner shown:", banner === false);

  let ok = cards === 9 && photos === "9" && species === "8" && Number(families) >= 3 && banner === false && famOpts > 1;

  // exercise a filter: family = Papilionidae
  const sel = q("#fFamily"); sel.value = "Papilionidae";
  sel.dispatchEvent(new window.Event("change"));
  setTimeout(() => {
    const filtered = window.document.querySelectorAll(".specimen").length;
    const chips = window.document.querySelectorAll(".chip").length;
    console.log("after family filter:", filtered, "cards,", chips, "chip(s)");
    ok = ok && filtered === 2 && chips === 1;

    // exercise search
    sel.value = ""; sel.dispatchEvent(new window.Event("change"));
    const s = q("#search"); s.value = "morpho"; s.dispatchEvent(new window.Event("input"));
    setTimeout(() => {
      const sr = window.document.querySelectorAll(".specimen").length;
      console.log("after search 'morpho':", sr, "card(s)");
      ok = ok && sr === 1;

      // open modal on first sample & check enrichment runs
      s.value = ""; s.dispatchEvent(new window.Event("input"));
      setTimeout(() => {
        // (waited past 140ms debounce)
        window.document.querySelector(".specimen").dispatchEvent(new window.Event("click"));
        setTimeout(() => {
          const modalOpen = q("#modalRoot").hidden === false;
          const wiki = q("#wiki") ? q("#wiki").textContent : "(none)";
          const taxo = window.document.querySelectorAll("#taxo li").length;
          console.log("modal open       :", modalOpen);
          console.log("wiki text        :", wiki.slice(0, 40));
          console.log("taxo chips       :", taxo);
          ok = ok && modalOpen && taxo >= 1;
          console.log("\n==== " + (ok ? "PASS" : "FAIL") + " ====");
          process.exit(ok ? 0 : 1);
        }, 200);
      }, 220);
    }, 260);
  }, 60);
}, 120);

// --- additional checks: GBIF link + lightbox (appended) ---
setTimeout(() => {
  const doc = window.document;
  const card = doc.querySelector(".specimen");
  card.dispatchEvent(new window.Event("click"));
  setTimeout(() => {
    const gbif = doc.getElementById("gbifOutlink");
    console.log("GBIF link href (before enrich):", gbif && gbif.href);
    setTimeout(() => {
      const gbif2 = doc.getElementById("gbifOutlink");
      console.log("GBIF link href (after enrich):", gbif2 && gbif2.href);
      const okGbif = gbif2 && /\/species\/\d+$/.test(gbif2.href) && !/taxon\/search/.test(gbif2.href);
      console.log("GBIF link is a direct species page (not /taxon/search):", okGbif);
      const img = doc.querySelector(".modal-fig img");
      img.dispatchEvent(new window.Event("click"));
      setTimeout(() => {
        const lb = doc.getElementById("lightboxRoot");
        console.log("lightbox opened:", !!lb);
        const okLb1 = !!lb;
        doc.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape" }));
        setTimeout(() => {
          const lbGone = !doc.getElementById("lightboxRoot");
          const modalStill = doc.getElementById("modalRoot").hidden === false;
          console.log("ESC closed lightbox only:", lbGone, "modal still open:", modalStill);
          const ok2 = lbGone && modalStill && okGbif && okLb1;
          console.log("\n==== EXTRA " + (ok2 ? "PASS" : "FAIL") + " ====");
          process.exit(ok2 ? 0 : 1);
        }, 30);
      }, 30);
    }, 250);
  }, 60);
}, 400);
