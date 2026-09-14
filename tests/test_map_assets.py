"""
The map's committed data files are generated, not hand-typed, and the tests
here pin each one to its generator or to the file it was derived from.

* src/ui/static/land.json is byte-for-byte what tools/build_land_path.py builds
  from docs/design/map-ref/land-50m.json, and stays under its 400 KB budget.
* data/airportsdata_iata.csv is what tools/filter_airportsdata.py produces from
  the full airportsdata CSV (compared when that 3 MB file is present; its
  structural invariants are checked always).
* data/hubs.json has the shape src.map_tools writes, and its `searchable` flag
  cannot drift from data/airports.csv.
"""
import csv
import hashlib
import io
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import build_land_path, filter_airportsdata  # noqa: E402
from src import map_tools, regions  # noqa: E402

LAND = ROOT / "src" / "ui" / "static" / "land.json"
TOPO = ROOT / "docs" / "design" / "map-ref" / "land-50m.json"
IATA_CSV = ROOT / "data" / "airportsdata_iata.csv"
FULL_CSV = ROOT / "docs" / "design" / "map-ref" / "airportsdata-20260905-airports.csv"
HUBS = ROOT / "data" / "hubs.json"
LAND_BUDGET = 400 * 1024


# ------------------------------------------------------------------ land.json


def test_land_json_matches_its_generator():
    assert TOPO.is_file(), "the generator's input is committed under docs/design/map-ref/"
    built = build_land_path.render(build_land_path.build(TOPO))
    assert LAND.read_bytes() == built.encode("utf-8"), \
        "src/ui/static/land.json was not produced by tools/build_land_path.py"


def test_land_json_is_under_400kb():
    assert LAND.stat().st_size <= LAND_BUDGET


def test_land_json_carries_the_projection_and_a_closed_path():
    d = json.loads(LAND.read_text(encoding="utf-8"))
    assert set(d) == {"w", "h", "lat_min", "lat_max", "d"}
    assert (d["w"], d["h"]) == (4000, 2080)
    assert (d["lat_min"], d["lat_max"]) == (-60.0, 83.0)
    path = d["d"]
    assert path.startswith("M")
    rings = path.split("M")[1:]
    assert rings and all(r.endswith("Z") for r in rings)
    # Integer coordinates only: one absolute move, then relative line-tos.
    assert re.fullmatch(r"(M-?\d+,-?\d+l(-?\d+,-?\d+)( -?\d+,-?\d+)*Z)+", path)


