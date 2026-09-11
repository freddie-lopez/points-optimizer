"""CLI entry point for points transfer optimizer."""
import argparse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console

from src import config
from src.formatter import (
    export_html,
    print_alternatives,
    print_fx_banner,
    print_leg_detail,
    print_leg_results,
    print_residue_report,
    print_strategies,
    print_summary,
    print_trip_totals,
    print_valuation_banner,
    print_wallet_banner,
)
from src.models import DateRange, Trip
from src.optimizer import evaluate_trip, optimize, trip_residue, trip_totals, validate_balances
from src.ratio_manager import RatioManager
from src.seats_client import SeatsClient
from src.surcharge import default_table
from src.trip_loader import load_trip_fixture
from src.wallet import WalletError, load_wallet, validate_wallet, wallet_from_flags

DATA_DIR = Path(__file__).parent.parent / "data"
FIXTURE_DIR = Path(__file__).parent.parent / "tests" / "fixtures" / "trips"

# THERE IS NO DEFAULT WALLET AND THERE MUST NOT BE ONE.
#
# v0 had `DEFAULT_CARDS = ["Chase Sapphire Reserve"]` here, so every run that did
# not pass --card silently asserted that the user held a Sapphire Reserve - which
# is the one Chase card that keeps World of Hyatt at 1:1 across the 2026-10-01
# change. A default there is not a convenience, it is an unearned claim about
# someone's wallet that changes real answers. Which cards and balances the user
# holds is a runtime INPUT.


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.main",
        description=(
            "Optimize Chase Ultimate Rewards transfers against cash prices. "
            "Every leg is also scored as 'pay cash' at 1 cent per point; a leg "
            "where cash wins is reported as PAY CASH, which is a correct answer."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Score a trip fixture LIVE (the default)
  python -m src.main --trip-fixture trip_b_europe.json --balance UR=160000

  # Score it with no network at all, from the fixture's own prices
  python -m src.main --trip-fixture trip_b_europe.json --offline --balance UR=160000

  # Replay a previous live run exactly, with a manifest hash beside the margin
  python -m src.main --trip-fixture trip_b_europe.json \
      --from-snapshot tests/fixtures/seats_aero/live_trip_b/MANIFEST.md

  # Live award search for a single route
  python -m src.main --origin SFO --destination LHR --date 2027-01-15

EXIT CODES (the single authoritative list; README.md quotes this one):
  0  Success. A margin was produced and, if a balance was given, the plan is
     executable from it.
  1  Error. Bad arguments, a missing or unreadable file, an unreplayable
     manifest, or an unhandled failure. NOTHING was scored.
  2  WALLET ERROR. No balances or cards were supplied, or the wallet file is
     malformed. The tool refuses to assume which cards and points you hold,
     because a default wallet changes real answers. NOTHING was scored.
  3  WITHHELD. At least one leg did not come back live (this is the DEFAULT;
     --allow-badge-fallback opts out), so no margin is quoted. This is a
     REFUSAL TO ANSWER, not a finding of zero value.
  4  NOT EXECUTABLE. A margin was produced, but the recommendation cannot be
     funded from the balance you supplied. The number is real; the plan is not
     actionable as printed.

  3 and 4 are deliberately different: "the number is not quotable" and "the
  plan cannot be executed" are different failures and a wrapping script must be
  able to tell them apart.

DEFAULTS:
  --trip-fixture implies live scoring unless --offline is passed. A run with no
  network and no --offline exits 3 rather than quietly scoring Google badges.
  --live and --require-all-live are still accepted and are no-ops.
        """,
    )

    mode = parser.add_argument_group("mode (choose one)")
    mode.add_argument(
        "--trip-fixture",
        help="Score a multi-leg trip fixture (filename in tests/fixtures/trips/, or a path)",
    )
    mode.add_argument("--origin", help="IATA code for origin (e.g. SFO)")
    mode.add_argument("--destination", help="IATA code for destination (e.g. LHR)")
    mode.add_argument(
        "--date",
        help="Travel date (YYYY-MM-DD) or range (YYYY-MM-DD:YYYY-MM-DD)",
    )

    build = parser.add_argument_group(
        "building a trip - writes a fixture that can ONLY be scored live"
    )
    build.add_argument(
        "--new-trip",
        dest="new_trip",
        default=None,
        metavar="NAME",
        help=(
            "Write tests/fixtures/trips/NAME.json from the flags below. The "
            "fixture carries NO points_candidates key on any leg - not an empty "
            "one, none - so the only way it can ever be scored on points is "
            "--live or --from-snapshot. Unknown airport codes are refused, not "
            "guessed, and each leg's description is GENERATED from its codes so "
            "the two cannot disagree."
        ),
    )
    build.add_argument(
        "--leg",
        action="append",
        dest="legs",
        metavar="ORIGIN:DEST:YYYY-MM-DD:CASH_USD",
        help="A flight leg; repeatable. Codes are validated against data/airports.csv.",
    )
    build.add_argument(
        "--hotel",
        action="append",
        dest="hotels",
        metavar="NAME:CHECKIN:NIGHTS:CASH_USD",
        help=(
            "A hotel leg; repeatable. Gets no origin/destination - Seats.aero is "
            "flights-only and a hotel leg carrying airport codes would be queried."
        ),
    )
    build.add_argument(
        "--travelers",
        type=str,
        default="1",
        metavar="N",
        help="Travellers on every leg (default 1). APD is charged PER PASSENGER.",
    )
    build.add_argument(
        "--cabin",
        default="Y",
        metavar="Y|W|J|F",
        help="Cabin for every flight leg (default Y).",
    )
    build.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing NAME.json. Without this an existing file is refused.",
    )

    bal = parser.add_argument_group("wallet (REQUIRED - nothing is assumed)")
    bal.add_argument(
        "--wallet",
        help=(
            "Path to a wallet JSON file (see data/wallet.example.json). Holds "
            "balances per currency, cards held, and optional per-currency "
            "valuations."
        ),
    )
    bal.add_argument(
        "--balance",
        action="append",
        dest="balances",
        metavar="CURRENCY=AMOUNT",
        help=(
            "Currency held and its balance; repeatable. 'UR=180000' for a known "
            "balance, 'UR=' for held-but-unknown (no feasibility ceiling), and "
            "OMIT the currency entirely if you do not hold it. Absent is NOT the "
            "same as zero."
        ),
    )
    bal.add_argument(
        "--card",
        action="append",
        dest="cards",
        metavar="NAME",
        help=(
            "Card held; repeatable. NO DEFAULT - the tool will not assume which "
            "cards you have. Card-dependent ratios (World of Hyatt) cannot be "
            "resolved without this."
        ),
    )
    bal.add_argument(
        "--valuation",
        action="append",
        dest="valuations",
        metavar="CURRENCY=CENTS",
        help=(
            "Per-currency valuation in cents per point, e.g. 'MR=1.4'; repeatable. "
            "Default is 1.0 cent for every currency - the neutral yardstick."
        ),
    )
    bal.add_argument(
        "--balance-ur",
        type=int,
        default=None,
        help="DEPRECATED shorthand for --balance UR=N. Kept so older scripts keep working.",
    )

    tune = parser.add_argument_group("tuning")
    tune.add_argument(
        "--transfer-date",
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "The date you would actually MOVE the points. Defaults to today. All "
            "ratio and bonus lookups use this, NOT the travel date - the ratio "
            "that applies is the one in force when you transfer."
        ),
    )
    tune.add_argument(
        "--fx",
        action="append",
        dest="fx",
        metavar="CURRENCY=RATE",
        help=(
            "Override an FX rate, e.g. 'GBP=1.29'. Recorded as user_supplied "
            "provenance, which clears the UNVERIFIED RATE marker on totals that "
            "depend on it."
        ),
    )
    tune.add_argument(
        "--show-alternatives",
        action="store_true",
        help=(
            "Show same-metal alternatives: other programs that can ticket the same "
            "aeroplane with a lower carrier-imposed surcharge."
        ),
    )
    tune.add_argument(
        "--valuation-cpp",
        type=float,
        default=config.CASH_VALUATION_CPP,
        help=(
            f"Cents per point used to score cash against points "
            f"(default {config.CASH_VALUATION_CPP} = 1 cent per point)"
        ),
    )
    tune.add_argument(
        "--transfer-increment",
        type=int,
        default=config.DEFAULT_TRANSFER_INCREMENT,
        help=f"Transfer block size (default {config.DEFAULT_TRANSFER_INCREMENT})",
    )
    tune.add_argument(
        "--max-stranded-points",
        type=int,
        default=None,
        help=(
            "Hard ceiling on stranded points per transfer. Default is the "
            "unavoidable minimum implied by the transfer increment. Pass 0 for "
            "a strict no-stranding-at-all policy."
        ),
    )
    live = parser.add_argument_group(
        "live trip mode - points from Seats.aero, cash still from captures"
    )
    live.add_argument(
        "--live",
        action="store_true",
        help=(
            "ACCEPTED AND NOW A NO-OP: --trip-fixture is now live by "
            "default and --offline is the opt-out. Kept so existing scripts and "
            "docs keep working and keep meaning what they said. CASH IS NEVER "
            "TOUCHED: screenshots remain the source of truth for cash and there "
            "is no cash-price API in scope. Hotels are unaffected - Seats.aero "
            "is flights-only."
        ),
    )
    live.add_argument(
        "--flex-days",
        type=int,
        default=config.DEFAULT_FLEX_DAYS,
        metavar="N",
        help=(
            "Widen each leg's query by N days either side (one call per leg "
            "regardless). Awards on OTHER dates are reported as advisories and "
            "are NEVER scored: there is no cash price for those dates and "
            "comparing them to this date's fare is biased in favour of points. "
            f"Default {config.DEFAULT_FLEX_DAYS}."
        ),
    )
    live.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore the disk cache and re-fetch, overwriting. Spends API calls.",
    )
    live.add_argument(
        "--cache-ttl",
        type=int,
        default=None,
        metavar="SECONDS",
        help=(
            f"How long a cached response may answer a fresh question (default "
            f"{config.CACHE_TTL_SECONDS}s = "
            f"{config.CACHE_TTL_SECONDS // 3600}h). 0 disables reuse. An expired "
            f"entry is kept on disk, never deleted."
        ),
    )
    live.add_argument(
        "--require-all-live",
        action="store_true",
        help=(
            "ACCEPTED AND NOW A NO-OP: this is now the DEFAULT and "
            "--allow-badge-fallback is the opt-out. It WITHHOLDS the trip margin "
            "entirely unless every flight leg with a points candidate came back "
            "live (or replayed from a snapshot that was live at capture). Exits "
            "3 when it withholds."
        ),
    )
    live.add_argument(
        "--snapshot-dir",
        default=None,
        metavar="PATH",
        help=(
            f"Where to archive every fetched response as a test fixture "
            f"(default {config.SNAPSHOT_DIR}). Archival is unconditional - there "
            f"is no opt-in flag, because an archival step you can forget is one "
            f"that does not happen."
        ),
    )

    live.add_argument(
        "--from-snapshot",
        dest="from_snapshot",
        default=None,
        metavar="MANIFEST.md",
        help=(
            "REPLAY a previous live run from its committed snapshots instead of "
            "calling Seats.aero. The manifest becomes an INPUT: every selected "
            "row's file is verified against a hash recomputed from its own "
            "bytes, and the resulting margin is printed WITH a manifest hash so "
            "anyone with this repo gets the same number. Refuses the whole run "
            "(exit 1) on a missing snapshot, a hash mismatch, or a manifest that "
            "does not cover every queryable leg - there is no partial replay. "
            "Mutually exclusive with --live, --offline, --refresh, --cache-ttl "
            "and --flex-days."
        ),
    )
    live.add_argument(
        "--offline",
        action="store_true",
        help=(
            "Score the fixture WITHOUT any transport: no API call, no replay. "
            "Every flight leg is NOT_QUERIED and the fixture's own points prices "
            "are what gets scored."
        ),
    )
    live.add_argument(
        "--allow-badge-fallback",
        action="store_true",
        help=(
            "Permit a fixture badge to stand in when live data does not arrive, "
            "rather than withholding the margin. The result is reported with "
            "margin provenance 'badge_fallback' and is quotable ONLY with that "
            "qualifier attached."
        ),
    )
    live.add_argument(
        "--trips",
        dest="trips",
        default=None,
        metavar="auto|all|off",
        help=(
            "Operating-airline lookup through Seats.aero's trips endpoint, one "
            "call per availability id after every search has finished. 'auto' "
            "(the default for --trip-fixture) looks up only awards whose cash "
            "side can depend on the metal: a direct UR partner whose carrier "
            "surcharge is not a program-wide $0. 'all' looks up every live "
            "award (disclosure only). 'off' looks up nothing. Under today's "
            "rules a lookup DISCLOSES the flight numbers and the carrier they "
            "name; it cannot change a score, a verdict or an exit code. Not "
            "accepted with --offline, --from-snapshot or a single-route search."
        ),
    )
    live.add_argument(
        "--trips-cap",
        dest="trips_cap",
        default=None,
        metavar="N",
        help=(
            "Most trips requests this run may SEND (disk-cache hits are free). "
            "Default 10, allowed 1-50. Lookups past it read NOT LOOKED UP."
        ),
    )
    live.add_argument(
        "--api-key",
        default=None,
        metavar="KEY",
        help=(
            "Seats.aero API key, highest priority of four sources (flag, "
            f"{config.KEY_ENV_VAR} in the environment, ./.env, "
            f"{config.USER_CONFIG_ENV_PATH}). Every run that needs a key prints "
            "which source it used and a MASKED key; the full key is never "
            "printed on any code path."
        ),
    )

    tune.add_argument("--passengers", type=int, default=1, help="Number of passengers")
    tune.add_argument("--max-results", type=int, default=5, help="Top N strategies")
    tune.add_argument("--human-cost", type=int, help="Human booking cost, for margin")
    tune.add_argument("--html", action="store_true", help="Export results to HTML")

    return parser


def print_key_banner(console: Console, resolution=None, note: str = "") -> None:
    """
    One line naming the key's SOURCE and a masked key. Printed on every run
    that resolves a key, and on `--from-snapshot` to say none was required.

    `config.mask_key` is the only formatter of key material anywhere in this
    codebase, and this is the only place that prints its output.
    """
    if resolution is None:
        console.print(f"[dim]Seats.aero key: not required ({note})[/dim]")
        return
    console.print(f"[dim]{resolution.describe()}[/dim]")


def run_new_trip(args, console: Console, read=None):
    """
    Build a fixture, echo it, write it, and say what it cannot do.

    Returns (exit_code, path). Emits NO scored number. The one number it writes
    is the cash amount the user typed, echoed back before the write.

    INTERACTIVE MODE IS THE SAME FUNCTION. With no --leg and no --hotel the
    inputs come from prompts instead of flags, and both paths then call the
    identical validators and the identical `build_fixture`. A test asserts the
    two produce byte-identical files.
    """
    from src import trip_builder

    if not (args.legs or args.hotels):
        name, flights, hotels, travelers, cabin = trip_builder.interactive_session(
            read=read or input,
            write=lambda line: console.print(f"[yellow]{line}[/yellow]"),
        )
    else:
        name = trip_builder.validate_name(args.new_trip)
        travelers = trip_builder.validate_travelers(args.travelers)
        cabin = trip_builder.validate_cabin(args.cabin)
        flights = [trip_builder.parse_leg_flag(raw) for raw in (args.legs or [])]
        hotels = [trip_builder.parse_hotel_flag(raw) for raw in (args.hotels or [])]

    fixture = trip_builder.build_fixture(name, flights, hotels, travelers, cabin)

    for line in trip_builder.echo_lines(fixture):
        console.print(f"[cyan]{line}[/cyan]")

    path = trip_builder.write_fixture(fixture, force=bool(args.force))
    console.print(f"[green]Wrote {path}[/green]")
    console.print(
        "[yellow]This fixture has NO points prices. Score it with:[/yellow]\n"
        f"[yellow]  python -m src.main --trip-fixture {name}.json --live "
        f"--balance UR=<n> --card \"<card>\"[/yellow]"
    )
    return 0, path


def parse_date_range(date_str: str) -> DateRange:
    if ":" in date_str:
        a, b = date_str.split(":", 1)
        return DateRange(
            from_date=datetime.strptime(a, "%Y-%m-%d").date(),
            to_date=datetime.strptime(b, "%Y-%m-%d").date(),
        )
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    return DateRange(from_date=d, to_date=d + timedelta(days=30))


def build_wallet(args, ratios: RatioManager):
    """
    Assemble the wallet from --wallet and/or the flags. NEVER from a default.

    Raises WalletError with an actionable message when nothing was supplied.
    """
    if args.wallet:
        wallet = load_wallet(Path(args.wallet))
        if args.cards:
            wallet.cards = list(dict.fromkeys(wallet.cards + list(args.cards)))
    else:
        balance_flags = list(args.balances or [])
        if args.balance_ur is not None:
            balance_flags.append(f"UR={args.balance_ur}")
        wallet = wallet_from_flags(balance_flags, args.cards, args.valuations)

    warnings = validate_wallet(
        wallet,
        known_currencies=set(ratios.issuer_currencies()),
        known_cards=ratios.cards_affecting_ratios(),
    )
    return wallet, warnings


def apply_fx_overrides(args, console: Console) -> None:
    """Apply --fx overrides and record their provenance as user_supplied."""
    for raw in args.fx or []:
        if "=" not in raw:
            raise ValueError(f"--fx expects CURRENCY=RATE, got {raw!r}.")
        cur, _, rate = raw.partition("=")
        try:
            config.set_fx_rate(cur.strip(), float(rate.strip()))
        except ValueError as e:
            raise ValueError(f"--fx {raw!r}: {e}") from None
        console.print(
            f"[green]FX override: 1 {cur.strip().upper()} = {float(rate):.4f} USD "
            f"(provenance: user_supplied)[/green]"
        )


def parse_transfer_date(args) -> date:
    if not args.transfer_date:
        return config.default_transfer_date()
    return datetime.strptime(args.transfer_date, "%Y-%m-%d").date()


def load_ratio_manager() -> RatioManager:
    return RatioManager(
        DATA_DIR / "ratios.csv",
        DATA_DIR / "bonuses.csv",
        DATA_DIR / "programs.yaml",
    )


def resolve_fixture(name: str) -> Path:
    p = Path(name)
    if p.exists():
        return p
    candidate = FIXTURE_DIR / name
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f"Trip fixture not found: {name}")


def build_live(args, console: Console):
    """
    Assemble the live-mode options, the response cache and the client.

    Returns (client, opts, cache). `--live` REQUIRES `--trip-fixture`: live mode
    replaces a leg's points candidates inside a trip, and there is no trip
    without a fixture. Raises ValueError with an actionable message otherwise.
    """
    from src.live_trip import LiveOptions
    from src.response_cache import ResponseCache

    # v5 STEP 6. LIVE IS THE DEFAULT; OFFLINE IS THE EXCEPTION.
    #
    # Until v5 a plain `--trip-fixture` run scored the fixture's Google badges
    # and printed a percentage, and going live was opt-in - which is backwards,
    # because live is the actual mode of use and the badge is the fallback. The
    # flip means a run with no network can no longer ARRIVE at a badge number by
    # accident: it fails loudly (exit 3) instead. Auto-detecting the absence of
    # a network and quietly falling back would be the failure-as-finding class
    # with a different subject, so `--offline` is an explicit opt-out.
    #
    # `--live` remains accepted and is now a NO-OP, so every existing script and
    # every line of the README keeps working and keeps meaning what it said.
    if getattr(args, "offline", False):
        return None, LiveOptions(live=False), None

    if not args.trip_fixture:
        raise ValueError(
            "--live requires --trip-fixture. Live trip mode swaps each FLIGHT "
            "leg's points candidates for real Seats.aero awards inside a trip, "
            "so there has to be a trip. For a single route use "
            "--origin/--destination/--date instead."
        )

    cache = ResponseCache(
        cache_dir=config.CACHE_DIR,
        snapshot_dir=Path(args.snapshot_dir) if args.snapshot_dir else config.SNAPSHOT_DIR,
        ttl_seconds=(
            config.CACHE_TTL_SECONDS if args.cache_ttl is None else args.cache_ttl
        ),
    )
    opts = LiveOptions(
        live=True,
        flex_days=max(int(args.flex_days or 0), 0),
        refresh=bool(args.refresh),
        cache_ttl=args.cache_ttl,
        # v5 STEP 6. --require-all-live IS THE DEFAULT. `--allow-badge-fallback`
        # is the opt-out, and it is the ONE thing that lets a badge answer a
        # question that was asked of the API and not answered. The result is
        # then labelled `badge_fallback` and is quotable only with that
        # qualifier, which travels on the same line as the number.
        require_all_live=not bool(getattr(args, "allow_badge_fallback", False)),
        allow_badge_fallback=bool(getattr(args, "allow_badge_fallback", False)),
        cache=cache,
        surcharges=default_table(),
        # The CLI ALWAYS engages the lookup; `None` is for direct callers only.
        trips_mode=getattr(args, "trips", None) or "auto",
        trips_cap=_trips_cap(args),
    )
    client = SeatsClient(getattr(args, "api_key", None))
    print_key_banner(console, client.key_resolution)
    print_relocation_banner(console)
    return client, opts, cache


def _trips_cap(args) -> int:
    """The validated --trips-cap, or the default. `trips_flag_problems` ran first."""
    from src.live_trip import DEFAULT_TRIPS_CAP

    raw = getattr(args, "trips_cap", None)
    return DEFAULT_TRIPS_CAP if raw is None else int(str(raw).strip())


def trips_flag_problems(args) -> list:
    """
    Every reason --trips / --trips-cap cannot be honoured on this invocation.

    A flag that would silently do nothing is refused (exit 1) rather than
    ignored: the lookup never runs offline, never runs on a replay (a replay
    reads what was recorded), and never runs on a single-route search.
    """
    from src.live_trip import TRIPS_CAP_MAX, TRIPS_CAP_MIN, TRIPS_MODES

    trips = getattr(args, "trips", None)
    cap = getattr(args, "trips_cap", None)
    named = [flag for flag, value in (("--trips", trips), ("--trips-cap", cap)) if value is not None]
    problems = []
    if trips is not None and trips not in TRIPS_MODES:
        problems.append(
            f"--trips {trips!r} is not one of {', '.join(TRIPS_MODES)}."
        )
    if cap is not None:
        text = str(cap).strip()
        value = None
        if text.lstrip("-").isdigit():
            try:
                # "²" is a digit to isdigit and not a number to int.
                value = int(text)
            except ValueError:
                value = None
        if value is None or not (TRIPS_CAP_MIN <= value <= TRIPS_CAP_MAX):
            problems.append(
                f"--trips-cap {cap!r} is not a whole number from {TRIPS_CAP_MIN} "
                f"to {TRIPS_CAP_MAX}."
            )
    if not named:
        return problems
    if getattr(args, "offline", False):
        problems.append(
            f"{' and '.join(named)} cannot be combined with --offline: an offline "
            f"run has no live awards, so there is nothing to look up. DROP "
            f"{' and '.join(named)}."
        )
    elif getattr(args, "from_snapshot", None):
        problems.append(
            f"{' and '.join(named)} cannot be combined with --from-snapshot: a "
            f"replay reads the lookups the live run RECORDED and asks nothing. "
            f"DROP {' and '.join(named)}."
        )
    elif not getattr(args, "trip_fixture", None) and not getattr(args, "new_trip", None):
        problems.append(
            f"{' and '.join(named)} cannot be combined with a single-route search: "
            f"it does not call the trips endpoint. Use --trip-fixture, or "
            f"`python -m src.trips_tools capture` for one award."
        )
    return problems


SINGLE_ROUTE_TRIPS_FOOTER = (
    "operating airline: NOT LOOKED UP - single-route search does not call the "
    "trips endpoint; use --trip-fixture or `python -m src.trips_tools capture`"
)


class ReplayRefused(ValueError):
    """A manifest that cannot be replayed. Nothing is scored. Exit 1."""


def build_replay(args, console: Console, fixture):
    """
    Assemble the replay transport, verify EVERYTHING, and compute the hash.

    Every refusal happens here, BEFORE a single leg is scored. A manifest that
    is missing a snapshot, whose bytes do not hash to what the manifest says, or
    which does not cover every queryable leg in this trip, produces no number at
    all - not a partial one with a warning. A partially-replayed margin printed
    beside a hash is an irreproducible number wearing a certificate.

    `LiveOptions.cache` is None on this path STRUCTURALLY, not by a flag check.
    A replay that appended to the manifest it is replaying would be a file
    writing itself, and the hash of the input would change while it is read.
    """
    from src import snapshot_replay
    from src.live_trip import LiveOptions

    manifest_path = Path(args.from_snapshot)
    snapshot_dir = manifest_path.parent

    try:
        rows = snapshot_replay.parse_manifest(manifest_path)
    except snapshot_replay.ManifestError as e:
        raise ReplayRefused(str(e)) from None

    selection = snapshot_replay.select_replay_set(rows, fixture.id)
    # FINDING H-1. The legs' ROUTES AND DATES travel with their ids, so a row
    # can be checked against the question it is about to answer. Passing ids
    # alone is what let a manifest captured for SFO->MAD score MRY->MAD.
    queryable = [
        snapshot_replay.LegQuery(leg.id, leg.origin, leg.destination, leg.date)
        for leg in fixture.legs
        if leg.kind == "flight" and leg.origin and leg.destination
    ]
    problems = (
        list(selection.problems)
        + snapshot_replay.verify(selection.selected, snapshot_dir)
        + snapshot_replay.verify_covers_legs(selection.selected, queryable)
    )
    # The recorded itinerary lookups, held to the same no-partial-replay rule.
    trips = snapshot_replay.load_trips_replay_set(
        snapshot_dir, fixture.id, leg_ids=[q.leg_id for q in queryable]
    )
    problems += trips.problems
    if problems:
        trips_hint = (
            "A trips snapshot that is missing, tampered with or empty refuses the "
            "whole replay too. Re-fetch that lookup live, or delete its row from "
            "trips_endpoint/MANIFEST.md - the lookup then replays as NOT RECORDED. "
            + snapshot_replay.HASH_CHANGE_NOTE
            + " "
            if trips.problems
            else ""
        )
        raise ReplayRefused(
            f"THIS MANIFEST CANNOT BE REPLAYED and NOTHING has been scored.\n"
            + "\n".join(f"  - {p.render()}" for p in problems)
            + "\n"
            "There is no partial-replay mode. A percentage printed next to a "
            "hash that covers only part of the run would look more trustworthy "
            "than one printed next to nothing. Re-fetch the missing legs live, "
            "or replay a manifest whose snapshots are all present and intact. "
            + trips_hint
            + "The manifest is NOT being updated to match the files."
        )

    # H-1: the certificate covers the ITINERARY as well as the bytes, so two
    # different trips can never print the same hash. Trips rows are hashed only
    # when there are any, so a trips-less manifest keeps its hash exactly.
    manifest_hash = snapshot_replay.manifest_hash(
        selection.selected,
        snapshot_dir,
        itinerary=queryable,
        trip_id=fixture.id,
        trips_rows=trips.selected,
        trips_dir=trips.directory,
    )
    transport = snapshot_replay.SnapshotTransport(
        selection.selected,
        snapshot_dir,
        manifest_hash,
        trips_rows=trips.selected if trips.manifest_path else None,
        trips_dir=trips.directory,
    )
    opts = LiveOptions(
        live=True,
        flex_days=0,
        refresh=False,
        cache_ttl=None,
        # v5 STEP 6: the default here too. A replay satisfies it (provenance
        # `snapshot`) as long as every replayed leg answered at capture time.
        require_all_live=not bool(getattr(args, "allow_badge_fallback", False)),
        # STRUCTURAL, not a flag check: there is no cache object to write to.
        cache=None,
        surcharges=default_table(),
        allow_badge_fallback=bool(getattr(args, "allow_badge_fallback", False)),
        # A replay reads the RECORDED lookups and never asks the network. "auto"
        # decides which awards would have been looked up; a recorded row is
        # used whatever that says, because it is evidence the live run had.
        trips_mode="auto",
    )
    return transport, opts, selection, manifest_hash


RELOCATION_VARS = (
    "POINTS_OPTIMIZER_ENV_FILE",
    "POINTS_OPTIMIZER_CACHE_DIR",
    "POINTS_OPTIMIZER_SNAPSHOT_DIR",
)


def print_relocation_banner(console: Console) -> None:
    """
    Name every POINTS_OPTIMIZER_* relocation that is in effect. They exist so the
    test suite can keep children away from real state; set in a user's shell,
    they silently change which key file a run reads and where it caches and
    archives - so a run that is affected by one says so.
    """
    import os

    for var in RELOCATION_VARS:
        if os.environ.get(var):
            console.print(
                f"[bold yellow]  {var} is set: {os.environ[var]} (overrides the "
                f"default location for this run)[/bold yellow]"
            )


def print_replay_banner(console: Console, selection, manifest_hash, transport) -> None:
    """The selection rule, the hash, and the parser-version pair. Always."""
    from src import snapshot_replay

    at_capture = ", ".join(snapshot_replay.parser_versions(selection.selected))
    now = transport.current_parser_version
    console.print(
        f"\n[bold cyan]REPLAY FROM COMMITTED SNAPSHOTS - nothing was asked of "
        f"Seats.aero on this run.[/bold cyan]"
    )
    console.print(f"[cyan]  manifest {manifest_hash}[/cyan]")
    console.print(f"[cyan]  selection: {selection.describe()}[/cyan]")
    console.print(
        f"[cyan]  parser {at_capture} at capture / {now} now[/cyan]"
    )
    if at_capture != now:
        # A parser upgrade REPARSES yesterday's bytes, which is the property
        # storing raw pages exists for. Refusing a stale snapshot would delete
        # it. So it replays - loudly, with both versions named.
        console.print(
            f"[bold red]  REPARSED UNDER A DIFFERENT PARSER VERSION: these bytes "
            f"were captured under {at_capture} and are being read by {now}. The "
            f"award counts below may differ from the run that wrote this "
            f"manifest. The manifest hash is UNCHANGED by a reparse - it is over "
            f"bytes, not over awards.[/bold red]"
        )
    if selection.superseded:
        console.print(
            f"[dim]  {len(selection.superseded)} superseded row(s) not replayed: "
            + ", ".join(r.describe() for r in selection.superseded)
            + "[/dim]"
        )
    from src import seats_trips

    trips_rows = list(getattr(transport, "trips_rows", []) or [])
    if not getattr(transport, "has_trips_manifest", False):
        console.print(
            "[cyan]  itinerary lookups: none recorded (no trips_endpoint/MANIFEST.md); "
            "every lookup this run would make reads NOT RECORDED[/cyan]"
        )
    else:
        console.print(
            f"[cyan]  itinerary lookups: {len(trips_rows)} recorded row(s) in "
            f"trips_endpoint/MANIFEST.md[/cyan]"
        )
    stale = sorted(
        {
            r.parser_version_display
            for r in trips_rows
            if r.parser_version_display != seats_trips.TRIPS_PARSER_VERSION
        }
    )
    if stale:
        console.print(
            f"[bold red]  TRIPS LOOKUPS REPARSED: {len(trips_rows)} recorded trips "
            f"row(s) include ones captured under {', '.join(stale)} and are read by "
            f"{seats_trips.TRIPS_PARSER_VERSION} now. Their operating-airline "
            f"lines may differ from the run that recorded them.[/bold red]"
        )


# ---------------------------------------------------------------------------
# Structured runs. THE CLI AND THE LOCAL UI READ THE SAME OBJECTS.
# ---------------------------------------------------------------------------
#
# `score_fixture` computes everything a trip report needs and prints only the
# banners that come before the first table; `print_fixture_report` prints the
# rest FROM THE SAME OBJECT. The UI (src/ui/) receives that object through a
# `sink` and renders it, so every refusal, UNKNOWN and provenance label it shows
# comes from the run the terminal printed - not from a second computation that
# could disagree with it. tests/test_cli_output_unchanged.py pins the bytes.


@dataclass
class FixtureRun:
    """Everything the trip report reads. Built BEFORE any post-scoring print."""

    args: Any
    fixture: Any
    fixture_path: Path
    wallet: Any
    wallet_warnings: List[str]
    transfer_date: date
    ratios: Any
    live_opts: Any
    cache: Any
    client: Any  # None offline; a SnapshotTransport on a replay
    replay_selection: Any
    manifest_hash: str
    outcomes: List[Any]
    results: List[Any]
    totals: Dict[str, Any]
    residue: Dict[str, Any]
    # 0 / 3 / 4, computed exactly as the report returns it.
    exit_code: int
    # `KeyResolution.source` (+ path) when a key was resolved. NEVER the key or
    # its mask: this object crosses into the UI.
    key_source: Optional[str] = None


@dataclass
class SearchRun:
    """Everything the single-route search report reads."""

    args: Any
    trip: Any
    date_range: Any
    wallet: Any
    wallet_warnings: List[str]
    strategies: List[Any]
    # What optimize() actually saw (client.last_search_awards), NOT a second
    # search: re-asking would reset the coverage line on a run that made one.
    awards: List[Any]
    last_error: Optional[str]
    pagination_note: str
    # The budget counter's movement across optimize(): calls this run spent.
    calls_spent: int
    key_source: Optional[str]
    exit_code: int
    client: Any = None


@dataclass
class RunRefusal:
    """A run that scored NOTHING (exit 1 or 2). `message` is the exact text the
    terminal printed, markup removed."""

    exit_code: int
    kind: str  # wallet|replay_refused|value|file|trips_flags|conflict|key|search_args|optimization
    message: str


def _plain(*markup: str) -> str:
    """The text rich prints for these markup strings, one line per string."""
    from rich.text import Text

    return "\n".join(Text.from_markup(m).plain for m in markup)


def _refuse(console: Console, sink, code: int, kind: str, *markup: str) -> None:
    """Print these markup strings exactly as before, and record the refusal.
    The caller then returns `code` as a LITERAL, so the exit-code contract stays
    discoverable from source (tests/test_exit_codes_are_documented.py)."""
    for m in markup:
        console.print(m)
    if sink is not None:
        sink.append(RunRefusal(exit_code=code, kind=kind, message=_plain(*markup)))


def _key_source(client) -> Optional[str]:
    resolution = getattr(client, "key_resolution", None)
    if resolution is None:
        return None
    return (
        f"{resolution.source} {resolution.path}" if resolution.path else resolution.source
    )


def score_fixture(args, console: Console) -> FixtureRun:
    """
    Score a multi-leg trip fixture: every leg, cash vs points. Prints ONLY the
    banners that come before scoring (key, relocation, header, wallet, FX,
    valuation, flags, replay banner). Everything after is `print_fixture_report`.
    """
    from src.live_trip import annotate_live_verdicts, apply_live

    path = resolve_fixture(args.trip_fixture)
    fixture = load_trip_fixture(path)
    ratios = load_ratio_manager()

    apply_fx_overrides(args, console)
    wallet, wallet_warnings = build_wallet(args, ratios)
    transfer_date = parse_transfer_date(args)
    replay_selection = None
    manifest_hash = ""
    if getattr(args, "from_snapshot", None):
        print_key_banner(
            console, None, note="--from-snapshot replays committed bytes"
        )
        client, live_opts, replay_selection, manifest_hash = build_replay(
            args, console, fixture
        )
        cache = None
    else:
        client, live_opts, cache = build_live(args, console)
    live_opts.trip_id = fixture.id

    console.print(f"\n[bold]{fixture.name}[/bold]")
    console.print(f"[dim]{fixture.description}[/dim]")
    console.print(f"[dim]Source: {fixture.source}[/dim]")

    print_wallet_banner(wallet, wallet_warnings, transfer_date, ratios, console)
    print_fx_banner(console)
    print_valuation_banner(args.valuation_cpp, console)

    if fixture.trip_level_flags:
        console.print("\n[bold red]Data problems flagged, NOT silently fixed[/bold red]")
        for flag in fixture.trip_level_flags:
            console.print(f"  [red]- {flag}[/red]")

    # THE SEAM. apply_live swaps points candidates and touches nothing else; the
    # fixture it returns goes to the SAME evaluate_trip as always.
    outcomes = []
    if live_opts.live:
        if replay_selection is not None:
            print_replay_banner(console, replay_selection, manifest_hash, client)
        fixture, outcomes = apply_live(fixture, client, live_opts)

    results = evaluate_trip(
        legs=fixture.legs,
        ratios_manager=ratios,
        wallet=wallet,
        transfer_date=transfer_date,
        surcharges=default_table(),
        valuation_cpp=args.valuation_cpp,
        transfer_increment=args.transfer_increment,
        max_stranded_points=args.max_stranded_points,
        show_alternatives=True,
    )

    if live_opts.live:
        annotate_live_verdicts(results)

    # THE WALLET IS PASSED IN so the trip-level balance ceiling is actually
    # checked (finding C-3). Without it `trip_totals` reports that the check did
    # not run - it never reports a pass it did not perform.
    totals = trip_totals(results, wallet=wallet)

    # --require-all-live WITHHOLDS the percentage rather than letting a
    # mixed-provenance number be quoted. The per-leg results are unaffected and
    # are still printed - what is withheld is only the single number somebody
    # would copy out of here without its qualifier.
    #
    # v5: `snapshot` satisfies it too, and ONLY because a snapshot exists only
    # for a leg the API answered live at capture time - so "every leg was live"
    # is true of the run being reproduced. It is the plan's third
    # most-likely-wrong decision (§10.3): the default whose purpose is to stop a
    # stale number being quoted is satisfied by bytes of arbitrary age, and the
    # per-leg `_replay_clause()` naming the capture date is the only thing
    # standing between a reader and "this is all live". Recorded, not hidden.
    withheld = live_opts.require_all_live and totals.get("margin_provenance") not in (
        "live",
        "snapshot",
    )
    # A trip with a flight leg for 2+ travellers has a flight that was NOT
    # priced. "Beats cash by 0.00%" would then be "could not price" reported as a
    # finding of zero value - withheld instead, exit 3, and the reason says why.
    party = totals.get("legs_party_pricing_unverified_ids") or []
    if party:
        reasons = []
        if withheld:
            # Both reasons are named: the provenance one does not go away because
            # a second reason arrived.
            reasons.append(
                f"--require-all-live and the margin's provenance is "
                f"'{totals.get('margin_provenance')}', not live"
            )
        reasons.append(
            f"flight leg(s) {', '.join(party)} are for 2+ travellers and were not "
            f"priced (award prices are per seat)"
        )
        withheld = True
        totals["margin_withheld_reason"] = "; AND ".join(reasons)
    totals["margin_withheld"] = withheld

    # THE HASH RIDES WITH THE PERCENTAGE. `print_trip_totals` puts it in the
    # same cell, so a margin from --from-snapshot can never be rendered without
    # the bytes it is quoted against.
    if manifest_hash:
        from src import snapshot_replay

        totals["manifest_hash"] = manifest_hash
        totals["manifest_snapshots"] = len(replay_selection.selected)
        totals["manifest_parser_at_capture"] = ", ".join(
            snapshot_replay.parser_versions(replay_selection.selected)
        )
        totals["manifest_parser_now"] = client.current_parser_version

    exit_code = fixture_exit_code(totals, withheld)

    return FixtureRun(
        args=args,
        fixture=fixture,
        fixture_path=path,
        wallet=wallet,
        wallet_warnings=wallet_warnings,
        transfer_date=transfer_date,
        ratios=ratios,
        live_opts=live_opts,
        cache=cache,
        client=client,
        replay_selection=replay_selection,
        manifest_hash=manifest_hash,
        outcomes=outcomes,
        results=results,
        totals=totals,
        residue=trip_residue(results, wallet),
        exit_code=exit_code,
        key_source=None if replay_selection is not None else _key_source(client),
    )


def fixture_exit_code(totals, withheld: bool) -> int:
    """The exit status of a scored trip: 4, 3 or 0."""
    if not totals.get("trip_funding_executable", True):
        # Non-zero, and a DIFFERENT code from the --require-all-live withholding
        # below: "the number is not quotable" and "the plan cannot be executed"
        # are different failures and a script must be able to tell them apart.
        return 4
    if withheld:
        # Non-zero so a script cannot mistake a withheld margin for a quoted one.
        return 3
    return 0


@dataclass
class FooterLine:
    """One line printed after the residue table: styled segments, in order."""

    segments: List[Tuple[str, str]]  # (text, rich style or "")
    blank_before: bool = False

    def markup(self) -> str:
        body = "".join(f"[{st}]{t}[/{st}]" if st else t for t, st in self.segments)
        return ("\n" if self.blank_before else "") + body

    @property
    def text(self) -> str:
        return "".join(t for t, _ in self.segments)


def fixture_footer_lines(results, totals) -> List[FooterLine]:
    """
    The lines printed after the residue table, in order. The UI shows these
    verbatim; the terminal prints them through `FooterLine.markup`.
    """
    lines: List[FooterLine] = []
    # H-4. THE TWO REASONS A LEG HAS NO POINTS PATH ARE DIFFERENT FACTS and used
    # to share one line. "(no partner exists)" is a claim about partnerships; an
    # absent capture is a claim about our own inputs, and printing the first when
    # the second is true is the house failure on a new surface.
    no_path = [
        r.leg.id
        for r in results
        if r.verdict == "cash (no points path)" and r.points_absence != "never_priced"
    ]
    never_priced = [
        r.leg.id for r in results if r.points_absence == "never_priced"
    ]
    if no_path:
        lines.append(FooterLine(
            [("Legs with NO points path at all (no partner exists):", "bold"),
             (f" {', '.join(no_path)}", "")],
            blank_before=True,
        ))
    if never_priced:
        lines.append(FooterLine(
            [("Legs whose points side was NEVER PRICED (no award price captured "
              "and none fetched - this says NOTHING about whether a partner "
              "covers them):", "bold yellow"),
             (f" {', '.join(never_priced)}", "")],
            blank_before=True,
        ))
    unpriced = [r.leg.id for r in results if r.verdict == "cash (points unpriced)"]
    if unpriced:
        lines.append(FooterLine(
            [("Legs where a partner exists but no award price was captured:", "bold"),
             (f" {', '.join(unpriced)}", "")],
        ))
    unknown = totals.get("legs_surcharge_unknown_ids") or []
    if unknown:
        lines.append(FooterLine(
            [("Legs where a points path exists but its carrier-imposed surcharge "
              "is UNKNOWN (NOT $0):", "bold yellow"),
             (f" {', '.join(unknown)}", "")],
        ))
    taxes_unknown = totals.get("legs_taxes_unknown_ids") or []
    if taxes_unknown:
        lines.append(FooterLine(
            [("Legs where an award's TAXES are UNKNOWN (NOT $0) - Seats.aero sent "
              "no usable figure:", "bold yellow"),
             (f" {', '.join(taxes_unknown)}", "")],
        ))
    if not totals.get("trip_funding_executable", True):
        lines.append(FooterLine(
            [("THE RECOMMENDATION ABOVE CANNOT BE EXECUTED FROM YOUR BALANCE.",
              "bold red"),
             (" ", ""),
             (f"{totals.get('trip_funding_note', '')}", "red")],
            blank_before=True,
        ))
    return lines


def print_fixture_report(run: FixtureRun, args, console: Console) -> int:
    """Everything after the pre-scoring banners, printed from `run`. Returns
    `run.exit_code`."""
    from src.formatter import print_live_banner, print_live_leg_detail

    if run.live_opts.live:
        print_live_banner(run.outcomes, run.live_opts, run.cache, console)
        for leg_id, row_ver, meta_ver in getattr(run.client, "parser_version_disagreements", []):
            console.print(
                f"[bold red]  PARSER VERSION DISAGREEMENT on {leg_id}: the manifest "
                f"row says {row_ver} but the snapshot itself says it was captured "
                f"under {meta_ver}; it is being read by "
                f"{run.client.current_parser_version}. Treat this leg as REPARSED.[/bold red]"
            )

    results = run.results
    console.print()
    print_leg_results(results, console)
    print_leg_detail(results, console)
    print_live_leg_detail(results, console)
    if args.show_alternatives:
        print_alternatives(results, console)

    console.print()
    print_trip_totals(run.totals, label=run.fixture.name, console=console)
    print_residue_report(run.residue, console)

    for line in fixture_footer_lines(results, run.totals):
        console.print(line.markup())
    return run.exit_code


def run_fixture(args, console: Console, sink=None) -> int:
    """Score a multi-leg trip fixture: every leg, cash vs points."""
    run = score_fixture(args, console)
    if sink is not None:
        sink.append(run)
    return print_fixture_report(run, args, console)


def unfundable_reason(award) -> str:
    """Why a returned award cannot be funded from the wallet. Never a claim
    about award space."""
    if getattr(award, "indirect_ur_path", ""):
        return f"reachable only INDIRECTLY - {award.indirect_ur_path}"
    if not (award.program or "").strip():
        return "the response named no program it could be attributed to"
    if award.ur_transferable is False:
        return "not a transfer partner of any currency you hold"
    if award.ur_transferable is True:
        return (
            "a transfer partner, but no fundable path: the balance "
            "(or the stranded-points limit) cannot cover it"
        )
    return "no fundable transfer path from the wallet above"


def search_route(args, console: Console):
    """
    The single-route search, up to and including the call. Prints the wallet
    warnings and the key/relocation banners. Returns a SearchRun, or a
    RunRefusal (already printed) when the key or the optimizer failed.
    """
    ratios_for_wallet = load_ratio_manager()
    apply_fx_overrides(args, console)
    wallet, wallet_warnings = build_wallet(args, ratios_for_wallet)
    for warning in wallet_warnings:
        console.print(f"[yellow]{warning}[/yellow]")

    date_range = parse_date_range(args.date)

    trip = Trip(
        origin=args.origin,
        destination=args.destination,
        date_range=date_range,
        balances=dict(wallet.balances),
        cards_held=list(wallet.cards),
        num_passengers=args.passengers,
    )

    try:
        seats_client = SeatsClient(getattr(args, "api_key", None))
    except ValueError as e:
        m = f"[red]Error: {e}[/red]"
        console.print(m)
        return RunRefusal(exit_code=1, kind="key", message=_plain(m))
    print_key_banner(console, seats_client.key_resolution)
    print_relocation_banner(console)

    ratios = load_ratio_manager()

    before = SeatsClient._budget_remaining()
    try:
        results = optimize(
            trip=trip,
            seats_client=seats_client,
            ratios_manager=ratios,
            valuation_cpp=args.valuation_cpp,
            max_results=args.max_results,
            transfer_increment=args.transfer_increment,
            max_stranded_points=args.max_stranded_points,
        )
    except Exception as e:
        m = f"[red]Error during optimization: {e}[/red]"
        console.print(m)
        return RunRefusal(exit_code=1, kind="optimization", message=_plain(m))
    spent = max(before - SeatsClient._budget_remaining(), 0)

    return SearchRun(
        args=args,
        trip=trip,
        date_range=date_range,
        wallet=wallet,
        wallet_warnings=wallet_warnings,
        strategies=results,
        awards=list(getattr(seats_client, "last_search_awards", None) or []),
        last_error=getattr(seats_client, "last_error", None),
        pagination_note=getattr(seats_client, "last_pagination_note", "") or "",
        calls_spent=spent,
        key_source=_key_source(seats_client),
        exit_code=0,
        client=seats_client,
    )


def print_search_report(run: SearchRun, args, console: Console) -> int:
    """Everything the search prints after the call, from `run`."""
    results = run.strategies
    date_range = run.date_range
    console.print(f"\nRoute: {args.origin} -> {args.destination}")
    console.print(f"Dates: {date_range.from_date} to {date_range.to_date}")
    console.print(f"Passengers: {args.passengers}")
    for line in run.wallet.describe():
        console.print(line)
    print_fx_banner(console)

    if not results:
        if run.last_error:
            console.print(
                f"\n[red]Seats.aero could NOT be reached: {run.last_error}[/red]"
            )
            console.print(
                "[red]This is an API failure, NOT a finding of no award "
                "availability. No conclusion about award space can be drawn "
                "from this run.[/red]"
            )
        else:
            # optimize() returns STRATEGIES - awards it could fund. An empty list
            # is a statement about THIS WALLET unless the API also returned no
            # awards. Reading it as "no award availability" turned a Qatar award
            # (reachable only indirectly) and an American award (not a UR
            # partner) into a claim that there was nothing on the route.
            # What optimize() actually saw, recorded by it - NOT a second search:
            # re-asking would reset the client's coverage line ("no API call
            # made") on a run that made one.
            awards = run.awards
            if not awards:
                console.print(
                    "\n[yellow]Seats.aero returned no award availability for this "
                    "route and date range.[/yellow]"
                )
            else:
                console.print(
                    f"\n[yellow]{none_fundable_header(len(awards))}[/yellow]"
                )
                for a in awards[:20]:
                    console.print(
                        f"  [dim]{a.program or '(program not named)'} {a.award_type} "
                        f"{a.cost:,} on {a.date}: {unfundable_reason(a)}[/dim]"
                    )

    # Which pagination path the client took, printed on EVERY run, empty result
    # or not. Seats.aero's cached search is known to paginate and the one
    # captured response was truncated before the pagination fields, so "this is
    # page one" and "this is everything" are not yet distinguishable from the
    # outside. Returning page one silently as the whole result set is an
    # undercount that would look exactly like a finding.
    note = run.pagination_note
    if note:
        style = "red" if "INCOMPLETE" in note else "dim"
        console.print(f"[{style}]Seats.aero result coverage: {note}[/{style}]")
    console.print(f"[yellow]{SINGLE_ROUTE_TRIPS_FOOTER}[/yellow]")

    if int(args.passengers or 1) > 1 and results:
        if args.html:
            console.print(
                "[bold yellow]--html was NOT written: the export is a "
                "recommendation, and a per-seat list is not one for "
                f"{args.passengers} passengers.[/bold yellow]"
            )
        # Every price below is for ONE seat. Ranking and summarising them as the
        # answer for a party would repeat the multi-traveller false win.
        console.print(
            f"\n[bold yellow]PRICED FOR ONE SEAT. You asked for {args.passengers} "
            f"passengers; multi-traveller award pricing is not modelled (points, "
            f"taxes and seat availability are all per seat here). The list below "
            f"is per-seat information, NOT a recommendation for the party, and no "
            f"top strategy is named.[/bold yellow]"
        )
        print_strategies(results, args.valuation_cpp, console)
        return run.exit_code
    print_strategies(results, args.valuation_cpp, console)
    print_summary(results, human_cost=args.human_cost, console=console)

    if args.html:
        export_html(results)
    return run.exit_code


def none_fundable_header(n_awards: int) -> str:
    """The search's none-fundable sentence. Shared by the terminal and the UI."""
    return (
        f"Seats.aero returned {n_awards} award(s) for this "
        f"route and date range, and NONE of them can be funded from the "
        f"wallet above. That is a finding about THIS WALLET - its "
        f"transfer partners and its balances - NOT about award "
        f"space:"
    )


def run_search(args, console: Console, sink=None) -> int:
    """Live award search for a single route via Seats.aero."""
    if not (args.origin and args.destination and args.date):
        _refuse(
            console, sink, 1, "search_args",
            "[red]Error: --origin, --destination and --date are all required "
            "for a live search (or use --trip-fixture).[/red]",
        )
        return 1

    run = search_route(args, console)
    if sink is not None:
        sink.append(run)
    if isinstance(run, RunRefusal):
        return run.exit_code
    return print_search_report(run, args, console)


def dispatch(args, console: Console, sink=None) -> int:
    """
    Today's CLI after argument parsing: every flag conflict, refusal and exit
    code. `sink`, when given, receives exactly one FixtureRun, SearchRun or
    RunRefusal per invocation that reaches one - the local UI's only view of a
    run, so the UI never re-implements a rule this function applies.
    """
    try:
        trips_problems = trips_flag_problems(args)
        if trips_problems:
            _refuse(
                console, sink, 1, "trips_flags",
                "[red]Error: the operating-airline lookup flags cannot be honoured.[/red]",
                *[f"[red]  - {problem}[/red]" for problem in trips_problems],
            )
            return 1
        if getattr(args, "offline", False) and getattr(args, "live", False):
            _refuse(
                console, sink, 1, "conflict",
                "[red]Error: --offline and --live are mutually exclusive.[/red]\n"
                "[red]One says score the fixture's own prices with no transport; "
                "the other says ask Seats.aero. This is a usage error rather "
                "than a precedence rule, because a precedence rule would mean "
                "one of the two flags you typed did nothing.[/red]",
            )
            return 1
        if getattr(args, "new_trip", None):
            if getattr(args, "from_snapshot", None):
                _refuse(
                    console, sink, 1, "conflict",
                    "[red]Error: --new-trip cannot be combined with "
                    "--from-snapshot.[/red]\n"
                    "[red]A trip built one second ago has no snapshots, and "
                    "replaying somebody else's manifest against it would hash "
                    "the wrong trip - a percentage printed beside a certificate "
                    "for a different itinerary. Build it, then run it --live "
                    "once to write a manifest of its own.[/red]",
                )
                return 1
            code, path = run_new_trip(args, console)
            if code != 0:
                return code
            if not getattr(args, "live", False):
                return code
            # THE CHAIN IS NOT A SPECIAL CASE. The fixture that was just written
            # is handed to the ORDINARY live path by filename, so the scoring
            # half is identical to running --trip-fixture --live by hand.
            args.trip_fixture = str(path)
            return run_fixture(args, console, sink)
        # Checked BEFORE dispatch. Handled inside run_search this would have
        # produced "--origin, --destination and --date are all required", which
        # answers a question the user did not ask and hides the real one.
        if getattr(args, "live", False) and not args.trip_fixture:
            _refuse(
                console, sink, 1, "conflict",
                "[red]Error: --live requires --trip-fixture.[/red]\n"
                "[red]Live trip mode swaps each FLIGHT leg's points candidates "
                "for real Seats.aero awards inside a trip, so there has to be a "
                "trip. Cash is never touched either way.[/red]\n"
                "[yellow]  For a whole trip:   python -m src.main --trip-fixture "
                "trip_b_europe.json --live[/yellow]\n"
                "[yellow]  For one route:      python -m src.main --origin SFO "
                "--destination MAD --date 2027-01-15[/yellow]",
            )
            return 1
        # v5 STEP 3. --from-snapshot NAMES A TRANSPORT, and so do --live and
        # --offline. Two transports is a usage error, not a precedence rule:
        # deciding which one wins silently would mean the flag the user typed
        # did not determine where the bytes came from.
        #
        # Checked here, before dispatch, alongside the --live check above, so
        # NOTHING is scored on a run whose transport is ambiguous.
        if getattr(args, "from_snapshot", None):
            conflicts = []
            if getattr(args, "live", False):
                conflicts.append(
                    "--live asks Seats.aero and --from-snapshot reads committed "
                    "bytes. DROP --live to replay, or drop --from-snapshot to go "
                    "live and write a new manifest."
                )
            if getattr(args, "offline", False):
                conflicts.append(
                    "--offline scores no transport at all and --from-snapshot is "
                    "a transport. DROP --offline to replay, or drop "
                    "--from-snapshot to score the fixture's own prices."
                )
            if getattr(args, "refresh", False):
                conflicts.append(
                    "--refresh names cache behaviour that does not exist on the "
                    "replay path: a replay reads a file and never consults or "
                    "writes the cache. DROP --refresh."
                )
            if getattr(args, "cache_ttl", None) is not None:
                conflicts.append(
                    "--cache-ttl names cache behaviour that does not exist on the "
                    "replay path. DROP --cache-ttl."
                )
            if int(getattr(args, "flex_days", 0) or 0) != config.DEFAULT_FLEX_DAYS:
                conflicts.append(
                    "--flex-days changes the request key, so it would ask for a "
                    "snapshot that was never captured. Silently ignoring a flag "
                    "that changes the answer is this project's failure mode in "
                    "miniature. DROP --flex-days."
                )
            if conflicts:
                _refuse(
                    console, sink, 1, "conflict",
                    "[red]Error: --from-snapshot cannot be combined with the "
                    "flags below.[/red]",
                    *[f"[red]  - {c}[/red]" for c in conflicts],
                )
                return 1
            if not args.trip_fixture:
                _refuse(
                    console, sink, 1, "conflict",
                    "[red]Error: --from-snapshot requires --trip-fixture. A "
                    "manifest is replayed AGAINST a trip - the hash is over the "
                    "rows that cover that trip's legs, and without a trip there "
                    "is nothing to cover.[/red]",
                )
                return 1
        if args.trip_fixture:
            return run_fixture(args, console, sink)
        return run_search(args, console, sink)
    except WalletError as e:
        # A missing or malformed wallet is a hard stop, not a reason to assume
        # one. Exits non-zero so a script cannot mistake it for a run.
        _refuse(console, sink, 2, "wallet", f"[red]Wallet error: {e}[/red]")
        return 2
    except ReplayRefused as e:
        # Its own clause so the message prints as the multi-line refusal it is,
        # without an "Error: " prefix flattening it into one sentence.
        #
        # ESCAPED. The problem kinds are rendered in square brackets and rich
        # reads those as markup tags - so an unescaped print SILENTLY DELETED
        # the name of every problem, leaving a refusal that would not say what
        # was wrong with the manifest.
        from rich.markup import escape

        _refuse(
            console, sink, 1, "replay_refused", f"[bold red]{escape(str(e))}[/bold red]"
        )
        return 1
    except ValueError as e:
        # The CLI prints every ValueError the same way; the kind only tells the
        # UI which of its own states to show (no key -> LIVE UNAVAILABLE).
        kind = "key" if str(e).startswith("No Seats.aero API key found") else "value"
        _refuse(console, sink, 1, kind, f"[red]Error: {e}[/red]")
        return 1
    except FileNotFoundError as e:
        _refuse(console, sink, 1, "file", f"[red]Error: {e}[/red]")
        return 1


def main() -> int:
    # Fixed width so the tables render identically in a terminal and in a
    # captured report file. Widened from 170 at v3: the per-leg table gained a
    # provenance column and 170 no longer fits it.
    return dispatch(build_parser().parse_args(), Console(width=190))


if __name__ == "__main__":
    import sys

    sys.exit(main())
