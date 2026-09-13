"""
F. THE WALLET PANEL. His wallet file is his: the panel is session-only and must
never write. And the three states the whole project turns on - a number, blank,
absent - must mean the same thing at every layer, from the form to the argv to
the CLI's own banner.

The wallet is also the only place where free text the user types becomes part of
an argv. A card name that argparse reads as a FLAG would be a command-injection
in a tool that can spend money.

RED = a defect that exists. GREEN = held up.
"""
import json
import os

import pytest

from conftest import LIVE, OFFLINE, g, server, write_wallet

SEARCH = {"origin": "SFO", "destination": "MAD", "date": "2027-01-15"}
CSP = "Chase Sapphire Preferred"


def stat(path):
    s = os.stat(path)
    return (s.st_mtime_ns, s.st_size, open(path, "rb").read())


def test_F1_no_wallet_edit_or_run_ever_writes_the_wallet_file(ui):
    before = stat(ui.wallet_file)
    ui.post("/api/wallet", {"balances": {"UR": "1000"}, "cards": [CSP]})
    ui.run_trip("trip_b_europe", OFFLINE)
    ui.post("/api/wallet", {"balances": {"UR": ""}, "cards": []})
    ui.run_trip("trip_b_europe", LIVE)
    ui.search(SEARCH)
    ui.post("/api/wallet", {"balances": {"MR": "5"}, "cards": ["nonsense card"]})
    assert stat(ui.wallet_file) == before


def test_F2_absent_blank_and_zero_stay_three_different_things(ui):
    """The project's oldest rule: absent is not zero and zero is not
    unconstrained. Check it end to end: state, argv, and the CLI's own banner."""
    seen = {}
    for label, balances in (("number", {"UR": "160000"}), ("blank", {"UR": ""}),
                            ("zero", {"UR": "0"}), ("absent", {})):
        posted = ui.post("/api/wallet", {"balances": balances, "cards": [CSP]})
        w = posted.json()
        run = ui.run_trip("trip_b_europe", OFFLINE)[1].json()
        seen[label] = {
            "balances": w.get("balances"),
            "argv": w.get("argv"),
            "wallet_lines": run.get("context", {}).get("wallet_lines")
                            or run.get("context", {}).get("wallet_block"),
            "refusal": (run.get("refusal") or {}).get("message"),
            "exit": run["exit_code"],
            "error": w.get("error"),
            "message": w.get("message", ""),
        }
    assert seen["blank"]["balances"] == {"UR": None}, seen["blank"]
    assert seen["zero"]["balances"] == {"UR": 0}, seen["zero"]
    assert seen["number"]["balances"] == {"UR": 160000}
    # An emptied panel is REFUSED in the CLI's own words, and the file wallet
    # stays in force - it is not silently turned into "no wallet".
    assert seen["absent"]["error"] == "wallet", seen["absent"]
    assert "will NOT assume you hold" in seen["absent"]["message"]
    assert "--balance" in " ".join(seen["blank"]["argv"])
    assert seen["blank"]["wallet_lines"] != seen["zero"]["wallet_lines"]
    assert seen["zero"]["exit"] != seen["blank"]["exit"] or \
        seen["zero"]["wallet_lines"] != seen["blank"]["wallet_lines"]


@pytest.mark.parametrize("card", ["--offline", "--api-key", "-h", "--help", "--live",
                                  "--balance", "--trip-fixture", "--force",
                                  "--wallet", "--fx", "--html"])
def test_F3_a_card_name_that_looks_like_a_flag_never_becomes_one(ui, card):
    """`--card <value>` with a flag-shaped value. Nothing may change mode, write
    a file, read a key, or print the CLI's help into the page."""
    r = ui.post("/api/wallet", {"balances": {"UR": "160000"}, "cards": [card]})
    assert r.status == 200, r.text
    run = ui.post("/api/trips/trip_b_europe/run", OFFLINE)
    assert run.status in (200, 400), (card, run.status, run.text[:200])
    text = run.text
    assert "usage: python -m src.main" not in text, "argparse HELP reached the page"
    assert g.FAKE_KEY not in text
    if run.status == 200:
        j = run.json()
        assert j["mode"] == "offline"
        assert "--offline" in j["argv_display"]


def test_F4_a_currency_or_valuation_that_looks_like_a_flag_is_refused_not_run(ui):
    for balances in ({"--api-key": "1"}, {"-h": "1"}, {"UR --live": "1"}):
        r = ui.post("/api/wallet", {"balances": balances, "cards": []})
        assert r.status == 200, r.text
        run = ui.post("/api/trips/trip_b_europe/run", OFFLINE)
        assert run.status in (200, 400), (balances, run.status)
        assert "usage: python -m src.main" not in run.text
        if run.status == 200:
            assert run.json()["mode"] == "offline"


def test_F5_the_session_wallet_is_labelled_as_not_saved_and_the_file_one_is_not(ui):
    st = ui.get("/api/state").json()["wallet"]
    assert st["source"] == str(ui.wallet_file)
    ui.post("/api/wallet", {"balances": {"UR": "1000"}, "cards": [CSP]})
    st = ui.get("/api/state").json()["wallet"]
    assert st["source"] == "entered in this session (not saved)"
    # Setting it back to exactly the file's contents returns to the file label.
    ui.post("/api/wallet", {"balances": {"UR": "160000"}, "cards": [CSP]})
    st = ui.get("/api/state").json()["wallet"]
    assert st["source"] == str(ui.wallet_file), st["source"]


def test_F6_a_wallet_error_is_the_cli_text_and_blocks_the_run_the_cli_way(ui):
    r = ui.post("/api/wallet", {"balances": {"UR": "not a number"}, "cards": []})
    j = r.json()
    assert j.get("error") == "wallet" and "not a number" in j["message"].lower() or \
        "UR" in j["message"], j
    # The wallet was NOT adopted, so the run still uses the file.
    run = ui.run_trip("trip_b_europe", OFFLINE)[1].json()
    assert run["exit_code"] == 0


def test_F7_an_edited_wallet_changes_the_answer_and_the_argv_together(ui):
    ui.post("/api/wallet", {"balances": {"UR": "30000"}, "cards": [CSP]})
    run = ui.run_trip("trip_b_europe", OFFLINE)[1].json()
    assert "--balance" in run["argv_display"] and "UR=30000" in run["argv_display"]
    assert "--wallet" not in run["argv_display"]
    joined = " ".join(l for l in run["context"]["wallet_block"])
    assert "30,000" in joined


def test_F8_the_wallet_file_is_read_fresh_and_a_stale_copy_is_not_served(ui):
    write_wallet(ui.wallet_file, balances={"UR": 42000}, cards=[CSP])
    st = ui.get("/api/state").json()["wallet"]
    assert st["balances"] == {"UR": 42000}, st["balances"]
    run = ui.run_trip("trip_b_europe", OFFLINE)[1].json()
    assert "42,000" in " ".join(run["context"]["wallet_block"])


def test_F9_a_deleted_wallet_file_is_a_refusal_not_a_default_wallet(ui):
    os.unlink(ui.wallet_file)
    st = ui.get("/api/state").json()
    run = ui.post("/api/trips/trip_b_europe/run", OFFLINE)
    assert run.status in (200, 500), run.status
    if run.status == 200:
        j = run.json()
        assert j["exit_code"] == 2 or j.get("refusal"), j.get("headline")
    assert "160,000" not in run.text, "a wallet that is gone was answered from memory"
