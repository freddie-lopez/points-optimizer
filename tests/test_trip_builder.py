"""
v5 Step 4: `--new-trip`, and the three things it refuses.

Every validation here is asserted twice: the refusal happens, AND the fixtures
directory is unchanged afterwards. A refusal that leaves a partial file behind
is a refusal that did not happen.
"""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

from src import trip_builder
from src.main import main
from src.trip_builder import TripBuilderError
from src.trip_loader import load_trip_fixture

TODAY = date(2026, 9, 9)
FUTURE = "2027-01-15"


@pytest.fixture
def out(tmp_path):
    d = tmp_path / "trips"
    d.mkdir()
    return d


def build(out, *, name="probe", legs=(), hotels=(), travelers=1, cabin="Y",
          force=False):
    return trip_builder.new_trip_from_flags(
        name, list(legs), list(hotels), travelers, cabin,
        directory=out, force=force, today=TODAY,
    )


def listing(d: Path):
    return sorted(p.name for p in d.iterdir())


def refuses(out, **kw):
    """A refusal that writes nothing. Both halves are the assertion."""
    before = listing(out)
    with pytest.raises(TripBuilderError) as excinfo:
        build(out, **kw)
    assert listing(out) == before, "a refusal left a file behind"
    return str(excinfo.value)


# ---------------------------------------------------------------------------
# REFUSAL 1: no points_candidates key, ever
# ---------------------------------------------------------------------------


def test_the_raw_json_contains_no_points_candidates_key(out):
    path = build(out, legs=[f"SFO:MAD:{FUTURE}:395", f"LHR:SFO:2027-01-27:610"])
    raw = path.read_text()
    # The QUOTED form: `points_candidates` appears once as English prose in the
    # trip_level_flags sentence, which is the point of that sentence. What must
    # not appear anywhere is the JSON KEY.
    assert '"points_candidates"' not in raw, (
        "asserted on the raw TEXT, not the loaded object: an empty list is a "
        "thing a future editor fills in, and the key must be absent"
    )
    for leg in json.loads(raw)["legs"]:
        assert "points_candidates" not in leg


def test_the_fixture_says_in_itself_why_it_has_no_points_prices(out):
    path = build(out, legs=[f"SFO:MAD:{FUTURE}:395"])
    flags = json.loads(path.read_text())["trip_level_flags"]
    assert flags, "the refusal must survive being read by a human"
    text = " ".join(flags)
    assert "NO POINTS PRICES" in text
    assert "--live" in text and "--from-snapshot" in text


def test_loaded_back_every_flight_leg_has_no_points_candidates(out):
    path = build(out, legs=[f"SFO:MAD:{FUTURE}:395"])
    fixture = load_trip_fixture(path)
    for leg in fixture.legs:
        assert leg.points_candidates == []


def test_scored_offline_it_reports_no_points_path_not_a_badge_number(out, capsys):
    """
    THE ROUND TRIP. Build a two-leg trip, score it, and assert the output
    contains no points price - because there is none to invent.
    """
    from src.optimizer import evaluate_trip, trip_totals
    from src.main import load_ratio_manager
    from src.surcharge import default_table
    from src.wallet import wallet_from_flags

    path = build(out, legs=[f"SFO:MAD:{FUTURE}:395", "LHR:SFO:2027-01-27:610"])
    fixture = load_trip_fixture(path)
    wallet = wallet_from_flags(["UR=160000"], ["Chase Sapphire Preferred"], None)
    results = evaluate_trip(
        legs=fixture.legs,
        ratios_manager=load_ratio_manager(),
        wallet=wallet,
        transfer_date=date(2026, 9, 15),
        surcharges=default_table(),
    )
    assert len(results) == 2
    for r in results:
        assert r.has_points_path is False
        assert r.points_required == 0
        assert "cash" in r.verdict
    totals = trip_totals(results, wallet=wallet)
    assert totals["points_spent"] == 0
    assert totals["all_cash_usd"] == pytest.approx(1005.0)


