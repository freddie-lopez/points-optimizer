"""
Live trip mode: score a WHOLE TRIP against real Seats.aero award space.

THE ONE FEATURE IN v3, AND THE REASON IT EXISTS.

The tool had two modes that could not meet. `--trip-fixture` scored a whole
multi-leg trip but every price in it - cash AND points - came from screenshots.
It never called Seats.aero, not once. `--origin/--destination/--date` called
Seats.aero for real but scored exactly one route.

So Trip B's headline - "beats cash by 6.55%-15.66%" - rested on Google Flights
badges on all four flight legs, of which exactly ONE has ever been corroborated
against a real response. This module builds the missing path.

IT IS A TRANSFORM, NOT A BRANCH. `apply_live(fixture, ...)` takes a TripFixture,
swaps each flight leg's points candidates for parsed live Awards, and hands the
result to the UNCHANGED `evaluate_trip`. Nothing here scores anything.
`evaluate_leg` is 480 lines carrying every honesty invariant in the tool and is
pinned by ~200 tests; live mode must not be able to change HOW a leg is scored,
only WHAT it is scored on.

THREE RULES THAT ARE NOT NEGOTIABLE HERE:

1. CASH IS NEVER TOUCHED. There is no cash-price API in scope and there will not
   be one. Screenshots are the source of truth for cash, permanently.
2. AN API FAILURE AND AN ABSENCE OF AWARD SPACE ARE DIFFERENT STATES. See
   models.LiveLegOutcome, whose constructor refuses to let them merge.
3. A LEG THAT FAILS DOES NOT FAIL THE TRIP. Score the legs that worked, report
   the ones that did not, and say which is which.
"""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import requests

from src.models import (
    Award,
    CashOption,
    DateRange,
    FlexibleFinding,
    Leg,
    LegResult,
    LiveLegOutcome,
    LiveQuerySpec,
    LiveQueryState,
    PointsCandidate,
    PointsProvenance,
)
from src.seats_client import SeatsAeroError, SeatsClient
from src.surcharge import SurchargeTable, default_table

# The provenance string a live candidate carries. DISTINCT from
# "google_badge_unverified" so the two can never be confused in output, and
# distinct from the bare "seats_aero" the single-route path uses, so a candidate
# that came through live TRIP mode is identifiable as such.
LIVE_SOURCE = "seats_aero_live"

# `carrier_source` for a live candidate whose metal is a single named carrier
# but whose TAXES CANNOT BE SAFELY COMBINED with a modeled surcharge (see
# `_taxes_are_the_whole_carrier_cash_figure`). It deliberately does NOT read as
# known metal, which is what routes the leg down evaluate_leg's unscoreable path
# and gets it a floor plus a break-even instead of a confident wrong number.
#
# NOTE A DEVIATION FROM THE PLAN, section 4.6's conversion table, which says a
# single-entry carrier list becomes carrier_source="seats_aero" (i.e. known
# metal). Taken literally that contradicts the RULE stated three paragraphs
# below the same table - that a live award whose surcharge is a nonzero modeled
# band is UNSCOREABLE, because we cannot tell whether {X}TotalTaxes already
# contains that surcharge. Following the table would have scored such a leg with
# the modeled surcharge and silently DROPPED the API's own tax figure. The rule
# is followed; the table cell is not. Recorded here rather than done quietly.
LIVE_UNRESOLVED_CARRIER_SOURCE = "seats_aero_live_taxes_unresolved"

# A new PAY CASH sub-state. It says NOTHING about award space - that is the whole
# point of it existing separately from "cash (no points path)".
VERDICT_NO_LIVE_DATA = "cash (no live points data)"


@dataclass
class LiveOptions:
    """Everything the CLI can vary about a live run."""

    live: bool = False
    flex_days: int = 0
    refresh: bool = False
    cache_ttl: Optional[int] = None
    require_all_live: bool = False
    cache: Optional[object] = None  # response_cache.ResponseCache
    trip_id: str = ""
    # When a leg's live query returns nothing usable, may the fixture's badge
    # stand in? Default yes, and the leg is then reported as badge_fallback -
    # never as live.
    allow_badge_fallback: bool = True
    surcharges: Optional[SurchargeTable] = None


# ---------------------------------------------------------------------------
# Step 3: querying one leg, and never raising
# ---------------------------------------------------------------------------


def _query_spec(leg: Leg, flex_days: int) -> LiveQuerySpec:
    return LiveQuerySpec(
        leg_id=leg.id,
        origin=leg.origin,
        destination=leg.destination,
        leg_date=leg.date,
        start_date=leg.date - timedelta(days=max(flex_days, 0)),
        end_date=leg.date + timedelta(days=max(flex_days, 0)),
        flex_days=max(flex_days, 0),
    )


def _not_queried(leg: Leg, why: str) -> LiveLegOutcome:
    return LiveLegOutcome(
        leg_id=leg.id,
        state=LiveQueryState.NOT_QUERIED,
        provenance=PointsProvenance.UNAVAILABLE,
        note=why,
    )


