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

---

# Re-test 4 — attacking the round-3 fixes

Against `001bd5f` (two commits on `3776cff`), round 3 of
`runs/points-optimizer/ui-fix-report-1.md`. New probes in
`ui-probes/test_ui_m_retest4.py`.

**468 probes: 8 red, 460 green.** Every probe from round 1 and re-tests 2 and 3
is green — R3-1, R3-2 and R3-3 are closed. The 8 reds are **two** new findings,
each in a cash-option and a mandatory-fee shape, each at the CLI and at the API.
Both are the same family as M-1 / R2-3 / R3-1: the guard is right, and the
arithmetic reaches one step past it — once on the caller's side of the guard,
once on the other.

| Check | Result |
|---|---|
| `.venv/bin/python -m pytest -q -p no:cacheprovider` | **2079 passed, 13 skipped** (as claimed) |
| the same under `-O` | **2079 passed, 13 skipped**, 1 warning |
| the same suite in an unpacked `git archive HEAD` export, outside interpreter | **2075 passed, 17 skipped, 0 failed** (as claimed) |
| `test_ui_g_cli_parity.py` | 69 / 69 green |
| v5 / adversarial / known-failures / operating-airline | **19 / 40 / 0 / 5 red — identical by id** |
| working tree | clean apart from this report and the probe files |
| network | nothing left the machine |

## New findings

### Medium

**R4-1 — a huge integer in a money field still crashes: the loader converts before the guard runs.**
Probes `test_ui_m_retest4.py::test_M1[cash_bigint]`, `::test_M1[fee_bigint]`,
`::test_M2[cash_bigint]`, `::test_M2[fee_bigint]` (RED).

Round 3 added `except OverflowError` **inside** `config.unscoreable_cash_reason`
and reported that "a huge int in a money field escaped the guard entirely" was
closed. The guard function does catch it now — but `trip_loader._finite_amount`
does its own conversion one line earlier:

```python
try:
    amount = float(value)            # float(10 ** 400) -> OverflowError
except (TypeError, ValueError) as e: # ... which is not caught here
```

so a fixture carrying `{"amount": 10**400}` in a cash option or a mandatory fee
never reaches the guard.

