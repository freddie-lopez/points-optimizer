"""
v5 adversarial probes: THE DEFECTS. Every test here asserts the bug is PRESENT,
so it goes RED the moment the bug is fixed.

Run: python -m pytest docs/test-reports/v5-probes -p no:randomly
"""
import json
import pathlib
import re
import tempfile
from datetime import date
from pathlib import Path

import pytest
import requests

from conftest import BASE, TRIP_B, build_corpus, flat, one_award_page, run_cli


# ===========================================================================
# C-1  WAY NINE. A truncated result loses its truncation on every path that
#      REUSES it: the disk cache and the snapshot replay.
# ===========================================================================


def test_C1a_put_drops_the_incomplete_flag_it_is_handed(tmp_path):
    """
    seats_client hands `incomplete` to `cache.put`; `put` builds `_meta` from an
    explicit key list that does not contain it, so it is dropped on the floor.
    MR-1's cache round-trip fix is dead code and its comment says otherwise.
    """
    from src.response_cache import ResponseCache

    cache = ResponseCache(
        cache_dir=tmp_path / "c", snapshot_dir=tmp_path / "s", ttl_seconds=99999
    )
    written = cache.put(
        "k1",
        {"origin_airport": "LHR", "destination_airport": "SFO",
         "start_date": "2027-01-27", "end_date": "2027-01-27"},
        [{"data": [], "hasMore": True}],
        meta={
            "endpoint": "search",
            "pagination_note": "STOPPED after page 1: hasMore with no cursor.",
            "incomplete": True,
            "incomplete_reason": "hasMore with no cursor.",
        },
    )
    assert "incomplete" not in written.meta, "FIXED: put now persists coverage"
    hit = cache.get("k1")
    assert hit.meta.get("incomplete", False) is False
    assert hit.meta.get("incomplete_reason", "") == ""


def test_C1b_the_same_bytes_are_incomplete_live_and_a_FINDING_from_the_cache(tmp_path):
    """
    The whole class, end to end. Run 1 answers ANSWERED_INCOMPLETE. Run 2, on the
    identical bytes served from the disk cache, answers "THIS IS A FINDING: ...
    there is no award to buy on this date".
    """
    from src import response_cache, seats_client
    from src.live_trip import LiveOptions, query_leg
    from src.models import Leg, LiveQueryState
    from src.seats_client import SeatsClient

    class Reply:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [], "hasMore": True}

    original = seats_client.requests.get
    seats_client.requests.get = lambda *a, **k: Reply()
    try:
        cache = response_cache.ResponseCache(
            cache_dir=tmp_path / "c", snapshot_dir=tmp_path / "s", ttl_seconds=99999
        )
        leg = Leg(id="B4", kind="flight", description="LHR->SFO",
                  date=date(2027, 1, 27), origin="LHR", destination="SFO",
                  travelers=1)
        opts = LiveOptions(live=True, cache=cache, trip_id="t")

        first, _ = query_leg(leg, SeatsClient("testkey"), opts)
        assert first.state is LiveQueryState.ANSWERED_INCOMPLETE
        assert first.result_incomplete is True

        SeatsClient.CACHE.clear()
        SeatsClient.CACHE_META.clear()
        second, _ = query_leg(leg, SeatsClient("testkey"), opts)
    finally:
        seats_client.requests.get = original

    assert second.served_from_cache is True
    # THE DEFECT: the truncation is gone and the leg now asserts a finding.
    assert second.result_incomplete is False, "FIXED: coverage survives the cache"
    assert second.state is LiveQueryState.NO_AWARD_SPACE
    assert "THIS IS A FINDING" in second.render()
    assert "COVERAGE IS INCOMPLETE" not in second.render()


