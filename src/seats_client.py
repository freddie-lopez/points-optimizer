"""
Seats.aero Partner API client for award availability search.

ENDPOINT AND AUTH: VERIFIED. RESPONSE SCHEMA: VERIFIED against one captured
response. LIVE BEHAVIOUR FROM THIS MACHINE: STILL UNVERIFIED.

On 2026-09-08 Tsuki authenticated from his own Mac and captured the first real
response this project has ever seen:

    GET https://seats.aero/partnerapi/search
        ?origin_airport=SFO&destination_airport=MAD
        &start_date=2027-01-15&end_date=2027-02-14
    Partner-Authorization: <key>

It is saved at tests/fixtures/seats_aero/sfo_mad_real.json (with the verbatim
truncated capture beside it as .raw.txt) and every parser test runs against it.

WHAT THAT CAPTURE PROVED THE OLD PARSER WRONG ABOUT. It read `result["cost"]`,
`result["taxes"]`, `result["Source"]` and `result["Carriers"]`. NONE of those
four keys exist. It therefore produced zero awards from a response containing
real availability, and that emptiness was reported to Tsuki as "no award
availability" - a failure presented as a finding, the same shape of error as
v0's phantom $0 surcharge. The five corrections are documented at each site
below and are numbered to match the fix brief.

WHAT IS STILL UNVERIFIED, and must not be described otherwise:
  * this sandbox has no network egress, so nothing here has been executed
    against the live service - only replayed against the recorded file;
  * the capture was truncated at 2000 characters, so whether the response
    carries count / hasMore / cursor was NOT observed. Pagination below is
    written defensively and logs which path it took;
  * only ONE source ("aeroplan") and one row shape have ever been seen. The
    source->program map covers more codes than that on documentation alone and
    says so per entry.
"""
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

from src import config, models, regions, response_cache
from src.models import Award, DateRange


class SeatsAeroError(RuntimeError):
    """Raised when the Seats.aero API cannot be reached or returns an error."""


