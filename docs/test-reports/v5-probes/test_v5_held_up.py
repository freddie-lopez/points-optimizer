"""
v5 adversarial probes: WHAT HELD UP.

These assert CORRECT behaviour, so they must survive every fix for the defects
in `test_v5_findings.py`. The Manager caught three of last round's held-up
probes being destroyed by the fixes they were meant to outlive, so each one here
is written against a clean, unmutated input: fixing the route binding, the
badge/hash coupling, the coverage round trip or the cabin lookup cannot make any
of these red.
"""
import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path

import pytest
import requests

from conftest import BASE, TRIP_B, build_corpus, flat, run_cli


# ---------------------------------------------------------------------------
# Key resolution: four sources, right order, nothing leaked
# ---------------------------------------------------------------------------


@pytest.fixture
def key_sources(tmp_path):
    from src import config

    repo = tmp_path / "repo.env"
    user = tmp_path / "user.env"
    repo.write_text("SEATS_AERO_KEY=repo_KEYVALUE_rrr\n")
    user.write_text("SEATS_AERO_KEY=user_KEYVALUE_uuu\n")
    old = (config._ENV_PATH, config.USER_CONFIG_ENV_PATH,
           os.environ.get("SEATS_AERO_KEY"))
    config._ENV_PATH, config.USER_CONFIG_ENV_PATH = repo, user
    os.environ["SEATS_AERO_KEY"] = "envv_KEYVALUE_eee"
    yield config, repo, user
    config._ENV_PATH, config.USER_CONFIG_ENV_PATH = old[0], old[1]
    if old[2] is None:
        os.environ.pop("SEATS_AERO_KEY", None)
    else:
        os.environ["SEATS_AERO_KEY"] = old[2]


def test_precedence_is_flag_env_repo_user(key_sources):
    config, repo, user = key_sources
    assert config.resolve_key("flag_KEYVALUE_fff").source == "flag --api-key"
    assert config.resolve_key(None).source == "environment"
    os.environ.pop("SEATS_AERO_KEY")
    assert config.resolve_key(None).source == "repo .env"
    repo.unlink()
    assert config.resolve_key(None).source == "user config"


def test_no_key_at_all_names_all_four_places(key_sources):
    config, repo, user = key_sources
    os.environ.pop("SEATS_AERO_KEY")
    repo.unlink()
    user.unlink()
    with pytest.raises(config.KeyResolutionError) as e:
        config.resolve_key(None)
    for label in ("flag --api-key", "environment", "repo .env", "user config"):
        assert label in str(e.value)


def test_an_empty_flag_does_not_win(key_sources):
    config, repo, user = key_sources
    assert config.resolve_key("   ").source == "environment"


def test_mask_shows_nothing_of_a_short_key_and_7_chars_of_a_long_one():
    from src.config import mask_key

    assert mask_key("pro_" + "x" * 24 + "jwV") == "pro_…jwV"
    assert mask_key("abcdefg") == "…"
    assert mask_key("") == "…"
    assert mask_key(None) == "…"


def test_the_full_key_is_never_printed_on_a_live_run(tmp_path, capsys):
    from src import seats_client
    from src.seats_client import SeatsClient

    key = "pro_SECRETKEYMATERIAL_jwV"
    old_env = os.environ.get("SEATS_AERO_KEY")
    os.environ["SEATS_AERO_KEY"] = key
    original = seats_client.requests.get
    seats_client.requests.get = lambda *a, **k: (_ for _ in ()).throw(
        requests.RequestException("no network")
    )
    try:
        SeatsClient._CALLS = {}
        code, out = run_cli(BASE + ["--snapshot-dir", str(tmp_path)], capsys)
    finally:
        seats_client.requests.get = original
        if old_env is None:
            os.environ.pop("SEATS_AERO_KEY", None)
        else:
            os.environ["SEATS_AERO_KEY"] = old_env
    assert key not in out
    assert "pro_…jwV" in out
    for path in Path(tmp_path).rglob("*"):
        if path.is_file():
            assert key not in path.read_text()
    assert code == 3


# ---------------------------------------------------------------------------
# The default flip
# ---------------------------------------------------------------------------


