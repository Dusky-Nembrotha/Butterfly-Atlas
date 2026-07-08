/* The Butterfly Atlas — front-end
   Vanilla JS. Reads data/butterflies.json (produced by the collector),
   renders a filterable specimen grid + world map, and enriches each
   species on demand from GBIF and Wikipedia (cached in localStorage). */
(function () {
  "use strict";

  var DATA_URL = "data/butterflies.json";
  var PAGE = 48;               // specimens rendered per lazy batch
  var CACHE_PREFIX = "atlas:";
  var CACHE_TTL = 1000 * 60 * 60 * 24 * 30; // 30 days

  // Family -> colour (used on cards and map). Falls back to a hash colour.
  var FAMILY_COLORS = {
    Papilionidae: "#c9a227",   // swallowtails — gold
    Pieridae:     "#6f9d3a",   // whites/sulphurs — green
    Nymphalidae:  "#1c5d99",   // brush-foots — morpho blue
    Lycaenidae:   "#7c5bd0",   // blues/hairstreaks — violet
    Riodinidae:   "#c1642d",   // metalmarks — copper
    Hesperiidae:  "#a0522d"    // skippers — sienna
  };

  var state = {
    all: [],
    species: {},
    albums: [],
    meta: {},
    filtered: [],
    rendered: 0,
    view: "specimens",
    map: null,
    cluster: null,
    miniMap: null,
    mapMarkers: [],       // {marker, key} for the current map draw, so tooltip visibility can be toggled by zoom
    filters: { q: "", family: "", genus: "", album: "", country: "", yearMin: "", yearMax: "", geoOnly: false, sort: "species", locationKey: "" }
  };

  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var el = function (tag, cls, txt) { var e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; };

  function famColor(fam) {
    if (fam && FAMILY_COLORS[fam]) return FAMILY_COLORS[fam];
    // deterministic fallback colour from string
    var h = 0, s = fam || "";
    for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
    return "hsl(" + h + ",42%,45%)";
  }

  /* ---------- image URLs ---------- */
  function thumb(p) { return p.urlThumb || ""; }
  function large(p) { return p.urlLarge || p.urlThumb || ""; }
  function full(p) { return p.urlOriginal || p.urlLarge || p.urlThumb || ""; }

  // Inline SVG hand-drawn "field sketch" — shown when a photo is missing,
  // framed as the collector's own ink drawing. Wing shape + ink vary by species.
  function placeholder(p, w, h) {
    var name = (p.species || "Indet.").replace(/&/g, "&amp;");
    var fam = p.family || "";
    // sepia inks with a faint family-tinted wash
    var ink = "#4a3a24";
    var washMap = {
      Papilionidae: "#b99127", Pieridae: "#7c8a3f", Nymphalidae: "#3f5c7a",
      Lycaenidae: "#6f5a94", Riodinidae: "#a8622f", Hesperiidae: "#8a5a34"
    };
    var wash = washMap[fam] || "#7a6a4a";
    // pick a wing silhouette by family so drawings differ
    var swallowtail = (fam === "Papilionidae");
    var pointed = (fam === "Pieridae" || fam === "Hesperiidae");

    // half-butterfly drawn once, mirrored via scale(-1,1)
    var fore, hind, tail = "";
    if (swallowtail) {
      fore = "M2,-6 C-30,-58 -92,-58 -104,-20 C-110,2 -78,14 -44,10 C-16,7 -2,-4 2,-14";
      hind = "M2,10 C-28,18 -66,26 -76,54 C-82,72 -60,84 -42,76 C-18,66 -2,34 2,16";
      tail = "M-58,72 C-60,90 -62,102 -66,114";
    } else if (pointed) {
      fore = "M2,-6 C-26,-56 -86,-50 -100,-14 C-106,6 -74,16 -46,10 C-18,5 -2,-6 2,-16";
      hind = "M2,10 C-24,16 -56,22 -66,44 C-72,60 -52,70 -36,64 C-14,56 -2,30 2,15";
    } else { // rounded nymphalid
      fore = "M2,-6 C-34,-54 -96,-44 -100,-6 C-102,20 -66,24 -40,16 C-16,9 -2,-2 2,-14";
      hind = "M2,12 C-26,20 -62,26 -70,50 C-76,68 -54,80 -34,72 C-12,63 -2,32 2,16";
    }

    var half =
      '<path d="' + fore + '"/>' +
      '<path d="' + hind + '"/>' +
      (tail ? '<path d="' + tail + '"/>' : '') +
      // venation
      '<path d="M-4,-8 C-30,-26 -60,-30 -86,-20" opacity=".55"/>' +
      '<path d="M-4,-4 C-28,-6 -54,-2 -74,6" opacity=".5"/>' +
      '<path d="M-2,14 C-24,26 -44,40 -56,56" opacity=".5"/>' +
      '<circle cx="-64" cy="40" r="4" opacity=".55"/>';

    var svg =
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 240" width="' + w + '" height="' + h + '" role="img" aria-label="' + name + ' — field sketch">' +
      '<rect width="320" height="240" fill="none"/>' +
      // faint pencil frame
      '<rect x="10" y="10" width="300" height="220" fill="none" stroke="' + ink + '" stroke-width="1" opacity=".28"/>' +
      '<g transform="translate(160,104)" fill="' + wash + '" fill-opacity="0.10">' +
        '<g>' + half + '</g><g transform="scale(-1,1)">' + half + '</g>' +
      '</g>' +
      '<g transform="translate(160,104)" fill="none" stroke="' + ink + '" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' +
        '<g>' + half + '</g><g transform="scale(-1,1)">' + half + '</g>' +
        // body
        '<path d="M0,-16 C3,6 3,34 0,58 C-3,34 -3,6 0,-16 Z" fill="' + ink + '" fill-opacity=".5"/>' +
        // antennae
        '<path d="M-2,-16 C-10,-34 -20,-42 -28,-45"/><path d="M2,-16 C10,-34 20,-42 28,-45"/>' +
        '<circle cx="-29" cy="-46" r="1.8" fill="' + ink + '"/><circle cx="29" cy="-46" r="1.8" fill="' + ink + '"/>' +
      '</g>' +
      '<text x="160" y="224" font-family="Georgia,serif" font-style="italic" font-size="15" fill="' + ink + '" text-anchor="middle">' + name + '</text>' +
      '<text x="160" y="28" font-family="Courier,monospace" font-size="8.5" letter-spacing="2.5" fill="' + ink + '" fill-opacity=".6" text-anchor="middle">SKETCH FROM LIFE — NO PLATE</text>' +
      '</svg>';
    return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
  }

  /* ---------- load ---------- */
  function load() {
    fetch(DATA_URL, { cache: "no-cache" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(init)
      .catch(function (err) {
        $("#grid").innerHTML = "";
        var e = el("p", "empty");
        e.innerHTML = "Couldn't load the collection data (<code>" + DATA_URL + "</code>). " +
          "If you've just deployed, run the collector to generate it. <br><small>" + String(err) + "</small>";
        $("#grid").appendChild(e);
      });
  }

  function init(data) {
    state.all = (data.photos || []).map(function (p, i) {
      p._i = i;
      p.year = p.year || (p.date ? parseInt(String(p.date).slice(0, 4), 10) : null);
      var sp = state ? null : null;
      return p;
    });
    state.species = data.species || {};
    state.albums = data.albums || [];
    state.meta = data;

    // backfill family/common onto photos from species table
    state.all.forEach(function (p) {
      var s = state.species[p.species];
      if (s) {
        p.family = p.family || s.family;
        p.genus = p.genus || s.genus;
        p.commonName = p.commonName || s.commonName;
      }
      if (!p.genus && p.species) p.genus = p.species.split(" ")[0];
    });

    if (data.photographer) { $("#photographer").textContent = data.photographer; }
    if (data.generated) {
      var d = new Date(data.generated);
      if (!isNaN(d)) $("#generatedAt").textContent = "Updated " + d.toISOString().slice(0, 10) + ".";
    }
    $("#sampleBanner").hidden = !data.is_sample;

    buildStats();
    buildFilterOptions();
    bindUI();
    readHash();
    apply();
  }

  /* ---------- stats ---------- */
  function buildStats() {
    var species = {}, families = {}, countries = {};
    state.all.forEach(function (p) {
      if (p.species) species[p.species] = 1;
      if (p.family) families[p.family] = 1;
      if (p.country) countries[p.country] = 1;
    });
    setStat("photos", state.all.length);
    setStat("species", Object.keys(species).length);
    setStat("families", Object.keys(families).length);
    setStat("countries", Object.keys(countries).length);
  }
  function setStat(k, v) { var n = $('[data-stat="' + k + '"]'); if (n) n.textContent = v.toLocaleString(); }

  /* ---------- filter option lists ---------- */
  function buildFilterOptions() {
    var fam = {}, gen = {}, ctry = {};
    state.all.forEach(function (p) {
      if (p.family) fam[p.family] = (fam[p.family] || 0) + 1;
      if (p.genus) gen[p.genus] = (gen[p.genus] || 0) + 1;
      if (p.country) ctry[p.country] = (ctry[p.country] || 0) + 1;
    });
    fillSelect($("#fFamily"), fam, true);
    fillSelect($("#fGenus"), gen, true);
    fillSelect($("#fCountry"), ctry, true);
    var albs = {};
    state.albums.forEach(function (a) { albs[a.title] = a.count || 0; });
    if (!Object.keys(albs).length) state.all.forEach(function (p) { if (p.albumTitle) albs[p.albumTitle] = (albs[p.albumTitle] || 0) + 1; });
    fillSelect($("#fAlbum"), albs, false);
  }
  function fillSelect(sel, counts, showCount) {
    if (!sel) return;
    var keys = Object.keys(counts).sort();
    keys.forEach(function (k) {
      var o = el("option", null, showCount ? (k + " (" + counts[k] + ")") : k);
      o.value = k;
      sel.appendChild(o);
    });
  }

  /* ---------- UI binding ---------- */
  function bindUI() {
    var deb;
    $("#search").addEventListener("input", function (e) {
      clearTimeout(deb); var v = e.target.value;
      deb = setTimeout(function () { state.filters.q = v.trim().toLowerCase(); apply(); }, 140);
    });
    [["#fFamily", "family"], ["#fGenus", "genus"], ["#fCountry", "country"], ["#fAlbum", "album"], ["#sort", "sort"]].forEach(function (pair) {
      var node = $(pair[0]); if (!node) return;
      node.addEventListener("change", function () { state.filters[pair[1]] = node.value; apply(); });
    });
    $("#fYearMin").addEventListener("input", function () { state.filters.yearMin = this.value; apply(); });
    $("#fYearMax").addEventListener("input", function () { state.filters.yearMax = this.value; apply(); });
    $("#fGeo").addEventListener("change", function () { state.filters.geoOnly = this.checked; apply(); });
    $("#resetFilters").addEventListener("click", resetFilters);
    $("#emptyReset").addEventListener("click", resetFilters);
    $("#viewSpecimens").addEventListener("click", function () { setView("specimens"); });
    $("#viewMap").addEventListener("click", function () { setView("map"); });

    // lazy loading sentinel
    if ("IntersectionObserver" in window) {
      var io = new IntersectionObserver(function (entries) {
        if (entries[0].isIntersecting) renderMore();
      }, { rootMargin: "600px" });
      io.observe($("#sentinel"));
    }
    window.addEventListener("popstate", function () { readHash(); syncControls(); apply(); });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") {
        if (document.getElementById("lightboxRoot")) closeLightbox(); else closeModal();
      }
    });
  }

  function resetFilters() {
    state.filters = { q: "", family: "", genus: "", country: "", album: "", yearMin: "", yearMax: "", geoOnly: false, sort: state.filters.sort, locationKey: "" };
    state.filters.locationLabel = "";
    syncControls(); apply();
  }
  function syncControls() {
    $("#search").value = state.filters.q;
    $("#fFamily").value = state.filters.family;
    $("#fGenus").value = state.filters.genus;
    $("#fCountry").value = state.filters.country;
    $("#fAlbum").value = state.filters.album;
    $("#fYearMin").value = state.filters.yearMin;
    $("#fYearMax").value = state.filters.yearMax;
    $("#fGeo").checked = state.filters.geoOnly;
    $("#sort").value = state.filters.sort;
  }

  /* ---------- filtering ---------- */
  function apply() {
    var f = state.filters;
    var terms = f.q ? f.q.split(/\s+/).filter(Boolean) : [];
    var ymin = f.yearMin ? parseInt(f.yearMin, 10) : null;
    var ymax = f.yearMax ? parseInt(f.yearMax, 10) : null;

    state.filtered = state.all.filter(function (p) {
      if (f.family && p.family !== f.family) return false;
      if (f.genus && p.genus !== f.genus) return false;
      if (f.country && p.country !== f.country) return false;
      if (f.album && p.albumTitle !== f.album) return false;
      if (f.geoOnly && !(hasCoords(p))) return false;
      if (f.locationKey && locationKey(p) !== f.locationKey) return false;
      if (ymin && (!p.year || p.year < ymin)) return false;
      if (ymax && (!p.year || p.year > ymax)) return false;
      if (terms.length) {
        var hay = [p.species, p.commonName, p.location, p.country, p.family, p.genus, p.albumTitle,
                   (p.tags || []).join(" ")].join(" ").toLowerCase();
        for (var i = 0; i < terms.length; i++) if (hay.indexOf(terms[i]) === -1) return false;
      }
      return true;
    });

    sortFiltered();
    writeHash();
    renderChips();
    renderCount();
    resetGrid();
    if (state.view === "map") drawMap();
  }

  function sortFiltered() {
    var s = state.filters.sort;
    state.filtered.sort(function (a, b) {
      // unidentified specimens always sink to the end, whatever the sort mode
      var aU = !a.species, bU = !b.species;
      if (aU !== bU) return aU ? 1 : -1;
      if (s === "date-desc") return (b.date || "").localeCompare(a.date || "");
      if (s === "date-asc") return (a.date || "").localeCompare(b.date || "");
      if (s === "country") return (a.country || "").localeCompare(b.country || "") || (a.species || "").localeCompare(b.species || "");
      return (a.species || "").localeCompare(b.species || "") || (a.date || "").localeCompare(b.date || "");
    });
  }

  function renderCount() {
    var n = state.filtered.length, total = state.all.length;
    var spp = {}; state.filtered.forEach(function (p) { if (p.species) spp[p.species] = 1; });
    $("#resultCount").innerHTML = "<b>" + n.toLocaleString() + "</b> specimen" + (n === 1 ? "" : "s") +
      " · " + Object.keys(spp).length + " species" + (n < total ? " (of " + total.toLocaleString() + ")" : "");
    $("#empty").hidden = n !== 0;
  }

  /* ---------- active filter chips ---------- */
  function renderChips() {
    var box = $("#activeChips"); box.innerHTML = "";
    var f = state.filters;
    var chips = [];
    if (f.q) chips.push(["Search: " + f.q, function () { f.q = ""; $("#search").value = ""; }]);
    if (f.family) chips.push(["Family: " + f.family, function () { f.family = ""; }]);
    if (f.genus) chips.push(["Genus: " + f.genus, function () { f.genus = ""; }]);
    if (f.country) chips.push(["Country: " + f.country, function () { f.country = ""; }]);
    if (f.album) chips.push(["Album: " + f.album, function () { f.album = ""; }]);
    if (f.yearMin || f.yearMax) chips.push(["Year: " + (f.yearMin || "…") + "–" + (f.yearMax || "…"), function () { f.yearMin = ""; f.yearMax = ""; }]);
    if (f.geoOnly) chips.push(["Mapped only", function () { f.geoOnly = false; }]);
    if (f.locationKey) chips.push(["Location: " + (f.locationLabel || "selected point"), function () { f.locationKey = ""; f.locationLabel = ""; }]);
    chips.forEach(function (c) {
      var chip = el("span", "chip"); chip.appendChild(document.createTextNode(c[0]));
      var x = el("button", null, "×"); x.setAttribute("aria-label", "Remove filter");
      x.addEventListener("click", function () { c[1](); syncControls(); apply(); });
      chip.appendChild(x); box.appendChild(chip);
    });
  }

  /* ---------- grid render (incremental) ---------- */
  function resetGrid() { $("#grid").innerHTML = ""; state.rendered = 0; renderMore(); }
  function renderMore() {
    if (state.view !== "specimens") return;
    var frag = document.createDocumentFragment();
    var end = Math.min(state.rendered + PAGE, state.filtered.length);
    for (var i = state.rendered; i < end; i++) frag.appendChild(card(state.filtered[i]));
    $("#grid").appendChild(frag);
    state.rendered = end;
  }

  function card(p) {
    var c = el("button", "specimen");
    c.style.setProperty("--fam", famColor(p.family));
    c.setAttribute("aria-label", (p.species || "Unidentified") + (p.location ? ", " + p.location : ""));
    var plate = el("div", "plate");
    var img = new Image();
    img.loading = "lazy"; img.alt = p.species || "butterfly specimen";
    img.src = thumb(p) || placeholder(p, 320, 240);
    img.onerror = function () { img.onerror = null; img.src = placeholder(p, 320, 240); };
    plate.appendChild(img);
    c.appendChild(plate);

    var lab = el("div", "label");
    var sci = el("span", "sci", p.species || "Unidentified");
    lab.appendChild(sci);
    if (p.commonName) lab.appendChild(el("span", "common", p.commonName));
    var meta = el("div", "meta");
    if (p.family) meta.appendChild(el("span", "fam-tag", p.family));
    if (p.year) meta.appendChild(el("span", null, String(p.year)));
    lab.appendChild(meta);
    if (p.location) lab.appendChild(el("span", "loc", p.location));
    if (hasCoords(p)) {
      lab.appendChild(el("span", "coord", fmtCoord(p.lat, p.lon)));
    }
    c.appendChild(lab);
    c.addEventListener("click", function () { openModal(p); });
    return c;
  }

  // Number.isFinite (unlike the global isFinite) does NOT coerce null/undefined
  // to 0, so records with no coordinates correctly show nothing instead of
  // a fake "0.000° N 0.000° E".
  function hasCoords(p) {
    return Number.isFinite(p.lat) && Number.isFinite(p.lon) && !(p.lat === 0 && p.lon === 0);
  }
  // Groups records that share (effectively) the same map point — used both
  // to aggregate overlapping map markers and to power the "view these
  // specimens" filter when a map location is clicked.
  function locationKey(p) {
    if (!hasCoords(p)) return "";
    return p.lat.toFixed(4) + "," + p.lon.toFixed(4);
  }

  function fmtCoord(lat, lon) {
    var a = Math.abs(lat).toFixed(3) + "° " + (lat >= 0 ? "N" : "S");
    var b = Math.abs(lon).toFixed(3) + "° " + (lon >= 0 ? "E" : "W");
    return a + "  " + b;
  }

  /* ---------- view toggle ---------- */
  function setView(v) {
    state.view = v;
    var spec = v === "specimens";
    $("#viewSpecimens").classList.toggle("is-active", spec);
    $("#viewMap").classList.toggle("is-active", !spec);
    $("#viewSpecimens").setAttribute("aria-selected", spec);
    $("#viewMap").setAttribute("aria-selected", !spec);
    $("#grid").hidden = !spec;
    $("#sentinel").hidden = !spec;
    $("#mapPanel").hidden = spec;
    if (spec) resetGrid(); else drawMap();
  }

  /* ---------- map ---------- */
  function drawMap() {
    if (typeof L === "undefined") { $("#mapPanel").innerHTML = "<p class='empty'>Map library unavailable (offline?).</p>"; return; }
    if (!state.map) {
      state.map = L.map("map", { worldCopyJump: true, scrollWheelZoom: true }).setView([10, 0], 2);

      // Clear, well-labelled base map (CARTO Voyager — free for this kind of
      // light use, and far easier to read than the previously-filtered tiles).
      var base = L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
        attribution: '© OpenStreetMap contributors © CARTO', subdomains: "abcd", maxZoom: 19
      }).addTo(state.map);

      // Satellite imagery laid over the top at partial opacity, so terrain
      // is visible without drowning out the map's labels and roads.
      var satellite = L.tileLayer(
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        { attribution: "Imagery © Esri", maxZoom: 19, opacity: 0.6 }
      ).addTo(state.map);

      L.control.layers(null, { "Satellite overlay": satellite }, { collapsed: false, position: "topright" }).addTo(state.map);

      state.cluster = L.markerClusterGroup ? L.markerClusterGroup({ maxClusterRadius: 42 }) : L.layerGroup();
      state.map.addLayer(state.cluster);
    }
    state.cluster.clearLayers();
    state.mapMarkers = [];

    // Aggregate records that share (almost) the same coordinates into a
    // single marker — many localities in this collection are geocoded to a
    // country/region centroid, so dozens of photos can land on the exact
    // same point. One labelled marker per location reads far better than a
    // pile of identical overlapping dots.
    var groups = {};
    state.filtered.forEach(function (p) {
      var k = locationKey(p);
      if (!k) return;
      if (!groups[k]) groups[k] = { lat: p.lat, lon: p.lon, photos: [], locNames: {}, families: {} };
      var g = groups[k];
      g.photos.push(p);
      var locName = p.location || p.country || "Unknown location";
      g.locNames[locName] = (g.locNames[locName] || 0) + 1;
      var fam = p.family || "Other";
      g.families[fam] = (g.families[fam] || 0) + 1;
    });

    var seenFam = {}, bounds = [];
    Object.keys(groups).forEach(function (k) {
      var g = groups[k];
      var count = g.photos.length;
      var label = topKey(g.locNames);
      var famKey = topKey(g.families);
      var col = famColor(famKey === "Other" ? "" : famKey);
      seenFam[famKey] = col;

      var radius = Math.min(8 + Math.sqrt(count) * 2.6, 26);
      var m = L.circleMarker([g.lat, g.lon], {
        radius: radius, color: "#fff", weight: 1.5, fillColor: col, fillOpacity: .85
      });
      m.bindTooltip(esc(label) + " (" + count + ")", { permanent: true, direction: "top", className: "loc-label", opacity: 0.92 });
      m.bindPopup(locationPopupHTML(g, k));
      m.on("popupopen", function (e) {
        var btn = e.popup._contentNode.querySelector("button[data-view]");
        if (btn) btn.addEventListener("click", function () { viewLocationOnBoard(k, label); });
      });
      state.cluster.addLayer(m);
      state.mapMarkers.push(m);
      bounds.push([g.lat, g.lon]);
    });

    updateLocationLabelVisibility();
    if (!state.map._labelZoomBound) {
      state.map.on("zoomend", updateLocationLabelVisibility);
      state.map._labelZoomBound = true;
    }

    buildLegend(seenFam);
    if (bounds.length) { try { state.map.fitBounds(bounds, { padding: [40, 40], maxZoom: 8 }); } catch (e) {} }
    setTimeout(function () { state.map.invalidateSize(); }, 60);
  }

  function topKey(counts) {
    var best = "", bestN = -1;
    Object.keys(counts).forEach(function (k) { if (counts[k] > bestN) { best = k; bestN = counts[k]; } });
    return best;
  }

  // Location labels only show once zoomed in enough to be legible and
  // non-overlapping; zoomed out, the marker cluster bubbles do that job instead.
  var LABEL_MIN_ZOOM = 6;
  function updateLocationLabelVisibility() {
    if (!state.map) return;
    var show = state.map.getZoom() >= LABEL_MIN_ZOOM;
    state.mapMarkers.forEach(function (m) {
      if (show) m.openTooltip(); else m.closeTooltip();
    });
  }

  function dateRangeLabel(photos) {
    var dates = photos.map(function (p) { return p.date; }).filter(Boolean).sort();
    if (!dates.length) return "date unknown";
    if (dates[0] === dates[dates.length - 1]) return dates[0];
    return dates[0] + " – " + dates[dates.length - 1];
  }

  function locationPopupHTML(g, key) {
    var speciesSet = {};
    g.photos.forEach(function (p) { if (p.species) speciesSet[p.species] = 1; });
    var speciesCount = Object.keys(speciesSet).length;
    var label = topKey(g.locNames);
    var country = topKey(g.photos.reduce(function (acc, p) { if (p.country) acc[p.country] = (acc[p.country] || 0) + 1; return acc; }, {}));
    return '<span class="sci" style="font-style:normal">' + esc(label) + '</span>' +
      (country && country !== label ? '<div class="pmeta">' + esc(country) + '</div>' : '') +
      '<div class="pmeta">' + esc(dateRangeLabel(g.photos)) + '</div>' +
      '<div class="pmeta">' + g.photos.length + ' photo' + (g.photos.length === 1 ? "" : "s") +
      ' · ' + speciesCount + ' species' + '</div>' +
      '<button data-view>View these specimens →</button>';
  }

  function viewLocationOnBoard(key, label) {
    state.filters.locationKey = key;
    state.filters.locationLabel = label;
    apply();
    setView("specimens");
    var results = $("#results"); if (results && results.scrollIntoView) results.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function buildLegend(map) {
    var box = $("#mapLegend"); box.innerHTML = "";
    var keys = Object.keys(map).sort();
    if (!keys.length) { box.innerHTML = '<span>No mapped records in this selection.</span>'; return; }
    keys.forEach(function (k) {
      var item = el("span", "legend-item");
      var dot = el("span", "legend-dot"); dot.style.background = map[k];
      item.appendChild(dot); item.appendChild(document.createTextNode(k));
      box.appendChild(item);
    });
  }

  /* ---------- modal + enrichment ---------- */
  function openModal(p) {
    var root = $("#modalRoot"); root.hidden = false; root.innerHTML = "";
    var backdrop = el("div", "modal-backdrop"); backdrop.addEventListener("click", closeModal);
    var modal = el("div", "modal");
    var close = el("button", "modal-close", "×"); close.setAttribute("aria-label", "Close"); close.addEventListener("click", closeModal);

    var grid = el("div", "modal-grid");
    var fig = el("div", "modal-fig");
    var img = new Image(); img.alt = p.species || "specimen";
    img.src = large(p) || placeholder(p, 800, 600);
    img.onerror = function () { img.onerror = null; img.src = placeholder(p, 800, 600); };
    img.tabIndex = 0;
    img.setAttribute("role", "button");
    img.setAttribute("aria-label", "View photo full screen");
    img.addEventListener("click", function () { openLightbox(p); });
    img.addEventListener("keydown", function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openLightbox(p); } });
    fig.appendChild(img);
    fig.appendChild(el("span", "zoom-hint", "⤢ Tap photo to view full screen"));

    var body = el("div", "modal-body");
    var det = el("div", "determination");
    det.appendChild(el("p", "eyebrow", "Determination"));
    var h = el("h2", "sci", p.species || "Unidentified"); det.appendChild(h);
    if (p.commonName) det.appendChild(el("p", "common", p.commonName));
    body.appendChild(det);

    // taxonomy chips (from species table; refined by GBIF below)
    var taxo = el("ul", "taxo"); taxo.id = "taxo"; body.appendChild(taxo);
    renderTaxo(taxo, state.species[p.species] || {});

    // field data
    var facts = el("dl", "factrow");
    addFact(facts, "Locality", p.location || "—");
    addFact(facts, "Country", p.country || "—");
    if (hasCoords(p)) addFact(facts, "Coordinates", fmtCoord(p.lat, p.lon));
    addFact(facts, "Date", p.date || "—");
    if (p.albumTitle) addFact(facts, "Album", p.albumTitle);
    body.appendChild(facts);

    var wiki = el("p", "wiki loading", "Looking up species notes…"); wiki.id = "wiki"; body.appendChild(wiki);

    // out-links
    var links = el("div", "outlinks");
    var q = encodeURIComponent(p.species || "");
    links.appendChild(gbifLink(p.species));
    links.appendChild(link("iNaturalist", "https://www.inaturalist.org/taxa/search?q=" + q));
    links.appendChild(link("Wikipedia", "https://en.wikipedia.org/wiki/" + (p.species || "").replace(/ /g, "_")));
    if (p.flickrPage) links.appendChild(link("On Flickr", p.flickrPage));
    body.appendChild(links);

    // same-species strip
    var same = state.all.filter(function (x) { return x.species && x.species === p.species; });
    if (same.length > 1) {
      var wrap = el("div", "same-species");
      wrap.appendChild(el("h3", null, same.length + " records of this species"));
      var strip = el("div", "same-strip");
      same.slice(0, 24).forEach(function (x) {
        var t = el("div", "thumb");
        var ti = new Image(); ti.loading = "lazy"; ti.alt = x.location || "";
        ti.src = thumb(x) || placeholder(x, 152, 120);
        ti.onerror = function () { ti.onerror = null; ti.src = placeholder(x, 152, 120); };
        t.appendChild(ti);
        t.addEventListener("click", function () { openModal(x); });
        strip.appendChild(t);
      });
      wrap.appendChild(strip); body.appendChild(wrap);
    }

    grid.appendChild(fig); grid.appendChild(body);
    modal.appendChild(close); modal.appendChild(grid);
    root.appendChild(backdrop); root.appendChild(modal);
    modal.appendChild(makeMiniMap(p));
    document.body.style.overflow = "hidden";
    close.focus();

    enrich(p.species);
  }

  function makeMiniMap(p) {
    if (!(hasCoords(p)) || typeof L === "undefined") return document.createComment("no-map");
    return document.createComment("mini-placeholder"); // mini map added after insert (needs layout)
  }

  function renderTaxo(ul, s) {
    ul.innerHTML = "";
    var ranks = [["Order", s.order], ["Family", s.family], ["Genus", s.genus]];
    ranks.forEach(function (r) {
      if (!r[1]) return;
      var li = document.createElement("li");
      li.innerHTML = r[0] + " <b>" + esc(r[1]) + "</b>";
      ul.appendChild(li);
    });
    if (!ul.children.length) ul.innerHTML = "<li>Taxonomy pending…</li>";
  }

  function addFact(dl, k, v) { dl.appendChild(el("dt", null, k)); dl.appendChild(el("dd", null, v)); }
  function link(label, href) { var a = el("a", null, label); a.href = href; a.target = "_blank"; a.rel = "noopener"; return a; }
  function gbifLink(species) {
    var s = state.species[species] || {};
    var a = s.gbifKey
      ? link("GBIF", "https://www.gbif.org/species/" + s.gbifKey)
      : link("GBIF", "https://www.gbif.org/taxon/search?q=" + encodeURIComponent(species || ""));
    a.id = "gbifOutlink";
    a.dataset.species = species || "";
    return a;
  }

  function closeModal() {
    var root = $("#modalRoot"); if (root.hidden) return;
    root.hidden = true; root.innerHTML = ""; document.body.style.overflow = "";
    state.miniMap = null;
  }

  /* ---------- lightbox: true full-screen photo view ---------- */
  function openLightbox(p) {
    closeLightbox(); // just in case
    var box = el("div", "lightbox");
    box.id = "lightboxRoot";
    var img = new Image();
    img.alt = p.species || "specimen";
    img.src = full(p) || placeholder(p, 1200, 900);
    img.onerror = function () { img.onerror = null; img.src = placeholder(p, 1200, 900); };
    var close = el("button", "lightbox-close", "×"); close.setAttribute("aria-label", "Close full screen");
    close.addEventListener("click", closeLightbox);
    box.appendChild(img);
    box.appendChild(close);
    box.addEventListener("click", function (e) { if (e.target === box) closeLightbox(); });
    document.body.appendChild(box);
    document.body.style.overflow = "hidden";
    close.focus();
    // best-effort true device fullscreen where supported; harmless if not.
    try { if (box.requestFullscreen) box.requestFullscreen().catch(function () {}); } catch (e) {}
  }
  function closeLightbox() {
    var box = document.getElementById("lightboxRoot");
    if (!box) return;
    try { if (document.fullscreenElement) document.exitFullscreen().catch(function () {}); } catch (e) {}
    box.remove();
    // restore body scroll lock state: keep locked if the record modal is still open
    var modalOpen = $("#modalRoot") && !$("#modalRoot").hidden;
    document.body.style.overflow = modalOpen ? "hidden" : "";
  }

  /* ---------- enrichment: GBIF + Wikipedia (cached) ---------- */
  function cacheGet(key) {
    try {
      var raw = localStorage.getItem(CACHE_PREFIX + key); if (!raw) return null;
      var o = JSON.parse(raw); if (Date.now() - o.t > CACHE_TTL) return null; return o.v;
    } catch (e) { return null; }
  }
  function cacheSet(key, v) { try { localStorage.setItem(CACHE_PREFIX + key, JSON.stringify({ t: Date.now(), v: v })); } catch (e) {} }

  function enrich(species) {
    if (!species) { setWiki("No species name on this record."); return; }
    var cached = cacheGet("sp:" + species);
    if (cached) { applyEnrichment(species, cached); return; }

    var out = {};
    var gbif = fetch("https://api.gbif.org/v1/species/match?name=" + encodeURIComponent(species))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (j && j.matchType !== "NONE") {
          out.order = j.order; out.family = j.family; out.genus = j.genus;
          out.gbifKey = j.usageKey || j.speciesKey;
        }
      }).catch(function () {});

    var title = species.replace(/ /g, "_");
    var wiki = fetch("https://en.wikipedia.org/api/rest_v1/page/summary/" + encodeURIComponent(title))
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (j) {
        if (j && j.extract && j.type !== "disambiguation") { out.wiki = j.extract; if (j.content_urls) out.wikiUrl = j.content_urls.desktop.page; }
      }).catch(function () {});

    Promise.all([gbif, wiki]).then(function () {
      // fall back to genus page if species page had nothing
      if (!out.wiki) {
        var genus = species.split(" ")[0];
        return fetch("https://en.wikipedia.org/api/rest_v1/page/summary/" + encodeURIComponent(genus))
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (j) { if (j && j.extract) out.wiki = j.extract + " (genus)"; })
          .catch(function () {});
      }
    }).then(function () {
      cacheSet("sp:" + species, out);
      applyEnrichment(species, out);
    });
  }

  function applyEnrichment(species, out) {
    // merge into species table so taxonomy chips + future opens improve
    var s = state.species[species] = state.species[species] || {};
    ["order", "family", "genus", "gbifKey"].forEach(function (k) { if (out[k] && !s[k]) s[k] = out[k]; });
    var ul = $("#taxo"); if (ul) renderTaxo(ul, s);
    setWiki(out.wiki || "No encyclopaedic summary found for this species.");
    var gl = $("#gbifOutlink");
    if (gl && gl.dataset.species === species && s.gbifKey) {
      gl.href = "https://www.gbif.org/species/" + s.gbifKey;
    }
  }
  function setWiki(text) { var w = $("#wiki"); if (w) { w.textContent = text; w.classList.remove("loading"); } }

  /* ---------- mini map after modal is in DOM ---------- */
  var _origOpen = openModal;
  openModal = function (p) {
    _origOpen(p);
    if (hasCoords(p) && typeof L !== "undefined") {
      var host = el("div", "modal-mini"); host.id = "modalMini";
      var body = $(".modal-body"); if (body) body.appendChild(host);
      setTimeout(function () {
        try {
          state.miniMap = L.map(host, { zoomControl: false, attributionControl: false, dragging: true, scrollWheelZoom: false }).setView([p.lat, p.lon], 5);
          L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", { subdomains: "abcd" }).addTo(state.miniMap);
          L.circleMarker([p.lat, p.lon], { radius: 8, color: "#fff", weight: 2, fillColor: famColor(p.family), fillOpacity: 1 }).addTo(state.miniMap);
          state.miniMap.invalidateSize();
        } catch (e) {}
      }, 40);
    }
  };

  /* ---------- URL hash state ---------- */
  function writeHash() {
    var f = state.filters, parts = [];
    ["q", "family", "genus", "country", "album", "yearMin", "yearMax", "sort"].forEach(function (k) {
      if (f[k]) parts.push(k + "=" + encodeURIComponent(f[k]));
    });
    if (f.geoOnly) parts.push("geo=1");
    var h = parts.join("&");
    var nu = location.pathname + (h ? "#" + h : "");
    history.replaceState(null, "", nu);
  }
  function readHash() {
    var h = location.hash.replace(/^#/, ""); if (!h) return;
    h.split("&").forEach(function (kv) {
      var i = kv.indexOf("="); if (i < 0) return;
      var k = kv.slice(0, i), v = decodeURIComponent(kv.slice(i + 1));
      if (k === "geo") state.filters.geoOnly = v === "1";
      else if (k in state.filters) state.filters[k] = v;
    });
    syncControls();
  }

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) { return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]; }); }

  document.addEventListener("DOMContentLoaded", load);
})();
