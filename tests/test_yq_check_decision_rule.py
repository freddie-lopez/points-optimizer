"""
Manager review, must-fix 2: the yq-check rule is stated by the site's TOTAL.

The block prints the row figure, the modelled band for the airline the lookup
names, and their sum; the rule is: site total ~ row figure -> includes_yq; site
total ~ row figure + band -> excludes_yq; anything else -> inconclusive, record
nothing. It tells Tsuki to confirm on the site that the flight is OPERATED by
that airline (Virgin Atlantic, not Delta), the record asks the same, and the
loader refuses a record that does not answer "yes".
"""
from datetime import date
from io import StringIO
from unittest.mock import patch

import pytest
from rich.console import Console

from src import trips_tools, yq_inclusion
from src.seats_client import SeatsClient
from src.yq_inclusion import YqInclusionError
from tests import _trips_payloads as tp
from tests._trips_label_state import unverified_constants  # noqa: F401
from tests.test_trips_tools import Stub, yq_args
from tests.test_yq_airline_scope import HEADER, body

TODAY = date(2026, 9, 11)


@pytest.fixture(autouse=True)
def fresh():
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient.reset_call_budget()
    yield
    SeatsClient.reset_call_budget()


def run_yq(tmp_path, trips=None):
    buf = StringIO()
    with patch("src.seats_client.requests.get", side_effect=Stub(trips=trips)):
        code = trips_tools.main(
            yq_args(tmp_path), read=lambda _: "y", console=Console(file=buf, width=400), today=TODAY
        )
    return code, " ".join(buf.getvalue().split()), next((tmp_path / "records").glob("*.md")).read_text()


def test_the_block_prints_the_band_and_states_the_rule_by_the_total(tmp_path):
    code, out, _ = run_yq(tmp_path)
    assert code == 0
    assert "modelled carrier surcharge band: $200-$350 (pt $275) one way (VS metal, cabin J)" in out
    assert "row figure + band: $809.30-$959.30" in out
    assert "OPERATED BY VS itself (Virgin Atlantic for this program), not by a partner such as Delta" in out
    assert "TOTAL of taxes, fees and carrier-imposed charges - one combined figure, or its lines added up" in out
    assert "Site total about equal to the row figure ($609.30): includes_yq" in out
    assert "row figure plus the band ($809.30-$959.30 (row $609.30 + band)): excludes_yq" in out
    assert "Anything else is inconclusive: record nothing" in out
    # The old rule, which sent a combined total with no separate line to "record nothing", is gone.
    assert "separate carrier-charge line" not in out


def test_the_record_asks_for_the_operator_and_the_total(tmp_path):
    _, _, record = run_yq(tmp_path)
    assert "about the row figure plus the modelled band (both above) is excludes_yq" in record
    assert "- modelled carrier surcharge band: $200-$350 (pt $275) one way" in record
    assert "- row figure + band: $809.30-$959.30" in record
    assert (
        "- the site shows this flight operated by VS itself, not a codeshare partner "
        "(yes / no): ____" in record
    )
    assert "- total taxes, fees and carrier-imposed charges for ONE adult, as the site shows it" in record
    assert "separate carrier-imposed charge line" not in record


@pytest.mark.usefixtures("unverified_constants")
def test_a_band_that_cannot_separate_the_answers_says_so(tmp_path):
    _, out, _ = run_yq(tmp_path, trips=tp.payload([tp.vs_direct("DL41")]))
    assert "NONE MODELLED for DL metal, so the site total cannot tell includes from excludes" in out
    assert "no band: this check cannot show excludes_yq" in out


def _load(tmp_path, text):
    d = tmp_path / "docs" / "yq-checks"
    d.mkdir(parents=True, exist_ok=True)
    (d / "a.md").write_text(text)
    csv = tmp_path / "yq.csv"
    csv.write_text(HEADER + "\nvirginatlantic,VS,includes_yq,2026-09-10,docs/yq-checks/a.md,x\n")
    return yq_inclusion.load(csv, today=TODAY, root=tmp_path)


OPERATED = "- the site shows this flight operated by VS itself, not a codeshare partner (yes / no): "


@pytest.mark.parametrize(
    "answer,needle",
    [("no", "does not confirm"), ("not sure", "does not confirm"), ("Delta", "does not confirm")],
)
def test_a_record_that_does_not_confirm_the_operator_is_refused(tmp_path, answer, needle):
    with pytest.raises(YqInclusionError, match=needle):
        _load(tmp_path, body().replace(OPERATED + "yes", OPERATED + answer))


def test_a_record_without_the_operator_line_is_refused(tmp_path):
    with pytest.raises(YqInclusionError, match="0 'the site shows this flight operated by' lines"):
        _load(tmp_path, body().replace(OPERATED + "yes\n", ""))


def test_a_record_asking_about_another_operator_is_refused(tmp_path):
    with pytest.raises(YqInclusionError, match="operated by DL, and the row says VS"):
        _load(tmp_path, body().replace(OPERATED, OPERATED.replace("by VS", "by DL")))


def test_a_confirmed_record_loads(tmp_path):
    assert _load(tmp_path, body())[("virginatlantic", "VS")].includes
