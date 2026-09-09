"""
Step 2: the runtime wallet.

The thing under test is mostly an ABSENCE: nothing about which cards anyone holds
or what their balances are may be compiled into the tool.
"""
import json
from pathlib import Path

import pytest

from src.ratio_manager import RatioManager
from src.wallet import Wallet, WalletError, load_wallet, validate_wallet, wallet_from_flags

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
WALLETS = Path(__file__).parent / "fixtures" / "wallets"
CSP = "Chase Sapphire Preferred"
CSR = "Chase Sapphire Reserve"


@pytest.fixture
def rm():
    return RatioManager(DATA / "ratios.csv", DATA / "bonuses.csv", DATA / "programs.yaml")


@pytest.fixture
def known(rm):
    return set(rm.issuer_currencies()), rm.cards_affecting_ratios()


# ---------------------------------------------------------------------------
# Nothing is hardcoded
# ---------------------------------------------------------------------------


CARD_NAMES = (
    "Chase Sapphire Reserve",
    "Chase Sapphire Preferred",
    "Chase Ink Business Preferred",
)


def _executable_strings(py_path: Path):
    """
    Every string literal in a module that is NOT a docstring.

    Comments never reach the AST, so prose explaining why a default was removed
    is correctly ignored while an actual `cards = ["Chase ..."]` is not.
    """
    import ast

    tree = ast.parse(py_path.read_text())
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                if isinstance(body[0].value.value, str):
                    docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_no_card_name_appears_in_executable_code():
    """
    Step 2's acceptance grep, as a test.

    A card name in executable code is a claim about what someone holds. The only
    legitimate places for one are ratios.csv (where a card scopes a RATIO, not a
    person) and the obviously-fake wallet template. Comments and docstrings that
    EXPLAIN the removed default are documentation, not assumptions, and are fine.
    """
    offenders = []
    for path in (ROOT / "src").rglob("*.py"):
        for literal in _executable_strings(path):
            for card in CARD_NAMES:
                if card in literal:
                    offenders.append(f"{path.name}: {literal[:60]!r}")
    assert offenders == [], (
        "card names found in executable code: " + "; ".join(offenders)
    )


def test_no_card_name_in_any_data_file_except_the_ratio_table():
    offenders = []
    for path in DATA.glob("*"):
        if path.is_dir() or path.name in ("ratios.csv", "wallet.example.json"):
            continue
        for lineno, line in enumerate(path.read_text(errors="ignore").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue  # a comment documenting the DATA, not a holdings claim
            for card in CARD_NAMES:
                if card in line:
                    offenders.append(f"{path.name}:{lineno}")
    assert offenders == [], "card names in data files: " + "; ".join(offenders)


def test_main_has_no_default_wallet():
    """
    v0's `DEFAULT_CARDS = ["Chase Sapphire Reserve"]` must not come back.

    That default silently asserted the one Chase card that keeps World of Hyatt
    at 1:1 across the 2026-10-01 change, on every run that did not override it.
    """
    import ast

    tree = ast.parse((ROOT / "src" / "main.py").read_text())
    assigned = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "DEFAULT_CARDS" not in assigned
    assert "DEFAULT_BALANCES" not in assigned


def test_running_with_no_wallet_exits_non_zero_rather_than_assuming_ur():
    """Step 2's acceptance criterion, exercised through the real CLI."""
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-m", "src.main", "--trip-fixture", "trip_b_europe.json"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert proc.returncode != 0, "a run with no wallet must not succeed"
    assert "No currencies supplied" in proc.stdout + proc.stderr
    assert "will NOT assume" in proc.stdout + proc.stderr


def test_example_wallet_is_obviously_a_template():
    data = json.loads((DATA / "wallet.example.json").read_text())
    assert "_README" in data
    assert "TEMPLATE ONLY" in " ".join(data["_README"])
    assert "FAKE" in " ".join(data["_README"]).upper()


# ---------------------------------------------------------------------------
# Three distinct balance states
# ---------------------------------------------------------------------------


def test_absent_none_and_zero_are_three_different_things():
    wallet = Wallet(balances={"UR": None, "MR": 0}, cards=[CSP])
    assert wallet.holds("UR") and wallet.is_unconstrained("UR")
    assert wallet.holds("MR") and wallet.balance_of("MR") == 0
    assert not wallet.holds("TY")


def test_asking_about_an_unheld_currency_raises_rather_than_returning_zero():
    wallet = Wallet(balances={"UR": 100}, cards=[])
    with pytest.raises(WalletError, match="not in the wallet"):
        wallet.balance_of("MR")


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_empty_wallet_is_rejected_with_an_actionable_message():
    with pytest.raises(WalletError, match="No currencies supplied"):
        validate_wallet(Wallet())


def test_empty_wallet_message_refuses_to_assume_ur():
    try:
        validate_wallet(Wallet())
    except WalletError as e:
        assert "will NOT assume" in str(e)


def test_unknown_currency_is_fatal(known):
    currencies, cards = known
    with pytest.raises(WalletError, match="Unknown currency"):
        validate_wallet(Wallet(balances={"URR": 1000}, cards=[CSP]), currencies, cards)


def test_unknown_card_is_fatal(known):
    currencies, cards = known
    with pytest.raises(WalletError, match="Unknown card"):
        validate_wallet(
            Wallet(balances={"UR": 1000}, cards=["Chase Saphire Preferred"]),
            currencies, cards,
        )


def test_negative_balance_is_rejected(known):
    currencies, cards = known
    with pytest.raises(WalletError, match="cannot be negative"):
        validate_wallet(Wallet(balances={"UR": -5}, cards=[CSP]), currencies, cards)


def test_all_null_balances_warns_that_the_ceiling_cannot_bind(known):
    currencies, cards = known
    warnings = validate_wallet(Wallet(balances={"UR": None}, cards=[CSP]), currencies, cards)
    assert any("UNCONSTRAINED" in w for w in warnings)


def test_all_zero_balances_is_valid_but_funds_nothing(known):
    currencies, cards = known
    assert validate_wallet(Wallet(balances={"UR": 0}, cards=[CSP]), currencies, cards) is not None


def test_cards_but_no_balances_is_rejected(known):
    currencies, cards = known
    with pytest.raises(WalletError):
        validate_wallet(Wallet(balances={}, cards=[CSP]), currencies, cards)


def test_no_cards_warns_about_card_dependent_ratios(known):
    currencies, cards = known
    warnings = validate_wallet(Wallet(balances={"UR": 1000}, cards=[]), currencies, cards)
    assert any("Card-dependent ratios" in w for w in warnings)


def test_non_positive_valuation_is_rejected(known):
    currencies, cards = known
    with pytest.raises(WalletError, match="must be positive"):
        validate_wallet(
            Wallet(balances={"UR": 1}, cards=[CSP], valuation_cpp={"UR": 0.0}),
            currencies, cards,
        )


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["ur_only.json", "ur_plus_mr.json", "all_five.json", "unconstrained.json"]
)
def test_synthetic_wallet_fixtures_load_and_validate(name, known):
    currencies, cards = known
    wallet = load_wallet(WALLETS / name)
    validate_wallet(wallet, currencies, cards)


