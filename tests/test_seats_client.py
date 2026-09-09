"""
Seats.aero client tests, run against the FIRST REAL RESPONSE the project has.

tests/fixtures/seats_aero/sfo_mad_real.json is a capture Tsuki took from his own
Mac on 2026-09-08. Every parser assertion below runs against that real shape.
The previous version of this file asserted an INVENTED shape - flat `cost`,
`taxes`, `program`, `airline`, `seats_available` keys - and passed cleanly while
the parser it was testing could not read a real response at all. A test written
against a made-up schema tests nothing but the imagination that produced it.
"""
import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from src import config
from src.models import DateRange
from src.seats_client import (
    SEATS_AERO_SOURCES,
    SeatsClient,
    convert_taxes,
    parse_availability_row,
    parse_carriers,
    resolve_source,
)
from src.surcharge import SurchargeTable

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "seats_aero"
REAL = FIXTURES / "sfo_mad_real.json"


@pytest.fixture
def real_response():
    return json.loads(REAL.read_text())


@pytest.fixture
def real_row(real_response):
    return real_response["data"][0]


@pytest.fixture
def seats_client():
    client = SeatsClient(api_key="test_key_12345")
    client.clear_cache()
    SeatsClient.reset_call_budget()
    yield client
    client.clear_cache()
    SeatsClient.reset_call_budget()


def _mock_get(payload):
    response = MagicMock()
    response.json.return_value = payload
    return response


# ---------------------------------------------------------------------------
# The fixture itself
# ---------------------------------------------------------------------------


def test_the_real_capture_exists_and_is_the_documented_request():
    assert REAL.exists(), "the real Seats.aero capture is the basis of every test here"
    payload = json.loads(REAL.read_text())
    assert "SFO" in payload["_capture"]["request"]
    assert "MAD" in payload["_capture"]["request"]
    # The key must never be committed with the capture.
    assert "key redacted" in payload["_capture"]["auth_header"].lower()
    assert "TRUNCATED" in payload["_capture"]["fidelity"]


def test_the_capture_does_not_pretend_to_know_about_pagination(real_response):
    """
    The capture was cut at 2000 characters. Nothing in the fixture may claim to
    know whether count / hasMore / cursor were present.
    """
    for key in ("count", "hasMore", "has_more", "cursor"):
        assert key not in real_response


# ---------------------------------------------------------------------------
# (1) Source is nested at Route.Source and is a source CODE
# ---------------------------------------------------------------------------


def test_source_is_nested_under_route_not_top_level(real_row):
    assert "Source" not in real_row
    assert real_row["Route"]["Source"] == "aeroplan"


def test_source_code_maps_to_the_ratios_csv_program_name(real_row):
    program, ur, _ = resolve_source(real_row["Route"]["Source"])
    assert program == "Air Canada Aeroplan"
    assert ur is True


def test_every_ur_transferable_source_names_a_real_ratios_csv_program():
    """A mapping typo would silently make an award unfundable. Cross-check it."""
    import csv

    partners = {
        row["to_program"]
        for row in csv.DictReader((ROOT / "data" / "ratios.csv").open())
        if row["from_program"] == "UR"
    }
    for src in SEATS_AERO_SOURCES.values():
        if src.ur_transferable:
            assert src.program in partners, (
                f"{src.code} maps to {src.program!r}, which is not a UR partner "
                f"row in data/ratios.csv"
            )


def test_a_non_ur_source_is_reported_not_dropped():
    """
    "The award exists, you cannot reach this program from UR" is a FINDING.
    Silently dropping the row would hide real availability.
    """
    program, ur, _ = resolve_source("delta")
    assert program == "Delta SkyMiles"
    assert ur is False


def test_an_unmapped_source_is_unknown_not_false():
    """
    None ("we do not know whether UR reaches this") and False ("we know it does
    not") are different answers and must not collapse into each other.
    """
    program, ur, note = resolve_source("someprogramwehaveneverseen")
    assert ur is None
    assert "UNKNOWN" in note
    # CHANGED AT THE v1-v3 ADVERSARIAL FIX, finding L-4. This used to assert
    # `program == "someprogramwehaveneverseen"` - i.e. it pinned the behaviour
    # where an unrecognised SOURCE CODE was handed onward as a PROGRAM NAME and
    # printed in a Program column as if it were one. A code is not a name. The
    # award is still emitted (see the next test), the code is still preserved on
    # Award.program_source_code and is named in the note; what changes is that
    # the tool no longer claims to know which program it denotes.
    assert program == ""
    assert "someprogramwehaveneverseen" in note


