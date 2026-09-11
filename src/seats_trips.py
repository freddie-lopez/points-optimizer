"""
Seats.aero trips endpoint: which flights an award is, and the carrier they name.

    GET https://seats.aero/partnerapi/trips/{availability_id}
        ?include_filtered=false&min_cabin_pct=100

THIS PARSER HAS NEVER SEEN A REAL RESPONSE. It is built from Seats.aero's
published OpenAPI document (developers.seats.aero/reference/get-trips.md,
re-fetched 2026-09-11) and nothing else. The search parser was built the same
way once, read four keys that do not exist, and turned a real 9-seat award into
"no award availability". So every line derived from this module carries a
label saying the parser is UNVERIFIED until a real capture, written by
`python -m src.trips_tools capture`, is committed under
tests/fixtures/seats_aero/trips_endpoint/real/ and named below.
"""

# Which parser read a trips response. Stamped into every trips snapshot and its
# manifest row, separately from the search `PARSER_VERSION`, so a change to this
# parser says REPARSED for trips lookups only and never for search snapshots
# whose parse did not change.
TRIPS_PARSER_VERSION = "2026-09-11.trips-unverified"

# The filename of the committed REAL capture this parser has been verified
# against, under tests/fixtures/seats_aero/trips_endpoint/real/. Empty means
# nobody has checked it against a real response, and every line derived from a
# parse says so. Only a real capture can fill it in; the label test refuses a
# file under synthetic/.
TRIPS_SCHEMA_VERIFIED_BY: str = ""

# The unit of a trip's own `TotalTaxes`. The search endpoint's `{X}TotalTaxes` is
# integer cents; the trips document only says "integer". "unverified" means the
# per-trip figure is DISPLAY ONLY. The allowed flipped values are "cents" and
# "units", and only a real capture whose availability row shows the same figure
# can justify either.
TRIPS_TOTALTAXES_UNIT = "unverified"
TRIPS_TOTALTAXES_UNITS = ("unverified", "cents", "units")

PARSER_UNVERIFIED_LABEL = (
    "[trips parser UNVERIFIED against a real Seats.aero response - built from "
    "the published schema only]"
)


def parser_is_verified() -> bool:
    """True only once a real capture has been named. Read at call time."""
    return bool(TRIPS_SCHEMA_VERIFIED_BY)


def trips_parser_label() -> str:
    """The label every parse-derived line carries, or "" once verified."""
    return "" if parser_is_verified() else PARSER_UNVERIFIED_LABEL
