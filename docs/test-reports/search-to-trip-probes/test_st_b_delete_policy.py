"""
B. Delete policy (D7-D9, R1-R3, C1/C2), at the HTTP layer, on tmp copies.
"""
import json
import os
import subprocess

import pytest

from conftest import (REPO_TRIPS, ROOT, eng, g, http_server, tree, ui_built, pinned)  # noqa: F401

UI = "sfo-mad-2027-01-15"


def _pf(c, trip_id):
    return c.post(f"/api/trips/{trip_id}/delete-preflight", {})


def _del(c, trip_id, cid):
    return c.post(f"/api/trips/{trip_id}/delete", {"confirm_id": cid})


def _all_committed():
    return sorted(p.name for p in REPO_TRIPS.glob("*.json"))


def test_B1_every_committed_fixture_is_refused_by_name_and_does_not_move(tmp_path, pinned):
    names = _all_committed()
    assert len(names) >= 7, names
    with http_server(tmp_path) as c:
        before = tree(tmp_path)
        listed = {t["id"] for t in c.get("/api/trips").json()}
        for n in names:
            tid = n[:-5]
            path = c.trips_dir / n
            if tid not in listed:
                assert n.endswith("_answer.json"), n
                assert _pf(c, tid).status == 404 and _del(c, tid, "x").status == 404
                continue
            d = c.get(f"/api/trips/{tid}")
            if d.status == 200:
                assert d.json()["deletable"] is False, tid
                reason = d.json()["not_deletable_reason"]
            r = _pf(c, tid)
            assert r.status == 409 and r.json()["error"] == "not_deletable", (tid, r.text)
            reason = r.json()["message"]
            raw = json.loads(path.read_text())
            src = raw.get("source")
            shown = "" if src is None else str(src)[:80]
            assert reason == eng.NOT_DELETABLE_NOT_BUILT_HERE.format(file=eng.display_path(path), source=shown), (tid, reason)
            assert "\n" not in reason
            r = _del(c, tid, "forged")
            assert r.status == 409 and r.json()["error"] == "confirm_required"
        assert tree(tmp_path) == before