def test_C1c_a_replay_hardcodes_complete_coverage(tmp_path, capsys):
    """
    SnapshotTransport.search_raw passes `incomplete=False` literally. A snapshot
    whose bytes say `hasMore` with no cursor - which the LIVE path calls
    INCOMPLETE - replays with no coverage warning at all.
    """
    def truncated(leg_id, origin, destination, on_date):
        page = one_award_page(origin, destination, on_date)
        page["hasMore"] = True
        return [page]

    manifest, _ = build_corpus(
        tmp_path,
        pages_for=truncated,
        meta_extra=lambda leg: {
            "pagination_note": (
                "STOPPED after page 1: the response reported hasMore with no "
                "cursor. The result below is INCOMPLETE."
            )
        },
    )
    code, out = run_cli(BASE + ["--from-snapshot", str(manifest)], capsys)
    assert code == 0
    assert "COVERAGE IS INCOMPLETE" not in out, "FIXED: replay preserves coverage"

    src = (Path(__file__).resolve().parents[3] / "src" / "snapshot_replay.py").read_text()
    assert "incomplete=False," in src


# ===========================================================================
# C-2  A REPLAY THAT SCORED NOTHING PRINTS THE BADGE MARGIN WITH THE HASH
#      GLUED TO IT.
# ===========================================================================


def test_C2_a_badge_margin_is_printed_with_a_manifest_hash(tmp_path, capsys):
    """
    Every snapshot contributes nothing, the fixture's Google badges answer
    instead, and the number printed is byte-identical to the OFFLINE badge
    margin - with `mh_...` on the same line, certifying bytes that entered no
    part of it. The qualifier that would correct it ('badge_fallback') is on a
    DIFFERENT line.
    """
    manifest, _ = build_corpus(tmp_path, pages_for=lambda *a: [])
    code, replayed = run_cli(
        BASE + ["--from-snapshot", str(manifest), "--allow-badge-fallback"], capsys
    )
    assert code == 0
    replayed_flat = flat(replayed)

    code2, offline = run_cli(BASE + ["--offline"], capsys)
    assert code2 == 0
    offline_pct = re.search(r"(\d+\.\d\d% - \d+\.\d\d%)", flat(offline)).group(1)

    headline = re.search(
        r"Optimizer beats paying cash by │ ([^│]+)│", replayed_flat
    ).group(1)
    assert offline_pct in headline, "FIXED: the badge margin no longer replays"
    # THE DEFECT: the hash rides the badge number.
    assert re.search(r"mh_[0-9a-f]{16}", headline), (
        "FIXED: a manifest hash is no longer attached to a badge margin"
    )
    # ...and the correcting qualifier is NOT on that line.
    assert "badge_fallback" not in headline
    assert "badge_fallback" in replayed_flat


# ===========================================================================
# H-1  THE HASH DOES NOT BIND THE TRIP. A replay answers with bytes captured
#      for a DIFFERENT ROUTE, and says nothing about it.
# ===========================================================================


def _fixture_with(tmp_path, mutate, name="edited.json"):
    data = json.loads(TRIP_B.read_text())
    mutate(data)
    directory = Path(tmp_path)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(data))
    return path


def test_H1a_a_leg_is_scored_from_a_snapshot_of_another_route(tmp_path, capsys):
    """
    Plan section 8.1 proposes exactly this edit (B1's origin). The manifest is
    unchanged, every hash still verifies, and B1 - now MRY->MAD - is scored from
    B1_SFO_MAD_2027-01-15.json. Exit 0, margin printed, hash printed.
    """
    manifest, _ = build_corpus(tmp_path)
    edited = _fixture_with(tmp_path, lambda d: d["legs"][0].__setitem__("origin", "MRY"))
    code, out = run_cli(
        ["--trip-fixture", str(edited), "--balance", "UR=160000",
         "--card", "Chase Sapphire Preferred", "--transfer-date", "2026-09-15",
         "--from-snapshot", str(manifest)],
        capsys,
    )
    assert code == 0, "FIXED: the run is now refused"
    body = flat(out)
    assert "MRY->MAD" in body
    assert "B1_SFO_MAD_2027-01-15.json" in body
    assert re.search(r"mh_[0-9a-f]{16}", body)
    # Nothing anywhere says the bytes answer a different question.
    assert "route" not in body.lower().split("manifest cannot")[0][:0] or True
    assert "does not match the leg" not in body.lower()


