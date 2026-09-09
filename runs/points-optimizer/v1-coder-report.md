# v1 Coder report — points optimizer

**Author**: Coder
**Date**: 2026-09-08
**Plan**: `docs/plans/v1.md`
**Baseline**: v0, 118 tests passing
**Now**: **345 tests passing, 0 failing.** All 118 v0 tests still present; 7 changed,
each justified in §5 below.

---

## 0. Headline

Re-scoring Trip B, the real January 2027 Europe trip, with the surcharge model in
place:

| | v0 | v1 |
|---|---|---|
| Trip headline | **16%** (single number) | **6.55% – 15.66%** (range) |
| Legs won by points | 3 (B1, B3, B4) | 1 (B4) |
| B1 MRY→MAD | POINTS, BA at $0 surcharge | **PAY CASH** — BA unscoreable, Aeroplan scoreable and loses |
| B3 AMS→LON | POINTS, KLM at $0 surcharge | **WITHHELD (surcharge unknown)**, break-even $36.00 |
| B4 LON→MRY | POINTS, BA at $0 surcharge, "$282 saving" | **POINTS** — won by **United at a confirmed $0**, $202.00 saving |

The 6.55% low end is what the tool can defend. The 15.66% high end is what you get
**only if every unknown British Airways surcharge turns out to be zero** — which is
exactly the assumption that produced v0's 16%. The old headline sits at the top of
the honest range and cannot be quoted as the answer. The architect predicted
"probably ~7%"; the defensible figure is 6.55%.

Trip A is unchanged at **0.00%** — cash wins both legs, which was already correct.

---

## 1. Steps completed

Worked in plan order. Each row states what was run and what came back.

| Step | Status | Verified by |
|---|---|---|
| 0 — Seats.aero gate | **NOT DONE — blocked** | See §2. Cannot be done from here. |
| 1 — Transfer date ≠ travel date | Done | `pytest tests/test_transfer_date.py` → 9 passed |
| 2 — Runtime wallet | Done | `pytest tests/test_wallet.py` → 30 passed |
| 3 — Multi-currency ratio data | **Schema done, DATA NOT DONE** | See §3. `pytest tests/test_ratio_table.py` → 65 passed |
| 4 — Per-currency valuation, no cross-currency sum | Done | `pytest tests/test_funding.py` → 22 passed |
| 5 — Funding plans, splits, stranding | Done | same file; R2 trap pinned |
| 6 — Airports / carriers / regions | Done | `pytest tests/test_regions.py` → 19 passed |
| 7 — Surcharge model | Done | `pytest tests/test_surcharge.py` → 32 passed |
| 8 — Wire surcharges into leg evaluation | Done | `pytest tests/test_acceptance.py` → 11 passed |
| 9 — Alternatives | Done, **with a plan correction** | `pytest tests/test_alternatives.py` → 12 passed. See §6.1 |
| 10 — Hotels | Done | `pytest tests/test_hotels.py` → 25 passed |
| 11 — Formatter, CLI, README | Done | `python -m src.main --help`; full fixture runs below |
| 12 — FX honesty | Done | `pytest tests/test_fx_and_flagship.py` → 13 passed |
| 13 — Flagship acceptance case | Done | same file; `trip_c_lon_mry_surcharge.json` |

### Full suite — real output

```
$ python -m pytest -q
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-7.4.0, pluggy-1.6.0
rootdir: /home/claude/points-optimizer
configfile: pytest.ini
testpaths: tests
plugins: anyio-4.13.0
collected 345 items

tests/test_acceptance.py ...........                                     [  3%]
tests/test_adversarial.py .............                                  [  6%]
tests/test_alternatives.py ............                                  [ 10%]
tests/test_bugfixes.py ....................                              [ 16%]
tests/test_cash_fallback.py ..............                               [ 20%]
tests/test_funding.py ......................                             [ 26%]
tests/test_fx_and_flagship.py .............                              [ 30%]
tests/test_hotels.py .........................                           [ 37%]
tests/test_invariants.py ......................................          [ 48%]
tests/test_optimizer.py ........                                         [ 51%]
tests/test_ratio_table.py .............................................. [ 64%]
...................                                                      [ 69%]
tests/test_ratios.py .........                                           [ 72%]
tests/test_regions.py ...................                                [ 77%]
tests/test_seats_client.py .....                                         [ 79%]
tests/test_surcharge.py ................................                 [ 88%]
tests/test_transfer_date.py .........                                    [ 91%]
tests/test_wallet.py ..............................                      [100%]

=============================== warnings summary ===============================
../../../usr/local/lib/python3.11/dist-packages/requests/__init__.py:109

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================== 345 passed, 1 warning in 5.87s ========================
```

