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


@pytest.mark.parametrize("width", [20, 40, 190, 400])
@pytest.mark.parametrize("name", sorted(PATHS))
def test_P7_every_line_the_relocation_banner_prints_arrives_whole(
    width, name, monkeypatch
):
    """RE-SCOPED BY THE TESTER, MAC ROUND 2. This probe was written red against
    the three prints that MAC-1 deliberately skipped, and it asserted the
    banner emits exactly ONE line. That was over-assertion: the banner emits one
    line PER relocation variable that is set, and the harness sets all three.
    What it MEANS - and now says - is that each line the banner prints is whole,
    one per set variable, with the path intact. Kept parametrised over the same
    path shapes and widths as P1, because the value is an environment variable
    and nothing bounds what a user puts in it."""
    import src.main as m

    path = PATHS[name]
    for var in m.RELOCATION_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("POINTS_OPTIMIZER_CACHE_DIR", path)
    monkeypatch.setenv("POINTS_OPTIMIZER_SNAPSHOT_DIR", path + "/snaps")

    out = printed(m.print_relocation_banner, width)
    lines = [l for l in out.splitlines() if l.strip()]
    assert len(lines) == 2, (
        f"width {width}, {name}: expected one line per set variable, got "
        f"{len(lines)}:\n{out[:600]}")
    assert path in lines[0] and (path + "/snaps") in lines[1], (
        f"width {width}, {name}: a path came back folded:\n{out[:600]}")
    assert "POINTS_OPTIMIZER_ENV_FILE" not in out, (
        "a variable that is not set was named anyway")


def test_P7b_markup_in_a_relocation_path_is_not_eaten(monkeypatch):
    """The banner prints with a style now. A path containing rich markup must
    survive as text, not be interpreted as a tag."""
    import src.main as m

    for var in m.RELOCATION_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("POINTS_OPTIMIZER_CACHE_DIR", "/Users/someone/[draft]/cache")
    out = printed(m.print_relocation_banner, 190)
    assert "/Users/someone/[draft]/cache" in out, out


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


# ============================== MAC-A, round 2: the fix, as a property


MAC_DEEP = ("private/var/folders/9w/8k2x7p1n5q3d_4m6r0j8t1_c0000gn/T/"
            "pytest-of-tsuki/pytest-4/test_a_very_long_test_name_that_macos_"
            "tmpdirs_really_do_produce0")


@pytest.mark.parametrize("width", [20, 40, 80, 190, 400, 20000])
def test_P21_new_trip_writes_a_path_and_a_command_that_are_both_whole(
    width, tmp_path, monkeypatch
):
    """The other two lines the MAC-A fix touched, driven end to end through
    `run_new_trip`, into a directory as deep as a macOS pytest tmpdir and with
    a fixture name long enough to push the command past any width."""
    from types import SimpleNamespace

    from src import trip_builder
    import src.main as m

    deep = tmp_path.joinpath(*DEEP, "trips")
    deep.mkdir(parents=True)
    monkeypatch.setattr(trip_builder, "FIXTURE_DIR", deep)

    name = "p21_" + "a" * 60
    args = SimpleNamespace(
        new_trip=name, travelers="1", cabin="J", force=True,
        legs=["LHR:SFO:2027-01-27:1200"], hotels=[],
    )
    buf = io.StringIO()
    code, path = m.run_new_trip(args, Console(file=buf, width=width, no_color=True,
                                              highlight=False))
    assert code == 0
    lines = [l.rstrip() for l in buf.getvalue().splitlines()]

    assert f"Wrote {path}" in lines, (
        f"width {width}: the written path came back folded:\n{lines[-6:]}")
    command = [l for l in lines if "--trip-fixture" in l]
    assert len(command) == 1, (
        f"width {width}: the next command is spread over {len(command)} lines: {command}")
    assert command[0] == (
        f'  python -m src.main --trip-fixture {name}.json --live '
        f'--balance UR=<n> --card "<card>"'), (width, command)


