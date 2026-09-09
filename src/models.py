"""Data models for points transfer optimizer."""
import dataclasses
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class Ratio:
    """Transfer ratio with date and card scoping."""

    from_program: str
    to_program: str
    card_dependency: str  # "all", "other", or an exact card name
    ratio_numerator: int
    ratio_denominator: int
    effective_from: date
    effective_to: date

    def is_active_on(self, check_date: date) -> bool:
        """Date-window check only, ignoring card."""
        return self.effective_from <= check_date <= self.effective_to

    def is_valid_on(
        self,
        check_date: date,
        card: Optional[str] = None,
        explicitly_named_cards: Optional[frozenset] = None,
    ) -> bool:
        """
        Check if this ratio applies on a given date for a given card.

        Card matching is EXACT (bug #4 in the test report: the old code used
        `card_dependency in card`, a substring test).

        `explicitly_named_cards` is the set of card names that appear in other
        rows for the same (from_program, to_program) pair active on this date.
        It is what gives "other" its meaning: "other" = any card that is not one
        of the specifically-named ones. Previously this was hardcoded to the
        single hardcoded card name.
        """
        if not self.is_active_on(check_date):
            return False

        if self.card_dependency == "all":
            return True

        if self.card_dependency == "other":
            if card is None:
                return False
            named = explicitly_named_cards or frozenset()
            return card not in named

        if card is None:
            return False

        # Exact card match - NOT substring.
        return self.card_dependency == card

    def apply(self, points: int) -> int:
        """
        Apply ratio to points, discarding any remainder.

        Ratio is from_program:to_program. 4:3 means 4 source points buy 3
        destination points, so multiply by 3/4 and floor.
        """
        return (points * self.ratio_denominator) // self.ratio_numerator

    def source_needed_for(self, target_points: int) -> int:
        """
        Minimum source points required to end up with at least `target_points`.

        Inverse of apply(), rounded UP: at 4:3, 90,000 Hyatt needs 120,000 UR.
        """
        num, den = self.ratio_numerator, self.ratio_denominator
        return -(-target_points * num // den)  # ceil division

    def remainder_for(self, source_points: int) -> int:
        """
        Source points that produce nothing because of floor rounding.

        At 4:3, transferring 10 source points yields 7 destination points; 10
        source points would have been needed for 7 (ceil(7*4/3)=10), so the
        remainder is 0 here. At 4:3 with 11 source points you still get 8
        (11*3//4 = 8), and 8 needs ceil(8*4/3) = 11, remainder 0. The remainder
        is non-zero when the trailing source points are not enough to buy one
        more destination point.
        """
        delivered = self.apply(source_points)
        return source_points - self.source_needed_for(delivered)

    @property
    def as_string(self) -> str:
        return f"{self.ratio_numerator}:{self.ratio_denominator}"


@dataclass
class Bonus:
    """Transfer bonus (time-boxed promotion)."""

    from_program: str
    to_program: str
    bonus_percent: float  # e.g. 25 for 25%
    effective_from: date
    effective_to: date
    notes: str = ""

    def is_active_on(self, check_date: date) -> bool:
        return self.effective_from <= check_date <= self.effective_to

    def apply(self, points: int) -> int:
        return int(points * (1 + self.bonus_percent / 100))


@dataclass
class Award:
    """Flight or hotel award availability."""

    date: date
    program: str
    award_type: str  # "J", "F", "W", "Y", or "HOTEL"
    cost: int  # Points cost in the program's own currency
    cash_component: float  # Surcharge / taxes / fees payable in cash
    airline: str
    route: str
    seats_available: int = 1
    # Where this award number came from. This is load-bearing for honesty:
    # "seats_aero" means confirmed award space; "google_badge_unverified" means
    # a Google Flights points badge, which often reflects dynamic revenue
    # pricing rather than partner saver space.
    source: str = "seats_aero"
    source_note: str = ""

    # --- Seats.aero real-schema additions (2026-09-08) --------------------
    # Added after the FIRST real Seats.aero response was captured. The v0/v1
    # parser read flat `cost` / `taxes` / `Source` / `Carriers` keys that do not
    # exist in the real payload, so it emitted nothing from a response full of
    # data and that emptiness was reported as "no award availability".

    # The lowercase Seats.aero source code the award came from ("aeroplan"),
    # kept beside the resolved program name so a mapping error is visible.
    program_source_code: str = ""
    # False means "this award is real but there is no Chase UR path into the
    # program". That is a REPORTABLE result, not a reason to drop the row.
    ur_transferable: Optional[bool] = None

    # `{X}Airlines` is a comma-separated list of POSSIBLE operating carriers.
    # One entry does not make it confirmed metal; several make the metal
    # genuinely ambiguous. `airline` above is the display string.
    candidate_carriers: List[str] = field(default_factory=list)
    # "seats_aero_single" | "seats_aero_ambiguous" | "unknown"
    carrier_source: str = "unknown"

    # Taxes as the API states them: an amount in `cash_component_currency`,
    # already divided out of the integer-cents field. `cash_component` is the
    # USD conversion and is ONLY meaningful when cash_component_known is True.
    # An unconvertible currency degrades to unknown - never to 0.0.
    #
    # MANAGER REVIEW MR-5. THIS DEFAULT USED TO BE `True`, WHICH IS THE UNSAFE
    # VALUE. An honesty flag must not default to "yes, I know". A Google-badge
    # award built with `cash_component=0.0` and no tax data was therefore a
    # KNOWN zero and rendered `$0.00` in the HTML export with no marker - which
    # is precisely v0's bug, silence rendering as a confirmed free, in a
    # codebase whose own comment two files away reads "`cash_surcharge: 0.0`
    # with no flag is silence, not a real zero". It sat on the
    # --origin/--destination single-route path, which the adversarial round
    # never attacked.
    #
    # `False` is the safe default: an Award nobody has told about taxes does not
    # know its cash component. Every construction site that DOES know now says
    # so explicitly, so the answer is stated rather than inherited.
    cash_component_known: bool = False
    cash_component_source_amount: Optional[float] = None
    cash_component_currency: str = "USD"
    cash_component_note: str = ""

    # From Route. OriginRegion/DestinationRegion feed the surcharge model's
    # route-region tier; distance is carried for display only.
    origin_region: str = ""
    destination_region: str = ""
    route_region: str = ""  # normalized pair, e.g. "NA-EU"
    distance_miles: Optional[int] = None
    direct: Optional[bool] = None

    # Raw/dynamic-pricing fields, retained for DIAGNOSTICS ONLY and never
    # scored. `JMileageCostRaw: 470500` on a row whose JAvailable is false is
    # exactly the phantom number this fence exists to keep out of a verdict.
    raw_diagnostics: Dict[str, object] = field(default_factory=dict)

    @property
    def is_verified(self) -> bool:
        return self.source == "seats_aero"

    @property
    def has_known_metal(self) -> bool:
        """
        True only when exactly one operating carrier is possible.

        A four-airline `YAirlines` list is not metal. Picking the first entry
        would produce a confident surcharge for an aeroplane nobody has
        confirmed you would be on.
        """
        return self.carrier_source == "seats_aero_single" and len(
            self.candidate_carriers
        ) == 1


@dataclass
class Transfer:
    """A single transfer operation from one program to another."""

    from_program: str
    to_program: str
    amount: int  # Source points moved
    ratio: str  # "1:1", "4:3" - for display
    ratio_numerator: int
    ratio_denominator: int
    card_used: Optional[str] = None
    points_delivered: int = 0  # Destination points actually received


@dataclass
class TransferPath:
    """A sequence of transfers to reach a target program."""

    transfers: List[Transfer] = field(default_factory=list)
    total_transferred: int = 0  # Source points moved
    target_points_received: int = 0  # Destination points delivered by transfers
    existing_target_points_used: int = 0  # Destination points already held & used
    stranded_points: int = 0  # Destination points delivered but NOT used
    ratio_remainder_points: int = 0  # Source points lost to floor rounding
    feasible: bool = True
    infeasible_reason: str = ""

    def add_transfer(self, transfer: Transfer):
        self.transfers.append(transfer)
        self.total_transferred += transfer.amount
        self.target_points_received += transfer.points_delivered

    @property
    def total_points_cost(self) -> int:
        """
        Total points this path costs the user. SINGLE-CURRENCY DISPLAY ONLY.

        NOT LOAD-BEARING AS OF v1. This adds source points to destination points
        and, across a multi-currency path, would add points from different
        issuers - a category error the moment two ratios differ. v0 ranked on
        this. v1 ranks on FundingPlan.score_usd (USD, at each currency's own
        valuation) and reports raw per-currency amounts separately.

        It is kept because a single-currency path's summary line is more readable
        with one number in it, and every current UR path is single-currency.
        Do not reintroduce it into a ranking key.
        """
        return self.total_transferred + self.existing_target_points_used

    @property
    def total_waste(self) -> int:
        """All points that go in and buy nothing."""
        return self.stranded_points + self.ratio_remainder_points

    def summary(self) -> str:
        if not self.transfers:
            if self.existing_target_points_used:
                return f"No transfer (used {self.existing_target_points_used:,} existing)"
            return "No transfers"
        parts = [
            f"{t.from_program} {t.amount:,} -> {t.to_program} {t.points_delivered:,} @ {t.ratio}"
            + (f" [{t.card_used}]" if t.card_used else "")
            for t in self.transfers
        ]
        return " + ".join(parts)


# ---------------------------------------------------------------------------
# v1: typed explanations
# ---------------------------------------------------------------------------


# Every Reason attached to a rendered plan must carry one of these codes. Tests
# assert CODES, not prose, so an explanation can be reworded without breaking the
# suite - and an explanation bug (v0 reported "not a UR partner" for a leg that
# was really blocked by the stranding constraint) becomes a test failure.
REASON_CODES = frozenset(
    {
        "RATIO_CHOSEN",
        "CARD_CHOSEN",
        "SURCHARGE_CAPTURED",
        "SURCHARGE_MODELED",
        "SURCHARGE_UNKNOWN",
        "SURCHARGE_BASIS_CONVERTED",
        "CARRIER_UNKNOWN",
        "SPLIT_REQUIRED",
        "SPLIT_REJECTED",
        "STRANDING_REJECTED",
        "BALANCE_REJECTED",
        "DEDUP_DROPPED",
        "VERDICT_SENSITIVE",
        "TRANSFER_DATE_WINDOW",
        "NOT_A_PARTNER",
        "MANDATORY_FEE",
        "MANUAL_CAPTURE",
        "FX_PLACEHOLDER",
        "ALTERNATIVE_UNPRICED",
        # v3 fix. An award the response did not attribute to a program. NOT the
        # same claim as NOT_A_PARTNER, which asserts something about the user's
        # transfer partners; this one asserts something about the response.
        "PROGRAM_UNATTRIBUTED",
        # v3 fix. The trip-level balance ceiling demoted this leg to cash
        # because earlier legs had already spent the shared balance.
        "TRIP_BALANCE_EXHAUSTED",
        # v3 fix. The leg was scored against a fare captured for a DIFFERENT
        # date than the leg's own, because the award is for that date.
        "DATE_SHIFTED_SCORING",
        # v5 Step 7. UK Air Passenger Duty was ADDED to this leg's points-side
        # cash total. Offline/badge legs only.
        "APD_ADDED",
        # v5 Step 7. The leg departs the UK and owes APD, and the amount was
        # NOT added because the leg's cash figure came from Seats.aero's
        # TotalTaxes and nobody has checked whether that already contains it.
        "APD_INCLUSION_UNVERIFIED",
        # v5 Step 7. The leg departs the UK and the amount owed is UNKNOWN -
        # not zero, and not the nearest band.
        "APD_UNKNOWN",
    }
)


@dataclass
class Reason:
    """Typed, machine-checkable explanation for something the optimizer did."""

    code: str
    detail: str
    data: Dict[str, object] = field(default_factory=dict)

    def __post_init__(self):
        if self.code not in REASON_CODES:
            raise ValueError(
                f"Unknown Reason code {self.code!r}. Add it to REASON_CODES in "
                f"models.py - an untyped reason cannot be asserted in a test."
            )


# ---------------------------------------------------------------------------
# v1: surcharges and mandatory fees
# ---------------------------------------------------------------------------


@dataclass
class SurchargeEstimate:
    """
    A carrier-imposed surcharge (YQ/YR) plus whatever stacks on it.

    `confidence == "unknown"` is a first-class state and is NOT zero. v0's single
    worst bug was treating an absent surcharge as $0, which scored two British
    Airways awards as free and produced a headline saving that does not exist.
    An unknown surcharge is scored as unknown and reported as a break-even.
    """

    amount_low: float
    amount_point: float
    amount_high: float
    currency: str = "USD"
    basis: str = "round_trip"  # "round_trip" | "one_way" | "per_segment"
    confidence: str = "unknown"  # "captured" | "modeled" | "unknown"
    matched_rule: str = ""
    source: str = ""
    verified_on: Optional[date] = None
    notes: str = ""

    @property
    def is_known(self) -> bool:
        return self.confidence != "unknown"

    @property
    def is_range(self) -> bool:
        return self.is_known and self.amount_low != self.amount_high

    @classmethod
    def unknown(cls, why: str = "", **kw) -> "SurchargeEstimate":
        """
        The only way to build an unknown. Note the amounts are NOT 0.0-as-a-value:
        they are placeholders that `is_known` guards, and every consumer must
        check `is_known` before reading them.
        """
        return cls(
            amount_low=0.0,
            amount_point=0.0,
            amount_high=0.0,
            confidence="unknown",
            matched_rule="",
            source="no matching surcharge rule",
            notes=why,
            **kw,
        )

    def render(self) -> str:
        if not self.is_known:
            return "UNKNOWN"
        if self.is_range:
            return (
                f"${self.amount_low:,.0f}-${self.amount_high:,.0f} "
                f"(pt ${self.amount_point:,.0f})"
            )
        return f"${self.amount_point:,.2f}"


@dataclass
class MandatoryFee:
    """
    Cash owed regardless of how the booking is paid for.

    The hotel analogue of a carrier surcharge and exactly the same bug shape: v0
    stored the Marriott destination fee and the Amsterdam city tax as free text
    on the CASH option, so they never entered any total and were invisible on the
    points side - where they are still owed.
    """

    label: str
    amount: float
    currency: str = "USD"
    per: str = "stay"  # "night" | "stay" | "person_night"
    payable_on_points: bool = True
    source: str = "captured"

    def total_for(self, nights: int, travelers: int = 1) -> float:
        """Total in the fee's own currency for a stay of `nights`."""
        if self.per == "night":
            return self.amount * max(nights, 0)
        if self.per == "person_night":
            return self.amount * max(nights, 0) * max(travelers, 1)
        return self.amount


@dataclass
class DateRange:
    """Date range for availability search."""

    from_date: date
    to_date: date

    def contains(self, check_date: date) -> bool:
        return self.from_date <= check_date <= self.to_date


@dataclass
class Trip:
    """User trip specification."""

    origin: str
    destination: str
    date_range: DateRange
    # A balance of None means UNCONSTRAINED - the user has not told us how many
    # points they hold, so no feasibility ceiling is applied.
    balances: Dict[str, Optional[int]] = field(default_factory=dict)
    cards_held: List[str] = field(default_factory=list)
    num_passengers: int = 1
    description: str = ""


@dataclass
class Strategy:
    """A ranked strategy (what to transfer, what award, total cost)."""

    award: Award
    transfer_path: TransferPath
    points_cost: int
    cash_cost: float
    total_value: float  # points_cost * valuation_cpp + cash_cost
    # False when the award's cash component could not be converted to USD (an
    # unsupported tax currency, or no tax figure at all). `total_value` is then
    # a LOWER BOUND, not a total: the missing cash can only push it up. Scoring
    # an unconvertible tax as $0 is the v0 surcharge bug wearing a new hat.
    #
    # MR-5, SAME SHAPE ONE LAYER UP. This default was `True` for the same reason
    # Award.cash_component_known was, and is unsafe for the same reason: a
    # Strategy built without stating its answer claimed to know its cash side
    # and `export_html` rendered a bare `$0.00`. `optimizer` always passes it
    # explicitly; the default is what protects every OTHER construction site,
    # including v5's.
    cash_cost_known: bool = False
    cash_cost_note: str = ""

    @property
    def is_lower_bound(self) -> bool:
        return not self.cash_cost_known

    def margin_vs_human(self, human_cost: int) -> float:
        if human_cost == 0:
            return 0.0
        return (self.points_cost - human_cost) / human_cost * 100


# ---------------------------------------------------------------------------
# Leg-level models: a real itinerary is a sequence of legs, each of which can be
# paid for with points OR cash. The cash option always exists.
# ---------------------------------------------------------------------------


@dataclass
class CashOption:
    """A cash price for a leg, as captured from a booking site."""

    label: str  # "American MRY-JFK 1 stop PHX"
    amount: float  # Amount in `currency`
    currency: str = "USD"
    amount_usd: float = 0.0  # Filled in by the evaluator via config FX
    notes: str = ""
    # Fees that CANNOT be paid with points and are therefore owed on every
    # option, points or cash (e.g. the Marriott daily destination fee).
    unavoidable_cash_note: str = ""

    # --- v3 -------------------------------------------------------------
    # Where this price came from, and when. ABSENT MEANS "unknown", never an
    # assumed capture: a screenshot somebody took is a different kind of fact
    # from a number of unrecorded origin, and v2 could not say which it had.
    source: str = "unknown"  # captured_screenshot|captured_booking_page|unknown
    captured_on: Optional[date] = None
    # The travel date this cash price is FOR. Normally the leg's own date, and
    # None means exactly that. It exists so a leg can carry cash prices for
    # SEVERAL dates - which is the only honest way an off-date award can ever
    # enter the trip margin (plan section 4.5). Screenshots remain the source of
    # truth for cash; this just lets there be more than one of them.
    date: Optional[date] = None

    @property
    def is_foreign(self) -> bool:
        return self.currency.upper() != "USD"


@dataclass
class PointsCandidate:
    """
    A candidate points price for a leg, before transfer maths.

    `program` is the loyalty program the points would be spent in. `points` is
    the price in that program's currency. `source` records provenance and is
    printed in the output.
    """

    label: str
    program: str
    points: int
    cash_surcharge: float = 0.0
    surcharge_currency: str = "USD"
    source: str = "google_badge_unverified"
    source_note: str = ""
    program_attribution_assumed: bool = False

    # --- v1 additions ----------------------------------------------------
    # v0's Award.airline was ambiguous (marketing or operating carrier?) and
    # PointsCandidate had no carrier at all. That ambiguity is exactly what hid
    # the BA surcharge: a surcharge is a function of the METAL, not of who sold
    # you the ticket.
    operating_carrier: str = ""  # IATA of the metal, e.g. "BA", "IB", "EI"
    marketing_carrier: str = ""  # who sells it; may differ
    carrier_source: str = "unknown"  # "seats_aero" | "captured" | "assumed" | "unknown"
    cabin: str = "Y"  # Y | W | J | F
    is_round_trip: bool = False
    # A surcharge read off a real booking page. When set, it OVERRIDES the
    # modeled table (captured > modeled > unknown). `surcharge_captured=False`
    # with cash_surcharge=0.0 is NOT a captured zero - it is simply no data.
    surcharge_captured: bool = False
    # Hotel-only: award price expressed per night.
    points_per_night: Optional[int] = None
    nights: int = 0

    # --- v3 fix: TAXES AND SURCHARGE ARE TWO DIFFERENT QUANTITIES -----------
    #
    # ADVERSARIAL FINDINGS C-2, M-3 AND M-4 ARE ONE BUG: `cash_surcharge` was
    # doing two jobs. It held the CARRIER-IMPOSED SURCHARGE (a modeled quantity,
    # a property of the metal) and it also held THE API'S REPORTED TAXES (an
    # observed quantity, a property of the itinerary) - and when it could not do
    # the second job it was set to 0.0 and quietly did neither.
    #
    # What that cost: an Aeroplan award with TaxesCurrency "MXN" and MXN 9,000
    # of real taxes had the amount dropped to 0.0 because no FX rate exists for
    # MXN, then picked up the tier-5 program-policy $0 from the modeled table and
    # scored as a fully known $500 against $520 cash - POINTS, margin $20,
    # verdict_sensitive False, no floor shown. The true cost is about $950. That
    # is v0's `unknown -> $0` bug on a new axis, and the fields below exist so it
    # cannot be expressed any more.
    #
    # `cash_surcharge` now means ONE thing: a captured CARRIER SURCHARGE.
    # Everything the API said about taxes lives here, and an unpriceable tax
    # propagates as UNKNOWN all the way to the verdict.
    observed_taxes_usd: float = 0.0
    # False means the figure could not be converted to USD. It NEVER means $0.
    observed_taxes_known: bool = False
    # True when the API reported a tax figure at all - convertible or not. The
    # difference between "no tax figure" and "a tax figure we cannot price"
    # matters: only the second one makes a leg unscoreable.
    observed_taxes_reported: bool = False
    observed_taxes_amount: Optional[float] = None
    observed_taxes_currency: str = ""
    observed_taxes_note: str = ""
    # True in exactly the program-policy-$0 case, where the API's taxes ARE the
    # whole carrier-side cash figure and therefore ride as `cash_surcharge`.
    # It exists to stop the taxes being added TWICE downstream.
    observed_taxes_are_the_surcharge: bool = False
    # The API reported taxes and they cannot be priced in USD. This forces the
    # candidate's surcharge to UNKNOWN regardless of what the modeled table
    # says - including a program-policy $0, which is a statement about the
    # CARRIER SURCHARGE and says nothing about taxes the tool cannot convert.
    taxes_unconvertible: bool = False
    # The travel date this candidate's award is FOR. None means the leg's own
    # date. It exists so a promoted off-date award is scored against the cash
    # fare for ITS date rather than the cheapest fare on any date (finding H-5).
    award_date: Optional[date] = None
    # True when the response named no program, or named a source code this tool
    # does not map. The award is real; its program is unattributable, and saying
    # "no transfer partner covers this leg" about it is a claim we cannot make
    # (finding M-5).
    program_attribution_missing: bool = False

    @property
    def extra_observed_taxes_usd(self) -> float:
        """
        Observed taxes NOT already counted inside `cash_surcharge`.

        Every consumer that builds a points-side total adds this. It is 0.0 both
        when there are no taxes and when they are already riding as the captured
        surcharge, so it can never double count - and it is 0.0 when the taxes
        are UNKNOWN, which is why an unknown must also set
        `taxes_unconvertible` and route the leg down the unscoreable path.
        """
        if self.observed_taxes_are_the_surcharge or not self.observed_taxes_known:
            return 0.0
        return self.observed_taxes_usd

    @property
    def has_known_metal(self) -> bool:
        """
        Whether we know which airline actually flies this.

        An assumed carrier is NOT known metal. The tool never guesses metal: a
        wrong guess produces a confident wrong surcharge, which is worse than an
        honest unknown.
        """
        return bool(self.operating_carrier) and self.carrier_source in (
            "seats_aero",
            "captured",
        )


# ---------------------------------------------------------------------------
# v3: live trip mode. The anti-collapse types.
# ---------------------------------------------------------------------------
#
# THIS PROJECT HAS MADE THE SAME MISTAKE TWICE.
#
#   v0: an absent surcharge row was scored as $0, so two British Airways awards
#       came out free and the trip headline said 16%.
#   v2: a parser reading keys that do not exist produced zero awards from a
#       response containing a real, bookable, 9-seat Aeroplan award - and that
#       emptiness was reported to the user as "no award availability".
#
# Both are the same shape: A FAILURE PRESENTED AS A FINDING. The mechanism that
# allowed it both times was a return value that could not tell the two apart -
# an empty list, a missing row, a `(results, last_error)` pair where nobody
# checked the second element.
#
# So live mode does not return a list. It returns a LiveLegOutcome whose state
# is one of five named things, and whose constructor REFUSES to build the
# combinations that would let the collapse happen again. NO_AWARD_SPACE means
# the API answered and the answer was "nothing" - that is a finding about the
# world. API_ERROR means we never got an answer - that is a finding about us,
# and it says NOTHING about award space.


class PointsProvenance(str, Enum):
    """
    Where a leg's points price came from. FOUR REPORTED states.

    SNAPSHOT IS THE v5 ADDITION AND IT IS A SEPARATE VALUE ON PURPOSE. A
    replayed run reuses `LIVE`'s bytes and none of its currency: the answer was
    true when it was captured and says nothing about today. Reusing `LIVE` with
    a flag beside it was the rejected alternative, and it was rejected because
    the provenance string is the thing that gets QUOTED - a replayed number that
    calls itself live is exactly the laundering `--from-snapshot` exists to
    prevent.
    """

    LIVE = "live"                    # queried Seats.aero; scoreable awards found
    SNAPSHOT = "snapshot"            # replayed from committed bytes. NOT live.
    BADGE_FALLBACK = "badge_fallback"  # fixture badge kept; live did not supply
    UNAVAILABLE = "unavailable"      # no points side at all for this leg


class LiveQueryState(str, Enum):
    """
    What actually happened when this leg was queried. SEVEN internal states.

    The split that matters is NO_AWARD_SPACE vs everything else. All the others
    surface as PointsProvenance.UNAVAILABLE, and they must never be rendered
    with the same words.

    ANSWERED_UNREADABLE IS THE SIXTH STATE AND IT WAS ADDED BECAUSE THE PROJECT
    MADE ITS SIGNATURE MISTAKE A THIRD TIME (adversarial finding C-1). The type
    system stopped exactly one variant of "a failure presented as a finding":
    API_ERROR carrying an error string. It did not stop the variant where the
    API answers, hands us nine rows, the parser cannot read any of them, and the
    resulting empty award list is announced as `NO_AWARD_SPACE` - "there is no
    award to buy on this date". That is not a fact about the world. It is a fact
    about the parser, and it is v2's bug wearing v3's clothes.

    So there is now a state for "answered, unreadable", and __post_init__
    REFUSES to build NO_AWARD_SPACE whenever a row was seen and could not be
    parsed. The difference between "the calendar is empty" and "we could not
    read the calendar" is the entire product.

    ANSWERED_INCOMPLETE IS THE SEVENTH STATE AND IT WAS ADDED BECAUSE THE
    PROJECT MADE ITS SIGNATURE MISTAKE A FOURTH TIME (manager review MR-1). The
    sixth state stopped "answered, unreadable". It did not stop "answered,
    readable, and we only saw part of it": a payload of
    `{"data": [], "hasMore": true}` - the server saying there is more and giving
    us no way to ask for it - produced zero awards, zero unreadable rows, and
    `NO_AWARD_SPACE`, rendered BYTE-IDENTICALLY to a genuinely empty, complete
    answer. One page of an admittedly truncated result set was empty and we
    announced "there is no award to buy on this date".

    Coverage is a THIRD axis, independent of reachability and readability. It
    lives on `result_incomplete`, not only in this enum, because a BUDGET_EXHAUSTED
    or an OK result can also be partial.
    """

    OK = "ok"                            # answered, >=1 award parsed
    NO_AWARD_SPACE = "no_award_space"    # answered, READ IN FULL, 0 awards. A FINDING.
    ANSWERED_UNREADABLE = "answered_unreadable"  # answered, unparseable. NOT a finding.
    ANSWERED_INCOMPLETE = "answered_incomplete"  # answered + readable + TRUNCATED, 0 awards. NOT a finding.
    API_ERROR = "api_error"              # never answered. NOT a finding.
    NOT_QUERIED = "not_queried"          # hotel leg, no IATA, or --live off
    BUDGET_EXHAUSTED = "budget_exhausted"  # cap hit mid-trip. NOT a finding.

    @property
    def is_a_finding_about_award_space(self) -> bool:
        """
        True only where the API answered AND we could read the answer.

        Any code that wants to say something about availability must go through
        this property, so that adding a seventh state cannot silently default to
        "yes, this tells you about award space". ANSWERED_UNREADABLE is
        deliberately NOT here: the API answered, but what it said is unknown.
        """
        return self in (LiveQueryState.OK, LiveQueryState.NO_AWARD_SPACE)


@dataclass(frozen=True)
class LiveQuerySpec:
    """Exactly what was asked of Seats.aero for one leg. Printed, not implied."""

    leg_id: str
    origin: str
    destination: str
    leg_date: date
    start_date: date
    end_date: date
    flex_days: int = 0
    cabins: str = "all (Y/W/J/F)"

    @property
    def is_windowed(self) -> bool:
        return self.start_date != self.end_date

    def describe(self) -> str:
        window = (
            f"{self.start_date}..{self.end_date} (+/-{self.flex_days} days)"
            if self.is_windowed
            else str(self.start_date)
        )
        return f"{self.origin}->{self.destination} on {window}"


@dataclass(frozen=True)
class LiveLegOutcome:
    """
    The result of querying one leg. The invariant is enforced BY CONSTRUCTION.

    Not by a convention, not by a code review checklist, and not by an `assert`
    (which `python -O` deletes). A ValueError, because this is the one rule in
    the codebase that must survive every optimisation flag.

    ==================================================================
    THE EXHAUSTIVE LIST OF WAYS THIS TOOL CAN FAIL TO KNOW SOMETHING
    ABOUT A LEG'S AWARD SPACE
    ==================================================================

    Written down because three consecutive fix rounds each closed ONE INSTANCE
    of "we could not get usable data, so we said there is nothing there" and
    none of them enumerated the class. v2 fixed the parser instance, v4 fixed
    the unreadable-row / budget / wrong-envelope instances, and the Manager then
    found a fourth on the pagination axis in fifteen minutes. The list below is
    the answer to "where else does this shape appear?", asked once, in full.

    A leg's knowledge has FOUR INDEPENDENT AXES. A failure on any one of them
    means "we do not know", and each axis has its OWN FIELD with its OWN
    INVARIANT, because a state enum alone cannot express two simultaneous
    failures (a budget break mid-pagination is both a budget failure AND a
    coverage failure, and the answer must say both).

    AXIS 1 - DID WE ASK?
      (1) We never asked at all: hotel leg, missing IATA codes, --live off, or
          no client. -> state NOT_QUERIED, field `note`.
          INVARIANT: NOT_QUERIED with an empty `note` raises. A leg that says
          "not queried" without saying why is a shrug the reader cannot audit,
          and render() would have supplied a plausible DEFAULT reason that may
          be false.

    AXIS 2 - DID WE GET AN ANSWER?
      (2) We asked and the transport failed: DNS, TLS, timeout, 4xx, 5xx,
          unparseable JSON at the envelope level. -> state API_ERROR, field
          `error`.
          INVARIANT: API_ERROR with no `error` raises; API_ERROR with
          `awards_parsed` raises.
      (3) We never sent the request because OUR OWN daily call cap was spent.
          A fact about us, not about the calendar. -> state BUDGET_EXHAUSTED,
          field `error` (+ `result_incomplete` when the cap hit MID-pagination).
          INVARIANT: same as (2), and a budget failure is never cached or
          archived, so it can never be replayed as an observation (finding H-1).

    AXIS 3 - COULD WE READ THE ANSWER WE GOT?
      (4) The API answered and the parser could not read the rows: a date format
          it rejects, a row that is not an object, a cabin marked available with
          an unreadable price, a page whose envelope is the wrong shape.
          -> state ANSWERED_UNREADABLE, fields `rows_unreadable`,
          `unreadable_reasons`.
          INVARIANT: NO_AWARD_SPACE with any `rows_unreadable` raises
          (finding C-1). ANSWERED_UNREADABLE with zero `rows_unreadable` also
          raises - it is not a parse failure, so say which state it is.
      (5) The page carried TWO documented row containers and they disagree:
          `{"data": [], "results": [...20 real rows...]}`. We read `data`,
          saw nothing, discarded twenty bookable rows and called it a finding.
          Choosing one of two containers is a GUESS, and a guess is not
          knowledge. -> routed to ANSWERED_UNREADABLE by
          `envelope_shape_error`, so invariant (4) covers it.
          Found while writing this list (manager review MR-1's "fifth way").

    AXIS 4 - DID WE SEE ALL OF THE ANSWER?
      (6) The API answered, every row was readable, and WE ONLY SAW PART OF THE
          RESULT SET: `hasMore` with no cursor and no advancing offset, a skip
          that does not move, the 25-page safety cap hit while the server was
          still reporting more, or the daily budget breaking mid-pagination.
          -> field `result_incomplete` + `incomplete_reason`, and when it
          leaves us with zero awards, state ANSWERED_INCOMPLETE.
          INVARIANT: NO_AWARD_SPACE with `result_incomplete` raises. This is
          MR-1. `result_incomplete` with an empty `incomplete_reason` also
          raises - a truncation nobody can explain is not auditable.
          `_truncation_clause()` is appended to EVERY render, unconditionally,
          exactly the way `_skipped_clause()` now is, because the previous
          version of this fix left the marker on `pagination_note`, which
          NO_AWARD_SPACE.render() never read.

    AXIS 5 - IS THE ANSWER STILL TRUE? (freshness, not coverage)
      (7) The bytes are a replay of an older fetch. -> fields
          `served_from_cache`, `cache_fetched_at`.
          INVARIANT: `served_from_cache` with no `cache_fetched_at` raises. An
          answer of unknown age is not an answer about today, and render() used
          to print the literal string "fetched None".
      (8) THE BYTES ARE A REPLAY OF A COMMITTED SNAPSHOT, NOT OF THE API. We
          did not ask anyone anything on this run: `--from-snapshot` swapped the
          transport for a file. -> fields `replayed_from_snapshot`,
          `snapshot_name`, `snapshot_content_hash`, `snapshot_captured_at`,
          `snapshot_parser_version`.
          INVARIANT: `replayed_from_snapshot` with no `snapshot_content_hash`
          raises - a replay whose bytes cannot be identified is not
          reproducible, which is the entire point of replaying.
          `replayed_from_snapshot` together with `served_from_cache` raises: the
          bytes came from ONE place and the outcome must say which.
          `provenance` may never be `PointsProvenance.LIVE` on a replayed
          outcome; it is `SNAPSHOT`.
          A `_replay_clause()` is appended to EVERY render unconditionally,
          naming the snapshot file and its capture time, so no reader can
          mistake a replayed observation of January for a fresh one.

          ON `is_a_finding_about_award_space`. Replay adds no new STATE, so the
          whitelist is unchanged, and that is deliberate rather than an
          oversight: a snapshot's emptiness WAS a finding at capture time and
          replay preserves findings. NO_AWARD_SPACE replays as NO_AWARD_SPACE,
          and ANSWERED_INCOMPLETE replays as ANSWERED_INCOMPLETE, because the
          truncation was a property of the bytes. What replay changes is
          FRESHNESS, which is axis 5, which is why (8) lives here and not in the
          enum. (The plan's §4.3 says "SNAPSHOT is added to the whitelist";
          taken literally that would put a PointsProvenance value into a
          LiveQueryState whitelist. The intent - that a replayed finding is
          still a finding - is what is implemented.)

    AXIS 6 - WILL THE ANSWER STILL BE THE SAME ANSWER NEXT TIME IT IS READ?
      (9) EVERY INVARIANT ABOVE IS ENFORCED AT CONSTRUCTION TIME AND NONE OF
          THEM SURVIVED A ROUND TRIP THROUGH STORAGE. The fields the invariants
          READ were not the fields storage WROTE, so the same bytes answered
          differently depending on how many times they had been read: a
          truncated fetch was ANSWERED_INCOMPLETE live, and on the second read -
          from the disk cache or from `--from-snapshot` - it was
          NO_AWARD_SPACE, "THIS IS A FINDING: there is no award to buy on this
          date". Nothing raised, because by the time the outcome was built the
          truncation no longer existed to raise about.
          -> fields: whichever ones the invariants read. There is no new field
          here, and that is the point of the way: it is a property of the
          STORAGE LAYERS, not of one axis.
          INVARIANT (and it is not in `__post_init__`, because a constructor
          cannot see a file): every field an invariant reads is CLASSIFIED
          below, and the classification is checked against the fields the
          invariants actually read - derived from this method's own source, not
          from a list somebody maintains. A field classified
          `CARRIED_BY_THE_TRANSPORT` is written by every storage layer and read
          back by every storage layer through ONE derived key set
          (`PERSISTED_PROVENANCE_KEYS`), so a tenth field cannot be added
          without either classifying it - or this module refusing to import.
          Coverage is additionally RECOMPUTED FROM THE STORED BYTES on read
          (`seats_client.coverage_of_pages`), so bytes written before this way
          was known still answer the same way twice.

    HOW TO EXTEND THIS. If you find a tenth way, it does NOT get to be a new
    branch inside some renderer. It gets: a FIELD on this dataclass, a `raise`
    in `__post_init__` forbidding the combination that would let it be reported
    as a finding, an unconditional clause in `render()`, a numbered entry above,
    AND - way (9) - a classification below saying whether it is recomputed from
    the bytes, carried by the transport (therefore persisted everywhere), or
    local to one run. `is_a_finding_about_award_space` is a WHITELIST for the
    same reason - a new state cannot silently default to "yes, this tells you
    about award space".
    """

    leg_id: str
    state: LiveQueryState
    provenance: PointsProvenance
    queried: Optional[LiveQuerySpec] = None
    awards_parsed: int = 0
    awards_on_leg_date: int = 0
    awards_off_date: int = 0
    rows_seen: int = 0
    # Rows that produced no award, for ANY reason. The sum of the two counters
    # below. Kept because it is the number a reader wants ("9 of 9 rows gave us
    # nothing") and because it was already on the wire.
    rows_skipped: int = 0
    # THE DEFECT SIGNAL. Rows the parser could not read at all: a date in a
    # format it rejects, a row that is not an object, a cabin marked available
    # whose price is unreadable, a page whose envelope is the wrong shape. A
    # non-zero value here means the tool does not know what the API said, and
    # the invariant below refuses to let that be called an absence of awards.
    rows_unreadable: int = 0
    # Rows read cleanly that simply carry no bookable cabin. THIS one is a real
    # signal about award space and is what legitimises NO_AWARD_SPACE.
    rows_without_availability: int = 0
    # Why the unreadable rows were unreadable, deduplicated. Printed, not stored
    # and forgotten: a skipped row is a defect signal, not a silent statistic.
    unreadable_reasons: List[str] = field(default_factory=list)
    # POPULATED IFF state is API_ERROR / BUDGET_EXHAUSTED / ANSWERED_UNREADABLE
    error: str = ""
    # AXIS 4, THE COVERAGE FIELD (manager review MR-1). True when we saw only
    # part of the result set. Deliberately a FIELD and not merely a state,
    # because coverage is orthogonal to reachability: a BUDGET_EXHAUSTED or an
    # OK outcome can also be partial, and a state enum cannot say two things.
    # Set from RawSearchResult.incomplete, which existed and reached nothing.
    result_incomplete: bool = False
    # Why the result set was truncated. Required whenever the flag is set: a
    # truncation nobody can explain is not auditable.
    incomplete_reason: str = ""
    pagination_note: str = ""
    snapshot_path: Optional[Path] = None
    served_from_cache: bool = False
    cache_fetched_at: Optional[datetime] = None
    # AXIS 5, WAY (8). These bytes are a replay of a COMMITTED SNAPSHOT. No
    # request was made on this run and no cache was consulted; a file was read.
    replayed_from_snapshot: bool = False
    snapshot_name: str = ""
    snapshot_content_hash: str = ""
    snapshot_captured_at: Optional[datetime] = None
    snapshot_parser_version: str = ""
    note: str = ""

    def __post_init__(self):
        if self.state in (
            LiveQueryState.API_ERROR,
            LiveQueryState.BUDGET_EXHAUSTED,
            LiveQueryState.ANSWERED_UNREADABLE,
        ):
            if not self.error:
                raise ValueError(
                    f"{self.leg_id}: state is {self.state.value} but no error was "
                    f"given. An error state must say what the error was, or it is "
                    f"indistinguishable from a finding of no availability."
                )
        if self.state in (LiveQueryState.API_ERROR, LiveQueryState.BUDGET_EXHAUSTED):
            if self.awards_parsed:
                raise ValueError(
                    f"{self.leg_id}: state is {self.state.value} with "
                    f"{self.awards_parsed} awards parsed. We never got an answer; "
                    f"there is nothing to have parsed."
                )
        if self.state is LiveQueryState.ANSWERED_UNREADABLE and not self.rows_unreadable:
            raise ValueError(
                f"{self.leg_id}: ANSWERED_UNREADABLE with zero unreadable rows is "
                f"not a parse failure. Say which state it actually is."
            )
        if self.state is LiveQueryState.NO_AWARD_SPACE:
            if self.error:
                raise ValueError(
                    f"{self.leg_id}: NO_AWARD_SPACE means the API ANSWERED. An "
                    f"error here ({self.error!r}) would mean an API failure is "
                    f"being reported as an absence of award space, which is the "
                    f"exact bug this project has now made twice."
                )
            if self.awards_parsed:
                raise ValueError(
                    f"{self.leg_id}: NO_AWARD_SPACE with {self.awards_parsed} "
                    f"awards parsed is a contradiction."
                )
            # FINDING C-1. This is the clause that was missing, and its absence
            # is why v2's bug came back inside the code written to prevent it.
            # Rows arrived and the parser could not read them. Whatever the API
            # said, we did not understand it, so we know NOTHING about award
            # space - which is the definition of ANSWERED_UNREADABLE, not of
            # NO_AWARD_SPACE.
            if self.rows_unreadable:
                raise ValueError(
                    f"{self.leg_id}: NO_AWARD_SPACE with {self.rows_unreadable} of "
                    f"{self.rows_seen} rows UNREADABLE. Rows that could not be "
                    f"parsed say nothing about award space; announcing them as "
                    f"'there is no award to buy' is the exact failure-as-finding "
                    f"bug this project has now made three times. Use "
                    f"LiveQueryState.ANSWERED_UNREADABLE."
                )
            # MANAGER REVIEW MR-1. THE FOURTH INSTANCE, ON THE COVERAGE AXIS.
            # Every row we saw was readable, and we did not see every row. An
            # empty page of a truncated result set is not an empty result set.
            # Without this clause `{"data": [], "hasMore": true}` rendered
            # BYTE-IDENTICALLY to a genuinely empty, complete answer.
            if self.result_incomplete:
                raise ValueError(
                    f"{self.leg_id}: NO_AWARD_SPACE on an INCOMPLETE result set "
                    f"({self.incomplete_reason or 'no reason recorded'}). We saw "
                    f"one part of the answer, that part was empty, and announcing "
                    f"'there is no award to buy on this date' asserts something "
                    f"about the part we never saw. That is the failure-as-finding "
                    f"bug for the FOURTH time, on a new axis. Use "
                    f"LiveQueryState.ANSWERED_INCOMPLETE."
                )
        # AXIS 4's own invariant, independent of state: the flag must be
        # explicable, or the unconditional render clause has nothing to print.
        if self.result_incomplete and not self.incomplete_reason:
            raise ValueError(
                f"{self.leg_id}: result_incomplete is set with no "
                f"incomplete_reason. A truncation that cannot say what stopped it "
                f"is not auditable, and _truncation_clause() would print an empty "
                f"warning."
            )
        if self.state is LiveQueryState.ANSWERED_INCOMPLETE:
            if not self.result_incomplete:
                raise ValueError(
                    f"{self.leg_id}: ANSWERED_INCOMPLETE without result_incomplete "
                    f"is a contradiction. If the result set was whole, this is "
                    f"NO_AWARD_SPACE (a finding) or OK."
                )
            if self.error:
                raise ValueError(
                    f"{self.leg_id}: ANSWERED_INCOMPLETE means the API ANSWERED "
                    f"and we READ what it sent. An error here ({self.error!r}) "
                    f"would be an API failure wearing a coverage failure's "
                    f"clothes. Use API_ERROR."
                )
            if self.awards_parsed:
                raise ValueError(
                    f"{self.leg_id}: ANSWERED_INCOMPLETE with "
                    f"{self.awards_parsed} awards parsed should be OK. Awards we "
                    f"found are found; the truncation is reported by "
                    f"result_incomplete, which OK also carries."
                )
        # AXIS 1. A leg that says "not queried" must say why, or render() will
        # supply a plausible DEFAULT reason that may not be the true one.
        if self.state is LiveQueryState.NOT_QUERIED and not self.note:
            raise ValueError(
                f"{self.leg_id}: NOT_QUERIED with no note. Say why this leg was "
                f"never asked about - a shrug the reader cannot audit is not an "
                f"answer, and the renderer must not invent the reason."
            )
        # AXIS 5. An answer of unknown age is not an answer about today.
        if self.served_from_cache and self.cache_fetched_at is None:
            raise ValueError(
                f"{self.leg_id}: served_from_cache with no cache_fetched_at. The "
                f"age of a replayed answer is part of the answer; without it "
                f"render() prints the literal string 'fetched None'."
            )
        # AXIS 5, WAY (8). Three raises, each forbidding a combination that
        # would let a replayed number pass as something it is not.
        if self.replayed_from_snapshot:
            if not self.snapshot_content_hash:
                raise ValueError(
                    f"{self.leg_id}: replayed_from_snapshot with no "
                    f"snapshot_content_hash. A replay whose bytes cannot be "
                    f"identified is not reproducible, and reproducibility is the "
                    f"only reason to replay anything. The hash is what a quoted "
                    f"percentage is quoted AGAINST."
                )
            if self.served_from_cache:
                raise ValueError(
                    f"{self.leg_id}: replayed_from_snapshot AND "
                    f"served_from_cache. The bytes came from ONE place and this "
                    f"outcome must say which. A replay does not consult the "
                    f"cache at all - ResponseCache is not constructed on that "
                    f"path - so this combination means two transports disagree "
                    f"about who answered."
                )
            if self.provenance is PointsProvenance.LIVE:
                raise ValueError(
                    f"{self.leg_id}: replayed_from_snapshot with provenance LIVE. "
                    f"A replay reuses live bytes and none of their currency: the "
                    f"answer was true when it was captured and says nothing about "
                    f"today. Use PointsProvenance.SNAPSHOT. A replayed number "
                    f"that calls itself live is the exact laundering this feature "
                    f"exists to prevent."
                )
        if self.state is LiveQueryState.OK and not self.awards_parsed:
            raise ValueError(
                f"{self.leg_id}: OK with zero awards parsed is NO_AWARD_SPACE. "
                f"Use that state, so the difference stays visible."
            )
        if self.rows_skipped and (
            self.rows_unreadable + self.rows_without_availability
        ) not in (0, self.rows_skipped):
            raise ValueError(
                f"{self.leg_id}: rows_skipped ({self.rows_skipped}) does not equal "
                f"rows_unreadable ({self.rows_unreadable}) + "
                f"rows_without_availability ({self.rows_without_availability}). "
                f"A skipped row is one or the other and the two must not drift."
            )

    @property
    def is_api_failure(self) -> bool:
        return self.state in (
            LiveQueryState.API_ERROR,
            LiveQueryState.BUDGET_EXHAUSTED,
        )

    @property
    def answered_but_not_understood(self) -> bool:
        """The API replied and we could not read the reply. Never a finding."""
        return self.state is LiveQueryState.ANSWERED_UNREADABLE

    @property
    def tells_us_nothing_about_award_space(self) -> bool:
        """
        Any state from which no conclusion about availability may be drawn.

        One property, so that `--require-all-live`, the provenance counters and
        the formatter cannot disagree about which states those are.
        """
        return not self.state.is_a_finding_about_award_space

    def _skipped_clause(self) -> str:
        """
        How many rows produced nothing, and why. NEVER omitted.

        `rows_skipped` used to be counted, stored on this object and never
        printed. A skipped row is a defect signal, not a silent statistic
        (finding C-1), so every state that has one says so.
        """
        if not self.rows_skipped:
            return ""
        parts = []
        if self.rows_without_availability:
            parts.append(
                f"{self.rows_without_availability} carried no bookable cabin"
            )
        if self.rows_unreadable:
            parts.append(f"{self.rows_unreadable} COULD NOT BE PARSED")
        why = f" ({'; '.join(self.unreadable_reasons)})" if self.unreadable_reasons else ""
        return (
            f" {self.rows_skipped} of {self.rows_seen} rows produced no award: "
            f"{', '.join(parts)}{why}."
        )

    def _truncation_clause(self) -> str:
        """
        Whether we saw the whole answer. NEVER omitted. Sibling of _skipped_clause.

        MANAGER REVIEW MR-1. The previous fix round put the INCOMPLETE marker on
        `pagination_note`, and `NO_AWARD_SPACE.render()` never read
        `pagination_note` - which is verbatim the structural complaint the Tester
        made in C-1. Fixing an instance by writing the truth somewhere nothing
        prints is not fixing it. This clause is appended by `render()` to EVERY
        state, unconditionally, so no branch can be added that forgets it.
        """
        if not self.result_incomplete:
            return ""
        return (
            f" COVERAGE IS INCOMPLETE: {self.incomplete_reason} Everything above "
            f"is true ONLY of the part of the result set we actually saw. It is "
            f"NOT a statement about the part we did not see, and it does NOT rule "
            f"out award space there."
        )

    def _replay_clause(self) -> str:
        """
        Whether anyone was asked anything on this run. NEVER omitted.

        Third sibling of `_skipped_clause` and `_truncation_clause`, and it is
        appended by `render()` for the same structural reason both of those are:
        a branch cannot forget a clause it does not write. Way (8) does not get
        to be a condition inside a renderer.

        It names the capture time on EVERY leg because `--from-snapshot` makes a
        stale number EASIER to quote, not harder (risk 9.2). A January manifest
        replays perfectly in June and prints a confident percentage. The hash
        proves reproducibility; it does not prove currency, and there is no
        expiry - adding one would reintroduce the TTL mistake. This sentence is
        what stands between a reader and that mistake.
        """
        if not self.replayed_from_snapshot:
            return ""
        captured = (
            f" captured {self.snapshot_captured_at}"
            if self.snapshot_captured_at
            else " with NO recorded capture time"
        )
        return (
            f" REPLAYED FROM A COMMITTED SNAPSHOT: nothing was asked of "
            f"Seats.aero on this run. These bytes are "
            f"{self.snapshot_name or '(unnamed file)'}{captured}, content hash "
            f"{self.snapshot_content_hash[:16]}, parsed at capture by "
            f"{self.snapshot_parser_version or 'an unrecorded parser version'}. "
            f"Everything above was true THEN. It is not a statement about award "
            f"space today."
        )

    def render(self) -> str:
        """
        The line the user reads. An error and an absence NEVER share wording.

        This is the single place where a state becomes prose, so the two cannot
        drift into looking alike.

        The three coverage/freshness clauses are appended ONCE, at the bottom,
        to whatever the state branch returned - not inside the branches. A
        branch cannot forget a clause it does not write.
        """
        return (
            self._render_state()
            + self._skipped_clause()
            + self._truncation_clause()
            + self._replay_clause()
        )

    def _render_state(self) -> str:
        """The state-specific prose only. Callers want `render()`."""
        where = self.queried.describe() if self.queried else "(not queried)"
        if self.state is LiveQueryState.OK:
            cached = (
                f", served from cache fetched {self.cache_fetched_at}"
                if self.served_from_cache
                else ""
            )
            return (
                f"Seats.aero returned {self.awards_parsed} award(s) for {where} "
                f"({self.awards_on_leg_date} on the leg's own date, "
                f"{self.awards_off_date} on other dates){cached}."
            )
        if self.state is LiveQueryState.NO_AWARD_SPACE:
            return (
                f"Seats.aero returned NO award space for {where} "
                f"(searched {where}, {self.rows_seen} rows). THIS IS A FINDING: "
                f"the API answered, every row it sent was READ SUCCESSFULLY, and "
                f"the answer is that there is no award to buy on this date."
            )
        if self.state is LiveQueryState.ANSWERED_INCOMPLETE:
            # MR-1. This wording exists so that it can NEVER be mistaken for the
            # NO_AWARD_SPACE branch above. Same zero awards, opposite meaning.
            return (
                f"Seats.aero ANSWERED for {where} and we READ every row it sent "
                f"({self.rows_seen} rows), and found no award - but the result "
                f"set was TRUNCATED and we never saw the rest of it. THIS IS NOT "
                f"A FINDING: an empty page of an incomplete answer says NOTHING "
                f"about whether award space exists on this date. It is a "
                f"PAGINATION failure on our side, reported as ours, not as the "
                f"calendar's."
            )
        if self.state is LiveQueryState.ANSWERED_UNREADABLE:
            return (
                f"Seats.aero ANSWERED for {where} but the response COULD NOT BE "
                f"PARSED: {self.rows_unreadable} of {self.rows_seen} rows were "
                f"unreadable and no award could be built from them. "
                f"{self.error} THIS IS NOT A FINDING: nothing whatsoever is known "
                f"about award space on this leg. It is a PARSER OR SCHEMA failure "
                f"on our side - the same shape of bug as reading `cost` from a "
                f"payload that calls it `YMileageCost` - and it is reported as "
                f"ours, not as the calendar's."
            )
        if self.state is LiveQueryState.API_ERROR:
            return (
                f"Seats.aero COULD NOT BE REACHED for {where} ({self.error}). "
                f"THIS IS AN API FAILURE. NOTHING is known about award space on "
                f"this leg. It is NOT a finding of no availability."
            )
        if self.state is LiveQueryState.BUDGET_EXHAUSTED:
            return (
                f"This leg was NEVER QUERIED: {self.error} THIS IS A BUDGET "
                f"FAILURE, not a finding. NOTHING is known about award space "
                f"on {where}."
            )
        # `self.note` is guaranteed non-empty by __post_init__ for NOT_QUERIED,
        # so there is no default reason to invent here any more.
        return f"Not queried: {self.note}."


# ---------------------------------------------------------------------------
# WAY (9): the classification, and why it is derived rather than listed
# ---------------------------------------------------------------------------
#
# `ResponseCache.put` used to build its `_meta` from an EXPLICIT KEY LIST, and
# `incomplete` / `incomplete_reason` were handed to it and were not on the list.
# `SnapshotTransport.search_raw` hard-coded `incomplete=False`. Both are the
# same mistake: the set of fields storage persists was maintained by hand, next
# to a set of invariants that grew. A list someone must remember to update is
# not an invariant, and this project has now been caught by that shape twice
# (way (6) reaching nothing, and then way (6) reaching nothing THROUGH STORAGE).
#
# So the persisted set is DERIVED from the fields the invariants read. Adding a
# field to an invariant without classifying it here makes this module fail to
# import - which is louder than any test, and survives `python -O`.


def _fields_read_by_invariants(cls) -> frozenset:
    """
    Every `self.X` that `cls.__post_init__` reads, parsed out of its own source.

    Deliberately AST over source rather than a maintained list: the question
    "which fields do the invariants depend on?" has exactly one truthful answer
    and it is the code. `inspect.getsource` works under `python -O` (which
    strips asserts, not docstrings or source files). If the source genuinely
    cannot be read - a frozen or zipped deployment - the classification below
    cannot be checked against anything, and `INVARIANT_FIELDS_DERIVED` records
    that so a caller can say so rather than assume the check ran.
    """
    import ast
    import inspect
    import textwrap

    try:
        source = textwrap.dedent(inspect.getsource(cls.__post_init__))
    except (OSError, TypeError):  # pragma: no cover - frozen deployments only
        return frozenset()
    tree = ast.parse(source)
    names = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    }
    return frozenset(names)


