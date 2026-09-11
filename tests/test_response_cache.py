"""
v3 Steps 1 and 2: the raw-response disk cache and the snapshot corpus.

THE THING BEING PROTECTED: raw pages are cached, not parsed Awards. v2's parser
was wrong for the entire life of the project and the fix was only possible
because one raw response happened to exist. A cache of parsed objects would have
frozen the bug in place and a parser fix would have had to spend fresh API calls
to prove itself.
"""
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src import response_cache
from src.models import DateRange
from src.response_cache import (
    CACHE_SCHEMA,
    ResponseCache,
    assert_no_key_material,
    canonical_request,
    content_hash,
    envelope_from_raw_payload,
    load_envelope,
    request_key,
)
from src.seats_client import SeatsClient, parse_availability_row

ROOT = Path(__file__).parent.parent
REAL_FIXTURE = ROOT / "tests" / "fixtures" / "seats_aero" / "sfo_mad_real.json"
COMMITTED_SNAPSHOTS = ROOT / "tests" / "fixtures" / "seats_aero" / "live_trip_b"

RANGE = DateRange(date(2027, 1, 15), date(2027, 1, 15))


@pytest.fixture
def real_payload():
    """
    The captured SFO->MAD response, RESPONSE-SHAPED.

    `sfo_mad_real.json` is a capture WRAPPER: it carries a `_capture` block
    documenting the request, the truncation and (by name only) the auth header.
    A real Seats.aero response has none of that, and the cache would rightly
    refuse to write a payload that names the auth header. So the tests below use
    the response body alone, which is what the transport layer actually sees.
    """
    captured = json.loads(REAL_FIXTURE.read_text())
    return {"data": captured["data"]}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("SEATS_AERO_KEY", "test_key_not_a_real_one")
    c = SeatsClient(api_key="test_key_not_a_real_one")
    c.clear_cache()
    SeatsClient.reset_call_budget()
    yield c
    c.clear_cache()
    SeatsClient.reset_call_budget()


@pytest.fixture
def cache(tmp_path):
    return ResponseCache(
        cache_dir=tmp_path / "cache",
        snapshot_dir=tmp_path / "snapshots",
    )


def _mock_response(payload, status=200):
    r = MagicMock()
    r.json.return_value = payload
    r.status_code = status
    r.raise_for_status.return_value = None
    return r


# ---------------------------------------------------------------------------
# Step 1: keys
# ---------------------------------------------------------------------------


def test_the_key_is_a_hash_of_a_canonical_sorted_parameter_string():
    params = {
        "origin_airport": "SFO",
        "destination_airport": "MAD",
        "start_date": "2027-01-15",
        "end_date": "2027-01-15",
    }
    canonical = canonical_request("search", params)
    assert canonical.startswith(f"v{CACHE_SCHEMA}|search|")
    # Sorted, so dict ordering cannot change a key.
    assert canonical == canonical_request("search", dict(reversed(list(params.items()))))
    assert request_key("search", params) == request_key(
        "search", dict(reversed(list(params.items())))
    )
    assert len(request_key("search", params)) == 64


@pytest.mark.parametrize(
    "field,value",
    [
        ("origin_airport", "SJC"),
        ("destination_airport", "BCN"),
        ("start_date", "2027-01-16"),
        ("end_date", "2027-01-18"),
    ],
)
def test_changing_any_request_parameter_changes_the_key(field, value):
    base = {
        "origin_airport": "SFO",
        "destination_airport": "MAD",
        "start_date": "2027-01-15",
        "end_date": "2027-01-15",
    }
    other = dict(base, **{field: value})
    assert request_key("search", base) != request_key("search", other), (
        "changing the window, the date or the route must MISS, never hit a "
        "stale entry answering a different question"
    )


def test_changing_the_cache_schema_changes_the_key(monkeypatch):
    params = {"origin_airport": "SFO", "destination_airport": "MAD"}
    before = request_key("search", params)
    monkeypatch.setattr(response_cache, "CACHE_SCHEMA", CACHE_SCHEMA + 1)
    assert request_key("search", params) != before


def test_the_endpoint_is_part_of_the_key():
    params = {"id": "abc"}
    assert request_key("search", params) != request_key("trips", params)


# ---------------------------------------------------------------------------
# Step 1: one HTTP call for two identical searches
# ---------------------------------------------------------------------------


