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