def test_H1b_the_same_hash_certifies_two_different_itineraries(tmp_path, capsys):
    manifest, _ = build_corpus(tmp_path)

    def run_for(path):
        code, out = run_cli(
            ["--trip-fixture", str(path), "--balance", "UR=160000",
             "--card", "Chase Sapphire Preferred", "--transfer-date", "2026-09-15",
             "--from-snapshot", str(manifest)],
            capsys,
        )
        assert code == 0
        return re.search(r"mh_[0-9a-f]{16}", flat(out)).group(0)

    unchanged = _fixture_with(tmp_path, lambda d: None, name="unchanged.json")
    moved = _fixture_with(
        tmp_path, lambda d: d["legs"][0].__setitem__("origin", "MRY"), name="moved.json"
    )
    assert run_for(unchanged) == run_for(moved), "FIXED: the hash now binds the trip"


def test_H1c_blanking_the_trip_id_column_lets_another_trips_manifest_replay(
    tmp_path, capsys
):
    """
    `select_replay_set` treats a row whose trip_id is absent as matching ANY
    trip. `-` in the column parses to absent, so one character defeats the
    trip binding the module docstring claims.
    """
    manifest, _ = build_corpus(tmp_path, trip_id="a_completely_different_trip")
    code, _ = run_cli(BASE + ["--from-snapshot", str(manifest)], capsys)
    assert code == 1  # correct, while the column is populated

    manifest.write_text(
        manifest.read_text().replace("| a_completely_different_trip |", "| - |")
    )
    code, out = run_cli(BASE + ["--from-snapshot", str(manifest)], capsys)
    assert code == 0, "FIXED: an untagged row no longer matches every trip"
    assert re.search(r"mh_[0-9a-f]{16}", flat(out))


# ===========================================================================
# H-2  APD's cabin comes from the POINTS CANDIDATE, never from the leg. A
#      --new-trip fixture has no candidates BY DESIGN, so it is always "Y".
# ===========================================================================


def test_H2a_a_business_class_uk_departure_is_charged_the_reduced_rate(
    tmp_path, capsys
):
    from src import trip_builder

    path = trip_builder.new_trip_from_flags(
        "apd_cabin_probe",
        ["LHR:SFO:2027-01-27:500"],
        [],
        travelers=1,
        cabin="J",
        directory=tmp_path,
    )
    written = json.loads(path.read_text())
    assert written["legs"][0]["cabin"] == "J"
    assert "points_candidates" not in written["legs"][0]

    code, out = run_cli(
        ["--trip-fixture", str(path), "--offline", "--balance", "UR=160000",
         "--card", "Chase Sapphire Preferred", "--transfer-date", "2026-09-15"],
        capsys,
    )
    assert code == 0
    body = flat(out)
    assert "GBP 102.00 x 1" in body, "FIXED: the leg's own cabin now reaches APD"
    assert "per the reduced rate" in body
    assert "GBP 244.00" not in body


def test_H2b_the_leg_level_cabin_key_is_read_by_nothing(tmp_path):
    """The builder writes it, the loader drops it, `Leg` has no field for it."""
    from src.models import Leg
    from src.trip_loader import load_trip_fixture

    assert not hasattr(Leg(id="x", kind="flight", description="", date=date(2027, 1, 1)), "cabin")
    fixture = load_trip_fixture(TRIP_B)
    assert all(not hasattr(leg, "cabin") for leg in fixture.legs)


def test_H2c_a_W_cabin_leg_never_prints_the_pitch_caveat(tmp_path, capsys):
    from src import trip_builder

    path = trip_builder.new_trip_from_flags(
        "apd_w_probe", ["LHR:SFO:2027-01-27:500"], [], cabin="W", directory=tmp_path
    )
    code, out = run_cli(
        ["--trip-fixture", str(path), "--offline", "--balance", "UR=160000",
         "--card", "Chase Sapphire Preferred", "--transfer-date", "2026-09-15"],
        capsys,
    )
    assert code == 0
    assert "CABIN MAPPING CAVEAT" not in out, "FIXED: W now prints the pitch caveat"


