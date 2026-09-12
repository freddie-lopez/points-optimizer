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

---

# Re-test 2 — attacking the round-1 fixes

Against `b99d566` (seven commits on top of my round-1 report at `f510a5b`),
fix report `runs/points-optimizer/ui-fix-report-1.md`. New probes are in
`ui-probes/test_ui_k_retest2.py`; `test_ui_e_builder.py::test_E6` was re-scoped
(ruling below).

**412 probes: 5 red, 407 green.** Every round-1 probe is green. The five reds
are all new findings from this round's attack.

| Check | Result |
|---|---|
| `.venv/bin/python -m pytest -q -p no:cacheprovider` | **2013 passed, 13 skipped** (as claimed) |
| the same under `-O` | **2013 passed, 13 skipped**, 1 warning |
| `test_ui_g_cli_parity.py` (69 CLI invocations vs the pre-UI tree) | **69 / 69 green** |
| goldens moved | **G7 only** (What column narrower, no value in it changes) plus the new G14 |
| v5 / adversarial / known-failures / operating-airline probes | **19 / 40 / 0 / 5 red — every set identical by id** |
| working tree after the run | clean apart from this report and the probe files |
| network | nothing left the machine (canary in every probe process; Chromium with no proxy, nothing resolvable but loopback) |

## Ruling: `test_E6`, the one probe the coder left red

**Re-scoped, as the fix intended. A refusal is what E6 was guarding.**

E6's title and docstring already sanctioned either behaviour ("survives the
whole round trip **or refuses loudly**"); only the assertion insisted the fare
be carried. What the probe existed to prevent was a *fixture on disk that made
every later run over that directory crash*, and refusing at validation prevents
it earlier and more completely than carrying it would. Refusing does not hide
anything, for three reasons I checked rather than assumed:

* the same rule is applied by the **loader**, so a fixture that already carries
  such a value (hand-written, or written before this round) is a clean load
  refusal, not a crash — verified for every money field (`K7`, `K8`);
* the bound is **mechanical**, not a made-up ceiling: finite, survives the JSON
  round trip, and has a finite points-equivalent at the run's valuation. `K9`
  walks it from both sides across ten values — `1e308`, `1e307`, `0.1e309`,
  `17976931348623157e292`, `9007199254740993`, `2400.000000000000000001`,
  `1e306`, `-1e308`, `nan`, `inf` — and requires that **whatever is accepted
  then scores without an internal error**, which is the half of the contract a
  refusal could otherwise hide;
* `--valuation-cpp` can still move the bound under the CLI (the UI does not
  offer the flag); `K10` checks that even then the result is not a traceback.

E6 now asserts the refusal, its wording, and that nothing was written.

## New findings

### High

**R2-1 — the delivered tree carries a symlink to a path on the build machine.**
Probe `test_ui_k_retest2.py::test_K20` (RED).

`git ls-tree HEAD .venv` → `120000 blob … .venv`, whose content is
`/home/claude/points-optimizer/.venv`. It was committed in `a53317f` (`Fix L-1
to L-4`); `.gitignore` has `.venv/`, which ignores a *directory* of that name
and not a symlink, so `git add -A` took it.

*Why it matters.* The delivery is a bundle. On Tsuki's Mac, checking out
`feature/ui` either fails (a real `.venv` directory is in the way) or leaves a
dangling `.venv` pointing at a directory that does not exist on his machine —
and every documented command in the README, the plan and the fix report starts
`.venv/bin/python`. It also puts a sandbox path into the repository.

*Fix.* `git rm --cached .venv`, add `.venv` (no slash) to `.gitignore`, and
amend or add a commit before the bundle is cut.

### Medium

**R2-2 — the VERDICT SENSITIVE reason still quotes the band as it stood before UK APD, when the flag was already set.**
Probe `test_ui_k_retest2.py::test_K1` (RED). Controls `K2`, `K3`, `K4`, `K6`
(GREEN) show the rest of the H-1 fix works.

