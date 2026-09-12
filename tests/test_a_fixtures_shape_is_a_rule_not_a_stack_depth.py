"""
MAC-2, THE PROPERTY: what makes a fixture too deeply nested to load is a RULE
written down in the loader, not whatever the interpreter happens to do with its
stack.

Found by Tsuki running the suite on his Mac. R5-4 closed "a 400-level-deep JSON
document is a refusal, not a traceback" by catching the `RecursionError`
`json.loads` raises walking it. In this sandbox that fires and the file is
refused (exit 1, CANNOT LOAD). On his macOS Python the scanner gets further, the
file LOADS, the run exits 0 with no `load_error` - and
`test_a_malformed_file_is_one_line_not_a_traceback[deeply_nested]` failed. The
boundary was deciding by platform, which is the class of problem the refusal
boundary exists to end.

So `trip_loader` now states the shape a trip fixture may have -
`MAX_NESTING_DEPTH`, `MAX_FILE_BYTES` - and checks it by SCANNING the text
before anything parses it. The scan uses no stack of its own, so its answer is a
fact about the file. The `RecursionError` handlers stay as a backstop and raise
the SAME sentence, so a reader is never told which interpreter ran out first.

The tests below assert exactly that:

* a file deeper than the rule is refused EVEN ON AN INTERPRETER THAT CAN READ IT
  (a recursion limit high enough to parse it changes nothing);
* the rule's own boundary: MAX levels load, MAX+1 does not;
* the same one line and exit 1 from the CLI and the same CANNOT LOAD row from
  the UI, with identical wording;
* and the limits do not refuse anything real - every committed fixture, and a
  trip far larger than any that exists, still loads.
"""
import json
import sys

import pytest

from src import trip_loader
from src.trip_loader import (
    MAX_FILE_BYTES,
    MAX_NESTING_DEPTH,
    TripFixtureError,
    json_nesting_depth,
    load_trip_fixture,
)
from tests._ui_harness import running_server
from tests.test_no_external_number_can_crash_a_run import ROOT, TRIPS, run_cli

# Deeper than the rule, and shallow enough that any CPython reads it without
# coming near its stack. THAT is the case a47dcb6 gets wrong: it loads here and
# it is refused there, for no reason written down anywhere.
READABLE_BUT_TOO_DEEP = 100


