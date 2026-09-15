# Manager review: map-based search home page

Manager, 2026-09-14. Branch `feature/map-search` at b20ec57 (over `feature/ui-restyle` 56742af).
Read: `docs/plans/map-search.md`, `runs/map-search/coder-report.md` (all three fix rounds), the
diff `56742af..HEAD` (map.js in full, the app.js hunks, server.py, map_tools.py, tools/*.py,
data/hubs.json, app.css, index.html), `docs/test-reports/map-search.md` (round 1 and both
re-tests) and the probes under `docs/test-reports/map-search-probes/`, plus the diff of 51a4fe1
on its own because the tester never attacked it. Everything under "What's solid" I ran or
diffed myself.

## Verdict

**Ship with fixes.** The map does what was approved, the engine/CLI/goldens/security surface is
byte-identical, the tool cannot spend or write anything it did not announce, and every suite is
where the coder says it is - but the last code commit has had no adversarial pass, two older
probe suites have been left red for three rounds, and one visual (the letterboxed world at zoom
1) is not the picture Tsuki approved and nobody has said so.

## Must fix before ship

1. **Tester attacks 51a4fe1 (fix round 3) once.** The coder verified N1a/N1b only by re-running
   the tester's own J-series; I re-ran it too (142/1, F6 only) - that is a re-run, not an attack.
   The new code in `map.js` `recluster()` has three paths no fixture reaches: the ten new label
   positions (above/below/diagonals/second ring), the "reserved before the neighbours move"
   ordering, and the last resort (a picked label drawn over whatever is there, without its hit
   rect, `pointer-events="none"`). Also untested: a moved cluster that finds none of its 24 spots
   free and stays on the pick. Two picks 20-40px apart at zoom 2-4 with a dense fixture is the
   attack. Half a session; the brief's "no ship without the adversarial round" applies to the
   last commit, not the first.
2. **Re-base the six stale pins so both older suites are 0-red by test id again**: ui-probes
   `P22` (entry-point set becomes four files), ui-restyle-probes `E5`, `H1`, `H2`, `J6` (server.py /
   script list / app.js diff pinned to 386b2fc) and `C2` after decision A below. All six were
   called "plan-invalidated, refresh" by the tester in round 1 and are still red at HEAD. Brief §5
   says the suites are compared by id and ui-probes is 0 red; a suite that stays red is exactly
   how the next regression hides. Tester's job; an hour.
3. **Say what the first screen looks like, or change it.** The approved `Main.png` fills the
   pane with the world (crop `0 30 1000 440`); the built page at zoom 1 (`shots/a-empty-hub-
   file.png`, `d-route-picked.png`) shows the world letterboxed with ~130px of empty ocean above
   and below in a 1440x900 window. The coder's deviation 1 is right that the plan's crop cannot be
   panned at z=1, but the fix chosen (show everything) was never shown to Tsuki as a change from
   the mockup. Either Tsuki accepts it (decision C) or the pane's height follows the map's
   aspect (`height: calc(width * 0.52)`-style, one CSS rule) so the world fills it. Not a code
   risk; an approval gap.

## Decisions Tsuki needs to make

- **A. Route line colour (restyle probe C2).** The approved `RoutePicked.png` draws the line in
  coral (`--accent2`); ui.md §4.7 says coral is "the ONE primary action". The built line is
  dashed, 1.5px, non-scaling, `pointer-events: none` - it cannot be mistaken for a button, and it
  is the one thing on the map the eye should find. **Recommend: keep coral as approved**, amend
  §4.7's sentence to "coral = the primary action and the picked route", and refresh C2 to allow
  `.route`. The reversal is one token in `app.css` if you prefer the rule over the picture.
- **B. The `data/airports.csv` gate (plan D1).** The map plots only hubs the engine will accept
  (141 codes, 137 with coordinates). What you will actually see after the capture: **at most 137
  dots - the airports.csv list minus any that fewer than 3 programmes serve** - and a footer
  like `N airports Seats.aero tracked on 2026-09-xx · K not plotted (J not in data/airports.csv,
  M without coordinates)` where N is probably several hundred and J most of them. The dots are
  "main hubs only", as you asked; the sentence is honest (brief §2) but reads like a defect and
  names a repo file. **Recommend: accept for v1.** The alternative (plot everything) puts dots on
  the map that preflight refuses - a click into a wall, worse than a big number. The tool prints
  the J candidates as `iata,name,country`; growing airports.csv needs a surcharge region per row
  and is the next round's decision, made from your paste-back. Not a trap, provided you read the
  footer before the map.
- **C. Zoom-1 letterbox vs the mockup's crop** (must-fix 3). Recommend: look at it on the Mac at
  your window size first; if the empty bands bother you, the aspect-locked pane is the cheap fix.
- **D. Results replace the map (plan D8).** Built as planned: after a run the map is gone and a
  `Show map` / `Show results` button toggles. Cost the plan did not price: with the drawer open the
  results column is 708px at 1440 where 56742af had 948, and the search column shrinks to 220px.
  If you pictured the map staying above the results, the plan's reversal (map at reduced height
  above `#search-result`) is CSS plus the toggle; testids do not move. Recommend: live with it
  for a week, then decide.

## Should fix soon

- `app.js` `ensureMap()`: `POMap.load().then(mount…)` has no `.catch`; `load()` never rejects but
  if `mount` ever throws, `S.map.loading` stays true and the pane is blank with no sentence -
  the one state map.js promises never to show. One `.catch` that writes string #2.
- Doc drift: `.gitignore` says the airportsdata test "skips when [the CSV] is absent" - it pins a
  sha256 instead (coder deviation 8); plan §2.3 says 30 sources, the code has 28 (coder noted it,
  nobody fixed the plan); the plan's §9 still describes L2/L7-era label rules.
- The ui-restyle probes E5/H1/H2/J6 pin `server.py`, the `<script>` list and the exact app.js
  diff to 386b2fc; every future UI round will trip them the same way. Re-base them to "diff
  against the branch's own base" or retire them (part of must-fix 2, but the design is the issue).
- `map.js` `recluster()` is now ~130 lines of pixel geometry (relocation rings, 12 label
  positions, reservations) with no unit test - only browser probes. It is the least reviewable
  code on the branch and it exists to serve a 137-dot fixture. A simpler rule ("picked label
  wins; a neighbour that would collide loses its label; clusters list their members") would have
  met "minimal". Not worth reopening now; worth remembering when it next breaks.
- `docs/test-reports/map-search-probes/shots-out/` still holds round-1 images (`E7-map-beside-
  drawer.png`, `F4-picked-over-cluster.png`) that describe defects fixed two rounds ago.
- The provenance footer names `data/airports.csv` in the UI. Fine for a local tool; when the
  wording is next open, "not searchable by this tool" says the same to a reader who has not
  seen the repo.
- Below 900px the column heading `Where are you flying?` stays; the subline swap (I2) is done.

## Agent performance

- **Architect:** the best plan this project has had - every call reversible and named, §9 was
  honest, the tool's exit codes and the "at most N calls" sentence were specified before a line
  was written, and D1/D4 kept the brief's rule (no invented airports) intact. Misses: §2.18 asked
  for `role="img"` *and* buttons inside it (a contradiction, L9); "30 sources" was not checked
  against the code (28); the drawer-beside-map rule was in §7 but not in §4.7's enumerated
  edits (M1); the grid arithmetic that gave results 308px at 1180 was never done (M2); and the
  zoom-1 crop was flagged as a risk and then left to the coder to resolve silently.
- **Coder:** disciplined on every boundary that matters - engine/CLI/goldens/CSP untouched,
  `textContent` everywhere, one global, every deviation listed, three fix rounds each small and
  correctly scoped, `git diff` of the last fix is exactly the two findings. But round 1 shipped a
  tool whose docstring promised "nothing on disk on every failure" while `_write_atomically`
  had no try/except (L1) - the same "verified" gap as the restyle round's - and "verified by hand
  in headless Chromium" for the layout-dependent behaviour turned into L2/L7/N1 the moment the
  tester wrote the probes. Round 3 added no test of its own.
- **Tester:** strong - 143 probes, the audits collect every problem instead of stopping at the
  first, own bad probes (F11, E8) called out and corrected in the report, the 8 older reds
  triaged on day one. Two real misses: **fix round 3 was never attacked** (the coder's last
  commit shipped on the tester's own re-run), and the six stale pins the tester diagnosed as
  "refresh the set" in round 1 are still red three rounds later - diagnosing a stale probe and
  leaving it red is half the job.

## What's solid (verified by me at b20ec57)

- `python3 -O -m pytest -q -p no:cacheprovider`: **3821 passed, 13 skipped** (240 s).
- `docs/test-reports/map-search-probes` (`.venv/bin/python -O`, `-p no:randomly`): **142 passed,
  1 failed** - `F6` only (metro pairs never separate at 8x; recorded as a design limit, the
  popover lists them). The J-series (N1 audits at every zoom, five picks, ten pairs) is green
  against 51a4fe1.
- `docs/test-reports/ui-restyle-probes`: **279 passed, 7 failed, 10 skipped** - exactly `C2`,
  `C5[search_ok]`, `E3`, `E5`, `H1`, `H2`, `J6`. C5/E3 are baseline reds from the restyle round.
  E5/H1/H2/J6 pin bytes to 386b2fc; behind each I checked the behaviour: the `server.py` diff is
  only the `STATIC_FILES` `(Path, ctype)` map plus the 404-on-`OSError` branch, the CSP string
  has no diff, the shell has two `<script src>`s and no inline script/style/handler, and the
  app.js diff carries no wording change or testid rename. Plan-invalidated pins, no regression.
- `docs/test-reports/ui-probes`: **662 passed, 1 failed** (771 s) - `P22` only. Its guard half (every
  `use_utf8_output()` call inside `if __name__ == "__main__"`) still holds for all four files;
  only the hard-coded three-file set fails. `src/map_tools.py:724` is guarded exactly like
  `trips_tools`. Plan-invalidated pin, not a gap in the guard.
- `git diff 56742af..HEAD` is **empty** for `src/main.py src/formatter.py src/optimizer.py
  src/live_trip.py src/seats_client.py src/trip_builder.py src/regions.py src/ui/api.py
  src/ui/engine.py src/ui/serialize.py src/config.py data/airports.csv tests/goldens`.
- `tools/build_land_path.py --check`: matches (212,308 bytes).
- `map.js`: no `innerHTML`/`outerHTML`/`insertAdjacentHTML`/`eval`/`javascript:`; every name,
  city and code reaches the DOM through `textContent` or `setAttribute` on a fixed attribute.
- `data/hubs.json` is `hubs: []` with the full `_meta` and the "No capture has run" note; the
  synthetic 12-hub fixture is under `tests/fixtures/ui/`, marked `synthetic: true`, and every
  row of it comes from the airportsdata CSV.
- No hand-typed airport: the only 3-letter literals in the app.js diff are the placeholders
  `SFO`/`MAD` that 56742af already had; none in map.js, map_tools.py, server.py or tools/.
- The capture tool (read, and `tests/test_map_tools.py` read): every request is counted on the
  shared budget *before* it is made; the announced number is `len(sources)` after dedupe and the
  loop can only make fewer (429 stops it, an exhausted budget skips); every refusal test asserts
  `stub.calls == []` and an empty output directory; the document passes `hubs_file_problems`
  and the key-egress check before the atomic write; `_meta` is always complete.
- Against the mockups: the column (heading, subline, fields, coral `Run search`), the map's
  dots/clusters/count glyphs, the London popover, the picked dot with halo and label, and the
  dashed coral great circle are the approved direction C pictures. Differences, all planned
  and disclosed: no chips-with-x (inputs stay inputs), no "One-way · Jan 15 2027" subline, the
  existing `Run search` label, a `To date` field the mockup omitted, the pane framed with a
  footer band instead of full-bleed, and the zoom-1 letterbox (must-fix 3). Fonts are the
  sandbox fallback, as last round.
- "Minimal": nothing user-facing was built that Tsuki did not ask for. The extras are all on
  the tool side (`mark-searchable`, `--raw-dir`, `--force`, the overwrite guard, the histogram)
  or tester-driven geometry; each is small and none touches the flow.

## What Tsuki must do on his Mac

```bash
cd ~/Downloads/points-optimizer-git
git fetch && git checkout feature/map-search
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_map_assets.py tests/test_map_tools.py
.venv/bin/python -m src.map_tools capture-hubs --raw-dir ~/Desktop/seats-routes-raw
```

Expect: the key banner (`Seats.aero key: pro_…xxx (source: …)`), then

```
This will make at most 28 Seats.aero API call(s): one GET /partnerapi/routes per source (28 sources). This process has spent 0 of 1,000; Seats.aero also counts your other runs today, which this tool cannot see.
Continue? [y/N]
```

Type `y`. Then one line per source (`aeroplan: ok (N routes)` or `…: failed(HTTP 4xx)` /
`unreadable(…)` / `incomplete(…)`), `This process has spent 28 of 1,000 today.`, the routes-seen
line, the histogram (5 rows x 4 columns), `N hubs (K searchable, M without coordinates, J not in
data/airports.csv)`, the J candidate lines, `wrote data/hubs.json`, and either `every source
answered.` (exit 0) or `WRITTEN WITH GAPS …` (exit 5). Exit 1 with `every source failed (28 of
28)` means the endpoint is not what the plan assumed - nothing is written, stop there.

Then `.venv/bin/python -m src.ui`, open the Search tab, click two airports, run one search.

Paste back: the whole console output from the key banner to the last line (the key is masked),
`echo $?`, the first 5 lines of `~/Desktop/seats-routes-raw/routes_united.json` (so the team
finally sees the real response shape - do not commit that folder), and a screenshot of the
Search tab at your normal window size. Then `git add data/hubs.json && git commit -m "Capture
hubs from Seats.aero"` and push. The 28 calls come out of the day's 1,000.

## Re-review: 7a8310c

Since 91081c9: be0d27b (tester: K-series attack on round 3, six pins refreshed), c5c52fb +
c68e3eb (coder fix round 4: `ensureMap` `.catch`, doc drift, decisions A and C recorded),
7a8310c (tester: A7 re-based, C10/C11, J6 re-pinned, "Final" section).

**Verdict: Ship.** Must-fix 1 and 2 are done and were done properly; must-fix 3 I withdraw
(below). One tester probe is red at HEAD against its own report, which is a tester nit, not a
ship blocker. The handoff condition is the Mac capture.

### Must-fixes, checked

1. **Round 3 attacked - yes, truly.** `test_ms_k_round3.py` (K1-K5, 16 probes) builds synthetic
   hub files from the projection's inverse to force each of the twelve picked-label positions
   by name (K1, including the two outer-ring positions that only a second pick's label can
   force), the last resort with a click through the label landing on the cluster beneath (K2),
   a displaced cluster whose first candidate spot is the reserved label box (K3), N1a's
   moved-single case through seven zoom steps (K4), and two picks with mutually avoiding labels
   (K5); every case runs the full audit (18px, `elementFromPoint`, no label over dot/label). All
   green at HEAD in my run. The one path still unforced is a moved cluster with all 24 spots
   taken (it stays on the pick); it needs nine markers within 40px of a pick and is not worth
   a probe.
2. **Pins refreshed - yes, not loosened.** P22 now names four entry points and P22b proves the
   fourth leaves stdout alone in-process; E5 pins `server.py` to 16f53b7's bytes and the CSP
   string to the base; H2 pins exactly `["/static/map.js", "/static/app.js"]`; J6 pins the
   restyle's 6 hunks unchanged plus an explicit allow-list of every string the map round added
   (I read the list: the §4.9 sentences, testids, class and ARIA tokens, and the existing
   wording renderSearch re-emits - nothing else gets through); C2 allows `.route` per decision
   A. Each is a real pin again.
3. **Letterbox - withdrawn.** I re-read `RoutePicked.png`/`Main.png`: the approved pictures also
   show ocean above Greenland and below South America at 1440x900; the built page's bands are
   the same shape, slightly deeper. Decision C is recorded in plan §2.20 and the built page is
   what was approved. My must-fix overstated the difference.

### Round 4 (c5c52fb), checked

`git diff 91081c9..HEAD -- src/` is the seven-line `.catch` in `app.js` `ensureMap()` and
nothing else; `server.py`, `map.js`, `map_tools.py`, the engine files and `tests/goldens`
have no diff. The `.catch` clears `S.map.loading`, replaces the host with string #2 carrying
the error's message via `textContent`, and C11 proves it (mount made to throw by an init
script; the sentence shows, no page error, a second mount is attempted on the next render,
the typed form still searches). C10 covers `fetch()` itself rejecting for both files. Plan
§2.3/§8 now say 28; `.gitignore` says sha256; ui.md §4.7 carries decision A in one sentence.

### Counts (this machine, 7a8310c)

| Suite | Result | Tester claimed |
|---|---|---|
| `python3 -O -m pytest -q -p no:cacheprovider` | **3821 passed, 13 skipped** (193 s) | 3821 / 13 |
| ui-probes (`-O -p no:randomly`) | **664 passed, 0 failed** (587 s) | 664 / 0 |
| ui-restyle-probes | **284 passed, 2 failed, 10 skipped** - `C5[search_ok]`, `E3` (restyle baseline, left by decision) | 284 / 2 / 10 |
| map-search-probes (161) | **159 passed, 2 failed** - `F6` (design limit) and **`A7`** | 160 / 1 |

**A7 is red at HEAD and the report says it is green.** The tester re-based A7 in 7a8310c to
"the two older probe trees are untouched since be0d27b" and, in the same commit, edited
`ui-restyle-probes/test_r_g_fonts_security_docs.py` (the J6 re-pin). `git diff be0d27b..HEAD`
on that tree is one file, 7+/2-, and the assertion fails. The tester evidently ran A7 with the
J6 edit still uncommitted (`git diff be0d27b..HEAD` cannot see a working-tree change), reported
160/1, then committed. Nothing about the product is wrong; the probe pins the tester's own
last commit and the tester moved it. Fix: pin to 7a8310c (or, better, name the files the tree
may differ in, as the third assertion already does). Tester's nit; do it with the next commit.

### Agent performance, this pass

- **Tester:** the K-series is exactly what "attack, don't re-run" means - fixtures derived from
  the code's own geometry to reach every branch, with a click-through proof for the last resort.
  The pin refreshes are careful. Then the A7 self-pin: the third time this round a probe has
  pinned a byte the tester's own next edit moves (F11/E8 in round 1, A7 now), and this one was
  reported green when HEAD says otherwise. Run the suite after the commit, not before.
- **Coder:** round 4 is the two lines asked for and the four doc edits, with the counts stated
  honestly including A7's red and why a coder commit cannot fix it. Clean.

### What Tsuki must do on his Mac

```bash
cd ~/Downloads/points-optimizer-git
git fetch && git checkout feature/map-search && git pull
.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_map_assets.py tests/test_map_tools.py
.venv/bin/python -m src.map_tools capture-hubs --raw-dir ~/Desktop/seats-routes-raw
```

It prints the key banner (`Seats.aero key: pro_…xxx (source: …)`), then

```
This will make at most 28 Seats.aero API call(s): one GET /partnerapi/routes per source (28 sources). This process has spent 0 of 1,000; Seats.aero also counts your other runs today, which this tool cannot see.
Continue? [y/N]
```

Type `y`. Then one line per source - `aeroplan: ok (N routes)`, or `…: failed(HTTP 4xx)`,
`…: unreadable(…)`, `…: incomplete(…)` - then `This process has spent 28 of 1,000 today.`, the
routes-seen line, the histogram (min-sources 1-5 x min-routes 5/10/20/50), `N hubs (K
searchable, M without coordinates, J not in data/airports.csv)`, one `iata,name,country` line
per unsearchable airport, `wrote data/hubs.json`, and either `every source answered.` (exit 0)
or `WRITTEN WITH GAPS …` (exit 5). Exit 1 with `every source failed (28 of 28); nothing was
written.` means the routes endpoint is not what the plan assumed - stop there and paste the
per-source lines. Expect at most 137 dots afterwards (decision B) and a footer whose "not
plotted" number is large; that is the airports.csv gate, not a fault.

Then `.venv/bin/python -m src.ui`, Search tab: click two airports, run one search, `Show map`.

Paste back: the whole console output from the key banner to the last line (the key is
masked), `echo $?`, the first 5 lines of `~/Desktop/seats-routes-raw/routes_united.json` (the
real response shape - do not commit that folder), and a screenshot of the Search tab at your
usual window size. Then `git add data/hubs.json && git commit -m "Capture hubs from
Seats.aero" && git push`. The 28 calls come out of the day's 1,000.
