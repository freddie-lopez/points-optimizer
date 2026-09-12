"""
The runtime wallet: which issuer currencies the user holds, how much, on what cards.

NOTHING about any particular person's holdings is compiled into this module, into
`data/`, or into any default. v0 hardcoded `DEFAULT_CARDS = ["Chase Sapphire
Reserve"]` in main.py, which meant every run silently asserted a card the user had
never said he held - and that card is precisely the one that keeps World of Hyatt
at 1:1. A wrong default there changes real answers.

Three balance states are DISTINCT and must stay distinct:

    absent   - the currency is NOT HELD. No path may be funded from it.
    None     - held, balance UNKNOWN. No feasibility ceiling is applied.
    0        - held, but empty. Contributes nothing.

Collapsing "absent" into "0" would be harmless; collapsing "absent" into "None"
would silently invent an unconstrained account.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from src import config


class WalletError(ValueError):
    """Raised when a wallet cannot be loaded or does not validate."""


@dataclass
class Wallet:
    """A user's issuer currencies, balances, cards, and valuation choices."""

    balances: Dict[str, Optional[int]] = field(default_factory=dict)
    cards: List[str] = field(default_factory=list)
    # Per-currency cents-per-point. Absent -> config default (1cpp).
    valuation_cpp: Dict[str, float] = field(default_factory=dict)
    source: str = "runtime"

    # -- membership ------------------------------------------------------

    @property
    def currencies(self) -> List[str]:
        """Currencies the user actually holds. Absence means NOT HELD."""
        return list(self.balances.keys())

    def holds(self, currency: str) -> bool:
        return currency in self.balances

    def balance_of(self, currency: str) -> Optional[int]:
        """
        Balance for a held currency. Raises for a currency that is not held.

        Deliberately raises rather than returning 0: a caller that asks about a
        currency the user does not hold has a bug, and a silent 0 would hide it.
        """
        if currency not in self.balances:
            raise WalletError(
                f"{currency!r} is not in the wallet. Absence means NOT HELD - it is "
                f"not a zero balance and not an unconstrained one."
            )
        return self.balances[currency]

    def is_unconstrained(self, currency: str) -> bool:
        return self.balance_of(currency) is None

    def valuation_of(self, currency: str) -> float:
        return config.valuation_for(currency, self.valuation_cpp)

    def is_empty(self) -> bool:
        return not self.balances

    # -- description -----------------------------------------------------

    def describe(self) -> List[str]:
        lines = []
        for cur in sorted(self.balances):
            bal = self.balances[cur]
            shown = "UNCONSTRAINED (balance not supplied)" if bal is None else f"{bal:,}"
            cpp = self.valuation_of(cur)
            lines.append(f"  {cur}: {shown}   valued at {cpp * 100:.2f} cents/point")
        lines.append(f"  cards: {', '.join(self.cards) if self.cards else '(none supplied)'}")
        return lines


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_wallet(
    wallet: Wallet,
    known_currencies: Optional[set] = None,
    known_cards: Optional[set] = None,
) -> List[str]:
    """
    Validate a wallet. Raises WalletError on anything structurally wrong;
    returns a list of non-fatal WARNINGS.

    Unknown currency and unknown card are FATAL: a typo'd currency name would
    otherwise become an account that silently funds nothing, and a typo'd card
    name would silently drop the user to a worse transfer ratio.
    """
    warnings: List[str] = []

    if wallet.is_empty():
        raise WalletError(
            "No currencies supplied. Pass --wallet path.json, or --balance UR=180000. "
            "The tool will NOT assume you hold Chase Ultimate Rewards - which cards "
            "and balances you have is an input, never a default."
        )

    for currency, balance in wallet.balances.items():
        if known_currencies is not None and currency not in known_currencies:
            raise WalletError(
                f"Unknown currency {currency!r}. Known currencies: "
                f"{', '.join(sorted(known_currencies))}."
            )
        if balance is None:
            continue
        if not isinstance(balance, int) or isinstance(balance, bool):
            raise WalletError(
                f"Invalid balance for {currency!r}: {balance!r} is not a whole number "
                f"of points. Use null for 'held, balance unknown'."
            )
        if balance < 0:
            raise WalletError(
                f"Invalid balance for {currency!r}: {balance:,} - a points balance "
                f"cannot be negative. Use 0 for an empty account, or null to leave "
                f"the balance unconstrained."
            )

    for card in wallet.cards:
        if known_cards is not None and card not in known_cards:
            raise WalletError(
                f"Unknown card {card!r}. Cards known to affect a transfer ratio: "
                f"{', '.join(sorted(known_cards)) or '(none)'}. A card that changes "
                f"no ratio can be omitted; a misspelt one would silently cost you "
                f"the better ratio."
            )

    for currency, cpp in wallet.valuation_cpp.items():
        if currency not in wallet.balances:
            warnings.append(
                f"Valuation supplied for {currency}, which is not in the wallet. Ignored."
            )
        if cpp <= 0:
            raise WalletError(
                f"Valuation for {currency} must be positive, got {cpp!r}."
            )

    if not wallet.cards:
        warnings.append(
            "No cards supplied. Card-dependent ratios (World of Hyatt from Chase UR) "
            "cannot be resolved and those transfers will find no ratio at all."
        )

    if all(b is None for b in wallet.balances.values()):
        warnings.append(
            "Every balance is UNCONSTRAINED. No feasibility ceiling is applied, so "
            "the stranded-points constraint cannot bind on this run."
        )

    return warnings


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_wallet(path: Path) -> Wallet:
    """Load a wallet from JSON. See data/wallet.example.json for the shape."""
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except OSError as e:
        raise WalletError(f"Could not read wallet file {path}: {e}") from e
    except json.JSONDecodeError as e:
        raise WalletError(f"Wallet file {path} is not valid JSON: {e}") from e

    if not isinstance(data, dict):
        raise WalletError(f"Wallet file {path} must contain a JSON object.")

    raw_balances = data.get("balances", {})
    if not isinstance(raw_balances, dict):
        raise WalletError("Wallet 'balances' must be an object of currency -> number|null.")

    balances: Dict[str, Optional[int]] = {}
    for cur, val in raw_balances.items():
        if val is None:
            balances[str(cur)] = None
        elif isinstance(val, bool):
            raise WalletError(f"Balance for {cur!r} must be a number or null.")
        elif isinstance(val, (int, float)):
            try:
                balances[str(cur)] = int(val)
            except (ValueError, OverflowError) as e:
                raise WalletError(
                    f"Balance for {cur!r} is not a number this tool can hold: {e}"
                ) from e
        else:
            raise WalletError(
                f"Balance for {cur!r} must be a number or null, got {val!r}."
            )

    cards = data.get("cards", []) or []
    if not isinstance(cards, list) or any(not isinstance(c, str) for c in cards):
        raise WalletError("Wallet 'cards' must be a list of card-name strings.")

    valuations = data.get("valuation_cpp", {}) or {}
    if not isinstance(valuations, dict):
        raise WalletError("Wallet 'valuation_cpp' must be an object of currency -> cpp.")
    try:
        valuation_cpp = {str(k): float(v) for k, v in valuations.items()}
    except (TypeError, ValueError, OverflowError) as e:
        raise WalletError(f"Wallet 'valuation_cpp' has a value that is not a number: {e}") from e

    return Wallet(
        balances=balances,
        cards=[str(c) for c in cards],
        valuation_cpp=valuation_cpp,
        source=str(path),
    )


