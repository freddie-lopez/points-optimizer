# Points Transfer Optimizer (v1)

Ranks ways to pay for a trip: **transfer points, or pay cash**, scored on one
yardstick so they compete head-to-head.

A leg where cash wins is reported as **PAY CASH**. The optimizer telling you *not*
to use points is a correct answer, not a failure.

A leg the optimizer **cannot** score is reported as that too. v1's central rule is
that a missing carrier-imposed surcharge is **UNKNOWN, never $0**.

---

## What changed in v1, and why it matters

v0 answered one question — *given a Chase UR balance, should I transfer or pay
cash?* — and answered it structurally wrong in three places.

**1. It could not see carrier-imposed surcharges.** So it scored two British
Airways awards, one of them departing Heathrow, at **$0** of surcharge, and both
beat their cash alternatives comfortably. The trip headline that came out of that
— *16% better than paying cash* — was not a real number.

v1 models surcharges as a function of **program × operating carrier metal × route
region × cabin × departure country**, and refuses to guess any of them. Re-scored,
the same trip reports **6.6% – 15.7%**. The low end is what the tool can defend.
The high end is what you get if every unknown surcharge turns out to be zero —
which is precisely the assumption that produced v0's 16%.

**2. It looked ratios up on the travel date.** You do not transfer points in
January because you fly in January. The ratio that applies is the one in force
**when you transfer**. `--transfer-date` (default: today) now drives every ratio
and bonus lookup, and the tool tells you when a better ratio expires.

**3. It added points across currencies.** `total_points_cost` summed source points
from different programs, which is only faithful because every Chase UR ratio in
scope happens to be 1:1. Ranking now happens in **USD at each currency's own
valuation**, with raw per-currency amounts reported separately.

Plus: **mandatory fees are modelled, not noted.** A destination fee or a city tax
is owed on a points stay too. v0 stored those as free text on the *cash* option,
so they entered no total at all.

---

## Install

```bash
pip install -r requirements.txt
cp .env.example .env      # then add your SEATS_AERO_KEY
cp data/wallet.example.json wallet.json   # then put your real numbers in
```

## Run

**A wallet is required. There is no default and there will not be one.**

```bash
python -m src.main --trip-fixture trip_b_europe.json --wallet wallet.json
```

or with flags:

```bash
python -m src.main --trip-fixture trip_b_europe.json \
    --balance UR=160000 --card "Chase Sapphire Preferred" \
    --transfer-date 2026-09-15 --show-alternatives
```

Running with no wallet exits non-zero with a message. v0 defaulted every run to a
Chase Sapphire Reserve — the one Chase card that keeps World of Hyatt at 1:1
across the 2026-10-01 change — which is an unearned claim about your wallet that
changes real answers.

`python -m src.main --help` lists everything.

### Exit codes

These are a contract. A script wrapping this tool must be able to tell a
refusal to answer apart from an unfundable plan apart from a crash.

| Code | Meaning |
|---|---|
| `0` | Success. A margin was produced and, if a balance was given, the plan is executable from it. |
| `1` | Error. Bad arguments, a missing or unreadable file, an unreplayable manifest, or an unhandled failure. **Nothing was scored.** |
| `2` | **WALLET ERROR.** No balances or cards were supplied, or the wallet file is malformed. The tool refuses to assume which cards and points you hold. **Nothing was scored.** |
| `3` | **WITHHELD.** At least one leg did not come back live — the default as of v5; `--allow-badge-fallback` opts out — or a flight leg is for 2+ travellers and was not priced, so no margin is quoted. A refusal to answer, **not** a finding of zero value. |
| `4` | **NOT EXECUTABLE.** A margin was produced, but the recommendation cannot be funded from the balance you supplied. The number is real; the plan is not actionable as printed. |

`3` and `4` are deliberately distinct: "the number is not quotable" and "the plan
cannot be executed" are different failures.

Code `2` was implemented from v1 and was missing from this table until v5, which
is its own small version of the bug this project keeps making: a behaviour that
exists, is correct, and is not written down anywhere the reader looks.

### Defaults as of v5

`--trip-fixture` scores **live** unless you pass `--offline`. A run with no
network and no `--offline` exits `3` rather than quietly scoring Google Flights
badges and printing a percentage. Auto-detecting the absence of a network and
falling back would be the same failure-as-finding shape with a different
subject, so the opt-out is explicit.

`--require-all-live` is the default; `--allow-badge-fallback` opts out and
labels the margin `badge_fallback`, on the same line as the number.

`--live` and `--require-all-live` are still accepted and are now no-ops, so
every script and every example above keeps working and keeps meaning what it
said.

The same table is printed by `python -m src.main --help`; if the two ever
disagree, `--help` is the one generated from the code.

### UK Air Passenger Duty, and the one question that is still open

APD is a **government departure tax**, charged on every passenger leaving a UK
airport, on award tickets too, by every carrier and every program. It lives in
`data/apd.csv` with its own loader and `SurchargeTable` never sees it: a
carrier's YQ and a government tax are different quantities, and merging them is
how "United charges no surcharge" becomes "this leg costs nothing in cash".