def test_all_five_wallet_distinguishes_the_balance_states(known):
    wallet = load_wallet(WALLETS / "all_five.json")
    assert wallet.balance_of("C1") is None
    assert wallet.balance_of("Bilt") == 0
    assert wallet.balance_of("UR") == 50000


def test_valuation_overrides_load(known):
    wallet = load_wallet(WALLETS / "ur_plus_mr.json")
    assert wallet.valuation_of("MR") == pytest.approx(0.014)
    assert wallet.valuation_of("UR") == pytest.approx(0.01), "default stays 1cpp"


def test_malformed_json_gives_a_clear_error(tmp_path):
    p = tmp_path / "w.json"
    p.write_text("{not json")
    with pytest.raises(WalletError, match="not valid JSON"):
        load_wallet(p)


def test_missing_file_gives_a_clear_error(tmp_path):
    with pytest.raises(WalletError, match="Could not read"):
        load_wallet(tmp_path / "nope.json")


def test_string_balance_is_rejected(tmp_path):
    p = tmp_path / "w.json"
    p.write_text(json.dumps({"balances": {"UR": "lots"}}))
    with pytest.raises(WalletError, match="must be a number or null"):
        load_wallet(p)


# ---------------------------------------------------------------------------
# CLI flags
# ---------------------------------------------------------------------------


def test_balance_flags_parse():
    wallet = wallet_from_flags(["UR=180000", "MR="], cards=[CSP])
    assert wallet.balance_of("UR") == 180000
    assert wallet.balance_of("MR") is None
    assert wallet.cards == [CSP]


def test_balance_flag_accepts_thousands_separators():
    assert wallet_from_flags(["UR=180,000"]).balance_of("UR") == 180000


def test_malformed_balance_flag_is_rejected():
    with pytest.raises(WalletError, match="CURRENCY=AMOUNT"):
        wallet_from_flags(["180000"])
    with pytest.raises(WalletError, match="not a whole number"):
        wallet_from_flags(["UR=lots"])


def test_valuation_flag_is_in_cents():
    wallet = wallet_from_flags(["MR=1000"], valuation_flags=["MR=1.4"])
    assert wallet.valuation_of("MR") == pytest.approx(0.014)