# (a) RECOMPUTED FROM THE STORED BYTES on every read, by the parser. Nothing to
#     persist: the pages ARE the storage, and a second read of the same pages
#     produces the same numbers. Persisting these would create a second source
#     of truth that could disagree with the bytes.
RECOMPUTED_FROM_BYTES = frozenset(
    {
        "state",
        "awards_parsed",
        "rows_seen",
        "rows_skipped",
        "rows_unreadable",
        "rows_without_availability",
    }
)

# (b) CARRIED BY THE TRANSPORT, therefore MUST ROUND-TRIP THROUGH EVERY STORAGE
#     LAYER. Facts about the fetch that the bytes alone do not state. The value
#     is the `_meta` key AND the `RawSearchResult` field name - they are
#     deliberately the same string, so a rename cannot make the two halves of a
#     round trip drift apart silently.
#
#     `cache_fetched_at` was the only member of this set that ever worked, which
#     is exactly why way (7) survived storage and way (6) did not.
CARRIED_BY_THE_TRANSPORT = {
    "result_incomplete": "incomplete",
    "incomplete_reason": "incomplete_reason",
    "cache_fetched_at": "fetched_at",
}

# (c) LOCAL TO THIS RUN. Facts about THIS read, not about the bytes: who
#     answered, whether a file or a socket produced them, which leg asked.
#     Persisting these would let a run inherit another run's provenance, which
#     is the laundering ways (7) and (8) exist to prevent.
LOCAL_TO_THIS_RUN = frozenset(
    {
        "leg_id",
        "provenance",
        "error",
        "note",
        "served_from_cache",
        "replayed_from_snapshot",
        "snapshot_content_hash",
    }
)

