"""Format optimizer results for CLI output.

BUILD, THEN PRINT. The table cells, the verdict label, the trip headline, the
totals rows, the trip notes and the residue rows are produced by builders
(`leg_table_cells`, `trip_headline`, `trip_totals_rows`, `trip_notes`,
`residue_rows`, ...) that return plain text plus a rich style, and every
`print_*` below prints FROM them. The local UI (src/ui/) renders the same
builders, so the page can never say something cleaner than the terminal: it is
the terminal's own sentence. tests/test_cli_output_unchanged.py pins the bytes.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from rich.console import Console
from rich.markup import escape
from rich.table import Table

from src import config
from src.live_trip import LIVE_SOURCE, VERDICT_NO_LIVE_DATA, supersession_lines
from src.models import LegResult, LiveQueryState, PointsProvenance, Strategy
from src.optimizer import (
    VERDICT_APD_UNKNOWN,
    VERDICT_AWARD_UNATTRIBUTED,
    VERDICT_INDIRECT_PATH,
    VERDICT_PARTY_NOT_PRICED,
)


# ---------------------------------------------------------------------------
# Builder primitives: text + rich style, never parsed back out of text
# ---------------------------------------------------------------------------


@dataclass
class Seg:
    """A run of text in one rich style ("" = unstyled). `esc` marks text the
    terminal prints ESCAPED (it may contain square brackets rich would read as
    markup) - the UI always shows `text` as-is."""

    text: str
    style: str = ""
    esc: bool = False

    def markup(self) -> str:
        body = escape(self.text) if self.esc else self.text
        return f"[{self.style}]{body}[/{self.style}]" if self.style else body

    def to_json(self) -> Dict[str, str]:
        return {"text": self.text, "style": self.style}


def segs_markup(segs: List[Seg]) -> str:
    return "".join(s.markup() for s in segs)


def segs_text(segs: List[Seg]) -> str:
    return "".join(s.text for s in segs)


@dataclass
class Cell:
    """One table cell. `kind` is set from ENGINE FIELDS, never from the text."""

    segments: List[Seg]
    kind: str = "text"

    @property
    def text(self) -> str:
        return segs_text(self.segments)

    def markup(self) -> str:
        return segs_markup(self.segments)

    def to_json(self) -> Dict[str, object]:
        return {"segments": [s.to_json() for s in self.segments], "kind": self.kind}


@dataclass
class Row:
    """One two-column row (totals, residue): label and value, each styled."""

    label: List[Seg]
    value: List[Seg]
    group: str = "totals"

    def to_json(self) -> Dict[str, object]:
        return {
            "label": segs_text(self.label),
            "value": segs_text(self.value),
            "label_style": self.label[0].style if self.label else "",
            "value_style": self.value[0].style if self.value else "",
            "label_segments": [s.to_json() for s in self.label],
            "value_segments": [s.to_json() for s in self.value],
            "group": self.group,
        }


def _one(text: str, style: str = "") -> List[Seg]:
    return [Seg(text, style)]


def _taxes_are_what_is_unknown(r) -> bool:
    """True when the unknown on this leg's points side is the award's TAXES."""
    cand = getattr(r, "best_points", None)
    return bool(
        cand is not None
        and (
            getattr(cand, "taxes_unknown", False)
            or getattr(cand, "taxes_unconvertible", False)
        )
    )


def _be_subject(r) -> str:
    """What a break-even figure is a break-even ON. Never 'the surcharge' alone
    when the taxes are unknown too - that reads as though they were known."""
    return (
        "the total of its unknown taxes and any carrier surcharge"
        if _taxes_are_what_is_unknown(r)
        else "the surcharge"
    )


def _names_surcharge(totals: Dict) -> bool:
    """
    Whether the trip range contains an unknown CARRIER SURCHARGE. Always True on
    a run with no unknown award taxes (the wording there is unchanged); on a run
    that has them, only when some leg really carries an unknown surcharge - a
    caveat naming an ingredient the run does not contain is its own falsehood.
    """
    return bool(totals.get("legs_any_surcharge_unknown")) or not totals.get(
        "legs_taxes_unknown"
    )


def _high_end_assumptions(totals: Dict) -> List[str]:
    parts = []
    if _names_surcharge(totals):
        parts.append("every unknown surcharge is $0")
    if totals.get("legs_taxes_unknown"):
        parts.append("unknown award taxes add nothing beyond any UK APD shown")
    if totals.get("legs_apd_unknown"):
        parts.append("the departure tax is $0")
    return parts


def metal_lines(cand) -> List[str]:
    """
    The operating-airline lines for one candidate, or [] when the lookup was
    never engaged. Plain text; callers ESCAPE it, because the parser label is in
    square brackets and rich would otherwise read it as markup and delete it.
    """
    metal = getattr(cand, "metal", None)
    if metal is None:
        return []
    lines = [metal.render()]
    if metal.flights:
        lines.append("flights: " + "; ".join(metal.flights))
    if metal.excluded_mixed:
        lines.append(
            f"{metal.excluded_mixed} further itinerar"
            f"{'y' if metal.excluded_mixed == 1 else 'ies'} at this price fly part "
            f"of the distance in a lower cabin; not counted"
        )
    if metal.other_price_note:
        lines.append(metal.other_price_note)
    if metal.trip_taxes_note:
        lines.append(f"per-itinerary taxes: {metal.trip_taxes_note}")
    note = getattr(cand, "metal_surcharge_note", "")
    if note:
        lines.append(note)
    return lines


def _trips_metal_label(carrier_source: str) -> str:
    """The escaped parser label for a metal line whose metal came from the trips
    parse, while the parser is unverified; "" otherwise (Re-test 2, R2-7)."""
    from src import seats_trips
    from src.models import METAL_PROVENANCE_TRIPS

    if carrier_source != METAL_PROVENANCE_TRIPS:
        return ""
    label = seats_trips.trips_parser_label()
    return f" {escape(label)}" if label else ""


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


def print_alternatives_one(r: LegResult, console: Console) -> bool:
    """One leg's same-metal block. Prints nothing and returns False when the
    leg has no alternatives."""
    if not r.alternatives:
        return False
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
            + (f" {escape(alt.metal_label)}" if alt.metal_label else "")
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
        console.print(f"    [dim]{escape(alt.note)}[/dim]")
    return True


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
        if print_alternatives_one(r, console):
            any_shown = True
    if not any_shown:
        console.print(
            "\n[dim]No same-metal alternatives were found. An alternative requires a "
            "CONFIRMED operating carrier and a surcharge row for another program on "
            "that same metal; data/surcharges.csv currently covers each program on "
            "its OWN metal only.[/dim]"
        )


def residue_rows(report: Dict[str, Dict]) -> List[List[Seg]]:
    """Rows of the residue table: currency, starting, spent, remaining, note."""
    rows: List[List[Seg]] = []
    for currency, row in sorted(report.items()):
        if row["unconstrained"]:
            rows.append([
                Seg(currency), Seg("unconstrained"), Seg(f"{row['spent']:,}"),
                Seg("unconstrained"),
                Seg("no balance supplied, so nothing can be reconciled"),
            ])
            continue
        note = Seg("")
        if row["overdrawn"]:
            note = Seg("OVERDRAWN - this plan spends more than the balance", "red")
        elif row["too_small_to_use"]:
            note = Seg("left over but probably too small to fund anything", "yellow")
        rows.append([
            Seg(currency),
            Seg(f"{row['starting']:,}"),
            Seg(f"{row['spent']:,}"),
            Seg(f"{row['remaining']:,}"),
            note,
        ])
    return rows


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

    for row in residue_rows(report):
        table.add_row(*[s.markup() for s in row])
    console.print()
    console.print(table)


# ---------------------------------------------------------------------------
# Leg-level reporting (cash vs points, head to head)
# ---------------------------------------------------------------------------


VERDICT_WORDS = {
    "points": ("POINTS", "bold magenta"),
    "cash": ("PAY CASH", "bold green"),
    "cash (no points path)": ("PAY CASH (no path)", "bold green"),
    "cash (points unpriced)": ("PAY CASH (pts unpriced)", "bold yellow"),
    "cash (points blocked)": ("PAY CASH (pts blocked)", "bold yellow"),
    # v3. Says NOTHING about award space - that is why it is separate
    # from "no path", whose reason text is a claim about partnerships.
    VERDICT_NO_LIVE_DATA: ("PAY CASH (no live pts data)", "bold yellow"),
    # v3 fix, finding M-5. The response returned real awards and named
    # no program for them. Also says nothing about partnerships.
    VERDICT_AWARD_UNATTRIBUTED: ("PAY CASH (award unattributed)", "bold yellow"),
    # A path exists in two hops (UR -> BA Avios -> combine). Not scored,
    # and not "no path".
    VERDICT_INDIRECT_PATH: ("PAY CASH (indirect, not scored)", "bold yellow"),
    VERDICT_PARTY_NOT_PRICED: ("PAY CASH (party of N not priced)", "bold yellow"),
    # WAY (10). A GOVERNMENT departure tax is owed and its size is not
    # known, so the points side cannot be scored. Deliberately worded
    # like the surcharge-unknown cell above and deliberately NOT the
    # same cell: one is a carrier's YQ and this is HMRC's duty, and the
    # whole point of keeping APD out of the surcharge table is that the
    # reader can tell which of the two is missing.
    VERDICT_APD_UNKNOWN: ("WITHHELD (APD unknown)", "bold red"),
}


def verdict_label(r: LegResult) -> List[Seg]:
    """The verdict cell: the label, then any !SENSITIVE / !DATE tags."""
    if r.verdict == "cash (surcharge unknown)":
        word = (
            ("WITHHELD (taxes unknown)", "bold red")
            if _taxes_are_what_is_unknown(r)
            else ("WITHHELD (surch unknown)", "bold red")
        )
    else:
        word = VERDICT_WORDS[r.verdict]
    if r.demoted_for_trip_balance:
        # C-3: this leg WOULD have been points; the trip ran out of balance.
        word = ("PAY CASH (trip out of points)", "bold yellow")
    segs = [Seg(*word)]
    if r.verdict_sensitive:
        segs += [Seg(" "), Seg("!SENSITIVE", "yellow")]
    if r.scored_off_date:
        segs += [Seg(" "), Seg(f"!DATE {r.scoring_date}", "yellow")]
    return segs


def verdict_kind(r: LegResult) -> str:
    """points | cash | cash_qualified | withheld - from the verdict CODE and the
    demotion flag, never from the words. Every yellow CLI verdict is qualified."""
    if r.demoted_for_trip_balance:
        return "cash_qualified"
    if r.verdict == "points":
        return "points"
    if r.verdict in ("cash (surcharge unknown)", VERDICT_APD_UNKNOWN):
        return "withheld"
    if r.verdict in ("cash", "cash (no points path)"):
        return "cash"
    return "cash_qualified"


@dataclass
class LegCells:
    """The twelve cells of one per-leg table row."""

    leg: Cell
    what: Cell
    cash: Cell
    cash_pts: Cell
    path: Cell
    points: Cell
    surcharge: Cell
    surch_source: Cell
    score_points: Cell
    score_cash: Cell
    provenance: Cell
    verdict: Cell

    ORDER = ("leg", "what", "cash", "cash_pts", "path", "points", "surcharge",
             "surch_source", "score_points", "score_cash", "provenance", "verdict")

    def row_markup(self) -> List[str]:
        return [getattr(self, name).markup() for name in self.ORDER]

    def to_json(self) -> Dict[str, object]:
        return {name: getattr(self, name).to_json() for name in self.ORDER}


def _path_cells(r: LegResult):
    """(path Cell, points Cell) for the 'Best points path' and 'Points' columns."""
    if r.best_points is not None and r.points_path:
        t = r.points_path.transfers[0] if r.points_path.transfers else None
        path_desc = f"{r.best_points.program} @ {t.ratio}" if t else r.best_points.program
        return Cell([Seg(path_desc)], "path"), Cell([Seg(f"{r.points_required:,}")], "number")
    if r.verdict == "cash (points blocked)":
        return Cell([Seg("partner exists, path blocked")], "blocked"), Cell([Seg("-")], "none")
    if r.break_even_programs:
        return (
            Cell([Seg(f"{r.break_even_programs[0]} (no price)")], "no_price"),
            Cell([Seg(f"<{r.break_even_points:,}?")], "break_even_points"),
        )
    if r.verdict == VERDICT_INDIRECT_PATH:
        # "none - not a partner" here would be false: UR reaches it in two
        # hops. The cell names the program and says it was not scored.
        _ind = min(
            (c for c in r.leg.points_candidates
             if getattr(c, "indirect_ur_path", "")),
            key=lambda c: c.points,
        )
        return (
            Cell([Seg(f"{_ind.program} (indirect, not scored)")], "indirect"),
            Cell([Seg(f"{_ind.points:,}")], "number_not_scored"),
        )
    if r.verdict == VERDICT_PARTY_NOT_PRICED:
        return (
            Cell([Seg(f"priced for ONE seat - {r.leg.travelers} travelling")], "party"),
            Cell([Seg("-")], "none"),
        )
    if r.verdict == VERDICT_AWARD_UNATTRIBUTED:
        # The same falsehood on the unattributed path: the program is not
        # NAMED, which says nothing about whether it is a partner.
        return Cell([Seg("program NOT NAMED - no claim")], "unattributed"), Cell([Seg("-")], "none")
    return Cell([Seg("none - not a partner")], "no_partner"), Cell([Seg("-")], "none")


def _score_points_cell(r: LegResult) -> Cell:
    if r.has_points_path and r.points_total_score_usd != float("inf"):
        if r.verdict_sensitive:
            return Cell([Seg(
                f"{_money(r.points_score_low_usd)}-{_money(r.points_score_high_usd)}",
                "yellow",
            )], "range")
        return Cell([Seg(_money(r.points_total_score_usd))], "number")
    if r.surcharge_cannot_change_verdict and r.points_floor_usd is not None:
        # The surcharge is unknown but INERT: points already lose at its $0
        # floor. Show the floor, not a "$0.00 break-even" - that reads like a
        # $0 surcharge, which is the exact confusion v1 exists to remove.
        return Cell([Seg(f">= {_money(r.points_floor_usd)}")], "floor")
    if r.break_even_surcharge_usd is not None:
        # When the award's TAXES are what is unknown, the break-even is on
        # taxes plus surcharge. "win if surch < $182" on a leg whose taxes
        # were never reported reads as though the taxes were known to be $0.
        _cand = r.best_points
        _what = (
            "taxes+surch"
            if _cand is not None
            and (
                getattr(_cand, "taxes_unknown", False)
                or getattr(_cand, "taxes_unconvertible", False)
            )
            else "surch"
        )
        return Cell([Seg(f"? (win if {_what} < {_money(r.break_even_surcharge_usd)})", "red")],
                    "break_even")
    return Cell([Seg("-")], "none")


def leg_table_cells(r: LegResult) -> LegCells:
    """One per-leg row. `print_leg_results` prints these; the UI renders them."""
    path_cell, points_cell = _path_cells(r)

    # SURCHARGE COLUMN. "UNKNOWN" is rendered as a word, never as a blank and
    # never as $0.00. The whole point of v1 is that those are different.
    if r.surcharge is None:
        surch = Cell([Seg("-")], "none")
        prov = Cell([Seg("-")], "none")
    elif r.surcharge.is_known:
        surch = Cell([Seg(r.surcharge.render())], "known")
        prov = Cell([Seg(r.surcharge.confidence)], "known")
    else:
        surch = Cell([Seg("UNKNOWN", "bold red")], "unknown")
        prov = Cell([Seg("unknown", "red")], "unknown")

    # A LEG WITH NO PRICEABLE CASH HAS NO CASH NUMBER, and `r.cash_usd`'s
    # default of 0.0 is not one. Before the L-1 fix this state was
    # unreachable from the CLI (an unconvertible currency aborted the whole
    # run), and reaching it now must not reintroduce `unknown -> $0` in the
    # very column the tool exists to keep honest.
    if r.best_cash is not None:
        cash = Cell([Seg(_money(r.cash_usd))], "number")
        cash_pts = Cell([Seg(f"{r.cash_as_points_equivalent:,}")], "number")
    else:
        cash = Cell([Seg("UNKNOWN", "bold red")], "unknown")
        cash_pts = Cell([Seg("-")], "unknown")

    score_cash = Cell(
        [Seg(_money(r.cash_total_score_usd))],
        "number" if r.cash_total_score_usd != float("inf") else "unavailable",
    )
    return LegCells(
        leg=Cell([Seg(r.leg.id)]),
        what=Cell([Seg(_short_label(r.leg))]),
        cash=cash,
        cash_pts=cash_pts,
        path=path_cell,
        points=points_cell,
        surcharge=surch,
        surch_source=prov,
        score_points=_score_points_cell(r),
        score_cash=score_cash,
        provenance=Cell(
            _points_provenance_segs(r) + [Seg(" | ")] + _cash_provenance_segs(r),
            points_provenance_kind(r),
        ),
        verdict=Cell(verdict_label(r), verdict_kind(r)),
    )


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
        table.add_row(*leg_table_cells(r).row_markup())

    console.print(table)


def points_provenance_kind(r: LegResult) -> str:
    """live | snapshot | badge | no_space | api_failed | budget | unreadable | none,
    from the engine's provenance and outcome state."""
    prov = r.leg.points_provenance
    outcome = r.leg.live_outcome
    if prov is PointsProvenance.LIVE:
        return "live"
    if prov is PointsProvenance.SNAPSHOT:
        return "snapshot"
    if prov is PointsProvenance.BADGE_FALLBACK:
        return "badge"
    if outcome is not None:
        return {
            LiveQueryState.NO_AWARD_SPACE: "no_space",
            LiveQueryState.API_ERROR: "api_failed",
            LiveQueryState.BUDGET_EXHAUSTED: "budget",
            LiveQueryState.ANSWERED_UNREADABLE: "unreadable",
        }.get(outcome.state, "none")
    return "none"