`set_verdict_sensitivity` starts with `if flips == result.verdict_sensitive:
return`. That is right for "nothing changed" — except that the *figures* have
changed: `apply_apd` has just moved both ends of the band. When the band
straddled the fare **before and after** the duty, the early return leaves the
reason built from the pre-APD numbers.

*Repro* (the probe builds it in tmp): LHR→JFK, business, BA Executive Club on IB
metal — the table models that band as `$522.50–$1,045.00` one-way, wider than
the $330.38 duty — against a $1,450 fare.

*Expected.* The reason quotes the scored band, as `K3`'s case now does.

*Actual.* `verdict_sensitive` is correctly `True`, the table and the leg cells
show the scored band `$1,352.88 – $1,875.38`, but the reason code reads:

```
The verdict FLIPS inside the surcharge range: points score $1,022.50 at the low
end and $1,545.00 at the high end, against $1,450.00 cash. This recommendation
is NOT settled - capture the real surcharge before booking.
```

Both figures are $330.38 low and the duty is not named, so the "defensible low
end" reads better than it is — the F-3 shape the fix was written to close, one
branch along. The drawer renders reason codes verbatim, so it reaches the page.
*Location:* `src/optimizer.py`, `set_verdict_sensitivity` (the early return),
called from `apply_apd`.

**R2-3 — a hand-written fixture whose points value is a huge integer is still a traceback in the CLI and a 500 in the UI.**
Probes `test_K7[points_bigint]` and `test_K8[points_bigint]` (RED); the other
seven broken-fixture shapes are green.

M-1's guard bounds **cash** (`_finite_amount` → `unscoreable_cash_reason`).
A points value has no such guard: `{"points": 10**400}` is legal JSON, loads,
and then `funding._score` does `points * _valuation(...)` →
`OverflowError: int too large to convert to float`.

* CLI: a full traceback, exit 1 — against the fix report's "the CLI prints one
  line and exits 1 with no traceback".
* UI: `500 {"error": "internal", "message": "Unexpected OverflowError; see the
  terminal."}` — against "the UI lists it as CANNOT LOAD and never 500s".

Only a hand-edited or externally supplied fixture can carry it (the form writes
no points prices), which is why this is Medium and not High — but "hand-written
broken fixtures" is exactly the class the guard claims. A float `1e308` points
value, by contrast, is handled sanely (`partner exists, path blocked`).

### Low

**R2-4 — Esc closes the drawer and drops focus to `<body>`.**
Probe `test_K23` (RED); `K21`, `K22`, `K24` (GREEN) confirm the M-4 fix itself.

Tab to a leg row (9 stops), Enter — the drawer opens and focus lands on its
"Close detail" button, as it should. Esc closes it, and because closing
re-renders the table the focused element is destroyed: focus falls to `<body>`,
and it takes **18 Tab presses** to get back to the row you were reading. Enter
and Space both open the drawer, and Space does not scroll the page.

## What I could not break in the fixes

**H-1.** A flip the duty *creates* is now marked, and names the duty in the
reason (`K3`). A flip the duty *removes* clears the marker, the reason and the
warning rather than leaving a stale one (`K2`). The flag survives into the UI
JSON with the scored figures on that path (`K4`). On every committed fixture the
flag, the CLI text and the `!SENSITIVE` tag agree (`K6`), and the transcripts
themselves are unchanged against the pre-UI CLI (69/69 parity), so no
previously-settled verdict moved. A leg with an unknown cash side is still not
recommended on points as settled (`K5`). I also walked the other comparisons
that happen before `apply_apd`: `surcharge_cannot_change_verdict` can only be
made *more* true by a duty that adds to the points side; the points→cash flip is
re-decided inside `apply_apd`; `annotate_live_verdicts`, `trip_funding_report`
and `trip_totals` all run after it; the search path has no verdict at all and
already carries the APD floor into its ranking and its `total_is_floor` flag
(`A7`). The off-date sentence quotes a pre-APD figure in its template, but
flexible-date awards are never scored, so I could not reach it — noted, not
filed.