def test_no_network_and_no_offline_withholds_with_exit_3(tmp_path, capsys):
    from src import seats_client
    from src.seats_client import SeatsClient

    old_env = os.environ.get("SEATS_AERO_KEY")
    os.environ["SEATS_AERO_KEY"] = "pro_TESTKEYMATERIAL_abc"
    original = seats_client.requests.get
    seats_client.requests.get = lambda *a, **k: (_ for _ in ()).throw(
        requests.RequestException("no network")
    )
    try:
        SeatsClient._CALLS = {}
        code, out = run_cli(BASE + ["--snapshot-dir", str(tmp_path)], capsys)
    finally:
        seats_client.requests.get = original
        if old_env is None:
            os.environ.pop("SEATS_AERO_KEY", None)
        else:
            os.environ["SEATS_AERO_KEY"] = old_env
    assert code == 3
    body = flat(out)
    assert "WITHHELD" in body
    # No badge number of any kind slipped through.
    assert not re.search(r"\d+\.\d\d% - \d+\.\d\d%", body)


def test_offline_and_live_together_is_a_usage_error(capsys):
    code, out = run_cli(BASE + ["--offline", "--live"], capsys)
    assert code == 1
    assert "mutually exclusive" in out


@pytest.mark.parametrize(
    "extra", [["--live"], ["--offline"], ["--refresh"], ["--cache-ttl", "60"],
              ["--flex-days", "3"]]
)
def test_from_snapshot_refuses_a_second_transport(tmp_path, capsys, extra):
    manifest, _ = build_corpus(tmp_path)
    code, out = run_cli(BASE + ["--from-snapshot", str(manifest)] + extra, capsys)
    assert code == 1
    assert "--from-snapshot" in out


def test_new_trip_cannot_be_chained_into_from_snapshot(tmp_path, capsys):
    code, out = run_cli(
        ["--new-trip", "x", "--leg", "SFO:MAD:2027-01-15:395",
         "--from-snapshot", "whatever.md"],
        capsys,
    )
    assert code == 1
    assert "--new-trip cannot be combined with --from-snapshot" in flat(out)


# ---------------------------------------------------------------------------
# Manifest refusals. Each mutates a corpus that is otherwise valid, so a fix
# elsewhere cannot make them pass for the wrong reason.
# ---------------------------------------------------------------------------


def _refused(code, out, kind):
    assert code == 1, out[-2000:]
    body = flat(out)
    assert "THIS MANIFEST CANNOT BE REPLAYED" in body
    assert kind in body
    assert not re.search(r"\d+\.\d\d%", body), "a percentage was printed on a refusal"


def test_a_deleted_snapshot_refuses_the_whole_run(tmp_path, capsys):
    manifest, snap = build_corpus(tmp_path)
    (snap / "B2_MAD_AMS_2027-01-19.json").unlink()
    code, out = run_cli(BASE + ["--from-snapshot", str(manifest)], capsys)
    _refused(code, out, "snapshot_missing")


def test_a_flipped_byte_refuses(tmp_path, capsys):
    manifest, snap = build_corpus(tmp_path)
    path = snap / "B1_SFO_MAD_2027-01-15.json"
    body = json.loads(path.read_text())
    body["pages"][0]["data"][0]["YMileageCost"] = 999
    path.write_text(json.dumps(body, indent=2))
    code, out = run_cli(BASE + ["--from-snapshot", str(manifest)], capsys)
    _refused(code, out, "meta_hash_mismatch")


def test_fixing_the_meta_hash_is_still_caught_by_the_manifest_column(
    tmp_path, capsys
):
    from src.response_cache import content_hash

    manifest, snap = build_corpus(tmp_path)
    path = snap / "B1_SFO_MAD_2027-01-15.json"
    body = json.loads(path.read_text())
    body["pages"][0]["data"][0]["YMileageCost"] = 999
    body["_meta"]["content_hash"] = content_hash(body["pages"])
    path.write_text(json.dumps(body, indent=2))
    code, out = run_cli(BASE + ["--from-snapshot", str(manifest)], capsys)
    _refused(code, out, "manifest_hash_mismatch")


def test_a_truncated_snapshot_file_refuses(tmp_path, capsys):
    manifest, snap = build_corpus(tmp_path)
    (snap / "B3_AMS_LHR_2027-01-23.json").write_text("")
    code, out = run_cli(BASE + ["--from-snapshot", str(manifest)], capsys)
    _refused(code, out, "snapshot_unloadable")


