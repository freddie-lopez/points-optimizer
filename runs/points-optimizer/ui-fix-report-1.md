# UI — coder fix report, round 1

Against `docs/test-reports/ui.md` at `f510a5b` (1 High, 6 Medium, 4 Low; 9 red
probes of 348) plus the three manager design calls. Worktree
`/home/claude/points-optimizer-ui`, branch `feature/ui`, seven commits on top of
`f510a5b`. No network call was made by anything here; every test stubs the
transport and the parity runner keeps a socket canary that refuses and reports
any `connect()`.

## Where it ended up

| Suite | Before | After |
|---|---|---|
| `tests/` (mine) | 1,982 pass | **2,013 pass, 13 skipped** — same under `-O` |
| `docs/test-reports/ui-probes` (the Tester's) | 339 pass / 9 red | **347 pass / 1 red** (E6, deliberate — below) |
| `test_ui_g_cli_parity.py` (69 CLI invocations vs the pre-UI tree) | 68 / 1 red | **69 / 0** |
| v5-probes | 19 red | 19 red, same ids |
| adversarial-probes | 40 red | 40 red, same ids |
| known-failures-probes | 0 red | 0 red |
| operating-airline-probes | 5 red | 5 red, same ids |

Every fix has a regression test. New test files:
`tests/test_verdict_sensitivity_after_apd.py` (H-1, 5),
`tests/test_unscoreable_cash_is_refused.py` (M-1, 17),
`tests/test_trip_create_is_atomic_and_addressable.py` (M-2/M-3, 11),
`tests/test_leg_table_money_is_not_truncated.py` (M-6, 5),
`tests/test_ui_round1_lows.py` (L-1..L-4, 10),
`tests/test_ui_run_strip_and_trip_listing.py` (design calls, 8).

## The findings

**H-1 — VERDICT SENSITIVE decided before APD.** `evaluate_leg` decided it once,
before `apply_apd` moved both ends of the surcharge band by the duty, so a UK
departure whose points side won at the band's low end and lost at its high end
*with the duty in* was reported as a settled POINTS verdict. The decision is now
a function, `set_verdict_sensitivity(result, apd_usd=…)`, called again at the
end of `apply_apd`: the flag, the reason and the warning are all restated on the
figures that were actually scored, and the duty is named in them. A flip the
duty *removes* clears the marker rather than leaving a stale one. Probes E3,
E11, E13, H9, H10 and J1 went green. No golden moved: no committed scenario
straddles its fare.

**M-1 — a fare that cannot be scored.** `1e308` is finite, so `validate_cash`
accepted it, wrote it, and every later run over that directory raised
`OverflowError` from `int(inf)`. The bound is now mechanical and lives in
`config.unscoreable_cash_reason`: the amount must be finite, survive the round
trip through the fixture's JSON, and have a finite points-equivalent at the
run's valuation. It is checked in `trip_builder` *before* anything is written,
so the CLI gets the same guard, and `trip_loader` raises `TripFixtureError` for
a fixture that already carries one — the CLI prints one line and exits 1 with no
traceback, and the UI lists it as CANNOT LOAD and never 500s.

**M-2 — two creates of one name.** `write_fixture` checked `path.exists()` and
then wrote; a threaded server let two callers pass the check and both report
"Wrote …". The create is now `os.open(..., O_CREAT | O_EXCL)`: the kernel
decides, and everyone else gets the writer's own "Refusing to overwrite"
refusal. Eight simultaneous writers leave one file, one success and seven
refusals, through the builder and through the API. `--force` still overwrites.

**M-3 — a name the app cannot address.** `validate_name` has
`MAX_NAME_LENGTH = 120` and refuses longer names in the builder's own words
(it names the length it got and the longest it can address). The longest
addressable name is written, listed and fetchable. Separately, a trip a user
calls `..._answer` is now listed and runnable: the acceptance ANSWER files are
recognised by *not loading as a trip*, not by their name.

**M-4 — Enter opened the drawer and closed it.** The row's `keydown` ran, then
the default action fired a `click` on the row, which the outside-click guard
read as a click outside the drawer. `preventDefault()` on Enter and Space, on
leg rows and search cells; verified in Chromium that the drawer stays open and
focus is inside it.

**M-5 (design call) — pinned columns.** LEG and VERDICT are `position: sticky`,
left and right, so the identifier every other line refers back to and the answer
the row carries are both on screen while the middle scrolls. Rows needed an
opaque background; hairlines mark the seams. `docs/design/ui-mockup.html` had
the same defect and got the same fix.

**M-6 — F-1's longer verdict label re-flowed a fare away.** `PAY CASH (never
priced)` is five characters longer than the `(no path)` it replaced, and rich
fits an over-wide table by shaving *every* column; those five came out of the
money columns and a $2,400.00 fare printed as `$2,400.…`. Two floors, in
`print_leg_results`, engaging **only when that label is in the table** (so no
other CLI output moves): the What column asks only for what it holds — it
demanded 12 characters to show `SFO->LHR`, and that padding came out of columns
that needed it — and the cheapest-cash column keeps room for its widest figure.
The verdict column gives way instead; its sentence is repeated in full under
"Per-leg detail". New golden **G14** pins the LIVE never-priced transcript (the
variant no golden covered). **G7 is the only existing golden that moved, by the
What column's width alone — no value in it changes:**

```
-┏━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━┳ …          -│ L1  │ SFO->LHR     │ $2,400.00 │ …
+┏━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━┳ …              +│ L1  │ SFO->LHR │ $2,400.00 │ …
```

*Filed, not fixed:* the derived **Score** columns can still be shaved
(`$2,400.…` in "Score cash"). That is older than this round — the pre-UI CLI
does it on G1, G5 and others — and the parity rule for this round forbids
moving it. It wants a round of its own.

**L-1 — "since launch" reset at midnight.** It was read off Seats.aero's *daily*
counter. `SeatsClient` now also keeps a count that never resets, the engine
takes its zero at launch, and the top bar and both confirm dialogs show two
numbers as two numbers: "5 since launch · 5 of 1,000 today".

**L-2 — the `[modeled]` rich swallows.** The drawer takes the word from
`surcharge.confidence` and shows it as a chip (MODELED / CAPTURED, with what
each means), so a `$0.00` surcharge is never shown without saying whether anyone
observed it. **The CLI line still loses the word** and a test pins that, because
escaping it changes `  Surcharge: $…` in G1, G3, G4, G6, G8 and about twenty
parity scenarios — output this round is not allowed to move. It predates the UI
(it is in the `pre_f1_f3/` goldens too). Filed.

**L-3** — `#drawer-trips` and `#drawer-search` have their own testids, and a
test now refuses any duplicate `data-testid` in the page.
**L-4** — `Cross-Origin-Resource-Policy: same-origin` on every response.