# ---------------------------------------------------------------------------
# REFUSAL 2: the description is generated from the codes
# ---------------------------------------------------------------------------


def test_the_description_is_derived_from_the_codes(out):
    path = build(out, legs=[f"SFO:MAD:{FUTURE}:395"])
    fixture = load_trip_fixture(path)
    leg = fixture.legs[0]
    assert leg.origin == "SFO" and leg.destination == "MAD"
    assert leg.description.startswith("SFO->MAD")
    assert "Jan 15 2027" in leg.description
    assert "economy" in leg.description
    assert "1 adult" in leg.description


def test_the_codes_in_the_description_always_equal_the_fields(out):
    """
    Trip B's B1 says MRY->MAD and is coded SFO->MAD. That drift survived four
    versions. Here only the codes are input, so it cannot be expressed.
    """
    path = build(
        out,
        legs=[f"SFO:MAD:{FUTURE}:395", "MAD:AMS:2027-01-19:120",
              "AMS:LHR:2027-01-23:95", "LHR:SFO:2027-01-27:610"],
    )
    for leg in load_trip_fixture(path).legs:
        if leg.kind != "flight":
            continue
        assert leg.description.split(",")[0] == f"{leg.origin}->{leg.destination}"


def test_the_cabin_and_traveller_count_reach_the_description(out):
    path = build(out, legs=[f"SFO:MAD:{FUTURE}:395"], travelers=2, cabin="J")
    desc = load_trip_fixture(path).legs[0].description
    assert "business" in desc and "2 adults" in desc


# ---------------------------------------------------------------------------
# REFUSAL 3: unknown IATA codes
# ---------------------------------------------------------------------------


def test_an_unknown_iata_is_refused_and_no_correction_offered(out):
    message = refuses(out, legs=[f"XXX:MAD:{FUTURE}:395"])
    assert "--leg ORIGIN" in message
    assert "XXX" in message
    for word in ("did you mean", "Did you mean", "perhaps", "closest"):
        assert word not in message


def test_lowercase_codes_are_accepted_and_upper_cased(out):
    path = build(out, legs=[f"sfo:mad:{FUTURE}:395"])
    leg = load_trip_fixture(path).legs[0]
    assert leg.origin == "SFO" and leg.destination == "MAD"


@pytest.mark.parametrize("code", ["SF", "SFOO", "S", ""])
def test_a_code_that_is_not_three_characters_is_refused(out, code):
    message = refuses(out, legs=[f"{code}:MAD:{FUTURE}:395"])
    assert "3-letter" in message


def test_a_leg_from_an_airport_to_itself_is_refused(out):
    assert "goes nowhere" in refuses(out, legs=[f"SFO:SFO:{FUTURE}:395"])


# ---------------------------------------------------------------------------
# Every other refusal, each leaving the directory unchanged
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "leg",
    [
        "SFO:MAD:15-01-2027:395",     # not ISO
        "SFO:MAD:2027-02-30:395",     # a day that does not exist
        "SFO:MAD:2020-01-15:395",     # in the past
    ],
)
def test_bad_dates_are_refused(out, leg):
    refuses(out, legs=[leg])


@pytest.mark.parametrize("cash", ["-1", "0", "abc", "", "nan", "inf"])
def test_bad_cash_amounts_are_refused(out, cash):
    refuses(out, legs=[f"SFO:MAD:{FUTURE}:{cash}"])


def test_a_large_but_finite_cash_amount_is_accepted(out):
    """1e9 is absurd, not malformed. The builder refuses malformed, not absurd."""
    path = build(out, legs=[f"SFO:MAD:{FUTURE}:1e9"])
    assert load_trip_fixture(path).legs[0].cash_options[0].amount == 1e9


def test_a_fractional_cent_is_kept_not_rounded(out):
    path = build(out, legs=[f"SFO:MAD:{FUTURE}:395.999"])
    assert load_trip_fixture(path).legs[0].cash_options[0].amount == 395.999