@patch("src.seats_client.requests.get")
def test_two_identical_searches_make_one_http_call(mock_get, client, cache, real_payload):
    mock_get.return_value = _mock_response(real_payload)

    first = client.search_raw("SFO", "MAD", RANGE, cache=cache)
    assert mock_get.call_count == 1
    assert first.served_from_cache is False
    assert first.fetched_at is not None

    second = client.search_raw("SFO", "MAD", RANGE, cache=cache)
    assert mock_get.call_count == 1, "the second call must not reach the network"
    assert second.served_from_cache is True
    assert second.pages == first.pages
    # The ORIGINAL fetched_at is reported, not now. A cache hit that claims to be
    # fresh is worse than no cache.
    assert second.fetched_at == first.fetched_at
    assert "NO API call made" in second.pagination_note


@patch("src.seats_client.requests.get")
def test_refresh_bypasses_the_cache_and_overwrites(mock_get, client, cache, real_payload):
    mock_get.return_value = _mock_response(real_payload)
    first = client.search_raw("SFO", "MAD", RANGE, cache=cache)
    assert mock_get.call_count == 1

    changed = json.loads(json.dumps(real_payload))
    changed["data"][0]["YRemainingSeats"] = 3
    mock_get.return_value = _mock_response(changed)

    second = client.search_raw("SFO", "MAD", RANGE, cache=cache, refresh=True)
    assert mock_get.call_count == 2
    assert second.served_from_cache is False
    assert second.pages[0]["data"][0]["YRemainingSeats"] == 3

    # The file on disk now holds the NEW response.
    on_disk = json.loads(first.cache_path.read_text())
    assert on_disk["pages"][0]["data"][0]["YRemainingSeats"] == 3


@patch("src.seats_client.requests.get")
def test_an_expired_entry_is_a_miss_but_the_file_is_kept(
    mock_get, client, cache, real_payload
):
    mock_get.return_value = _mock_response(real_payload)
    written = client.search_raw("SFO", "MAD", RANGE, cache=cache)
    path = written.cache_path
    assert path.exists()

    key = written.request_key
    long_after = datetime.now(timezone.utc) + timedelta(hours=99)
    assert cache.get(key, now=long_after) is None, "past the TTL it must miss"
    assert path.exists(), "an expired response is EVIDENCE and must not be deleted"
    assert any("is KEPT, not deleted" in w for w in cache.warnings)

    # Within the TTL it is still a hit.
    assert cache.get(key) is not None


@patch("src.seats_client.requests.get")
def test_cache_ttl_zero_disables_reuse(mock_get, client, cache, real_payload):
    mock_get.return_value = _mock_response(real_payload)
    client.search_raw("SFO", "MAD", RANGE, cache=cache)
    assert mock_get.call_count == 1
    client.search_raw("SFO", "MAD", RANGE, cache=cache, cache_ttl=0)
    assert mock_get.call_count == 2


def test_a_corrupt_cache_file_is_a_miss_with_a_warning_never_a_crash(cache):
    cache.cache_dir.mkdir(parents=True, exist_ok=True)
    key = "deadbeef"
    path = cache.path_for(key)
    path.write_text('{"pages": [{"data": [')  # truncated JSON

    assert cache.get(key) is None
    assert path.exists()
    warning = " ".join(cache.warnings)
    assert "could not be read" in warning
    assert "NOT being read as an empty result" in warning, (
        "a corrupt cache file must never be silently reported as no availability"
    )


def test_a_cache_entry_with_no_fetched_at_is_not_assumed_fresh(cache):
    cache.cache_dir.mkdir(parents=True, exist_ok=True)
    key = "nodate"
    cache.path_for(key).write_text(json.dumps({"_meta": {}, "pages": [{"data": []}]}))
    assert cache.get(key) is None
    assert any("cannot be checked" in w for w in cache.warnings)


# ---------------------------------------------------------------------------
# Step 1: NO KEY MATERIAL, ANYWHERE
# ---------------------------------------------------------------------------


@patch("src.seats_client.requests.get")
def test_no_cache_or_snapshot_file_contains_key_material(
    mock_get, client, cache, real_payload, monkeypatch
):
    monkeypatch.setenv("SEATS_AERO_KEY", "pro_ThisIsTheSecretKeyValue12345")
    mock_get.return_value = _mock_response(real_payload)
    client.search_raw("SFO", "MAD", RANGE, cache=cache, leg_id="B1")

    files = list(cache.cache_dir.glob("*.json")) + list(cache.snapshot_dir.glob("*.json"))
    assert files
    for path in files:
        text = path.read_text()
        assert "pro_ThisIsTheSecretKeyValue12345" not in text
        assert "Partner-Authorization" not in text
        assert json.loads(text)["_meta"]["key_redacted"] is True