The one warning is a pre-existing urllib3/chardet version mismatch in the
environment's `requests` install. It predates v1 and is unrelated to this code.

---

## 2. Step 0 is NOT done, and could not be

The build environment has **no outbound network egress at all**. Not just
seats.aero:

```
$ curl -sS -o /dev/null -w "%{http_code}\n" https://www.google.com
curl: (56) CONNECT tunnel failed, response 403
$ curl -sS -o /dev/null -w "%{http_code}\n" https://seats.aero
curl: (56) CONNECT tunnel failed, response 403
```

Nothing was routed around. The gate stays closed and **every live number the tool
produces is still unverified**. The endpoint, the `Partner-Authorization` header,
and every response field name (`Date`, `Source`, `cost`, `taxes`, `Carriers`,
`seats_available`) remain the blind corrections made in v0.

`tests/fixtures/seats_aero/README.md` records exactly what to check and what to
write down, and `tests/test_adversarial.py` asserts the UNVERIFIED banner is still
on the client and that no recorded response is being claimed. If someone records
one, that test fails on purpose so the findings get written up.

**Two unknowns from the spec still matter more than anything else in v1:**
whether the API returns real per-flight taxes (if so, `data/surcharges.csv`
demotes to a fallback and §4.3 of the plan shrinks a lot), and whether it
distinguishes operating from marketing carrier (if not, the surcharge model
degrades to `unknown` on most live results).

---

## 3. Step 3: the machinery shipped, the data did not

`data/ratios.csv` gained `source` and `verified_on` columns and every existing UR
row was backfilled. It contains **no MR / TY / C1 / Bilt rows.**

No partner list could be verified from a machine with no network. The v0 standard
— *a partner that cannot be verified is omitted, not guessed* — is the standard
that caught Marriott at 3:1, IHG at 2:1 and a JAL partnership that does not exist,
and I did not lower it to fill the table from memory.

What did ship:

- MR / TY / C1 / Bilt are **declared as issuer currencies** in `programs.yaml`, so
  a wallet can name them and validation accepts them, with
  `partners_verified: false` and a note saying why.
- The whole multi-currency engine (per-currency valuation, two-currency splits,
  R1–R4, residue) is built and unit-tested against
  `tests/fixtures/ratios_multicurrency_test.csv`, every row of which is labelled
  SYNTHETIC.
- `tests/test_ratio_table.py::test_other_issuers_have_no_verified_partner_rows`
  pins the gap. It fails the moment someone adds rows — which is fine and expected
  — and the tests either side of it force every new row to carry a source and a
  `verified_on` inside 90 days.
- The CLI prints a banner when a wallet holds a currency with no verified rows,
  saying it is a **data gap, not a finding about your account**.

This matches the plan's own §10 risk ("if it must be cut, cut multi-currency") and
its §9.1 open question. Practically, this costs nothing today: Tsuki has confirmed
only a Chase Sapphire Preferred, so with a UR-only wallet the missing rows change
no output at all.

---

## 4. The two bugs (priority 1)

### 4.1 Ratio lookup keyed on travel date

`evaluate_leg` looked ratios up on `leg.date`. Fixed: `--transfer-date` (default
today) drives every ratio and bonus lookup; travel date is used only for
availability and pricing.

Pinned by `tests/test_transfer_date.py`. The live case: a January 2027 Hyatt stay
of 45,000 points on a Sapphire Preferred wallet.

- transfer 2026-09-15 → **1:1**, costs **45,000 UR**
- transfer 2026-10-02 → **4:3**, costs **60,000 UR**
- moving the *travel* date does not move the ratio at all

`TRANSFER_DATE_WINDOW` fires before the cliff and names it:

> `UR -> World of Hyatt is 1:1 on 2026-09-15 but 4:3 from 2026-10-01. The ratio
> that applies is the one in force WHEN YOU TRANSFER, not when you fly.`

The window search walks ratio-row **boundaries**, not days, so it is a handful of
comparisons rather than a 365-day scan.

### 4.2 `total_points_cost` summing across currencies

Two changes:

- `TransferPath.total_points_cost` is **out of every ranking path**. Its docstring
  now says so explicitly. It is retained only for single-currency display, which
  is all it was ever faithful for.
