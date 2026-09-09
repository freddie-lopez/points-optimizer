"""
Central configuration constants for the points optimizer.

Everything here is a named, single-source-of-truth constant. Nothing in this
module should be duplicated as a magic number elsewhere in the codebase.
"""
import os
from datetime import date
from pathlib import Path
from typing import Dict, Optional

# ---------------------------------------------------------------------------
# .env loading
# ---------------------------------------------------------------------------
# Nothing was loading .env, so SEATS_AERO_KEY was never actually read - a live
# search failed with "key not found" before it ever attempted a request. Done
# here with no new dependency. Values already in the environment win, and the
# key is never logged or printed.

_ENV_PATH = Path(__file__).parent.parent / ".env"


def load_env(path: Path = _ENV_PATH) -> None:
    """Load KEY=VALUE pairs from .env into os.environ without overriding it."""
    try:
        text = path.read_text()
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


load_env()

# ---------------------------------------------------------------------------
# Points valuation
# ---------------------------------------------------------------------------

# The cash fallback is scored at ONE CENT PER POINT. This is the number that
# lets a cash price compete head-to-head with a points price:
#   $367 cash  ->  36,700 points-equivalent
# It is deliberately NOT an aspirational redemption value (2cpp etc.). It is the
# neutral yardstick both sides are measured against.
CASH_VALUATION_CPP: float = 0.01

# Default valuation used when ranking. Same yardstick as above so that points
# paths and cash paths are directly comparable.
DEFAULT_VALUATION_CPP: float = CASH_VALUATION_CPP


def cash_to_points_equivalent(cash_usd: float, cpp: float = CASH_VALUATION_CPP) -> int:
    """
    Convert a cash amount to its points-equivalent at `cpp` cents per point.

    At the default 1cpp: $367.00 -> 36,700 points.
    """
    if cpp <= 0:
        raise ValueError("valuation cpp must be positive")
    return int(round(cash_usd / cpp))


def points_to_cash_equivalent(points: int, cpp: float = CASH_VALUATION_CPP) -> float:
    """Convert points to their cash-equivalent at `cpp` cents per point."""
    return points * cpp


# ---------------------------------------------------------------------------
# Transfer mechanics
# ---------------------------------------------------------------------------

# Chase Ultimate Rewards transfers move in 1,000-point increments. This is the
# structural reason a small amount of stranding is unavoidable: you cannot
# transfer 1,437 points, only 1,000 or 2,000.
DEFAULT_TRANSFER_INCREMENT: int = 1000

# A balance value of None means "unconstrained" - the user has not told us their
# balance, so we cost the trip out without a feasibility ceiling.
UNCONSTRAINED = None

# Per-issuer transfer increments. Only UR is verified; anything absent falls back
# to DEFAULT_TRANSFER_INCREMENT and is reported as an assumption.
TRANSFER_INCREMENTS: Dict[str, int] = {
    "UR": 1000,
}


def transfer_increment_for(currency: str) -> int:
    """Transfer block size for a source currency, defaulting to 1,000."""
    return TRANSFER_INCREMENTS.get(currency, DEFAULT_TRANSFER_INCREMENT)


# ---------------------------------------------------------------------------
# Transfer date vs travel date  (v1 Step 1)
# ---------------------------------------------------------------------------
# v0 looked up every ratio on the TRAVEL date. That is wrong: the ratio that
# applies is the one in force when you actually move the points. Travelling in
# January 2027 does not mean you transfer in January 2027 - you can transfer
# today. v0 therefore quoted the post-2026-10-01 Hyatt 4:3 ratio for a transfer
# that could still be made at 1:1.


def default_transfer_date() -> date:
    """Today. Deliberately a function so tests can freeze it by passing a date."""
    return date.today()


# How far ahead to look when hunting for a better ratio at a different transfer
# date (the TRANSFER_DATE_WINDOW reason).
TRANSFER_WINDOW_DAYS: int = 365