As of v5 it is **applied asymmetrically, on purpose**:

| leg | APD |
|---|---|
| offline / badge | **added** to the points-side cash total, with the GOV.UK source and the band that was chosen |
| live / replayed | **stated and not added**, flagged "APD inclusion in TotalTaxes unverified" |

The reason is that nobody has checked whether Seats.aero's `TotalTaxes` already
includes it — there has never been a live LHR-departure row. Adding it there
would risk double-charging; omitting it silently would understate. Stating the
amount without applying it is the only option that is not a guess.

**The consequence is real and it is confusing on purpose**: the same trip scores
differently in the two modes. Trip B's LHR→SFO leg reports a **$63.89** saving
offline and **$202.06** live, and the live one carries the flag explaining why.

The band comes from `data/apd_bands.csv` — destination country → band, with the
capital and the London distance that justified it. HMRC bands by distance to the
**destination country's capital**, so London→SFO's 5,350 miles is irrelevant and
London→Washington DC's 3,665 miles is what puts every US destination in band B.
**15 of 63 countries are recorded as band `UNKNOWN`** because their distance
falls within 300 miles of a band boundary and the distances here were computed
rather than read off HMRC's published list. UNKNOWN is not the nearer band and
it is not zero.

`tests/test_apd_verification_gate.py` is the gate that closes the question. It
skips until `docs/research/surcharge-and-apd-data.md` records an answer, and
then fails loudly with v6's instruction — so an answer cannot land and be
forgotten. See that document for the exact command.

### The wallet

| In the wallet | Means |
|---|---|
| `"UR": 180000` | held, that many points. The feasibility ceiling applies. |
| `"UR": null` | held, balance not supplied. **No** ceiling. |
| currency **absent** | **not held.** Nothing may be funded from it. |

Absent is not zero and zero is not unconstrained. All three are distinct and the
tool keeps them distinct.

### Valuation

Every currency defaults to **1 cent per point** — the neutral yardstick, not an
aspirational redemption value. `--valuation MR=1.4` overrides one currency.
This choice decides verdicts: a leg that loses on points at 1cpp may win at 0.5cpp.

---

## Live Trip Mode (v3)

**The one feature in v3.** Before it, the tool had two modes that could not meet:
`--trip-fixture` scored a whole multi-leg trip but every price in it — cash *and*
points — came from screenshots and it never called Seats.aero once;
`--origin/--destination/--date` called Seats.aero for real but scored exactly one
route. So Trip B's headline rested on Google Flights badges on all four flight
legs, of which **exactly one has ever been corroborated** against a real
response.

```bash
python -m src.main --trip-fixture trip_b_europe.json --live \
    --balance UR=160000 --card "Chase Sapphire Preferred" \
    --transfer-date 2026-09-15 --flex-days 0
```

`--live` requires `--trip-fixture` and errors clearly without it.

### In plain language

- **Cash is from screenshots, and always will be.** There is no cash-price API in
  scope and there is not going to be one. Live mode replaces a flight leg's
  *points* candidates and touches `cash_options` **never**.
- **Points are live where the API answered.** Where it did not, the leg says so
  and says *which* kind of "did not".
- **Hotels are manual.** Seats.aero is flights-only; no hotel award API exists.
- **A mixed-provenance margin is not a live margin.** If three of four legs came
  back live and one fell back to a badge, the percentage is labelled `mixed` and
  must not be quoted as a live number. `--require-all-live` withholds the
  percentage entirely and exits non-zero, for when a number is going to be
  quoted somewhere.

### An API failure and an absence of award space are different states

This project has reported a failure as a finding twice — v0's phantom $0
surcharge, and v2's parser turning a real bookable 9-seat award into "no award
availability". The two are now kept apart by the type system rather than by
convention:

| State | Meaning | Is it a finding? |
|---|---|---|
| `ok` | answered, awards parsed | yes |
| `no_award_space` | **answered**, read **in full**, and the answer was nothing | **yes** — there is no award to buy |
| `answered_unreadable` | answered, and the parser could not read the rows | **no** — a defect on our side |
| `answered_incomplete` | answered and readable, but we saw only **part** of the result set and found nothing in that part | **no** — an empty page of a truncated answer is not an empty answer |
| `api_error` | never answered | **no** — nothing is known about this leg |
| `budget_exhausted` | never asked; the daily cap ran out | **no** |
| `not_queried` | hotel leg, missing airports, or `--live` off | n/a |

`LiveLegOutcome`'s constructor **refuses** to build a `no_award_space` carrying
an error message, an error state carrying awards, a `no_award_space` over
unreadable rows, or a `no_award_space` over a **truncated** result set. No two of
these states share wording in the output either.

The full enumeration — **every way this tool can fail to know something about a
leg's award space**, each one a field with an invariant — is the docstring on
`LiveLegOutcome` in `src/models.py`. Read it before adding an eighth.

**A failing leg does not fail the trip.** The legs that worked are scored, the
ones that did not are reported, and the margin says which is which.

### Flags