def query_leg(
    leg: Leg,
    client: Optional[SeatsClient],
    opts: LiveOptions,
) -> Tuple[LiveLegOutcome, List[Award]]:
    """
    Query ONE leg. Returns (outcome, awards). NEVER RAISES.

    Partial failure is a normal, expected outcome here - Trip B is sixteen
    months out and award space that far ahead is thin and partner-dependent - so
    a failing leg is a value, not an exception. Raising per leg would make the
    common case an error path.

    The mapping, and every branch of it is load-bearing:

      * hotel leg / no IATA codes / --live off  -> NOT_QUERIED, no HTTP call
      * transport or parse failure              -> API_ERROR, message required
      * answered with zero parsed awards        -> NO_AWARD_SPACE, no message
      * answered with awards                    -> OK
    """
    if not opts.live:
        return _not_queried(leg, "live mode was not requested (--live off)"), []
    if leg.kind != "flight":
        return _not_queried(
            leg,
            "this is a hotel leg. Seats.aero is flights-only; no hotel award API "
            "exists and hotels stay manual.",
        ), []
    if not leg.origin or not leg.destination:
        return _not_queried(
            leg,
            f"the leg does not name both airports (origin={leg.origin or '(none)'}, "
            f"destination={leg.destination or '(none)'}), so there is nothing to "
            f"query. Add them to the fixture.",
        ), []
    if client is None:
        return _not_queried(leg, "no Seats.aero client was supplied"), []

    spec = _query_spec(leg, opts.flex_days)

    try:
        awards = client.search(
            spec.origin,
            spec.destination,
            DateRange(spec.start_date, spec.end_date),
            cache=opts.cache,
            cache_ttl=opts.cache_ttl,
            refresh=opts.refresh,
            leg_id=leg.id,
            trip_id=opts.trip_id,
        )
    except (SeatsAeroError, requests.RequestException) as e:
        # WE NEVER GOT AN ANSWER. This says nothing whatsoever about award space.
        return LiveLegOutcome(
            leg_id=leg.id,
            state=LiveQueryState.API_ERROR,
            provenance=PointsProvenance.UNAVAILABLE,
            queried=spec,
            error=f"{type(e).__name__}: {e}",
        ), []
    except Exception as e:  # noqa: BLE001 - a leg must never take down the trip
        return LiveLegOutcome(
            leg_id=leg.id,
            state=LiveQueryState.API_ERROR,
            provenance=PointsProvenance.UNAVAILABLE,
            queried=spec,
            error=(
                f"UNEXPECTED {type(e).__name__}: {e}. This is an API/client "
                f"failure, not a finding about award space."
            ),
        ), []

    pagination_note = getattr(client, "last_pagination_note", "")
    rows_seen = getattr(client, "last_rows_seen", 0)
    rows_skipped = getattr(client, "last_rows_skipped", 0)
    rows_unreadable = getattr(client, "last_rows_unreadable", 0)
    rows_no_avail = getattr(
        client, "last_rows_without_availability", max(rows_skipped - rows_unreadable, 0)
    )
    unreadable_reasons = list(getattr(client, "last_unreadable_reasons", []) or [])
    budget_exhausted = bool(getattr(client, "last_budget_exhausted", False))
    # MANAGER REVIEW MR-1. This getattr is the wire that was missing. The flag
    # was computed in search_raw and set on RawSearchResult four versions ago and
    # NOTHING ever read it, so coverage survived only as prose inside
    # pagination_note - which NO_AWARD_SPACE.render() does not read.
    result_incomplete = bool(getattr(client, "last_incomplete", False))
    incomplete_reason = str(getattr(client, "last_incomplete_reason", "") or "")
    if result_incomplete and not incomplete_reason:
        # LiveLegOutcome REQUIRES a reason with the flag. A client that sets the
        # flag without one is still a truncation and must not be downgraded to
        # "complete" just because it failed to explain itself.
        incomplete_reason = (
            "the client reported an INCOMPLETE result set and recorded no reason."
        )
    snapshot_name = getattr(client, "last_snapshot_name", None)
    manifest_key = getattr(client, "last_manifest_key", "")
    served_from_cache = bool(getattr(client, "last_served_from_cache", False))
    fetched_at = getattr(client, "last_fetched_at", None)
    # MR5-4: a cache may archive nowhere (`snapshot_dir is None`), in which case
    # there is no snapshot NAME either - but the two are read off different
    # objects, so this does not assume they agree.
    snapshot_path = (
        (opts.cache.snapshot_dir / snapshot_name)
        if (
            snapshot_name
            and opts.cache is not None
            and opts.cache.snapshot_dir is not None
        )
        else None
    )
    # v5 STEP 3, WAY (8). Read exactly the way every other piece of byte
    # provenance here is read - off the transport, by name, with a safe default.
    # `query_leg` gains no branch: the object it was handed either says it
    # replayed a file or it does not, and the five fields travel to every
    # outcome below through one dict so no construction site can forget them.
    replayed = bool(getattr(client, "last_replayed_from_snapshot", False))
    replay_fields = {
        "replayed_from_snapshot": replayed,
        "snapshot_name": (str(snapshot_name or "") if replayed else ""),
        "snapshot_content_hash": str(
            getattr(client, "last_snapshot_content_hash", "") or ""
        ),
        "snapshot_captured_at": getattr(client, "last_snapshot_captured_at", None),
        "snapshot_parser_version": str(
            getattr(client, "last_snapshot_parser_version", "") or ""
        ),
    }
    if replayed and opts.cache is None:
        # There is no cache on the replay path, so `snapshot_path` above is
        # None. The file that WAS read is still worth naming.
        snapshot_path = (
            getattr(client, "snapshot_dir", None) and
            getattr(client, "snapshot_dir") / snapshot_name
        ) or None

    on_date = sum(1 for a in awards if a.date == leg.date)
    off_date = len(awards) - on_date

    # FINDING H-1, THE OTHER HALF. `apply_live` checks the budget BEFORE each
    # leg, but `query_leg` did not check it at all - so a budget that ran out
    # inside a leg's own pagination came back as zero pages, zero awards, and a
    # confident finding of no award space, with zero HTTP calls made. A request
    # that was never sent is a BUDGET failure. It is checked before the
    # `not awards` branch precisely because that branch is where the collapse
    # used to happen.
    if budget_exhausted:
        return LiveLegOutcome(
            leg_id=leg.id,
            state=LiveQueryState.BUDGET_EXHAUSTED,
            provenance=PointsProvenance.UNAVAILABLE,
            queried=spec,
            rows_seen=rows_seen,
            pagination_note=pagination_note,
            result_incomplete=result_incomplete,
            incomplete_reason=incomplete_reason,
            error=(
                f"the {SeatsClient.DAILY_CALL_CAP} calls/day Seats.aero budget ran "
                f"out during this leg's own request, so it was never completed. "
                f"Nothing was cached and nothing was archived."
            ),
            **replay_fields,
        ), []

    state_for_manifest = (
        LiveQueryState.OK.value
        if awards
        else LiveQueryState.ANSWERED_UNREADABLE.value
        if rows_unreadable
        else LiveQueryState.ANSWERED_INCOMPLETE.value
        if result_incomplete
        else LiveQueryState.NO_AWARD_SPACE.value
    )
    # Complete the manifest row this fetch wrote. Only the parser knows how many
    # awards a response yields, and only now do we know what state the leg is in.
    if opts.cache is not None and snapshot_name:
        opts.cache.annotate_manifest(
            snapshot_name, state_for_manifest, len(awards), manifest_key=manifest_key
        )

    if not awards and rows_unreadable:
        # FINDING C-1, THE ROOT. Rows arrived and the parser could not read them.
        # The old code fell straight through to NO_AWARD_SPACE and told the user
        # "there is no award to buy on this date" - a fact about the world
        # asserted from a fact about our parser. That is v2's bug, third time.
        # LiveLegOutcome now REFUSES to build that combination, so this branch is
        # not merely the polite thing to do; the alternative raises.
        return LiveLegOutcome(
            leg_id=leg.id,
            state=LiveQueryState.ANSWERED_UNREADABLE,
            provenance=PointsProvenance.UNAVAILABLE,
            queried=spec,
            rows_seen=rows_seen,
            rows_skipped=rows_skipped,
            rows_unreadable=rows_unreadable,
            rows_without_availability=rows_no_avail,
            unreadable_reasons=unreadable_reasons,
            result_incomplete=result_incomplete,
            incomplete_reason=incomplete_reason,
            error=(
                "The response was received and then could not be parsed: "
                + ("; ".join(unreadable_reasons) or "no reason was recorded")
                + "."
            ),
            pagination_note=pagination_note,
            snapshot_path=snapshot_path,
            served_from_cache=served_from_cache,
            cache_fetched_at=fetched_at,
            **replay_fields,
        ), []

    if not awards and result_incomplete:
        # MANAGER REVIEW MR-1, THE FOURTH INSTANCE OF FAILURE-AS-FINDING.
        # Every row we saw was readable and we did not see every row. The old
        # code fell straight through to NO_AWARD_SPACE below and rendered text
        # BYTE-IDENTICAL to a genuinely empty, complete result: "there is no
        # award to buy on this date". An empty page of a truncated result set is
        # not an empty result set. LiveLegOutcome now REFUSES to build that
        # combination, so this branch is not politeness; the alternative raises.
        return LiveLegOutcome(
            leg_id=leg.id,
            state=LiveQueryState.ANSWERED_INCOMPLETE,
            provenance=PointsProvenance.UNAVAILABLE,
            queried=spec,
            rows_seen=rows_seen,
            rows_skipped=rows_skipped,
            rows_unreadable=0,
            rows_without_availability=rows_no_avail,
            result_incomplete=True,
            incomplete_reason=incomplete_reason,
            pagination_note=pagination_note,
            snapshot_path=snapshot_path,
            served_from_cache=served_from_cache,
            cache_fetched_at=fetched_at,
            **replay_fields,
        ), []

    if not awards:
        # THE API ANSWERED, EVERY ROW WAS READ IN FULL, AND THE ANSWER WAS
        # "NOTHING".
        # That is a real finding about the world and is reported as one. Note
        # there is NO error string here, and LiveLegOutcome refuses to accept
        # one - or a single unreadable row - in this state.
        return LiveLegOutcome(
            leg_id=leg.id,
            state=LiveQueryState.NO_AWARD_SPACE,
            provenance=PointsProvenance.UNAVAILABLE,
            queried=spec,
            rows_seen=rows_seen,
            rows_skipped=rows_skipped,
            rows_unreadable=0,
            rows_without_availability=rows_no_avail,
            # Guaranteed False by the branch above; passed explicitly so the
            # invariant is exercised rather than relied on by omission.
            result_incomplete=False,
            pagination_note=pagination_note,
            snapshot_path=snapshot_path,
            served_from_cache=served_from_cache,
            cache_fetched_at=fetched_at,
            **replay_fields,
        ), []

    return LiveLegOutcome(
        leg_id=leg.id,
        state=LiveQueryState.OK,
        # WAY (8): a replayed outcome is never LIVE. `LiveLegOutcome` RAISES on
        # that combination, so this is not politeness - the alternative refuses
        # to be constructed.
        provenance=(
            PointsProvenance.SNAPSHOT if replayed else PointsProvenance.LIVE
        ),
        queried=spec,
        awards_parsed=len(awards),
        awards_on_leg_date=on_date,
        awards_off_date=off_date,
        rows_seen=rows_seen,
        rows_skipped=rows_skipped,
        rows_unreadable=rows_unreadable,
        rows_without_availability=rows_no_avail,
        unreadable_reasons=unreadable_reasons,
        # An OK result can ALSO be partial: we found awards AND did not see the
        # whole result set. Coverage is a field, not a state, for exactly this.
        result_incomplete=result_incomplete,
        incomplete_reason=incomplete_reason,
        pagination_note=pagination_note,
        snapshot_path=snapshot_path,
        served_from_cache=served_from_cache,
        cache_fetched_at=fetched_at,
        **replay_fields,
    ), awards


