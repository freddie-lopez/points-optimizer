"""
Engine objects -> JSON-safe dicts for the browser (docs/plans/ui.md 4.6).

TWO RULES, BOTH ABOUT THE PROJECT'S RECURRING FAILURE:

* AN UNKNOWN IS `null` PLUS A STATUS, never a number. `inf` becomes `null`; a
  headline that is a range or withheld does not carry the single `pct` at all;
  the server serialises with `allow_nan=False`, so a stray inf/nan fails loudly
  instead of reaching the page.
* WORDS COME FROM THE CLI. Every sentence here is either produced by the same
  builder the terminal prints from, or is a verbatim line of the terminal's own
  output. Nothing is parsed out of the text, and nothing is re-worded.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

EXIT_LABELS = {
    0: "OK",
    1: "ERROR — NOTHING SCORED",
    2: "WALLET ERROR",
    3: "WITHHELD",
    4: "NOT EXECUTABLE",
}


def num(x) -> Optional[float]:
    """A finite number, or None. `inf` and `nan` are unknowns, not numbers."""
    if x is None or isinstance(x, bool):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def iso(d) -> Optional[str]:
    return None if d is None else str(d)


# ---------------------------------------------------------------------------
# Fixture (before any run)
# ---------------------------------------------------------------------------


def fixture_detail(trip_id: str, path, fx, no_legs_note: str = None) -> Dict[str, Any]:
    legs = []
    for leg in fx.legs:
        legs.append({
            "id": leg.id,
            "kind": leg.kind,
            "description": leg.description,
            "date": iso(leg.date),
            "origin": leg.origin or None,
            "destination": leg.destination or None,
            "travelers": leg.travelers,
            # "" means the fixture did not say. That is UNKNOWN, not "Y".
            "cabin": leg.cabin or None,
            "nights": leg.nights or None,
            "cash_options": [
                {
                    "label": c.label,
                    "amount": num(c.amount),
                    "currency": c.currency,
                    "source": c.source or "unknown",
                    "captured_on": iso(c.captured_on),
                    "for_date": iso(c.date),
                }
                for c in leg.cash_options
            ],
            "points_candidates": [
                {
                    "label": p.label,
                    "program": p.program,
                    "points": p.points,
                    "source": p.source,
                    "source_note": p.source_note,
                    "unverified": p.source != "seats_aero_live",
                    "attribution_assumed": bool(p.program_attribution_assumed),
                    "cabin": p.cabin,
                }
                for p in leg.points_candidates
            ],
        })
    return {
        "id": trip_id,
        "file": path.name,
        "name": fx.name,
        "description": fx.description,
        "source": fx.source,
        "flags": list(fx.trip_level_flags),
        "no_legs_note": no_legs_note if not legs else None,
        "legs": legs,
    }


# ---------------------------------------------------------------------------
# A trip run
# ---------------------------------------------------------------------------


def _headline(totals: Dict[str, Any]) -> Dict[str, Any]:
    """The headline, from the same totals the terminal prints."""
    from src.formatter import trip_headline

    return trip_headline(totals).to_json()


def trip_run(out: Dict[str, Any], mode: str, trip_id: str, calls: Dict[str, int]) -> Dict[str, Any]:
    from src.main import FixtureRun, RunRefusal

    run = out["run"]
    code = out["code"]
    payload: Dict[str, Any] = {
        "kind": "trip",
        "mode": mode,
        "trip_id": trip_id,
        "started_at": out["started_at"],
        "duration_s": out["duration_s"],
        "exit_code": code,
        "exit_label": EXIT_LABELS.get(code, f"EXIT {code}"),
        "argv_display": out["argv_display"],
        "refusal": None,
        "transcript": out["transcript"],
        "calls": {
            "this_run": _calls_this_run(mode, run, out),
            "since_launch": calls["since_launch"],
            "cap": calls["cap"],
        },
    }
    if isinstance(run, RunRefusal) or not isinstance(run, FixtureRun):
        payload["refusal"] = {
            "kind": getattr(run, "kind", "unknown"),
            "message": getattr(run, "message", out["transcript"].strip()),
        }
        return payload
    payload.update(fixture_run_body(run))
    return payload


def _calls_this_run(mode: str, run, out) -> Optional[Dict[str, int]]:
    if mode == "offline":
        return {"search": 0, "trips": 0}
    if mode == "replay":
        return None  # a replay asks nothing, and "0 measured" is not what it did
    report = getattr(getattr(run, "live_opts", None), "metal_report", None)
    if report is not None and report.search_calls is not None:
        return {"search": int(report.search_calls), "trips": int(report.trips_calls or 0)}
    return {"search": int(out["calls_spent"]), "trips": 0}


def _recorded(fn, *args, width: int = 10_000) -> List[str]:
    """Lines a print_* function writes, from a recording console. WIDE, so each
    logical line stays one line in the drawer; blank lines dropped."""
    import io

    from rich.console import Console

    buf = io.StringIO()
    fn(*args, Console(file=buf, width=width))
    return [line.rstrip() for line in buf.getvalue().splitlines() if line.strip()]


def _line_objs(lines: List[str]) -> List[Dict[str, str]]:
    return [{"text": t, "style": ""} for t in lines]


def fixture_run_body(run) -> Dict[str, Any]:
    from src import config
    from src.formatter import (
        print_fx_banner,
        print_valuation_banner,
        print_wallet_banner,
        residue_rows,
        trip_funding_banner,
        trip_headline,
        trip_notes,
        trip_totals_rows,
    )
    from src.main import fixture_footer_lines

    fx = run.fixture
    totals = run.totals
    results = run.results
    wallet_block = _recorded(
        lambda c: print_wallet_banner(run.wallet, run.wallet_warnings, run.transfer_date,
                                      run.ratios, c)
    )
    unusable = [
        c for c in run.wallet.currencies if not run.ratios.has_verified_partners(c)
    ]
    body: Dict[str, Any] = {
        "trip": {
            "id": fx.id,
            "name": fx.name,
            "description": fx.description,
            "source": fx.source,
            "flags": list(fx.trip_level_flags),
        },
        "context": {
            "key_source": run.key_source,
            "wallet_lines": run.wallet.describe(),
            "wallet_warnings": list(run.wallet_warnings),
            "unusable_currencies": sorted(unusable),
            "wallet_block": wallet_block,
            "transfer_date": iso(run.transfer_date),
            "fx_lines": list(config.fx_report_lines()),
            "valuation_line": " ".join(_recorded(
                lambda c: print_valuation_banner(run.args.valuation_cpp, c))),
        },
        "live_banner_lines": _live_banner(run),
        "replay": _replay(run),
        "metal_summary": _metal_summary(run),
        "funding_banner": [s.to_json() for s in trip_funding_banner(totals)],
        "headline": trip_headline(totals).to_json(),
        "totals_rows": [r.to_json() for r in trip_totals_rows(totals)],
        "trip_notes": [n.to_json() for n in trip_notes(totals)],
        "residue_rows": [
            {
                "currency": row[0].text,
                "starting": row[1].text,
                "spent": row[2].text,
                "remaining": row[3].text,
                "note": row[4].text,
                "note_style": row[4].style,
                "unconstrained": row[1].text == "unconstrained",
            }
            for row in residue_rows(run.residue)
        ],
        "footer_lines": [
            {"text": f.text, "segments": [{"text": t, "style": st} for t, st in f.segments]}
            for f in fixture_footer_lines(results, totals)
        ],
        "legs": [leg_json(r) for r in results],
    }
    return body


def _live_banner(run) -> Optional[List[Dict[str, str]]]:
    if not run.live_opts.live:
        return None
    from src.formatter import print_live_banner

    lines = _recorded(lambda c: print_live_banner(run.outcomes, run.live_opts, run.cache, c))
    for leg_id, row_ver, meta_ver in getattr(run.client, "parser_version_disagreements", []):
        lines.append(
            f"  PARSER VERSION DISAGREEMENT on {leg_id}: the manifest row says {row_ver} "
            f"but the snapshot itself says it was captured under {meta_ver}; it is being "
            f"read by {run.client.current_parser_version}. Treat this leg as REPARSED."
        )
    return _line_objs(lines)


def _replay(run) -> Optional[Dict[str, Any]]:
    if run.replay_selection is None:
        return None
    from src import snapshot_replay
    from src.main import print_replay_banner

    at_capture = ", ".join(snapshot_replay.parser_versions(run.replay_selection.selected))
    now = run.client.current_parser_version
    return {
        "manifest_hash": run.manifest_hash,
        "selection": run.replay_selection.describe(),
        "parser_at_capture": at_capture,
        "parser_now": now,
        "reparsed": at_capture != now,
        "snapshots": len(run.replay_selection.selected),
        "banner_lines": _line_objs(_recorded(
            lambda c: print_replay_banner(c, run.replay_selection, run.manifest_hash,
                                          run.client))),
    }


def _metal_summary(run) -> Optional[Dict[str, Any]]:
    report = getattr(run.live_opts, "metal_report", None)
    if report is None:
        return None
    from src import seats_trips

    return {
        "mode": report.mode,
        "cap": None if report.replay else report.cap,
        "replay": bool(report.replay),
        "candidates": report.candidates,
        "requests_sent": report.requests_sent,
        "served_from_cache": report.served_from_cache,
        "replayed": report.replayed,
        "by_status": dict(report.by_status),
        "gaps": report.not_looked_up_missing,
        "rate_limited": bool(report.rate_limited),
        "search_calls": report.search_calls,
        "trips_calls": report.trips_calls,
        "parser_label": seats_trips.trips_parser_label(),
    }


# ---------------------------------------------------------------------------
# One leg
# ---------------------------------------------------------------------------


def _taxes_json(r) -> Dict[str, Any]:
    """Which taxes sentence the CLI prints for this leg, as a status, with the
    figures that sentence carries. Engine fields only."""
    from src.models import PointsProvenance

    cand = r.best_points
    blank = {"status": "n/a", "usd": None, "amount": None, "currency": None, "note": None,
             "evidence": None}
    if cand is None or r.leg.points_provenance not in (PointsProvenance.LIVE,
                                                        PointsProvenance.SNAPSHOT):
        return blank
    whole = getattr(cand, "observed_taxes_whole_because", "") or ""
    if cand.surcharge_captured and whole.startswith("yq_included_verified:"):
        return {**blank, "status": "verified_complete", "usd": num(cand.cash_surcharge),
                "evidence": whole.split(":", 1)[1]}
    if cand.surcharge_captured:
        return {**blank, "status": "policy_complete", "usd": num(cand.cash_surcharge),
                "amount": num(cand.observed_taxes_amount),
                "currency": cand.observed_taxes_currency or None}
    if getattr(cand, "taxes_unconvertible", False):
        return {**blank, "status": "unconvertible", "amount": num(cand.observed_taxes_amount),
                "currency": cand.observed_taxes_currency or None,
                "note": cand.observed_taxes_note}
    if getattr(cand, "taxes_unknown", False):
        return {**blank, "status": "unknown", "note": cand.observed_taxes_note}
    if cand.observed_taxes_known and cand.observed_taxes_reported:
        return {**blank, "status": "known", "usd": num(cand.observed_taxes_usd),
                "amount": num(cand.observed_taxes_amount),
                "currency": cand.observed_taxes_currency or None}
    if cand.observed_taxes_reported:
        return {**blank, "status": "reported_unusable"}
    return blank


def _apd_json(r) -> Dict[str, Any]:
    codes = {x.code for x in r.reasons}
    line = next((w for w in r.warnings if w.startswith("UK AIR PASSENGER DUTY on ")), None)
    if "APD_UNKNOWN" in codes:
        state = "unknown"
    elif "APD_ADDED" in codes and r.apd_added_usd:
        state = "added"
    elif "APD_INCLUSION_UNVERIFIED" in codes:
        state = "stated_not_added"
    elif r.apd is not None:
        state = "added" if r.apd_added_usd else "stated_not_added"
    else:
        state = "none"
    return {"state": state, "line": line, "added_usd": num(r.apd_added_usd) if r.apd_added_usd else None}


def _metal_json(cand) -> Optional[Dict[str, Any]]:
    from src.formatter import metal_lines

    metal = getattr(cand, "metal", None)
    if metal is None:
        return None
    return {
        "status": metal.status.value,
        "reason_code": metal.reason_code or None,
        "line": metal.render(),
        "extra_lines": metal_lines(cand)[1:],
        "unverified": metal.parser_label_text != "",
        "parser_label": metal.parser_label_text or None,
        "carriers": list(metal.carriers),
        "possible_carriers": list(metal.possible_carriers),
        "domain_unbounded": bool(metal.domain_unbounded),
        "served_from_cache": bool(metal.served_from_cache),
        "fetched_at": iso(metal.fetched_at),
        "replayed": bool(metal.replayed_from_snapshot),
    }


def _legacy_metal_line(r) -> Optional[str]:
    from src.formatter import _trips_metal_label

    cand = r.best_points
    if cand is None or not r.points_path or not cand.operating_carrier:
        return None
    label = _trips_metal_label(cand.carrier_source)
    # The terminal prints the label ESCAPED; this is the text it shows.
    from rich.text import Text

    return (
        f"metal: {cand.operating_carrier} (source: {cand.carrier_source}), "
        f"cabin {cand.cabin}" + (Text.from_markup(label).plain if label else "")
    )


def _other_lookups(r) -> List[Dict[str, Any]]:
    from src.formatter import metal_lines
    from src.models import MetalStatus

    out = []
    for cand in r.leg.points_candidates:
        if cand is r.best_points or getattr(cand, "metal", None) is None:
            continue
        if cand.metal.status is MetalStatus.NOT_LOOKED_UP:
            continue
        out.append({
            "title": (f"other live award {cand.program} {cand.cabin} {cand.points:,} "
                      f"points (not the chosen option)"),
            "lines": metal_lines(cand),
            "unverified": cand.metal.parser_label_text != "",
        })
    return out


def _live_json(r) -> Optional[Dict[str, Any]]:
    o = r.leg.live_outcome
    if o is None:
        return None
    return {
        "state": o.state.value,
        "line": o.render(),
        "is_api_failure": bool(o.is_api_failure),
        "pagination_note": o.pagination_note or None,
        "coverage_incomplete": bool(o.result_incomplete)
        or "INCOMPLETE" in (o.pagination_note or ""),
        "served_from_cache": bool(o.served_from_cache),
        "cache_fetched_at": iso(o.cache_fetched_at),
        "replayed_from_snapshot": bool(o.replayed_from_snapshot),
        "snapshot_name": o.snapshot_name or None,
        "snapshot_captured_at": iso(o.snapshot_captured_at),
        "snapshot_parser_version": o.snapshot_parser_version or None,
        "points_provenance": r.leg.points_provenance.value,
        "queried": o.queried.describe() if o.queried else None,
    }


def leg_json(r) -> Dict[str, Any]:
    from src.formatter import (
        _short_label,
        _taxes_are_what_is_unknown,
        alternatives_lines,
        leg_detail_lines,
        leg_table_cells,
        leg_warning_lines,
        live_detail_lines,
        verdict_kind,
    )
    from src.live_trip import supersession_lines

    leg = r.leg
    cand = r.best_points
    cash = r.best_cash
    scoreable = r.has_points_path and num(r.points_total_score_usd) is not None
    # A leg whose verdict FLIPS inside the surcharge band has a RANGE, not a
    # point estimate: the terminal prints "$a-$b" there, so the page is not
    # given the midpoint it would otherwise draw as the answer.
    sensitive = bool(r.verdict_sensitive)
    be = num(r.break_even_surcharge_usd) if r.break_even_surcharge_usd is not None else None
    surcharge = None
    if r.surcharge is not None:
        known = r.surcharge.is_known
        surcharge = {
            "known": known,
            "render": r.surcharge.render(),
            # An unknown's amounts are placeholders, NOT values: never sent.
            "low": num(r.surcharge.amount_low) if known else None,
            "point": num(r.surcharge.amount_point) if known else None,
            "high": num(r.surcharge.amount_high) if known else None,
            "confidence": r.surcharge.confidence,
            "matched_rule": r.surcharge.matched_rule or None,
            "source": r.surcharge.source or None,
            "notes": r.surcharge.notes or None,
            "cannot_change_verdict": bool(r.surcharge_cannot_change_verdict),
        }
    alt_lines = [l.to_json() for l in alternatives_lines(r) if l.text.strip()]
    cli_lines = [
        l.to_json()
        for l in leg_detail_lines(r) + live_detail_lines(r) + alternatives_lines(r)
        if l.text.strip()
    ]
    flex = sorted(leg.flexible_date_findings, key=lambda x: x.award_date)
    return {
        "id": leg.id,
        "kind": leg.kind,
        "description": leg.description,
        "short": _short_label(leg),
        "origin": leg.origin or None,
        "destination": leg.destination or None,
        "date": iso(leg.date),
        "travelers": leg.travelers,
        "nights": leg.nights or None,
        "cabin": leg.cabin or (cand.cabin if cand is not None else None),
        "cells": leg_table_cells(r).to_json(),
        "verdict": {
            "code": r.verdict,
            "chip": verdict_kind(r),
            "sensitive": bool(r.verdict_sensitive),
            "scored_off_date": bool(r.scored_off_date),
            "scoring_date": iso(r.scoring_date) if r.scored_off_date else None,
            "date_shifted_to": iso(leg.date_shifted_to) if leg.date_shifted else None,
            "demoted_for_trip_balance": bool(r.demoted_for_trip_balance),
            "points_absence": r.points_absence or None,
            "reason": r.verdict_reason,
        },
        "numbers": {
            "cash_usd": num(r.cash_usd) if cash is not None else None,
            "cash_pts": r.cash_as_points_equivalent if cash is not None else None,
            "points_required": r.points_required if (cand is not None and r.points_path) else None,
            "score_points": num(r.points_total_score_usd) if scoreable and not sensitive else None,
            "score_low": num(r.points_score_low_usd) if scoreable and sensitive else None,
            "score_high": num(r.points_score_high_usd) if scoreable and sensitive else None,
            "floor": num(r.points_floor_usd) if r.points_floor_usd is not None else None,
            "break_even": be,
            "break_even_subject": (
                None if be is None
                else ("taxes_plus_surcharge" if _taxes_are_what_is_unknown(r) else "surcharge")
            ),
            "margin_usd": num(r.margin_usd) if scoreable else None,
            "margin_pct": num(r.margin_pct) if scoreable else None,
            "score_cash": num(r.cash_total_score_usd),
            "mandatory_fees_usd": (
                None if r.mandatory_fees_unpriceable else num(r.mandatory_fees_usd)
            ),
            "mandatory_fees_unpriceable": bool(r.mandatory_fees_unpriceable),
            "apd_added_usd": num(r.apd_added_usd) if r.apd_added_usd else None,
        },
        "cash": None if cash is None else {
            "label": cash.label,
            "amount": num(cash.amount),
            "currency": cash.currency,
            "amount_usd": num(cash.amount_usd),
            "foreign": bool(cash.is_foreign),
            "rate_unconfirmed": bool(cash.is_foreign),
            "unavoidable_cash_note": cash.unavoidable_cash_note or None,
            "provenance": leg.cash_provenance or "unknown",
            "captured_on": iso(leg.cash_captured_on),
        },
        "points": None if cand is None else {
            "label": cand.label,
            "program": cand.program,
            "points": cand.points,
            "cabin": cand.cabin,
            "source": cand.source,
            "source_note": cand.source_note or None,
            "attribution_assumed": bool(cand.program_attribution_assumed),
            "unverified": cand.source != "seats_aero_live",
            "path_summary": r.points_path.summary() if r.points_path else None,
            "spend_summary": r.funding_plan.spend_summary() if r.funding_plan else None,
            "stranded": (r.points_path.stranded_points or None) if r.points_path else None,
        },
        "taxes": _taxes_json(r),
        "surcharge": surcharge,
        "apd": _apd_json(r),
        "metal": _metal_json(cand) if cand is not None else None,
        "legacy_metal_line": _legacy_metal_line(r),
        "other_lookups": _other_lookups(r),
        "live": _live_json(r),
        "superseded_lines": list(supersession_lines(leg)),
        "date_shift_line": (
            f"DATE SHIFTED to {leg.date_shifted_to}: an off-date award was promoted "
            f"because the fixture carries a CAPTURED cash price for that date. The "
            f"comparison is therefore real, and the fact that the date moved is "
            f"recorded here rather than lost."
        ) if leg.date_shifted else None,
        "flexible": [
            {"line": f.render(), "date_match": f.date_match, "advisory": f.advisory()}
            for f in flex
        ],
        "alternatives_lines": alt_lines,
        "reasons": [{"code": x.code, "detail": x.detail} for x in r.reasons],
        "warnings": [
            {"prefix": prefix, "text": w, "style": style}
            for prefix, w, style in leg_warning_lines(r)
        ],
        "notes": list(leg.notes),
        "data_flags": list(leg.data_flags),
        "cli_lines": cli_lines,
    }


# ---------------------------------------------------------------------------
# A single-route search
# ---------------------------------------------------------------------------


def _fx_summary() -> str:
    from src import config

    rates = [c for c in config.FX_RATES_TO_USD if c != "USD"]
    confirm = [c for c in rates if config.needs_confirmation(c) or config.is_placeholder_rate(c)]
    tail = (
        "every one CONFIRM BEFORE TRUSTING" if rates and len(confirm) == len(rates)
        else f"{len(confirm)} of them CONFIRM BEFORE TRUSTING"
    )
    return f"FX rates used (table as of {config.FX_RATES_AS_OF}) — {len(rates)} rates, {tail}"


def search_run(out: Dict[str, Any], req: Dict[str, Any], calls: Dict[str, int]) -> Dict[str, Any]:
    from src import config
    from src.formatter import _money, unknown_cash_line
    from src.main import (
        SINGLE_ROUTE_TRIPS_FOOTER,
        RunRefusal,
        SearchRun,
        none_fundable_header,
        unfundable_reason,
    )
    from src.optimizer import search_award_cash
    from src.seats_client import PARSER_VERSION

    run = out["run"]
    code = out["code"]
    payload: Dict[str, Any] = {
        "kind": "search",
        "mode": "live",
        "started_at": out["started_at"],
        "duration_s": out["duration_s"],
        "exit_code": code,
        "exit_label": EXIT_LABELS.get(code, f"EXIT {code}"),
        "argv_display": out["argv_display"],
        "refusal": None,
        "transcript": out["transcript"],
        "route": {"origin": req["origin"], "destination": req["destination"],
                  "from": req["from"], "to": req["to"]},
        "passengers": 1,
        "calls": {"this_run": {"search": int(out["calls_spent"]), "trips": 0},
                  "since_launch": calls["since_launch"], "cap": calls["cap"]},
        "rows": [],
    }
    if isinstance(run, RunRefusal) or not isinstance(run, SearchRun):
        payload["refusal"] = {
            "kind": getattr(run, "kind", "unknown"),
            "message": getattr(run, "message", out["transcript"].strip()),
        }
        return payload

    strategies = run.strategies
    rank_of = {}
    for i, st in enumerate(strategies, 1):
        rank_of.setdefault(id(st.award), (i, st))
    if strategies:
        state = "ok"
    elif run.last_error:
        state = "api_error"
    elif not run.awards:
        state = "no_awards"
    else:
        state = "none_fundable"

    rows: Dict[tuple, Dict[str, Any]] = {}
    for award in run.awards:
        key = (str(award.date), award.program_source_code or award.program or "")
        row = rows.setdefault(key, {
            "date": str(award.date),
            "program": award.program or None,
            "source_code": award.program_source_code or None,
            "cabins": {"Y": None, "W": None, "J": None, "F": None},
        })
        cabin = award.award_type if award.award_type in row["cabins"] else None
        if cabin is None:
            continue
        known, cash, note, apd_floor = search_award_cash(run.trip, award)
        ranked = rank_of.get(id(award))
        strategy = ranked[1] if ranked else None
        cell = {
            "cost": award.cost,
            "seats": award.seats_available,
            "carriers": list(award.candidate_carriers),
            "direct": award.direct,
            "taxes": {
                "known": bool(known),
                "usd": num(cash) if known else None,
                "text": _money(cash) if known else None,
                "amount": num(award.cash_component_source_amount),
                "currency": award.cash_component_currency or None,
                "source_text": (
                    f"{award.cash_component_currency} {award.cash_component_source_amount:,.2f}"
                    if award.cash_component_source_amount is not None
                    and (award.cash_component_currency or "USD") != "USD" else None
                ),
                "confirm": bool(known) and config.needs_confirmation(
                    award.cash_component_currency or "USD"),
                "note": note or None,
                "apd_floor": num(apd_floor) if apd_floor else None,
            },
            "fundable": strategy is not None,
            "rank": ranked[0] if ranked else None,
            "path_summary": strategy.transfer_path.summary() if strategy else None,
            "stranded": (strategy.transfer_path.stranded_points or None) if strategy else None,
            "total": num(strategy.total_value) if strategy else None,
            "total_text": (
                (_money(strategy.total_value) if strategy.cash_cost_known
                 else f">= {_money(strategy.total_value)}") if strategy else None
            ),
            "total_is_floor": bool(strategy and not strategy.cash_cost_known),
            "why_not": None if strategy else unfundable_reason(award),
            "indirect_path": getattr(award, "indirect_ur_path", "") or None,
            "source_note": award.source_note or None,
            "parser_version": PARSER_VERSION,
        }
        prev = row["cabins"][cabin]
        # One row can in principle repeat a cabin; the cheaper award shows and
        # the count of the others is said, never silently dropped.
        if prev is None or award.cost < prev["cost"]:
            if prev is not None:
                cell["others_hidden"] = prev.get("others_hidden", 0) + 1
            row["cabins"][cabin] = cell
        else:
            prev["others_hidden"] = prev.get("others_hidden", 0) + 1

    note = run.pagination_note or ""
    payload.update({
        "state": state,
        "api_error": run.last_error if state == "api_error" else None,
        "coverage_note": note or None,
        "coverage_incomplete": bool(getattr(run.client, "last_incomplete", False))
        or "INCOMPLETE" in note,
        "trips_footer": SINGLE_ROUTE_TRIPS_FOOTER,
        "header_line": none_fundable_header(len(run.awards)) if state == "none_fundable" else None,
        "unknown_cash_line": unknown_cash_line(strategies) or None,
        "fundable_count": len(strategies),
        "rows": sorted(rows.values(), key=lambda r: (r["date"], r["program"] or "")),
        "context": {
            "key_source": run.key_source,
            "wallet_lines": run.wallet.describe(),
            "wallet_warnings": list(run.wallet_warnings),
            "fx_lines": list(config.fx_report_lines()),
            "fx_summary": _fx_summary(),
        },
    })
    return payload
