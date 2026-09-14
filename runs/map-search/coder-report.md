# Coder report: map-based search home page

Branch `feature/map-search` (forked from `feature/ui-restyle` at 56742af). Plan:
docs/plans/map-search.md. Every step below was committed as it was finished.

## Steps completed

| Step | What | Commit |
|---|---|---|
| 0 | Plan and the three approved mockups committed; `land-50m.json` (545 KB) committed because the generator reads it; the 3 MB airportsdata CSV gitignored (`.gitignore`), kept on disk as the source of record | 3ed2e85 |
| 1 | `tools/build_land_path.py` → `src/ui/static/land.json`: **212,308 bytes, 676 rings, 43,079 points**, Miller 4000×2080, lat [-60, 83], integer relative coords `M x,y l dx,dy … Z`; `--check` exits 1 on a one-byte edit. `tools/filter_airportsdata.py` → `data/airportsdata_iata.csv` (**7,884 rows, 487 KB**). `data/hubs.json` committed EMPTY with the note. `tests/test_map_assets.py` | e5d639a |
| 2 | `server.py` `STATIC_FILES` values are `(Path, ctype)`; `/static/map.js`, `/static/land.json`, `/static/hubs.json` (from `data/`); a missing hubs file is a 404 JSON, never a 500. `test_ui_security.py`: the two enumerations gain the three paths (5 lines). Egress tested against a planted key in hubs.json (500, logged) | 16f53b7 |
| 3-8 | `map.js` (576 lines, one global `window.POMap`), `app.js` (+242/-42 lines), `app.css` (+86), `index.html`; `test_ui_static_rules.py` (scripts list, scans over both files, 18 new testids); `tests/test_map_js.py` (node-backed: projection equality with the generator, validation counters, ranking); `tests/_map_fixture.py` + `tests/fixtures/ui/hubs_synthetic.json` (12 hubs; every coordinate/name/city from the CSV, marked `synthetic: true`, never in `data/`) | a3b6d86 |
| 9 | `src/map_tools.py` (`capture-hubs`, `mark-searchable`) + `tests/test_map_tools.py` (39 tests: happy path, every refusal with an empty output dir asserted, exit 5 gaps, 429 stop, cursor recorded not followed, key in a body / in a field, >400 warning, mark-searchable against a tmp airports.csv, the shape check) | c04ae21 |
| 10 | README "Local UI" **Map search** paragraph + `### map_tools` section; `docs/plans/ui.md` D19, the static contract line under §4.5, **S0 Map** in §4.8 | d5d8b61 |
| 11 | Full suite, ui-probes, ui-restyle-probes, smoke screenshots (below); the two fixes noted under Verification (digest pin; CSS-only list) | 6ceb891, abb2d91 |

### Verification

| Suite | Result |
|---|---|
| `python3 -O -m pytest -q -p no:cacheprovider` | **3808 passed, 13 skipped** (baseline 3692 / 13; +116 new) |
| the same without `-O` | **3808 passed, 13 skipped** |
| ui-probes (`-O`, `-p no:randomly`) | **662 passed, 1 failed** — red: `test_P22_use_utf8_output_is_called_at_process_entry_points_only` (see Known gaps: the probe hard-codes the set of entry points as three files; `src/map_tools.py` is a fourth, guarded exactly like `trips_tools`). `M13` went red on the first run because `test_map_assets` skipped in an export (the 3 MB CSV is not committed); fixed by pinning the filtered CSV to a sha256 digest instead of skipping — M12/M13/M14 green after that. |
| ui-restyle-probes (`-O`, `-p no:randomly`) | **279 passed, 7 failed, 10 skipped** (baseline 284 / 2 / 10) — baseline red E3 and C5[search_ok] unchanged; **5 new reds, all pins of the restyle round that this plan's own changes invalidate**: `E5` and `H1` (server.py byte-identical to 386b2fc — the plan changes `STATIC_FILES`), `H2` (only `app.js` may be a `<script>` — the plan adds `map.js`), `J6` (app.js diff must be exactly the restyle's 6 hunks), and `C2` (`--accent2` used only by `.btn-primary` — the route line uses it per D12; one-token reversal, see Deviations). `H4` was red for one commit (an inline `style.top` in app.js, since removed; green now). |

Smoke screenshots at 1440×900 (and 400×800), under `docs/design/map-ref/shots/`, each ≤ 204 KB, looked at:
`a-empty-hub-file.png` (the shipped state: land, controls, the NO AIRPORT DATA sentence),
`b-synthetic-world-clusters.png` (clusters 3/3/2 at world zoom), `c-zoomed-cluster-list.png`
(London list open at ~3×), `d-route-picked.png` (SFO→LHR dashed great circle, filled dots
with labels), `e-400px-form.png` (map absent, wrapping strip, `bodySW == 400`).

## Deviations (each is the reading that changed the least; reversible)

1. **z = 1 shows the full height, letterboxed** (plan §8 risk, "decide at step 3"). The
   viewBox is always `x y W/z H/z` so the clamp `y ∈ [0, H − H/z]` is consistent; the
   mockup's 60-unit crop would have been un-pannable at z = 1. Reset is `{0, 0, 1}`.
2. **`SEATS_AERO_SOURCES` has 28 codes, not 30.** The tool reads the dict; the promise is "at
   most 28 calls" and the sentence prints 28.
3. **Marker scale and clustering use the SVG's `getScreenCTM()`** (`k = 1/ctm.a`) rather than
   `(W/z)/paneW`: identical when the pane is width-limited, and still right when a short,
   wide pane is height-limited.
