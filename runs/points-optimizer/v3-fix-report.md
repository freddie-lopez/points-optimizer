# Fix report — the 25 v1/v2/v3 adversarial findings

**Author**: Coder
**Date**: 2026-09-09
**Input**: `docs/test-reports/v1-v3-adversarial.md` (25 findings: 3 Critical,
5 High, 9 Medium, 8 Low), and the 78 executed probes at
`docs/test-reports/adversarial-probes/`.

**Balance used throughout**: **UR = 160,000** (Tsuki's real balance).

---

## Headline

**All 25 findings are fixed. None is disputed. None is unfixed.**

Every probe that asserts a defect is present has flipped from pass to fail,
which was the agreed acceptance criterion.

```
$ python -m pytest -q
512 passed, 9 skipped, 1 warning in 9.83s

$ python -m pytest docs/test-reports/adversarial-probes/ -q
39 failed, 39 passed, 1 warning in 2.10s
```

Before: `470 passed, 9 skipped` / `78 passed`.
After:  `512 passed, 9 skipped` / `39 failed, 39 passed`.

The 470 tests still pass. The 42 new ones are
`tests/test_v1_v3_adversarial_fixes.py` (see "The probes are not a regression
net" below). The 39 probes that still pass are the Tester's own **15 held-up
properties** plus six probes that document behaviour rather than a finding
(`B3b`, `B7b`, `C2`'s 14 parametrised cases, `C4`, `C6`, `C6b`, `E6`) — none of
them is a finding, and none should flip.

`python -O -m pytest -q` also gives `512 passed, 9 skipped`.

---

## Root causes

The Tester said three Criticals collapse into two root causes and that fixing
them independently would leave the fourth hat somewhere. That is right, and it is
how the work was done.

| Root cause | Findings it accounts for | The fix |
|---|---|---|
| **1. "We did not get usable data, so we said there is nothing there."** | **C-1**, H-1, H-2, M-5, M-8, M-9, L-3, L-4 | A sixth `LiveQueryState`, and a parser that stops conflating "read it, nothing there" with "could not read it" |
| **2. "The API's tax figure and the modeled surcharge share one float field."** | **C-2**, M-3, M-4 | `PointsCandidate.cash_surcharge` does one job again; observed taxes get their own fields and an unknown propagates as unknown |
| **3. A missing constraint.** | **C-3** | A real trip-level budget: legs draw from a shared, depleting balance |

Everything else (H-3, H-4, H-5, M-1, M-2, M-6, M-7, L-1, L-2, L-5, L-6, L-7,
L-8) is independent.

---

## Root cause 1 — a failure presented as a finding

### What was actually wrong

`LiveQueryState` had five states and `LiveLegOutcome.__post_init__` forbade
exactly one bad combination: `API_ERROR` carrying no error string. It permitted
`NO_AWARD_SPACE` with `rows_seen == rows_skipped > 0`, which is precisely what v2
was. The reason the type system could not catch it is one layer down:
`parse_availability_row` returned a bare list, so its caller could not tell

* a row it **read**, carrying no available cabin — real evidence of no award
  space, from
* a row it **could not read** — evidence of nothing at all,

and both were counted into one `rows_skipped` integer. The union was announced as
"there is no award to buy on this date".

### The fix

1. **`LiveQueryState.ANSWERED_UNREADABLE`** — the sixth state, for "answered,
   unreadable". `is_a_finding_about_award_space` excludes it, and a new
   `tells_us_nothing_about_award_space` property is the single place the
   provenance counters, `--require-all-live` and the formatter agree on which
   states those are.
2. **`__post_init__` refuses `NO_AWARD_SPACE` whenever `rows_unreadable > 0`.**
   The wrong answer is now unbuildable, not merely discouraged. It is a
   `ValueError`, so it survives `python -O` like the other clauses.
3. **`parse_availability_row_detail` / `parse_pages_detail`** split
   `rows_skipped` into `rows_unreadable` (a defect signal) and
   `rows_without_availability` (a finding). `parse_pages` keeps its 3-tuple
   shape for back-compat and simply cannot express the distinction, which is
   noted in its docstring.
4. **`rows_skipped` is surfaced.** `LiveLegOutcome._skipped_clause()` is appended
   to *every* state's render, and `print_live_banner` prints a run-wide count. It
   was counted, stored on the outcome, and printed nowhere.
5. **`data` is validated as a list of dicts.** `envelope_shape_error()` catches
   the two cheap variants; `_rows_of` returns `[]` rather than a dict or a
   string.

```
$ python -m pytest docs/test-reports/adversarial-probes/test_adv_a.py::test_A1... -q -s
state          : LiveQueryState.ANSWERED_UNREADABLE
rows_seen      : 9
rows_skipped   : 9
awards         : 0
error          : "The response was received and then could not be parsed: a row
                  carries Date='15/01/2027', which is not an ISO date this parser
                  can read."
RENDER         : Seats.aero ANSWERED for SFO->MAD on 2027-01-15 but the response
                 COULD NOT BE PARSED: 9 of 9 rows were unreadable and no award
                 could be built from them. [...] THIS IS NOT A FINDING: nothing
                 whatsoever is known about award space on this leg. It is a
                 PARSER OR SCHEMA failure on our side [...] 9 of 9 rows produced
                 no award: 9 COULD NOT BE PARSED (a row carries
                 Date='15/01/2027', ...).
FAILED test_A1_unparseable_rows_are_reported_as_a_finding_of_no_award_space
```

**The other half matters as much**: a response we *read* that carries no bookable
cabin is still `NO_AWARD_SPACE`, still "THIS IS A FINDING", and now says
"`1 of 1 rows produced no award: 1 carried no bookable cabin`". Pinned by
`test_C1_a_genuinely_empty_response_is_still_a_finding`.

---

## Root cause 2 — taxes and surcharge sharing one float

### What was actually wrong

`cash_surcharge` held two different quantities: a **carrier-imposed surcharge**
(modeled, a property of the metal) and **the API's reported taxes** (observed, a
property of the itinerary). When it could not do the second job it was set to
`0.0` and did neither — and the leg then collected the tier-5 program-policy `$0`
from the modeled table, **a figure for a different quantity**, and scored as
fully known.

### The fix

`PointsCandidate` gained `observed_taxes_usd`, `observed_taxes_known`,
`observed_taxes_reported`, `observed_taxes_amount`, `observed_taxes_currency`,
`observed_taxes_note`, `observed_taxes_are_the_surcharge` and
`taxes_unconvertible`. `cash_surcharge` now means one thing: a **captured carrier
surcharge**.

* `extra_observed_taxes_usd` is the taxes *not already counted inside* the
  surcharge. It is `0.0` when there are none, `0.0` when they already ride as the
  captured surcharge, so **it cannot double count** — which was the trap
  `_taxes_are_the_whole_carrier_cash_figure` was written to avoid, and the reason
  the old code preferred to drop the figure entirely.
* `resolve_leg_surcharge` checks `taxes_unconvertible` **before the table**, and
  returns UNKNOWN. A program-wide no-YQ policy is a statement about YQ/YR and
  does not price a tax the tool cannot convert.
* The floor and the break-even now carry the taxes (M-4).

```
THE C-2 REPRO (adversarial test_A4b), AFTER THE FIX
  verdict              : cash (surcharge unknown)
  surcharge            : UNKNOWN / unknown
  points score         : inf
  points floor         : 500.0
  break-even surcharge : 20.0
  cash                 : 520.0
  margin_usd           : 0.0
  candidate cash_surcharge / captured: 0.0 False
  candidate taxes_unconvertible     : True
  candidate observed taxes          : MXN 9000.0 known: False
```

Before: `POINTS, margin $20.00, verdict_sensitive=False, points_floor_usd=None`,
with the per-leg table showing `$0.00 / modeled / $500.00`. The true cost is
about $950. Now the leg is a floor plus a break-even and the table shows
`UNKNOWN`. The user is told points win only if the unpriced cash is below $20 —
which, at MXN 9,000, it is not.

The self-contradicting provenance paragraph the Tester found (the same string
saying both *"carried as an unknown cash component - not as $0"* and *"it can be
taken as the complete carrier-side cash cost"*) is gone, because
`_taxes_are_the_whole_carrier_cash_figure` no longer returns `scoreable=True` in
that case. The verdict text also names **which** figure is missing — saying "the
carrier surcharge is unknown" when the real gap is an unpriceable tax would
repeat in prose the conflation the fix was about in code.

**M-3** (negative taxes): `convert_taxes` refuses a negative figure as corrupt
data, and `_captured_surcharge` refuses a negative captured amount as a second
fence. The $500 award that scored $137.20 and won its leg now cannot.

**M-4** (the floor): $500.00 → **$532.36**, which is 50,000 points at 1cpp plus
the API's known CAD 44.60. The formatter's label already claimed the taxes were
in there; now they are, and the label adapts when there is no tax figure to add.

> **One existing test was changed, not weakened.**
> `tests/test_live_trip.py::test_a_floor_that_already_loses_gives_a_confident_verdict_not_a_withheld_one`
> asserted `points_floor_usd == 500.0` — it was pinning finding M-4. Its intent
> (a floor that already loses gives a confident verdict) is unchanged and still
> holds at $532.36 > $395. The reason is written into the test.

---

## Root cause 3 — C-3, the balance ceiling at trip level

### What was actually wrong

`evaluate_trip` mapped `evaluate_leg` over the legs with no shared budget. The
ceiling was enforced *within* a leg (`best_plan` refuses a transfer larger than
the balance — the Tester's `test_B4` proves it binds exactly at 160,000) and not
at all *across* legs. Nothing owned the trip-level view.

### The fix

`evaluate_trip` is now two passes:

1. Score every leg independently, exactly as before — "which legs would be points
   wins if each were the only leg".
2. Spend the wallet on them, best value first, **re-running `evaluate_leg`
   against what is actually left**.

Re-running `evaluate_leg` rather than second-guessing it means a leg that can no
longer afford UR but could use MR is re-planned honestly, instead of being
demoted on a subtraction the budget function did itself.

If the unconstrained plan already fits, **nothing is re-scored** and an ordinary
trip is bit-for-bit unchanged. Ordering is greedy by savings-per-point; that is
the standard approximation to a multi-dimensional knapsack and is **not proven
optimal** — the docstring says so. What matters far more is that the answer is
executable, which no ordering can be if the constraint is absent.

`trip_totals(results, wallet=...)` now carries `trip_funding_executable`,
`trip_points_spend`, `trip_legs_demoted_for_balance` and `trip_funding_note`,
emitted by the same call as the headline so they cannot be separated by accident
— exactly as v3 made the margin's provenance ride with it.
`print_trip_totals` prints the constraint **before** the table; `main.py` passes
the wallet and exits **4** (distinct from `--require-all-live`'s 3) if a plan is
not executable.

```
$ python -m pytest ...::test_B3_the_trip_recommends_spending_more_UR_than_Tsuki_has -q -s
verdicts     : ['points', 'points', 'cash (points blocked)']
points_spent : 140000
beat_cash_pct: 14.814814814814813
residue      : {'UR': {'starting': 160000, 'spent': 140000, 'remaining': 20000,
                       'unconstrained': False, 'overdrawn': False}}
FAILED test_B3_the_trip_recommends_spending_more_UR_than_Tsuki_has
```

210,000 UR out of 160,000 → **140,000 out of 160,000, with 20,000 left and
`overdrawn: False`**. The demoted leg says why:

> PAY CASH — THE TRIP RAN OUT OF POINTS. On its own this leg would have been
> funded with 70,000 UR, but the same balance also funds L1, L2, and after those
> there is only UR 20,000 left. A recommendation you cannot execute is not a
> recommendation, so this leg is scored as cash. To move points here instead,
> drop one of L1, L2.

---

## Trip B at 160,000 UR

### Badge mode (`--trip-fixture trip_b_europe.json --balance UR=160000`)

```
│ Pay cash for everything          │ $3,126.11 │
│ Optimizer's recommendation       │ $2,924.11 │
│ Saving                           │   $202.00 │
│ Optimizer beats paying cash by   │ 6.46% - 15.45% │
│ Points spent                     │    28,000 │
│   drawn from                     │ 28,000 UR │
```

**Fundable, comfortably.** 28,000 UR of 160,000, 132,000 left, nothing demoted.
The headline is unchanged from before the fix, which is the correct outcome: C-3
does not bind here.

### Live mode

There is **no network in this sandbox and no committed Trip B snapshot**
(`tests/test_live_trip_b.py` is the 9 skips). Everything below is the committed
real capture `sfo_mad_real.json` replayed through a patched `requests.get`,
re-dated per leg. **No claim is made about live behaviour.**

| Scenario | Result at UR = 160,000 |
|---|---|
| 50,000-pt Aeroplan on all 4 flight legs, CAD taxes | every leg loses to cash; **0 UR spent**; fundable trivially |
| Same, MXN 9,000 taxes (C-2) | all four legs correctly UNKNOWN with `>= $500.00` floors; **0 UR spent** |
| 30,000-pt awards, CAD taxes | B1 and B4 win on points; **60,000 UR of 160,000**, 100,000 left; `executable: True`, nothing demoted |
| 30,000-pt awards, MXN taxes (C-2) | B1 and B4 become `cash (surcharge unknown)` with floors instead of confident wins; **0 UR spent** |

**Is Trip B fundable at all within 160,000 UR? Yes, and it cannot fail to be.**
That is structural, not luck:

* B5, B6, B7 are hotels with no Chase UR partner — they can never spend points.
* B2 ($44 cash) and B3 ($76) can never be beaten by any award of 4,400 / 7,600
  points or more, so they cannot spend points either.
* Only **B1** ($395 cash → 39,500 points-equivalent) and **B4** ($482 → 48,200)
  can ever be points wins at the run's 1cpp yardstick. Their combined ceiling is
  **87,700 UR**, comfortably inside 160,000.

So C-3 would not have cost Tsuki points **on Trip B specifically**. It would have
cost him points on any trip with three or more expensive legs that win on points
— which is what the Tester's three-leg repro is, and what the fix now prevents.

The finding that **does** change Trip B's numbers is C-2: in the MXN scenarios
two legs that previously scored as confident wins are now correctly withheld as
floors plus break-evens. That is the reintroduced v0 bug being caught.

---

## Every finding, with its probe

A probe **flipping** (pass → fail) is the proof the finding is fixed.

| # | Finding | Status | Probe(s) that flipped |
|---|---|---|---|
| **C-1** | Unreadable rows announced as no award space | **fixed** | `A1`, `A2`, `A3`, `F2` |
| **C-2** | Unconvertible tax → $0 → flipped verdict | **fixed** | `A4`, `A4b` |
| **C-3** | No balance ceiling across legs | **fixed** | `B3` |
| **H-1** | Budget failure cached and snapshotted | **fixed** | `C8`, `B7` |
| **H-2** | `hasMore: true`, no cursor, reported complete | **fixed** | `F1`, `F2` |
| **H-3** | HTML export prints unknown as `$0.00` | **fixed** | `C1`, `C1b` |
| **H-4** | `--require-all-live` does not withhold a dead leg | **fixed** | `B1`, `E1` |
| **H-5** | Award scored against the wrong date's cash | **fixed** | `B2`, `B2b` |
| **M-1** | Unreachable surcharge row loses to a generic | **fixed** | `A5` |
| **M-2** | Duplicates resolve by file order; `validate()` never called | **fixed** | `A6`, `D11` |
| **M-3** | Negative tax becomes a cash credit | **fixed** | `D6` |
| **M-4** | Floor drops the API's known tax | **fixed** | `G3` |
| **M-5** | Unattributable award → "no UR partner covers this leg" | **fixed** | `D7` |
| **M-6** | API failure hidden behind a badge | **fixed** | `E2` |
| **M-7** | `--refresh` defeated by the in-memory cache | **fixed** | `B6`, `B6b` |
| **M-8** | `skip: 0` re-requests page one 25 times | **fixed** | `F3` |
| **M-9** | Disk write failure loses a good response | **fixed** | `F4` |
| **L-1** | One leg's currency aborts the whole trip | **fixed** | `C7`, `C7b`, `D9` |
| **L-2** | Badge provenance note asserts an unchecked source | **fixed** | `D8` |
| **L-3** | Absurd mileage accepted verbatim | **fixed** | `C3` |
| **L-4** | Unknown source code becomes the program name | **fixed** | `C5` |
| **L-5** | Duplicated carrier codes announced as ambiguous | **fixed** | `A7`, `G1` |
| **L-6** | `MAX_SPLIT_CURRENCIES > 2` ignored; blamed on balance | **fixed** | `B5`, `D4` |
| **L-7** | Trip B fixture still calls GBP a placeholder | **fixed** | (no probe — see below) |
| **L-8** | Unlocked manifest append; orphaned row | **fixed** | `E4`, `E5` |

**L-7 has no probe.** The Tester reported it as a fixture-text contradiction with
no executed test. It is pinned instead by
`tests/test_v1_v3_adversarial_fixes.py::test_L7_the_trip_b_fixture_no_longer_calls_GBP_a_placeholder`,
which asserts `is_placeholder_rate("GBP") is False`, `needs_confirmation("GBP")
is True`, and that the B7 flag no longer contains the phrase.

### Notes on the ones with a decision in them

**H-2 / M-8.** `_next_page_params` now returns `(params, stall_reason)`, so the
note cannot contradict the payload. `skip` is followed **only when the server's
value moves past what we last sent** — an offset that does not advance is not an
offset scheme, and the module's own rule is that it will not invent one. Verified
both ways: `hasMore` with no cursor → 1 call, `INCOMPLETE`; `skip: 0` → 1 call
(was 25), `INCOMPLETE`; a `skip` that *does* advance is still followed.

**H-5.** The Tester's stated expectation — "`evaluate_leg` selects the cash
option whose `date` matches the candidate's date, or the leg is not scored" — is
implemented. I added one thing beyond it. Making the leg's cash the *shifted*
date's fare would let a Jan-17 award beat a $900 Jan-17 fare and be recommended
even though flying on Jan 15 as planned and paying $395 beats both — the
min-over-window bias moved to the cash side. So `cash_baseline_usd` keeps the
leg's own-date fare, the trip's all-cash total is built from it, and a points
verdict requires beating **both**. On an on-date leg the two comparisons are
identical, so nothing else changes.

```
H-5a  only a Jan-17 award; cash captured for Jan 15 ($395) and Jan 17 ($900)
   date_shifted: True -> 2027-01-17
   cash used   : Jan 17 fare 900.0 | baseline (own date): 395.0
   points score: 532.36176 | verdict: cash
   reason: Pay cash, and fly on the leg's own date. The award is for 2027-01-17
           and it does beat that date's fare ($532.36 vs $900.00) - but flying on
           2027-01-15 as planned and paying cash costs only $395.00, which beats
           both.

H-5b  award on the leg's OWN date (Jan 15, $900); a Jan-17 fare of $400 captured
   cash used   : Jan 15 fare 900.0 | date: 2027-01-15
   points score: 532.36176 | verdict: points | date_shifted: False
```

**M-1 / M-2.** The Tester offered a choice: walk all 16 wildcard patterns, or
have `validate()` reject a row whose shape `_tier_keys` can never produce. I took
the second, because changing the matcher's semantics would move every existing
lookup and the reachable patterns are exactly those whose wildcards form a
*suffix* — a clean, checkable property. `check_structure()` (duplicates,
reachability, ordering, negatives, source, basis) runs in `SurchargeTable.__init__`
on every load; `validate()` keeps the policy checks that need `programs.yaml`
and is now called by `default_table()`, which is what production uses.
`blanket_no_yq_map()` reads `programs.yaml` directly so the surcharge table can
validate itself without importing the ratio machinery.

**M-5 / L-4.** An award with no `Route.Source`, and an award whose source code
is not in the map, are both **unattributable**. `resolve_source` returns `""`
rather than handing a source code onward as a program name, the code is preserved
on `Award.program_source_code`, and a new verdict `cash (award unattributed)`
plus a `PROGRAM_UNATTRIBUTED` reason say what actually happened. The award is
still emitted — it is real availability and dropping it would hide it.

> **A second existing test was changed**:
> `tests/test_seats_client.py::test_an_unmapped_source_is_unknown_not_false`
> asserted `program == "someprogramwehaveneverseen"` — it pinned L-4. Its real
> subject (`ur is None` is a different answer from `False`) is untouched and
> still asserted.

**L-1.** An unpriceable cash option or mandatory fee is now a missing input for
*that leg*, named with the leg id and the currency, and the rest of the trip
still scores. Fixing it exposed a latent hole that had been unreachable from the
CLI: a leg with no priceable cash rendered `$0.00` in the summary table and its
`+inf` winner cost turned the headline into `-inf%`. Both fixed — the cell reads
`UNKNOWN`, and such a leg is excluded from **both** sides of the total with an
explicit `Legs EXCLUDED from both totals` row so it is not dropped silently.

```
│ X1  │ NRT->SFO │  UNKNOWN │  - │ none - not a partner     │ ... │ PAY CASH (no path) │
│ X2  │ SFO->MAD │  $395.00 │ 39,500 │ United MileagePlus @ 1:1 │ ... │ POINTS │
│ Optimizer beats paying cash by                              │ 24.05% │
│ Legs EXCLUDED from both totals because nothing              │ 1 (X1) │
EXIT=0                                        (was: exit 1, whole trip abandoned)
```

**L-6.** `all_plans` now enumerates widths 2..`max_split`. The default is still
2, so default behaviour is unchanged; what changes is that the setting means what
it says, and that a refusal caused by the split cap says so instead of blaming
the balance.

---

## The probes are not a regression net

The Tester put the probes outside `pytest.ini`'s `testpaths` deliberately, so
they "do not have to be maintained as the code is fixed". That is right for their
purpose and it means **nothing in the maintained suite would have pinned any of
these fixes**. All 25 could have silently regressed with 470 tests green.

So `tests/test_v1_v3_adversarial_fixes.py` (42 tests) asserts the *correct*
behaviour for every finding, organised by the three root causes. It includes the
cases the fix must **not** break, which are as important as the fix:

* a genuinely empty response is still `NO_AWARD_SPACE` and still "THIS IS A
  FINDING";
* a convertible tax still scores exactly as before (`532.36176`) and is not
  double counted;
* a `skip` that *does* advance is still followed;
* a trip that fits the balance is completely unchanged, and an unconstrained
  balance still imposes no ceiling;
* R3 still holds under N-way splits;
* the new invariant clause survives `python -O` and `-OO`.

---

## What I could not do, and what I did not touch

* **No network.** Nothing here was executed against Seats.aero. Every "live"
  result in this report is the committed capture replayed through a patched
  `requests.get`. The nine `test_live_trip_b.py` skips remain skips — they are
  gated on committed snapshots that this sandbox cannot produce.
* **Trip B's live numbers are unverified as live numbers.** The four scenarios
  above are replays with a re-dated single row, not four real responses.
* **The greedy trip-budget ordering is not proven optimal.** It is
  savings-per-point, documented as an approximation. A leg demoted by it says so
  and names the legs that were funded first, so the user can override.
* **`_taxes_are_the_whole_carrier_cash_figure` is still a guess**, exactly as v3
  section 11.1 says: it rests on one captured row on one program that happens to
  levy no YQ. C-2 narrows it (it can no longer answer when the tax figure is
  unpriceable) but does not resolve it. Resolving it is still empirical — find a
  live row for a YQ-levying program, compare against a real booking page, write
  the answer down.
* **One thing I noticed and did not fix, because it is not in the 25.** In the
  HTML export a badge award with no tax data has `cash_cost_known=True` and a
  `cash_component` of `0.0`, so it renders `$0.00` — a *silence* rendering as a
  known zero. That is the v0 shape on the `Strategy`/`Award` construction path
  rather than in `export_html`, it is not any of the 25 findings, and fixing it
  properly means deciding what `Award.cash_component_known` should default to for
  a badge, which touches the single-route path the Tester did not attack. Flagged
  rather than done quietly.
* **`test_B7b`** (25 calls when a server returns the *same cursor* forever) still
  passes. The 25-page cap and the `INCOMPLETE` marker handle it, the Tester did
  not raise it as a finding, and M-8 was specifically about `skip`. Left alone.

---

## Files changed

| File | Findings |
|---|---|
| `src/models.py` | C-1, C-2, H-5, C-3, M-4, M-5 (new state, invariant, candidate/result fields, reason codes) |
| `src/seats_client.py` | C-1, H-1, H-2, M-3, M-7, M-8, M-9, L-3, L-4 |
| `src/live_trip.py` | C-1, C-2, H-1, H-4, M-5, M-6, L-2 |
| `src/optimizer.py` | C-2, C-3, H-5, M-3, M-4, M-5, L-1 |
| `src/formatter.py` | C-1, C-2, C-3, H-3, M-4, L-1 |
| `src/surcharge.py` | M-1, M-2, L-5 |
| `src/funding.py` | L-6 |
| `src/response_cache.py` | L-8 |
| `src/main.py` | C-3 (wallet passed to `trip_totals`, exit code 4) |
| `tests/fixtures/trips/trip_b_europe.json` | L-7 |
| `tests/test_v1_v3_adversarial_fixes.py` | **new** — 42 regression tests |
| `tests/test_live_trip.py` | one assertion corrected (M-4) |
| `tests/test_seats_client.py` | one assertion corrected (L-4) |
| `tests/test_live_trip_b.py` | replay client carries the new counters |

No probe was edited.
