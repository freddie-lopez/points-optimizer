# Real-trip report: Points Transfer Optimizer

**Date**: 2026-09-07
**Author**: Coder Agent
**Scope**: Chase Ultimate Rewards only
**Trips**: TRIP A (Monterey-New York, Jan 15-18 2027), TRIP B (Europe multi-city, Jan 15-27 2027)

---

## READ THIS FIRST: Seats.aero was never reached

**No award availability in this report is confirmed. Not one number.**

I attempted a live Seats.aero query. Every request was blocked at this sandbox's
egress proxy before it left the machine — `seats.aero`, `api.seats.aero` and
`www.seats.aero` all returned **HTTP 403 to CONNECT** (organization egress
policy). Actual captured output from the CLI:

```
Seats.aero could NOT be reached: Seats.aero API error: HTTPSConnectionPool(host='seats.aero', port=443):
Max retries exceeded with url: /partnerapi/search?origin_airport=SFO&destination_airport=MAD&start_date=2027-01-15&end_date=2027-01-15
(Caused by ProxyError('Unable to connect to proxy', OSError('Tunnel connection failed: 403 Forbidden')))
This is an API failure, NOT a finding of no award availability. No conclusion about award space can be drawn from this run.
```

And from the raw curl probe:

```
--- seats.aero ---        curl: (56) CONNECT tunnel failed, response 403
--- api.seats.aero ---    curl: (56) CONNECT tunnel failed, response 403
--- www.seats.aero ---    curl: (56) CONNECT tunnel failed, response 403
```

Be precise about what this does and does not tell us:

- **The API key was never tested.** The request never reached Seats.aero, so I
  cannot say whether the key in `.env` is live, dead, or rate-limited. It is
  **unverified**, not "dead".
- Separately, I found the key was **never being loaded at all** — nothing in the
  codebase read `.env`, so a live search would have failed with
  "SEATS_AERO_KEY not found" before attempting a request. Fixed (`src/config.py`).
- The Seats.aero client also pointed at `https://api.seats.aero/v2/search` with an
  `X-API-Key` header, which does not match the documented Partner API. I changed
  it to `https://seats.aero/partnerapi/search` with `Partner-Authorization`.
  **That correction is itself unverified** — I could not exercise it.

**Every points number below is a Google Flights "pts" badge**, used as a rough
proxy exactly as the brief permits, and labelled as such everywhere it appears.
Google badges frequently reflect dynamic/revenue pricing rather than partner
saver award space. **They are not equivalent to a Seats.aero result and must not
be treated as bookable.** In addition, the program each badge maps to was
**assumed by me** from the marketing carrier — Google did not name a program.

---

## The single number the brief asks for

| Trip | Pay all cash | Optimizer | Saving | **Beats cash by** |
|---|---:|---:|---:|---:|
| TRIP A | $1,200.26 | $1,200.26 | $0.00 | **0.00%** |
| TRIP B | $3,018.27 | $2,535.27 | $483.00 | **16.00%** |

**TRIP B flights only (excluding the three cash-only hotels): 48.45%**
($997.00 → $514.00). That is the honest way to read the 16%: the optimizer does
nothing at all for 60% of Trip B's cost, because those hotels have no UR path.

**TRIP A returns 0.00%, and that is a real answer, not a failure.** On this data
the optimizer's correct advice for Trip A is: *pay cash for everything, do not
transfer any points.*

These two trips are **scored separately** — both begin Jan 15 2027 and appear to
be alternatives, not one itinerary. The totals are not combined.

---

## TRIP A — Monterey to New York, Jan 15-18 2027

```
┏━━━━━┳━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃     ┃                    ┃ Cheapest ┃ Cash as pts ┃                            ┃          ┃        ┃   Score ┃   Score ┃                         ┃
┃ Leg ┃ What               ┃     cash ┃     (@1cpp) ┃ Best UR points path        ┃   Points ┃ Surch. ┃  points ┃    cash ┃ Verdict                 ┃
┡━━━━━╇━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ A1  │ MRY-JFK round trip │  $367.00 │      36,700 │ JetBlue TrueBlue @ 1:1     │   43,000 │  $0.00 │ $430.00 │ $367.00 │ PAY CASH                │
│ A2  │ Marriott NYC       │  $833.26 │      83,326 │ Marriott Bonvoy (no price) │ <83,326? │      - │       - │ $833.26 │ PAY CASH (pts unpriced) │
└─────┴────────────────────┴──────────┴─────────────┴────────────────────────────┴──────────┴────────┴─────────┴─────────┴─────────────────────────┘
```

