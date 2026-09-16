"""
A. Data integrity: nothing hand-typed, every committed file pinned to its
generator, the generators deterministic, the empty hub file exactly the
plan's shape, no engine/CLI diff, goldens untouched.
"""
import csv
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT))
from src import map_tools, regions  # noqa: E402
from tools import build_land_path, filter_airportsdata  # noqa: E402

BASE = "56742af"
LAND = ROOT / "src" / "ui" / "static" / "land.json"
HUBS = ROOT / "data" / "hubs.json"
IATA_CSV = ROOT / "data" / "airportsdata_iata.csv"
FULL_CSV = ROOT / "docs" / "design" / "map-ref" / "airportsdata-20260905-airports.csv"


def git(*args):
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True).stdout


def test_A1_the_shipped_hubs_file_is_empty_with_the_plans_provenance_shape():
    doc = json.loads(HUBS.read_text(encoding="utf-8"))
    assert doc["hubs"] == []
    assert map_tools.hubs_file_problems(doc) == []
    meta = doc["_meta"]
    assert meta["captured_at"] is None and meta["captured_by"] is None
    assert meta["synthetic"] is False and meta["key_redacted"] is True
    assert meta["note"] == map_tools.EMPTY_NOTE
    assert "hand" not in json.dumps(doc).lower()
    # byte-identical to what the tool itself calls the empty document
    assert HUBS.read_text(encoding="utf-8") == map_tools.render_document(map_tools.empty_document())


def test_A2_land_json_matches_the_generator_twice_and_check_mode_holds(tmp_path):
    a = build_land_path.render(build_land_path.build())
    b = build_land_path.render(build_land_path.build())
    assert a == b, "the generator is not deterministic"
    assert LAND.read_text(encoding="utf-8") == a
    assert build_land_path.main(["--check"]) == 0
    copy = tmp_path / "land.json"
    copy.write_text(a[:500] + ("2" if a[500] != "2" else "3") + a[501:], encoding="utf-8")
    assert build_land_path.main(["--check", "--out", str(copy)]) == 1


def test_A3_the_filtered_csv_is_the_filter_output_and_the_filter_is_idempotent(tmp_path):
    if not FULL_CSV.is_file():
        pytest.skip("the 3 MB source CSV is not on this machine (gitignored)")
    with open(FULL_CSV, newline="", encoding="utf-8") as f:
        once = filter_airportsdata.render(filter_airportsdata.filter_rows(f))
    assert IATA_CSV.read_text(encoding="utf-8") == once
    # idempotent: filtering the filtered file gives the same bytes
    import io
    again = filter_airportsdata.render(filter_airportsdata.filter_rows(io.StringIO(once)))
    assert again == once


def test_A4_the_filtered_csv_digest_the_test_pins_is_the_committed_file():
    digest = hashlib.sha256(IATA_CSV.read_bytes()).hexdigest()
    src = (ROOT / "tests" / "test_map_assets.py").read_text(encoding="utf-8")
    assert digest in src, "tests/test_map_assets.py does not pin the committed CSV's digest"


def test_A5_no_hand_typed_airport_or_coordinate_in_src_or_data():
    """Every 3-letter upper-case string literal in the new code must be a
    placeholder the old code already had (SFO/MAD input placeholders) or a
    regex/format token, never a hub list; no decimal lat/lon pairs in src/."""
    for path in (ROOT / "src" / "ui" / "static" / "map.js", ROOT / "src" / "map_tools.py",
                 ROOT / "src" / "ui" / "server.py", ROOT / "tools" / "build_land_path.py",
                 ROOT / "tools" / "filter_airportsdata.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\"[A-Z]{3}\"\s*,\s*\"[A-Z]{3}\"", text), path
        assert not re.search(r"-?\d{1,2}\.\d{4,}\s*,\s*-?\d{1,3}\.\d{4,}", text), path
    app = (ROOT / "src" / "ui" / "static" / "app.js").read_text(encoding="utf-8")
    base_app = git("show", f"{BASE}:src/ui/static/app.js")
    new_codes = set(re.findall(r"\"([A-Z]{3})\"", app)) - set(re.findall(r"\"([A-Z]{3})\"", base_app))
    assert new_codes == set(), f"new 3-letter literals in app.js: {new_codes}"