* CLI: `OverflowError: int too large to convert to float`, a traceback, exit 1.
* UI: the trip list's CANNOT LOAD row reads `OverflowError: int too large to
  convert to float`, `/api/trips/{id}` answers 422 with the same text, and a run
  answers **500 `Unexpected OverflowError`**.

One `except OverflowError` beside the existing one closes it. The count path
(`_count`) already catches `OverflowError`; only the money path does not.

**R4-2 — the bound is currency-blind: an amount that only overflows once the FX rate is applied.**
Probes `::test_M3[cash_in_eur]`, `::test_M3[fee_in_eur]`, `::test_M4[cash_in_eur]`,
`::test_M4[fee_in_eur]` (RED).

The guard checks the number **as the file states it**, at the default valuation.
What is scored is that number times an FX rate. `1.6e306 EUR` passes
(`1.6e306 / 0.01` is finite), is converted to `1.859e306 USD` at the EUR rate of
1.1620, and `cash_to_points_equivalent` then divides by 0.01 into `inf`:

* CLI: `OverflowError: cannot convert float infinity to integer`, traceback, exit 1.
* UI: **500 `Unexpected OverflowError`**; the trip lists and opens normally
  first, so it looks fine until it is run.

Both a `cash_options` amount and a `mandatory_fees` amount reach it. The bound
would have to be taken on the USD figure (or `cash_to_points_equivalent` would
have to refuse rather than raise); the same hole exists for any rate the run
applies after loading.

## What I could not break in the round-3 fixes

**R3-1 (the products).** Every product the file determines is now a clean load
refusal: `points_per_night × nights`, `points × travellers`, `cash ×
travellers`, `fee.amount × nights × travellers` (`M5`). The refusal names the
fixture's own fields — `leg 'B5': the award points (points_per_night x nights)
is too large to score … (a 401-digit number …)` — rather than reporting `inf is
not a finite amount` at the arithmetic (`M6`). Realistic numbers are untouched:
30 nights × 12,000 points a night prints `360,000`, and a 1,000,000-point award
prints in full and scores (`M7`). No run-time knob turns a legal fixture into a
traceback — `--valuation-cpp 1e-10`, `1e300` and `0.000001`, a 301-digit
`--transfer-increment`, a 301-digit `--max-stranded-points` (`M8`). APD ×
travellers on a UK departure with a 10³⁰⁰ party is exit 3, not a crash. And the
search path — whose numbers come from the API and have never been through any of
these guards — survives a 401-digit `MileageCost`, a `1e308` one, a 401-digit
tax figure and a negative cost: exit 0 or a refusal, no 500, no traceback
(`M9`).

**R3-2 (the export).** I unpacked `git archive HEAD` into a clean directory and
ran it with an interpreter from outside the tree: **2075 passed, 17 skipped, 0
failed**, 296 files, no symlink, no `.venv` (`M12`). The skips are the right
ones: 13 documented gates (4 APD-verification, 9 live-snapshot) plus exactly the
4 portability tests, skipped only for "no .git here" (`M13`) — and those 4 do
run, and pass, in a clone (`M14`). Nothing is silently skipped that would run on
his machine.

**R3-3 (the number in the message).** No path I could find prints the digits:
the CLI line, the UI's CANNOT LOAD row, `/api/trips/{id}`'s 422, the run's
refusal and the transcript all read "a 401-digit number", and the builder's
`--travelers`, `--hotel NIGHTS` and `--leg CASH_USD` refusals do too, with
nothing written (`M10`, `M11`). A number a person can read is still printed
unchanged.

**Everything earlier still holds.** All 441 probes from the previous rounds
pass: DOM parity for the recurring failure in every scenario, the security suite
and the cross-origin attacker page, spending (no call without a fresh matching
confirm; the maxima hold under pagination, flex days and lookups), long-lived
process state, couple trips WITHHELD at exit 3, the `-O` import guards, the
phone and docked layouts with the pinned columns, focus restore, and the
transcript equalling the CLI's own output for the argv the page shows.

## If there were another round, this is what I would attack

Named so the manager can tell what is *tested* from what is merely *unbroken so
far*:

1. **The real Seats.aero.** Nothing in four rounds has touched it. Real
   pagination shapes, a 429 in the middle of a trip run, a slow or partial page,
   and the trips endpoint's real field names (the parser is still UNVERIFIED
   against a real response) are all inherited by the UI untested. This is the
   biggest unknown by a distance, and only his Mac can close it.
2. **Wall-clock life.** I ran many scenarios in one process; I did not run one
   server across a real midnight, or for hours against the 6-hour disk cache.
   The date rollover is now two counters instead of one, which is better and
   still untested against a real clock.
3. **Concurrency other than two runs.** The run lock is proven. Preflights,
   wallet edits and trip creates race freely against each other and against a
   run; I tested creates against creates and runs against runs, not the
   interleavings, and not two browser tabs doing different things.
4. **The replay allowlist over time.** Manifest ids are positional; a file
   appearing between page load and Run is a known, documented gap, and a
   manifest edited under a running app is untested.
5. **The small end of the number line.** Four rounds of overflow work have all
   been about the top: `1e-320` fares, fractional points, negative zero, and
   cent-level rounding in the totals are untouched.
6. **Fixture shape rather than fixture numbers.** 10,000 legs, deeply nested
   JSON, duplicate leg ids, a leg id that collides with a `data-testid`
   selector, a fixture that is a 50 MB file.
7. **A browser that is not headless Chromium 1194** with Google Fonts blocked —
   his Safari or Chrome, with the fonts actually loading, at a real retina
   width.
8. **The two filed CLI items** (Score-column truncation, the swallowed
   `[modeled]`) and F-2 (`0.00% (none)`, kept verbatim by decision T4): all
   three are known, agreed and still there.

## Findings ledger

| Finding | State |
|---|---|
| Round 1: H-1, M-1…M-6, L-1…L-4 | **Closed** (M-6 and L-2 with their CLI-output halves filed) |
| Re-test 2: R2-1 … R2-4 | **Closed** |
| Re-test 3: R3-1 products | **Closed** for the products the file determines; the family continues in R4-1/R4-2 |
| Re-test 3: R3-2 export | **Closed** — verified by running the export myself |
| Re-test 3: R3-3 number in the message | **Closed** on every path I could find |
| New: R4-1 huge int in a money field | Open (Medium) |
| New: R4-2 FX-converted amount | Open (Medium) |

---

# Re-test 5 — the boundary as a property, and the untested list

Against `565bafd` (two commits on `28fabeb`), round 4 of
`runs/points-optimizer/ui-fix-report-1.md`. New probes in
`ui-probes/test_ui_n_retest5.py`.

**490 probes: 9 red, 481 green.** Every probe from rounds 1–4 is green: R4-1 and
R4-2 are closed, and so is the whole overflow family the boundary was built for.
The 9 reds are **four new findings**, and — this is the point — none of them is
another overflow. Three are the *other* half of the same idea: a number that is
readable, finite and small, and still not a price.

| Check | Result |
|---|---|
| `.venv/bin/python -m pytest -q -p no:cacheprovider` | **3190 passed, 13 skipped** (as claimed) |
| the same under `-O` | **3190 passed, 13 skipped**, 1 warning |
| the unpacked `git archive HEAD` export, outside interpreter | **3186 passed, 17 skipped, 0 failed** (as claimed); skips = 4 APD gates + 9 live-snapshot gates + 4 git-only checks |
| `test_ui_g_cli_parity.py` | 69 / 69 green |
| v5 / adversarial / known-failures / operating-airline | **19 / 40 / 0 / 5 red — identical by id** |
| working tree | clean apart from this report and the probe files |
| network | nothing left the machine |

## 1. The boundary, tested as a property

**It holds.** I instrumented `config.scoreable_amount` / `scoreable_count` and
loaded every committed fixture: every number in each per-leg fixture passes the
boundary (12 calls for Trip A's 9 numbers, 53 for Trip B's 26, 16 for Trip C's
8). `trip_001`/`trip_002` make 0 calls and contain 0 scoreable numbers — they
have no `legs` key at all, which is what the UI now says about them.

* **The conversions are total** (`N6`): `cash_to_points_equivalent(1e308,
  1e-300)` and `points_to_cash_equivalent(10**400)` raise `UnscoreableNumber`;
  `convert_to_usd(1.6e306, "EUR")` returns the finite product and the *next*
  step refuses, which is where R4-2 crashed; an unknown currency and `cpp <= 0`
  raise `ValueError`; and `367 → 36,700`, `42,600 → $426.00`, `USD → USD` are
  unchanged.
* **The other outside sources hold too.** A 401-digit `MileageCost`, a `1e308`
  one, a 401-digit tax figure, a negative cost and a fractional one, through the
  search path and through a snapshot replayed from disk: a refusal or
  `no_awards`, never a wrong number in a cell, never a traceback (`N3`, `N4`).
  Every numeric CLI flag refuses cleanly — `--valuation-cpp 0 / -1 / 1e400 /
  nan`, `--transfer-increment 0 / -5`, `--fx GBP=0 / -1 / 1e400`,
  `--max-stranded-points`, `--cache-ttl`, `--balance UR=1e400`.
* **The backstop does not swallow a named refusal** (`N7`): a `10**400` cash
  amount is still "leg 'A1' cash option amount: … too large to score", not "this
  run could not be scored".
* **A legal fixture is untouched** (`N8`): Trip B offline is still
  `2.04% - 11.03% (badge)`, B1 `$500.00`, B4 `$418.11`.

The `ast` ban on `int()`/`float()` in `trip_loader` is a guard on *code*, and
nothing a JSON document can carry defeats it: JSON produces only int, float,
str, bool, None, list and dict, so there is no object with a misbehaving
`__float__` to smuggle in. It would not stop a future author importing
`decimal` or calling `operator.index`, which is worth knowing but is not
something an input can do.

## 2. New findings

### Medium

**R5-1 — the loader scores money the builder refuses: zero, negative zero, negative, `true`, and a denormal.**
Probes `test_ui_n_retest5.py::test_N1[zero|negative_zero|negative|true|denormal]` (RED).

`trip_builder.validate_cash` refuses these in the project's own words — *"a cash
price of 0.0 is refused. Zero is not a price - it is silence, and this project
has confused the two before"*. `trip_loader` has no such rule, so a hand-edited
or externally supplied fixture is scored on the figure:

| in the file | the table prints | the verdict sentence |
|---|---|---|
| `"amount": 0` | `$0.00` | `Cash is cheaper: $0.00 vs at least $430.00` |
| `"amount": -0.0` | `$-0.00` | same |
| `"amount": -50.0` | `$-50.00`, cash-as-points `-5,000` | same, and it feeds the trip totals |
| `"amount": true` | `$1.00` | a boolean scored as a dollar |
| `"amount": 1e-320` | `$0.00` | a fare that renders as zero |

The overflow boundary made the top of the number line safe; the bottom is where
this project's own failure mode lives. `scoreable_amount` is the obvious place
for the rule the builder already has (the count path already rejects booleans
explicitly; the money path does not).

**R5-2 — a points price of `0` or a negative one becomes `none - not a partner`.**
Probes `::test_N2[0]`, `::test_N2[-42600]` (RED).

The candidate is dropped silently and the leg reports *"none - not a partner"* —
a claim about transfer partnerships that nothing checked, on a leg where a
partner demonstrably exists in the file. This is F-1's exact shape (the finding
this round's own plan called "the project's recurring failure"), reached from a
different direction. Reproduced identically at `3c104b3`, so it is pre-existing
and was never tested until now; the UI renders it in the path cell and the
drawer.

### Low

**R5-3 — a 401-digit wallet balance is accepted and printed in full.**
Probe `::test_N5` (RED). `--balance UR=<10**400>` is accepted; one run prints
**914 digits**, in the wallet banner and again in the residue table, and the UI's
`/api/wallet` echoes it. The wallet is the one outside-number path that
`config.short_number` does not cover. No crash, no wrong verdict — an unreadable
screen.

**R5-4 — a deeply nested fixture is a `RecursionError` traceback and a 500.**
Probe `::test_N17` (RED). 400 levels of nesting: `json.loads` exceeds Python's
recursion limit, the CLI prints a traceback and exits 1, the UI's trip list
shows `RecursionError: maximum recursion depth exceeded…` and a run answers
**500 `Unexpected RecursionError`**. The backstop covers `ValueError` and
`ArithmeticError`; `RecursionError` is a `RuntimeError`. The boundary's own
stated property is "nothing that overflows anywhere reaches the reader as a
traceback" — this is a different overflow (the stack), and it does.

## 3. The list I said I would attack, worked

* **Wall-clock life.** A cache entry older than the 6-hour TTL is re-fetched,
  not served (`N9`); a cached answer does not re-stamp itself as freshly fetched
  when the clock moves inside the TTL, and its line still says when the bytes
  were fetched (`N10`); the two call counters still survive midnight (`K17`).
* **Concurrency beyond two runs.** A draft, a create, a wallet edit and a state
  read, all fired *during* a LIVE run: the run completes, uses the wallet it
  started with, and none of them is refused or corrupted (`N11`). Two tabs on
  one server: both authenticate, and the second run is refused `busy` while the
  first holds the slot (`N12`).
* **The positional replay manifest id.** With a second manifest inserted into
  the corpus between the preflight and the Run — so index 0 now names a
  different file — the run used the manifest the confirm had named (`N13`).
* **The small end of the number line.** This is R5-1: denormals, zero, negative
  zero and negatives are accepted and scored. Cent-level rounding across the
  totals I did **not** test (see below).
* **Fixture shape rather than magnitude.** 10,000 legs in a 1.2 MB file: scored,
  no crash, the UI lists and runs it (`N14`). Two legs sharing one id: both are
  printed, neither silently swallows the other (`N15`). A leg id carrying
  `leg-row-B1"><img src=x onerror=alert(1)>`: it round-trips as text through the
  API and breaks nothing (`N16`). Deep nesting is R5-4.
