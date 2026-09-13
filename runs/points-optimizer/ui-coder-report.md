# Coder report: local web UI over the engine (feature 2 of 3)

Branch `feature/ui`, worktree `/home/claude/points-optimizer-ui`. Base 3d8b99d (the plan
and the design mockup), which sits on 3c104b3. Plan: `docs/plans/ui.md`, Addendum included.

## Final counts

| Run | Result |
|---|---|
| `.venv/bin/python -m pytest -q -p no:cacheprovider` | **1954 passed, 13 skipped** (baseline 1603 / 13) |
| same under `-O` | **1954 passed, 13 skipped**, 1 warning (pytest's own "-O ignores asserts outside test modules"; also there at baseline) |

351 new tests: goldens and seam (`test_cli_output_unchanged`, `test_main_dispatch_sink`),
builders (`test_formatter_builders`), F-1/F-3 (`test_f1_f3_cli_changes`), JSON contract
(`test_ui_serialize`), API (`test_ui_api`), security (`test_ui_security`), static page rules
(`test_ui_static_rules`). The sandbox runs pytest 9.0.3. Nothing in the new tests needs
anything newer than 7.4.

## Probe red sets, by test id

| Suite | Result |
|---|---|
| v5-probes | **same** 19 red |
| adversarial-probes | **same** 40 red |
| known-failures-probes | **same** 0 red |
| operating-airline-probes | the 5 R5-1/R5-2 reds are unchanged, **plus 1 new red**: `test_oa_r5_retest.py::test_the_rewording_changed_only_string_literals[src/main.py]` |

That new red cannot be avoided. The probe compares the AST of `src/main.py` with round 4's and
passes only if every difference is inside a string literal. Plan step 2 (D1) requires changing
the structure of `main.py`: `score_fixture` / `print_fixture_report` / `dispatch` / sinks. Any
version of that step turns this probe red. The alternative was to keep `main.py`
byte-identical and put a second copy of the dispatch rules somewhere else, which is exactly the
drift D1 exists to prevent. The behaviour this probe was protecting (feature 1's reword changed
text only) is now covered by the byte-identical goldens (`tests/fixtures/cli_golden/`). The
probe's sibling checks still pass: `[src/snapshot_replay.py]` and
`test_help_is_round_4_help_with_exactly_the_listed_rewordings_and_nothing_dropped`. **The
manager needs to accept this, or tell me to take a different route.**

A second probe, `test_oa_h_guards_docs.py::test_the_existing_contract_tests_are_untouched`,
went red for one commit and is green again. My first cut of step 2 edited
`tests/test_exit_codes_are_documented.py`. Commit 892d1b6 restores it byte-for-byte and makes
`main()` the dispatcher again, so the contract test finds every exit code in the same four
functions it always read. **No existing test file has been modified** since a17497d.

## Commits (oldest first)

| Commit | Step |
|---|---|
| bdf2af4 | step 1: CLI goldens, recorded before any src change |
| 971ffb3 | step 2: main.py split into score/print + dispatch with sinks |
| 7e29964 | step 3: server skeleton, gates and launch |
| 892d1b6 | step 2 (fix): leave the exit-code contract test untouched |
| df0110c | step 5: formatter builders, search_award_cash, parser_label_text |
| f8c2909 | step 4: first end to end, OFFLINE trip |
| 42ff882 | step 6: F-1 and F-3, the only intended CLI output changes |
| 21dc161 | step 7: full trip result and leg drawer, with the static page rules |
| b53bfeb | step 8: LIVE and REPLAY with server-side confirms |
| b5485b4 | step 9: single-route search |
| c964d49 | step 10: new-trip form and per-leg cabin |
| 7e2137d | step 11: wallet panel, session wallet -> argv |
| bae0b5f | step 12: cache-aware LIVE preflight |
| e60ef4b | step 13: README "Local UI" section and polish |

## Steps completed, and how each was verified

1. **Goldens.** G1-G13 run in-process under conftest's harness, recorded at 3d8b99d. The
   normalizer masks nothing in G1/G2/G3/G7/G8/G12, and masks the same token count in two runs
   of G4/G6/G13. Verified: 36 tests.
2. **Split + dispatch + sinks.** The goldens stayed identical. The sink receives exactly one
   FixtureRun/SearchRun/RunRefusal per scenario, with the returned exit code. A console spy
   shows `score_fixture` prints no table, live banner or headline. Verified: 22 tests.
3. **Server.** Every row of plan 4.4 is tested against a real server on an ephemeral port with
   `http.client`. The launch command prints the URL line. A busy port exits 1 and never moves
   to another port. Verified: 76 tests.
4. **OFFLINE end to end.** Trip B offline through the API returns `2.04% - 11.03%` with
   qualifier `(badge)` and exit 0, and **the transcript equals golden G1 byte for byte**. In the
   browser, the headline element reads `2.04% – 11.03%(badge)`.
5. **Builders.** `leg_table_cells` (12 cells, each with a `kind` taken from engine fields),
   `verdict_label/kind`, `trip_headline`, `trip_funding_banner`, `trip_totals_rows`,
   `trip_notes`, `residue_rows`, `leg_warning_lines`, `unknown_cash_line`, and topic-tagged
   `leg_detail_lines` / `live_detail_lines` / `alternatives_lines` (the per-leg print bodies run
   unchanged against a recording sink). `search_award_cash` is now called by `optimize()`.
   `MetalLookup.parser_label_text` added. Goldens identical after each one.
6. **F-1 and F-3.** Goldens G1/G3/G5/G7/G8 regenerated. The old versions are kept in
   `tests/fixtures/cli_golden/pre_f1_f3/`, and a test asserts every changed line is an F-1 cell
   or the F-3 sentence (a table border may change only in column width).
7. **Result + drawer.** JSON-contract tests pass for G1-G8. Every data-testid in the inventory
   is present (static test).
8. **LIVE/REPLAY.** No confirm means 409 and zero transport calls. `calls.this_run` equals the
   stub's count (4 search + 1 trips). A confirm can be used once. Changing the fixture bytes or
   the options after confirming makes it stale. A second concurrent run gets 409. Each run
   starts with an empty in-process cache. The stated maximum holds against a 3-page
   paginating stub. REPLAY of the live run's manifest returns `(snapshot mh_…)`. The LIVE
   transcript equals golden G4 except the redacted key line and the tmp paths. The fake key and
   its mask appear in no body and no log line.
9. **Search.** G9, G10 and G11 states are tested. A typo is refused in the builder's own words
   with zero transport calls. Every cell's taxes come from `search_award_cash`, including an
   UNKNOWN KrisFlyer cell that carries no number.
10. **New trip.** `test_trip_builder.py` passes, including the interactive == flags byte check.
    A fixture built through the CLI flags path is byte-identical to one recorded before the
    change. A mixed-cabin fixture loads with each leg's own cabin. A trip written in the UI
    runs OFFLINE with the LIVE_ONLY flag and `never priced` shown.
11. **Wallet.** With no wallet, offline, LIVE and search each return the CLI's exit-2 refusal
    and spend nothing. An edited wallet shows `entered in this session (not saved)`, and
    wallet.json's bytes and mtime are unchanged.
12. **Cache-aware preflight.** The predicted count equals the number of legs a second stubbed
    run reports as served from cache (4 of 4). It is 0 with refresh.
13. **README + polish.** The README verdict and exit tables are untouched, and the
    no-changelog scan over the static files passes. Browser checks with headless Chromium
    (a scratch script, not a repo test), each at 1440px and 400px, for OFFLINE, LIVE and
    network-down runs. Results: every verdict chip equals `cells.verdict`; no `$0.00` in an
    unknown-kind cell; the headline always carries its qualifier; every `cli_lines` entry
    appears in the drawer; the UNVERIFIED tag appears exactly where `metal.unverified` says;
    no "not a partner" on an API-failed or never-priced leg; no page-level horizontal scroll at
    400px; the drawer is a full sheet on a phone. No page errors apart from Google Fonts,
    which the sandbox blocks.

## Deviations from the plan

1. **The golden harness pins "today" and the fetch clock instead of masking them.** The FX
   banner prints `(N days old)`, and from 2026-10-09 it adds STALE RATE lines, so masking alone
   could not keep the goldens valid on Tsuki's Mac. `config.date`, `response_cache._utcnow` and
   `seats_client.datetime.now` are pinned during golden runs, and paths are relative (each
   scenario runs in its own work directory). The normalizer is kept as a narrow safety net:
   instants after "fetched"/"captured", snapshot filename stamps, "N minutes ago". The plan
   listed G1/G2/G3/G12 as having nothing volatile in them; G1 actually has the day count, which
   pinning makes stable.
2. **G8 (exit 4) is synthetic.** The trip-level balance ceiling demotes any leg the balance
   cannot fund, so no real fixture and wallet reach exit 4. G8 forces the funding report into
   its overdrawn state to pin the CLI's exit-4 rendering, and says so in its header.
3. **F-1 wording is shorter than the plan's.** The path cell reads `no live data` / `never
   priced`, not `… - no claim`. With the longer text, the 190-column table on a `--new-trip`
   trip (G7) overflowed and rich truncated the cash cell to `$2,400.…`. A truncated dollar
   figure seemed worse than two fewer words. The verdict cell keeps the plan's
   `PAY CASH (never priced)`. Nothing in either cell claims anything about partnerships.
4. **F-3 also fixes the cash branch.** "Cash is cheaper: $C vs $P on points" had the same
   pre-APD figure. Both sentences are restated from the same helper. Only B4 changes in the
   goldens.
5. **Step order and one extra commit.** Step 5 (builders) was committed before step 4, because
   the step-4 serializer renders the builders. Step 2 has a follow-up fix commit (see the
   probes section).
6. **app.js was written whole in step 4.** The search, new-trip and wallet views became usable
   as their endpoints arrived in steps 9-11. Step 7 then added the static rules and later steps
   refined the page.
7. **The UI always passes `--show-alternatives`.** It adds only display lines, the drawer shows
   the alternatives anyway, and the transcript then matches what the CLI prints with that flag.
   LIVE runs also carry an explicit `--live` (a no-op) so the equivalent command states the
   mode.
8. **Small UI decisions the plan left open.**
   * The `What` cell shows the description's first part untruncated, plus the rest underneath.
   * The `Cash as pts` cell of a leg with UNKNOWN cash uses the UNKNOWN chip. The CLI prints
     `-` there.
   * A `floor` score is drawn as `≥ $X`. The CLI prints `>= $X`.
   * Surcharge-source `unknown` is shown as the CLI's word, not as a chip.
   * The drawer adds a "Notes, flags and UNVERIFIED claims" section (the leg's own CLI lines).
   * The search taxes column shows the CLI's money strings. The page never formats a dollar
     figure itself.
9. **`exit_label` wording.** For exit 1 it uses 4.6's "ERROR — NOTHING SCORED". The chip is
   built from it, so it reads `exit 1 · ERROR — NOTHING SCORED`, not 4.7's shorter
   `exit 1 · NOTHING SCORED`.

## How to run it on his Mac

```bash
cd ~/Downloads/points-optimizer-git
git fetch <bundle> feature/ui && git checkout feature/ui     # or however the bundle is applied
.venv/bin/python -m pytest -q -p no:cacheprovider            # expect 1954 passed, 13 skipped
.venv/bin/python -m src.ui                                    # opens http://127.0.0.1:8777/
.venv/bin/python -m src.ui --wallet wallet.json --no-open     # explicit wallet, no browser tab
```

Run it from the repo root. `SEATS_AERO_KEY` in `~/.zshrc` is picked up exactly as the CLI picks
it up; the top bar reads `key: environment`. Every LIVE run and every search asks for
confirmation first and states its maximum call count. REPLAY becomes available after the first
LIVE run writes `tests/fixtures/seats_aero/live_trip_b/MANIFEST.md`.

## Known gaps

* **Nothing has run against the real Seats.aero.** Every LIVE path in this report is the
  synthetic stub. Real pagination, 429s and slow answers are untested. The page is synchronous
  with an elapsed timer and has no cancel.
* **A run in flight cannot be cancelled.** A second run is refused with 409.
* **The REPLAY manifest id is an index into the list.** If files appear between loading the
  page and pressing Run, the index can point at a different manifest. The run's equivalent
  command names the manifest actually used.
* **`cache_answerable` counts disk-cache hits for searches only.** Trips lookups served from
  cache are not predicted.
* **The drawer's "The math" section** shows the CLI's cells and lines. It does not recompute
  anything.
* **The `test_ui_serialize` fixture is module-scoped,** so it runs its scenarios before
  conftest's function-scoped network guard exists. Every scenario that needs a transport patches
  it, and `run_scenario` chdirs into tmp and sets relative cache and snapshot directories. But
  if a stub were mis-routed there, it would not be caught by the `requests` guard.
* **Hostile-data rendering (7.5)** is guaranteed by construction (textContent only; the static
  test bans HTML-string APIs). I did not run a 5,000-character or U+202E layout test.
* **Google Fonts** could not be loaded in the sandbox, so the screenshots use the fallback
  fonts. On his Mac they will load unless he is offline.

## Out-of-scope observations

1. **The CLI silently drops the surcharge confidence.** `print_leg_detail` prints
   `Surcharge: $0.00 [modeled] via …` unescaped. Rich reads `[modeled]` as a markup tag and
   deletes it, so the terminal shows `Surcharge: $0.00  via …` (two spaces). This is the same
   class of bug as the ReplayRefused escape fix. Not changed. The UI shows the terminal's text,
   so it drops the word too. Its `surcharge.confidence` field carries the value.
2. **`validate_iata`'s wording ends "No file has been written."** In the search view that
   sentence is irrelevant but true. It is shown verbatim.
3. **Trip B LIVE, network down: B4's UK APD line says the cabin is "'(none recorded)'".** Trip B
   legs have no leg-level cabin and no live award is chosen. Pre-existing and correct-as-worded.
4. **`trip_001` / `trip_002`** (v0 single-route fixtures) show in the trip list as
   "0 legs · 0 flights". They load, so they are listed, not hidden.

## The real UI next to the mockup

Screenshots (PNG) are in `/tmp/claude-0/-home-claude/9657234d-f462-5460-a2b8-6817bcaf3c16/scratchpad/ui-coder/`:
`final-trips-offline.png`, `final-trips-live.png`, `final-search.png`,
`final-phone-400.png`, `final-phone-400-drawer.png`. For comparison: `mockup-trips.png` and
`mockup-search.png` (the design mockup rendered at the same width).

* **Trips, OFFLINE Trip B with B3 open** (`final-trips-offline.png` vs `mockup-trips.png`). The
  layout, tokens, headline block, legs table and docked 440px drawer match the mockup closely.
  The headline reads `2.04% – 11.03% (badge)` with `CARRIER SURCHARGE UNKNOWN ON B3` under it,
  the low/high rows, and the provenance chip `BADGE · 0 of 4 legs live` above the range caveat.
  The main difference is content: the real page shows Trip B's "Data problems flagged" block
  (5 fixture flags) and the fixture's full descriptions. It also renders
  the CLI's words where the mockup had invented tidier ones. B1's What cell reads `MRY→MAD`
  because the fixture's description says so. The drawer's score-points cell is the CLI's
  `? (win if surch < $36.00)` where the mockup had an UNKNOWN chip. B4's verdict sentence is
  the F-3 one the mockup anticipated. The trip list also shows `trip_001`/`trip_002`.
* **Trips, LIVE with the synthetic stub, B4 open** (`final-trips-live.png`). Headline
  `0.00% (live)`, `LIVE · 4 of 4 legs live`, the `Legs carrying UK APD that is STATED but NOT
  ADDED … 1 (B4)` row, calls `5 this run (4 search + 1 trips)`. The B4 drawer shows
  `PAY CASH`, `≥ $1,209.30` vs `$482.00`, the known-taxes sentence ($609.30, GBP 450.00), the
  UNKNOWN surcharge and the "does not matter here" line, and the operating-airline section with
  the UNVERIFIED tag on `VS by flight number (VS19)`. The mockup's version of the same run was a
  simplified example. The real one is longer, because it is the CLI's full text.
* **Search, SFO-MAD with the Aeroplan cell open** (`final-search.png` vs `mockup-search.png`).
  This matches the mockup almost one to one: `50,000 · 9 seats`, `$32.36 total $532.36`,
  `FUNDABLE #1` in the neutral chip, `no space` in W/J/F. The drawer shows taxes
  `$32.36 (CAD 44.60) confirm before trusting`, the funding path and rank, `$532.36`, the NOT
  LOOKED UP operating-airline line with the possible carriers, provenance, and "Score against a
  fare →". There is no synthetic KrisFlyer row; that UNKNOWN state is covered by an API test
  instead.
* **Phone, 400px** (`final-phone-400*.png`). The top bar wraps to two rows, the legs table
  scrolls inside its frame, the page itself does not scroll sideways, and the drawer is a
  full-screen sheet that Esc closes.