4. **String #6 is appended only for a code-shaped value** (`/^[A-Za-z0-9]{3}$/`): "SF"
   mid-typing and "San Fran" would otherwise be told they "can still be searched".
5. **Autofill ranking follows the stated rule, not the example order.** `san` gives
   `SAN, SFO, SJC` (exact code first), where the plan's example lists SFO first; the rule
   (exact code, code prefix, city prefix, substring) is what is implemented and tested.
6. **A picked hub is never clustered**: it always draws as its own marker so the line joins
   its exact point (mockup RoutePicked).
7. **Provenance counting**: `K` (not plotted) = not-in-airports.csv + without-coordinates +
   rows that failed validation (bad code, routes, sources, duplicate). The parenthesis lists
   only J and M as the plan pins; a string `lat` is counted as "without coordinates".
8. **The 3 MB airportsdata CSV is not committed** (per the brief). The equality test runs
   where it exists and otherwise pins the committed filtered CSV to a sha256 — no skip, so
   the export probe M13 stays green.
9. **Route line colour is `--accent2` (D12, the approved mockup)**, which makes the restyle
   probe C2 red. If the manager prefers §4.7's rule, change `.route { stroke: var(--accent2) }`
   to `var(--accent)` in app.css — one token.
10. **`tests/test_map_js.py` needs `node`** (skips without it; the ui-probes need node anyway).
11. **`src/map_tools.py` calls `config.use_utf8_output()` in its `__main__` guard**, exactly
    as `trips_tools` does and for the same reason (MAC-A: the `wrote …` line is copied out of
    stdout). The ui-probe P22 pins the entry-point set to three files and is red for that.
12. The "unreadable" reason for a broken hubs.json that is valid JSON but has no `hubs` list
    is the phrase `hubs is not a list` (the plan names only an HTTP status or an error
    class; there is neither in that case).

## How to run it

Sandbox / any clone:

```bash
python3 -O -m pytest -q -p no:cacheprovider                     # 3808 passed, 13 skipped
python3 -m pytest -q -p no:cacheprovider tests/test_map_assets.py tests/test_map_tools.py tests/test_map_js.py
python3 tools/build_land_path.py --check                          # land.json matches the generator
python3 -m src.ui --no-open                                       # Search tab: column + map
```

On the Mac (the only place the capture can run - it needs the key):

```bash
cd ~/Downloads/points-optimizer-git
.venv/bin/python -m src.map_tools capture-hubs
```

Expect, in order: the key banner (`Seats.aero key: pro_…xxx   (source: …)`), then

```
This will make at most 28 Seats.aero API call(s): one GET /partnerapi/routes per source (28 sources). This process has spent 0 of 1,000; Seats.aero also counts your other runs today, which this tool cannot see.
Continue? [y/N]
```

then one line per source (`aeroplan: ok (N routes)` / `smiles: failed(HTTP 500)` /
`azul: unreadable(...)` / `united: incomplete(...)`), `This process has spent 28 of 1,000
today.`, the routes-seen line, the histogram (`hubs at each threshold pair`, min-sources
1-5 × min-routes 5/10/20/50), `N hubs (K searchable, M without coordinates, J not in
data/airports.csv)`, the `iata,name,country` lines for the J unsearchable airports, and
`wrote data/hubs.json`. Exit 0 if every source answered, 5 with gaps (listed in `_meta`),
1 with nothing written (the sentence says why). Then commit `data/hubs.json` and reload
the page: the map plots the searchable hubs and the footer reads
`N airports Seats.aero tracked on YYYY-MM-DD · K not plotted (…)`.

Run `.venv/bin/python -m src.map_tools mark-searchable` (0 calls) after any change to
`data/airports.csv`; `tests/test_map_assets.py::test_searchable_cannot_drift_from_airports_csv`
goes red until it is run.

## Known gaps

- **The Routes endpoint is unobserved** (plan §8). Pagination, budget accounting and
  response size are guesses; the tool never follows a cursor and records the source as
  incomplete. The first real run on the Mac decides; if every source comes back
  `incomplete`, the hub set is page-one-biased and the histogram is the only diagnostic.
- **Day one will say "K not plotted"** for a large K: only the 141 codes in
  `data/airports.csv` are plotted. The tool prints the candidates; growing that file is a
  surcharge-region decision this round does not make.
- **P22 (ui-probes)** and **C2, E5, H1, H2, J6 (ui-restyle-probes)** are red as pins of an
  earlier round; each is explained above and none is a defect in the product as planned.
  They are the tester's to refresh or the manager's to decide (C2).
- Below 900px the column subline still says "or pick it on the map" with no map on
  screen; the string is pinned by the plan and left as is.
- The map's DOM tests that need layout (clustering pixel rule, popover clamping at the
  four corners, focus return, the 400-hub performance run) were verified by hand in
  headless Chromium (scratch scripts, not committed) and are the tester's probes to write;
  `tests/test_map_js.py` covers the data logic and `docs/design/map-ref/shots/` the look.
- `POMap.suggest` matches `city`/`name` by substring, so `sf` also offers SYD
  ("King**sf**ord Smith"). The rule is the plan's; the ordering puts the code match first.

## Out-of-scope observations

- `SEATS_AERO_SOURCES` documents 28 sources; the plan's "30" and README's earlier prose
  should agree with the code (the README section I added says 28).
- The results pane is narrower than before at 1440px with the drawer open (568px, the
  table scrolls inside its frame) - a consequence of D8 (results replace the map in the
  right pane). D8's reversal in the plan (map above results) would widen it again.
- The restyle probes pin `src/ui/server.py`, the `<script>` list and the exact app.js diff
  to the restyle round; any later UI round will trip them the same way. A tester round
  that re-bases those pins on this commit would stop that.