* **A second browser.** Not possible here: `/opt/pw-browsers` holds Chromium
  1194 and its headless shell only; the Firefox and WebKit paths Playwright
  names do not exist on this machine. Everything visual and behavioural in every
  round has been measured in headless Chromium 1194 with Google Fonts blocked.

## 4. What is still untested, plainly

So the manager can judge what is merely *unbroken so far*:

1. **The real Seats.aero.** Unreachable from here and out of bounds by rule.
   Real pagination, a mid-run 429, a slow or partial page, and the trips
   endpoint's real field names (the parser is still UNVERIFIED against a real
   response) are all inherited by the UI untested.
2. **Any browser but headless Chromium 1194**, and any real display: Safari,
   his Chrome, a retina width, Google Fonts actually loading, and the whole
   pointer/touch path on a phone rather than a 400px viewport.
3. **Cent-level arithmetic**: rounding across totals and residues, foreign
   currency rounding, and whether the trip totals add up to the penny on a long
   trip. Every money probe so far has been about magnitude, not precision.
4. **A long soak**: a server up for days, memory growth across hundreds of runs,
   a snapshot corpus that grows all the while. I have run dozens of runs in one
   process, not thousands.
5. **Real wall-clock**: I moved a pinned clock. A genuine midnight, a genuine
   six-hour-old cache entry and a genuinely stale FX table are untested.