def _points_provenance_segs(r: LegResult) -> List[Seg]:
    """
    The points side's provenance, and for an unavailable leg, WHY.

    An API failure and an absence of award space both land on "unavailable", and
    the whole design turns on their never being rendered alike - so they are
    given different words and different colours even in a one-word table cell.
    """
    prov = r.leg.points_provenance
    outcome = r.leg.live_outcome

    if prov is PointsProvenance.LIVE:
        return [Seg("live", "bold green")]
    # A replayed leg's points came from committed bytes. It rendered as "none",
    # which is the word for a leg with NO points data - the opposite of the truth.
    if prov is PointsProvenance.SNAPSHOT:
        return [Seg("snapshot", "bold cyan")]
    if prov is PointsProvenance.BADGE_FALLBACK:
        return [Seg("badge", "yellow")]

    if outcome is not None:
        if outcome.state is LiveQueryState.NO_AWARD_SPACE:
            return [Seg("none: no award space", "yellow")]
        if outcome.state is LiveQueryState.API_ERROR:
            return [Seg("none: API FAILED", "bold red")]
        if outcome.state is LiveQueryState.BUDGET_EXHAUSTED:
            return [Seg("none: BUDGET", "bold red")]
        if outcome.state is LiveQueryState.ANSWERED_UNREADABLE:
            # The sixth state gets its own words for the same reason the other
            # five do: an unreadable answer and an empty calendar must never
            # look alike, not even in a one-word table cell (finding C-1).
            return [Seg("none: UNREADABLE", "bold red")]
    return [Seg("none", "dim")]