| Flag | Does |
|---|---|
| `--live` | score flight legs against real award space |
| `--flex-days N` | widen each leg's query N days either side — still one call per leg |
| `--refresh` | ignore the cache, re-fetch, overwrite. Spends API calls |
| `--cache-ttl SECONDS` | default 21600 (6h). `0` disables reuse |
| `--require-all-live` | withhold the margin unless every leg is live; exit non-zero |
| `--snapshot-dir PATH` | where responses are archived (default the committed corpus) |
| `--trips auto\|all\|off` | the operating-airline lookup (see below). Default `auto`: only awards whose cash side can depend on the metal. Refused with `--offline`, `--from-snapshot` and a single-route search |
| `--trips-cap N` | most trips requests one run may send, 1-50, default 10. Cache hits are free |

### Flexible dates are reported, never scored

With `--flex-days 3`, an award found on a date **other than the leg's own** goes
into an advisory block and never enters a verdict or the margin. It is not being
withheld out of caution — there is genuinely nothing to compare it against, since
the cash price on the leg is for a different day.

Scoring it anyway would be biased *toward points*, and the bias grows with the
window: a flexible search takes the **minimum over a window** on the points side
while cash stays a **single fixed draw**, and award space is released on
low-demand dates, which are also low cash-fare dates. So the fixed cash price the
tool would compare against is probably *higher* than the real price on the
award's own date.

The honest fix is an input, not a code change: **capture the cash fare for that
date** and add it to the fixture with `"date": "2027-01-17"` on the cash option.
The award is then promoted, scored against *that* price, and the leg is flagged
`DATE SHIFTED`.

### Caching and the snapshot corpus

Every fetch writes **two** files in one operation:

- a cache entry under `data/cache/seats_aero/` (gitignored, keyed by request
  hash, 6-hour TTL — an expired entry is *kept*, never deleted);
- a **snapshot** under `tests/fixtures/seats_aero/live_trip_b/` (committed,
  browsably named), plus a row in `MANIFEST.md`.

Both are the same envelope format, so any snapshot is directly loadable as a test
fixture. **Raw response pages are stored, never parsed Awards** — the v2 parser
was wrong for the entire life of the project, and the fix was only possible
because one raw response happened to exist. Caching parsed objects would have
cached the bug; caching raw means a future parser fix replays every response ever
seen, offline, for zero API calls.

The `Partner-Authorization` header is not part of the cache key, is never
written, and a test greps the committed tree for it.

### A live leg with an unknown surcharge is still useful

Seats.aero returns a **candidate carrier list** (`"AC, LH, UA, VL"`), not the
operating metal, so a program whose surcharge depends on metal resolves to
UNKNOWN on most live legs. That is expected, not a bug, and no version of this
tool picks a carrier off the list to make the number appear.

Such a leg still reports a **floor** (points at valuation plus the API's taxes,
with the carrier surcharge at its $0 floor — the least it could possibly cost)
and a **break-even** (the surcharge above which points stop winning). Never a
blank cell, never a dash, never a zero standing in for an unknown. And when the
floor *already* loses to cash, the verdict is stated as **certain**, because a
surcharge can only add.


## Operating airline (Seats.aero trips lookup)

An availability row names the carriers that **might** fly a cabin (`"VS, DL"`).
Seats.aero's trips endpoint names the **flights** behind the row. For each live
award whose cash side can depend on the metal, a live trip run calls

```
GET https://seats.aero/partnerapi/trips/{availability_id}?include_filtered=false&min_cabin_pct=100
```

once, matches the returned itineraries to the award (same source, same cabin,
same `MileageCost`, not mixed-cabin), and prints the flight numbers and the
carrier they name, with their provenance.

**Under today's rules a lookup changes disclosure only.** It cannot change a
score, a verdict, a floor, a break-even, the trip range or an exit code - that
needs a verified answer to "does Seats.aero's `TotalTaxes` already include the
carrier surcharge?" for the award's source (see "The YQ question" below). Until
then the committed table is empty and every source is unverified.

### When it calls

| Mode | Calls the trips endpoint? |
|---|---|
| `--trip-fixture`, live (the default) | yes - after **every** leg's search has finished, so a lookup can never spend budget a search needed |
| `--from-snapshot` | never - it reads the lookups the live run **recorded** in `trips_endpoint/MANIFEST.md` |
| `--offline` | never - there are no live awards |
| `--origin/--destination/--date` | never - the output ends with "operating airline: NOT LOOKED UP - single-route search does not call the trips endpoint" |

`--trips auto` (the default) looks up an on-date or promoted live award only when
its program is a **direct** Chase UR partner **and** its carrier surcharge is
not a program-wide $0 - today that is Virgin Atlantic, Flying Blue, JetBlue and
KrisFlyer. United and Aeroplan read `NOT_NEEDED_POLICY`: they levy no carrier
surcharge whatever the metal, so the metal cannot change the answer.
Off-date (flexible-date) findings are never looked up.