def test_A6_no_engine_cli_or_golden_diff_since_the_base():
    """Re-pinned again at ecab878 (search->trip fix round 1, F1 in
    engine.py). A pin, not a regression. Earlier:
    Re-pinned by the tester at d6be134 (search->trip round; the plan
    docs/plans/search-to-trip.md 6 step 7 names this pin): src/ui/api.py and
    src/ui/engine.py carry that round's delete routes and are pinned to the
    coder's head d6be134; every other path is still byte-identical to the
    map round's base. A pin, not a regression."""
    paths = ["src/main.py", "src/formatter.py", "src/seats_client.py", "src/optimizer.py",
             "src/live_trip.py", "src/trip_builder.py", "src/regions.py",
             "src/ui/serialize.py", "data/airports.csv",
             "tests/fixtures/cli_golden", "tests/test_cli_golden.py"]
    out = git("diff", f"{BASE}..HEAD", "--stat", "--", *paths)
    assert out.strip() == "", out
    out = git("diff", "ecab878..HEAD", "--stat", "--", "src/ui/api.py", "src/ui/engine.py")
    assert out.strip() == "", out
    out = git("diff", "d6be134..HEAD", "--stat", "--", "src/ui/api.py")
    assert out.strip() == "", out


def test_A7_the_parity_suite_and_goldens_are_untouched():
    """Re-based at dad93cb (search->trip round, docs/plans/search-to-trip.md 6
    step 7: H1/E5/J6 re-pinned by the tester in dad93cb, the only edit to the
    restyle tree since 7a8310c - test_r_g_fonts_security_docs.py and
    test_r_e_keychip.py, nothing under ui-probes). A pin, not a regression.
    Earlier: re-based at 7a8310c: the goldens are untouched since the base; the
    two older probe trees have had exactly two edits, both the tester's own:
    be0d27b (the pin refresh - P22/P22b, E5, H1, H2, J6, C2, each a
    plan-invalidated pin, see the report's round-3 section) and 7a8310c
    (J6 re-pinned for the round-4 .catch hunk). Nothing else, by anyone."""
    out = git("diff", f"{BASE}..HEAD", "--stat", "--", "tests/fixtures/cli_golden")
    assert out.strip() == "", out
    out = git("diff", "dad93cb..HEAD", "--stat", "--", "docs/test-reports/ui-probes",
              "docs/test-reports/ui-restyle-probes")
    assert out.strip() == "", out
    out = git("diff", "7a8310c..dad93cb", "--name-only", "--", "docs/test-reports/ui-probes",
              "docs/test-reports/ui-restyle-probes")
    assert sorted(out.split()) == ["docs/test-reports/ui-restyle-probes/test_r_e_keychip.py",
                                   "docs/test-reports/ui-restyle-probes/test_r_g_fonts_security_docs.py"], out
    out = git("diff", "be0d27b..7a8310c", "--name-only", "--", "docs/test-reports/ui-probes",
              "docs/test-reports/ui-restyle-probes")
    assert out.split() == ["docs/test-reports/ui-restyle-probes/test_r_g_fonts_security_docs.py"], out
    out = git("diff", f"{BASE}..be0d27b", "--name-only", "--", "docs/test-reports/ui-probes",
              "docs/test-reports/ui-restyle-probes")
    assert sorted(out.split()) == [
        "docs/test-reports/ui-probes/test_ui_p_mac.py",
        "docs/test-reports/ui-restyle-probes/test_r_c_colour.py",
        "docs/test-reports/ui-restyle-probes/test_r_e_keychip.py",
        "docs/test-reports/ui-restyle-probes/test_r_g_fonts_security_docs.py"], out
    assert len(list((ROOT / "tests" / "fixtures" / "cli_golden").glob("G*.txt"))) == 14


def test_A8_searchable_in_the_synthetic_fixture_is_the_airports_csv_flag():
    doc = json.loads((ROOT / "tests" / "fixtures" / "ui" / "hubs_synthetic.json").read_text())
    known = set(regions.known_airports())
    assert doc["_meta"]["synthetic"] is True
    for h in doc["hubs"]:
        assert h["searchable"] == (h["iata"] in known), h["iata"]
    # and the fixture never reaches data/
    assert not (ROOT / "data" / "hubs_synthetic.json").exists()
    assert json.loads(HUBS.read_text())["hubs"] == []


def test_A9_every_iata_csv_row_has_numeric_in_range_coordinates():
    with open(IATA_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 7884
    for r in rows:
        assert re.fullmatch(r"[A-Z0-9]{3}", r["iata"]), r
        lat, lon = float(r["lat"]), float(r["lon"])
        assert -90 <= lat <= 90 and -180 <= lon <= 180, r