def _points_provenance_cell(r: LegResult) -> str:
    return segs_markup(_points_provenance_segs(r))


def _cash_provenance_segs(r: LegResult) -> List[Seg]:
    """
    Where the cash price came from, and when.

    Cash is ALWAYS a capture - there is no cash-price API in scope and there will
    not be one - so this column never says "live". What it can say is "unknown",
    which means nobody recorded the origin of the number, and that is worth
    seeing next to a live points price.
    """
    provenance = r.leg.cash_provenance or "unknown"
    when = f" {r.leg.cash_captured_on}" if r.leg.cash_captured_on else ""
    if provenance == "unknown":
        segs = [Seg("unknown", "yellow")]
    else:
        short = {
            "captured_screenshot": "screenshot",
            "captured_booking_page": "booking pg",
        }.get(provenance, provenance)
        segs = [Seg(short)]
    return segs + ([Seg(when)] if when else [])


def _cash_provenance_cell(r: LegResult) -> str:
    return segs_markup(_cash_provenance_segs(r))


def print_leg_detail(results: List[LegResult], console: Console = None) -> None:
    """Print the reasoning, flags, and warnings for each leg."""
    console = console or Console()
    console.print("\n[bold]Per-leg detail[/bold]")
    for r in results:
        print_leg_detail_one(r, console)


def print_leg_detail_one(r: LegResult, console: Console) -> None:
    """One leg's detail block (the loop body of `print_leg_detail`)."""
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
        # The legacy line stays whenever it printed before: the lookup's
        # lines are ADDED under it, never swapped in for what the scorer
        # itself uses as the metal.
        if r.best_points.operating_carrier:
            console.print(
                f"        metal: {r.best_points.operating_carrier} "
                f"(source: {r.best_points.carrier_source}), "
                f"cabin {r.best_points.cabin}"
                + _trips_metal_label(r.best_points.carrier_source)
            )
        for line in metal_lines(r.best_points):
            console.print(f"        {escape(line)}")
    if r.surcharge is not None:
        if r.surcharge.is_known:
            console.print(
                f"  Surcharge: {r.surcharge.render()} "
                f"[{r.surcharge.confidence}]"
                + (f" via {r.surcharge.matched_rule}" if r.surcharge.matched_rule else "")
                # The rule is keyed by the looked-up metal when that is where
                # the metal came from (R2-7).
                + (
                    _trips_metal_label(r.best_points.carrier_source)
                    if r.best_points is not None
                    else ""
                )
            )
            if r.surcharge.source:
                console.print(f"        source: {r.surcharge.source}")
            if r.surcharge.notes:
                console.print(f"        [yellow]{escape(r.surcharge.notes)}[/yellow]")
        else:
            console.print(
                "  [bold red]Surcharge: UNKNOWN - this is NOT $0.[/bold red]"
            )
            if r.break_even_surcharge_usd is not None:
                console.print(
                    f"        [red]Break-even: points beat cash only if "
                    f"{_be_subject(r)} is below "
                    f"{_money(r.break_even_surcharge_usd)}.[/red]"
                )
            if r.surcharge.notes:
                console.print(f"        [dim]{escape(r.surcharge.notes)}[/dim]")
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
        console.print(f"  [dim]note: {escape(note)}[/dim]")
    for flag in r.leg.data_flags:
        console.print(f"  [yellow]FLAG: {escape(flag)}[/yellow]")
    # v5 STEP 7. The APD line gets its own prefix, not "UNVERIFIED:".
    # An ADDED government tax is not an unverified claim - it is a rate read
    # off gov.uk and applied - and printing it under the same word as an
    # uncorroborated Google badge would flatten the difference between "we
    # looked this up" and "somebody typed this".
    for prefix, w, style in leg_warning_lines(r):
        console.print(f"  [{style}]{prefix}: {escape(w)}[/{style}]")


