"""
A. Delete security, at the HTTP layer (docs/plans/search-to-trip.md 7).

Every server here runs on a tmp copy of the fixtures. Before and after every
probe the tmp tree is walked with lstat mtimes; the repository's own
tests/fixtures/trips/ is compared by mtime in the harness.
"""
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from conftest import (LIVE, OFFLINE, ROOT, eng, g, http_server, tree, ui_built, pinned)  # noqa: F401

UI = "sfo-mad-2027-01-15"
R4 = "No trip {id!r} in {dir}."


def _pf(c, trip_id):
    return c.post(f"/api/trips/{trip_id}/delete-preflight", {})


def _del(c, trip_id, cid):
    return c.post(f"/api/trips/{trip_id}/delete", {"confirm_id": cid})


# ------------------------------------------------------------- traversal


@pytest.mark.parametrize("trip_id", [
    "../trip_b_europe", "..%2f..%2fsrc%2fconfig", "%2e%2e%2f%2e%2e%2fREADME.md", "..%5c..%5cREADME",
    "trip_b_europe%00", "trip_b_europe%00.json", "trip_b_europe.json", "trip_b_europe.", "trip_b_europe%20",
    "TRIP_B_EUROPE", "Trip_B_Europe", "x" * 121, "x" * 4000, "adir.json", "adir", ".", "..", "%2e",
    "trip_b_europe%2f", "trip_b_europe/", "%74rip_b_europe", "trip_b_europe;rm", "trip_b_europe%3f",
    "trip_%75_europe", "trip_b_europe%E2%80%8B", "%EF%BC%8E%EF%BC%8E", "e%CC%81", "..%00",
    "trip_b_europe%2fdelete", "trip_b_europe%2f..%2ftrip_b_europe", "%2ftmp%2fx",
    "-", "_", "trip_b_europe%0a", "trip_b_europe%0d%0aX-PO-Token:%20x",
])
def test_A1_a_delete_id_that_is_not_a_listing_key_is_404_on_both_routes(tmp_path, pinned, trip_id):
    with http_server(tmp_path, names=["trip_b_europe.json"]) as c:
        (c.trips_dir / "adir.json").mkdir()
        (c.trips_dir / "adir.json" / "inner.json").write_text("{}")
        before = tree(tmp_path)
        for suffix in ("/delete-preflight", "/delete"):
            r = c.post(f"/api/trips/{trip_id}{suffix}", {"confirm_id": "x"})
            assert r.status == 404, (trip_id, suffix, r.status, r.text[:200])
            # And the sentence never echoes a decoded path segment as a path.
            assert "not_found" == r.json()["error"]
        assert tree(tmp_path) == before


def test_A2_a_symlink_to_a_deletable_file_outside_the_dir_is_refused_and_the_target_lives(tmp_path, pinned):
    outside = ui_built(tmp_path / "elsewhere", name="victim")
    with http_server(tmp_path, names=["trip_b_europe.json"]) as c:
        (c.trips_dir / "link.json").symlink_to(outside)
        (c.trips_dir / "rel.json").symlink_to(Path("..") / "elsewhere" / "victim.json")
        (c.trips_dir / "readme.json").symlink_to(ROOT / "README.md")
        (c.trips_dir / "dirlink.json").symlink_to(tmp_path / "elsewhere")
        before = tree(tmp_path)
        ids = [t["id"] for t in c.get("/api/trips").json()]
        assert "dirlink" not in ids, "a symlink to a directory is not a trip"
        for tid in ("link", "rel", "readme"):
            assert tid in ids, (tid, ids)
            d = c.get(f"/api/trips/{tid}")
            if d.status == 200:
                assert d.json()["deletable"] is False
                assert d.json()["not_deletable_reason"].startswith("Nothing was deleted: ")
                assert " is a symbolic link, and this page only deletes the trip files it wrote." in d.json()["not_deletable_reason"]
            r = _pf(c, tid)
            assert r.status == 409 and r.json()["error"] == "not_deletable", (tid, r.text)
            assert str(c.trips_dir / f"{tid}.json") in r.json()["message"], "R8 names the LINK, not its target"
            assert "elsewhere" not in r.json()["message"] and "README" not in r.json()["message"]
            r = _del(c, tid, "x")
            assert r.status == 409
        # /delete with a confirm minted for the real file, used on the link: stale, not a delete.
        pf = _pf(c, "trip_b_europe")
        assert pf.status == 409
        assert tree(tmp_path) == before
    assert outside.exists() and (ROOT / "README.md").exists()


