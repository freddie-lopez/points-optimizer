"""
I. D29 against MASTER's own code, and a handful of smaller attacks: rich markup
in API strings, MixedCabinPct under min_cabin_pct=100, and a yq-check record
whose own verdict contradicts the CSV row it backs.
"""
import os
import subprocess
import sys
import tarfile
import io
from datetime import date
from pathlib import Path

import pytest

from conftest import BASE, B4_VS, ROOT, Stub, evaluate, flat, run_cli, vs_b4_rows, vs_itinerary  # noqa: F401
from src import seats_trips, yq_inclusion
from src.yq_inclusion import YqInclusionError
from tests import _trips_payloads as tp

PY = str(ROOT / ".venv" / "bin" / "python")

SCRIPT = r'''
import copy, json, sys
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch
root = Path(sys.argv[1]); sys.path.insert(0, str(root))
from rich.console import Console
from src.formatter import print_leg_detail, print_leg_results, print_live_leg_detail, print_trip_totals, print_alternatives
from src.live_trip import LiveOptions, annotate_live_verdicts, apply_live
from src.optimizer import evaluate_trip, trip_totals
from src.ratio_manager import RatioManager
from src.seats_client import SeatsClient
from src.trip_loader import load_trip_fixture
from src.wallet import Wallet
REAL = json.loads((root / "tests/fixtures/seats_aero/sfo_mad_real.json").read_text())
ISO = {("SFO","MAD"):"2027-01-15",("MAD","AMS"):"2027-01-19",("AMS","LHR"):"2027-01-23",("LHR","SFO"):"2027-01-27"}
def row(o, d, airlines):
    r = copy.deepcopy(REAL["data"][0]); iso = ISO[(o, d)]
    r["Route"].update(OriginAirport=o, DestinationAirport=d); r["Date"] = iso; r["ParsedDate"] = iso + "T00:00:00Z"
    r["ID"] = (o + d + "x" * 30)[:27]
    if (o, d) == ("LHR", "SFO"):
        r["Route"].update(Source="virginatlantic", OriginRegion="Europe", DestinationRegion="North America")
        r.update(YAvailable=False, JAvailable=True, JMileageCost="60000", JTotalTaxes=45000,
                 TaxesCurrency="GBP", JAirlines=airlines, JRemainingSeats=2)
    return r
def side(url, **kw):
    p = kw.get("params") or {}
    m = MagicMock(); m.status_code = 200; m.raise_for_status.return_value = None
    body = {"data": [row(p["origin_airport"], p["destination_airport"], sys.argv[2])]}
    m.json.return_value = body; m.text = json.dumps(body); return m
fx = load_trip_fixture(root / "tests/fixtures/trips/trip_b_europe.json")
with patch("src.seats_client.requests.get", side_effect=side):
    fx, _ = apply_live(fx, SeatsClient(api_key="k_legacy"), LiveOptions(live=True, cache=None, trip_id=fx.id))
rm = RatioManager(root/"data/ratios.csv", root/"data/bonuses.csv", root/"data/programs.yaml")
w = Wallet(balances={"UR": 160000}, cards=["Chase Sapphire Preferred"])
res = annotate_live_verdicts(evaluate_trip(fx.legs, ratios_manager=rm, wallet=w, transfer_date=date(2026,9,15), today=date(2026,9,10)))
buf = StringIO(); c = Console(file=buf, width=250, no_color=True)
print_leg_results(res, console=c); print_leg_detail(res, console=c); print_live_leg_detail(res, console=c)
print_alternatives(res, console=c); print_trip_totals(trip_totals(res, w), console=c)
sys.stdout.write(buf.getvalue())
'''


def _master_tree(tmp_path):
    data = subprocess.run(["git", "archive", "a17497d", "src", "data", "tests/fixtures", "tests/_child_guard"],
                          cwd=ROOT, capture_output=True, check=True).stdout
    dst = tmp_path / "master"
    dst.mkdir()
    tarfile.open(fileobj=io.BytesIO(data)).extractall(dst)
    return dst