6. **His own data**: the real `wallet.json`, the real key resolution on macOS
   (permissions, `~/.zshrc`), and a trips directory with his own fixtures in it.
7. **The three known, agreed, still-open items**: the Score-column truncation,
   the swallowed `[modeled]`, and F-2 (`0.00% (none)`, kept verbatim by decision
   T4).

## Findings ledger

| Finding | State |
|---|---|
| Round 1: H-1, M-1…M-6, L-1…L-4 | **Closed** |
| Re-test 2: R2-1 … R2-4 | **Closed** |
| Re-test 3: R3-1, R3-2, R3-3 | **Closed** |
| Re-test 4: R4-1 huge int in a money field, R4-2 FX-converted amount | **Closed** — and the family with them: the boundary holds as a property, not as a list of shapes |
| New: R5-1 money the builder refuses is scored | Open (Medium) |
| New: R5-2 unreadable points price → "not a partner" | Open (Medium, pre-existing) |
| New: R5-3 401-digit wallet balance printed in full | Open (Low) |
| New: R5-4 deep nesting → RecursionError / 500 | Open (Low) |

---

# Re-test 6 — the extended boundary, and cent-level arithmetic

Against `d31e0c9` (two commits on `12d865d`), round 5 of
`runs/points-optimizer/ui-fix-report-1.md`. New probes in
`ui-probes/test_ui_o_retest6.py`.

