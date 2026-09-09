"""Data models for points transfer optimizer."""
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
    cash_component_known: bool = True
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
    cash_cost_known: bool = True
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
    """Where a leg's points price came from. Three REPORTED states."""

    LIVE = "live"                    # queried Seats.aero; scoreable awards found
    BADGE_FALLBACK = "badge_fallback"  # fixture badge kept; live did not supply
    UNAVAILABLE = "unavailable"      # no points side at all for this leg


class LiveQueryState(str, Enum):
    """
    What actually happened when this leg was queried. SIX internal states.

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
    """

    OK = "ok"                            # answered, >=1 award parsed
    NO_AWARD_SPACE = "no_award_space"    # answered, 0 awards. A FINDING.
    ANSWERED_UNREADABLE = "answered_unreadable"  # answered, unparseable. NOT a finding.
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
    pagination_note: str = ""
    snapshot_path: Optional[Path] = None
    served_from_cache: bool = False
    cache_fetched_at: Optional[datetime] = None
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

    def render(self) -> str:
        """
        The line the user reads. An error and an absence NEVER share wording.

        This is the single place where a state becomes prose, so the two cannot
        drift into looking alike.
        """
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
                + self._skipped_clause()
            )
        if self.state is LiveQueryState.NO_AWARD_SPACE:
            return (
                f"Seats.aero returned NO award space for {where} "
                f"(searched {where}, {self.rows_seen} rows). THIS IS A FINDING: "
                f"the API answered, every row it sent was READ SUCCESSFULLY, and "
                f"the answer is that there is no award to buy on this date."
                + self._skipped_clause()
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
                + self._skipped_clause()
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
        return f"Not queried: {self.note or 'live mode did not apply to this leg'}."


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