def test_an_unmapped_source_still_produces_an_award(real_row):
    row = json.loads(json.dumps(real_row))
    row["Route"]["Source"] = "mysteryairlines"
    awards = parse_availability_row(row)
    assert len(awards) == 1
    assert awards[0].ur_transferable is None
    assert awards[0].cost == 50000


# ---------------------------------------------------------------------------
# (2) One row, four cabins
# ---------------------------------------------------------------------------


def test_the_real_row_yields_exactly_the_one_available_cabin(real_row):
    awards = parse_availability_row(real_row)
    assert [a.award_type for a in awards] == ["Y"]


def test_a_row_with_several_available_cabins_yields_one_award_each(real_row):
    """
    The old parser emitted ONE award per row and read a `cabin` key that does
    not exist, so three quarters of every response was thrown away.
    """
    row = json.loads(json.dumps(real_row))
    row["JAvailable"] = True
    row["JMileageCost"] = "70000"
    row["JTotalTaxes"] = 8900
    row["JRemainingSeats"] = 2
    row["JAirlines"] = "AC"
    row["FAvailable"] = True
    row["FMileageCost"] = "100000"
    row["FRemainingSeats"] = 1
    row["FAirlines"] = "LH"

    awards = parse_availability_row(row)
    assert [a.award_type for a in awards] == ["Y", "J", "F"]
    by_cabin = {a.award_type: a for a in awards}
    assert by_cabin["J"].cost == 70000
    assert by_cabin["J"].seats_available == 2
    assert by_cabin["F"].cost == 100000
    assert by_cabin["Y"].cost == 50000


def test_an_unavailable_cabin_produces_no_award(real_row):
    awards = parse_availability_row(real_row)
    assert all(a.award_type != "J" for a in awards)
    assert all(a.award_type != "W" for a in awards)


# ---------------------------------------------------------------------------
# (3) Taxes are integer cents in TaxesCurrency, which is not USD here
# ---------------------------------------------------------------------------


def test_taxes_are_cents_in_the_stated_currency(real_row):
    award = parse_availability_row(real_row)[0]
    assert real_row["YTotalTaxes"] == 4460
    assert real_row["TaxesCurrency"] == "CAD"
    assert award.cash_component_source_amount == pytest.approx(44.60)
    assert award.cash_component_currency == "CAD"


def test_reading_the_cents_field_as_dollars_would_overstate_by_100x(real_row):
    """The failure this guards: 4460 read as $4,460 instead of CAD 44.60."""
    award = parse_availability_row(real_row)[0]
    assert award.cash_component_source_amount < 100
    assert award.cash_component != 4460


def test_cad_now_converts_because_v3_sourced_a_rate_for_it(real_row):
    """
    CHANGED AT v3 STEP 0 (was
    test_an_unsupported_currency_degrades_to_unknown_never_to_zero).

    NOT LISTED IN THE PLAN'S SECTION 8, but REQUIRED BY STEP 0's OWN ACCEPTANCE
    CRITERION: "the CAD 44.60 in the SFO->MAD fixture now converts and
    Award.cash_component_known is True for it." The parser fix report closed with
    "CAD needs a sourced FX rate" as an open item for the architect; v3 supplies
    0.7256 and this test records that the open item is closed.

    Nothing about the RULE changed - an unsupported currency still degrades to
    unknown rather than to zero. CAD is simply no longer unsupported. The rule
    itself is now pinned by the JPY test below, which cannot go stale the same
    way because nothing will ever source a JPY rate for this project.
    """
    award = parse_availability_row(real_row)[0]
    assert award.cash_component_known is True
    assert award.cash_component_currency == "CAD"
    assert award.cash_component_source_amount == pytest.approx(44.60)
    assert award.cash_component == pytest.approx(32.36, abs=0.01)
    assert "CAD 44.60 converted" in award.cash_component_note