**546 probes: 2 red, 544 green.** Every probe from rounds 1–5 is green: R5-1,
R5-2, R5-3 and R5-4 are closed. The two reds are **one Medium and one Low**, and
neither is Critical or High.

| Check | Result |
|---|---|
| `.venv/bin/python -m pytest -q -p no:cacheprovider` | **3530 passed, 13 skipped** (as claimed) |
| the same under `-O` | **3530 passed, 13 skipped**, 1 warning |
| the unpacked `git archive HEAD` export | **3526 passed, 17 skipped, 0 failed** (as claimed); skips = 4 APD gates + 9 live-snapshot gates + 4 git-only checks |
| `test_ui_g_cli_parity.py` | 69 / 69 green |
| v5 / adversarial / known-failures / operating-airline | **19 / 40 / 0 / 5 red — identical by id** |
| working tree | clean apart from this report and the probe files |
| network | nothing left the machine |

## 1. The extended boundary, as a property

**The write/read rule really is one rule.** I put 37 spellings through
`trip_builder.validate_cash` (write) and `config.scoreable_price` (read) and
compared both the verdict and the reason: `"2400"`, `" 2400 "`, `"+2400"`,
`"2400\n"`, `"1e3"`, `"1_000"`, `"٣.٥"` and `"١٢٣"` are accepted by both (the
last two are Python's `float()` reading Arabic-Indic digits — odd, but
identical on both sides); `"2,400"`, `"$2400"`, `"0x10"`, `"abc"`, `""`,
`"   "`, `nan`, `inf`, `None`, `[]`, `{}`, `True`, `False`, `0`, `-0.0`,
`-50`, `0.004`, `1e-320` and `10**400` are refused by both, with the same
reason (`O1`). The only difference is cosmetic: the builder strips a string
before quoting it, so `"   "` is reported as `''` there and as `'   '` at load.

**The price/amount split is right where it matters.** A `$0.00` carrier
surcharge still prints on Trip B's B1 and B4 (`O2`); a `$0.00` fare is refused
on both surfaces. `cash_surcharge` goes through the amount rule, fares and
nightly rates through the price rule.

**`scoreable_points` covers the live path too, by a different mechanism and
correctly**: a Seats.aero row with `MileageCost` of `0`, `-50000`, `0.5` or `""`
comes back as `none: UNREADABLE`, path `no live data`, exit 3 — not "not a
partner", not a scored figure, not a crash (`O3`). And the fixture-side refusal
names the claim the silence would have made (`O4`).

**Nothing legal became illegal.** The three committed per-leg fixtures load and
score unchanged, Trip B is still `2.04% - 11.03% (badge)` (`O5`), and the
builder still accepts `0.01`, `1`, `2400`, `199999.99` while refusing `0`, `-1`
and `0.004` (`O6`). One consequence worth stating rather than hiding: a fixture
with *one* zero-points candidate is now refused **as a file**, rather than
having that candidate dropped. That is the trade the fix makes — a dropped one
produced "none - not a partner" — and I agree with it, but a hand-edited file
that used to score will now refuse.

**R5-3 and R5-4 are closed**: a 401-digit balance is a wallet refusal in words
with no digits printed (`O7`), and a 400-level-deep fixture is "nested too
deeply to read", exit 1, no traceback, no 500 (`O8`).

## 2. New findings

### Medium

**R6-1 — a negative mandatory fee is a discount that is not there.**
Probe `test_ui_o_retest6.py::test_O9` (RED).

`mandatory_fees.amount` is read as an *amount*, not a *price*, so the R5-1 rule
does not apply to it. A fixture carrying `{"label": "…", "amount": -500.0}`
scores:

```
│ A1 │ MRY-JFK round trip │ $367.00 │ -13,300 │ … │ >= $-70.00 │ $-133.00 │
```

— a negative cash-as-points figure, a negative points floor and a **negative
cash side**, which then feeds the trip totals. It is the same shape as the
negative fare R5-1 closed, on the one money field that did not get the rule. A
fee of `0.00` is fine and should stay fine (a stated "no resort fee" is a real
figure); a *negative* one is not a figure, it is a broken one. The fix is to
bound fees below at zero, not to route them through the price rule.

### Low

**R6-2 — a table of sub-cent figures does not add up on the page.**
Probe `::test_O12` (RED).

Four legs at `$10.005`: each row prints `$10.01`, and the total prints `$40.02`.
The engine keeps full precision and rounds only when printing, so the printed
total is two cents below the sum of the printed rows. On the committed trips the
figures agree to the cent — I checked Trip A, Trip B and Trip C (`O10`) and the
residue tables balance exactly (`O11`) — and a captured fare always has two
decimals. But an **FX-converted** amount is sub-cent routinely (Trip B's
`EUR 643.57 × 1.1620 = $747.828…`), so the general case is reachable with real
data; it happens not to bite on the committed trips. Displayed foreign amounts
and the figures behind them agree to the cent (`O13`).

## 3. The invariant sweep

All 544 other probes green, including: DOM parity for the recurring failure
across every golden scenario (markers, unknown discipline, F-1, the headline
qualifier, the drawer), the security suite and the cross-origin attacker page,
spending and confirms (no call without a fresh matching confirm; the stated
maximum holds under pagination, flex days and lookups), long-lived process
state, couple trips WITHHELD at exit 3, the `-O` import guards, the phone and
docked layouts with the pinned columns and focus restore, the CLI parity of 69
invocations, and the transcript equalling the CLI's own output for the argv the
page shows.

## 4. Is this ready for the manager?

**Yes.** Six rounds in, the thing this project exists to prevent — a failure
reported as a finding — is defended at every layer I can reach, and I have
tried: 546 probes, 69 CLI invocations diffed against the pre-UI tree, a real
browser driving the real page, a hostile page on another origin, and five rounds
of numbers designed to break the scorer. What is left is one Medium that needs a
hand-edited fixture (a negative fee), one Low about rounding that needs sub-cent
inputs, and three known items everyone has already agreed to defer. The
qualification is the one I have made in every round and it has not moved:
**nothing here has ever spoken to the real Seats.aero.** Every LIVE path is a
stub, the trips parser is still UNVERIFIED against a real response, and the
first real run on his Mac is the first time any of it meets the API it was
written for. That is a risk about *the world*, not about this code, and no
amount of testing here can close it — but the manager should sign off knowing
the LIVE column of this report is synthetic from top to bottom.

## Findings ledger

| Finding | State |
|---|---|
| Round 1: H-1, M-1…M-6, L-1…L-4 | **Closed** |
| Re-test 2: R2-1 … R2-4 | **Closed** |
| Re-test 3: R3-1, R3-2, R3-3 | **Closed** |
| Re-test 4: R4-1, R4-2 | **Closed** |
| Re-test 5: R5-1 money the builder refuses; R5-2 points → "not a partner"; R5-3 wallet digits; R5-4 deep nesting | **Closed** |
| New: R6-1 negative mandatory fee | Open (Medium) |
| New: R6-2 sub-cent rounding on the page | Open (Low) |
| Filed, agreed, unchanged | Score-column truncation; `[modeled]` swallowed by rich; F-2 (`0.00% (none)`, decision T4) |

---

# Mac round

Tsuki ran the suite on his Mac for the first time: **5 failed / 3565 passed /
13 skipped**. The Coder fixed two defects behind those five (MAC-1, copyable
lines folding at narrow widths; MAC-2, the file-shape rules), and left one
deviation for me to rule on. This round I attacked the two fixes as
*properties*, finished the platform sweep they did not, re-verified the
invariants I own, and ruled on the `main.py` probe.

**620 probes at this head: 618 green, 2 red.** The new file is
`docs/test-reports/ui-probes/test_ui_p_mac.py` (P1-P20). The two reds are P7
and P8, and they are the finding below.

## Findings

### MAC-A (Low) - the relocation banner is the one copyable line still left folding

**Repro** (`test_ui_p_mac.py::test_P7_the_relocation_banner_is_not_copyable_yet`,
`::test_P8_a_very_long_relocation_path_is_split_mid_path`):

