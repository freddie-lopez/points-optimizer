"""Format optimizer results for CLI output."""
from typing import Dict, List, Optional

from rich.console import Console
from rich.table import Table

from src import config
from src.live_trip import LIVE_SOURCE, VERDICT_NO_LIVE_DATA, supersession_lines
from src.models import LegResult, LiveQueryState, PointsProvenance, Strategy
from src.optimizer import VERDICT_AWARD_UNATTRIBUTED


def _money(x: float) -> str:
    if x == float("inf"):
        return "n/a"
    return f"${x:,.2f}"


def _short_label(leg) -> str:
    """A compact label for the summary table: route or hotel name only."""
    return leg.description.split(",")[0].strip()[:28]


# ---------------------------------------------------------------------------
# FX / provenance banner
# ---------------------------------------------------------------------------


def print_fx_banner(console: Console = None, today=None) -> None:
    """
    Print the FX rates in use, their provenance, their source date and their age.

    v3: a `sourced` rate is not a placeholder - someone looked it up - but it is
    not a user-supplied rate either, so the CONFIRM BEFORE TRUSTING marker stays,
    and past config.FX_STALE_AFTER_DAYS it gains a STALE RATE marker.
    """
    console = console or Console()
    console.print()
    console.print("[bold yellow]" + "=" * 78 + "[/bold yellow]")
    for line in config.fx_report_lines(today):
        console.print(f"[yellow]{line}[/yellow]")
    console.print("[bold yellow]" + "=" * 78 + "[/bold yellow]")


def print_valuation_banner(valuation_cpp: float, console: Console = None) -> None:
    console = console or Console()
    console.print(
        f"\n[bold]Valuation:[/bold] cash is scored at "
        f"{valuation_cpp * 100:.2f} cents per point "
        f"(${1:.2f} cash = {int(1 / valuation_cpp):,} points-equivalent). "
        f"Points and cash are ranked on this single yardstick."
    )


def print_wallet_banner(
    wallet,
    warnings: List[str],
    transfer_date,
    ratios_manager=None,
    console: Console = None,
) -> None:
    """The wallet in use, and the transfer date every ratio was looked up on."""
    console = console or Console()
    console.print("\n[bold]Wallet (supplied at runtime - nothing is assumed)[/bold]")
    for line in wallet.describe():
        console.print(line)
    console.print(
        f"\n[bold]Transfer date:[/bold] {transfer_date} "
        f"[dim](ratios and bonuses are looked up on THIS date, not on the travel "
        f"date - the ratio that applies is the one in force when you transfer)[/dim]"
    )
    if ratios_manager is not None:
        unusable = [
            c for c in wallet.currencies if not ratios_manager.has_verified_partners(c)
        ]
        if unusable:
            console.print(
                f"[yellow]No verified transfer partners exist in data/ratios.csv for: "
                f"{', '.join(sorted(unusable))}. These currencies can fund nothing "
                f"on this run. That is a DATA gap, not a finding about your "
                f"account.[/yellow]"
            )
    for warning in warnings:
        console.print(f"[yellow]! {warning}[/yellow]")


def print_alternatives(results: List[LegResult], console: Console = None) -> None:
    """
    "Same metal, cheaper program" blocks, indented under each leg.

    Every alternative here is UNPRICED. It names a program that can ticket the
    same aeroplane for less cash, and the points price you would need to beat.
    It is never scored and never enters a total.
    """
    console = console or Console()
    any_shown = False
    for r in results:
        if not r.alternatives:
            continue
        any_shown = True
        console.print(f"\n[bold cyan]{r.leg.id}[/bold cyan] - same-metal alternatives")
        for alt in r.alternatives:
            console.print(
                f"  [magenta]{alt.program}[/magenta] on {alt.operating_carrier} metal "
                f"- surcharge {alt.surcharge.render()}"
                + (
                    f", saving ~{_money(alt.cash_saved_vs_best_usd)}"
                    if alt.cash_saved_vs_best_usd
                    else ""
                )
            )
            if alt.break_even_points is not None:
                console.print(
                    f"    [yellow]UNPRICED counterfactual:[/yellow] beats the option "
                    f"above if its award price is below "
                    f"{alt.break_even_points:,} points. NOT scored - go look it up."
                )
            if alt.partnership_assumed:
                console.print(
                    "    [dim]Bookability inferred from alliance membership, not "
                    "from a verified partnership. Confirm before relying on it.[/dim]"
                )
            console.print(f"    [dim]{alt.note}[/dim]")
    if not any_shown:
        console.print(
            "\n[dim]No same-metal alternatives were found. An alternative requires a "
            "CONFIRMED operating carrier and a surcharge row for another program on "
            "that same metal; data/surcharges.csv currently covers each program on "
            "its OWN metal only.[/dim]"
        )


def print_residue_report(report: Dict[str, Dict], console: Console = None) -> None:
    """What each currency is left with after the trip. A real cost v0 never showed."""
    console = console or Console()
    if not report:
        return
    table = Table(title="Residue: what is left in each account afterwards")
    table.add_column("Currency", style="cyan")
    table.add_column("Starting", justify="right")
    table.add_column("Spent", justify="right")
    table.add_column("Remaining", justify="right")
    table.add_column("Note")

    for currency, row in sorted(report.items()):
        if row["unconstrained"]:
            table.add_row(
                currency, "unconstrained", f"{row['spent']:,}", "unconstrained",
                "no balance supplied, so nothing can be reconciled",
            )
            continue
        note = ""
        if row["overdrawn"]:
            note = "[red]OVERDRAWN - this plan spends more than the balance[/red]"
        elif row["too_small_to_use"]:
            note = "[yellow]left over but probably too small to fund anything[/yellow]"
        table.add_row(
            currency,
            f"{row['starting']:,}",
            f"{row['spent']:,}",
            f"{row['remaining']:,}",
            note,
        )
    console.print()
    console.print(table)


# ---------------------------------------------------------------------------
# Leg-level reporting (cash vs points, head to head)
# ---------------------------------------------------------------------------


