# Tester report: search -> trip, and the delete button

Branch `feature/search-to-trip`, coder head **d6be134** (9 commits over `feature/map-search`
at 4f879d4). Plan: `docs/plans/search-to-trip.md`. Coder report:
`runs/search-to-trip/coder-report.md`. Probes: `docs/test-reports/search-to-trip-probes/`
(harness pattern of the map-search probes; screenshots to the gitignored `shots-out/`, the
record copies in `shots/`). Every delete in this round ran against a tmp copy of
`tests/fixtures/trips/`; the harness compares the repository directory's mtimes before and
after every server, and a tripwire in the coder's own suite lists the committed fixtures.
No test called seats.aero: the probe server refuses every `connect()`, Chromium resolves
nothing but 127.0.0.1, and the round-1 socket canary is armed in the test process.

**Verdict: ship-able after two small fixes.** No security finding. No golden, parity, server
or fixture byte moved. Four findings, all Low; one of them (F1) is a wrong sentence in a
race, one (F2) is a width regression the plan's own scope rule covers, two are keyboard /
stale-render gaps that this round inherits and extends.

## Findings

### F1 - Low - A raced second delete says the file "cannot be read as a trip"

- **Repro** (`test_st_a_delete_security.py::test_A15`, deterministic; `test_A11` hits it
  about once in ten runs of two concurrent deletes): preflight the same UI-built trip twice
  (two confirms); delete #2 passes `trip_path` and redeems its valid confirm; delete #1
  runs to completion; delete #2 takes the lock and re-checks provenance on a file that is
  now gone.
- **Expected**: 404 `No trip '<id>' in <dir>.` (R4), the answer a second delete of the id
  gets a moment later; or the 409 `busy` R5 the lock gives when the two overlap exactly.
- **Actual**: 409 `not_deletable` with R1 - `NOT DELETABLE - <file> cannot be read as a
  trip, so where it came from cannot be checked. Nothing was deleted; remove it by hand if
  you know what it is.` - for a file this very page just deleted. The sentence invites the
  user to go and remove a file that no longer exists.
- **Location**: `src/ui/engine.py` `deletable_reason` (`except (OSError, ValueError)` folds
  `FileNotFoundError` into R1) called from `trip_delete` under the lock. Fix: in
  `trip_delete`, `if not path.exists(): raise self._no_trip(trip_id)` before the re-check,
  or let `deletable_reason` return a distinct value for a missing file. Nothing wrong
  happens on disk: exactly one unlink, nothing else touched (A10/A11 assert that).

### F2 - Low - The `trip-deleted` landing panel widens the page at 400 px with a 120-char name

- **Repro** (`test_st_d_delete_ui.py::test_D3b`, shot `shots/d3b-deleted-400.png`): delete
  the trip named `x` * 120 at a 400 px viewport.
- **Expected**: plan 3 "nothing widens the page"; the dialog itself was given
  `overflow-wrap: anywhere` for exactly this name and fits (asserted, `d3b-dialog-400.png`).
- **Actual**: `Deleted /…/trips/xxxx….json` is one unbroken token in a `panel funding` div
  with no `overflow-wrap`; `document.scrollWidth` 930 at `clientWidth` 400. The
  pre-existing `nt-wrote` panel (`Wrote <file>`) has the same gap with the same name
  (recorded in the probe's docstring, not this round's).
- **Location**: `app.js` `renderTrips` (`trip-deleted`), `app.css` - one rule
  `.funding > div { overflow-wrap: anywhere; }` or the panel's own class fixes both.

### F3 - Low - Focus is dropped to `<body>` after Cancel/Esc on the delete dialog and after the delete

- **Repro** (`test_st_d_delete_ui.py::test_D8`): keyboard: Delete trip -> Enter -> Esc.
- **Expected**: focus back on `trip-delete` (the app already does this for the drawers:
  `closeDrawer` records `cameFrom` and refocuses the rebuilt element, with a long comment
  on why - 18 Tab presses back). After a successful delete, focus on something reasonable
  (the trip list or `new-trip`).
- **Actual**: `document.activeElement` is `<body>` in both cases. The spend dialog
  (`openConfirm`) has the same gap - pre-existing - and `openDialog` copied it (D14 said
  "its own small dialog builder", which was the moment to do better).
- **Location**: `app.js` `openDialog` / `closeConfirm`.

### F4 - Low - Delete stays disabled (and Run says "Running…") on another trip's page after the run finished elsewhere

- **Repro** (`test_st_d_delete_ui.py::test_D5`, recorded): start a run on trip X, open
  trip B while it runs, wait for it to finish.
- **Expected**: plan D11 - "disabled client-side while `S.busy`"; `S.busy` is false once
  the run returns, so the button should be enabled.
- **Actual**: `sendRun` re-renders only when `S.tripId === id`; on any other trip the page
  keeps `run-busy`, `Run` reads "Running…" (disabled), and `Delete trip` is disabled,
  until something else re-renders. Pre-existing for `run-go`; this round adds a second
  button to the stale state. Clicking through it is not possible (disabled), and the server
  would refuse correctly anyway.
- **Location**: `app.js` `sendRun` (`refreshState()` without `renderTrips()` on the else
  branch of `S.tripId === id`).

## Judgements

**C5 (restyle probe, `search_ok`: 3 `.btn-primary`)** - the coder's deviation 6 made
`search-add-trip` coral, the twin of `drawer-to-trip`. Who is right: the plan is silent -
D2 fixes label, testid and box but no class; D13 fixes only Delete's class and quotes ui.md
4.7 "the ONE primary action" as the reason. ui.md 4.7 itself lists `Score against a fare`
among `.btn-primary`'s uses, so the coder's reading ("its twin is primary too") is
defensible, and C5 was already red at the baseline (2: `Run search` + `drawer-to-trip`).
This round makes the results screen the only one with THREE coral buttons visible at once
(`shots/c10-search-picked-1440.png`). My recommendation, one class name: keep
`search-add-trip` coral - it is the action the selection exists for and the one Tsuki asked
for "at the bottom of the search" - and demote `drawer-to-trip` to `.btn`. Then C5 reads 2
with the drawer open and 2 with it closed, no worse than the baseline. A manager's call, not
a defect I can pin.