# ---------------------------------------------------------------------------
# Per-currency valuation  (v1 Step 4)
# ---------------------------------------------------------------------------
# Points in different issuer currencies CANNOT be added together: 10,000 UR and
# 10,000 MR are only interchangeable if every ratio in play is 1:1, which is an
# accident of the current UR table and not a property of the world. v1 scores
# every funding plan in USD at a per-currency valuation and reports the raw
# per-currency point counts separately.
#
# Every currency defaults to the SAME neutral 1cpp yardstick used in v0, so the
# default behaviour of the tool is unchanged.
DEFAULT_CURRENCY_VALUATION_CPP: float = CASH_VALUATION_CPP

PER_CURRENCY_VALUATION_CPP: Dict[str, float] = {}


def valuation_for(currency: str, overrides: Optional[Dict[str, float]] = None) -> float:
    """
    Cents-per-point for one issuer currency.

    Resolution order: caller override -> configured table -> the 1cpp default.
    There is no "market value" table and there will not be one: a valuation is a
    user's choice about their own points, not a fact we can look up.
    """
    if overrides and currency in overrides:
        return overrides[currency]
    return PER_CURRENCY_VALUATION_CPP.get(currency, DEFAULT_CURRENCY_VALUATION_CPP)


# Splitting one award across more than two source currencies is operationally
# absurd (two one-way transfers, residue in three accounts) and is refused.
MAX_SPLIT_CURRENCIES: int = 2


# ---------------------------------------------------------------------------
# FX rates
# ---------------------------------------------------------------------------
# SINGLE NAMED CONSTANT. These are printed in every run's output and flagged as
# requiring confirmation. They are NOT fetched live.
#
# v3 (2026-09-08) replaced the two worst entries here with SOURCED values:
#
# GBP: was 1.2700, a PLACEHOLDER with nothing behind it and the single largest
#      unverified input in the model. Now 1.354, looked up on 2026-09-08. The
#      correction moves the Hilton London leg from $638.35 to $680.57 - about
#      $42 - and therefore moves the all-cash denominator of every Trip B
#      headline. That is an FX effect and must never be reported as a
#      live-data effect.
# CAD: NEW. Previously absent entirely, which meant the one real tax figure this
#      project owns - the CAD 44.60 on the captured SFO->MAD Aeroplan award -
#      could not be scored at all and was carried as an explicit unknown. At
#      0.7256 it converts to $32.36.
# EUR: unchanged at 1.1620, back-derived from the Novotel Madrid dual-currency
#      quote ($747.81 / EUR643.57 = 1.16197...). It keeps its own tier because
#      it is an implied BOOKING-SITE rate on one date, not a market rate, and
#      calling it "sourced" would overstate it.
FX_RATES_TO_USD: Dict[str, float] = {
    "USD": 1.0,
    "EUR": 1.1620,
    "GBP": 1.3540,
    "CAD": 0.7256,
}

FX_RATES_AS_OF: str = "2026-09-08"

FX_RATES_PROVENANCE: Dict[str, str] = {
    "USD": "base currency",
    "EUR": "BOOKING-SITE IMPLIED: back-derived from the Novotel Madrid dual-currency quote ($747.81 / EUR643.57 = 1.1620). One booking page on one date - NOT a market rate.",
    "GBP": "SOURCED on 2026-09-08. Replaces the 1.2700 placeholder, which had no source and understated the London hotel by about $42. A sourced rate still goes stale - confirm before trusting.",
    "CAD": "SOURCED on 2026-09-08. Added so the captured CAD 44.60 tax on the real SFO-MAD Aeroplan award can be scored instead of carried as an unknown. Confirm before trusting.",
}

# Machine-checkable provenance tier per currency, so code can ask "is this rate
# a placeholder?" without string-matching the prose above.
#   "base" | "sourced" | "booking_site_implied" | "derived" | "user_supplied"
#   | "placeholder"
#
# v3 added the `sourced` tier BETWEEN `placeholder` and `user_supplied`. Someone
# looked the rate up, so it is not a placeholder - but nobody has taken personal
# responsibility for it the way --fx does, and a rate looked up on a date goes
# stale. Hence `needs_confirmation()`, which is True for both `sourced` and
# `booking_site_implied` and is what drives the banner marker.
FX_PROVENANCE_TIER: Dict[str, str] = {
    "USD": "base",
    "EUR": "booking_site_implied",
    "GBP": "sourced",
    "CAD": "sourced",
}

