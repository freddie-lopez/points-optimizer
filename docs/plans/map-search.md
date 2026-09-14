# Plan: map-based search home page

Architect, 2026-09-14, on feature/map-search (forked from feature/ui-restyle at 56742af).
Inputs: docs/design/UI-BRIEF.md (§2, §5, §8 binding); docs/design/map-ref/{Main,RegionPick,
RoutePicked}.{dc.html,png} (approved, direction C); docs/design/map-ref/topo2svg.py,
land-50m.json, airportsdata-20260905-airports.csv (+ METADATA); docs/plans/ui.md §4.5–4.8;
docs/plans/ui-restyle.md; src/ui/static/*; src/ui/{api,engine,server,serialize}.py;
src/seats_client.py; src/trips_tools.py; src/regions.py; src/trip_builder.py;
tests/test_ui_static_rules.py; tests/test_ui_security.py; docs/test-reports/ui-probes/
{conftest.py,scenarios.py,drive.js}. Nobody could be asked; every call below is stated so
it can be reversed.

## 1. Summary

The Search tab becomes a two-column page: the existing search strip, restyled into a
360px column with autofill on From/To, and a pannable, zoomable SVG world map on the
right that plots only the airports Seats.aero itself tracks. Clicking an airport fills
From, then To; a dashed great-circle line joins the two picks; the search then goes
through the unchanged preflight → confirm → run flow and the results replace the map in
the right pane. The land is one committed `<path>` generated from the world-atlas 50m
TopoJSON by a committed script (with a test that the file matches the generator). The
airport set is NOT hand-typed: a Mac-side tool (`python -m src.map_tools capture-hubs`,
same shape as `src.trips_tools`) asks Seats.aero for its routes per source and derives
the hub set with stated thresholds and full provenance. Until that capture has run the
map ships with an empty hub file and says so in the pane, in words. No engine, CLI,
serializer or API-route change; no new dependency; CSP and static rules unchanged
except for two new same-origin static files and one new script. Existing wording,
testids and the 400px behaviour of everything outside the map are untouched; the map
hides below 900px.

## 2. Assumptions & decisions

1. **The map only offers airports the engine will accept.** `validate_iata` refuses any
   code not in `data/airports.csv` (141 rows, four of which are metro codes NYC/LON/PAR/
   TYO with no coordinates). A map that offered LIS and then had preflight refuse it would
   be a click into a wall. So the capture tool stamps each hub `searchable: true|false`
   (= in `data/airports.csv` at capture time), the map plots only `searchable` hubs, and
   the pane footer states the count it did not plot and why. `data/airports.csv` is NOT
   edited by this round (a region assignment selects a surcharge band; that is an engine
   data decision for a later round, and the tool prints the candidate rows so Tsuki can
   make it). An offline subcommand `mark-searchable` recomputes the flag without a call;
   a test pins the flag to the CSV so the two cannot drift silently.
2. **Hub thresholds**: an airport is a hub when it appears as origin or destination in
   the routes of **≥ 3 sources AND ≥ 20 routes in total** (defaults `--min-sources 3
   --min-routes 20`). Why: one source's quirky station (a JetBlue Caribbean outstation, an
   Ethiopian domestic) is not "a hub Seats.aero covers", three programmes agreeing is;
   20 routes filters the ≥3-source long tail. Both are guesses made without data, so the
   tool prints a histogram (hubs at min-sources 1/2/3/4/5 × min-routes 5/10/20/50) before
   asking to write, records the thresholds used in `_meta`, and warns above 400 hubs (the
   performance target is "several hundred"). Never a fixed top-N: a rank cut has no
   meaning a reader can check; a threshold does.
3. **Routes endpoint**: `GET https://seats.aero/partnerapi/routes?source=<code>`, header
   `Partner-Authorization`, response fields `ID, OriginAirport, OriginRegion,
   DestinationAirport, DestinationRegion, NumDaysOut, Distance, Source` - VERIFIED from
   developers.seats.aero/reference/get-routes-1.md (fetched 2026-09-14 from the sandbox);
   NOT yet observed in a response. Whether it paginates and whether it counts against the
   1,000/day budget are UNVERIFIED: the tool assumes one call per source, counts every
   request, accepts either a JSON list or `{"data": [...]}`, treats any other shape as
   that source being `unreadable` (recorded, never a hub), and if a dict answer carries
   `hasMore`/`cursor` it records the source as `incomplete` and does not follow it. 28
   sources are in `SEATS_AERO_SOURCES`, so the promise is "at most 28 calls".
4. **Empty-data state ships.** `data/hubs.json` is committed with `hubs: []` and a `_meta`
   saying no capture has run. The map then renders land, pan/zoom and the sentence in
   §4.9 #1, and autofill is inert (typing works exactly as today). No placeholder list,
   ever - a hand-typed "SFO, LHR, …" is exactly the invented finding §2 forbids.
5. **Coordinates** come from airportsdata 20260905 (MIT) filtered to rows with an IATA
   code: 7,884 rows, committed as `data/airportsdata_iata.csv` (~495 KB, columns
   `iata,icao,name,city,country,lat,lon`, produced by `tools/filter_airportsdata.py`
   from the full CSV, which stays under docs/design/map-ref/ as the source of record).
   The capture tool joins on IATA and writes coordinates into `hubs.json`; the browser
   never fetches the 495 KB file. A hub with no row (a metro code) gets `lat: null` and
   is counted as "no coordinates", not plotted. Neither CSV is hand-edited; a test asserts
   the filtered file equals the filter's output.
6. **Land**: world-atlas 2.0.2 `land-50m.json` ("medium" - the mockups used 110m), Miller
   cylindrical, lat clamped to [-60, 83] (Antarctica dropped), rings under 8 points
   dropped, a **4000×2080 viewBox with integer relative coordinates** (`M x,y l dx,dy …
   Z`). Measured: 230,686 bytes (1,115 rings, 54,434 points). Budget: the committed file
   must be ≤ 400 KB (test). Precision: at max zoom 8× in a 1,080px pane one unit is 2.2px,
   so the integer rounding is at most ±1px - invisible against a 0.5px stroke. The file is
   `src/ui/static/land.json` = `{"w":4000,"h":2080,"lat_min":-60,"lat_max":83,"d":"M…"}`
   so map.js and the generator share one projection definition and one test compares
   the whole file to `tools/build_land_path.py`'s output byte for byte.
7. **map.js is a separate file**, not a section of app.js. app.js is 1,687 lines; the map
   (projection, viewBox pan/zoom, clustering, popover, markers, route line, data load)
   is ~550-650 lines that touch nothing else in app.js, and app.js's own edits are ~150
   lines (renderSearch restructure, autofill, glue). Cost: one more `<script>`, one more
   STATIC_FILES entry, and `test_ui_static_rules.py::test_the_page_loads_only_its_own_
   script_and_style` changes from `["/static/app.js"]` to `["/static/map.js",
   "/static/app.js"]` - the only existing test this round edits, and the manager must see
   it. The banned-API and no-changelog scans in that file are parametrised over both JS
   files. map.js is an IIFE that sets exactly one global, `window.POMap`; app.js never
   reads anything else from it, and map.js never touches app.js's state or DOM outside
   the host it is given.
8. **The right pane is the map OR the results, never both.** `S.search.pane` is
   `"map"` while there is no run and nothing in flight, `"result"` once a run exists or
   is in flight; a `Show map` / `Show results` button (§4.9) toggles when a run exists.
   The map controller is mounted once and kept across renders (only `setPicks`/
   `setStatus` are called), so a keystroke never rebuilds 300 markers.
9. **Picking never touches the wording rules.** The inputs `search-from`/`search-to`
   stay text inputs (the probes `fill()` them); a pick writes the IATA code into the input
   and into `S.search`; the chips-with-× in the mockup are NOT built (a chip would move the
   testid off an input). Clearing = deleting the text. The typed value is authoritative:
   autofill never substitutes on blur, and a code the map does not know is still sent
   exactly as typed (the footer says it is not on the map).
10. **Click order rule**: a click fills the first empty of From, To; when both are filled
    it replaces To. Clicking the airport already in From while To is empty is a no-op
    (origin == destination is refused by preflight anyway; the map does not pre-empt the
    engine's sentence). Nothing zooms on pick.
11. **Clusters**: computed in screen pixels at the current zoom, greedy by route count:
    a hub within 18px of an existing cluster centre joins it. A cluster of 2+ draws the
    count (mockup); a click on it opens the list popover, never zooms. A single hub at
    zoom ≥ 3 also draws its IATA label (mockup RegionPick). Reclustering runs on zoom
    change only (rAF-throttled); panning changes the viewBox and nothing else.
12. **Route line** is a great-circle polyline (64 points, split at the antimeridian),
    dashed, no animation, drawn in `--accent2` exactly as the approved RoutePicked mockup
    does. This is the one non-button use of coral in the app; it is dashed and 1.5px so
    it cannot read as a button. If the manager prefers §4.7's rule ("coral = the ONE
    primary action") over the mockup, the line becomes `--accent` - a one-token change.
13. **Below 900px** the map pane is `display:none`, the column is the whole width and
    the strip is the existing wrapping strip. The 400px probes fill the same testids and
    click the same button; the idle `search-state` note stays rendered.
14. **"Minimal" excludes**: country/ocean labels; graticule; search-by-drag or box
    select; hover tooltips; distance/bearing in the popover ("24 km W" in the mockup
    needs a reference point the data does not carry); airport "type" glyphs; mini-map;
    URL state for the view; touch gestures beyond what pointer events give for free;
    the "One-way · Jan 15 2027 · economy · 1 adult" subtitle (the existing
    `search-window` line already states the window); the "Search award space" button
    label (the button is the existing `Run search`; wording is pinned).
15. **The map never implies availability**: dots mean "Seats.aero has routes touching
    this airport in ≥3 programmes at capture time" and the legend says exactly that; the
    line is the user's pick and the footer sentence says so; nothing on the map changes
    after a search (the results pane replaces it). Dot colour is uniform `--accent`; the
    only other forms are the picked dot (filled + halo, mockup) and the cluster count.
16. **Static serving**: `STATIC_FILES` values become `(Path, content_type)`;
    `/static/hubs.json` maps to `DATA_DIR / "hubs.json"` and `/static/land.json` to the
    static dir. The server keeps reading text, running the key-egress filter (`in` over
    230 KB ≈ free) and `Cache-Control: no-store`. A missing hubs.json is a 404 and map.js
    treats a 404 or unreadable JSON as the "unreadable" state (§4.9 #2), never as empty.
17. **Autofill scope** = the plotted hub set (searchable, with coordinates) - the same
    set the map offers, so "everything the map can do the keyboard can do". Ranking:
    exact code, then code prefix, then city prefix, then name/city substring; ties by
    route count desc; max 8 rows. Two more fields are indexed for matching only:
    `name` and `city` lower-cased once at load.
18. **Accessibility floor**: the SVG is `role="img"` with an `aria-label`; the map
    controls are real `<button>`s with aria-labels; the popover is `role="listbox"` of
    `<button role="option">`s, Esc closes and returns focus to the cluster's marker; the
    autofill is `role="listbox"` driven by ArrowUp/Down/Enter/Esc with
    `aria-activedescendant`. Keyboard users pick by typing; the map is an accelerator.
19. **Docs**: docs/plans/ui.md §4.8 gains an `S0 Map` block and §4.5 a line for the two
    static files; README "Local UI" gains a "Map search" paragraph and a `src.map_tools`
    section mirroring the `trips_tools` one (`test_readme_local_ui_is_accurate` reads
    that section - the coder runs it after editing). UI-BRIEF.md is not edited.
20. **Zoom-1 letterbox stays (decision C, manager review).** At z=1 the whole world is
    shown, letterboxed with ocean above and below in a wide pane, rather than the
    mockup's `0 30 1000 440` crop (which cannot be panned at z=1, coder deviation 1).
    Tsuki accepted the built page as it matches the last approved mockup.

## 3. Out of scope

Phone width (the map hides < 900px; nothing else changes there); hotels; multi-city or
return routing; anything in src/main.py, optimizer, formatter, seats_client.py,
trip_builder.py, regions.py or `data/airports.csv`; new API routes or `/api/state`
fields; the Trips tab; wording of any existing string; a light theme; touch gestures;
the tester's probes and reports (never edited to pass).

## 4. Architecture

### 4.1 Files

| File | New/edit | What |
|---|---|---|
| `src/ui/static/map.js` | new (~600 lines) | `window.POMap`: data load, projection, viewBox pan/zoom, markers, clustering, popover, route line, suggest() |
| `src/ui/static/app.js` | edit (~150 lines, §4.6) | `renderSearch` split into column + pane; autofill; pick glue; `S.search.pane`, `S.map` |
| `src/ui/static/app.css` | edit (~130 lines) | `.search-grid` columns, `.strip.column`, `.map-pane`, markers, popover, suggest list, 900px query |
| `src/ui/static/index.html` | edit | `<script src="/static/map.js" defer>` before app.js; `#search-pane` with `#map-pane` and `#search-result` |
| `src/ui/static/land.json` | new, generated (≤ 400 KB) | `{"w","h","lat_min","lat_max","d"}` |
| `tools/build_land_path.py` | new (~100 lines) | topo2svg.py rewritten as a module with `build(topojson_path) -> dict` and `main()`; writes land.json |
| `tools/filter_airportsdata.py` | new (~40 lines) | full CSV → `data/airportsdata_iata.csv` |
| `data/airportsdata_iata.csv` | new (~495 KB) | 7,884 IATA rows: `iata,icao,name,city,country,lat,lon` |
| `data/hubs.json` | new, committed EMPTY | shape in §4.2; written only by `src.map_tools` |
| `src/map_tools.py` | new (~350 lines) | `capture-hubs`, `mark-searchable` |
| `src/ui/server.py` | edit (~8 lines) | `STATIC_FILES` values as `(Path, ctype)`; two entries |
| `tests/test_ui_static_rules.py` | edit | scripts list; parametrise scans over app.js + map.js; new testids |
| `tests/test_map_assets.py` | new | land.json == generator output, size cap, hubs.json shape + `searchable` consistency, airportsdata filter |
| `tests/test_map_tools.py` | new | the capture tool with a stubbed transport (§7) |
| `tests/test_ui_security.py` | edit (2 lines) | the new static paths are served with the same headers; nothing else |
| `docs/plans/ui.md`, `README.md` | edit | §2 D19 |

### 4.2 `data/hubs.json`

```
{
  "_meta": {
    "captured_by": "src.map_tools capture-hubs" | null,
    "captured_at": "2026-09-20T18:02:11Z" | null,
    "endpoint": "/partnerapi/routes" | null,
    "sources_asked": ["aeroplan", …],          // every code in SEATS_AERO_SOURCES unless --sources
    "sources_ok": ["aeroplan", …],
    "sources_failed": {"smiles": "HTTP 500", "azul": "unreadable: top level is a string"},
    "sources_incomplete": ["united"],           // answered with hasMore/cursor; not followed
    "routes_seen": 48213,
    "thresholds": {"min_sources": 3, "min_routes": 20},
    "airport_table": "airportsdata 20260905 (MIT) via data/airportsdata_iata.csv",
    "engine_airports_sha256": "<sha256 of data/airports.csv when searchable was computed>",
    "searchable_marked_at": "…Z",
    "key_redacted": true,
    "synthetic": false,
    "note": "No capture has run; the map plots nothing until python -m src.map_tools capture-hubs is run on a machine with a Seats.aero key."   // committed empty file only
  },
  "hubs": [
    {"iata": "SFO", "name": "San Francisco Intl", "city": "San Francisco", "country": "US",
     "lat": 37.618972, "lon": -122.374889, "routes": 812, "sources": ["aeroplan", "united", …],
     "regions": ["North America"], "searchable": true}
  ]
}
```

Invariants (tested): every hub has ≥ 1 source and `routes ≥ 1`; `iata` matches
`^[A-Z0-9]{3}$`; `searchable == (iata in regions.known_airports())`; the file has no
key material; `hubs` is sorted by `iata`; `_meta.note` is present only when `hubs` is
empty and `captured_at` is null.

### 4.3 Data flow

```
data/hubs.json ──GET /static/hubs.json──▶ map.js POMap.load()   (once per page)
src/ui/static/land.json ──GET /static/land.json──▶ map.js       (once per page)
                                     │
   app.js renderSearch ──POMap.mount(host,{onPick})──▶ controller
   app.js input handlers ──ctl.setPicks(fromHub|null, toHub|null, {fromTyped, toTyped})
   map click ──onPick(iata)──▶ app.js writes S.search.from/to, re-renders the column
   Run search ──POST /api/search/preflight──▶ unchanged; results render in #search-result
```

### 4.4 The client state machine (app.js owns it; map.js only reflects it)

```
pane: "map" ──(preflight confirmed, run starts)──▶ "result" ──(Show map)──▶ "map" ──(Show results)──▶ "result"
picks:  idle ──click A──▶ from=A ──click B──▶ from=A,to=B ──click C──▶ from=A,to=C
        any state ──edit input──▶ recomputed from the two input values (a 3-char code that is a plotted hub = a pick; anything else = no mark for that field)
```

There is no separate "map state": `S.search.from/to` are the state, the map is a view
of them, and `Escape` (existing handler) closes popover → suggestions → drawer in that
order. Out-of-order actions cannot exist because the map has no memory of its own.

### 4.5 viewBox math (map.js)

- Base: `W=4000, H=2080` from land.json. View = `{x, y, z}` with `z ∈ [1, 8]`; the SVG's
  `viewBox = "x y W/z H/z"`, `preserveAspectRatio="xMidYMid meet"` (the pane is wider
  than 4000:2080 at most desktop sizes; ocean fills the letterbox, the mockup's crop
  `0 30 1000 440` is reproduced by an initial `y` offset of 60 units and z=1).
- Projection (shared with the generator, both implement it from the same four numbers):
  `X = (lon+180)/360·W`; `Y_m(φ) = 1.25·ln(tan(π/4 + 0.4·φ))`; `Y = (Y_m(lat_max) − Y_m(lat)) / (Y_m(lat_max) − Y_m(lat_min)) · H`, lat clamped to `[lat_min, lat_max]`.
- Screen ↔ map: `u = svg.getScreenCTM().inverse()`; a pointer event's map point is
  `pt.matrixTransform(u)`. Zoom about a point `p` by factor `f`: `x' = p.x − (p.x − x)/f`,
  same for y, `z' = clamp(z·f)`. Wheel: `f = 1.2^(−deltaY/100)`; buttons `+`/`−`: f=1.5
  about the pane centre; reset: `{0, 60, 1}`; double-click: f=2 about the pointer.
- Pan: pointerdown captures; move applies `dx/dy` in map units (`Δscreen · (W/z)/paneW`);
  clamp so the view never leaves `[0,W]×[0,H]`; pointerup with total movement < 4px is a
  click (hit-test below). Drag sets `cursor: grabbing` via a class.
- Marker scale: each marker `<g transform="translate(X Y) scale(k)">` with `k = (W/z)/
  paneW` so an `r=4` circle is 4px at every zoom; on zoom, one pass updates every
  marker's `k` and then reclusters. The land `<path>` has `vector-effect: non-scaling-
  stroke` and a `.5px` stroke.

### 4.6 Clustering and hit-test

- Input: plotted hubs with projected `(X, Y)` computed once at load. Per zoom: screen px
  per unit `s = paneW·z/W`; sort hubs by `routes` desc; for each hub, find the first
  cluster whose centre is within 18px (`|ΔX·s| ≤ 18 && |ΔY·s| ≤ 18`, Chebyshev, no sqrt);
  join it (centre stays the first member's) or start one. O(n·clusters) with n ≤ ~400.
- Output: `<g class="mk" data-iata="SFO" data-testid="map-hub-SFO">` for a single (circle
  r=3.2, `fill: var(--bg); stroke: var(--accent)`; label `<text>` at zoom ≥ 3, IATA only,
  `textContent`), or `<g class="mk cl" data-testid="map-cluster-<leadIATA>">` (circle r=9,
  `fill: var(--raised)`, the count as `<text>`). Picked hub: class `pick` → filled
  `--accent` r=4.2 plus a halo circle r=8.3 at opacity .18 (mockup); the label always shown.
- Hit-test: the `<g>` carries the handler; the SVG's `pointerup` reads
  `e.target.closest("g.mk")` to decide click-vs-drag; nothing is done on `pointerdown`
  except capture. `tabindex=0` on each `g.mk` and Enter/Space fire the same handler.
- Popover (`<div class="map-pop" role="listbox" data-testid="map-cluster-list">`, HTML,
  positioned absolutely inside `.map-pane` at the cluster's screen point, clamped 8px
  inside the pane): header = title + subline (§4.9), a close `<button aria-label="Close list">`;
  rows = `<button role="option" data-testid="map-pick-<IATA>">` with `<span class="mono">
  IATA</span>` + name + `<span class="dim">City, CC</span>`, all `textContent`. Title rule:
  the shared `city` when every member has the same one, else `"{n} airports near {city
  of the lead hub}"`. Sorted by routes desc. Click/Enter → `onPick(iata)`, close, focus
  returns to the cluster marker. Any pan/zoom closes it.

### 4.7 app.js edits (enumerated)

1. `S.search` gains `pane: "map"`, `sugg: null` (`{key, items, active}`); new `S.map =
   {ctl: null, data: null}`.
2. `renderSearch` builds the column into `#search-main`: heading `h1` + subline (§4.9 #9/10),
   the strip (`panel strip column`, same `inp()` factory, same testids, same handlers plus
   the two autofill handlers), `search-window`, field errors, then the idle/no-key
   `search-state` note when `pane === "map"`. Then `renderSearchPane()`: if `pane ===
   "map"` → `#search-result` cleared and hidden, `#map-pane` shown and `ensureMap()`; else
   → `#map-pane` hidden and the existing result rendering (busy note / `renderSearchResult`)
   into `#search-result`. The `Show map`/`Show results` button (`search-pane-toggle`) is
   in the column when `q.run` exists.
3. `ensureMap()`: if `S.map.ctl` is null and `window.POMap` exists, `POMap.load().then(d =>
   { S.map.data = d; S.map.ctl = POMap.mount($("map-pane"), {onPick: onMapPick}); syncMap(); })`.
   `syncMap()` = `ctl.setPicks(POMap.hub(q.from), POMap.hub(q.to), q.from, q.to)`; the
   status sentence is chosen by map.js from what it is given (§4.9 #3-#6).
4. `onMapPick(iata)`: D10 rule on `q.from/q.to`; `q.errors = {}`; `renderSearch()` (rebuilds
   the column, not the map); focus goes to the To input after a From pick, to `search-run`
   after a To pick.
5. Autofill: the `input` handler also calls `showSuggest(key, value)` →
   `POMap.suggest(value, 8)`; renders `<div role="listbox" data-testid="search-suggest-
   <key>">` directly after the field with `<button role="option" data-testid="suggest-
   <IATA>">`; keydown handles ArrowDown/Up/Enter/Escape (Enter with an active row picks it;
   Enter with no active row = the existing `doSearch()`); `blur` hides after 120ms so a
   click on a row lands. Picking writes the code (uppercase) to the input and `q[key]`,
   calls `updateWindow()` and `syncMap()`. When `POMap` is absent or has no data,
   `suggest` returns `[]` and nothing renders.
6. `doSearch` confirm callback: set `q.pane = "result"` before the run render (the pane
   shows the busy note); on completion `q.pane` stays `"result"`.
7. The global Escape handler gains, before the drawer cases: popover open → close it;
   suggestions open → close them.
8. Nothing else: no `innerHTML`, no wording change, no testid renamed. `x || 0` and
   `?? 0` remain banned in both files (route counts are integers from JSON; an absent one
   is rejected at load, not defaulted).

### 4.8 `src/map_tools.py`

```
python -m src.map_tools capture-hubs [--sources aeroplan,united,…] [--min-sources 3]
    [--min-routes 20] [--yes] [--out data/hubs.json] [--raw-dir DIR] [--api-key KEY]
python -m src.map_tools mark-searchable [--file data/hubs.json]
```

Exit codes as trips_tools: 0 written and every source answered; 1 nothing written
(usage, no key, declined, stdin closed, every source failed, 0 hubs met the thresholds,
key material in the output); 5 written with gaps (≥1 source failed/unreadable/incomplete;
the file carries them in `_meta` and the map footer will not know - the console says so).

Sequence: `config.resolve_key` (its `describe()` printed) → the call-count line (§4.9
#14) → `Continue? [y/N]` unless `--yes` (EOF/Ctrl-C = refusal, as trips_tools) → for
each source in order: `requests.get(f"{SeatsClient.BASE_URL}/routes", params={"source":
code}, headers={"Partner-Authorization": key, "Accept": "application/json"},
timeout=30)`; every request is counted on `SeatsClient` (`_count_call()` - the tool
prints the same "spent N of 1,000" numbers a trip run does) and, when `--raw-dir` is
given, the verbatim body is written to `<raw-dir>/routes_<source>.json` after the key
check (default: not written; raw bodies are large and are not fixtures). Per-source
outcomes: `ok(n routes)`, `failed(HTTP s | Timeout | ConnectionError)`, `unreadable
(reason)`, `incomplete`. A 429 stops the loop (sticky, as the client does); the rest are
still recorded as `failed("not asked: rate limited")`. Then: derive hubs (an airport
code is counted once per route side; codes failing `^[A-Z0-9]{3}$` are counted as
`rows_dropped` and never become hubs), print the histogram, apply thresholds, join
coordinates from `data/airportsdata_iata.csv`, compute `searchable`, print `N hubs (K
searchable, M without coordinates, J not in data/airports.csv)` and the candidate
`iata,name,country` lines for the J unsearchable ones (for a later airports.csv
decision), `assert_no_key_material` on the JSON text, write atomically (tmp + rename),
print `wrote data/hubs.json` copyable. Transport is injectable (`get=requests.get`) so
the tests drive every path without a socket.

`mark-searchable`: reads the file, recomputes `searchable` and `engine_airports_sha256`,
rewrites; 0 calls; prints the count that changed.

### 4.9 New user-facing strings (verbatim; the tester pins these)

| # | Where | String |
|---|---|---|
| 1 | map footer, `hubs: []` | `NO AIRPORT DATA - run "python -m src.map_tools capture-hubs" on your Mac. The map plots nothing until then.` |
| 2 | map footer, fetch/parse failed | `AIRPORT DATA UNREADABLE - /static/hubs.json could not be read ({reason}). The map plots nothing.` |
| 3 | map footer, idle | `Click an airport to set From.` |
| 4 | map footer, From set | `Click an airport to set To.` |
| 5 | map footer, both set | `The line is your route, not availability. Award space appears only after the search runs.` |
| 6 | appended to 3-5 when a typed code is not plotted | ` · {CODE} is not on this map. It can still be searched.` |
| 7 | map footer provenance (data present) | `{N} airports Seats.aero tracked on {YYYY-MM-DD} · {K} not plotted ({J} not in data/airports.csv, {M} without coordinates)` (the parenthesis is omitted when K = 0) |
| 8 | legend | `airport Seats.aero tracks` · `several airports - click to list` · `Drag to pan · scroll to zoom` |
| 9 | column heading | `Where are you flying?` |
| 10 | column subline | `Type an airport or city, or pick it on the map.` |
| 11 | popover subline | `{n} airports Seats.aero tracks · pick one` |
| 12 | popover title, mixed cities | `{n} airports near {City}` |
| 13 | buttons / aria | `Zoom in` · `Zoom out` · `Reset view` · `Close list` · `Suggestions` · `Show map` · `Show results` · SVG aria-label `World map of airports Seats.aero tracks` |
| 14 | tool | `This will make at most {n} Seats.aero API call(s): one GET /partnerapi/routes per source ({n} sources). This process has spent {s} of 1,000; Seats.aero also counts your other runs today, which this tool cannot see.` |
| 15 | tool refusals | `declined. No call was made and nothing was written.` · `no answer at the prompt (stdin closed or interrupted). No call was made and nothing was written. Pass --yes to run without the question.` · `every source failed ({n} of {n}); nothing was written.` · `0 airports met the thresholds (--min-sources {a}, --min-routes {b}); nothing was written. The histogram above shows what lower thresholds would give.` |

| 16 | map footer, land.json fetch/parse failed (fix round 1, L4) | `AIRPORT DATA UNREADABLE - /static/land.json could not be read ({reason}). The map plots nothing.` (`{reason}` also `not a land file` for a JSON that is not one) |
| 17 | column, under `Show results`, while the map is shown and a run exists (fix round 1, I1; testid `search-ran`) | `The search has run: its results are under Show results.` |
| 18 | column subline below 900px, where the map is not displayed (fix round 1, I2) | `Type an airport or city.` |
| 19 | tool refusals (fix round 1, L1, L12) | `{path} could not be written ({ErrorClass}: {detail}). Nothing was written.` · `--min-sources {a} can never be met by the {n} source(s) asked; no airport could be a hub. No call was made and nothing was written.` · `NOT OVERWRITTEN: {path} holds a capture with {k} source(s) ok, this run has {n}. The existing file is kept and nothing was written. Pass --force to replace it with this run.` |
| 20 | tool console (fix round 1, L12, L13, L14) | `--min-sources not given: using {n}, the number of sources asked (the default 3 could never be met). Recorded in _meta.thresholds.` · `{seen} routes seen across {s} source(s); {b} route side(s) dropped for a code that is not three upper-case letters or digits; {o} row(s) that were not objects ignored; {u} region label(s) not in the known Seats.aero regions counted, not stored.` |

`{reason}` in #2 is the HTTP status or the JSON error class name - never response text.
Fix round 1 (L5): in #7, `{J}` counts every row that is not a plotted, engine-accepted
airport - `searchable: false` and rows that failed validation (bad code, routes, sources,
duplicate, not an object) alike - so that `K = J + M` always holds. Fix round 1 (L6): #3 is
shown while From does not hold a plotted airport (empty, partial or an unplotted code), #4
while From does and To does not, #5 when both do. Fix round 1 (L12): exit 5 also covers
"the run had gaps and the existing, more complete file was kept". Fix round 2 (N2): "more
complete" = the existing capture's `sources_ok` is larger than, or a strict superset of,
this run's, whatever this run's gaps; a gapless narrower run is NOT OVERWRITTEN with exit 1.
`{City}`, names and codes are data and go through `textContent` only.

### 4.10 Testids (all new; nothing renamed)

`map-pane`, `map-svg`, `map-land`, `map-hub-<IATA>`, `map-cluster-<IATA>`,
`map-cluster-list`, `map-pick-<IATA>`, `map-route`, `map-status`, `map-provenance`,
`map-zoom-in`, `map-zoom-out`, `map-reset`, `search-suggest-from`, `search-suggest-to`,
`suggest-<IATA>`, `search-pane-toggle`, `search-result`. Added to `TESTIDS` in
test_ui_static_rules.py.

## 5. Tech choices

| Choice | Why | Rejected |
|---|---|---|
| One committed `<path>` in a JSON file, generated by a committed script with a byte-equality test | no build step, reproducible, reviewable diff; JSON so the projection constants travel with the path | committing an `.svg` (would need `<img>`/inline; loses the constants); generating at server start (a build step in disguise); 110m (mockup used it; Tsuki said medium) |
| 4000×2080 integer relative coords | 230 KB vs 650 KB for 1000×520 one-decimal absolute; ±1px at 8× | absolute coords; Douglas-Peucker (changes the data; not needed at 230 KB) |
| viewBox pan/zoom, markers scaled by 1/z | one attribute per frame while panning; DOM rebuilt only on zoom, ≤ 400 nodes | CSS transforms on a `<g>` (blurs text, per-marker inverse scale still needed); canvas (no DOM, no testids, no focus) |
| Greedy pixel clustering, recomputed per zoom | O(n·k), no library, deterministic (sorted by routes) | quadtree/supercluster (dependency); precomputed clusters per zoom level (8 lists to keep consistent) |
| Hubs from `/partnerapi/routes` on the Mac | the only source of "what Seats.aero covers"; airportsdata has no hub signal | OpenFlights/ourairports "large_airport" (a different question; a dependency on a second dataset's judgement); a hand list (forbidden) |
| Thresholds, printed histogram | a rule the reader can check; the tool shows the alternatives before writing | top-N (arbitrary); ≥1 source (the long tail of one programme's outstations) |
| map.js as a second file with one global | keeps app.js reviewable; the CSP already allows `'self'` scripts | one 2,300-line app.js; ES modules (`type=module` changes execution timing under `defer` and the probes' script-include CSRF test assumptions) |
| Inputs stay inputs; picks write codes | the probes `fill()` them; the wording rules; zero risk to the 69-scenario parity | chips with × (mockup) |
| Great-circle polyline | 15 lines; a straight line on a cylindrical map lies about direction | quadratic Bézier (the mockup's picture, wrong for SFO-SYD); animation (excluded) |

## 6. Build steps

Do not run the full suite until step 11. `tests/test_ui_static_rules.py`,
`tests/test_ui_security.py` and the new test files are cheap and run after each step.

1. **Land path.** `tools/build_land_path.py` (from topo2svg.py: reads
   `docs/design/map-ref/land-50m.json`, writes `src/ui/static/land.json`, integer relative
   coords, `--check` mode that exits 1 when the committed file differs); run it; commit
   the output. `tests/test_map_assets.py::test_land_json_matches_its_generator` (build in
   memory, compare bytes) and `::test_land_json_is_under_400kb`.
   *Accept*: file ≤ 400 KB; both tests green; the `d` string starts with `M` and every
   ring closes with `Z`; `w/h/lat_min/lat_max` present.
2. **Static serving.** server.py `STATIC_FILES` → `(Path, ctype)`; add `/static/land.json`
   (static dir) and `/static/hubs.json` (`DATA_DIR / "hubs.json"`); `/static/map.js`.
   Commit the empty `data/hubs.json` (§4.2). test_ui_security: the three new paths get the
   same headers and the traversal list still 404s. *Accept*: `curl` of each new path
   returns 200 with the CSP/CORP headers; `/static/hubs.json` missing on disk → 404 JSON,
   never 500.
3. **Empty map that pans and zooms.** map.js skeleton: `POMap.load()` (fetches land +
   hubs, resolves `{land, hubs, meta, error}`), `mount()` builds the SVG (`map-svg`, land
   `path#map-land`, marker layer, route layer), controls, legend, status; §4.5 math; the
   NO AIRPORT DATA sentence. index.html script tag + `#search-pane`. app.js edits #1-#3
   (column + pane; no autofill yet). app.css: grid `360px minmax(0,1fr)` ≥ 900px (plus
   `440px` with `.has-drawer` ≥ 1180), `.strip.column`, `.map-pane` height `max(520px,
   calc(100vh - 160px))`, `@media (max-width: 899.98px) .map-pane { display: none }`.
   test_ui_static_rules updated (scripts list, scans over both files, new testids).
   *Accept*: at 1440 the Search tab shows the column and a dark-blue world with the
   mockup's crop; wheel/drag/buttons work, view clamps, reset restores; footer reads
   string #1; at 400 and 899 the map is absent, `bodySW <= width`, `search-from` /
   `search-to` / `search-run` work exactly as before; `test_ui_static_rules.py` green.
4. **Data.** `tools/filter_airportsdata.py` → `data/airportsdata_iata.csv`; test that it
   equals the filter output of the map-ref CSV. map.js: hub load validation (drop and
   count rows failing the code regex or with non-numeric lat/lon; `routes` must be an
   integer), projection of each plotted hub, provenance line #7, the "unreadable" state
   #2 on 404/JSON error. *Accept*: with a test fixture hubs.json of 12 hubs (synthetic,
   marked so in `_meta.synthetic: true`, kept under `tests/fixtures/ui/hubs_synthetic.json`
   and injected by the probe server, never committed to `data/`), 12 dots appear at the
   right places (SFO left coast, LHR over London within 6px of the projected point
   computed independently in the test).
5. **Markers, clusters, popover.** §4.6 complete. *Accept*: at z=1 two hubs 10px apart
   are one cluster with `2`; at z=8 they are two dots with labels; clicking a cluster
   opens the list with the title rule; Esc closes and focus returns; panning closes it.
6. **Picking + inputs.** app.js #4, `syncMap`, `setPicks` in map.js (picked form, halo,
   label). *Accept*: click SFO → `search-from` value `SFO`, status #4, focus on To; click
   LHR → `search-to` `LHR`, status #5; click JFK → To becomes `JFK`; type `SOF` into From
   → status #3/4 with #6 appended, no mark; delete From → mark cleared, next click fills
   From.
7. **Autofill.** app.js #5 + `POMap.suggest`. *Accept*: `san` → SFO, SJC, SAN, SAT, SJU
   order by the ranking rule (given those are hubs in the fixture); `sf` → SFO first;
   ArrowDown+Enter fills; Enter with no active row runs preflight (existing behaviour);
   Esc closes; with the empty hubs file nothing renders and Enter still runs preflight.
8. **Route line + preflight handoff.** `map-route` great-circle; `Show map`/`Show results`;
   `pane` transitions (#6). *Accept*: with both picks the dashed line joins the two dots
   and crosses the antimeridian correctly for SFO→SYD (two segments, no line across the
   map); `Run search` opens the SAME `search-confirm` dialog with the same text as
   before; after the run the results pane shows `search-state`/`search-table`, the drawer
   opens from a cell, `Show map` brings the map back with the picks still marked, `Show
   results` returns; the probes' `drive.js` search flow passes unchanged.
9. **Mac capture tool.** `src/map_tools.py` (§4.8) + `tests/test_map_tools.py` (§7).
   *Accept*: with a stubbed `get` returning 30 lists, the file is written with the
   invariants of §4.2; every refusal path exits 1 with the sentence in #15 and writes
   nothing (tmp dir asserted empty); one failing source → exit 5, listed in `_meta`;
   `mark-searchable` flips flags after the test edits a tmp copy of airports.csv.
10. **Docs.** README "Local UI" map paragraph + `src.map_tools` section; ui.md §4.5/§4.8
    S0; `tests/test_readme_local_ui_is_accurate.py` green. No `docs/plans/`, `vN`, `Step
    N` strings in anything under src/ui/static or in hubs.json.
11. **Verification.** Full suite and the same under `-O`; ui-probes suite (~11 min),
    compared by test id, 0 red; then the tester's round.

## 7. Testing strategy - what the tester attacks

- **Hostile strings in SVG/HTML.** A hubs fixture whose `name`/`city` are
  `<img src=x onerror=alert(1)>`, `</script><script>…`, `"'&`, RTL overrides, 10 KB
  names, `city: null`, `name: 42`; a `sources` entry of `"<b>x</b>"`. Assert: rendered as
  text (no element created; `document.querySelector("img")` is null in the map), the
  popover and autofill show the literal bytes, a 10 KB name is clipped by CSS not
  truncated by JS (the full name stays in `title`/`textContent`), and `iata: "<b>"`,
  `"SFOX"`, `"sfo"` are dropped and counted in #7's "not plotted".
- **Empty and broken data.** `hubs: []` → sentence #1, no dots, autofill inert, typing +
  Run search identical to 56742af's flow. 404 → #2 with `HTTP 404`; truncated JSON → #2 with
  `SyntaxError`; `hubs` not a list → #2; a hub with `lat: "37"` (string) → dropped and
  counted; `searchable: false` → not plotted, counted in J; `lat: null` → counted in M.
  Never a dot without a source: a hub with `sources: []` or `routes: 0` is dropped.
- **Clusters at every zoom.** For z in 1..8 (buttons, wheel, double-click): sum of
  cluster counts + singles == plotted count; no two markers within 18px; labels only
  at z ≥ 3; the popover title rule with a mixed-city cluster (EWR/JFK/LGA is one city
  in airportsdata? check - the fixture must include a cluster whose cities differ);
  popover clamped inside the pane at the four corners; pan closes it; focus returns.
- **State machine out of order.** Click To-first is impossible (From fills first) -
  assert that; pick, then type over the From input, then click; pick the same airport
  twice; pick, clear both, pick again; open a suggestion list, then click the map; Esc
  ordering (popover before suggestions before drawer); `Show map` while a run is in
  flight is not offered; picks survive `Show results` → `Show map`.
- **Availability never implied.** After a `no_awards` and an `api_error` run, `Show map`
  shows the same dots and line as before the run - nothing on the map changed, and
  string #5 is the only sentence about the line. Grayscale screenshot: picked dots
  (filled+halo), plain dots (hollow), clusters (count) remain distinguishable.
- **Parity and goldens.** The 69-scenario parity suite and 14 goldens untouched; `git
  diff 56742af -- src/ui/static/app.js | grep '^[-+].*"'` shows only the strings in §4.9,
  testids and class names; `search-confirm` text byte-identical for `SFO/MAD/2027-01-15`
  whether typed or clicked; `argv_display` identical.
- **CSP/static rules.** No inline script/style (the SVG is built by script; check the
  mockup's inline `style=` attributes were NOT copied); `img-src`/`connect-src` unchanged;
  the two JSON files carry CSP, CORP, nosniff, no-store; the traversal list still 404s;
  `/static/hubs.json` with a key string planted in `_meta` is refused by egress (500
  body, logged) - the tool's own check must have caught it first (test both layers).
  map.js: no `innerHTML`, no `x || 0`, no `setTimeout("…")`, no `javascript:`.
- **400px non-regression.** At 400 and 899: `.map-pane` not displayed, `document.body.
  scrollWidth <= width`, the strip wraps as before, drawer full-screen, the H/K/L probes
  green by test id. At 900 and 1179: two columns, no drawer column; at 1180+: drawer as
  a third column with results, never beside the map.
- **Performance.** With a 400-hub synthetic fixture: pan for 2 s at 60 Hz produces no
  layout thrash (PerformanceObserver long tasks = 0 over 50 ms); zoom in/out 20 times
  keeps DOM node count constant; `land.json` fetched once per page load.
- **The capture tool.** Stubbed `get`: declined; EOF; no key (`KeyResolutionError`
  sentence); HTTP 500 on one source (exit 5, file written, `_meta.sources_failed`); 429
  on source 3 (loop stops, 27 listed as not asked, exit 5); a string body; a dict with
  `hasMore: true` (`sources_incomplete`, its routes still counted, exit 5); every source
  failing (exit 1, nothing on disk); thresholds that yield 0 (exit 1, histogram printed);
  a route with `OriginAirport: "<b>"` (dropped, counted); the key echoed in a response
  body (refused, nothing written); `--sources bogus` (usage, exit 1); the output has no
  key; `mark-searchable` against a tmp airports.csv; the socket canary in every test.
- **Generators.** `build_land_path.py --check` exits 1 after a one-byte edit to
  land.json; `filter_airportsdata.py` idempotent; both tests run under `LC_ALL=C`.

## 8. Open risks

- The Routes endpoint's behaviour (pagination, budget accounting, response size - a
  source could return tens of thousands of routes) is unobserved. The tool promises "at
  most 28 calls" and keeps it by never following a cursor; if Seats.aero paginates at,
  say, 1,000 routes, the hub set will be biased toward whatever page one holds, and
  `_meta.sources_incomplete` is the only warning. The first real run decides.
- Thresholds 3/20 are unverified guesses; too tight loses real hubs (e.g. an airport
  only Aeroplan and United serve), too loose plots 800. The histogram exists for this.
- `data/airports.csv` has 141 airports; the plotted set is capped by it, and the map's
  footer will likely say "N not in data/airports.csv" for a large N on day one. Growing
  that file is a surcharge-region decision and is deliberately not in this round.
- Metro codes (NYC/LON/PAR/TYO) may appear in Seats.aero routes; they have no coordinates
  and are counted, not plotted. If they carry most of a city's routes, the city's real
  airports may fall under the thresholds.
- `getScreenCTM` with `preserveAspectRatio meet` and a letterboxed pane: the inverse
  transform must be taken from the SVG element, not the `<g>`; a wrong matrix shows up
  as clicks landing beside dots at high zoom - test at 8× in a 900px pane.
- The 60-unit initial `y` crop hides Svalbard; fine, but the clamp math must allow z=1
  with `y ∈ [0, H − H/z]` = `[0, 0]` - i.e. at z=1 the crop cannot be panned. Decide at
  step 3 whether z=1 shows the full height (letterbox) instead; the mockup crop wins
  unless it fights the pane aspect.
- Defer-script order: map.js must execute before app.js's boot promise resolves; it
  does under `defer` (document order), but if index.html is ever reordered the map
  silently never mounts. app.js reports it: if `window.POMap` is missing, the pane shows
  string #2 with `map.js not loaded`.

## 9. The 3 decisions most likely to be wrong

1. **Plotting only `searchable` hubs (D1).** Tsuki may want to see everything Seats.aero
   covers and have the tool grow `data/airports.csv` (region from Seats.aero's own
   `OriginRegion`, which `regions.SEATS_AERO_REGIONS` already maps). Reversal: drop the
   filter in map.js and add a `propose-airports` subcommand; the hub file already carries
   `regions` and `country` for it.
2. **A second script file (D7).** If the manager wants one static JS file, the map
   section moves into app.js under its own banner; the test edit is then unnecessary.
   ~1 hour of moving code, no design change.
3. **Results replace the map (D8).** Tsuki may have pictured results under or beside the
   map. Reversal: keep `#map-pane` shown at a reduced height above `#search-result` in
   result mode; the state machine and testids do not change, only CSS and the toggle.