def leg_warning_lines(r: LegResult) -> List[tuple]:
    """(prefix, warning, style) for each of the leg's warnings, as printed.

    v5 STEP 7. The APD line gets its own prefix, not "UNVERIFIED:". An ADDED
    government tax is not an unverified claim - it is a rate read off gov.uk
    and applied - and printing it under the same word as an uncorroborated
    Google badge would flatten the difference between "we looked this up" and
    "somebody typed this".
    """
    out = []
    for w in r.warnings:
        if w.startswith("UK AIR PASSENGER DUTY on "):
            style = "yellow" if "IT IS NOT ADDED HERE" in w or "UNKNOWN" in w else "cyan"
            out.append(("APD", w, style))
            continue
        out.append(("UNVERIFIED", w, "red"))
    return out


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
        # MR5-4: archiving is opt-in, so say plainly when nothing is being
        # archived rather than printing a bare "None" that reads like a bug.
        if cache.snapshot_dir is None:
            console.print(
                "  snapshots: [yellow]NOT ARCHIVED - this cache was given no "
                "snapshot directory[/yellow]"
            )
        else:
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
    _print_metal_banner(getattr(opts, "metal_report", None), console)
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


def _print_metal_banner(report, console: Console) -> None:
    """The operating-airline lookup's share of the banner. Nothing if not engaged."""
    if report is None:
        return
    from src import seats_trips

    counts = report.by_status
    cap = "no cap applies to a replay" if report.replay else f"cap {report.cap}"
    console.print(
        f"  itinerary lookups (--trips {report.mode}, {cap}): "
        f"{report.candidates} live award(s) - "
        f"{report.requests_sent} request(s) sent, "
        f"{report.served_from_cache} served from the disk cache, "
        f"{report.replayed} replayed from a snapshot"
    )
    console.print(
        f"    operating airline known {counts.get('known', 0)}, "
        f"ambiguous {counts.get('ambiguous', 0)}, "
        f"NOT KNOWN {counts.get('unknown', 0)}, "
        f"NOT LOOKED UP {counts.get('not_looked_up', 0)}, "
        f"NOT RECORDED {counts.get('not_recorded', 0)} "
        f"({report.not_looked_up_missing} of those not looked up or not recorded "
        f"are gaps in this run; the rest cannot change the answer)"
    )
    if report.search_calls is not None:
        console.print(
            f"  Seats.aero calls spent this run: {report.search_calls} search + "
            f"{report.trips_calls} trips"
        )
    if report.rate_limited:
        console.print(
            "  [bold red]Seats.aero rate-limited a request in this run (HTTP 429); "
            "no itinerary request was sent after it (answers already in the disk "
            "cache were still read).[/bold red]"
        )
    label = seats_trips.trips_parser_label()
    if label:
        console.print(f"  [yellow]{escape(label)}[/yellow]")


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
        print_live_leg_detail_one(r, console)


def print_live_leg_detail_one(r: LegResult, console: Console) -> bool:
    """One leg's live block. Prints nothing (returns False) for a leg that was
    not queried."""
    outcome = r.leg.live_outcome
    if outcome is None or outcome.state is LiveQueryState.NOT_QUERIED:
        return False

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
    _print_other_lookups(r, console)
    _print_flexible_findings(r, console)
    return True


def _print_live_scoring_block(r: LegResult, console: Console) -> None:
    """The floor / break-even block. Rendered for EVERY live or replayed leg."""
    # A REPLAY scores the same Awards through the same path; its unknown-tax
    # line ("NONE USABLE ... IT IS NOT $0") and its floor must print too.
    if r.leg.points_provenance not in (PointsProvenance.LIVE, PointsProvenance.SNAPSHOT):
        return
    if not r.best_points:
        return

    cand = r.best_points
    kind = (
        "replayed award"
        if r.leg.points_provenance is PointsProvenance.SNAPSHOT
        else "live award"
    )
    console.print(
        f"  {kind}: {cand.program}  {cand.cabin}  {cand.points:,} points"
    )
    for line in metal_lines(cand):
        console.print(f"     {escape(line)}")
    whole_because = getattr(cand, "observed_taxes_whole_because", "") or ""
    if cand.surcharge_captured and whole_because.startswith("yq_included_verified:"):
        evidence = whole_because.split(":", 1)[1]
        console.print(
            f"     taxes from the API: {_money(cand.cash_surcharge)} - taken as "
            f"the COMPLETE carrier-side cash figure because Seats.aero's taxes for "
            f"this source were VERIFIED to include carrier-imposed surcharges "
            f"(evidence: {escape(evidence)}). NO modelled surcharge is added."
        )
    elif cand.surcharge_captured:
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
    elif getattr(cand, "taxes_unknown", False):
        # Every other way a live award's taxes are unknown - none sent, a source
        # that does not report them, or a 0 that means "not reported". Same
        # loudness as C-2: this used to print nothing, and score as $0.
        console.print(
            "     [bold red]taxes from the API: NONE USABLE, so the cash side of "
            "this award is UNKNOWN. IT IS NOT $0.[/bold red]"
        )
        console.print(f"     [red]{cand.observed_taxes_note}[/red]")
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
            else "with NO usable tax figure to add "
        )
        # A floor that silently contains UK APD reads as "points + nothing"; the
        # duty is named whenever it was added to this leg's points side.
        apd_clause = (
            f"+ UK Air Passenger Duty of {_money(r.apd_added_usd)} "
            if getattr(r, "apd_added_usd", 0.0)
            else ""
        )
        console.print(
            f"     [bold]floor {_money(floor) if floor is not None else 'n/a'}[/bold]"
            f"  (points at the run's valuation {taxes_clause}{apd_clause}, with the "
            f"carrier surcharge at its $0 floor - the least this can possibly cost)"
        )
        if r.break_even_surcharge_usd is not None:
            console.print(
                (
                    f"     points win ONLY if its unknown taxes plus any carrier "
                    f"surcharge are below "
                    if _taxes_are_what_is_unknown(r)
                    else "     points win ONLY if the carrier surcharge above those "
                    "taxes is below "
                )
                + f"[bold]{_money(r.break_even_surcharge_usd)}[/bold]"
            )
        console.print(
            f"     [bold red]surcharge UNKNOWN - this is NOT $0.[/bold red] "
            f"{escape(r.surcharge.notes)}"
        )
        if r.surcharge_cannot_change_verdict:
            console.print(
                "     [green]...and it does not matter here: the floor already "
                "loses to cash, and a surcharge can only ADD to the points side. "
                "The verdict is CERTAIN despite the unknown.[/green]"
            )
    # THE HONESTY INVARIANT: a live price never renders without its timestamp.
    if cand.source == LIVE_SOURCE:
        console.print(f"     [dim]provenance: {cand.source} - {escape(cand.source_note)}[/dim]")


def _print_other_lookups(r: LegResult, console: Console) -> None:
    """
    Every lookup this run made or read for an award that is NOT the chosen one.

    Counters and the trip block follow the chosen award only, but a lookup that
    was paid for (or read from a recording) must show up somewhere: a banner
    count nobody can trace to a leg is not an answer.
    """
    from src.models import MetalStatus

    others = [
        c for c in r.leg.points_candidates
        if c is not r.best_points
        and getattr(c, "metal", None) is not None
        and c.metal.status is not MetalStatus.NOT_LOOKED_UP
    ]
    for cand in others:
        console.print(
            f"  other live award {escape(str(cand.program))} {cand.cabin} "
            f"{cand.points:,} points (not the chosen option):"
        )
        for line in metal_lines(cand):
            console.print(f"     {escape(line)}")


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


