"""
P. THE macOS ROUND: the two defects his own machine found, attacked as
properties rather than as the five failures.

MAC-1  a line meant to be copied is never folded, at any width, with any path.
MAC-2  how deep and how large an input file may be is a RULE, not whatever the
       interpreter's stack happens to allow.

RED = a defect that exists. GREEN = the fix held up.
"""
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from rich.console import Console

from conftest import OFFLINE, ROOT

PY = str(ROOT / ".venv" / "bin" / "python")
TRIPS = ROOT / "tests" / "fixtures" / "trips"

# Paths a person can really have, and the two that have bitten this project.
PATHS = {
    "mac_pytest_tmp": "/private/var/folders/9w/8k2x7p1n5q3d_4m6r0j8t1_c0000gn/T/"
                      "pytest-of-tsuki/pytest-4/test_a_capture_outside_real_ex0/records",
    "two_hundred_chars": "/Users/tsuki/" + "d" * 170 + "/records",
    "with_spaces": "/Users/tsuki/Google Drive/My Points Research/docs/yq-checks",
    "unicode": "/Users/tsuki/Documents/vöyages/2027-über-tour/docs/yq-checks",
    "brackets": "/Users/tsuki/docs/[draft]/yq-checks",
    "backslash": "/Users/tsuki/docs/a\\b/yq-checks",
}
WIDTHS = [20, 40, 80, 190, 400, 20000]


def printed(fn, width):
    buf = io.StringIO()
    fn(Console(file=buf, width=width, no_color=True, highlight=False))
    return buf.getvalue()


# ==================================================== MAC-1, as a property


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("name", sorted(PATHS))
def test_P1_a_copyable_line_arrives_whole_at_every_width(width, name):
    from src.formatter import print_copyable

    path = PATHS[name]
    row = f"virginatlantic,VS,includes_yq,2026-09-12,{path}/2026-09-12-virginatlantic.md,"
    out = printed(lambda c: print_copyable(c, row), width)
    assert row in out, (
        f"width {width}, {name}: the line came back folded:\n{out[:400]}")
    assert len([l for l in out.splitlines() if l.strip()]) == 1, out[:400]


@pytest.mark.parametrize("width", [40, 190])
def test_P2_a_bracket_in_a_path_is_not_eaten_as_markup(width):
    """The rich-markup bug this project has had twice: `[draft]` read as a
    style tag and deleted."""
    from src.formatter import print_copyable

    line = f"wrote {PATHS['brackets']}/2026-09-12-virginatlantic.md"
    out = printed(lambda c: print_copyable(c, line), width)
    assert "[draft]" in out, out
    assert line in out, out


@pytest.mark.parametrize("style", ["", "bold", "red", "bold yellow"])
def test_P3_colour_comes_from_the_style_and_adds_no_characters(style):
    from src.formatter import print_copyable

    line = f"wrote {PATHS['mac_pytest_tmp']}/x.md"
    out = printed(lambda c: print_copyable(c, line, style), 40)
    assert out.strip() == line, (style, out[:200])


def test_P4_prose_still_wraps():
    """The fix must not have turned every line into a soft-wrapped one."""
    buf = io.StringIO()
    c = Console(file=buf, width=40, no_color=True)
    c.print("This is an ordinary sentence of prose that is much longer than forty "
            "columns and is expected to wrap like prose.")
    assert len([l for l in buf.getvalue().splitlines() if l.strip()]) > 1


DEEP = ("private-var-folders-9w-0000gn-T", "pytest-of-tsuki", "pytest-4",
        "test_yq_check_prints_every_fie0")


@pytest.mark.parametrize("width", [20, 40, 190, 400])
def test_P5_every_path_the_yq_check_command_prints_is_whole(tmp_path, width):
    """The real command, driven end to end at four widths, in a directory as
    deep as a macOS pytest tmp path: every path it names must appear inside ONE
    line, and the CSV row must be one line."""
    from unittest.mock import patch

    from src import trips_tools
    from tests._trips_label_state import unverified_constants  # noqa: F401
    from tests.test_trips_tools import FLAG_KEY, TODAY, Stub

    real = tmp_path.joinpath(*DEEP, "real")
    records = tmp_path.joinpath(*DEEP, "records")
    real.mkdir(parents=True)
    records.mkdir(parents=True)
    argv = ["yq-check", "--cabin", "J", "--origin", "LHR", "--destination", "SFO",
            "--date", "2027-01-27", "--source", "virginatlantic", "--out-dir", str(real),
            "--record-dir", str(records), "--api-key", FLAG_KEY, "--yes"]
    buf = io.StringIO()
    from src import config as _config

    with patch("src.seats_client.requests.get", side_effect=Stub()), \
            patch.object(_config, "TRIPS_SCHEMA_VERIFIED_BY", "", create=True):
        code = trips_tools.main(argv, read=lambda prompt: "y",
                                console=Console(file=buf, width=width), today=TODAY)
    lines = [l.rstrip() for l in buf.getvalue().splitlines()]
    assert code in (0, 5), (code, lines[-6:])
    rows = [l for l in lines if ",<VERDICT>," in l]
    assert rows, [l for l in lines if "virginatlantic" in l][:4]
    assert len(rows) == 1 and str(records) in rows[0], rows
    for path in sorted(real.iterdir()) + sorted(records.iterdir()):
        assert any(str(path) in line for line in lines), (path, lines[-6:])