def wallet_from_flags(
    balance_flags: Optional[List[str]] = None,
    cards: Optional[List[str]] = None,
    valuation_flags: Optional[List[str]] = None,
) -> Wallet:
    """
    Build a wallet from repeated CLI flags.

        --balance UR=180000     held, 180,000 points
        --balance MR=           held, balance unknown (unconstrained)
        --valuation MR=1.4      value MR at 1.4 cents per point
    """
    balances: Dict[str, Optional[int]] = {}
    for raw in balance_flags or []:
        if "=" not in raw:
            raise WalletError(
                f"--balance expects CURRENCY=AMOUNT, got {raw!r}. Use 'UR=180000', "
                f"or 'UR=' to declare the currency held with an unknown balance."
            )
        cur, _, amount = raw.partition("=")
        cur = cur.strip()
        amount = amount.strip()
        if not cur:
            raise WalletError(f"--balance {raw!r} has an empty currency name.")
        if amount == "":
            balances[cur] = None
            continue
        try:
            balances[cur] = int(amount.replace(",", "").replace("_", ""))
        except ValueError:
            raise WalletError(
                f"--balance {raw!r}: {amount!r} is not a whole number of points."
            ) from None

    valuation_cpp: Dict[str, float] = {}
    for raw in valuation_flags or []:
        if "=" not in raw:
            raise WalletError(f"--valuation expects CURRENCY=CENTS, got {raw!r}.")
        cur, _, cents = raw.partition("=")
        try:
            valuation_cpp[cur.strip()] = float(cents.strip()) / 100.0
        except ValueError:
            raise WalletError(f"--valuation {raw!r}: {cents!r} is not a number.") from None

    return Wallet(
        balances=balances,
        cards=list(cards or []),
        valuation_cpp=valuation_cpp,
        source="command line flags",
    )
