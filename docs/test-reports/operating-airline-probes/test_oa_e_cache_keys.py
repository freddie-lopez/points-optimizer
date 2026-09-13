"""
E. Cache, snapshot, manifest, key material and path injection.
"""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from conftest import (  # noqa: F401
    BASE, B4_VS, Stub, evaluate, flat, row, run_cli, vs_b4_rows, vs_itinerary,
)
from src import config
from src.models import MetalStatus
from src.response_cache import ResponseCache, request_key
from src.seats_client import SeatsClient
from src import seats_trips
from tests import _trips_payloads as tp

KEY = "PROBEKEYzz9f8e7d6c5b4a3"


def _all_text(*dirs):
    out = []
    for d in dirs:
        d = Path(d)
        if d.exists():
            for p in d.rglob("*"):
                if p.is_file():
                    out.append((p, p.read_text(errors="replace")))
    return out


@pytest.mark.parametrize("how", ["env", "dotenv", "flag"])
def test_the_key_is_in_no_trips_file_and_not_in_the_output(how, tmp_path, capsys, monkeypatch):
    argv = BASE + ["--allow-badge-fallback"]
    env_key = False
    if how == "env":
        env_key = True
    elif how == "dotenv":
        envfile = tmp_path / "repo.env"
        envfile.write_text(f"SEATS_AERO_KEY={KEY}\n")
        monkeypatch.setattr(config, "_ENV_PATH", envfile)
    else:
        argv += ["--api-key", KEY]
    stub = Stub(rows_for=vs_b4_rows(), trips={B4_VS: tp.payload([vs_itinerary(B4_VS)])})
    code, out, fl, stub = run_cli(argv, capsys, monkeypatch, stub=stub, key=KEY, env_key=env_key)
    assert stub.trips_calls, "precondition: a trips request was made"
    assert all(c[2].get("Partner-Authorization") == KEY for c in stub.calls)
    files = _all_text(config.CACHE_DIR, config.SNAPSHOT_DIR)
    assert any("trips_endpoint" in str(p) for p, _ in files), "precondition: a trips snapshot was archived"
    for p, text in files:
        assert KEY not in text, p
        assert "Partner-Authorization" not in text, p
    assert KEY not in out


@pytest.mark.parametrize("how", ["env", "flag"])
def test_a_trips_body_that_reflects_the_key_is_not_written(how, tmp_path, capsys, monkeypatch):
    argv = BASE + ["--allow-badge-fallback"] + (["--api-key", KEY] if how == "flag" else [])
    body = tp.payload([vs_itinerary(B4_VS)], echo=KEY)
    stub = Stub(rows_for=vs_b4_rows(), trips={B4_VS: body})
    code, out, fl, stub = run_cli(argv, capsys, monkeypatch, stub=stub, key=KEY, env_key=(how == "env"))
    trips_files = [(p, t) for p, t in _all_text(config.CACHE_DIR, config.SNAPSHOT_DIR)
                   if "trips" in str(p)]
    for p, text in trips_files:
        assert KEY not in text, p
    assert "COULD NOT ARCHIVE the trips response" in fl or "REFUSING TO WRITE" in fl
    assert KEY not in out


def test_a_padded_key_never_reaches_a_header_error_or_the_output(tmp_path, monkeypatch):
    """
    requests validates header values while PREPARING a request, and its
    InvalidHeader message quotes the value - the key - which TRANSPORT_ERROR
    would print. Held up: SeatsClient re-resolves the key through the flag path,
    which strips it, so an env key with stray whitespace validates cleanly.
    """
    from io import StringIO
    from unittest.mock import patch
    from rich.console import Console
    from src import trips_tools

    def prepare_then_answer(url, **kw):
        requests.Request("GET", url, headers=kw.get("headers"), params=kw.get("params")).prepare()
        r = MagicMock()
        r.status_code = 200
        body = tp.payload([vs_itinerary(B4_VS)])
        r.json.return_value = body
        r.text = json.dumps(body)
        return r

    monkeypatch.setenv("SEATS_AERO_KEY", "  " + KEY + " \t")
    buf = StringIO()
    with patch("src.seats_client.requests.get", side_effect=prepare_then_answer):
        code = trips_tools.main(
            ["capture", "--availability-id", B4_VS, "--yes", "--out-dir", str(tmp_path / "real")],
            console=Console(file=buf, width=250), read=lambda _: "y",
        )
    out = buf.getvalue()
    assert KEY not in out, out
    for p, text in _all_text(tmp_path / "real"):
        assert KEY not in text


