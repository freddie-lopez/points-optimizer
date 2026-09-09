"""CLI entry point for points transfer optimizer."""
import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

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
  # Score a real multi-leg trip fixture (no Seats.aero call needed)
  python -m src.main --trip-fixture trip_b_europe.json

  # Same, but constrained to a real UR balance
  python -m src.main --trip-fixture trip_b_europe.json --balance UR=160000

  # Live award search for a single route
  python -m src.main --origin SFO --destination LHR --date 2027-01-15

EXIT CODES (the single authoritative list; README.md quotes this one):
  0  Success. A margin was produced and, if a balance was given, the plan is
     executable from it.
  1  Error. Bad arguments, a missing or unreadable file, or an unhandled
     failure. NOTHING was scored.
  3  WITHHELD. --require-all-live was given and at least one leg did not come
     back live, so no margin is quoted. This is a REFUSAL TO ANSWER, not a
     finding of zero value.
  4  NOT EXECUTABLE. A margin was produced, but the recommendation cannot be
     funded from the balance you supplied. The number is real; the plan is not
     actionable as printed.

  3 and 4 are deliberately different: "the number is not quotable" and "the
  plan cannot be executed" are different failures and a wrapping script must be
  able to tell them apart.
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
        "building a trip (v5) - writes a fixture that can ONLY be scored live"
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
        help="DEPRECATED shorthand for --balance UR=N. Kept for v0 compatibility.",
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
        "live trip mode (v3) - points from Seats.aero, cash still from captures"
    )
    live.add_argument(
        "--live",
        action="store_true",
        help=(
            "Score each FLIGHT leg of a trip fixture against real Seats.aero "
            "award availability. Requires --trip-fixture. CASH IS NEVER TOUCHED: "
            "screenshots remain the source of truth for cash and there is no "
            "cash-price API in scope. Hotels are unaffected - Seats.aero is "
            "flights-only."
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
            "WITHHOLD the trip margin entirely unless every flight leg with a "
            "points candidate came back live. For when a number is going to be "
            "quoted. Exits non-zero when it withholds."
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


def run_new_trip(args, console: Console) -> int:
    """
    Build a fixture from the flags, echo it, write it, and say what it can't do.

    Emits NO scored number. The one number it writes is the cash amount the
    user typed, echoed back before the write.
    """
    from src import trip_builder

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
    return 0


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

    if not getattr(args, "live", False):
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
        require_all_live=bool(args.require_all_live),
        cache=cache,
        surcharges=default_table(),
    )
    client = SeatsClient(getattr(args, "api_key", None))
    print_key_banner(console, client.key_resolution)
    return client, opts, cache


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
    queryable = [
        leg.id
        for leg in fixture.legs
        if leg.kind == "flight" and leg.origin and leg.destination
    ]
    problems = (
        list(selection.problems)
        + snapshot_replay.verify(selection.selected, snapshot_dir)
        + snapshot_replay.verify_covers_legs(selection.selected, queryable)
    )
    if problems:
        raise ReplayRefused(
            f"THIS MANIFEST CANNOT BE REPLAYED and NOTHING has been scored.\n"
            + "\n".join(f"  - {p.render()}" for p in problems)
            + "\n"
            "There is no partial-replay mode. A percentage printed next to a "
            "hash that covers only part of the run would look more trustworthy "
            "than one printed next to nothing. Re-fetch the missing legs live, "
            "or replay a manifest whose snapshots are all present and intact. "
            "The manifest is NOT being updated to match the files."
        )

    manifest_hash = snapshot_replay.manifest_hash(selection.selected, snapshot_dir)
    transport = snapshot_replay.SnapshotTransport(
        selection.selected, snapshot_dir, manifest_hash
    )
    opts = LiveOptions(
        live=True,
        flex_days=0,
        refresh=False,
        cache_ttl=None,
        require_all_live=bool(args.require_all_live),
        # STRUCTURAL, not a flag check: there is no cache object to write to.
        cache=None,
        surcharges=default_table(),
        allow_badge_fallback=bool(getattr(args, "allow_badge_fallback", False)),
    )
    return transport, opts, selection, manifest_hash


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


def run_fixture(args, console: Console) -> int:
    """Score a multi-leg trip fixture: every leg, cash vs points."""
    from src.formatter import print_live_banner, print_live_leg_detail
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
        print_live_banner(outcomes, live_opts, cache, console)

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

    console.print()
    print_leg_results(results, console)
    print_leg_detail(results, console)
    print_live_leg_detail(results, console)
    if args.show_alternatives:
        print_alternatives(results, console)

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

    console.print()
    print_trip_totals(totals, label=fixture.name, console=console)
    print_residue_report(trip_residue(results, wallet), console)

    no_path = [r.leg.id for r in results if r.verdict == "cash (no points path)"]
    if no_path:
        console.print(
            f"\n[bold]Legs with NO points path at all (no partner exists):[/bold] "
            f"{', '.join(no_path)}"
        )
    unpriced = [r.leg.id for r in results if r.verdict == "cash (points unpriced)"]
    if unpriced:
        console.print(
            f"[bold]Legs where a partner exists but no award price was "
            f"captured:[/bold] {', '.join(unpriced)}"
        )
    unknown = totals.get("legs_surcharge_unknown_ids") or []
    if unknown:
        console.print(
            f"[bold yellow]Legs where a points path exists but its carrier-imposed "
            f"surcharge is UNKNOWN (NOT $0):[/bold yellow] {', '.join(unknown)}"
        )

    if not totals.get("trip_funding_executable", True):
        # Non-zero, and a DIFFERENT code from the --require-all-live withholding
        # below: "the number is not quotable" and "the plan cannot be executed"
        # are different failures and a script must be able to tell them apart.
        console.print(
            f"\n[bold red]THE RECOMMENDATION ABOVE CANNOT BE EXECUTED FROM YOUR "
            f"BALANCE.[/bold red] [red]{totals.get('trip_funding_note', '')}[/red]"
        )
        return 4
    if withheld:
        # Non-zero so a script cannot mistake a withheld margin for a quoted one.
        return 3
    return 0


def run_search(args, console: Console) -> int:
    """Live award search for a single route via Seats.aero."""
    if not (args.origin and args.destination and args.date):
        console.print(
            "[red]Error: --origin, --destination and --date are all required "
            "for a live search (or use --trip-fixture).[/red]"
        )
        return 1

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
        console.print(f"[red]Error: {e}[/red]")
        return 1
    print_key_banner(console, seats_client.key_resolution)

    ratios = load_ratio_manager()

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
        console.print(f"[red]Error during optimization: {e}[/red]")
        return 1

    console.print(f"\nRoute: {args.origin} -> {args.destination}")
    console.print(f"Dates: {date_range.from_date} to {date_range.to_date}")
    console.print(f"Passengers: {args.passengers}")
    for line in wallet.describe():
        console.print(line)
    print_fx_banner(console)

    if not results:
        if getattr(seats_client, "last_error", None):
            console.print(
                f"\n[red]Seats.aero could NOT be reached: {seats_client.last_error}[/red]"
            )
            console.print(
                "[red]This is an API failure, NOT a finding of no award "
                "availability. No conclusion about award space can be drawn "
                "from this run.[/red]"
            )
        else:
            console.print(
                "\n[yellow]Seats.aero returned no award availability for this "
                "route and date range.[/yellow]"
            )

    # Which pagination path the client took, printed on EVERY run, empty result
    # or not. Seats.aero's cached search is known to paginate and the one
    # captured response was truncated before the pagination fields, so "this is
    # page one" and "this is everything" are not yet distinguishable from the
    # outside. Returning page one silently as the whole result set is an
    # undercount that would look exactly like a finding.
    note = getattr(seats_client, "last_pagination_note", "")
    if note:
        style = "red" if "INCOMPLETE" in note else "dim"
        console.print(f"[{style}]Seats.aero result coverage: {note}[/{style}]")

    print_strategies(results, args.valuation_cpp, console)
    print_summary(results, human_cost=args.human_cost, console=console)

    if args.html:
        export_html(results)
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    # Fixed width so the tables render identically in a terminal and in a
    # captured report file. Widened from 170 at v3: the per-leg table gained a
    # provenance column and 170 no longer fits it.
    console = Console(width=190)

    try:
        # Checked BEFORE dispatch. Handled inside run_search this would have
        # produced "--origin, --destination and --date are all required", which
        # answers a question the user did not ask and hides the real one.
        if getattr(args, "live", False) and not args.trip_fixture:
            console.print(
                "[red]Error: --live requires --trip-fixture.[/red]\n"
                "[red]Live trip mode swaps each FLIGHT leg's points candidates "
                "for real Seats.aero awards inside a trip, so there has to be a "
                "trip. Cash is never touched either way.[/red]\n"
                "[yellow]  For a whole trip:   python -m src.main --trip-fixture "
                "trip_b_europe.json --live[/yellow]\n"
                "[yellow]  For one route:      python -m src.main --origin SFO "
                "--destination MAD --date 2027-01-15[/yellow]"
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
                console.print(
                    "[red]Error: --from-snapshot cannot be combined with the "
                    "flags below.[/red]"
                )
                for c in conflicts:
                    console.print(f"[red]  - {c}[/red]")
                return 1
            if not args.trip_fixture:
                console.print(
                    "[red]Error: --from-snapshot requires --trip-fixture. A "
                    "manifest is replayed AGAINST a trip - the hash is over the "
                    "rows that cover that trip's legs, and without a trip there "
                    "is nothing to cover.[/red]"
                )
                return 1
        if getattr(args, "offline", False) and getattr(args, "live", False):
            console.print(
                "[red]Error: --offline and --live are mutually exclusive.[/red]\n"
                "[red]One says score the fixture's own prices with no transport; "
                "the other says ask Seats.aero. This is a usage error rather "
                "than a precedence rule, because a precedence rule would mean "
                "one of the two flags you typed did nothing.[/red]"
            )
            return 1
        if getattr(args, "new_trip", None):
            return run_new_trip(args, console)
        if args.trip_fixture:
            return run_fixture(args, console)
        return run_search(args, console)
    except WalletError as e:
        # A missing or malformed wallet is a hard stop, not a reason to assume
        # one. Exits non-zero so a script cannot mistake it for a run.
        console.print(f"[red]Wallet error: {e}[/red]")
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

        console.print(f"[bold red]{escape(str(e))}[/bold red]")
        return 1
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        return 1
    except FileNotFoundError as e:
        console.print(f"[red]Error: {e}[/red]")
        return 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