@dataclass
class Headline:
    """
    The trip headline, as the terminal prints it, in pieces the UI can draw.

    `text` + `suffix` is EXACTLY the CLI value cell ("2.04% - 11.03%" +
    "  (badge)"); `qualifier` is the suffix without its two leading spaces. A
    range or withheld headline carries NO single `pct`: the page cannot render
    a number it never receives.
    """

    state: str  # single | range | withheld | not_fundable
    text: str
    suffix: str
    label_segments: List[Seg]
    value_segments: List[Seg]
    pct: Optional[float] = None
    pct_low: Optional[float] = None
    pct_high: Optional[float] = None
    low_row: Optional[Row] = None
    high_row: Optional[Row] = None
    reason_row: Optional[Row] = None
    withheld_reason: Optional[str] = None
    funding_note: Optional[str] = None
    range_parts: List[Dict[str, object]] = field(default_factory=list)
    provenance: str = "badge"
    provenance_note: str = ""
    provenance_style: str = "yellow"
    legs_counted_text: str = ""
    manifest_rows: List[Row] = field(default_factory=list)
    hash_covers_margin: bool = False
    manifest_hash: str = ""

    @property
    def qualifier(self) -> str:
        return self.suffix.strip()

    def to_json(self) -> Dict[str, object]:
        def _row(r: Optional[Row]):
            return None if r is None else {
                "label": segs_text(r.label).strip(), "value": segs_text(r.value)}

        return {
            "state": self.state,
            "pct": self.pct if self.state == "single" else None,
            "pct_low": self.pct_low if self.state == "range" else None,
            "pct_high": self.pct_high if self.state == "range" else None,
            "text": self.text,
            "qualifier": self.qualifier,
            "cli_value": segs_text(self.value_segments),
            "low_row": _row(self.low_row),
            "high_row": _row(self.high_row),
            "range_parts": list(self.range_parts),
            "withheld_reason": self.withheld_reason,
            "funding_note": self.funding_note,
            "provenance": self.provenance,
            "provenance_note": self.provenance_note,
            "provenance_style": self.provenance_style,
            "legs_counted_text": self.legs_counted_text,
            "manifest_hash": self.manifest_hash or None,
            "hash_covers_margin": self.hash_covers_margin,
            "manifest_rows": [r.to_json() for r in self.manifest_rows],
        }


def trip_headline(totals: Dict) -> Headline:
    """The headline rows of the totals table, and what the UI needs around them."""
    executable = totals.get("trip_funding_executable", True)
    funding_note = totals.get("trip_funding_note", "")

    # v3 RULE (plan section 4.7, rule 2): THE PERCENTAGE MAY NEVER BE PRINTED
    # WITHOUT ITS PROVENANCE LINE. They are emitted by one call so they cannot
    # be separated by accident. A single number that hides its own provenance
    # is worse than no number - and `--require-all-live` withholds the
    # percentage entirely rather than let a mixed one be quoted.
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
    # v5 FIX C-2. THE QUALIFIER, NOT JUST THE HASH, RIDES WITH THE NUMBER.
    #
    # The hash used to be appended to every percentage on every --from-snapshot
    # run, unconditionally. A replay in which not one snapshot contributed a
    # point still printed the OFFLINE Google-badge margin - byte-identical -
    # with `mh_...` glued to it, while the qualifier that corrects it
    # (`badge_fallback`) sat two rows below, where a copy-paste loses it. The
    # only token on the number was the false one.
    #
    # Step 6's acceptance criterion is "the qualifier is on the same line". It
    # is now implemented for EVERY provenance, and the hash is quoted only
    # against a number the hashed bytes actually produced: `snapshot` means
    # every scoreable leg was replayed from them. Anything else says what the
    # number really is, on the number.
    hash_covers_margin = bool(manifest_hash) and provenance == "snapshot"
    if hash_covers_margin:
        qualifier = f"  ({provenance} {manifest_hash})"
    elif manifest_hash:
        qualifier = f"  ({provenance} - NOT from {manifest_hash})"
    else:
        qualifier = f"  ({provenance})"
    # Kept as a separate name so the "percentage never without its hash" rule
    # reads the same as before wherever the hash IS earned.
    hash_suffix = qualifier

    h: Headline
    if not executable:
        # C-3: an unfundable plan gets no percentage at all. There is nothing to
        # qualify - the plan does not exist.
        h = Headline(
            state="not_fundable",
            text="WITHHELD - PLAN NOT FUNDABLE",
            suffix="",
            label_segments=_one("Optimizer beats paying cash by", "bold red"),
            value_segments=_one("WITHHELD - PLAN NOT FUNDABLE", "bold red"),
            reason_row=Row(_one("  withheld because", "red"), _one(f"{funding_note}", "red"),
                           group="headline"),
            funding_note=funding_note,
        )
    elif withheld:
        reason = totals.get("margin_withheld_reason") or (
            "--require-all-live and provenance is " + repr(provenance)
        )
        h = Headline(
            state="withheld",
            text="WITHHELD",
            suffix="",
            label_segments=_one("Optimizer beats paying cash by", "bold red"),
            value_segments=_one("WITHHELD", "bold red"),
            reason_row=Row(_one("  withheld because", "red"), _one(f"{reason}", "red"),
                           group="headline"),
            withheld_reason=reason,
        )
    elif totals.get("headline_is_a_range"):
        low, high = totals["beat_cash_pct_low"], totals["beat_cash_pct_high"]
        text = f"{low:.2f}% - {high:.2f}%"
        h = Headline(
            state="range",
            text=text,
            suffix=hash_suffix,
            label_segments=_one("Optimizer beats paying cash by", "bold"),
            value_segments=_one(f"{text}{hash_suffix}", "bold"),
            pct_low=low,
            pct_high=high,
            low_row=Row(
                _one("  low end = what is actually defensible", "dim"),
                _one(f"{low:.2f}%{hash_suffix}", "dim"),
                group="headline",
            ),
            # MR5-1, WAY (10), the same sentence one row up from the caveat. This
            # label names what the optimistic end ASSUMES AWAY, so on a run where
            # part of that is an unknown departure tax it has to say so - naming
            # only the surcharge is the identical falsehood the caveat had.
            high_row=Row(
                _one("  high end = only if " + " AND ".join(_high_end_assumptions(totals)), "dim"),
                _one(f"{high:.2f}%{hash_suffix}", "dim"),
                group="headline",
            ),
            range_parts=_range_parts(totals),
        )
    else:
        pct = totals["beat_cash_pct"]
        text = f"{pct:.2f}%"
        h = Headline(
            state="single",
            text=text,
            suffix=hash_suffix,
            label_segments=_one("Optimizer beats paying cash by", "bold"),
            value_segments=_one(f"{text}{hash_suffix}", "bold"),
            pct=pct,
        )

    h.provenance = provenance
    h.provenance_note = provenance_note
    h.manifest_hash = manifest_hash
    h.hash_covers_margin = hash_covers_margin
    if manifest_hash:
        # Rows that carry NO percentage, so their length cannot wrap a number
        # away from its hash. C-2: when the margin did NOT come from these
        # bytes, this row says so in its own label - a reader who sees a hash
        # anywhere on the page must not have to work out what it covers.
        h.manifest_rows = [
            Row(
                _one("  replayed from manifest", "cyan")
                if hash_covers_margin
                else _one("  replayed from manifest (these bytes produced\n"
                          "NO part of the margin above)", "bold yellow"),
                _one(f"{manifest_hash}", "cyan")
                if hash_covers_margin
                else _one(f"{manifest_hash}", "bold yellow"),
                group="manifest",
            ),
            Row(
                _one("  snapshots / parser at capture / parser now", "cyan"),
                _one(
                    f"{int(totals.get('manifest_snapshots', 0))} / "
                    f"{totals.get('manifest_parser_at_capture', 'unknown')} / "
                    f"{totals.get('manifest_parser_now', 'unknown')}",
                    "cyan",
                ),
                group="manifest",
            ),
        ]

    style = {
        "live": "green",
        "snapshot": "cyan",
        "mixed": "bold yellow",
        "badge": "yellow",
        "badge_fallback": "bold yellow",
        "none": "dim",
    }.get(provenance, "yellow")
    h.provenance_style = style
    counted = int(totals.get("legs_points_live", 0))
    label = "live"
    if int(totals.get("legs_points_snapshot", 0)):
        counted = int(totals.get("legs_points_snapshot", 0)) + counted
        label = "live or replayed"
    h.legs_counted_text = (
        f"{counted} of {int(totals.get('legs_flight_total', 0))} legs {label}"
    )
    return h


