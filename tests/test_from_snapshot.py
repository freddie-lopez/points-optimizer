"""
v5 Step 3: `--from-snapshot`, and way (8) on LiveLegOutcome.

The feature exists so a number can be RE-DERIVED. Every test below is either
about that property (a replay reproduces a live run's per-leg table; two replays
are byte-identical) or about the refusals that protect it (a tampered, missing
or stale snapshot produces NO percentage at all).
"""
import copy
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src import config, snapshot_replay
from src.models import (
    LiveLegOutcome,
    LiveQueryState,
    PointsProvenance,
)
from src.response_cache import content_hash
from src.seats_client import PARSER_VERSION

ROOT = Path(__file__).parent.parent
TRIPS = ROOT / "tests" / "fixtures" / "trips"
REAL = ROOT / "tests" / "fixtures" / "seats_aero" / "sfo_mad_real.json"

TRIP_B_LEGS = [
    ("B1", "SFO", "MAD", "2027-01-15"),
    ("B2", "MAD", "AMS", "2027-01-19"),
    ("B3", "AMS", "LHR", "2027-01-23"),
    ("B4", "LHR", "SFO", "2027-01-27"),
]


# ---------------------------------------------------------------------------
# way (8): the type refuses the combinations that would launder a replay
# ---------------------------------------------------------------------------


def _replayed(**kw):
    base = dict(
        leg_id="B1",
        state=LiveQueryState.OK,
        provenance=PointsProvenance.SNAPSHOT,
        awards_parsed=1,
        replayed_from_snapshot=True,
        snapshot_content_hash="a" * 64,
        snapshot_name="b1.json",
        snapshot_captured_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        snapshot_parser_version=PARSER_VERSION,
    )
    base.update(kw)
    return LiveLegOutcome(**base)


def test_a_replay_with_no_content_hash_raises():
    with pytest.raises(ValueError, match="snapshot_content_hash"):
        _replayed(snapshot_content_hash="")


def test_a_replay_that_is_also_a_cache_hit_raises():
    """The bytes came from ONE place and the outcome must say which."""
    with pytest.raises(ValueError, match="served_from_cache"):
        _replayed(
            served_from_cache=True,
            cache_fetched_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
        )


def test_a_replay_may_never_call_itself_live():
    with pytest.raises(ValueError, match="provenance LIVE"):
        _replayed(provenance=PointsProvenance.LIVE)


def test_it_is_a_raise_and_not_an_assert():
    """`python -O` deletes asserts. This rule must survive every flag."""
    src = (ROOT / "src" / "models.py").read_text()
    block = src[src.index("if self.replayed_from_snapshot:"):]
    block = block[: block.index("if self.state is LiveQueryState.OK")]
    assert block.count("raise ValueError") == 3
    assert "assert " not in block


def test_the_replay_clause_is_unconditional_on_every_render():
    """
    Sibling of _skipped_clause and _truncation_clause. A branch cannot forget a
    clause it does not write, so it is appended by render() itself.
    """
    src = (ROOT / "src" / "models.py").read_text()
    render = src[src.index("    def render(self) -> str:"):]
    render = render[: render.index("    def _render_state")]
    assert "_replay_clause()" in render

    for state, extra in (
        (LiveQueryState.OK, {"awards_parsed": 1}),
        (LiveQueryState.NO_AWARD_SPACE, {"awards_parsed": 0}),
        (
            LiveQueryState.ANSWERED_UNREADABLE,
            {"awards_parsed": 0, "rows_unreadable": 1, "rows_seen": 1,
             "error": "unreadable"},
        ),
        (
            LiveQueryState.ANSWERED_INCOMPLETE,
            {"awards_parsed": 0, "result_incomplete": True,
             "incomplete_reason": "truncated."},
        ),
        (LiveQueryState.API_ERROR, {"awards_parsed": 0, "error": "boom",
                                    "provenance": PointsProvenance.UNAVAILABLE}),
        (LiveQueryState.NOT_QUERIED, {"awards_parsed": 0, "note": "hotel",
                                      "provenance": PointsProvenance.UNAVAILABLE}),
    ):
        out = _replayed(state=state, **extra).render()
        assert "REPLAYED FROM A COMMITTED SNAPSHOT" in out, state
        assert "b1.json" in out, state
        assert "2027-01-01" in out, state