class TripsLookupError(SeatsAeroError):
    """
    A trips lookup that produced no response to read, with the reason as DATA.

    `code` is one of the MetalLookup reason codes (HTTP_404, HTTP_429,
    HTTP_ERROR, TIMEOUT, TRANSPORT_ERROR, JSON_ERROR, BUDGET_EXHAUSTED,
    AVAILABILITY_ID_INVALID, NO_TRIPS_SNAPSHOT), so the caller maps it to a
    status without parsing a sentence. `request_sent` says whether an HTTP
    request actually left this machine - the per-run cap counts those.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        request_sent: bool = False,
        http_status: Optional[int] = None,
    ):
        super().__init__(message)
        self.code = code
        self.request_sent = request_sent
        self.http_status = http_status


# v5 STEP 2. WHICH PARSER READ A SET OF BYTES.
#
# Stamped into every snapshot's `_meta` and into its manifest row at `put` time.
# It exists for exactly one property: storing raw pages means a parser fix can
# be replayed against every response this project has ever seen, and a replay
# that silently reparsed under a DIFFERENT parser would change the award count
# with nothing in the output to say so.
#
# BUMP THIS whenever `parse_pages_detail` or anything it calls changes what a
# given page yields. Rows written before v5 carry no version at all; those read
# as "unknown", which is reported as unknown and never as "matches".
#
# It is NOT part of the manifest hash. The hash is over BYTES, so a reparse
# under a new parser reproduces the same hash and a different award count -
# which is the honest pair.
# 2026-09-10: BUMPED. The parse of a given page changed: a 0 tax figure on an
# available cabin, and any figure from qatar/turkish/singapore, now yield
# UNKNOWN taxes; six more sources are named; qatar/finnair carry an indirect UR
# path. A replay of a snapshot captured under the previous version must say it
# was REPARSED - the same bytes now produce a different answer, and Tsuki's
# first real corpus (captured 2026-09-10 under the previous version, with a
# qatar row at tax 0) is exactly such a snapshot.
PARSER_VERSION = "2026-09-10.taxes-trust"


def _rows_of(payload: Dict[str, Any]) -> List[Any]:
    """
    The availability rows in one response page, under either documented key.

    RETURNS A LIST OR NOTHING. Finding C-1's cheapest two variants both got in
    here: `{"data": {...}}` returned the dict, whose iteration yields key
    strings; `{"data": "no results"}` returned the string, whose length became
    `rows_seen`, so a leg reported a finding over ten imaginary rows. A rows
    container that is not a list is not zero rows - it is a response shape this
    tool does not understand, and `envelope_shape_error` below is what says so.
    """
    rows = payload.get("data")
    if rows is None:
        rows = payload.get("results")
    return rows if isinstance(rows, list) else []


def envelope_shape_error(payload: Any) -> str:
    """
    Why this response page cannot be read as availability rows, or "".

    An empty string means "readable" - which includes a genuinely empty list of
    rows, the one case that legitimately means no award space.
    """
    if not isinstance(payload, dict):
        return (
            f"the response page is a {type(payload).__name__}, not a JSON object"
        )
    # MANAGER REVIEW MR-1, THE FIFTH WAY TO FAIL TO KNOW. Both documented row
    # containers can be present at once, and `_rows_of` picks 'data' and
    # silently discards 'results'. `{"data": [], "results": [...20 real rows...]}`
    # therefore produced rows_seen=0, rows_unreadable=0, no shape error, and a
    # confident NO_AWARD_SPACE - "there is no award to buy on this date" - over a
    # payload carrying twenty bookable rows. Choosing one of two containers that
    # DISAGREE is a guess, and a guess is not knowledge. This is checked BEFORE
    # the loop below so that the disagreement cannot be resolved by luck of key
    # order.
    present = [k for k in ("data", "results") if isinstance(payload.get(k), list)]
    if len(present) == 2 and payload["data"] != payload["results"]:
        return (
            f"the response carried BOTH a 'data' list ({len(payload['data'])} "
            f"rows) and a 'results' list ({len(payload['results'])} rows) and "
            f"they are not the same rows. There is no way to tell which one is "
            f"the answer, so neither is read. Picking one would be a guess, and a "
            f"guess about which container holds the availability is not a finding "
            f"about award space"
        )
    for key in ("data", "results"):
        if key not in payload:
            continue
        rows = payload[key]
        if rows is None:
            continue
        if not isinstance(rows, list):
            return (
                f"{key!r} is a {type(rows).__name__}, not a list of availability "
                f"rows"
            )
        return ""
    return (
        "the response carried neither a 'data' nor a 'results' list, so there is "
        "no way to tell an empty answer from a shape this parser does not know"
    )


def _iso_or_unknown(dt: Optional[datetime]) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else "at an unrecorded time"


@dataclass
class ParsedPages:
    """
    What the parser made of some raw pages, with the failure modes kept apart.

    `rows_unreadable` is a DEFECT SIGNAL and `rows_without_availability` is a
    FINDING. Collapsing them into one integer is what let nine rows the parser
    could not read be announced as "there is no award to buy on this date"
    (adversarial finding C-1). They are never summed except for display.
    """

    awards: List[Award] = field(default_factory=list)
    rows_seen: int = 0
    rows_unreadable: int = 0
    rows_without_availability: int = 0
    unreadable_reasons: List[str] = field(default_factory=list)

    # Reasons are deduplicated and capped: nine identical broken rows should read
    # as one problem, and a 500-row corrupt page must not produce a 500-line note.
    MAX_REASONS = 5

    def add_reason(self, why: str) -> None:
        if not why or why in self.unreadable_reasons:
            return
        if len(self.unreadable_reasons) < self.MAX_REASONS:
            self.unreadable_reasons.append(why)

    @property
    def rows_skipped(self) -> int:
        return self.rows_unreadable + self.rows_without_availability


@dataclass
class RawSearchResult:
    """
    What the transport layer returns: bytes, and everything known about them.

    Deliberately NOT a (list, error_string) pair. That shape is what let "empty
    list" be reported as "no availability" for the entire life of this project;
    the caller of `search_raw` gets pages plus provenance and has to decide what
    an empty `pages` MEANS, rather than being handed a default interpretation.
    """

    pages: List[Dict[str, Any]] = field(default_factory=list)
    http_status: Optional[int] = None
    pages_fetched: int = 0
    pagination_note: str = ""
    served_from_cache: bool = False
    fetched_at: Optional[datetime] = None
    request: Dict[str, str] = field(default_factory=dict)
    request_key: str = ""
    cache_path: Optional[Any] = None
    snapshot_name: Optional[str] = None
    manifest_key: str = ""
    # The daily call budget ran out before or during this fetch. NOT DATA. See
    # the refusal to cache it in `search_raw` (finding H-1).
    budget_exhausted: bool = False
    # Set when we saw only PART of the result set: the response said there is
    # more and gave no way to ask for it, an offset that did not advance, the
    # page cap, or the daily budget breaking mid-pagination.
    incomplete: bool = False
    # WHY it is incomplete, as data rather than as prose buried in
    # `pagination_note`. MR-1: `LiveLegOutcome.result_incomplete` requires a
    # reason, and the reason has to come from the layer that stopped.
    incomplete_reason: str = ""

    @property
    def rows_seen(self) -> int:
        return sum(len(_rows_of(p)) for p in self.pages)


# WAY (9), CHECKED IN THE RUNNING PROCESS. Every field the LiveLegOutcome
# invariants need carried from the bytes must exist on the transport's result
# type, under the same name storage uses. Raises at import if it does not.
models.assert_transport_carries(RawSearchResult)


@dataclass
class RawTripsResult:
    """
    One trips response, verbatim, and everything known about how it arrived.

    `payload` is exactly what `response.json()` returned - `[]`, `None` and a
    string are kept as they came, because coercing them to `{}` would turn a
    wrong-shaped answer into an empty one. The parser names each shape.
    """

    payload: Any = None
    http_status: Optional[int] = None
    served_from_cache: bool = False
    fetched_at: Optional[datetime] = None
    request: Dict[str, str] = field(default_factory=dict)
    request_key: str = ""
    snapshot_name: Optional[str] = None
    manifest_key: str = ""
    incomplete: bool = False
    incomplete_reason: str = ""
    # The verbatim response body when it was fetched on THIS run, else "".
    raw_text: str = ""
    # Whether an HTTP request left this machine for this result.
    request_sent: bool = False
    # Replay provenance, set only by the snapshot transport.
    replayed_from_snapshot: bool = False
    snapshot_content_hash: str = ""
    snapshot_parser_version: str = ""
    snapshot_captured_at: Optional[datetime] = None


# The same way (9) check, for the trips transport: the provenance keys every
# storage layer persists must be fields here too.
models.assert_transport_carries(RawTripsResult)


def coverage_of_pages(pages: List[Dict[str, Any]]) -> Tuple[bool, str]:
    """
    Truncation as a FUNCTION OF THE STORED BYTES. Way (9)'s second half.

    Persisting `incomplete` fixes every envelope written from now on. It does
    nothing for the ones already on disk, and `SnapshotTransport` used to carry
    a comment claiming coverage "is RECOMPUTED from the archived pages by the
    parser" while passing `incomplete=False`. This is that recomputation, and it
    is now true: the last archived page is asked the same question the live
    pagination loop asks it, with the same code. If it says there is more, the
    archive ends before the result set does, whatever `_meta` claims.

    Returns (incomplete, reason). An EMPTY page list is not an answer either way
    and is refused upstream (a zero-page snapshot is not a fetch); it returns
    False here rather than inventing a reason.
    """
    if not pages:
        return False, ""
    last = pages[-1]
    if not isinstance(last, dict):
        return False, ""
    next_params, stall_reason = SeatsClient._next_page_params(last, None)
    if stall_reason:
        return True, (
            f"RECOMPUTED FROM THE STORED BYTES: the last archived page says "
            f"{stall_reason}."
        )
    if next_params:
        return True, (
            "RECOMPUTED FROM THE STORED BYTES: the last archived page advertises "
            "a further page ("
            + ", ".join(f"{k}={v}" for k, v in sorted(next_params.items()))
            + ") which is not in this archive, so these pages are part of the "
            "result set and not the whole of it."
        )
    return False, ""


# ---------------------------------------------------------------------------
# (1) Source codes.  `Source` is nested at Route.Source, NOT top level, and its
#     value is a lowercase Seats.aero source code ("aeroplan"), not a program
#     name.  The map is EXPLICIT rather than derived from the alias table so
#     that a code with no Chase UR path is a first-class, reportable answer:
#     "the award exists, you cannot reach this program from UR" is a real
#     finding.  Dropping such a row silently would hide availability.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeatsSource:
    code: str
    program: str
    # DIRECT Chase UR transfer. See `indirect_ur_path` for the other kind.
    ur_transferable: bool
    note: str = ""
    indirect_ur_path: str = ""


_OBSERVED = "Observed in the SFO-MAD capture of 2026-09-08."
_OBSERVED_TRIP_B = (
    "Reported observed in Tsuki's live Trip B run of 2026-09-10 (recorded in the "
    "known-failures triage); no capture of that run is in this repo."
)
_DOCS = (
    "From Seats.aero's published source table "
    "(developers.seats.aero/reference/concepts-copy, read 2026-09-10). NOT yet "
    "observed in a captured response."
)

# THE AVIOS FAMILY. Chase UR transfers 1:1 to British Airways Club, and BA Club
# Avios can be combined into Qatar Privilege Club and Finnair Plus - British
# Airways lists both on its own combine page
# (britishairways.com/content/the-british-airways-club/avios/combine-avios).
# That is a real path from a UR balance into these programs, so saying "no UR
# path" about them is FALSE. It is also not a plain 1:1 partner: it is two hops,
# and each hop has conditions this tool cannot check against anyone's accounts.
# So these awards are NAMED, the path and its conditions are PRINTED, and they are
# NOT SCORED. Whether to score two-hop paths is Tsuki's decision, not this map's.
_AVIOS_VIA_BA = (
    "No DIRECT Chase UR transfer. An INDIRECT path exists: Chase UR -> British "
    "Airways Club Avios (1:1) -> combine into {program} Avios (1:1)."
)
_QATAR_CONDITIONS = (
    " Conditions (Thrifty Traveler, updated 2026-06-30): both accounts at least 30 "
    "days old; an ID upload for Qatar to approve linking the accounts; matching "
    "names and two-factor authentication on both. To book a COMPANION with Qatar "
    "Avios, the companion needs their OWN Privilege Club account, at least 30 days "
    "old, that has earned Avios by flying or card spend. Combining can be paused "
    "without notice. Not scored by this tool."
)
_FINNAIR_CONDITIONS = (
    " Conditions (finnair.com, transfer Avios between Finnair and British "
    "Airways): Finnair Plus account at least 30 days old; age 18+; two-factor "
    "authentication on both accounts; names and email addresses must match. Not "
    "scored by this tool."
)

SEATS_AERO_SOURCES: Dict[str, SeatsSource] = {
    # --- Chase UR transfer partners -------------------------------------
    "aeroplan": SeatsSource("aeroplan", "Air Canada Aeroplan", True, _OBSERVED),
    "united": SeatsSource("united", "United MileagePlus", True, _DOCS),
    "virginatlantic": SeatsSource(
        "virginatlantic", "Virgin Atlantic Flying Club", True, _DOCS
    ),
    "flyingblue": SeatsSource(
        "flyingblue", "Air France-KLM Flying Blue", True, _DOCS
    ),
    "jetblue": SeatsSource("jetblue", "JetBlue TrueBlue", True, _DOCS),
    "singapore": SeatsSource(
        "singapore", "Singapore Airlines KrisFlyer", True, _DOCS
    ),
    "aeromexico": SeatsSource(
        "aeromexico", "Aeromexico Club Premier", False,
        _DOCS + " Not a Chase UR partner.",
    ),
    # British Airways / Iberia / Aer Lingus: Seats.aero does not expose these
    # as separate cached sources under codes this project has verified, so they
    # are deliberately ABSENT rather than guessed. An unmapped code is reported,
    # not silently coerced onto the wrong Avios chart - the three members share
    # a currency but not an award chart, and substituting one for another is the
    # single most tempting invention in this codebase.
    # --- Real awards with NO Chase UR path -------------------------------
    "delta": SeatsSource("delta", "Delta SkyMiles", False, _DOCS),
    "american": SeatsSource("american", "American AAdvantage", False, _DOCS),
    "alaska": SeatsSource("alaska", "Alaska Mileage Plan", False, _DOCS),
    "emirates": SeatsSource("emirates", "Emirates Skywards", False, _DOCS),
    "etihad": SeatsSource("etihad", "Etihad Guest", False, _DOCS),
    "qantas": SeatsSource("qantas", "Qantas Frequent Flyer", False, _DOCS),
    "lifemiles": SeatsSource("lifemiles", "Avianca LifeMiles", False, _DOCS),
    "ana": SeatsSource("ana", "ANA Mileage Club", False, _DOCS),
    "turkish": SeatsSource("turkish", "Turkish Miles&Smiles", False, _DOCS),
    "eurobonus": SeatsSource("eurobonus", "SAS EuroBonus", False, _DOCS),
    "smiles": SeatsSource("smiles", "GOL Smiles", False, _DOCS),
    "azul": SeatsSource("azul", "Azul TudoAzul", False, _DOCS),
    "connectmiles": SeatsSource("connectmiles", "Copa ConnectMiles", False, _DOCS),
    "velocity": SeatsSource("velocity", "Virgin Australia Velocity", False, _DOCS),
    "saudia": SeatsSource("saudia", "Saudia AlFursan", False, _DOCS),
    # --- Added 2026-09-10 from the published table ------------------------
    "lufthansa": SeatsSource("lufthansa", "Lufthansa Miles & More", False, _DOCS),
    "ethiopian": SeatsSource("ethiopian", "Ethiopian ShebaMiles", False, _DOCS),
    "frontier": SeatsSource("frontier", "Frontier Airlines", False, _DOCS),
    "spirit": SeatsSource("spirit", "Spirit Airlines", False, _DOCS),
    # --- No direct UR transfer, but an indirect one via BA Avios ----------
    "qatar": SeatsSource(
        "qatar", "Qatar Privilege Club", False,
        _OBSERVED_TRIP_B + " Taxes are NOT reported for this source.",
        indirect_ur_path=(
            _AVIOS_VIA_BA.format(program="Qatar Privilege Club")
            + _QATAR_CONDITIONS
        ),
    ),
    "finnair": SeatsSource(
        "finnair", "Finnair Plus", False, _DOCS,
        indirect_ur_path=(
            _AVIOS_VIA_BA.format(program="Finnair Plus") + _FINNAIR_CONDITIONS
        ),
    ),
}

# Sources observed in the live Trip B run (2026-09-10): the note says so rather
# than "unverified". Recorded from Tsuki's run output, not re-derived.
for _code in ("flyingblue", "jetblue", "american", "alaska", "qantas"):
    _src = SEATS_AERO_SOURCES[_code]
    SEATS_AERO_SOURCES[_code] = SeatsSource(
        _src.code, _src.program, _src.ur_transferable,
        _OBSERVED_TRIP_B + (" Not a Chase UR partner." if not _src.ur_transferable else ""),
        _src.indirect_ur_path,
    )
del _code, _src


def resolve_indirect_path(code: Optional[str]) -> str:
    """The indirect UR path into this source's program, or "" if none is known."""
    src = SEATS_AERO_SOURCES.get(str(code or "").strip().lower())
    return src.indirect_ur_path if src is not None else ""