def test_a_genuinely_unsupported_currency_still_degrades_to_unknown_never_to_zero():
    """
    THE RULE THE TEST ABOVE USED TO CARRY, re-pinned on a currency that is
    actually unconfigured. The amount must NOT be scored at 0.0 and must NOT be
    converted at an invented rate - the same rule the surcharge model enforces,
    for the same reason.
    """
    known, amount, cur, usd, note = convert_taxes(4460, "JPY")
    assert known is False
    assert usd == 0.0
    assert amount == pytest.approx(44.60), "the source amount survives the failure"
    assert cur == "JPY"
    assert "No FX rate for JPY" in note
    assert "not as $0" in note
    assert "not at a rate the tool invented" in note


def test_a_supported_currency_converts_and_carries_the_source_amount():
    known, amount, cur, usd, note = convert_taxes(6440, "EUR")
    assert known is True
    assert amount == pytest.approx(64.40)
    assert cur == "EUR"
    assert usd == pytest.approx(64.40 * config.FX_RATES_TO_USD["EUR"])


def test_usd_taxes_convert_one_to_one():
    known, amount, cur, usd, _ = convert_taxes(4460, "USD")
    assert (known, amount, cur, usd) == (True, 44.60, "USD", 44.60)


def test_an_unconfirmed_fx_rate_is_disclosed_on_the_converted_figure():
    """
    CHANGED AT v3 STEP 0 (was
    test_a_placeholder_fx_rate_is_disclosed_on_the_converted_figure).
    NOT LISTED IN THE PLAN'S SECTION 8; a mechanical consequence of GBP ceasing
    to be a placeholder.

    The subject survives intact: a tax figure converted through a rate nobody has
    vouched for must say so on the figure itself, not only in a banner someone
    might scroll past. Only the WORD changes, because the rate now has a source.
    """
    known, _, _, usd, note = convert_taxes(10000, "GBP")
    assert known is True
    assert usd == pytest.approx(100.0 * 1.3540, abs=0.01)
    assert "CONFIRM BEFORE TRUSTING" in note
    assert "sourced 2026-09-08" in note
    assert "PLACEHOLDER" not in note, "GBP has a source now; saying otherwise is stale"


def test_a_true_placeholder_rate_is_still_disclosed_as_a_placeholder(monkeypatch):
    """The placeholder disclosure path itself, exercised on a synthetic rate."""
    monkeypatch.setitem(config.FX_RATES_TO_USD, "ZZZ", 2.0)
    monkeypatch.setitem(config.FX_PROVENANCE_TIER, "ZZZ", "placeholder")
    monkeypatch.setitem(config.FX_RATES_PROVENANCE, "ZZZ", "made up for a test")
    known, _, _, usd, note = convert_taxes(10000, "ZZZ")
    assert known is True
    assert usd == pytest.approx(200.0)
    assert "PLACEHOLDER" in note


def test_missing_taxes_are_unknown_not_zero():
    known, amount, cur, usd, note = convert_taxes(None, "USD")
    assert known is False
    assert "not zero" in note.lower()


def test_a_missing_taxes_currency_is_unknown_not_assumed_usd():
    known, amount, cur, usd, note = convert_taxes(4460, "")
    assert known is False
    assert amount == pytest.approx(44.60)
    assert "no TaxesCurrency" in note


# ---------------------------------------------------------------------------
# (4) Non-Raw fields only, for availability and for price
# ---------------------------------------------------------------------------


def test_the_raw_fields_disagree_with_the_clean_ones_in_the_real_row(real_row):
    """This is the whole reason for the rule, stated as an assertion."""
    assert real_row["JAvailable"] is False
    assert real_row["JAvailableRaw"] is True
    assert real_row["JMileageCost"] == "0"
    assert real_row["JMileageCostRaw"] == 470500


def test_the_phantom_470500_never_reaches_an_award(real_row):
    awards = parse_availability_row(real_row)
    assert all(a.cost != 470500 for a in awards)
    assert all(a.cash_component != 5590 for a in awards)


def test_raw_values_are_retained_but_only_as_labelled_diagnostics(real_row):
    award = parse_availability_row(real_row)[0]
    assert award.raw_diagnostics["YMileageCostRaw"] == 50000
    assert "never scored" in award.raw_diagnostics["WARNING"]


def test_availability_follows_the_clean_flag_not_the_raw_flag(real_row):
    row = json.loads(json.dumps(real_row))
    row["WAvailableRaw"] = True
    row["WMileageCostRaw"] = 999999
    awards = parse_availability_row(row)
    assert [a.award_type for a in awards] == ["Y"]