```
POINTS_OPTIMIZER_CACHE_DIR=/private/var/folders/9w/8k2x7p1n5q3d_4m6r0j8t1_c0000gn/T/\
pytest-of-tsuki/pytest-4/test_the_live_transcript_matc0/snapshots
.venv/bin/python -m src.main <any subcommand>
```

**Expected:** a path a user is meant to read and paste comes out whole on one
line, like every other copyable line after MAC-1.

**Actual:** `print_relocation_banner` is still three ordinary `console.print`
calls, so rich wraps them at the console width. Measured at width 190:

| path | whole banner line | printed as | path intact? |
|---|---|---|---|
| 122 chars (a realistic pytest tmp path on his Mac) | 205 chars | 2 lines, continuation `location for this run)` | yes |
| 161 chars | 244 chars | 3 lines | yes |
| **217 chars** | 300 chars | 3 lines | **no - the fold lands inside the path** |

**Location:** `src/main.py`, `print_relocation_banner`.

**Why it is a finding and not a preference:** the Coder's fix report declines
this one on the ground that "none of those three can pass 190 columns in
practice". That premise is wrong. The value is an environment variable, and the
suite's own harness sets it to a macOS pytest tmp path; at 122 characters -
shorter than paths pytest actually generates - the line is already 205
characters and folds. It is Low rather than Medium because below roughly 190
characters of path the path itself still survives whole on its own line, so a
double-click still copies something usable; past that it does not.

**Fix is one line of the same kind already applied elsewhere:** route these
three through `print_copyable`. The only thing that stopped it was the
operating-airline probe going red, and that is my call - see the ruling.

## Ruling on the `main.py` probe

**Reverting was not right. I widened the probe.**

`docs/test-reports/operating-airline-probes/test_oa_r5_retest.py` exists to
catch *undeclared restructuring* of `main.py` while the operating-airline work
was in flight - not to freeze the file against a named, reviewed fix. Holding a
correctness fix hostage to an adversarial pin inverts what the pin is for. I
added, in the file, with the reasoning recorded next to it:

```python
CHANGED_BY_THE_MAC_1_FIX = {"print_relocation_banner", "run_new_trip"}
allowed = SPLIT_BY_THE_UI_PLAN | CHANGED_BY_THE_MAC_1_FIX
```

What those functions *output* is still pinned - byte-identically - by the CLI
golden transcripts and by the 69-scenario parity suite in `ui-probes`, which
diffs this tree against pre-UI `3c104b3`. So the fix can land without loosening
anything that matters. The operating-airline baseline is still 5 red by id
after the widening; I changed what is allowed, not how many probes bite.

The Coder should now un-revert `1185dbf` and land the three `print_copyable`
calls.

## MAC-1 as a property - what held

`print_copyable` emits its text as **one whole line**, at console widths 20,
40, 80, 190, 400 and 20000, for: a macOS `/private/var/folders/...` tmp path, a
200-character path, a path with spaces, a unicode path, a path containing
`[draft]`, and a path with backslashes (P1-P4).

- `[draft]` is not eaten - the markup is escaped, not interpreted.
- A style adds **zero** characters to the emitted text.
- Prose that is *not* copyable still wraps, so the fix did not turn the whole
  program into one long line.
- The real `yq-check` command, end to end in a macOS-shaped deep directory at
  widths 20/40/190/400, prints exactly **one** CSV row with every path whole
  (P5).
- The live-run banner manifest line (P6) and the UI's `argv_display` and
  transcript (P9, P10) are unfolded.

The only call site in the program that still folds is the one in MAC-A.

## MAC-2 as a property - what held

The scanner in `src/config.py` agrees with the **parsed** depth on ten string-
literal edge cases: braces and brackets inside strings, an escaped quote, a
trailing escaped backslash, a `\u007b` escape, a close brace inside a string (P11).

- Cost: 0.058 s for a 1.4 MB file; 0.002 s for an unterminated string with
  40,000 backslashes. No pathological blowup (P12).
- Every committed fixture is depth 2-5: builder output 5, wallets 2, a 400-leg
  trip depth 5 at 920 KB. The rule refuses nothing real (P13, P14).
- Exactly 32 loads; 33, 35 and 120 refuse with the **identical** sentence at
  recursion limits 1000 and 20000, with no traceback (P15).
- A 5 MB fixture is refused in one sentence: `is 5,000,101 bytes, and may be at
  most 4,194,304` (P16).
- The wallet carries the same two rules: `Wallet error: ... nested 41 levels
  deep ...`, exit 2 (P17).
