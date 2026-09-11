"""
Re-test 5 (fix round 4: 34509a5..a8283da). The new behaviour attacked:
R4-1 across its class (yq-check prints no row and writes the
"yq-check: NO VERDICT POSSIBLE" marker when no nonzero band is modelled; the
loader requires exactly one nonzero band line and refuses the marker), and the
changelog rewording in --help and two replay refusals.

RED = a defect that exists. GREEN = held up.
"""
import ast
import importlib.util
import re
import subprocess
import sys

import pytest

from conftest import ROOT, YQ_DEFAULT_BAND, yq_load, yq_record_body, yq_write_record  # noqa: F401
from src import trips_tools, yq_inclusion
from src.yq_inclusion import YqInclusionError
from tests import _trips_payloads as tp
from test_oa_r4_retest import CHANGELOG, _fill, _itin, _jfk_lhr_row, _template, _yq

ROUND4 = "34509a5"
GOOD = "virginatlantic,{airline},{verdict},2026-09-10,{ev},{notes}"


# ===========================================================================
# 1. The tool side of the class: every check that cannot tell includes from
#    excludes prints no row, writes the marker, and - filled honestly, with a
#    row typed by hand because none was printed - backs nothing.
# ===========================================================================


def _multi():
    r = _jfk_lhr_row()
    return tp.payload([tp.trip([tp.segment("VS4", "JFK", "BOS", 1, AvailabilityID=r["ID"]),
                                tp.segment("DL1", "BOS", "LHR", 2)], availability_id=r["ID"], cost=60000)])


def _ambiguous():
    trips = tp.payload([_itin(), _itin("DL1")])
    trips["data"][1]["ID"] = "t2"
    return trips


# name -> (row, trips, yq-check kwargs, the airlines a hand-typed row could plausibly name)
NO_VERDICT = {
    "VS W, JFK-LHR (no W row)": (
        lambda: _jfk_lhr_row(extra_cabins={"W": {"cost": "40000", "taxes": 25000, "airlines": "VS"}}),
        lambda: tp.payload([_itin(cabin="premium", cost=40000)]), {"cabin": "W"}, ["VS"]),
    "VS J, JFK-LOS (no NA-AF row)": (
        lambda: {**_jfk_lhr_row(), "Route": {**_jfk_lhr_row()["Route"], "DestinationAirport": "LOS"}},
        lambda: tp.payload([_itin(d="LOS")]), {"d": "LOS"}, ["VS"]),
    "DL single metal (no DL row at all)": (
        _jfk_lhr_row, lambda: tp.payload([_itin("DL1")]), {}, ["DL", "VS"]),
    "KNOWN multi-carrier VS+DL": (_jfk_lhr_row, _multi, {}, ["VS", "DL"]),
    "AMBIGUOUS VS|DL": (_jfk_lhr_row, _ambiguous, {}, ["VS", "DL"]),
}


def _run(tmp_path, monkeypatch, name):
    monkeypatch.setattr(trips_tools, "ROOT", tmp_path)
    make_row, make_trips, kw, _ = NO_VERDICT[name]
    return _yq(tmp_path, make_row(), make_trips(), **kw)


@pytest.mark.parametrize("name", list(NO_VERDICT))
def test_no_verdict_check_prints_no_row_and_writes_the_marker(tmp_path, monkeypatch, name):
    code, out, rec = _run(tmp_path, monkeypatch, name)
    assert code == 0
    assert "<VERDICT>" not in out and _template(out) is None and _template(out, "DL") is None
    assert "This check cannot back a verdict" in out and "no row is printed" in out
    text = rec.read_text()
    assert len(re.findall(r"^- yq-check: NO VERDICT POSSIBLE - ", text, re.M)) == 1
    # and the band line the tool wrote carries no dollar amount, so the marker is not the only guard
    band = re.findall(r"^- modelled carrier surcharge band: (.*)$", text, re.M)
    assert len(band) == 1 and "$" not in band[0], band


@pytest.mark.parametrize("verdict", ["includes_yq", "excludes_yq"])
@pytest.mark.parametrize("name", list(NO_VERDICT))
def test_no_verdict_record_filled_honestly_backs_nothing_for_any_hand_typed_row(tmp_path, monkeypatch,
                                                                                 name, verdict):
    code, out, rec = _run(tmp_path, monkeypatch, name)
    _fill(rec, verdict=verdict)
    ev = rec.relative_to(tmp_path).as_posix()
    for airline in NO_VERDICT[name][3]:
        with pytest.raises(YqInclusionError):
            yq_load(tmp_path, GOOD.format(airline=airline, verdict=verdict, ev=ev, notes="typed by hand"))


