# Test report: the local web UI (feature 2 of 3) — tester round 1

Branch `feature/ui`, worktree `/home/claude/points-optimizer-ui`, build
`bdf2af4..b14172b` on top of `3d8b99d` (plan + mockup). Plan `docs/plans/ui.md`
(Addendum included), coder report `runs/points-optimizer/ui-coder-report.md`.

Everything below was executed. Probes live in `docs/test-reports/ui-probes/`
(outside `testpaths`): **RED = the defect is still there, GREEN = the attack
held up**. Run them with

```bash
.venv/bin/python -m pytest -q -p no:cacheprovider docs/test-reports/ui-probes
```

**348 probes: 9 red, 339 green.** Screenshots are in `ui-probes/shots/`.

## What I ran

| Check | Result |
|---|---|
| `.venv/bin/python -m pytest -q -p no:cacheprovider` | **1954 passed, 13 skipped** (as claimed) |
| the same under `-O` | **1954 passed, 13 skipped**, 1 warning (pytest's own) |
| `docs/test-reports/v5-probes` | **19 red — same set by id** as the baseline |
| `docs/test-reports/adversarial-probes` | **40 red — same set by id** |
| `docs/test-reports/known-failures-probes` | **0 red — same** |
| `docs/test-reports/operating-airline-probes` | **5 red** (R5-1/R5-2, the two open Lows) after the re-scope below — the coder's 6th red is gone |
| working tree after the whole run | clean apart from this report and the probe files |

No network call was made to any real host. Every probe process refuses
`socket.connect` outright and records the attempt; Chromium is launched with no
proxy, with every name but `127.0.0.1` unresolvable, and with a route
interceptor that aborts and records anything that is not the probe server. The
only entry that ever appears is the page's Google Fonts stylesheet, which never
loads (`ui-probes/conftest.py`, `ui_probe_server.py`, `drive.js`).

## Ruling: `test_oa_r5_retest.py::test_the_rewording_changed_only_string_literals[src/main.py]`

**The restructure is legitimate; the probe was over-scoped. I re-scoped it and
it is green.**

The probe pinned the *whole* AST of `src/main.py` (string literals blanked) to
round 4, so that round 4's reword could be shown to be text-only. Plan decision
D1 deliberately restructures `main.py`: `main()`'s body becomes
`dispatch(args, console, sink)`, and `run_fixture`/`run_search` split into a
scoring half and a printing half so the UI runs the CLI's own code instead of a
second copy of the dispatch rules. I checked what actually moved:

* **no round-4 definition was deleted**;
* 16 of the 19 round-4 top-level definitions are shape-identical;
* exactly three changed — `main`, `run_fixture`, `run_search` — the three the
  plan names;
* the new names are exactly the declared split (`dispatch`, `score_fixture`,
  `print_fixture_report`, `search_route`, `print_search_report`,
  `fixture_footer_lines`, `fixture_exit_code`, `unfundable_reason`,
  `none_fundable_header`, three private helpers and the four dataclasses).

The probe is now two tests (`test_snapshot_replay_is_still_round_4_with_only_its_strings_reworded`,
`test_main_py_changed_only_by_the_dispatch_split_the_ui_plan_declares`) that
assert exactly that, with the allowed names listed in the file. A later round
that restructures anything further, or deletes a round-4 function, turns it red
again. `snapshot_replay.py` stays pinned whole. The behaviour the probe
protected is independently held by the byte-identical CLI goldens, the help
test (untouched, still green), and probe A1b below.

## Findings

### High

**H-1 — a verdict that flips inside the surcharge band is reported as settled, because VERDICT SENSITIVE is decided before UK APD is added.**
Probe `ui-probes/test_ui_j_engine.py::test_J1` (RED). Control `test_J2` (GREEN)
proves the rule itself works.

*Repro.* Synthetic fixture built in tmp by the probe: LHR→JFK, business, one
traveller, cash $1,150, points 50,000 in Virgin Atlantic Flying Club on VS
metal — so `data/surcharges.csv` models the surcharge as the band
`$200-$350 (pt $275)` and the leg is a UK departure, so APD adds $330.38.

*Expected.* The points side costs $1,030.38 at the band's low end and $1,180.38
at its high end, against $1,150.00 cash. The verdict therefore **flips inside
the range**, which is what `VERDICT_SENSITIVE` exists to say
(`optimizer.py:1585`): "NOT settled - capture the real surcharge before
booking."

*Actual.* `verdict = points`, `verdict_sensitive = False`, no `VERDICT
SENSITIVE` reason, no warning, no `!SENSITIVE` tag. The CLI prints
`Verdict: POINTS - Points path scores $1,105.38 vs $1,150.00 cash (points
$775.00 + UK APD $330.38).` and the UI paints the green POINTS chip. The trip
headline does show a range (`0.00% - 10.40%`), so the trip level hedges while
the leg the reader acts on does not.

*Location.* `src/optimizer.py` — `evaluate_leg` computes `wins_at_low` /
`wins_at_high` from `points_score_low_usd` / `points_score_high_usd` before
`apply_apd` (`src/optimizer.py:2225`) adds the duty to both ends. Pre-APD the
band is $700–$850, entirely below $1,150, so the flip is invisible to the test.

*Pre-existing.* I reproduced the same non-flag at `3c104b3` (before this
branch). It is not a regression — but this round is the round that puts a
verdict chip on it, and F-3 was a fix of exactly this shape (a sentence quoting
a figure that is not the one scored). A LHR departure on VS or BA metal in
business is Tsuki's own use case, which is why this is High and not Medium.

### Medium

**M-1 — a fare the New-trip form accepts writes a fixture that then crashes every run of that trip, and breaks the test suite.**
Probe `test_ui_e_builder.py::test_E6` (RED).

*Repro.* New trip, one leg SFO→LHR, cash `1e308` (finite, so
`trip_builder.validate_cash` accepts it). Preview, Write. The file lands in
`tests/fixtures/trips/`. Run it OFFLINE.

*Expected.* Either the value is refused, or it is carried and scored.

*Actual.* `config.cash_to_points_equivalent` does `int(round(cash_usd / cpp))`
on an infinite quotient: `OverflowError`, HTTP 500 `Unexpected OverflowError;
see the terminal`, and the same crash with a raw traceback from the CLI
(`exit 1`). The fixture stays on disk, the UI has no delete, and
`tests/test_no_changelog_in_user_output.py` runs the CLI over **every** file in
`tests/fixtures/trips/` and requires exit 0 with no traceback — so one such
trip makes his own suite fail until he removes the file by hand.
*Location:* `src/trip_builder.py:149` (`validate_cash` bounds only NaN/inf),
`src/config.py:266`. The same hole exists in CLI `--new-trip`; the UI makes it
one click and the plan's risk row claims "UI trips are valid fixtures".

**M-2 — two concurrent creates of the same name both succeed; one trip silently overwrites the other.**
Probe `test_ui_e_builder.py::test_E11` (RED; the race is run five times and
reproduces).

*Repro.* Preview eight drafts under the same name with different fares, POST
all eight `create`s at once (a double-click, or two tabs).

*Expected.* One `200`, seven refusals — `write_fixture`'s own words are
"Refusing to overwrite: the file may hold captures nobody can reproduce."

*Actual.* Two report `200 Wrote tests/fixtures/trips/probe_race0.json`; the file
holds one of them. `src/trip_builder.py:413` checks `path.exists()` and then
writes; nothing serialises `trip_create` (the run lock covers runs only). A
one-shot CLI could not race itself; a threaded server can.

**M-3 — the create endpoint accepts names the rest of the app cannot address.**
Probes `test_ui_e_builder.py::test_E3` and `::test_E13` (RED).

* `probe_answer` → written, `200 Wrote …`, then **absent from `/api/trips`**
  (the listing excludes `*_answer.json`) and `/api/trips/probe_answer` 404s. The
  user is told the trip exists and cannot open or run it.
* A 60,000-character name previews as valid (`validate_name` has no length
  limit); a ~200-character name is written but the route regex caps an id at
  121 characters, so it 404s; a ~300-character name raises `OSError`
  (`ENAMETOOLONG`) and returns `500 Unexpected OSError` instead of a refusal.

*Location:* `src/ui/engine.py` `_trip_files` / `trip_create`, `src/ui/api.py`
`_TRIP`, `src/trip_builder.py` `validate_name`.

**M-4 — Enter on a focused leg row opens the drawer and the same keypress closes it again.**
Probe `test_ui_h_browser.py::test_H9` (RED), evidence from a real keyboard walk
(`ui-probes/keyboard.js`), not a synthetic event.

*Repro.* Score a trip, press Tab until a leg row has focus (9 stops), press
Enter.

*Expected* (plan §4.7): "Rows are focusable (`tabindex=0`); Enter opens, Esc
closes."

*Actual.* A mutation trace shows the drawer open and close within the same
keypress: the `keydown` handler runs `open()` → `renderTrips()` →
`focusDrawer()`, which moves focus onto the drawer's `×` button, and the Enter
key's default action then activates that button. The drawer ends hidden and
focus ends on `<body>`. The focus ring itself is correct (2px `--ember`,
`:focus-visible`). A `preventDefault()` in the row's keydown handler is the
obvious fix. *Location:* `src/ui/static/app.js:867` (and the same pattern for
search cells at `:1260`).

**M-5 — the Verdict column is never visible without scrolling the legs table sideways.**
Probe `test_ui_h_browser.py::test_H10` (RED). Screenshot
`ui-probes/shots/offline_b.png` (1440px) and `shots/narrow_1100.png`.

At 1100, 1440 and 1920 px the legs table needs 1461–1620px inside a 838–1178px
container (the content column is capped at 1280px and the drawer takes 440 of
it, docked or not). The table is cut after `POINTS`, so `SURCHARGE`,
`SCORE POINTS`, `SCORE CASH`, `PROVENANCE` and `VERDICT` — the answer — are off
screen until the reader scrolls the table. **The design mockup has exactly the
same property** (measured: 1623px of table in a 718px container at 1440), so
this is a design decision to confirm rather than a coder regression; I am
reporting it because the verdict is the product.

**M-6 — F-1's longer verdict text makes the CLI truncate a money figure in a case the goldens do not cover.**
Probe `test_ui_g_cli_parity.py::test_G1_cli_output_differs_from_the_pre_ui_cli_only_by_F1_and_F3[live-g7-offdate]`
(RED). That probe runs 69 CLI invocations against the pre-UI tree (`3c104b3`,
`git archive`d) and this tree, in separate processes under the same pins, and
allows only the two sanctioned changes; 68 of 69 pass.

*Repro.* The never-priced/2-traveller fixture (`tests/fixtures/cli_golden/inputs/`)
run LIVE against a stub whose LHR→SFO award is on another date.

*Expected.* Only the F-1 cells change.

*Actual.* `PAY CASH (never priced)` is wider than `PAY CASH (no path)`, rich
re-flows the 190-column table, and the **cash cell truncates**:
`$2,400.00` → `$2,400.…`, provenance `2026-09-11` → `2026-09-…`. The coder
shortened the *path* cell for exactly this reason (deviation 3) but the verdict
cell still overflows in the LIVE variant, which no golden covers. The UI's own
table is unaffected (it does not truncate); the transcript shows the truncated
figure.

### Low

**L-1 — the calls counter says "since launch" but resets at midnight.**
Probe `test_ui_c_spending.py::test_C18` (RED). `SeatsClient._budget_remaining`
zeroes `_calls_made` on a date change, so a server left running overnight
reports fewer calls than it has spent since launch. The plan accepted the reset
(risk table); it is the label that is then wrong. Shown in the top bar and in
every confirm dialog.

**L-2 — the `[modeled]` confidence word is swallowed in the drawer too.**
Verified in the rendered page: the drawer reads
`Surcharge: $0.00  via Air Canada Aeroplan / metal=* …`. Rich eats `[modeled]`
as markup in `print_leg_detail`, and the UI shows the terminal's text. The
coder listed this as an out-of-scope observation; confirming it reaches the
page. `surcharge.confidence` carries the value, so the fix is display-only.

**L-3 — both drawers carry `data-testid="drawer"`.** `#drawer-trips` and
`#drawer-search` (`src/ui/static/index.html:39,46`). Harmless today; a
`querySelector('[data-testid="drawer"]')` in a future test would silently pick
the trips one.

**L-4 — `/static/app.js` is readable cross-origin as a `<script>`.** Confirmed
from a hostile page (`test_ui_h_browser.py::test_H14`, GREEN overall). It
carries no secret and every `/api/*` call needs the per-launch token, so the
attacker gains nothing; a `Cross-Origin-Resource-Policy: same-origin` header
would close it for tidiness.

## What I could not break

**The recurring failure** (91 probes, `test_ui_a_contract.py`, all green).
For every golden scenario — offline ×3, LIVE, network-down (exit 3), replay,
replay-refused (exit 1), never-priced, couple, exit 4, no wallet (exit 2),
search ok / api_error / none_fundable / no_awards:

* every `UNKNOWN`, `NOT $0`, `WITHHELD`, `UNVERIFIED`, `NOT LOOKED UP`,
  `NOT RECORDED`, `API FAILED`, `NEVER PRICED`, `NOT ADDED`,
  `CONFIRM BEFORE TRUSTING`, `UNREADABLE`, `REPARSED` in a leg's CLI lines is
  also in that leg's structured JSON outside `cli_lines`, and (A6, H5) in the
  rendered drawer;
* an unknown never carries a number: unknown cash ⇒ `cash_usd` null, unknown
  surcharge ⇒ no point estimate, unknown taxes ⇒ no USD, floor/break-even ⇒ no
  single `score_points`; a range or withheld headline carries no `pct`; a single
  headline always carries a qualifier; `json.dumps(allow_nan=False)` never
  raised;
* exit 3 / exit 4 never produce a quotable number, and `exit_label` matches the
  code in every scenario;
* `A1` — the UI's transcript for Trip B and Trip C offline is **byte-identical**
  to the goldens recorded before any `src/` change (preamble and budget line
  aside), and `A1b` — for every offline scenario the transcript equals what the
  CLI prints in this process for the argv the page itself shows as "Equivalent
  command";
* F-1: no API-failed or never-priced leg says "not a partner" anywhere, in JSON
  or in the DOM (A12, H4, H6);
* the couple trip is WITHHELD with exit 3 through the API and in the browser,
  with `flight leg(s) L2 are for 2+ travellers and were not priced` on the
  headline and `PAY CASH (party of N not priced)` on the leg (A11, H7,
  `shots/g7_couple.png`);
* F-2 (`0.00% (none)`) renders with its qualifier, the NEVER-PRICED counts and
  the sentence "No points price of any provenance entered this margin."

**Security** (76 probes, `test_ui_b_security.py`, plus a real attacker page in
Chromium). Binds `127.0.0.1` only. 14 rebinding-shaped Host headers (including
`127.0.0.1.nip.io:P`, `[::1]:P`, `127.0.0.1:P@evil.com`, `2130706433:P`, absent)
→ 421 with an empty body on `/`, `/static/*` and `/api/*`. Missing, wrong,
case-swapped, padded, cookie-borne and query-string tokens → 403. Seven foreign
`Origin`s on POST → 403 with zero transport calls; `text/plain`,
`x-www-form-urlencoded` and `multipart/form-data` bodies → 400. Thirteen
traversal spellings on static and ten on trip ids → 404. `NaN`, `Infinity` and
`1e309` in a body → 400. Security headers (CSP, nosniff, no-referrer, DENY) are
on error responses too, and no `Access-Control-*` header is ever sent. The fake
key and its mask appear in no body, no log line, no transcript and no stored
run — including when an upstream error echoes the key (the egress filter turns
the whole body into a 500) and when the echo is long enough for rich to fold it
across a transcript line. A hostile page on another loopback origin could not
read the app page, could not read `/api/state`, could not make a simple POST
stick, and spent **zero** Seats.aero calls; the page's token never reached it.
XSS through Seats.aero strings (`<img onerror>`, `</pre><script>`, U+202E, a
5,000-character carrier name) renders as text at 1440px and 400px, with no page
error, no `window.__pwned`, and no page-level horizontal scroll
(`shots/hostile_trip.png`).

**Spending** (22 probes, `test_ui_c_spending.py`). No LIVE run and no search
reached the transport without a fresh, matching, unused confirm: missing,
forged (six shapes), reused, raced (four threads, one confirm → exactly one
run), expired, cross-kind (a trip confirm for a search and back), cross-trip,
stale after a wallet edit, after a wallet-file edit, after an options change,
and after a one-byte edit to the fixture. The stated maximum held against a
3-page paginating stub with `--trips all` at cap 50 and `--flex-days 7`
(`legs × 25 + cap`), a search that never stops paging stops at 25, and
`calls.this_run` equalled the transport's own count every time. Two runs at once
→ exactly one 409 `busy`, and the loser spent nothing. OFFLINE and REPLAY
touched the transport zero times, with REPLAY returning `(snapshot mh_…)` while
the network was refused.

**Long-lived process state** (12 probes, `test_ui_d_state.py`). Trip B offline
is byte-identical before and after a LIVE run, a REPLAY, a search and a wallet
edit (the only difference being "since launch"). A LIVE run is identical whether
it is the first or the fifth. The in-process cache is cleared between runs (two
identical LIVE runs spend twice; a search after a trip run is not answered from
the trip's memory). A 429 in one run does not silence the next run's lookups.
FX lines and the valuation line never move. A wallet error, a bad option and a
replay refusal do not poison the next good run. The run store keeps 20, evicts
the oldest with an honest message, and re-serves the redacted transcript.
`/api/state.running` is true only while the slot is held.

**Wallet** (19 probes, `test_ui_f_wallet.py`). `wallet.json`'s bytes and mtime
were unchanged after every edit, run, search and error. Blank (`UR` present,
balance not supplied), zero and absent stay three different things end to end —
in the JSON, in the argv and in the CLI's own banner — and an emptied panel is
refused in the CLI's words rather than silently becoming "no wallet". Eleven
flag-shaped card names (`--api-key`, `--offline`, `-h`, `--help`, …) and three
flag-shaped currencies could not inject a CLI flag, change the mode, or get
argparse's help into the page. A deleted wallet file is a refusal, not a
remembered wallet.

**The structural guards** (5 probes, `test_ui_i_guards.py`). Ways nine and ten
still refuse at import under `-O` and `-OO` (proved by breaking a copy of the
tree in tmp, never this one). `src/ui/` writes no `add_reason` code, so the
`src/*.py` glob cannot be dodged through it, and deleting `src/ui/` does not
change the discovered unknown vocabulary. Every `src/ui/` module imports
under `-O`.

**Layout** (`test_ui_h_browser.py`, `shots/`). At 400px there is no page-level
horizontal scroll on the trips or the search view, the legs table scrolls inside
its own frame, and the drawer is a full-screen sheet with a close button
(`shots/phone_offline.png`). The drawer is `position: sticky` (docked) at
1440px and `fixed` (sheet) at 1100px, as the Addendum specifies. Nothing in the
page asks for anything but this server and Google Fonts.

## Notes for the manager, not findings

* **A confirm is consumed even when the run is refused as busy**
  (`test_C15`, green, asserted as designed): the page re-preflights on every Run
  press, so it costs a dialog, not a call. Worth knowing it is deliberate.
* **The goldens changed only where claimed.** `test_G2_goldens_changed_only_where_claimed`
  (13 parametrisations, green) diffs every golden at HEAD against the bytes
  recorded at `bdf2af4`, before any `src/` change: only G1/G3/G5/G7/G8 moved,
  only in the F-1 cells and the F-3 sentence, and `pre_f1_f3/` holds the
  pre-change bytes exactly.
* **`test_ui_g_cli_parity.py` is the widest net in this round**: 69 CLI
  invocations (offline/live/down/replay/search, wallet variants, flag conflicts,
  paging, endless paging, no key, badge fallback) diffed against the pre-UI CLI.
  Keep it; it is what would catch a future "small" formatter change.