# ---------------------------------------------------------------------------
# Step 8 / section 4.6: award -> candidate, and the taxes/surcharge trap
# ---------------------------------------------------------------------------


def _taxes_are_the_whole_carrier_cash_figure(
    leg: Leg, award: Award, surcharges: SurchargeTable
) -> Tuple[bool, str]:
    """
    May `{X}TotalTaxes` be taken as the COMPLETE carrier-side cash figure?

    THE TRAP, STATED SO NOBODY WALKS INTO IT. For a live award we hold two facts
    that are easy to conflate: a KNOWN tax figure from the API, and a SEPARATELY
    MODELED carrier surcharge whose relationship to that figure has never been
    observed. Adding them risks double counting. Ignoring the surcharge risks
    undercounting - which is v0's bug exactly.

    The rule, deliberately conservative:

      * If the surcharge resolves to a TIER-5 PROGRAM-POLICY $0 - United,
        Aeroplan, KrisFlyer, which are metal-independent by construction - then
        there IS no carrier surcharge to stack, and TotalTaxes is the whole
        carrier-side figure. Scoreable. This is the case the real SFO->MAD
        capture falls into.
      * Otherwise - a nonzero modeled band, or UNKNOWN - the tool CANNOT TELL
        whether TotalTaxes already contains it. Not scoreable; reported as a
        floor plus a break-even instead.

    Plan section 11.1 flags this as one of the three decisions most likely to be
    wrong, and it is right to: it is a guess about what Seats.aero's "total
    taxes" contains, made from ONE captured row on ONE program that happens to
    levy no YQ. Resolving it is empirical - find a live row for a program known
    to levy YQ, compare against a real booking page, write the answer down.
    """
    program = award.program
    est = surcharges.resolve(
        program,
        "",
        award.route_region or "",
        award.award_type,
        "",
        is_round_trip=False,
        carrier_is_known=False,
    )
    if est.is_known and est.amount_high == 0.0:
        return True, (
            f"{program} levies no carrier-imposed surcharge as a matter of "
            f"PROGRAM POLICY, which is metal-independent, so there is nothing to "
            f"stack on top of the API's tax figure and it can be taken as the "
            f"complete carrier-side cash cost. GOVERNMENT TAXES AND AIRPORT "
            f"CHARGES BEYOND WHAT SEATS.AERO REPORTS ARE STILL NOT MODELLED."
        )
    return False, (
        f"NOT SCORED: Seats.aero reports a tax figure for this award, but "
        f"{program}'s carrier-imposed surcharge here is "
        f"{'a nonzero modeled band' if est.is_known else 'UNKNOWN'}, and it has "
        f"never been observed whether the API's 'total taxes' already includes "
        f"it. Adding them could double count; ignoring the surcharge would "
        f"undercount, which is the v0 bug. Reported as a floor plus a break-even "
        f"instead of as a number the tool cannot defend."
    )