@pytest.mark.parametrize("name", list(NO_VERDICT))
def test_deleting_only_the_marker_line_still_backs_nothing(tmp_path, monkeypatch, name):
    code, out, rec = _run(tmp_path, monkeypatch, name)
    _fill(rec)
    rec.write_text(re.sub(r"^- yq-check: NO VERDICT POSSIBLE - .*\n", "", rec.read_text(), flags=re.M))
    assert "NO VERDICT" not in rec.read_text()
    ev = rec.relative_to(tmp_path).as_posix()
    for airline in NO_VERDICT[name][3]:
        with pytest.raises(YqInclusionError):
            yq_load(tmp_path, GOOD.format(airline=airline, verdict="includes_yq", ev=ev, notes="x"))


def test_a_band_bearing_j_check_on_another_na_eu_route_still_loads_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(trips_tools, "ROOT", tmp_path)
    r = _jfk_lhr_row()
    r["Route"]["OriginAirport"], r["Route"]["DestinationAirport"] = "BOS", "MAN"
    code, out, rec = _yq(tmp_path, r, tp.payload([_itin(o="BOS", d="MAN")]), o="BOS", d="MAN")
    assert "NO VERDICT POSSIBLE" not in rec.read_text()
    _fill(rec, verdict="excludes_yq", total="USD 525.00")
    template = _template(out)
    assert template
    assert yq_load(tmp_path, template.replace("<VERDICT>", "excludes_yq"))[("virginatlantic", "VS")].excludes


@pytest.mark.parametrize("name", list(NO_VERDICT))
def test_the_key_is_in_no_no_verdict_output_record_or_capture(tmp_path, monkeypatch, name):
    from test_oa_r4_retest import KEY

    code, out, rec = _run(tmp_path, monkeypatch, name)
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert rec in files and any(p.suffix == ".json" for p in files)
    assert KEY not in out
    for p in files:
        assert KEY not in p.read_text(errors="replace"), p


@pytest.mark.parametrize("name", ["VS W, JFK-LHR (no W row)", "VS J, JFK-LOS (no NA-AF row)"])
def test_R5_2_a_vs_metal_check_with_no_band_is_not_told_to_pick_a_flight_on_vs_metal(tmp_path, monkeypatch,
                                                                                     name):
    """
    The flight IS on VS metal; what it lacks is a modelled band (VS has one only
    for J on NA-EU). The new last line says so ("run the check there"), but the
    older WARNING, printed first and written into the record, still tells Tsuki
    to "pick a flight on VS metal" - which she did. Following it re-runs the same
    kind of check (2 more API calls) and gets the same refusal.
    """
    code, out, rec = _run(tmp_path, monkeypatch, name)
    assert "checked airline (the award's KNOWN flight-number carrier): VS" in rec.read_text()
    assert "cabin J on NA-EU routes - run the check there" in out
    for text in (out, " ".join(rec.read_text().split())):
        assert "pick a flight on VS metal" not in text


# ===========================================================================
# 2. R5-1: the loader reads the band line as text. It never checks it against
#    the record's own route/cabin/airline or data/surcharges.csv.
# ===========================================================================


def _forge(rec, band):
    text = rec.read_text()
    text = re.sub(r"^- yq-check: NO VERDICT POSSIBLE - .*\n", "", text, flags=re.M)
    text = re.sub(r"^(- modelled carrier surcharge band: ).*$", lambda m: m.group(1) + band, text, flags=re.M)
    rec.write_text(text)


