# Seats.aero parser fix — coder report

**Date:** 2026-09-08
**Trigger:** Tsuki authenticated from his own Mac and captured the first real
Seats.aero response this project has ever seen.
**Files:** `src/seats_client.py` (rewritten), `src/models.py`, `src/surcharge.py`,
`src/regions.py`, `src/optimizer.py`, `src/main.py`,
`tests/test_seats_client.py` (rewritten), `tests/test_adversarial.py`,
`tests/fixtures/seats_aero/` (new fixture + rewritten README),
`tests/fixtures/trips/trip_b_europe.json` (one provenance note).

---

## What was wrong, stated plainly

The old parser read `result["cost"]`, `result["taxes"]`, `result["Source"]` and
`result["Carriers"]`. Run against the real response, every one of those is
`None`:

```
$ python -c "... r.get('Source'), r.get('cost'), r.get('taxes'), r.get('Carriers') ..."
old parser would have read: [(None, None, None, None, 'Y', None)]
```

So the parser produced **zero awards from a response containing a real,
bookable, 9-seat award**, and that emptiness was reported to Tsuki as "no award
availability". That is the same class of error as v0's phantom $0 surcharge: a
failure presented as a finding. The client's `last_error` machinery could not
catch it, because nothing failed — the request succeeded and the parse
"succeeded" on keys that do not exist.

The old tests passed throughout, because they asserted an invented schema. A
test written against a made-up shape tests only the imagination that produced
it. That is now structurally impossible here: every parser test runs against the
recorded response.

---

## The five corrections

### 1. `Source` is nested at `Route.Source`, and it is a source *code*

`resolve_source()` maps lowercase Seats.aero codes to the program names used in
`data/ratios.csv`, via an explicit `SEATS_AERO_SOURCES` table. Three states, not
two:

| `ur_transferable` | meaning |
| --- | --- |
| `True` | a Chase UR path exists (aeroplan, united, virginatlantic, flyingblue, jetblue, singapore) |
| `False` | the award is real and bookable, but **not from a UR balance** (delta, american, alaska, emirates, etihad, qantas, lifemiles, ana, turkish, eurobonus, …) |
| `None` | the code is not in the map — **we do not know**, and are not guessing |

None of the three is a reason to drop the row. "Award exists, you cannot reach
this program" is a reportable result, and it is now reported in
`Award.source_note`.

Every entry is labelled with its provenance: `aeroplan` is marked *observed*;
every other code is marked *from Seats.aero's published source list, not
observed in any captured response*. **British Airways, Iberia and Aer Lingus are
deliberately absent** rather than guessed — the three share a currency but not an
award chart, and mapping a code onto the wrong Avios chart is the single most
tempting invention in this codebase. A test cross-checks that every
`ur_transferable` entry names a real UR partner row in `data/ratios.csv`.

### 2. One row carries four cabins

`parse_availability_row()` emits **one Award per available cabin**, reading
`{X}Available`, `{X}MileageCost`, `{X}TotalTaxes`, `{X}RemainingSeats`,
`{X}Airlines`, `{X}Direct` for X in Y/W/J/F. The old one-per-row model, keyed on
a `cabin` field that does not exist, discarded three quarters of every response.

An available cabin with no clean mileage price is **skipped and counted**
(`last_rows_skipped`), not emitted at cost 0 (which would look free) and not
back-filled from the Raw price (which would be an invented number).

### 3. Taxes are integer cents, in a currency that is not USD

`convert_taxes()` divides by 100, then converts through the existing FX config,
and carries the source amount and currency through on the Award for display.

On the captured row: `YTotalTaxes: 4460`, `TaxesCurrency: "CAD"` → **CAD 44.60**.
Read naively as USD that is $4,460 — an overstatement of about 100x, which would
have been added to a points score as if it were real cash.