def print_leg_results(
    results: List[LegResult],
    console: Console = None,
) -> None:
    """Print one row per leg: cash, points, and the verdict."""
    console = console or Console()

    table = Table(title="Per-leg: cash vs points")
    # min_width, because rich starves the narrowest column first and the leg
    # ID is the one thing every other line of output refers back to. v3 added
    # a provenance column and squeezed it to zero width without this.
    table.add_column("Leg", style="cyan", no_wrap=True, min_width=3)
    table.add_column("What", style="white", no_wrap=True, min_width=12)
    table.add_column("Cheapest\ncash", justify="right", style="green", no_wrap=True)
    table.add_column("Cash as pts\n(@1cpp)", justify="right", style="green", no_wrap=True)
    table.add_column("Best points path", style="magenta", no_wrap=True)
    table.add_column("Points", justify="right", style="magenta", no_wrap=True)
    table.add_column("Surcharge", justify="right", style="magenta", no_wrap=True)
    table.add_column("Surch.\nsource", no_wrap=True)
    table.add_column("Score\npoints", justify="right", no_wrap=True)
    table.add_column("Score\ncash", justify="right", no_wrap=True)
    # v3: BOTH PROVENANCES, IN ONE CELL, as "points | cash". They are independent
    # facts about a leg and v2 had no vocabulary for the difference. Cash always
    # comes from a capture; points may or may not be live. A row that shows only
    # one of them invites the reader to assume the other. One column rather than
    # two only because a 13-column table squeezes the leg ID off the screen, and
    # a table you cannot read is its own kind of dishonesty.
    table.add_column("Provenance\npoints | cash", no_wrap=True)
    table.add_column("Verdict", style="bold", no_wrap=True)

    for r in results:
        if r.best_points is not None and r.points_path:
            t = r.points_path.transfers[0] if r.points_path.transfers else None
            path_desc = (
                f"{r.best_points.program} @ {t.ratio}" if t else r.best_points.program
            )
            pts = f"{r.points_required:,}"
        elif r.verdict == "cash (points blocked)":
            path_desc = "partner exists, path blocked"
            pts = "-"
        elif r.break_even_programs:
            path_desc = f"{r.break_even_programs[0]} (no price)"
            pts = f"<{r.break_even_points:,}?"
        else:
            path_desc = "none - not a partner"
            pts = "-"

        # SURCHARGE COLUMN. "UNKNOWN" is rendered as a word, never as a blank and
        # never as $0.00. The whole point of v1 is that those are different.
        if r.surcharge is None:
            surch = "-"
            prov = "-"
        elif r.surcharge.is_known:
            surch = r.surcharge.render()
            prov = r.surcharge.confidence
        else:
            surch = "[bold red]UNKNOWN[/bold red]"
            prov = "[red]unknown[/red]"

        if r.has_points_path and r.points_total_score_usd != float("inf"):
            pts_score = _money(r.points_total_score_usd)
            if r.verdict_sensitive:
                pts_score = (
                    f"[yellow]{_money(r.points_score_low_usd)}-"
                    f"{_money(r.points_score_high_usd)}[/yellow]"
                )
        elif r.surcharge_cannot_change_verdict and r.points_floor_usd is not None:
            # The surcharge is unknown but INERT: points already lose at its $0
            # floor. Show the floor, not a "$0.00 break-even" - that reads like a
            # $0 surcharge, which is the exact confusion v1 exists to remove.
            pts_score = f">= {_money(r.points_floor_usd)}"
        elif r.break_even_surcharge_usd is not None:
            pts_score = f"[red]? (win if surch < {_money(r.break_even_surcharge_usd)})[/red]"
        else:
            pts_score = "-"

        verdict_style = {
            "points": "[bold magenta]POINTS[/bold magenta]",
            "cash": "[bold green]PAY CASH[/bold green]",
            "cash (no points path)": "[bold green]PAY CASH (no path)[/bold green]",
            "cash (points unpriced)": "[bold yellow]PAY CASH (pts unpriced)[/bold yellow]",
            "cash (points blocked)": "[bold yellow]PAY CASH (pts blocked)[/bold yellow]",
            "cash (surcharge unknown)": "[bold red]WITHHELD (surch unknown)[/bold red]",
            # v3. Says NOTHING about award space - that is why it is separate
            # from "no path", whose reason text is a claim about partnerships.
            VERDICT_NO_LIVE_DATA: "[bold yellow]PAY CASH (no live pts data)[/bold yellow]",
            # v3 fix, finding M-5. The response returned real awards and named
            # no program for them. Also says nothing about partnerships.
            VERDICT_AWARD_UNATTRIBUTED:
                "[bold yellow]PAY CASH (award unattributed)[/bold yellow]",
        }[r.verdict]
        if r.demoted_for_trip_balance:
            # C-3: this leg WOULD have been points; the trip ran out of balance.
            verdict_style = "[bold yellow]PAY CASH (trip out of points)[/bold yellow]"
        if r.verdict_sensitive:
            verdict_style += " [yellow]!SENSITIVE[/yellow]"
        if r.scored_off_date:
            verdict_style += f" [yellow]!DATE {r.scoring_date}[/yellow]"

        # A LEG WITH NO PRICEABLE CASH HAS NO CASH NUMBER, and `r.cash_usd`'s
        # default of 0.0 is not one. Before the L-1 fix this state was
        # unreachable from the CLI (an unconvertible currency aborted the whole
        # run), and reaching it now must not reintroduce `unknown -> $0` in the
        # very column the tool exists to keep honest.
        cash_cell = _money(r.cash_usd) if r.best_cash is not None else (
            "[bold red]UNKNOWN[/bold red]"
        )
        cash_pts_cell = (
            f"{r.cash_as_points_equivalent:,}" if r.best_cash is not None else "-"
        )

        table.add_row(
            r.leg.id,
            _short_label(r.leg),
            cash_cell,
            cash_pts_cell,
            path_desc,
            pts,
            surch,
            prov,
            pts_score,
            _money(r.cash_total_score_usd),
            f"{_points_provenance_cell(r)} | {_cash_provenance_cell(r)}",
            verdict_style,
        )

    console.print(table)