def test_the_writer_refuses_a_payload_containing_the_key(monkeypatch):
    monkeypatch.setenv("SEATS_AERO_KEY", "pro_secret_value_here_0001")
    with pytest.raises(ValueError, match="REFUSING TO WRITE"):
        assert_no_key_material("...pro_secret_value_here_0001...", "a test blob")
    with pytest.raises(ValueError, match="REFUSING TO WRITE"):
        assert_no_key_material('{"Partner-Authorization": "x"}', "a test blob")


def test_no_committed_fixture_carries_anything_shaped_like_a_key():
    """
    Grep the whole committed corpus for a long opaque token in a field that has
    no business holding one. Availability IDs are legitimately 27 characters of
    base58, so the check is scoped to the metadata block rather than the body.
    """
    paths = list(COMMITTED_SNAPSHOTS.glob("*.json")) + [REAL_FIXTURE]
    for path in paths:
        meta = json.dumps((json.loads(path.read_text()) or {}).get("_meta") or {})
        assert "Partner-Authorization: " not in meta
        for token in re.findall(r"[A-Za-z0-9_\-]{25,}", meta):
            assert token in (
                "canonical_request",
                "omitted_by_truncation",
            ) or re.fullmatch(r"[0-9a-f]{64}", token), (
                f"{path.name} metadata holds an unexplained long token {token!r}"
            )


# ---------------------------------------------------------------------------
# Step 1: THE SPLIT CHANGED NOTHING
# ---------------------------------------------------------------------------


def test_the_real_capture_round_trips_through_the_cache_envelope_unchanged(real_payload):
    """
    THE PROOF THAT STEP 1 WAS A REFACTOR.

    Parse the committed capture directly, then parse the SAME capture after it
    has gone into the cache envelope and come back out. Identical Awards, field
    for field. If the transport/parse split had moved anything in the parser,
    this is where it would show.
    """
    direct = parse_availability_row(real_payload["data"][0])

    envelope = envelope_from_raw_payload(
        real_payload,
        {
            "origin_airport": "SFO",
            "destination_airport": "MAD",
            "start_date": "2027-01-15",
            "end_date": "2027-02-14",
        },
    )
    round_tripped, rows_seen, rows_skipped = SeatsClient.parse_pages(envelope["pages"])

    assert rows_seen == 1
    assert rows_skipped == 0
    assert len(round_tripped) == len(direct) == 1
    assert round_tripped[0] == direct[0]
    # And the known answer is still the known answer.
    assert round_tripped[0].program == "Air Canada Aeroplan"
    assert round_tripped[0].cost == 50000
    assert round_tripped[0].seats_available == 9


@patch("src.seats_client.requests.get")
def test_search_is_exactly_parse_of_search_raw(mock_get, client, cache, real_payload):
    mock_get.return_value = _mock_response(real_payload)
    raw = client.search_raw("SFO", "MAD", RANGE, cache=cache)
    from_raw, _, _ = SeatsClient.parse_pages(raw.pages)

    client.clear_cache()
    via_search = client.search("SFO", "MAD", RANGE, cache=cache)
    assert from_raw == via_search


# ---------------------------------------------------------------------------
# Step 2: snapshots and the manifest
# ---------------------------------------------------------------------------


@patch("src.seats_client.requests.get")
def test_a_fetch_writes_exactly_one_snapshot_and_one_manifest_row(
    mock_get, client, cache, real_payload
):
    mock_get.return_value = _mock_response(real_payload)
    client.search_raw("SFO", "MAD", RANGE, cache=cache, leg_id="B1", trip_id="trip_b_europe")

    snapshots = cache.snapshots()
    assert len(snapshots) == 1
    name = snapshots[0].name
    # Browsable, per section 4.4: leg, route, date, timestamp.
    assert name.startswith("B1_SFO_MAD_2027-01-15_")
    assert re.search(r"_\d{8}T\d{4}Z\.json$", name)

    manifest = cache.manifest_path.read_text()
    assert manifest.count("| B1 |") == 1
    assert "SFO->MAD" in manifest
    assert name in manifest