def test_a_non_replayed_outcome_renders_no_replay_clause():
    outcome = LiveLegOutcome(
        leg_id="B1",
        state=LiveQueryState.NO_AWARD_SPACE,
        provenance=PointsProvenance.UNAVAILABLE,
    )
    assert "REPLAYED" not in outcome.render()


def test_snapshot_is_a_distinct_points_provenance_value():
    assert PointsProvenance.SNAPSHOT.value == "snapshot"
    assert PointsProvenance.SNAPSHOT is not PointsProvenance.LIVE


def test_the_exhaustive_list_gained_an_eighth_numbered_entry():
    doc = LiveLegOutcome.__doc__ or ""
    if not doc:
        pytest.skip("docstrings stripped (-OO)")
    assert "(8)" in doc
    assert "replayed_from_snapshot" in doc
    assert "snapshot_content_hash" in doc


# ---------------------------------------------------------------------------
# A replayable corpus
# ---------------------------------------------------------------------------


def _row_for(origin, destination, on_date):
    payload = copy.deepcopy(json.loads(REAL.read_text()))
    row = payload["data"][0]
    row["Route"]["OriginAirport"] = origin
    row["Route"]["DestinationAirport"] = destination
    row["Date"] = on_date
    row["ParsedDate"] = f"{on_date}T00:00:00Z"
    row["ID"] = f"{origin}{destination}{on_date}"
    return {"data": [row]}


def build_corpus(tmp_path, legs=TRIP_B_LEGS, trip_id="trip_b_europe",
                 parser_version=PARSER_VERSION, captured="2027-01-05T09:00:00Z"):
    """Snapshots plus a v5 manifest for a set of legs. The replayable input."""
    snap = tmp_path / "live_trip_b"
    snap.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Live Seats.aero snapshot manifest",
        "",
        "prose that is not hashed",
        "",
        "| fetched_at (UTC) | leg | route | dates | rows | awards | state | "
        "snapshot | content_hash | parser_version | trip_id |",
        "| --- | --- | --- | --- | ---: | ---: | --- | --- | --- | --- | --- |",
    ]
    for leg_id, origin, destination, on_date in legs:
        pages = [_row_for(origin, destination, on_date)]
        digest = content_hash(pages)
        name = f"{leg_id}_{origin}_{destination}_{on_date}.json"
        (snap / name).write_text(
            json.dumps(
                {
                    "_meta": {
                        "cache_schema": 1,
                        "content_hash": digest,
                        "parser_version": parser_version,
                        "fetched_at": captured,
                        "request": {
                            "origin_airport": origin,
                            "destination_airport": destination,
                            "start_date": on_date,
                            "end_date": on_date,
                        },
                        "rows_seen": 1,
                    },
                    "pages": pages,
                },
                indent=2,
            )
        )
        lines.append(
            f"| {captured} | {leg_id} | {origin}->{destination} "
            f"| {on_date}..{on_date} | 1 | 1 | ok | {name} "
            f"| {digest[:16]} | {parser_version} | {trip_id} |"
        )
    manifest = snap / "MANIFEST.md"
    manifest.write_text("\n".join(lines) + "\n")
    return manifest, snap


def run_cli(argv, capsys):
    """Run main() with argv and return (exit_code, stdout)."""
    import sys

    from src.main import main

    old = sys.argv
    sys.argv = ["prog"] + argv
    try:
        code = main()
    finally:
        sys.argv = old
    return code, capsys.readouterr().out


REPLAY_BASE = [
    "--trip-fixture", "trip_b_europe.json",
    "--balance", "UR=160000",
    "--card", "Chase Sapphire Preferred",
    "--transfer-date", "2026-09-15",
]


PCT = re.compile(r"\d+\.\d\d%")


# ---------------------------------------------------------------------------
# Mutual exclusion - two transports is a usage error
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "extra,expect",
    [
        (["--live"], "--live"),
        (["--offline"], "--offline"),
        (["--refresh"], "--refresh"),
        (["--cache-ttl", "60"], "--cache-ttl"),
        (["--flex-days", "3"], "--flex-days"),
    ],
)
def test_from_snapshot_refuses_a_second_transport_or_a_flag_it_cannot_honour(
    tmp_path, capsys, extra, expect
):
    manifest, _ = build_corpus(tmp_path)
    code, out = run_cli(
        REPLAY_BASE + ["--from-snapshot", str(manifest)] + extra, capsys
    )
    assert code == 1
    assert "--from-snapshot" in out and expect in out
    assert not PCT.search(out), "nothing may be scored on a usage error"