Lookups are deduplicated by availability id (one row carries four cabins and one
response carries them all), counted on the **same** call counter as search, and
capped per run. A cache hit is free: it never counts against the cap, and once
the cap is spent - or after an HTTP 429 on any search or lookup in the run -
answers already in the disk cache are still read while nothing more is sent. A 2xx response is cached and archived like
a search response - under `data/cache/seats_aero/trips/` and
`<snapshot dir>/trips_endpoint/`, with a manifest of its own in the search
manifest's column layout - whatever its shape. HTTP errors (404 included),
timeouts, transport errors, bad JSON and budget refusals are never cached.

### What a lookup can say

Every status except `KNOWN` names its **domain** - the award's possible
carriers - and none of them is ever printed as a carrier, as "no trips", or as
a finding about whether the award has flights.

| Status | Reason code | Means | Trip block |
|---|---|---|---|
| `KNOWN` | - | the matching itineraries name ONE carrier set by flight number, every carrier in the row's own list | - |
| `AMBIGUOUS` | - | two or more matching itineraries name DIFFERENT carrier sets; every set is listed | `METAL_UNKNOWN` |
| `UNKNOWN` | `HTTP_404` | Seats.aero has no itinerary record for the id (the row may have been refreshed since the search). Not a finding about flights | `METAL_UNKNOWN` |
| `UNKNOWN` | `HTTP_429` | Seats.aero rate-limited this lookup; every later lookup in the run is `RATE_LIMITED_EARLIER` | `METAL_UNKNOWN` |
| `UNKNOWN` | `HTTP_ERROR` | any other non-2xx | `METAL_UNKNOWN` |
| `UNKNOWN` | `TIMEOUT` | the request timed out | `METAL_UNKNOWN` |
| `UNKNOWN` | `TRANSPORT_ERROR` | the request could not be completed | `METAL_UNKNOWN` |
| `UNKNOWN` | `JSON_ERROR` | the body is not JSON | `METAL_UNKNOWN` |
| `UNKNOWN` | `SHAPE_ERROR` | not an object, or `data` missing or not a list | `METAL_UNKNOWN` |
| `UNKNOWN` | `EMPTY_DATA` | `data: []` - an empty itinerary list is NOT a finding about flights | `METAL_UNKNOWN` |
| `UNKNOWN` | `INCOMPLETE` | the response says there is more (`hasMore`, a cursor, a skip) | `METAL_UNKNOWN` |
| `UNKNOWN` | `TRIP_UNREADABLE` | an itinerary that could be this award could not be read | `METAL_UNKNOWN` |
| `UNKNOWN` | `CABIN_UNMAPPED` | an itinerary that could be this award has a cabin word outside economy/premium/business/first | `METAL_UNKNOWN` |
| `UNKNOWN` | `FLIGHT_NUMBER_UNPARSEABLE` | a flight number with no two-character airline code (an ICAO `BAW123` is not guessed) | `METAL_UNKNOWN` |
| `UNKNOWN` | `TRIP_INCONSISTENT` | `FlightNumbers` or `Carriers` disagree with the segments, `Order` repeats, or the segments do not run the award's route in one chain | `METAL_UNKNOWN` |
| `UNKNOWN` | `AVAILABILITY_ID_MISMATCH` | an itinerary for a different availability id | `METAL_UNKNOWN` |
| `UNKNOWN` | `NO_MATCH` | no readable itinerary at this award's source, cabin and price (the costs seen are named) | `METAL_UNKNOWN` |
| `UNKNOWN` | `MIXED_CABIN_ONLY` | the only itineraries at this price fly part of the way in a lower cabin | `METAL_UNKNOWN` |
| `UNKNOWN` | `CARRIER_NOT_IN_ROW_LIST` | a flight-number carrier the row's `{X}Airlines` does not list - two Seats.aero fields disagree | `METAL_UNKNOWN` |
| `UNKNOWN` | `ROW_CARRIERS_ABSENT` | the row lists no carriers for the cabin, so nothing can be cross-checked | `METAL_UNKNOWN` |
| `UNKNOWN` | `UNEXPECTED_ERROR` | anything unexpected inside the lookup; the metal pass never raises | `METAL_UNKNOWN` |
| `NOT_LOOKED_UP` | `TRIPS_OFF` | `--trips off` | `METAL_LOOKUP_MISSING` |
| `NOT_LOOKED_UP` | `CAP_REACHED` | the per-run cap was reached | `METAL_LOOKUP_MISSING` |
| `NOT_LOOKED_UP` | `BUDGET_EXHAUSTED` | the call counter is at 0; no request was made | `METAL_LOOKUP_MISSING` |
| `NOT_LOOKED_UP` | `RATE_LIMITED_EARLIER` | an earlier lookup in this run got HTTP 429 | `METAL_LOOKUP_MISSING` |
| `NOT_LOOKED_UP` | `NO_AVAILABILITY_ID` | the availability row carried no id | `METAL_LOOKUP_MISSING` |
| `NOT_LOOKED_UP` | `AVAILABILITY_ID_INVALID` | the id is not 10-64 letters and digits; no URL or filename is built from it | `METAL_LOOKUP_MISSING` |
| `NOT_LOOKED_UP` | `TRANSPORT_HAS_NO_TRIPS` | the transport in use has no itinerary lookup | `METAL_LOOKUP_MISSING` |
| `NOT_LOOKED_UP` | `NOT_NEEDED_POLICY` | the program levies no carrier surcharge whatever the metal | not counted - cannot change the answer |
| `NOT_LOOKED_UP` | `NOT_DIRECT_PARTNER` | not a direct Chase UR partner (`--trips all` looks it up anyway) | not counted - cannot change the answer |
| `NOT_RECORDED` | `NO_TRIPS_SNAPSHOT` | a replay, and the live run recorded no lookup for this award | `METAL_LOOKUP_MISSING` |