INVARIANT_FIELDS = _fields_read_by_invariants(LiveLegOutcome)
INVARIANT_FIELDS_DERIVED = bool(INVARIANT_FIELDS)

# The one set every storage layer loops over. Writers write these keys, readers
# read these keys, and neither has a list of its own.
PERSISTED_PROVENANCE_KEYS = tuple(sorted(CARRIED_BY_THE_TRANSPORT.values()))


def _check_way_nine_classification() -> None:
    """
    Every field an invariant reads is classified exactly once. Import-time.

    A ValueError rather than an assert, for the reason every other rule in this
    file is a ValueError: `python -O` deletes asserts and this is a rule that
    must survive every optimisation flag.
    """
    declared = (
        set(RECOMPUTED_FROM_BYTES)
        | set(CARRIED_BY_THE_TRANSPORT)
        | set(LOCAL_TO_THIS_RUN)
    )
    overlap = (
        (RECOMPUTED_FROM_BYTES & set(CARRIED_BY_THE_TRANSPORT))
        | (RECOMPUTED_FROM_BYTES & LOCAL_TO_THIS_RUN)
        | (set(CARRIED_BY_THE_TRANSPORT) & LOCAL_TO_THIS_RUN)
    )
    if overlap:
        raise ValueError(
            f"way (9): {sorted(overlap)} is classified twice. A field is "
            f"recomputed from the bytes, carried by the transport, or local to "
            f"this run - exactly one of the three."
        )
    known = {f.name for f in dataclasses.fields(LiveLegOutcome)}
    invented = declared - known
    if invented:
        raise ValueError(
            f"way (9): {sorted(invented)} is classified but is not a field of "
            f"LiveLegOutcome. A classification that names nothing protects "
            f"nothing."
        )
    unclassified = (INVARIANT_FIELDS & known) - declared
    if unclassified:
        raise ValueError(
            f"way (9): the invariants in LiveLegOutcome.__post_init__ read "
            f"{sorted(unclassified)}, which no storage classification covers. "
            f"Every field an invariant depends on must be recomputed from the "
            f"bytes, carried by the transport (and therefore persisted by every "
            f"storage layer), or explicitly local to one run. An unclassified "
            f"field is one that answers differently on the second read - that "
            f"is way (9), and it is not allowed to be added silently."
        )


