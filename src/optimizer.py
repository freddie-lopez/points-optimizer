"""Core optimizer: transfer paths, stranding constraint, and cash-vs-points scoring."""
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from src import alternatives as alternatives_mod
from src import config, hotels, regions
from src.config import (
    CASH_VALUATION_CPP,
    DEFAULT_TRANSFER_INCREMENT,
    DEFAULT_VALUATION_CPP,
    cash_to_points_equivalent,
    convert_to_usd,
)
from src.funding import all_plans, best_plan, residue_report
from src.models import (
    METAL_PROVENANCE_TRIPS,
    MetalStatus,
    Award,
    CashOption,
    FundingPlan,
    Leg,
    LegResult,
    PointsCandidate,
    Ratio,
    Strategy,
    SurchargeEstimate,
    Transfer,
    TransferPath,
    Trip,
    check_trip_level_answers,
)
from src.ratio_manager import RatioManager
from src.regions import UnknownAirportError
from src.seats_client import SeatsClient
from src.surcharge import SurchargeTable, default_table
from src.wallet import Wallet


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


# A new PAY CASH sub-state. It says NOTHING about the user's transfer partners:
# the response named no program for the awards it returned, so the partnership
# question was never reached (finding M-5).
VERDICT_AWARD_UNATTRIBUTED = "cash (award unattributed)"
# An award in a program the wallet reaches only INDIRECTLY (UR -> BA Avios ->
# combine into Qatar / Finnair Avios). Not scored, and NOT "no points path".
VERDICT_INDIRECT_PATH = "cash (indirect path not scored)"
# A FLIGHT leg for more than one traveller. Award prices are per seat, and
# nothing in the scorer multiplies points, award taxes or the balance ceiling by
# the party size, or checks seats against it - so a couple's leg was scored as
# ONE seat of points against the party's cash and could print a false POINTS
# win. Until party pricing is modelled, such a leg is not scored at all.
VERDICT_PARTY_NOT_PRICED = "cash (multi-traveller points not priced)"


def _fmt_usd(x: float) -> str:
    return "n/a" if x == float("inf") else f"${x:,.2f}"


def validate_balances(user_balances: Dict[str, Optional[int]]) -> None:
    """
    Reject negative balances with a clear error. (Bug #2.)

    A balance of None means UNCONSTRAINED and is allowed. Zero is allowed and
    simply means that program contributes nothing.
    """
    for program, balance in (user_balances or {}).items():
        if balance is None:
            continue
        if not isinstance(balance, (int, float)) or isinstance(balance, bool):
            raise ValueError(
                f"Invalid balance for {program!r}: {balance!r} is not a number."
            )
        if balance < 0:
            raise ValueError(
                f"Invalid balance for {program!r}: {balance:,} - a points balance "
                f"cannot be negative. Pass 0 for an empty account, or omit the "
                f"flag entirely to leave the balance unconstrained."
            )


# ---------------------------------------------------------------------------
# Transfer sizing and the stranded-points constraint
# ---------------------------------------------------------------------------