def test_a_manifest_covering_three_of_four_legs_refuses(tmp_path, capsys):
    from conftest import TRIP_B_LEGS

    manifest, _ = build_corpus(tmp_path, legs=TRIP_B_LEGS[:3])
    code, out = run_cli(BASE + ["--from-snapshot", str(manifest)], capsys)
    _refused(code, out, "leg_not_covered")


def test_a_manifest_naming_a_leg_the_trip_does_not_have_refuses(tmp_path, capsys):
    from conftest import TRIP_B_LEGS

    manifest, _ = build_corpus(
        tmp_path, legs=TRIP_B_LEGS + [("Z9", "JFK", "DUB", "2027-03-10")]
    )
    code, out = run_cli(BASE + ["--from-snapshot", str(manifest)], capsys)
    _refused(code, out, "row_for_unknown_leg")


def test_a_manifest_whose_trip_id_names_another_trip_refuses(tmp_path, capsys):
    manifest, _ = build_corpus(tmp_path, trip_id="trip_a_mry_nyc")
    code, out = run_cli(BASE + ["--from-snapshot", str(manifest)], capsys)
    _refused(code, out, "leg_not_covered")


def test_two_rows_with_the_same_timestamp_and_different_files_refuse(
    tmp_path, capsys
):
    manifest, _ = build_corpus(tmp_path)
    lines = manifest.read_text().splitlines()
    clash = [
        line.replace("B1_SFO_MAD_2027-01-15.json", "B2_MAD_AMS_2027-01-19.json")
        for line in lines
        if "| B1 |" in line
    ]
    manifest.write_text("\n".join(lines + clash) + "\n")
    code, out = run_cli(BASE + ["--from-snapshot", str(manifest)], capsys)
    _refused(code, out, "ambiguous_latest_row")


def test_a_pre_v5_row_reads_as_unknown_and_not_as_a_match(tmp_path, capsys):
    manifest, _ = build_corpus(tmp_path)
    trimmed = []
    for line in manifest.read_text().splitlines():
        if line.startswith("| 2027-"):
            line = "|".join(line.split("|")[:9]) + " |"
        trimmed.append(line)
    manifest.write_text("\n".join(trimmed) + "\n")
    code, out = run_cli(BASE + ["--from-snapshot", str(manifest)], capsys)
    _refused(code, out, "content_hash_unknown")


def test_a_file_that_is_not_a_manifest_refuses(tmp_path, capsys):
    path = Path(tmp_path) / "README.md"
    path.write_text("# this is a README, not a manifest\n\nprose only.\n")
    code, out = run_cli(BASE + ["--from-snapshot", str(path)], capsys)
    assert code == 1
    assert "no manifest rows" in flat(out)


def test_a_missing_manifest_refuses(tmp_path, capsys):
    code, out = run_cli(
        BASE + ["--from-snapshot", str(Path(tmp_path) / "nope.md")], capsys
    )
    assert code == 1
    assert "Nothing can be replayed" in flat(out)


def test_a_row_pointing_outside_the_snapshot_directory_refuses(tmp_path, capsys):
    manifest, _ = build_corpus(tmp_path)
    manifest.write_text(
        manifest.read_text().replace(
            "B1_SFO_MAD_2027-01-15.json", "../../../etc/passwd"
        )
    )
    code, out = run_cli(BASE + ["--from-snapshot", str(manifest)], capsys)
    _refused(code, out, "snapshot_outside_corpus")


# ---------------------------------------------------------------------------
# A clean replay: deterministic, hashed, never live, never cached
# ---------------------------------------------------------------------------


def test_a_clean_replay_never_prints_a_percentage_without_its_hash(
    tmp_path, capsys
):
    manifest, _ = build_corpus(tmp_path)
    code, out = run_cli(BASE + ["--from-snapshot", str(manifest)], capsys)
    assert code == 0
    offenders = [
        line.strip()
        for line in out.splitlines()
        if re.search(r"\d+\.\d\d%", line) and "mh_" not in line
    ]
    assert offenders == []


def test_two_replays_of_one_manifest_agree(tmp_path, capsys):
    manifest, _ = build_corpus(tmp_path)
    _, first = run_cli(BASE + ["--from-snapshot", str(manifest)], capsys)
    _, second = run_cli(BASE + ["--from-snapshot", str(manifest)], capsys)
    strip = lambda t: re.sub(r"\d{4}-\d\d-\d\d \d\d:\d\d", "<clock>", flat(t))
    assert strip(first) == strip(second)