def nested(levels: int, trip_id: str = "deep") -> dict:
    """A fixture whose `notes` nest `levels` deep in total."""
    doc = {"id": trip_id, "legs": []}
    node = doc
    # The document itself is level 1; each round adds a list and two objects.
    for _ in range(max(levels - 1, 0) // 3):
        node["notes"] = [{"deeper": {}}]
        node = node["notes"][0]["deeper"]
    return doc


def dumps(doc) -> str:
    """`json.dumps` recurses too - the test's own writer must not be the thing
    that decides what can be written."""
    was = sys.getrecursionlimit()
    sys.setrecursionlimit(20000)
    try:
        return json.dumps(doc)
    finally:
        sys.setrecursionlimit(was)


def write(tmp_path, doc, name="probe"):
    path = tmp_path / f"{name}.json"
    path.write_text(dumps(doc))
    return path


def walked_depth(node) -> int:
    """The depth by WALKING the parsed object: an independent second opinion,
    so the scan is not simply asserted against itself."""
    if isinstance(node, dict):
        return 1 + max([walked_depth(v) for v in node.values()] or [0])
    if isinstance(node, list):
        return 1 + max([walked_depth(v) for v in node] or [0])
    return 0


# ------------------------------------------------------- the scan is a fact


@pytest.mark.parametrize("levels", [1, 4, 7, 31, 100])
def test_the_scan_agrees_with_walking_the_parsed_document(levels):
    doc = nested(levels)
    assert json_nesting_depth(dumps(doc)) == walked_depth(doc), levels


def test_the_scan_does_not_need_a_stack_at_all():
    """5,000 levels - far past any recursion limit - measured, not raised."""
    text = "[" * 5000 + "]" * 5000
    assert json_nesting_depth(text) == 5000


def test_a_bracket_inside_a_string_is_not_nesting():
    assert json_nesting_depth('{"note": "[[[[{{{{", "b": [1]}') == 2
    assert json_nesting_depth('{"note": "a \\" [[[", "b": {}}') == 2


# ------------------------------------------------ the rule, not the platform


def test_a_file_the_interpreter_can_read_is_still_refused_by_the_rule(tmp_path):
    """
    The macOS case, made local: the recursion limit is raised high enough that
    `json.loads` reads the file comfortably - and the answer does not change.
    """
    path = write(tmp_path, nested(READABLE_BUT_TOO_DEEP))
    assert json.loads(path.read_text())  # the interpreter CAN read it
    with pytest.raises(TripFixtureError) as refused:
        load_trip_fixture(path)
    assert "may be at most 32" in str(refused.value)


@pytest.mark.parametrize("limit", [1000, 3000, 20000])
def test_the_refusal_is_the_same_at_every_recursion_limit(tmp_path, limit):
    path = write(tmp_path, nested(READABLE_BUT_TOO_DEEP))
    was = sys.getrecursionlimit()
    sys.setrecursionlimit(limit)
    try:
        with pytest.raises(TripFixtureError) as refused:
            load_trip_fixture(path)
    finally:
        sys.setrecursionlimit(was)
    assert str(refused.value) == (
        f"probe.json is nested {json_nesting_depth(path.read_text())} levels "
        f"deep, and a trip fixture may be at most {MAX_NESTING_DEPTH}. It is "
        f"not a trip fixture."
    )


def test_the_rules_own_boundary(tmp_path):
    """At the limit it loads; one level past it, it does not. Nothing about
    this sentence mentions an interpreter."""
    inside = write(tmp_path, nested(MAX_NESTING_DEPTH), "inside")
    assert json_nesting_depth(inside.read_text()) <= MAX_NESTING_DEPTH
    assert load_trip_fixture(inside).id == "deep"

    outside = write(tmp_path, nested(MAX_NESTING_DEPTH + 3), "outside")
    assert json_nesting_depth(outside.read_text()) > MAX_NESTING_DEPTH
    with pytest.raises(TripFixtureError, match="may be at most"):
        load_trip_fixture(outside)


def test_the_400_deep_document_r5_4_found_is_refused_by_the_rule(tmp_path):
    """The original R5-4 shape, now refused for a reason rather than by
    accident - and the RecursionError backstop is not what did it."""
    path = write(tmp_path, nested(1201))
    with pytest.raises(TripFixtureError) as refused:
        load_trip_fixture(path)
    assert refused.value.__cause__ is None
    assert "1201 levels deep" in str(refused.value)


# --------------------------------------------------------------- the size rule


def test_a_fixture_larger_than_the_rule_is_refused_before_it_is_parsed(tmp_path):
    doc = json.loads((TRIPS / "trip_a_mry_nyc.json").read_text())
    doc["description"] = "x" * (MAX_FILE_BYTES + 1)
    path = write(tmp_path, doc, "huge")
    assert path.stat().st_size > MAX_FILE_BYTES
    with pytest.raises(TripFixtureError, match="may be at most"):
        load_trip_fixture(path)


def test_a_trip_far_larger_than_any_real_one_still_loads(tmp_path):
    """The limits are a bound, not a ceiling somebody will meet: 400 legs, each
    with its options, is nowhere near either rule."""
    doc = json.loads((TRIPS / "trip_b_europe.json").read_text())
    template = doc["legs"][0]
    doc["legs"] = [dict(template, id=f"L{i}") for i in range(400)]
    path = write(tmp_path, doc, "big")
    assert path.stat().st_size < MAX_FILE_BYTES
    assert len(load_trip_fixture(path).legs) == 400


@pytest.mark.parametrize("name", sorted(p.name for p in TRIPS.glob("*.json")))
def test_no_committed_fixture_is_anywhere_near_either_limit(name):
    """A limit that would refuse real data is not a limit, it is a bug."""
    path = TRIPS / name
    assert path.stat().st_size < MAX_FILE_BYTES // 10, path
    assert json_nesting_depth(path.read_text()) <= MAX_NESTING_DEPTH // 4, path


# ------------------------------------------- one line from the CLI, one row in
# the UI, the same words


def test_the_cli_refusal_is_one_line_and_exit_1(tmp_path):
    code, text = run_cli(write(tmp_path, nested(READABLE_BUT_TOO_DEEP)))
    assert code == 1, text[-300:]
    assert "Traceback" not in text
    assert "may be at most 32. It is not a trip fixture." in text
    # Not the interpreter's accident: the backstop's wording is absent.
    assert "RecursionError" not in text


def test_the_ui_says_cannot_load_with_the_same_sentence(tmp_path):
    trips = tmp_path / "trips"
    trips.mkdir()
    write(trips, nested(READABLE_BUT_TOO_DEEP), "probe")
    with running_server(trips_dir=trips) as c:
        listing = c.get("/api/trips")
        assert listing.status == 200, listing.text[:200]
        (row,) = listing.json()
        assert row["load_error"], row
        assert "may be at most 32. It is not a trip fixture." in row["load_error"]
        assert "Traceback" not in row["load_error"]
        assert c.get("/api/trips/probe").status == 422
        run = c.post("/api/trips/probe/run", {"mode": "offline", "options": {}})
    assert run.status == 200 and run.json()["exit_code"] == 1


# ------------------------------------------------------ the limits are stated


def test_the_limits_are_named_constants_a_reader_can_find():
    """The whole point of MAC-2 is that the rule is written down. If these stop
    being module constants, it is back to being an accident."""
    assert isinstance(MAX_NESTING_DEPTH, int) and MAX_NESTING_DEPTH >= 16
    assert isinstance(MAX_FILE_BYTES, int) and MAX_FILE_BYTES >= 1024 * 1024
    source = (ROOT / "src" / "trip_loader.py").read_text()
    assert "MAX_NESTING_DEPTH = " in source and "MAX_FILE_BYTES = " in source
    # And the reasoning for each is beside it, not in a commit message.
    assert "NUMBER OF LEGS" in source and "deliberately NOT limited" in source
    assert trip_loader.json_nesting_depth.__doc__