def _points_provenance_cell(r: LegResult) -> str:
    """
    The points side's provenance, and for an unavailable leg, WHY.

    An API failure and an absence of award space both land on "unavailable", and
    the whole design turns on their never being rendered alike - so they are
    given different words and different colours even in a one-word table cell.
    """
    prov = r.leg.points_provenance
    outcome = r.leg.live_outcome

    if prov is PointsProvenance.LIVE:
        return "[bold green]live[/bold green]"
    if prov is PointsProvenance.BADGE_FALLBACK:
        return "[yellow]badge[/yellow]"

    if outcome is not None:
        if outcome.state is LiveQueryState.NO_AWARD_SPACE:
            return "[yellow]none: no award space[/yellow]"
        if outcome.state is LiveQueryState.API_ERROR:
            return "[bold red]none: API FAILED[/bold red]"
        if outcome.state is LiveQueryState.BUDGET_EXHAUSTED:
            return "[bold red]none: BUDGET[/bold red]"
        if outcome.state is LiveQueryState.ANSWERED_UNREADABLE:
            # The sixth state gets its own words for the same reason the other
            # five do: an unreadable answer and an empty calendar must never
            # look alike, not even in a one-word table cell (finding C-1).
            return "[bold red]none: UNREADABLE[/bold red]"
    return "[dim]none[/dim]"


def _cash_provenance_cell(r: LegResult) -> str:
    """
    Where the cash price came from, and when.

    Cash is ALWAYS a capture - there is no cash-price API in scope and there will
    not be one - so this column never says "live". What it can say is "unknown",
    which means nobody recorded the origin of the number, and that is worth
    seeing next to a live points price.
    """
    provenance = r.leg.cash_provenance or "unknown"
    when = f" {r.leg.cash_captured_on}" if r.leg.cash_captured_on else ""
    short = {
        "captured_screenshot": "screenshot",
        "captured_booking_page": "booking pg",
        "unknown": "[yellow]unknown[/yellow]",
    }.get(provenance, provenance)
    return f"{short}{when}"


def print_leg_detail(results: List[LegResult], console: Console = None) -> None:
    """Print the reasoning, flags, and warnings for each leg."""
    console = console or Console()
    console.print("\n[bold]Per-leg detail[/bold]")
    for r in results:
        console.print(f"\n[bold cyan]{r.leg.id}[/bold cyan] - {r.leg.description}")
        console.print(f"  Verdict: [bold]{r.verdict.upper()}[/bold] - {r.verdict_reason}")
        if r.best_cash:
            console.print(f"  Cash: {r.best_cash.label}")
            if r.best_cash.is_foreign:
                console.print(
                    f"        {r.best_cash.amount:,.2f} {r.best_cash.currency} "
                    f"-> {_money(r.best_cash.amount_usd)} at the FX rate above "
                    f"[yellow](rate unconfirmed)[/yellow]"
                )
            if r.best_cash.unavoidable_cash_note:
                console.print(f"        [yellow]! {r.best_cash.unavoidable_cash_note}[/yellow]")
        if r.mandatory_fees_usd:
            console.print(
                f"  [yellow]Mandatory fees: {_money(r.mandatory_fees_usd)} - owed "
                f"whether you pay cash OR points, and included in BOTH totals.[/yellow]"
            )
        if r.best_points is not None and r.points_path:
            console.print(f"  Points: {r.best_points.label}")
            console.print(f"        {r.points_path.summary()}")
            if r.funding_plan:
                console.print(f"        spend: {r.funding_plan.spend_summary()}")
            if r.points_path.stranded_points:
                console.print(
                    f"        stranded: {r.points_path.stranded_points:,} "
                    f"(within the unavoidable transfer increment)"
                )
            if r.best_points.operating_carrier:
                console.print(
                    f"        metal: {r.best_points.operating_carrier} "
                    f"(source: {r.best_points.carrier_source}), "
                    f"cabin {r.best_points.cabin}"
                )
        if r.surcharge is not None:
            if r.surcharge.is_known:
                console.print(
                    f"  Surcharge: {r.surcharge.render()} "
                    f"[{r.surcharge.confidence}]"
                    + (f" via {r.surcharge.matched_rule}" if r.surcharge.matched_rule else "")
                )
                if r.surcharge.source:
                    console.print(f"        source: {r.surcharge.source}")
                if r.surcharge.notes:
                    console.print(f"        [yellow]{r.surcharge.notes}[/yellow]")
            else:
                console.print(
                    "  [bold red]Surcharge: UNKNOWN - this is NOT $0.[/bold red]"
                )
                if r.break_even_surcharge_usd is not None:
                    console.print(
                        f"        [red]Break-even: points beat cash only if the "
                        f"surcharge is below {_money(r.break_even_surcharge_usd)}."
                        f"[/red]"
                    )
                if r.surcharge.notes:
                    console.print(f"        [dim]{r.surcharge.notes}[/dim]")
        if r.verdict == "points" or r.verdict.startswith("cash"):
            if r.has_points_path:
                if r.verdict == "points":
                    console.print(
                        f"  Margin: points save {_money(r.margin_usd)} "
                        f"({r.margin_pct:.1f}% of the cash price)"
                    )
                else:
                    console.print(
                        f"  Margin: points cost {_money(r.margin_usd)} MORE than cash "
                        f"({r.margin_pct:.1f}% worse)"
                    )
        for note in r.leg.notes:
            console.print(f"  [dim]note: {note}[/dim]")
        for flag in r.leg.data_flags:
            console.print(f"  [yellow]FLAG: {flag}[/yellow]")
        for w in r.warnings:
            console.print(f"  [red]UNVERIFIED: {w}[/red]")