@pytest.mark.parametrize("n", ["0", "-1", "abc", "1.5"])
def test_bad_traveller_counts_are_refused(out, n):
    refuses(out, legs=[f"SFO:MAD:{FUTURE}:395"], travelers=n)


@pytest.mark.parametrize("cabin", ["X", "", "economy", "YY"])
def test_a_bad_cabin_is_refused(out, cabin):
    refuses(out, legs=[f"SFO:MAD:{FUTURE}:395"], cabin=cabin)


def test_lowercase_cabin_is_accepted(out):
    path = build(out, legs=[f"SFO:MAD:{FUTURE}:395"], cabin="j")
    assert json.loads(path.read_text())["legs"][0]["cabin"] == "J"


@pytest.mark.parametrize(
    "leg", ["SFO:MAD:2027-01-15", "SFO:MAD:2027-01-15:395:extra", "", "SFO"]
)
def test_a_leg_with_the_wrong_number_of_fields_is_refused(out, leg):
    assert "colon-separated fields" in refuses(out, legs=[leg])


def test_zero_legs_and_zero_hotels_is_refused(out):
    assert "at least one" in refuses(out)


def test_an_existing_file_is_not_overwritten_without_force(out):
    build(out, legs=[f"SFO:MAD:{FUTURE}:395"])
    before = (out / "probe.json").read_text()
    message = refuses(out, legs=["MAD:AMS:2027-01-19:120"])
    assert str(out / "probe.json") in message
    assert "--force" in message
    assert (out / "probe.json").read_text() == before


def test_force_overwrites(out):
    build(out, legs=[f"SFO:MAD:{FUTURE}:395"])
    build(out, legs=["MAD:AMS:2027-01-19:120"], force=True)
    assert load_trip_fixture(out / "probe.json").legs[0].origin == "MAD"


@pytest.mark.parametrize(
    "name", ["../escape", "sub/dir", "..", ".", "a/../b", "\\windows"]
)
def test_a_name_that_could_write_outside_the_fixtures_directory_is_refused(out, name):
    before = listing(out)
    with pytest.raises(TripBuilderError):
        build(out, name=name, legs=[f"SFO:MAD:{FUTURE}:395"])
    assert listing(out) == before
    assert not (out.parent / "escape.json").exists()


# ---------------------------------------------------------------------------
# Hotels
# ---------------------------------------------------------------------------


def test_a_hotel_leg_has_nights_and_no_airport_codes(out):
    path = build(out, hotels=[f"Novotel Madrid:{FUTURE}:4:747.81"])
    raw = json.loads(path.read_text())["legs"][0]
    assert raw["kind"] == "hotel"
    assert raw["nights"] == 4
    assert "origin" not in raw and "destination" not in raw, (
        "Seats.aero is flights-only; a hotel leg carrying codes would be queried"
    )
    leg = load_trip_fixture(path).legs[0]
    assert leg.origin == "" and leg.destination == ""


def test_a_hotel_name_may_contain_a_colon(out):
    path = build(out, hotels=[f"Hotel Kyoto: Gion:{FUTURE}:2:300"])
    assert "Hotel Kyoto: Gion" in load_trip_fixture(path).legs[0].description


@pytest.mark.parametrize("nights", ["0", "-2", "abc"])
def test_a_hotel_with_a_bad_night_count_is_refused(out, nights):
    refuses(out, hotels=[f"Novotel:{FUTURE}:{nights}:300"])


def test_a_nameless_hotel_is_refused(out):
    refuses(out, hotels=[f":{FUTURE}:2:300"])


def test_flights_and_hotels_together(out):
    path = build(
        out,
        legs=[f"SFO:MAD:{FUTURE}:395"],
        hotels=[f"Novotel Madrid:{FUTURE}:4:747.81"],
    )
    fixture = load_trip_fixture(path)
    assert [leg.id for leg in fixture.legs] == ["L1", "H1"]
    assert [leg.kind for leg in fixture.legs] == ["flight", "hotel"]


# ---------------------------------------------------------------------------
# The cash option's provenance
# ---------------------------------------------------------------------------


