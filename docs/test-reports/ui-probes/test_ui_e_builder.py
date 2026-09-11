"""
E. THE TRIP BUILDER THROUGH THE UI. It writes files into a directory the test
suite reads, from a form anyone with the page open can post to. Names, numbers
and dates are attacked here; the run lock and the confirm are not involved,
because a create spends no calls - but it does spend disk.

RED = a defect that exists. GREEN = held up.
"""
import json
import threading

import pytest

from conftest import OFFLINE, g

LEG = {"origin": "SFO", "destination": "LHR", "date": "2027-01-15", "cash": "2400"}


def draft(ui, **over):
    body = {"name": "probe_trip", "cabin": "Y", "legs": [dict(LEG)]}
    legs = over.pop("legs", None)
    body.update(over)
    if legs is not None:
        body["legs"] = legs
    r = ui.post("/api/trips/draft", body)
    return body, r


def create(ui, **over):
    body, r = draft(ui, **over)
    if r.status != 200 or not r.json().get("ok"):
        return body, r, None
    body["draft_hash"] = r.json()["draft_hash"]
    return body, r, ui.post("/api/trips/create", body)


# --------------------------------------------------------------------- names


@pytest.mark.parametrize("name", [
    "../evil", "..", "a/b", "a\\b", "/etc/passwd", "trip_b_europe/../x", ".hidden",
    "-dash", "", "   ", "trip b", "tripé", "x" * 300, "trip\x00b", "a..b",
])
def test_E1_a_hostile_trip_name_writes_nothing_anywhere(ui, tmp_path, name):
    before = sorted(p.name for p in ui.trips_dir.glob("*"))
    body, r, created = create(ui, name=name)
    assert created is None or created.status >= 400, (name, created.status, created.text[:200])
    assert sorted(p.name for p in ui.trips_dir.glob("*")) == before
    # nothing outside the trips dir either
    assert not (tmp_path / "evil.json").exists()
    assert not (ui.trips_dir.parent / "evil.json").exists()


def test_E2_an_existing_name_is_refused_in_the_builders_own_words(ui):
    body, r, created = create(ui, name="trip_b_europe")
    assert created.status == 409
    assert "already exists and --force was not given" in created.json()["message"]
    # and the fixture on disk is untouched
    text = (ui.trips_dir / "trip_b_europe.json").read_text()
    assert "Trip B" in text or "trip_b" in text


def test_E3_a_name_ending_in_answer_is_written_but_then_invisible(ui):
    """`*_answer.json` is excluded from the listing. The builder does not know
    that, so a trip written under such a name vanishes from the app."""
    body, r, created = create(ui, name="probe_answer")
    assert created.status == 200, created.text
    written = ui.trips_dir / "probe_answer.json"
    assert written.is_file(), "the create reported success"
    ids = [t["id"] for t in ui.get("/api/trips").json()]
    assert "probe_answer" in ids, (
        "the file was written and the app reported success, but the trip is not "
        "in the list and cannot be opened or run")


def test_E4_the_written_file_is_where_the_app_says_it_is_and_loads(ui):
    body, r, created = create(ui, name="probe_trip")
    assert created.status == 200, created.text
    j = created.json()
    assert j["id"] == "probe_trip"
    assert (ui.trips_dir / "probe_trip.json").is_file()
    assert j["lines"][1] == "This fixture has NO points prices. Score it LIVE or REPLAY."
    detail = ui.get("/api/trips/probe_trip")
    assert detail.status == 200
    run = ui.run_trip("probe_trip", OFFLINE)[1].json()
    assert run["exit_code"] in (0, 3, 4), run.get("refusal")
    assert ui.net.calls == []


# -------------------------------------------------------------------- money


@pytest.mark.parametrize("cash", ["0", "0.0", "-1", "-0.01", "", "  ", "abc", "1e309",
                                  "nan", "NaN", "inf", "Infinity", "1,200", "$2400",
                                  "2400.", "2e1000"])
def test_E5_a_cash_price_that_is_not_a_price_is_refused_and_writes_nothing(ui, cash):
    before = sorted(p.name for p in ui.trips_dir.glob("*"))
    legs = [dict(LEG, cash=cash)]
    body, r, created = create(ui, legs=legs)
    ok = r.status == 200 and r.json().get("ok")
    if cash in ("2400.",):  # a trailing dot IS a float; documented, not a defect
        assert ok
        return
    assert not ok, (cash, r.text[:200])
    assert sorted(p.name for p in ui.trips_dir.glob("*")) == before


def test_E6_a_huge_but_finite_fare_survives_the_whole_round_trip_or_refuses_loudly(ui):
    """1e308 is finite, so the builder accepts it. Everything downstream must
    either carry it or refuse - never emit a NaN/inf into the page."""
    legs = [dict(LEG, cash="1e308")]
    body, r, created = create(ui, name="probe_huge", legs=legs)
    assert created.status == 200, created.text
    run = ui.run_trip("probe_huge", OFFLINE)
    assert run[1].status == 200, run[1].text[:400]
    payload = run[1].json()
    text = json.dumps(payload)
    assert "Infinity" not in text and "NaN" not in text
    assert payload["exit_code"] in (0, 3, 4)


