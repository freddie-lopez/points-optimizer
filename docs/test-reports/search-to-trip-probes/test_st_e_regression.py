"""
E. Regression: what the plan says is untouched, the static rules, the
verbatim strings, the docs, and the coder's own string list (the J6 copy).
"""
import re
import subprocess

import pytest

from conftest import ROOT, eng

BASE = "4f879d4"
JS = (ROOT / "src" / "ui" / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "src" / "ui" / "static" / "app.css").read_text(encoding="utf-8")
HTML = (ROOT / "src" / "ui" / "static" / "index.html").read_text(encoding="utf-8")


def git(*args):
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True).stdout


def testids(src):
    """Every data-testid app.js actually registers or looks up.

    Not "any quoted lowercase token": the helpers are `tid(node, "id")` and
    `btn(cls, text, fn, "id")`, so the argument lists are bracket-matched and
    only the testid position is read, plus the literal
    `setAttribute("data-testid", ...)`, `[data-testid="..."]` selectors and the
    `testid:`/`goid:` keys of the confirm-dialog descriptors.
    """
    ids = set()
    for m in re.finditer(r"\b(tid|btn)\(", src):
        name, i, depth, args, cur = m.group(1), m.end(), 1, [], ""
        while i < len(src) and depth > 0:
            c = src[i]
            if c in "\"'":
                j = i + 1
                while j < len(src) and src[j] != c:
                    j += 2 if src[j] == "\\" else 1
                cur += src[i:j + 1]; i = j + 1; continue
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
                if depth == 0:
                    break
            if c == "," and depth == 1:
                args.append(cur); cur = ""; i += 1; continue
            cur += c; i += 1
        args.append(cur)
        pick = args[-1] if name == "tid" else (args[3] if len(args) > 3 else "")
        m2 = re.fullmatch(r'"([a-z0-9][a-z0-9-]*)"', pick.strip())
        if m2:
            ids.add(m2.group(1))
    ids |= set(re.findall(r'setAttribute\(\s*"data-testid"\s*,\s*"([a-z0-9][a-z0-9-]*)"', src))
    ids |= set(re.findall(r'\[data-testid="([a-z0-9][a-z0-9-]*)"\]', src))
    ids |= set(re.findall(r'\b(?:testid|goid):\s*"([a-z0-9][a-z0-9-]*)"', src))
    return ids


def code_only(src):
    """`src` with its comments removed, so a word in a comment is not read as
    a user-facing string."""
    out, inblk = [], False
    for line in src.splitlines():
        s = line.strip()
        if inblk:
            if "*/" not in s:
                continue
            inblk, s = False, s.split("*/", 1)[1]
        if s.startswith("/*"):
            if "*/" not in s:
                inblk = True
                continue
            s = s.split("*/", 1)[1]
        out.append(re.sub(r"//.*$", "", s))
    return "\n".join(out)


def test_E1_the_untouched_list_is_untouched_since_the_base():
    paths = ["src/main.py", "src/formatter.py", "src/trip_builder.py", "src/trip_loader.py", "src/serialize.py",
             "src/ui/server.py", "src/ui/serialize.py", "src/ui/static/map.js", "src/ui/static/index.html",
             "src/seats_client.py", "src/optimizer.py", "src/live_trip.py", "src/regions.py", "data/",
             "tests/fixtures", "tests/test_cli_golden.py", "docs/test-reports/ui-probes",
             "docs/test-reports/ui-restyle-probes", "docs/test-reports/map-search-probes"]
    out = git("diff", f"{BASE}..d6be134", "--stat", "--", *paths)
    assert out.strip() == "", out
    # and the tree the coder handed over is exactly what the report says it changed
    names = git("diff", f"{BASE}..d6be134", "--name-only").split()
    assert sorted(names) == sorted([
        "README.md", "docs/design/search-to-trip-ref/shots/a-search-picked.png",
        "docs/design/search-to-trip-ref/shots/b-new-trip-prefilled.png",
        "docs/design/search-to-trip-ref/shots/c-trip-delete-button.png",
        "docs/design/search-to-trip-ref/shots/d-delete-confirm.png",
        "docs/design/search-to-trip-ref/shots/e-trip-b-refused.png",
        "docs/plans/search-to-trip.md", "docs/plans/ui.md", "runs/search-to-trip/coder-report.md",
        "src/ui/api.py", "src/ui/engine.py", "src/ui/static/app.css", "src/ui/static/app.js",
        "tests/test_ui_api.py", "tests/test_ui_security.py", "tests/test_ui_static_rules.py"]), names