**A1 — MRY→JFK round trip.** Cheapest cash **$367** (American, MRY-JFK via PHX).
The only option that maps to a UR partner is the **$428 JetBlue** fare
(TrueBlue, 1:1) at a badge of 21.3k one-way. **Verdict: PAY CASH** — 43,000
points scores $430 against $367 cash, so points lose by $63 (17.2% worse).

- The round-trip points figure (42,600, transferred as 43,000) is **my inference**:
  Google showed only a one-way badge and I doubled it. The return leg's award
  price was never captured and may differ.
- American AAdvantage and Alaska Mileage Plan are **not** Chase UR partners, so
  the two $367 fares and the $389 fare have no UR path at all.

**A2 — Marriott NYC, 3 nights, $833.26.** Marriott Bonvoy **is** a UR partner at
1:1, but **no award price was captured**, so I refuse to score it. Instead:
**break-even is 83,326 Bonvoy points.** Below that, points win; above it, pay
cash. Someone needs to look up the actual award price for those three nights.

- A daily destination fee is charged at check-in, **cannot be paid with points**,
  and is owed on a points stay too. The amount was not captured, so no total
  including it exists.

---

## TRIP B — Europe multi-city, Jan 15-27 2027

```
┏━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┓
┃     ┃                              ┃ Cheapest ┃ Cash as pts ┃                                      ┃        ┃        ┃   Score ┃   Score ┃                       ┃
┃ Leg ┃ What                         ┃     cash ┃     (@1cpp) ┃ Best UR points path                  ┃ Points ┃ Surch. ┃  points ┃    cash ┃ Verdict               ┃
┡━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━┩
│ B1  │ MRY->MAD                     │  $395.00 │      39,500 │ British Airways Executive Club @ 1:1 │ 23,000 │  $0.00 │ $230.00 │ $395.00 │ POINTS                │
│ B2  │ MAD->AMS                     │   $44.00 │       4,400 │ Club Iberia Plus @ 1:1               │  8,000 │  $0.00 │  $80.00 │  $44.00 │ PAY CASH              │
│ B3  │ AMS->LON                     │   $76.00 │       7,600 │ Air France-KLM Flying Blue @ 1:1     │  4,000 │  $0.00 │  $40.00 │  $76.00 │ POINTS                │
│ B4  │ LON->MRY                     │  $482.00 │      48,200 │ British Airways Executive Club @ 1:1 │ 20,000 │  $0.00 │ $200.00 │ $482.00 │ POINTS                │
│ B5  │ Novotel Madrid Center (Accor │  $747.83 │      74,783 │ none - not a UR partner              │      - │      - │       - │ $747.83 │ PAY CASH (no UR path) │
│ B6  │ NH City Centre Amsterdam     │  $635.09 │      63,509 │ none - not a UR partner              │      - │      - │       - │ $635.09 │ PAY CASH (no UR path) │
│ B7  │ Hilton London Hyde Park      │  $638.35 │      63,835 │ none - not a UR partner              │      - │      - │       - │ $638.35 │ PAY CASH (no UR path) │
└─────┴──────────────────────────────┴──────────┴─────────────┴──────────────────────────────────────┴────────┴────────┴─────────┴─────────┴───────────────────────┘
```

| Leg | Cheapest cash | Best UR path | Ratio | Points | Surcharge | Verdict | Margin |
|---|---:|---|---|---:|---:|---|---|
| B1 MRY→MAD Jan 15 | $395 BA via LHR | British Airways Executive Club | 1:1 | 23,000 | $0 *(see below)* | **POINTS** | saves $165 (41.8%) |
| B2 MAD→AMS Jan 19 | $44 Air Europa | Club Iberia Plus | 1:1 | 8,000 | $0 | **PAY CASH** | points cost $36 more (81.8% worse) |
| B3 AMS→LON Jan 23 | $76 easyJet LGW | Air France-KLM Flying Blue (KLM $111 fare) | 1:1 | 4,000 | $0 | **POINTS** | saves $36 (47.4%) |
| B4 LON→MRY Jan 27 | $482 BA nonstop | British Airways Executive Club | 1:1 | 20,000 | $0 *(see below)* | **POINTS** | saves $282 (58.5%) |
| B5 Novotel Madrid | $747.83 (€643.57) | none — Accor not a UR partner | — | — | — | **PAY CASH** | — |
| B6 NH Amsterdam | $635.09 (€546.55) | none — NH not a UR partner | — | — | — | **PAY CASH** | — |
| B7 Hilton London | $638.35 (£502.64) | none — Hilton is Amex MR, not UR | — | — | — | **PAY CASH** | — |