def test_an_available_cabin_with_no_clean_price_is_skipped_not_zeroed(real_row):
    """
    Emitting it at cost 0 would make an award look free; falling back to the Raw
    price would make up a number. Neither. It is dropped and counted.
    """
    row = json.loads(json.dumps(real_row))
    row["JAvailable"] = True  # JMileageCost stays "0", JMileageCostRaw is 470500
    awards = parse_availability_row(row)
    assert [a.award_type for a in awards] == ["Y"]


# ---------------------------------------------------------------------------
# (5) Airlines is a LIST of possible operating carriers
# ---------------------------------------------------------------------------


def test_airlines_parses_to_a_list(real_row):
    assert parse_carriers(real_row["YAirlines"]) == ["AC", "LH", "UA", "VL"]
    assert parse_carriers("") == []
    assert parse_carriers("AC") == ["AC"]
    assert parse_carriers("AC, AC , ac") == ["AC"]


def test_a_multi_carrier_list_is_not_known_metal(real_row):
    award = parse_availability_row(real_row)[0]
    assert award.candidate_carriers == ["AC", "LH", "UA", "VL"]
    assert award.carrier_source == "seats_aero_ambiguous"
    assert award.has_known_metal is False


def test_the_first_carrier_is_not_silently_promoted_to_the_metal(real_row):
    award = parse_availability_row(real_row)[0]
    assert award.airline != "AC"
    assert award.airline == "AC, LH, UA, VL"


def test_a_single_carrier_list_is_known_metal(real_row):
    row = json.loads(json.dumps(real_row))
    row["YAirlines"] = "AC"
    award = parse_availability_row(row)[0]
    assert award.has_known_metal is True
    assert award.carrier_source == "seats_aero_single"


def test_ambiguous_metal_resolves_when_all_carriers_agree(real_row):
    """
    Aeroplan's no-surcharge policy is program-wide, so AC / LH / UA / VL all
    resolve to the same $0. The ambiguity genuinely does not change the answer,
    and the result says so rather than pretending a carrier was chosen.
    """
    table = SurchargeTable()
    award = parse_availability_row(real_row)[0]
    est = table.resolve_ambiguous_metal(
        award.program, award.candidate_carriers, award.route_region,
        award.award_type, "US", is_round_trip=False,
    )
    assert est.is_known
    assert est.amount_point == 0.0
    assert "AMBIGUOUS" in est.notes
    assert "No carrier was chosen" in est.notes


def test_ambiguous_metal_is_unknown_when_the_carriers_disagree():
    """
    Flying Blue has a modeled row for AF and KL metal and nothing for DL. Which
    aeroplane you get changes the answer, so the answer is unknown - with a
    break-even, per the v1 rules - not the AF figure.
    """
    table = SurchargeTable()
    est = table.resolve_ambiguous_metal(
        "Air France-KLM Flying Blue", ["AF", "DL"], "NA-EU", "J", "US",
    )
    assert not est.is_known
    assert "AMBIGUOUS" in est.notes
    assert "invent" in est.notes


def test_ambiguous_metal_with_no_rule_for_any_carrier_is_unknown():
    table = SurchargeTable()
    est = table.resolve_ambiguous_metal(
        "Virgin Atlantic Flying Club", ["DL", "AF"], "NA-EU", "J", "US",
    )
    assert not est.is_known
    assert "not zero" in est.notes.lower()


def test_a_single_carrier_resolves_normally():
    table = SurchargeTable()
    est = table.resolve_ambiguous_metal(
        "British Airways Executive Club", ["BA"], "NA-EU", "J", "GB",
    )
    assert est.is_known
    assert est.amount_point == 900


# ---------------------------------------------------------------------------
# Route metadata
# ---------------------------------------------------------------------------


def test_route_regions_feed_the_surcharge_region_tier(real_row):
    award = parse_availability_row(real_row)[0]
    assert award.origin_region == "North America"
    assert award.destination_region == "Europe"
    assert award.route_region == "NA-EU"


def test_an_unrecognised_region_label_falls_back_to_airports_csv(real_row):
    row = json.loads(json.dumps(real_row))
    row["Route"]["OriginRegion"] = "Somewhere Else"
    award = parse_availability_row(row)[0]
    assert award.route_region == "NA-EU"  # derived from SFO/MAD, not defaulted