**M-1 (cash).** Ten money spellings at and around the bound are refused before
anything is written, or accepted and then scored without an internal error
(`K9`). Seven of the eight hand-written broken fixtures — a string amount, a
null amount, a 1e308 fee, a wrong-typed `legs`, a huge traveller count, a 1e308
points float, a 1e308 cash amount — are one clean line in the CLI and a listed
row in the UI (`K7`, `K8`). `--valuation-cpp 0.000001` on a 1e300 fare is not a
traceback (`K10`).

**M-2.** Eight simultaneous creates of one name: one success, seven refusals in
the writer's own words, one file on disk (`K11`). A refused create leaves
nothing behind — no partial file, no temp file (`K12`). `--force` still
overwrites for the CLI, and the exclusive create still refuses without it
(`K13`).

**M-3.** 118, 119 and 120-character names are written, listed, fetchable and
runnable; 121 and 122 are refused before anything is written (`K14`). Nine
non-ASCII or odd names either refuse or round-trip to exactly the id the app
then addresses (`K15`). Two names that a case-insensitive or normalising
filesystem could fold together never produce two successes for one file
(`K16`).

**M-4.** Enter and Space both open the drawer, focus lands on the close button,
Space does not scroll the page, and the drawer is not a focus trap (`K21`,
`K22`, `K24`). The focus ring is still 2px `--ember` under `:focus-visible`.

**M-5.** At 400, 1100, 1440 and 1920 px, with the drawer docked and as a sheet,
the LEG and VERDICT columns stay inside the frame at both ends of the scroll,
are `position: sticky`, paint an opaque background, and win the hit test against
the middle columns sliding under them (`K25`, `K26`). At 400px the two pinned
columns take 242px of a 368px frame, leaving 126px of scrolling middle — tight,
but the middle still scrolls and nothing overlaps (`K27`). Screenshot:
`ui-probes/shots/pinned_1440.png` (table scrolled fully right, LEG and VERDICT
both in place) and `shots/pinned_400.png`.

**M-6.** The 69-scenario parity run is green, so F-1's label no longer moves any
money figure that the pre-UI CLI did not already move. I confirmed both filings:
the derived **Score cash** column still truncates (`$2,400.…` in the new G14,
and the pre-UI CLI truncates the same way in G1 and G5 — the parity diff shows
no change there), and the CLI's surcharge line still reads
`Surcharge: $0.00  via …` with rich having eaten `[modeled]`.

**The lows.** "Since launch" no longer falls at midnight and is shown beside
"of 1,000 today" as a second number (`K17`). The drawer's confidence chip comes
from `surcharge.confidence`, which is `modeled` / `captured` / `sourced` and
never empty on a known surcharge (`K18`). `Cross-Origin-Resource-Policy:
same-origin` is on every response, including errors and static files (`K19`).
The only `data-testid` that repeats in the rendered page is `unknown-chip`,
which marks a kind rather than an element.

**Everything from round 1 still holds.** All 347 round-1 probes pass: the
recurring-failure parity in the JSON and in the DOM for every scenario, the
security suite plus the cross-origin attacker page, spending (no call without a
fresh matching confirm; maxima hold with pagination, flex and lookups),
long-lived process state, the couple trip WITHHELD at exit 3, the `-O` import
guards, the phone layout, and the transcript equalling the CLI's own output for
the argv the page shows.

## Round-1 findings: closed or open

| Round 1 | State |
|---|---|
| H-1 sensitivity before APD | **Closed** (`J1`, `K2`, `K3`, `K6`). New neighbouring finding R2-2 |
| M-1 unscoreable fare | **Closed for cash** (`E6` re-scoped, `K9`, `K10`). Open for points: R2-3 |
| M-2 concurrent create | **Closed** (`E11`, `K11`–`K13`) |
| M-3 unaddressable names | **Closed** (`E3`, `E13`, `K14`–`K16`) |
| M-4 Enter closes the drawer | **Closed** (`H9`, `K21`, `K22`, `K24`). New Low R2-4 |
| M-5 verdict column off screen | **Closed** (`H10`, `K25`–`K27`) |
| M-6 F-1 truncated a fare | **Closed** for the cash column; the Score-column truncation is unchanged from the pre-UI CLI and is filed |
| L-1 "since launch" at midnight | **Closed** (`C18`, `K17`) |
| L-2 swallowed `[modeled]` | **Closed in the UI** (`K18`); the CLI line is filed, verified still swallowed |
| L-3 duplicate drawer testid | **Closed** |
| L-4 cross-origin read of app.js | **Closed** (`K19`) |