def print_live_banner(
    outcomes: List, opts=None, cache=None, console: Console = None
) -> None:
    """
    What this live run actually did, printed BEFORE the table.

    Fetched vs cache-served, calls left against the 1,000/day cap, and where the
    snapshots went. A live run that does not say how many calls it spent is a
    live run somebody will accidentally put in a loop.
    """
    from src.seats_client import SeatsClient

    console = console or Console()
    queried = [o for o in outcomes if o.state is not LiveQueryState.NOT_QUERIED]
    cached = [o for o in queried if o.served_from_cache]
    ok = [o for o in queried if o.state is LiveQueryState.OK]
    empty = [o for o in queried if o.state is LiveQueryState.NO_AWARD_SPACE]
    failed = [o for o in queried if o.is_api_failure]
    unreadable = [o for o in queried if o.answered_but_not_understood]
    skipped_rows = sum(o.rows_unreadable for o in queried)

    console.print("\n[bold cyan]" + "=" * 78 + "[/bold cyan]")
    console.print("[bold cyan]LIVE TRIP MODE - points from Seats.aero, cash from captures[/bold cyan]")
    console.print(
        f"  legs queried: {len(queried)}   answered with awards: {len(ok)}   "
        f"answered with NO award space: {len(empty)}   "
        f"[red]API/budget failures: {len(failed)}[/red]   "
        f"[red]answered but UNREADABLE: {len(unreadable)}[/red]"
    )
    if skipped_rows:
        # A skipped row is a defect signal, not a silent statistic (finding
        # C-1). The count was carried on every outcome and printed nowhere.
        console.print(
            f"  [bold red]{skipped_rows} availability row(s)/page(s) across this "
            f"run COULD NOT BE PARSED.[/bold red] [red]Any leg whose rows were all "
            f"unreadable is reported as UNREADABLE, never as 'no award space'."
            f"[/red]"
        )
    console.print(f"  served from the disk cache: {len(cached)} of {len(queried)}")
    console.print(
        f"  Seats.aero budget: {SeatsClient._budget_remaining():,} of "
        f"{SeatsClient.DAILY_CALL_CAP:,} calls remaining today"
    )
    if cache is not None:
        console.print(f"  snapshots: {cache.snapshot_dir}")
        console.print(f"  manifest:  {cache.manifest_path}")
        for warning in getattr(cache, "warnings", []):
            console.print(f"  [yellow]cache: {warning}[/yellow]")
    if opts is not None:
        console.print(
            f"  flex-days: {opts.flex_days}   refresh: {opts.refresh}   "
            f"cache-ttl: "
            f"{'default' if opts.cache_ttl is None else str(opts.cache_ttl) + 's'}"
        )
    console.print(
        "  [dim]CASH IS FROM SCREENSHOTS AND ALWAYS WILL BE. There is no "
        "cash-price API in scope. Hotels are manual - Seats.aero is "
        "flights-only.[/dim]"
    )
    if failed:
        console.print(
            "  [bold red]At least one leg was never answered. Nothing is known "
            "about award space on those legs; they are NOT reported as having "
            "none.[/bold red]"
        )
    if unreadable:
        console.print(
            "  [bold red]At least one leg WAS answered and the answer could not "
            "be parsed. That is a failure on our side, not an empty calendar, "
            "and it is NOT reported as no award space.[/bold red]"
        )
    console.print("[bold cyan]" + "=" * 78 + "[/bold cyan]")


def print_live_leg_detail(results: List[LegResult], console: Console = None) -> None:
    """
    Per-leg live reporting: outcome, superseded badges, and flexible findings.

    STEP 8'S RULE LIVES HERE. A live leg with an unknown surcharge is still
    useful and must render as such: a FLOOR (the least it could possibly cost)
    and a BREAK-EVEN (the surcharge above which points stop winning). Never a
    blank, never a dash, never a zero standing in for an unknown.
    """
    console = console or Console()
    any_live = any(r.leg.live_outcome is not None for r in results)
    if not any_live:
        return

    console.print("\n[bold]Live award data, per leg[/bold]")
    for r in results:
        outcome = r.leg.live_outcome
        if outcome is None or outcome.state is LiveQueryState.NOT_QUERIED:
            continue

        console.print(f"\n[bold cyan]{r.leg.id}[/bold cyan] {r.leg.description}")
        style = "red" if outcome.is_api_failure else "white"
        console.print(f"  [{style}]{outcome.render()}[/{style}]")
        if outcome.pagination_note:
            note_style = "red" if "INCOMPLETE" in outcome.pagination_note else "dim"
            console.print(
                f"  [{note_style}]coverage: {outcome.pagination_note}[/{note_style}]"
            )

        for line in supersession_lines(r.leg):
            console.print(f"  [yellow]{line}[/yellow]")

        if r.leg.date_shifted:
            console.print(
                f"  [bold yellow]DATE SHIFTED to {r.leg.date_shifted_to}: an "
                f"off-date award was promoted because the fixture carries a "
                f"CAPTURED cash price for that date. The comparison is therefore "
                f"real, and the fact that the date moved is recorded here rather "
                f"than lost.[/bold yellow]"
            )

        _print_live_scoring_block(r, console)
        _print_flexible_findings(r, console)