_APD_TABLES = None


def _apd_tables():
    """Load the APD rate and band tables once per process (they are static CSVs)."""
    global _APD_TABLES
    if _APD_TABLES is None:
        from src import apd as apd_module

        _APD_TABLES = (apd_module.load_apd_table(), apd_module.load_apd_bands())
    return _APD_TABLES


def taxes_below_owed_uk_duty(leg: Leg, award: Award) -> str:
    """
    Why a KNOWN live tax figure must not be believed on a UK departure, or "".

    UK Air Passenger Duty is owed on an award ticket departing the UK and sits
    INSIDE the taxes figure (that is why the live path does not add it on top).
    A figure SMALLER than the duty for this award's own cabin therefore cannot
    be the whole of the taxes: it is incomplete, and scoring it scores the
    duty at $0. The parser's zero rule catches exactly 0; one cent walked past
    it and a B4 award with $5.00 of "taxes" beat $482 of cash by 4.22% while
    GBP 102 was owed on it.

    Only applies when the figure is known, the leg departs GB, and the duty for
    the award's cabin is itself known. Compared per passenger - Seats.aero's
    figure is per passenger, and so is the rate.
    """
    if not award.cash_component_known:
        return ""
    try:
        from src import apd as apd_module

        rates, bands = _apd_tables()
        charge = apd_module.apd_for_leg(
            leg,
            cabin=award.award_type,
            travelers=1,
            on=award.date or leg.date,
            rates=rates,
            bands=bands,
        )
    except Exception:  # noqa: BLE001 - a table problem is reported by apply_apd
        return ""
    if charge is None or not charge.is_known:
        return ""
    duty = float(charge.total_usd)
    figure = float(award.cash_component)
    if figure + 0.005 >= duty:
        return ""
    return (
        f"Seats.aero reported taxes of ${figure:,.2f} on this award, which departs "
        f"the UK. UK Air Passenger Duty of ${duty:,.2f} per passenger "
        f"(GBP {charge.rate_gbp:,.2f}) is owed on this ticket and belongs INSIDE "
        f"that figure, so a figure smaller than the duty is INCOMPLETE. The taxes "
        f"on this award are UNKNOWN - not ${figure:,.2f}."
    )


