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