_check_way_nine_classification()


def assert_transport_carries(raw_cls) -> None:
    """
    Every persisted key is a real field of the transport's result type.

    Called at import of `seats_client`, so the two halves of the round trip -
    "what the invariants need" and "what the transport can supply" - are checked
    against each other in the running process rather than in a test somebody may
    not run.
    """
    fields = {f.name for f in dataclasses.fields(raw_cls)}
    missing = set(PERSISTED_PROVENANCE_KEYS) - fields
    if missing:
        raise ValueError(
            f"way (9): {sorted(missing)} must round-trip through storage but "
            f"{raw_cls.__name__} has no such field, so nothing can carry it "
            f"from the bytes to LiveLegOutcome."
        )


@dataclass
class FlexibleFinding:
    """
    An award found on a date OTHER than the leg's own date.

    NEVER SCORED, and section 4.5 of the plan is the argument: a flexible search
    takes the MINIMUM over a window on the points side while the cash side stays
    a SINGLE FIXED DRAW. Even uncorrelated, min-of-(2N+1) versus one draw favours
    points. With correlation it is worse in the same direction - award space is
    released on low-demand dates, which are also low CASH-fare dates, so the
    fixed cash price the tool would compare against is probably HIGHER than the
    true price on the award's own date.

    The honest escape hatch is not a code change: capture the cash fare for that
    date and add it to the fixture. Then the award is promoted and the leg is
    marked DATE_SHIFTED.
    """

    leg_id: str
    award_date: date
    leg_date: date
    program: str
    cabin: str
    points: int
    taxes_amount: Optional[float] = None
    taxes_currency: str = ""
    taxes_usd: Optional[float] = None
    taxes_known: bool = False
    seats: int = 0
    carriers: List[str] = field(default_factory=list)
    source_note: str = ""

    @property
    def offset_days(self) -> int:
        return (self.award_date - self.leg_date).days

    @property
    def date_match(self) -> str:
        return f"offset({self.offset_days:+d})"

    def render(self) -> str:
        taxes = (
            f" + {self.taxes_currency} {self.taxes_amount:,.2f}"
            if self.taxes_known and self.taxes_amount is not None
            else " + taxes UNKNOWN"
        )
        return (
            f"{self.award_date}  {self.program}  {self.cabin}  "
            f"{self.points:,}{taxes}   {self.seats} seats"
        )

    def advisory(self) -> str:
        return (
            f"Cash for {self.award_date} has NOT been captured. The cash price on "
            f"this leg is for {self.leg_date} and is NOT a comparison for this "
            f"award. To score it, capture the cash fare for {self.award_date} and "
            f"add it to the fixture with that date on the cash option."
        )


