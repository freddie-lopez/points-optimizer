# Tester report: map-based search home page

Branch `feature/map-search` at **5f42f1d** (9 commits over feature/ui-restyle at 56742af).
Plan: docs/plans/map-search.md. Coder report: runs/map-search/coder-report.md.
Probes: `docs/test-reports/map-search-probes/` (run with
`python3 -O -m pytest -q -p no:cacheprovider -p no:randomly docs/test-reports/map-search-probes`;
screenshots go to the gitignored `shots-out/`). RED = a defect present, GREEN = the attack
held up. No byte left this machine: the probe server refuses every `connect()`, Chromium
resolves nothing but 127.0.0.1, the round-1 socket canary is armed in every test, and
`seats.aero` was never called. Nothing was committed; nothing in src/, tools/ or data/ was
touched; no existing probe was edited (one line was appended to `.gitignore` for the new
`shots-out/`).

## Verdict in one paragraph

The core holds: the empty shipped state says its sentence, every broken hub file is
named rather than drawn, hostile names land as text everywhere (SVG, popover, autofill,
aria), the two-click state machine and the preflight → confirm → run flow produce a
byte-identical confirm dialog and transcript whether typed or clicked, clustering keeps
its count and 18px rule at every zoom, the CSP and static rules are unchanged, the
engine/CLI/goldens are untouched, and the generators are deterministic. What breaks is
around the edges: **the drawer sits beside the map** (plan §7 says never), **the results
pane is 308px wide at 1180px** with a drawer open (a layout regression the plan's grid
causes), **the capture tool crashes with a traceback and leaves a half-written
`hubs.json.tmp`** on a disk error, a picked marker is drawn on top of a cluster, and a
corrupt-but-parseable land.json leaves a blank pane. Nothing here is a security or
money defect. Two Mediums, the rest Low.

## Findings

### M1 · Medium · With a drawer open, `Show map` puts the map beside the drawer

- **Repro**: 1440px, run a search (typed or clicked), click an award cell (drawer opens
  as the third column), click `Show map`. Probe **E7**; shot `E7-map-beside-drawer.png`.
- **Expected**: plan §7 "at 1180+: drawer as a third column with results, never beside
  the map"; §2.8 "the right pane is the map OR the results". The drawer is a detail of a
  cell in a table that is now hidden.
- **Actual**: grid `360px 568px 440px`, the map in 568px, the drawer still showing the
  hidden table's cell. At 1180 the map gets 308px.
- **Location**: `src/ui/static/app.js` `renderSearchDrawer()` renders whenever `q.sel`
  is set regardless of `q.pane`; the `Show map` handler does not clear `q.sel`.
  Fix is one line either way (close the drawer on `Show map`, or hide it while
  `pane === "map"`).

### M2 · Medium · The results pane beside a docked drawer is 308px at 1180px (428px at 1300)

- **Repro**: 1180px, run a search, open a cell. Probe **H3**; shot
  `H3-1180-results-drawer.png`: the table shows DATE, PROGRAM and half of Y; the
  `Equivalent command` row is clipped.
- **Expected**: at 56742af the results had `1180 − 32 − 20 − 440 = 688px` beside the
  drawer; the plan's own §8/§9 treat the drawer as "a third column with results".