---

# Re-test 3 — attacking the round-2 fixes

Against `8b579ff` (five commits on `3f1ccfa`), round 2 of
`runs/points-optimizer/ui-fix-report-1.md`. New probes in
`ui-probes/test_ui_l_retest3.py` and `ui-probes/focus.js`.

**441 probes: 4 red, 437 green.** Every probe from round 1 and re-test 2 is
green — all four round-2 findings (R2-1 … R2-4) are closed. The four reds are
new, and three of them are the same shape as the fix they sit next to: the guard
is right and its edge is one step further out.

| Check | Result |
|---|---|
| `.venv/bin/python -m pytest -q -p no:cacheprovider` | **2056 passed, 13 skipped** (as claimed) |
| the same under `-O` | **2056 passed, 13 skipped**, 1 warning |
| the suite inside a `git archive HEAD` export, run with an outside interpreter | **2052 passed, 4 failed** — see R3-1 |
| `test_ui_g_cli_parity.py` | 69 / 69 green |
| v5 / adversarial / known-failures / operating-airline | **19 / 40 / 0 / 5 red — every set identical by id** |
| working tree | clean apart from this report and the probe files |
| network | nothing left the machine |

## New findings

### Medium

**R3-1 — two numbers that each pass the new bound still multiply into an
OverflowError: a hotel leg's `nights × points_per_night`.**
Probes `test_ui_l_retest3.py::test_L5[nights_x_points_per_night]` and
`::test_L6[nights_x_points_per_night]` (RED); the other three product shapes
(`travelers × cash`, `travelers × fee`, `travelers × points`) are green.

R2-3 bounds each field **on its own**: `nights = 10**200` passes
(`10**200 × 0.01` is finite), and so does `points_per_night = 10**200`. The
scale multiplies them: `funding._score` does `points * _valuation(...)` on
`nights × points_per_night = 10**400` and raises
`OverflowError: int too large to convert to float`.

* CLI: a traceback, exit 1.
* UI: `500 {"error": "internal", "message": "Unexpected OverflowError; …"}`.