@dataclass
class Leg:
    """One bookable component of a trip: a flight or a hotel stay."""

    id: str
    kind: str  # "flight" | "hotel"
    description: str
    date: date
    cash_options: List[CashOption] = field(default_factory=list)
    points_candidates: List[PointsCandidate] = field(default_factory=list)
    travelers: int = 1
    notes: List[str] = field(default_factory=list)
    data_flags: List[str] = field(default_factory=list)
    # --- v1 additions ----------------------------------------------------
    origin: str = ""  # IATA, for region/surcharge classification
    destination: str = ""  # IATA
    # FINDING H-2. THE CABIN IS A PROPERTY OF THE LEG, not only of a points
    # candidate. `--new-trip` writes a leg-level "cabin" key and nothing read
    # it: `Leg` had no field for it, the loader dropped it, and APD took the
    # cabin from `best_points` or from the literal "Y". A --new-trip fixture has
    # NO candidates by design, so every one of them was charged the reduced rate
    # - GBP 102 instead of GBP 244 on a J-cabin UK departure, understated by
    # GBP 142 per passenger and described confidently as "per the reduced rate".
    # Empty means the fixture did not say, which is UNKNOWN and not "Y".
    cabin: str = ""
    # Fees owed whether you pay cash or points. On the LEG, not on the cash
    # option: that placement is the bug fix.
    mandatory_fees: List[MandatoryFee] = field(default_factory=list)
    nights: int = 0  # hotel legs only
    # Programs that ARE reachable from the user's currency but for which no
    # award price was captured. We refuse to invent a points price; instead the
    # evaluator reports the break-even award price at which points would win.
    unpriced_partner_programs: List[str] = field(default_factory=list)

    # --- v3: live trip mode ----------------------------------------------
    # CASH PROVENANCE AND POINTS PROVENANCE ARE TWO INDEPENDENT FACTS about a
    # leg and v2 had no vocabulary for the difference. The trip fixture owns the
    # cash side FOREVER - there is no cash-price API in scope and there will not
    # be one - while live mode owns the points side. Every leg carries both.
    cash_provenance: str = "unknown"  # captured_screenshot|captured_booking_page|unknown
    cash_captured_on: Optional[date] = None
    points_provenance: "PointsProvenance" = PointsProvenance.UNAVAILABLE
    live_outcome: Optional["LiveLegOutcome"] = None
    # Badge candidates displaced by live data. NONE IS EVER DELETED: exactly one
    # of Trip B's four badges has ever been corroborated, so a divergence between
    # a Google badge and real award space is the interesting case, and it must
    # become visible rather than vanish.
    superseded_candidates: List["PointsCandidate"] = field(default_factory=list)
    flexible_date_findings: List["FlexibleFinding"] = field(default_factory=list)
    # Set when an off-date award was PROMOTED because the fixture carries a
    # captured cash price for that date. The comparison is then legitimate, and
    # the fact that a date moved is recorded rather than lost.
    date_shifted: bool = False
    date_shifted_to: Optional[date] = None