def test_route_distance_is_carried(real_row):
    award = parse_availability_row(real_row)[0]
    assert award.distance_miles == 5805
    assert award.route == "SFO-MAD"


# ---------------------------------------------------------------------------
# Defensive parsing
# ---------------------------------------------------------------------------


def test_mileage_cost_is_a_string_in_the_real_response(real_row):
    assert isinstance(real_row["YMileageCost"], str)
    assert isinstance(real_row["YMileageCostRaw"], int)
    assert parse_availability_row(real_row)[0].cost == 50000


def test_an_integer_mileage_cost_also_parses(real_row):
    row = json.loads(json.dumps(real_row))
    row["YMileageCost"] = 50000
    assert parse_availability_row(row)[0].cost == 50000


def test_a_junk_mileage_cost_drops_the_cabin_rather_than_scoring_it(real_row):
    row = json.loads(json.dumps(real_row))
    row["YMileageCost"] = "n/a"
    assert parse_availability_row(row) == []


def test_a_row_with_no_usable_date_is_dropped(real_row):
    row = json.loads(json.dumps(real_row))
    row["Date"] = ""
    row["ParsedDate"] = ""
    assert parse_availability_row(row) == []


def test_a_row_with_no_route_object_does_not_crash():
    assert parse_availability_row({"Date": "2027-01-15", "YAvailable": True}) == []


# ---------------------------------------------------------------------------
# search(), pagination, cache, errors
# ---------------------------------------------------------------------------


@patch("src.seats_client.requests.get")
def test_search_parses_the_real_response(mock_get, seats_client, real_response):
    mock_get.return_value = _mock_get(real_response)
    awards = seats_client.search(
        "SFO", "MAD", DateRange(date(2027, 1, 15), date(2027, 2, 14))
    )
    assert len(awards) == 1
    a = awards[0]
    assert (a.program, a.award_type, a.cost, a.seats_available) == (
        "Air Canada Aeroplan", "Y", 50000, 9
    )
    assert a.date == date(2027, 1, 15)


@patch("src.seats_client.requests.get")
def test_the_old_parser_would_have_returned_nothing(mock_get, seats_client, real_response):
    """
    The regression this whole change exists for: a response full of data that
    produced zero awards, reported as "no award availability".
    """
    row = real_response["data"][0]
    assert row.get("cost") is None
    assert row.get("taxes") is None
    assert row.get("Carriers") is None
    mock_get.return_value = _mock_get(real_response)
    assert seats_client.search(
        "SFO", "MAD", DateRange(date(2027, 1, 15), date(2027, 2, 14))
    ) != []


@patch("src.seats_client.requests.get")
def test_a_single_page_response_says_it_took_the_single_page_path(
    mock_get, seats_client, real_response
):
    mock_get.return_value = _mock_get(real_response)
    seats_client.search("SFO", "MAD", DateRange(date(2027, 1, 15), date(2027, 2, 14)))
    assert "single page" in seats_client.last_pagination_note
    assert "NOT been ruled out" in seats_client.last_pagination_note
    assert seats_client.last_pages_fetched == 1


@patch("src.seats_client.requests.get")
def test_a_cursor_is_followed_to_the_end(mock_get, seats_client, real_row):
    page1 = {"data": [real_row], "hasMore": True, "cursor": "abc"}
    row2 = json.loads(json.dumps(real_row))
    row2["Date"] = "2027-01-16"
    page2 = {"data": [row2], "hasMore": False}
    mock_get.side_effect = [_mock_get(page1), _mock_get(page2)]

    awards = seats_client.search(
        "SFO", "MAD", DateRange(date(2027, 1, 15), date(2027, 2, 14))
    )
    assert len(awards) == 2
    assert {a.date for a in awards} == {date(2027, 1, 15), date(2027, 1, 16)}
    assert mock_get.call_count == 2
    assert mock_get.call_args_list[1].kwargs["params"]["cursor"] == "abc"
    assert "2 pages" in seats_client.last_pagination_note


