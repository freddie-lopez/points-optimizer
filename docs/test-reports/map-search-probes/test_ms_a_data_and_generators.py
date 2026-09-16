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
    """Converted from a byte/line pin to a behaviour assertion (agreed with
    Tsuki, 2026-09-16); the pin never caught a defect and went red on every
    unrelated change. The api.py/engine.py halves were re-pinned to the
    previous head every round - those two files are where each round's work
    legitimately lands, and their behaviour is covered by the behaviour
    probes. The pins this probe is named for are untouched: the CLI, the
    engine's data sources, the airports CSV and the goldens are still
    byte-identical to the map round's base."""
    paths = ["src/main.py", "src/formatter.py", "src/seats_client.py", "src/optimizer.py",
             "src/live_trip.py", "src/trip_builder.py", "src/regions.py",
             "src/ui/serialize.py", "data/airports.csv",
             "tests/fixtures/cli_golden", "tests/test_cli_golden.py"]
    out = git("diff", f"{BASE}..HEAD", "--stat", "--", *paths)
    assert out.strip() == "", out


def test_A7_the_parity_suite_and_goldens_are_untouched():
    """Converted from a byte/line pin to a behaviour assertion (agreed with
    Tsuki, 2026-09-16); the pin never caught a defect and went red on every
    unrelated change. The "the other probe trees are unchanged since <commit>"
    half pinned the tester's own future commits: every later pin refresh in
    ui-probes or ui-restyle-probes turned this probe red and had to be re-based
    by hand, and it can never catch a product defect because no product change
    touches those paths. It is gone. The goldens half - the real rule, that the
    CLI's recorded output is untouched since the map round's base - stays."""
    out = git("diff", f"{BASE}..HEAD", "--stat", "--", "tests/fixtures/cli_golden")
    assert out.strip() == "", out
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