@dataclass
class FundingPlan:
    """
    How to get `points_needed` into one destination program. Multi-currency aware.

    Replaces TransferPath as the RANKING unit. The difference that matters:
    TransferPath.total_points_cost added source points across programs, which is
    a category error the moment two currencies transfer at different ratios.
    A FundingPlan is ranked on `score_usd` and reports raw point counts per
    currency, separately, never summed.
    """

    target_program: str = ""
    transfers: List[Transfer] = field(default_factory=list)
    existing_target_points_used: int = 0
    per_currency_spend: Dict[str, int] = field(default_factory=dict)
    delivered: int = 0
    points_needed: int = 0
    stranded_points: int = 0
    ratio_remainder_points: Dict[str, int] = field(default_factory=dict)
    score_usd: float = 0.0
    feasible: bool = True
    infeasible_reason: str = ""
    reasons: List[Reason] = field(default_factory=list)

    @property
    def currencies_used(self) -> int:
        return len([c for c, n in self.per_currency_spend.items() if n > 0])

    @property
    def total_waste_destination_points(self) -> int:
        return self.stranded_points

    def spend_summary(self) -> str:
        """Per-currency point counts, NEVER summed into one integer."""
        parts = [f"{n:,} {c}" for c, n in sorted(self.per_currency_spend.items()) if n]
        if self.existing_target_points_used:
            parts.append(
                f"{self.existing_target_points_used:,} {self.target_program} (already held)"
            )
        return " + ".join(parts) if parts else "no transfer"

    def summary(self) -> str:
        if not self.transfers:
            if self.existing_target_points_used:
                return f"No transfer (used {self.existing_target_points_used:,} existing)"
            return "No transfers"
        parts = [
            f"{t.from_program} {t.amount:,} -> {t.to_program} {t.points_delivered:,} @ {t.ratio}"
            + (f" [{t.card_used}]" if t.card_used else "")
            for t in self.transfers
        ]
        return " + ".join(parts)

    def add_reason(self, code: str, detail: str, **data) -> None:
        self.reasons.append(Reason(code=code, detail=detail, data=data))