@patch("src.seats_client.requests.get")
def test_page_one_is_never_returned_silently_as_the_whole_result(
    mock_get, seats_client, real_row
):
    """
    hasMore=true with no cursor and no skip: we cannot ask for page two. The
    result must be labelled incomplete rather than handed back as the total.
    """
    mock_get.return_value = _mock_get({"data": [real_row], "hasMore": True})
    seats_client.search("SFO", "MAD", DateRange(date(2027, 1, 15), date(2027, 2, 14)))
    assert seats_client.last_pagination_note != ""
    assert "hasMore" in seats_client.last_pagination_note


@patch("src.seats_client.requests.get")
def test_pagination_respects_the_daily_call_cap(mock_get, seats_client, real_row):
    mock_get.return_value = _mock_get({"data": [real_row], "hasMore": True, "cursor": "x"})
    SeatsClient._calls_date = date.today()
    SeatsClient._calls_made = SeatsClient.DAILY_CALL_CAP - 1
    seats_client.search("SFO", "MAD", DateRange(date(2027, 1, 15), date(2027, 2, 14)))
    assert mock_get.call_count == 1
    assert "budget is exhausted" in seats_client.last_pagination_note
    assert "INCOMPLETE" in seats_client.last_pagination_note


@patch("src.seats_client.requests.get")
def test_pagination_stops_at_the_page_cap_and_says_so(mock_get, seats_client, real_row):
    mock_get.return_value = _mock_get({"data": [real_row], "hasMore": True, "cursor": "x"})
    seats_client.search("SFO", "MAD", DateRange(date(2027, 1, 15), date(2027, 2, 14)))
    assert mock_get.call_count == SeatsClient.MAX_PAGES
    assert "INCOMPLETE" in seats_client.last_pagination_note


@patch("src.seats_client.requests.get")
def test_search_filters_by_any_possible_carrier(mock_get, seats_client, real_response):
    mock_get.return_value = _mock_get(real_response)
    dr = DateRange(date(2027, 1, 15), date(2027, 2, 14))
    assert seats_client.search("SFO", "MAD", dr, airlines=["UA"]) != []
    seats_client.clear_cache()
    mock_get.return_value = _mock_get(real_response)
    assert seats_client.search("SFO", "MAD", dr, airlines=["BA"]) == []


@patch("src.seats_client.requests.get")
def test_search_caches_results(mock_get, seats_client, real_response):
    mock_get.return_value = _mock_get(real_response)
    dr = DateRange(date(2027, 1, 15), date(2027, 2, 14))
    first = seats_client.search("SFO", "MAD", dr)
    assert mock_get.call_count == 1
    second = seats_client.search("SFO", "MAD", dr)
    assert mock_get.call_count == 1
    assert first == second
    assert "cache" in seats_client.last_pagination_note


@patch("src.seats_client.requests.get")
def test_search_api_error_handling(mock_get, seats_client):
    mock_get.side_effect = requests.RequestException("Connection timeout")
    with pytest.raises(RuntimeError):
        seats_client.search("SFO", "MAD", DateRange(date(2027, 1, 15), date(2027, 2, 14)))
    assert seats_client.last_error and "API error" in seats_client.last_error


def test_clear_cache(seats_client):
    seats_client.CACHE["test_key"] = ["dummy_data"]
    assert len(seats_client.CACHE) > 0
    seats_client.clear_cache()
    assert len(seats_client.CACHE) == 0


# ---------------------------------------------------------------------------
# The known answer: SFO->MAD, Aeroplan, 50k, and it still loses to $395 cash
# ---------------------------------------------------------------------------


def test_the_sfo_mad_leg_reproduces_the_google_badge(real_row):
    """
    Tsuki's Google Flights badge for this route said "Air Canada 50k pts". The
    real Seats.aero data CONFIRMS the badge on this one leg - which is a finding
    about the badge, not a general licence to trust badges.
    """
    award = parse_availability_row(real_row)[0]
    assert award.program == "Air Canada Aeroplan"
    assert award.cost == 50000
    assert award.award_type == "Y"
    assert award.seats_available == 9
    assert award.cash_component_source_amount == pytest.approx(44.60)
    assert award.cash_component_currency == "CAD"