def award_to_candidate(
    leg: Leg,
    award: Award,
    outcome: LiveLegOutcome,
    surcharges: SurchargeTable,
    snapshot_name: str = "",
) -> PointsCandidate:
    """
    Convert one parsed live Award into a PointsCandidate for the existing scorer.

    Note what is NOT dropped. An award in a program with no Chase UR path
    (`ur_transferable is False`) is KEPT and marked; so is one whose UR path is
    unknown (`None`). "This award is real and you cannot reach this program from
    UR" is a finding, and dropping the row would hide real availability.
    """
    carriers = list(award.candidate_carriers)
    scoreable, why = _taxes_are_the_whole_carrier_cash_figure(leg, award, surcharges)

    # What this function believes about the taxes. Starts as the parser's view
    # and can only get LESS certain here (see the UK duty check below).
    taxes_known = bool(award.cash_component_known)
    taxes_note = award.cash_component_note
    taxes_reported_amount = award.cash_component_source_amount
    below_duty = taxes_below_owed_uk_duty(leg, award)
    if below_duty:
        taxes_known = False
        taxes_note = below_duty
        # Nothing USABLE: the figure is known to be incomplete, which is not the
        # same as "exists in a currency we cannot price".
        taxes_reported_amount = None

    # FINDING C-2. THE PROGRAM-POLICY $0 IS A STATEMENT ABOUT THE CARRIER
    # SURCHARGE. IT SAYS NOTHING ABOUT TAXES.
    #
    # `scoreable` means "Aeroplan levies no YQ, so the API's TotalTaxes is the
    # whole carrier-side cash figure". That conclusion needs the tax figure. If
    # TaxesCurrency has no configured FX rate the figure exists and cannot be
    # priced, so the whole carrier-side cash cost is UNKNOWN - and the old code
    # instead set the amount to 0.0, dropped the captured flag, fell through to
    # the modeled table, and collected the tier-5 $0 as if it were an answer.
    taxes_unconvertible = bool(
        taxes_reported_amount is not None and not taxes_known
    )
    # THE SAME RULE FOR EVERY WAY TAXES CAN BE UNKNOWN. C-2 above covers a figure
    # that exists and cannot be converted. It left the other ways out: no
    # figure at all, a source Seats.aero does not report taxes for, or a 0 that
    # means "nothing reported". Those have no amount, so `taxes_unconvertible` was
    # False, the program-policy $0 made the award scoreable, and the missing
    # taxes were scored as $0 - a United award with NO tax figure beat cash on
    # Trip B's B4 by 5.82%. Unknown taxes are unknown cash, whatever the reason.
    taxes_unknown = not taxes_known
    if taxes_unknown and not taxes_unconvertible:
        scoreable = False
        why = (
            f"NOT SCORED: the taxes on this award are UNKNOWN. "
            f"{taxes_note} That is real cash of unknown size, so "
            f"the carrier-side cash cost of this award is UNKNOWN - not $0. A "
            f"program's no-carrier-surcharge policy is a statement about YQ/YR and "
            f"does NOT price taxes. Reported as a floor plus a break-even instead "
            f"of as a number the tool cannot defend."
        )
    if taxes_unconvertible:
        scoreable = False
        why = (
            f"NOT SCORED: Seats.aero reports taxes of "
            f"{award.cash_component_currency or '(unnamed currency)'} "
            f"{award.cash_component_source_amount:,.2f} on this award and the tool "
            f"CANNOT convert them to USD. {award.cash_component_note} That is real "
            f"cash of unknown size, so the carrier-side cash cost of this award is "
            f"UNKNOWN - not $0. {award.program}'s no-carrier-surcharge policy is a "
            f"statement about YQ/YR and does NOT price these taxes. Reported as a "
            f"floor plus a break-even instead of as a number the tool cannot defend."
        )

    if len(carriers) == 1 and scoreable:
        operating, carrier_source = carriers[0], "seats_aero"
    elif len(carriers) == 1:
        operating, carrier_source = carriers[0], LIVE_UNRESOLVED_CARRIER_SOURCE
    else:
        # A four-airline list is NOT metal. Picking the first entry would produce
        # a confident surcharge for an aeroplane nobody has confirmed you would
        # be on. `has_known_metal` already reads this as False.
        operating, carrier_source = "", "seats_aero_ambiguous"

    ur_note = ""
    if award.indirect_ur_path:
        # NOT "no UR path": there is one, it is two hops, and it is not scored.
        ur_note = ""
    elif award.ur_transferable is False:
        ur_note = (
            f" NO UR PATH: {award.program} is not a Chase UR transfer partner. "
            f"The award is real and bookable, just not from a UR balance."
        )
    elif award.ur_transferable is None:
        ur_note = (
            " Whether Chase UR reaches this program is UNKNOWN and is not being "
            "guessed."
        )

    fetched = (
        f" Fetched {outcome.cache_fetched_at}"
        if outcome.cache_fetched_at
        else " Fetched during this run"
    )
    snapshot = f", snapshot {snapshot_name}" if snapshot_name else ""

    taxes_are_the_surcharge = bool(scoreable and taxes_known)
    program_missing = not (award.program or "").strip()
    label_program = award.program or "(program NOT NAMED by the response)"

    return PointsCandidate(
        label=(
            f"LIVE {label_program} {award.route or leg.id} {award.award_type} "
            f"{award.cost:,} pts ({award.seats_available} seats)"
        ),
        program=award.program,
        points=award.cost,
        # The taxes ride as a CAPTURED SURCHARGE only in the program-policy $0
        # case, where they genuinely ARE the whole carrier-side cash figure.
        # Everywhere else `cash_surcharge` stays what its name says - a captured
        # carrier surcharge - and the taxes travel in their own fields below,
        # known or unknown, rather than being flattened to 0.0 (finding C-2).
        cash_surcharge=award.cash_component if taxes_are_the_surcharge else 0.0,
        surcharge_currency="USD",
        surcharge_captured=taxes_are_the_surcharge,
        observed_taxes_usd=award.cash_component if taxes_known else 0.0,
        observed_taxes_known=taxes_known,
        observed_taxes_reported=taxes_reported_amount is not None,
        observed_taxes_amount=taxes_reported_amount,
        observed_taxes_currency=award.cash_component_currency,
        observed_taxes_note=taxes_note,
        observed_taxes_are_the_surcharge=taxes_are_the_surcharge,
        taxes_unconvertible=taxes_unconvertible,
        taxes_unknown=taxes_unknown,
        award_date=award.date,
        program_attribution_missing=program_missing,
        indirect_ur_path=award.indirect_ur_path,
        source=LIVE_SOURCE,
        source_note=(
            f"{award.source_note} {why}{ur_note}{fetched}{snapshot}."
        ).strip(),
        program_attribution_assumed=False,
        operating_carrier=operating,
        marketing_carrier="",
        carrier_source=carrier_source,
        cabin=award.award_type,
        is_round_trip=False,
    )


def _finding(leg: Leg, award: Award) -> FlexibleFinding:
    return FlexibleFinding(
        leg_id=leg.id,
        award_date=award.date,
        leg_date=leg.date,
        program=award.program,
        cabin=award.award_type,
        points=award.cost,
        taxes_amount=award.cash_component_source_amount,
        taxes_currency=award.cash_component_currency,
        taxes_usd=award.cash_component if award.cash_component_known else None,
        taxes_known=award.cash_component_known,
        seats=award.seats_available,
        carriers=list(award.candidate_carriers),
        source_note=award.source_note,
    )


# ---------------------------------------------------------------------------
# Step 4 + 5: the walk, the window, the budget, and the merge
# ---------------------------------------------------------------------------


def _cash_dates(leg: Leg) -> Dict[date, CashOption]:
    """Captured cash prices by the date they are FOR."""
    out: Dict[date, CashOption] = {}
    for opt in leg.cash_options:
        out.setdefault(opt.date or leg.date, opt)
    return out