@dataclass
class Alternative:
    """
    "Same metal, different program, less cash."

    The headline capability of v1. An alternative is almost always UNPRICED - we
    know Iberia charges far less YQ than BA on the same aeroplane, but we do not
    know Iberia's award price, and inventing one would be the exact failure mode
    the whole tool exists to avoid. So an unpriced alternative carries a
    break-even points figure and an instruction to go look the price up.
    """

    program: str
    operating_carrier: str
    surcharge: SurchargeEstimate
    cash_saved_vs_best_usd: float = 0.0
    points_price: Optional[int] = None  # None = we do not know and will not guess
    break_even_points: Optional[int] = None
    note: str = ""
    partnership_assumed: bool = False

    @property
    def is_priced(self) -> bool:
        return self.points_price is not None


@dataclass
class LegResult:
    """The evaluated outcome for one leg: cash vs points, head to head."""

    leg: Leg
    best_cash: Optional[CashOption] = None
    best_points: Optional[PointsCandidate] = None
    points_path: Optional[TransferPath] = None
    funding_plan: Optional[FundingPlan] = None

    cash_usd: float = 0.0
    cash_as_points_equivalent: int = 0
    # The cheapest captured cash price FOR THE LEG'S OWN DATE, plus mandatory
    # fees. `cash_total_score_usd` above is the fare for whatever date the leg
    # was actually SCORED on, which differs only when an off-date award was
    # promoted. The trip's all-cash total is built from THIS one: paying cash
    # means flying on the date the trip says, so an off-date fare must never
    # inflate the baseline the margin is measured against (finding H-5).
    cash_baseline_usd: float = float("inf")
    # The date this leg's head-to-head was scored on, and whether that is not
    # the leg's own date. A date that moved is disclosed, never absorbed.
    scoring_date: Optional[date] = None
    scored_off_date: bool = False
    # The API's own reported taxes, in USD, as counted into the points side.
    # Zero both when there are none and when they already ride inside the
    # captured surcharge - see PointsCandidate.extra_observed_taxes_usd.
    observed_taxes_usd: float = 0.0
    # A mandatory fee exists and could not be converted to USD. BOTH totals are
    # then lower bounds; the fee is unknown, not zero.
    mandatory_fees_unpriceable: bool = False
    # Set by the trip-level balance ceiling when this leg would have been a
    # points win on its own but the shared balance was already committed.
    demoted_for_trip_balance: bool = False

    points_required: int = 0  # Source (UR) points the points option costs
    points_surcharge_usd: float = 0.0
    points_total_score_usd: float = 0.0
    cash_total_score_usd: float = 0.0

    verdict: str = "cash"  # "points" | "cash" | "cash (no points path)"
    # H-4. WHY there is no points path, when there is none. "no_partner" is a
    # finding about partnerships; "never_priced" is an absence of award data and
    # says nothing about partnerships. They rendered identically, so a fixture
    # built by --new-trip (no candidates BY DESIGN) reported "no partner exists"
    # on a route another leg of another trip scores in the same binary.
    points_absence: str = ""  # "" | "no_partner" | "never_priced"
    verdict_reason: str = ""
    margin_usd: float = 0.0
    margin_pct: float = 0.0
    has_points_path: bool = False
    warnings: List[str] = field(default_factory=list)

    # For legs with a reachable partner but no captured award price: the award
    # price at which points would exactly match cash. Below it, points win.
    break_even_points: Optional[int] = None
    break_even_programs: List[str] = field(default_factory=list)

    # --- v1 additions ----------------------------------------------------
    surcharge: Optional[SurchargeEstimate] = None
    # Score at the low and high ends of the surcharge range. When the verdict
    # differs between them the recommendation is NOT settled.
    points_score_low_usd: float = 0.0
    points_score_high_usd: float = 0.0
    verdict_sensitive: bool = False
    # When the surcharge is unknown: the surcharge at which points exactly tie
    # cash. Above it, cash wins. This is the direct replacement for v0's silent
    # $0 assumption.
    break_even_surcharge_usd: Optional[float] = None
    # The points score with the surcharge at its floor of $0 - the best case the
    # unknown could possibly take. If even that loses to cash, the unknown cannot
    # change the verdict and the answer is certain after all.
    points_floor_usd: Optional[float] = None
    surcharge_cannot_change_verdict: bool = False
    mandatory_fees_usd: float = 0.0
    # --- v5 Step 7: UK Air Passenger Duty ---------------------------------
    # `apd` is None when the leg does not depart the UK. None means NOTHING,
    # not zero: a leg that owes no UK departure tax carries no APD line at all,
    # because "$0.00 APD" is a claim and an absence is not.
    apd: object = None
    # What actually entered `points_total_score_usd`. Zero both when APD is
    # UNKNOWN and when it is merely FLAGGED on a live/replayed leg, so it can
    # never be added twice or reported as added when it was not.
    apd_added_usd: float = 0.0
    alternatives: List[Alternative] = field(default_factory=list)
    reasons: List[Reason] = field(default_factory=list)
    rests_on_placeholder_fx: bool = False

    def add_reason(self, code: str, detail: str, **data) -> None:
        self.reasons.append(Reason(code=code, detail=detail, data=data))

    # --- v3: provenance, read through from the Leg -----------------------
    # These are PROPERTIES, not fields, and that is deliberate. `apply_live`
    # transforms the FIXTURE and hands the result to the UNCHANGED `evaluate_leg`
    # - so the provenance has to live on the Leg, which crosses that boundary,
    # rather than on the LegResult, which `evaluate_leg` constructs. Reading
    # through keeps one copy of each fact and makes it structurally impossible
    # for a LegResult's provenance to disagree with its own Leg's.

    @property
    def points_provenance(self) -> "PointsProvenance":
        return self.leg.points_provenance

    @property
    def live_outcome(self) -> Optional["LiveLegOutcome"]:
        return self.leg.live_outcome

    @property
    def superseded_candidates(self) -> List["PointsCandidate"]:
        return self.leg.superseded_candidates

    @property
    def flexible_date_findings(self) -> List["FlexibleFinding"]:
        return self.leg.flexible_date_findings

    @property
    def cash_provenance(self) -> str:
        return self.leg.cash_provenance

    @property
    def cash_captured_on(self) -> Optional[date]:
        return self.leg.cash_captured_on

    @property
    def is_live(self) -> bool:
        return self.leg.points_provenance is PointsProvenance.LIVE

    @property
    def scored_points_win(self) -> bool:
        """Whether the points side is both scoreable AND cheaper."""
        return (
            self.has_points_path
            and self.points_total_score_usd != float("inf")
            and self.points_total_score_usd < self.cash_total_score_usd
        )

    @property
    def winner_cost_usd(self) -> float:
        """
        What the optimizer's recommendation costs for this leg.

        An unscoreable points side (unknown surcharge) can never be the winner:
        its score is +inf, so cash is what this leg contributes to the total. v0
        scored those legs at $0 surcharge and let them win, which is precisely
        the bug that produced the 16% headline.
        """
        if self.has_points_path and self.points_total_score_usd != float("inf"):
            return min(self.points_total_score_usd, self.cash_total_score_usd)
        return self.cash_total_score_usd

    @property
    def winner_cost_low_usd(self) -> float:
        """
        Trip-total lower bound: the best this leg could possibly turn out.

        Two sources of optimism, and both belong here:
          * a modeled surcharge RANGE, at its low end;
          * an UNKNOWN surcharge, at its floor of $0 - `points_floor_usd` is the
            cheapest the points side could conceivably be, so a leg we refused to
            score still widens the trip's range rather than silently resolving to
            cash. That refusal is honest; pretending it costs exactly cash is not.
        """
        best = self.cash_total_score_usd
        if self.has_points_path and self.points_score_low_usd != float("inf"):
            best = min(best, self.points_score_low_usd)
        if self.points_floor_usd is not None:
            best = min(best, self.points_floor_usd)
        return best

    @property
    def winner_cost_high_usd(self) -> float:
        """Trip-total upper bound: the surcharge range's pessimistic end."""
        if self.has_points_path and self.points_score_high_usd != float("inf"):
            return min(self.points_score_high_usd, self.cash_total_score_usd)
        return self.cash_total_score_usd