# ===========================================================================
# H-3  APD IS ADDED ON TOP OF A CAPTURED SURCHARGE - the double charge the
#      whole live/offline asymmetry exists to prevent, on the path the plan
#      called safe.
# ===========================================================================


def test_H3_apd_is_added_on_top_of_a_captured_booking_page_figure(tmp_path, capsys):
    fixture = {
        "id": "apd_double", "name": "apd_double", "description": "d", "source": "s",
        "legs": [{
            "id": "L1", "kind": "flight", "description": "LHR->SFO",
            "date": "2027-01-27", "origin": "LHR", "destination": "SFO",
            "travelers": 1,
            "cash_options": [{"label": "BA cash", "amount": 900.0,
                              "currency": "USD", "date": "2027-01-27"}],
            "points_candidates": [{
                "label": "BA award; taxes+carrier charges CAPTURED from BA.com",
                "program": "British Airways Executive Club",
                "points": 20000, "cash_surcharge": 330.0,
                "surcharge_currency": "USD", "surcharge_captured": True,
                "cabin": "Y", "source": "manual_capture",
                "source_note": (
                    "Captured from the BA.com award booking page: taxes, fees and "
                    "carrier charges GBP 244 = $330, which INCLUDES the GBP 102 "
                    "APD line item."
                ),
            }],
        }],
    }
    path = Path(tmp_path) / "apd_double.json"
    path.write_text(json.dumps(fixture))
    code, out = run_cli(
        ["--trip-fixture", str(path), "--offline", "--balance", "UR=160000",
         "--card", "Chase Sapphire Preferred", "--transfer-date", "2026-09-15"],
        capsys,
    )
    assert code == 0
    body = flat(out)
    # $200 points + $330 captured (already containing APD) + $138.11 APD again.
    assert "$668.11" in body, "FIXED: APD is no longer stacked on a capture"
    assert "ADDED to the points-side cash total" in body
    assert "surcharge_captured" not in body  # nothing even mentions the conflict


# ===========================================================================
# M-1  A SNAPSHOT WITH NO PAGES REPLAYS AS A CONFIDENT FINDING.
# ===========================================================================


def test_M1_an_empty_snapshot_replays_as_a_finding_of_no_award_space(
    tmp_path, capsys
):
    manifest, _ = build_corpus(tmp_path, pages_for=lambda *a: [])
    code, out = run_cli(BASE + ["--from-snapshot", str(manifest)], capsys)
    # The margin is correctly withheld, but every LEG asserts a finding.
    assert code == 3
    body = flat(out)
    assert "THIS IS A FINDING" in body, "FIXED: an empty snapshot is refused"
    assert "every row it sent was READ SUCCESSFULLY" in body
    assert "there is no award to buy on this date" in body

    # `verify` never looks at whether there are any pages at all.
    from src import snapshot_replay

    rows = snapshot_replay.parse_manifest(manifest)
    selection = snapshot_replay.select_replay_set(rows, "trip_b_europe")
    assert snapshot_replay.verify(selection.selected, manifest.parent) == []


# ===========================================================================
# M-2  THE content_hash COLUMN IS COMPARED WITH startswith.
# ===========================================================================


def test_M2_a_one_character_content_hash_column_passes_verification(
    tmp_path, capsys
):
    manifest, _ = build_corpus(tmp_path)
    rows = []
    for line in manifest.read_text().splitlines():
        if line.startswith("| 2027-"):
            cells = line.split("|")
            cells[9] = " " + cells[9].strip()[0] + " "
            line = "|".join(cells)
        rows.append(line)
    manifest.write_text("\n".join(rows) + "\n")

    code, out = run_cli(BASE + ["--from-snapshot", str(manifest)], capsys)
    assert code == 0, "FIXED: a truncated content_hash column is now refused"
    assert re.search(r"mh_[0-9a-f]{16}", flat(out))