def apply_live(
    fixture,
    client: Optional[SeatsClient],
    opts: LiveOptions,
) -> Tuple[object, List[LiveLegOutcome]]:
    """
    THE SEAM. Fixture in, fixture out, plus one outcome record per leg.

    Walks the flight legs IN ORDER, queries each, and replaces its
    `points_candidates` with live ones. `cash_options` is never read for
    modification and never written - a test deep-compares the cash side before
    and after.

    Returns (fixture, outcomes). The fixture is then handed to the UNCHANGED
    `evaluate_trip`.
    """
    surcharges = opts.surcharges or default_table()
    outcomes: List[LiveLegOutcome] = []

    flight_legs = [leg for leg in fixture.legs if leg.kind == "flight"]
    remaining_flight_legs = len(flight_legs)
    budget_blown = False

    for leg in fixture.legs:
        if leg.kind != "flight":
            outcome, awards = query_leg(leg, client, opts)
            _record(leg, outcome, awards, opts, surcharges)
            outcomes.append(outcome)
            continue

        remaining_flight_legs -= 1

        # BUDGET. Checked BEFORE the call, against what is still to come, so the
        # trip stops cleanly rather than half-filling itself and leaving the
        # reader to guess which legs were real. An exhausted budget is an ERROR
        # state, never an absence of award space.
        # v5 STEP 3. A REPLAY SPENDS NO BUDGET, so it is not subject to one.
        # `SnapshotTransport.spends_api_budget` is False and makes no calls; a
        # replay refused because an earlier live run in the same process had
        # exhausted the cap would be a budget failure reported over bytes that
        # are sitting on disk.
        spends_budget = bool(getattr(client, "spends_api_budget", True))

        if opts.live and client is not None and spends_budget and not budget_blown:
            remaining_calls = SeatsClient._budget_remaining()
            if remaining_calls <= 0:
                budget_blown = True

        if budget_blown or (
            opts.live
            and client is not None
            and spends_budget
            and SeatsClient._budget_remaining() <= 0
        ):
            budget_blown = True
            outcome = LiveLegOutcome(
                leg_id=leg.id,
                state=LiveQueryState.BUDGET_EXHAUSTED,
                provenance=PointsProvenance.UNAVAILABLE,
                queried=_query_spec(leg, opts.flex_days),
                error=(
                    f"the {SeatsClient.DAILY_CALL_CAP} calls/day Seats.aero "
                    f"budget was exhausted before this leg was reached, so no "
                    f"request was made for it."
                ),
            )
            _record(leg, outcome, [], opts, surcharges)
            outcomes.append(outcome)
            continue

        outcome, awards = query_leg(leg, client, opts)
        _record(leg, outcome, awards, opts, surcharges)
        outcomes.append(outcome)

    return fixture, outcomes


def _record(
    leg: Leg,
    outcome: LiveLegOutcome,
    awards: List[Award],
    opts: LiveOptions,
    surcharges: SurchargeTable,
) -> None:
    """
    Attach one leg's live result to the leg. Sets provenance on EVERY leg.

    THE DATE RULE (plan section 4.5). Only an award on the leg's OWN date may be
    scored against that leg's cash price. An award found three days earlier is
    compared against nothing, because there is no cash price for that date and
    the tool cannot invent one. Off-date awards become advisory findings and
    never enter a verdict or the trip margin.

    The one exception is not an exception to the rule but an application of it:
    if the fixture CARRIES a captured cash price dated to the award's date, then
    a legitimate comparison exists, the award is promoted, and the leg is marked
    DATE_SHIFTED so the fact that a date moved is never lost.
    """
    leg.live_outcome = outcome

    if not opts.live or outcome.state is LiveQueryState.NOT_QUERIED:
        # Untouched by live mode. Provenance still gets set, because a leg with
        # no recorded provenance is a leg whose number came from nowhere.
        leg.points_provenance = (
            PointsProvenance.BADGE_FALLBACK
            if leg.points_candidates
            else PointsProvenance.UNAVAILABLE
        )
        return

    cash_by_date = _cash_dates(leg)
    on_date: List[Award] = []
    promoted: List[Award] = []
    findings: List[FlexibleFinding] = []

    for award in awards:
        if award.date == leg.date:
            on_date.append(award)
        elif award.date in cash_by_date:
            promoted.append(award)
        else:
            findings.append(_finding(leg, award))

    leg.flexible_date_findings = findings

    usable = on_date + promoted
    if promoted:
        leg.date_shifted = True
        leg.date_shifted_to = sorted(a.date for a in promoted)[0]

    if not usable:
        # Live supplied nothing scoreable for this leg's own date. The badge may
        # stand in - but the leg is then BADGE_FALLBACK, never live.
        if leg.points_candidates and opts.allow_badge_fallback:
            leg.points_provenance = PointsProvenance.BADGE_FALLBACK
        else:
            leg.superseded_candidates = list(leg.points_candidates)
            leg.points_candidates = []
            leg.points_provenance = PointsProvenance.UNAVAILABLE
        return

    # BADGES ARE NEVER SILENTLY DISCARDED. Exactly one of Trip B's four badges
    # has ever been corroborated, so a divergence between a Google badge and what
    # live actually returned is the interesting case - it must become visible
    # instead of vanishing.
    leg.superseded_candidates = list(leg.points_candidates)
    leg.points_candidates = [
        award_to_candidate(
            leg, a, outcome, surcharges,
            snapshot_name=(outcome.snapshot_path.name if outcome.snapshot_path else ""),
        )
        for a in usable
    ]
    # v5 STEP 3. A replayed leg is SNAPSHOT, never LIVE. The provenance string
    # is what gets quoted, and a replayed number that calls itself live is the
    # laundering `--from-snapshot` exists to prevent.
    leg.points_provenance = (
        PointsProvenance.SNAPSHOT
        if outcome.replayed_from_snapshot
        else PointsProvenance.LIVE
    )


def supersession_lines(leg: Leg) -> List[str]:
    """
    One line per displaced badge: what it claimed, and what live returned.

    This is how a badge/reality divergence becomes readable. It is deliberately
    stated as a comparison rather than a verdict - a badge that disagrees with
    Seats.aero is not automatically wrong; it may be pricing a different fare
    class, a different routing, or dynamic revenue space.
    """
    if not leg.superseded_candidates:
        return []
    if leg.points_candidates:
        found = "; ".join(
            f"{c.program} {c.points:,} {c.cabin}" for c in leg.points_candidates
        )
    else:
        found = "nothing scoreable on this leg's own date"
    return [
        (
            f"SUPERSEDED BADGE on {leg.id}: the fixture claimed "
            f"{c.program} {c.points:,} ({c.source}). Live Seats.aero returned "
            f"{found}. The badge is retained, not deleted - it is evidence about "
            f"the badge, not about the award."
        )
        for c in leg.superseded_candidates
    ]