# --------------------------------------------------------------------- dates


@pytest.mark.parametrize("date", ["2020-01-01", "2027-02-30", "15/01/2027",
                                  "2027-01-15T00:00", "today", "", "0000-01-01",
                                  "99999-01-01"])
def test_E7_a_date_that_is_not_a_future_iso_date_is_refused(ui, date):
    body, r, created = create(ui, legs=[dict(LEG, date=date)])
    assert not (r.status == 200 and r.json().get("ok")), (date, r.text[:200])


# ------------------------------------------------------------- per-leg cabin


def test_E8_per_leg_cabins_reach_the_file_and_the_description(ui):
    legs = [dict(LEG, cabin="J"), dict(LEG, origin="LHR", destination="SFO",
                                       date="2027-01-27", cash="1800", cabin="Y")]
    body, r, created = create(ui, name="probe_cabins", legs=legs, cabin="Y")
    assert created.status == 200, created.text
    fixture = json.loads((ui.trips_dir / "probe_cabins.json").read_text())
    cabins = [l.get("cabin") for l in fixture["legs"]]
    assert cabins == ["J", "Y"], cabins
    assert "cabins by leg" in fixture["description"], fixture["description"]


def test_E9_a_cli_built_fixture_is_byte_identical_after_the_per_leg_cabin_change(ui, tmp_path):
    """The CLI never sets a per-leg cabin, so its output must not have moved."""
    from src import trip_builder as tb

    out = tb.new_trip_from_flags(
        "probe_cli", ["SFO:LHR:2027-01-15:2400"], [], 1, "J", directory=tmp_path,
        today=g.PINNED_TODAY)
    fixture = json.loads(out.read_text())
    assert all("cabin" not in l or l["cabin"] == "J" for l in fixture["legs"])
    assert "cabins by leg" not in fixture["description"]
    assert fixture["legs"][0].get("cabin") in (None, "J")


# ------------------------------------------------------------------- drafts


def test_E10_a_create_without_a_matching_preview_is_refused(ui):
    body, r = draft(ui, name="probe_nodraft")
    assert r.json()["ok"]
    forged = dict(body, draft_hash="0" * 64)
    assert ui.post("/api/trips/create", forged).status == 409
    body["draft_hash"] = r.json()["draft_hash"]
    body["legs"][0]["cash"] = "999"          # changed after the preview
    assert ui.post("/api/trips/create", body).status == 409
    assert not (ui.trips_dir / "probe_nodraft.json").exists()


def test_E11_concurrent_creates_of_one_name_write_one_file_once(ui):
    """A double-click, or two tabs. `write_fixture` checks exists() and then
    writes; if the check and the write can interleave, one trip silently
    overwrites another and BOTH callers are told "Wrote ...". The window is
    small, so the race is run several times; one double success is the defect."""
    worst = 1
    for attempt in range(5):
        name = f"probe_race{attempt}"
        bodies = []
        for i in range(8):
            body, r = draft(ui, name=name, legs=[dict(LEG, cash=str(1000 + i))])
            body["draft_hash"] = r.json()["draft_hash"]
            bodies.append(body)
        barrier = threading.Barrier(len(bodies))
        out = []

        def fire(b):
            barrier.wait()
            out.append(ui.post("/api/trips/create", b))

        threads = [threading.Thread(target=fire, args=(b,)) for b in bodies]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        oks = [r for r in out if r.status == 200]
        worst = max(worst, len(oks))
    assert worst == 1, (
        f"{worst} creates reported success for ONE name in the same race: "
        f"trip_builder.write_fixture checks exists() and then writes, so one "
        f"trip silently overwrote another")


def test_E12_a_draft_never_touches_the_transport(ui):
    create(ui, name="probe_nospend")
    create(ui, name="probe_bad", legs=[dict(LEG, origin="SOF")])
    ui.post("/api/search/preflight", {"origin": "SOF", "destination": "MAD",
                                      "date": "2027-01-15"})
    assert ui.net.calls == []


def test_E13_the_leg_count_is_bounded_and_a_giant_form_is_refused(ui):
    legs = [dict(LEG) for _ in range(50)]
    body, r = draft(ui, name="probe_many", legs=legs)
    assert not r.json().get("ok")
    huge = {"name": "x" * 60000, "cabin": "Y", "legs": [dict(LEG)]}
    r = ui.post("/api/trips/draft", huge)
    assert r.status == 200 and not r.json()["ok"], (
        "a 60,000-character trip name previews as a valid trip: validate_name "
        "has no length limit, and the app cannot address what it then writes "
        "(the /api/trips/{id} route caps an id at 121 characters)")
    over = json.dumps({"name": "x" * 70000, "cabin": "Y", "legs": [dict(LEG)]}).encode()
    assert ui.request("POST", "/api/trips/draft", raw=over).status == 413