## The other two design calls

**Run strip.** The three mode segments are one control and are read across, so
they are the same width whatever each has to say (`flex: 1 1 0`; measured 264 /
264 / 263 at 1440 and 114 / 114 / 113 at 400). The REPLAY button now says "no
snapshot to replay yet"; the sentence carrying the path sits on its own line
under the control, the path shortened to its last two parts, the whole of it in
the `title`. The state sends the short reason and the path *separately*, so the
page never cuts a string it did not build.

**The trip listing.** `trip_001` and `trip_002` are the acceptance suite's
original inputs: a route, a date range and balances, with no `legs` key at all.
They load, so "CANNOT LOAD" would be false, and "0 legs · 0 flights" read as a
finding about the trip rather than a fact about the file. The sidebar row now
reads **NOT A PER-LEG TRIP — a single-route search request for SFO->LHR**, and
the trip's own page says the whole of it instead of showing an empty table.
Every word is read out of the file; nothing is inferred.

## Deviations, and one probe left red

1. **E6 stays red, deliberately.**
   `test_E6_a_huge_but_finite_fare_survives_the_whole_round_trip_or_refuses_loudly`
   asserts `created.status == 200` — the fare is carried — while the
   coordinator's instruction for M-1 was to refuse it "at validation time,
   BEFORE anything is written, in `trip_builder`". I followed the instruction.
   The draft refuses, so the probe's own helper returns `created = None` and the
   assertion raises `AttributeError` rather than reporting. The probe's title
   and docstring sanction a refusal ("or refuses loudly" / "either carry it or
   refuse"); only the assertion does not. **Tester's call**, and the fix is one
   line in the probe if refusal is the wanted behaviour.

2. **M-6's floors are conditional.** Protecting the money columns
   unconditionally is the rule I would want, and it turns 19 of the 69 parity
   scenarios red, because the pre-UI CLI truncates money in all of them. The
   floors therefore engage only where F-1's label actually widened the table.
   That is the round's contract enforced in code rather than a principle; if the
   Score-column truncation above is scheduled, both should be done together and
   the condition dropped.

3. **L-2 is fixed in the UI only**, for the same reason — see above.

4. **One flaky test fixed on the way.** The request log line is now written
   *before* the response goes out; a client that had already read the body could
   otherwise look at the log before the serving thread appended to it, which
   failed `test_the_request_log_is_method_path_status_only` about one run in ten.

## Verification

Full suite normally and under `-O` (2,013 pass, 13 skipped, identical both
ways). The whole Tester probe suite (348). The four baseline probe directories
compared by test id, all unchanged. DOM parity against the run JSON in headless
Chromium at 1440 and 400 px, offline and LIVE, with no page errors: verdicts,
every UNKNOWN cell, every transcript line in the drawer, the keyboard path, the
pinned columns, the confidence chip, the run strip and the trip listing.

---

# Round 2 — against the re-test at `3f1ccfa`

One High, two Medium, one Low; 5 red probes in `test_ui_k_retest2.py`.
Five commits on top of `b99d566`. Nothing here made a network call.

| Suite | Before | After |
|---|---|---|
| `tests/` (mine) | 2,013 pass | **2,056 pass, 13 skipped** — identical under `-O` |
| `docs/test-reports/ui-probes` | 407 pass / 5 red | **412 pass / 0 red** |
| v5 / adversarial / known-failures / operating-airline | 19 / 40 / 0 / 5 red | **same sets, by id** |

New test files: `tests/test_the_delivered_tree_is_portable.py` (R2-1, 4),
`tests/test_verdict_sensitivity_reason_is_restated.py` (R2-2, 5),
`tests/test_unscoreable_counts_are_refused.py` (R2-3, 26),
`tests/test_drawer_returns_focus.py` (R2-4, 7); plus one rewritten idempotence
test in `test_verdict_sensitivity_after_apd.py`.

**R2-1 — the tracked `.venv` symlink.** `git rm --cached .venv`, and the ignore
rule loses its trailing slash: `.venv/` ignores a *directory* of that name and
nothing else, which is exactly how `git add -A` took the symlink in `a53317f`.
The link stays on disk, untracked, so this sandbox keeps working. Verified: no
tracked path is a symlink or a non-regular file; `git archive HEAD` exports 321
entries with no `.venv`, no symlink whose target is absolute, no leading `/` and
no `..` segment. The only absolute paths left anywhere in the tree are inside
prose — the plan and these reports naming the worktree they were written in, and
one synthetic `/Users/someone/...` string used as test data. The regression test
checks the archive, not just the worktree, because the archive is the delivery.

**R2-2 — the sensitivity sentence.** The early return was right about the flag
and wrong about the figures: `apply_apd` had just moved both ends of the band.
The reason and the warning are now rebuilt from the current figures every time
and compared with what is there. Identical text is left untouched **in place**,
so re-deciding the same answer still moves nothing; different figures are
restated **at the same index**, so a restated sentence does not travel to the
end of the list and reorder the output. The probe's own case — BA on IB metal,
a band wider than the duty, straddling $1,450 both before and after — now quotes
$1,352.88 / $1,875.38 and names the $330.38 duty. No golden moved.

**R2-3 — the bound reaches every number that reaches arithmetic.**
`config.unscoreable_count_reason` applies the money rule to whole numbers: a
whole number, surviving the round trip through the fixture's JSON, whose product
at the run's valuation is finite. Checked **at validation** (`--travelers`,
`--hotel NIGHTS`, before anything is written) and **at load** (`points`,
`points_per_night`, both night counts, and `travelers` — which multiplies every
money figure on the leg and was the same hole one multiplication along). It is
not a ceiling on award prices: `10**30` and `1e308` points still load and are
reported exactly as before; only a figure no run could put on the scale is
refused. The reason never quotes the number in full — `repr(10 ** 400)` is 401
digits — it says "a 401-digit number", which is the same mistake M-1 made once
and fixed.