def _range_parts(totals: Dict) -> List[Dict[str, object]]:
    """What the range is made of, named from the SAME predicates the caveat
    and the high-end label use - never from the text."""
    parts: List[Dict[str, object]] = []
    if _names_surcharge(totals):
        parts.append({
            "part": "surcharge",
            "legs": list(totals.get("legs_surcharge_unknown_ids") or []),
        })
    if totals.get("legs_taxes_unknown"):
        parts.append({"part": "award_taxes",
                      "legs": list(totals.get("legs_taxes_unknown_ids") or [])})
    if totals.get("legs_apd_unknown"):
        parts.append({"part": "apd", "legs": list(totals.get("legs_apd_unknown_ids") or [])})
    return parts


def trip_funding_banner(totals: Dict) -> List[Seg]:
    """
    THE BALANCE CEILING IS PRINTED FIRST, BEFORE ANY NUMBER (finding C-3).

    The overdraft used to appear in exactly one place: the residue table, in a
    column called "Note", which main.py prints AFTER this whole block. So a
    plan that spent 210,000 UR out of a 160,000 balance led with a clean
    22.22% and mentioned the impossibility three tables later. A recommendation
    you cannot execute is not a recommendation, and the reader has to know that
    before they read the number, not after.

    Returns the banner's lines (each one Seg), or [] when there is none.
    """
    executable = totals.get("trip_funding_executable", True)
    funding_note = totals.get("trip_funding_note", "")
    if not executable:
        return [
            Seg("=" * 78, "bold red"),
            Seg("THIS TRIP CANNOT BE FUNDED FROM YOUR BALANCE.", "bold red"),
            Seg(f"{funding_note}", "red"),
            Seg("The margin below is WITHHELD. Nothing is quoted for a plan the "
                "points do not exist for.", "red"),
            Seg("=" * 78, "bold red"),
        ]
    if totals.get("trip_balance_bound"):
        return [
            Seg("YOUR BALANCE IS THE BINDING CONSTRAINT ON THIS TRIP.", "bold yellow"),
            Seg(f"{funding_note}", "yellow"),
        ]
    return []


