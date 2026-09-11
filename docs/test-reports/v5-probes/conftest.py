"""Shared corpus builders for the v5 adversarial probes.

These live OUTSIDE `testpaths` on purpose: they assert defects are PRESENT, so
they must not run in the normal suite. Run them with:

    python -m pytest docs/test-reports/v5-probes -p no:randomly
"""
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT))

# FIRST, before anything imports `src`: the main suite's conftest clears the key
# and moves the key files away at import. Importing `src.config` before it lets
# `load_env()` read the developer's real repo `.env` during collection.
from tests.conftest import REAL_HOME as _REAL_HOME  # noqa: E402,F401

from src.response_cache import content_hash  # noqa: E402
from src.seats_client import PARSER_VERSION  # noqa: E402

REAL = ROOT / "tests" / "fixtures" / "seats_aero" / "sfo_mad_real.json"
TRIP_B = ROOT / "tests" / "fixtures" / "trips" / "trip_b_europe.json"

TRIP_B_LEGS = [
    ("B1", "SFO", "MAD", "2027-01-15"),
    ("B2", "MAD", "AMS", "2027-01-19"),
    ("B3", "AMS", "LHR", "2027-01-23"),
    ("B4", "LHR", "SFO", "2027-01-27"),
]

BASE = [
    "--trip-fixture", "trip_b_europe.json",
    "--balance", "UR=160000",
    "--card", "Chase Sapphire Preferred",
    "--transfer-date", "2026-09-15",
]


def one_award_page(origin, destination, on_date):
    payload = copy.deepcopy(json.loads(REAL.read_text()))
    row = payload["data"][0]
    row["Route"]["OriginAirport"] = origin
    row["Route"]["DestinationAirport"] = destination
    row["Date"] = on_date
    row["ParsedDate"] = f"{on_date}T00:00:00Z"
    row["ID"] = f"{origin}{destination}{on_date}"
    return {"data": [row]}


def build_corpus(
    tmp_path,
    legs=TRIP_B_LEGS,
    trip_id="trip_b_europe",
    pages_for=None,
    meta_extra=None,
    captured="2027-01-05T09:00:00Z",
):
    """Snapshots + a v5 manifest. The replayable input, in the shape `put` writes."""
    snap = Path(tmp_path) / "live_trip_b"
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
        pages = (
            pages_for(leg_id, origin, destination, on_date)
            if pages_for
            else [one_award_page(origin, destination, on_date)]
        )
        digest = content_hash(pages)
        name = f"{leg_id}_{origin}_{destination}_{on_date}.json"
        meta = {
            "cache_schema": 1,
            "content_hash": digest,
            "parser_version": PARSER_VERSION,
            "fetched_at": captured,
            "request": {
                "origin_airport": origin,
                "destination_airport": destination,
                "start_date": on_date,
                "end_date": on_date,
            },
            "rows_seen": len(pages),
        }
        if meta_extra:
            meta.update(meta_extra(leg_id))
        (snap / name).write_text(json.dumps({"_meta": meta, "pages": pages}, indent=2))
        lines.append(
            f"| {captured} | {leg_id} | {origin}->{destination} "
            f"| {on_date}..{on_date} | 1 | 1 | ok | {name} "
            f"| {digest[:16]} | {PARSER_VERSION} | {trip_id} |"
        )
    manifest = snap / "MANIFEST.md"
    manifest.write_text("\n".join(lines) + "\n")
    return manifest, snap


def run_cli(argv, capsys):
    from src.main import main

    old = sys.argv
    sys.argv = ["prog"] + argv
    try:
        code = main()
    finally:
        sys.argv = old
    return code, capsys.readouterr().out


def flat(text):
    """Rich draws box rules; collapse whitespace so a table cell is greppable."""
    import re

    return re.sub(r"\s+", " ", text)


@pytest.fixture(autouse=True)
def _isolate_client_state():
    """Every probe starts with an empty in-process award cache and call budget."""
    from src.seats_client import SeatsClient

    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient._CALLS = {}
    yield
    SeatsClient.CACHE.clear()
    SeatsClient.CACHE_META.clear()
    SeatsClient._CALLS = {}


# The main suite's environment isolation, applied here too. Without it these
# probes read the developer's real key and real runtime cache exactly as the
# main suite's CLI tests did: under the Mac's conditions (exported key, warm
# data/cache/ from a live run) this directory read 21 red / 77 green instead of
# 19 / 79 on master AND on the fix branch, because two probes got cache hits.
# "Deviation from the recorded counts means a regression" is only true if the
# counts do not depend on the machine.
from tests.conftest import isolated_environment, no_network_egress  # noqa: E402,F401