def _render(root, airlines, tmp_path):
    script = tmp_path / "render.py"
    script.write_text(SCRIPT)
    env = {k: v for k, v in os.environ.items()}
    env["PYTHONPATH"] = str(ROOT / "tests" / "_child_guard")
    env.pop("SEATS_AERO_KEY", None)
    r = subprocess.run([PY, str(script), str(root), airlines], cwd=root, env=env,
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr[-1500:]
    return r.stdout


# RE-TEST 4, coordinator ruling on D29: byte-identical to master EXCEPT the
# changelog sentences the Manager asked to reword (should-fix 5a). Exactly these
# (old -> new) pairs are normalised; any other difference fails.
REWORDED = [
    ("undercount, which is the v0 bug. Reported as a floor plus a break-even ",
     "undercount and turn an unknown charge into a saving that is not there. Reported as a floor plus a break-even "),
    ("The v1 modeled band 150-200 is KEPT", "The earlier modelled band 150-200 is KEPT"),
    ("matching the v1 modeled band.", "matching the earlier modelled band."),
    ("which matches the v1 modeled band exactly.", "which matches the earlier modelled band exactly."),
]


def _unwrap(text):
    return " ".join(text.split())


def reworded_only(master_text):
    out = _unwrap(master_text)
    for old, new in REWORDED:
        out = out.replace(old, new)
    return out


@pytest.mark.parametrize("airlines", ["VS, DL", "VS"])
def test_not_engaged_output_is_byte_identical_to_master(tmp_path, airlines):
    """D29 (as ruled in re-test 4): `trips_mode=None` renders master's output, bar the reworded sentences."""
    master = _render(_master_tree(tmp_path), airlines, tmp_path)
    branch = _render(ROOT, airlines, tmp_path)
    assert "which is the v0 bug" in _unwrap(master), "precondition: master carries the old sentence"
    assert _unwrap(branch) == reworded_only(master)


def test_rich_markup_in_api_strings_is_printed_literally_and_cannot_crash(capsys, monkeypatch):
    t = vs_itinerary(B4_VS)
    t["AvailabilitySegments"][0]["AircraftName"] = "[/red][bold]787-9[link=file:///etc]"
    stub = Stub(rows_for=vs_b4_rows(), trips={B4_VS: tp.payload([t])})
    code, out, fl, stub = run_cli(BASE + ["--allow-badge-fallback"], capsys, monkeypatch, stub=stub)
    assert "[/red][bold]787-9[link=file:///etc]" in out


def test_mixed_cabin_itineraries_under_min_cabin_pct_100_are_recorded_as_drift():
    """
    D16: "Under min_cabin_pct=100 its presence is also drift." The parser counts
    MixedCabinPct presence but writes a drift line only for the value 0; an
    itinerary with MixedCabinPct 30 - which min_cabin_pct=100 should have
    excluded server-side, so its presence says the request parameter was not
    honoured - adds nothing to `parsed.drift`.
    """
    parsed = seats_trips.parse_trips_payload(
        tp.payload([vs_itinerary(B4_VS, mixed=30)]), B4_VS, ("LHR", "SFO"))
    assert any("MixedCabinPct" in d for d in parsed.drift), parsed.drift


@pytest.mark.parametrize("record_says,why", [("excludes_yq", "says excludes_yq and the row says includes_yq"),
                                            ("inconclusive", "INCONCLUSIVE")])
def test_a_record_whose_own_verdict_disagrees_with_the_row_is_refused(tmp_path, record_says, why):
    """
    RE-TEST 4: 6-column CSV and the D1/must-fix-2 record lines, with the refusal
    REASON asserted (a 5-column CSV now fails on its header, which would make
    this probe pass for the wrong reason).
    """
    from conftest import yq_load, yq_record_body, yq_write_record

    ev = yq_write_record(tmp_path, yq_record_body(verdict=record_says))
    with pytest.raises(YqInclusionError, match=why):
        yq_load(tmp_path, f"virginatlantic,VS,includes_yq,2026-09-10,{ev},x")


def test_a_failed_archive_is_said_out_loud_and_the_line_still_prints(capsys, monkeypatch):
    """Disk full while archiving a trips response: the run goes on, and says so."""
    from src.response_cache import ResponseCache

    real_put = ResponseCache.put

    def put(self, key, request, pages, meta=None, **kw):
        if (meta or {}).get("endpoint") == "trips":
            raise OSError(28, "No space left on device")
        return real_put(self, key, request, pages, meta, **kw)

    monkeypatch.setattr(ResponseCache, "put", put)
    stub = Stub(rows_for=vs_b4_rows(), trips={B4_VS: tp.payload([vs_itinerary(B4_VS)])})
    code, out, fl, stub = run_cli(BASE + ["--allow-badge-fallback"], capsys, monkeypatch, stub=stub)
    assert "COULD NOT ARCHIVE the trips response" in fl
    assert "operating airline: VS by flight number" in fl


def test_a_lookup_that_was_paid_for_is_shown_somewhere():
    """
    B4 carries an Aeroplan award (program-policy $0, scoreable, chosen) and a
    Virgin Atlantic award (qualifies in auto, looked up, HTTP 404). A trips call
    is spent on the VS award; its outcome is printed nowhere but a banner count,
    because metal lines and the trip-block counters exist for the CHOSEN award
    only (D25). Plan section 7 item 2: "Attack a candidate whose status line is
    missing from the output entirely."
    """
    from conftest import aeroplan_row, row

    def rows_for(o, d, iso):
        if (o, d) == ("LHR", "SFO"):
            return [aeroplan_row(o, d, iso), row(rid=B4_VS, iso=iso)]
        return None

    stub = Stub(rows_for=rows_for, trips={B4_VS: (404, None)})
    results, totals, text, opts, fx = evaluate(stub)
    assert stub.trips_calls, "precondition: a call was spent on the VS award"
    assert "HTTP 404" in flat(text), "the paid-for lookup's outcome appears nowhere in the output"


def test_a_single_carrier_row_the_scorer_treats_as_known_metal_is_not_printed_as_nothing_known():
    """
    Before this feature a single-carrier policy award printed "metal: UA (source:
    seats_aero)" - the model's has_known_metal is True for it and the surcharge
    and alternatives use UA. The CLI now always engages the lookup, the award
    reads NOT_NEEDED_POLICY, and the legacy line is REPLACED by "Nothing is
    known about which airline flies it; the possible carriers are UA" - the
    output now contradicts the model it prints.
    """
    from conftest import row

    def rows_for(o, d, iso):
        if (o, d) == ("LHR", "SFO"):
            return [row(source="united", cost="60000", taxes=45000, currency="GBP", airlines="UA",
                        rid=B4_VS, iso=iso)]
        return None

    results, totals, text, opts, fx = evaluate(Stub(rows_for=rows_for))
    cand = results["B4"].best_points
    assert cand.has_known_metal and cand.operating_carrier == "UA"
    assert "Nothing is known about which airline flies it; the possible carriers are UA" not in flat(text)