def test_the_generator_check_mode_notices_a_one_byte_edit(tmp_path):
    copy = tmp_path / "land.json"
    copy.write_bytes(LAND.read_bytes())
    assert build_land_path.main(["--check", "--out", str(copy)]) == 0
    data = bytearray(copy.read_bytes())
    data[len(data) // 2] ^= 1
    copy.write_bytes(bytes(data))
    assert build_land_path.main(["--check", "--out", str(copy)]) == 1


@pytest.mark.parametrize("lon,lat,x,y", [
    (-180, 83, 0, 0),
    (180, -60, 4000, 2080),
    (0, 0, 2000, None),
])
def test_the_projection_pins_its_corners(lon, lat, x, y):
    px, py = build_land_path.project(lon, lat)
    assert abs(px - x) < 1e-9
    if y is not None:
        assert abs(py - y) < 1e-9
    else:
        assert 0 < py < 2080


def test_latitude_is_clamped_not_extrapolated():
    assert build_land_path.project(0, 89) == build_land_path.project(0, 83)
    assert build_land_path.project(0, -89) == build_land_path.project(0, -60)


# ------------------------------------------------------- airportsdata_iata.csv


def _read_iata_csv(path: Path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_the_iata_csv_has_the_documented_columns_and_only_iata_rows():
    rows = _read_iata_csv(IATA_CSV)
    assert rows, "data/airportsdata_iata.csv is empty"
    assert list(rows[0].keys()) == filter_airportsdata.COLUMNS
    codes = [r["iata"] for r in rows]
    assert all(re.fullmatch(r"[A-Z0-9]{3}", c) for c in codes)
    assert len(set(codes)) == len(codes), "an IATA code appears twice"
    assert codes == sorted(codes)
    for r in rows:
        float(r["lat"])
        float(r["lon"])


# sha256 of data/airportsdata_iata.csv as tools/filter_airportsdata.py wrote it
# from airportsdata 20260905. The full 3 MB CSV is not committed, so an export
# cannot rerun the filter; this digest pins the committed bytes to that run
# instead of skipping (a hand edit changes the digest, and the test says so).
IATA_CSV_SHA256 = "5762e376acc068cc01bdd7fd2069559f7641bbe3263fa8ea991bec7136e98098"


def test_the_iata_csv_equals_the_filter_output():
    text = IATA_CSV.read_text(encoding="utf-8")
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == IATA_CSV_SHA256, \
        "data/airportsdata_iata.csv is not the bytes tools/filter_airportsdata.py wrote"
    if FULL_CSV.is_file():
        with open(FULL_CSV, newline="", encoding="utf-8") as f:
            expected = filter_airportsdata.render(filter_airportsdata.filter_rows(f))
        assert text == expected


def test_the_filter_is_idempotent_and_keeps_only_iata_rows():
    src = (
        'icao,iata,name,city,subd,country,elevation,lat,lon,tz,lid\n'
        '"KSFO","SFO","San Francisco Intl","San Francisco","California","US",13,37.618972,-122.374889,"America/Los_Angeles","SFO"\n'
        '"00AA","","Aero B Ranch","Leoti","Kansas","US",3435,38.7,-101.4,"America/Chicago","00AA"\n'
        '"EGLL","LHR","London Heathrow","London","England","GB",83,51.4775,-0.461389,"Europe/London",""\n'
    )
    out = filter_airportsdata.render(filter_airportsdata.filter_rows(io.StringIO(src)))
    assert out == (
        "iata,icao,name,city,country,lat,lon\n"
        "LHR,EGLL,London Heathrow,London,GB,51.4775,-0.461389\n"
        "SFO,KSFO,San Francisco Intl,San Francisco,US,37.618972,-122.374889\n"
    )
    again = filter_airportsdata.render(filter_airportsdata.filter_rows(io.StringIO(out)))
    assert again == out


# ------------------------------------------------------------------ hubs.json


def test_hubs_json_has_the_capture_tools_shape():
    doc = json.loads(HUBS.read_text(encoding="utf-8"))
    problems = map_tools.hubs_file_problems(doc)
    assert problems == [], problems


def test_the_committed_hubs_file_says_it_is_empty_in_words():
    doc = json.loads(HUBS.read_text(encoding="utf-8"))
    meta = doc["_meta"]
    if doc["hubs"]:
        assert meta["captured_at"] is not None
        assert "note" not in meta
    else:
        assert meta["captured_at"] is None and meta["captured_by"] is None
        assert meta["note"].startswith("No capture has run;")
        assert "python -m src.map_tools capture-hubs" in meta["note"]


def test_searchable_cannot_drift_from_airports_csv():
    doc = json.loads(HUBS.read_text(encoding="utf-8"))
    known = regions.known_airports()
    for hub in doc["hubs"]:
        assert hub["searchable"] == (hub["iata"] in known), hub["iata"]
    if doc["hubs"]:
        assert doc["_meta"]["engine_airports_sha256"] == map_tools.airports_csv_sha256()


def test_hubs_json_carries_no_key_material():
    text = HUBS.read_text(encoding="utf-8")
    assert "Partner-Authorization" not in text
    assert not re.search(r"\bpro_[A-Za-z0-9]{20,}", text)
    assert doc_sha(text)  # readable


def doc_sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_the_synthetic_fixture_is_what_its_helper_builds_and_says_it_is_synthetic():
    from tests import _map_fixture

    text = _map_fixture.FIXTURE.read_text(encoding="utf-8")
    assert text == map_tools.render_document(_map_fixture.synthetic_document())
    doc = json.loads(text)
    assert doc["_meta"]["synthetic"] is True
    assert "SYNTHETIC" in doc["_meta"]["captured_by"]
    assert map_tools.hubs_file_problems(doc) == []
    # It is a test input, never the shipped file.
    assert HUBS.read_text(encoding="utf-8") != text


# --------------------------------------------------------------------- served


@pytest.fixture
def client(tmp_path):
    from tests._ui_harness import running_server, write_wallet

    with running_server(wallet_path=write_wallet(tmp_path / "wallet.json")) as c:
        yield c


@pytest.mark.parametrize("path,ctype", [
    ("/static/map.js", "text/javascript; charset=utf-8"),
    ("/static/land.json", "application/json; charset=utf-8"),
    ("/static/hubs.json", "application/json; charset=utf-8"),
])
def test_the_map_files_are_served_with_the_security_headers(client, path, ctype):
    r = client.get(path, token=False)
    assert r.status == 200
    assert r.headers["content-type"] == ctype
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["cross-origin-resource-policy"] == "same-origin"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    if path.endswith(".json"):
        json.loads(r.text)


def test_a_missing_hubs_file_is_a_404_never_a_500(client, tmp_path, monkeypatch):
    from src.ui import server as server_mod

    absent = tmp_path / "absent" / "hubs.json"
    monkeypatch.setitem(server_mod.STATIC_FILES, "/static/hubs.json",
                        (absent, "application/json; charset=utf-8"))
    r = client.get("/static/hubs.json", token=False)
    assert r.status == 404
    assert r.json() == {"error": "not_found", "message": "Not found."}


def test_a_hubs_file_carrying_the_key_is_refused_by_egress(client, tmp_path, monkeypatch):
    """Two layers: the tool refuses to write it (test_map_tools), and if one
    were planted anyway the server refuses to send it."""
    from src import config
    from src.ui import server as server_mod

    fake = "sec_fake_key_ABCDEFGHIJ"
    monkeypatch.setenv(config.KEY_ENV_VAR, fake)
    doc = map_tools.empty_document()
    doc["_meta"]["note"] = doc["_meta"]["note"] + " " + fake
    planted = tmp_path / "hubs.json"
    planted.write_text(map_tools.render_document(doc), encoding="utf-8")
    monkeypatch.setitem(server_mod.STATIC_FILES, "/static/hubs.json",
                        (planted, "application/json; charset=utf-8"))
    r = client.get("/static/hubs.json", token=False)
    assert r.status == 500
    assert fake not in r.text
    assert r.json()["error"] == "internal"
    assert any("REFUSED" in line for line in client.logs)
