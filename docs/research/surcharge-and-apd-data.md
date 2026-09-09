(Mirror of project doc research/surcharge-and-apd-data.md — see that for the canonical version with source links.)

UK APD, GOV.UK, authoritative. Departures 1 Apr 2026–31 Mar 2027:
  Domestic  reduced £8      standard £16
  Band A    reduced £15     standard £32     (0–2,000 mi)
  Band B    reduced £102    standard £244    (2,001–5,500 mi)  <- ALL US destinations (band by capital city)
  Band C    reduced £106    standard £253    (>5,500 mi)
From 1 Apr 2027: Dom 8.26/16.52, A 15.49/33.04, B 105.33/251.95, C 109.46/261.25.
Reduced = lowest cabin, pitch <40in. Charged on DEPARTURE from UK only, on awards too, every program/carrier.
Rule: separate table from carrier surcharge; key (departure_country=UK, band, cabin, date); additive.
Live Seats.aero TotalTaxes SHOULD include it — unverified until first LHR-departure live row. Flag as such.

Carrier surcharge by program x operating metal (sourced, TPG / Points Uncovered / AwardWallet / AwardLocker):
  program=IberiaPlus metal=BA     JFK->LHR ow  J  $729
  program=BAClub     metal=BA     JFK->LHR ow  J  $748   (program barely matters on BA metal)
  program=IberiaPlus metal=IB     NYC<->MAD rt Y  $151
  program=BAClub     metal=IB     NYC<->MAD rt Y  $494   (program matters hugely on IB metal)
  program=IberiaPlus metal=IB     NYC<->MAD rt J  $175
  program=BAClub     metal=IB     NYC<->MAD rt J  $1,494
  program=IberiaPlus metal=IB     MAD->NYC ow  J  ~GBP115
  program=BAClub     metal=BA     LHR<->NYC rt J  GBP850 std / GBP350 RFS@160k (incl APD)
  program=BAClub     metal=BA     USWest->LHR ow J  ~$508
  program=IberiaPlus metal=IB     transatl ow  J  ~$127
  program=AerClub    metal=EI     JFK->DUB     J  ~$150  (vs ~$600 via BA)
  United / Aeroplan / KrisFlyer: $0 YQ policy.  FlyingBlue AF/KL US<->EU rt J ~$150-250.  Virgin VS transatl rt Upper $400-700.

GAPS — seed as UNKNOWN, do not guess: BA metal economy ex-UK (floor = APD £102); BA metal economy ex-US; IberiaPlus on EI metal; AerClub on BA metal.
Every figure is one article, one date. Seed at point value, keep band wide.

---

## v5 Step 8 — the APD verification run (GATE, runs on Tsuki's Mac)

**Status: NOT RUN.** No live LHR-departure row exists in this project's corpus,
so it is still unknown whether Seats.aero's `{X}TotalTaxes` already contains UK
Air Passenger Duty.

v5 Step 7 wired APD into scoring **asymmetrically** because of that gap:

| leg | APD |
|---|---|
| offline / badge | **ADDED** to the points-side cash total, disclosed with the GOV.UK source and the band chosen |
| live / snapshot | **STATED and NOT ADDED**, flagged "APD inclusion in TotalTaxes unverified" |

Adding it on the live path risks double-charging; omitting it silently risks
understating. Stating it without applying it is the only third option that is
not a guess in one direction or the other — and it converts the unknown into a
one-row experiment.

### The run

```bash
python -m src.main --new-trip apd_probe \
    --leg LHR:SFO:<a date within 330 days>:500 \
    --live --balance UR=160000 --card "Chase Sapphire Preferred"
```

1. Read the resulting live row's `TotalTaxes` (and its currency) for a **Y**
   cabin, off the per-leg detail block.
2. Open the **same route, date and cabin** on a real award booking page —
   BA.com award search, or AA/Iberia for the same metal — and read the
   taxes-and-carrier-charges breakdown.
3. Write the answer below, **with the date and the screenshot**.

### The answer

Record it on a single line containing the literal marker
`APD-IN-TOTALTAXES ANSWER:` followed by one of `INCLUDED`, `NOT_INCLUDED` or
`INCONCLUSIVE`, and an ISO date. `tests/test_apd_verification_gate.py` skips
until that line exists and then asserts on it.

| Answer | What it means | What v6 does |
|---|---|---|
| `INCLUDED` — `TotalTaxes` ≥ £102 **and** the booking page attributes ~£102 to APD | Seats.aero includes it | Remove the flag; add nothing |
| `NOT_INCLUDED` — `TotalTaxes` is materially below £102 | Seats.aero does not include it | Add APD on live and replayed legs too; the flag becomes a real charge |
| `INCONCLUSIVE` | The breakdown does not separate it | The flag **stays** and keeps saying the question is open |

**There is no fourth outcome in which the flag is quietly dropped.** The gate
test enforces that: `INCLUDED` and `NOT_INCLUDED` both make it FAIL LOUDLY with
the instruction for v6 in the failure message, so an answer cannot land and then
be forgotten.

<!-- NOT YET RUN. Delete this comment block and write ONE uncommented line in
     its place. It must contain the marker, one of the three answers, and an ISO
     date - the gate ignores commented lines, table rows and code-quoted
     mentions, so nothing here can accidentally open it. Shape:

       MARKER NOT_INCLUDED 2026-09-20 - TotalTaxes GBP 41.20 vs BA.com GBP
       143.20 of which GBP 102 is APD; screenshot
       docs/research/apd-probe-2026-09-20.png

     where MARKER is the literal string named above. -->

### One thing the run should also record

`data/apd_bands.csv` marks **15 of 63** countries as band `UNKNOWN` because
their London–capital distance falls within 300 miles of the 2,000- or
5,500-mile band boundary and the distance was **computed from capital
coordinates rather than read off HMRC's published country list**. They are:
BR, CO, CR, EC, EG, IL, JO, KR, LK, MV, MX, PA, TR, VN, ZA. Each resolves to
UNKNOWN, which is not the nearer band and not zero. Replacing those rows from
the GOV.UK country list is a small, separate, entirely offline job.
