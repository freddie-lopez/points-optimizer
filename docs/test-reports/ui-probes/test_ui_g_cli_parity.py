"""
G. CLI PARITY. The UI plan allows exactly two CLI output changes this round:
F-1 (two table cells: the path cell and the verdict cell of an API-failed or
never-priced leg) and F-3 (the POINTS / "Cash is cheaper" sentence restated
with the post-APD score). The goldens pin 13 invocations. This probe runs a
much wider matrix against the pre-UI CLI (3c104b3, extracted with
`git archive`) and against this tree, in separate processes, under the same
pins, and allows ONLY those two changes.

Also: the goldens at HEAD differ from the goldens recorded before any src
change (bdf2af4) only in G1/G3/G5/G7/G8, and pre_f1_f3/ holds the bdf2af4
bytes exactly.

RED = a defect that exists. GREEN = held up.
"""
import difflib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import HERE, ROOT

PRE_UI = "3c104b3"
GOLDENS_BEFORE_SRC = "bdf2af4"
RUNNER = HERE / "cli_diff_runner.py"
G7 = str(ROOT / "tests" / "fixtures" / "cli_golden" / "inputs" / "g7_never_priced_couple.json")
W = ["--balance", "UR=160000", "--card", "Chase Sapphire Preferred", "--transfer-date", "2026-09-15"]
FIXTURES = ["trip_001.json", "trip_002.json", "trip_a_mry_nyc.json", "trip_b_europe.json",
            "trip_c_lon_mry_surcharge.json", G7]


def _scenarios():
    s = {}
    for f in FIXTURES:
        n = Path(f).stem
        s[f"off-{n}"] = {"argv": ["--trip-fixture", f, "--offline", *W]}
        s[f"off-alt-{n}"] = {"argv": ["--trip-fixture", f, "--offline", *W, "--show-alternatives"]}
        s[f"off-ral-{n}"] = {"argv": ["--trip-fixture", f, "--offline", *W, "--require-all-live"]}
    for f in ("trip_a_mry_nyc.json", "trip_b_europe.json", "trip_c_lon_mry_surcharge.json"):
        n = Path(f).stem
        s[f"live-{n}"] = {"argv": ["--trip-fixture", f, *W], "transport": "stub", "key": True}
        s[f"live-alt-{n}"] = {"argv": ["--trip-fixture", f, *W, "--show-alternatives", "--trips", "all"],
                              "transport": "stub", "key": True}
        s[f"down-{n}"] = {"argv": ["--trip-fixture", f, *W], "transport": "refused", "key": True}
    s["live-g7-offdate"] = {"argv": ["--trip-fixture", G7, *W], "transport": "offdate", "key": True}
    s["live-g7-ondate"] = {"argv": ["--trip-fixture", G7, *W], "transport": "ondate", "key": True}
    s["live-b-page3"] = {"argv": ["--trip-fixture", "trip_b_europe.json", *W, "--trips", "all"],
                         "transport": "page3", "key": True}
    s["live-b-endless"] = {"argv": ["--trip-fixture", "trip_b_europe.json", *W],
                           "transport": "endless", "key": True}
    s["live-b-flex2"] = {"argv": ["--trip-fixture", "trip_b_europe.json", *W, "--flex-days", "2"],
                         "transport": "stub", "key": True}
    s["live-b-trips-off"] = {"argv": ["--trip-fixture", "trip_b_europe.json", *W, "--trips", "off"],
                             "transport": "stub", "key": True}
    s["live-b-allow-badge"] = {"argv": ["--trip-fixture", "trip_b_europe.json", *W,
                                        "--allow-badge-fallback"], "transport": "refused", "key": True}
    s["live-b-nokey"] = {"argv": ["--trip-fixture", "trip_b_europe.json", *W], "transport": "stub"}
    live_b = ["--trip-fixture", "trip_b_europe.json", *W]
    s["replay-b"] = {"argv": [*live_b, "--from-snapshot", "snapshots/MANIFEST.md"],
                     "before": [live_b], "transport": "stub", "key": True}
    s["replay-b-refused"] = {"argv": [*live_b, "--from-snapshot", "snapshots/MANIFEST.md"],
                             "before": [live_b], "drop": "B2_*.json", "transport": "stub", "key": True}
    for label, bal in (("blank", "UR="), ("zero", "UR=0"), ("tight", "UR=30000"), ("mr", "MR=100000")):
        s[f"off-b-wallet-{label}"] = {"argv": ["--trip-fixture", "trip_b_europe.json", "--offline",
                                               "--balance", bal, "--card", "Chase Sapphire Preferred",
                                               "--transfer-date", "2026-09-15"]}
        s[f"live-b-wallet-{label}"] = {"argv": ["--trip-fixture", "trip_b_europe.json",
                                                "--balance", bal, "--card", "Chase Sapphire Preferred",
                                                "--transfer-date", "2026-09-15"],
                                       "transport": "stub", "key": True}
    s["nowallet"] = {"argv": ["--trip-fixture", "trip_b_europe.json", "--offline"]}
    srch = ["--origin", "SFO", "--destination", "MAD", "--date", "2027-01-15", *W[:4]]
    s["search-ok"] = {"argv": srch, "transport": "stub", "key": True}
    s["search-down"] = {"argv": srch, "transport": "refused", "key": True}
    s["search-empty"] = {"argv": srch, "transport": "empty", "key": True}
    s["search-endless"] = {"argv": srch, "transport": "endless", "key": True}
    s["search-page3"] = {"argv": srch, "transport": "page3", "key": True}
    s["search-mr"] = {"argv": ["--origin", "SFO", "--destination", "MAD", "--date", "2027-01-15",
                               "--balance", "MR=100000"], "transport": "stub", "key": True}
    s["search-lhr"] = {"argv": ["--origin", "LHR", "--destination", "SFO", "--date", "2027-01-27",
                                *W[:4]], "transport": "stub", "key": True}
    s["conflict-off-live"] = {"argv": ["--trip-fixture", "trip_b_europe.json", "--offline", "--live", *W]}
    s["conflict-snap-refresh"] = {"argv": [*live_b, "--from-snapshot", "x.md", "--refresh"]}
    s["bad-trips-cap"] = {"argv": [*live_b, "--trips-cap", "0"], "key": True, "transport": "stub"}
    return s