# ---------------------------------------------------------------------------
# Step 6: partial failure scores the rest of the trip honestly
# ---------------------------------------------------------------------------


def annotate_live_verdicts(results: List[LegResult]) -> List[LegResult]:
    """
    Re-label the verdict on legs that have NO POINTS DATA BECAUSE LIVE FAILED.

    Applied AFTER `evaluate_trip`, as a post-pass, precisely so that
    `evaluate_leg` stays untouched. `evaluate_leg` would otherwise report such a
    leg as "cash (no points path)", whose reason text says "No UR transfer
    partner covers this leg" - a claim about PARTNERSHIPS that we have no
    evidence for here. What actually happened is that we could not get, or did
    not get, award data.

    The new sub-state says nothing whatsoever about award space, and the two
    failure modes get different reason text, so a reader can tell an API outage
    from a genuinely empty calendar.
    """
    for r in results:
        outcome = r.leg.live_outcome
        if outcome is None:
            continue
        if outcome.state in (LiveQueryState.OK, LiveQueryState.NOT_QUERIED):
            continue

        if r.has_points_path or r.leg.points_candidates:
            # FINDING M-6. A LEG THAT FAILED BUT HAS A BADGE STILL FAILED.
            #
            # This used to `continue` here, so when a fixture badge stood in for
            # a dead API call the failure never reached the leg's verdict or its
            # warnings - only the provenance cell hinted at it, and the verdict
            # was indistinguishable from a clean badge-only run. Because
            # `allow_badge_fallback` defaults to True, that is the COMMON case
            # for a failing leg on any fixture that carries badges, i.e. Trip B.
            # The verdict itself is left alone (the badge really is what was
            # scored); what is added is the fact that live data was asked for
            # and did not arrive.
            r.warnings.append(outcome.render())
            r.verdict_reason = (
                f"{r.verdict_reason} LIVE DATA WAS NOT AVAILABLE FOR THIS LEG: "
                f"{outcome.render()} The figure above is the FIXTURE'S BADGE, "
                f"which is not corroborated award space, and nothing here is a "
                f"finding about award space on this leg."
            ).strip()
            continue

        r.verdict = VERDICT_NO_LIVE_DATA
        if outcome.state is LiveQueryState.NO_AWARD_SPACE:
            r.verdict_reason = (
                f"Pay cash. Seats.aero ANSWERED for "
                f"{outcome.queried.describe() if outcome.queried else 'this leg'} "
                f"and returned NO award space on this date. That is a FINDING: "
                f"there is no award to buy here. Cash is the only option, and it "
                f"is a real answer rather than a missing one."
            )
        else:
            r.verdict_reason = (
                f"Pay cash BY DEFAULT, not by finding. {outcome.render()} No "
                f"conclusion about award space on this leg can be drawn from "
                f"this run."
            )
        r.warnings.append(outcome.render())
    return results


# ---------------------------------------------------------------------------
# Step 7: the margin's provenance
# ---------------------------------------------------------------------------


