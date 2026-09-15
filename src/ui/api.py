"""
The API routes (docs/plans/ui.md section 4.5). Every handler returns
(status, JSON-safe payload) or raises ApiError; the server does the rest.

Trip ids come from the URL but are ONLY resolved against the current listing of
the trips directory (src/ui/engine.py); a run id only against the in-memory
store. Nothing in a URL is ever joined onto a filesystem path.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple

from src.ui.server import ApiError

_TRIP = r"(?P<trip>[A-Za-z0-9][A-Za-z0-9_.-]{0,120})"
ROUTES = [
    ("GET", re.compile(r"^/api/state$"), "state"),
    ("POST", re.compile(r"^/api/wallet$"), "wallet"),
    ("GET", re.compile(r"^/api/trips$"), "trips"),
    ("POST", re.compile(r"^/api/trips/draft$"), "trip_draft"),
    ("POST", re.compile(r"^/api/trips/create$"), "trip_create"),
    ("GET", re.compile(rf"^/api/trips/{_TRIP}$"), "trip_detail"),
    ("POST", re.compile(rf"^/api/trips/{_TRIP}/preflight$"), "trip_preflight"),
    ("POST", re.compile(rf"^/api/trips/{_TRIP}/run$"), "trip_run"),
    ("POST", re.compile(r"^/api/search/preflight$"), "search_preflight"),
    ("POST", re.compile(r"^/api/search/run$"), "search_run"),
    ("GET", re.compile(r"^/api/runs/(?P<run>[0-9a-f]{12})$"), "run"),
    # Delete (docs/plans/search-to-trip.md 4.2): POST, so the Origin gate and
    # the "Only GET and POST" rule stay as they are; the same trip group.
    ("POST", re.compile(rf"^/api/trips/{_TRIP}/delete-preflight$"), "trip_delete_preflight"),
    ("POST", re.compile(rf"^/api/trips/{_TRIP}/delete$"), "trip_delete"),
]


def route(engine, method: str, path: str, body: Optional[Dict[str, Any]]) -> Tuple[int, Any]:
    for m, pattern, name in ROUTES:
        match = pattern.match(path)
        if not match:
            continue
        if m != method:
            raise ApiError(405, "method_not_allowed", f"{path} does not accept {method}.")
        return 200, _HANDLERS[name](engine, body, **match.groupdict())
    raise ApiError(404, "not_found", "Not found.")


def _state(engine, body):
    return engine.state()


def _wallet(engine, body):
    return engine.set_wallet(body)


def _trips(engine, body):
    return engine.list_trips()


def _trip_detail(engine, body, trip):
    return engine.trip_detail(trip)


def _trip_draft(engine, body):
    return engine.trip_draft(body)


def _trip_create(engine, body):
    return engine.trip_create(body)


def _trip_preflight(engine, body, trip):
    return engine.trip_preflight(trip, body)


def _trip_run(engine, body, trip):
    return engine.trip_run(trip, body)


def _search_preflight(engine, body):
    return engine.search_preflight(body)


def _search_run(engine, body):
    return engine.search_run(body)


def _run(engine, body, run):
    return engine.stored_run(run)


def _trip_delete_preflight(engine, body, trip):
    return engine.trip_delete_preflight(trip, body)


def _trip_delete(engine, body, trip):
    return engine.trip_delete(trip, body)


_HANDLERS = {
    "state": _state,
    "wallet": _wallet,
    "trips": _trips,
    "trip_detail": _trip_detail,
    "trip_draft": _trip_draft,
    "trip_create": _trip_create,
    "trip_preflight": _trip_preflight,
    "trip_run": _trip_run,
    "search_preflight": _search_preflight,
    "search_run": _search_run,
    "run": _run,
    "trip_delete_preflight": _trip_delete_preflight,
    "trip_delete": _trip_delete,
}
