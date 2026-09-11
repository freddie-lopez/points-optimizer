# yq-check records

Each file here is ONE comparison of a Seats.aero award against the airline
program's own booking site, made to settle one question for one Seats.aero
source: does its `TotalTaxes` already include the carrier-imposed surcharge
(YQ), or not?

`python -m src.trips_tools yq-check ...` writes `<date>-<source>.md` here with
the Seats.aero half filled in and the site half left as `____` blanks. Fill the
blanks from the program's site (taxes, fees and carrier-imposed charges for ONE
adult on the SAME flight), then add ONE row to `data/yq_inclusion.csv`:

```
source,airline,verdict,verified_on,evidence,notes
virginatlantic,VS,includes_yq,2026-09-12,docs/yq-checks/2026-09-12-virginatlantic.md,JFK-LHR J on VS metal
```

- About equal to the row figure: `includes_yq`.
- About the row figure plus a separate carrier-charge line: `excludes_yq`.
- Anything else is inconclusive: record nothing. No row means unverified.

`src/yq_inclusion.py` refuses to load a row whose evidence is missing, outside
this directory, lacks the `yq-check record` marker, or still has `____` in it. It
also refuses a record that is for another source (its `# yq-check record:
<source>, <date>` title and `(source <code>)` program line), and a row whose
verdict is not the word on the record's one verdict line - including a record
that says `inconclusive`. yq-check prints the row with `<VERDICT>` in it, never a
verdict, for exactly that reason.
A verdict is per SOURCE AND AIRLINE (decision D1): it applies only to awards
whose itinerary lookup is KNOWN on the airline the check was run on, which
yq-check writes into the record ("itinerary lookup status" and "checked
airline"); the loader refuses a record whose lookup was not KNOWN or names
another airline. It rests on one flight and may not carry over to other routes.