def test_the_confirmed_aeroplan_award_still_loses_to_395_cash(real_row):
    """
    50,000 UR at the neutral 1cpp yardstick is $500 against a $395 cash fare.
    Confirming the availability does not change the verdict, and the margin is
    wide enough that the unconvertible CAD 44.60 tax cannot close it from either
    direction: the points side only gets MORE expensive once taxes are known.
    """
    award = parse_availability_row(real_row)[0]
    points_score_usd = award.cost * 0.01  # 1:1 UR -> Aeroplan, 1cpp
    assert points_score_usd == 500.0
    assert points_score_usd > 395.0
    # Aeroplan's surcharge is a real, program-wide zero - so the $500 is not
    # flattered by a missing surcharge. It loses on the points price alone.
    table = SurchargeTable()
    est = table.resolve_ambiguous_metal(
        award.program, award.candidate_carriers, award.route_region, "Y", "US",
        is_round_trip=False,
    )
    assert est.is_known and est.amount_point == 0.0
    assert points_score_usd + est.amount_point > 395.0


# ---------------------------------------------------------------------------
# The optimizer must not treat an unconvertible tax as $0
# ---------------------------------------------------------------------------


@patch("src.seats_client.requests.get")
def test_the_optimizer_now_scores_the_cad_tax_instead_of_bounding_it(
    mock_get, seats_client, real_response
):
    """
    CHANGED AT v3 STEP 0 (was
    test_the_optimizer_marks_an_unconvertible_tax_as_a_lower_bound).

    NOT LISTED IN THE PLAN'S SECTION 8, but it is the visible half of Step 0's
    stated purpose: "the captured CAD 44.60 becomes scoreable at $32.36 instead
    of being carried as an unknown, so the SFO-MAD leg goes from 'unconvertible
    tax, verdict robust from one direction only' to a fully priced
    $532.36-vs-$395 PAY CASH."

    The lower-bound machinery itself is NOT removed and is still exercised by
    test_an_award_with_no_tax_figure_is_still_a_lower_bound below - it is simply
    no longer reachable via CAD, because CAD now has a rate.
    """
    from src.models import Trip
    from src.optimizer import optimize
    from src.ratio_manager import RatioManager

    mock_get.return_value = _mock_get(real_response)
    trip = Trip(
        origin="SFO",
        destination="MAD",
        date_range=DateRange(date(2027, 1, 15), date(2027, 2, 14)),
        balances={"UR": 200000},
        cards_held=["Chase Sapphire Reserve"],
    )
    rm = RatioManager(ROOT / "data" / "ratios.csv", ROOT / "data" / "bonuses.csv")
    results = optimize(trip, seats_client, rm)
    assert results, "the real response must now produce a strategy"
    s = results[0]
    assert s.award.program == "Air Canada Aeroplan"
    assert s.points_cost == 50000
    assert s.cash_cost_known is True
    assert s.is_lower_bound is False
    assert s.cash_cost == pytest.approx(32.36, abs=0.01)
    # THE FULLY PRICED FIGURE: 50,000 UR at 1cpp + $32.36 of real taxes.
    assert s.total_value == pytest.approx(532.36, abs=0.01)
    # And it still loses to the $395 cash fare - now from both directions, not
    # just from the one the missing tax left open.
    assert s.total_value > 395.0


@patch("src.seats_client.requests.get")
def test_an_award_with_no_tax_figure_is_still_a_lower_bound(
    mock_get, seats_client, real_response
):
    """
    The lower-bound path, re-pinned on the case that keeps it reachable: a
    response that carries no tax figure at all. Unknown is still not zero, and
    a total built on it is still only a floor.
    """
    import copy

    from src.models import Trip
    from src.optimizer import optimize
    from src.ratio_manager import RatioManager

    payload = copy.deepcopy(real_response)
    payload["data"][0].pop("YTotalTaxes")
    payload["data"][0].pop("TaxesCurrency")
    mock_get.return_value = _mock_get(payload)
    trip = Trip(
        origin="SFO",
        destination="MAD",
        date_range=DateRange(date(2027, 1, 15), date(2027, 2, 14)),
        balances={"UR": 200000},
        cards_held=["Chase Sapphire Reserve"],
    )
    rm = RatioManager(ROOT / "data" / "ratios.csv", ROOT / "data" / "bonuses.csv")
    seats_client.clear_cache()
    results = optimize(trip, seats_client, rm)
    assert results
    s = results[0]
    assert s.cash_cost_known is False
    assert s.is_lower_bound is True
    assert s.cash_cost == 0.0, "an unknown contributes nothing, and is FLAGGED"
    assert "not zero" in s.cash_cost_note.lower()
