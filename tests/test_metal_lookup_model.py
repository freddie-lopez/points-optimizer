"""
The operating-airline lookup's vocabulary: MetalStatus and MetalLookup.

The recurring failure in this project is a failure printed as a finding. For
this feature that would be "operated by X" or "no trips" from a lookup that did
not establish either. These tests pin the constructor that refuses such a
lookup, and the prose every non-KNOWN state renders.
"""
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src import models, seats_trips
from src.models import METAL_REASONS, MetalLookup, MetalStatus

ROOT = Path(__file__).parent.parent
NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)

KNOWN_OK = dict(
    status=MetalStatus.KNOWN,
    availability_id="abcDEF1234567890",
    carriers=("VS",),
    matched_trips=1,
    provenance="seats_aero_trips",
    row_carriers=("VS", "DL"),
    flight_numbers=("VS19",),
)
AMBIGUOUS_OK = dict(
    status=MetalStatus.AMBIGUOUS,
    carrier_sets=(("AF",), ("KL",)),
    possible_carriers=("AF", "KL"),
    matched_trips=2,
    provenance="seats_aero_trips",
)
UNKNOWN_OK = dict(
    status=MetalStatus.UNKNOWN,
    reason_code="NO_MATCH",
    detail="costs seen in J: 70,000",
    possible_carriers=("VS", "DL"),
)


def _with(base, **kw):
    return {**base, **kw}


FORBIDDEN = {
    "known_without_carriers": _with(KNOWN_OK, carriers=()),
    "known_without_a_matched_trip": _with(KNOWN_OK, matched_trips=0),
    "known_with_a_reason": _with(KNOWN_OK, reason_code="NO_MATCH", detail="x"),
    "known_without_provenance": _with(KNOWN_OK, provenance=""),
    "known_with_carrier_sets": _with(KNOWN_OK, carrier_sets=(("VS",), ("DL",))),
    "ambiguous_with_one_set": _with(AMBIGUOUS_OK, carrier_sets=(("AF",),)),
    "ambiguous_with_the_same_set_twice": _with(
        AMBIGUOUS_OK, carrier_sets=(("AF", "KL"), ("KL", "AF"))
    ),
    "ambiguous_naming_carriers": _with(AMBIGUOUS_OK, carriers=("AF",)),
    "ambiguous_with_one_trip": _with(AMBIGUOUS_OK, matched_trips=1),
    "ambiguous_with_a_reason": _with(AMBIGUOUS_OK, reason_code="NO_MATCH"),
    "ambiguous_without_provenance": _with(AMBIGUOUS_OK, provenance=""),
    "ambiguous_without_a_domain": _with(AMBIGUOUS_OK, possible_carriers=()),
    "ambiguous_with_a_short_domain": _with(AMBIGUOUS_OK, possible_carriers=("AF",)),
    "ambiguous_unbounded": _with(AMBIGUOUS_OK, domain_unbounded=True),
    "unknown_with_a_not_looked_up_reason": _with(UNKNOWN_OK, reason_code="CAP_REACHED"),
    "unknown_with_an_invented_reason": _with(UNKNOWN_OK, reason_code="NO_TRIPS"),
    "unknown_without_a_reason": _with(UNKNOWN_OK, reason_code=""),
    "unknown_without_detail": _with(UNKNOWN_OK, detail=""),
    "unknown_with_blank_detail": _with(UNKNOWN_OK, detail="   "),
    "unknown_naming_a_carrier": _with(UNKNOWN_OK, carriers=("VS",)),
    "unknown_naming_carrier_sets": _with(UNKNOWN_OK, carrier_sets=(("VS",), ("DL",))),
    "unknown_with_both_domains": _with(UNKNOWN_OK, domain_unbounded=True),
    "unknown_with_no_domain": _with(UNKNOWN_OK, possible_carriers=()),
    "not_looked_up_served_from_cache": dict(
        status=MetalStatus.NOT_LOOKED_UP, reason_code="CAP_REACHED", detail="10",
        possible_carriers=("VS",), served_from_cache=True, fetched_at=NOW,
    ),
    "not_looked_up_with_fetched_at": dict(
        status=MetalStatus.NOT_LOOKED_UP, reason_code="CAP_REACHED", detail="10",
        possible_carriers=("VS",), fetched_at=NOW,
    ),
    "not_looked_up_replayed": dict(
        status=MetalStatus.NOT_LOOKED_UP, reason_code="CAP_REACHED", detail="10",
        possible_carriers=("VS",), on_replay=True, replayed_from_snapshot=True,
        snapshot_content_hash="ab" * 32,
    ),
    "not_looked_up_with_matched_trips": dict(
        status=MetalStatus.NOT_LOOKED_UP, reason_code="CAP_REACHED", detail="10",
        possible_carriers=("VS",), matched_trips=1,
    ),
    "not_recorded_on_a_live_run": dict(
        status=MetalStatus.NOT_RECORDED, reason_code="NO_TRIPS_SNAPSHOT",
        detail="no row", possible_carriers=("VS",),
    ),
    "not_recorded_with_bytes": dict(
        status=MetalStatus.NOT_RECORDED, reason_code="NO_TRIPS_SNAPSHOT",
        detail="no row", possible_carriers=("VS",), on_replay=True,
        replayed_from_snapshot=True, snapshot_content_hash="ab" * 32,
    ),
    "replayed_without_a_hash": _with(
        KNOWN_OK, on_replay=True, replayed_from_snapshot=True
    ),
    "replayed_and_cached": _with(
        KNOWN_OK, on_replay=True, replayed_from_snapshot=True,
        snapshot_content_hash="ab" * 32, served_from_cache=True, fetched_at=NOW,
    ),
    "replayed_outside_a_replay": _with(
        KNOWN_OK, replayed_from_snapshot=True, snapshot_content_hash="ab" * 32
    ),
    "cached_without_fetched_at": _with(KNOWN_OK, served_from_cache=True),
    "status_is_not_the_enum": _with(KNOWN_OK, status="known"),
}