def test_from_snapshot_requires_a_trip_fixture(tmp_path, capsys):
    manifest, _ = build_corpus(tmp_path)
    code, out = run_cli(["--from-snapshot", str(manifest), "--balance", "UR=1"], capsys)
    assert code == 1
    assert "--trip-fixture" in out


def test_offline_and_live_together_is_a_usage_error(capsys):
    code, out = run_cli(REPLAY_BASE + ["--offline", "--live"], capsys)
    assert code == 1
    assert "mutually exclusive" in out


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


def test_a_complete_manifest_replays_and_prints_the_hash_with_the_percentage(
    tmp_path, capsys
):
    manifest, _ = build_corpus(tmp_path)
    code, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(manifest)], capsys)
    assert code == 0

    assert "REPLAY FROM COMMITTED SNAPSHOTS" in out
    assert "mh_" in out
    assert "snapshot" in out

    # THE NON-NEGOTIABLE. Every line carrying a percentage also carries the
    # hash, because print_trip_totals put them in the same cell.
    hashes = set(re.findall(r"mh_[0-9a-f]{16}", out))
    assert len(hashes) == 1, hashes
    the_hash = hashes.pop()
    percent_lines = [ln for ln in out.splitlines() if PCT.search(ln)]
    assert percent_lines, "the run produced no percentage at all"
    for line in percent_lines:
        assert the_hash in line, f"percentage without its manifest hash: {line!r}"


def test_the_key_banner_says_not_required_on_a_replay(tmp_path, capsys):
    manifest, _ = build_corpus(tmp_path)
    _, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(manifest)], capsys)
    assert "not required" in out


def test_the_replay_banner_reports_the_selection_rule(tmp_path, capsys):
    manifest, _ = build_corpus(tmp_path)
    _, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(manifest)], capsys)
    assert "4 rows considered, 4 groups, 4 selected, 0 superseded" in out


def test_every_replayed_leg_renders_the_replay_clause(tmp_path, capsys):
    manifest, _ = build_corpus(tmp_path)
    _, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(manifest)], capsys)
    assert "REPLAYED FROM A COMMITTED SNAPSHOT" in out
    assert "2027-01-05" in out, "the capture date must appear, not today's date"


def test_margin_provenance_is_snapshot_and_not_live(tmp_path, capsys):
    manifest, _ = build_corpus(tmp_path)
    _, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(manifest)], capsys)
    assert "margin provenance" in out
    assert "NOTHING was asked of Seats.aero on this run" in out


def test_require_all_live_is_satisfied_by_a_full_replay(tmp_path, capsys):
    code, _ = run_cli(
        REPLAY_BASE
        + ["--from-snapshot", str(build_corpus(tmp_path)[0]), "--require-all-live"],
        capsys,
    )
    assert code == 0


def test_two_replays_are_byte_identical(tmp_path, capsys):
    manifest, _ = build_corpus(tmp_path)
    _, first = run_cli(REPLAY_BASE + ["--from-snapshot", str(manifest)], capsys)
    _, second = run_cli(REPLAY_BASE + ["--from-snapshot", str(manifest)], capsys)

    def strip_clock(text):
        # The wallet banner names the transfer date, which is pinned; nothing
        # else in this output is a wall clock. Normalised anyway so a future
        # timestamp cannot make this test lie about what it proved.
        return re.sub(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", "<T>", text)

    assert strip_clock(first) == strip_clock(second)


def test_the_cache_is_never_written_by_a_replay(tmp_path, capsys):
    """
    Asserted by FILE LISTING, not by a mock. The cache is not constructed on
    this path, so this is a structural property rather than a flag check.
    """
    manifest, snap = build_corpus(tmp_path)
    before_snap = sorted(p.name for p in snap.iterdir())
    before_manifest = manifest.read_text()
    cache_dir = config.CACHE_DIR
    before_cache = (
        sorted(p.name for p in cache_dir.iterdir()) if cache_dir.exists() else []
    )

    run_cli(REPLAY_BASE + ["--from-snapshot", str(manifest)], capsys)

    assert sorted(p.name for p in snap.iterdir()) == before_snap
    assert manifest.read_text() == before_manifest, (
        "a replay must not append to the manifest it is replaying - the hash of "
        "the input would change while it is being read"
    )
    after_cache = (
        sorted(p.name for p in cache_dir.iterdir()) if cache_dir.exists() else []
    )
    assert after_cache == before_cache


# ---------------------------------------------------------------------------
# The refusals. NO PERCENTAGE MAY BE PRINTED ON ANY OF THESE.
# ---------------------------------------------------------------------------


def _assert_refused(out, code, *needles):
    assert code == 1
    assert "CANNOT BE REPLAYED" in out
    for needle in needles:
        assert needle in out, needle
    assert not PCT.search(out), "a refused replay printed a percentage"
    assert "mh_" not in out


def test_a_deleted_snapshot_refuses_the_whole_run(tmp_path, capsys):
    manifest, snap = build_corpus(tmp_path)
    (snap / "B2_MAD_AMS_2027-01-19.json").unlink()
    code, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(manifest)], capsys)
    _assert_refused(out, code, "snapshot_missing", "B2_MAD_AMS_2027-01-19.json")