**Totals**: all-cash **$3,018.27** → optimizer **$2,535.27**, saving **$483.00**
(**16.00%**), spending **47,000 UR points** and still owing **$2,065.27** cash.

### The $0 surcharges on B1 and B4 are almost certainly wrong

Both British Airways options route through or depart **LHR**. BA awards
departing the UK carry Air Passenger Duty plus BA carrier-imposed surcharges —
commonly **$200-300+** even in economy on a transatlantic. **That surcharge was
never captured** and is modelled as $0, which materially flatters the points
option on the two legs that produce most of the saving.

If B4's real surcharge is $250, its points score becomes $450 against $482 cash
— a $32 saving instead of $282, and the trip-level figure collapses from 16% to
roughly **7%**. **The 16% should be treated as an optimistic ceiling until those
surcharges are looked up.**

---

## Legs with NO UR path at all

**Trip B: B5 (Novotel Madrid / Accor), B6 (NH City Centre Amsterdam), B7 (Hilton
London Hyde Park).** These are hard constraints, not missing data — none of the
three chains is a Chase UR transfer partner. Together they are **$2,021.27 of
Trip B's $3,018.27 (67%)** and are untouchable with UR points.

Also with no UR path, at the option level:

- **easyJet** (B3, the three cheapest AMS→LON fares, marked "Unsupported") — a
  low-cost carrier with no UR-transferable currency.
- **Air Europa** (B2, the cheapest $44 fare) — not a UR partner.
- **American AAdvantage**, **Alaska Mileage Plan**, **Delta SkyMiles** (A1, B1) —
  none is a Chase UR partner.

Separately: **A2 (Marriott) DOES have a UR path** (Bonvoy, 1:1) — it is missing
an award price, not a partner. The report keeps these two categories distinct.

---

## Data problems — flagged, not fixed

1. **The hotel dates are impossible.** Novotel Madrid and NH City Centre
   Amsterdam are **both booked Jan 15-19 2027**. You cannot be in Madrid and
   Amsterdam for the same four nights. The flight legs imply **Madrid ~Jan 16-19,
   Amsterdam Jan 19-23, London Jan 23-27**. I have **transcribed both as Jan 15
   and not corrected them** — picking the right dates is Tsuki's call. Note the
   Madrid stay would also start the day *before* the Jan 15 flight even arrives
   (it lands Jan 16).
2. **Trip A and Trip B both start Jan 15** and appear to be alternatives.
   Scored separately; totals never combined.
3. **Passenger counts are inconsistent** — flights priced for 1 adult, hotels for
   2 people. **Nothing has been doubled.** Trip totals therefore mix a 1-person
   flight cost with a 2-person hotel cost and are not a real per-person or
   per-couple price.
4. **No flight actually touches MRY.** Every Trip B option departs SFO or SJC and
   returns to SFO; two of the four Trip A options depart SFO. **Monterey
   positioning is unpriced** in both directions.
5. **Every Google "from $X" headline is below the cheapest fare actually listed**
   ($388 vs $395, $35 vs $44, $450 vs $482). I used the listed fares.
6. **No London hotel dates were captured** — only "4 nights". Jan 23 is inferred
   from the flights and is not confirmed.
7. **NH Amsterdam's €56.47 city tax is excluded** from the scored total, as it is
   payable at the property rather than at booking. Including it adds ~$65.
8. **Marriott's daily destination fee** is charged at check-in, cannot be paid
   with points, and its amount was never captured.

---

## FX rates — single named constant, unconfirmed

Defined once in **`src/config.py`** as `FX_RATES_TO_USD`, printed at the top of
every run, and flagged. Nothing is buried.

```
FX rates used (as of 2026-09-07):
  1 EUR = 1.1620 USD   [DERIVED from the Novotel Madrid dual-currency quote
                        ($747.81 / EUR643.57 = 1.1620). Implied booking-site rate, not a market rate.]
  1 GBP = 1.2700 USD   [PLACEHOLDER. No source in the captured data. UNVERIFIED
                        - must be confirmed before any of the London numbers are trusted.]
  !! FX RATES REQUIRE CONFIRMATION.
```

