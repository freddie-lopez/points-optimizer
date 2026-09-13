"""
Fix round 1, Tester finding 20: `yq-check` compares only a tax figure scoring
itself would use.

A negative or unconvertible row figure (the parser's "not known"), and a figure
below the UK Air Passenger Duty owed on a UK departure in that cabin, are
refused before the trips call, and no record is written that could back a
verdict.
"""
import pytest

from src.seats_client import SeatsClient
from tests.test_metal_end_to_end import vs_row
from tests.test_trips_tools import Stub, run, yq_args


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()


@pytest.mark.parametrize(
    "taxes,needle",
    [
        (-100, "scoring does not believe"),
        ("abc", "scoring does not believe"),
        (500, "UK Air Passenger Duty"),
        (1, "UK Air Passenger Duty"),
    ],
    ids=["negative", "unconvertible", "GBP-5-below-APD", "one-cent"],
)
def test_an_untrusted_row_figure_is_refused_before_the_trips_call(tmp_path, taxes, needle):
    row = vs_row()
    row["JTotalTaxes"] = taxes
    code, out, stub = run(yq_args(tmp_path), stub=Stub(row=row))
    assert code == 1
    assert needle in out
    assert "No trips call was made" in out
    assert [c for c in stub.calls if "/trips/" in c] == []
    assert not (tmp_path / "records").exists() or not list((tmp_path / "records").iterdir())


def test_a_figure_scoring_believes_still_goes_through(tmp_path):
    code, out, stub = run(yq_args(tmp_path))
    assert code == 0, out
    assert len([c for c in stub.calls if "/trips/" in c]) == 1
    assert list((tmp_path / "records").glob("*.md"))