**Deviations 1-10**: 1 (step-3 commit holds step-4 engine code, unwired) - fine, stated.
2 (`link_display` for R8/R9 names the link, not its target) - correct and asserted
(A2: the sentence never names `elsewhere`/`README`). 3 (`(source: "")` for `trip_001`) -
acceptable; the sentence is a little odd but exact. 4 (malformed shapes -> R3) - the right
side of every edge (B2: 30 hand edits classified; a non-list `legs`, a non-dict leg, a
non-list `cash_options`, `trip_level_flags` as a string, `points_candidates: null`, a
duplicate key that the parser resolves to the last value - all refused). 5 (box only when
the table is) - agreed; a disabled offer under `api_error` would read as an offer. 6 - above.
7 (`(program not named)` in P3/P4) - asserted (C4c). 8 (`S.lastDelete` cleared when any trip
renders) - fine; it survives Search-tab-and-back, which is right. 9 (`panel refusal` for P7)
- it is the refusal form; fine. 10 (non-200 does not re-fetch the detail) - recorded in D6;
the preflight's re-check catches every stale button click with the right sentence (R3 in the
banner, file intact), so the stale `enabled` is cosmetic.

**Known gaps**: the lifted cash field (shot `c10-prefilled-1440.png`) - visible, cosmetic.
The cabin filter hiding the picked cabin keeps `picked` (C9) - **not a defect**: the box
names the pick in words (`Picked: … · J`) and the button adds exactly what it names; the
table shows no highlight because the cell is not rendered. Clearing the pick on a filter
change would lose a keyboard user's selection for a display choice. `grep -c
points_candidates` reads 1 because `LIVE_ONLY_FLAG` says the word - the probes check the KEY
on every leg (C5/C8), as the coder said to. Provenance-not-checksum (a hand-edited cash
amount stays deletable) is the plan's own D7/8 rule; B2 pins it (`cash_amount`,
`name_changed`, `description_changed`: DELETABLE) and the confirm quotes the description.

**Two observations, not findings**: (a) a UI-built file with a UTF-8 BOM prepended is
refused by the loader (T2 shows `trip-error`, so the page never offers it) but
`json.loads(bytes)` strips the BOM, so the raw rule says deletable and the API preflight
returns 200 - reachable by curl only; likewise a UI-built file whose leg date was hand-edited
to `not a date` (B6). The plan's "unloadable file is refused" means json-unparsable; these
are loader-unloadable. Harmless: both are builder-provenance files. (b) The prefilled cash
field keeps the typed form's grey placeholder `2400` under a note that says "type the cash
fare you found"; a dim number where the user is told nothing is known. Pre-existing
placeholder; worth a look.

## What I could not break

- **Path traversal on the two delete routes** (A1, 36 ids x 2 routes): `../`, `..%2f`,
  `%2e%2e`, backslashes, `%00`, `.json` suffix, trailing dot/space, case variants, a 121-char
  and a 4000-char id, `.`/`..`, a slash in every encoding, CRLF, a zero-width space, a
  full-width dot, a combining accent, `-`, `_` - every one 404 `not_found` on both routes, the
  tmp tree byte-for-byte and mtime-for-mtime unchanged.
- **Symlinks** (A2): absolute and relative links to a deletable file outside the dir, a link
  to `README.md`, a link to a directory (not listed at all) - R8 naming the LINK, 409 on
  both routes, targets intact. **Hard links** (A3): to Trip B -> R2; to a UI-built file
  outside -> deletable by design, one name removed, the other name and its bytes survive.
  A directory `x.json` and `trip_001_answer` -> 404 (A4).
- **Confirms** (A5-A8): A's confirm on B and B's on A -> `confirm_stale`, both consumed; a
  RUN confirm on delete and a delete confirm on `/run` and `/search/run` -> stale/refused;
  list/dict/bool/60 KB/digest-shaped ids -> `confirm_required`; the confirm in the query
  string or a header -> nothing; a confirm from a previous launch -> `confirm_required`; an
  edit that keeps provenance (cash amount) after the preflight -> stale (bytes, not
  provenance); the same bytes rewritten -> not stale; TTL exact boundary (300.000 s good,
  300.001 s stale).
- **Gates** (A9, both routes): no/foreign/`null`/scheme-wrong/port-wrong/octal/IPv6 Origin ->
  403 `forbidden_origin`; no/wrong/case-flipped token -> 403 `stale_page`; foreign or missing
  Host -> refused; GET/PUT/DELETE/PATCH/OPTIONS/HEAD -> 405, TRACE/PROPFIND -> the stdlib's
  501 (pre-existing, every route); `text/plain` and form bodies -> 400; 65 KB -> 413; a JSON
  list/string/null/number body -> 400 "must be one JSON object"; the request log is
  method-path-status only and carries neither key, token nor confirm id.
- **Races** (A10-A12): two threads, one confirm -> exactly one 200, the loser
  `confirm_required` or 404; delete during a real LIVE run against a stub with a blocking
  transport (same trip and another trip: both R5 on preflight and on delete) and after the
  run the delete succeeds and `GET /api/runs/{id}` still serves the stored run; the lock held
  artificially -> R5 in the banner from the page (D5). Create -> delete -> create of the same
  name: the writer accepts the name again and the second file is byte-identical (A13). One
  unlink, no `.tmp`, nothing else in the tree moves (A14).
- **Policy** (B1-B7): every committed fixture (7 + both `_answer` files -> 404) refused
  with R2 exactly as formatted, bytes and mtimes unchanged; 30 hand edits classified
  (points key empty/full/null/on the second leg, flag removed/replaced/not a list, cash
  source, extra cash source, source rewritten/null/int/list/upper-cased/leading space, the
  prefix kept with a suffix -> deletable, unparsable/list/string/empty/NUL -> R1); R2 quotes
  the source cut at 80 chars; a UI-built file committed in a tmp git repo is deletable and C2
  names git (D9); the description is the file's own and null when not a string; the listing
  has no `deletable` key; the last trip can be deleted and the listing is then `[]`.
- **Prefill** (C1-C4c): the state machine in every order (click, Esc, x, Trips tab and
  back, `#new-trip` and back, Show map, a new run, browser Back/Forward after Add as trip);
  keyboard only from the cell (Space/Enter) to the cash field (Esc keeps the pick and
  refocuses the cell; Tab to `search-add-trip`; Enter lands with focus in `nt-leg-1-cash`);
  both buttons build the identical form; a J cell gives `nt-cabin` J with the leg on
  `trip cabin (J)`; 11 hostile row dates injected in the browser (`<img onerror>`,
  `2027-13-45`, `2027-02-30`, U+202E, an ISO datetime, `15-01-2027`, 5,000 chars, empty,
  null, a number, a past date) plus a hostile program - the name is suggested only for
  `^\d{4}-\d{2}-\d{2}$` (so `sfo-mad-2027-13-45` IS suggested and the builder then refuses
  the date inline; the plan's guard is shape, not validity - fine), the date input holds the
  literal value, P3/P4 are text (`__pwned` false, no `img`), `nt-cabin` is always one of
  YWJF, Preview shows the builder's own `Leg 1 date` refusal and writes nothing. A hostile
  `source_code` in the cell testid (pre-existing) renders as text at every width.
- **Bodies and bytes** (C5, C6): the draft body is exactly
  `{name, cabin, legs[{origin, destination, date, cabin, cash}]}`, the create body that plus
  `draft_hash`; no `50000`, `32.36`, `from_search`, `run_id`, `Aeroplan`, `focus`, `seats`,
  `taxes` in any `/api/trips` POST; the written file has no `points_candidates` key on any
  leg and no award figure. Prefilled -> Write, delete from the page, typed by hand -> Write,
  and `python -m src.main --new-trip sfo-jfk-2027-03-05 --leg SFO:JFK:2027-03-05:2400
  --cabin Y` (argv -> parser -> dispatch -> `run_new_trip`, pinned day, tmp dir): three
  identical files; the two create bodies identical; the two echoes identical.
- **Refusals through the prefilled form** (C7, 21 cases): non-ISO/impossible/past/empty
  date, same origin and destination, an unknown code, a 4-letter code, a 2-letter code,
  cash `0`/`-1`/`abc`/`2,400`/`nan`/`inf`/`$2400`/empty, name with `/`, `../escape`, empty,
  121 chars - each the builder's wording inline under its field, no echo, no Write button,
  nothing written; `trip_b_europe` as the name -> Write refused with the writer's own
  `already exists … --force` + `Choose another name.` (D6). `1e9` and `395.999` accepted.
- **No points price** (C8): flags panel carries `LIVE_ONLY_FLAG`, the legs table says
  `none`, OFFLINE headline `0.00% (none)` with its qualifier.
- **Widths** (C10, 400/899/1180/1440): the box never widens the page, `drawer-to-trip` is
  visible under the sheet, the prefilled form fits at 400. **The map** with a picked row
  (C11, synthetic hubs): Show map clears the pick, draws the route, markers click.
- **Delete UI** (D1-D7): Trip A/B/C/001/002 disabled with R2 verbatim under it in the
  refusal form; a disabled click sends nothing; the button is `--warn-bg`/`--warn` and the
  only coral on T2 is Run; the dialog is `role=dialog aria-modal`, label, `Delete <file>?`,
  C1, C2, the description, Cancel focused, the go button warn-form; Esc, Cancel and a click
  on the scrim send nothing (request log: 3 preflights, 0 deletes); the delete from a page
  with a run RESULT open lands on `#trips` with `Deleted <file>` and the empty note, the row
  gone, no `aria-current`, its run chips gone, a hash to the id -> `trip-error` with R4, the
  panel gone once a trip renders, and a namesake written afterwards shows none of the
  deleted trip's chips; the last trip deletes to an empty list, `+ New trip` still works, a
  reload has no default and no stale panel; a hand edit after page load -> R3 in the banner;
  an edit / a corruption after the preflight -> R7, file intact; a hostile 3,000-char
  description renders as text in the dialog at 400 px.
- **Regression** (E1-E8): `git diff 4f879d4..d6be134` is empty for main/formatter/
  trip_builder/trip_loader/serialize/ui.server/ui.serialize/map.js/index.html/seats_client/
  optimizer/live_trip/regions/`data/`/`tests/fixtures`/the three older probe trees; the
  changed-file list is exactly the coder report's; no `innerHTML`/`.style.` beyond 2/`|| 0`/
  `?? 0`/changelog strings; 2,044 lines; every base testid survives and the nine new ones
  exist; every R/C/P string verbatim; the spend-confirm sentences byte-identical to the base;
  no ui-brief 2 marker word in the added lines; the coder's string list equals the set of
  double-quoted literals on added lines (so the J6 re-pin is a copy, checked); the CSS diff
  is exactly the eight listed rules, no rule changed or removed (the report says "four
  comment lines"; there are five); ui.md/README name the routes and `test_readme_local_ui`
  is green; the engine joins nothing from the URL, unlinks one path, digest binds
  `kind`+`trip_id`+sha256.

## Counts

| Suite | Command | Result | Claimed |
|---|---|---|---|
| full suite `-O` | `python3 -O -m pytest -q -p no:cacheprovider` | **3890 passed, 13 skipped** (226 s) | 3890/13 |
| full suite | `python3 -m pytest -q -p no:cacheprovider` | **3890 passed, 13 skipped** (229 s) | 3890/13 |
| ui-probes | `-O -p no:randomly` | **664 passed, 0 failed** (805 s) | 664/0 |
| ui-restyle-probes (before my re-pins) | `-O -p no:randomly` | **281 passed, 5 failed, 10 skipped** (284 s): H1, E5, J6, C5[search_ok], E3 | 281/5/10 |
| map-search-probes (before my re-pins) | `-O -p no:randomly` | **159 passed, 2 failed** (358 s): A6, F6 | 159/2 |
| ui-restyle-probes (after my re-pins) | `-O -p no:randomly` | **284 passed, 2 failed, 10 skipped** (290 s): C5[search_ok], E3 - both baseline | — |
| map-search-probes (after my re-pins) | `-O -p no:randomly` | **160 passed, 1 failed** (357 s): F6 - baseline | — |
| search-to-trip-probes (mine) | `-O -p no:randomly docs/test-reports/search-to-trip-probes` | **153 passed, 3 failed** (223 s) | — |

A7 (map-search) goes red at the commit that carries the three restyle re-pins and is
re-based to that commit's hash in the follow-up commit, exactly as in the map round.

### My suite, by id

RED (defects present): **A15** (F1), **D3b** (F2), **D8** (F3).
Recorded, green by construction: **A11** (the race, non-deterministic - prints the losers;
A15 pins it), **D5** (F4, prints the stale state), **C9** (filter keeps the pick, judged
not a defect), **C12** (note kept and hint on leg 1 after the prefilled leg is removed -
cosmetic, not filed).
GREEN: A1 (36) A2 A3 A4 A5 A6 A7 A8 A9 (2) A10 A12 A13 A14 · B1 B2 (30) B3 B4 B5 B6 B7 ·
C1 C2 C3 C4 (11) C4b C4c C5 C6 C7 (21) C7b C8 C10 (4) C11 · D1 (5) D1b D2 D3 D4 D6 D7 ·
E1-E8.

## Pin verdicts

| Probe | Verdict | What changed |
|---|---|---|
| restyle **H1** `server_and_csp_are_byte_identical_to_the_base` | **re-pinned** (plan 6 step 7 names it) | api.py/engine.py now pinned to d6be134; serialize/main/formatter still 386b2fc; server.py still 16f53b7; CSP still the base's. The behaviour behind the pin - server and CSP untouched - confirmed. |
| restyle **E5** `api_state_key_shape_is_byte_identical_to_the_base_commit` | **re-pinned** (the same pin as H1 under another name; the coder listed it) | api.py/engine.py to d6be134; serialize.py to 386b2fc; and the real pin added: `Engine.state()` and `calls_state()` are byte-identical to the base (extracted and compared). |
| restyle **J6** `app_js_diff_is_exactly_the_three_enumerated_edits` | **re-pinned** (plan 6 step 7) | `56742af..HEAD` 17 hunks; second allowlist = the coder report's string list, verified against the diff by my E5 first (equal sets). The `386b2fc..56742af` half untouched. |
| restyle **J8** | not touched | still green: it lists removed rules only; the additions are listed and pinned by my E6. |
| restyle **C5** `[search_ok]` | **left red** (baseline 2 -> 3) | judged above; a manager decision, not a pin. |
| restyle **E3** | left red | baseline, unrelated. |
| map-search **A6** `no_engine_cli_or_golden_diff_since_the_base` | **re-pinned** (plan 6 step 7) | api.py/engine.py to d6be134; the other eleven paths still the map base's. |
| map-search **A7** `the_parity_suite_and_goldens_are_untouched` | **re-based in the follow-up commit** | its `7a8310c..HEAD` diff of the restyle tree is no longer empty because of the three re-pins above (the tester's own edits, as before). |
| map-search **F6** | left red | baseline, unrelated. |
| ui-probes `test_B*` | nothing to re-pin | the route tables do not enumerate `/api/trips/` POST paths; 664/0 confirmed. |

Each refreshed pin's docstring names the plan section and says "a pin, not a regression".

## Re-test: ecab878

Coder fix round 1 (d2a4473 F1, 06a2f73 F2, 0a9b1f3 + 63d9499 F3, 9e6a845 F4, 394f51c the
cash placeholder, 7eb65c2 C5). Probes: `test_st_f_retest.py` (F1a-c, F2 x4, F3a-c, F4a-b,
F5). Each fix attacked independently; the diff read in full (`622d914..ecab878`: engine.py,
app.js 9 hunks, app.css +1 rule, one ui.md sentence, +1 test, `TESTIDS` +1).

| Finding | Status | What I did |
|---|---|---|
| **F1** raced second delete said R1 | **fixed** | F1a: the A15 interleaving now answers 404 R4 with the exact `No trip … in …` sentence. F1b: the file vanishing between `trip_path` and the preflight's read -> 404; between `trip_path` and the delete's digest read -> 404. F1c: a symlink whose target vanishes drops out of the listing (404 on both routes, the link itself untouched); a real file preflighted then swapped for a directory of the same name -> 404 and the directory and its contents survive; swapped for a dangling symlink -> 404, link survives; swapped for a symlink to a live deletable file -> 409 (stale/R8), target intact; a genuinely unreadable file still gets R1 verbatim. |
| **F2** `trip-deleted` widened the page | **fixed** | F2 at 360 and 400 px, 120-char name, in both panels (`nt-wrote` then `trip-deleted`, and the dialog in between): `scrollWidth <= clientWidth`, no element's right edge past the viewport. The 300-char case: the builder refuses a name over 120, so a 300-char unbroken token is put into the server's `Wrote …`/`Deleted …` line by a response patch in the browser - both panels break it. Shots `shots/f2-*.png`. |
| **F3** focus dropped after the dialog | **fixed** (with a judged gap) | F3a keyboard only: Tab to Delete, Enter (Cancel focused), Esc -> `trip-delete`; Enter on Cancel -> `trip-delete`; scrim click -> `trip-delete`; Tab, Enter on Go -> after the delete focus is on `h2 trip-list-heading` (`tabIndex -1`), Tab from there reaches the first trip row, Shift+Tab from the first row skips the heading (it is out of the Tab order). F3b spend dialog: Esc -> `run-go`, Cancel -> `run-go`. F3c: after a refused Go (stale) the banner shows and focus is on `<body>` - recorded below. |
| **F4** stale disabled state after a run elsewhere | **fixed for the trips view; a pre-existing sibling surfaced (F5)** | F4a: a run finishing while Trip B's detail is open re-renders it - `Run` reads `Run`, `run-busy` gone, Delete disabled by R2 only; on a deletable other trip Delete becomes enabled the moment the run returns; the run's own trip keeps its chip. F4b: see F5. |
| obs. (b) cash placeholder | **fixed** | F5 probe: `nt-leg-1-cash` has an empty placeholder on both the prefilled and the typed form; the other placeholders (SFO/LHR/YYYY-MM-DD) untouched; the P5 hint is still there. |
| **C5** | **demoted as recommended** | `drawer-to-trip` is `btn`; `search-add-trip` keeps the coral. Visible `.btn-primary` on the results screen: `search-run` + `search-add-trip` (the latter disabled-coral before a pick, as C5 counts it) with the drawer open or closed: 2, the baseline count. |

### F5 - Low (new; pre-existing line, surfaced by the F4 re-test) - A run finishing while the new-trip form is open throws the user to the run result and the typed input is gone

- **Repro** (`test_st_f_retest.py::test_F4b`, red): open trip X, Run, click `+ New trip`
  while it runs, type a name and part of a leg; the run returns.
- **Expected**: the coordinator's brief and the F4 fix's own comment: the form must not
  be wiped; the user stays on `#new-trip` with focus where it was.
- **Actual**: `sendRun`'s `if (S.tripId === id) { go("#trips/" + id + "/run/" + run.run_id); }`
  fires because the new-trip view keeps `S.tripId` (the run's trip is the selected one,
  the usual case): the hash becomes `#trips/<id>/run/<rid>`, the form is gone; `+ New trip`
  then starts a fresh `S.nt`, so the typed name is `''`. The F4 fix's `else if (S.view ===
  "trips")` guard is on the wrong branch to protect the form. Line present at 622d914:673
  and before this round.
- **Location**: `app.js` `sendRun`: guard the `go(...)` with `S.view === "trips"` too (a
  finished run on a trip whose page is not open lands as a chip, as it already does when
  another trip is open), or leave `S.nt` and only navigate when the form is untouched.

**Judgements.** (1) Spend-dialog Go not restoring focus: the coder's reasoning holds -
`run-go` is rebuilt disabled ("Running…") the moment the run starts, so a `focus()` cannot
take, and after the run the page navigates to the result. F3b records `BODY` during and
after. Acceptable for this round; the right place for focus after a run is the result's
headline or the run chip, which is a run-page decision, not a delete-round one. Same for
F3c (a refused Go leaves focus on `<body>` with the banner showing): the banner is
`aria-live`-less text at the top; a keyboard user has lost their place once. Low, out of
this round's scope, noted for the run page. (2) **C5's bar**: the coder rewrote ui.md
§4.7's token sentence to "one primary per surface" and moved `Add as trip` into the
`.btn-primary` list. I am **not** raising C5's bar to `<= 2`: the probe pins the design
system's own words at the restyle ("the ONE primary action"), and a coder's edit to the
design doc in a fix round is not a plan decision that invalidates a tester pin (the rule I
work under names the plan). C5[search_ok] stays a documented baseline red at 2, the same
count it had before this round; if the manager adopts "one primary per surface", C5 should
be re-pinned to count per surface in that round, with the sentence quoted. (3) The raced
`GET /api/trips/{id}` (F1b, recorded): when the file vanishes between the listing and the
loader's read, the detail answers the pre-existing 422 `cannot_load` with the OS sentence
(`FileNotFoundError: [Errno 2] …`), not 404. Not a delete route; not this round's; noted.

### Pins refreshed (by construction, each docstring says so)

search-to-trip **E2** (2,087 lines), **E3** (`"2400"` was the placeholder, never a
testid; `trip-list-heading` asserted), **E6** (+ the fix round's one CSS rule, nothing
removed), new **E5b** (the coder's "app.js diff since 622d914" string list equals the
diff's added literals - it does, and `"2400"` is the one removed literal). restyle **H1**,
**E5** (api.py/engine.py to ecab878, api.py also still d6be134's bytes; `state()` /
`calls_state()` still the base's), **H7** (`{"2400"}` is the whole difference), **J6** (22
hunks + `fix_allowed`). map-search **A6** (same as H1); **A7** re-based in the follow-up
commit.

### Counts at ecab878 + this commit

See the table appended below by the run.

## Final: 23eba49

Coder fix round 2: **a022cf4** (F5, one line in `sendRun`) and **9dea889** (the pin
cleanup, four probe files). Diff `0d1e2fb..23eba49` read in full: `src/ui/static/app.js`
one line plus a five-line comment, four probe files of mine, the coder report. No engine,
no server, no CSS, no HTML, no fixture, no golden.

**Verdict: F5 is fixed and I cannot break it. The pin cleanup weakened nothing that is a
defect.** One new Low observation (F6, below), recorded rather than filed as a blocker.
My own F4a flake is fixed and was my probe's race, not a product defect.

### F5 - fixed, attacked in seven arrangements (`test_st_g_f5.py`, new)

`if (S.tripId === id && S.view === "trips")`. Every case asserts the same four things: the
hash is still `#new-trip`, the typed values are still in the inputs, **`S.nt` itself
survived** - checked by leaving the form and returning BY HASH, never with `+ New trip`,
which resets `S.nt` by design and would hide the defect - and the finished run is still
reachable as a chip on its own trip (clicked; it opens its result). The run is the
`slow_run` scenario's four-second one and its completion is waited for on the response
itself (`expect_response`), not on a sleep.

| id | arrangement | result |
|---|---|---|
| **G1** | run on X, then `+ New trip` and type (name, cabin J, half-typed origin, two chars typed into destination) | green; hash `#new-trip`, all four values, focus still in `nt-leg-1-destination`, chip on X |
| **G2** | run on X, to the Search tab, then to `#new-trip` and type | green; and Search-and-back keeps the form |
| **G3** | a FULL leg typed first (name, trip cabin, SFO/LHR, date, leg cabin F, cash 3150), then the run started from X's page and the form re-entered by hash | green; all seven fields, still one leg, the form still works afterwards |
| **G4** | two runs in sequence with the form open, typing more between them | green; both chips on X, nothing lost either time |
| **G5** | a run that FAILS with a non-200 while the form is open - the engine's own 409 `busy` (the slow run sleeps first and takes the run slot after, so holding the lock from stdin makes THIS run a real refusal) | green on the form; **focus is dropped - F6 below** |
| **G6** | a run REFUSED by the wallet (the probe server's tmp wallet clobbered mid-run): exit 2 WALLET ERROR, which comes back as a **200**, the same branch a good run takes | green; the guard, not the exit code, is what keeps the form; chip reads `exit 2` |
| **G7** | the normal case, unchanged: on the trips view showing that trip | green; still navigates straight to `#trips/<id>/run/<rid>`, chip `aria-pressed=true`; another trip's page still only re-renders (F4) with Delete re-enabled; the search view keeps its picked cell |

Counter-check that the suite is measuring the fix and not itself: with `0d1e2fb`'s
`app.js` restored in a scratch worktree, **six of the seven go red** (G1, G2, G3, G4, G6,
G7 - the pre-fix line also threw the user off the SEARCH view, which is why G7's third
part fails there too). G5 passes either way, correctly: the non-200 branch never
navigated.

### F6 - Low (new, recorded in G5) - a REFUSED run rebuilds the open new-trip form and drops focus

- **Repro** (`test_st_g_f5.py::test_G5`, asserted): start a run on X, open `+ New trip`,
  type; the run comes back non-200 (409 `busy`).
- **Actual**: the values survive (`S.nt` is the source of truth and the inputs write to it
  on `input`), the banner shows the refusal - but `document.activeElement` is `<body>`.
  `sendRun`'s non-200 branch calls `renderTrips()` unconditionally, and on the new-trip
  view that rebuilds the whole form; only the prefilled form's one-shot `nt.focus === "cash"`
  ever refocuses anything.
- **Expected**: the caret stays where the user left it, as it does on the 200 path (G1,
  G6 assert `nt-leg-1-destination` is still focused).
- **Location**: `app.js` `sendRun`, else branch. Same family as F3 and the same fix shape
  (`closeDrawer`'s `cameFrom`): remember the focused testid before the re-render and
  restore it, or skip the re-render when `S.view === "new-trip"` as the 200 branch now does.
- Text is never lost, so this is cosmetic-plus-keyboard, not data loss. Low.

### F4a - my probe's race, fixed in my file (not a product defect)

`test_st_f_retest.py::test_F4a` failed about 3 runs in 10 at `assert
delete_state(pg)["disabled"] is True`, with `None` - the button absent. The coder measured
3/10 red with AND without the F5 fix and left it to me. It is mine, and here is the
mechanism, which I traced with a `MutationObserver` in the page:

`hashchange` is delivered asynchronously. `pg.click(q("trip-row-other-trip"))` returns
while the page is still showing the PREVIOUS trip's detail, `trip-delete` and all - and
`tab-trips` goes to `"#trips/" + S.tripId`, so the previous page is a trip detail, not the
empty list. The bare `wait_for_selector(q("trip-delete"))` matched that stale button and
returned immediately; a few milliseconds later `onHash` set `S.trip = null`, rendered
`Loading…`, and the next read found nothing. The product was correct throughout.

Fixed with `wait_delete_settled(pg, trip_name)` in `conftest.py`, used at both waits in
F4a: the detail on screen must be the one that was asked for (its `h1` is that trip's
name), `trip-delete` must exist, and its `disabled` must be identical across **three
consecutive animation frames**. One trap worth recording for the next round: the obvious
`wait_for_function` returning a Promise does NOT work - `wait_for_function` reads the
returned Promise object as a truthy value and returns on the first poll, which is no
better than the bare selector (my first attempt did exactly that and made the flake
worse, 9/10 red). The settle is therefore a synchronous frame counter on `window`.

**Pass rate after the fix: 10/10** (`-k F4a`, isolated, ten separate pytest invocations),
against 1/10 for the promise version and 7/10 for the original. Green in file context as
well, in the full-suite run below. Nothing about what F4a asserts changed.

### Pin-cleanup audit (9dea889)

Method: a detached scratch worktree at 23eba49, one change at a time, committed there when
the assertion reads `git diff <base>..HEAD` (a working-tree edit is invisible to those),
then the converted probe run against it and the worktree reset. Nothing was committed to
the branch and no committed fixture was touched.

| Probe | Pin dropped | A change the OLD pin caught | Does the NEW assertion catch it? |
|---|---|---|---|
| st **E2** | `len(JS.splitlines()) == 2087` | 100 blank lines appended to app.js | **No - and it is not a defect.** A line count is not a rule. The rules E2 is named for all still bite: `innerHTML` added -> RED; a third `.style.` -> RED (`assert 3 == 2`); `url(...)` in app.css -> RED; `style=` on `<body>` -> RED. |
| st **E3** | `old - new == {"2400"}` (regex read any quoted lowercase token as a testid) | a `data-testid` dropped | **Yes.** `"trip-delete"` removed from its `btn(...)` -> RED; `"trip-delete-reason"` removed from its `tid(...)` -> RED; `"run-go"` removed -> RED. The old regex's 226 "testids" in the base were 150 CSS class names and DOM tokens plus 76 real ones; the new extractor finds 80 and every one is real. Nothing genuine is missing from it: the only app.js literals the old regex had and the new one does not are `$("...")` element ids (`page`, `scrim`, `trip-main`, `view-trips`, ...), which are **not** testids - they carry no `data-testid` in index.html - and whose loss breaks behaviour the D probes assert. |
| st **E5** | hunk count (14) on a frozen range + "the coder report's bullet list equals the diff" | a bullet missing from a document, or git regrouping a historical diff | **No, and nothing is lost.** Both halves were bookkeeping on a report, over a commit range that can never change again. The product-side rule moved to E5b. |
| st **E5b** | a literal string-set for one frozen diff | a user-facing sentence added that no plan writes | **Yes.** An invented note (`"Your points balance looks low…"`) -> RED with the sentence named, working-tree or committed. |
| restyle **H1** | api.py/engine.py bytes pinned to the last head | server.py or the CSP changed | **Yes.** `default-src 'none'` -> `'self'` in the CSP (working tree) -> RED; any other server.py edit, committed -> RED; `src/main.py` touched -> RED. The api.py/engine.py half is gone - see the engine row below. |
| restyle **E5** | the same two byte pins | `/api/state`'s key shape moved | **Yes.** A key added to `Engine.state()` -> RED (`AssertionError: state`); serialize.py and server.py pins both still RED on a change. |
| restyle **H7** | `old - new == {"2400"}` | a `data-testid` dropped | **Yes**, on both halves: `"run-go"` removed from app.js -> RED; `data-testid="banner-error"` removed from index.html -> RED. (A testid this round INVENTED, e.g. `trip-delete`, is not in the restyle base and never was in this probe's scope - unchanged, and st E3 covers it.) |
| restyle **J6** | both app.js hunk counts (6 and 22) | git regrouping hunks after a line moved | **Mostly yes, and it is now stronger than it was.** New sentence added on an added line, committed -> RED; `innerHTML` -> RED; a base testid lost -> RED (the old J6 did not check testids at all). **One thing it no longer catches:** a literal that already exists elsewhere in app.js re-emitted on an added line - I re-used the base's own `"Running…"` as a new note and J6 stayed green (the old J6 would have gone red). See the judgement below. |
| map **A6** | api.py/engine.py byte pins | the CLI, the data or a golden moved | **Yes.** A golden edited -> RED; a golden deleted -> RED; `src/main.py` touched -> RED. |
| map **A7** | "the other probe trees are unchanged since `<commit>`" | one of MY probe files edited | **No - and that is the point.** It pinned the tester's own future commits; an edit under `ui-probes` now leaves it green, as it should. The goldens half, which is the real rule, still bites: a golden edited -> RED; a golden deleted -> RED; the `14 x G*.txt` count still asserted. |

**The api.py/engine.py byte pins (H1, E5, A6): no protection lost.** I put a real defect in
`engine.py` - one word added to the R2 refusal sentence (`"Nothing was deleted yet."`),
committed - and confirmed the three converted pins stay green while **`test_st_b_delete_policy.py`
goes red six times** (`B2[source_null|source_int|source_list|source_case|source_leading_space]`
and the fixture case), naming the sentence. That is the right place for it to be caught: the
behaviour probes own the sentence, the byte pins only owned the bytes.

**The one genuine loss, and my judgement on it.** J6's subtraction of the left-hand side
means an existing literal re-emitted on a new line is no longer read as new wording. The
old pin caught that; the new one does not. I do not think it should be restored, and I have
not restored it: the literal in question is by construction a sentence the plans already
write, so the rule the pin stands for - *the page may not start saying something no plan
wrote* - is intact; what changes is only WHERE a planned sentence appears, which is a
placement bug and is caught, if it matters at all, by the behaviour probes that assert what
each panel says. Restoring the old form would re-introduce exactly the false positive Tsuki
authorised removing: every re-indented or moved line re-triggers it. I also checked whether
E5b could be strengthened to the whole file rather than to added literals only - it cannot
usefully: 67 of app.js's 148 sentence-shaped literals are earlier rounds' wording whose
plans are not in the two files E5b reads, so the strict form would be a wall of noise, not a
rule. Recorded here so the next round knows the gap exists.

Nothing else moved: no probe lost a security, refusal-sentence, marker, layout or
data-integrity assertion, and `tests/` was not edited by the coder at all (verified:
`git diff 0d1e2fb..23eba49 --stat` lists four probe files, one report and `app.js`).

### Probe changes of mine in this commit

- `conftest.py`: `wait_delete_settled(pg, trip_name)` (above). Nothing else in the harness
  changed; no existing helper's behaviour moved.
- `test_st_f_retest.py`: F4a's two waits, with the race written into the docstring; F4b's
  docstring rewritten - it described F5 as present and F5 is fixed. Its assertions are
  untouched and it is the original repro.
- `test_st_g_f5.py`: new, seven probes (above).

### Counts at 23eba49

| Suite | Command | Result |
|---|---|---|
| full suite `-O` | `python3 -O -m pytest -q -p no:cacheprovider` | **3892 passed, 13 skipped** (178 s) |
| full suite | `python3 -m pytest -q -p no:cacheprovider` | **3892 passed, 13 skipped** (177 s) |
| ui-probes | `-O -p no:randomly` | **664 passed, 0 failed** (622 s) |
| ui-restyle-probes | `-O -p no:randomly` | **284 passed, 2 failed, 10 skipped** (246 s): `C5[search_ok]`, `E3` - both documented baseline reds |
| map-search-probes | `-O -p no:randomly` | **160 passed, 1 failed** (306 s): `F6` - the documented design limit |
| search-to-trip-probes (mine) | `-O -p no:randomly docs/test-reports/search-to-trip-probes` | **176 passed, 0 failed** (323 s) - no red; the three reds of the first round (A15/F1, D3b/F2, D8/F3) stayed fixed, F4b/F5 is green, G1-G7 new |
| `test_F4a` alone, ten invocations | `-O -p no:randomly -k F4a` | **10 / 10 green** |

Both full-suite counts are the coder's (3892/13). The four probe-tree reds are the same
four as at `0d1e2fb` minus the ones the cleanup retired: `C5[search_ok]` and restyle `E3`
(baseline, unrelated) and map `F6` (baseline). **The pin refresh round is gone:** H1, E5,
J6, A6 and A7 are green at a head they were never re-pinned to, which is what the
conversion was for.

### Still open, unchanged from the re-test

C5's bar (a manager decision, not a defect I can pin), the spend dialog's focus after Go
and after a refused Go (F3b/F3c, out of the delete round's scope - and F6 above is the same
family, so the run page's focus story is now three notes long), and the raced
`GET /api/trips/{id}` answering 422 `cannot_load` rather than 404 (not a delete route).

## Final: 3232e95

Coder fix round 3, one commit: **3232e95** - F6. `sendRun`'s non-200 branch now carries the
same guard as the 200 branch (`if (S.view !== "new-trip") { renderTrips(); }`) plus a
four-line comment. One line of product code; nothing else in `src/` moved.

**F6: fixed.** A refused run no longer rebuilds the open new-trip form, so the caret stays
where the user left it. The refusal is still said - `showBanner` runs before the guard and
the banner lives in the shell, not in the re-rendered view - and nothing on the new-trip
view follows `S.busy` (the only three readers are `trip-delete`, `run-go` and `run-busy`,
all on the trip detail), so there is nothing the skipped re-render was needed for.

`test_st_g_f5.py::test_G5` now ASSERTS the fixed behaviour instead of recording the old
one: focus is still in `nt-leg-1-destination` after the 409, the banner is visible and
says the engine's own busy sentence verbatim, and everything G5 already checked (hash,
the four typed values, `S.nt` after leaving and re-entering by hash, no chip because
nothing ran) is unchanged.

Two new probes attack the fix:

| id | attack | result |
|---|---|---|
| **G8** | a real refusal with the caret in **each of the seven fields** in turn - `nt-name`, `nt-cabin`, `nt-leg-1-origin`, `-destination`, `-date`, `-cabin`, `-cash`, the two `<select>`s included - against a form with a FULL leg typed | green: seven refusals, the caret stays in the field every time, all seven values re-checked after each one, the banner right each time, and nothing ever ran (no chip) |
| **G9** | two refusals in a row with more typed in between; then, with the banner on screen, typing and changing a select in place | green: caret and values kept both times; and with the banner up the form still takes input without moving the caret or losing the banner; Preview afterwards still answers with the builder's own inline refusals for the half-typed leg, so the form is live, not frozen |

One behaviour G9 pins in passing, which is correct and worth writing down: `onHash` calls
`hideBanner()` on every hash change, so the banner from refusal 1 is cleared on the way
back to the trip page to start refusal 2. Every route into the new-trip form is a hash
change (`+ New trip` calls `go("#new-trip")`, and `go()` runs `onHash` even when the hash
is unchanged), so a refusal always lands on a hidden banner and raises it itself. "A
refusal arriving while a banner is already showing" is therefore not reachable in this
app; the closest real thing - the user typing into the form while the refusal's banner is
up - is what G9 exercises instead.

Counter-check that the three probes measure the fix: with `3ffea9b`'s `app.js` restored in
a detached scratch worktree, **G5, G8 and G9 all go red** and the other six stay green.

No new finding. F6 was the last open item of this round's own findings; what remains open
is unchanged from the section above (C5's bar - a manager call; the spend dialog's focus
after Go and after a refused Go, F3b/F3c, a run-page question; and the raced
`GET /api/trips/{id}` answering 422 `cannot_load` rather than 404, not a delete route).

### Counts at 3232e95

| Suite | Command | Result |
|---|---|---|
| full suite `-O` | `python3 -O -m pytest -q -p no:cacheprovider` | **3892 passed, 13 skipped** (179 s) |
| search-to-trip-probes (mine) | `-O -p no:randomly docs/test-reports/search-to-trip-probes` | **178 passed, 0 failed** (370 s) - G8 and G9 are the two new ones |

The other three trees were not re-run at this head and do not need to be: the commit
touches `app.js` only, inside `sendRun`'s error branch. Their counts at 23eba49 stand -
ui-probes 664/0, ui-restyle 284/2/10 (`C5[search_ok]`, `E3` - baseline), map-search 160/1
(`F6` the map probe, unrelated to this round's F6 - baseline).