def trip_totals_rows(totals: Dict) -> List[Row]:
    """Every row of the totals table, in order, conditionals included. Each row
    carries its `group`: totals | headline | manifest | provenance."""
    rows: List[Row] = []
    rows.append(Row(_one("Pay cash for everything"), _one(_money(totals["all_cash_usd"]))))
    if totals.get("margin_withheld_reason"):
        # A trip withheld because legs were NOT PRICED has no recommendation
        # total and no saving: printing them would report "could not price" as a
        # dollar figure one row above the WITHHELD percentage.
        rows.append(Row(_one("Optimizer's recommendation"), _one("WITHHELD", "bold red")))
        rows.append(Row(_one("Saving"), _one("WITHHELD", "bold red")))
    else:
        rows.append(Row(_one("Optimizer's recommendation"),
                        _one(_money(totals["optimized_usd"]))))
        rows.append(Row(_one("Saving"), _one(_money(totals["savings_usd"]))))

    # THE HEADLINE. When any surcharge on the trip is a range or an unknown, the
    # honest headline is an INTERVAL. Printing the point estimate alone is what
    # produced v0's 16%.
    h = trip_headline(totals)
    rows.append(Row(h.label_segments, h.value_segments, group="headline"))
    for extra in (h.reason_row, h.low_row, h.high_row):
        if extra is not None:
            rows.append(extra)
    rows.extend(h.manifest_rows)

    style = h.provenance_style
    rows.append(Row(_one("  margin provenance", style), _one(f"{h.provenance}", style),
                    group="provenance"))
    rows.append(Row(_one(f"  {h.provenance_note}", style), _one(h.legs_counted_text, style),
                    group="provenance"))
    rows.append(Row(_one("Points spent"), _one(f"{int(totals['points_spent']):,}")))
    spend = totals.get("trip_points_spend") or {}
    if spend:
        rows.append(Row(
            _one("  drawn from"),
            _one(", ".join(f"{n:,} {c}" for c, n in sorted(spend.items()))),
        ))
    if totals.get("trip_legs_demoted_for_balance"):
        rows.append(Row(
            _one("Legs that would win on points but the\n"
                 "balance cannot fund (scored as cash)", "bold yellow"),
            _one(f"{', '.join(totals['trip_legs_demoted_for_balance'])}", "bold yellow"),
        ))
    rows.append(Row(_one("Cash still owed"), _one(_money(totals["cash_still_owed_usd"]))))
    rows.append(Row(_one("Legs where points win"),
                    _one(str(int(totals["legs_where_points_win"])))))
    # H-4: the never-priced legs are reported on their own row, so "NO UR path
    # at all" keeps meaning what it says.
    #
    # MR5-3. THIS ROW USED TO PRINT `legs_without_points_path - legs_never_priced`
    # AND IT PRINTED -1. The two counts come from DIFFERENT predicates - one
    # reads a VERDICT, the other a `points_absence` - and `annotate_live_verdicts`
    # rewrites the verdict on the live path while leaving the absence set, so
    # four legs left the first set, stayed in the second, and 3 - 4 reached the
    # screen as a count of legs. The footer of that same run named B5, B6, B7
    # correctly, because `main.py` builds it from the compound condition. There
    # is now ONE predicate - `optimizer.leg_has_no_partner` - and the table and
    # the footer both ask it. A derived count that can go negative is a count
    # nobody checked, so the derivation is gone rather than repaired.
    never_priced = int(totals.get("legs_never_priced", 0))
    rows.append(Row(_one("Legs with NO UR path at all"),
                    _one(str(int(totals["legs_no_partner"])))))
    if never_priced:
        rows.append(Row(
            _one("Legs whose points side was NEVER PRICED\n"
                 "(no award data - NOT a claim about partners)", "yellow"),
            _one(f"{never_priced} "
                 f"({', '.join(totals.get('legs_never_priced_ids') or [])})", "yellow"),
        ))
    rows.append(Row(
        _one("Legs where a UR partner exists but no\naward price was captured"),
        _one(str(int(totals.get("legs_points_unpriced", 0)))),
    ))
    if totals.get("legs_points_blocked"):
        rows.append(Row(
            _one("Legs where a path exists but was\nblocked by balance/stranding"),
            _one(str(int(totals["legs_points_blocked"]))),
        ))
    if totals.get("legs_surcharge_unknown"):
        rows.append(Row(
            _one("Legs where a points path exists but its\n"
                 "surcharge is UNKNOWN (NOT $0)", "red"),
            _one(f"{int(totals['legs_surcharge_unknown'])}", "red"),
        ))
    if totals.get("legs_party_pricing_unverified"):
        rows.append(Row(
            _one("Flight legs for 2+ travellers - points NOT\n"
                 "scored (award prices are per seat)", "bold yellow"),
            _one(f"{int(totals['legs_party_pricing_unverified'])} "
                 f"({', '.join(totals.get('legs_party_pricing_unverified_ids') or [])})",
                 "bold yellow"),
        ))
    # Counted at trip level since v3 (way ten) and never PRINTED there - a
    # counter nobody sees is not an answer to "what does the trip do with it".
    if totals.get("legs_award_unattributed"):
        rows.append(Row(
            _one("Legs carrying an award the response did NOT\n"
                 "attribute to a program - NOT scored, no claim", "yellow"),
            _one(f"{int(totals['legs_award_unattributed'])} "
                 f"({', '.join(totals.get('legs_award_unattributed_ids') or [])})", "yellow"),
        ))
    if totals.get("legs_indirect_path_unverified"):
        rows.append(Row(
            _one("Legs with an award reachable only INDIRECTLY\n"
                 "(UR -> BA Avios -> combine) - NOT scored", "yellow"),
            _one(f"{int(totals['legs_indirect_path_unverified'])} "
                 f"({', '.join(totals.get('legs_indirect_path_unverified_ids') or [])})",
                 "yellow"),
        ))
    if totals.get("legs_metal_lookup_missing"):
        rows.append(Row(
            _one("Legs whose operating airline was NOT LOOKED UP\n"
                 "(or NOT RECORDED) - nothing known about the metal", "yellow"),
            _one(f"{int(totals['legs_metal_lookup_missing'])} "
                 f"({', '.join(totals.get('legs_metal_lookup_missing_ids') or [])})", "yellow"),
        ))
    if totals.get("legs_metal_unknown"):
        rows.append(Row(
            _one("Legs whose operating airline is NOT KNOWN\n"
                 "(looked up, metal not settled)", "yellow"),
            _one(f"{int(totals['legs_metal_unknown'])} "
                 f"({', '.join(totals.get('legs_metal_unknown_ids') or [])})", "yellow"),
        ))
    if totals.get("legs_taxes_unknown"):
        rows.append(Row(
            _one("Legs where the award's TAXES are UNKNOWN\n"
                 "(NOT $0) - the leg is not scored", "red"),
            _one(f"{int(totals['legs_taxes_unknown'])} "
                 f"({', '.join(totals.get('legs_taxes_unknown_ids') or [])})", "red"),
        ))
    # MR5-1, WAY (10). The leg line has always said this; the trip block said
    # nothing, and the trip block is where the number Tsuki quotes comes from.
    # An owed duty of unknown size is a real dollar missing from the points
    # side, so it gets its own row and names its legs - it is NOT folded into
    # the surcharge row above, because a carrier's YQ and HMRC's duty are
    # different quantities with different payers and the reader must be able to
    # tell which one is missing.
    if totals.get("legs_apd_unknown"):
        rows.append(Row(
            _one("Legs where UK APD is OWED but its amount\n"
                 "is UNKNOWN (NOT $0)", "red"),
            _one(f"{int(totals['legs_apd_unknown'])} "
                 f"({', '.join(totals.get('legs_apd_unknown_ids') or [])})", "red"),
        ))
    # The amount IS known here and excluding it is defensible - the cash figure
    # came from Seats.aero's TotalTaxes and nobody has checked whether it
    # already contains the duty, so adding it could double-charge. That is a
    # decision, not an oversight, and it stays. But a tax the headline left out
    # must not be INVISIBLE at trip level, which is what it was.
    if totals.get("legs_apd_unverified"):
        rows.append(Row(
            _one("Legs carrying UK APD that is STATED but NOT\n"
                 "ADDED (unverified whether the fare includes it)", "yellow"),
            _one(f"{int(totals['legs_apd_unverified'])} "
                 f"({', '.join(totals.get('legs_apd_unverified_ids') or [])})", "yellow"),
        ))
    if totals.get("legs_verdict_sensitive"):
        rows.append(Row(
            _one("Legs whose verdict FLIPS inside the\n"
                 "surcharge estimate's own range", "yellow"),
            _one(f"{int(totals['legs_verdict_sensitive'])}", "yellow"),
        ))

    if totals.get("legs_unpriceable"):
        rows.append(Row(
            _one("Legs EXCLUDED from both totals because nothing\n"
                 "on them could be priced (NOT counted as $0)", "bold red"),
            _one(f"{int(totals['legs_unpriceable'])} "
                 f"({', '.join(totals.get('legs_unpriceable_ids') or [])})", "bold red"),
        ))
    if totals.get("legs_api_error"):
        rows.append(Row(
            _one("Flight legs where Seats.aero was NEVER REACHED\n"
                 "(this says NOTHING about award space)", "bold red"),
            _one(f"{int(totals['legs_api_error'])} "
                 f"({', '.join(totals.get('legs_api_error_ids') or [])})", "bold red"),
        ))
    if totals.get("legs_no_award_space"):
        rows.append(Row(
            _one("Flight legs where Seats.aero ANSWERED with no\n"
                 "award space (this IS a finding)", "yellow"),
            _one(f"{int(totals['legs_no_award_space'])} "
                 f"({', '.join(totals.get('legs_no_award_space_ids') or [])})", "yellow"),
        ))
    if totals.get("legs_budget_exhausted"):
        rows.append(Row(
            _one("Flight legs never queried because the daily\n"
                 "call budget ran out (NOT a finding)", "bold red"),
            _one(f"{int(totals['legs_budget_exhausted'])} "
                 f"({', '.join(totals.get('legs_budget_exhausted_ids') or [])})", "bold red"),
        ))
    return rows


@dataclass
class NoteLine:
    """One line printed after the totals table: styled segments, in order."""

    segments: List[Seg]
    blank_before: bool = False
    # What kind of line this is, from the totals keys that produced it.
    topic: str = ""

    def markup(self) -> str:
        return ("\n" if self.blank_before else "") + segs_markup(self.segments)

    @property
    def text(self) -> str:
        return segs_text(self.segments)

    def to_json(self) -> Dict[str, object]:
        return {
            "text": self.text,
            "style": self.segments[0].style if self.segments else "",
            "segments": [s.to_json() for s in self.segments],
            "topic": self.topic,
        }