@pytest.mark.parametrize("width", [40, 190])
def test_P6_the_live_banner_names_its_manifest_on_one_line(tmp_path, width, monkeypatch):
    """The `snapshots:` / `manifest:` lines: what `--from-snapshot` is handed
    next, printed after every live run."""
    from src import config
    from src.formatter import print_copyable

    deep = tmp_path.joinpath(*DEEP, "snapshots")
    deep.mkdir(parents=True)
    manifest = deep / "MANIFEST.md"
    manifest.write_text("| x |\n")
    out = printed(lambda c: print_copyable(c, f"  manifest:  {manifest}"), width)
    assert str(manifest) in out, out
    assert len([l for l in out.splitlines() if l.strip()]) == 1, out


def test_P7_the_relocation_banner_is_not_copyable_yet(tmp_path, monkeypatch):
    """The three lines in `src/main.py` that were deliberately left alone. The
    stated reason is that they cannot pass 190 columns in practice - but the
    path comes from an environment variable, and the suite's own harness sets
    it to a macOS tmp path."""
    import src.main as m

    mac = ("/private/var/folders/9w/8k2x7p1n5q3d_4m6r0j8t1_c0000gn/T/pytest-of-tsuki/"
           "pytest-4/test_the_live_transcript_matc0/snapshots")
    monkeypatch.setenv("POINTS_OPTIMIZER_CACHE_DIR", mac)
    out = printed(m.print_relocation_banner, 190)
    lines = [l for l in out.splitlines() if l.strip()]
    assert len(lines) == 1, (
        f"the relocation banner folds at 190 columns with a {len(mac)}-character "
        f"macOS tmp path: {lines}")


def test_P8_a_very_long_relocation_path_is_split_mid_path(monkeypatch):
    """Where it actually breaks: above ~190 characters the fold lands INSIDE
    the path, so what the reader can copy is not a path."""
    import src.main as m

    long_path = "/Users/tsuki/" + "d" * 210 + "/snapshots"
    monkeypatch.setenv("POINTS_OPTIMIZER_CACHE_DIR", long_path)
    out = printed(m.print_relocation_banner, 190)
    assert any(long_path in line for line in out.splitlines()), (
        f"a {len(long_path)}-character path came back split across lines:\n{out}")


def test_P9_the_uis_equivalent_command_is_never_folded(ui, tmp_path):
    """The UI's own copyable line. It is a JSON field, not console output, and
    the page must not be the thing that breaks it."""
    run = ui.run_trip("trip_b_europe", OFFLINE)[1].json()
    argv = run["argv_display"]
    assert "\n" not in argv and argv.count("--trip-fixture") == 1
    assert str(ui.trips_dir) in argv or "trip_b_europe.json" in argv


def test_P10_the_transcript_never_folds_a_path_it_names(ui):
    """The transcript is the CLI's own output at 190 columns, and the UI shows
    it verbatim: anything folded there is folded on the page too."""
    run = ui.run_trip("trip_b_europe", OFFLINE)[1].json()
    transcript = run["transcript"]
    trips = str(ui.trips_dir)
    for line in transcript.splitlines():
        if "is set:" in line or line.startswith("wrote ") or "MANIFEST.md" in line:
            assert len(line) <= 190, line
    # and the paths the transcript does name are whole
    if trips in transcript:
        assert any(trips in line for line in transcript.splitlines())


# ==================================================== MAC-2, as a property


def depth_of(path):
    from src import config

    return config.json_nesting_depth(Path(path).read_text())