def _print_live_scoring_block(r: LegResult, console: Console) -> None:
    """The floor / break-even block. Rendered for EVERY live leg, always."""
    if r.leg.points_provenance is not PointsProvenance.LIVE:
        return
    if not r.best_points:
        return

    cand = r.best_points
    console.print(
        f"  live award: {cand.program}  {cand.cabin}  {cand.points:,} points"
    )
    if cand.surcharge_captured:
        console.print(
            f"     taxes from the API: {_money(cand.cash_surcharge)} - taken as "
            f"the COMPLETE carrier-side cash figure because this program levies "
            f"no carrier surcharge as a matter of policy."
        )
        console.print(
            f"     [dim]Government taxes and airport charges beyond what "
            f"Seats.aero reports are still owed and are NOT modelled. A $0 "
            f"carrier surcharge is not a $0 ticket.[/dim]"
        )
    elif getattr(cand, "taxes_unconvertible", False):
        # FINDING C-2. This is the case that used to print a clean "$0.00 /
        # modeled" in the per-leg table while the leg carried real, unpriced
        # cash. It gets the loudest line in the block.
        console.print(
            f"     [bold red]taxes from the API: "
            f"{cand.observed_taxes_currency or 'unnamed currency'} "
            f"{cand.observed_taxes_amount:,.2f} - CANNOT BE CONVERTED TO USD, so "
            f"the cash side of this award is UNKNOWN. IT IS NOT $0.[/bold red]"
        )
        console.print(
            f"     [red]{cand.observed_taxes_note}[/red]"
        )
        console.print(
            f"     [dim]This program's no-carrier-surcharge policy is a statement "
            f"about YQ/YR and does NOT price these taxes. Supply the rate with "
            f"--fx to score this leg.[/dim]"
        )
    elif cand.observed_taxes_known and cand.observed_taxes_reported:
        console.print(
            f"     taxes from the API: {_money(cand.observed_taxes_usd)} "
            f"({cand.observed_taxes_currency} "
            f"{cand.observed_taxes_amount:,.2f}) - a KNOWN cost, counted into "
            f"every points figure below. The carrier-imposed surcharge is a "
            f"SEPARATE quantity and is reported separately."
        )
    elif cand.observed_taxes_reported:
        console.print(
            "     [yellow]taxes from the API: reported but NOT USABLE. Carried as "
            "unknown, never as $0.[/yellow]"
        )

    if r.surcharge is not None and not r.surcharge.is_known:
        floor = r.points_floor_usd
        taxes_clause = (
            f"+ the API's taxes of {_money(r.observed_taxes_usd)} "
            if r.observed_taxes_usd
            else "with NO tax figure available to add "
        )
        console.print(
            f"     [bold]floor {_money(floor) if floor is not None else 'n/a'}[/bold]"
            f"  (points at the run's valuation {taxes_clause}, with the "
            f"carrier surcharge at its $0 floor - the least this can possibly cost)"
        )
        if r.break_even_surcharge_usd is not None:
            console.print(
                f"     points win ONLY if the carrier surcharge above those taxes "
                f"is below [bold]{_money(r.break_even_surcharge_usd)}[/bold]"
            )
        console.print(
            f"     [bold red]surcharge UNKNOWN - this is NOT $0.[/bold red] "
            f"{r.surcharge.notes}"
        )
        if r.surcharge_cannot_change_verdict:
            console.print(
                "     [green]...and it does not matter here: the floor already "
                "loses to cash, and a surcharge can only ADD to the points side. "
                "The verdict is CERTAIN despite the unknown.[/green]"
            )
    # THE HONESTY INVARIANT: a live price never renders without its timestamp.
    if cand.source == LIVE_SOURCE:
        console.print(f"     [dim]provenance: {cand.source} - {cand.source_note}[/dim]")


def _print_flexible_findings(r: LegResult, console: Console) -> None:
    """
    Awards found on OTHER dates. Advisory only; they never enter a score.

    The advisory always states that cash for that date was not captured, because
    the reason these are not scored is not that they are uninteresting - it is
    that there is nothing legitimate to compare them against.
    """
    findings = r.leg.flexible_date_findings
    if not findings:
        return
    spec = r.leg.live_outcome.queried if r.leg.live_outcome else None
    window = f"+/-{spec.flex_days} days" if spec else "a flexible window"
    console.print(
        f"  [bold]FLEXIBLE-DATE FINDINGS for {r.leg.id}[/bold] (searched "
        f"{window}; [bold]these are NOT scored[/bold])"
    )
    for f in sorted(findings, key=lambda x: x.award_date):
        console.print(f"    {f.render()}   [dim]{f.date_match}[/dim]")
        console.print(f"       [yellow]{f.advisory()}[/yellow]")
    console.print(
        "    [dim]Why not simply score these against this leg's cash price? "
        "Because a flexible search takes the MINIMUM over a window on the points "
        "side while cash stays a SINGLE FIXED DRAW, and award space is released "
        "on low-demand dates that also carry low cash fares. The bias runs in "
        "favour of points and grows with the window.[/dim]"
    )