def test_E2_static_rules_of_app_js_still_hold():
    """Converted from a byte/line pin to a behaviour assertion (agreed with
    Tsuki, 2026-09-16); the pin never caught a defect and went red on every
    unrelated change. The line count (2,087 at ecab878) is gone; the banned
    constructs and the `.style.` budget below are the real rules and are
    unchanged."""
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "cssText",
                   "setAttribute(\"style\"", "|| 0", "?? 0", "javascript:", "docs/plans/", "Step 1", "Step 2",
                   "Step 3", "Step 4", "Step 5", "Step 6", "v1 ", "v2 ", "v3 ", "v4 ", "v5 "):
        assert banned not in JS, banned
    base = git("show", f"{BASE}:src/ui/static/app.js")
    assert JS.count(".style.") == base.count(".style.") == 2
    assert "url(" not in CSS and "@import" not in CSS and "http" not in CSS
    assert " style=" not in HTML and "<style" not in HTML


def test_E3_every_testid_of_the_base_survives_and_the_nine_new_ones_exist_once_each():
    """Converted from a byte/line pin to a behaviour assertion (agreed with
    Tsuki, 2026-09-16); the pin never caught a defect and went red on every
    unrelated change. The literal diff `old - new == {"2400"}` read any quoted
    lowercase token as a testid, so removing the cash placeholder - never a
    testid - was indistinguishable from losing one. The rule it stood for is
    below: every testid the base actually registers still exists, plus the new
    ones by name."""
    old = testids(git("show", f"{BASE}:src/ui/static/app.js"))
    new = testids(JS)
    assert old, "the extractor found no testid in the base - it is broken, not the code"
    assert old - new == set(), f"testids of the base that no longer exist: {sorted(old - new)}"
    assert '"trip-list-heading"' in JS
    for t in ("search-add-trip", "search-add-trip-box", "search-add-trip-note", "nt-prefill", "trip-delete",
              "trip-delete-reason", "delete-confirm", "delete-confirm-go", "trip-deleted"):
        assert f'"{t}"' in JS, t   # uniqueness: tests/test_ui_static_rules.py (full suite)
    from tests import test_ui_static_rules as sr
    assert all(t in sr.TESTIDS for t in ("search-add-trip", "trip-delete", "trip-deleted", "delete-confirm-go"))