def test_A3_a_hard_link_carries_its_targets_provenance(tmp_path, pinned):
    """A hard link to Trip B inside the dir reads as Trip B: R2. A hard link to
    a UI-built file is the same inode as a UI-built file: deletable by design
    (plan 7) - and the delete removes ONE name, the other survives."""
    outside = ui_built(tmp_path / "elsewhere", name="victim")
    with http_server(tmp_path, names=["trip_b_europe.json"]) as c:
        os.link(c.trips_dir / "trip_b_europe.json", c.trips_dir / "hard_b.json")
        os.link(outside, c.trips_dir / "hard_ui.json")
        d = c.get("/api/trips/hard_b").json()
        assert d["deletable"] is False and "was not built by --new-trip or this page" in d["not_deletable_reason"]
        assert _pf(c, "hard_b").status == 409
        d = c.get("/api/trips/hard_ui").json()
        assert d["deletable"] is True
        pf = _pf(c, "hard_ui")
        assert pf.status == 200
        r = _del(c, "hard_ui", pf.json()["confirm_id"])
        assert r.status == 200
        assert not (c.trips_dir / "hard_ui.json").exists()
        assert outside.exists() and json.loads(outside.read_text())["name"] == "victim"
        assert (c.trips_dir / "trip_b_europe.json").exists() and (c.trips_dir / "hard_b.json").exists()


def test_A4_a_directory_named_like_a_trip_and_a_trip_named_like_an_answer_file(tmp_path, pinned):
    with http_server(tmp_path, names=["trip_b_europe.json", "trip_001_answer.json"]) as c:
        (c.trips_dir / "x.json").mkdir()
        (c.trips_dir / "x.json" / "trip_b_europe.json").write_text("{}")
        ids = [t["id"] for t in c.get("/api/trips").json()]
        assert "x" not in ids and "trip_001_answer" not in ids
        for tid in ("x", "x.json", "trip_001_answer", "x%2ftrip_b_europe"):
            assert _pf(c, tid).status == 404, tid
            assert _del(c, tid, "x").status == 404, tid
        assert (c.trips_dir / "x.json").is_dir() and (c.trips_dir / "trip_001_answer.json").exists()


# -------------------------------------------------------------- confirms