def test_a_replay_does_not_append_to_the_manifest_it_reads(tmp_path, capsys):
    manifest, snap = build_corpus(tmp_path)
    before = manifest.read_text()
    listing = sorted(p.name for p in snap.iterdir())
    run_cli(BASE + ["--from-snapshot", str(manifest)], capsys)
    assert manifest.read_text() == before
    assert sorted(p.name for p in snap.iterdir()) == listing


def test_a_replayed_leg_is_never_provenance_live(tmp_path, capsys):
    manifest, _ = build_corpus(tmp_path)
    _, out = run_cli(BASE + ["--from-snapshot", str(manifest)], capsys)
    body = flat(out)
    assert "REPLAYED FROM A COMMITTED SNAPSHOT" in body
    assert "margin provenance │ snapshot" in body


def test_a_stale_parser_version_replays_loudly(tmp_path, capsys):
    from src.seats_client import PARSER_VERSION

    manifest, _ = build_corpus(tmp_path)
    manifest.write_text(
        manifest.read_text().replace(f"| {PARSER_VERSION} |", "| 2099-01-01.v99 |")
    )
    code, out = run_cli(BASE + ["--from-snapshot", str(manifest)], capsys)
    assert code == 0
    assert "REPARSED UNDER A DIFFERENT PARSER VERSION" in flat(out)


def test_a_duplicate_row_is_reported_as_superseded_not_double_counted(
    tmp_path, capsys
):
    manifest, _ = build_corpus(tmp_path)
    lines = manifest.read_text().splitlines()
    manifest.write_text("\n".join(lines + [l for l in lines if "| B1 |" in l]) + "\n")
    code, out = run_cli(BASE + ["--from-snapshot", str(manifest)], capsys)
    assert code == 0
    body = flat(out)
    assert "5 rows considered, 4 groups, 4 selected, 1 superseded" in body


# ---------------------------------------------------------------------------
# APD: the parts that are right
# ---------------------------------------------------------------------------


def _leg(origin, destination, when):
    from src.models import Leg

    return Leg(id="X", kind="flight", description="", date=when,
               origin=origin, destination=destination, travelers=1)


@pytest.mark.parametrize(
    "when,gbp",
    [(date(2027, 3, 31), 102.0), (date(2027, 4, 1), 105.33),
     (date(2027, 4, 2), 105.33), (date(2026, 4, 1), 102.0)],
)
def test_the_rate_change_boundary_is_exact(when, gbp):
    from src import apd

    charge = apd.apd_for_leg(_leg("LHR", "SFO", when), cabin="Y", travelers=1)
    assert charge.total_gbp == gbp


def test_a_date_before_every_published_period_is_unknown_not_the_nearest_rate():
    from src import apd

    charge = apd.apd_for_leg(_leg("LHR", "SFO", date(2026, 3, 31)), cabin="Y",
                             travelers=1)
    assert charge.is_known is False
    assert "$0.00" not in charge.render()
    assert "UNKNOWN IS NOT ZERO" in charge.render()


def test_a_non_uk_departure_carries_no_apd_field_at_all():
    from src import apd

    assert apd.apd_for_leg(_leg("SFO", "MAD", date(2027, 1, 15)), cabin="Y",
                           travelers=1) is None


def test_arriving_at_lhr_is_not_a_uk_departure():
    from src import apd

    assert apd.apd_for_leg(_leg("AMS", "LHR", date(2027, 1, 23)), cabin="Y",
                           travelers=1) is None


def test_a_uk_domestic_departure_uses_the_domestic_band_not_band_a():
    from src import apd

    charge = apd.apd_for_leg(_leg("LHR", "EDI", date(2027, 1, 27)), cabin="Y",
                             travelers=1)
    assert charge.band == "DOMESTIC"
    assert charge.total_gbp == 8.0


def test_a_destination_country_the_band_table_calls_unknown_prints_no_dollar_zero():
    from src import apd

    charge = apd.apd_for_leg(_leg("LHR", "CAI", date(2027, 1, 27)), cabin="Y",
                             travelers=1)
    assert charge.is_known is False
    assert "$0.00" not in charge.render()
    assert charge.band is None