def test_E4_the_plans_strings_are_verbatim_in_the_code():
    # server (4.4)
    assert eng.NOT_DELETABLE_UNREADABLE == ("NOT DELETABLE - {file} cannot be read as a trip, so where it came from cannot be "
                                            "checked. Nothing was deleted; remove it by hand if you know what it is.")
    assert eng.NOT_DELETABLE_NOT_BUILT_HERE == ("NOT DELETABLE - {file} was not built by --new-trip or this page (source: \"{source}\"). "
                                                "The trips that came with the repository are test data; remove them with git, not "
                                                "from here. Nothing was deleted.")
    assert eng.NOT_DELETABLE_EDITED == ("NOT DELETABLE - {file} was built by --new-trip but has been edited since: it carries "
                                        "points prices, a cash source it did not write, or the flag that says it holds no points "
                                        "prices is gone. It may hold captures nobody can reproduce. Nothing was deleted; remove it "
                                        "by hand if you mean to.")
    assert eng.DELETE_BUSY == ("Nothing was deleted: a run is in progress and may be reading this trip. Wait for it to finish, "
                               "then delete.")
    assert eng.DELETE_CONFIRM_REQUIRED == ("Nothing was deleted: a delete needs a confirmation from its own preflight, and this "
                                           "request carried none or one that was already used.")
    assert eng.DELETE_CONFIRM_STALE == ("Nothing was deleted: the trip file changed since you confirmed, or the confirmation "
                                        "expired. Confirm again.")
    assert eng.NOT_A_REGULAR_FILE_SYMLINK == ("Nothing was deleted: {file} is a symbolic link, and this page only deletes the trip "
                                              "files it wrote.")
    assert eng.NOT_A_REGULAR_FILE_OUTSIDE == "Nothing was deleted: {file} does not resolve to a regular file inside {dir}."
    assert eng.DELETE_LINE_REMOVES == "This removes {file} from disk."
    assert eng.DELETE_LINE_NO_UNDO == ("There is no undo in this app. If the file is committed, git can restore it; if it is "
                                       "not, it is gone.")
    # the spend-confirm sentences are byte-identical to the base (D10, D14)
    base_eng = git("show", f"{BASE}:src/ui/engine.py")
    now_eng = (ROOT / "src" / "ui" / "engine.py").read_text(encoding="utf-8")
    for name in ("CONFIRM_REQUIRED", "CONFIRM_STALE", "BUSY"):
        pat = rf"^{name} = .*?(?=\n\n)"
        assert re.search(pat, base_eng, re.S | re.M).group(0) == re.search(pat, now_eng, re.S | re.M).group(0), name
    assert "Before anything is spent" in JS and JS.count("Before anything is spent") == 1
    # page (P1-P9)
    for s in ('"Add as trip"', '"Pick an award in the results first."', '"Picked: "', '"Prefilled from the search "',
              '"required: the one thing a search cannot know"', '"Delete trip"', '"Before anything is removed"',
              '"Cancel"', '"(program not named)"'):
        assert s in JS, s
    assert JS.count('"(program not named)"') == 4, "the table, the drawer head, P3 and P4"
    assert ('". The award price the search showed is NOT written into this trip: only a LIVE or " +\n'
            '        "REPLAY run can price it. Type the cash fare you found - the search cannot know it."') in JS
    # no marker word of ui-brief 2 in a new sense in the added lines
    d = git("diff", f"{BASE}..d6be134", "--", "src/ui/static/app.js")
    added = "\n".join(l[1:] for l in d.splitlines() if l.startswith("+") and not l.startswith("+++"))
    for marker in ("UNKNOWN", "WITHHELD", "UNVERIFIED", "NOT LOOKED UP", "NOT RECORDED", "NEVER PRICED", "API FAILED",
                   "UNREADABLE", "CONFIRM BEFORE TRUSTING", "REPARSED", "$0"):
        assert marker not in added, marker


# E5 ("the coder's string list is the diff and nothing more") is gone: converted
# from a byte/line pin to a behaviour assertion (agreed with Tsuki, 2026-09-16);
# the pin never caught a defect and went red on every unrelated change. It was a
# hunk count plus an equality between one round's diff and one report's bullet
# list - a bookkeeping check on a document, not on the product, that had to be
# rewritten by hand each round. The rule underneath it is E5b below, which now
# holds for the whole file rather than for one frozen commit range.


def test_E5b_no_user_facing_sentence_was_added_that_the_plan_does_not_name():
    """Converted from a byte/line pin to a behaviour assertion (agreed with
    Tsuki, 2026-09-16); the pin never caught a defect and went red on every
    unrelated change. It compared a hunk count and a literal string list for
    one frozen fix-round diff, which had to be rewritten by hand each round.
    The rule underneath is the one that matters: the page may not start saying
    something the plan never wrote. Every sentence-shaped string literal
    app.js has gained since the base (comments excluded - a comment says
    nothing to the user) must appear in the plans."""
    plans = "".join((ROOT / "docs" / "plans" / n).read_text(encoding="utf-8") for n in ("search-to-trip.md", "ui.md"))
    lits = lambda s: set(re.findall(r'"([^"\\\n]*)"', code_only(s)))
    added = lits(JS) - lits(git("show", f"{BASE}:src/ui/static/app.js"))
    sentences = [s for s in added
                 if len(s) >= 12 and " " in s and not re.fullmatch(r"[a-z0-9 .#\-\[\]=]+", s)]
    unplanned = sorted(s for s in sentences if s not in plans)
    assert unplanned == [], f"user-facing strings in app.js that no plan writes: {unplanned}"