def test_A5_every_confirm_that_is_not_this_trips_own_fresh_preflight_is_refused(tmp_path, pinned):
    with http_server(tmp_path, names=["trip_b_europe.json"]) as c:
        a = ui_built(c.trips_dir, name="a")
        b = ui_built(c.trips_dir, name="b", leg="MAD:AMS:2027-01-19:120")
        before = tree(tmp_path)
        pa = _pf(c, "a").json()["confirm_id"]
        pb = _pf(c, "b").json()["confirm_id"]
        # A's confirm on B and B's on A: stale (digest mismatch), and both consumed.
        r = _del(c, "b", pa)
        assert (r.status, r.json()["error"], r.json()["message"]) == (409, "confirm_stale", eng.DELETE_CONFIRM_STALE)
        r = _del(c, "a", pb)
        assert (r.status, r.json()["error"]) == (409, "confirm_stale")
        r = _del(c, "a", pa)
        assert (r.status, r.json()["error"], r.json()["message"]) == (409, "confirm_required", eng.DELETE_CONFIRM_REQUIRED)
        # A RUN confirm (offline needs none, LIVE does) minted for A, used to delete A.
        pf = c.post("/api/trips/a/preflight", LIVE)
        assert pf.status == 200 and pf.json().get("confirm_id"), pf.text
        r = _del(c, "a", pf.json()["confirm_id"])
        assert (r.status, r.json()["error"]) == (409, "confirm_stale")
        # A search confirm used on delete.
        sp = c.post("/api/search/preflight", {"origin": "SFO", "destination": "MAD", "date": "2027-01-15"})
        if sp.status == 200 and sp.json().get("confirm_id"):
            r = _del(c, "a", sp.json()["confirm_id"])
            assert (r.status, r.json()["error"]) == (409, "confirm_stale")
        # A delete confirm on /run and on /search/run.
        pa = _pf(c, "a").json()["confirm_id"]
        r = c.post("/api/trips/a/run", dict(LIVE, confirm_id=pa))
        assert r.status == 409 and r.json()["error"] == "confirm_stale", r.text
        pa = _pf(c, "a").json()["confirm_id"]
        r = c.post("/api/search/run", {"origin": "SFO", "destination": "MAD", "date": "2027-01-15", "confirm_id": pa})
        assert r.status in (400, 409), r.text
        # Shapes: list, dict, bool, huge string, the digest itself.
        for cid in ([pa], {"id": pa}, True, "a" * 70000 if False else "a" * 60000, eng.canonical_digest({"kind": "delete"})):
            r = c.post("/api/trips/a/delete", {"confirm_id": cid})
            assert r.status in (409, 413), (type(cid), r.status, r.text[:100])
            if r.status == 409:
                assert r.json()["error"] == "confirm_required"
        # Confirm in the query string / a header, not the body.
        r = c.post(f"/api/trips/a/delete?confirm_id={_pf(c, 'a').json()['confirm_id']}", {})
        assert r.status in (404, 409)
        r = c.post("/api/trips/a/delete", {}, headers={"X-Confirm-Id": _pf(c, "a").json()["confirm_id"]})
        assert r.status == 409 and r.json()["error"] == "confirm_required"
        assert tree(tmp_path) == before
        assert a.exists() and b.exists()


def test_A6_a_confirm_minted_before_an_edit_that_keeps_provenance_is_stale(tmp_path, pinned):
    """The digest binds the BYTES, not the provenance: a cash edit after the
    preflight makes the confirm stale even though the file stays deletable."""
    with http_server(tmp_path, names=["trip_b_europe.json"]) as c:
        path = ui_built(c.trips_dir)
        cid = _pf(c, UI).json()["confirm_id"]
        d = json.loads(path.read_text())
        d["legs"][0]["cash_options"][0]["amount"] = 1.0
        path.write_text(json.dumps(d, indent=2) + "\n")
        assert c.get(f"/api/trips/{UI}").json()["deletable"] is True
        r = _del(c, UI, cid)
        assert (r.status, r.json()["error"]) == (409, "confirm_stale")
        assert path.exists()
        # Same bytes rewritten (touch only): NOT stale - the rule is bytes.
        cid = _pf(c, UI).json()["confirm_id"]
        raw = path.read_bytes()
        time.sleep(0.01)
        path.write_bytes(raw)
        r = _del(c, UI, cid)
        assert r.status == 200, r.text
        assert not path.exists()


def test_A7_a_confirm_from_a_previous_launch_is_nothing_here(tmp_path, pinned):
    cid = None
    with http_server(tmp_path, names=["trip_b_europe.json"]) as c:
        ui_built(c.trips_dir)
        cid = _pf(c, UI).json()["confirm_id"]
    with http_server(tmp_path / "again", names=["trip_b_europe.json"]) as c:
        path = ui_built(c.trips_dir)
        r = _del(c, UI, cid)
        assert (r.status, r.json()["error"]) == (409, "confirm_required")
        assert path.exists()