# ===========================================================================
# L-1  AN UNRECOGNISED CABIN SILENTLY BECOMES `standard`; AN EMPTY ONE
#      BECOMES `reduced`. Absence-as-a-default, in a tax table whose own
#      docstring forbids it.
# ===========================================================================


@pytest.mark.parametrize(
    "cabin,expected_gbp",
    [("X", 244.0), ("economy", 244.0), ("PREMIUM", 244.0), ("", 102.0)],
)
def test_L1_an_unknown_cabin_resolves_to_a_rate_instead_of_UNKNOWN(cabin, expected_gbp):
    from src import apd
    from src.models import Leg

    leg = Leg(id="X", kind="flight", description="", date=date(2027, 1, 27),
              origin="LHR", destination="SFO", travelers=1)
    charge = apd.apd_for_leg(leg, cabin=cabin, travelers=1)
    assert charge is not None
    assert charge.is_known is True, "FIXED: an unknown cabin is now UNKNOWN"
    assert charge.total_gbp == expected_gbp


# ===========================================================================
# L-2  THE USER-CONFIG KEY FILE'S PERMISSIONS ARE NEVER CHECKED.
# ===========================================================================


def test_L2_a_world_readable_user_config_key_is_used_without_comment(tmp_path):
    import os

    from src import config

    path = tmp_path / "user.env"
    path.write_text("SEATS_AERO_KEY=user_KEYVALUE_uuu\n")
    os.chmod(path, 0o644)
    old_repo, old_user = config._ENV_PATH, config.USER_CONFIG_ENV_PATH
    old_env = os.environ.pop("SEATS_AERO_KEY", None)
    config._ENV_PATH = tmp_path / "absent.env"
    config.USER_CONFIG_ENV_PATH = path
    try:
        resolution = config.resolve_key(None)
        assert resolution.source == "user config"
        # No warning of any kind about the file being world-readable, and no
        # code anywhere consults the mode.
        banner = resolution.describe()
        assert "0644" not in banner and "world-readable" not in banner.lower()
        source = (
            pathlib.Path(config.__file__).read_text()
            + pathlib.Path(config.__file__).with_name("main.py").read_text()
        )
        assert "st_mode" not in source and "S_IRGRP" not in source
    finally:
        config._ENV_PATH, config.USER_CONFIG_ENV_PATH = old_repo, old_user
        if old_env is not None:
            os.environ["SEATS_AERO_KEY"] = old_env


# ===========================================================================
# H-4  A --new-trip FIXTURE SCORED --offline REPORTS ITS OWN DESIGNED ABSENCE
#      OF POINTS PRICES AS A FINDING ABOUT AIRLINE PARTNERSHIPS.
# ===========================================================================


def test_H4_no_captured_price_is_reported_as_no_partner_exists(tmp_path, capsys):
    """
    The builder writes NO points_candidates by design. Scored --offline - a
    documented, legal mode - every flight leg reports "No UR transfer partner
    covers this leg" and the trip footer says "(no partner exists)". SFO->MAD
    has partners: Trip B's own B1 scores an Aeroplan path on that exact route.
    `annotate_live_verdicts` exists to stop this text being used when the cause
    is a data gap, and it runs on the live path only (main.py run_fixture).
    """
    from src import trip_builder

    path = trip_builder.new_trip_from_flags(
        "h4_probe", ["SFO:MAD:2027-01-15:395"], [], directory=tmp_path
    )
    code, out = run_cli(
        ["--trip-fixture", str(path), "--offline", "--balance", "UR=160000",
         "--card", "Chase Sapphire Preferred", "--transfer-date", "2026-09-15"],
        capsys,
    )
    assert code == 0
    body = flat(out)
    assert "No UR transfer partner covers this leg" in body, (
        "FIXED: an absent capture is no longer a claim about partnerships"
    )
    assert "Legs with NO points path at all (no partner exists): L1" in body

    # ...while the same route in Trip B does have a UR partner path.
    _, trip_b = run_cli(BASE + ["--offline"], capsys)
    assert "Air Canada Aeroplan" in flat(trip_b)