def resolve_source(code: Optional[str]) -> Tuple[str, Optional[bool], str]:
    """
    (program_name, ur_transferable, note) for a Seats.aero source code.

    An UNRECOGNISED code returns the raw code with ur_transferable=None. That is
    a third state and it matters: None means "we do not know whether UR reaches
    this", which is not the same as the False that means "we know it does not".
    Neither is a reason to discard the award.
    """
    key = str(code or "").strip().lower()
    if not key:
        return "", None, (
            "The response carried no Route.Source, so the award cannot be "
            "attributed to a program at all."
        )
    src = SEATS_AERO_SOURCES.get(key)
    if src is None:
        # FINDING L-4. This used to return `key` as the PROGRAM NAME, so
        # `Source: "hawaiianairlines"` produced `Award.program ==
        # "hawaiianairlines"`, which `normalize_program` passed through unchanged
        # and the formatter printed in a Program column as if it were one. A
        # source code is not a program name, and printing it as one asserts a
        # mapping this project has not made. The code is preserved on
        # `Award.program_source_code` and named in the note; the program stays
        # EMPTY, which routes the award down the "unattributable" path where the
        # tool says what it actually knows.
        return "", None, (
            f"Seats.aero source code {key!r} is NOT in the source->program map in "
            f"src/seats_client.py, so this award CANNOT be attributed to a loyalty "
            f"program. The award itself is real and is reported as-is. The code is "
            f"not being printed as though it were a program name, and whether "
            f"Chase UR can reach it is UNKNOWN and is not being guessed. Add "
            f"{key!r} to SEATS_AERO_SOURCES once the program it denotes has been "
            f"verified."
        )
    return src.program, src.ur_transferable, src.note


# ---------------------------------------------------------------------------
# (2) Cabins.  ONE ROW CARRIES FOUR CABINS.  Every field is prefixed by the
#     cabin letter: YAvailable / YMileageCost / YTotalTaxes / YRemainingSeats /
#     YAirlines / YDirect.  The old parser emitted one Award per row and read a
#     `cabin` key that does not exist, throwing away three quarters of the data.
# ---------------------------------------------------------------------------
CABINS: Tuple[str, ...] = ("Y", "W", "J", "F")