@pytest.mark.parametrize("name,band", [
    ("VS W, JFK-LHR (no W row)", YQ_DEFAULT_BAND),                                 # copied from a J record
    ("VS W, JFK-LHR (no W row)", "$200-$350 (pt $275) one way (VS metal, cabin W)"),  # edited to match
    ("VS J, JFK-LOS (no NA-AF row)", YQ_DEFAULT_BAND),                             # right cabin, wrong region
])
def test_R5_1_a_band_line_the_surcharge_table_does_not_model_for_the_records_own_route_is_refused(
        tmp_path, monkeypatch, name, band):
    """
    The record states its own route and cabin ("- route: JFK->LHR on ..., cabin W")
    and airline ("checked airline: VS"). data/surcharges.csv models no VS band
    there, so no nonzero band line can be true for it. The loader could recompute
    the band from those lines (the same _modelled_band yq-check used) and compare;
    it only reads the band text, so a record whose marker is deleted and whose
    band line is replaced backs a verdict D1 then applies to every VS award.
    """
    code, out, rec = _run(tmp_path, monkeypatch, name)
    _fill(rec)
    _forge(rec, band)
    assert re.search(r"^- route: .*cabin [A-Z]$", rec.read_text(), re.M)
    ev = rec.relative_to(tmp_path).as_posix()
    try:
        loaded = yq_load(tmp_path, GOOD.format(airline="VS", verdict="includes_yq", ev=ev, notes="x"))
    except YqInclusionError:
        loaded = None
    assert loaded is None, f"a {name} record with a band the table does not model backed {sorted(loaded)}"


# ===========================================================================
# 3. The band line: currency, format, count
# ===========================================================================


@pytest.mark.parametrize("band,why", [
    (None, "has 0 'modelled carrier surcharge band' lines"),
    ("", "no nonzero surcharge band"),
    ("NONE MODELLED for VS metal, so the site total cannot tell includes from excludes", "no nonzero"),
    ("none - the lookup did not name one KNOWN airline", "no nonzero"),
    ("n/a", "no nonzero"),
    ("$0", "no nonzero"),
    ("$0.00", "no nonzero"),
    ("$0-$0 (pt $0) one way (VS metal, cabin J)", "no nonzero"),
    ("GBP 200-350 one way", "no nonzero"),
    ("£200-£350 (pt £275) one way", "no nonzero"),
    ("USD 200-350", "no nonzero"),
    ("200-350", "no nonzero"),
    ("$-200", "no nonzero"),
    ("TBD", "no nonzero"),
])
def test_a_band_line_that_is_missing_zero_or_not_in_dollars_is_refused(tmp_path, band, why):
    ev = yq_write_record(tmp_path, yq_record_body(band=band))
    with pytest.raises(YqInclusionError, match=re.escape(why)):
        yq_load(tmp_path, GOOD.format(airline="VS", verdict="includes_yq", ev=ev, notes="x"))


@pytest.mark.parametrize("where", ["seats", "site"])
def test_two_band_lines_are_refused_even_if_both_are_nonzero(tmp_path, where):
    second = "- modelled carrier surcharge band: $200-$350 (pt $275) one way (VS metal, cabin J)\n"
    body = yq_record_body(extra=second) if where == "seats" else yq_record_body() + second
    ev = yq_write_record(tmp_path, body)
    with pytest.raises(YqInclusionError, match="has 2 'modelled carrier surcharge band' lines"):
        yq_load(tmp_path, GOOD.format(airline="VS", verdict="includes_yq", ev=ev, notes="x"))


def test_a_band_line_in_another_case_counts_as_no_band_line(tmp_path):
    body = yq_record_body(band=None, extra="- Modelled Carrier Surcharge Band: $200-$350\n")
    ev = yq_write_record(tmp_path, body)
    with pytest.raises(YqInclusionError, match="has 0 'modelled carrier surcharge band' lines"):
        yq_load(tmp_path, GOOD.format(airline="VS", verdict="includes_yq", ev=ev, notes="x"))


@pytest.mark.parametrize("band", [
    YQ_DEFAULT_BAND,
    "$ 200-$ 350 (pt $ 275) one way (VS metal, cabin J)",
    "$1,200-$1,350",
    YQ_DEFAULT_BAND + "   \t",
])
def test_a_nonzero_dollar_band_loads(tmp_path, band):
    ev = yq_write_record(tmp_path, yq_record_body(band=band))
    assert yq_load(tmp_path, GOOD.format(airline="VS", verdict="includes_yq", ev=ev, notes="x"))


def test_a_crlf_record_with_a_band_loads_and_a_crlf_record_without_one_does_not(tmp_path):
    ev = yq_write_record(tmp_path, yq_record_body().replace("\n", "\r\n"), "a.md")
    assert yq_load(tmp_path, GOOD.format(airline="VS", verdict="includes_yq", ev=ev, notes="x"))
    ev = yq_write_record(tmp_path, yq_record_body(band="NONE MODELLED").replace("\n", "\r\n"), "b.md")
    with pytest.raises(YqInclusionError, match="no nonzero"):
        yq_load(tmp_path, GOOD.format(airline="VS", verdict="includes_yq", ev=ev, notes="x"))


