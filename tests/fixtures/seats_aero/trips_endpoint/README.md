# Seats.aero trips endpoint fixtures

`GET https://seats.aero/partnerapi/trips/{availability_id}` names the flights
behind one availability row. The operating-airline lookup reads it.

**NO REAL TRIPS RESPONSE HAS BEEN CAPTURED.** The parser in
`src/seats_trips.py` is built from the published OpenAPI document only, and every
line it produces says so ("trips parser UNVERIFIED against a real Seats.aero
response"). This directory is where that changes.

| directory | what goes in it |
| --- | --- |
| `synthetic/` | hand-written payloads shaped like the published schema. Every file carries `_meta.synthetic: true` and says which values are published and which are placeholders. **Never** evidence about the real API. |
| `real/` | captures written by `python -m src.trips_tools capture` on a machine that can reach seats.aero, key redacted, each with a `.raw.txt` sibling holding the verbatim body. Empty until the first capture. |

## Flipping the UNVERIFIED label

1. Run `python -m src.trips_tools capture ...` on the Mac. It writes
   `real/<date>_<source>_<O><D>_<id>.json` and `.raw.txt`, and prints a drift
   report. Exit 5 means the capture showed required-field drift: the parser
   needs fixing first, and the label must not be flipped.
2. Commit both files.
3. Set `TRIPS_SCHEMA_VERIFIED_BY` in `src/seats_trips.py` to the `.json` filename.
   `tests/test_trips_verification_label.py` then checks the file is a real
   capture (not synthetic), carries a matching content hash and a `.raw.txt`
   sibling, and parses with no unreadable itinerary and no required-field drift.

`TRIPS_TOTALTAXES_UNIT` flips from `unverified` to `cents` the same way, and only
on a capture whose recorded availability row shows a matched itinerary with the
same `TotalTaxes` as the row's `{X}TotalTaxes`.

## Why these are not in `live_trip_b/`

Trips responses carry a `data` list too, and the search parser would read them
as unreadable availability rows. Everything for this endpoint is kept in its own
directories (`cache/trips/`, `<snapshot dir>/trips_endpoint/`, this one), and the
names say `trips_endpoint` so they cannot be confused with the trip FIXTURES in
`tests/fixtures/trips/`.