`METAL_LOOKUP_MISSING` and `METAL_UNKNOWN` are counted in the trip block for the
chosen award on each leg, and the block names the legs ("operating airline NOT
LOOKED UP on B2, B4"). They are counted rather than widened because neither is
itself an unknown dollar: wherever metal moves a dollar, it does so by leaving
the surcharge unresolved, which is `SURCHARGE_UNKNOWN` and already widens.

### Marketing, not operating

A `KNOWN` line reads **"operating airline: VS by flight number (VS19)"**, then
"Seats.aero reports the MARKETING carrier; it does not report who operates the
flight", and - when the row lists more than one carrier - "A codeshare operated
by another airline in this award's list (VS, DL) cannot be detected". The words
"operated by X" are never printed as a finding. A flight-number carrier that the
row's own list does not contain is `UNKNOWN`, not "the other one".

### The UNVERIFIED label

No real trips response has been captured. The parser is built from the published
OpenAPI document only, so every `KNOWN`, `AMBIGUOUS` and parse-derived `UNKNOWN`
line - and the live banner - ends with **"[trips parser UNVERIFIED against a
real Seats.aero response - built from the published schema only]"**. The cabin
words other than "business" are assumed, and so is `min_cabin_pct=100`.

What flips it: a capture written by `python -m src.trips_tools capture` is
committed under `tests/fixtures/seats_aero/trips_endpoint/real/`, and
`TRIPS_SCHEMA_VERIFIED_BY` in `src/seats_trips.py` is set to its filename.
`tests/test_trips_verification_label.py` then checks the file is a real capture
(never one under `synthetic/`), its content hash matches, its `.raw.txt` sibling
exists, and it parses with at least one itinerary, none unreadable and no
required-field drift.

A trip's own `TotalTaxes` is shown **raw with both unit readings** and used in no
figure while `TRIPS_TOTALTAXES_UNIT` is `unverified`. It flips to `cents` only on
a real capture whose recorded availability row shows a matched itinerary with
the same `TotalTaxes` as the row's `{X}TotalTaxes`. After that, an itinerary
whose own figure is unknown, or above the row's by more than max($1, 1%), makes
the award's taxes UNKNOWN; a lower one is disclosed only.

### The YQ question

`data/yq_inclusion.csv` (`source,verdict,verified_on,evidence,notes`) holds one
verdict per Seats.aero source. It is committed with the header only. **An absent
row means unverified; a row can never say "unverified".** The loader refuses a
source it does not map, a source whose taxes Seats.aero does not report, a
verdict other than `includes_yq` / `excludes_yq`, a future date, a duplicate, and
evidence that is missing, outside `docs/yq-checks/`, lacks the `yq-check record`
marker or still has `____` blanks. The record must also be **for the row's
source** (its title and program line name it) and its **one verdict line must say
the row's verdict**; a record that says `inconclusive` backs nothing. Two
independent statements that must agree, so a verdict copied from another
source's record, or typed differently from the one written after reading the
airline's site, is refused rather than scored.

| Source verdict | Scored cash side of a live award (trusted taxes only) |
|---|---|
| no row (the default) | today's rule: scoreable only under a program-wide $0 surcharge. Known metal's modelled band is printed and marked **NOT ADDED** |
| `includes_yq` | the API's taxes are the whole carrier figure; **no band is added**, and the line names the evidence file |
| `excludes_yq` | the taxes **plus** the modelled band for the looked-up metal (KNOWN or AMBIGUOUS, resolved together across its carriers); anything else stays unscoreable as `SURCHARGE_UNKNOWN` |

Untrusted taxes (unreported source, 0, negative, unconvertible, below UK APD)
are unscoreable under every verdict, exactly as before.

`python -m src.trips_tools yq-check` produces the evidence: run it on an award
**on the program's own metal** (for example a `virginatlantic` J award on VS
metal), compare the block it prints against the program's own site, and fill in
the record it writes. It prints the CSV row with `<VERDICT>` in place of the
verdict - never a row with a verdict already in it - so the only verdict that can
be typed is the one on the record's own verdict line. It warns "likely INCONCLUSIVE" when the flight-number
carrier is not known or has no nonzero surcharge row.

### Replay