@pytest.mark.parametrize("edit,expect", [
    ("points_empty", "EDITED"), ("points_full", "EDITED"), ("points_null", "EDITED"),
    ("points_on_second_leg", "EDITED"), ("flag_removed", "EDITED"), ("flag_replaced", "EDITED"),
    ("flags_not_a_list", "EDITED"), ("cash_source", "EDITED"), ("extra_cash_source", "EDITED"),
    ("cash_options_empty", "DELETABLE"), ("cash_options_not_list", "EDITED"), ("legs_not_list", "EDITED"),
    ("leg_not_dict", "EDITED"), ("source_rewritten", "NOT_BUILT"), ("source_prefix_kept", "DELETABLE"),
    ("source_null", "NOT_BUILT"), ("source_int", "NOT_BUILT"), ("source_list", "NOT_BUILT"),
    ("source_case", "NOT_BUILT"), ("source_leading_space", "NOT_BUILT"), ("cash_amount", "DELETABLE"),
    ("name_changed", "DELETABLE"), ("description_changed", "DELETABLE"), ("not_json", "UNREADABLE"),
    ("json_list", "UNREADABLE"), ("json_string", "UNREADABLE"), ("empty_file", "UNREADABLE"),
    ("bom", "DELETABLE"), ("nul_byte", "UNREADABLE"), ("duplicate_keys", "EDITED"),
])
def test_B2_a_hand_edited_builder_fixture_is_classified_by_what_changed(tmp_path, pinned, edit, expect):
    with http_server(tmp_path, names=["trip_b_europe.json"]) as c:
        from src import trip_builder
        # two legs, so "second leg" edits mean something
        path = trip_builder.new_trip_from_flags(
            "two", ["SFO:MAD:2027-01-15:2400", "MAD:AMS:2027-01-19:120"], [], cabin="Y",
            directory=c.trips_dir, today=g.PINNED_TODAY)
        raw = path.read_bytes()
        d = json.loads(raw)
        if edit == "points_empty":
            d["legs"][0]["points_candidates"] = []
        elif edit == "points_full":
            d["legs"][0]["points_candidates"] = [{"program": "Aeroplan", "points": 50000}]
        elif edit == "points_null":
            d["legs"][0]["points_candidates"] = None
        elif edit == "points_on_second_leg":
            d["legs"][1]["points_candidates"] = []
        elif edit == "flag_removed":
            d["trip_level_flags"] = []
        elif edit == "flag_replaced":
            d["trip_level_flags"] = ["something else"]
        elif edit == "flags_not_a_list":
            d["trip_level_flags"] = d["trip_level_flags"][0]
        elif edit == "cash_source":
            d["legs"][0]["cash_options"][0]["source"] = "screenshot"
        elif edit == "extra_cash_source":
            d["legs"][0]["cash_options"].append(dict(d["legs"][0]["cash_options"][0], source="google_flights"))
        elif edit == "cash_options_empty":
            d["legs"][0]["cash_options"] = []
        elif edit == "cash_options_not_list":
            d["legs"][0]["cash_options"] = d["legs"][0]["cash_options"][0]
        elif edit == "legs_not_list":
            d["legs"] = d["legs"][0]
        elif edit == "leg_not_dict":
            d["legs"] = [1]
        elif edit == "source_rewritten":
            d["source"] = "screenshot, captured 2026-09-14 by hand"
        elif edit == "source_prefix_kept":
            d["source"] = d["source"] + " and then edited by hand"
        elif edit == "source_null":
            d["source"] = None
        elif edit == "source_int":
            d["source"] = 12
        elif edit == "source_list":
            d["source"] = [d["source"]]
        elif edit == "source_case":
            d["source"] = d["source"].upper()
        elif edit == "source_leading_space":
            d["source"] = " " + d["source"]
        elif edit == "cash_amount":
            d["legs"][0]["cash_options"][0]["amount"] = 1.0
        elif edit == "name_changed":
            d["name"] = "renamed"
        elif edit == "description_changed":
            d["description"] = "D" * 3000
        if edit == "not_json":
            path.write_text("{ not json")
        elif edit == "json_list":
            path.write_text("[1, 2]")
        elif edit == "json_string":
            path.write_text('"a string"')
        elif edit == "empty_file":
            path.write_bytes(b"")
        elif edit == "bom":
            path.write_bytes(b"\xef\xbb\xbf" + raw)
        elif edit == "nul_byte":
            path.write_bytes(raw + b"\x00")
        elif edit == "duplicate_keys":
            # JSON with the key twice: json.loads keeps the LAST. The first
            # says LIVE_ONLY, the last does not - what the loader sees wins.
            body = json.dumps(d, indent=2)
            body = body[:-2] + ',\n  "trip_level_flags": []\n}'
            path.write_text(body)
        else:
            path.write_text(json.dumps(d, indent=2) + "\n")
        before = (path.read_bytes(), os.stat(path).st_mtime_ns)
        det = c.get("/api/trips/two")
        r = _pf(c, "two")
        file = eng.display_path(path)
        if expect == "DELETABLE":
            # A BOM: json.loads(bytes) strips it (utf-8-sig), the loader does not.
            # The raw rule says deletable; the page never offers it (T2 is trip-error).
            assert det.status == (422 if edit == "bom" else 200), (edit, det.text[:300])
            assert det.status != 200 or det.json()["deletable"] is True
            assert r.status == 200, (edit, r.text)
            assert r.json()["lines"] == [f"This removes {file} from disk.", eng.DELETE_LINE_NO_UNDO]
            assert r.json()["description"] == json.loads(path.read_bytes())["description"]
        else:
            assert r.status == 409 and r.json()["error"] == "not_deletable", (edit, r.text)
            if det.status == 200:
                assert det.json()["deletable"] is False and det.json()["not_deletable_reason"] == r.json()["message"]
            msg = r.json()["message"]
            if expect == "EDITED":
                assert msg == eng.NOT_DELETABLE_EDITED.format(file=file), (edit, msg)
            elif expect == "NOT_BUILT":
                assert msg.startswith(f"NOT DELETABLE - {file} was not built by --new-trip or this page (source: \""), (edit, msg)
                assert msg.endswith("\"). The trips that came with the repository are test data; remove them with git, not from here. Nothing was deleted."), msg
            elif expect == "UNREADABLE":
                assert msg == eng.NOT_DELETABLE_UNREADABLE.format(file=file), (edit, msg)
            assert (path.read_bytes(), os.stat(path).st_mtime_ns) == before


def test_B3_r2_quotes_the_source_cut_at_80_and_never_a_newline_or_a_tag_as_markup(tmp_path, pinned):
    with http_server(tmp_path, names=["trip_b_europe.json"]) as c:
        path = ui_built(c.trips_dir)
        d = json.loads(path.read_text())
        d["source"] = '<img src=x onerror=alert(1)>\n"quoted" ' + "S" * 500
        path.write_text(json.dumps(d))
        r = _pf(c, UI)
        msg = r.json()["message"]
        assert r.status == 409
        assert '(source: "' + d["source"][:80] + '")' in msg
        assert len(msg) < 500
        assert "\n" in msg, "the 80-char cut keeps the newline the file had: the page renders it in a <pre>"