def provenance_counts(results: List[LegResult]) -> Dict[str, object]:
    """
    Counters for `trip_totals`, plus the label that must ride with the headline.

    `margin_provenance == "live"` ONLY when every flight leg that has a points
    candidate came back LIVE. One badge leg makes it "mixed". A single number
    hiding a mixed source is worse than no number.
    """
    flights = [r for r in results if r.leg.kind == "flight"]
    live = [r for r in flights if r.leg.points_provenance is PointsProvenance.LIVE]
    # v5 STEP 3. Replayed legs are counted SEPARATELY from live ones and the
    # two are never summed into a single "live" count.
    snapshot = [
        r for r in flights if r.leg.points_provenance is PointsProvenance.SNAPSHOT
    ]
    badge = [
        r for r in flights if r.leg.points_provenance is PointsProvenance.BADGE_FALLBACK
    ]
    unavailable = [
        r for r in flights if r.leg.points_provenance is PointsProvenance.UNAVAILABLE
    ]

    def _in_state(state: LiveQueryState) -> List[str]:
        return [
            r.leg.id
            for r in flights
            if r.leg.live_outcome is not None and r.leg.live_outcome.state is state
        ]

    no_space = _in_state(LiveQueryState.NO_AWARD_SPACE)
    api_error = _in_state(LiveQueryState.API_ERROR)
    budget = _in_state(LiveQueryState.BUDGET_EXHAUSTED)
    unreadable = _in_state(LiveQueryState.ANSWERED_UNREADABLE)

    # FINDING H-4. A LEG THAT NEVER ANSWERED MUST BREAK "live".
    #
    # `provenance` was computed purely from legs that HAVE candidates, so a leg
    # whose API call failed and which carries no badge contributed nothing, was
    # not in `scoreable`, and could not make the run "mixed". The margin was
    # therefore labelled `live` with a dead leg in it, and `--require-all-live`
    # - whose entire purpose is "the moment someone wants to quote a number
    # publicly" - did not withhold. The prose note did say "1 of 2 flight legs",
    # so a careful reader was warned; the machine-readable gate was not.
    #
    # A leg that did not answer is counted here whether or not it has candidates.
    never_answered = [
        r
        for r in flights
        if r.leg.live_outcome is not None
        and r.leg.live_outcome.tells_us_nothing_about_award_space
        and r.leg.live_outcome.state is not LiveQueryState.NOT_QUERIED
    ]

    # v5 STEP 6/3. Was a live or replay run even ATTEMPTED on this trip? A run
    # that never asked and a run that asked and got nothing are different
    # failures, and until v5 both were reported as `badge`.
    attempted = [
        r
        for r in flights
        if r.leg.live_outcome is not None
        and r.leg.live_outcome.state is not LiveQueryState.NOT_QUERIED
    ]

    scoreable = [r for r in flights if r.leg.points_candidates]
    fresh = live + snapshot
    if not scoreable and not fresh:
        provenance = "none"
    elif fresh and not badge and not never_answered and len(fresh) == len(scoreable):
        # v5. A run whose every scoreable leg was REPLAYED is `snapshot`, not
        # `live`. It satisfies --require-all-live only when every replayed leg
        # was live AT CAPTURE TIME - which it was, because a snapshot only
        # exists for a leg the API answered. A manifest row that replays to a
        # badge fallback lands in `badge_fallback` below instead, so replay
        # reproduces a number without laundering it.
        provenance = "snapshot" if snapshot and not live else "live"
    elif fresh:
        provenance = "mixed"
    elif attempted:
        # v5 STEP 6. A live or replay attempt was made, it produced nothing
        # usable on any leg, and the fixture's badges answered instead. That is
        # NOT the same as a run that never asked, and quoting it needs the
        # qualifier attached - so it gets its own value rather than sharing
        # `badge` with the offline case.
        provenance = "badge_fallback"
    else:
        provenance = "badge"

    failure_clause = ""
    if never_answered:
        failure_clause = (
            f" {len(never_answered)} flight leg(s) NEVER PRODUCED A USABLE ANSWER "
            f"({', '.join(r.leg.id for r in never_answered)}): "
            + "; ".join(
                f"{r.leg.id} {r.leg.live_outcome.state.value}"
                for r in never_answered
            )
            + ". NOTHING is known about award space on those legs, so this margin "
            "is NOT a live number and must not be quoted as one."
        )

    if provenance == "live":
        note = (
            f"{len(live)} of {len(flights)} flight legs scored against live "
            f"Seats.aero availability ({', '.join(r.leg.id for r in live)}). "
            f"Cash is from captures, as it always is."
        )
    elif provenance == "snapshot":
        note = (
            f"{len(snapshot)} of {len(flights)} flight legs REPLAYED from "
            f"committed snapshots ({', '.join(r.leg.id for r in snapshot)}). "
            f"NOTHING was asked of Seats.aero on this run. Every one of those "
            f"legs was answered live AT CAPTURE TIME, which is why this margin "
            f"is quotable - but it is a reproducible number, not a fresh one, "
            f"and each leg names its own capture date. Cash is from captures, "
            f"as it always is."
        )
    elif provenance == "badge_fallback":
        note = (
            f"A live/replay run WAS attempted and produced nothing scoreable on "
            f"any of the {len(flights)} flight legs "
            f"({', '.join(r.leg.id for r in attempted)}). The fixture's badges "
            f"answered instead. THIS NUMBER IS QUOTABLE ONLY WITH THAT "
            f"QUALIFIER ATTACHED: it is a badge margin produced by a run that "
            f"asked for live data and did not get it."
        )
    elif provenance == "mixed":
        parts = []
        if live:
            parts.append(f"{len(live)} of {len(flights)} flight legs live "
                         f"({', '.join(r.leg.id for r in live)})")
        if snapshot:
            parts.append(
                f"{len(snapshot)} replayed from snapshots "
                f"({', '.join(r.leg.id for r in snapshot)})"
            )
        if badge:
            parts.append(
                f"{len(badge)} badge-derived ({', '.join(r.leg.id for r in badge)})"
            )
        note = (
            "; ".join(parts)
            + ". THIS MARGIN MIXES PROVENANCES AND MUST NOT BE QUOTED AS A LIVE "
            "NUMBER."
            + failure_clause
        )
    elif provenance == "badge":
        # FINDING L-2. The old text asserted "Every points price here is a Google
        # Flights badge" on every non-live run - including runs whose candidates
        # are `manual_capture` or `seats_aero`, which it had not looked at. The
        # sources are now read rather than assumed.
        sources = sorted(
            {c.source for r in flights for c in r.leg.points_candidates}
        )
        has_badges = "google_badge_unverified" in sources
        badge_only = sources == ["google_badge_unverified"]
        described = ", ".join(sources) or "no recorded source"
        if badge_only:
            provenance_sentence = (
                "Every points price here is a Google Flights badge, of which "
                "exactly one has ever been corroborated. "
            )
        elif has_badges:
            provenance_sentence = (
                f"The points prices here come from: {described} - a MIX. The "
                f"google_badge_unverified ones are Google Flights badges, of "
                f"which exactly one has ever been corroborated. "
            )
        else:
            # NO BADGE IS INVOLVED AT ALL, so nothing is said about badges. The
            # old text asserted "Every points price here is a Google Flights
            # badge, of which exactly one has ever been corroborated" on every
            # non-live run, without ever reading the candidates' own `source`
            # field - so it said it over prices whose source was manual_capture
            # or seats_aero (finding L-2). A provenance note that does not check
            # provenance is worse than no note.
            provenance_sentence = (
                f"The points prices here come from: {described}. None was checked "
                f"against live availability on this run, so each is only as good "
                f"as the capture behind it. "
            )
        note = (
            "NO leg was scored against live availability. "
            + provenance_sentence
            + "This is not a live margin."
            + failure_clause
        )
    else:
        note = "No points price of any provenance entered this margin." + failure_clause

    return {
        "legs_answer_unusable": len(never_answered),
        "legs_answer_unusable_ids": [r.leg.id for r in never_answered],
        "legs_unreadable": len(unreadable),
        "legs_unreadable_ids": unreadable,
        "legs_flight_total": len(flights),
        "legs_points_live": len(live),
        "legs_points_snapshot": len(snapshot),
        "legs_snapshot_ids": [r.leg.id for r in snapshot],
        "legs_points_badge": len(badge),
        "legs_points_unavailable": len(unavailable),
        "legs_no_award_space": len(no_space),
        "legs_no_award_space_ids": no_space,
        "legs_api_error": len(api_error),
        "legs_api_error_ids": api_error,
        "legs_budget_exhausted": len(budget),
        "legs_budget_exhausted_ids": budget,
        "legs_live_ids": [r.leg.id for r in live],
        "legs_badge_ids": [r.leg.id for r in badge],
        "margin_provenance": provenance,
        "margin_provenance_note": note,
    }