def _as_int(value: Any) -> Optional[int]:
    """
    (5, second half) `{X}MileageCost` is a STRING, `{X}MileageCostRaw` is an int.
    Parse defensively; a value we cannot read becomes None, never 0.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _as_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("true", "1", "yes"):
        return True
    if text in ("false", "0", "no"):
        return False
    return None


def parse_carriers(value: Any) -> List[str]:
    """
    (5) `{X}Airlines` is a COMMA-SEPARATED LIST of possible operating carriers
    ("AC, LH, UA, VL"), not a single metal.

    Returns every code in the list, in order, deduplicated. The caller must not
    treat a multi-entry list as known metal: see Award.has_known_metal and
    SurchargeTable.resolve_ambiguous_metal, which resolve a surcharge only when
    all listed carriers share the same outcome.
    """
    if not value:
        return []
    out: List[str] = []
    for part in str(value).split(","):
        code = part.strip().upper()
        if code and code not in out:
            out.append(code)
    return out


def convert_taxes(
    cents: Optional[int], currency: Optional[str]
) -> Tuple[bool, Optional[float], str, float, str]:
    """
    (3) TAXES ARE INTEGER CENTS IN `TaxesCurrency`, WHICH IS NOT NECESSARILY USD.

    The captured row has YTotalTaxes: 4460 with TaxesCurrency: "CAD". That is
    CAD 44.60. Reading 4460 as USD overstates it by a factor of about 100 and
    would then be added to a points score as if it were real cash.

    Returns (known, source_amount, source_currency, usd, note).

    An unknown or unsupported currency degrades to known=False. It does NOT
    degrade to 0.0 and it does NOT get an assumed rate - the same rule the
    surcharge model enforces, for the same reason.
    """
    if cents is None:
        return False, None, "", 0.0, (
            "The response carried no tax figure for this cabin. Unknown, not zero."
        )

    amount = cents / 100.0

    # FINDING M-3. A NEGATIVE TAX FIGURE IS NOT A CASH CREDIT. `YTotalTaxes:
    # -50000` came through as CAD -500.00, converted to -$362.80, and rode into
    # the score as a CAPTURED surcharge - the strongest confidence tier in the
    # model - making a 50,000-point award cost $137.20 and win its leg.
    # The surcharge table's validator rejects a negative amount on a row; a
    # figure captured from live data bypassed that check entirely. Nobody pays
    # you to fly, so a negative here is corrupt data, and corrupt data is
    # UNKNOWN - never a discount.
    if amount < 0:
        return False, amount, str(currency or "").strip().upper(), 0.0, (
            f"Taxes came back as {amount:,.2f}, a NEGATIVE figure. An airline "
            f"does not pay you to take the seat, so this is corrupt data, not a "
            f"credit. Carried as UNKNOWN - it is neither $0 nor a discount, and "
            f"it must never reduce the cost of a points option."
        )

    cur = str(currency or "").strip().upper()

    if not cur:
        return False, amount, "", 0.0, (
            f"Taxes are {amount:,.2f} but the response named no TaxesCurrency, so "
            f"the amount cannot be converted. Unknown, not zero."
        )

    if cur not in config.FX_RATES_TO_USD:
        return False, amount, cur, 0.0, (
            f"Taxes are {cur} {amount:,.2f}. No FX rate for {cur} is configured "
            f"in src/config.py, so this CANNOT be scored in USD. It is carried "
            f"as an unknown cash component - not as $0, and not at a rate the "
            f"tool invented. Add {cur} to config.FX_RATES_TO_USD with a source, "
            f"or pass --fx {cur}=<rate>."
        )

    usd = config.convert_to_usd(amount, cur)
    note = f"Taxes {cur} {amount:,.2f} converted at {config.FX_RATES_TO_USD[cur]} = ${usd:,.2f}."
    if config.is_placeholder_rate(cur):
        note += (
            f" The {cur} rate is a PLACEHOLDER with no source behind it; this "
            f"dollar figure inherits that."
        )
    elif config.needs_confirmation(cur):
        # v3 STEP 0. Before v3 the only non-clean tier was `placeholder`, so a
        # rate that stopped being a placeholder printed as a clean number. GBP
        # and CAD are now `sourced` - better than a placeholder, not as good as
        # a rate someone has vouched for - and the marker must follow the figure
        # rather than living only in a banner that can be scrolled past.
        sourced_on = config.FX_SOURCED_ON.get(cur)
        note += (
            f" CONFIRM BEFORE TRUSTING: the {cur} rate is "
            f"{config.FX_PROVENANCE_TIER.get(cur, '?')}"
            + (f", sourced {sourced_on}" if sourced_on else "")
            + (
                f" and now {config.fx_rate_age_days(cur)} days old"
                if sourced_on
                else ""
            )
            + f"; this dollar figure inherits that."
        )
        if config.is_stale_rate(cur):
            note += f" STALE RATE: older than {config.FX_STALE_AFTER_DAYS} days."
    return True, amount, cur, usd, note


# ---------------------------------------------------------------------------
# (3b) A TAX FIGURE THE TOOL MAY NOT BELIEVE.
#
#     Seats.aero's developer docs mark three sources with the footnote "Taxes and
#     surcharges are not available for this mileage program":
#     https://developers.seats.aero/reference/concepts-copy (read 2026-09-10).
#     Whatever number those rows carry in {X}TotalTaxes is therefore not a tax
#     figure. In practice it is 0, and a KrisFlyer row - a DIRECT Chase UR
#     partner - arriving with "$0 taxes" and a UR path is a false points win
#     waiting to happen.
#
#     And for every source: an AVAILABLE cabin priced at exactly zero taxes is
#     not a free ticket. No commercial award ticket carries zero government
#     charges (a US domestic award still pays the security fee; an international
#     departure pays departure taxes). The same payload writes 0 into every
#     unavailable cabin's TotalTaxes, which is what 0 means here: nothing
#     reported. Both cases are UNKNOWN - never $0.
# ---------------------------------------------------------------------------
TAXES_UNREPORTED_SOURCES = frozenset({"qatar", "turkish", "singapore"})


def untrusted_tax_reason(source_code: Any, cents: Optional[int]) -> str:
    """
    Why this row's tax figure must not be believed, or "" if it may be.

    Only answers the two questions above. A missing, negative or unconvertible
    figure is `convert_taxes`'s business and is already UNKNOWN there.
    """
    code = str(source_code or "").strip().lower()
    if code in TAXES_UNREPORTED_SOURCES:
        shown = "nothing" if cents is None else f"{cents / 100.0:,.2f}"
        return (
            f"Seats.aero does not report taxes for the {code!r} source (its "
            f"documentation: 'Taxes and surcharges are not available for this "
            f"mileage program'), so the figure it sent ({shown}) is not a tax "
            f"figure. The taxes on this award are UNKNOWN - not $0."
        )
    if cents == 0:
        return (
            "Seats.aero reported taxes of exactly 0 on a cabin it marks "
            "available. No award ticket carries zero government taxes and "
            "charges, and the same payload writes 0 into every cabin it has no "
            "data for - so 0 here means NOT REPORTED. The taxes on this award "
            "are UNKNOWN - not $0."
        )
    return ""


def _route_regions(route: Dict[str, Any], origin: str, destination: str):
    """
    Route.OriginRegion / Route.DestinationRegion feed the surcharge model's
    route-region tier, in preference to re-deriving a region from the IATA code:
    the API's own classification is what the award was filed under.

    Falls back to airports.csv when a label is unrecognised, and to "" when that
    fails too. It is never defaulted to a region - a wrong region silently picks
    a wrong surcharge band.
    """
    o_label = str(route.get("OriginRegion") or "")
    d_label = str(route.get("DestinationRegion") or "")
    o = regions.region_from_seats_aero(o_label)
    d = regions.region_from_seats_aero(d_label)
    if o and d:
        return o_label, d_label, regions.normalize_pair(o, d)
    try:
        return o_label, d_label, regions.classify(origin, destination)
    except (regions.UnknownAirportError, ValueError):
        return o_label, d_label, ""


# A price nobody could ever book. `_as_int` correctly rejects "fifty thousand"
# and the `cost <= 0` fence correctly rejects a negative, but there was no upper
# fence at all, so `YMileageCost: "99999999999999"` became an Award verbatim
# (finding L-3). The ceiling is deliberately far above any real award chart -
# the most expensive published redemption in scope is under a million miles -
# so it can only ever catch corruption, never a real price.
MAX_PLAUSIBLE_MILEAGE = 5_000_000

# How `parse_availability_row_detail` classifies a row that produced no Award.
ROW_OK = "ok"
ROW_UNREADABLE = "unreadable"          # a defect signal. NOT a finding.
ROW_NO_AVAILABILITY = "no_availability"  # read fine, nothing bookable. A finding.


def parse_availability_row_detail(row: Any) -> Tuple[List[Award], str, str]:
    """
    Parse one row AND say why it produced nothing. Returns (awards, status, why).

    THE DISTINCTION THIS FUNCTION EXISTS FOR (finding C-1). `parse_availability_row`
    returned a bare list, so its caller could not tell these two apart:

      * a row it READ, which carries no available cabin - that is real evidence
        of no award space;
      * a row it COULD NOT READ - a date in a format it rejects, a cabin marked
        available at an unreadable price - which is evidence of nothing at all.

    Both came back as `[]`, both were counted into one `rows_skipped` number,
    and the caller announced the union of them as "there is no award to buy on
    this date". Splitting the return value is the root-cause fix; the state
    machine in models.LiveLegOutcome then makes the wrong answer unbuildable.
    """
    if not isinstance(row, dict):
        return [], ROW_UNREADABLE, (
            f"an availability row is a {type(row).__name__}, not a JSON object"
        )

    raw_date = row.get("Date") or (row.get("ParsedDate") or "")[:10]
    try:
        date.fromisoformat(str(raw_date)[:10])
    except (TypeError, ValueError):
        return [], ROW_UNREADABLE, (
            f"a row carries Date={raw_date!r}, which is not an ISO date this "
            f"parser can read"
        )

    awards = parse_availability_row(row)
    if awards:
        return awards, ROW_OK, ""

    # No award came out. Was anything actually ON OFFER that we failed to price?
    unreadable_cabins = []
    for cabin in CABINS:
        if _as_bool(row.get(f"{cabin}Available")) is not True:
            continue
        cost = _as_int(row.get(f"{cabin}MileageCost"))
        if cost is None:
            unreadable_cabins.append(
                f"{cabin} is marked available but its MileageCost "
                f"({row.get(f'{cabin}MileageCost')!r}) cannot be read"
            )
        elif cost <= 0:
            unreadable_cabins.append(
                f"{cabin} is marked available at {cost:,} miles, which is not a "
                f"bookable price"
            )
        elif cost > MAX_PLAUSIBLE_MILEAGE:
            unreadable_cabins.append(
                f"{cabin} is marked available at {cost:,} miles, which is beyond "
                f"the {MAX_PLAUSIBLE_MILEAGE:,} plausibility ceiling - a price "
                f"nobody can book is corruption, not an award"
            )
    if unreadable_cabins:
        return [], ROW_UNREADABLE, "; ".join(unreadable_cabins)

    return [], ROW_NO_AVAILABILITY, ""


def parse_availability_row(row: Dict[str, Any]) -> List[Award]:
    """
    Turn ONE Seats.aero availability row into zero or more Awards - one per
    AVAILABLE cabin.

    (4) PREFER THE NON-RAW FIELDS; NEVER PRICE FROM RAW.
    The captured row is the argument for this all by itself: JAvailable is false
    while JAvailableRaw is true, and JMileageCost is "0" while JMileageCostRaw
    is 470500. The Raw fields carry dynamic/unreliable pricing. Scoring 470,500
    miles as a business-class price would poison a verdict with a number nobody
    can book. Raw values are kept in Award.raw_diagnostics, clearly labelled,
    and are never read by the optimizer.
    """
    route = row.get("Route") or {}
    origin = str(route.get("OriginAirport") or "").upper()
    destination = str(route.get("DestinationAirport") or "").upper()
    program, ur, source_note = resolve_source(route.get("Source"))

    raw_date = row.get("Date") or (row.get("ParsedDate") or "")[:10]
    try:
        flight_date = date.fromisoformat(str(raw_date)[:10])
    except (TypeError, ValueError):
        return []

    o_label, d_label, region_pair = _route_regions(route, origin, destination)
    distance = _as_int(route.get("Distance"))
    taxes_currency = row.get("TaxesCurrency")

    awards: List[Award] = []
    for cabin in CABINS:
        if _as_bool(row.get(f"{cabin}Available")) is not True:
            continue

        cost = _as_int(row.get(f"{cabin}MileageCost"))
        if cost is None or cost <= 0 or cost > MAX_PLAUSIBLE_MILEAGE:
            # Available but with no clean price. Emitting it with cost 0 would
            # make it look free; falling back to the Raw price would make up a
            # number; accepting 99,999,999,999,999 verbatim (finding L-3) puts a
            # figure nobody can book into a verdict. Skipped, and the caller's
            # log records it as UNREADABLE - not as an absence of award space.
            continue

        tax_cents = _as_int(row.get(f"{cabin}TotalTaxes"))
        untrusted = untrusted_tax_reason(route.get("Source"), tax_cents)
        if untrusted:
            # Nothing USABLE was reported, so nothing is carried as a reported
            # amount - a reported amount is what makes the scorer treat taxes as
            # a figure that merely needs converting. The number the API sent is
            # kept in raw_diagnostics below, labelled.
            known, src_amount, src_cur, usd, tax_note = (
                False, None, "", 0.0, untrusted
            )
        else:
            known, src_amount, src_cur, usd, tax_note = convert_taxes(
                tax_cents, taxes_currency
            )
            if not known and src_amount is not None and src_amount < 0:
                # A NEGATIVE figure is corrupt, not a figure in a currency we
                # cannot price. Leaving the amount set routed it down the
                # "unconvertible" path, which (a) told the reader USD has no FX
                # rate, and (b) kept the live rule "the figure may already contain
                # UK APD" - about a figure that contains nothing. Nothing usable
                # was reported; the note says what was.
                src_amount = None
        carriers = parse_carriers(row.get(f"{cabin}Airlines"))
        seats = _as_int(row.get(f"{cabin}RemainingSeats"))

        raw_diag = {
            "WARNING": (
                "DIAGNOSTIC ONLY. These are Seats.aero's *Raw fields, which "
                "include dynamic and unreliable pricing. They are never scored."
            ),
            f"{cabin}AvailableRaw": row.get(f"{cabin}AvailableRaw"),
            f"{cabin}MileageCostRaw": row.get(f"{cabin}MileageCostRaw"),
            f"{cabin}TotalTaxesRaw": row.get(f"{cabin}TotalTaxesRaw"),
            f"{cabin}AirlinesRaw": row.get(f"{cabin}AirlinesRaw"),
            # The clean figure is diagnostic too when it was not believed.
            f"{cabin}TotalTaxes (NOT BELIEVED)" if untrusted else f"{cabin}TotalTaxes": (
                row.get(f"{cabin}TotalTaxes")
            ),
            "TaxesCurrency": taxes_currency,
            "availability_id": row.get("ID"),
        }

        note_bits = [tax_note]
        if source_note:
            note_bits.append(source_note)
        indirect = resolve_indirect_path(route.get("Source"))
        if indirect:
            note_bits.append(f"INDIRECT UR PATH ONLY: {indirect}")
        elif ur is False:
            note_bits.append(
                f"NO CHASE UR PATH: {program} is not a Chase UR transfer partner. "
                f"This award is real and bookable, but not from a UR balance."
            )
        elif ur is None:
            note_bits.append(
                "Whether Chase UR reaches this program is UNKNOWN and is not "
                "being guessed."
            )
        if len(carriers) > 1:
            note_bits.append(
                f"OPERATING METAL AMBIGUOUS: {', '.join(carriers)} are all "
                f"possible operating carriers on this segment. No single metal "
                f"is confirmed, so the surcharge model must resolve them "
                f"together or return unknown."
            )
        elif not carriers:
            note_bits.append("No operating carrier was listed for this cabin.")

        awards.append(
            Award(
                date=flight_date,
                program=program,
                award_type=cabin,
                cost=cost,
                cash_component=usd if known else 0.0,
                airline=", ".join(carriers),
                route=f"{origin}-{destination}" if origin and destination else "",
                seats_available=seats if seats is not None else 0,
                source="seats_aero",
                source_note=" ".join(b for b in note_bits if b),
                program_source_code=str(route.get("Source") or "").strip().lower(),
                ur_transferable=ur,
                indirect_ur_path=indirect,
                candidate_carriers=carriers,
                carrier_source=(
                    "seats_aero_single"
                    if len(carriers) == 1
                    else "seats_aero_ambiguous" if carriers else "unknown"
                ),
                cash_component_known=known,
                cash_component_source_amount=src_amount,
                cash_component_currency=src_cur,
                cash_component_note=tax_note,
                origin_region=o_label,
                destination_region=d_label,
                route_region=region_pair,
                distance_miles=distance,
                direct=_as_bool(row.get(f"{cabin}Direct")),
                raw_diagnostics=raw_diag,
            )
        )

    return awards


class SeatsClient:
    """Thin wrapper around the Seats.aero Partner API."""

    BASE_URL = "https://seats.aero/partnerapi"
    CACHE: Dict[str, List[Award]] = {}  # stays under the 1,000/day rate limit
    # The provenance of each in-memory entry, kept beside it rather than
    # discarded. Without this a second call in one process returned real awards
    # with `last_fetched_at = None`, and the candidate note read "Fetched during
    # this run" over bytes fetched earlier (finding M-7).
    CACHE_META: Dict[str, Dict[str, Any]] = {}

    # Seats.aero's partner plan allows 1,000 calls/day. Pagination can burn that
    # in one careless loop, so the budget is counted and enforced here.
    DAILY_CALL_CAP = 1000
    MAX_PAGES = 25

    _calls_made = 0
    _calls_date: Optional[date] = None

    def __init__(self, api_key: Optional[str] = None):
        # v5 STEP 1. The key comes from `config.resolve_key`, not from
        # `os.getenv` here. Two sources of truth for one secret is how a banner
        # ends up naming a place the key did not come from - and the banner is
        # the only thing that makes key provenance auditable at all.
        from src import config as _config

        try:
            self.key_resolution = _config.resolve_key(api_key)
        except _config.KeyResolutionError as e:
            # Kept as ValueError: every existing caller (and `run_search`)
            # catches ValueError and reports it. The MESSAGE is the new part -
            # it names all four locations in priority order.
            raise ValueError(str(e)) from None
        self.api_key = self.key_resolution.key
        self._init_run_state()

    def _init_run_state(self) -> None:
        """
        Every `last_*` field, set to its "nothing has happened yet" value.

        Split out of `__init__` in v5 Step 3 so `SnapshotTransport` - which
        needs the same bookkeeping and NO key, because a replay asks nobody
        anything - initialises it by calling this rather than by copying it.
        Two copies of this list is how a replay ends up with a stale flag from
        a previous run attached to a number.
        """
        # Records why the last search failed, so callers can tell "no award
        # availability" apart from "we never reached the API". Conflating those
        # two is how a tool ends up silently reporting an empty result as fact -
        # and it is exactly what happened when the parser read the wrong keys.
        self.last_error: Optional[str] = None
        # What the pagination logic actually did on the last search. Never left
        # implicit: returning page one as if it were the whole result set is a
        # silent undercount.
        self.last_pagination_note: str = ""
        # MANAGER REVIEW MR-1. `RawSearchResult.incomplete` was set and then
        # reached nothing: the client never exposed it, so `live_trip.query_leg`
        # could not read it and `LiveLegOutcome` had no field for it. Coverage
        # travelled only inside the prose of `pagination_note`, which
        # `NO_AWARD_SPACE.render()` never read. These two carry it as DATA.
        self.last_incomplete: bool = False
        self.last_incomplete_reason: str = ""
        self.last_pages_fetched: int = 0
        self.last_rows_seen: int = 0
        self.last_rows_skipped: int = 0
        # v5 STEP 3, WAY (8). Provenance of the BYTES on the replay path. A
        # normal client resets these to False/"" on every search, so a live run
        # can never claim to be a replay by inheriting a stale flag.
        self.last_replayed_from_snapshot: bool = False
        self.last_snapshot_content_hash: str = ""
        self.last_snapshot_captured_at = None
        self.last_snapshot_parser_version: str = ""

    # -- rate limiting ---------------------------------------------------

    @classmethod
    def _budget_remaining(cls) -> int:
        today = date.today()
        if cls._calls_date != today:
            cls._calls_date = today
            cls._calls_made = 0
        return cls.DAILY_CALL_CAP - cls._calls_made

    @classmethod
    def _count_call(cls) -> None:
        cls._budget_remaining()
        cls._calls_made += 1

    @classmethod
    def reset_call_budget(cls) -> None:
        cls._calls_made = 0
        cls._calls_date = date.today()

    # -- pagination ------------------------------------------------------

    @staticmethod
    def _next_page_params(
        payload: Dict[str, Any], sent: Optional[Dict[str, str]] = None
    ) -> Tuple[Optional[Dict[str, str]], str]:
        """
        How to ask for the next page, plus WHY when there is no way to ask.

        Returns (params, stall_reason). A non-empty stall_reason means the
        response SAID there is more and gave us no usable way to fetch it - the
        caller must mark the result INCOMPLETE.

        PAGINATION WAS NOT OBSERVED. Tsuki's capture was truncated at 2000
        characters, so whether the response carries count / hasMore / cursor is
        genuinely unknown. This handles the documented shapes if they turn up
        and reports "single page" if they do not - what it must never do is
        return page one as though it were the whole result set without saying so.

        TWO FIXES LIVE HERE.

        H-2: the old version returned None for "hasMore with no cursor" and left
        a comment saying "let the note record the gap". The note did not record
        it - it said the response "carried hasMore and none of them indicated a
        further page", which asserts the opposite of what the payload said, and
        carried no INCOMPLETE marker. The reason now comes back with the answer
        so the note cannot contradict the payload.

        M-8: the old version echoed `skip` back UNCHANGED, so `skip: 0` with
        `hasMore: true` re-requested page one twenty-five times - 2.5% of the
        daily budget for one page of data, duplicated 25 times into `pages`. An
        offset that does not advance is not an offset scheme, and inventing one
        is exactly what this method refuses to do. It is now followed only when
        the server's own value MOVES PAST what we last sent.
        """
        has_more = _as_bool(payload.get("hasMore"))
        if has_more is None:
            has_more = _as_bool(payload.get("has_more"))
        cursor = payload.get("cursor") or payload.get("next_cursor")
        skip = payload.get("skip")

        if has_more is False:
            return None, ""
        if cursor:
            return {"cursor": str(cursor)}, ""
        if not has_more:
            return None, ""

        sent_skip = _as_int((sent or {}).get("skip")) or 0
        next_skip = _as_int(skip)
        if next_skip is not None and next_skip > sent_skip:
            return {"skip": str(next_skip)}, ""
        if next_skip is not None:
            return None, (
                f"the response says there are more results and returns "
                f"skip={next_skip}, which does not advance past the skip={sent_skip} "
                f"this request already used. An offset that does not move is not "
                f"an offset scheme, and one will not be invented"
            )
        return None, (
            "the response says there are more results (hasMore) but carries no "
            "cursor and no usable offset, so there is no way to ask for the rest"
        )

    # -- search ----------------------------------------------------------

    def search_raw(
        self,
        origin: str,
        destination: str,
        date_range: DateRange,
        *,
        cache: Optional["response_cache.ResponseCache"] = None,
        cache_ttl: Optional[int] = None,
        refresh: bool = False,
        leg_id: Optional[str] = None,
        trip_id: Optional[str] = None,
    ) -> "RawSearchResult":
        """
        TRANSPORT ONLY. Fetch the verbatim response pages. Parse NOTHING.

        v3 Step 1 split this out of `search()`. The point of the split is that
        the thing worth keeping - the bytes Seats.aero actually sent - is
        obtainable without going through a parser that has been wrong before and
        may be wrong again. `search()` is now exactly parse(search_raw(...)) and
        the 54 parser tests prove the parser body did not move.

        `cache` is OPTIONAL and defaults to OFF, so the behaviour of every
        existing caller and every existing test is unchanged. Live trip mode
        supplies one; nothing else does.

        Raises SeatsAeroError if the API cannot be reached.
        """
        request = {
            "origin_airport": origin,
            "destination_airport": destination,
            "start_date": str(date_range.from_date),
            "end_date": str(date_range.to_date),
        }
        key = response_cache.request_key("search", request)

        if cache is not None and not refresh:
            hit = cache.get(key, ttl=cache_ttl)
            if hit is not None:
                # WAY (9). The persisted provenance and the coverage recomputed
                # from the cached bytes, unioned: a value that was stored is
                # honoured, a value that was never stored (an envelope written
                # before way (9) was known) is recovered from the pages. Neither
                # half can quietly answer "complete" for the other.
                stored = response_cache.provenance_from_meta(hit.meta)
                recomputed, recomputed_why = coverage_of_pages(hit.pages)
                incomplete = bool(stored.get("incomplete")) or recomputed
                reasons = [
                    r
                    for r in (str(stored.get("incomplete_reason") or ""), recomputed_why)
                    if r
                ]
                return RawSearchResult(
                    pages=hit.pages,
                    http_status=hit.http_status,
                    pages_fetched=len(hit.pages),
                    pagination_note=(
                        f"served from the disk cache ({hit.path.name}), fetched "
                        f"{_iso_or_unknown(hit.fetched_at)}, "
                        f"{(hit.age_seconds or 0) / 60:.0f} minutes ago; NO API "
                        f"call made. Original coverage note: "
                        f"{hit.pagination_note or '(none recorded)'}"
                    ),
                    served_from_cache=True,
                    fetched_at=hit.fetched_at,
                    request=request,
                    request_key=key,
                    cache_path=hit.path,
                    # MR-1, MADE TRUE BY WAY (9). `put` now writes the coverage
                    # keys (derived from the fields the invariants read, not from
                    # a hand-maintained list), and the pages are re-asked as
                    # well. Before this, the flag was never persisted, so the
                    # SECOND run of a truncated query lost the coverage warning
                    # entirely and reported a clean finding of no award space.
                    incomplete=incomplete,
                    incomplete_reason=(
                        "; ".join(reasons)
                        if incomplete
                        else ""
                    )
                    or (
                        "the cached response is INCOMPLETE and the envelope "
                        "records no reason."
                        if incomplete
                        else ""
                    ),
                )

        pages: List[Dict[str, Any]] = []
        pagination_notes: List[str] = []
        extra_params: Dict[str, str] = {}
        http_status: Optional[int] = None
        pages_fetched = 0
        budget_exhausted = False
        incomplete = False
        # MR-1: kept SEPARATE from pagination_notes. pagination_notes is prose
        # for a human; this is the machine-readable coverage reason that has to
        # reach LiveLegOutcome.incomplete_reason.
        incomplete_reasons: List[str] = []

        try:
            for page in range(1, self.MAX_PAGES + 1):
                if self._budget_remaining() <= 0:
                    budget_exhausted = True
                    incomplete = True
                    incomplete_reasons.append(
                        f"the {self.DAILY_CALL_CAP} calls/day Seats.aero budget "
                        f"was exhausted at page {page}, so pagination stopped "
                        f"before the result set was exhausted."
                    )
                    pagination_notes.append(
                        f"STOPPED at page {page}: the {self.DAILY_CALL_CAP} "
                        f"calls/day Seats.aero budget is exhausted. The result "
                        f"below is INCOMPLETE. NO REQUEST WAS MADE - this says "
                        f"NOTHING about award space."
                    )
                    break

                params = dict(request)
                params.update(extra_params)

                self._count_call()
                response = requests.get(
                    f"{self.BASE_URL}/search",
                    headers={
                        "Partner-Authorization": self.api_key,
                        "Accept": "application/json",
                    },
                    params=params,
                    timeout=15,
                )
                response.raise_for_status()
                payload = response.json() or {}
                http_status = getattr(response, "status_code", None)
                pages_fetched = page
                pages.append(payload)

                next_params, stall_reason = self._next_page_params(payload, extra_params)
                extra_params = next_params or {}
                if not extra_params:
                    if stall_reason:
                        # H-2 / M-8: the payload SAID there is more. Saying
                        # "single page" here would assert the opposite of what
                        # the API just told us, and main.py only reddens output
                        # that carries the INCOMPLETE marker.
                        incomplete = True
                        incomplete_reasons.append(
                            f"pagination stopped after page {page} because "
                            f"{stall_reason}."
                        )
                        pagination_notes.append(
                            f"STOPPED after page {page}: {stall_reason}. The "
                            f"result below is INCOMPLETE - it is page "
                            f"{page} of an unknown number, NOT the whole result "
                            f"set, and an empty or thin result here is NOT a "
                            f"finding about award space."
                        )
                    elif page == 1:
                        markers = [
                            k for k in ("count", "hasMore", "has_more", "cursor", "skip")
                            if k in payload
                        ]
                        if markers:
                            pagination_notes.append(
                                f"single page: the response carried "
                                f"{', '.join(markers)} and none of them indicated "
                                f"a further page"
                            )
                        else:
                            pagination_notes.append(
                                "single page: the response carried NO count / "
                                "hasMore / cursor field, so there was nothing to "
                                "follow. If Seats.aero is in fact paginating this "
                                "endpoint by another mechanism, this result is "
                                "page one only - that has NOT been ruled out"
                            )
                    else:
                        pagination_notes.append(
                            f"followed pagination to the end: {page} pages fetched"
                        )
                    break
            else:
                incomplete = True
                incomplete_reasons.append(
                    f"the {self.MAX_PAGES}-page safety cap was reached while the "
                    f"API was still reporting more results."
                )
                pagination_notes.append(
                    f"STOPPED at the {self.MAX_PAGES}-page safety cap while the "
                    f"API was still reporting more results. The result below is "
                    f"INCOMPLETE."
                )
        except requests.RequestException as e:
            self.last_error = f"Seats.aero API error: {e}"
            raise SeatsAeroError(self.last_error) from e
        except (ValueError, KeyError, TypeError) as e:
            self.last_error = f"Seats.aero response could not be parsed: {e}"
            raise SeatsAeroError(self.last_error) from e

        result = RawSearchResult(
            pages=pages,
            http_status=http_status,
            pages_fetched=pages_fetched,
            pagination_note="; ".join(pagination_notes),
            served_from_cache=False,
            fetched_at=None,
            request=request,
            request_key=key,
            cache_path=None,
            budget_exhausted=budget_exhausted,
            incomplete=incomplete,
            incomplete_reason="; ".join(incomplete_reasons),
        )

        # FINDING H-1: A BUDGET FAILURE IS NOT DATA AND IS NEVER ARCHIVED.
        #
        # `cache.put` used to run unconditionally, so a run that made ZERO HTTP
        # calls because the daily cap was spent wrote a zero-page envelope to the
        # cache AND committed it to the snapshot corpus in
        # tests/fixtures/seats_aero/live_trip_b/. For the next six hours every
        # run got a cache HIT with zero pages and reported a confident finding of
        # no award space, with no API call and nothing to contradict it - and the
        # regression corpus permanently gained a fixture that records our budget
        # state rather than Seats.aero's answer.
        #
        # A request that was never sent has nothing to cache. The same applies to
        # a budget break part-way through pagination: half a result set stored
        # under a key that will be served as the whole answer is worse than a
        # miss, because a miss re-fetches.
        if cache is not None and budget_exhausted:
            cache.warnings.append(
                "The Seats.aero daily call budget was exhausted during this "
                "request, so NOTHING was written to the cache and NOTHING was "
                "archived as a snapshot. A budget failure is a fact about us, not "
                "a response from the API, and caching it would re-serve it as one."
            )
        elif cache is not None:
            try:
                written = cache.put(
                    key,
                    request,
                    pages,
                    meta={
                        "endpoint": "search",
                        "http_status": http_status,
                        "pagination_note": result.pagination_note,
                        "leg_id": leg_id,
                        "trip_id": trip_id,
                        "rows_seen": sum(len(_rows_of(p)) for p in pages),
                        # WAY (9). NOT written out by hand: every field an
                        # invariant reads is derived from the classification in
                        # models.py and copied off `result` here, so a tenth
                        # field cannot be added to an invariant and left out of
                        # the write.
                        **response_cache.provenance_meta(result),
                    },
                )
            except OSError as e:
                # FINDING M-9. The response ARRIVED, a call was spent on it, and
                # it parsed. A failure to ARCHIVE it is a storage problem, not an
                # API failure - the old code let the OSError escape into
                # query_leg's catch-all, which discarded the awards and told the
                # user Seats.aero "COULD NOT BE REACHED". That message was false
                # and the evidence was thrown away with it. ResponseCache already
                # owns a `warnings` list for exactly this class of problem.
                cache.warnings.append(
                    f"COULD NOT ARCHIVE this response ({type(e).__name__}: {e}). "
                    f"The response itself arrived and parsed normally and IS being "
                    f"used; what failed is writing it to {cache.cache_dir} and to "
                    f"the snapshot corpus. This run is therefore not reproducible "
                    f"from disk. Free space or fix permissions and re-run to "
                    f"restore the archive."
                )
                pagination_notes.append(
                    f"NOT ARCHIVED: the response could not be written to the cache "
                    f"or the snapshot corpus ({type(e).__name__}). The awards below "
                    f"are real and were parsed from a live response."
                )
                result.pagination_note = "; ".join(pagination_notes)
            else:
                result.fetched_at = written.fetched_at
                result.cache_path = written.path
                result.snapshot_name = written.meta.get("snapshot")
                result.manifest_key = str(written.meta.get("manifest_key") or "")

        return result

    # -- trips -----------------------------------------------------------

    def trips_raw(
        self,
        availability_id: str,
        *,
        cache: Optional["response_cache.ResponseCache"] = None,
        cache_ttl: Optional[int] = None,
        refresh: bool = False,
        leg_id: Optional[str] = None,
        trip_id: Optional[str] = None,
        award_date: Optional[str] = None,
        route: str = "",
    ) -> "RawTripsResult":
        """
        TRANSPORT ONLY. One GET /partnerapi/trips/{id}, verbatim. Parse NOTHING.

        Raises TripsLookupError, whose `code` is the reason, for everything that
        yields no response to read. Nothing is sent for an id that fails
        validation, or when the call budget is at 0. A call is counted BEFORE it
        is sent, on the same counter search uses, so trips can never spend past
        the cap the search loop respects.
        """
        from src import seats_trips

        if not seats_trips.valid_availability_id(availability_id):
            raise TripsLookupError(
                "AVAILABILITY_ID_INVALID",
                f"{availability_id!r} is not a plain 10-64 character "
                f"letters-and-digits availability id; no request was built from it.",
            )
        request = {"availability_id": availability_id, **seats_trips.TRIPS_REQUEST_PARAMS}
        key = response_cache.request_key("trips", request)

        if self._budget_remaining() <= 0:
            raise TripsLookupError(
                "BUDGET_EXHAUSTED",
                f"{self._budget_remaining()} of {self.DAILY_CALL_CAP} calls left "
                f"in this process",
            )

        self._count_call()
        try:
            response = requests.get(
                f"{self.BASE_URL}/trips/{availability_id}",
                headers={
                    "Partner-Authorization": self.api_key,
                    "Accept": "application/json",
                },
                params=dict(seats_trips.TRIPS_REQUEST_PARAMS),
                timeout=15,
            )
        except requests.Timeout as e:
            raise TripsLookupError(
                "TIMEOUT", f"{type(e).__name__}: {e}", request_sent=True
            ) from e
        except requests.RequestException as e:
            raise TripsLookupError(
                "TRANSPORT_ERROR", f"{type(e).__name__}: {e}", request_sent=True
            ) from e

        status = getattr(response, "status_code", None)
        if isinstance(status, bool) or not isinstance(status, int):
            raise TripsLookupError(
                "HTTP_ERROR",
                f"the response carried no HTTP status ({status!r})",
                request_sent=True,
            )
        if status == 404:
            raise TripsLookupError(
                "HTTP_404", "HTTP 404 Not Found", request_sent=True, http_status=404
            )
        if status == 429:
            raise TripsLookupError(
                "HTTP_429",
                "HTTP 429 Too Many Requests",
                request_sent=True,
                http_status=429,
            )
        if not 200 <= status < 300:
            raise TripsLookupError(
                "HTTP_ERROR", f"HTTP {status}", request_sent=True, http_status=status
            )
        try:
            payload = response.json()
        except ValueError as e:
            raise TripsLookupError(
                "JSON_ERROR",
                f"{type(e).__name__}: {e}",
                request_sent=True,
                http_status=status,
            ) from e
        text = getattr(response, "text", "")
        incomplete, why = seats_trips.trips_coverage(payload)
        return RawTripsResult(
            payload=payload,
            http_status=status,
            served_from_cache=False,
            fetched_at=None,
            request=request,
            request_key=key,
            incomplete=incomplete,
            incomplete_reason=why,
            raw_text=text if isinstance(text, str) else "",
            request_sent=True,
        )

    @staticmethod
    def parse_pages_detail(pages: List[Dict[str, Any]]) -> "ParsedPages":
        """
        PARSING ONLY, with the two kinds of failure kept apart. No network call.

        This is `parse_pages` with the one distinction that finding C-1 turned
        on: a row we READ that has nothing bookable, versus a row we COULD NOT
        READ. The old return shape - one `rows_skipped` integer - could not
        express it, so nine unreadable rows and nine genuinely empty ones were
        the same number, and the caller announced both as a finding of no award
        space.
        """
        out = ParsedPages()
        for payload in pages:
            shape_error = envelope_shape_error(payload)
            if shape_error:
                # The page itself is the wrong shape. It is ONE unreadable unit,
                # not zero rows - and emphatically not `len("no results")` rows.
                out.rows_unreadable += 1
                out.add_reason(shape_error)
                continue
            rows = _rows_of(payload)
            out.rows_seen += len(rows)
            for row in rows:
                parsed, status, why = parse_availability_row_detail(row)
                if status == ROW_UNREADABLE:
                    out.rows_unreadable += 1
                    out.add_reason(why)
                elif status == ROW_NO_AVAILABILITY:
                    out.rows_without_availability += 1
                out.awards.extend(parsed)
        return out

    @staticmethod
    def parse_pages(pages: List[Dict[str, Any]]) -> Tuple[List[Award], int, int]:
        """
        Back-compatible 3-tuple wrapper: (awards, rows_seen, rows_skipped).

        `rows_skipped` keeps its original meaning - rows that produced no award
        for ANY reason. Anything that has to tell the two reasons apart must use
        `parse_pages_detail`; this shape provably cannot.
        """
        detail = SeatsClient.parse_pages_detail(pages)
        return detail.awards, detail.rows_seen, detail.rows_skipped

    def search(
        self,
        origin: str,
        destination: str,
        date_range: DateRange,
        airlines: Optional[List[str]] = None,
        *,
        cache: Optional["response_cache.ResponseCache"] = None,
        cache_ttl: Optional[int] = None,
        refresh: bool = False,
        leg_id: Optional[str] = None,
        trip_id: Optional[str] = None,
    ) -> List[Award]:
        """
        Search for award availability on a route and date range.

        Exactly `parse_pages(search_raw(...))` since v3 Step 1. Returns one Award
        PER AVAILABLE CABIN, not one per response row. Raises SeatsAeroError if
        the API cannot be reached or parsed.

        The in-memory CACHE below sits ABOVE the disk cache: it short-circuits to
        parsed Awards for a repeat call inside one process, which is a different
        job from replaying raw bytes across processes and across parser versions.

        FINDING M-7, TWO BUGS IN THAT SHORT-CIRCUIT.

        First, it ran BEFORE `search_raw`, which is where `refresh` is handled -
        so within one process `--refresh` was a no-op and returned the stale
        price while reporting "served from cache; no API call made". A flag whose
        acceptance criterion is "forces a re-fetch" must reach the code that
        fetches.

        Second, the hit stored only the Awards, so `last_fetched_at` came back
        None and `last_served_from_cache` came back False. `award_to_candidate`
        then stamped "Fetched during this run" onto bytes that were not fetched
        during this run, and the outcome claimed it had not been served from a
        cache. A live price that renders without its true fetched-at timestamp
        violates v3's own honesty invariant, so the provenance is now cached
        alongside the awards and replayed with them.
        """
        self.last_error = None
        self.last_pagination_note = ""
        # v5 STEP 3. Reset before anything else, so a live search can never
        # inherit a replay flag from an earlier call on the same object.
        self.last_replayed_from_snapshot = False
        self.last_snapshot_content_hash = ""
        self.last_snapshot_captured_at = None
        self.last_snapshot_parser_version = ""
        self.last_incomplete = False
        self.last_incomplete_reason = ""
        self.last_pages_fetched = 0
        self.last_rows_seen = 0
        self.last_rows_skipped = 0
        self.last_rows_unreadable = 0
        self.last_rows_without_availability = 0
        self.last_unreadable_reasons = []
        self.last_snapshot_name = None
        self.last_served_from_cache = False
        self.last_fetched_at = None
        self.last_request_key = ""
        self.last_budget_exhausted = False
        self.last_manifest_key = ""

        cache_key = f"{origin}-{destination}-{date_range.from_date}-{date_range.to_date}"
        if cache_key in self.CACHE and not refresh:
            entry = self.CACHE_META.get(cache_key, {})
            self.last_rows_seen = entry.get("rows_seen", 0)
            self.last_rows_skipped = entry.get("rows_skipped", 0)
            self.last_rows_unreadable = entry.get("rows_unreadable", 0)
            self.last_rows_without_availability = entry.get(
                "rows_without_availability", 0
            )
            self.last_unreadable_reasons = list(entry.get("unreadable_reasons", []))
            self.last_snapshot_name = entry.get("snapshot_name")
            self.last_fetched_at = entry.get("fetched_at")
            self.last_request_key = entry.get("request_key", "")
            # MR-1 / WAY (9). The in-process short-circuit replays a PARSED
            # result. If the bytes behind it were truncated, the replay is
            # truncated too, and dropping the flag here would make the second
            # call in a process report a clean finding where the first reported
            # an incomplete one. Read back through the SAME derived key set the
            # write above uses.
            carried = {k: entry.get(k) for k in models.PERSISTED_PROVENANCE_KEYS}
            self.last_incomplete = bool(carried.get("incomplete", False))
            self.last_incomplete_reason = str(carried.get("incomplete_reason") or "")
            self.last_served_from_cache = True
            self.last_pagination_note = (
                f"served from the IN-PROCESS cache; no API call made. The bytes "
                f"behind this answer were fetched "
                f"{_iso_or_unknown(entry.get('fetched_at'))}. Original coverage "
                f"note: {entry.get('pagination_note') or '(none recorded)'}"
            )
            return self._filter(self.CACHE[cache_key], airlines)

        raw = self.search_raw(
            origin,
            destination,
            date_range,
            cache=cache,
            cache_ttl=cache_ttl,
            refresh=refresh,
            leg_id=leg_id,
            trip_id=trip_id,
        )
        parsed = self.parse_pages_detail(raw.pages)
        awards = parsed.awards

        self.last_pages_fetched = raw.pages_fetched
        self.last_rows_seen = parsed.rows_seen
        self.last_rows_skipped = parsed.rows_skipped
        self.last_rows_unreadable = parsed.rows_unreadable
        self.last_rows_without_availability = parsed.rows_without_availability
        self.last_unreadable_reasons = list(parsed.unreadable_reasons)
        # Provenance of the BYTES, carried up so a caller can report it without
        # reaching back into the transport layer. A live price that renders
        # without its fetched-at timestamp is an honesty invariant violation, so
        # the timestamp has to survive the trip from the cache to the formatter.
        self.last_snapshot_name = raw.snapshot_name
        self.last_served_from_cache = raw.served_from_cache
        self.last_fetched_at = raw.fetched_at
        self.last_request_key = raw.request_key
        self.last_budget_exhausted = raw.budget_exhausted
        self.last_manifest_key = raw.manifest_key
        self.last_incomplete = raw.incomplete
        self.last_incomplete_reason = raw.incomplete_reason

        pagination_notes = [raw.pagination_note] if raw.pagination_note else []
        if parsed.rows_without_availability:
            pagination_notes.append(
                f"{parsed.rows_without_availability} of {parsed.rows_seen} rows "
                f"were read successfully and carried no cabin marked available "
                f"with a clean mileage price"
            )
        if parsed.rows_unreadable:
            # NEVER a quiet statistic. An unreadable row means the tool does not
            # know what the API said, and that has to travel with the result.
            pagination_notes.append(
                f"UNREADABLE: {parsed.rows_unreadable} row(s)/page(s) COULD NOT BE "
                f"PARSED ("
                + "; ".join(parsed.unreadable_reasons)
                + "). Nothing can be concluded about award space from them."
            )
        self.last_pagination_note = "; ".join(pagination_notes)

        # A budget failure is not an observation and must not be replayed as one,
        # in this process any more than on disk (finding H-1).
        if not raw.budget_exhausted:
            self.CACHE[cache_key] = awards
            self.CACHE_META[cache_key] = {
                "rows_seen": parsed.rows_seen,
                "rows_skipped": parsed.rows_skipped,
                "rows_unreadable": parsed.rows_unreadable,
                "rows_without_availability": parsed.rows_without_availability,
                "unreadable_reasons": list(parsed.unreadable_reasons),
                "snapshot_name": raw.snapshot_name,
                "request_key": raw.request_key,
                "pagination_note": self.last_pagination_note,
                # WAY (9). THE IN-PROCESS CACHE IS A STORAGE LAYER TOO, and it
                # gets the same derived key set as the disk cache and the
                # snapshot - by name, so a tenth carried field lands here
                # without anyone editing this dict.
                **{k: getattr(raw, k) for k in models.PERSISTED_PROVENANCE_KEYS},
                # Bytes fetched THIS run carry no cache timestamp of their own,
                # so record when we saw them. The replay must not claim they were
                # fetched during whatever run reads this next.
                "fetched_at": raw.fetched_at or datetime.now(timezone.utc),
            }
        return self._filter(awards, airlines)

    @staticmethod
    def _filter(awards: List[Award], airlines: Optional[List[str]]) -> List[Award]:
        """
        Filter by carrier.

        An award whose metal is ambiguous matches if ANY of its possible
        carriers is requested - the filter is "could this be on one of these
        airlines", which is the only question the data can answer.
        """
        if not airlines:
            return awards
        wanted = {str(a).strip().upper() for a in airlines}
        return [
            a
            for a in awards
            if wanted & set(a.candidate_carriers) or a.airline in airlines
        ]

    def clear_cache(self):
        """Clear the in-process response cache and its provenance."""
        self.CACHE.clear()
        self.CACHE_META.clear()
