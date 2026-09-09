# Adversarial test report — v1, v2 (parser), v3 (live mode)

**Author**: Tester
**Date**: 2026-09-09
**Under attack**: `docs/plans/v1.md`, `docs/plans/v3.md`, the v1 / parser-fix / v3
coder reports, and `src/` as shipped.
**Baseline**: `python -m pytest -q` → **470 passed, 9 skipped**. Unchanged by
this report; nothing was fixed.

**Balance used throughout**: **UR = 160,000** (Tsuki's real balance). Every
feasibility, stranding and residue number below is against that figure, not
against the synthetic `UR=180000` every prior run used.

## How to reproduce everything in this report

The probe suite lives at `docs/test-reports/adversarial-probes/` — deliberately
**outside** `pytest.ini`'s `testpaths`, so it does not run with the normal suite
and does not have to be maintained as the code is fixed.

```
$ cd /home/claude/points-optimizer
$ python -m pytest docs/test-reports/adversarial-probes/ -q -s
78 passed
```

**Every one of those 78 tests asserts that a defect is present.** They pass
today. Each will fail when the corresponding finding is fixed — that is the
intended signal. No finding below is reported without an executed test and
pasted output.

---

## Counts

| Severity | Count |
|---|---:|
| **Critical** | 3 |
| **High** | 5 |
| **Medium** | 9 |
| **Low** | 8 |
| **Total** | **25** |

**Attacked and held up: 15 properties** (§7). The honest split is roughly two
thirds real defects, one third machinery that survived a deliberate attempt to
break it. The stranding rule, the `python -O` invariant, the transfer-date
cliff, and the `UNKNOWN`-is-not-`$0` rule in the terminal output are all sound.

### The single worst thing

**C-1: the project's signature failure mode is present for a third time, in the
exact code written to prevent it.** Nine availability rows that the parser
cannot read produce zero awards, and `query_leg` reports that as
`NO_AWARD_SPACE` — rendered as *"THIS IS A FINDING: the API answered, and the
answer is that there is no award to buy on this date."* The client counts
`rows_skipped = 9` and hands it to `LiveLegOutcome`, which stores it and never
prints it. `LiveLegOutcome.__post_init__` forbids `NO_AWARD_SPACE` with an
error string, but permits `NO_AWARD_SPACE` with `rows_seen == rows_skipped > 0`,
which is precisely what v2 was.

---

# CRITICAL

## C-1 — Rows the parser cannot read are announced as a finding of no award space

**Files**: `src/live_trip.py:220-235` (the `if not awards:` branch),
`src/models.py:715-721` (`render()` for `NO_AWARD_SPACE`),
`src/models.py:670-682` (`__post_init__`, which does not guard this),
`src/seats_client.py:711-721` (`parse_pages` counts `rows_skipped` and drops it).

**Repro**: `test_adv_a.py::test_A1_unparseable_rows_are_reported_as_a_finding_of_no_award_space`
— take the committed real capture, change `Date` to `15/01/2027` (a real date in
a format `date.fromisoformat` rejects), return nine such rows.

```
state          : LiveQueryState.NO_AWARD_SPACE
rows_seen      : 9
rows_skipped   : 9
awards         : 0
error          : ''
RENDER         : Seats.aero returned NO award space for SFO->MAD on 2027-01-15
                 (searched SFO->MAD on 2027-01-15, 9 rows). THIS IS A FINDING:
                 the API answered, and the answer is that there is no award to
                 buy on this date.
```

**Expected**: a response whose rows were all skipped is a *parse* failure. It
must not be `NO_AWARD_SPACE`; at minimum the render must say that 9 of 9 rows
were unreadable.
**Actual**: `rows_skipped` is carried on the outcome and never rendered. The word
"skipped" does not appear in the output. The user is told there is nothing to
buy.

Two cheaper variants of the same hole, both executed:

- `test_A2`: `{"data": {"unexpected": "envelope"}}` — `_rows_of` returns the
  dict, iteration yields key strings, none is a dict, zero awards →
  `NO_AWARD_SPACE`, `THIS IS A FINDING`.
- `test_A3`: `{"data": "no results"}` — `rows_seen` becomes **10**, the
  character count of the string, and the leg reports a finding over ten
  imaginary rows.

`SeatsClient.search()` builds a note saying *"1 of 1 rows produced no award"*,
but that note lands on `outcome.pagination_note`, which `render()` never reads
for the `NO_AWARD_SPACE` case.

## C-2 — An unconvertible tax currency collapses to $0 and can flip a verdict to POINTS

**Files**: `src/live_trip.py:373-375` (`cash_surcharge=... if (scoreable and
award.cash_component_known) else 0.0`), `src/optimizer.py:404-425`
(`_captured_surcharge` returns `None`, so the modeled tier-5 `$0` answers
instead).

The chain: an Aeroplan / United / KrisFlyer award resolves to a tier-5
program-policy `$0`, so `_taxes_are_the_whole_carrier_cash_figure` returns
`scoreable=True`. If `TaxesCurrency` has no configured FX rate,
`cash_component_known` is `False`, so `award_to_candidate` sets
`surcharge_captured=False` **and drops the amount to `0.0`**. `evaluate_leg`
then finds no captured surcharge, falls through to the modeled table, gets the
program-policy `$0`, and scores the leg as fully known.

**Repro**: `test_adv_a.py::test_A4b_unconvertible_taxes_flip_a_verdict_the_wrong_way`
— the real SFO→MAD Aeroplan row with `TaxesCurrency: "MXN"`,
`YTotalTaxes: 900000` (MXN 9,000.00 ≈ $450), against $520 cash.

```
verdict : points
reason  : Points path scores $500.00 vs $520.00 cash.
points  : 500.0 cash: 520.0
```

**Expected**: MXN 9,000 is real cash the tool cannot price. The leg must be a
floor plus a break-even, or a lower bound — never a scored win.
**Actual**: `POINTS`, margin $20.00, `verdict_sensitive=False`,
`points_floor_usd=None`. The true cost is ≈ $950 against $520 cash. The
per-leg table (`test_A4`) shows `Surcharge $0.00 / source modeled / Score points
$500.00`, with no marker of any kind:

```
│ B1 │ SFO->MAD │ $395.00 │ 39,500 │ Air Canada Aeroplan @ 1:1 │ 50,000 │ $0.00 │ modeled │ $500.00 │ $395.00 │ live | unknown │ PAY CASH │
```

The disclosure exists only inside `print_live_leg_detail`'s provenance
paragraph, where it also **contradicts itself** — the same string says both
*"carried as an unknown cash component - not as $0"* and *"it can be taken as the
complete carrier-side cash cost."*

This is exactly the v0 bug (`unknown → $0`) reintroduced by v3 on a new axis.
It is reachable today: before v3, CAD was itself an unconfigured currency.

## C-3 — Every leg funds independently from the full balance, so the trip recommends spending more UR than exists

**Files**: `src/optimizer.py:1081-1098` (`evaluate_trip` maps `evaluate_leg` over
legs with no shared budget), `src/optimizer.py:1115-1190` (`trip_totals` computes
the headline without consulting the residue).

**Repro**: `test_adv_b.py::test_B3_the_trip_recommends_spending_more_UR_than_Tsuki_has`
— three legs, each a 70,000-point United award against $900 cash, wallet
`UR=160,000`.

```
verdicts     : ['points', 'points', 'points']
points_spent : 210000
beat_cash_pct: 22.22222222222222
residue      : {'UR': {'starting': 160000, 'spent': 210000, 'remaining': -50000,
                       'unconstrained': False, 'overdrawn': True}}
```

The totals block prints:

```
│ Optimizer's recommendation      │ $2,100.00 │
│ Saving                          │   $600.00 │
│ Optimizer beats paying cash by  │    22.22% │
```

**Expected**: a recommendation that cannot be executed is not a recommendation.
The headline must be withheld or the plan re-solved under the balance.
**Actual**: the headline is printed as a clean 22.22%. The word "OVERDRAWN"
appears nowhere in the totals block — only in the residue table, which
`main.py:440` prints **after** the headline, as a note in a "Note" column
(`test_B3b`). `--max-stranded-points` and the per-leg ceiling both fire
correctly *within* a leg; there is no ceiling *across* legs.

This is the ceiling the v1 plan called "the most safety-critical rule in the
tool" and it has now been exercised against a real balance for the first time.
It is not enforced at trip level.

---

# HIGH

## H-1 — A budget failure is cached and snapshotted as an empty response, then re-served for six hours as a finding

**Files**: `src/seats_client.py:600-607` (budget break returns with `pages=[]` and
**no exception**), `src/seats_client.py:679-696` (`cache.put` runs
unconditionally), `src/response_cache.py:277-355`.

**Repro**: `test_adv_c.py::test_C8_budget_exhaustion_is_written_to_the_cache_as_an_empty_response`

```
http calls: 0 awards: 0
note      : STOPPED at page 1: the 1000 calls/day Seats.aero budget is exhausted.
            The result below is INCOMPLETE.
cache files: ['c5c963bd...json']
snapshots  : ['adhoc_SFO_MAD_2027-01-15_20260909T0555Z.json']

second run state : LiveQueryState.NO_AWARD_SPACE
second run render: Seats.aero returned NO award space for SFO->MAD on 2027-01-15
                   (searched ..., 0 rows). THIS IS A FINDING: the API answered,
                   and the answer is that there is no award to buy on this date.
```

**Expected**: a request that was never sent has nothing to cache. Nothing should
enter the committed regression corpus.
**Actual**: a zero-page envelope is written to the cache **and archived as a
snapshot** in `tests/fixtures/seats_aero/live_trip_b/`, and for the next six
hours every run gets a cache HIT with zero pages, mapped to a confident finding
of no award space, with zero API calls. The `INCOMPLETE` note is on
`RawSearchResult.pagination_note` for run 1 only; the cached envelope's
`_meta.pagination_note` carries it but `NO_AWARD_SPACE.render()` never reads it.

`apply_live` guards this by checking `_budget_remaining() <= 0` before each leg
(`src/live_trip.py:456-465`), but `query_leg` itself does not
(`test_adv_b.py::test_B7`, same result, zero HTTP calls), and the guard does not
cover exhaustion inside a leg's own pagination.

## H-2 — `hasMore: true` with no cursor is reported as a complete single page

**Files**: `src/seats_client.py:529-533` (`if has_more: return None`, comment says
"let the note record the gap"), `src/seats_client.py:628-648` (the note that does
not record it).

**Repro**: `test_adv_f.py::test_F1_hasMore_true_without_a_cursor_is_reported_as_a_complete_single_page`

```
http calls: 1
note      : single page: the response carried hasMore and none of them indicated
            a further page
```

**Expected**: the payload said `hasMore: true`. The note must say the result is
partial and must contain `INCOMPLETE` (which is the only token `main.py:546`
reddens on).
**Actual**: the note names `hasMore` and then asserts the opposite of what it
said. No `INCOMPLETE`. Two claims in the parser-fix report are false in this
branch: *"If told there are more results with no way to ask for them, it stops
rather than inventing an offset scheme"* (it stops, but silently) and *"Page one
is never returned silently as the whole result set."*

Compounded in `test_F2`: page 1 unparseable + `hasMore: true` →
`NO_AWARD_SPACE`, `THIS IS A FINDING`, `INCOMPLETE` absent from the coverage
note.

## H-3 — The HTML export renders an unknown cash component as `$0.00` and drops every provenance marker

**File**: `src/formatter.py:837-867`.

`Strategy` carries `cash_cost_known`, `cash_cost_note` and `is_lower_bound`
specifically so an unconvertible tax cannot be totalled as zero
(`src/models.py:441-447`). `export_html` reads none of them.

**Repro**: `test_adv_c.py::test_C1_html_export_prints_an_unknown_cash_component_as_zero_dollars`

```html
<tr><td>1</td><td>Air Canada Aeroplan</td><td>2027-01-15</td><td>Y</td>
<td>50,000</td><td>$0.00</td><td>$500.00</td></tr>
```

**Expected**: the v1 non-negotiable is *"`unknown` never renders as `$0` — output,
totals, HTML export."*
**Actual**: `$0.00` in the Cash Cost column and a clean `$500.00` total, with no
"unknown", no "lower bound", no "MXN", and no `9,000`. `test_C1b` shows the same
file omits `google_badge_unverified` and the string `UNVERIFIED` entirely, so an
unverified Google badge exports as a plain number.

## H-4 — `margin_provenance` reads `live` when a leg's API call failed, so `--require-all-live` does not withhold

**Files**: `src/live_trip.py:677-685` (`scoreable = [r for r in flights if
r.leg.points_candidates]`; `live` when `len(live) == len(scoreable)`),
`src/main.py:435`.

A leg whose API call failed and which carries no fixture badge has **no**
`points_candidates`, so it is not in `scoreable` and cannot make the run
"mixed".

**Repro**: `test_adv_b.py::test_B1...` and `test_adv_e.py::test_E1_require_all_live_passes_a_run_with_a_dead_leg`

```
margin_provenance: live
legs_api_error   : ['L2']
would --require-all-live withhold? False
```

**Expected**: `--require-all-live` exists for "the moment someone wants to quote a
number publicly." A run containing a leg that never answered is not that moment.
**Actual**: `withheld = require_all_live and margin_provenance != "live"` is
`False`; the percentage prints, `WITHHELD` never appears. The prose note *does*
say "1 of 2 flight legs", so a careful reader is warned — but the machine-readable
gate that exists to stop the number being quoted does not fire.

## H-5 — A leg is scored against the cheapest cash option across all dates, not the cash option for the award's own date

**Files**: `src/optimizer.py:587-616` (`priced_cash.sort(); result.best_cash =
priced_cash[0]` — `CashOption.date` is never consulted), `src/live_trip.py:412-418`
and `527-540` (promotion by date).

v3 §4.5 refuses to score off-date awards *specifically* to avoid a bias toward
points, and offers one escape hatch: capture the cash fare for that date. The
escape hatch does not work, because the scorer ignores the date it just matched on.

**Repro A** — `test_adv_b.py::test_B2_promoted_off_date_award_is_scored_against_the_cheapest_date_not_its_own`.
Cash captured for Jan 15 ($395) and Jan 17 ($900); only a Jan-17 award returns,
so it is promoted and `DATE_SHIFTED` is recorded:

```
date_shifted   : True -> 2027-01-17
cash used      : Jan 15 fare 395.0
points score   : 532.36
verdict        : cash
```

The award is for Jan 17. It is scored against the Jan-15 fare.

**Repro B** — `test_B2b`, prices swapped. A Jan-15 award (cash $900) is scored
against a Jan-17 fare of $400 that it cannot be booked with, and
`leg.date_shifted` is `False`, so **nothing in the output says a date was mixed**.

**Expected**: `evaluate_leg` selects the cash option whose `date` matches the
candidate's date, or the leg is not scored.
**Actual**: `min()` over every captured price regardless of date. This does not
need `--flex-days` — any fixture carrying cash for more than one date is
affected, and v3 added `CashOption.date` precisely to encourage that.

---

# MEDIUM

## M-1 — A more-specific surcharge row is unreachable and a less-specific row answers instead

**Files**: `src/surcharge.py:148-167` (`_tier_keys` enumerates 5 of 16 wildcard
patterns) vs `src/surcharge.py:72-88` (`specificity`, which happily ranks rows the
matcher can never reach).

**Repro**: `test_adv_a.py::test_A5_a_more_specific_row_is_unreachable_and_a_less_specific_row_wins`

```
matched_rule: BAX / metal=BA / * / cabin=* / dep=*
amount      : $40-$60 (pt $50)
specificity of the ex-GB row: 3
specificity of the generic  : 4
```

A row `(BAX, BA, NA-EU, *, GB)` — the natural shape for "UK APD applies in any
cabin" — is tier 3 by the table's own `specificity` property and is never
consulted. The tier-4 generic `(BAX, BA, *, *, *)` answers with **$50 instead of
$900**. `validate()` accepts the unreachable row without comment.

**Expected**: either the matcher walks all patterns, or `validate()` rejects a row
whose key shape can never be produced by `_tier_keys`.
**Actual**: silent, and in the dangerous direction — a cheaper number wins.

## M-2 — Duplicate surcharge rows resolve by file order at runtime; `validate()` is never called outside tests

**Files**: `src/surcharge.py:178` (`index = {rule.key: rule for rule in self.rules}`
— last row wins), `src/surcharge.py:386` (`validate` is defined and never called).

**Repro**: `test_adv_a.py::test_A6_duplicate_rows_at_one_tier_resolve_by_file_order_not_by_error`
and `test_adv_d.py::test_D11_surcharge_table_validate_is_never_called_at_load_time`

```
resolved to: 900.0 notes: second      # reversing the two lines gives 100.0
actual .validate() call sites in src/: (none)
```

**Expected**: `validate()` raises `SurchargeTableError` on a duplicate at one tier —
"Two rows matching one lookup means the answer depends on file order." It is
right, and it never runs in production.
**Actual**: `SurchargeTable.__init__` and `default_table()` never validate. A
corrupt production CSV — a duplicate, a negative amount, a future `verified_on`,
a `carrier=*` row for a YQ-levying program — is caught only by
`tests/test_surcharge.py`, never by a run.

## M-3 — A negative `{X}TotalTaxes` becomes a negative *captured* surcharge and makes points cheaper

**Files**: `src/seats_client.py:245-311` (`convert_taxes` has no sign check),
`src/seats_client.py:423`, `src/live_trip.py:373`.

**Repro**: `test_adv_d.py::test_D6_negative_taxes_become_a_cash_CREDIT_on_the_points_side`
— the real Aeroplan row with `YTotalTaxes: -50000` (CAD −500.00).

```
cash_surcharge: -362.8 captured: True
surcharge: $-362.80 | points score: 137.2 | cash: 395.0

│ L1 │ SFO->MAD │ $395.00 │ 39,500 │ Air Canada Aeroplan @ 1:1 │ 50,000 │ $-362.80 │ captured │ $137.20 │ $395.00 │ live | unknown │ POINTS │
```

**Expected**: `SurchargeTable.validate()` rejects `amount_low < 0` for a table row
(`src/surcharge.py:418`). A captured surcharge from live data bypasses that
check entirely.
**Actual**: a $500 award is scored at $137.20 and wins the leg, with provenance
`captured` — the strongest confidence tier in the model.

## M-4 — On the unscoreable live path the API's own known tax is dropped, and the printed floor claims otherwise

**Files**: `src/live_trip.py:373` (`cash_surcharge=0.0` whenever `scoreable` is
False), `src/formatter.py:534`.

**Repro**: `test_adv_g.py::test_G3_the_api_tax_figure_is_dropped_entirely_on_the_unscoreable_path`
— the real capture re-sourced to `flyingblue` with a single `AF` carrier, so the
surcharge is UNKNOWN and the leg takes the floor + break-even path.

```
candidate cash_surcharge: 0.0
floor                    : 500.0
50,000 pts @1cpp         : 500.00   + known CAD 44.60 = 532.36
```

The formatter prints:

```
floor $500.00  (points at the run's valuation + the API's taxes, with the
carrier surcharge at its $0 floor - the least this can possibly cost)
```

**Expected**: the floor is *"the least this can possibly cost"*, and the API's
CAD 44.60 → $32.36 is a **known** cost. The floor should be $532.36.
**Actual**: $500.00, and the label asserts the taxes are included when they were
zeroed two files earlier. Step 8's acceptance criterion is met in form and
violated in value. This is v3 §11.1's rule doing more damage than the plan
anticipated: the rule says "do not *add* the modeled surcharge", but the code
drops the *observed* tax instead.

## M-5 — An award whose response named no program is reported as "No UR transfer partner covers this leg"

**Files**: `src/seats_client.py:349-352` (`route = row.get("Route") or {}`;
`resolve_source(None)` returns `("", None, note)`), `src/optimizer.py:640-655`,
`src/optimizer.py:926-931`.

**Repro**: `test_adv_d.py::test_D7_an_award_with_no_program_reaches_the_leg_as_a_nameless_candidate`

```
label  : LIVE  L1 Y 50,000 pts (9 seats)
program: ''
verdict: cash (no points path)
warnings: ["LIVE  L1 Y 50,000 pts (9 seats):  is not a transfer partner of any
           currency you hold (UR) - no points path."]
```

`verdict_reason` reads *"No UR transfer partner covers this leg. Cash is the only
option."*

**Expected**: a response that named no program is a data failure. The user must be
told the response was unattributable, not told a fact about Chase's partner list.
**Actual**: a definitive statement about partnerships, generated from a missing
field, with an empty program name rendered mid-sentence. This is the same
failure-as-finding shape as C-1, one layer up.

## M-6 — An API failure hidden behind a badge never reaches the leg's verdict or warnings

**File**: `src/live_trip.py:616-641` (`annotate_live_verdicts` returns early when
`r.leg.points_candidates` is non-empty).

**Repro**: `test_adv_e.py::test_E2_an_api_failure_hidden_behind_a_badge_is_never_stated_in_the_verdict`

```
state    : LiveQueryState.API_ERROR
provenance: PointsProvenance.BADGE_FALLBACK
verdict  : cash (surcharge unknown)
reason   : A points path exists (British Airways Executive Club, 23,000 points)
           but the carrier-imposed surcharge on this metal is UNKNOWN...
warnings containing the error: []
```

**Expected**: the leg's warnings should carry `outcome.render()` — the API-failure
text — as they do when the badge is absent.
**Actual**: the failure appears only in `print_live_leg_detail`. The verdict and
its reason are indistinguishable from a clean badge-only run. Given
`allow_badge_fallback` defaults to `True`, this is the *common* case for a failing
leg on a fixture that has badges — i.e. Trip B.

## M-7 — `--refresh` is defeated by the in-memory class cache, which also erases the live fetched-at timestamp

**File**: `src/seats_client.py:758-762` (`if cache_key in self.CACHE: return ...`,
placed **before** `search_raw`, where `refresh` is handled).

**Repro**: `test_adv_b.py::test_B6_refresh_is_defeated_by_the_in_memory_cache`

```
http calls: 1 (after first: 1 )
cost seen with --refresh: 50000        # upstream had changed to 25000
note: served from cache; no API call made
```

and `test_B6b`:

```
first fetched_at : 2026-09-09 05:53:48+00:00
second fetched_at: None
```

**Expected**: `--refresh` forces a re-fetch (v3 Step 1 acceptance). A live
candidate never renders without its fetched-at timestamp (v3 §8 honesty
invariant).
**Actual**: within one process `--refresh` is a no-op and returns the stale
price. On the in-memory hit `last_fetched_at` is `None` and
`last_served_from_cache` is `False`, so `award_to_candidate` writes
*"Fetched during this run"* onto bytes that were not fetched during this run,
and the outcome claims it was not served from a cache.

## M-8 — `skip: 0` with `hasMore: true` re-requests page one 25 times

**File**: `src/seats_client.py:527-528` — `if has_more and skip is not None and
_as_int(skip) is not None: return {"skip": str(_as_int(skip))}`. The skip value is
echoed back unchanged rather than advanced.

**Repro**: `test_adv_f.py::test_F3_hasMore_with_a_skip_field_of_zero_also_stops_silently`

```
calls: 25 note: STOPPED at the 25-page safety cap while the API was still
reporting more results. The result below is INCOMPLETE.
```

**Expected**: an offset scheme advances, or the client stops after one page.
**Actual**: 25 identical requests — 2.5% of the 1,000/day budget on one leg, for
one page of data, duplicated 25 times into `pages`. A four-leg `--live` run
against a server that returns `skip` would spend 100 calls. The `INCOMPLETE`
marker is correct here, which is why this is Medium and not High.

## M-9 — A disk write failure turns a successful API call into "COULD NOT BE REACHED"

**File**: `src/seats_client.py:679-696` — `cache.put()` runs after
`_count_call()` and after a successful parse, outside any `try`.

**Repro**: `test_adv_f.py::test_F4_enospc_during_the_cache_write_loses_a_successful_response`
(`Path.write_text` patched to raise `OSError(ENOSPC)`).

```
budget spent: 1
http calls  : 1
state       : LiveQueryState.API_ERROR
error       : UNEXPECTED OSError: [Errno 28] No space left on device...
render      : Seats.aero COULD NOT BE REACHED for SFO->MAD on 2027-01-15 ...
```

**Expected**: the response arrived and parsed. A failure to archive it is a
warning; the awards should still be returned.
**Actual**: the awards are discarded, a call is spent, and the run reports the
API was unreachable. The direction is safe (an error, not a finding) but the
message is false and the evidence is lost. `ResponseCache` already has a
`warnings` list for exactly this class of problem and `put()` does not use it.

---

# LOW

## L-1 — A cash price in an unconfigured currency aborts the whole trip, naming no leg
`src/optimizer.py:589`, `src/config.py:259-266`.
`test_adv_c.py::test_C7`, `test_adv_d.py::test_D9`. Exit code 1, message
`Error: No FX rate configured for 'JPY'. Add it to config.FX_RATES_TO_USD.` —
clean, but it names no leg, and one leg's currency kills six other legs' results.
v3 §9 requires "no unhandled exception on the malformed-input matrix"; this is
handled at `main()` but only as a whole-run abort.

## L-2 — The `badge` provenance note asserts a source it has not checked
`src/live_trip.py:701-706`. `test_adv_d.py::test_D8`. A run without `--live`
always prints *"Every points price here is a Google Flights badge, of which
exactly one has ever been corroborated"* — even for a candidate whose own
`source` is `manual_capture` or `seats_aero`. Observed on the C-3 repro, whose
three candidates are all `seats_aero`.

## L-3 — An absurd mileage price is accepted verbatim
`test_adv_c.py::test_C3`. `YMileageCost: "99999999999999"` becomes
`Award.cost = 99999999999999` with no plausibility flag. `_as_int` correctly
returns `None` for `"fifty thousand"` and the `cost <= 0` fence correctly rejects
`-50000` — but there is no upper fence.

## L-4 — An unknown Seats.aero source code becomes the program name
`src/seats_client.py:157-180`. `test_adv_c.py::test_C5`. Source
`"hawaiianairlines"` yields `Award.program == "hawaiianairlines"`, which
`normalize_program` passes through unchanged and the formatter prints as if it
were a program. `ur_transferable=None` is correctly preserved.

## L-5 — Duplicated or padded carrier codes are announced as ambiguous metal
`src/surcharge.py:320-360` — `codes` is not deduplicated.
`test_adv_a.py::test_A7`. `["BA", "BA"]` and `[" ba ", "BA"]` both take the
multi-carrier branch and append *"OPERATING METAL AMBIGUOUS: Seats.aero listed
BA, BA as possible operating carriers"*. The figure is right; the disclosure is
false. `test_adv_g.py::test_G1` also shows `[None]` becoming a carrier code
literally named `NONE` in the unknown-reason text.

## L-6 — `MAX_SPLIT_CURRENCIES > 2` is silently ignored, and a split-capped award is blamed on the balance
`src/funding.py:290` — `permutations(sources, 2)` regardless of `max_split`.
`test_adv_b.py::test_B5`, `test_adv_d.py::test_D4`. With
`UR=160,000 + MR=50,000 + TY=50,000` (260,000 available) and a 250,000-point
award, `best_plan` returns `None` and `infeasible_reason` reads
`"Needs 250,000 UR but balance is 160,000"` — a balance claim for what is
actually an `R4` policy refusal. This is the "explanation bug hides a behaviour
bug" pattern v1 §5 warns about.

## L-7 — The Trip B fixture still says GBP is an unverified placeholder
`tests/fixtures/trips/trip_b_europe.json:363`. Printed verbatim on every Trip B
run: *"the GBP/USD rate in use is an unverified placeholder. This is the least
reliable number in the run."* v3 Step 0 made GBP `sourced`, so
`is_placeholder_rate("GBP")` is `False` and `rests_on_placeholder_fx` is no
longer set for B7 — the structured marker and the free-text flag now contradict
each other, and the free-text one is wrong.

## L-8 — Manifest bookkeeping: unlocked append, and an interrupted run's row can be filled by a later run
`src/response_cache.py:415-471`. `test_adv_e.py::test_E4`, `test_E5`.
`_append_manifest` uses a plain unlocked `open(path, "a")`.
`annotate_manifest` walks backwards for the last unfilled row naming a snapshot;
because a byte-identical re-fetch reuses the snapshot **name**, a run that dies
between `put` and `annotate` leaves a permanently `- | -` row, and the next
identical fetch's state is written onto its own row while the orphan stays blank
forever. Sequential, uninterrupted runs are correct.

---

# Held up under attack

Fifteen properties I tried to break and could not. Listing them because a padded
finding list is worth less than an honest one.

| # | Property | Evidence |
|---|---|---|
| 1 | `LiveLegOutcome`'s invariant survives `python -O` and `-OO` | `test_adv_b.py::test_B8` — `RAISED` under all three flag sets. The coder's conversion from `assert` to `raise ValueError` is correct. |
| 2 | `Reason` code validation also survives `-O` | `test_adv_d.py::test_D10` |
| 3 | **R2**: a two-currency split at 160,000 UR + 50,000 MR strands one block, not two | `test_adv_d.py::test_D1` — need 200,500 → `41,000 MR + 160,000 UR`, delivered 201,000, **stranded 500** ≤ 999 |
| 4 | **R3**: no split is proposed when one currency suffices | `test_adv_d.py::test_D2` — `[('50,000 UR', 1), ('50,000 MR', 1)]` |
| 5 | `--max-stranded-points 0` still rejects a real overshoot | `test_adv_d.py::test_D3` — 57,300 pts: strict `None`, loose strands 700 |
| 6 | The balance ceiling binds exactly at Tsuki's number | `test_adv_b.py::test_B4` — 160,000 feasible, 160,001 `None`, 159,001 → 160,000 UR / 999 stranded |
| 7 | The Hyatt Oct-1 cliff is keyed on the **transfer** date and correct on both sides and the exact day | `test_adv_b.py::test_B9` — 2026-09-30 → 45,000 UR; 2026-10-01 → 60,000; 2026-10-02 → 60,000 |
| 8 | `VERDICT_SENSITIVE` fires on every flip, **including both exact boundaries** | `test_adv_c.py::test_C9`, 5 cases. Band $980/$1,130/$1,330: cash $1,000 → sensitive; cash == high ($1,330) → sensitive; cash == low ($980) → not (points never strictly win); outside the band → not. |
| 9 | `UNKNOWN` never renders as `$0.00` in the per-leg table or the live detail block | `test_adv_c.py::test_C10`, `test_adv_g.py::test_G2` |
| 10 | **`JMileageCostRaw: 470500` provably cannot surface** — it is not even retained, because `raw_diagnostics` is built per emitted cabin and J is never emitted | `test_adv_c.py::test_C6` — neither `470500` nor `5590` appears anywhere in the Award objects. (The parser-fix report's claim that Raw values *are* retained is therefore wrong for a skipped cabin — in the safe direction.) |
| 11 | Negative, null and non-numeric mileage are skipped, never priced at 0 | `test_adv_c.py::test_C2`, 14 mutations, none raised |
| 12 | A corrupt cache file is a miss with a warning — never a crash, never an empty result | `test_adv_b.py::test_B6d` |
| 13 | Cache TTL boundary is sane (`age == ttl` hits, `ttl+1` misses) and expired files are kept | `test_adv_b.py::test_B6c` |
| 14 | Snapshot dedup by content hash; two different responses for one leg inside one minute get distinct files via a hash suffix | `test_adv_e.py::test_E4` |
| 15 | The percentage and its provenance line are always emitted by the same call and are adjacent | `test_adv_b.py::test_B1`, `test_B3` renders |

Also confirmed sound, incidentally: the cache key changes with every request
parameter and with the flex window (so no cross-window collisions); a
`--flex-days 3` query is still one HTTP call; a program-policy `$0` resolves for
garbage carrier codes, which is correct by design (`v1` coder report §6.2) since
tier-5 rows are metal-independent.

---

# Notes for whoever fixes this

Three of the findings are one bug wearing three hats, and fixing them
independently will leave the fourth hat somewhere:

- **C-1, H-1, M-5** are all *"we did not get usable data, so we said there is
  nothing there."* The type system was built to stop this and stops exactly one
  variant (`API_ERROR` carrying an error string). It does not stop
  `NO_AWARD_SPACE` with `rows_skipped == rows_seen`, with a zero-page cached
  envelope, or with an unattributable award. `LiveQueryState` needs a sixth
  state for "answered, unreadable", and `__post_init__` needs to forbid
  `NO_AWARD_SPACE` when `rows_skipped == rows_seen > 0` or when
  `pages == []` came from a budget break.
- **C-2, M-3, M-4** are all *"the API's tax figure and the modeled surcharge share
  one float field."* `PointsCandidate.cash_surcharge` is doing two jobs and is
  zeroed whenever the second job cannot be done. Splitting observed taxes from
  the modeled surcharge would close all three.
- **C-3** is the only finding that has nothing to do with provenance. It is a
  missing constraint, and it is the one that would have cost Tsuki money the
  first time he ran the tool against his real balance on a trip with two winning
  legs.

Nothing in this report was fixed. The 470-test suite is untouched and still green.