- **Actual**: `.has-drawer .search-grid { 360px minmax(0,1fr) 440px }` at ≥1180 leaves
  `w − 32 − 40 − 800` for the results: 308px at 1180, 428px at 1300, 568px at 1440 (the
  coder's own out-of-scope note). The table scrolls inside its frame, so nothing is
  lost, but a 1180–1300 laptop sees a results column narrower than the drawer.
- **Location**: `src/ui/static/app.css` the two `.search-grid` media rules. Options the
  plan already names: collapse the 360px column when the drawer is open, or the D8
  reversal (map above results). Manager's call.

### L1 · Low · A disk error in the capture tool is a Python traceback and leaves a partial `hubs.json.tmp`

- **Repro**: probes **B1** (ENOSPC half-way through the tmp write, simulated) and **B2**
  (`--out` names a directory). Also B3: a transport exception that is not a
  `requests.RequestException` (e.g. `RuntimeError`) propagates.
- **Expected**: module docstring and plan §4.8: "refuses, with a named reason and
  nothing on disk, on every failure"; exit 1 with a sentence.
- **Actual**: `OSError` / `IsADirectoryError` / `RuntimeError` are uncaught; Python
  prints a traceback (exit 1 by accident, no "nothing was written" sentence); B1 leaves a
  2,893-byte `hubs.json.tmp` (half the document) next to the target; B2 leaves the
  `.tmp` too. The target file itself is never partial (rename is atomic) - that part
  holds.
- **Location**: `src/map_tools.py` `_write_atomically` (no try/except, no tmp cleanup),
  `run_capture` loop (`_ask_source` catches only `requests` errors).

### L2 · Low · A picked hub is drawn on top of the cluster that forms at the same pixel

- **Repro**: world zoom, click the SFO cluster, pick SFO. Probe **F4**; shot
  `F4-picked-over-cluster.png`: the filled SFO dot + label sits 16.4px from the new
  SAN/SJC "2" cluster, halo over the count.
- **Expected**: plan §4.6 "no marker within 18px of a cluster centre"; the coder's
  deviation 6 (a picked hub is never clustered) is fine for the line's endpoint but
  the cluster it leaves behind must move or hide.
- **Actual**: the remaining members re-cluster at their own lead's position, which is
  within 18px of the picked hub; the picked `g` is appended last so the cluster's count
  is partly covered (still clickable at its edge).
- **Location**: `src/ui/static/map.js` `recluster()` - picked hubs are skipped before
  clustering instead of seeding a cluster whose other members are then hidden/offset.

### L3 · Low · A land.json that parses but is not a land file leaves a blank pane and an unhandled rejection

- **Repro**: probe **C5** (`{"w": 1}` served as land.json). `pageerror`: `land.json: not
  a land file`; `#map-pane` innerText is empty; no status sentence.
- **Expected**: map.js's own rule ("no data is a state with words"); plan §8 pins the
  `map.js not loaded` case but not this one.
- **Actual**: `load()` throws inside the land `.then` success handler; the sibling
  rejection handler does not catch it; `ensureMap` never resolves and `S.map.loading`
  stays true forever. The committed land.json is pinned to its generator by a test, so
  this needs a corrupt deploy - Low.
- **Location**: `src/ui/static/map.js` `load()` (`throw new Error("land.json: not a land
  file")` on line 127 is outside any `catch`).

### L4 · Low · A land.json failure is reported as "/static/hubs.json could not be read (land.json: HTTP 404)"

- **Repro**: probe **C6** (land.json 404 or malformed).
- **Expected**: the sentence names the file that failed.
- **Actual**: string #2 is hard-wired to `/static/hubs.json`, the reason carries the
  `land.json:` prefix - a contradictory sentence.
- **Location**: `src/ui/static/map.js` `S_UNREADABLE_A` used for both files.

### L5 · Low · The provenance line's arithmetic does not add up when rows were dropped

- **Repro**: probe **C4** (a file with 9 rows dropped for bad code/routes/sources/
  duplicate, 1 not searchable, 3 without coordinates): `16 airports … · 14 not plotted
  (1 not in data/airports.csv, 3 without coordinates)`.
- **Expected**: a reader can check the sentence: 14 = 1 + 3 + (something named).
- **Actual**: the coder's deviation 7 folds dropped rows into K and names them nowhere.
  With the real tool's output this cannot happen (the writer validates), so it needs a
  hand-edited file - Low, but the sentence is the plan's "a rule the reader can check".
- **Location**: `map.js` `renderProvenance()`; `readHubs()` counts `dropped` but never
  prints it.

### L6 · Low · The footer says "Click an airport to set To." when To was just set and From holds a non-code

- **Repro**: type `s` in From, click SYD on the map. Probe **C8** (also G4).
  Inputs: `From = "s"`, `To = "SYD"`; footer: `Click an airport to set To.`; the next
  click replaces To again (both fields non-empty).
- **Expected**: the sentence describes the state (§4.4: "recomputed from the two input
  values"); here To is set and From is the field that needs attention.
- **Location**: `map.js` `setPicks()` chooses #4 from `fromTyped` only; `app.js`
  `onMapPick` fills "the first empty" by text, not by pick.

### L7 · Low · IATA labels at zoom ≥ 3 are drawn over neighbouring dots

- **Repro**: 137-hub fixture (every engine airport with coordinates), z = 3.4: probe
  **F5** finds 6 label-over-dot intersections (AMS→DUS cluster, BRU→DUS, DOH→AUH,
  GVA→LIN, VIE→BUD, YOW→YUL); 2 remain at 8× (GLA→EDI, TLV→AMM). Shot `F5-labels-z3.png`.
- **Expected**: §4.6's 18px rule keeps dots apart; labels are 7–28px to the right and
  have no rule. Visual only (`pointer-events: none` keeps the dot beneath clickable).
- **Location**: `map.js` `singleMarker()` label placement.

### L8 · Low · Metro pairs never separate: at 8× in a 1,028px pane 18px ≈ 85 km

- **Repro**: probe **F6**: at z = 8 nine clusters remain (LCY "6" = every London
  airport, EWR "3", OAK "3", CDG, DCA, HND, IST, LIN, PEK). Plan §6 step 5's acceptance
  ("at z=8 they are two dots with labels") is stated for hubs 10px apart at z=1; real
  metro pairs are 1–3px apart at z=1.
- **Expected/actual**: a design limit, not a bug in the code - the list popover is the
  only way to LGW or LGA. Recorded so the ZMAX=8 / 18px pair is a known decision.

### L9 · Low · `role="img"` on the SVG hides the marker buttons from assistive tech

- **Repro**: probe **H9**: Chromium's accessibility snapshot of `map-svg` is `{}` - no
  `SYD` button, no cluster button.
- **Expected**: plan §2.18 asks for both `role="img"` and real buttons inside; an `img`
  role makes its subtree presentational, so the two requirements contradict. Keyboard
  users still reach the markers by Tab (verified: Enter/Space pick, F9), a screen reader
  does not hear them. Typing remains the accessible path, as the plan says.
- **Location**: `map.js` `mount()`, the `role: "img"` attribute (a `group` with the
  same aria-label keeps the buttons exposed).

### L10 · Low · A marker's label is not clickable

- **Repro**: zoom ≥ 3, click the `SYD` text beside the dot. Probe **F11**: nothing
  picked. The text is the largest part of the marker and has `pointer-events: none`
  (needed so it does not block the dot beneath a neighbour, see L7).
- **Location**: `app.css` `.mk .lbl`.

### L11 · Low · A plain click on the ocean does not close the popover

- **Repro**: open a cluster list, click empty ocean. Probe **E9**: still open. Esc, ×,
  any pan and any zoom close it (F9 green). Plan §4.6 names only pan/zoom, so this is a
  gap in the plan as much as the code.

### L12 · Low · The capture tool spends calls it can know are wasted, and asks the same source twice

- **B4**: `--sources aeroplan,united --min-sources 3` spends 2 calls, then refuses with
  "0 airports met the thresholds". The refusal is decidable before the prompt.
- **B5**: `--sources aeroplan,aeroplan,united,united,virginatlantic` announces and
  spends 5 calls for 3 sources (the announced number is honest, the calls are not
  needed; `sources_asked` lists duplicates).
- **B6**: an existing, complete `hubs.json` is replaced without a word by a re-run in
  which 27 of 28 sources fail (exit 5). The plan does not ask for a question here; the
  README says `_meta` records the gaps. Recorded because the committed file is the
  one thing this tool produces and a bad afternoon at Seats.aero can silently shrink it.
- **Location**: `src/map_tools.py` `run_capture`, `_parse_sources`.

### L13 · Low · `OriginRegion`/`DestinationRegion` are stored verbatim, unbounded

- **Repro**: probe **B13**: a 10 kB `<img onerror>` + U+202E region string lands in
  `hubs.json` `regions` as is. `hubs_file_problems` checks only "is a list". map.js
  never renders `regions`, so no browser impact; the file is committed and reviewed by
  eye.
- **Expected**: the plan maps regions through `regions.SEATS_AERO_REGIONS` (§9.1); a
  string not in that map, or longer than a name, should be counted, not stored.

### L14 · Low · Docs: two flags the README never mentions; one console line mislabels non-object rows

- **B19**: `--api-key` (capture-hubs) and `--file` (mark-searchable) are in the parser
  and not in the README's map_tools section (`--sources`, `--out`, `--raw-dir`,
  `--min-*`, `--yes` are).
- **B14**: a route that is not an object (a string, a number, null) is reported inside
  "N route side(s) dropped for a code that is not three upper-case letters or digits" -
  it had no code.
- ui.md never names `mark-searchable` (the README does); string #7's parenthesis and
  the histogram grid match the code.

### I1 · Info · "Award space appears only after the search runs." stays after the search ran

After a run, `Show map` shows string #5 unchanged (probe C9 records it). The plan pins
the string; a reader who just ran the search reads a sentence in the wrong tense.

### I2 · Info · Below 900px the column says "or pick it on the map" with no map

Coder's known gap; pinned string; recorded.

## What I could not break

- **Empty shipped state** (C1): `data/hubs.json` is byte-identical to
  `map_tools.empty_document()`; the pane shows the plan's #1 sentence, visibly, at
  1440; no dots; autofill inert; a typed search opens the same confirm dialog with
  `--origin SFO --destination MAD`.
- **404 / malformed / wrong types / NaN / key-planted** (C2, C3 ×7, C4, H5): every case is
  string #2 with `HTTP 404`, `SyntaxError`, `hubs is not a list` or `HTTP 500`, zero
  markers, zero page errors, and the typed form still searches. The egress filter
  refuses a planted key with a 500 and logs it; the key never reaches the DOM.
- **Hostile names** (D1–D3): `<img onerror>`, `</svg><script>`, U+202E, 5,000-char
  city, emoji, `city: null`, `name: 42`, `"'&`, `sources: ["<b>"]`, a `<script>`
  captured_at - all textContent; `window.__pwned` never set; no `img`/`script`/`b`
  element anywhere in the view; the popover is 340×722 inside the pane and scrolls;
  the 5,000-char strings are kept in full (title/textContent) and clipped by CSS; the
  autofill list stays inside the 360px column; `bodySW == 1440`.
- **State machine** (E1–E4, E6, E8, E10, G4): A/A/B/C, From's airport with To filled
  (replaces To, no A→A line), clear To / clear From / lower-case / whitespace / unknown
  code / 2-char partial, Trips-and-back, the pane round trip - inputs, marks, route and
  status stay consistent; the confirm dialog text is byte-identical typed vs clicked;
  the transcript is identical modulo the server's own call counter and duration;
  `Show map` is not offered in flight (E5); after `api_error` and `no_awards` runs the
  markers, line and sentence are unchanged and "availability" appears once (E6); Esc
  closes popover → suggestions → drawer in that order (E8); a hub flagged searchable
  that airports.csv does not know is refused by preflight in the builder's words, in
  the column (E10); the engine binds `confirm_id` to a digest of the body, so a pick
  during the preflight round-trip cannot smuggle a different route under an old
  confirmation.
- **Clusters and zoom** (F1–F3, F8–F10): with 137 hubs at every button step 1 → 8 and by
  wheel to 8, cluster counts + singles = 137, no two marker centres within 18px, labels
  exactly when z ≥ 3, every dot 6.4px at every zoom (1/z rescale), zoom clamps at 1 and
  8, pan clamps to `[0, W−W/z] × [0, H−H/z]`, double-click = 2×, reset = `0 0 1`; a click
  at 8× in a 900px pane lands on the dot (CTM from the SVG); the popover is clamped ≥ 8px
  inside the pane at all four corners (zoom controls excluded); Esc/× return focus to
  the cluster, ArrowDown moves inside the list, Enter/Space on a focused marker act,
  pan and wheel close the list; with 400 hubs a 2 s drag produces no long task > 50 ms,
  20 zoom cycles keep the DOM node count constant, keystrokes that never make a pick
  cause zero marker-layer mutations, land.json and hubs.json are fetched once per page.
- **Route line** (F7): SFO→SYD is two segments, no edge spans the map, dashed,
  `--accent2`, 1.5px non-scaling at every zoom.
- **Autofill** (G1–G3, G5): the ranking equals an independent implementation of the
  plan's rule for 15 queries over 137 hubs (exact code, code prefix, city prefix,
  substring, ties by routes then code, max 8; `sf` → SFO, ATL (Hart**sf**ield), SYD;
  `lon` includes BCN (Barce**lon**a) - the rule's, as the coder says); `ü`, emoji, 500
  chars, blank → no list and `aria-expanded=false`; ArrowDown/Up/Enter/Esc; Enter with
  no active row runs preflight with the typed value; a pick writes exactly the code;
  mouse pick; one list at a time; blur closes; no list widens the page at 400/899/900/
  1440 (absolute overlay under the strip below 900).
- **Layout** (H1, H2): at 400/720/899 `.map-pane` is `display:none`, one grid column,
  `bodySW == width`, the old idle `search-state` note, run/results/drawer (fixed,
  ≤ width) as before; 900/1179 two columns with a fixed drawer.
- **Security/static** (H4, H6, H7): CSP byte-identical to 56742af; two `<script>`s
  (`map.js` then `app.js`), no `style=`; `/static/hubs.json`, `/static/land.json`,
  `/static/map.js` carry CSP/CORP/nosniff/no-store and the right content type; ten
  traversal/case/query variants 404 (the CSV is not served); map.js has no
  innerHTML/eval/`javascript:`/`||0`, writes only `style.left/top` (numbers + px), sets
  one global; app.css's diff removes only the old `.search-grid` rule and adds no token.
- **Data** (A1–A9): land.json == generator output, twice, under `LC_ALL=C`; `--check`
  exits 1 on a one-byte edit; the filtered CSV == the filter's output and the filter
  is idempotent; the digest pinned in `test_map_assets.py` is the committed file's;
  7,884 rows, every lat/lon numeric and in range; no new 3-letter literals in app.js
  beyond the placeholders 56742af already had, none in map.js/map_tools/server/tools;
  the synthetic fixture's `searchable` equals the airports.csv membership and never
  reaches `data/`; `git diff 56742af..HEAD` is empty for main/formatter/seats_client/
  optimizer/live_trip/trip_builder/regions/api/engine/serialize/airports.csv/goldens;
  the 14 goldens and both older probe suites are untouched.
- **Capture tool** (B7–B12, B15–B18): announced count == calls made (28, or 2 with
  `--sources`); a 429 on source 3 stops at 3 calls and lists 25 as "not asked: rate
  limited"; an exhausted budget makes 0 calls and writes nothing; the key is masked
  (`flag…789`) and never printed; a key in a body is refused before `--raw-dir` gets
  it; hostile codes (RTL, 10 kB, lower-case, `<b>`, ints, lists, dicts, null rows) never
  become hubs; only `y`/`yes` (any case, trimmed) is a yes, EOF and Ctrl-C give the
  plan's exact sentence; `mark-searchable` on the committed empty file is a byte no-op;
  histogram grid 1–5 × 5/10/20/50, defaults 3/20.

## Suite counts (this machine, 5f42f1d)

| Suite | Result | Coder claimed |
|---|---|---|
| `python3 -O -m pytest -q -p no:cacheprovider` | **3808 passed, 13 skipped** (185 s) | 3808 / 13 |
| same without `-O` | **3808 passed, 13 skipped** (210 s) | 3808 / 13 |
| ui-probes (`-O -p no:randomly`) | **662 passed, 1 failed** (667 s): P22 | 662 / 1 |
| ui-restyle-probes (`-O -p no:randomly`) | **279 passed, 7 failed, 10 skipped** (263 s): C2, C5[search_ok], E3, E5, H1, H2, J6 | 279 / 7 / 10 |
| map-search-probes (this round) | **71 passed, 23 failed** (180 s) | - |
| `tools/build_land_path.py --check` | matches (212,308 bytes) | - |

## Verdict on the 8 pre-existing probe reds

| Probe | Pins | Verdict |
|---|---|---|
| ui-probes **P22** | `use_utf8_output()` called in exactly three entry points | **Plan-invalidated pin.** `src/map_tools.py` is a fourth, guarded in `__main__` exactly as `trips_tools` (line 640); plan §2 "same shape as `src.trips_tools`". Refresh the set to four. |
| restyle **E5** | `git diff 386b2fc -- engine/api/serialize/server.py` empty | **Plan-invalidated pin.** Only `server.py` differs (my A6: engine/api/serialize empty since 56742af); the diff is the `STATIC_FILES` `(Path, ctype)` map and the 404-on-OSError branch the plan names (§2.16). |
| restyle **H1** | `server.py`/CSP byte-identical to 386b2fc | **Plan-invalidated pin.** Same diff; the CSP string itself is byte-identical (my H4 pins it). |
| restyle **H2** | only `app.js` may be a `<script>` | **Plan-invalidated pin.** `map.js` is the plan's D7. The other three assertions in H2 (no inline script body, no `style=`, no `on*=`) still hold (my H4). |
| restyle **J6** | app.js diff from 386b2fc is exactly 6 hunks | **Plan-invalidated pin.** §4.7 enumerates the new edits; the diff adds no wording, no testid rename, no banned API (my A5/H6 and the coder's static-rules test). |
| restyle **C2** | `--accent2` only in `.btn-primary` | **Plan-invalidated pin, but a live design question.** D12 chooses the mockup's coral for the route line and offers the one-token reversal; UI-BRIEF §4.7 says coral is "the ONE primary action". The line is dashed, 1.5px and drawn where no button is - I do not read it as a button. Manager decides; the pin should then be refreshed either way. |
| restyle **E3** | key chip follows the key mid-session | **Baseline red** (red at 56742af, R-round decision). Unrelated to this branch. |
| restyle **C5[search_ok]** | ≤ 1 primary button on screen | **Baseline red** (Run search + "Score against a fare →" in the drawer, both at 56742af). Unrelated. |

No regression hides behind any of the eight: each red's assertion is the pinned
byte/count from the earlier round, and the behaviour behind each pin (CSP, no inline
script, no wording change, entry-point guard) is re-checked green in this round's probes.

## This round's probes by id

GREEN: A1–A9 · B7 (×2), B8–B12, B15 (×7), B16–B18 · C1, C2, C3 (×7), C7, C9 · D1–D3 ·
E1–E6, E8, E10 · F1–F3, F7–F10 · G1–G5 · H1 (×3), H2 (×2), H3[1440], H4–H8.

RED (each a finding above): B1, B2, B3 → L1 · B4, B5, B6 → L12 · B13 → L13 · B14, B19 →
L14 · C4 → L5 · C5 → L3 · C6 (×2) → L4 · C8 → L6 · E7 → M1 · E9 → L11 · F4 → L2 · F5 → L7 ·
F6 → L8 · F11 → L10 · H3[1180], H3[1300] → M2 · H9 → L9.

## Coder's admitted gaps, checked

- "The Routes endpoint is unobserved" - true and unavoidable here; the tool's promise
  (≤ 28 calls, cursor recorded not followed) is kept in every stubbed path I ran.
- "P22 and C2/E5/H1/H2/J6 are pins" - agreed (table above).
- "Below 900 the subline says 'or pick it on the map'" - as described (I2).
- "The layout-dependent DOM tests were verified by hand" - now written (F-series); they
  found L2 and L7, which a hand check at 12 hubs would not show.
- "`sf` also offers SYD" - as described and per the rule (G1).
- Not admitted: M1, M2, L1, L3–L6, L9–L14.

---

# Re-test: f1fe286

Fix commits 32b503a (tool), 4dacd19 (app.js/app.css), 33dd029 (map.js); coder report
"Fix round 1". Each fix was attacked on its own terms, not only re-run: new probe file
`test_ms_i_retest.py` (I1–I16), plus two of my own probes corrected (below). Suites
re-run in full. Nothing committed; nothing outside `docs/test-reports/map-search-probes/`
and this file touched.

## Per-finding status

| # | Status | What I did to it |
|---|---|---|
| M1 | **Fixed** | `Show map` closes the drawer, clears `has-drawer`, the grid goes back to `360px …`; `Show results` does not resurrect the drawer (`q.sel` cleared); Esc order with results shown = suggestions → drawer, with the map shown = popover → suggestions (E7, E8 rewritten, I7 ×3). |
| M2 | **Fixed** | Column 220px with a docked drawer: results 448/568/708px at 1180/1300/1440 (grid pinned byte-exact); every input, the cabin select and Run are 186px and inside the column; nothing clips (`scrollWidth ≤ clientWidth` on every column element); the autofill list stays inside 219px, codes readable (the airport name is ellipsised after ~10 characters at 220 - acceptable, the code is what the pick writes); a typed search from the narrow column opens the same confirm dialog; `bodySW == width` (I7 ×3, H3 ×3 green). Shot `rt-1180-drawer.png`. |
| L1 | **Fixed** | ENOSPC half-way, `--out` a directory, `--out` under a file: each is exit 1 with `{path} could not be written ({Class}: …). Nothing was written.`, no `.tmp` left, target untouched; `mark-searchable` on ENOSPC the same; a `RuntimeError` from the transport is `failed(RuntimeError)` in `_meta` (B1–B3, I10 green). Ctrl-C mid-loop is still a traceback (a `BaseException`, nothing written) - fine. |
| L2 | **Partly fixed → see N1** | The pushed cluster no longer overlaps the picked dot (F4 green) but the push looks only at the picked hub: with 137 hubs, picking SFO pushes the LAS cluster to 13.3px from the MRY cluster (18px rule broken between two clusters, I3 red), and under SFO's always-shown label (I2, I4 red). |
| L3 | **Fixed** | `{"w": 1}` as land.json → string #16 with `not a land file`, no page error, no stuck loading; the typed form still searches (C5, I9). |
| L4 | **Fixed** | `/static/land.json could not be read (SyntaxError)` / `(HTTP 404)` / `(not a land file)` byte-exact (#16); hubs failures keep #2 (C6 ×2, I9 ×3). |
| L5 | **Fixed** | `8 airports … · 6 not plotted (5 not in data/airports.csv, 1 without coordinates)`: K = J + M with dropped rows inside J, as the plan now says (C4). |
| L6 | **Fixed** | `s`/SYD → #3; `SOF`/SYD → #3 + `SOF is not on this map…`; `""`/SYD → #3; `LHR`/SYD → #5; `LHR`/`xx` → #4 (C8, I-series). |
| L7 | **Partly fixed → see N1** | Without a pick: no label over any dot and no label over any label at all 8 button steps and 12 wheel steps (I1 green); a hidden-label hub's dot is still clickable (I4 first half). With a pick: the picked label is exempt from the check and sits on the neighbouring cluster at every zoom (I2 red: LHR's label over the AMS/LCY cluster, 9 hits across the zoom range). |
| L8 | Open by design | F6 stays red as recorded; the popover lists every member (verified in F4/E-series picks). |
| L9 | **Fixed** | `role=group`, `tabindex=-1`: the tree shows the group with 66 marker buttons (12-hub file: 5, SYD named); Tab from `Run search` lands on a hub marker and reaches a cluster within 5 tabs; the svg itself is never a tab stop (H9, I5). |
| L10 | **Fixed** | Clicking the label text picks; clicking the label box's corner (between glyphs, the `rect.lblhit`) picks (F11 rewritten, I-series). |
| L11 | **Fixed** | Ocean click closes the list; a click on the list keeps it; a click on another cluster swaps lists (E9, I6). |
| L12 | **Mostly fixed → see N2** | Impossible `--min-sources` refused with 0 calls and before the prompt (the prompt is never asked, I11); the default follows a shorter list with the #20 sentence and lands in `_meta.thresholds`; duplicates asked once, in first-seen order, announced as 3; a gappy run keeps a more complete file, exit 5, `NOT OVERWRITTEN …` byte-exact, `.tmp`-free, and `--force` replaces it with `WRITTEN WITH GAPS` (B4–B6, I11–I13). But a **gapless** narrower run walks past the guard (N2). |
| L13 | **Fixed** | `regions` hold codes (`["EU"]`), the hostile label is counted in the #20 line and never stored (B13, I15). |
| L14 | **Fixed** | README names `--api-key`, `--file`, `--force`; the console line separates not-object rows (B14, B19, I16). |
| I1 | **Fixed** | `search-ran` = `The search has run: its results are under Show results.` in the column while the map is shown after a run; gone under Show results; the map's own footer is byte-stable (C9, E6). |
| I2 | **Fixed** | Two sublines, exactly one displayed: `Type an airport or city.` at 899, the map sentence at 900 (I8 ×2). |

Strings **#16–#20**: every one pinned byte-exact against the DOM or the console (I9 ×3,
I7 ×3 for #17, I8 for #18, I10/I11/I13 for #19, I11/I15 for #20).

## New findings

### N1 · Medium · The L2 push and the L7/L10 label rules combine into an unclickable cluster under the picked label

- **Repro**: 137-hub fixture, world zoom, type/pick `SFO`. Probes **I2, I3, I4**; shot
  `rt-I4-pushed.png`: the LAS "5" cluster is pushed 18px to the right of SFO - exactly
  under SFO's label, which a picked hub always draws (`picked || labelFits`) and which
  now carries a hit rect (L10). `elementFromPoint` at the cluster's centre returns
  `map-hub-SFO/lbl`; a click there is the From-airport no-op, the list never opens.
  The pushed cluster is also 13.3px from the MRY "3" cluster (18px rule between two
  clusters broken; the push checks only the picked hub, not the other markers).
- **Expected**: coordinator's rule for this round: no label overlaps a dot or another
  label; every marker keeps 18px; every marker clickable at its centre.
- **Location**: `map.js` `recluster()` - the push loop (only `pickedHubs` are avoided)
  and `singleMarker(h, labelFits)` (`picked ||` exempts the picked label). A picked
  label placed on the free side (left/above when right is taken), or hidden like any
  other when it would cover a marker, plus a push that re-checks the other clusters,
  would close it. Real data on day one (~141 airports) is the dense fixture.

### N2 · Low · A gapless narrower run still replaces a more complete capture without a word

- **Repro**: full 28-source capture, then `capture-hubs --sources aeroplan --yes`
  (every asked source answers → no gaps). Probe **I14**: the 28-source file becomes a
  1-source file, exit 0, no `NOT OVERWRITTEN`, `--force` not needed.
- **Expected**: "overwrite of a more complete capture refused without --force" - the
  guard keys on `gaps`, not on `sources_ok` shrinking.
- **Location**: `map_tools.run_capture` - `kept = _more_complete_existing(...) if gaps
  and not args.force`. Dropping `gaps and` (compare `sources_ok` counts always) is the
  one-token fix; `--sources` for "a partial or inspectable run" would then need
  `--force` or a different `--out`, which the README already offers.

### Info

- At 220px the autofill rows show the code and ~10 characters of the name; the `title`
  keeps the full name. Acceptable at that width.
- Ctrl-C while a source is in flight is a Python traceback (`KeyboardInterrupt` is not
  an `Exception`); nothing is written. Same as trips_tools.

## My own probes, corrected (as the coordinator noted)

- **F11** (round 1) zoomed about the pane centre and clicked SYD's label ~950px outside
  the pane - my error, not a defect; the L10 red was unfounded. Rewritten: wheel-zoom
  about SYD until its label shows, assert the label box is inside the SVG, click its
  centre and its top-left corner. Green at f1fe286.
- **E8** (round 1) expected the drawer to stay open beside the map after `Show map` -
  the exact behaviour M1 forbids; it and E7 could not both pass. Rewritten to the new
  rule (results shown: Esc closes suggestions then the drawer; map shown: `Show map`
  has closed the drawer, Esc closes the popover then the suggestions). Green.
- I7's first draft demanded ≥ 100px of the `Show map` toggle (99px natural width) and
  I4 looked for a cluster by lead code; both corrected before the run counted.

## Counts at f1fe286

| Suite | Result | Coder claimed |
|---|---|---|
| `python3 -O -m pytest -q -p no:cacheprovider` | **3819 passed, 13 skipped** | 3819 / 13 |
| same without `-O` | **3819 passed, 13 skipped** | 3819 / 13 |
| ui-probes (`-O -p no:randomly`) | **662 passed, 1 failed** (P22) | 662 / 1 |
| ui-restyle-probes | **279 passed, 7 failed, 10 skipped** (C2, C5[search_ok], E3, E5, H1, H2, J6 - unchanged pins/baseline) | 279 / 7 / 10 |
| map-search-probes (115 = 94 round-1 + 5 corrected/rewritten + 16 new) | **110 passed, 5 failed**: F6 (L8, by design), I2, I3, I4 (N1), I14 (N2) | 91 / 3 at 33dd029 (F6, F11, E8) |

Round-1 reds now green: B1–B6, B13, B14, B19, C4, C5, C6 ×2, C8, E7, E9, F4, F5, H3 ×2,
H9 (20 of 23); F11 and E8 green after my corrections; F6 red by design. Verdict on the
8 older probe reds unchanged (all pins or baseline; C2 still the manager's coral call).

---

# Re-test: fdd4a65

Fix commits d644183 (N2) and 5250645 (N1); coder report "Fix round 2". New probe file
`test_ms_j_final.py` (J1–J9): N1 with the 137-hub fixture at every button zoom step
in and out, picks at SFO, JFK, LHR, SIN, SYD alone and as all ten From/To pairs, plus
three pairs of hubs closer than 18px to each other; every audit collects EVERY problem
(18px between all markers, `elementFromPoint` at every on-screen marker centre must be
that marker, no label over a dot or a label, picked label present right or mirrored)
so one failure hides none. N2 with subset / superset / equal / disjoint source sets,
each with and without gaps and with and without `--force`, plus an empty, a broken and
a hostile existing file.

## Final status per finding

| # | Status |
|---|---|
| M1, M2 | Fixed (f1fe286), re-verified green (E7, H3 ×3, I7 ×3). |
| L1 | Fixed, re-verified (B1–B3, I10). |
| L2 | Fixed as far as multi-member clusters go: a cluster displaced from a pick lands on a free 18px spot, lists its members, the pick sits in the top layer (F4, I3, I4, J4 green). The single-member case is N1 below. |
| L3, L4, L5, L6 | Fixed, re-verified (C4–C6, C8, I9). |
| L7 | Fixed without a pick (I1: no label over any dot or label at 8 button and 12 wheel zoom steps). With a pick see N1. |
| L8 | Open by design (F6). |
| L9, L10, L11 | Fixed, re-verified (I5, I6, F11). |
| L12 | Fixed; the overwrite guard is now N2's. |
| L13, L14, I1, I2 | Fixed, re-verified (I15, I16, I7, I8). |
| N1 | **Partly fixed.** I2, I3, I4 (the round-2 probes) are green: the picked label no longer covers a cluster, clusters keep 18px from each other, the displaced cluster is clickable. The wider audit (J1, J2) finds two things left, below. |
| N2 | **Fixed.** Subset kept (exit 1 gapless, 5 with gaps, `NOT OVERWRITTEN … holds a capture with 4 source(s) ok, this run has 2/1.` byte-exact, file byte-identical, no `.tmp`), `--force` replaces (exit 0 / 5); superset replaces; equal set replaces (the newer run); disjoint: larger count replaces, smaller is kept, equal count replaces; the guard ignores the committed empty file, a broken file and a `sources_ok` full of non-strings (J5–J9, I13, I14 all green). |

## What is left (N1's remainder)

### N1a · Medium · A single hub within 18px of a pick is not moved: it sits under the pick, unclickable, under the pick's label

- **Repro**: 137-hub fixture, world zoom, pick `SIN` (alone or in any pair). Probes
  J1[SIN], J2[*-SIN] ×4; shot `final-sin.png`: KUL's hollow dot 6.5px from SIN's, inside
  the halo, under the mirrored `SIN` label; `elementFromPoint` at KUL's centre is
  `map-hub-SIN`.
- **Why**: the relocation moves `c.X/c.Y` and works for clusters (`clusterMarker(c)`
  draws at `c.X`), but `singleMarker(h)` draws at `h.X/h.Y` - the hub's own point - so
  a one-member cluster stays where it was, while the label placement (`labelSide(c.X,
  c.Y)` / the `dots` list) believes it moved and puts the picked label over it.
- **Location**: `map.js` `recluster()` → `singleMarker(c.members[0], side)` /
  `place(g, h.X, h.Y)`; drawing singles at `c.X/c.Y` closes it (the marker's testid
  and pick still identify the hub).

### N1b · Low · The picked hub loses its label when both sides are taken

- **Repro**: pick `SFO`, `JFK` (z = 1) or `LHR` (z = 1–1.5): J1 ×3, J2 ×8 (`picked-label-
  hidden`); shot `final-sfo.png` - filled dot with halo between the "3" and "5"
  clusters, no `SFO` text.
- **Expected**: plan §4.6 "Picked hub: … the label always shown"; the coordinator's
  bar for this round "picked label present or mirrored". The coder's round-2 rule
  applies right/left/hidden to picks too. The pick stays distinguishable (filled +
  halo, the only such dot), the input holds the code, so Low - but it is the plan's
  own sentence.
- **Location**: `map.js` `labelSide()`/`pickSides` - a third position (above or
  below) or a picked-label-wins rule (hide the neighbour's label instead) would keep
  the promise.

Everything else in the N1 audit holds: at every zoom step in and out, for every pick
and pair, 137 counted, no two marker centres within 18px (except N1a), every on-screen
marker returns itself from `elementFromPoint` (except N1a), no label over a dot or
another label (except N1a), picked labels mirrored left where the right is taken
(`text-anchor: end`), `SYD` clean at all 16 steps, the three <18px pick pairs
(SFO/SJC, JFK/EWR, LHR/LGW) leave everything else clean.

## Counts at fdd4a65

| Suite | Result | Coder claimed |
|---|---|---|
| `python3 -O -m pytest -q -p no:cacheprovider` | **3821 passed, 13 skipped** | 3821 / 13 |
| same without `-O` | **3821 passed, 13 skipped** | - |
| ui-probes (`-O -p no:randomly`) | **662 passed, 1 failed** (P22, plan-invalidated pin) | 662 / 1 |
| ui-restyle-probes | **279 passed, 7 failed, 10 skipped** (C2, C5[search_ok], E3, E5, H1, H2, J6 - unchanged) | 279 / 7 / 10 |
| map-search-probes (143 = 115 + 28 new J) | **129 passed, 14 failed**: F6 (L8, by design); J1[SFO/JFK/LHR] and J2 ×8 → N1b; J1[SIN] and J2[*-SIN] ×4 → N1a (two of the J2 cases carry both) | 114 / 1 (before the J-series) |

Round-1 and round-2 reds now green: every B, C, E, H probe; F4, F5, F11; I2, I3, I4,
I14. Verdict on the 8 older probe reds unchanged.

---

# Round 3 attack + pin refresh: 91081c9

Fix round 3 = 51a4fe1 (map.js: singles drawn where they were moved; the picked label
always shown - right, left, above, below, four diagonals, four of an outer ring, its spot
reserved BEFORE neighbouring clusters are displaced; last resort = label right with no hit
rect and `pointer-events: none`). The manager's review (runs/map-search/manager-review.md)
noted round 3 had never been attacked. New probe file `test_ms_k_round3.py` (K1–K5, 16
probes) builds a fixture for every new path, and the six stale pins were refreshed.

## The round-3 attack (all green)

Fixtures are synthetic hub files placed by screen offset: the pick (SFO's real point), and
2-hub clusters (r 10.5) at chosen offsets ≥ 19px from the pick (so they stay) and ≥ 19px
from each other (so they never merge), converted to lat/lon through the projection's
inverse at the zoom-1 scale measured from the SVG's CTM. Each case runs the audit: 18px
between every pair of markers, every on-pane marker returns itself from
`elementFromPoint` at its centre, no label over any dot or any label, the picked label
present with the expected `x`/`y`/`text-anchor`, a click on the label's centre reaching
the pick and never a cluster.

| Probe | Path forced | Result |
|---|---|---|
| K1 ×12 | each of the twelve positions. Ten (right, left, above, below, four diagonals, above-2, below-2) by the smallest non-merging blocker set found by exhaustive search over a 9×9 offset grid (the set is verified against the plan's boxes before the browser sees it). **right-2 and left-2 cannot be forced by dots alone** - any staying marker that covers the inner box covers the outer one - so they are reached through the OTHER pick's label placed first (more routes): right-2 with the second pick 20px left as the last resort, left-2 with the second pick at (−15, −13) labelled above; an independent Python simulation of the placement rule predicts both, and the DOM agrees byte for byte (x, y, anchor, hit rect, pointer-events). | green |
| K2 | last resort: eight clusters at 26px on the compass points take every position; the label is drawn right with no `rect.lblhit` and `pointer-events="none"`; `elementFromPoint` at the text's centre is the east cluster and a click there opens THAT cluster's list, the inputs untouched | green |
| K3 | reserve-before-move: a 2-hub cluster 10px east must move and its first candidate (east, 18.6px) is under the reserved right label; it goes elsewhere (≥ 17.5px, not east), the label stays right with its hit rect, the moved cluster lists its two members | green |
| K4 | N1a on purpose: a single 6px from the pick and a 2-cluster 12px the other side, at z = 1 and seven zoom steps: both moved to free spots, both clickable, the single picks when clicked | green |
| K5 | two picks 26px apart, each labelled right with a hit rect, no label over anything | green |

Shots `K1-00-right.png` … `K1-11-below-2.png`, `K2-last-resort.png`, `K3-reserve.png`.

**Findings**: none that fails. Two observations, Info: (a) a last-resort label drawn as
the second pick's (K1[8]: `M01` runs under SFO's dot) is partly unreadable - inherent in
"last resort", and the input holds the code; (b) `right-2`/`left-2` are in practice only
reachable through another pick's label, so the outer ring is mostly decoration - harmless.
N1a, N1b: **fixed**. J1/J2 (137-hub fixture, five picks, ten pairs, every zoom): green.

## Pin refresh (the manager's must-fix 2)

Each of the six was the team's own pin from an earlier round; the manager confirmed each is
a pin the plan invalidated, not a regression. Refreshed in place, each with a docstring
naming the round, the commit and the plan section; each states "a pin, not a regression".

| Probe | Was | Now | Pin, not regression, because |
|---|---|---|---|
| ui-probes **P22** | three UTF-8 entry points | four: `src/map_tools.py` added; new **P22b** imports `src.map_tools`, calls `main([])` in-process under `LC_ALL=C` and proves `sys.stdout` is the same object with the same encoding | the guard is inside `if __name__ == "__main__"` (map_tools.py:640), exactly as trips_tools; the `all(guards)` check already covered it |
| restyle **E5** | engine/api/serialize/server byte-identical to 386b2fc | engine/api/serialize still identical to 386b2fc; server.py identical to 16f53b7 (the map round's static map, plan 2.16) | `/api/state`'s key shape never moved; server.py's only change is `STATIC_FILES` as (Path, ctype) + 404 on a missing hubs.json |
| restyle **H1** | server.py + CSP byte-identical to 386b2fc | api/engine/serialize/main/formatter identical to 386b2fc; server.py identical to 16f53b7; the `CSP = (…)` literal byte-identical to 386b2fc | the CSP string is unchanged (also pinned by map-search H4) |
| restyle **H2** | only `app.js` may be a `<script>` | exactly `["/static/map.js", "/static/app.js"]`; no inline body, no `style=`, no `on*=` | plan D7: one more same-origin script, `defer`, document order |
| restyle **J6** | app.js diff from 386b2fc = 6 hunks with the restyle's strings | the restyle's 6 hunks (386b2fc..56742af) unchanged with the same allowed strings; plus the map round's 7 hunks (56742af..HEAD) whose added strings are enumerated: plan 4.9 #9/#10/#13/#17/#18, the testids of 4.10, class names, DOM/ARIA tokens, and the existing wording the split `renderSearch` re-emits verbatim | plan 4.7 enumerates the edits; no wording change, no testid renamed |
| restyle **C2** | `--accent2` only in `.btn-primary` | `.btn-primary` rules plus exactly one `.route` rule | decision A (manager): the route line stays coral as the approved RoutePicked mockup draws it (D12); dashed, 1.5px, never a button |

E3, C5[search_ok] (restyle baseline reds) and F6 (map-search, by design) are left as they
are, as ordered.

## Counts at 91081c9

| Suite | Result |
|---|---|
| `python3 -O -m pytest -q -p no:cacheprovider` | **3821 passed, 13 skipped** |
| same without `-O` | **3821 passed, 13 skipped** |
| ui-probes (`-O -p no:randomly`, P22 refreshed, P22b added) | **664 passed, 0 failed** |
| ui-restyle-probes (E5, H1, H2, J6, C2 refreshed) | **284 passed, 2 failed, 10 skipped** - E3, C5[search_ok]: the restyle baseline, left as ordered |
| map-search-probes (159 = 143 + 16 K) | **158 passed, 1 failed** - F6 (L8, by design) |

Every finding of this feature is closed except L8 (design limit, popover) and the two
baseline reds that predate it. Ship.

---

# Final: c68e3eb

Coder fix round 4 (c5c52fb): `ensureMap` catches a `mount()` that throws and shows string
#2 with the error's message; doc drift (28 sources, decisions A and C recorded).

- **A7** re-based: the goldens untouched since the base; the two older probe trees
  untouched since be0d27b, the tester's own pin refresh - the only edit they have had
  (the four files are named in the probe). Green.
- **C10** (new): `fetch()` itself rejecting for hubs.json → `AIRPORT DATA UNREADABLE -
  /static/hubs.json could not be read (fetch failed). The map plots nothing.`; for
  land.json → #16 with `fetch failed`; no markers, no page error, the typed form searches.
  Green.
- **C11** (new): `POMap.mount` made to throw before app.js runs (an init script, no inline
  script in the page): the pane shows #2 with `boom from mount` byte-exact, no page error;
  `S.map.loading` is proved cleared by a second mount attempt on the next render (tab round
  trip), still one `map-status` element; the typed form searches. Green.
- **J6** re-pinned once more: the only app.js change since be0d27b is the round-4 `.catch`
  hunk (`git diff be0d27b..c68e3eb -- src/ui/static/app.js` = that hunk alone; plan 4.9
  #2 / §8's `map.js not loaded` case generalised), its three strings added to the allowed
  set, the docstring says so. A pin, not a regression.

## Counts at c68e3eb (observed)

| Suite | Result |
|---|---|
| `python3 -O -m pytest -q -p no:cacheprovider` | **3821 passed, 13 skipped** |
| same without `-O` | **3821 passed, 13 skipped** |
| ui-probes (`-O -p no:randomly`) | **664 passed, 0 failed** |
| ui-restyle-probes | **283 passed, 3 failed, 10 skipped** before the J6 re-pin (C5[search_ok], E3, J6); after it `-k J6` is green → **284 / 2 / 10** with C5[search_ok] and E3 the restyle baseline, left as ordered |
| map-search-probes (161 = 159 + C10 + C11) | **160 passed, 1 failed** - F6 (L8, by design) |

Ship.