- **EUR is back-derived** from the one line that was quoted in both currencies.
  It reproduces Novotel at $747.83 against the captured $747.81 — a 2-cent
  rounding difference, which is why B5 shows $747.83 rather than $747.81.
- **GBP has no source whatsoever.** 1.27 is a placeholder I chose. The entire
  $638.35 Hilton figure rests on it. **This is the least reliable number in the
  report.**

## Valuation

Cash is scored at **1 cent per point**, per the brief: `$367 cash = 36,700
points-equivalent`. Points and cash are ranked on that single yardstick, and
every leg reports points, cash, and the combined score separately.

This yardstick decides verdicts. At 1cpp, B2's 7,500-point Iberia option loses to
$44 cash; at 0.5cpp it would win. `--valuation-cpp` exposes this, and a unit test
pins the behaviour.

## Balances

Tsuki has not given a UR balance. `--balance-ur` **defaults to unconstrained** —
no feasibility ceiling is applied and the points cost of every leg is reported
regardless. Runs print this state explicitly.

**The stranded-points constraint cannot be meaningfully exercised on this data**
without a real balance, exactly as the brief anticipated. It is verified by unit
tests instead (see below). One thing the real data *does* exercise: B1's 22,500
badge requires a **23,000** transfer because Chase UR moves in 1,000-point
blocks, **stranding 500 points**. That is now computed and reported rather than
hardcoded to zero.

---

## TASK 1 — the four tester bugs

### 1. Stranded points (CRITICAL) — fixed

The root cause was worse than a missing calculation: `find_transfer_paths()`
**moved the user's entire balance** into the destination program regardless of
what the award cost, then set `stranded_points = 0` unconditionally. A 250,000
UR balance funding a 22,500-point award transferred all 250,000.

Transfers are now **sized to demand** (`size_transfer()` in `src/optimizer.py`):
the minimum source amount that covers the award, rounded up to the transfer
increment. `stranded_points` is the real overshoot in destination currency, and
`ratio_remainder_points` captures source points lost to floor rounding.

**Design decision worth challenging:** a literal "never strand a single point"
rule would reject nearly every real transfer, because 1,000-point increments make
small overshoots unavoidable — a 22,500-point award simply cannot be funded
exactly. The constraint implemented is **"strand no more than one indivisible
transfer block"** (`max_unavoidable_stranding()`: 999 at 1:1, 749 at 4:3).
`--max-stranded-points 0` enforces strict zero-stranding, and a test proves it
rejects the 500-point overshoot. If you want strict-zero as the default, that is
a one-line change — but it makes B1 unbookable.

### 2. Negative balance — fixed
`validate_balances()` raises a clear `ValueError`. Enforced in
`find_transfer_paths()`, `optimize()`, `evaluate_leg()` and at the CLI (exit 1).
`None` means unconstrained; `0` is valid and contributes nothing.

```
$ python -m src.main --trip-fixture trip_a_mry_nyc.json --balance-ur -5000
Error: Invalid balance for 'UR': -5,000 - a points balance cannot be negative.
Pass 0 for an empty account, or omit the flag entirely to leave the balance unconstrained.
(exit code 1)
```

### 3. Multiple cards — fixed
The `break` after the first matching card is gone.
`RatioManager.best_ratio_across_cards()` evaluates **every** card and returns all
(card, ratio) pairs sorted best-first. Results are now order-independent: a user
holding both Sapphire Preferred and Sapphire Reserve gets the 1:1 Reserve ratio
to Hyatt whichever order the cards are listed in.

### 4. Card matching — fixed
`ratio.card_dependency in card` → exact `==`. CSV card names are now full
(`Chase Sapphire Reserve`), and a partial name like `"Sapphire Reserve"` no
longer matches.

I also removed a related latent bug: `"other"` was hardcoded in `models.py` to
mean "not Chase Sapphire Reserve". It now means "any card not explicitly named
for this program pair on this date", computed from the table. The UR data uses
explicit card rows and no longer needs `"other"` at all, but the schema still
supports it.

---

## TASK 2 — ratio table rewritten (UR only)

`data/ratios.csv` rebuilt from scratch: **14 partners, all Chase UR, all 1:1
except Hyatt-on-non-Reserve after Oct 2026.**

**Corrected:** Marriott Bonvoy **3:1 → 1:1**. IHG **2:1 → 1:1**. Both were
wrong and both would have made a correct hotel transfer look 2-3x worse.