def test_apd_is_per_passenger_and_shows_the_multiplication():
    from src import apd

    charge = apd.apd_for_leg(_leg("LHR", "SFO", date(2027, 1, 27)), cabin="Y",
                             travelers=2)
    assert charge.total_gbp == 204.0
    assert "GBP 102.00 x 2 = GBP 204.00" in charge.render()


def test_a_live_leg_states_apd_without_adding_it(tmp_path, capsys):
    manifest, _ = build_corpus(tmp_path)
    code, out = run_cli(BASE + ["--from-snapshot", str(manifest)], capsys)
    assert code == 0
    body = flat(out)
    assert "IT IS NOT ADDED HERE" in body
    assert "NOBODY HAS CHECKED" in body


def test_offline_trip_b_b4_carries_the_added_apd_line(capsys):
    code, out = run_cli(BASE + ["--offline"], capsys)
    assert code == 0
    body = flat(out)
    assert "ADDED to the points-side cash total: GBP 102.00 x 1" in body
    # B1/B2/B3 do not depart the UK, so no APD line and no $0.00 for them.
    assert body.count("UK AIR PASSENGER DUTY on") == 1
    assert "UK AIR PASSENGER DUTY on B4" in body


# ---------------------------------------------------------------------------
# --new-trip: every refusal, and nothing written
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,legs,hotels,kw",
    [
        ("../../etc/foo", ["SFO:MAD:2027-01-15:395"], [], {}),
        ("a/b", ["SFO:MAD:2027-01-15:395"], [], {}),
        (".", ["SFO:MAD:2027-01-15:395"], [], {}),
        ("ok", ["XXX:MAD:2027-01-15:395"], [], {}),
        ("ok", ["SF:MAD:2027-01-15:395"], [], {}),
        ("ok", ["SFOO:MAD:2027-01-15:395"], [], {}),
        ("ok", ["SFO:SFO:2027-01-15:395"], [], {}),
        ("ok", ["SFO:MAD:2027-02-30:395"], [], {}),
        ("ok", ["SFO:MAD:15/01/2027:395"], [], {}),
        ("ok", ["SFO:MAD:2020-01-15:395"], [], {}),
        ("ok", ["SFO:MAD:2027-01-15:0"], [], {}),
        ("ok", ["SFO:MAD:2027-01-15:-1"], [], {}),
        ("ok", ["SFO:MAD:2027-01-15:abc"], [], {}),
        ("ok", ["SFO:MAD:2027-01-15:$395"], [], {}),
        ("ok", ["SFO:MAD:2027-01-15:nan"], [], {}),
        ("ok", ["SFO:MAD:2027-01-15"], [], {}),
        ("ok", ["SFO:MAD:2027-01-15:395:9"], [], {}),
        ("ok", ["SFO:MAD:2027-01-15:395"], [], {"travelers": 0}),
        ("ok", ["SFO:MAD:2027-01-15:395"], [], {"travelers": -1}),
        ("ok", ["SFO:MAD:2027-01-15:395"], [], {"cabin": "X"}),
        ("ok", [], [], {}),
        ("ok", [], ["Hilton:2027-01-15:0:200"], {}),
    ],
)
def test_every_bad_input_is_refused_and_writes_nothing(tmp_path, name, legs, hotels, kw):
    from src import trip_builder

    before = sorted(os.listdir(tmp_path))
    with pytest.raises(trip_builder.TripBuilderError):
        trip_builder.new_trip_from_flags(name, legs, hotels, directory=tmp_path, **kw)
    assert sorted(os.listdir(tmp_path)) == before


def test_lowercase_and_padded_codes_are_upper_cased_not_refused(tmp_path):
    from src import trip_builder

    path = trip_builder.new_trip_from_flags(
        "lc", [" sfo : mad :2027-01-15:395"], [], directory=tmp_path
    )
    leg = json.loads(path.read_text())["legs"][0]
    assert (leg["origin"], leg["destination"]) == ("SFO", "MAD")


def test_an_existing_fixture_is_not_overwritten_without_force(tmp_path):
    from src import trip_builder

    trip_builder.new_trip_from_flags(
        "dup", ["SFO:MAD:2027-01-15:395"], [], directory=tmp_path
    )
    with pytest.raises(trip_builder.TripBuilderError, match="already exists"):
        trip_builder.new_trip_from_flags(
            "dup", ["SFO:MAD:2027-01-15:395"], [], directory=tmp_path
        )
    trip_builder.new_trip_from_flags(
        "dup", ["SFO:MAD:2027-01-15:400"], [], directory=tmp_path, force=True
    )