def test_P22_use_utf8_output_is_called_at_process_entry_points_only():
    """The fix forces UTF-8 on the real stdout. That must happen where a
    PROCESS starts and nowhere else: the UI calls `src.main` in-process, and so
    does every test, and neither may have its console reconfigured underneath
    it."""
    import ast

    called_in = {}
    for py in sorted((ROOT / "src").rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        guarded = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and "__main__" in ast.unparse(node.test):
                for sub in ast.walk(node):
                    guarded.add(id(sub))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and "use_utf8_output" in ast.unparse(node.func):
                called_in.setdefault(py.relative_to(ROOT).as_posix(), []).append(
                    id(node) in guarded)

    assert called_in, "nothing calls use_utf8_output at all"
    for where, guards in called_in.items():
        assert all(guards), (
            f"{where} calls use_utf8_output outside an `if __name__ == '__main__'` "
            f"guard: an in-process caller (the UI, a test) would have its stdout "
            f"reconfigured underneath it")
    assert set(called_in) == {"src/main.py", "src/trips_tools.py", "src/ui/__main__.py"}, (
        f"the set of entry points that force UTF-8 changed: {sorted(called_in)}")


def test_P23_calling_main_in_process_does_not_touch_this_process_stdout():
    """The behavioural half of P22: import and call, and prove sys.stdout is
    the same object with the same encoding afterwards."""
    code = (
        "import sys, io, json;"
        "sys.path.insert(0, %r);" % str(ROOT) +
        "before=(id(sys.stdout), getattr(sys.stdout,'encoding',None));"
        "import src.main as m;"
        "buf=io.StringIO();"
        "from rich.console import Console;"
        "m.main(['--help'], console=Console(file=buf, width=100)) "
        "if False else None;"
        "import src.config as c;"
        "after=(id(sys.stdout), getattr(sys.stdout,'encoding',None));"
        "print(json.dumps([before[1], after[1], before[0]==after[0]]))"
    )
    p = subprocess.run([PY, "-c", code], cwd=str(ROOT), capture_output=True, text=True,
                       env=dict(os.environ, LC_ALL="C", LANG="C", PYTHONUTF8="0"))
    assert p.returncode == 0, (p.stdout + p.stderr)[-500:]
    before_enc, after_enc, same = json.loads(p.stdout.strip().splitlines()[-1])
    assert same, "importing src.main replaced sys.stdout"
    assert before_enc == after_enc, (
        f"importing src.main reconfigured stdout under a C locale: "
        f"{before_enc} -> {after_enc}")


@pytest.mark.parametrize("fixture", ["trip_b_europe.json", "trip_c_lon_mry_surcharge.json"])
def test_P24_a_whole_report_prints_under_a_C_locale(fixture):
    """The failure MAC-A's UTF-8 forcing exists to stop: score the trip, then
    die of UnicodeEncodeError partway through printing it."""
    env = dict(os.environ, LC_ALL="C", LANG="C", PYTHONUTF8="0")
    env.pop("PYTHONIOENCODING", None)
    p = subprocess.run(
        [PY, "-m", "src.main", "--trip-fixture", str(TRIPS / fixture), "--offline",
         "--balance", "UR=160000", "--card", "Chase Sapphire Preferred",
         "--transfer-date", "2026-09-15"],
        cwd=str(ROOT), capture_output=True, env=env)
    out = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
    assert "UnicodeEncodeError" not in out, out[-800:]
    assert "Traceback" not in out, out[-800:]
    assert p.returncode in (0, 3, 4), f"exit {p.returncode}\n{out[-800:]}"
    assert out.strip().splitlines(), "nothing printed at all"


def test_P25_the_yq_check_row_prints_under_a_C_locale():
    """`src/trips_tools.py` is the other entry point that forces UTF-8, and its
    row is the thing a reader copies."""
    env = dict(os.environ, LC_ALL="C", LANG="C", PYTHONUTF8="0")
    env.pop("PYTHONIOENCODING", None)
    p = subprocess.run([PY, "-m", "src.trips_tools", "--help"],
                       cwd=str(ROOT), capture_output=True, env=env)
    out = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
    assert p.returncode == 0, out[-500:]
    assert "UnicodeEncodeError" not in out and "Traceback" not in out, out[-500:]


def test_P26_forcing_utf8_twice_and_on_a_replaced_stream_is_harmless():
    """Idempotence and the honest fallback: a stream this process does not own
    (a StringIO someone swapped in) is left exactly as it is, not crashed on."""
    code = (
        "import sys, io;"
        "sys.path.insert(0, %r);" % str(ROOT) +
        "import src.config as c;"
        "c.use_utf8_output(); c.use_utf8_output();"
        "real=sys.stdout;"
        "sys.stdout=io.StringIO();"
        "c.use_utf8_output();"
        "swapped=sys.stdout;"
        "sys.stdout=real;"
        "print('same' if swapped.getvalue()=='' else 'wrote');"
        "print(sys.stdout.encoding)"
    )
    p = subprocess.run([PY, "-c", code], cwd=str(ROOT), capture_output=True, text=True,
                       env=dict(os.environ, LC_ALL="C", LANG="C", PYTHONUTF8="0"))
    assert p.returncode == 0, (p.stdout + p.stderr)[-500:]
    lines = p.stdout.strip().splitlines()
    assert lines[0] == "same", "use_utf8_output wrote to a stream it was only inspecting"
    assert lines[-1].lower().replace("-", "") == "utf8", lines


# ---------------------------------------------- the case-exact evidence rule


VERDICT_LINE = "- verdict (includes_yq / excludes_yq / inconclusive): includes_yq"


def _record_body():
    return (
        "# yq-check record: virginatlantic, 2026-09-10\n\n## Seats.aero\n\n"
        "- program: Some Program (source virginatlantic)\n"
        "- itinerary lookup status: KNOWN\n"
        "- checked airline (the award's KNOWN flight-number carrier): VS\n"
        "- modelled carrier surcharge band: $200-$350 (pt $275) one way "
        "(VS metal, cabin J)\n\n## site\n\n"
        "- the site shows this flight operated by VS itself, not a codeshare "
        "partner (yes / no): yes\n"
        "- taxes, fees and carrier-imposed charges for ONE adult: GBP 450.00\n"
        f"{VERDICT_LINE}\n"
    )


def _load_citing(tmp_path, evidence, filename="2026-09-10-virginatlantic.md"):
    """A repository-shaped root with one record on disk, and a table whose row
    cites it however the caller spells it. Returns whatever `load` does."""
    from datetime import date

    from src import yq_inclusion

    d = tmp_path / "docs" / "yq-checks"
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(_record_body(), encoding="utf-8")
    csv = tmp_path / "yq.csv"
    csv.write_text(
        "source,airline,verdict,verified_on,evidence,notes\n"
        f"virginatlantic,VS,includes_yq,2026-09-10,{evidence},x\n", encoding="utf-8")
    return yq_inclusion.load(csv, today=date(2026, 9, 11), root=tmp_path)


def test_P27_a_mis_cased_evidence_path_is_refused_by_name_not_by_the_filesystem(tmp_path):
    """MAC-A's other half, through the real `load`. `Path.is_file()` answers
    differently on APFS and on ext4, so a mis-cased row was evidence on his Mac
    and refused in CI. The rule now compares the row's spelling with the names
    the directory lists, so both machines agree. This probe runs on ext4, where
    the old code already said no for the WRONG REASON, so what it pins is the
    words: 'is spelt' for a case mismatch, 'does not exist' for an absent
    file."""
    from src.yq_inclusion import YqInclusionError

    exact = "docs/yq-checks/2026-09-10-virginatlantic.md"
    table = _load_citing(tmp_path, exact)
    assert table is not None

    for miscased in ("docs/yq-checks/2026-09-10-VirginAtlantic.md",
                     "docs/yq-checks/2026-09-10-VIRGINATLANTIC.MD",
                     "docs/yq-checks/2026-09-10-virginatlantic.MD"):
        with pytest.raises(YqInclusionError) as e:
            _load_citing(tmp_path, miscased)
        msg = str(e.value)
        assert "is spelt" in msg, msg
        assert "2026-09-10-virginatlantic.md" in msg, msg
        assert "does not exist" not in msg, (
            "a mis-cased row is told the file is missing, which sends the reader "
            f"looking for the wrong thing: {msg}")

    with pytest.raises(YqInclusionError) as e:
        _load_citing(tmp_path, "docs/yq-checks/2026-09-10-nosuchairline.md")
    msg = str(e.value)
    assert "does not exist" in msg, msg
    assert "is spelt" not in msg, msg


def test_P28_the_case_rule_is_a_function_of_the_names_the_directory_lists(tmp_path):
    """The property behind P27: the verdict depends on the NAMES, not on what
    the filesystem underneath thinks two names mean. Every shape a
    case-insensitive filesystem produces - upper-cased stem, upper-cased
    extension, both - and the shapes that must stay 'absent'."""
    from src.yq_inclusion import _spelt_differently_on_disk

    root = tmp_path / "repo"
    (root / "docs" / "yq-checks").mkdir(parents=True)
    (root / "docs" / "yq-checks" / "2026-09-10-virginatlantic.md").write_text("x")

    exact = "docs/yq-checks/2026-09-10-virginatlantic.md"
    cases = {
        exact: "",
        "docs/yq-checks/2026-09-10-VIRGINATLANTIC.md": exact,
        "docs/yq-checks/2026-09-10-virginatlantic.MD": exact,
        "docs/yq-checks/2026-09-10-VirginAtlantic.Md": exact,
        "docs/yq-checks/absent.md": "",
        "docs/yq-checks/nosuchdir/b.md": "",
    }
    for row, expected in cases.items():
        got = _spelt_differently_on_disk(root, Path(row))
        assert got == expected, f"{row}: expected {expected!r}, got {got!r}"


def test_P29_a_mis_cased_intermediate_directory_is_named_wrongly(tmp_path):
    """RED, Low. The rule allows a record in a subdirectory (it requires at
    least three path parts, not exactly three). When the mis-cased component is
    a DIRECTORY rather than the file, `_spelt_differently_on_disk` returns the
    path truncated at that component, so the message tells the reader the
    evidence 'is spelt docs/yq-checks/sub on disk' - naming a directory as
    though it were the corrected file. Unreachable at this head, because
    docs/yq-checks/ is flat; reachable the day anyone nests a record."""
    from src.yq_inclusion import _spelt_differently_on_disk

    root = tmp_path / "repo"
    (root / "docs" / "yq-checks" / "sub").mkdir(parents=True)
    (root / "docs" / "yq-checks" / "sub" / "b.md").write_text("x")

    got = _spelt_differently_on_disk(root, Path("docs/yq-checks/SUB/b.md"))
    assert got == "docs/yq-checks/sub/b.md", (
        f"the corrected spelling names a directory, not the file: {got!r}")


def test_P30_reading_names_does_not_become_the_slow_part_of_a_check(tmp_path):
    """A directory with a thousand records, asked a hundred times."""
    import time

    from src.yq_inclusion import _spelt_differently_on_disk

    d = tmp_path / "docs" / "yq-checks"
    d.mkdir(parents=True)
    for i in range(1000):
        (d / f"2026-09-{i:04d}-airline.md").write_text("x")
    t0 = time.monotonic()
    for i in range(0, 1000, 10):
        _spelt_differently_on_disk(tmp_path, Path(f"docs/yq-checks/2026-09-{i:04d}-airline.md"))
    took = time.monotonic() - t0
    assert took < 5.0, f"100 lookups in a 1000-file directory took {took:.2f}s"


def test_P31_the_committed_table_still_loads_under_the_new_rule():
    """If any row in the repository is mis-cased, this is where it shows."""
    from src import yq_inclusion

    table = yq_inclusion.load()
    assert table is not None


def test_P32_the_ui_launcher_starts_under_a_C_locale():
    """The third entry point. Its banner prints the URL and the masked key, and
    a masked key carries U+2026."""
    env = dict(os.environ, LC_ALL="C", LANG="C", PYTHONUTF8="0")
    env.pop("PYTHONIOENCODING", None)
    p = subprocess.run([PY, "-m", "src.ui", "--help"], cwd=str(ROOT),
                       capture_output=True, env=env)
    out = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
    assert p.returncode == 0, out[-400:]
    assert "UnicodeEncodeError" not in out and "Traceback" not in out, out[-400:]
    assert "127.0.0.1" in out, out[-400:]


def test_P33_the_ui_never_reconfigures_the_process_it_is_imported_into(ui):
    """The UI runs the CLI in-process (`from src import main as cli`). Driving a
    real run through the server must not have touched this process's stdout."""
    before = (id(sys.stdout), getattr(sys.stdout, "encoding", None))
    run = ui.run_trip("trip_b_europe", OFFLINE)[1].json()
    assert run.get("argv_display"), run.keys()
    after = (id(sys.stdout), getattr(sys.stdout, "encoding", None))
    assert before == after, (
        f"a UI run reconfigured the importing process's stdout: {before} -> {after}")