def nested(tmp_path, name, target):
    from src import config

    doc = json.loads((TRIPS / "trip_a_mry_nyc.json").read_text())
    doc["id"] = name
    node = doc
    while True:
        current = config.json_nesting_depth(json.dumps(doc))
        if current >= target:
            break
        if target - current == 1:
            node["deeper"] = {}          # one level exactly
            node = node["deeper"]
        else:
            node["notes"] = [{}]         # two levels
            node = node["notes"][0]
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(doc))
    return path


def run_cli(path, extra=()):
    p = subprocess.run([PY, "-m", "src.main", "--trip-fixture", str(path), "--offline",
                        "--balance", "UR=160000", "--card", "Chase Sapphire Preferred",
                        "--transfer-date", "2026-09-15", *extra],
                       cwd=str(ROOT), capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def run_at_limit(path, limit):
    code = (f"import sys; sys.setrecursionlimit({limit});"
            f"sys.path.insert(0, {str(ROOT)!r});"
            f"sys.argv=['x','--trip-fixture',{str(path)!r},'--offline','--balance',"
            f"'UR=160000','--card','Chase Sapphire Preferred','--transfer-date','2026-09-15'];"
            f"import src.main as m; raise SystemExit(m.main())")
    p = subprocess.run([PY, "-c", code], cwd=str(ROOT), capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


@pytest.mark.parametrize("value,want", [
    ('{"a": "{{{{{"}', 1), ('{"a": "[[[[["}', 1), ('{"a": "\\""}', 1),
    ('{"a": "\\\\"}', 1), ('{"a": "\\\\", "b": {"c": 1}}', 2),
    ('{"a": "\\u007b\\u007b"}', 1), ('{"a": "x", "b": [1, [2, [3]]]}', 4),
    ('{"a": "}"}', 1), ('[]', 1), ('{"k": "a\\"b{c"}', 1),
])
def test_P11_the_depth_scan_agrees_with_the_parsed_document(value, want):
    from src import config

    def walk(o):
        if isinstance(o, dict):
            return 1 + max([walk(v) for v in o.values()] or [0])
        if isinstance(o, list):
            return 1 + max([walk(v) for v in o] or [0])
        return 0

    assert config.json_nesting_depth(value) == walk(json.loads(value)) == want


def test_P12_the_scan_is_not_a_way_to_hang_the_reader():
    """It runs before the parse, on a file a person points at, and the UI runs
    it on every file in the trips directory on every page load."""
    import time

    from src import config

    big = json.dumps({"legs": [{"id": f"L{i}", "cash_options": [
        {"label": "x" * 80, "amount": 1.0}]} for i in range(10000)]})
    start = time.monotonic()
    assert config.json_nesting_depth(big) == 5
    assert time.monotonic() - start < 2.0
    evil = '{"a": "' + "\\" * 40000          # unterminated, all escapes
    start = time.monotonic()
    config.json_nesting_depth(evil)
    assert time.monotonic() - start < 2.0


def test_P13_the_limit_is_a_rule_and_the_same_rule_on_every_interpreter(tmp_path):
    ok = nested(tmp_path, "p13_ok", 32)
    assert depth_of(ok) == 32
    code, out = run_cli(ok)
    assert code in (0, 3, 4) and "Error:" not in out, out[-300:]
    for target in (33, 35, 120):
        bad = nested(tmp_path, f"p13_bad{target}", target)
        first = None
        for limit in (1000, 20000):
            code, out = run_at_limit(bad, limit)
            line = [l for l in out.splitlines() if l.startswith("Error:")]
            assert code == 1 and line, (target, limit, out[-300:])
            assert "Traceback" not in out
            assert "levels deep, and may be at most 32" in line[0], line[0]
            first = first or line[0]
            assert line[0] == first, (limit, line[0], first)


def test_P14_the_size_rule_refuses_nothing_real(tmp_path):
    from src import config

    for path in sorted(TRIPS.glob("*.json")):
        assert path.stat().st_size < config.MAX_INPUT_FILE_BYTES
        assert config.json_nesting_depth(path.read_text()) <= config.MAX_JSON_NESTING_DEPTH
    # a 400-leg trip: big, legal, and it must load
    base = json.loads((TRIPS / "trip_a_mry_nyc.json").read_text())
    leg = [l for l in base["legs"] if l["kind"] == "flight"][0]
    base["legs"] = [dict(leg, id=f"L{i}") for i in range(400)]
    base["id"] = "p14_400legs"
    path = tmp_path / "p14_400legs.json"
    path.write_text(json.dumps(base))
    code, out = run_cli(path)
    assert "Traceback" not in out and code in (0, 3, 4), out[-300:]


def test_P15_an_oversized_file_is_one_sentence(tmp_path):
    path = tmp_path / "p15_big.json"
    path.write_text(json.dumps({"id": "p15_big", "name": "x" * 5_000_000, "description": "x",
                                "source": "x", "trip_level_flags": [], "legs": []}))
    code, out = run_cli(path)
    assert code == 1 and "Traceback" not in out
    assert "bytes, and may be at most" in out, out[-300:]


@pytest.mark.parametrize("kind", ["deep", "big"])
def test_P16_the_wallet_has_the_same_rule(tmp_path, kind):
    wallet = {"balances": {"UR": 160000}, "cards": ["Chase Sapphire Preferred"]}
    if kind == "deep":
        node = wallet
        for _ in range(40):
            node["x"] = {}
            node = node["x"]
    else:
        wallet["cards"].append("x" * 5_000_000)
    path = tmp_path / f"p16_{kind}.json"
    path.write_text(json.dumps(wallet))
    p = subprocess.run([PY, "-m", "src.main", "--trip-fixture",
                        str(TRIPS / "trip_a_mry_nyc.json"), "--offline", "--wallet", str(path),
                        "--transfer-date", "2026-09-15"],
                       cwd=str(ROOT), capture_output=True, text=True)
    out = p.stdout + p.stderr
    assert p.returncode == 2 and "Traceback" not in out, out[-300:]
    assert "Wallet error:" in out and ("levels deep" in out or "bytes, and may be" in out), out[-200:]


@pytest.mark.parametrize("kind", ["deep", "big"])
def test_P17_the_ui_answers_the_same_files_without_an_internal_error(ui, tmp_path, kind):
    if kind == "deep":
        path = nested(tmp_path, "p17_deep", 120)
    else:
        path = tmp_path / "p17_big.json"
        path.write_text(json.dumps({"id": "p17_big", "name": "x" * 5_000_000,
                                    "description": "x", "source": "x",
                                    "trip_level_flags": [], "legs": []}))
    import shutil

    shutil.copy(path, ui.trips_dir / path.name)
    listing = ui.get("/api/trips")
    assert listing.status == 200, listing.text[:200]
    row = [t for t in listing.json() if t["id"] == path.stem]
    assert row, [t["id"] for t in listing.json()]
    error = row[0]["load_error"] or ""
    assert "RecursionError" not in error and "Traceback" not in error, error
    assert "levels deep" in error or "bytes, and may be" in error, error
    detail = ui.get(f"/api/trips/{path.stem}")
    assert detail.status == 422, detail.status
    run = ui.post(f"/api/trips/{path.stem}/run", OFFLINE)
    assert run.status != 500, run.text[:200]


def test_P18_the_uis_wallet_panel_survives_a_deep_wallet_file(tmp_path):
    """`--wallet` is given at launch; the panel reads that file on every
    /api/state. Before the fix this was a 500 on a small stack."""
    from conftest import server

    wallet = {"balances": {"UR": 160000}, "cards": []}
    node = wallet
    for _ in range(40):
        node["x"] = {}
        node = node["x"]
    path = tmp_path / "p18_wallet.json"
    path.write_text(json.dumps(wallet))
    from tests._ui_harness import copy_trips

    trips = copy_trips(tmp_path / "trips")
    with server(wallet_path=path, trips_dir=trips) as c:
        state = c.get("/api/state")
        assert state.status == 200, state.text[:200]
        w = state.json()["wallet"]
        assert w.get("error"), w
        assert "levels deep" in w["error"] or "bytes" in w["error"], w["error"]


# ================================================== the platform sweep


def test_P19_the_package_imports_under_a_C_locale():
    """`src/models.py` reads this package's own source at import; seven files
    under src/ carry non-ASCII. Under LANG=C that used to be a
    UnicodeDecodeError and 98 collection errors."""
    env = dict(os.environ, LC_ALL="C", LANG="C", PYTHONUTF8="0")
    p = subprocess.run([PY, "-c", "import src.models; print('ok')"],
                       cwd=str(ROOT), capture_output=True, text=True, env=env)
    assert p.returncode == 0 and "ok" in p.stdout, (p.stdout + p.stderr)[-500:]


def test_P20_a_trip_fixture_with_crlf_line_endings_reads_the_same(tmp_path):
    src = (TRIPS / "trip_a_mry_nyc.json").read_text()
    path = tmp_path / "p20_crlf.json"
    path.write_bytes(src.replace("\n", "\r\n").encode("utf-8"))
    code, out = run_cli(path)
    assert "Traceback" not in out and code in (0, 3, 4), out[-300:]