def print_trip_totals(
    totals: Dict[str, float],
    label: str = "Trip",
    console: Console = None,
    today=None,
) -> None:
    """Print the both-ways totals and the headline beat-cash percentage."""
    console = console or Console()

    # THE BALANCE CEILING IS PRINTED FIRST, BEFORE ANY NUMBER (finding C-3).
    #
    # The overdraft used to appear in exactly one place: the residue table, in a
    # column called "Note", which main.py prints AFTER this whole block. So a
    # plan that spent 210,000 UR out of a 160,000 balance led with a clean
    # 22.22% and mentioned the impossibility three tables later. A recommendation
    # you cannot execute is not a recommendation, and the reader has to know that
    # before they read the number, not after.
    executable = totals.get("trip_funding_executable", True)
    funding_note = totals.get("trip_funding_note", "")
    if not executable:
        console.print(
            f"\n[bold red]{'=' * 78}[/bold red]\n"
            f"[bold red]THIS TRIP CANNOT BE FUNDED FROM YOUR BALANCE.[/bold red]\n"
            f"[red]{funding_note}[/red]\n"
            f"[red]The margin below is WITHHELD. Nothing is quoted for a plan the "
            f"points do not exist for.[/red]\n"
            f"[bold red]{'=' * 78}[/bold red]"
        )
    elif totals.get("trip_balance_bound"):
        console.print(
            f"\n[bold yellow]YOUR BALANCE IS THE BINDING CONSTRAINT ON THIS TRIP."
            f"[/bold yellow]\n[yellow]{funding_note}[/yellow]"
        )

    table = Table(title=f"{label}: totals both ways")
    table.add_column("Measure", style="cyan")
    table.add_column("Value", justify="right")

    table.add_row("Pay cash for everything", _money(totals["all_cash_usd"]))
    table.add_row("Optimizer's recommendation", _money(totals["optimized_usd"]))
    table.add_row("Saving", _money(totals["savings_usd"]))

    # THE HEADLINE. When any surcharge on the trip is a range or an unknown, the
    # honest headline is an INTERVAL. Printing the point estimate alone is what
    # produced v0's 16%.
    #
    # v3 RULE (plan section 4.7, rule 2): THE PERCENTAGE MAY NEVER BE PRINTED
    # WITHOUT ITS PROVENANCE LINE. They are emitted by this one call so they
    # cannot be separated by accident. A single number that hides its own
    # provenance is worse than no number - and `--require-all-live` withholds
    # the percentage entirely rather than let a mixed one be quoted.
    provenance = totals.get("margin_provenance", "badge")
    provenance_note = totals.get("margin_provenance_note", "")
    withheld = bool(totals.get("margin_withheld"))

    # v5. THE MANIFEST HASH TRAVELS WITH THE PERCENTAGE, IN THE SAME CELL,
    # EMITTED BY THIS CALL.
    #
    # v3 established that a percentage may never be printed without its
    # provenance line, and made that structural by emitting both here. v5 adds a
    # second thing that must travel with the number: on a replayed run the
    # margin is only meaningful against the bytes it was derived from, so
    # `mh_...` is appended to EVERY cell that carries a percentage - including
    # the dim low/high rows, which are percentages too.
    #
    # Deliberately SHORT. The snapshot count and the parser-version pair go on
    # their own rows below, which carry no percentage: a suffix long enough to
    # wrap would put the number on one physical line and the hash on another,
    # which is the failure this rule exists to prevent.
    manifest_hash = str(totals.get("manifest_hash") or "")
    hash_suffix = f"  {manifest_hash}" if manifest_hash else ""

    if not executable:
        # C-3: an unfundable plan gets no percentage at all. There is nothing to
        # qualify - the plan does not exist.
        table.add_row(
            "[bold red]Optimizer beats paying cash by[/bold red]",
            "[bold red]WITHHELD - PLAN NOT FUNDABLE[/bold red]",
        )
        table.add_row(
            "[red]  withheld because[/red]",
            f"[red]{funding_note}[/red]",
        )
    elif withheld:
        table.add_row(
            "[bold red]Optimizer beats paying cash by[/bold red]",
            "[bold red]WITHHELD[/bold red]",
        )
        table.add_row(
            "[red]  withheld because[/red]",
            f"[red]--require-all-live and provenance is '{provenance}'[/red]",
        )
    elif totals.get("headline_is_a_range"):
        table.add_row(
            "[bold]Optimizer beats paying cash by[/bold]",
            f"[bold]{totals['beat_cash_pct_low']:.2f}% - "
            f"{totals['beat_cash_pct_high']:.2f}%{hash_suffix}[/bold]",
        )
        table.add_row(
            "[dim]  low end = what is actually defensible[/dim]",
            f"[dim]{totals['beat_cash_pct_low']:.2f}%{hash_suffix}[/dim]",
        )
        table.add_row(
            "[dim]  high end = only if every unknown surcharge is $0[/dim]",
            f"[dim]{totals['beat_cash_pct_high']:.2f}%{hash_suffix}[/dim]",
        )
    else:
        table.add_row(
            "[bold]Optimizer beats paying cash by[/bold]",
            f"[bold]{totals['beat_cash_pct']:.2f}%{hash_suffix}[/bold]",
        )

    if manifest_hash:
        # Rows that carry NO percentage, so their length cannot wrap a number
        # away from its hash.
        table.add_row(
            "[cyan]  replayed from manifest[/cyan]",
            f"[cyan]{manifest_hash}[/cyan]",
        )
        table.add_row(
            "[cyan]  snapshots / parser at capture / parser now[/cyan]",
            f"[cyan]{int(totals.get('manifest_snapshots', 0))} / "
            f"{totals.get('manifest_parser_at_capture', 'unknown')} / "
            f"{totals.get('manifest_parser_now', 'unknown')}[/cyan]",
        )

    style = {
        "live": "green",
        "snapshot": "cyan",
        "mixed": "bold yellow",
        "badge": "yellow",
        "badge_fallback": "bold yellow",
        "none": "dim",
    }.get(provenance, "yellow")
    table.add_row(
        f"[{style}]  margin provenance[/{style}]",
        f"[{style}]{provenance}[/{style}]",
    )
    counted = int(totals.get("legs_points_live", 0))
    label = "live"
    if int(totals.get("legs_points_snapshot", 0)):
        counted = int(totals.get("legs_points_snapshot", 0)) + counted
        label = "live or replayed"
    table.add_row(
        f"[{style}]  {provenance_note}[/{style}]",
        f"[{style}]{counted} of "
        f"{int(totals.get('legs_flight_total', 0))} legs {label}[/{style}]",
    )
    table.add_row("Points spent", f"{int(totals['points_spent']):,}")
    spend = totals.get("trip_points_spend") or {}
    if spend:
        table.add_row(
            "  drawn from",
            ", ".join(f"{n:,} {c}" for c, n in sorted(spend.items())),
        )
    if totals.get("trip_legs_demoted_for_balance"):
        table.add_row(
            "[bold yellow]Legs that would win on points but the\n"
            "balance cannot fund (scored as cash)[/bold yellow]",
            f"[bold yellow]"
            f"{', '.join(totals['trip_legs_demoted_for_balance'])}[/bold yellow]",
        )
    table.add_row("Cash still owed", _money(totals["cash_still_owed_usd"]))
    table.add_row("Legs where points win", str(int(totals["legs_where_points_win"])))
    table.add_row(
        "Legs with NO UR path at all", str(int(totals["legs_without_points_path"]))
    )
    table.add_row(
        "Legs where a UR partner exists but no\naward price was captured",
        str(int(totals.get("legs_points_unpriced", 0))),
    )
    if totals.get("legs_points_blocked"):
        table.add_row(
            "Legs where a path exists but was\nblocked by balance/stranding",
            str(int(totals["legs_points_blocked"])),
        )
    if totals.get("legs_surcharge_unknown"):
        table.add_row(
            "[red]Legs where a points path exists but its\n"
            "surcharge is UNKNOWN (NOT $0)[/red]",
            f"[red]{int(totals['legs_surcharge_unknown'])}[/red]",
        )
    if totals.get("legs_verdict_sensitive"):
        table.add_row(
            "[yellow]Legs whose verdict FLIPS inside the\n"
            "surcharge estimate's own range[/yellow]",
            f"[yellow]{int(totals['legs_verdict_sensitive'])}[/yellow]",
        )

    if totals.get("legs_unpriceable"):
        table.add_row(
            "[bold red]Legs EXCLUDED from both totals because nothing\n"
            "on them could be priced (NOT counted as $0)[/bold red]",
            f"[bold red]{int(totals['legs_unpriceable'])} "
            f"({', '.join(totals.get('legs_unpriceable_ids') or [])})[/bold red]",
        )
    if totals.get("legs_api_error"):
        table.add_row(
            "[bold red]Flight legs where Seats.aero was NEVER REACHED\n"
            "(this says NOTHING about award space)[/bold red]",
            f"[bold red]{int(totals['legs_api_error'])} "
            f"({', '.join(totals.get('legs_api_error_ids') or [])})[/bold red]",
        )
    if totals.get("legs_no_award_space"):
        table.add_row(
            "[yellow]Flight legs where Seats.aero ANSWERED with no\n"
            "award space (this IS a finding)[/yellow]",
            f"[yellow]{int(totals['legs_no_award_space'])} "
            f"({', '.join(totals.get('legs_no_award_space_ids') or [])})[/yellow]",
        )
    if totals.get("legs_budget_exhausted"):
        table.add_row(
            "[bold red]Flight legs never queried because the daily\n"
            "call budget ran out (NOT a finding)[/bold red]",
            f"[bold red]{int(totals['legs_budget_exhausted'])} "
            f"({', '.join(totals.get('legs_budget_exhausted_ids') or [])})[/bold red]",
        )

    console.print(table)

    if totals.get("margin_withheld"):
        console.print(
            f"\n[bold red]THE TRIP MARGIN IS WITHHELD.[/bold red] --require-all-live "
            f"was passed and the margin's provenance is "
            f"'{totals.get('margin_provenance')}', not 'live'. "
            f"{totals.get('margin_provenance_note', '')} Per-leg results above are "
            f"unaffected and remain valid. Re-run without --require-all-live to "
            f"see the mixed-provenance number, clearly labelled as mixed."
        )
    elif totals.get("margin_provenance") == "mixed":
        console.print(
            f"\n[bold yellow]THIS MARGIN MIXES LIVE AND BADGE-DERIVED LEGS.[/bold yellow] "
            f"{totals.get('margin_provenance_note', '')} Do not quote it as a live "
            f"number. Run with --require-all-live if you need one that can be."
        )
    elif totals.get("margin_provenance") == "badge":
        console.print(
            f"\n[yellow]{totals.get('margin_provenance_note', '')}[/yellow]"
        )

    if totals.get("headline_is_a_range") and not totals.get("margin_withheld"):
        console.print(
            "\n[bold yellow]The headline above is a RANGE and must not be quoted as "
            "a single number.[/bold yellow] The spread is carrier-imposed surcharges "
            "that are not known. The low end is what the tool can defend today; the "
            "high end assumes every unknown surcharge turns out to be $0, which is "
            "the assumption that made v0's number wrong."
        )
    if totals.get("legs_verdict_sensitive_ids"):
        console.print(
            f"[bold yellow]VERDICT SENSITIVE: "
            f"{', '.join(totals['legs_verdict_sensitive_ids'])} reverse inside their "
            f"own surcharge range. Do not treat these as settled.[/bold yellow]"
        )
    if totals.get("rests_on_placeholder_fx"):
        console.print(
            "[bold yellow]At least one total above rests on an FX rate with NO "
            "SOURCE. Supply one with --fx to clear the marker.[/bold yellow]"
        )
    # A rate that WAS looked up, but a month ago. Every total above that touched
    # it inherits the staleness, exactly as a placeholder total does.
    stale = config.stale_currencies(today)
    if stale:
        console.print(
            f"[bold yellow]STALE RATE: {', '.join(stale)} were sourced more than "
            f"{config.FX_STALE_AFTER_DAYS} days ago. EVERY TOTAL ABOVE THAT USED "
            f"THEM IS MARKED STALE. Re-source them, or supply one with --fx "
            f"{stale[0]}=<rate>.[/bold yellow]"
        )