Same class as M-1 and R2-3, one multiplication further along. The shape of a fix
is presumably to bound the product the loader can already compute (nights ×
points_per_night, travellers × the leg's figures) rather than each field alone —
or to make `funding._score` refuse rather than raise.

### Low

**R3-2 — in a `git archive` export the suite fails four tests, because the
portability tests themselves need `.git`.**
Probe `::test_L3` (RED).

`git archive HEAD` into a clean directory, run with an interpreter from outside
the tree: **2052 passed, 4 failed**. All four failures are
`tests/test_the_delivered_tree_is_portable.py`, and all four are
`CalledProcessError: … not a git repository`: the tests shell out to
`git ls-files`, `git archive`, `git status` and `git check-ignore`, and one also
asserts `(ROOT / ".venv").exists()` — the two things a delivered copy does not
have. The delivery is a git bundle, so in his normal flow (fetch into the
existing clone) they pass; anyone running from an unpacked archive gets four red
tests that say nothing about their tree. `pytest.skip` when
`(ROOT / ".git").exists()` is false would keep the guard and lose the false
alarm.

Everything else in the export is portable: no symlinks, no `.venv`, no
`/home/claude`, `/tmp/claude` or `/Users/` path in any shipped source (the only
`/Users/...` is the synthetic string `test_no_changelog_in_user_output.py` uses
as test data), no CRLF in any `.py`, no executable bits, 293 files. One CRLF
file survives — `data/apd_bands.csv`, which has had CRLF since v5 (`c73d278`)
and which `csv` reads correctly; the export run proves it.

**R3-3 — the refusal prints the 401-digit number it says it will not print.**
Probe `::test_L7` (RED).

`config.unscoreable_count_reason` carefully shortens the number to
`"a 401-digit number"`, and then `trip_loader._count` wraps it:

```python
raise TripFixtureError(f"{what} is {value!r}: {unscoreable}")
```

so the line the user actually sees is 401 digits followed by "…: a 401-digit
number is too large to score…". The UI shows the same string in the trip list's
CANNOT LOAD row. Cosmetic, and exactly the mistake the round-2 note says the
guard avoids — the guard does; its caller does not. `_finite_amount` has the
same `{value!r}` wrapper, which is harmless for a float and would not be for an
int.

## What I could not break in the round-2 fixes

**R2-1 (the delivered tree).** The export carries no symlink, no `.venv`, no
build-machine path in anything under `src/`, `tests/` or `data/`, and no CRLF in
any Python file (`L1`, `L2`, `L4`). 2052 of 2056 tests pass in that export with
an interpreter from outside it; the four that do not are R3-2 above, and none of
them is product code.

**R2-2 (the restated sentence).** On the case that found it — BA Avios on IB
metal, band `$522.50–$1,045.00`, straddling $1,450 both before and after the
$330.38 duty — the reason now quotes `$1,352.88` / `$1,875.38` and names the
duty (`L10`). It is restated **in place**: `VERDICT_SENSITIVE` still sits before
`ALTERNATIVE_UNPRICED` and `APD_ADDED` in the reason list, and the warning keeps
its position among the leg's warnings. Re-deciding the same answer three times
changes nothing at all — not the text, not the order, not the count (`L11`). A
flag flipped off and then on again leaves exactly one reason and one warning
(`L12`). No other reason or warning is touched when the sentence is rebuilt
(`L13`). A leg with no APD gets no duty clause (`L14`). The drawer shows the same
figures and the terminal's own warning line (`L15`).

**R2-3 (numbers that reach arithmetic), apart from R3-1.** `travelers × cash`,
`travelers × mandatory fee` and `travelers × points` are all clean; a count that
cannot be scored is a one-line load refusal and exit 1, not a crash (`L9`); and
the bound has not become a ceiling on award prices — `10**12` and `10**30` load
and score, and a large number the CLI does print is printed in full (`L8`).

**R2-4 (focus).** At 1440px and at 400px: Enter opens the drawer and focus lands
on its close button; Esc returns focus to exactly the row it came from; the ×
button does the same; focus is never left on an element that has been removed
from the document or on one inside a `hidden` section — checked after a re-run
with the drawer open, after a tab switch, and after a second Esc (`L16`–`L19`).
No page errors in any of it. One observation, not a finding: at 400px the open
sheet covers the top bar, so the tabs and the wallet chip are unreachable until
it is closed — expected of a full-screen sheet, and Esc and × both close it.

**Everything earlier still holds.** All 412 probes from round 1 and re-test 2
pass: DOM parity for the recurring failure in every scenario (markers, unknown
discipline, F-1, the headline qualifier), the security suite plus the
cross-origin attacker page, spending (no call without a fresh matching confirm;
the stated maximum holds with pagination, flex days and lookups), long-lived
process state, couple trips WITHHELD at exit 3, the `-O` import guards, the
phone layout and the pinned columns, the three-state wallet label (number /
blank / absent), and the transcript equalling the CLI's own output for the argv
the page shows.

## Findings ledger

| Finding | State |
|---|---|
| Round 1: H-1, M-1…M-6, L-1…L-4 | **Closed** (M-6 and L-2 with the two CLI-output items filed, unchanged and re-verified) |
| Re-test 2: R2-1 `.venv` symlink | **Closed** — untracked, ignored without the slash, and the export proves it |
| Re-test 2: R2-2 stale sensitivity figures | **Closed** — and the restatement survives ordering, idempotence and a double flip |
| Re-test 2: R2-3 unbounded counts | **Closed for single fields**; open for a product: R3-1 |
| Re-test 2: R2-4 Esc drops focus | **Closed** at both widths and through four ways of closing |
| New: R3-1 `nights × points_per_night` | Open (Medium) |
| New: R3-2 portability tests need `.git` | Open (Low) |
| New: R3-3 refusal prints the whole number | Open (Low) |