@patch("src.seats_client.requests.get")
def test_an_identical_refetch_adds_a_manifest_row_but_no_second_snapshot(
    mock_get, client, cache, real_payload
):
    mock_get.return_value = _mock_response(real_payload)
    client.search_raw("SFO", "MAD", RANGE, cache=cache, leg_id="B1")
    client.search_raw("SFO", "MAD", RANGE, cache=cache, leg_id="B1", refresh=True)

    assert len(cache.snapshots()) == 1, "the corpus is a set of observations, not a log"
    manifest = cache.manifest_path.read_text()
    assert manifest.count("| B1 |") == 2, "but the re-fetch itself is recorded"
    assert "re-fetch, identical" in manifest


@patch("src.seats_client.requests.get")
def test_a_different_response_adds_a_second_snapshot(
    mock_get, client, cache, real_payload
):
    mock_get.return_value = _mock_response(real_payload)
    client.search_raw("SFO", "MAD", RANGE, cache=cache, leg_id="B1")

    changed = json.loads(json.dumps(real_payload))
    changed["data"][0]["YRemainingSeats"] = 2
    mock_get.return_value = _mock_response(changed)
    client.search_raw("SFO", "MAD", RANGE, cache=cache, leg_id="B1", refresh=True)

    assert len(cache.snapshots()) == 2
    assert content_hash([real_payload]) != content_hash([changed])


@patch("src.seats_client.requests.get")
def test_a_snapshot_is_directly_loadable_as_a_fixture(
    mock_get, client, cache, real_payload
):
    """Snapshot and cache entry are the same envelope, so either loads as either."""
    mock_get.return_value = _mock_response(real_payload)
    client.search_raw("SFO", "MAD", RANGE, cache=cache, leg_id="B1")

    snapshot = load_envelope(cache.snapshots()[0])
    cached = load_envelope(cache.path_for(request_key("search", {
        "origin_airport": "SFO", "destination_airport": "MAD",
        "start_date": "2027-01-15", "end_date": "2027-01-15",
    })))
    assert snapshot.pages == cached.pages
    assert snapshot.meta["content_hash"] == cached.meta["content_hash"]


# ---------------------------------------------------------------------------
# Step 2: THE CORPUS IS EXECUTABLE, NOT DECORATIVE
# ---------------------------------------------------------------------------


UNMAPPED_NOTE = "is NOT in the source->program map"


def assert_award_is_honest(award, where: str) -> None:
    """
    ATTRIBUTED, OR EXPLICITLY UNATTRIBUTED - NEVER SILENTLY BLANK.

    This used to assert `award.program`, which demanded a guarantee the parser
    deliberately does not make: an award from a source code the map does not
    know is a REAL award with NO program, and `resolve_source` says so rather
    than printing the code as a program name (finding L-4). The first real
    snapshot committed from Tsuki's Mac (a `qatar` row on LHR-SFO) failed the
    old assertion while the code was doing exactly the right thing.

    What must never happen is a blank program with nothing saying why, or a
    blank program that still claims to know whether UR reaches it.
    """
    if award.program:
        assert award.program_source_code, f"{where}: a program with no source code"
    else:
        assert UNMAPPED_NOTE in award.source_note or (
            "carried no Route.Source" in award.source_note
        ), f"{where}: an award with no program AND no explanation of why"
        assert award.ur_transferable is None, (
            f"{where}: an unattributed award cannot know whether UR reaches it"
        )
    assert award.cost > 0, f"{where}: an award priced at {award.cost}"
    assert award.source_note, f"{where}: an award with no source note"
    assert award.source == "seats_aero"
    # And its taxes are either a known figure or explicitly unknown - a note
    # is what separates "unknown" from "we never looked".
    if not award.cash_component_known:
        assert award.cash_component_note, f"{where}: unknown taxes with no note"


def test_every_committed_snapshot_parses_into_valid_awards():
    """
    A committed corpus that nobody replays rots. This is the guard, and it runs
    in CI rather than by hand.

    An EMPTY corpus passes this test on its own - which is why the one below
    runs the same check over a synthetic envelope on every machine, so the rule
    is never only as strong as whatever happens to be committed.
    """
    snapshots = sorted(COMMITTED_SNAPSHOTS.glob("*.json"))
    for path in snapshots:
        envelope = load_envelope(path)
        assert envelope.pages, f"{path.name} carries no pages"
        awards, rows_seen, _ = SeatsClient.parse_pages(envelope.pages)
        for award in awards:
            assert_award_is_honest(award, path.name)


def _row_from_real(source, taxes):
    row = json.loads(json.dumps(REAL_ROW))
    row["Route"]["Source"] = source
    row["YTotalTaxes"] = taxes
    return row