def test_E6_the_css_additions_are_the_reports_list_and_no_rule_changed_or_went():
    d = git("diff", f"{BASE}..d6be134", "--", "src/ui/static/app.css")
    removed = [l for l in d.splitlines() if l.startswith("-") and not l.startswith("---")]
    assert removed == [], removed
    added = [l[1:] for l in d.splitlines() if l.startswith("+") and not l.startswith("+++")]
    rules = [l for l in added if not l.startswith("/*")]
    assert rules == [
        ".btn-warn { background: var(--warn-bg); border-color: var(--warn-line); color: var(--warn); }",
        ".btn-warn:hover:not(:disabled) { border-color: var(--warn); }",
        ".trip-acts { margin-top: 10px; display: grid; gap: 8px; justify-items: start; max-width: 90ch; }",
        ".trip-acts .refusal { justify-self: stretch; }",
        ".addtrip { padding: 12px 16px; display: flex; gap: 12px; align-items: center; justify-content: space-between; flex-wrap: wrap; }",
        ".addtrip .note { flex: 1 1 240px; min-width: 0; max-width: none; }",
        ".nt-prefill { padding: 10px 14px; border: 1px solid var(--line); border-left: 3px solid var(--accent); border-radius: var(--radius); background: var(--accent-tint); font-size: var(--s13); max-width: 90ch; overflow-wrap: anywhere; }",
        ".modal h3, .modal .acts .btn { overflow-wrap: anywhere; min-width: 0; }",
    ], rules
    # the report says "four comment lines"; the diff has five (a report slip, not a rule)
    assert len([l for l in added if l.startswith("/*")]) == 5
    assert "td.cab.sel { background: var(--accent-tint); }" in CSS
    # fix round 1 (ecab878): exactly one rule and one comment, for F2
    d = git("diff", "d6be134..ecab878", "--", "src/ui/static/app.css")
    assert [l for l in d.splitlines() if l.startswith("-") and not l.startswith("---")] == []
    assert [l[1:] for l in d.splitlines() if l.startswith("+") and not l.startswith("+++") and not l[1:].startswith("/*")] == [
        ".funding > div { overflow-wrap: anywhere; min-width: 0; }"]


def test_E7_the_docs_name_the_routes_and_the_readme_test_is_green():
    ui = (ROOT / "docs" / "plans" / "ui.md").read_text(encoding="utf-8")
    assert "/api/trips/{id}/delete-preflight" in ui and "/api/trips/{id}/delete" in ui
    assert "`deletable`" in ui and "`not_deletable_reason`" in ui
    assert "search-add-trip" in ui and "nt-prefill" in ui and "trip-delete" in ui
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "**Delete trip.**" in readme and "no\nundo in this app" in readme
    assert "Add as trip" in readme
    r = subprocess.run([str(ROOT / ".venv" / "bin" / "python"), "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        "tests/test_readme_local_ui_is_accurate.py"], cwd=str(ROOT), capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-2000:]


def test_E8_the_api_route_table_and_the_engine_use_no_path_join_from_the_url():
    api = (ROOT / "src" / "ui" / "api.py").read_text(encoding="utf-8")
    assert api.count("_TRIP}/delete") == 2
    src = (ROOT / "src" / "ui" / "engine.py").read_text(encoding="utf-8")
    body = src.split("def trip_delete_preflight")[1].split("def _trip_options")[0]
    assert "trips_dir /" not in body and "os.path.join" not in body and "Path(trip_id" not in body
    assert body.count("self.trip_path(trip_id)") == 2
    assert "os.unlink(path)" in body and "shutil" not in body and "rmtree" not in body and "rename" not in body
    # the confirm digest binds kind, id and the bytes
    dg = src.split("def _delete_digest")[1].split("def trip_delete_preflight")[0]
    assert '"kind": "delete"' in dg and '"trip_id": trip_id' in dg and "sha256" in dg