# ---------------------------------------------------------------------------
# An availability id with a trailing newline passes the gate (see test_oa_b)
# ---------------------------------------------------------------------------

NL_ID = B4_VS + "\n"


def test_a_newline_id_builds_no_request_and_no_manifest_row(tmp_path):
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=tmp_path / "s")
    stub = Stub(rows_for=vs_b4_rows(rid=NL_ID), trips={NL_ID: tp.payload([vs_itinerary(NL_ID)])})
    results, totals, text, opts, fx = evaluate(stub, cache=cache)
    assert stub.trips_calls == [], "a request was built from an id carrying a newline"
    manifest = tmp_path / "s" / "trips_endpoint" / "MANIFEST.md"
    if manifest.exists():
        for line in manifest.read_text().splitlines():
            if line.startswith("|") and "trips:" in line:
                assert line.rstrip().endswith("|"), f"a manifest row was split: {line!r}"


# ---------------------------------------------------------------------------
# Way (9): the stored coverage and the recomputed one, unioned - and USED
# ---------------------------------------------------------------------------


def test_a_cache_entry_that_records_incomplete_never_reads_known(tmp_path):
    """
    `trips_raw` unions the envelope's stored `incomplete` with the one it
    recomputes from the bytes (way nine: "neither can answer complete for the
    other") into RawTripsResult.incomplete. The metal pass then ignores that
    field and re-derives completeness from the payload alone, so a stored
    INCOMPLETE is dropped on the floor and the lookup reads KNOWN.
    """
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=None, ttl_seconds=3600)
    evaluate(Stub(rows_for=vs_b4_rows(), trips={B4_VS: tp.payload([vs_itinerary(B4_VS)])}), cache=cache)
    files = list((tmp_path / "c" / "trips").glob("*.json"))
    assert len(files) == 1
    env = json.loads(files[0].read_text())
    env["_meta"]["incomplete"] = True
    env["_meta"]["incomplete_reason"] = "the stored record says the list was cut short"
    files[0].write_text(json.dumps(env))
    for p in (tmp_path / "c").glob("*.json"):
        p.unlink()
    stub = Stub(rows_for=vs_b4_rows(), trips={B4_VS: tp.payload([vs_itinerary(B4_VS)])})
    results, *_ = evaluate(stub, cache=cache)
    assert stub.trips_calls == [], "precondition: served from the cache"
    assert results["B4"].best_points.metal.status is not MetalStatus.KNOWN


def test_a_wrong_shape_2xx_reads_the_same_unknown_twice(tmp_path):
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=tmp_path / "s", ttl_seconds=3600)
    r1, *_ = evaluate(Stub(rows_for=vs_b4_rows(), trips={B4_VS: {"data": {"x": 1}}}), cache=cache)
    stub = Stub(rows_for=vs_b4_rows(), trips={B4_VS: tp.payload([vs_itinerary(B4_VS)])})
    r2, *_ = evaluate(stub, cache=cache)
    m1, m2 = r1["B4"].best_points.metal, r2["B4"].best_points.metal
    assert m1.reason_code == m2.reason_code == "SHAPE_ERROR"
    assert m2.served_from_cache and stub.trips_calls == []


def test_trips_files_stay_out_of_the_search_globs(tmp_path):
    cache = ResponseCache(cache_dir=tmp_path / "c", snapshot_dir=tmp_path / "s")
    evaluate(Stub(rows_for=vs_b4_rows(), trips={B4_VS: tp.payload([vs_itinerary(B4_VS)])}), cache=cache)
    assert not [p for p in (tmp_path / "c").glob("*.json") if "trips" in p.read_text()[:2000]
                and '"endpoint": "trips"' in p.read_text()]
    assert not [p for p in (tmp_path / "s").glob("*.json") if '"endpoint": "trips"' in p.read_text()]
    assert all("trips" not in Path(str(p)).name for p in cache.snapshots())


def test_the_request_is_exactly_the_documented_one():
    stub = Stub(rows_for=vs_b4_rows(), trips={B4_VS: tp.payload([vs_itinerary(B4_VS)])})
    evaluate(stub)
    url, params, headers = [c for c in stub.calls if "/trips/" in c[0]][0]
    assert url == f"https://seats.aero/partnerapi/trips/{B4_VS}"
    assert params == {"include_filtered": "false", "min_cabin_pct": "100"}
    assert set(headers) == {"Partner-Authorization", "Accept"}