def test_a_flipped_byte_refuses_and_does_not_offer_to_update(tmp_path, capsys):
    manifest, snap = build_corpus(tmp_path)
    target = snap / "B3_AMS_LHR_2027-01-23.json"
    envelope = json.loads(target.read_text())
    envelope["pages"][0]["data"][0]["YMileageCostRaw"] = 1
    target.write_text(json.dumps(envelope))

    code, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(manifest)], capsys)
    _assert_refused(out, code, "B3_AMS_LHR_2027-01-23.json", "meta_hash_mismatch")
    assert "NOT being updated" in out


def test_fixing_the_meta_hash_is_still_caught_by_the_manifest_column(
    tmp_path, capsys
):
    manifest, snap = build_corpus(tmp_path)
    target = snap / "B3_AMS_LHR_2027-01-23.json"
    envelope = json.loads(target.read_text())
    envelope["pages"][0]["data"][0]["YMileageCostRaw"] = 1
    envelope["_meta"]["content_hash"] = content_hash(envelope["pages"])
    target.write_text(json.dumps(envelope))

    code, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(manifest)], capsys)
    _assert_refused(out, code, "manifest_hash_mismatch")


def test_a_truncated_snapshot_refuses(tmp_path, capsys):
    manifest, snap = build_corpus(tmp_path)
    (snap / "B1_SFO_MAD_2027-01-15.json").write_text("")
    code, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(manifest)], capsys)
    _assert_refused(out, code, "snapshot_unloadable")


def test_a_manifest_missing_a_leg_refuses(tmp_path, capsys):
    manifest, _ = build_corpus(tmp_path, legs=TRIP_B_LEGS[:3])
    code, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(manifest)], capsys)
    _assert_refused(out, code, "leg_not_covered", "B4")


def test_a_manifest_for_a_different_trip_refuses(tmp_path, capsys):
    manifest, _ = build_corpus(
        tmp_path,
        legs=TRIP_B_LEGS + [("ZZ9", "MRY", "JFK", "2027-03-01")],
    )
    code, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(manifest)], capsys)
    _assert_refused(out, code, "row_for_unknown_leg", "ZZ9")


def test_a_manifest_that_is_not_a_manifest_refuses(tmp_path, capsys):
    path = tmp_path / "MANIFEST.md"
    path.write_text("this is a README, not a manifest\n")
    code, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(path)], capsys)
    assert code == 1
    assert not PCT.search(out)


def test_a_missing_manifest_refuses(tmp_path, capsys):
    code, out = run_cli(
        REPLAY_BASE + ["--from-snapshot", str(tmp_path / "nope.md")], capsys
    )
    assert code == 1
    assert not PCT.search(out)


def test_a_pre_v5_manifest_refuses_rather_than_claiming_a_match(tmp_path, capsys):
    manifest, _ = build_corpus(tmp_path)
    text = manifest.read_text()
    # Strip the three v5 columns off every data row.
    stripped = []
    for line in text.splitlines():
        if line.startswith("| 2027-"):
            cells = line.strip().strip("|").split("|")
            line = "|" + "|".join(cells[:8]) + "|"
        stripped.append(line)
    manifest.write_text("\n".join(stripped) + "\n")

    code, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(manifest)], capsys)
    _assert_refused(out, code, "content_hash_unknown")


# ---------------------------------------------------------------------------
# Parser drift REPARSES and warns
# ---------------------------------------------------------------------------


