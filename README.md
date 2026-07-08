# The Butterfly Atlas

A searchable, mapped catalogue of wild butterflies photographed around the
world — built from a public Flickr photostream and hosted for free on GitHub
Pages. Each record carries its species, locality, date and (where available)
map coordinates, and links out to GBIF, iNaturalist and Wikipedia so the site
works as a genuine reference for lepidopterists.

- **Front-end:** a static site (`index.html`, `css/`, `js/`) — no build step,
  no framework. It reads one file, `data/butterflies.json`.
- **Collector:** `scraper/fetch_flickr.py` builds that JSON from Flickr.
- **Automation:** a GitHub Action re-runs the collector on a schedule.

The site ships with **sample data** so it renders the moment you deploy. Run
the collector once to replace it with the real collection.

---

## 1. Deploy the site (5 minutes)

1. Create a new GitHub repository and push these files to it.
2. In the repo, go to **Settings → Pages**.
3. Under **Build and deployment**, set **Source: Deploy from a branch**,
   **Branch: `main`**, folder **`/ (root)`**, and save.
4. After a minute your site is live at
   `https://<your-username>.github.io/<repo-name>/`.

At this point you'll see the sample specimens. Next, load your real data.

---

## 2. Load the real collection

The collector needs **no Flickr Pro account**. You have two options.

### Option A — with a free API key (recommended, most reliable)

A Flickr API key is free for non-commercial use and takes about two minutes —
Pro is **not** required.

1. Sign in to Flickr and visit
   <https://www.flickr.com/services/apps/create/> → *Request an API Key* →
   *Apply for a non-commercial key*.
2. Copy the **Key**.
3. Run the collector:

   ```bash
   pip install -r scraper/requirements.txt
   FLICKR_API_KEY=your_key_here python3 scraper/fetch_flickr.py --user robertgodden
   ```

### Option B — keyless (no signup)

If you'd rather not register anything, the collector can extract the public
"site key" Flickr embeds in its own pages and use that:

```bash
pip install -r scraper/requirements.txt
python3 scraper/fetch_flickr.py --user robertgodden
```

This works today, but it depends on Flickr's page markup and can break without
warning if they change their site. If it ever stops finding a key, switch to
Option A.

Either way, the collector writes `data/butterflies.json`. Commit and push it:

```bash
git add data/butterflies.json scraper/geocache.json scraper/taxocache.json
git commit -m "Load collection"
git push
```

Refresh the site and the full collection appears.

---

## 3. Keep it up to date automatically

`.github/workflows/update-data.yml` runs the collector every Monday and
whenever you trigger it manually (**Actions → Update butterfly data → Run
workflow**). It commits any changes back to the repo.

To use the more reliable API-key path in the Action, add your key as a repo
secret: **Settings → Secrets and variables → Actions → New repository secret**,
name it `FLICKR_API_KEY`. Without it, the Action uses keyless mode.

The workflow needs write access: **Settings → Actions → General → Workflow
permissions → Read and write permissions**.

---

## How your photo titles are read

The collector parses each photo **title**, which in this collection reliably
starts with the scientific name and lists the place, e.g.:

```
Papilio dardanus, Bwindi, Uganda
Morpho helenor helenor, Cristalino, Mato Grosso, Brazil
```

From that it extracts:

- **Species** — the leading `Genus species` binomial (an optional third word
  is treated as a subspecies only when it's a clean Latin epithet, so
  descriptive notes like "*Charaxes candiope* basking on fruit" are handled).
- **Locality / country** — the comma-separated text after the name; the album
  name (often a country) is used as a fallback.

Taxonomy (order / family / genus) is then looked up per species from **GBIF**,
and any photo without Flickr coordinates is geocoded from its locality text via
**OpenStreetMap Nominatim** (results are cached in `scraper/geocache.json` and
`scraper/taxocache.json` so repeat runs are fast and polite).

If your titles use a different pattern, adjust `parse_title()` in
`scraper/fetch_flickr.py` — its expected behaviour is pinned by tests:

```bash
python3 scraper/fetch_flickr.py --self-test
```

---

## Data format

`data/butterflies.json`:

```jsonc
{
  "generated": "2026-07-07T00:00:00Z",
  "source": "https://www.flickr.com/photos/robertgodden",
  "photographer": "Robert Godden",
  "is_sample": false,
  "albums":  [ { "id": "...", "title": "Uganda", "count": 120 } ],
  "species": { "Papilio dardanus": { "order": "...", "family": "Papilionidae",
                                      "genus": "Papilio", "gbifKey": 1795841,
                                      "count": 3 } },
  "photos":  [ { "id": "...", "species": "Papilio dardanus",
                 "location": "Bwindi, Uganda", "country": "Uganda",
                 "lat": -1.08, "lon": 29.67, "date": "2026-02-14",
                 "year": 2026, "albumTitle": "Uganda",
                 "urlThumb": "...", "urlLarge": "...",
                 "flickrPage": "..." } ]
}
```

The front-end computes everything else (filter lists, counts, the map) from
this file, and enriches species notes live from GBIF + Wikipedia in the
visitor's browser (cached in `localStorage`).

---

## Optional: test the front-end locally

```bash
# serve the static site
python3 -m http.server 8000
# open http://localhost:8000

# (optional) run the headless UI smoke test
npm install jsdom
node scraper/_test_frontend.js
```

---

## Credits

- Photographs © Robert Godden (from his Flickr collection).
- Taxonomy: [GBIF](https://www.gbif.org/). Species notes:
  [Wikipedia](https://www.wikipedia.org/) & [iNaturalist](https://www.inaturalist.org/).
- Basemap © OpenStreetMap contributors, © CARTO.
- Maps by [Leaflet](https://leafletjs.com/).
