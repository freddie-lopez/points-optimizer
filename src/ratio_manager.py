"""Manage transfer ratios and bonuses with date and card scoping."""
import csv
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.models import Bonus, Ratio


class RatioManager:
    """Load, query, and validate transfer ratios and bonuses."""

    def __init__(
        self,
        ratios_path: Path,
        bonuses_path: Path,
        programs_path: Optional[Path] = None,
    ):
        self.ratios: List[Ratio] = []
        self.bonuses: List[Bonus] = []
        self._alias_map: Dict[str, str] = {}
        self.programs: Dict[str, dict] = {}
        self.program_doc: Dict[str, object] = {}
        # Row-level provenance, parallel to self.ratios. Kept out of the Ratio
        # dataclass so the v0 equality/behaviour of Ratio is untouched.
        self.ratio_provenance: List[Dict[str, str]] = []
        self._load_ratios(ratios_path)
        self._load_bonuses(bonuses_path)
        if programs_path is None:
            programs_path = Path(ratios_path).parent / "programs.yaml"
        self._load_program_aliases(programs_path)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_ratios(self, path: Path):
        with open(path, "r") as f:
            for row in csv.DictReader(f):
                self.ratios.append(
                    Ratio(
                        from_program=row["from_program"].strip(),
                        to_program=row["to_program"].strip(),
                        card_dependency=row["card_dependency"].strip(),
                        ratio_numerator=int(row["ratio_numerator"]),
                        ratio_denominator=int(row["ratio_denominator"]),
                        effective_from=date.fromisoformat(row["effective_from"]),
                        effective_to=date.fromisoformat(row["effective_to"]),
                    )
                )
                self.ratio_provenance.append(
                    {
                        "source": (row.get("source") or "").strip(),
                        "verified_on": (row.get("verified_on") or "").strip(),
                    }
                )

    def _load_bonuses(self, path: Path):
        with open(path, "r") as f:
            for row in csv.DictReader(f):
                if not row.get("from_program"):
                    continue
                self.bonuses.append(
                    Bonus(
                        from_program=row["from_program"].strip(),
                        to_program=row["to_program"].strip(),
                        bonus_percent=float(row["bonus_percent"]),
                        effective_from=date.fromisoformat(row["effective_from"]),
                        effective_to=date.fromisoformat(row["effective_to"]),
                        notes=row.get("notes", "") or "",
                    )
                )

    def _load_program_aliases(self, path: Path):
        """Build alias -> canonical name map from programs.yaml (best effort)."""
        try:
            import yaml
        except ImportError:
            return
        try:
            with open(path, "r") as f:
                data = yaml.safe_load(f) or {}
        except OSError:
            return

        self.program_doc = data
        for canonical, meta in (data.get("programs") or {}).items():
            self._alias_map[canonical.lower()] = canonical
            if not isinstance(meta, dict):
                continue
            self.programs[canonical] = meta
            name = meta.get("name")
            if name:
                self._alias_map[str(name).lower()] = canonical
            for alias in meta.get("aliases") or []:
                self._alias_map[str(alias).lower()] = canonical

    # ------------------------------------------------------------------
    # Program metadata (v1)
    # ------------------------------------------------------------------

    def meta(self, program: str) -> dict:
        """Metadata block for a program, or {} if unknown."""
        return self.programs.get(self.normalize_program(program), {}) or {}

    def issuer_currencies(self) -> List[str]:
        """
        Every currency a wallet may legitimately name.

        Being on this list is NOT a claim that the currency has verified transfer
        partners - see `has_verified_partners`.
        """
        return sorted(
            p for p, m in self.programs.items()
            if (m or {}).get("category") == "issuer_currency"
        )

    def has_verified_partners(self, currency: str) -> bool:
        """Whether ratios.csv actually holds rows for this issuer currency."""
        canonical = self.normalize_program(currency)
        return any(r.from_program == canonical for r in self.ratios)

    def cards_affecting_ratios(self) -> set:
        """
        Every card name that appears in a card_dependency cell.

        This is the ONLY place card names live. Nothing in src/ hardcodes one.
        """
        return {
            r.card_dependency
            for r in self.ratios
            if r.card_dependency not in ("all", "other")
        }

    def blanket_no_yq(self, program: str) -> bool:
        """Whether a program has a structural no-carrier-surcharge policy."""
        return bool(self.meta(program).get("blanket_no_yq", False))

    def bookable_carriers(self, program: str) -> List[str]:
        return list(self.meta(program).get("bookable_carriers") or [])

    def own_metal(self, program: str) -> List[str]:
        return list(self.meta(program).get("own_metal") or [])

    def award_rules(self, program: str) -> dict:
        return dict(self.meta(program).get("award_rules") or {})

    def avios_family(self) -> List[str]:
        return list((self.program_doc.get("avios_family") or {}).get("members") or [])

    def is_avios_family(self, program: str) -> bool:
        return self.normalize_program(program) in self.avios_family()

    def programs_that_can_ticket(self, carrier: str) -> List[str]:
        """
        Every airline program whose bookable_carriers list includes this metal.

        Alliance-derived and NOT a verified partnership matrix - see the caveat
        block in programs.yaml. Consumers must present results as assumed.
        """
        if not carrier:
            return []
        carrier = carrier.strip().upper()
        return sorted(
            p
            for p, m in self.programs.items()
            if (m or {}).get("category") == "airline"
            and carrier in [c.upper() for c in (m.get("bookable_carriers") or [])]
        )

    def normalize_program(self, program: Optional[str]) -> Optional[str]:
        """
        Resolve a program name or alias to its canonical name.

        "United" -> "United MileagePlus", "Hyatt" -> "World of Hyatt",
        "united" -> "United MileagePlus". Unknown names pass through unchanged
        so that an unrecognised program simply finds no ratio rather than
        silently matching the wrong one.
        """
        if program is None:
            return None
        return self._alias_map.get(program.strip().lower(), program.strip())

    # ------------------------------------------------------------------
    # Querying
    # ------------------------------------------------------------------

    def _named_cards_for(
        self, from_program: str, to_program: str, check_date: date
    ) -> frozenset:
        """Card names explicitly listed for this pair on this date."""
        return frozenset(
            r.card_dependency
            for r in self.ratios
            if r.from_program == from_program
            and r.to_program == to_program
            and r.is_active_on(check_date)
            and r.card_dependency not in ("all", "other")
        )

    def get_all_ratios(
        self,
        from_program: str,
        to_program: str,
        check_date: date,
        card: Optional[str] = None,
    ) -> List[Ratio]:
        """
        Every ratio valid for this pair/date, optionally filtered to one card.

        With `card` given, this is the full candidate set for that card - which
        is what lets the optimizer evaluate ALL of a user's cards and choose the
        best, rather than stopping at the first match (bug #1).
        """
        from_program = self.normalize_program(from_program)
        to_program = self.normalize_program(to_program)

        candidates = [
            r
            for r in self.ratios
            if r.from_program == from_program
            and r.to_program == to_program
            and r.is_active_on(check_date)
        ]
        if card is None:
            return candidates

        named = self._named_cards_for(from_program, to_program, check_date)
        return [r for r in candidates if r.is_valid_on(check_date, card, named)]

    def query_ratio(
        self,
        from_program: str,
        to_program: str,
        check_date: date,
        card: Optional[str] = None,
    ) -> Optional[Ratio]:
        """
        Return the BEST ratio for a transfer on a given date and card.

        "Best" = most destination points per source point. A specific card match
        always outranks an "all"/"other" match at the same value, so an explicit
        card row wins ties.
        """
        matches = self.get_all_ratios(from_program, to_program, check_date, card)
        if not matches:
            return None

        def rank(r: Ratio):
            value = r.ratio_denominator / r.ratio_numerator
            card_specific = 1 if r.card_dependency not in ("all", "other") else 0
            return (value, card_specific)

        return max(matches, key=rank)

    def best_ratio_across_cards(
        self,
        from_program: str,
        to_program: str,
        check_date: date,
        cards: List[str],
    ) -> List[Tuple[str, Ratio]]:
        """
        Evaluate EVERY card the user holds and return (card, best_ratio) pairs.

        This is the fix for bug #1: the old code broke out of the card loop on
        the first match, making results order-dependent. Results are sorted best
        ratio first, so callers that want only the best can take the head.
        """
        out: List[Tuple[str, Ratio]] = []
        seen = set()
        for card in cards or [None]:
            ratio = self.query_ratio(from_program, to_program, check_date, card)
            if ratio is None:
                continue
            # Collapse duplicates: an "all" ratio matches every card identically.
            key = (
                ratio.card_dependency,
                ratio.ratio_numerator,
                ratio.ratio_denominator,
                ratio.effective_from,
            )
            if ratio.card_dependency in ("all", "other") and key in seen:
                continue
            seen.add(key)
            out.append((card, ratio))

        out.sort(key=lambda cr: cr[1].ratio_denominator / cr[1].ratio_numerator, reverse=True)
        return out

    def query_bonus(
        self, from_program: str, to_program: str, check_date: date
    ) -> Optional[Bonus]:
        from_program = self.normalize_program(from_program)
        to_program = self.normalize_program(to_program)
        for bonus in self.bonuses:
            if (
                self.normalize_program(bonus.from_program) == from_program
                and self.normalize_program(bonus.to_program) == to_program
                and bonus.is_active_on(check_date)
            ):
                return bonus
        return None

    def apply_ratio_and_bonus(
        self,
        points: int,
        from_program: str,
        to_program: str,
        check_date: date,
        card: Optional[str] = None,
    ) -> Tuple[int, str]:
        """Apply the best ratio and any active bonus. Returns (delivered, ratio_str)."""
        ratio = self.query_ratio(from_program, to_program, check_date, card)
        if ratio is None:
            raise ValueError(
                f"No ratio found for {from_program} -> {to_program} on {check_date}"
                + (f" with card {card!r}" if card else "")
            )

        delivered = ratio.apply(points)
        bonus = self.query_bonus(from_program, to_program, check_date)
        if bonus:
            delivered = bonus.apply(delivered)
        return delivered, ratio.as_string

    def partners_of(self, from_program: str, check_date: date) -> List[str]:
        """All destination programs reachable from `from_program` on a date."""
        from_program = self.normalize_program(from_program)
        return sorted(
            {
                r.to_program
                for r in self.ratios
                if r.from_program == from_program and r.is_active_on(check_date)
            }
        )

    def is_partner(self, from_program: str, to_program: str, check_date: date) -> bool:
        return bool(self.get_all_ratios(from_program, to_program, check_date))