`--from-snapshot` reads `trips_endpoint/MANIFEST.md` beside the search manifest
and never the network. A recorded lookup is used whatever `auto` would have
decided; an award with no recorded lookup reads `NOT RECORDED`. A selected trips
row whose file is missing, tampered with or empty **refuses the whole replay**
(exit `1`); re-fetch it, or delete its row, and that lookup replays as NOT
RECORDED. The manifest hash gains `trips|...` lines **only when trips rows
exist**, so every hash quoted before this feature reproduces byte for byte. A
trips row captured under an older trips parser prints **TRIPS LOOKUPS
REPARSED**.

### trips_tools exit codes

`python -m src.trips_tools` is a separate module so the table in "Exit codes"
above stays the whole contract of `python -m src.main`.

| Code | Meaning |
|---|---|
| 0 | captured and clean |
| 1 | nothing captured: usage error, no key, declined at the prompt, HTTP or network error, key material detected, or refused input (an unreported source, a 0 tax figure) |
| 5 | captured WITH drift: the file is written; do not flip the label |

Both subcommands resolve the key the way `python -m src.main` does, print the
key banner, print how many calls they will make, and ask `Continue? [y/N]`
unless `--yes` is passed.

```bash
# at most 2 calls: 1 search (0 if cached) + 1 trips
python -m src.trips_tools capture --origin SFO --destination LHR \
    --date 2027-01-15 --source virginatlantic

# a capture of an award on the program's own metal, plus a YQ record to fill in
python -m src.trips_tools yq-check --origin JFK --destination LHR \
    --date 2027-01-15 --source virginatlantic --cabin J
```


---

## Surcharges

`data/surcharges.csv` is looked up **most-specific-wins** across five fixed tiers:

1. `(program, carrier, region, cabin, departure_country)`
2. `(program, carrier, region, cabin, *)`
3. `(program, carrier, region, *, *)`
4. `(program, carrier, *, *, *)`
5. `(program, *, *, *, *)` — only legitimate for a program with a **blanket
   no-surcharge policy**. A validator test rejects a wildcard row for any other
   program.

First match wins. No averaging, no nearest-neighbour, no defaults.

**No match means `unknown`.** Never zero. An unknown surcharge makes the points
side **unscoreable**, and the leg reports a **break-even surcharge** instead:
*"points beat cash only if the surcharge is below $X."*

Three things follow that are worth knowing before you use this:

- **Precedence is captured > modeled > unknown.** A surcharge read off a real
  booking page always wins — including a real, genuinely-surcharge-free **$0**,
  which is a known zero and is deliberately distinguishable from an unknown.
- **The tool never guesses operating metal.** A missing or `assumed` carrier
  degrades to unknown. A guessed aeroplane produces a confident wrong surcharge,
  which is worse than an honest gap.
- **Estimates are ranges.** If the verdict flips between the low and high ends,
  the leg is flagged **VERDICT_SENSITIVE** and the recommendation is not presented
  as settled.

### What the surcharge table does *not* cover

- **Economy.** The seed research is business-cabin and was **not** extrapolated
  down. Both of Tsuki's real British Airways legs are economy, so they resolve to
  *unknown* — correct behaviour, but not a satisfying answer.
- **A program on a partner's metal.** Rows cover each program on its **own**
  metal. There is no row for "Iberia Plus on a British Airways aeroplane", so that
  lookup returns unknown rather than borrowing a neighbour's figure.
- **Government taxes and airport charges, including UK Air Passenger Duty.** A
  `$0` in this table means *no carrier-imposed surcharge*. It does not mean a free
  ticket. Every zero row says so.

---

## Hotels

**v1 does not find hotel awards. It evaluates ones you bring.** There is no public
award-rate API for Hyatt, Marriott, IHG or Wyndham, and Seats.aero is flights
only. You read the points price off the hotel's site and put it in the fixture;
the tool scores the transfer and the cash comparison. Every hotel row of the
output says so.

What v1 *does* add is **mandatory fees**: destination fees, resort fees, city tax,
mandatory parking. These are structured, currency-aware, and priced per night /
per stay / per person-night — and they land on **both** sides when
`payable_on_points`, because that is when they are genuinely owed either way.

Award rules (`fifth_night_free`, peak/off-peak calendars, award-stay tax
treatment) are declared per program in `programs.yaml` and **all default to off**.
Enabling one requires a source and a `verified_on` date, enforced by a test.

---

## Multi-currency status — read this before relying on it

The machinery is **built and unit-tested**: per-currency valuation, bounded
two-currency split funding, the stranding rule, and the residue report.

The **data is not there**. `data/ratios.csv` contains **no verified transfer rows**
for Amex MR, Citi ThankYou, Capital One or Bilt. They are declared in
`programs.yaml` so a wallet can name them, but today they fund nothing.

That is deliberate. The v1 build environment had no network egress at all, so no
partner list could be verified, and the standard that produced v0's UR table — *a
partner that cannot be verified is omitted, not guessed* — was not lowered to fill
the table in. Adding rows is a research task; the code is ready for them and the
tests enforce that each new row carries a source and a verification date.

---

## Where the numbers come from