- `FundingPlan` (`src/funding.py`) is the new ranking unit. It ranks on
  `score_usd` — each currency's points at that currency's own cents-per-point,
  summed in dollars — and reports `per_currency_spend` separately. There is no
  cross-currency points integer anywhere in the ranking path.
- `find_transfer_paths` (kept as the v0 API for its 20 bug-fix tests) had its sort
  key changed from `total_points_cost` to the same per-currency USD score.

Every currency defaults to the same 1cpp yardstick v0 used, so default behaviour is
numerically unchanged. That is not asserted, it is **proved**:
`test_single_currency_matches_v0_exactly` runs six cases through both the v0
function and `funding.all_plans` and compares transferred amount, stranding,
delivered points and score.

---

## 5. Tests that changed (7), each with a justification

The plan requires these to be listed. None was deleted.

**`tests/test_ratio_table.py::test_schema_can_still_hold_other_issuers`** — the
CSV header assertion gained `source` and `verified_on`. Step 3 requires those
columns. The `from_program` column is untouched, so the schema still holds any
issuer, which is what the test is actually about.

**Five tests in `tests/test_cash_fallback.py`** —
`test_points_and_cash_reported_separately_and_combined`,
`test_cash_wins_is_reported_as_pay_cash`, `test_points_win_is_reported_as_points`,
`test_trip_totals_beat_cash_percentage`, `test_valuation_cpp_changes_the_verdict`.
Not one assertion changed. The shared `make_leg` helper now sets
`surcharge_captured=True` with a `$0` surcharge. These tests are about the 1cpp
yardstick and the verdict logic; the surcharge is not their subject. In v0, silence
about a surcharge meant $0, so the helper did not have to say anything. In v1
silence means UNKNOWN, so the helper now **declares the zero it always assumed**.
`surcharge_captured=True` is the "genuinely surcharge-free fare read off a real
page" case, which is a real known zero and is deliberately distinguishable from an
unknown. The unknown path has its own tests.

**`tests/test_acceptance.py::test_trip_b_real`** — rewritten. This one *must*
change: it encoded the wrong answer. v0 asserted points won B1, B3 and B4. Two of
those wins were bought with a British Airways surcharge silently modelled as $0.
The new test asserts the corrected verdicts and, importantly, that the unscoreable
BA candidate is still **reported** rather than silently dropped.

**`tests/test_acceptance.py::test_balance_constraint_binds_when_supplied`** — the
counts changed from 4/1 to 2/0 because `has_points_path` now means "a path that can
be **scored**". The thing the test exists to prove — that the balance ceiling binds
— is now asserted on `funding_plan` instead, which is what actually tracks
fundability. Both are checked.

---

## 6. Deviations from the plan

Three, all forced by something found in the code or the data. Everything else was
built as specified.

### 6.1 Steps 9 and 13 ask for something that cannot exist

Both steps ask for **"Aeroplan and United alternatives on a BA-metal candidate"**.
That contradicts the plan's own §4.7, which defines an alternative as *another
program that can ticket the same metal*. **Aeroplan and United are Star Alliance;
British Airways is oneworld.** Neither can issue an award on a BA aeroplane.

What is really going on in the real B4 leg is a different comparison: LHR→SFO has a
BA-metal option **and a separate United-metal option** — two different aeroplanes on
the same route, both already candidates on the leg.

So `src/alternatives.py` implements two clearly separated things:

- `find_same_metal_alternatives()` — §4.7 exactly as written. The Iberia-vs-BA shape.
- `cross_metal_note()` — names a zero-surcharge option **already on the leg**, on
  different metal. It never claims one program can ticket another's aeroplane.

Step 13's required one-liner is produced by the second, and reads:

> `SAME ROUTE, DIFFERENT PROGRAM, NO CARRIER SURCHARGE: United MileagePlus carries
> $0 in carrier-imposed surcharge here as a matter of program policy, while British
> Airways Executive Club could not be scored at all because its surcharge on this
> metal is unknown. Government taxes and airport charges are still owed on both and
> are not modelled.`

### 6.2 A blanket no-YQ program resolves without knowing the metal

The plan says an assumed or missing operating carrier always degrades to unknown.
Taken literally that would make United's and Aeroplan's zeros unreachable on any
leg where the metal is ambiguous — including the real B1 and B4.

A tier-5 `(program, *, *, *, *)` row is **by construction metal-independent**, and
the Step 7 validator already guarantees such a row exists only for a program with a
blanket no-surcharge policy. So `resolve()` falls back to the tier-5 row when the
carrier is unknown, and to nothing else. Every other tier still requires confirmed
metal. This is what lets B4 be won by United's confirmed $0 rather than withheld.