- The UI lists `load_error`, 422s the detail, and **never 500s**; the wallet
  panel survives a deep wallet file (P18).

## The platform sweep - what matters for his Mac, and what is theoretical

| Item | Verdict |
|---|---|
| **Case-insensitive filesystem (evidence filenames)** | **Real for his Mac.** APFS is case-insensitive by default; two evidence files differing only in case collide there. Not reproducible in this sandbox (ext4), so I could not write a probe that bites here - it needs a run on his machine or a case-insensitive loopback image. This is the one platform item still genuinely open. |
| **`LANG=C` end to end** | **Theoretical for his Mac**, real for a C-locale CI box. `c52cd9a` fixed the import (`import src.models` under `LANG=C` now works - P19 green), but `python -m src.main` on `trip_b`/`trip_c` still dies `UnicodeEncodeError: '…'`, exit 1, and 4 tests in `test_no_changelog_in_user_output.py` fail. His terminal is UTF-8, so he will not hit it; a Linux CI container with no locale set will. |
| **`/private/var` tmp shape** | **Covered.** This was MAC-1's cause. The suite passes at a long macOS-shaped `--basetemp` (3656 passed / 13 skipped) once the basetemp directory exists - note that pytest errors every test with `FileNotFoundError` if you point `--basetemp` at a path whose parent is missing, which is a harness trap, not a defect. |
| CRLF fixtures | Green (P20). |

## Standing invariants, re-verified at this head

- Full suite: **3656 passed / 13 skipped**, and identical under `-O` (the
  structural import guards still refuse to be optimised away).
- Long macOS-shaped `--basetemp`: **3656 / 13**.
- Clean `git archive` export unpacked into a fresh directory: **3652 passed /
  17 skipped / 0 failed**.
- Baselines identical **by id**: v5 19 red, adversarial 40 red, known-failures
  0, operating-airline 5 red (still 5 after the widening).
- **No network call left this box.** The socket canary is still armed in
  `ui-probes/conftest.py`, the browser context still aborts and records any
  non-loopback request, and nothing was recorded. Nothing in this round spoke
  to seats.aero or any real host.
- Nothing was written into the repo tree or Tsuki's paths except my probe files
  and this report.

## Are his five failures actually closed?

**Four of five: yes. The fifth: yes for the failure he saw, no for the class.**

The three MAC-1 failures were copyable lines folding because a macOS tmp path
is long; `print_copyable` fixes that as a property at every width I can throw at
it, including widths far narrower than any his terminal will be. The two MAC-2
failures were the file-shape rules disagreeing with themselves; scanner and
parser now agree on every string-literal edge case I could construct, at two
recursion limits, and refuse nothing real. Re-running the exact shapes that
failed on his Mac - long `/private/var` basetemp, deep and oversized files -
is green here.

The caveat is MAC-A: the same class of bug the MAC-1 fix exists to kill is
still live in `print_relocation_banner`, declined on a premise I measured to be
false. It did not cause one of his five because that banner only prints when a
`POINTS_OPTIMIZER_*` variable is set, and his run happened not to hit a long
enough path in a way pytest compared. It will.

And the qualification I have made in every round has not moved: **nothing here
has ever spoken to the real Seats.aero.** His Mac run is the first contact with
the real world, and it found two real defects in one afternoon. That is the
point, and it is also the warning.

## What I could not break, this round

`print_copyable` itself - not with 20-column terminals, 20000-column
terminals, 200-character paths, spaces, unicode, backslashes, rich markup in
the path, or styles. The nesting scanner - not with braces in strings, escaped
quotes, trailing backslashes, `\u007b` escapes, unterminated strings, 40,000
backslashes, 1.4 MB of input, or recursion limits twenty times the default. The
size rule - not with a fixture one byte over. The UI under either rule - it
refuses in one sentence and never 500s. And the invariants: clean tree, clean
export, `-O`, the baselines by id, and the socket canary.

## Findings ledger, updated

| Finding | State |
|---|---|
| Round 1 through Re-test 6 (all prior) | **Closed** except R6-1 (Medium), R6-2 (Low) |
| MAC-1 (copyable lines fold) | **Closed** - property-verified |
| MAC-2 (file-shape rules) | **Closed** - property-verified |
| New: MAC-A relocation banner still folds | Open (Low) - probe widened so the fix can land |
| Open platform item: case-insensitive evidence filenames | Needs a run on his Mac; not reproducible here |
| Open platform item: `LANG=C` end to end | Theoretical for his Mac, real for C-locale CI |