# ---------------------------------------------------------------------------
# Award-level reporting (Seats.aero path)
# ---------------------------------------------------------------------------


def print_strategies(
    strategies: List[Strategy],
    valuation_cpp: float = config.DEFAULT_VALUATION_CPP,
    console: Console = None,
) -> None:
    """Print award strategies as a rich table."""
    console = console or Console()

    if not strategies:
        console.print("[yellow]No strategies found.[/yellow]")
        return

    table = Table(title="Points Transfer Optimizer Results")
    table.add_column("Rank", style="cyan", no_wrap=True)
    table.add_column("Program", style="magenta")
    table.add_column("Date", style="green")
    table.add_column("Cabin", style="blue")
    table.add_column("Transfer", style="white")
    table.add_column("Points Cost", justify="right", style="yellow")
    table.add_column("Stranded", justify="right", style="yellow")
    table.add_column("Cash Cost", justify="right", style="yellow")
    table.add_column("Total Value", justify="right")

    for i, s in enumerate(strategies, 1):
        table.add_row(
            str(i),
            s.award.program,
            str(s.award.date),
            s.award.award_type,
            s.transfer_path.summary(),
            f"{s.points_cost:,}",
            f"{s.transfer_path.stranded_points:,}",
            _money(s.cash_cost),
            _money(s.total_value),
        )

    console.print(table)