def test_the_cash_option_records_that_a_user_typed_it(out):
    path = build(out, legs=[f"SFO:MAD:{FUTURE}:395"])
    option = json.loads(path.read_text())["legs"][0]["cash_options"][0]
    assert option["source"] == "user_entered_via_new_trip"
    assert option["currency"] == "USD"
    assert option["date"] == FUTURE
    assert option["captured_on"] == str(TODAY)
    loaded = load_trip_fixture(path).legs[0]
    assert loaded.cash_provenance == "user_entered_via_new_trip"


def test_no_surcharge_is_written_at_all(out):
    """
    `cash_surcharge: 0.0` with no flag is silence, not a real zero. The builder
    writes neither the amount nor a currency rather than writing a bare zero.
    """
    raw = build(out, legs=[f"SFO:MAD:{FUTURE}:395"]).read_text()
    assert "cash_surcharge" not in raw
    assert "surcharge_currency" not in raw


# ---------------------------------------------------------------------------
# Through the CLI
# ---------------------------------------------------------------------------


def run_cli(argv, capsys):
    old = sys.argv
    sys.argv = ["prog"] + argv
    try:
        code = main()
    finally:
        sys.argv = old
    return code, capsys.readouterr().out


def test_cli_writes_the_fixture_and_echoes_the_cash_first(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(trip_builder, "FIXTURE_DIR", tmp_path)
    code, output = run_cli(
        ["--new-trip", "apd_probe", "--leg", f"LHR:SFO:{FUTURE}:500"], capsys
    )
    assert code == 0
    assert "About to write apd_probe.json" in output
    assert "$500.00" in output
    assert "LHR->SFO" in output
    assert "points_candidates: NONE ON ANY LEG" in output
    assert (tmp_path / "apd_probe.json").exists()


def test_cli_refuses_an_unknown_code_with_exit_1_and_writes_nothing(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setattr(trip_builder, "FIXTURE_DIR", tmp_path)
    code, output = run_cli(
        ["--new-trip", "bad", "--leg", f"XXX:MAD:{FUTURE}:395"], capsys
    )
    assert code == 1
    assert "XXX" in output
    assert list(tmp_path.iterdir()) == []


# ===========================================================================
# v5 Step 5: interactive mode, and chaining into --live
# ===========================================================================


class Script:
    """A scripted stdin. Raises rather than blocking if the script runs out."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.asked = []

    def __call__(self, question):
        self.asked.append(question)
        if not self.answers:
            raise AssertionError(f"the session asked more than scripted: {question!r}")
        return self.answers.pop(0)


SESSION = [
    "probe",                    # name
    "1",                        # travelers
    "Y",                        # cabin
    f"SFO:MAD:{FUTURE}:395",    # leg 1
    "MAD:AMS:2027-01-19:120",   # leg 2
    "",                         # no more legs
    f"Novotel Madrid:{FUTURE}:4:747.81",
    "",                         # no more hotels
]


def _interactive(out, answers, force=False):
    script = Script(answers)
    name, flights, hotels, travelers, cabin = trip_builder.interactive_session(
        read=script, write=lambda _l: None, today=TODAY
    )
    fixture = trip_builder.build_fixture(
        name, flights, hotels, travelers, cabin, today=TODAY
    )
    return trip_builder.write_fixture(fixture, out, force=force), script


def test_a_scripted_session_produces_the_flags_file_byte_for_byte(tmp_path):
    """
    The two paths call ONE set of validators and ONE build_fixture, so their
    outputs are the same bytes. Asserted by comparing the files.
    """
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()

    from_flags = build(
        a,
        legs=[f"SFO:MAD:{FUTURE}:395", "MAD:AMS:2027-01-19:120"],
        hotels=[f"Novotel Madrid:{FUTURE}:4:747.81"],
    )
    from_prompts, _ = _interactive(b, SESSION)

    assert from_prompts.read_text() == from_flags.read_text()


def test_a_bad_answer_re_prompts_rather_than_aborting(tmp_path):
    out = tmp_path / "trips"
    out.mkdir()
    answers = ["probe", "0", "1", "Y", f"SFO:MAD:{FUTURE}:395", "", ""]
    path, script = _interactive(out, answers)
    assert path.exists()
    assert script.answers == [], "every scripted answer should have been consumed"


def test_a_bad_leg_re_prompts_and_keeps_the_earlier_legs(tmp_path):
    out = tmp_path / "trips"
    out.mkdir()
    answers = [
        "probe", "1", "Y",
        f"SFO:MAD:{FUTURE}:395",
        "XXX:MAD:2027-01-19:120",     # refused
        "MAD:AMS:2027-01-19:120",     # accepted on the retry
        "", "",
    ]
    path, _ = _interactive(out, answers)
    fixture = load_trip_fixture(path)
    assert [leg.origin for leg in fixture.legs] == ["SFO", "MAD"], (
        "a typo in leg two must not throw away leg one"
    )


def test_three_bad_answers_in_a_row_abort_rather_than_looping(tmp_path):
    out = tmp_path / "trips"
    out.mkdir()
    with pytest.raises(TripBuilderError) as excinfo:
        _interactive(out, ["probe", "0", "-1", "abc", "Y"])
    assert "invalid answers in a row" in str(excinfo.value)
    assert list(out.iterdir()) == [], "nothing is written on an abort"


def test_the_prompt_seam_never_loops_forever():
    """A prompt that cannot be satisfied and will not stop is worse than one
    that quits and says why."""
    calls = []

    def always_bad(_q):
        calls.append(1)
        return "not a cabin"

    with pytest.raises(TripBuilderError):
        trip_builder._prompt(
            "Cabin", trip_builder.validate_cabin, always_bad, lambda _l: None
        )
    assert len(calls) == trip_builder.MAX_PROMPT_ATTEMPTS


def test_blank_ends_a_section_and_is_never_validated_into_a_leg(tmp_path):
    out = tmp_path / "trips"
    out.mkdir()
    path, _ = _interactive(out, ["probe", "1", "Y", "", f"Hotel A:{FUTURE}:2:200", ""])
    fixture = load_trip_fixture(path)
    assert [leg.kind for leg in fixture.legs] == ["hotel"]


def test_new_trip_with_from_snapshot_is_refused(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(trip_builder, "FIXTURE_DIR", tmp_path)
    manifest = tmp_path / "MANIFEST.md"
    manifest.write_text("| a | b | c | d | e | f | g | h |\n")
    code, output = run_cli(
        [
            "--new-trip", "probe",
            "--leg", f"SFO:MAD:{FUTURE}:395",
            "--from-snapshot", str(manifest),
        ],
        capsys,
    )
    assert code == 1
    assert "no snapshots" in output
    assert list(tmp_path.glob("*.json")) == []


def test_new_trip_then_live_writes_then_scores_through_the_ordinary_path(
    tmp_path, capsys, monkeypatch
):
    """
    The scoring half is the ORDINARY live path with no special-casing: the
    fixture that was just written is handed to run_fixture by filename.
    Asserted by the live banner being present, and by the run failing the way
    any live run with no network fails rather than in some new way.
    """
    monkeypatch.setattr(trip_builder, "FIXTURE_DIR", tmp_path)
    monkeypatch.setenv("SEATS_AERO_KEY", "test_key_not_a_real_one")

    code, output = run_cli(
        [
            "--new-trip", "chained",
            "--leg", f"SFO:MAD:{FUTURE}:395",
            "--live",
            "--balance", "UR=160000",
            "--card", "Chase Sapphire Preferred",
            "--transfer-date", "2026-09-15",
        ],
        capsys,
    )
    assert (tmp_path / "chained.json").exists()
    assert "Wrote " in output
    assert "Seats.aero key:" in output, "the ordinary live key banner"
    # No network in this sandbox, so every leg is an API failure - which is the
    # ordinary live-path outcome, not a --new-trip-specific one.
    assert "THIS IS AN API FAILURE" in output
    assert code in (0, 3)