REAL_ROW = json.loads(
    (ROOT / "tests" / "fixtures" / "seats_aero" / "sfo_mad_real.json").read_text()
)["data"][0]


def test_the_honesty_check_runs_on_every_machine_not_only_where_a_corpus_exists():
    """
    The rows the Mac's corpus actually contained, rebuilt: a mapped source, the
    `qatar` row that failed the old assertion (tax 0), a KrisFlyer row (tax 0,
    taxes not reported), and a code nobody has mapped.
    """
    rows = [
        _row_from_real("aeroplan", 4460),
        _row_from_real("qatar", 0),
        _row_from_real("singapore", 0),
        _row_from_real("hawaiianairlines", 4460),
    ]
    awards, _, _ = SeatsClient.parse_pages([{"data": rows}])
    assert len(awards) == 4
    for award in awards:
        assert_award_is_honest(award, f"synthetic {award.program_source_code}")
    by_code = {a.program_source_code: a for a in awards}
    assert by_code["qatar"].program == "Qatar Privilege Club"
    assert by_code["hawaiianairlines"].program == ""


def test_the_honesty_check_rejects_a_silently_blank_program():
    """The check itself must be able to fail, or it is decoration."""
    (award,) = parse_availability_row(_row_from_real("aeroplan", 4460))
    award.program = ""
    award.source_note = "no explanation at all"
    award.ur_transferable = None
    with pytest.raises(AssertionError, match="no explanation"):
        assert_award_is_honest(award, "tampered")
    award.source_note = f"code 'x' {UNMAPPED_NOTE}"
    award.ur_transferable = True
    with pytest.raises(AssertionError, match="cannot know whether UR"):
        assert_award_is_honest(award, "tampered")


def test_the_snapshot_directory_exists_and_explains_itself():
    assert COMMITTED_SNAPSHOTS.is_dir()
    readme = COMMITTED_SNAPSHOTS / "README.md"
    assert readme.exists()
    text = readme.read_text()
    assert "raw" in text.lower()
    assert "no key" in text.lower() or "key" in text.lower()


# ---------------------------------------------------------------------------
# MR5-4: a cache nobody gave a snapshot directory archives NOTHING
# ---------------------------------------------------------------------------


def test_a_cache_given_only_a_cache_dir_does_not_archive_anywhere(tmp_path):
    """
    `ResponseCache(tmp)` must not write into the committed corpus.

    `snapshot_dir` used to default to `DEFAULT_SNAPSHOT_DIR` INDEPENDENTLY of
    `cache_dir`, so a cache that reads as completely isolated still appended
    snapshots and manifest rows to `tests/fixtures/seats_aero/live_trip_b/` -
    the directory `--from-snapshot` treats as trusted, committed, hash-verified
    input. The Manager's MR-1 probe polluted the repo corpus by accident that
    way. A corpus that any code constructing a cache can append to is not
    evidence.
    """
    cache = ResponseCache(cache_dir=tmp_path / "cache")
    assert cache.snapshot_dir is None
    assert cache.manifest_path is None
    assert cache.archives is False
    assert cache.snapshots() == []


def test_the_committed_corpus_is_untouched_by_a_tmp_dir_cache(tmp_path):
    """The pollution itself, end to end: a real put() writes no corpus file."""
    corpus = Path(response_cache.DEFAULT_SNAPSHOT_DIR)
    before = sorted(p.name for p in corpus.iterdir()) if corpus.exists() else []

    request = {
        "origin_airport": "LHR",
        "destination_airport": "SFO",
        "start_date": "2027-01-27",
        "end_date": "2027-01-27",
    }
    key = response_cache.request_key("search", request)
    cache = ResponseCache(cache_dir=tmp_path / "cache")
    entry = cache.put(
        key,
        request,
        [{"data": [{"ID": "x", "Date": "2027-01-27"}], "hasMore": False}],
        meta={"leg_id": "B4", "rows_seen": 1},
        now=datetime(2027, 1, 1, 12, 0, tzinfo=timezone.utc),
    )

    after = sorted(p.name for p in corpus.iterdir()) if corpus.exists() else []
    assert after == before, f"the committed corpus was written to: {set(after) - set(before)}"
    # The response is still CACHED - archiving is what was declined, not caching.
    assert (tmp_path / "cache" / f"{key}.json").exists()
    assert entry.meta["snapshot"] is None