### 6.3 An unknown surcharge that cannot change the verdict does not withhold one

A surcharge can only ever **add** to the points side. If the points option already
loses with the surcharge at its floor of $0, no value the unknown could take will
rescue it, and the answer is certain. Following the plan literally would report
"withheld — surcharge unknown" for legs where cash wins for sure, which withholds an
answer we actually have.

So: break-even ≤ $0 → verdict is a confident `cash`, with the reason spelled out
("the surcharge is UNKNOWN, but it can only ADD to the points side, so cash wins
whatever it turns out to be"). Break-even > $0 → the unknown really is decisive and
the verdict is withheld. This is what separates Trip B's B2 (certain cash) from B3
(genuinely withheld).

The same logic feeds the trip range: an unscoreable leg's **floor** is what widens
the low bound, so a leg we refuse to score still shows up as uncertainty rather than
silently resolving to its cash price.

---

## 7. Things in the plan that turned out to be wrong in the code

Beyond the three deviations above.

**7.1 The seed surcharge table cannot produce the plan's headline sentence.**
§1 promises: *"Same flight, same 20,000 Avios — book it through Iberia instead of
British Airways and pay $400 less cash."* Every row in §7 is keyed
`(program, that program's OWN metal)`. There is no row for "Club Iberia Plus on a
British Airways aeroplane", and §7's own notes forbid borrowing one (the Flying Blue
note says an award on a partner's metal "must resolve to unknown"). So a BA-metal
candidate has **no** same-metal alternative in the shipped data, and
`test_an_alternative_with_no_surcharge_row_is_not_offered` pins that. This is a
**data** gap, not a code one: add `(Club Iberia Plus, BA)` and
`(BA Executive Club, IB)` rows with sources and the engine produces the sentence
immediately. The Step 9 case that *does* work today — a BA-ticketed award on Aer
Lingus metal, where AerClub's own-metal row applies — is built and tested, and is
what `trip_c_lon_mry_surcharge.json`'s C2 leg exercises.

**7.2 §6 Step 6's region example fixes an ordering the alphabet does not give.**
The plan asks for `classify("SFO","MAD") == "NA-EU"`. Alphabetical sorting yields
`EU-NA`. The pair is normalised on a fixed region precedence (`regions.REGIONS`)
that reproduces the plan's own examples (`NA-EU`, `NA-AS`, `EU-EU`) and keys
`surcharges.csv`. That tuple is now load-bearing and says so.

**7.3 §4.6's stranding ceiling formula needs care.** `max over transfers of
(destination points per source increment) − 1` is only a **one-block** bound
because R2 guarantees a single transfer carries slack. If the greedy fill were ever
changed to round more than one currency up, the same formula would silently
under-count. `plan_stranding_ceiling` documents the dependency and
`test_two_currency_split_strands_one_block_not_two` is the trap.

**7.4 §4.5's mandatory-fee example is not fully modellable.** The plan names the
Marriott destination fee as one of two live examples. **Its amount was never
captured** — the Trip A fixture says so. It is therefore *not* modelled as a
`MandatoryFee`; inventing a figure to make the model look complete is exactly what
this tool must not do. It stays a note, with a new data flag explaining why. The
Amsterdam €56.47 city tax **was** captured and is now modelled, and it moves B6's
scored total from $635.09 to $700.71.

**7.5 A both-sides fee can never flip a verdict.** §Testing asks for "a fee that
flips the verdict" to be pinned. A `payable_on_points` fee lands on both totals
equally and shifts neither verdict — arithmetically it cannot. The decisive case is
a fee that is **waived on award stays** (`payable_on_points: false`), which lands on
cash only. That is what `test_a_fee_that_is_decisive_actually_flips_the_verdict`
pins, with `test_a_fee_owed_on_both_sides_shifts_both_totals_and_does_not_flip` as
the complement.

---

## 8. The two real trips, re-run

Command (identical for both, fixture name changed):

```
python -m src.main --trip-fixture trip_b_europe.json \
    --balance UR=180000 --card "Chase Sapphire Preferred" \
    --transfer-date 2026-09-15
```

`--balance UR=180000` is a **synthetic** figure supplied to exercise the ceiling;
Tsuki has still never supplied a real balance. Nothing about it is stored.

### Trip A — MRY/NYC, January 2027

```
│ A1  │ MRY-JFK round trip │  $367.00 │  36,700 │ JetBlue TrueBlue @ 1:1     │   43,000 │ UNKNOWN │ unknown │ >= $430.00 │ $367.00 │ PAY CASH                │
│ A2  │ Marriott NYC       │  $833.26 │  83,326 │ Marriott Bonvoy (no price) │ <83,326? │       - │ -       │          - │ $833.26 │ PAY CASH (pts unpriced) │
```

```
│ Pay cash for everything               │ $1,200.26 │
│ Optimizer's recommendation            │ $1,200.26 │
│ Saving                                │     $0.00 │
│ Optimizer beats paying cash by        │     0.00% │
```

**Unchanged from v0 at 0.00%**, and still a real answer. A1 is now more precisely
stated: JetBlue's metal is known (its own nonstop) but no JetBlue surcharge row
exists, so the surcharge is UNKNOWN — and yet the verdict is a *confident* PAY CASH,
because 43,000 points is $430 before any surcharge and cash is $367. The unknown is
real but inert. A2 still reports a break-even instead of inventing a Marriott award
price.

### Trip B — Europe multi-city, January 2027

```
│ B1 │ MRY->MAD                    │ $395.00 │ 39,500 │ Air Canada Aeroplan @ 1:1       │ 50,000 │   $0.00 │ modeled │      $500.00 │ $395.00 │ PAY CASH                │
│ B2 │ MAD->AMS                    │  $44.00 │  4,400 │ Club Iberia Plus @ 1:1          │  8,000 │ UNKNOWN │ unknown │   >= $80.00  │  $44.00 │ PAY CASH                │
│ B3 │ AMS->LON                    │  $76.00 │  7,600 │ Air France-KLM Flying Blue @ 1:1│  4,000 │ UNKNOWN │ unknown │ ? (win if surch < $36.00) │ $76.00 │ WITHHELD (surch unknown) │
│ B4 │ LON->MRY                    │ $482.00 │ 48,200 │ United MileagePlus @ 1:1        │ 28,000 │   $0.00 │ modeled │      $280.00 │ $482.00 │ POINTS                  │
│ B5 │ Novotel Madrid Center       │ $747.83 │ 74,783 │ none - not a partner            │      - │       - │ -       │            - │ $747.83 │ PAY CASH (no path)      │
│ B6 │ NH City Centre Amsterdam    │ $635.09 │ 70,071 │ none - not a partner            │      - │       - │ -       │            - │ $700.71 │ PAY CASH (no path)      │
│ B7 │ Hilton London Hyde Park     │ $638.35 │ 63,835 │ none - not a partner            │      - │       - │ -       │            - │ $638.35 │ PAY CASH (no path)      │
```

```
│ Pay cash for everything                            │      $3,083.89 │
│ Optimizer's recommendation                         │      $2,881.89 │
│ Saving                                             │        $202.00 │
│ Optimizer beats paying cash by                     │ 6.55% - 15.66% │
│   low end = what is actually defensible            │          6.55% │
│   high end = only if every unknown surcharge is $0 │         15.66% │
│ Points spent                                       │         28,000 │
│ Legs where points win                              │              1 │
│ Legs where a points path exists but its            │              1 │
│ surcharge is UNKNOWN (NOT $0)                      │                │
```

**The margin is a range and must not be quoted as a single number: 6.55% – 15.66%.**

Leg by leg, versus v0:

- **B1 MRY→MAD: POINTS → PAY CASH.** The BA option's surcharge is UNKNOWN, so it is
  not scored. The best option that *can* be scored is Aeroplan at a confirmed $0
  (50,000 points = $500), and $500 loses to $395 cash. The unscoreable BA candidate
  is still reported: *"NOT SCORED — its carrier-imposed surcharge is UNKNOWN (not
  $0). At 23,000 UR its cost floor is $230.00 before any surcharge."*
  The metal here is genuinely ambiguous — Google lists American, Iberia **and**
  Finnair alongside British Airways on a SFO–LHR–MAD itinerary — so the fixture
  records `carrier_source: "assumed"`, which is what degrades it. That is the
  non-negotiable working: the tool does not guess which aeroplane it is.
- **B2 MAD→AMS: PAY CASH, unchanged, and now for a stated reason.** 8,000 Iberia
  points is $80 before any surcharge against $44 cash. Certain cash.
- **B3 AMS→LON: POINTS → WITHHELD (surcharge unknown).** 4,000 Flying Blue points
  = $40 would beat $76 cash at a $0 surcharge, but KLM's surcharge on a EU-EU
  economy leg is not in the model. Break-even: **points win only if the surcharge
  is below $36.00**.
- **B4 LON→MRY: POINTS, but won by a different program.** This is the leg that
  produced v0's phantom "$282 saving" on BA at $0. BA is now unscoreable; United's
  zero is a **program policy** and resolves confirmed, so 27,600 United points
  ($280 after the 1,000-point increment) beats $482 cash for a **$202.00** saving.
  The $0 carries its caveat everywhere it appears: *"Government taxes and airport
  charges including UK APD are still owed and are NOT modelled here."*
- **B6 NH Amsterdam: $635.09 → $700.71 scored.** The €56.47 city tax is now a
  structured `MandatoryFee` and enters the total. In v0 it was free text on the cash
  option and entered nothing.
- **B7 Hilton London** is flagged as resting on an unverified GBP rate.
  `--fx GBP=1.29` clears the marker and records `user_supplied` provenance.

---

## 9. Known gaps

1. **Step 0 is not done.** Highest-value outstanding item by a wide margin. §2.
2. **No verified multi-currency data.** Engine ready, table empty. §3.
3. **Economy surcharges are the actual gap in the real trip.** Every one of Tsuki's
   BA legs is economy; the seed research is business-cabin and was not extrapolated.
   So the flagship case resolves to *unknown* rather than to a corrected number.
   Correct, but as the architect predicted, not satisfying — it is why the headline
   is a range rather than a fixed lower figure.
4. **No (program × partner-metal) surcharge rows**, so the plan's Iberia-vs-BA
   headline sentence is not producible from shipped data. §7.1.
5. **`bookable_carriers` is alliance-derived, not verified.** Mitigated by never
   pricing an alternative built from it; every one is unpriced, break-even only, and
   labelled "partnership assumed — verify".
6. **The Marriott destination fee is still unquantified.** §7.4.
7. **The stranding constraint has still never bound on real data** — no real balance
   has been supplied. Unit tests only, as in v0.
8. **Split funding is greedy, not optimal**, per the plan's own §11.2. Inert today
   anyway: with no multi-currency rows, no split can be constructed from real data.

## 10. Out-of-scope observations

- **The Trip A / Trip B date conflict is still unresolved** and still flagged, not
  fixed. Both trips start Jan 15 2027 and the Madrid and Amsterdam hotels are both
  booked Jan 15–19, which is impossible. Flights are priced for 1 adult and hotels
  for 2. §9.5 of the plan; still an input question for Tsuki.
- **A `$0` surcharge is not a `$0` ticket.** United's and Aeroplan's zeros cover the
  *carrier-imposed* surcharge only. Government taxes and UK Air Passenger Duty are
  still owed on B4 and C1 and are **not modelled anywhere in v1**. Every zero row
  says so, but it is worth flagging at the top level: the $280 for B4 is understated
  by whatever UK APD is on that ticket. Modelling government taxes would be a real
  v2 feature and is a different data problem from carrier surcharges.
- **If Step 0 shows Seats.aero returns per-flight taxes and an operating-carrier
  field, a good chunk of §4.3 becomes a fallback** and the economy gap above closes
  by itself. That makes the gate the highest-leverage next action, ahead of
  extending the surcharge table by hand.

## 11. Non-negotiables — status

| Requirement | Status |
|---|---|
| `unknown` never renders as `$0` | Enforced. `test_an_unknown_surcharge_never_renders_as_zero_dollars` and `test_an_unknown_surcharge_never_enters_a_score`, run over every fixture. |
| Unknown produces a break-even; verdict flipping in range → VERDICT_SENSITIVE | Both implemented and tested. |
| Never guess operating metal | Missing **or** `assumed` carrier degrades to unknown. The only exception is a metal-independent program-policy row (§6.2). |
| Provenance model kept; `google_badge_unverified` never presented as confirmed | Kept and extended to surcharges. Asserted as a property over every fixture. |
| PAY CASH stays first-class | Five distinct sub-states, all pinned. |
| `data/bonuses.csv` stays empty | Empty. `test_the_production_bonus_table_is_still_empty` asserts it. |
| No hardcoded card holdings or balances | `DEFAULT_CARDS` deleted. An AST-based test forbids any card name in executable code; running with no wallet exits non-zero. |
| No routing around the seats.aero block; no claimed live behaviour | Nothing attempted beyond the two probes quoted in §2. No live behaviour is claimed anywhere. |
