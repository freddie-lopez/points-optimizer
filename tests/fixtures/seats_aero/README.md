# Recorded Seats.aero responses

**ONE REAL RESPONSE HAS BEEN CAPTURED.** Tsuki authenticated from his own Mac on
2026-09-08 and recorded the first real Seats.aero response this project has ever
seen. It is the ground truth for `src/seats_client.py`, and every parser test in
`tests/test_seats_client.py` runs against it.

| file | what it is |
| --- | --- |
| `sfo_mad_real.json` | the capture as machine-readable JSON, containing **only** the fields actually visible in it |
| `sfo_mad_real.raw.txt` | the verbatim capture, truncated at 2,000 characters, with the request and the redaction noted |

The capture was cut at 2,000 characters. The JSON fixture contains no field that
was not in the capture: the elided regions are listed in its `_capture` block
rather than filled in.

## What the capture SETTLED

* `https://seats.aero/partnerapi/search` is the real endpoint and
  `Partner-Authorization` is the real auth header. Both were guesses until now.
* The response is `{"data": [...]}`.
* **`Source` is nested at `Route.Source`**, not top level, and its value is a
  lowercase source code (`"aeroplan"`), not a program name.
* **One row carries four cabins.** Y/W/J/F each have their own `{X}Available`,
  `{X}MileageCost`, `{X}TotalTaxes`, `{X}RemainingSeats`, `{X}Airlines`,
  `{X}Direct`.
* **Taxes are integer cents** in `TaxesCurrency`, which is **not** necessarily
  USD — the captured row is `YTotalTaxes: 4460` with `TaxesCurrency: "CAD"`,
  i.e. CAD 44.60.
* **The `*Raw` fields carry dynamic, unreliable pricing** and disagree with the
  clean fields on this very row: `JAvailable` is false while `JAvailableRaw` is
  true, `JMileageCost` is `"0"` while `JMileageCostRaw` is `470500`.
* **`{X}Airlines` is a comma-separated list** of possible operating carriers
  (`"AC, LH, UA, VL"`), not a single metal. So the API does **not** resolve the
  operating carrier — the largest technical unknown named in the v1 plan is now
  answered, and the answer is the unfavourable one.
* `{X}MileageCost` is a **string** while `{X}MileageCostRaw` is an int.
* `Route` also carries `Distance`, `OriginRegion` and `DestinationRegion`.

Every one of the field names the old parser used — `cost`, `taxes`, `Source` at
top level, `Carriers`, `seats_available`, `cabin` — was **wrong**. That parser
returned zero awards from this response and the emptiness was reported as "no
award availability".

## STILL UNKNOWN

* **Pagination.** The capture was truncated before the end of the payload, so
  whether the response carries `count` / `hasMore` / `cursor` was never seen.
  The client handles those shapes defensively, follows them when present,
  respects the 1,000-calls/day cap, and **logs which path it took**; it never
  returns page one as if it were the whole result set.
* **Live behaviour from any build environment.** This sandbox has no network
  egress. Nothing in `seats_client.py` has been executed against the live
  service — only replayed against this file.
* **Every source code except `aeroplan`.** The source-to-program map in
  `seats_client.py` covers more codes on documentation alone; each entry says
  which it is. British Airways / Iberia / Aer Lingus are deliberately absent
  rather than guessed — the three share a currency but not an award chart.
* **Whether per-flight taxes here are the whole cash cost** or only part of it.
  The captured CAD 44.60 is what the API calls `YTotalTaxes`; it is not proof
  that no carrier surcharge stacks on top for other programs.
* **CAD has no FX rate** in `src/config.py`, so the captured tax figure cannot
  currently be scored in USD. It is carried as an explicit unknown.

## To extend this

Capture from a machine that can reach seats.aero, **strip the API key from every
header, URL and body**, save the raw JSON here one file per response shape, and
add tests against it. Do not hand-write a fixture: the point of this directory is
that the parser is tested against shapes the API actually produced.

## The trips endpoint (operating-airline lookup)

`trips_endpoint/` holds fixtures for `GET /partnerapi/trips/{availability_id}`,
which names the flights behind one availability row. **No real trips response
has been captured**: `synthetic/` is hand-written from the published schema and
says so in every file, and `real/` is empty until `python -m src.trips_tools
capture` is run on a machine that can reach seats.aero. See
`trips_endpoint/README.md` for what a capture must pass before the parser's
UNVERIFIED label may be flipped. Those checks catch honest mistakes and the
synthetic example, not a deliberately hand-built file: nothing is signed, so a
commit adding a file to `real/` has to be reviewed. Live trips snapshots are archived under
`<snapshot dir>/trips_endpoint/`, never beside the search snapshots, because the
search parser would read a trips `data` list as unreadable availability rows.