| File | What |
|---|---|
| `src/config.py` | **FX rates, valuations, transfer increments, transfer-date default.** Single source of truth. FX rates print on every run; a rate with no source is marked `UNVERIFIED RATE`. |
| `data/ratios.csv` | Transfer ratios, date- and card-scoped. Every row carries a `source` and a `verified_on`. |
| `data/surcharges.csv` | The surcharge model. Every row carries a source, a verification date, and a low/point/high range. |
| `data/bonuses.csv` | Transfer bonuses. **Deliberately empty** — no current bonus has been verified, and inventing one corrupts every result downstream. The mechanics are tested against a fixture labelled SYNTHETIC. |
| `data/programs.yaml` | Program metadata, aliases, the Avios-family warning, award-rule flags, and an explicit non-partner list. |
| `data/airports.csv` / `data/carriers.csv` | IATA → country → region, and carrier → alliance. An unknown code raises rather than defaulting. |
| `data/wallet.example.json` | **Template only.** Every number in it is fake. |

## Provenance is part of the data model

Every points figure carries a `source`. `seats_aero` means confirmed award space;
`google_badge_unverified` means a Google Flights badge, which often reflects
dynamic revenue pricing rather than partner saver space. Unverified figures are
labelled in the output, every time, and `google_badge_unverified` is never
presented as confirmed award space.

Every surcharge carries a `confidence` (`captured` / `modeled` / `unknown`), the
rule that matched, and where the rule came from.

When a partner exists but **no award price was captured**, the tool reports a
**break-even** ("points win below N") instead of inventing a price.

An empty award search distinguishes "no availability" from "the API was never
reached". Conflating those is how a tool reports a network failure as fact.

### Tax figures the tool will not believe

A live award's taxes are treated as **UNKNOWN - never $0 -** and the leg is
reported as a floor plus a break-even instead of a score, when:

- the Seats.aero source is `singapore`, `qatar` or `turkish` (Seats.aero
  documents "Taxes and surcharges are not available for this mileage program").
  **KrisFlyer is a direct 1:1 UR partner, so KrisFlyer awards will show a floor,
  never a POINTS verdict**, until a captured tax figure can be supplied;
- the figure is exactly 0 on an available cabin (the payload writes 0 into every
  cabin it has no data for), missing, negative, or in a currency with no FX rate;
- the award departs the UK and the figure is **below the UK Air Passenger Duty**
  for its cabin (the duty belongs inside it). Assumes an adult who is not on an
  onward connection; the duty's exemptions are not modelled.

When a UK departure's taxes are unknown because NO usable figure exists, the
duty is added to the floor (it is owed whatever else is), and the break-even is
quoted after it. When a figure exists but is in a currency with no FX rate, the
duty is stated but not added - the unconverted figure may already contain it.

Flight legs for **2+ travellers** have no reachable award scored (and no
per-seat break-even quoted): award prices are per seat and party pricing is not
modelled yet. A trip with such a leg withholds its headline (exit `3`).

Replays of snapshots captured under an earlier parser print **REPARSED** - the
same bytes can now give a different answer, by design.

### Test-harness environment variables

`POINTS_OPTIMIZER_ENV_FILE`, `POINTS_OPTIMIZER_CACHE_DIR` and
`POINTS_OPTIMIZER_SNAPSHOT_DIR` relocate the repo key file, the runtime cache and
the snapshot archive. The test suite sets them so child processes never touch
real state. If one is set in your shell, live runs print it in the banner.

### The PAY CASH sub-states are distinct

Every verdict the code can produce is listed here;
`tests/test_verdicts_are_documented.py` fails if one is added without a row.

| Verdict | Means |
|---|---|
| `points` | Points win on the merits, against a captured cash fare. |
| `cash` | Points lose on the merits. A real answer - including a leg where an unknown can only ADD to the points side and points already lose at its floor. |
| `cash (no points path)` | No transfer partner covers this leg at all. |
| `cash (points unpriced)` | A partner exists; no award price was captured. |
| `cash (points blocked)` | A path exists but the balance or stranding ceiling rejects it. |
| `cash (surcharge unknown)` | A fundable path exists and cannot be scored, because the carrier surcharge **or the award's taxes** are unknown. Reported with a floor and a break-even, never as $0. The table cell says which (`surch unknown` / `taxes unknown`). |
| `cash (APD unknown)` | UK Air Passenger Duty is owed on this departure and its amount is unknown, so the points side cannot be scored. |
| `cash (award unattributed)` | Seats.aero returned awards but named no program the tool can attribute. No claim about partners. |
| `cash (multi-traveller points not priced)` | A FLIGHT leg for 2+ travellers. Award prices are per seat and party pricing (N x points, N x taxes, N seats open) is not modelled, so the points side is not scored. Price it by hand. |
| `cash (indirect path not scored)` | The award's program is reachable only indirectly (Chase UR -> British Airways Avios -> combine into Qatar Privilege Club or Finnair Plus). The path and its conditions are printed; it is not scored, and it is not "no points path". |
| `cash (no live points data)` | Live mode was asked and Seats.aero was not reached, or was reached and could not be read. Says NOTHING about award space. |

---

## Known limitations