**R2-4 — Esc gives the row back.** Both drawers record the testid they were
opened from and restore focus to the rebuilt element, on Esc and on the close
button; navigating to another trip or run forgets it rather than focusing a
stale row. Verified in Chromium: Esc from `leg-row-B4` leaves focus on
`leg-row-B4`, the close button from `leg-row-B2` leaves it on `leg-row-B2`, and
no page error. Mouse users see no focus ring, because `:focus-visible` does not
match a programmatic focus that follows a click.

## Still filed, not fixed

Unchanged from round 1 and confirmed by the Tester: the derived **Score**
columns still truncate money exactly as the pre-UI CLI does, and the CLI's
surcharge line still loses `[modeled]` to rich. Both need a round in which CLI
output is allowed to move; the parity rule forbids it now.

## Verification

Full suite normally and under `-O` (2,056 / 13 skipped, identical). All 412
Tester probes. The four baseline probe directories compared by test id, all
unchanged. DOM parity against the run JSON in headless Chromium at 1440 and
400 px, offline and LIVE, with no page errors, plus the keyboard round trip
above.

---

# Round 3 — against the re-test at `3776cff`

One Medium, two Lows; 4 red probes in `test_ui_l_retest3.py`. One commit on
`8b579ff`. Nothing here made a network call.