**CAD has no FX rate in `src/config.py`**, and none was invented. The award
therefore carries `cash_component_known=False` with the reason spelled out, and
`cash_component` is not a scoreable 0.0. `Strategy` gained `cash_cost_known` and
`is_lower_bound` so the optimizer cannot quietly total an unconvertible tax as
zero — the v0 surcharge bug wearing a new hat. **Open item for the architect:
CAD needs a sourced FX rate, or `--fx CAD=<rate>` must be passed.**

### 4. Non-Raw fields for availability and price; Raw for diagnostics only

The captured row is its own argument: `JAvailable: false` / `JAvailableRaw:
true`, and `JMileageCost: "0"` / `JMileageCostRaw: 470500`. Scoring 470,500
miles as a business-class price would have poisoned a verdict with a number
nobody can book.

Raw values are retained in `Award.raw_diagnostics` behind an explicit
`"DIAGNOSTIC ONLY … never scored"` warning, and nothing in the optimizer reads
them. A test asserts 470500 and 5590 never reach an Award.

### 5. `{X}Airlines` is a list of *possible* operating carriers

`"AC, LH, UA, VL"` parses to `["AC","LH","UA","VL"]`.
`Award.has_known_metal` is True **only** for a single-entry list.

`SurchargeTable.resolve_ambiguous_metal()` resolves the list per the v1 rules:

* **all carriers agree** → that figure, with the ambiguity disclosed and "No
  carrier was chosen" stated in the notes. Safe precisely because the answer does
  not depend on which one flies it.
* **they differ** → `UNKNOWN`, with the per-carrier outcomes named, scored as a
  break-even. Picking the first entry would invent the answer.
* **no rule for any of them** → `UNKNOWN`, explicitly "not zero".

For this row that means Aeroplan's program-wide no-YQ policy applies to AC, LH,
UA and VL alike, so the $0 is real and no metal was assumed to get it.

**A finding for the plan:** the v1 plan named "does the API distinguish operating
from marketing carrier" as its largest single technical unknown. It is now
answered, and the answer is the unfavourable one — Seats.aero gives a *candidate
list*, not the metal. Wherever a program's surcharge is metal-dependent (BA, VS,
Flying Blue on partner metal), live Seats.aero results will resolve to UNKNOWN
rather than to a figure.

### Also

`Route.OriginRegion` / `DestinationRegion` now feed the surcharge model's
route-region tier (`regions.region_from_seats_aero` + `normalize_pair`), in
preference to re-deriving one from the IATA code — the API's own classification
is what the award was filed under. An unrecognised label falls back to
`airports.csv`; it is never defaulted to a region. `Distance` is carried for
display. `{X}MileageCost` string vs `{X}MileageCostRaw` int is handled by
`_as_int()`, which returns `None` — never 0 — on anything it cannot read.

---

## Pagination — handled defensively, and it is still unknown

**UNVERIFIED.** The capture was truncated at 2,000 characters, so whether the
response carries `count` / `hasMore` / `cursor` was never observed.

The client follows `cursor` / `next_cursor` when present, falls back to `skip`,
stops on `hasMore: false`, enforces the **1,000 calls/day** cap (`DAILY_CALL_CAP`,
counted per calendar day across instances) and a 25-page safety cap. If told
there are more results with no way to ask for them, it stops rather than
inventing an offset scheme.

Every path writes `last_pagination_note`, which `src/main.py` now prints on
**every** run — in red when the word `INCOMPLETE` appears. On a single-page
response with no pagination markers the note says so explicitly and adds that
another pagination mechanism "has NOT been ruled out". Page one is never returned
silently as the whole result set.

---

## Tests

```
$ python -m pytest -q
======================== 395 passed, 1 warning in 5.88s ========================
```

Baseline before this change was 345 passing; all 345 still pass in substance.
Net +50: `tests/test_seats_client.py` went from 5 invented-schema tests to 54
against the real fixture, and `tests/test_adversarial.py`'s Step-0 gate tests
were rewritten (see below).

**Three adversarial tests deliberately changed**, because their premise was that
Step 0 had not been run and it now has:

* `test_no_recorded_seats_aero_response_is_claimed_to_exist` → **`test_a_real_seats_aero_response_is_recorded`**. It now asserts the fixture exists; deleting it would return the parser to being tested against invented shape.
* `test_the_seats_aero_client_is_still_labelled_unverified` → **`test_the_seats_aero_client_does_not_overclaim_verification`**. The banner no longer says "UNVERIFIED AGAINST A LIVE API" (endpoint, auth and schema *are* now verified against one real response), and the test pins the three things that must keep being said: live behaviour from a build machine is unverified, this sandbox has no egress, pagination was not observed.
* The fixture-README test now checks the README records what the capture settled **and** what is still unknown.
* Added: `test_the_recorded_response_carries_no_api_key`.

The fixture is stored two ways: `sfo_mad_real.json` (machine-readable, containing
**only** fields actually visible in the capture, with the capture's own elisions
listed rather than filled in) and `sfo_mad_real.raw.txt` (the verbatim truncated
capture, key redacted).

---

## The known answer, verified

`test_the_sfo_mad_leg_reproduces_the_google_badge` and
`test_the_confirmed_aeroplan_award_still_loses_to_395_cash`:

The SFO→MAD leg now produces **Air Canada Aeroplan, economy (Y), 50,000 miles,
9 seats remaining, CAD 44.60 taxes, on 2027-01-15**, metal ambiguous across
AC/LH/UA/VL, surcharge a real program-policy **$0**.

Tsuki's Google badge said "Air Canada 50k pts". **The real data confirms the
badge on this one leg.** That is a fact about this leg and not a licence to trust
badges generally — the other badges in Trip B remain unverified, and B1's
`source_note` in the trip fixture now says exactly that.

And it still loses: 50,000 UR at 1:1 into Aeroplan, valued at the neutral 1cpp
yardstick, is **$500 against a $395 cash fare**. Confirming availability did not
change the verdict, and the margin is robust from the correct direction — the
unconvertible CAD 44.60 can only make the points side *worse*.

Live CLI output for leg B1, unchanged:

```
B1 - MRY->MAD, Jan 15 2027, one-way, economy, 1 adult
  Verdict: CASH - Cash is cheaper: $395.00 vs $500.00 on points at 1.0cpp. Do NOT burn points here.
```

---

## Does the Trip B range move? **No. 6.55% – 15.66%, unchanged.**

```
│ Optimizer beats paying cash by                     │ 6.55% - 15.66% │
│   low end = what is actually defensible            │          6.55% │
│   high end = only if every unknown surcharge is $0 │         15.66% │
```

**Why it does not move, which matters more than the number.** Trip B is scored by
`evaluate_leg()` over the captured JSON trip fixture — Google Flights badges and
hotel quotes. It does not call `SeatsClient` at all. The parser fix repairs the
*live search* path (`optimize()`), which Trip B never touches. So a correct
parser cannot, by itself, move a number that was never derived from the API.

What the real data did do to Trip B is **raise the confidence of one input
without changing its value**: B1's 50k Aeroplan candidate is now corroborated by
a real award, and its `source_note` records the confirmation, the true carrier
ambiguity (AC/LH/UA/VL, not AC alone), and the CAD 44.60 in uncounted taxes. The
verdict on B1 was PAY CASH before and is PAY CASH now.

The range would only move if Trip B were re-run against live Seats.aero data for
all four flight legs. That has not been done and must not be implied.

---

## What is still unverified — do not let this be read as more than it is

* **Nothing here has been executed against the live service.** This sandbox has
  no network egress; every test replays the recorded file. No live behaviour is
  claimed.
* **Pagination was never observed.** Handled defensively and logged; not verified.
* **One source code and one row shape have ever been seen.** Everything the map
  says about the other 15 codes rests on documentation, and each entry says so.
* **CAD has no FX rate**, so the one real tax figure the project possesses cannot
  currently be scored in USD. It is carried as an explicit unknown.
* **`{X}TotalTaxes` may not be the whole cash cost.** It is what the API calls
  total taxes; it is not proof that no carrier surcharge stacks on top for other
  programs.
* Whether the response includes fields beyond the truncation point that would
  change any of the above is, by construction, unknown.