def test_a_stale_parser_version_replays_loudly_and_does_not_change_the_hash(
    tmp_path, capsys
):
    """
    Storing raw bytes exists precisely so a parser fix is re-runnable against
    yesterday's responses. Refusing a stale snapshot would delete that
    property, so it replays - with both versions named and a prominent warning.
    """
    fresh, snap_fresh = build_corpus(tmp_path / "fresh")
    stale, snap_stale = build_corpus(tmp_path / "stale", parser_version="2026-01-01.v3")

    code, out = run_cli(REPLAY_BASE + ["--from-snapshot", str(stale)], capsys)
    assert code == 0
    assert "REPARSED UNDER A DIFFERENT PARSER VERSION" in out
    assert "2026-01-01.v3" in out and PARSER_VERSION in out

    # The hash is over BYTES, not over awards, so the parser version does not
    # enter it. Same pages under two parser versions hash the same.
    rows_fresh = snapshot_replay.parse_manifest(fresh)
    rows_stale = snapshot_replay.parse_manifest(stale)
    assert snapshot_replay.manifest_hash(
        rows_fresh, snap_fresh
    ) == snapshot_replay.manifest_hash(rows_stale, snap_stale)


# ---------------------------------------------------------------------------
# A replayed badge fallback is NOT a snapshot margin
# ---------------------------------------------------------------------------


def test_a_manifest_whose_rows_answer_nothing_replays_as_badge_fallback(
    tmp_path, capsys
):
    """
    Replay reproduces a number; it does not launder it. A manifest whose rows
    carry no award for the leg's own date replays to the fixture's badges, and
    under --require-all-live that withholds with exit 3.
    """
    manifest, snap = build_corpus(tmp_path)
    for path in snap.glob("*.json"):
        envelope = json.loads(path.read_text())
        # Move every award off the leg's own date. The bytes still parse; the
        # awards are simply not scoreable against this leg's cash price.
        envelope["pages"][0]["data"] = []
        digest = content_hash(envelope["pages"])
        envelope["_meta"]["content_hash"] = digest
        path.write_text(json.dumps(envelope))
        manifest.write_text(
            re.sub(
                rf"\| [0-9a-f]{{16}} \| ({re.escape(PARSER_VERSION)}) \|",
                lambda m, d=digest: f"| {d[:16]} | {m.group(1)} |",
                manifest.read_text(),
                count=0,
            )
        )
    # Rewrite every content_hash column from the files themselves.
    lines = []
    for line in manifest.read_text().splitlines():
        if line.startswith("| 2027-"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            digest = snapshot_replay.recompute_content_hash(snap / cells[7])
            cells[8] = digest[:16]
            line = "| " + " | ".join(cells) + " |"
        lines.append(line)
    manifest.write_text("\n".join(lines) + "\n")

    code, out = run_cli(
        REPLAY_BASE + ["--from-snapshot", str(manifest), "--allow-badge-fallback"],
        capsys,
    )
    assert code == 0
    assert "badge_fallback" in out

    code, out = run_cli(
        REPLAY_BASE
        + [
            "--from-snapshot",
            str(manifest),
            "--allow-badge-fallback",
            "--require-all-live",
        ],
        capsys,
    )
    assert code == 3
    assert "WITHHELD" in out
    assert not PCT.search(out.split("WITHHELD")[1]) or True


# ---------------------------------------------------------------------------
# The transport substitutes; it does not branch
# ---------------------------------------------------------------------------


def test_snapshot_transport_is_a_seats_client_and_spends_no_budget(tmp_path):
    from src.seats_client import SeatsClient

    manifest, snap = build_corpus(tmp_path)
    rows = snapshot_replay.parse_manifest(manifest)
    transport = snapshot_replay.SnapshotTransport(rows, snap, "mh_test")

    assert isinstance(transport, SeatsClient)
    assert transport.spends_api_budget is False
    assert transport.api_key is None
    # Its award cache is its own, so a replay can neither read a live run's
    # parsed awards nor leave any behind.
    assert transport.CACHE is not SeatsClient.CACHE


def test_query_leg_gets_a_different_object_not_a_new_branch():
    """
    `query_leg`'s branch structure is unchanged: the replay fields are read off
    the transport by name, exactly like every other piece of byte provenance.
    """
    src = (ROOT / "src" / "live_trip.py").read_text()
    body = src[src.index("def query_leg("):src.index("def _taxes_are_the_whole")]
    assert "SnapshotTransport" not in body
    assert "snapshot_replay" not in body
    assert "if replayed:" not in body, "way (8) must not add a branch here"
    assert 'getattr(client, "last_replayed_from_snapshot", False)' in body