- **Seats.aero is UNVERIFIED. Step 0 of the v1 plan has not been run.** The
  endpoint, auth header and every response field name are still guesses corrected
  blind in v0. See `tests/fixtures/seats_aero/README.md` for exactly what to check
  and what to record. No live number from the client should be trusted until then.
- **No multi-currency transfer data.** See "Multi-currency status" above.
- **The surcharge table is business-cabin, own-metal, and small.** See "What the
  surcharge table does not cover".
- **`bookable_carriers` is alliance-derived, not a verified partnership matrix.**
  Alternatives built from it are always emitted **unpriced** and flagged as
  assumed, and are never scored or totalled.
- **GBP FX is a placeholder with no source** and carries the entire London hotel
  figure. Any total resting on it is printed with a marker; `--fx GBP=1.29` clears
  the marker and records the new provenance as `user_supplied`.
- **Split funding is greedy, not optimal.** Bounded to two currencies with one
  slack transfer (R2). Defensible operationally; not proven optimal.
- Positioning flights (MRY↔SFO/SJC) stay unpriced input, as in v0.
- **The trips parser has never seen a real response.** Every operating-airline
  line says so until a real capture is committed and named. The cabin words other
  than "business" and `min_cabin_pct=100` are assumptions; the first capture may
  turn every lookup into `UNKNOWN` - safe, and useless until the parser is fixed.
- **A flight number names the MARKETING carrier.** A codeshare between two
  carriers that are both in the award's own list cannot be detected.
- **No YQ verdict exists yet**, so a lookup changes no number. One verdict is per
  source and rests on one flight; it may not carry over to other metal or routes.
- **The trips call budget is per process.** It cannot see your other runs today;
  Seats.aero's real limit shows up as HTTP 429. A 429 on any search or itinerary
  lookup in a run stops every later trips REQUEST in that run; a lookup the disk
  cache can answer is still read, because a cache hit sends nothing. The same
  holds once the per-run cap is spent: nothing more is sent, and cached answers
  are still read. A lookup neither the cap nor the cache allows reads NOT LOOKED
  UP and is counted.
- **An award you cannot book from UR is never pointed at a UR program on the same
  metal** (for example AAdvantage space on IB metal and Iberia Plus). Not built.

## Tests

```bash
python -m pytest
```

| File | Covers |
|---|---|
| `tests/test_bugfixes.py` | The four v0 bugs, including the stranded-points constraint |
| `tests/test_ratio_table.py` | The ratio **data**: ratios, dates, card scoping, provenance, removed partners |
| `tests/test_surcharge.py` | Five-tier specificity, ranges, basis conversion, and the table validator |
| `tests/test_regions.py` | IATA → region, the UK departure hook, unknown-code handling |
| `tests/test_funding.py` | Per-currency valuation, split funding, the R2 stranding trap, residue |
| `tests/test_wallet.py` | Runtime wallet, the three balance states, and that nothing is hardcoded |
| `tests/test_transfer_date.py` | The Oct 2026 Hyatt cliff from both sides |
| `tests/test_hotels.py` | Per-night pricing, mandatory fees on both sides, award-rule flags |
| `tests/test_alternatives.py` | Same-metal alternatives, and that none is ever priced by guess |
| `tests/test_cash_fallback.py` | 1cpp scoring, cash-vs-points verdicts, FX, trip totals |
| `tests/test_invariants.py` | **Honesty properties over every fixture.** An unknown never enters a score. |
| `tests/test_adversarial.py` | Degenerate inputs, and the Step 0 gate status |
| `tests/test_fx_and_flagship.py` | FX provenance, and the flagship LON→MRY case |
| `tests/test_acceptance.py` | Synthetic fixtures plus the two real January 2027 trips |
| `tests/test_optimizer.py`, `tests/test_ratios.py`, `tests/test_seats_client.py` | v0 coverage, unchanged |
| `tests/test_metal_lookup_model.py`, `tests/test_seats_trips_parser.py` | The operating-airline vocabulary, and the trips parser against published-schema payloads |
| `tests/test_trips_transport.py`, `tests/test_trips_cache.py`, `tests/test_trips_replay.py` | The trips request, its cache and snapshots, and replay |
| `tests/test_metal_end_to_end.py`, `tests/test_trips_flags.py`, `tests/test_metal_way_ten.py` | The lookup through `apply_live` and the CLI; a lookup moves no number |
| `tests/test_yq_inclusion.py`, `tests/test_trip_taxes.py`, `tests/test_metal_alternatives.py` | The YQ table and the cash rule, per-itinerary taxes, alternatives from looked-up metal |
| `tests/test_trips_tools.py`, `tests/test_trips_verification_label.py` | `python -m src.trips_tools`, and what flips the UNVERIFIED label |

The stranded-points constraint is proven by unit test rather than by the real
trips, because no balance has been supplied and the real runs are unconstrained.

## Security

`.env` holds `SEATS_AERO_KEY` and must never be committed or pasted into a
report. Nothing in this codebase logs or prints the key. `wallet.json` holds real
balances and should be treated the same way — only `wallet.example.json`, whose
numbers are fake, belongs in version control.