# ===========================================================================
# 4. The marker: case and whitespace. Each record also carries a NONZERO band,
#    so only the marker can refuse it.
# ===========================================================================


@pytest.mark.parametrize("marker", [
    "- yq-check: NO VERDICT POSSIBLE - the lookup is AMBIGUOUS",
    "- YQ-CHECK: no verdict possible - x",
    "- Yq-Check : No Verdict Possible",
    "-yq-check:NO VERDICT POSSIBLE",
    "   -   yq-check   :   NO VERDICT POSSIBLE   ",
    "\t- yq-check:\tNO VERDICT POSSIBLE",
])
@pytest.mark.parametrize("half", ["seats", "site"])
def test_the_marker_in_any_case_and_spacing_is_refused_whatever_the_band_says(tmp_path, marker, half):
    body = yq_record_body(extra=marker + "\n") if half == "seats" else yq_record_body() + marker + "\n"
    ev = yq_write_record(tmp_path, body)
    with pytest.raises(YqInclusionError, match="NO VERDICT POSSIBLE"):
        yq_load(tmp_path, GOOD.format(airline="VS", verdict="includes_yq", ev=ev, notes="x"))


def test_the_marker_in_a_crlf_record_is_refused(tmp_path):
    body = yq_record_body(extra="- yq-check: NO VERDICT POSSIBLE - x\n").replace("\n", "\r\n")
    ev = yq_write_record(tmp_path, body)
    with pytest.raises(YqInclusionError, match="NO VERDICT POSSIBLE"):
        yq_load(tmp_path, GOOD.format(airline="VS", verdict="includes_yq", ev=ev, notes="x"))


@pytest.mark.parametrize("marker", [
    "- yq-check: NO  VERDICT  POSSIBLE - x",   # double spaces inside the phrase: the marker regex misses it
    "* yq-check: NO VERDICT POSSIBLE - x",     # another bullet: the marker regex misses it
    "yq-check: NO VERDICT POSSIBLE - x",       # no bullet: the marker regex misses it
])
def test_a_garbled_marker_on_a_real_no_band_record_is_still_refused_by_the_band_line(tmp_path, marker):
    """Observation, not a finding: these spellings evade the marker regex, but a record
    yq-check wrote without a band still carries a band line with no dollar amount."""
    body = yq_record_body(band="NONE MODELLED for VS metal, so the site total cannot tell includes "
                               "from excludes", extra=marker + "\n")
    ev = yq_write_record(tmp_path, body)
    with pytest.raises(YqInclusionError, match="no nonzero"):
        yq_load(tmp_path, GOOD.format(airline="VS", verdict="includes_yq", ev=ev, notes="x"))


def test_the_marker_the_tool_writes_is_the_loaders_literal():
    assert yq_inclusion.NO_VERDICT_MARKER == "yq-check: NO VERDICT POSSIBLE"
    assert yq_inclusion.RECORD_NO_VERDICT_RE.search("- " + yq_inclusion.NO_VERDICT_MARKER + " - x")


# ===========================================================================
# 5. Cabin: the row's notes are free text; a verdict is keyed (source, airline)
# ===========================================================================


def test_row_notes_naming_another_cabin_do_not_change_what_a_j_record_backs(tmp_path):
    """D1(b) as specified: the verdict is per (source, airline). Notes are informational."""
    ev = yq_write_record(tmp_path, yq_record_body())
    t = yq_load(tmp_path, GOOD.format(airline="VS", verdict="includes_yq", ev=ev, notes="JFK-LHR W 2027-02-10"))
    assert list(t) == [("virginatlantic", "VS")]


# ===========================================================================
# 6. The rewording: text only, no exit code or remedy lost
# ===========================================================================