def test_B4_a_ui_built_fixture_committed_in_a_git_repo_is_still_deletable_and_c2_says_git(tmp_path, pinned):
    """D9: no git at runtime; a committed UI-built file is deletable and the
    confirm's second line names git as the way back."""
    with http_server(tmp_path, names=["trip_b_europe.json"]) as c:
        path = ui_built(c.trips_dir)
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x", GIT_COMMITTER_NAME="t",
                   GIT_COMMITTER_EMAIL="t@x", HOME=str(tmp_path))
        subprocess.run(["git", "init", "-q"], cwd=c.trips_dir, check=True, env=env)
        subprocess.run(["git", "add", "-A"], cwd=c.trips_dir, check=True, env=env)
        subprocess.run(["git", "commit", "-q", "-m", "x"], cwd=c.trips_dir, check=True, env=env)
        r = _pf(c, UI)
        assert r.status == 200
        assert r.json()["lines"][1] == ("There is no undo in this app. If the file is committed, git can restore it; "
                                        "if it is not, it is gone.")
        assert _del(c, UI, r.json()["confirm_id"]).status == 200
        assert not path.exists()
        out = subprocess.run(["git", "status", "--porcelain"], cwd=c.trips_dir, capture_output=True, text=True, env=env)
        assert out.stdout.rstrip("\n") == f" D {UI}.json"
        # .git/ is not a trip; the listing is unbothered.
        assert {t["id"] for t in c.get("/api/trips").json()} == {"trip_b_europe"}


def test_B5_the_preflight_description_is_the_files_own_and_a_non_string_is_null(tmp_path, pinned):
    with http_server(tmp_path, names=["trip_b_europe.json"]) as c:
        path = ui_built(c.trips_dir)
        r = _pf(c, UI).json()
        assert r["description"].startswith("Built by --new-trip on 2026-09-11: 1 flight leg(s)")
        assert r["path"] == eng.display_path(path) and r["id"] == UI
        assert set(r) == {"id", "path", "description", "confirm_id", "lines"}
        d = json.loads(path.read_text())
        d["description"] = ["not", "a", "string"]
        path.write_text(json.dumps(d))
        r = _pf(c, UI)
        assert r.status == 200 and r.json()["description"] is None
        del d["description"]
        path.write_text(json.dumps(d))
        r = _pf(c, UI)
        assert r.status == 200 and r.json()["description"] is None


def test_B6_the_detail_keys_and_the_listing_shape(tmp_path, pinned):
    with http_server(tmp_path, names=["trip_b_europe.json"]) as c:
        ui_built(c.trips_dir)
        d = c.get(f"/api/trips/{UI}").json()
        assert d["deletable"] is True and d["not_deletable_reason"] is None
        from src.trip_builder import LIVE_ONLY_FLAG
        assert d["flags"] == [LIVE_ONLY_FLAG] and all(l["points_candidates"] == [] for l in d["legs"])
        b = c.get("/api/trips/trip_b_europe").json()
        assert b["deletable"] is False and isinstance(b["not_deletable_reason"], str)
        for row in c.get("/api/trips").json():
            assert "deletable" not in row and "not_deletable_reason" not in row
        # A file that does not LOAD as a trip but is valid JSON with the marks: the
        # detail is 422 (cannot_load) and the preflight decides from the raw json.
        path = c.trips_dir / f"{UI}.json"
        raw = json.loads(path.read_text())
        raw["legs"][0]["date"] = "not a date"
        path.write_text(json.dumps(raw))
        det = c.get(f"/api/trips/{UI}")
        assert det.status == 422, det.text
        r = _pf(c, UI)
        # Provenance says builder, nobody added a points key: the raw rule says deletable.
        assert r.status == 200, r.text
        assert _del(c, UI, r.json()["confirm_id"]).status == 200
        assert not path.exists()


def test_B7_the_last_trip_can_be_deleted_and_the_listing_is_then_empty(tmp_path, pinned):
    with http_server(tmp_path, names=[]) as c:
        path = ui_built(c.trips_dir)
        assert [t["id"] for t in c.get("/api/trips").json()] == [UI]
        r = _pf(c, UI)
        assert _del(c, UI, r.json()["confirm_id"]).status == 200
        assert c.get("/api/trips").json() == []
        assert c.get("/api/state").status == 200
        assert not path.exists() and list(c.trips_dir.iterdir()) == []