def _round_up_to_increment(amount: int, increment: int) -> int:
    if increment <= 1:
        return amount
    return -(-amount // increment) * increment


def size_transfer(
    points_needed: int,
    ratio: Ratio,
    increment: int = DEFAULT_TRANSFER_INCREMENT,
    bonus_percent: float = 0.0,
) -> Tuple[int, int, int, int]:
    """
    Work out the smallest transfer that covers `points_needed`.

    This is the heart of the stranded-points fix (bug #3). The old code moved the
    user's ENTIRE balance into the destination program regardless of what the
    award cost, then recorded stranded_points = 0 unconditionally. Transfers are
    one-way and irreversible, so over-transferring permanently strands the
    excess in a program the user may have no other use for.

    Returns (source_to_transfer, delivered, stranded, ratio_remainder):
      source_to_transfer - source points moved, rounded up to `increment`
      delivered          - destination points received (after ratio + bonus)
      stranded           - destination points delivered but not needed
      ratio_remainder    - source points that bought nothing due to floor rounding
    """
    if points_needed <= 0:
        return 0, 0, 0, 0

    def deliver(src: int) -> int:
        out = ratio.apply(src)
        if bonus_percent:
            out = int(out * (1 + bonus_percent / 100))
        return out

    source_raw = ratio.source_needed_for(points_needed)
    source = _round_up_to_increment(source_raw, increment)

    # Floor rounding can leave us a hair short; step up by whole increments.
    guard = 0
    while deliver(source) < points_needed and guard < 1000:
        source += max(increment, 1)
        guard += 1

    delivered = deliver(source)
    stranded = delivered - points_needed
    ratio_remainder = source - ratio.source_needed_for(ratio.apply(source))
    return source, delivered, stranded, ratio_remainder


def max_unavoidable_stranding(
    ratio: Ratio, increment: int = DEFAULT_TRANSFER_INCREMENT
) -> int:
    """
    The largest overshoot that transfer increments make unavoidable.

    Chase UR moves in 1,000-point blocks, so an award priced at 57,300 points
    cannot be funded exactly - the closest you can get is 58,000. Rejecting all
    stranding outright would reject every real transfer, so the constraint is
    "strand no more than one indivisible block", i.e. the destination value of a
    single increment, minus one point.
    """
    per_increment = ratio.apply(increment)
    return max(per_increment - 1, 0)


def find_transfer_paths(
    user_balances: Dict[str, Optional[int]],
    user_cards: List[str],
    target_program: str,
    check_date: date,
    ratios_manager: RatioManager,
    points_needed: int,
    transfer_increment: int = DEFAULT_TRANSFER_INCREMENT,
    max_stranded_points: Optional[int] = None,
    include_infeasible: bool = False,
) -> List[TransferPath]:
    """
    Find every valid way to fund `points_needed` in `target_program`.

    Constraints enforced:
      - ratio effective dates and card dependencies (exact card matching)
      - EVERY card the user holds is evaluated, not just the first match (bug #1)
      - negative balances are rejected outright (bug #2)
      - transfers are sized to demand and stranding is measured and enforced (bug #3)

    `max_stranded_points` overrides the automatic per-ratio ceiling. Pass 0 for a
    strict no-stranding-at-all policy.

    A balance of None means unconstrained (no feasibility ceiling).

    Returns feasible paths sorted cheapest first.
    """
    validate_balances(user_balances)

    target_program = ratios_manager.normalize_program(target_program)
    paths: List[TransferPath] = []

    # ---- Option 0: points already sitting in the destination program --------
    existing_raw = None
    for program, balance in user_balances.items():
        if ratios_manager.normalize_program(program) == target_program:
            existing_raw = balance
            break

    existing_unconstrained = existing_raw is None and target_program in {
        ratios_manager.normalize_program(p) for p in user_balances
    }
    existing = existing_raw if existing_raw is not None else 0
    if existing_unconstrained:
        existing = points_needed  # unconstrained: assume it covers the award

    if existing >= points_needed and points_needed > 0:
        path = TransferPath()
        path.existing_target_points_used = points_needed
        path.stranded_points = 0
        paths.append(path)
        # Holding enough already is strictly best; still evaluate transfers below
        # so the caller can see alternatives.

    existing_used = min(existing, points_needed)
    shortfall = points_needed - existing_used

    # ---- Option 1..N: transfer from each source program, via each card ------
    for from_program, balance in user_balances.items():
        canonical_from = ratios_manager.normalize_program(from_program)
        if canonical_from == target_program:
            continue
        if balance is not None and balance == 0:
            continue
        if shortfall <= 0:
            continue

        unconstrained = balance is None

        # BUG #1 FIX: evaluate all cards, not just the first that matches.
        card_ratios = ratios_manager.best_ratio_across_cards(
            canonical_from, target_program, check_date, user_cards
        )
        if not card_ratios:
            continue

        bonus = ratios_manager.query_bonus(canonical_from, target_program, check_date)
        bonus_pct = bonus.bonus_percent if bonus else 0.0

        for card, ratio in card_ratios:
            source, delivered, stranded, remainder = size_transfer(
                shortfall, ratio, transfer_increment, bonus_pct
            )

            path = TransferPath()
            path.existing_target_points_used = existing_used
            path.stranded_points = stranded
            path.ratio_remainder_points = remainder

            transfer = Transfer(
                from_program=canonical_from,
                to_program=target_program,
                amount=source,
                ratio=ratio.as_string,
                ratio_numerator=ratio.ratio_numerator,
                ratio_denominator=ratio.ratio_denominator,
                card_used=card,
                points_delivered=delivered,
            )
            path.add_transfer(transfer)

            # Feasibility: can the user actually afford this?
            if not unconstrained and source > balance:
                path.feasible = False
                path.infeasible_reason = (
                    f"Needs {source:,} {canonical_from} but balance is {balance:,}"
                )

            # BUG #3 FIX: the stranding constraint now actually fires.
            ceiling = (
                max_stranded_points
                if max_stranded_points is not None
                else max_unavoidable_stranding(ratio, transfer_increment)
            )
            if path.feasible and stranded > ceiling:
                path.feasible = False
                path.infeasible_reason = (
                    f"Would strand {stranded:,} {target_program} points "
                    f"(ceiling {ceiling:,}). Transfers are one-way; the excess "
                    f"cannot be moved back."
                )

            if path.feasible or include_infeasible:
                paths.append(path)

    if not include_infeasible:
        paths = [p for p in paths if p.feasible]

    # BUG FIX (v1): the ranking key was `total_points_cost`, which ADDS SOURCE
    # POINTS ACROSS PROGRAMS. With a multi-currency balance dict this function
    # emits one path per source currency and then ranked them against each other
    # as if 10,000 UR and 10,000 MR were the same unit. They are not, the moment
    # any two ratios differ.
    #
    # Ranking now happens in USD at each currency's own valuation. With every
    # currency on the default 1cpp yardstick this produces numerically identical
    # ordering to v0, which is what the golden test in tests/test_funding.py
    # pins. The proper multi-currency ranking unit is funding.FundingPlan; this
    # function is retained for the single-currency v0 API and its own tests.
    def _usd(path: TransferPath) -> float:
        total = 0.0
        for t in path.transfers:
            total += t.amount * config.valuation_for(t.from_program)
        total += path.existing_target_points_used * config.valuation_for(target_program)
        return total

    paths.sort(key=lambda p: (_usd(p), p.total_waste))
    return paths


# ---------------------------------------------------------------------------
# Award-level optimization (Seats.aero path)
# ---------------------------------------------------------------------------


def optimize(
    trip: Trip,
    seats_client: SeatsClient,
    ratios_manager: RatioManager,
    valuation_cpp: float = DEFAULT_VALUATION_CPP,
    max_results: int = 5,
    transfer_increment: int = DEFAULT_TRANSFER_INCREMENT,
    max_stranded_points: Optional[int] = None,
) -> List[Strategy]:
    """
    Rank transfer strategies for a trip against live award availability.

    Returns top N strategies sorted by total cost (points valued at
    `valuation_cpp`, plus cash surcharge).
    """
    validate_balances(trip.balances)

    try:
        awards = seats_client.search(trip.origin, trip.destination, trip.date_range)
    except RuntimeError:
        # Degrade gracefully rather than crashing, but the caller MUST check
        # seats_client.last_error before reporting an empty result as "no award
        # availability" - it may mean the API was never reached.
        return []

    seen: Dict[str, Strategy] = {}
    # Recorded for run_search, so "none fundable" can be told apart from "none
    # returned" without asking the API (or its cache) a second time.
    try:
        seats_client.last_search_awards = list(awards)
    except Exception:  # noqa: BLE001 - a stub that refuses attributes is fine
        pass

    for award in awards:
        paths = find_transfer_paths(
            user_balances=trip.balances,
            user_cards=trip.cards_held,
            target_program=award.program,
            check_date=award.date,
            ratios_manager=ratios_manager,
            points_needed=award.cost,
            transfer_increment=transfer_increment,
            max_stranded_points=max_stranded_points,
        )

        for path in paths:
            if not path.feasible:
                continue

            points_cost = path.total_points_cost
            # An award whose taxes could not be converted to USD contributes
            # 0.0 here, which would silently understate it. Flag it rather than
            # let it pass as a total: this is the same failure shape as v0's
            # phantom $0 surcharge.
            # MR-5: the getattr FALLBACK was `True` as well - a second copy of
            # the unsafe default, which would have survived flipping the field.
            # An object that cannot say whether it knows does not know.
            cash_known = getattr(award, "cash_component_known", False)
            below_duty = _search_taxes_below_owed_uk_duty(trip, award) if cash_known else ""
            if below_duty:
                # The trip path's rule, applied here too: a UK-departure tax
                # figure below the duty it must contain is incomplete.
                cash_known = False
            cash_cost = award.cash_component if cash_known else 0.0
            total_value = points_cost * valuation_cpp + cash_cost
            apd_floor = 0.0 if cash_known else _search_uk_duty_floor(trip, award)
            if apd_floor:
                # An UNKNOWN cash side still has a known floor on a UK departure:
                # the duty is owed on the ticket. The displayed ">=" total is a
                # floor, and a floor that leaves out a certain tax is not one.
                total_value += apd_floor

            strategy = Strategy(
                award=award,
                transfer_path=path,
                points_cost=points_cost,
                cash_cost=cash_cost,
                total_value=total_value,
                cash_cost_known=cash_known,
                cash_cost_note=(
                    ""
                    if cash_known
                    else (
                        below_duty
                        or getattr(award, "cash_component_note", "")
                        or "The cash component of this award is unknown."
                    )
                    + (
                        f" The total shown is a FLOOR that includes UK Air "
                        f"Passenger Duty of ${apd_floor:,.2f}, owed on this ticket."
                        if apd_floor
                        else ""
                    )
                ),
            )

            # DEDUPLICATION USES THE RANKING RULE, not the raw total. Comparing
            # `total_value` let a 52,000-point award with NO tax figure ($520 +
            # an unknown) evict a 50,000-point one with $56 of known taxes ($556)
            # - and the known award vanished before the ranking could put it first.
            # Known and unknown cash are deduplicated SEPARATELY: an unknown can
            # never evict a known award (R-1), and a known award must not delete
            # a cheaper unknown one either (it is ranked after, not hidden).
            key = (
                f"{award.program}-{award.date}-{award.award_type}-"
                f"{'known' if strategy.cash_cost_known else 'unknown'}"
            )
            if key not in seen or _strategy_rank(strategy) < _strategy_rank(seen[key]):
                seen[key] = strategy

    results = list(seen.values())
    # AN UNKNOWN CASH COST CANNOT BE RANKED ON. `total_value` for such a strategy
    # counts the unknown as 0.0, so sorting on it put a KrisFlyer award whose
    # taxes Seats.aero does not report ABOVE a United award with $56 of known
    # taxes, and the summary named it the top strategy at "$0.00". Every
    # strategy whose cash is known ranks first, on its real total; the unknowns
    # follow, ordered by points only, and are printed as UNKNOWN - never $0.
    results.sort(key=_strategy_rank)
    return results[:max_results]


def _strategy_rank(s: "Strategy"):
    """Known cash first, on its real total; unknown cash after, by points only."""
    return (
        0 if s.cash_cost_known else 1,
        s.total_value if s.cash_cost_known else 0.0,
        s.points_cost,
    )


def _search_uk_duty_floor(trip, award) -> float:
    """Per-passenger UK APD owed on this award's ticket, or 0.0 if none/unknown."""
    from types import SimpleNamespace

    from src.live_trip import uk_duty_per_passenger_usd

    leg = SimpleNamespace(
        kind="flight",
        origin=str(getattr(trip, "origin", "") or "").upper(),
        destination=str(getattr(trip, "destination", "") or "").upper(),
        date=award.date,
    )
    return uk_duty_per_passenger_usd(leg, award) or 0.0


def _search_taxes_below_owed_uk_duty(trip, award) -> str:
    """The trip path's below-duty rule for a single-route search award."""
    from types import SimpleNamespace

    from src.live_trip import taxes_below_owed_uk_duty

    leg = SimpleNamespace(
        kind="flight",
        origin=str(getattr(trip, "origin", "") or "").upper(),
        destination=str(getattr(trip, "destination", "") or "").upper(),
        date=award.date,
    )
    return taxes_below_owed_uk_duty(leg, award)


# ---------------------------------------------------------------------------
# Leg-level evaluation: cash fallback scored head-to-head against points
# ---------------------------------------------------------------------------


def _wallet_from(
    wallet: Optional[Wallet],
    user_balances: Optional[Dict[str, Optional[int]]],
    user_cards: Optional[List[str]],
) -> Wallet:
    """Accept either a v1 Wallet or the v0 (balances, cards) pair."""
    if wallet is not None:
        return wallet
    validate_balances(user_balances or {})
    return Wallet(balances=dict(user_balances or {}), cards=list(user_cards or []))


def _leg_region_and_country(leg: Leg) -> Tuple[Optional[str], Optional[str], str]:
    """
    (route_region, departure_country, why_not) for a leg.

    Returns (None, None, reason) when the leg does not name its airports. An
    unclassifiable leg CANNOT receive a modeled surcharge - a wrong region picks
    a wrong band.
    """
    if not leg.origin or not leg.destination:
        return None, None, (
            "the leg does not name its origin and destination airports, so its "
            "route region cannot be classified"
        )
    try:
        return regions.classify(leg.origin, leg.destination), regions.departure_country(
            leg.origin
        ), ""
    except UnknownAirportError as e:
        return None, None, str(e)


def _captured_surcharge(cand: PointsCandidate) -> Optional[SurchargeEstimate]:
    """
    A surcharge read off a real booking page, if the fixture declares one.

    `surcharge_captured` must be set explicitly. A fixture with
    `cash_surcharge: 0.0` and no flag is NOT a captured zero - it is silence, and
    silence is exactly what v0 mistook for free.
    """
    if not cand.surcharge_captured:
        return None
    try:
        usd = convert_to_usd(cand.cash_surcharge, cand.surcharge_currency)
    except ValueError as e:
        # MANAGER REVIEW MR-2. L-1's THIRD CALL SITE.
        #
        # L-1 - "one leg's unconfigured currency aborts the whole trip" - was
        # fixed for `cash_options` and for `mandatory_fees` and reported as
        # complete. It was not fixed here, and `surcharge_currency` is a
        # documented trip-fixture field (trip_loader.py:83), so a fixture with
        # `surcharge_captured: true, surcharge_currency: "JPY"` killed the entire
        # run with `Error: No FX rate configured for 'JPY'` and exit 1, naming no
        # leg and taking every other leg's result with it. That is WORSE than the
        # bug L-1 described, because L-1's message at least came from a path the
        # fix later taught to name the leg.
        #
        # This matters for v5 specifically: `--new-trip` will GENERATE this field.
        #
        # An unpriceable captured surcharge is UNKNOWN for this candidate - not
        # $0, not a guessed rate, and not a dead run. Same shape as C-2's
        # unconvertible taxes: a cash figure we cannot price poisons the cash
        # side of THIS option and nothing else.
        return SurchargeEstimate.unknown(
            f"The captured surcharge for {cand.label!r} CANNOT BE PRICED. It was "
            f"captured as {cand.surcharge_currency.upper()} "
            f"{cand.cash_surcharge:,.2f} and {e} The amount is UNKNOWN - it is "
            f"NOT $0 and it is NOT being converted at a rate the tool invented. "
            f"Supply one with --fx {cand.surcharge_currency.upper()}=<rate> to "
            f"score this option. This leg's other options and the rest of the "
            f"trip are unaffected."
        )
    if usd < 0:
        # FINDING M-3, BELT AND BRACES. `SurchargeTable.validate()` rejects a
        # negative amount on a table row; a figure captured from live data used
        # to bypass that check entirely and became a cash CREDIT on the points
        # side. The parser now refuses a negative tax at source; this refuses one
        # from any other capture route too. A negative surcharge is corrupt data,
        # and corrupt data is unknown - never a discount.
        return SurchargeEstimate.unknown(
            f"A captured surcharge of ${usd:,.2f} was supplied for {cand.label!r}. "
            f"A carrier-imposed surcharge cannot be negative - nobody pays you to "
            f"fly - so this is corrupt data and is carried as UNKNOWN. It is NOT a "
            f"credit and it must never make a points option look cheaper."
        )
    return SurchargeEstimate(
        amount_low=usd,
        amount_point=usd,
        amount_high=usd,
        currency="USD",
        basis="one_way" if not cand.is_round_trip else "round_trip",
        confidence="captured",
        matched_rule="captured from source",
        source=cand.source,
        notes=cand.source_note,
    )


def resolve_leg_surcharge(
    leg: Leg,
    cand: PointsCandidate,
    surcharges: SurchargeTable,
    ratios_manager: RatioManager,
    today: Optional[date] = None,
) -> SurchargeEstimate:
    """
    The surcharge for one candidate on one leg. Never returns a silent zero.

    Hotels have no carrier-imposed surcharge; their cash-owed-anyway costs are
    MandatoryFee objects on the leg, handled separately.
    """
    # FINDING C-2. AN UNPRICEABLE TAX POISONS THE WHOLE CASH SIDE, AND MUST.
    #
    # This runs BEFORE the captured check and before the table, because the
    # modeled table is what used to answer here: a tier-5 program-policy $0 row
    # said "this program levies no carrier surcharge", which is TRUE and is not
    # an answer to the question being asked. The question is what the leg costs
    # in cash, and MXN 9,000 of taxes the tool cannot convert is part of that and
    # is unknown. Substituting a modeled figure for a DIFFERENT quantity is how a
    # $950 leg scored as $500 and beat $520 of cash.
    if getattr(cand, "taxes_unconvertible", False):
        return SurchargeEstimate.unknown(
            f"The cash side of {cand.label!r} CANNOT BE PRICED. Seats.aero "
            f"reported taxes of {cand.observed_taxes_currency or '(unnamed currency)'} "
            f"{cand.observed_taxes_amount:,.2f} and no FX rate for that currency is "
            f"configured, so the amount is UNKNOWN - not $0. "
            f"{cand.observed_taxes_note} A program-wide no-carrier-surcharge policy "
            f"does NOT price these taxes: it is a statement about YQ/YR, a different "
            f"quantity. Supply the rate with --fx to score this leg."
        )
    # The same poison, for every other way a live award's taxes can be unknown:
    # no figure, a source that does not report taxes, or a 0 that means "not
    # reported". Before this, the table's program-policy $0 answered here and the
    # missing taxes were scored as $0.
    if getattr(cand, "taxes_unknown", False):
        return SurchargeEstimate.unknown(
            f"The cash side of {cand.label!r} CANNOT BE PRICED: the taxes on this "
            f"award are UNKNOWN - not $0. {cand.observed_taxes_note} A "
            f"program-wide no-carrier-surcharge policy does NOT price taxes: it is "
            f"a statement about YQ/YR, a different quantity."
        )

    captured = _captured_surcharge(cand)
    if captured is not None:
        return captured

    if leg.kind == "hotel":
        return SurchargeEstimate(
            amount_low=0.0, amount_point=0.0, amount_high=0.0,
            confidence="modeled", matched_rule="hotel: no carrier surcharge exists",
            source="structural - a hotel award has no operating carrier",
            notes="Mandatory hotel fees are modelled separately as MandatoryFee.",
        )

    # Region and metal are each supplied only when actually known. An empty value
    # simply fails to match the tiers that need it; it is never substituted for.
    # A program-wide no-YQ policy row needs neither, which is why a United or
    # Aeroplan candidate still resolves to a confirmed $0 here.
    region, country, why_not = _leg_region_and_country(leg)

    # METAL FROM THE ITINERARY LOOKUP, for a source verified `excludes_yq`
    # (only `award_to_candidate` sets this carrier_source, and only then). The
    # itinerary's carriers - or the union of AMBIGUOUS sets - resolve TOGETHER:
    # a figure only when every one gives the same outcome. For a set that means
    # "all of these fly segments", which is safe for exactly that reason.
    metal = getattr(cand, "metal", None)
    if cand.carrier_source == METAL_PROVENANCE_TRIPS and metal is not None and (
        metal.status in (MetalStatus.KNOWN, MetalStatus.AMBIGUOUS)
    ):
        est = surcharges.resolve_ambiguous_metal(
            ratios_manager.normalize_program(cand.program),
            list(metal.all_carriers),
            region or "",
            cand.cabin,
            country or "",
            is_round_trip=cand.is_round_trip,
            today=today,
        )
        if not est.is_known:
            from src import seats_trips

            label = seats_trips.trips_parser_label()
            est.notes = (
                f"Cannot model a surcharge for {cand.label!r} on the metal its "
                f"itinerary lookup found ({', '.join(metal.all_carriers)})"
                + (f": {why_not}" if region is None else "")
                + f". {est.notes}"
                + (f" {label}" if label else "")
            ).strip()
        return est
    est = surcharges.resolve(
        ratios_manager.normalize_program(cand.program),
        cand.operating_carrier if cand.has_known_metal else "",
        region or "",
        cand.cabin,
        country or "",
        is_round_trip=cand.is_round_trip,
        carrier_is_known=cand.has_known_metal,
        today=today,
    )
    if not est.is_known and metal is not None and metal.status in (
        MetalStatus.KNOWN, MetalStatus.AMBIGUOUS
    ):
        # The lookup DID name metal; it is deliberately not used here, and the
        # note must say that rather than "no operating carrier is recorded".
        # The metal came from the trips parser: its label goes on the note
        # while the parser is unverified (Re-test 2, R2-7).
        from src import seats_trips

        label = seats_trips.trips_parser_label()
        est.notes = (
            f"Cannot model a surcharge for {cand.label!r}: the itinerary lookup "
            f"names {', '.join(metal.all_carriers)} by flight number, and that "
            f"metal is NOT used for a surcharge because whether Seats.aero's taxes "
            f"for this source already include one is not verified. The band for "
            f"that metal is stated on the operating-airline line and is NOT ADDED."
            + (f" {label}" if label else "")
        )
        return est
    if not est.is_known:
        why = []
        if not cand.operating_carrier:
            why.append("no operating carrier is recorded")
        elif not cand.has_known_metal:
            why.append(
                f"the operating carrier ({cand.operating_carrier}) is "
                f"{cand.carrier_source}, not confirmed"
            )
        if region is None:
            why.append(why_not)
        est.notes = (
            f"Cannot model a surcharge for {cand.label!r}"
            + (f": {'; '.join(why)}" if why else "")
            + f". {est.notes}"
        ).strip()
    return est


def _better_transfer_date(
    ratios_manager: RatioManager,
    source_currency: str,
    target_program: str,
    transfer_date: date,
    cards: List[str],
    horizon_days: int = config.TRANSFER_WINDOW_DAYS,
) -> Optional[Tuple[date, str, str]]:
    """
    Whether a better ratio exists at some other transfer date within the horizon.

    Returns (cliff_date, ratio_now, ratio_then) or None. This is what surfaces
    "transfer before 2026-10-01 to keep 1:1" as an actionable finding instead of
    silently quoting whichever ratio happens to apply on the travel date - which
    is what v0 did.
    """
    def value_on(d: date) -> Optional[Tuple[float, str]]:
        pairs = ratios_manager.best_ratio_across_cards(
            source_currency, target_program, d, cards
        )
        if not pairs:
            return None
        ratio = pairs[0][1]
        return ratio.ratio_denominator / ratio.ratio_numerator, ratio.as_string

    now = value_on(transfer_date)
    if now is None:
        return None

    # Ratio changes are step functions at row boundaries, so only the boundaries
    # need checking - no day-by-day scan.
    boundaries = sorted(
        {
            b
            for r in ratios_manager.ratios
            if r.from_program == ratios_manager.normalize_program(source_currency)
            and r.to_program == target_program
            for b in (r.effective_from, r.effective_to + timedelta(days=1))
            if transfer_date < b <= transfer_date + timedelta(days=horizon_days)
        }
    )
    for boundary in boundaries:
        later = value_on(boundary)
        if later is None:
            continue
        if later[0] < now[0]:
            # The ratio gets WORSE here: this is a cliff to beat.
            return boundary, now[1], later[1]
        if later[0] > now[0]:
            # A better ratio arrives later.
            return boundary, now[1], later[1]
    return None


def evaluate_leg(
    leg: Leg,
    user_balances: Optional[Dict[str, Optional[int]]] = None,
    user_cards: Optional[List[str]] = None,
    ratios_manager: RatioManager = None,
    source_program: str = "UR",
    valuation_cpp: float = CASH_VALUATION_CPP,
    transfer_increment: int = DEFAULT_TRANSFER_INCREMENT,
    max_stranded_points: Optional[int] = None,
    *,
    wallet: Optional[Wallet] = None,
    transfer_date: Optional[date] = None,
    surcharges: Optional[SurchargeTable] = None,
    show_alternatives: bool = True,
    today: Optional[date] = None,
) -> LegResult:
    """
    Score one leg: cheapest cash vs best points path, on the same yardstick.

    Every leg gets a cash option. Cash is converted to a points-equivalent at
    `valuation_cpp` (1 cent per point by default) so the two compete directly.
    A leg where cash wins is reported as "pay cash" - that is a correct answer,
    not a failure to find a points path.

    v1 changes, in the order they bite:

      * RATIOS ARE LOOKED UP ON `transfer_date`, NOT ON THE TRAVEL DATE. You do
        not transfer points in January because you fly in January; you transfer
        them when you choose to. Defaults to today.
      * MANDATORY FEES ARE ADDED TO BOTH SIDES. A destination fee is owed on a
        points stay too.
      * AN UNKNOWN SURCHARGE MAKES THE POINTS SIDE UNSCOREABLE. It does not make
        it free. The leg reports a BREAK-EVEN SURCHARGE instead: "points win only
        if the surcharge is below $X". This is the direct inverse of the v0 bug.
      * A VERDICT THAT FLIPS INSIDE THE SURCHARGE RANGE is flagged
        VERDICT_SENSITIVE and is not presented as settled.
    """
    wallet = _wallet_from(wallet, user_balances, user_cards)
    surcharges = surcharges or default_table()
    transfer_date = transfer_date or config.default_transfer_date()
    today = today or config.default_transfer_date()
    result = LegResult(leg=leg)

    nights = hotels.nights_for(leg)

    # ---- Cash side ----------------------------------------------------------
    #
    # FINDING H-5: CASH IS PRICED PER DATE, NOT AS ONE CHEAPEST NUMBER.
    #
    # v3 added `CashOption.date` so a leg can carry captured fares for several
    # dates - the ONLY honest route by which an off-date live award becomes
    # scoreable (plan section 4.5). The scorer then ignored the date entirely and
    # took `min()` over every captured price, so a promoted Jan-17 award was
    # scored against the Jan-15 fare. The escape hatch section 4.5 offers, whose
    # whole purpose is to remove a bias toward points, was itself biased toward
    # points. Cash is therefore bucketed by the date it is FOR, and each points
    # candidate is scored against the fare for ITS OWN date, below.
    priced_cash: List[Tuple[float, CashOption]] = []
    cash_by_date: Dict[Optional[date], Tuple[float, CashOption]] = {}
    for opt in leg.cash_options:
        try:
            usd = convert_to_usd(opt.amount, opt.currency)
        except ValueError as e:
            # FINDING L-1. One leg's unconfigured currency used to abort the
            # whole trip through main(), naming no leg, and taking six other
            # legs' results with it. An unpriceable cash option is a missing
            # input for THIS leg: it is dropped with a message that names the
            # leg and the currency, and everything else still gets scored.
            result.warnings.append(
                f"{leg.id}: the cash option {opt.label!r} is quoted in "
                f"{opt.currency.upper()} and {e} It CANNOT be scored and is not "
                f"being converted at a rate the tool invented. Supply one with "
                f"--fx {opt.currency.upper()}=<rate>. This leg's other options "
                f"are unaffected and the rest of the trip still scores."
            )
            continue
        opt.amount_usd = usd
        if config.is_placeholder_rate(opt.currency):
            result.rests_on_placeholder_fx = True
        priced_cash.append((usd, opt))
        # An option with no date belongs to the leg's own date - that is what
        # `date=None` means, and `trip_loader` never guesses one.
        key = opt.date or leg.date
        if key not in cash_by_date or usd < cash_by_date[key][0]:
            cash_by_date[key] = (usd, opt)

    # MANDATORY FEES. On the LEG, not on the cash option - that placement is the
    # bug fix. They are owed whichever way the booking is paid for.
    try:
        fees_on_cash = hotels.fees_usd(
            leg.mandatory_fees, nights, leg.travelers, points_side=False
        )
        fees_on_points = hotels.fees_usd(
            leg.mandatory_fees, nights, leg.travelers, points_side=True
        )
    except ValueError as e:
        # Same rule as above: a fee in an unconfigured currency is a missing
        # input for this leg, not a reason to abandon the trip (finding L-1).
        # It is NOT dropped to zero - a fee that cannot be priced makes both
        # totals lower bounds, and the leg says so.
        fees_on_cash = fees_on_points = 0.0
        currencies = sorted(
            {f.currency.upper() for f in leg.mandatory_fees}
        )
        result.warnings.append(
            f"{leg.id}: a mandatory fee is quoted in {', '.join(currencies)} and "
            f"{e} The fee is UNKNOWN, NOT $0 - it is owed on the cash side AND on "
            f"the points side, so BOTH totals below are lower bounds. Supply the "
            f"rate with --fx to price it."
        )
        result.mandatory_fees_unpriceable = True
    result.mandatory_fees_usd = fees_on_cash
    for fee in leg.mandatory_fees:
        if config.is_placeholder_rate(fee.currency):
            result.rests_on_placeholder_fx = True
        result.add_reason(
            "MANDATORY_FEE",
            f"{fee.label}: {fee.amount:,.2f} {fee.currency} per {fee.per}"
            + (" - owed on a points booking too" if fee.payable_on_points else ""),
            label=fee.label,
            payable_on_points=fee.payable_on_points,
        )

    def _cash_for(when: Optional[date]) -> Optional[Tuple[float, CashOption]]:
        """The cheapest captured cash price FOR A GIVEN TRAVEL DATE, or None."""
        return cash_by_date.get(when or leg.date)

    # THE LEG'S OWN-DATE BASELINE. This is what "pay cash" costs: you fly on the
    # date the trip says, so this - never an off-date fare - is what the trip's
    # all-cash total is built from.
    own_date_cash = _cash_for(leg.date)
    if own_date_cash is not None:
        result.cash_baseline_usd = own_date_cash[0] + fees_on_cash
    elif priced_cash:
        # No fare for the leg's own date, but fares for other dates exist. The
        # baseline is the cheapest of what there is, and the leg is flagged.
        result.cash_baseline_usd = min(u for u, _ in priced_cash) + fees_on_cash
        result.warnings.append(
            f"{leg.id}: no cash fare was captured for this leg's OWN date "
            f"({leg.date}). The cash figures here are for other dates and are not "
            f"a like-for-like comparison; capture the fare for {leg.date}."
        )
    else:
        result.cash_baseline_usd = float("inf")

    if priced_cash:
        # Provisional: the scoring cash is the own-date fare unless a points
        # candidate for another date wins the leg, in which case it is replaced
        # below with the fare for THAT date. Both sides of a head-to-head must
        # be about the same day.
        chosen = own_date_cash or min(priced_cash, key=lambda t: t[0])
        result.cash_usd, result.best_cash = chosen
        result.cash_total_score_usd = result.cash_usd + fees_on_cash
        result.cash_as_points_equivalent = cash_to_points_equivalent(
            result.cash_total_score_usd, valuation_cpp
        )
    else:
        result.warnings.append("No cash option captured for this leg.")
        result.cash_total_score_usd = float("inf")

    if result.rests_on_placeholder_fx:
        result.add_reason(
            "FX_PLACEHOLDER",
            "This leg's total rests on an FX rate with no source. Supply one with "
            "--fx to take responsibility for it.",
        )

    # ---- Points side --------------------------------------------------------
    # Best is chosen on the SCOREABLE score. A candidate whose surcharge is
    # unknown cannot be scored and therefore cannot win; it is remembered
    # separately so the leg can still report its break-even.
    best: Optional[Tuple[float, PointsCandidate, FundingPlan, SurchargeEstimate]] = None
    unscoreable: List[Tuple[PointsCandidate, FundingPlan, SurchargeEstimate]] = []
    blocked_partner_candidates: List[str] = []
    unattributed_candidates: List[PointsCandidate] = []
    indirect_candidates: List[PointsCandidate] = []
    party_candidates: List[PointsCandidate] = []
    # The cash context each candidate was scored against, keyed by id(cand), so
    # the winner's own date's fare can become the leg's reported cash.
    cash_context: Dict[int, Tuple[float, Optional[CashOption], Optional[date]]] = {}

    # MULTI-TRAVELLER FLIGHT LEGS ARE NOT PRICED. Award prices are per seat and
    # nothing below multiplies them by the party. Facts that do NOT depend on the
    # party size - an award naming no program, a program reachable only
    # indirectly, a program that is no partner at all - are still recorded per
    # candidate; only a candidate that would be SCORED is held back.
    party_leg = leg.kind == "flight" and int(getattr(leg, "travelers", 1) or 1) > 1
    for cand in leg.points_candidates:
        # FINDING M-5. A RESPONSE THAT NAMED NO PROGRAM IS A DATA FAILURE, NOT A
        # FACT ABOUT CHASE'S PARTNER LIST.
        #
        # `resolve_source(None)` returns an empty program name, which then
        # travelled all the way to `is_partner("UR", "", ...)` -> False -> "No UR
        # transfer partner covers this leg. Cash is the only option." That is a
        # definitive statement about partnerships generated from a MISSING FIELD,
        # with an empty program name rendered mid-sentence. It is C-1's shape one
        # layer up: we did not get usable data, so we announced a finding.
        if getattr(cand, "program_attribution_missing", False) or not (
            cand.program or ""
        ).strip():
            unattributed_candidates.append(cand)
            result.warnings.append(
                f"{cand.label}: THE RESPONSE DID NOT NAME A PROGRAM for this "
                f"award, so it cannot be attributed and cannot be scored. This is "
                f"a DATA FAILURE, not a finding: nothing is being claimed about "
                f"whether a UR transfer partner covers this leg. "
                f"{cand.source_note}".strip()
            )
            result.add_reason(
                "PROGRAM_UNATTRIBUTED",
                f"An award of {cand.points:,} points arrived with no program name "
                f"(or a source code this tool does not map). It is real and it is "
                f"unattributable; no partnership claim is made about it.",
                points=cand.points,
            )
            continue

        target = ratios_manager.normalize_program(cand.program)

        reachable = any(
            ratios_manager.is_partner(currency, target, transfer_date)
            for currency in wallet.currencies
        )
        if not reachable and getattr(cand, "indirect_ur_path", ""):
            # THERE IS A PATH; IT IS TWO HOPS AND IT IS NOT SCORED. Falling
            # through to NOT_A_PARTNER below would print "no points path" and
            # count the leg under "NO UR path at all" - a claim about the wallet
            # that is false, because UR -> BA Avios -> combine reaches it.
            indirect_candidates.append(cand)
            result.warnings.append(
                f"{cand.label}: {cand.program} is not a DIRECT transfer partner, "
                f"but it IS reachable indirectly - NOT scored. "
                f"{cand.indirect_ur_path}"
            )
            result.add_reason(
                "INDIRECT_PATH_UNVERIFIED",
                f"{cand.program} is reachable only indirectly "
                f"({cand.points:,} points). The path has conditions this tool "
                f"cannot check against your accounts, so it is not scored; no "
                f"claim is made that no points path exists.",
                points=cand.points,
            )
            continue
        if not reachable:
            held = ", ".join(sorted(wallet.currencies)) or "(no currencies supplied)"
            result.warnings.append(
                f"{cand.label}: {cand.program} is not a transfer partner of any "
                f"currency you hold ({held}) - no points path."
            )
            result.add_reason(
                "NOT_A_PARTNER",
                f"{cand.program} is not reachable from {held} on {transfer_date}.",
                program=cand.program,
            )
            continue
        if party_leg:
            party_candidates.append(cand)
            continue

        try:
            points_needed = hotels.resolve_candidate_points(leg, cand)
        except hotels.HotelDataError as e:
            result.warnings.append(str(e))
            continue
        if not points_needed or points_needed <= 0:
            result.warnings.append(
                f"{cand.label}: no award price supplied, so this candidate cannot "
                f"be scored. Reported as a break-even instead of a guess."
            )
            continue

        plan = best_plan(
            wallet=wallet,
            target_program=target,
            points_needed=points_needed,
            transfer_date=transfer_date,
            ratios_manager=ratios_manager,
            max_stranded_points=max_stranded_points,
            default_valuation_cpp=valuation_cpp,
        )
        if plan is None:
            blocked_partner_candidates.append(f"{cand.label} -> {target}")
            result.warnings.append(
                f"{cand.label}: {target} IS a transfer partner, but no feasible "
                f"transfer path exists - blocked by the balance ceiling or the "
                f"stranded-points constraint, NOT by a missing partner."
            )
            continue

        surcharge = resolve_leg_surcharge(leg, cand, surcharges, ratios_manager, today)

        # H-5: this candidate is FOR a date, and only the cash captured for that
        # same date may be scored against it. `award_date=None` means the leg's
        # own date, which is every badge candidate and every on-date award.
        cand_date = getattr(cand, "award_date", None) or leg.date
        matched_cash = _cash_for(cand_date)
        if matched_cash is None:
            # A promoted award should always have a matching fare - promotion is
            # gated on one existing - so this is the residual case of a candidate
            # for a date nobody captured. It is NOT scored against another date's
            # fare, which is the entire finding.
            result.warnings.append(
                f"{cand.label}: this award is for {cand_date} and NO cash fare was "
                f"captured for that date, so there is nothing legitimate to score "
                f"it against. It is NOT being compared to another date's fare - "
                f"that comparison is biased in favour of points (plan section 4.5). "
                f"Capture the fare for {cand_date} to score it."
            )
            continue
        cand_cash_usd, cand_cash_opt = matched_cash
        cand_cash_score = cand_cash_usd + fees_on_cash
        cash_context[id(cand)] = (cand_cash_score, cand_cash_opt, cand_date)

        # M-4: the API's OWN tax figure is a KNOWN cost and belongs in every
        # points-side total. `extra_observed_taxes_usd` is zero when the taxes
        # already ride as the captured surcharge, so this cannot double count.
        taxes = cand.extra_observed_taxes_usd

        if surcharge.is_known:
            score = plan.score_usd + surcharge.amount_point + fees_on_points + taxes
            if best is None or score < best[0]:
                best = (score, cand, plan, surcharge)
        else:
            unscoreable.append((cand, plan, surcharge))

        # Would a different transfer date give a better ratio?
        for currency in sorted(plan.per_currency_spend):
            window = _better_transfer_date(
                ratios_manager, currency, target, transfer_date, wallet.cards
            )
            if window:
                cliff, now_ratio, then_ratio = window
                result.add_reason(
                    "TRANSFER_DATE_WINDOW",
                    f"{currency} -> {target} is {now_ratio} on {transfer_date} but "
                    f"{then_ratio} from {cliff}. The ratio that applies is the one "
                    f"in force WHEN YOU TRANSFER, not when you fly.",
                    currency=currency,
                    program=target,
                    cliff=str(cliff),
                    ratio_now=now_ratio,
                    ratio_then=then_ratio,
                )

    # ---- Adopt the winning candidate's own date's cash (H-5) ----------------
    #
    # Both sides of the head-to-head must be about the same day. Whichever
    # candidate wins, the leg's reported cash becomes the fare captured FOR THAT
    # CANDIDATE'S DATE - which for every on-date award and every badge is the
    # leg's own date, i.e. no change at all. `cash_baseline_usd` keeps the
    # own-date fare separately so the trip's all-cash total is never inflated by
    # an off-date comparison, and so a date-shifted "win" that loses to simply
    # flying on the original day is caught below.
    winning_cand = best[1] if best is not None else (
        min(unscoreable, key=lambda t: t[1].score_usd)[0] if unscoreable else None
    )
    if winning_cand is not None and id(winning_cand) in cash_context:
        cash_score, cash_opt, cand_date = cash_context[id(winning_cand)]
        if cash_opt is not None:
            result.cash_usd = cash_opt.amount_usd
            result.best_cash = cash_opt
            result.cash_total_score_usd = cash_score
            result.cash_as_points_equivalent = cash_to_points_equivalent(
                cash_score, valuation_cpp
            )
            result.scoring_date = cand_date
            if cand_date != leg.date:
                result.scored_off_date = True
                result.warnings.append(
                    f"{leg.id}: this leg is scored on {cand_date}, NOT on its own "
                    f"date of {leg.date}. The award is for {cand_date} and the "
                    f"comparison uses the cash fare captured for {cand_date} "
                    f"({_fmt_usd(cash_score)}), which is the only honest "
                    f"comparison for it. Flying on {leg.date} and paying cash "
                    f"costs {_fmt_usd(result.cash_baseline_usd)}."
                )

    # ---- Resolve the points side -------------------------------------------
    if best is not None:
        score, cand, plan, surcharge = best
        taxes = cand.extra_observed_taxes_usd
        result.has_points_path = True
        result.best_points = cand
        result.funding_plan = plan
        result.points_path = _as_transfer_path(plan)
        result.points_required = sum(plan.per_currency_spend.values()) + plan.existing_target_points_used
        result.surcharge = surcharge
        result.points_surcharge_usd = surcharge.amount_point
        result.observed_taxes_usd = taxes
        result.points_total_score_usd = score
        result.points_score_low_usd = (
            plan.score_usd + surcharge.amount_low + fees_on_points + taxes
        )
        result.points_score_high_usd = (
            plan.score_usd + surcharge.amount_high + fees_on_points + taxes
        )
        result.reasons.extend(plan.reasons)
        result.add_reason(
            "SURCHARGE_CAPTURED" if surcharge.confidence == "captured" else "SURCHARGE_MODELED",
            f"Surcharge {surcharge.render()} ({surcharge.confidence})"
            + (f" via {surcharge.matched_rule}" if surcharge.matched_rule else ""),
            confidence=surcharge.confidence,
            low=surcharge.amount_low,
            point=surcharge.amount_point,
            high=surcharge.amount_high,
        )
        if "BASIS CONVERTED" in (surcharge.notes or ""):
            result.add_reason(
                "SURCHARGE_BASIS_CONVERTED",
                "A round-trip surcharge row was halved for this one-way leg. Real "
                "surcharges are directional and are not symmetric.",
            )
        if surcharge.notes:
            result.warnings.append(f"Surcharge note: {surcharge.notes}")
        _record_candidate_warnings(result, cand)

        # A candidate we could NOT score is still worth reporting even when a
        # different candidate won. v0's B1 was won by exactly such a candidate on
        # a false $0; saying nothing about it would hide that it exists.
        for other, other_plan, other_surch in unscoreable:
            floor = other_plan.score_usd + fees_on_points + other.extra_observed_taxes_usd
            other_taxes = _candidate_taxes_unknown(other)
            what = (
                "TAXES are UNKNOWN (not $0)" if other_taxes
                else "carrier-imposed surcharge is UNKNOWN (not $0)"
            )
            before = "before any taxes or surcharge" if other_taxes else "before any surcharge"
            result.warnings.append(
                f"{other.label}: NOT SCORED - its {what}. At "
                f"{other_plan.spend_summary()} its cost floor is ${floor:,.2f} "
                f"{before}, against ${score:,.2f} for the option chosen above."
            )
            if floor < score:
                # Literal codes, one per branch: way (10) discovers reason codes
                # by parsing `add_reason("CODE", ...)` calls, by design.
                if other_taxes:
                    result.add_reason(
                        "TAXES_UNKNOWN",
                        f"{other.label} could beat the chosen option if its taxes "
                        f"plus any surcharge come to less than "
                        f"${score - floor:,.2f}. It was not scored because its "
                        f"taxes are unknown.",
                        label=other.label,
                    )
                else:
                    result.add_reason(
                        "SURCHARGE_UNKNOWN",
                        f"{other.label} could beat the chosen option if its surcharge "
                        f"is below ${score - floor:,.2f}. It was not scored because "
                        f"that figure is unknown.",
                        label=other.label,
                    )
                # The unknown could beat the winner, so it widens the low bound.
                if result.points_floor_usd is None or floor < result.points_floor_usd:
                    result.points_floor_usd = floor
                    result.points_floor_candidate = other

        # THE ONE-LINE ANSWER v0 could not give. When the option we CAN score
        # carries a structurally zero surcharge and the one we cannot is the
        # surcharge-heavy incumbent, say so plainly.
        if unscoreable and surcharge.is_known and surcharge.amount_point == 0.0:
            result.warnings.append(
                f"SAME ROUTE, DIFFERENT PROGRAM, NO CARRIER SURCHARGE: "
                f"{cand.program} carries $0 in carrier-imposed surcharge here as a "
                f"matter of program policy, while "
                f"{', '.join(o.program for o, _, _ in unscoreable)} could not be "
                f"scored at all because its surcharge on this metal is unknown. "
                f"Government taxes and airport charges are still owed on both and "
                f"are not modelled."
            )

    elif unscoreable:
        # A points path EXISTS and is fundable; we simply cannot score it,
        # because we do not know the cash that comes with it. Report the
        # break-even surcharge rather than pretending it is zero.
        cand, plan, surcharge = min(unscoreable, key=lambda t: t[1].score_usd)
        # FINDING M-4. THE FLOOR IS "THE LEAST THIS CAN POSSIBLY COST", AND THE
        # API'S OWN TAX FIGURE IS A KNOWN PART OF THAT COST.
        #
        # The formatter has always LABELLED this figure "points at the run's
        # valuation + the API's taxes, with the carrier surcharge at its $0
        # floor". The taxes were not in it: `award_to_candidate` zeroed
        # `cash_surcharge` on this path two files earlier, so a 50,000-point
        # Flying Blue award with a KNOWN CAD 44.60 of taxes printed a floor of
        # $500.00 instead of $532.36. The label asserted a component the number
        # did not contain. Both the floor and the break-even now carry it.
        taxes = cand.extra_observed_taxes_usd
        result.best_points = cand
        result.funding_plan = plan
        result.points_path = _as_transfer_path(plan)
        result.points_required = sum(plan.per_currency_spend.values()) + plan.existing_target_points_used
        result.surcharge = surcharge
        result.observed_taxes_usd = taxes
        result.points_total_score_usd = float("inf")
        result.points_score_low_usd = float("inf")
        result.points_score_high_usd = float("inf")
        result.reasons.extend(plan.reasons)
        if result.cash_total_score_usd != float("inf"):
            result.break_even_surcharge_usd = max(
                result.cash_total_score_usd - plan.score_usd - fees_on_points - taxes,
                0.0,
            )
            # A surcharge can only ever ADD to the points side. So if the points
            # option already loses with the surcharge at its floor of $0, no
            # value the unknown could take would rescue it - and the verdict is
            # CERTAIN cash, not blocked. Only a break-even above zero means the
            # missing number actually decides the answer.
            result.points_floor_usd = plan.score_usd + fees_on_points + taxes
            result.points_floor_candidate = cand
            if result.points_floor_usd >= result.cash_total_score_usd:
                result.surcharge_cannot_change_verdict = True
        if _candidate_taxes_unknown(cand):
            result.add_reason(
                "TAXES_UNKNOWN",
                surcharge.notes
                or "The taxes on this award are unknown. This is NOT zero.",
            )
        else:
            result.add_reason(
                "SURCHARGE_UNKNOWN",
                surcharge.notes
                or "No surcharge rule matched and none was captured. This is NOT zero.",
            )
        # Dropped when the itinerary lookup named the metal: "the tool never
        # guesses metal" beside "operating airline: VS by flight number" would
        # contradict the line above it. The surcharge stays unresolved either
        # way unless the source's YQ inclusion is verified - that is
        # SURCHARGE_UNKNOWN's job, not this reason's.
        metal = getattr(cand, "metal", None)
        if not cand.has_known_metal and not (metal is not None and metal.is_known):
            result.add_reason(
                "CARRIER_UNKNOWN",
                f"Operating carrier for {cand.label!r} is "
                f"{cand.operating_carrier or '(absent)'} / source="
                f"{cand.carrier_source}. The tool never guesses metal.",
            )
        _record_candidate_warnings(result, cand)
        _taxes = _candidate_taxes_unknown(cand)
        result.warnings.append(
            f"{cand.label}: the "
            + ("TAXES on this award are" if _taxes else "carrier-imposed surcharge is")
            + " UNKNOWN, so this points option CANNOT be scored against cash. It is "
            "NOT $0. "
            + (
                f"Points beat cash only if "
                f"{'its taxes plus any surcharge come to less than' if _taxes else 'the surcharge is below'} "
                f"${result.break_even_surcharge_usd:,.2f}."
                if result.break_even_surcharge_usd is not None
                else ""
            )
        )
    else:
        result.points_total_score_usd = float("inf")
        result.points_score_low_usd = float("inf")
        result.points_score_high_usd = float("inf")

    # ---- Reachable partners with no captured award price --------------------
    # We do NOT invent an award price. Instead we report the break-even: the
    # award price at which points would exactly match the cash option.
    reachable_unpriced = [
        p
        for p in leg.unpriced_partner_programs
        if any(
            ratios_manager.is_partner(
                currency, ratios_manager.normalize_program(p), transfer_date
            )
            for currency in wallet.currencies
        )
    ]
    party_reachable_unpriced = list(reachable_unpriced) if party_leg else []
    if party_leg and reachable_unpriced:
        # A break-even in points is a ONE-SEAT price; quoting it against the
        # party's cash is the false win this leg is guarded against.
        result.warnings.append(
            f"{', '.join(reachable_unpriced)} IS a {source_program} partner for "
            f"this leg, but no break-even is quoted: the leg is for "
            f"{leg.travelers} travellers and award prices are per seat."
        )
        reachable_unpriced = []
    if reachable_unpriced and result.cash_total_score_usd != float("inf"):
        result.break_even_programs = [
            ratios_manager.normalize_program(p) for p in reachable_unpriced
        ]
        result.break_even_points = cash_to_points_equivalent(
            result.cash_usd, valuation_cpp
        )
        result.warnings.append(
            "No award price was captured for "
            + ", ".join(result.break_even_programs)
            + f". Break-even is {result.break_even_points:,} points: below that, "
            f"points beat cash; above it, pay cash. NOT scored - input missing."
        )

    # ---- Verdict ------------------------------------------------------------
    if not result.has_points_path:
        if result.surcharge is not None and not result.surcharge.is_known and (
            result.surcharge_cannot_change_verdict
        ):
            # The unknown is real but INERT: points lose even at a $0 surcharge,
            # and a surcharge can only add. Reporting this as "blocked" would
            # withhold an answer we actually have.
            result.verdict = "cash"
            result.verdict_reason = (
                f"Cash is cheaper: ${result.cash_total_score_usd:,.2f} vs at least "
                f"${result.points_floor_usd:,.2f} on points at "
                f"{valuation_cpp * 100:.1f}cpp. "
                + (
                    "The TAXES on this award are UNKNOWN"
                    if _candidate_taxes_unknown(result.best_points)
                    else "The carrier-imposed surcharge is UNKNOWN"
                )
                + ", but it can only ADD to the points side, so cash wins "
                "whatever it turns out to be. Do NOT burn points here."
            )
            result.margin_usd = result.points_floor_usd - result.cash_total_score_usd
            result.margin_pct = (
                result.margin_usd / result.cash_total_score_usd * 100
                if result.cash_total_score_usd not in (0, float("inf"))
                else 0.0
            )
        elif result.surcharge is not None and not result.surcharge.is_known:
            # The FIFTH pay-cash sub-state, new in v1. A points path exists and
            # is fundable; what is missing is the cash that comes with it.
            result.verdict = "cash (surcharge unknown)"
            # WHICH cash figure is missing matters, and saying "the carrier
            # surcharge" when the real gap is an unpriceable TAX would repeat in
            # prose the exact conflation finding C-2 was about in code.
            cand = result.best_points
            if getattr(cand, "taxes_unconvertible", False):
                missing = (
                    f"Seats.aero's own tax figure for it - "
                    f"{cand.observed_taxes_currency or 'an unnamed currency'} "
                    f"{cand.observed_taxes_amount:,.2f} - CANNOT be converted to "
                    f"USD, so the cash that comes with this award is UNKNOWN"
                )
                what = "that unpriced cash"
            elif getattr(cand, "taxes_unknown", False):
                missing = (
                    "the TAXES on it are UNKNOWN (Seats.aero sent no usable tax "
                    "figure for this award), so the cash that comes with it is "
                    "UNKNOWN"
                )
                what = "its taxes plus any carrier surcharge"
            else:
                missing = (
                    "the carrier-imposed surcharge on this metal is UNKNOWN"
                )
                what = "the surcharge"
            result.verdict_reason = (
                f"A points path exists ({result.best_points.program}, "
                f"{result.points_required:,} points) but {missing}, so it cannot "
                f"be scored against ${result.cash_total_score_usd:,.2f} cash. It is "
                # The CURRENT FACT belongs in the output; why the tool once got
                # it wrong belongs in the report. Tsuki is reading a booking
                # recommendation, not this project's commit history.
                f"NOT $0, and it must not be treated as $0. "
                + (
                    f"Points win only if {what} is below "
                    f"${result.break_even_surcharge_usd:,.2f}."
                    if result.break_even_surcharge_usd is not None
                    else "No cash price was captured either, so nothing can be compared."
                )
            )
            result.margin_usd = 0.0
            result.margin_pct = 0.0
        elif party_leg and (party_candidates or party_reachable_unpriced):
            _party_verdict(result, leg, party_candidates)
        elif result.break_even_programs:
            # A partner DOES exist - we just have no award price for it. That is
            # a missing input, not an absent path, and the two must not be
            # conflated in the report.
            result.verdict = "cash (points unpriced)"
            result.verdict_reason = (
                f"{', '.join(result.break_even_programs)} IS a {source_program} "
                f"partner, but no award price was captured, so the points option "
                f"cannot be scored. Points would win below "
                f"{result.break_even_points:,} points."
            )
        elif blocked_partner_candidates:
            result.verdict = "cash (points blocked)"
            result.verdict_reason = (
                f"A {source_program} partner exists for this leg "
                f"({'; '.join(blocked_partner_candidates)}), but every transfer "
                f"path was blocked by the balance ceiling or the stranded-points "
                f"constraint. This is a constraint, not a missing partner."
            )
        elif indirect_candidates:
            best_indirect = min(indirect_candidates, key=lambda c: c.points)
            result.verdict = VERDICT_INDIRECT_PATH
            result.verdict_reason = (
                f"Pay cash BY DEFAULT, not by finding. Seats.aero returned "
                f"{len(indirect_candidates)} award(s) for this leg in "
                f"{', '.join(sorted({c.program for c in indirect_candidates}))}, "
                f"which your {source_program} balance reaches only INDIRECTLY "
                f"(cheapest: {best_indirect.points:,} points). "
                f"{best_indirect.indirect_ur_path} Whether that path is open to "
                f"you depends on your own accounts, so it is NOT scored - and it "
                f"is NOT a finding that no points path exists."
            )
        elif unattributed_candidates:
            # FINDING M-5's verdict half. Saying "No UR transfer partner covers
            # this leg" here would be a claim about Chase's partner list built
            # out of a field the response did not send. The tool knows nothing
            # about partnerships on this leg, and says exactly that.
            result.verdict = VERDICT_AWARD_UNATTRIBUTED
            result.verdict_reason = (
                f"Pay cash BY DEFAULT, not by finding. Seats.aero returned "
                f"{len(unattributed_candidates)} real award(s) for this leg and "
                f"NAMED NO PROGRAM for them (Route.Source was absent, or was a "
                f"source code this tool does not map), so they cannot be "
                f"attributed, cannot be checked against your transfer partners and "
                f"cannot be scored. NOTHING is being claimed about whether a "
                f"{source_program} partner covers this leg - that question was "
                f"never reached."
            )
        elif leg.kind == "flight" and not leg.points_candidates:
            # FINDING H-4. ABSENCE OF A CAPTURED PRICE IS NOT A FACT ABOUT
            # AIRLINE PARTNERSHIPS - way (9) wearing another hat, on the verdict
            # instead of on a storage layer.
            #
            # A --new-trip fixture carries NO points_candidates BY DESIGN and
            # --offline is a documented mode for it, so SFO->MAD reported "No UR
            # transfer partner covers this leg / (no partner exists)" while Trip
            # B's own B1 scored an Air Canada Aeroplan path on that exact route
            # in the same binary. `annotate_live_verdicts` was written to stop
            # this text being used when the cause is a data gap and it runs on
            # the LIVE path only; the honest answer belongs here, where the
            # verdict is decided, on every path.
            result.verdict = "cash (no points path)"
            result.points_absence = "never_priced"
            result.verdict_reason = (
                f"NO AWARD PRICE WAS CAPTURED FOR THIS LEG and none was fetched, "
                f"so the points side was never priced. NOTHING is claimed about "
                f"whether a {source_program} transfer partner covers "
                f"{leg.origin or 'this leg'}->{leg.destination or ''} - that "
                f"question was never reached. Score it with --live or "
                f"--from-snapshot, or add a captured award price to the fixture."
            ).strip()
        else:
            result.verdict = "cash (no points path)"
            result.points_absence = "no_partner"
            result.verdict_reason = (
                f"No {source_program} transfer partner covers this leg. "
                f"Cash is the only option."
            )
        result.margin_usd = 0.0
        result.margin_pct = 0.0
    elif (
        result.points_total_score_usd < result.cash_total_score_usd
        and result.points_total_score_usd < result.cash_baseline_usd
    ):
        # H-5's safeguard. The first test is the honest same-date head-to-head.
        # The second stops a DATE-SHIFTED "win" that is worse than simply flying
        # on the planned date and paying cash: a Jan-17 award beating the Jan-17
        # fare is not a reason to burn points if the Jan-15 fare is cheaper than
        # both. On an on-date leg the two comparisons are identical.
        result.verdict = "points"
        result.margin_usd = result.cash_total_score_usd - result.points_total_score_usd
        result.margin_pct = (
            result.margin_usd / result.cash_total_score_usd * 100
            if result.cash_total_score_usd not in (0, float("inf"))
            else 0.0
        )
        result.verdict_reason = (
            f"Points path scores ${result.points_total_score_usd:,.2f} vs "
            f"${result.cash_total_score_usd:,.2f} cash."
        )
    else:
        result.verdict = "cash"
        beaten_by = min(result.cash_total_score_usd, result.cash_baseline_usd)
        result.margin_usd = result.points_total_score_usd - beaten_by
        result.margin_pct = (
            result.margin_usd / beaten_by * 100
            if beaten_by not in (0, float("inf"))
            else 0.0
        )
        if (
            result.scored_off_date
            and result.points_total_score_usd < result.cash_total_score_usd
        ):
            result.verdict_reason = (
                f"Pay cash, and fly on the leg's own date. The award is for "
                f"{result.scoring_date} and it does beat that date's fare "
                f"(${result.points_total_score_usd:,.2f} vs "
                f"${result.cash_total_score_usd:,.2f}) - but flying on {leg.date} as "
                f"planned and paying cash costs only "
                f"${result.cash_baseline_usd:,.2f}, which beats both. Shifting the "
                f"date to reach award space is not worth it here."
            )
        else:
            result.verdict_reason = (
                f"Cash is cheaper: ${result.cash_total_score_usd:,.2f} vs "
                f"${result.points_total_score_usd:,.2f} on points at "
                f"{valuation_cpp * 100:.1f}cpp. Do NOT burn points here."
            )

    # ---- Verdict sensitivity -----------------------------------------------
    # A surcharge estimate is a RANGE. If the verdict differs between the low and
    # the high end, the recommendation is not settled and the tool says so rather
    # than picking the midpoint and sounding confident.
    if (
        result.has_points_path
        and result.surcharge is not None
        and result.surcharge.is_range
        and result.cash_total_score_usd != float("inf")
    ):
        wins_at_low = result.points_score_low_usd < result.cash_total_score_usd
        wins_at_high = result.points_score_high_usd < result.cash_total_score_usd
        if wins_at_low != wins_at_high:
            result.verdict_sensitive = True
            result.add_reason(
                "VERDICT_SENSITIVE",
                f"The verdict FLIPS inside the surcharge range: points score "
                f"${result.points_score_low_usd:,.2f} at the low end and "
                f"${result.points_score_high_usd:,.2f} at the high end, against "
                f"${result.cash_total_score_usd:,.2f} cash. This recommendation is "
                f"NOT settled - capture the real surcharge before booking.",
                low=result.points_score_low_usd,
                high=result.points_score_high_usd,
                cash=result.cash_total_score_usd,
            )
            result.warnings.append(
                "VERDICT SENSITIVE: the recommendation reverses inside the "
                "surcharge estimate's own range. Do not treat it as settled."
            )

    # ---- Alternatives -------------------------------------------------------
    if show_alternatives and leg.kind == "flight" and result.best_points is not None:
        region, country, _ = _leg_region_and_country(leg)
        if region is not None:
            result.alternatives = alternatives_mod.find_same_metal_alternatives(
                winning_candidate=result.best_points,
                winning_surcharge=result.surcharge or SurchargeEstimate.unknown(),
                region=region,
                departure_country=country,
                ratios_manager=ratios_manager,
                surcharges=surcharges,
                wallet_currencies=wallet.currencies,
                transfer_date=transfer_date,
                cash_usd=result.cash_total_score_usd,
                valuation_cpp=valuation_cpp,
            )
            for alt in result.alternatives:
                result.add_reason(
                    "ALTERNATIVE_UNPRICED",
                    alt.note,
                    program=alt.program,
                    carrier=alt.operating_carrier,
                    break_even_points=alt.break_even_points,
                )
            note = alternatives_mod.cross_metal_note(
                leg.points_candidates, result.best_points, region, country, surcharges
            )
            if note:
                result.warnings.append(note)

    # ---- The operating-airline lookup: counted, never scored --------------
    # For the chosen award only. A lookup that could not change the answer
    # (a program-wide $0 surcharge, not a direct partner) is not a gap and is
    # not counted. Literal codes, one per branch: way (10) discovers reason
    # codes by parsing `add_reason("CODE", ...)`.
    metal = getattr(result.best_points, "metal", None) if result.best_points else None
    if metal is not None and metal.is_missing_lookup:
        result.add_reason(
            "METAL_LOOKUP_MISSING",
            f"The operating airline of {result.best_points.label!r} was "
            f"{metal.status.value.replace('_', ' ').upper()} "
            f"({metal.reason_code}). Nothing is known about which airline flies "
            f"it; it is NOT known metal.",
            status=metal.status.value,
            reason=metal.reason_code,
        )
    elif metal is not None and metal.is_unresolved:
        result.add_reason(
            "METAL_UNKNOWN",
            f"The operating-airline lookup for {result.best_points.label!r} did "
            f"not settle one carrier set ({metal.status.value}"
            f"{', ' + metal.reason_code if metal.reason_code else ''}).",
            status=metal.status.value,
            reason=metal.reason_code,
        )

    return result


def _as_transfer_path(plan: FundingPlan) -> TransferPath:
    """
    Adapt a FundingPlan to the v0 TransferPath shape for display and back-compat.

    One-way only, and display only: TransferPath.total_points_cost is not a
    ranking key any more (see the note on that property).
    """
    path = TransferPath()
    path.existing_target_points_used = plan.existing_target_points_used
    path.stranded_points = plan.stranded_points
    path.ratio_remainder_points = sum(plan.ratio_remainder_points.values())
    path.feasible = plan.feasible
    path.infeasible_reason = plan.infeasible_reason
    for transfer in plan.transfers:
        path.add_transfer(transfer)
    return path


def _record_candidate_warnings(result: LegResult, cand: PointsCandidate) -> None:
    """Provenance warnings that must accompany any candidate we present."""
    if cand.source != "seats_aero":
        result.warnings.append(
            f"Points price for {cand.label} is UNVERIFIED ({cand.source}). "
            f"{cand.source_note}".strip()
        )
    if cand.program_attribution_assumed:
        result.warnings.append(
            f"Program attribution for {cand.label} -> {cand.program} is an "
            f"ASSUMPTION; the source did not name the program."
        )
    if cand.source == MANUAL_CAPTURE_SOURCE or cand.points_per_night is not None:
        result.add_reason(
            "MANUAL_CAPTURE",
            hotels.HOTEL_DISCLAIMER,
            label=cand.label,
        )


MANUAL_CAPTURE_SOURCE = hotels.MANUAL_CAPTURE


def evaluate_trip(
    legs: List[Leg],
    user_balances: Optional[Dict[str, Optional[int]]] = None,
    user_cards: Optional[List[str]] = None,
    ratios_manager: RatioManager = None,
    source_program: str = "UR",
    valuation_cpp: float = CASH_VALUATION_CPP,
    transfer_increment: int = DEFAULT_TRANSFER_INCREMENT,
    max_stranded_points: Optional[int] = None,
    *,
    wallet: Optional[Wallet] = None,
    transfer_date: Optional[date] = None,
    surcharges: Optional[SurchargeTable] = None,
    show_alternatives: bool = True,
    today: Optional[date] = None,
    enforce_trip_balance: bool = True,
) -> List[LegResult]:
    """
    Evaluate every leg of a trip AGAINST ONE SHARED, DEPLETING BALANCE.

    FINDING C-3, AND IT IS THE ONE THAT WOULD HAVE COST REAL POINTS.

    Until this fix every leg was funded independently from the FULL balance.
    Three legs at 70,000 United points each therefore "spent" 210,000 UR out of
    a 160,000 balance, and the headline printed a clean 22.22% for a plan the
    user cannot execute. The only place the overdraft appeared was the residue
    table, which main.py prints AFTER the headline, in a column called "Note".

    v1's own plan calls the balance ceiling "the most safety-critical rule in
    the tool". It was enforced WITHIN a leg - `best_plan` refuses a transfer
    larger than the balance, and `test_B4` proves it binds exactly at 160,000 -
    and not at all ACROSS legs, because nothing owned the trip-level view.

    So this is now two passes:

      1. Score every leg independently, exactly as before. That answers "which
         legs WOULD be points wins if each were the only leg".
      2. Spend the wallet on them, best value first, RE-EVALUATING each leg
         against what is actually left. A leg the remaining balance cannot fund
         falls back to cash through the ordinary machinery.

    Pass 2 re-runs `evaluate_leg` rather than second-guessing it, so a leg that
    can no longer afford UR but could use MR is re-planned honestly instead of
    being demoted on a subtraction this function did itself.

    ORDERING IS GREEDY BY SAVINGS PER POINT and is NOT proven optimal - the
    exact problem is a multi-dimensional knapsack. Greedy-by-efficiency is the
    standard approximation and it is defensible; what matters far more is that
    the answer is EXECUTABLE, which no ordering can be if the constraint is
    absent. A demoted leg says why.

    `enforce_trip_balance=False` restores the old per-leg independence. It
    exists for tests that want to see the unconstrained plan; it is never the
    default, because the unconstrained plan is not a recommendation.
    """
    def _score_leg(leg: Leg, w: Wallet) -> LegResult:
        return evaluate_leg(
            leg,
            user_balances,
            user_cards,
            ratios_manager,
            source_program=source_program,
            valuation_cpp=valuation_cpp,
            transfer_increment=transfer_increment,
            max_stranded_points=max_stranded_points,
            wallet=w,
            transfer_date=transfer_date,
            surcharges=surcharges,
            show_alternatives=show_alternatives,
            today=today,
        )

    full_wallet = _wallet_from(wallet, user_balances, user_cards)
    results = [_score_leg(leg, full_wallet) for leg in legs]

    if not enforce_trip_balance:
        return apply_apd(results, today=today, valuation_cpp=valuation_cpp)
    return apply_apd(
        _apply_trip_balance_ceiling(legs, results, full_wallet, _score_leg),
        today=today,
        valuation_cpp=valuation_cpp,
    )


# ---------------------------------------------------------------------------
# v5 Step 7: UK Air Passenger Duty. THE ONLY PLACE THIS MODULE TOUCHES IT.
# ---------------------------------------------------------------------------


def _apd_cabin(result: LegResult) -> Tuple[str, str]:
    """
    Which cabin this leg is taxed in, and a warning when the inputs disagree.

    FINDING H-2. This used to be `result.best_points.cabin`, else the first
    candidate's, else the literal `"Y"` - the REDUCED rate. A `--new-trip`
    fixture carries no points candidates BY DESIGN, so every one of them fell to
    the default and a `--cabin J` LHR->SFO leg was charged GBP 102 "per the
    reduced rate" instead of GBP 244. The builder had been writing a leg-level
    `cabin` key since v5 Step 4 and nothing read it.

    THE ORDER, AND WHY. The LEG's own cabin wins: it is what the fixture says
    the traveller is flying, and it exists whether or not anybody has priced an
    award. A scored candidate's cabin is used when the leg does not say. When
    both exist and DISAGREE the leg still wins and the disagreement is REPORTED
    rather than resolved silently - HMRC's two rates differ by more than GBP 140
    and a silent choice between them is a GBP 140 guess.

    Returns ("", note) when nothing records a cabin. Empty is UNKNOWN, and
    `apd_for_leg` turns it into a written-down unknown rather than a rate.
    """
    leg_cabin = (getattr(result.leg, "cabin", "") or "").strip().upper()
    candidate = result.best_points or (
        result.leg.points_candidates[0] if result.leg.points_candidates else None
    )
    candidate_cabin = (getattr(candidate, "cabin", "") or "").strip().upper()

    if leg_cabin and candidate_cabin and leg_cabin != candidate_cabin:
        return leg_cabin, (
            f"CABIN DISAGREEMENT on {result.leg.id}: the leg says {leg_cabin} and "
            f"the award being scored ({getattr(candidate, 'label', 'the candidate')}) "
            f"says {candidate_cabin}. UK APD is charged on the leg's cabin here "
            f"({leg_cabin}); if the award is really flown in {candidate_cabin} the "
            f"duty differs. Fix the fixture rather than trusting this line."
        )
    return (leg_cabin or candidate_cabin), ""


def _apd_inclusion_unverified(result: LegResult) -> Tuple[bool, str]:
    """
    May this leg's points-side cash ALREADY contain APD? (flag, what it is).

    THE RULE, STATED ONCE. APD is ADDED only when this tool MODELLED the whole
    points-side cash figure itself. The moment any part of that figure was
    CAPTURED from somebody else's page, we do not know what is inside it, and
    adding a tax that may already be in there is a double charge.

    FINDING H-3. The only test used to be `points_provenance in (LIVE,
    SNAPSHOT)`, and `surcharge_captured` was never read - so a candidate
    carrying a captured "taxes, fees and carrier charges" figure for a UK
    departure (which necessarily contains APD, for exactly the reason
    TotalTaxes might) was charged it a second time, on the offline path the plan
    called safe. The asymmetry rests on "the tool owns the whole cash figure and
    knows APD is missing from it", which is true of a MODELLED surcharge and
    false of a CAPTURED one.

    Where else this shape appears: a captured MANDATORY FEE payable on the
    points side is the same thing wearing a different field name, so it is
    covered here too rather than waiting to be found separately.
    """
    from src.models import PointsProvenance

    leg = result.leg
    candidate = result.best_points
    # WHEN THE LIVE TAX FIGURE WAS NOT USABLE, THERE IS NO TOTALTAXES FOR APD TO
    # HIDE IN. The live rule below exists because Seats.aero's TotalTaxes may
    # already contain the duty. If the chosen award's taxes are UNKNOWN - none
    # sent, a source that does not report them, a 0 meaning "not reported" -
    # nothing counted on the points side came from TotalTaxes, so nothing counted
    # can contain APD. Declining to add it then leaves a duty that is certainly
    # owed at $0 in the floor, which is the triage's "tax=0 on an LHR departure"
    # bug one step later. Falls through to the captured-surcharge and
    # captured-fee checks, which still apply.
    # NOT the unconvertible case: there a TotalTaxes figure EXISTS in a currency
    # we cannot price, and it may well contain the duty - so the live rule holds.
    live_taxes_unusable = _taxes_unusable_not_unconvertible(candidate)
    if (
        leg.points_provenance in (PointsProvenance.LIVE, PointsProvenance.SNAPSHOT)
        and not live_taxes_unusable
    ):
        return True, (
            "this leg's points-side cash came from Seats.aero's TotalTaxes, "
            "captured on this run or replayed from a snapshot"
        )
    if candidate is not None and getattr(candidate, "surcharge_captured", False):
        return True, (
            f"this leg's points-side cash includes a CAPTURED surcharge of "
            f"{getattr(candidate, 'surcharge_currency', 'USD')} "
            f"{float(getattr(candidate, 'cash_surcharge', 0.0)):,.2f} taken from a "
            f"booking page ({candidate.label!r}), not modelled by this tool"
        )
    captured_fees = [
        fee
        for fee in leg.mandatory_fees
        if fee.payable_on_points and "captur" in str(fee.source or "").lower()
    ]
    if captured_fees:
        return True, (
            f"this leg's points-side cash includes CAPTURED mandatory fee(s) "
            f"({', '.join(f.label for f in captured_fees)}) taken from a booking "
            f"page, not modelled by this tool"
        )
    return False, ""


VERDICT_APD_UNKNOWN = "cash (APD unknown)"


def _withhold_points_side_for_unknown_apd(
    result: LegResult, charge, valuation_cpp: float
) -> None:
    """
    WAY (10), THE APD HALF. An OWED-BUT-UNKNOWN duty is not a $0 duty.

    An unknown carrier surcharge already resolves this way: the points side
    becomes UNSCOREABLE (+inf), so the trip's pessimistic end takes CASH for
    this leg while `points_floor_usd` - the cheapest the points side could
    conceivably be, i.e. the duty at its floor of $0 - keeps the optimistic end
    honest. A government departure tax whose size nobody knows is exactly the
    same quantity of ignorance about exactly the same dollar, so it gets exactly
    the same treatment.

    WHAT WENT WRONG WITHOUT THIS. `apd_added_usd` was set to 0.0 and none of the
    four score fields were touched, so an unknown duty entered NEITHER end of
    the range whose whole job is to bracket unknowns. Setting B4's cabin to one
    the table cannot price moved the trip headline from $63.89 / 2.04%-11.03% to
    $202.00 / 6.46%-15.45%: knowing LESS made the recommendation look three
    times better, and returned the number to its pre-APD value. Fifteen of the
    63 rows in `data/apd_bands.csv` are band UNKNOWN, so this is reachable from
    an ordinary UK departure to Mexico, Brazil, Turkey, South Korea or South
    Africa - not from a contrived fixture.

    A leg with no points path is left alone: there is no points side to withhold
    and the APD line still prints, because the tax is still owed.
    """
    if not result.has_points_path:
        return
    if result.points_total_score_usd == float("inf"):
        return  # already unscoreable, for some other unknown. Nothing to add.

    # The floor is the points side BEFORE the duty - APD can only ever add, so
    # this is the least this leg could possibly cost on points.
    floor = min(result.points_total_score_usd, result.points_score_low_usd)
    result.points_floor_usd = (
        floor if result.points_floor_usd is None else min(result.points_floor_usd, floor)
    )
    result.points_total_score_usd = float("inf")
    result.points_score_low_usd = float("inf")
    result.points_score_high_usd = float("inf")
    result.apd_unknown_withheld = True

    cash = result.cash_total_score_usd
    if cash != float("inf") and result.points_floor_usd >= cash:
        # The unknown is real but INERT: cash already wins with the duty at $0,
        # and a duty can only add. Same reasoning as the unknown-surcharge case.
        result.verdict = "cash"
        result.margin_usd = result.points_floor_usd - cash
        result.margin_pct = (result.margin_usd / cash * 100) if cash else 0.0
        result.verdict_reason = (
            f"Cash is cheaper: ${cash:,.2f} vs at least "
            f"${result.points_floor_usd:,.2f} on points at "
            f"{valuation_cpp * 100:.1f}cpp. UK Air Passenger Duty IS owed on this "
            f"leg and its amount is UNKNOWN, but it can only ADD to the points "
            f"side, so cash wins whatever it turns out to be."
        )
        return

    result.verdict = VERDICT_APD_UNKNOWN
    result.margin_usd = 0.0
    result.margin_pct = 0.0
    result.verdict_reason = (
        f"UK AIR PASSENGER DUTY IS OWED on this leg and its amount is UNKNOWN, so "
        f"the points side CANNOT be scored against "
        f"${result.cash_total_score_usd:,.2f} cash. It is NOT $0. The points side "
        f"costs AT LEAST ${result.points_floor_usd:,.2f} - that figure is a LOWER "
        f"BOUND with the duty at zero, and points win only if the duty turns out "
        f"to be below "
        f"${max(result.cash_total_score_usd - result.points_floor_usd, 0.0):,.2f}. "
        f"{charge.unknown_reason}"
    ).strip()


def _party_verdict(result: LegResult, leg: Leg, party_candidates) -> None:
    result.verdict = VERDICT_PARTY_NOT_PRICED
    result.add_reason(
        "PARTY_PRICING_UNVERIFIED",
        f"{len(party_candidates)} fundable points option(s) on a flight leg for "
        f"{leg.travelers} travellers were not scored: award prices are per seat "
        f"and multi-traveller pricing is not modelled.",
        travelers=leg.travelers,
    )
    result.verdict_reason = (
        f"Pay cash BY DEFAULT, not by finding. This flight leg is for "
        f"{leg.travelers} travellers and every award price here is for ONE seat; "
        f"the tool does not yet multiply points, award taxes or the balance by "
        f"the party size, or check that {leg.travelers} seats are open. Scoring "
        f"one seat of points against the party's cash could print a points win "
        f"that does not exist, so the reachable options are not scored. Price "
        f"them by hand: {leg.travelers} x the points, {leg.travelers} x the "
        f"taxes, against the party's total cash."
    )


def _legs_with(results, code: str) -> List[str]:
    """Leg ids carrying at least one reason with this code."""
    return [r.leg.id for r in results if any(x.code == code for x in r.reasons)]


def _candidate_taxes_unknown(cand) -> bool:
    """A live candidate whose TAXES are unknown, for any reason (incl. unconvertible)."""
    return bool(
        cand is not None
        and (
            getattr(cand, "taxes_unknown", False)
            or getattr(cand, "taxes_unconvertible", False)
        )
    )


def _taxes_unusable_not_unconvertible(cand) -> bool:
    """
    No USABLE tax figure at all - so nothing counted can contain UK APD. Not the
    unconvertible case, where a figure exists and may well contain it.
    """
    return bool(
        cand is not None
        and getattr(cand, "taxes_unknown", False)
        and not getattr(cand, "taxes_unconvertible", False)
    )


def _rewrite_stale_break_even(result: LegResult, old_be: float, new_be: float, amount: float) -> None:
    """
    Replace a pre-APD break-even figure everywhere it was written - the verdict
    reason AND the warnings - so the leg never prints $182 beside $43.89.
    """
    for stale in (f"below ${old_be:,.2f}.", f"less than ${old_be:,.2f}."):
        fresh = stale.replace(
            f"${old_be:,.2f}.",
            f"${new_be:,.2f} - after UK Air Passenger Duty of ${amount:,.2f}, which "
            f"is owed on top of it.",
        )
        if stale in (result.verdict_reason or ""):
            result.verdict_reason = result.verdict_reason.replace(stale, fresh)
        result.warnings = [w.replace(stale, fresh) for w in result.warnings]


def _add_apd_to_unscored_floor(
    result: LegResult, amount: float, valuation_cpp: float
) -> None:
    """
    Move an UNSCOREABLE points side's floor and break-even by a known, owed APD.

    Only reached when APD is being ADDED (known amount, not possibly inside a
    captured figure). Recomputes the head-to-head from the moved floor rather
    than leaving a stale break-even in the reason text: a leg whose floor has
    grown by the duty and whose sentence still quotes the pre-duty figure is
    worse than not moving it.
    """
    floor = result.points_floor_usd
    if floor is None or floor == float("inf"):
        return
    cash = result.cash_total_score_usd
    old_be = result.break_even_surcharge_usd
    result.points_floor_usd = floor + amount
    if old_be is not None:
        new_be = max(old_be - amount, 0.0)
        result.break_even_surcharge_usd = new_be
        _rewrite_stale_break_even(result, old_be, new_be, amount)
    if cash == float("inf") or result.points_floor_usd < cash:
        return
    # The duty alone makes points lose: the unknown is now INERT, exactly as in
    # the unknown-surcharge case where the floor already loses. Also reached when
    # the leg was ALREADY a certain "cash" before the duty: its sentence quoted
    # the pre-duty floor and must be rewritten, not left beside the new one.
    result.surcharge_cannot_change_verdict = True
    if result.verdict in ("cash (surcharge unknown)", "cash"):
        result.verdict = "cash"
        result.margin_usd = result.points_floor_usd - cash
        result.margin_pct = (result.margin_usd / cash * 100) if cash else 0.0
        what = (
            "the award's other taxes are UNKNOWN"
            if _candidate_taxes_unknown(result.best_points)
            else "the carrier-imposed surcharge is UNKNOWN"
        )
        result.verdict_reason = (
            f"Cash is cheaper: ${cash:,.2f} vs at least "
            f"${result.points_floor_usd:,.2f} on points at "
            f"{valuation_cpp * 100:.1f}cpp, INCLUDING UK Air Passenger Duty of "
            f"${amount:,.2f} that is owed on the award ticket too. Beyond that, "
            f"{what}, but that can only ADD, so cash wins whatever it turns out "
            f"to be. Do NOT burn points here."
        )


def apply_apd(
    results: List[LegResult],
    today: Optional[date] = None,
    valuation_cpp: float = config.DEFAULT_VALUATION_CPP,
):
    """
    One additive term on the offline points-side total, AFTER evaluate_leg ran.

    OFF-LIMITS COMPLIANCE. `evaluate_leg`'s own logic, the Seats.aero parser and
    `SurchargeTable.resolve*` are not read, not called differently, and not
    modified by this. APD never enters the surcharge table, never becomes a
    surcharge row, and never participates in the captured-beats-table
    precedence: it is a GOVERNMENT DEPARTURE TAX and a carrier YQ is a different
    quantity with a different payer. Merging them is how "United charges no YQ"
    becomes "this leg costs nothing in cash".

    THE ASYMMETRY IS THE DESIGN, not a gap:

      * OFFLINE / BADGE leg - the tool owns the whole cash figure and knows APD
        is missing from it, so it is ADDED.
      * LIVE / SNAPSHOT leg - the cash figure came from Seats.aero's TotalTaxes
        and nobody has checked whether that already includes APD. The amount is
        STATED and NOT ADDED. Adding it would risk double-charging.

    APD IS ADDED TO THE POINTS SIDE ONLY. A captured cash fare is a published
    fare and already contains APD; adding it to both sides would cancel out of
    the margin and hide the entire effect.

    ONE APPROXIMATION, STATED. This runs AFTER the greedy trip-balance ordering,
    which ranks legs by savings per point. Those savings are therefore ranked
    pre-APD. The ordering was never proven optimal (see `evaluate_trip`), and
    re-running the ceiling with APD folded in would mean APD changing WHICH legs
    are recommended rather than only what they cost - a much larger change than
    this step is scoped for.
    """
    from src import apd as apd_module
    from src.models import PointsProvenance

    try:
        rates = apd_module.load_apd_table()
        bands = apd_module.load_apd_bands()
    except apd_module.APDTableError:
        # A missing or malformed table must not silently score every UK
        # departure at zero. It is loud at load time elsewhere; here the safe
        # behaviour is to leave every leg untouched rather than to add nothing
        # while claiming APD was considered.
        return results

    for result in results:
        leg = result.leg
        cabin, cabin_conflict = _apd_cabin(result)
        if cabin_conflict:
            result.warnings.append(cabin_conflict)
        flagged, inclusion_source = _apd_inclusion_unverified(result)
        charge = apd_module.apd_for_leg(
            leg,
            cabin=cabin,
            travelers=leg.travelers,
            on=leg.date,
            rates=rates,
            bands=bands,
            inclusion_unverified=flagged,
            inclusion_source=inclusion_source,
            today=today,
        )
        if charge is None:
            # NOT a UK departure. Nothing is set - not a zero, not a field.
            continue

        result.apd = charge
        result.warnings.append(charge.render())

        if not charge.is_known:
            result.apd_added_usd = 0.0
            result.add_reason(
                "APD_UNKNOWN",
                charge.render(),
                destination_country=charge.destination_country,
            )
            _withhold_points_side_for_unknown_apd(result, charge, valuation_cpp)
            continue
        if charge.inclusion_unverified:
            result.apd_added_usd = 0.0
            result.add_reason(
                "APD_INCLUSION_UNVERIFIED",
                charge.render(),
                gbp=charge.total_gbp,
                usd=charge.total_usd,
            )
            # THE CHOSEN award's taxes may contain the duty; a REJECTED one's may
            # not. When the leg's floor was set by a rejected alternative with NO
            # usable tax figure, nothing counted in THAT floor can contain APD -
            # and that floor is the leg's optimistic end. The duty is owed on its
            # ticket as surely as on the winner's, so it goes into the floor.
            floor_cand = result.points_floor_candidate
            if (
                floor_cand is not None
                and floor_cand is not result.best_points
                and _taxes_unusable_not_unconvertible(floor_cand)
                and result.points_floor_usd is not None
            ):
                result.points_floor_usd += float(charge.total_usd)
                result.warnings.append(
                    f"{floor_cand.label}: its floor now includes UK Air Passenger "
                    f"Duty of ${float(charge.total_usd):,.2f}, owed on that ticket "
                    f"too - its own taxes are unknown, so nothing counted in it "
                    f"could already contain the duty."
                )
            continue

        amount = float(charge.total_usd)
        result.apd_added_usd = amount
        result.add_reason(
            "APD_ADDED", charge.render(), gbp=charge.total_gbp, usd=amount
        )

        if not result.has_points_path:
            # No SCORED points side to add it to. The line still prints, because
            # the tax is still owed and a reader comparing this leg against a
            # points option elsewhere needs to see it.
            #
            # BUT AN UNSCOREABLE POINTS SIDE STILL HAS A FLOOR, and the floor is
            # "the least this can possibly cost". A duty that is known, owed and
            # counted nowhere else belongs in it. Skipping it left the optimistic
            # end of the trip range - and the printed break-even - assuming the
            # duty was $0 on exactly the legs whose cash side is least known: a
            # live UK departure whose taxes Seats.aero did not report printed
            # "points win if surcharge < $182" when GBP 102 of that was owed
            # before any surcharge at all.
            _add_apd_to_unscored_floor(result, amount, valuation_cpp)
            continue

        for attr in (
            "points_total_score_usd",
            "points_score_low_usd",
            "points_score_high_usd",
            "points_floor_usd",
        ):
            value = getattr(result, attr)
            if value is not None and value != float("inf"):
                setattr(result, attr, value + amount)

        # The head-to-head numbers are recomputed from the moved score rather
        # than left stale. A leg whose points side has grown by GBP 102 and
        # whose printed margin still says otherwise is worse than not applying
        # APD at all.
        if result.points_total_score_usd != float("inf"):
            result.margin_usd = (
                result.cash_total_score_usd - result.points_total_score_usd
            )
            result.margin_pct = (
                (result.margin_usd / result.cash_total_score_usd * 100)
                if result.cash_total_score_usd
                else 0.0
            )
            if (
                result.verdict == "points"
                and result.points_total_score_usd > result.cash_total_score_usd
            ):
                result.verdict = "cash"
                result.verdict_reason = (
                    f"Pay cash. The points side WON before UK Air Passenger Duty "
                    f"and LOSES after it: APD adds ${amount:,.2f} to the "
                    f"points-side cash total (${result.points_total_score_usd:,.2f}) "
                    f"and the captured cash fare "
                    f"(${result.cash_total_score_usd:,.2f}) already contains its "
                    f"own APD. {result.verdict_reason}"
                ).strip()
    return results


def leg_points_demand(result: LegResult, wallet: Wallet) -> Dict[str, int]:
    """
    What this leg's recommendation actually removes from the wallet.

    Only a leg the optimizer recommends paying with POINTS spends anything. A
    leg where cash wins costs no points, so its funding plan must not be counted
    - counting it would overstate every trip.
    """
    plan = result.funding_plan
    if result.verdict != "points" or plan is None:
        return {}
    demand = {c: n for c, n in plan.per_currency_spend.items() if n}
    if plan.existing_target_points_used and wallet.holds(plan.target_program):
        demand[plan.target_program] = (
            demand.get(plan.target_program, 0) + plan.existing_target_points_used
        )
    return demand


def _apply_trip_balance_ceiling(legs, results, wallet, score_leg):
    """
    Spend one wallet across the whole trip. See `evaluate_trip` for the argument.

    Returns the results in the ORIGINAL leg order, with any leg the balance
    could not reach re-scored as cash and marked `demoted_for_trip_balance`.
    """
    wanted = [
        (i, r) for i, r in enumerate(results) if leg_points_demand(r, wallet)
    ]
    if not wanted:
        return results

    # Does the unconstrained plan already fit? If so nothing is re-scored, so an
    # ordinary trip is bit-for-bit unchanged by this pass.
    total: Dict[str, int] = {}
    for _, r in wanted:
        for currency, points in leg_points_demand(r, wallet).items():
            total[currency] = total.get(currency, 0) + points
    over = {
        c: n
        for c, n in total.items()
        if wallet.holds(c)
        and wallet.balance_of(c) is not None
        and n > wallet.balance_of(c)
    }
    if not over:
        return results

    # Greedy by savings per point spent: the legs that turn the fewest points
    # into the most dollars go first.
    def _efficiency(item) -> float:
        _, r = item
        spend = sum(leg_points_demand(r, wallet).values())
        if spend <= 0:
            return float("inf")
        return (r.cash_baseline_usd - r.points_total_score_usd) / spend

    order = sorted(wanted, key=_efficiency, reverse=True)

    remaining = dict(wallet.balances)
    out = list(results)
    funded: List[str] = []
    demoted: List[str] = []

    for index, original in order:
        trimmed = Wallet(
            balances=dict(remaining),
            cards=list(wallet.cards),
            valuation_cpp=dict(wallet.valuation_cpp),
            source=wallet.source,
        )
        rescored = score_leg(legs[index], trimmed)
        demand = leg_points_demand(rescored, wallet)
        if demand:
            for currency, points in demand.items():
                if remaining.get(currency) is not None:
                    remaining[currency] = remaining[currency] - points
            funded.append(legs[index].id)
            out[index] = rescored
            continue

        # The leg wanted points and the trip cannot afford them. That is a
        # TRIP-LEVEL constraint and it is stated as one: the per-leg machinery
        # would otherwise blame "the balance ceiling or the stranded-points
        # constraint" without saying that earlier legs are what spent it.
        rescored.demoted_for_trip_balance = True
        left = ", ".join(
            f"{c} {v:,}" for c, v in sorted(remaining.items()) if v is not None
        ) or "nothing measurable"
        rescored.verdict_reason = (
            f"PAY CASH - THE TRIP RAN OUT OF POINTS. On its own this leg would "
            f"have been funded with "
            f"{original.funding_plan.spend_summary() if original.funding_plan else 'points'}"
            f", but the same balance also funds "
            f"{', '.join(funded) if funded else 'other legs'}, and after those "
            f"there is only {left} left. A recommendation you cannot execute is "
            f"not a recommendation, so this leg is scored as cash. To move points "
            f"here instead, drop one of {', '.join(funded) or 'the funded legs'}."
        )
        rescored.add_reason(
            "TRIP_BALANCE_EXHAUSTED",
            rescored.verdict_reason,
            leg=legs[index].id,
            funded_first=list(funded),
            remaining={c: v for c, v in remaining.items() if v is not None},
        )
        rescored.warnings.append(rescored.verdict_reason)
        demoted.append(legs[index].id)
        out[index] = rescored

    return out


def trip_residue(
    results: List[LegResult], wallet: Wallet
) -> Dict[str, Dict[str, object]]:
    """
    Residue report for a whole trip: what each currency is left with.

    Only legs the optimizer actually recommends paying with points spend points.
    A leg where cash wins costs no points, so its funding plan must not be
    counted - that was easy to get wrong and would have overstated every spend.
    """
    plans = [r.funding_plan for r in results if r.verdict == "points" and r.funding_plan]
    return residue_report(wallet, plans)


def trip_funding_report(
    results: List[LegResult], wallet: Optional[Wallet]
) -> Dict[str, object]:
    """
    Can the trip's recommendation actually be executed from the wallet?

    THE QUESTION `trip_totals` NEVER ASKED (finding C-3). It summed
    `points_required` across legs and printed a percentage, without once
    checking that sum against the balance the points would come out of. This
    answers it, and `print_trip_totals` prints the answer ABOVE the headline
    rather than in a note underneath a table underneath it.
    """
    spend: Dict[str, int] = {}
    for r in results:
        if r.verdict != "points" or r.funding_plan is None:
            continue
        for currency, points in r.funding_plan.per_currency_spend.items():
            spend[currency] = spend.get(currency, 0) + points
        used = r.funding_plan.existing_target_points_used
        if used and wallet is not None and wallet.holds(r.funding_plan.target_program):
            target = r.funding_plan.target_program
            spend[target] = spend.get(target, 0) + used

    demoted = [r.leg.id for r in results if r.demoted_for_trip_balance]
    report: Dict[str, object] = {
        "trip_points_spend": spend,
        "trip_legs_demoted_for_balance": demoted,
        "trip_balance_bound": bool(demoted),
        "trip_balance_checked": wallet is not None,
        "trip_overdrawn_currencies": {},
        "trip_funding_executable": True,
    }
    if wallet is None:
        # No wallet, no ceiling, and the report SAYS the check did not happen
        # rather than reporting a pass it did not perform.
        report["trip_funding_note"] = (
            "No wallet was supplied to trip_totals, so the trip-level balance "
            "ceiling was NOT checked here. This is not a statement that the plan "
            "fits."
        )
        return report

    over = {}
    for currency, points in spend.items():
        if not wallet.holds(currency):
            continue
        balance = wallet.balance_of(currency)
        if balance is not None and points > balance:
            over[currency] = {"needed": points, "balance": balance}
    report["trip_overdrawn_currencies"] = over
    report["trip_funding_executable"] = not over
    if over:
        report["trip_funding_note"] = (
            "THIS PLAN CANNOT BE EXECUTED: "
            + "; ".join(
                f"it spends {v['needed']:,} {c} against a balance of "
                f"{v['balance']:,}"
                for c, v in sorted(over.items())
            )
            + ". No margin is quoted for a plan the balance cannot fund."
        )
    elif demoted:
        report["trip_funding_note"] = (
            f"THE BALANCE IS THE BINDING CONSTRAINT ON THIS TRIP. "
            f"{', '.join(demoted)} would each have been a points win in "
            f"isolation; the wallet cannot fund every leg, so they are scored as "
            f"cash and the figures below are for the plan you can actually "
            f"execute."
        )
    else:
        report["trip_funding_note"] = (
            "The whole recommendation fits inside the balances supplied: "
            + (
                ", ".join(
                    f"{n:,} {c} of "
                    f"{'unconstrained' if wallet.balance_of(c) is None else format(wallet.balance_of(c), ',')}"
                    for c, n in sorted(spend.items())
                    if wallet.holds(c)
                )
                or "no points are spent at all"
            )
            + "."
        )
    return report


def leg_has_no_partner(result: LegResult) -> bool:
    """
    MR5-3. "No points path at all" means NO PARTNER EXISTS, and one predicate says so.

    The totals table used to print `legs_without_points_path - legs_never_priced`.
    Those two counts are derived from DIFFERENT predicates - a verdict and a
    `points_absence` - and `live_trip.annotate_live_verdicts` rewrites the
    verdict on the live path while leaving the absence set. Legs therefore left
    the first set, stayed in the second, and the table printed

        Legs with NO UR path at all | -1

    while the footer of the same run correctly named B5, B6, B7. A derived count
    that can go negative is a count nobody checked, so the derivation is gone:
    this is the footer's own condition (`main.py`), and both callers ask it.
    """
    return (
        result.verdict == "cash (no points path)"
        and result.points_absence != "never_priced"
    )


def trip_totals(
    results: List[LegResult], wallet: Optional[Wallet] = None
) -> Dict[str, float]:
    """
    Aggregate a trip both ways: all-cash vs optimizer-chosen.

    `beat_cash_pct` is the headline number the brief asks for: how much the
    optimizer's recommendation beats simply paying cash for everything.

    Pass `wallet` and the totals also carry whether the recommendation can be
    EXECUTED from it. Without one the feasibility keys still appear and say the
    check did not run - the one thing they never do is imply it passed.
    """
    # ALL-CASH IS PRICED ON EACH LEG'S OWN DATE. `cash_baseline_usd` and
    # `cash_total_score_usd` differ only for a date-shifted leg, where the
    # baseline is the fare for the day the trip actually says. Building the
    # baseline from an off-date fare would inflate the saving by exactly the
    # bias plan section 4.5 exists to prevent (finding H-5).
    finite = [r for r in results if r.cash_baseline_usd != float("inf")]
    all_cash = sum(r.cash_baseline_usd for r in finite)

    # A LEG NOBODY CAN PRICE IS EXCLUDED FROM BOTH SIDES AND COUNTED SEPARATELY.
    #
    # Its `winner_cost_usd` is +inf, and summing an infinity into the optimized
    # total produced a headline of `-inf%`. Before the L-1 fix this state was
    # unreachable from the CLI, because a cash option in an unconfigured
    # currency aborted the entire run before any total was computed; now that a
    # single bad leg no longer kills six good ones, the totals have to cope with
    # one. Dropping such a leg from BOTH sides keeps the percentage meaningful
    # and the count below stops it being dropped SILENTLY, which would be its
    # own version of pretending a missing number is zero.
    priceable = [r for r in results if r.winner_cost_usd != float("inf")]
    unpriced = [r for r in results if r.winner_cost_usd == float("inf")]
    optimized = sum(r.winner_cost_usd for r in priceable)
    # v1: the recommendation is a RANGE whenever any surcharge estimate is one.
    optimized_low = sum(
        r.winner_cost_low_usd for r in priceable if r.winner_cost_low_usd != float("inf")
    )
    optimized_high = sum(
        r.winner_cost_high_usd
        for r in priceable
        if r.winner_cost_high_usd != float("inf")
    )
    points_spent = sum(
        r.points_required for r in results if r.verdict == "points"
    )
    cash_still_owed = sum(
        (r.points_surcharge_usd + r.mandatory_fees_usd)
        if r.verdict == "points"
        else r.cash_total_score_usd
        for r in results
        if r.cash_total_score_usd != float("inf") or r.verdict == "points"
    )

    savings = all_cash - optimized
    beat_pct = (savings / all_cash * 100) if all_cash else 0.0
    beat_pct_low = ((all_cash - optimized_high) / all_cash * 100) if all_cash else 0.0
    beat_pct_high = ((all_cash - optimized_low) / all_cash * 100) if all_cash else 0.0

    sensitive = [r.leg.id for r in results if r.verdict_sensitive]
    # The verdict "cash (surcharge unknown)" is shared by a leg whose unknown is
    # the carrier SURCHARGE and one whose unknown is the award's TAXES. This
    # counts only the first; the second is `legs_taxes_unknown`. Counting both
    # here printed one leg under "surcharge is UNKNOWN" AND "TAXES are UNKNOWN".
    unknown_surcharge = [
        r.leg.id for r in results
        if r.verdict == "cash (surcharge unknown)"
        and not _candidate_taxes_unknown(r.best_points)
    ]
    apd_unknown = [
        r.leg.id for r in results if r.apd is not None and not r.apd.is_known
    ]
    # The legs whose points side is unscoreable because the award's TAXES are
    # unknown (none usable, or unconvertible) - as distinct from a carrier
    # surcharge that is unknown. The spread on these is partly TAXES, and a
    # headline label that names only "surcharge" says something false about it.
    # Keyed on the REASON CODE, not on the chosen candidate: a rejected
    # alternative with unknown taxes that sets the leg's floor is a taxes unknown
    # in the trip range just as surely as a chosen one.
    taxes_unknown = [
        r.leg.id for r in results
        if any(x.code == "TAXES_UNKNOWN" for x in r.reasons)
    ]
    surcharge_unknown_any = [
        r.leg.id for r in results
        if any(x.code == "SURCHARGE_UNKNOWN" for x in r.reasons)
    ]
    apd_unverified = [
        r.leg.id
        for r in results
        if r.apd is not None and r.apd.is_known and r.apd.inclusion_unverified
    ]
    apd_added = [r.leg.id for r in results if r.apd_added_usd]
    # MR5-3's predicate, written once and read by the totals AND the footer.
    no_partner = [r.leg.id for r in results if leg_has_no_partner(r)]
    fee_unpriceable = [r.leg.id for r in results if r.mandatory_fees_unpriceable]

    # v3 STEP 7: the margin carries its own provenance, or it is not emitted.
    # `evaluate_leg` is untouched; this is a pure aggregation over what
    # live_trip.apply_live already recorded on each Leg.
    from src.live_trip import provenance_counts

    provenance = provenance_counts(results)

    # C-3: whether the recommendation can actually be executed rides WITH the
    # headline, in the same dict, emitted by the same call - exactly as v3 made
    # the margin's provenance ride with it. A number and the reason it may be
    # unusable must not be separable by accident.
    funding = trip_funding_report(results, wallet)

    totals = {
        **funding,
        **provenance,
        "legs_unpriceable": len(unpriced),
        "legs_unpriceable_ids": [r.leg.id for r in unpriced],
        "all_cash_usd": all_cash,
        "optimized_usd": optimized,
        "optimized_low_usd": optimized_low,
        "optimized_high_usd": optimized_high,
        "savings_usd": savings,
        "beat_cash_pct": beat_pct,
        # The honest headline. When any surcharge is a range these differ, and
        # the single `beat_cash_pct` above must NOT be quoted on its own.
        "beat_cash_pct_low": beat_pct_low,
        "beat_cash_pct_high": beat_pct_high,
        "headline_is_a_range": abs(beat_pct_high - beat_pct_low) > 1e-9,
        "points_spent": points_spent,
        "cash_still_owed_usd": cash_still_owed,
        "legs_with_points_path": sum(1 for r in results if r.has_points_path),
        # "No path at all" means no partner exists. A leg whose partner exists
        # but whose award price we never captured is counted separately, and so
        # is one whose surcharge we cannot pin down.
        "legs_without_points_path": sum(
            1 for r in results if r.verdict == "cash (no points path)"
        ),
        "legs_points_unpriced": sum(
            1 for r in results if r.verdict == "cash (points unpriced)"
        ),
        # H-4. The subset of the above whose points side was NEVER PRICED. Not a
        # claim about partnerships, and counted separately so the row that IS a
        # claim keeps its literal meaning.
        "legs_never_priced": sum(
            1 for r in results if r.points_absence == "never_priced"
        ),
        "legs_never_priced_ids": [
            r.leg.id for r in results if r.points_absence == "never_priced"
        ],
        "legs_points_blocked": sum(
            1 for r in results if r.verdict == "cash (points blocked)"
        ),
        # WAY (10). Every leg-level UNKNOWN gets a trip-level answer, and for
        # these two the answer is a COUNTER with the leg ids in it. The first
        # also moves the pessimistic end of the range (see
        # `_withhold_points_side_for_unknown_apd`); the second does not, because
        # there the amount IS known and only its inclusion is unverified - but a
        # tax the headline left out must not be invisible at trip level either.
        "legs_apd_unknown": len(apd_unknown),
        "legs_apd_unknown_ids": apd_unknown,
        "legs_apd_unverified": len(apd_unverified),
        "legs_apd_unverified_ids": apd_unverified,
        "legs_apd_added": len(apd_added),
        "legs_apd_added_ids": apd_added,
        "apd_added_usd": sum(r.apd_added_usd for r in results),
        # MR5-3. THE SAME PREDICATE THE FOOTER USES, not a subtraction of two
        # counts derived from different predicates. `legs_without_points_path`
        # counts a VERDICT and `legs_never_priced` counts a `points_absence`;
        # the live path rewrites the verdict and leaves the absence set, so
        # legs left the first set, stayed in the second, and the difference
        # printed as -1 legs.
        "legs_no_partner": len(no_partner),
        "legs_no_partner_ids": no_partner,
        "legs_mandatory_fee_unpriceable": len(fee_unpriceable),
        "legs_mandatory_fee_unpriceable_ids": fee_unpriceable,
        "legs_surcharge_unknown": len(unknown_surcharge),
        "legs_surcharge_unknown_ids": unknown_surcharge,
        "legs_taxes_unknown": len(taxes_unknown),
        "legs_taxes_unknown_ids": taxes_unknown,
        # Any leg carrying an unknown CARRIER SURCHARGE, on the chosen option or
        # a rejected one - what the headline caveat must name. Separate from
        # `legs_surcharge_unknown`, which counts the verdict.
        "legs_any_surcharge_unknown": len(surcharge_unknown_any),
        "legs_verdict_sensitive": len(sensitive),
        "legs_verdict_sensitive_ids": sensitive,
        "legs_where_points_win": sum(1 for r in results if r.verdict == "points"),
        # Both counted by REASON CODE, not by verdict. A leg carries one verdict
        # but can carry an indirect-only award AND an unattributed one; keyed on
        # the verdict, whichever branch won the elif hid the other from the trip
        # block - and an unattributed award may be a DIRECT partner's.
        "legs_party_pricing_unverified": len(_legs_with(results, "PARTY_PRICING_UNVERIFIED")),
        "legs_party_pricing_unverified_ids": _legs_with(results, "PARTY_PRICING_UNVERIFIED"),
        "legs_indirect_path_unverified": len(_legs_with(results, "INDIRECT_PATH_UNVERIFIED")),
        "legs_indirect_path_unverified_ids": _legs_with(results, "INDIRECT_PATH_UNVERIFIED"),
        "legs_award_unattributed": len(_legs_with(results, "PROGRAM_UNATTRIBUTED")),
        "legs_award_unattributed_ids": _legs_with(results, "PROGRAM_UNATTRIBUTED"),
        # The operating-airline lookup, by reason code on the CHOSEN award. Always
        # present, zero when the lookup was never engaged.
        "legs_metal_lookup_missing": len(_legs_with(results, "METAL_LOOKUP_MISSING")),
        "legs_metal_lookup_missing_ids": _legs_with(results, "METAL_LOOKUP_MISSING"),
        "legs_metal_not_recorded_ids": [
            r.leg.id
            for r in results
            if any(
                x.code == "METAL_LOOKUP_MISSING" and x.data.get("status") == "not_recorded"
                for x in r.reasons
            )
        ],
        "legs_metal_unknown": len(_legs_with(results, "METAL_UNKNOWN")),
        "legs_metal_unknown_ids": _legs_with(results, "METAL_UNKNOWN"),
        "rests_on_placeholder_fx": any(r.rests_on_placeholder_fx for r in results),
    }
    # WAY (10). The declarations in `models.TRIP_LEVEL_ANSWERS` are checked
    # against THIS run before the dict leaves this function: every promised
    # counter is really here, and no leg carrying an unknown declared to widen
    # the range kept a finite points score it could win the total with.
    check_trip_level_answers(results, totals)
    return totals