def test_A8_the_ttl_boundary(tmp_path, pinned, monkeypatch):
    with http_server(tmp_path, names=["trip_b_europe.json"]) as c:
        path = ui_built(c.trips_dir)
        real = time.monotonic
        t0 = real()
        monkeypatch.setattr(eng.time, "monotonic", lambda: t0)
        cid = _pf(c, UI).json()["confirm_id"]
        monkeypatch.setattr(eng.time, "monotonic", lambda: t0 + eng.CONFIRM_TTL_SECONDS)   # exactly at expiry: still good
        r = _del(c, UI, cid)
        assert r.status == 200, r.text
        assert not path.exists()
        path = ui_built(c.trips_dir)
        monkeypatch.setattr(eng.time, "monotonic", lambda: t0)
        cid = _pf(c, UI).json()["confirm_id"]
        monkeypatch.setattr(eng.time, "monotonic", lambda: t0 + eng.CONFIRM_TTL_SECONDS + 0.001)
        r = _del(c, UI, cid)
        assert (r.status, r.json()["error"]) == (409, "confirm_stale")
        assert path.exists()


# ------------------------------------------------------------------ gates


@pytest.mark.parametrize("suffix", ["/delete-preflight", "/delete"])
def test_A9_origin_token_host_method_content_type_and_size_gates(tmp_path, pinned, suffix):
    with http_server(tmp_path, names=["trip_b_europe.json"]) as c:
        path = ui_built(c.trips_dir)
        cid = _pf(c, UI).json()["confirm_id"]
        body = {"confirm_id": cid}
        p = f"/api/trips/{UI}{suffix}"
        r = c.post(p, body, origin=False)
        assert r.status == 403 and r.json()["error"] == "forbidden_origin"
        for o in ("http://evil.com", "null", f"http://localhost:{c.port}.evil.com", f"http://127.0.0.1:{c.port + 1}",
                  f"https://127.0.0.1:{c.port}", f"HTTP://127.0.0.1:{c.port}", f"http://127.0.0.1:{c.port}/",
                  f"http://127.0.0.1:{c.port} http://evil.com", f"http://0177.0.0.1:{c.port}", f"http://[::1]:{c.port}"):
            r = c.post(p, body, origin=o)
            assert r.status == 403, (o, r.status, r.text[:100])
        r = c.post(p, body, token=False)
        assert r.status == 403 and r.json()["error"] == "stale_page"
        r = c.post(p, body, token="x" * 64)
        assert r.status == 403
        r = c.post(p, body, token=c.token.upper() if c.token != c.token.upper() else c.token[::-1])
        assert r.status == 403
        r = c.post(p, body, host="evil.com")
        assert r.status in (403, 421, 400), r.status
        r = c.post(p, body, host=None)
        assert r.status in (403, 421, 400), r.status
        for m in ("GET", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD", "TRACE", "PROPFIND"):
            r = c.request(m, p)
            # TRACE/PROPFIND: the stdlib handler's own 501 (pre-existing, every route).
            assert r.status == (501 if m in ("TRACE", "PROPFIND") else 405), (m, r.status)
            if m in ("TRACE", "PROPFIND"):
                continue
            if m == "GET":
                assert r.json()["message"] == f"{p} does not accept GET."
            elif m != "HEAD":
                assert r.json()["message"] == "Only GET and POST are accepted."
        r = c.post(p, body, content_type="text/plain")
        assert r.status in (400, 415), r.status
        r = c.post(p, body, content_type="application/x-www-form-urlencoded")
        assert r.status in (400, 415), r.status
        r = c.request("POST", p, raw=b'{"confirm_id": "' + b"a" * (65 * 1024) + b'"}')
        assert r.status == 413, r.status
        r = c.request("POST", p, raw=b"[1, 2]")
        assert r.status in (400, 409, 200), r.status
        assert r.status != 200 or suffix == "/delete-preflight"
        r = c.request("POST", p, raw=b"not json")
        assert r.status == 400
        r = c.request("POST", p, raw=b"")
        assert r.status in (400, 409, 200)
        # Whatever was refused, the file is still there, and the key is in no log line.
        assert path.exists()
        for line in c.logs:
            assert g.FAKE_KEY not in line and c.token not in line and "confirm_id" not in line
            assert line.split(" ")[0] in ("GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD", "TRACE", "PROPFIND", "REFUSED")


# ------------------------------------------------------------------ races


def test_A10_two_threads_one_confirm_exactly_one_wins(tmp_path, pinned):
    with http_server(tmp_path, names=["trip_b_europe.json"]) as c:
        for i in range(10):
            path = ui_built(c.trips_dir, name=f"race{i}")
            cid = _pf(c, f"race{i}").json()["confirm_id"]
            results = []
            barrier = threading.Barrier(2)

            def hit():
                barrier.wait()
                results.append(_del(c, f"race{i}", cid))
            ts = [threading.Thread(target=hit) for _ in range(2)]
            [t.start() for t in ts]
            [t.join() for t in ts]
            codes = sorted(r.status for r in results)
            assert codes[0] == 200 and codes[1] in (404, 409), [(r.status, r.text[:80]) for r in results]
            loser = [r for r in results if r.status != 200][0]
            assert loser.json()["error"] in ("confirm_required", "not_found"), loser.text
            assert not path.exists()


def test_A11_two_confirms_two_threads_one_file(tmp_path, pinned):
    """Preflight twice, delete twice at once: exactly one 200, the file gone,
    nothing else touched. What the loser SAYS is recorded here (404 R4, 409
    busy, or - about one run in ten - 409 not_deletable R1) and pinned
    deterministically by A15, so this probe is not left to the scheduler."""
    with http_server(tmp_path, names=["trip_b_europe.json"]) as c:
        seen = []
        for i in range(12):
            path = ui_built(c.trips_dir, name=f"twin{i}")
            c1 = _pf(c, f"twin{i}").json()["confirm_id"]
            c2 = _pf(c, f"twin{i}").json()["confirm_id"]
            results = []
            barrier = threading.Barrier(2)

            def hit(cid):
                barrier.wait()
                results.append(_del(c, f"twin{i}", cid))
            ts = [threading.Thread(target=hit, args=(cid,)) for cid in (c1, c2)]
            [t.start() for t in ts]
            [t.join() for t in ts]
            codes = sorted(r.status for r in results)
            assert codes[0] == 200, [(r.status, r.text[:80]) for r in results]
            loser = [r for r in results if r.status != 200][0]
            seen.append((loser.status, loser.json()["error"], loser.json()["message"][:90]))
            assert not path.exists()
        print("A11 losers:", sorted(set(s[:2] for s in seen)))
        assert all(s[:2] in ((404, "not_found"), (409, "busy"), (409, "not_deletable")) for s in seen), seen


def test_A12_delete_during_a_real_run_and_during_an_artificial_lock(tmp_path, pinned):
    from unittest.mock import patch
    gate = threading.Event()
    started = threading.Event()

    class Slow(g.Stub):
        def __call__(self, url, **kw):
            started.set()
            gate.wait(20)
            return super().__call__(url, **kw)

    with http_server(tmp_path, names=["trip_b_europe.json"]) as c:
        with patch("src.seats_client.requests.get", side_effect=Slow()):
            path = ui_built(c.trips_dir)
            other = ui_built(c.trips_dir, name="other", leg="MAD:AMS:2027-01-19:120")
            cid = _pf(c, UI).json()["confirm_id"]
            pf = c.post(f"/api/trips/{UI}/preflight", LIVE).json()
            out = []
            t = threading.Thread(target=lambda: out.append(c.post(f"/api/trips/{UI}/run", dict(LIVE, confirm_id=pf["confirm_id"]))))
            t.start()
            assert started.wait(20), "the run never reached the transport"
            # The run holds the lock on THIS trip; delete this one and another.
            for tid in (UI, "other"):
                r = _pf(c, tid)
                assert (r.status, r.json()) == (409, {"error": "busy", "message": eng.DELETE_BUSY}), (tid, r.text)
            r = _del(c, UI, cid)
            assert (r.status, r.json()) == (409, {"error": "busy", "message": eng.DELETE_BUSY})
            gate.set()
            t.join(60)
            run = out[0]
            assert run.status == 200, run.text
            rid = run.json()["run_id"]
            # After the run: the delete succeeds and the stored run is still served.
            cid = _pf(c, UI).json()["confirm_id"]
            assert _del(c, UI, cid).status == 200
            assert not path.exists() and other.exists()
            r = c.get(f"/api/runs/{rid}")
            assert r.status == 200 and r.json()["run_id"] == rid
            assert c.get(f"/api/trips/{UI}").status == 404
            # An OFFLINE run of the deleted trip is 404, not a crash.
            assert c.post(f"/api/trips/{UI}/run", OFFLINE).status == 404


def test_A13_create_and_delete_of_the_same_name_interleaved(tmp_path, pinned):
    """Write from the form, delete, write again with the same name: the writer
    must accept the name again (the refusal is 'already exists', and it no
    longer does), and the second file is byte-identical to the first."""
    with http_server(tmp_path, names=["trip_b_europe.json"]) as c:
        body = {"name": UI, "cabin": "Y", "legs": [{"origin": "SFO", "destination": "MAD",
                                                   "date": "2027-01-15", "cabin": "Y", "cash": "2400"}]}
        d = c.post("/api/trips/draft", body).json()
        assert c.post("/api/trips/create", dict(body, draft_hash=d["draft_hash"])).status == 200
        first = (c.trips_dir / f"{UI}.json").read_bytes()
        r = c.post("/api/trips/create", dict(body, draft_hash=d["draft_hash"]))
        assert r.status == 409 and "already exists" in r.json()["message"]
        cid = _pf(c, UI).json()["confirm_id"]
        assert _del(c, UI, cid).status == 200
        assert c.post("/api/trips/create", dict(body, draft_hash=d["draft_hash"])).status == 200
        assert (c.trips_dir / f"{UI}.json").read_bytes() == first
        assert not list(c.trips_dir.glob("*.tmp")) and not list(c.trips_dir.glob(".*"))


def test_A14_the_tmp_tree_and_the_repo_fixtures_never_move_outside_the_one_unlink(tmp_path, pinned):
    with http_server(tmp_path) as c:
        path = ui_built(c.trips_dir)
        before = tree(tmp_path)
        assert _pf(c, UI).status == 200
        assert tree(tmp_path) == before, "a preflight moved something"
        cid = _pf(c, UI).json()["confirm_id"]
        assert _del(c, UI, cid).status == 200
        after = tree(tmp_path)
        gone = set(before) - set(after)
        assert gone == {str(path.relative_to(tmp_path))}
        changed = {k for k in after if k != "trips" and after[k] != before.get(k)}
        assert changed == set(), changed
        assert not list(c.trips_dir.glob("*.tmp"))


def test_A15_a_second_delete_that_redeemed_before_the_first_unlink_says_the_file_is_unreadable(tmp_path, pinned, monkeypatch):
    """The interleaving A11 hits once in ~10 runs, made deterministic: delete
    #2 passes trip_path and redeems its (valid) confirm, then delete #1 runs
    to completion, then #2 takes the lock. The plan's pseudo-code re-checks
    provenance under the lock; on a file that is now GONE that re-check
    returns R1 - 'cannot be read as a trip ... remove it by hand if you know
    what it is' - for a file this very page just deleted. The honest answer
    is the 404 (R4) a second delete of the id returns a moment later."""
    with http_server(tmp_path, names=["trip_b_europe.json"]) as c:
        path = ui_built(c.trips_dir)
        c1 = _pf(c, UI).json()["confirm_id"]
        c2 = _pf(c, UI).json()["confirm_id"]
        real = eng.Engine.redeem_confirm
        done = {"first": False}

        def redeem(self, confirm_id, digest, **kw):
            real(self, confirm_id, digest, **kw)
            if confirm_id == c2 and not done["first"]:
                done["first"] = True
                # Between #2's redeem and #2's lock, #1 runs start to finish.
                assert self.trip_delete(UI, {"confirm_id": c1})["lines"][0].startswith("Deleted ")

        monkeypatch.setattr(eng.Engine, "redeem_confirm", redeem)
        r = _del(c, UI, c2)
        assert not path.exists()
        assert r.status == 404, (r.status, r.text)
        assert r.json()["error"] == "not_found", r.text