def test_no_points_candidates_key_is_written_anywhere(tmp_path):
    from src import trip_builder

    path = trip_builder.new_trip_from_flags(
        "np", ["SFO:MAD:2027-01-15:395"], ["Hotel Kyoto: Gion:2027-01-16:3:200"],
        directory=tmp_path,
    )
    raw = json.loads(path.read_text())
    assert all("points_candidates" not in leg for leg in raw["legs"])
    hotel = raw["legs"][1]
    assert hotel["kind"] == "hotel"
    assert "origin" not in hotel and "destination" not in hotel
    assert hotel["nights"] == 3
    # A colon inside the hotel name survives rsplit.
    assert "Hotel Kyoto: Gion" in hotel["description"]


def test_the_description_is_generated_from_the_codes(tmp_path):
    from src import trip_builder

    path = trip_builder.new_trip_from_flags(
        "desc", ["SFO:MAD:2027-01-15:395"], [], directory=tmp_path
    )
    leg = json.loads(path.read_text())["legs"][0]
    assert leg["description"].startswith("SFO->MAD,")
    assert leg["origin"] in leg["description"]
    assert leg["destination"] in leg["description"]


def test_a_built_fixture_scores_offline_with_no_points_path(tmp_path, capsys):
    from src import trip_builder

    path = trip_builder.new_trip_from_flags(
        "e2e", ["SFO:MAD:2027-01-15:395", "MAD:AMS:2027-01-19:44"], [],
        directory=tmp_path,
    )
    code, out = run_cli(
        ["--trip-fixture", str(path), "--offline", "--balance", "UR=160000",
         "--card", "Chase Sapphire Preferred", "--transfer-date", "2026-09-15"],
        capsys,
    )
    assert code == 0
    body = flat(out)
    assert "THIS FIXTURE CARRIES NO POINTS PRICES AT ALL" in body
    assert "CASH (NO POINTS PATH)" in body


# ---------------------------------------------------------------------------
# The eight-way invariants still raise under -O
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs,fragment",
    [
        (dict(state="NO_AWARD_SPACE", error="boom"), "NO_AWARD_SPACE means the API"),
        (dict(state="NO_AWARD_SPACE", rows_unreadable=1, rows_seen=1),
         "UNREADABLE"),
        (dict(state="NO_AWARD_SPACE", result_incomplete=True,
              incomplete_reason="truncated."), "INCOMPLETE result set"),
        (dict(state="API_ERROR"), "no error was given"),
        (dict(state="NOT_QUERIED"), "NOT_QUERIED with no note"),
        (dict(state="ANSWERED_UNREADABLE", error="e"), "zero unreadable rows"),
        (dict(state="OK", served_from_cache=True, awards_parsed=1),
         "no cache_fetched_at"),
        (dict(state="OK", awards_parsed=1, replayed_from_snapshot=True),
         "no snapshot_content_hash"),
    ],
)
def test_the_invariants_are_raises_not_asserts(kwargs, fragment):
    """`python -O` deletes asserts; these must survive it. Run this file with -O."""
    from src.models import LiveLegOutcome, LiveQueryState, PointsProvenance

    kwargs = dict(kwargs)
    state = getattr(LiveQueryState, kwargs.pop("state"))
    with pytest.raises(ValueError) as e:
        LiveLegOutcome(
            leg_id="B1", state=state,
            provenance=PointsProvenance.UNAVAILABLE, **kwargs
        )
    assert fragment in str(e.value)


def test_a_replay_may_not_also_be_a_cache_hit_or_call_itself_live():
    from datetime import datetime, timezone

    from src.models import LiveLegOutcome, LiveQueryState, PointsProvenance

    base = dict(
        leg_id="B1", state=LiveQueryState.OK,
        provenance=PointsProvenance.SNAPSHOT, awards_parsed=1,
        replayed_from_snapshot=True, snapshot_content_hash="a" * 64,
        snapshot_name="b1.json",
        snapshot_captured_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
    )
    with pytest.raises(ValueError, match="served_from_cache"):
        LiveLegOutcome(**base, served_from_cache=True,
                       cache_fetched_at=datetime(2027, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="provenance LIVE"):
        LiveLegOutcome(**{**base, "provenance": PointsProvenance.LIVE})