| Suite | Before | After |
|---|---|---|
| `tests/` (mine) | 2,056 pass | **2,079 pass, 13 skipped** — identical under `-O` |
| the same suite inside an unpacked `git archive` export | 2,052 pass / **4 fail** | **2,075 pass, 17 skipped, 0 fail** |
| `docs/test-reports/ui-probes` | 437 pass / 4 red | **441 pass / 0 red** |
| v5 / adversarial / known-failures / operating-airline | 19 / 40 / 0 / 5 red | **same sets, by id** |

New test file `tests/test_unscoreable_products_are_refused.py` (23), plus the
two edits to `test_the_delivered_tree_is_portable.py`.

**R3-1 — the products, not the fields.** Bounding each field on its own was one
multiplication short of the arithmetic. The loader now checks every product the
FILE itself determines, by the same mechanical rule, at load: `points_per_night
× nights` (what `hotels.award_points_for` computes), `points × travellers`,
`cash × travellers`, and a fee's `amount × nights × travellers` (what
`MandatoryFee.total_for` computes). Three of the four are clean today and are
checked anyway — "clean today" is what was said about the shape before this one,
twice. The builder checks the products it determines *before anything is
written*. The refusal names what multiplied ("the award points
(points_per_night x nights) is too large to score — this fixture's own numbers
multiply past what a run can hold"), because a product that has already
overflowed to `inf` otherwise reports itself as "inf is not a finite amount",
which points at the arithmetic rather than at the file. Zero nights with a
per-night price is still the `HotelDataError` it always was, and a real
three-night 12,000-a-night award is untouched.

**R3-2 — the export.** The portability tests ask git what is TRACKED, so they
need a repository; in an unpacked export they were four red tests that said
nothing about the reader's tree. They are skipped when there is no `.git`, and
the "`.venv` exists on disk" assertion is gone: the guard is about what git
tracks, not what happens to be beside it. Verified by unpacking `git archive
HEAD` into a clean directory and running it with an interpreter from outside the
tree — 2,075 passed, 17 skipped, nothing failed, 296 files, no symlink, no
`.venv`.

**R3-3 — the number in the message.** `short_number` is now a function in
`config`, used where the printing happens rather than where the reason is built:
the reason shortened `10 ** 400` to "a 401-digit number" and `trip_loader._count`
wrapped it in `{value!r}`, so the reader got the 401 digits anyway — in the CLI
line and in the UI's CANNOT LOAD row. Every message that names a value goes
through it now (`_finite_amount`, `_count`, `_travelers`, and the builder's
`--travelers`, `--hotel NIGHTS` and `--leg CASH_USD`), and a number a person can
read is printed unchanged. It also closed a latent crash on the way:
`float(10 ** 400)` raises `OverflowError`, which `unscoreable_cash_reason` did
not catch, so a huge int in a *money* field escaped the guard entirely.

## Still filed, not fixed

Unchanged and confirmed again: the derived **Score** columns truncate money
exactly as the pre-UI CLI does, and the CLI's surcharge line still loses
`[modeled]` to rich. Both need a round in which CLI output may move.

## Verification

Full suite normally and under `-O` (2,079 / 13 skipped, identical), the same
suite inside an unpacked export with an outside interpreter, all 441 Tester
probes, the four baseline probe directories compared by test id, and Chromium at
1440 px offline and LIVE with no page errors plus the Esc/× focus round trip.