SCENARIOS = _scenarios()


@pytest.fixture(scope="module")
def old_tree(tmp_path_factory):
    d = tmp_path_factory.mktemp("pre_ui_tree")
    tar = subprocess.run(["git", "archive", PRE_UI], cwd=ROOT, check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(d)], input=tar, check=True)
    return d


def _run(tree, scen):
    p = subprocess.run([sys.executable, str(RUNNER), str(tree), json.dumps(scen)],
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stderr[-3000:]
    out = json.loads(p.stdout)
    assert out["attempts"] == [], f"a real connection was attempted: {out['attempts']}"
    return out


F1_OLD = "none - not a partner"
F1_NEW = ("no live data", "never priced")
F3 = re.compile(r"(Points path scores|Cash is cheaper:) ")
BORDER = re.compile(r"^[\s┏┳┓┡╇┩└┴┘━─┃│]*$")


def _cells(line):
    return [c.strip() for c in re.split(r"[│┃]", line)]


_F3_PTS = re.compile(r"Points path scores \$([\d,.]+) vs \$([\d,.]+) cash "
                     r"\(points \$([\d,.]+) \+ UK APD \$([\d,.]+)\)\.")
_F3_CASH = re.compile(r"Cash is cheaper: \$([\d,.]+) vs \$([\d,.]+) on points at "
                      r"([\d.]+cpp) \(points \$([\d,.]+) \+ UK APD \$([\d,.]+)\)\.")


def _money(t):
    return float(t.replace(",", ""))


def _f3_back(text):
    """Undo an F-3 restatement (checking its arithmetic) so the result can be
    compared with the pre-UI sentence. Returns None if the sums do not add up."""
    bad = []

    def pts(m):
        if abs(_money(m.group(1)) - _money(m.group(3)) - _money(m.group(4))) > 0.011:
            bad.append(m.group(0))
        return f"Points path scores ${m.group(3)} vs ${m.group(2)} cash."

    def cash(m):
        if abs(_money(m.group(2)) - _money(m.group(4)) - _money(m.group(5))) > 0.011:
            bad.append(m.group(0))
        return f"Cash is cheaper: ${m.group(1)} vs ${m.group(4)} on points at {m.group(3)}."

    out = _F3_CASH.sub(cash, _F3_PTS.sub(pts, text))
    return None if bad else out


def _same_but_f3(old_lines, new_lines):
    """A block of lines that differs only by an F-3 restatement, however it re-wraps."""
    olds = re.sub(r"\s+", " ", " ".join(old_lines)).strip()
    news = re.sub(r"\s+", " ", " ".join(new_lines)).strip()
    if olds == news or "UK APD $" not in news:
        return False
    back = _f3_back(news)
    return back is not None and re.sub(r"\s+", " ", back) == olds


def _allowed(a, b):
    """Is old line `a` -> new line `b` one of the two sanctioned changes?"""
    if BORDER.match(a) and BORDER.match(b):
        return True  # a column changed width
    if a.strip() == b.strip():
        return True  # a centred title re-padded
    ca, cb = _cells(a), _cells(b)
    if ("│" in a or "┃" in a) and len(ca) == len(cb):
        diffs = [(x, y) for x, y in zip(ca, cb) if x != y]
        for x, y in diffs:
            if x == F1_OLD and y in F1_NEW:
                continue
            if x.startswith("PAY CASH (no path)") and y.startswith("PAY CASH (never priced)"):
                continue
            if x.startswith("PAY CASH (no pat") and y.startswith("PAY CASH (never pric"):
                continue
            # header cells re-wrapped by a width change
            if x in ("", "Best points path", "Verdict") and y in ("", "Best points path", "Verdict"):
                continue
            return False
        return True
    if F3.search(a) and F3.search(b) and "UK APD $" in b:
        return True
    return False


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_G1_cli_output_differs_from_the_pre_ui_cli_only_by_F1_and_F3(old_tree, name):
    scen = SCENARIOS[name]
    old = _run(old_tree, scen)
    new = _run(ROOT, scen)
    assert str(old["code"]) == str(new["code"]), (old["code"], new["code"])
    a, b = old["out"].splitlines(), new["out"].splitlines()
    bad = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if _same_but_f3(a[i1:i2], b[j1:j2]):
            continue
        if tag == "replace" and i2 - i1 == j2 - j1:
            for x, y in zip(a[i1:i2], b[j1:j2]):
                if not _allowed(x, y):
                    bad.append(("-" + x, "+" + y))
            continue
        bad.append(("-" + "\n-".join(a[i1:i2]), "+" + "\n+".join(b[j1:j2])))
    assert not bad, "\n".join(f"{x}\n{y}" for x, y in bad[:6])


# ----------------------------------------------------------------- goldens


def _git_show(rev, path):
    return subprocess.run(["git", "show", f"{rev}:{path}"], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout


@pytest.mark.parametrize("n", [f"G{i}" for i in range(1, 14)])
def test_G2_goldens_changed_only_where_claimed(n):
    before = _git_show(GOLDENS_BEFORE_SRC, f"tests/fixtures/cli_golden/{n}.txt")
    now = (ROOT / "tests" / "fixtures" / "cli_golden" / f"{n}.txt").read_text()
    if n in ("G1", "G3", "G5", "G7", "G8"):
        pre = (ROOT / "tests" / "fixtures" / "cli_golden" / "pre_f1_f3" / f"{n}.txt").read_text()
        assert pre == before, f"pre_f1_f3/{n}.txt is not the golden recorded before any src change"
        a, b = before.splitlines(), now.splitlines()
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
            if tag == "equal":
                continue
            assert tag == "replace" and i2 - i1 == j2 - j1, (a[i1:i2], b[j1:j2])
            for x, y in zip(a[i1:i2], b[j1:j2]):
                assert _allowed(x, y), (x, y)
    else:
        assert now == before, f"{n} changed although no F-1/F-3 line is in it"
