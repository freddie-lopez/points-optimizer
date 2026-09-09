"""
The UK APD table loads, validates, and is NOT wired into scoring.

That last one is a test, not a comment. `data/apd.csv` is seed data landed ahead
of v5, and a table that quietly started being applied would move every
UK-departure verdict in the tool - by about $138 on Trip B's LHR->SFO leg - with
no plan and no disclosure. So the "not wired" property is pinned here, and the
test that pins it is the thing v5 deletes on purpose.
"""
from datetime import date

import pytest

from src.apd import (
    APDTable,
    APDTableError,
    APDRate,
    load_apd_table,
)


@pytest.fixture
def table():
    return load_apd_table()


# ---------------------------------------------------------------------------
# The loader reads the file
# ---------------------------------------------------------------------------


def test_the_production_apd_table_loads_and_validates(table):
    assert len(table) == 16, "4 bands x 2 cabin classes x 2 rate periods"


def test_every_row_carries_a_source_and_a_verification_date(table):
    today = date.today()
    for r in table.rates:
        assert r.source.startswith("https://"), r.describe()
        assert "gov.uk" in r.source, "APD rates come from the statute, not a blog"
        assert r.verified_on <= today, r.describe()


def test_both_published_rate_periods_are_present(table):
    periods = {(r.effective_from, r.effective_to) for r in table.rates}
    assert (date(2026, 4, 1), date(2027, 3, 31)) in periods
    assert (date(2027, 4, 1), date(2099, 12, 31)) in periods
    assert len(periods) == 2


def test_the_band_b_economy_rate_is_the_one_trip_b_would_owe(table):
    """LHR->SFO in economy on 2027-01-27: band B, reduced rate, first period."""
    rate = table.lookup("GB", "B", "reduced", date(2027, 1, 27))
    assert rate is not None
    assert rate.rate == 102.00
    assert rate.currency == "GBP"


def test_the_rate_changes_on_the_first_of_april_2027(table):
    before = table.lookup("GB", "B", "reduced", date(2027, 3, 31))
    after = table.lookup("GB", "B", "reduced", date(2027, 4, 1))
    assert before.rate == 102.00
    assert after.rate == 105.33
    assert before is not after


def test_standard_rate_is_charged_on_every_cabin_above_the_lowest(table):
    reduced = table.lookup("GB", "B", "reduced", date(2027, 1, 27))
    standard = table.lookup("GB", "B", "standard", date(2027, 1, 27))
    assert standard.rate > reduced.rate
    assert reduced.cabins == ["Y"]
    assert standard.cabins == ["W", "J", "F"]


def test_band_c_has_no_upper_distance_bound(table):
    rate = table.lookup("GB", "C", "reduced", date(2027, 1, 27))
    assert rate.distance_min_miles == 5501
    assert rate.distance_max_miles is None, "blank means NO BOUND, not zero"


# ---------------------------------------------------------------------------
# A miss is a miss, never a zero
# ---------------------------------------------------------------------------


def test_a_lookup_with_no_matching_row_returns_none_not_zero(table):
    """
    The distinction the whole project turns on, applied to a tax.

    A departure from a country this table does not cover is a country whose
    departure tax is UNKNOWN. Returning 0.0 would say "nothing is owed", which
    is a claim about the world made from an absence of data.
    """
    assert table.lookup("US", "B", "reduced", date(2027, 1, 27)) is None
    assert table.lookup("GB", "B", "reduced", date(2020, 1, 1)) is None
    assert table.lookup("GB", "Z", "reduced", date(2027, 1, 27)) is None


# ---------------------------------------------------------------------------
# The validator refuses a table that would produce a confident wrong number
# ---------------------------------------------------------------------------


def _rate(**kw):
    base = dict(
        departure_country="GB",
        band="B",
        cabin_class="reduced",
        rate=102.0,
        currency="GBP",
        effective_from=date(2026, 4, 1),
        effective_to=date(2027, 3, 31),
        source="https://www.gov.uk/guidance/rates-and-allowances-for-air-passenger-duty",
        verified_on=date(2026, 9, 9),
    )
    base.update(kw)
    return APDRate(**base)


def test_an_empty_table_is_rejected():
    with pytest.raises(APDTableError, match="empty"):
        APDTable(rates=[]).validate()


def test_a_negative_rate_is_rejected():
    with pytest.raises(APDTableError, match="negative"):
        APDTable(rates=[_rate(rate=-1.0)]).validate()


def test_a_row_with_no_source_is_rejected():
    with pytest.raises(APDTableError, match="no source"):
        APDTable(rates=[_rate(source="")]).validate()


def test_an_unknown_cabin_class_is_rejected_rather_than_ignored():
    """HMRC has two rate names. A third value is a row we cannot read."""
    with pytest.raises(APDTableError, match="cabin_class"):
        APDTable(rates=[_rate(cabin_class="business")]).validate()


def test_a_backwards_date_window_is_rejected():
    with pytest.raises(APDTableError, match="after effective_to"):
        APDTable(
            rates=[_rate(effective_from=date(2027, 4, 1), effective_to=date(2026, 4, 1))]
        ).validate()


def test_overlapping_windows_are_rejected_so_row_order_cannot_decide():
    with pytest.raises(APDTableError, match="overlaps"):
        APDTable(
            rates=[
                _rate(),
                _rate(effective_from=date(2026, 6, 1), effective_to=date(2027, 6, 1)),
            ]
        ).validate()


def test_a_missing_file_is_an_error_not_an_empty_table(tmp_path):
    with pytest.raises(APDTableError, match="not found"):
        load_apd_table(tmp_path / "nope.csv")


# ---------------------------------------------------------------------------
# NOT WIRED IN. Delete this section in v5, deliberately.
# ---------------------------------------------------------------------------


def test_apd_is_not_conflated_with_the_carrier_surcharge_table():
    """
    Two tables, two files, two loaders, and nothing shared.

    A government departure tax and a carrier-imposed YQ are different
    quantities: United charges no YQ and that does not exempt anyone from APD.
    Merging them is how "United charges no surcharge" would silently become
    "this leg costs nothing in cash".
    """
    from src.surcharge import default_table

    surcharges = default_table()
    for rule in surcharges.rules:
        assert "APD" not in rule.program.upper()
        assert "PASSENGER DUTY" not in rule.notes.upper() or rule.amount_point > 0


def test_apd_does_not_yet_affect_any_score():
    """
    THE GUARD ON SEED DATA. `src.optimizer` must not import `src.apd` yet.

    Applying APD cuts Trip B's reported $202 saving on the LHR->SFO leg to about
    $64. That is a change that needs a plan and a disclosure, which is v5's job.
    When v5 does it, this test is the one to delete - on purpose, in the same
    commit, with the plan referenced.
    """
    import src.optimizer as optimizer

    src = open(optimizer.__file__, encoding="utf-8").read()
    assert "src.apd" not in src, (
        "optimizer now imports the APD table. If that is intentional, it is a "
        "v5 change: delete this test in the same commit as the plan that "
        "justifies it, and make sure the double-counting question against "
        "Seats.aero's TotalTaxes has been answered first."
    )
    assert "from src import apd" not in src