def _git_show(rev, path):
    return subprocess.run(["git", "show", f"{rev}:{path}"], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout


class _Blank(ast.NodeTransformer):
    def visit_Constant(self, node):
        return ast.copy_location(ast.Constant(value=""), node) if isinstance(node.value, str) else node

    def visit_JoinedStr(self, node):
        return ast.copy_location(ast.Constant(value=""), node)


def _shape(src):
    return ast.dump(_Blank().visit(ast.parse(src)), include_attributes=False)


@pytest.mark.parametrize("path", ["src/main.py", "src/snapshot_replay.py"])
def test_the_rewording_changed_only_string_literals(path):
    assert _shape(_git_show(ROUND4, path)) == _shape((ROOT / path).read_text())


REWORDED_HELP = [
    ("LIVE (the default as of v5)", "LIVE (the default)"),
    ("(this is the DEFAULT as of v5; --allow-badge-fallback opts out)",
     "(this is the DEFAULT; --allow-badge-fallback opts out)"),
    ("DEFAULTS AS OF v5:", "DEFAULTS:"),
    ("building a trip (v5) - writes", "building a trip - writes"),
    ("Kept for v0 compatibility.", "Kept so older scripts keep working."),
    ("live trip mode (v3) - points", "live trip mode - points"),
    ("ACCEPTED AND NOW A NO-OP: as of v5 --trip-fixture is live by",
     "ACCEPTED AND NOW A NO-OP: --trip-fixture is now live by"),
    ("ACCEPTED AND NOW A NO-OP: as of v5 this is the DEFAULT and",
     "ACCEPTED AND NOW A NO-OP: this is now the DEFAULT and"),
]


def _help_of(src, name, monkeypatch):
    monkeypatch.setenv("COLUMNS", "2000")  # no wrapping, so textwrap cannot split "opt-out"
    spec = importlib.util.spec_from_loader(name, loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = str(ROOT / "src" / "main.py")  # same directory, so its relative paths resolve alike
    sys.modules[name] = mod
    try:
        exec(compile(src, f"<{name}>", "exec"), mod.__dict__)
        return " ".join(mod.build_parser().format_help().split())
    finally:
        sys.modules.pop(name, None)


def test_help_is_round_4_help_with_exactly_the_listed_rewordings_and_nothing_dropped(monkeypatch):
    old = _help_of(_git_show(ROUND4, "src/main.py"), "_r4_main", monkeypatch)
    new = _help_of((ROOT / "src/main.py").read_text(), "_head_main", monkeypatch)
    for before, after in REWORDED_HELP:
        assert before in old, before
        old = old.replace(before, after)
    assert old == new
    for code in ("0 ", "1 ", "2 WALLET ERROR", "3 WITHHELD", "4 NOT EXECUTABLE"):
        assert code in new


@pytest.mark.parametrize("module", ["src.main", "src.trips_tools"])
def test_help_exits_0_and_carries_no_changelog_text(module):
    p = subprocess.run([sys.executable, "-m", module, "--help"], cwd=ROOT, capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "COLUMNS": "200", "HOME": "/nonexistent"})
    assert p.returncode == 0, p.stderr
    assert not CHANGELOG.search(p.stdout), CHANGELOG.search(p.stdout).group(0)


def test_the_old_row_refusal_still_exits_1_and_still_says_what_to_do(tmp_path, capsys):
    from tests.test_from_snapshot import REPLAY_BASE, build_corpus, run_cli

    manifest, _ = build_corpus(tmp_path)
    lines = []
    for line in manifest.read_text().splitlines():
        if line.startswith("| 2027-"):
            line = "|" + "|".join(line.strip().strip("|").split("|")[:8]) + "|"
        lines.append(line)
    manifest.write_text("\n".join(lines) + "\n")
    code, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(manifest)], capsys)
    flat = " ".join(out.split())
    assert code == 1 and "CANNOT BE REPLAYED" in flat and "content_hash_unknown" in flat
    for needle in ("written by an older version of the tool", "nothing to check the file against",
                   "UNKNOWN, never as 'matches'", "Re-fetch the leg"):
        assert needle in flat, needle
    assert not CHANGELOG.search(flat), CHANGELOG.search(flat).group(0)


def test_the_archived_budget_refusal_still_calls_the_manifest_corrupt(tmp_path):
    from src import snapshot_replay
    from tests.test_snapshot_manifest import _three_row_manifest

    manifest, snap = _three_row_manifest(tmp_path)
    manifest.write_text(manifest.read_text().replace("| ok | b2_synth.json", "| budget_exhausted | b2_synth.json"))
    probs = [p for p in snapshot_replay.verify(snapshot_replay.parse_manifest(manifest), snap)
             if p.kind == "impossible_state_archived"]
    assert probs
    text = " ".join(" ".join(p.render() for p in probs).split())
    for needle in ("BUDGET_EXHAUSTED against an archived snapshot", "never cached and never archived",
                   "cannot exist", "The manifest is corrupt"):
        assert needle in text, needle
    assert not CHANGELOG.search(text)