**Added (all 1:1):** Aer Lingus AerClub, Air Canada Aeroplan, British Airways
Executive Club, Club Iberia Plus, JetBlue TrueBlue, Southwest Rapid Rewards,
Wyndham Rewards. **Five of the seven are load-bearing for these trips** — without
BA, Iberia, Aeroplan and JetBlue, Trip B would have shown almost no points paths.

**Hyatt gap closed:** a non-Reserve card had no row before 2026-10-01, so
pre-change lookups returned nothing. Sapphire Preferred and Ink Business
Preferred now have explicit 1:1 rows through 2026-09-30, then 4:3 from
2026-10-01. Sapphire Reserve stays 1:1 throughout.

**The Oct 2026 4:3 change is Hyatt-only** and is not applied anywhere else. A
parametrised test asserts all ten airlines stay 1:1 on all three cards across
2026-09-30 / 2026-10-01 / 2027-01-15, and that IHG, Marriott and Wyndham are
unaffected.

**Removed:** Emirates, LATAM. **Also removed: Japan Airlines** — it was in the
old table, is not on the verified partner list, and Chase UR does not transfer to
JAL. Flagging that explicitly since it was not in your removal list.

**Dropped MR / TY / CapitalOne / Bilt rows.** The CSV schema is unchanged and
still holds them; a test asserts the header is intact.

**`data/bonuses.csv` is now empty (header only).** The previous file contained
four transfer bonuses I could not verify — inventing a 25% bonus silently
inflates every result that touches it. Bonus mechanics are tested against
`tests/fixtures/bonuses_test.csv`, labelled SYNTHETIC.

I also added an alias layer (`programs.yaml` → `normalize_program()`) so
`"United"`, `"Hyatt"`, `"BA"` and API-style codes resolve to canonical names.

---

## TASK 3 — cash fallback at 1cpp

Implemented in `src/config.py` (`CASH_VALUATION_CPP`,
`cash_to_points_equivalent()`) and `src/optimizer.py` (`evaluate_leg()`,
`evaluate_trip()`, `trip_totals()`).

Every leg produces a cash option. Output always shows **points, cash, and the
combined score separately**. A leg where cash wins is reported as **PAY CASH**
with an explicit "Do NOT burn points here" — B2 and A1 are exactly that case.

Legs are classified three ways, and the distinction matters:
`PAY CASH` (points path exists, cash is better) · `PAY CASH (no UR path)` (no
partner exists) · `PAY CASH (pts unpriced)` (partner exists, award price missing
— break-even reported, nothing invented).

---

## Test run — actual output

```
$ python -m pytest
============================= test session starts ==============================
platform linux -- Python 3.11.15, pytest-7.4.0, pluggy-1.6.0 -- /usr/bin/python
cachedir: .pytest_cache
rootdir: /home/claude/points-optimizer
configfile: pytest.ini
testpaths: tests
plugins: anyio-4.13.0
collecting ... collected 118 items

[... 118 lines, all PASSED ...]

======================== 118 passed, 1 warning in 1.09s ========================
```

**118 passed, 0 failed** (baseline before this work: 25 passed).

The stranding tests, run individually:

```
$ python -m pytest tests/test_bugfixes.py
tests/test_bugfixes.py::test_stranded_points_are_actually_calculated PASSED [  5%]
tests/test_bugfixes.py::test_stranded_points_zero_when_award_lands_on_increment PASSED [ 10%]
tests/test_bugfixes.py::test_stranding_at_four_to_three_ratio PASSED     [ 15%]
tests/test_bugfixes.py::test_stranding_constraint_rejects_over_transfer PASSED [ 20%]
tests/test_bugfixes.py::test_stranding_constraint_allows_unavoidable_rounding PASSED [ 25%]
tests/test_bugfixes.py::test_transfers_are_sized_to_demand_not_to_balance PASSED [ 30%]
tests/test_bugfixes.py::test_infeasible_when_balance_too_small PASSED    [ 35%]
tests/test_bugfixes.py::test_infeasible_reason_is_reported PASSED        [ 40%]
tests/test_bugfixes.py::test_unconstrained_balance_has_no_ceiling PASSED [ 45%]
tests/test_bugfixes.py::test_max_unavoidable_stranding_tracks_the_ratio PASSED [ 50%]
tests/test_bugfixes.py::test_negative_balance_rejected PASSED            [ 55%]
tests/test_bugfixes.py::test_negative_balance_rejected_in_find_transfer_paths PASSED [ 60%]
tests/test_bugfixes.py::test_zero_balance_is_allowed_and_contributes_nothing PASSED [ 65%]
tests/test_bugfixes.py::test_non_numeric_balance_rejected PASSED         [ 70%]
tests/test_bugfixes.py::test_multiple_cards_picks_best_ratio_regardless_of_order PASSED [ 75%]
tests/test_bugfixes.py::test_all_cards_are_evaluated_not_just_the_first PASSED [ 80%]
tests/test_bugfixes.py::test_best_ratio_is_first PASSED                  [ 85%]
tests/test_bugfixes.py::test_card_matching_is_exact_not_substring PASSED [ 90%]
tests/test_bugfixes.py::test_partial_card_name_does_not_match PASSED     [ 95%]
tests/test_bugfixes.py::test_other_dependency_is_not_hardcoded_to_one_card PASSED [100%]

======================== 20 passed, 1 warning in 0.29s =========================
```