def test_the_good_shapes_build():
    for kw in (KNOWN_OK, AMBIGUOUS_OK, UNKNOWN_OK):
        MetalLookup(**kw)


@pytest.mark.parametrize("name", sorted(FORBIDDEN))
def test_every_forbidden_combination_raises(name):
    with pytest.raises(ValueError):
        MetalLookup(**FORBIDDEN[name])


def test_the_forbidden_combinations_still_raise_under_python_O():
    """The invariants are ValueErrors, not asserts, so -O cannot delete them."""
    script = (
        "from datetime import datetime, timezone\n"
        "from tests.test_metal_lookup_model import FORBIDDEN\n"
        "from src.models import MetalLookup\n"
        "raised = 0\n"
        "for kw in FORBIDDEN.values():\n"
        "    try:\n"
        "        MetalLookup(**kw)\n"
        "    except ValueError:\n"
        "        raised += 1\n"
        "print(raised, len(FORBIDDEN), __debug__)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-O", "-c", script], cwd=ROOT, capture_output=True, text=True
    )
    assert proc.returncode == 0, proc.stderr
    raised, total, debug = proc.stdout.split()
    assert debug == "False", "the child did not run under -O"
    assert raised == total == str(len(FORBIDDEN))


def test_the_source_has_no_assert_in_the_metal_section():
    source = (ROOT / "src" / "models.py").read_text()
    block = source[source.index("class MetalStatus"):]
    assert "raise ValueError" in block
    assert "\n        assert " not in block and "\n    assert " not in block


# ---------------------------------------------------------------------------
# Every non-KNOWN render
# ---------------------------------------------------------------------------


def _every_non_known():
    out = []
    for status, reasons in METAL_REASONS.items():
        for reason in sorted(reasons):
            kw = dict(
                status=status,
                availability_id="abcDEF1234567890",
                reason_code=reason,
                detail="some detail",
                possible_carriers=("VS", "DL"),
            )
            if status is MetalStatus.NOT_RECORDED:
                kw["on_replay"] = True
            out.append(MetalLookup(**kw))
            out.append(
                MetalLookup(**{**kw, "possible_carriers": (), "domain_unbounded": True})
            )
    out.append(MetalLookup(**AMBIGUOUS_OK))
    return out


FORBIDDEN_WORDS = ("operated by", "no trips", "no flights", "not available")


@pytest.mark.parametrize("lookup", _every_non_known(), ids=lambda m: f"{m.status.value}-{m.reason_code or 'ambiguous'}-{'unbounded' if m.domain_unbounded else 'bounded'}")
def test_every_non_known_render_says_so_and_names_its_domain(lookup):
    text = lookup.render()
    assert any(s in text for s in ("NOT KNOWN", "NOT LOOKED UP", "NOT RECORDED")), text
    assert "possible carriers" in text, text
    lowered = text.lower()
    for word in FORBIDDEN_WORDS:
        assert word not in lowered, f"{word!r} in: {text}"
    # A bare carrier as the answer: "operating airline:" followed by anything
    # other than the NOT marker.
    assert re.search(r"operating airline:\s+NOT ", text), text
    assert not re.search(r"operating airline:\s+(?!NOT )", text), text


def test_every_reason_code_has_its_own_prose():
    for reasons in METAL_REASONS.values():
        for reason in reasons:
            assert reason in models._METAL_REASON_PROSE, reason


def test_the_d6_wording_is_what_renders():
    cap = MetalLookup(
        status=MetalStatus.NOT_LOOKED_UP, reason_code="CAP_REACHED", detail="1",
        possible_carriers=("VS", "DL"),
    ).render()
    assert "NOT LOOKED UP - the per-run cap of 1 lookups was reached" in cap
    assert "Nothing is known about which airline flies it; the possible carriers are VS, DL" in cap
    off = MetalLookup(
        status=MetalStatus.NOT_LOOKED_UP, reason_code="TRIPS_OFF",
        detail="--trips off", possible_carriers=("VS",),
    ).render()
    assert "NOT LOOKED UP (--trips off)" in off
    budget = MetalLookup(
        status=MetalStatus.NOT_LOOKED_UP, reason_code="BUDGET_EXHAUSTED",
        detail="0 calls left", possible_carriers=("VS",),
    ).render()
    assert (
        "NOT LOOKED UP - the Seats.aero call budget for this run is spent; no "
        "request was made" in budget
    )