# When each non-base rate was looked up. A rate without a date cannot go stale,
# which is a claim about it that is never true.
FX_SOURCED_ON: Dict[str, date] = {
    "EUR": date(2026, 9, 7),   # the Novotel capture date
    "GBP": date(2026, 9, 8),
    "CAD": date(2026, 9, 8),
}

# Past this age a sourced rate prints a STALE RATE marker and every total that
# depends on it is marked. It warns; it never fails.
FX_STALE_AFTER_DAYS: int = 30

# Tiers where somebody looked the number up but nobody has vouched for it.
FX_NEEDS_CONFIRMATION_TIERS = frozenset({"sourced", "booking_site_implied", "derived"})

FX_CONFIRMATION_REQUIRED: bool = True

FX_WARNING: str = (
    "FX RATES REQUIRE CONFIRMATION. EUR is back-derived from a single hotel "
    "quote; GBP and CAD were looked up on a date and go stale. Every "
    "foreign-currency total below inherits that uncertainty."
)


# ---------------------------------------------------------------------------
# v3: live trip mode
# ---------------------------------------------------------------------------

# Where raw response envelopes live. The CACHE is runtime state and gitignored;
# the SNAPSHOT directory is committed and is the regression corpus. See
# src/response_cache.py for why raw pages are stored rather than parsed Awards.
CACHE_DIR = Path(__file__).parent.parent / "data" / "cache" / "seats_aero"
SNAPSHOT_DIR = (
    Path(__file__).parent.parent / "tests" / "fixtures" / "seats_aero" / "live_trip_b"
)

# Award space moves. A day-old cache quietly re-answering a fresh question is its
# own kind of lie, so six hours. Plan section 11.3 is right that a time-based TTL
# makes the answer a function of WHEN you ran the tool, and that the thing which
# actually makes a result reproducible is a committed snapshot manifest. v3
# builds the manifest and treats it as documentation; `--from-snapshot` is the
# small feature that would close this and it was deliberately left out to keep
# v3 to one feature.
CACHE_TTL_SECONDS: int = 6 * 60 * 60

# Query one date per leg by default. A window is opt-in because everything it
# finds off-date is unscoreable, and an unscoreable finding printed by default is
# clutter that trains people to skip the block.
DEFAULT_FLEX_DAYS: int = 0


def convert_to_usd(amount: float, currency: str) -> float:
    """Convert an amount in `currency` to USD using the configured FX rates."""
    cur = currency.upper()
    if cur not in FX_RATES_TO_USD:
        raise ValueError(
            f"No FX rate configured for {cur!r}. Add it to config.FX_RATES_TO_USD."
        )
    return amount * FX_RATES_TO_USD[cur]


def is_placeholder_rate(currency: str) -> bool:
    """
    True if this currency's FX rate has NO source behind it.

    Before v3, GBP was the live example: 1.27 was written down with nothing to
    back it and it carried the entire London hotel figure. GBP and CAD are now
    `sourced`, so this is False for them - but `needs_confirmation()` is still
    True and the banner still says so. An unconfigured currency is still treated
    as a placeholder, which is the safe direction.
    """
    return FX_PROVENANCE_TIER.get(currency.upper(), "placeholder") == "placeholder"


def needs_confirmation(currency: str) -> bool:
    """
    True if this rate was looked up but nobody has vouched for it.

    `sourced` and `booking_site_implied` both mean "a human found a number on a
    date". That is strictly better than a placeholder and strictly worse than a
    user-supplied rate, and the output must keep saying so rather than rounding
    it to either neighbour.
    """
    return FX_PROVENANCE_TIER.get(currency.upper(), "placeholder") in (
        FX_NEEDS_CONFIRMATION_TIERS
    )


