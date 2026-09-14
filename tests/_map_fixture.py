"""
A SYNTHETIC hubs file for tests and probes - never committed to data/.

The codes are a test's choice; every coordinate, name, city and country is a
row of data/airportsdata_iata.csv (nothing is typed by hand), the route counts
are made up and the file says so in `_meta.synthetic`. `write_synthetic_hubs`
gives a probe server a file to inject in place of data/hubs.json.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from src import map_tools, regions

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "ui" / "hubs_synthetic.json"

# Twelve airports the engine accepts (data/airports.csv), chosen so a test can
# see: a west-coast cluster with the mockup's "san" autofill set (SFO, SJC,
# SAN), one New York cluster whose members carry two city names (JFK, LGA are
# "New York"; EWR is "Newark"), a London pair, and SYD for the antimeridian.
SYNTHETIC_CODES = ["SFO", "SJC", "SAN", "SAT", "SJU", "JFK", "LGA", "EWR", "LHR", "LGW",
                   "SYD", "MAD"]
# Made-up route counts, descending so the lead of each cluster is fixed.
SYNTHETIC_ROUTES = {
    "SFO": 812, "JFK": 640, "LHR": 590, "EWR": 410, "LGA": 380, "SYD": 300, "MAD": 260,
    "SAN": 240, "SJC": 180, "LGW": 160, "SAT": 120, "SJU": 90,
}
SYNTHETIC_SOURCES = ["aeroplan", "united", "virginatlantic"]


def synthetic_document(codes: Iterable[str] = SYNTHETIC_CODES,
                       routes: Optional[Dict[str, int]] = None,
                       captured_at: str = "2026-09-20T18:02:11Z") -> dict:
    routes = routes or SYNTHETIC_ROUTES
    coords = map_tools.load_coordinates()
    known = set(regions.known_airports())
    hubs: List[dict] = []
    for code in sorted(codes):
        row = coords[code]
        hubs.append({
            "iata": code,
            "name": row["name"], "city": row["city"], "country": row["country"],
            "lat": float(row["lat"]), "lon": float(row["lon"]),
            "routes": routes.get(code, 30),
            "sources": list(SYNTHETIC_SOURCES),
            "regions": [],
            "searchable": code in known,
        })
    return {
        "_meta": {
            "captured_by": "tests/_map_fixture.py (SYNTHETIC - not a Seats.aero answer)",
            "captured_at": captured_at,
            "endpoint": map_tools.ENDPOINT,
            "sources_asked": list(SYNTHETIC_SOURCES),
            "sources_ok": list(SYNTHETIC_SOURCES),
            "sources_failed": {},
            "sources_incomplete": [],
            "routes_seen": sum(routes.get(c, 30) for c in codes),
            "thresholds": {"min_sources": 3, "min_routes": 20},
            "airport_table": map_tools.AIRPORT_TABLE,
            "engine_airports_sha256": "synthetic",
            "searchable_marked_at": captured_at,
            "key_redacted": True,
            "synthetic": True,
        },
        "hubs": hubs,
    }


def write_synthetic_hubs(path: Path, doc: Optional[dict] = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(map_tools.render_document(doc or synthetic_document()), encoding="utf-8")
    return path


if __name__ == "__main__":
    write_synthetic_hubs(FIXTURE)
    print("wrote", FIXTURE, json.dumps(synthetic_document())[:80])
