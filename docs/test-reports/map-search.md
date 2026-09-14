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