def fx_rate_age_days(currency: str, today: Optional[date] = None) -> Optional[int]:
    """Days since this rate was looked up, or None if it carries no source date."""
    sourced = FX_SOURCED_ON.get(currency.upper())
    if sourced is None:
        return None
    return ((today or date.today()) - sourced).days


def is_stale_rate(currency: str, today: Optional[date] = None) -> bool:
    """
    True once a dated rate is older than FX_STALE_AFTER_DAYS.

    Only rates that HAVE a source date can be stale. A placeholder is not stale;
    it is worse, and has its own marker.
    """
    age = fx_rate_age_days(currency, today)
    return age is not None and age > FX_STALE_AFTER_DAYS


def set_fx_rate(currency: str, rate: float, provenance: str = "user_supplied") -> None:
    """
    Override an FX rate at runtime (the --fx flag).

    Recording the provenance is the point: a user-supplied rate clears BOTH the
    placeholder marker and the confirm-before-trusting marker, because someone
    has taken responsibility for it. Inventing a rate here would not. The source
    date is dropped too - a user-supplied rate is as of now, and leaving the old
    date attached would let it inherit a staleness that is not its own.
    """
    cur = currency.upper()
    if rate <= 0:
        raise ValueError(f"FX rate for {cur} must be positive, got {rate!r}.")
    FX_RATES_TO_USD[cur] = float(rate)
    FX_PROVENANCE_TIER[cur] = provenance
    if provenance == "user_supplied":
        FX_SOURCED_ON.pop(cur, None)
    FX_RATES_PROVENANCE[cur] = (
        f"USER SUPPLIED at runtime via --fx {cur}={rate}. Not fetched, not verified "
        f"by the tool - the user vouches for this number."
        if provenance == "user_supplied"
        else FX_RATES_PROVENANCE.get(cur, provenance)
    )


def any_placeholder_rates() -> bool:
    return any(is_placeholder_rate(c) for c in FX_RATES_TO_USD)


def any_unconfirmed_rates() -> bool:
    return any(
        is_placeholder_rate(c) or needs_confirmation(c) for c in FX_RATES_TO_USD
    )


def stale_currencies(today: Optional[date] = None) -> list:
    return sorted(c for c in FX_RATES_TO_USD if is_stale_rate(c, today))


def any_stale_rates(today: Optional[date] = None) -> bool:
    return bool(stale_currencies(today))


def fx_report_lines(today: Optional[date] = None) -> list:
    """Lines describing the FX rates in use, for printing at the top of output."""
    lines = [
        f"FX rates used (table as of {FX_RATES_AS_OF}):",
    ]
    for cur, rate in FX_RATES_TO_USD.items():
        if cur == "USD":
            continue
        markers = []
        if is_placeholder_rate(cur):
            markers.append("[UNVERIFIED RATE]")
        if is_stale_rate(cur, today):
            markers.append("[STALE RATE]")
        if needs_confirmation(cur):
            markers.append("[CONFIRM BEFORE TRUSTING]")
        marker = (" " + " ".join(markers)) if markers else ""
        age = fx_rate_age_days(cur, today)
        dated = ""
        if cur in FX_SOURCED_ON:
            dated = f"   sourced {FX_SOURCED_ON[cur]} ({age} days old)"
        lines.append(
            f"  1 {cur} = {rate:.4f} USD{marker}{dated}   "
            f"[{FX_RATES_PROVENANCE[cur]}]"
        )
    stale = stale_currencies(today)
    if stale:
        lines.append(
            f"  !! STALE RATE: {', '.join(stale)} were looked up more than "
            f"{FX_STALE_AFTER_DAYS} days ago. Every total below that depends on "
            f"them is marked. This is a warning, not a failure."
        )
    if FX_CONFIRMATION_REQUIRED and any_unconfirmed_rates():
        lines.append(f"  !! {FX_WARNING}")
        lines.append(
            "  !! Supply a rate with --fx GBP=1.36 to take responsibility for it "
            "and clear the marker."
        )
    return lines