# ---------------------------------------------------------------------------
# KNOWN
# ---------------------------------------------------------------------------


def test_a_known_render_claims_only_the_marketing_carrier():
    text = MetalLookup(**KNOWN_OK).render()
    assert text.startswith("operating airline: VS by flight number (VS19)")
    assert "Seats.aero reports the MARKETING carrier" in text
    assert "it does not report who operates the flight" in text
    assert "A codeshare operated by another airline in this award's list (VS, DL) cannot be detected" in text


def test_a_known_render_on_a_single_carrier_row_has_no_codeshare_clause():
    text = MetalLookup(**_with(KNOWN_OK, row_carriers=("VS",))).render()
    assert "codeshare" not in text


def test_while_the_constant_is_empty_every_known_render_is_labelled_unverified():
    assert seats_trips.TRIPS_SCHEMA_VERIFIED_BY == ""
    for kw in (KNOWN_OK, _with(KNOWN_OK, parser_verified=True)):
        assert "UNVERIFIED" in MetalLookup(**kw).render()
    assert "UNVERIFIED" in MetalLookup(**AMBIGUOUS_OK).render()
    assert "UNVERIFIED" in MetalLookup(**UNKNOWN_OK).render()


def test_a_transport_unknown_carries_no_parser_label():
    text = MetalLookup(
        status=MetalStatus.UNKNOWN, reason_code="TIMEOUT", detail="15s",
        possible_carriers=("VS",),
    ).render()
    assert "trips parser UNVERIFIED" not in text


def test_the_label_drops_only_when_both_the_module_and_the_lookup_say_verified(monkeypatch):
    monkeypatch.setattr(seats_trips, "TRIPS_SCHEMA_VERIFIED_BY", "x.json")
    assert "UNVERIFIED" not in MetalLookup(**_with(KNOWN_OK, parser_verified=True)).render()
    assert "UNVERIFIED" in MetalLookup(**KNOWN_OK).render()


def test_a_replayed_lookup_says_so_and_flags_a_reparse():
    same = MetalLookup(
        **_with(
            KNOWN_OK, on_replay=True, replayed_from_snapshot=True,
            snapshot_name="B4_trips_x.json", snapshot_content_hash="ab" * 32,
            snapshot_parser_version=seats_trips.TRIPS_PARSER_VERSION,
        )
    ).render()
    assert "REPLAYED FROM A COMMITTED TRIPS SNAPSHOT" in same
    assert "REPARSED" not in same
    old = MetalLookup(
        **_with(
            KNOWN_OK, on_replay=True, replayed_from_snapshot=True,
            snapshot_name="B4_trips_x.json", snapshot_content_hash="ab" * 32,
            snapshot_parser_version="2026-01-01.old",
        )
    ).render()
    assert "REPARSED" in old and "2026-01-01.old" in old


def test_a_cached_lookup_names_its_fetch_time():
    text = MetalLookup(**_with(KNOWN_OK, served_from_cache=True, fetched_at=NOW)).render()
    assert "Served from the disk cache (fetched 2026-09-11T12:00:00Z)" in text


# ---------------------------------------------------------------------------
# Storage classification
# ---------------------------------------------------------------------------


def test_every_invariant_field_is_classified():
    fields = {f.name for f in models.dataclasses.fields(MetalLookup)}
    read = models.METAL_INVARIANT_FIELDS & fields
    declared = (
        set(models.METAL_RECOMPUTED_FROM_BYTES)
        | set(models.METAL_CARRIED_BY_THE_TRANSPORT)
        | set(models.METAL_LOCAL_TO_THIS_RUN)
    )
    assert read, "the AST derivation found nothing - the check would be vacuous"
    assert read <= declared


def test_the_classification_check_refuses_an_unclassified_field(monkeypatch):
    monkeypatch.setattr(
        models,
        "METAL_LOCAL_TO_THIS_RUN",
        models.METAL_LOCAL_TO_THIS_RUN - {"provenance"},
    )
    with pytest.raises(ValueError, match="no storage classification covers"):
        models._check_metal_classification()


def test_the_missing_and_unresolved_predicates():
    policy = MetalLookup(
        status=MetalStatus.NOT_LOOKED_UP, reason_code="NOT_NEEDED_POLICY",
        detail="United MileagePlus", possible_carriers=("UA",),
    )
    cap = MetalLookup(
        status=MetalStatus.NOT_LOOKED_UP, reason_code="CAP_REACHED", detail="10",
        possible_carriers=("UA",),
    )
    assert not policy.is_missing_lookup and cap.is_missing_lookup
    assert MetalLookup(**UNKNOWN_OK).is_unresolved
    assert MetalLookup(**AMBIGUOUS_OK).is_unresolved
    assert not MetalLookup(**KNOWN_OK).is_unresolved
    assert MetalLookup(**AMBIGUOUS_OK).all_carriers == ("AF", "KL")