def print_summary(
    strategies: List[Strategy],
    human_cost: Optional[int] = None,
    console: Console = None,
) -> float:
    """Print a summary. Returns margin as a percentage if human_cost is given."""
    console = console or Console()

    if not strategies:
        return 0.0

    console.print("\n[bold]Summary[/bold]")
    console.print(f"Top strategy points cost: {strategies[0].points_cost:,}")
    console.print(f"Top strategy cash cost: {_money(strategies[0].cash_cost)}")
    console.print(f"Top strategy total value: {_money(strategies[0].total_value)}")

    margin_pct = 0.0
    if human_cost:
        margin_pct = (strategies[0].points_cost - human_cost) / human_cost * 100
        console.print(f"\n[bold]Margin vs. human answer:[/bold] {margin_pct:+.2f}%")
        if margin_pct < 0:
            console.print("[green]Optimizer beats human answer[/green]")
        elif margin_pct == 0:
            console.print("[yellow]Optimizer ties human answer[/yellow]")
        else:
            console.print("[red]Human answer beats optimizer[/red]")

    return margin_pct


def _html_escape(text: object) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _html_cash_cell(s: Strategy) -> str:
    """
    The cash column for one strategy. AN UNKNOWN NEVER RENDERS AS A NUMBER.

    FINDING H-3. `Strategy` has carried `cash_cost_known`, `cash_cost_note` and
    `is_lower_bound` since v1 precisely so an unconvertible tax cannot be
    totalled as zero, and `export_html` read NONE of them - it printed
    `${s.cash_cost:.2f}`, so an award with MXN 9,000 of unpriceable taxes
    exported as a clean `$0.00` with a clean total beside it. v1's
    non-negotiable is "`unknown` never renders as `$0` - output, totals, HTML
    export"; the export path was quietly exempt from the guarantees the CLI path
    makes, which is the worst place to be exempt, because an export is the
    artefact that gets forwarded to somebody else.
    """
    if s.cash_cost_known:
        return f"<td>${s.cash_cost:,.2f}</td>"
    award = s.award
    detail = ""
    if getattr(award, "cash_component_source_amount", None) is not None:
        detail = (
            f"{_html_escape(award.cash_component_currency or 'unnamed currency')} "
            f"{award.cash_component_source_amount:,.2f}"
        )
    return (
        '<td class="unknown"><strong>UNKNOWN &mdash; NOT $0.00</strong>'
        + (f"<br><span class=\"note\">{detail} could not be converted to USD."
           f"</span>" if detail else "")
        + (
            f"<br><span class=\"note\">{_html_escape(s.cash_cost_note)}</span>"
            if s.cash_cost_note
            else ""
        )
        + "</td>"
    )


def export_html(strategies: List[Strategy], output_file: str = "results.html") -> None:
    """
    Export award results to HTML, carrying every marker the CLI carries.

    An export is what gets forwarded, so it must not be a weaker document than
    the terminal output it was made from: an unknown cash component, a lower
    bound, and an unverified points price all survive into the file (finding
    H-3).
    """
    def _row(i: int, s: Strategy) -> str:
        total = (
            f"<td>${s.total_value:,.2f}</td>"
            if not s.is_lower_bound
            else f'<td class="unknown">&ge; ${s.total_value:,.2f}'
            f'<br><span class="note">LOWER BOUND: the cash component above is '
            f"unknown and can only push this up.</span></td>"
        )
        verified = s.award.source == "seats_aero"
        provenance = (
            f"<td>{_html_escape(s.award.source)}</td>"
            if verified
            else f'<td class="unknown"><strong>UNVERIFIED</strong><br>'
            f'<span class="note">{_html_escape(s.award.source)}. '
            f"{_html_escape(s.award.source_note)}</span></td>"
        )
        return (
            f"<tr><td>{i}</td><td>{_html_escape(s.award.program)}</td>"
            f"<td>{s.award.date}</td>"
            f"<td>{_html_escape(s.award.award_type)}</td><td>{s.points_cost:,}</td>"
            + _html_cash_cell(s)
            + total
            + provenance
            + "</tr>"
        )

    rows = "".join(_row(i, s) for i, s in enumerate(strategies, 1))
    lower_bounds = [s for s in strategies if s.is_lower_bound]
    unverified = [s for s in strategies if s.award.source != "seats_aero"]
    banners = ""
    if lower_bounds:
        banners += (
            '<p class="warn"><strong>AT LEAST ONE CASH COMPONENT IS UNKNOWN AND '
            "IS NOT $0.</strong> Rows marked UNKNOWN carry real cash this tool "
            "cannot price - an award tax in a currency with no configured FX "
            "rate. Their totals are LOWER BOUNDS: the true cost can only be "
            "higher. Supply the rate with <code>--fx</code> to price them.</p>"
        )
    if unverified:
        banners += (
            '<p class="warn"><strong>AT LEAST ONE POINTS PRICE IS '
            "UNVERIFIED.</strong> A Google Flights points badge often reflects "
            "dynamic revenue pricing rather than partner saver award space, and "
            "exactly one badge in this project has ever been corroborated "
            "against a real response. Rows marked UNVERIFIED have not been "
            "confirmed as bookable award space.</p>"
        )

    html_content = f"""<html>
<head><title>Points Optimizer Results</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 20px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; vertical-align: top; }}
th {{ background-color: #4CAF50; color: white; }}
tr:nth-child(even) {{ background-color: #f2f2f2; }}
td.unknown {{ background-color: #ffe9e9; color: #8a1f1f; }}
span.note {{ font-size: 0.85em; color: #555; }}
p.warn {{ border-left: 4px solid #c0392b; background: #fdf2f2; padding: 8px 12px; }}
</style></head>
<body>
<h1>Points Optimizer Results</h1>
<p>{config.FX_WARNING}</p>
{banners}
<table>
<tr><th>Rank</th><th>Program</th><th>Date</th><th>Cabin</th>
<th>Points Cost</th><th>Cash Cost</th><th>Total Value</th><th>Points price provenance</th></tr>
{rows}
</table>
</body>
</html>"""

    with open(output_file, "w") as f:
        f.write(html_content)
    print(f"Results exported to {output_file}")