def trip_notes(totals: Dict, today=None) -> List[NoteLine]:
    """Every line printed after the totals table, in order."""
    notes: List[NoteLine] = []
    if totals.get("margin_withheld") and totals.get("margin_withheld_reason"):
        notes.append(NoteLine([
            Seg("THE TRIP MARGIN IS WITHHELD.", "bold red"),
            Seg(f" {totals['margin_withheld_reason']}. A total that leaves those legs "
                f"out would report 'could not price' as a saving of zero. Per-leg "
                f"results above are unaffected."),
        ], blank_before=True, topic="withheld"))
    elif totals.get("margin_withheld"):
        notes.append(NoteLine([
            Seg("THE TRIP MARGIN IS WITHHELD.", "bold red"),
            Seg(f" --require-all-live "
                f"was passed and the margin's provenance is "
                f"'{totals.get('margin_provenance')}', not 'live'. "
                f"{totals.get('margin_provenance_note', '')} Per-leg results above are "
                f"unaffected and remain valid. Re-run without --require-all-live to "
                f"see the mixed-provenance number, clearly labelled as mixed."),
        ], blank_before=True, topic="withheld"))
    elif totals.get("margin_provenance") == "mixed":
        notes.append(NoteLine([
            Seg("THIS MARGIN MIXES LIVE AND BADGE-DERIVED LEGS.", "bold yellow"),
            Seg(f" {totals.get('margin_provenance_note', '')} Do not quote it as a live "
                f"number. Run with --require-all-live if you need one that can be."),
        ], blank_before=True, topic="mixed"))
    elif totals.get("margin_provenance") == "badge":
        notes.append(NoteLine(
            [Seg(f"{totals.get('margin_provenance_note', '')}", "yellow")],
            blank_before=True, topic="badge",
        ))

    if totals.get("headline_is_a_range") and not totals.get("margin_withheld"):
        # MR5-1, WAY (10). This sentence used to say the spread was carrier
        # surcharges FULL STOP, on runs where part of it was an unknown
        # government departure tax - a sentence that was actively false about
        # what the reader was looking at. It names the tax when the tax is
        # actually in the spread, and does NOT name it otherwise: a caveat that
        # lists an ingredient this run does not contain is the same kind of
        # false as the one it replaces. On a run with no unknown duty the
        # wording is unchanged to the byte.
        # Each ingredient is named only when this run has it. A run with no
        # unknown award taxes prints the wording it always printed, to the byte.
        what_parts, assume_parts = [], []
        if _names_surcharge(totals):
            what_parts.append("carrier-imposed surcharges that are not known")
            assume_parts.append("every unknown surcharge turns out to be $0")
        if totals.get("legs_apd_unknown"):
            what_parts.append(
                "UK AIR PASSENGER DUTY that is OWED on "
                f"{', '.join(totals.get('legs_apd_unknown_ids') or [])} in an "
                "amount this tool does not know"
            )
            assume_parts.append(
                "the departure tax turns out to be $0, which it will not be"
            )
        what = " AND ".join(what_parts)
        assumption = " AND ".join(assume_parts)
        # Same rule, for award taxes Seats.aero did not usefully report.
        if totals.get("legs_taxes_unknown"):
            ids = ", ".join(totals.get("legs_taxes_unknown_ids") or [])
            what = (what + ", AND " if what else "") + (
                f"award TAXES on {ids} that Seats.aero did not report in a "
                f"usable form"
            )
            assumption = (assumption + " AND " if assumption else "") + (
                "those award taxes turn out to be nothing beyond any UK Air "
                "Passenger Duty already counted, which they will not be"
            )
        notes.append(NoteLine([
            Seg("The headline above is a RANGE and must not be quoted as "
                "a single number.", "bold yellow"),
            Seg(f" The spread is {what}"
                ". The low end is what the tool can defend today; the "
                f"high end assumes {assumption}, which is "
                # CARRIED FORWARD: no version numbers in user-facing output. The
                # reader is being told what the high end depends on, and "an
                # earlier version of this tool got it wrong" is a fact about the
                # tool's history, not about their trip. The WARNING survives; the
                # changelog does not.
                "the assumption that turns an unknown into a saving that is not there."),
        ], blank_before=True, topic="range_caveat"))
    missing = list(totals.get("legs_metal_lookup_missing_ids") or [])
    not_recorded = list(totals.get("legs_metal_not_recorded_ids") or [])
    not_looked_up = [leg for leg in missing if leg not in not_recorded]
    if not_looked_up:
        notes.append(NoteLine([Seg(
            f"operating airline NOT LOOKED UP on {', '.join(not_looked_up)}: "
            f"nothing is known about which airline flies the chosen award there; "
            f"each leg's line says why.", "yellow")], topic="metal_not_looked_up"))
    if not_recorded:
        notes.append(NoteLine([Seg(
            f"operating airline NOT RECORDED on {', '.join(not_recorded)}: "
            f"this replay holds no itinerary lookup for the chosen award there.",
            "yellow")], topic="metal_not_recorded"))
    if totals.get("legs_metal_unknown_ids"):
        notes.append(NoteLine([Seg(
            f"operating airline NOT KNOWN on "
            f"{', '.join(totals['legs_metal_unknown_ids'])}: a lookup was made and did "
            f"not settle one carrier set.", "yellow")], topic="metal_unknown"))
    if totals.get("legs_verdict_sensitive_ids"):
        notes.append(NoteLine([Seg(
            f"VERDICT SENSITIVE: "
            f"{', '.join(totals['legs_verdict_sensitive_ids'])} reverse inside their "
            f"own surcharge range. Do not treat these as settled.", "bold yellow")],
            topic="sensitive"))
    if totals.get("rests_on_placeholder_fx"):
        notes.append(NoteLine([Seg(
            "At least one total above rests on an FX rate with NO "
            "SOURCE. Supply one with --fx to clear the marker.", "bold yellow")],
            topic="fx_placeholder"))
    # A rate that WAS looked up, but a month ago. Every total above that touched
    # it inherits the staleness, exactly as a placeholder total does.
    stale = config.stale_currencies(today)
    if stale:
        notes.append(NoteLine([Seg(
            f"STALE RATE: {', '.join(stale)} were sourced more than "
            f"{config.FX_STALE_AFTER_DAYS} days ago. EVERY TOTAL ABOVE THAT USED "
            f"THEM IS MARKED STALE. Re-source them, or supply one with --fx "
            f"{stale[0]}=<rate>.", "bold yellow")], topic="fx_stale"))
    return notes


def print_trip_totals(
    totals: Dict[str, float],
    label: str = "Trip",
    console: Console = None,
    today=None,
) -> None:
    """Print the both-ways totals and the headline beat-cash percentage."""
    console = console or Console()

    banner = trip_funding_banner(totals)
    if banner:
        console.print("\n" + "\n".join(s.markup() for s in banner))

    table = Table(title=f"{label}: totals both ways")
    table.add_column("Measure", style="cyan")
    table.add_column("Value", justify="right")
    for row in trip_totals_rows(totals):
        table.add_row(segs_markup(row.label), segs_markup(row.value))
    console.print(table)

    for note in trip_notes(totals, today):
        console.print(note.markup())


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
            # The HTML export has refused to print an unknown as $0.00 since
            # finding H-3; the terminal table printed it. Same rule, both places.
            _money(s.cash_cost) if s.cash_cost_known else "[bold red]UNKNOWN[/bold red]",
            (
                _money(s.total_value)
                if s.cash_cost_known
                else f"[red]>= {_money(s.total_value)}[/red]"
            ),
        )

    console.print(table)
    unknown = [s for s in strategies if not s.cash_cost_known]
    if unknown:
        console.print(
            f"[red]{len(unknown)} strateg{'y' if len(unknown) == 1 else 'ies'} "
            f"above carr{'ies' if len(unknown) == 1 else 'y'} cash that is UNKNOWN "
            f"(NOT $0) and cannot be ranked on it: shown after every strategy whose "
            f"cash is known, with its total as a FLOOR (>=).[/red]"
        )
        for s in unknown:
            console.print(f"  [dim]{s.award.program}: {s.cash_cost_note}[/dim]")


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
    top = strategies[0]
    if top.cash_cost_known:
        console.print(f"Top strategy cash cost: {_money(top.cash_cost)}")
        console.print(f"Top strategy total value: {_money(top.total_value)}")
    else:
        # Reached only when NO strategy has a known cash cost (known ones rank
        # first). The top one is then "top" by points alone, and says so.
        console.print(
            "Top strategy cash cost: [bold red]UNKNOWN - NOT $0.00[/bold red] "
            "(no strategy on this search has a known cash cost; ranked by points "
            "alone)"
        )
        console.print(
            f"Top strategy total value: [red]>= {_money(top.total_value)}[/red] "
            f"(a floor: the unknown cash is not in it)"
        )

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