**Six pre-existing tests were changed**, all because they asserted the old buggy
behaviour. Called out so nobody thinks tests were bent to pass:

| Test | Why changed |
|---|---|
| `test_find_transfer_paths_single_direct` → `..._sizes_to_demand` | asserted the whole balance was transferred — the stranding bug itself |
| `test_find_transfer_paths_multiple` → `..._uses_existing_balance_first` | same API change; now asserts existing balance offsets the transfer |
| `test_find_transfer_paths_respects_dates` | `points_needed` now required; still asserts the Oct 1 1:1→4:3 flip |
| `test_find_transfer_paths_no_stranding` | asserted `stranded == 0` when it was hardcoded to 0; now asserts real values |
| `test_chase_ur_hyatt_other_before_oct1` | asserted `None` for a pre-change non-Reserve lookup — that was the data gap Task 2 fixed |
| `test_apply_ratio_with_bonus` | production bonus table is now empty; repointed at the synthetic fixture |

### One bug I found in my own output while testing

Running the real trip under strict `--max-stranded-points 0`, the MAD-AMS leg
reported **"none - not a UR partner"**. That was false: Club Iberia Plus *is* a
partner, and the path had been rejected by the stranding constraint. The tool was
blaming the data for its own policy decision. Fixed — there is now a distinct
`PAY CASH (pts blocked)` state, and two tests pin the difference between "no
partner exists" and "a path exists but a constraint rejected it".

---

## Everything I could not verify

- **All award availability.** Seats.aero was unreachable (403 at the egress
  proxy). No points figure here is confirmed award space.
- **The Seats.aero API key.** Never validated — the request never left the
  machine. Unverified, not dead.
- **The Seats.aero client's endpoint and response parsing.** Corrected to the
  documented Partner API shape but never exercised.
- **Every Google "pts" badge** (22.5k, 50k, 7.5k, 4k, 20k, 27.6k, 21.3k, 17.5k,
  46k). Unverified estimates.
- **Which program each badge belongs to.** Assumed from the marketing carrier.
  Google did not say.
- **The $0 surcharges**, particularly BA out of LHR on B1 and B4. Almost
  certainly materially wrong; the single biggest risk to the 16% figure.
- **The GBP/USD rate (1.27).** A placeholder with no source.
- **The EUR/USD rate (1.1620).** Back-derived from one hotel quote, not a market
  rate.
- **Marriott's award price for A2** and the **daily destination fee**. Not
  captured; break-even reported instead.
- **The Trip A round-trip points cost.** My doubling of a one-way badge.
- **The correct hotel dates for Trip B.** Reported as broken, deliberately not
  guessed.
- **Monterey positioning** to/from SFO/SJC. Unpriced in the source data.
- **Current transfer bonuses.** None verified, so `bonuses.csv` is empty. If a
  real UR bonus is running, these results understate the points side.

---

## What I would do next

1. **Get the real BA/UK surcharges for B1 and B4.** They are the difference
   between a 16% result and something near 7%.
2. **Run Seats.aero from an unblocked network** and replace every Google badge.
   Until then this tool is comparing cash against estimates.
3. **Resolve the Madrid/Amsterdam date conflict**, then re-run.
4. **Get the Marriott award price** for A2 and settle it against the 83,326
   break-even.
5. **Get a real UR balance** so the feasibility and stranding constraints can
   bind on live data instead of only in tests.
