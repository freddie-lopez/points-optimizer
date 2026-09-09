"""
The surcharge model: program x operating carrier x region x cabin x departure country.

THE ONE RULE THIS MODULE EXISTS TO ENFORCE:

    NO MATCHING ROW MEANS UNKNOWN. IT NEVER MEANS ZERO.

v0 had no surcharge concept at all, so two British Airways awards - one of them
departing Heathrow, where UK Air Passenger Duty stacks on top of BA's own
carrier-imposed surcharge - were scored at $0 cash and beat their cash
alternatives by a wide margin. The trip headline that came out of that (16%
better than paying cash) is not a real number.

The inverse failure would be just as bad: substituting a neighbouring row, a
program average, or a "reasonable default" produces a confident figure with
nothing behind it. So the matcher is exact-or-wildcard at five fixed specificity
tiers, first match wins, and anything unmatched is `confidence="unknown"`.

Precedence across the whole model is: CAPTURED > MODELED > UNKNOWN.
A captured $0 (a genuinely surcharge-free fare, read off a real booking page) is
a real, known zero and must never be confused with an unknown that renders blank.
"""
import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.models import SurchargeEstimate

DATA_DIR = Path(__file__).parent.parent / "data"

WILDCARD = "*"

# How many days before a modeled row is old enough to warn about. Carrier
# surcharges change without notice; a stale $800 is more dangerous than an
# unknown because it looks confident.
STALE_AFTER_DAYS = 180


class SurchargeTableError(ValueError):
    """Raised by validate() when data/surcharges.csv is structurally wrong."""


@dataclass(frozen=True)
class SurchargeRule:
    program: str
    operating_carrier: str
    route_region: str
    cabin: str
    departure_country: str
    amount_low: float
    amount_point: float
    amount_high: float
    currency: str
    basis: str
    confidence: str
    source: str
    verified_on: Optional[date]
    notes: str

    @property
    def key(self) -> Tuple[str, str, str, str, str]:
        return (
            self.program,
            self.operating_carrier,
            self.route_region,
            self.cabin,
            self.departure_country,
        )

    @property
    def specificity(self) -> int:
        """
        Which of the five tiers this row sits at. 1 is most specific.

        Tier 5 - (program, *, *, *, *) - is legitimate ONLY for a program with a
        blanket no-surcharge policy. validate() rejects it otherwise.
        """
        if self.operating_carrier == WILDCARD:
            return 5
        if self.route_region == WILDCARD:
            return 4
        if self.cabin == WILDCARD:
            return 3
        if self.departure_country == WILDCARD:
            return 2
        return 1

    @property
    def is_reachable(self) -> bool:
        """
        Whether any lookup could ever match this row.

        FINDING M-1. `_tier_keys` enumerates FIVE of the sixteen possible
        wildcard patterns, and they are exactly the ones where the wildcards form
        a SUFFIX of (carrier, region, cabin, departure_country). A row such as
        `(BAX, BA, NA-EU, *, GB)` - the natural shape for "UK APD applies in any
        cabin" - has a wildcard followed by a specific value, is never consulted,
        and the less specific `(BAX, BA, *, *, *)` answers instead. In the
        adversarial repro that meant $50 instead of $900, silently, in the
        dangerous direction.

        `specificity` happily ranks such a row (it called that one tier 3, ahead
        of the tier-4 generic that actually answered), so the table's own
        reckoning disagreed with the matcher. Rather than let the two drift, an
        unreachable row is now a LOAD-TIME ERROR: a row nobody can match is
        always a mistake, and a mistake in a surcharge table is how v0 produced a
        16% headline that did not exist.
        """
        fields = [
            self.operating_carrier,
            self.route_region,
            self.cabin,
            self.departure_country,
        ]
        seen_wildcard = False
        for value in fields:
            if value == WILDCARD:
                seen_wildcard = True
            elif seen_wildcard:
                return False
        return True

    def describe(self) -> str:
        return (
            f"{self.program} / metal={self.operating_carrier} / "
            f"{self.route_region} / cabin={self.cabin} / "
            f"dep={self.departure_country}"
        )


class SurchargeTable:
    """Loads data/surcharges.csv and answers most-specific-wins lookups."""

    def __init__(self, path: Optional[Path] = None, check_structure: bool = True):
        self.path = Path(path) if path else DATA_DIR / "surcharges.csv"
        self.rules: List[SurchargeRule] = []
        self._load()
        # FINDING M-2. `validate()` was written, tested, and NEVER CALLED outside
        # the test suite - so a corrupt production CSV (a duplicate row, a
        # negative amount, a future verified_on, a row nothing can match) was
        # caught only by `tests/test_surcharge.py` and never by a run. The
        # structural half of the validator now runs every time a table is loaded,
        # because a table that is wrong is wrong at runtime too, and answering a
        # surcharge lookup from a corrupt table is precisely the failure this
        # module exists to prevent. The POLICY half - which needs
        # programs.yaml's blanket_no_yq map - stays in `validate()` and is run by
        # `default_table()`, which is what production actually calls.
        if check_structure:
            self.check_structure()

    def _load(self) -> None:
        with open(self.path, "r") as f:
            for lineno, row in enumerate(csv.DictReader(f), start=2):
                if not (row.get("program") or "").strip():
                    continue
                verified = (row.get("verified_on") or "").strip()
                try:
                    self.rules.append(
                        SurchargeRule(
                            program=row["program"].strip(),
                            operating_carrier=row["operating_carrier"].strip().upper()
                            if row["operating_carrier"].strip() != WILDCARD
                            else WILDCARD,
                            route_region=row["route_region"].strip().upper()
                            if row["route_region"].strip() != WILDCARD
                            else WILDCARD,
                            cabin=row["cabin"].strip().upper()
                            if row["cabin"].strip() != WILDCARD
                            else WILDCARD,
                            departure_country=row["departure_country"].strip().upper()
                            if row["departure_country"].strip() != WILDCARD
                            else WILDCARD,
                            amount_low=float(row["amount_low"]),
                            amount_point=float(row["amount_point"]),
                            amount_high=float(row["amount_high"]),
                            currency=(row.get("currency") or "USD").strip().upper(),
                            basis=(row.get("basis") or "round_trip").strip(),
                            confidence=(row.get("confidence") or "modeled").strip(),
                            source=(row.get("source") or "").strip(),
                            verified_on=date.fromisoformat(verified) if verified else None,
                            notes=(row.get("notes") or "").strip(),
                        )
                    )
                except (KeyError, ValueError) as e:
                    raise SurchargeTableError(
                        f"{self.path}:{lineno}: could not parse row: {e}"
                    ) from e

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def _tier_keys(
        self,
        program: str,
        carrier: str,
        region: str,
        cabin: str,
        departure_country: str,
    ) -> List[Tuple[str, str, str, str, str]]:
        """The five specificity tiers, most specific first. Fixed order, no blending."""
        c = (carrier or "").upper()
        r = (region or "").upper()
        cb = (cabin or "").upper()
        dc = (departure_country or "").upper()
        return [
            (program, c, r, cb, dc),
            (program, c, r, cb, WILDCARD),
            (program, c, r, WILDCARD, WILDCARD),
            (program, c, WILDCARD, WILDCARD, WILDCARD),
            (program, WILDCARD, WILDCARD, WILDCARD, WILDCARD),
        ]

    def match(
        self,
        program: str,
        carrier: str,
        region: str,
        cabin: str,
        departure_country: str,
    ) -> Optional[SurchargeRule]:
        """The first tier that matches wins. No averaging, no nearest-neighbour."""
        index = {rule.key: rule for rule in self.rules}
        for key in self._tier_keys(program, carrier, region, cabin, departure_country):
            if key in index:
                return index[key]
        return None

    def resolve(
        self,
        program: str,
        carrier: str,
        region: str,
        cabin: str,
        departure_country: str,
        *,
        is_round_trip: bool = True,
        carrier_is_known: bool = True,
        captured: Optional[SurchargeEstimate] = None,
        today: Optional[date] = None,
    ) -> SurchargeEstimate:
        """
        Resolve a surcharge. Returns a SurchargeEstimate, possibly `unknown`.

        `carrier_is_known=False` short-circuits to UNKNOWN before any lookup. The
        tool never guesses metal: the whole model is keyed on which airline
        actually flies the segment, so an assumed carrier can only produce a
        confident wrong answer.
        """
        # CAPTURED BEATS MODELED. A real figure off a real booking page wins,
        # including a real $0.
        if captured is not None and captured.confidence == "captured":
            return captured

        if not carrier_is_known or not carrier:
            # ONE exception, and it is not a loophole: a tier-5 (program, *, *, *, *)
            # row is by construction METAL-INDEPENDENT. The validator has already
            # confirmed such a row exists only for a program with a blanket no-YQ
            # POLICY - United and Aeroplan levy no carrier surcharge whatever
            # aeroplane you end up on, so not knowing the metal does not stop us
            # answering. Every other tier needs the metal and does not get it.
            blanket = self.match(program, WILDCARD, WILDCARD, WILDCARD, WILDCARD)
            if blanket is not None:
                return SurchargeEstimate(
                    amount_low=blanket.amount_low,
                    amount_point=blanket.amount_point,
                    amount_high=blanket.amount_high,
                    currency=blanket.currency,
                    basis=blanket.basis,
                    confidence=blanket.confidence,
                    matched_rule=blanket.describe(),
                    source=blanket.source,
                    verified_on=blanket.verified_on,
                    notes=(
                        f"{blanket.notes} Applied without a confirmed operating "
                        f"carrier because this is a program-wide policy row, which "
                        f"does not depend on the metal."
                    ).strip(),
                )
            return SurchargeEstimate.unknown(
                "Operating carrier is unknown or assumed. A surcharge is a property "
                "of the metal, so it cannot be modelled without knowing which "
                "airline actually flies the segment."
            )

        rule = self.match(program, carrier, region, cabin, departure_country)
        if rule is None:
            return SurchargeEstimate.unknown(
                f"No surcharge rule for {program} on {carrier} metal, "
                f"{region or '?'} {cabin or '?'} departing {departure_country or '?'}. "
                f"This is NOT zero - the figure is simply not known."
            )

        low, point, high = rule.amount_low, rule.amount_point, rule.amount_high
        notes = rule.notes
        basis = rule.basis

        # BASIS CONVERSION. The research is expressed round-trip. Halving it for a
        # one-way is an approximation and is disclosed every time, because real
        # surcharges are directional and are not symmetric out of the UK.
        converted = False
        # Halving a zero-policy row is a no-op, so do not disclose a conversion
        # that did not change anything - it reads as a caveat on a number that
        # has none.
        if rule.basis == "round_trip" and not is_round_trip and high > 0:
            low, point, high = low / 2.0, point / 2.0, high / 2.0
            basis = "one_way"
            converted = True

        if rule.verified_on and today:
            age = (today - rule.verified_on).days
            if age > STALE_AFTER_DAYS:
                notes = (
                    f"{notes} STALE: last verified {rule.verified_on} "
                    f"({age} days ago)."
                ).strip()

        est = SurchargeEstimate(
            amount_low=low,
            amount_point=point,
            amount_high=high,
            currency=rule.currency,
            basis=basis,
            confidence=rule.confidence,
            matched_rule=rule.describe(),
            source=rule.source,
            verified_on=rule.verified_on,
            notes=notes,
        )
        if converted:
            est.notes = (
                f"{est.notes} BASIS CONVERTED: the source row is round-trip and was "
                f"halved for a one-way leg. Real surcharges are directional and are "
                f"not symmetric, particularly out of the UK."
            ).strip()
        return est

    def resolve_ambiguous_metal(
        self,
        program: str,
        carriers: List[str],
        region: str,
        cabin: str,
        departure_country: str,
        **kw,
    ) -> SurchargeEstimate:
        """
        Resolve a surcharge when the metal is a LIST of possible carriers.

        Seats.aero's `{X}Airlines` is "AC, LH, UA, VL" - four airlines that could
        operate the segment, not one that will. There are exactly three honest
        outcomes:

          * no carriers at all           -> the ordinary unknown path, which a
                                            blanket program policy row may still
                                            answer (Aeroplan, United);
          * every listed carrier resolves to the SAME figure -> that figure, with
            the ambiguity disclosed. This is safe precisely because the answer
            does not depend on which of them flies it;
          * they differ                  -> UNKNOWN with a break-even, because
                                            the answer DOES depend on it.

        Taking the first entry of the list is not one of them.
        """
        # FINDING L-5. `["BA", "BA"]` and `[" ba ", "BA"]` are ONE carrier
        # written twice, and both used to take the multi-carrier branch and
        # append "OPERATING METAL AMBIGUOUS: Seats.aero listed BA, BA as possible
        # operating carriers". The figure was right; the disclosure was false,
        # and a false disclosure trains a reader to discount the true ones. A
        # `None` entry became `str(None)` -> a carrier code literally named
        # "NONE", which was then reported as an airline. Deduplicated after
        # normalising, with `None` treated as the absence it is.
        codes: List[str] = []
        for raw in carriers or []:
            if raw is None:
                continue
            code = str(raw).strip().upper()
            if code and code not in codes:
                codes.append(code)
        if not codes:
            return self.resolve(
                program, "", region, cabin, departure_country,
                carrier_is_known=False, **kw
            )

        if len(codes) == 1:
            return self.resolve(
                program, codes[0], region, cabin, departure_country,
                carrier_is_known=True, **kw
            )

        outcomes = {
            code: self.resolve(
                program, code, region, cabin, departure_country,
                carrier_is_known=True, **kw
            )
            for code in codes
        }

        def fingerprint(e: SurchargeEstimate):
            if not e.is_known:
                return ("unknown",)
            return (
                "known", e.amount_low, e.amount_point, e.amount_high,
                e.currency, e.basis, e.confidence,
            )

        prints = {code: fingerprint(e) for code, e in outcomes.items()}
        distinct = set(prints.values())

        listed = ", ".join(codes)
        if len(distinct) == 1 and next(iter(distinct)) != ("unknown",):
            est = outcomes[codes[0]]
            est.notes = (
                f"{est.notes} OPERATING METAL AMBIGUOUS: Seats.aero listed "
                f"{listed} as possible operating carriers. Every one of them "
                f"resolves to this same figure under {program}'s policy, so the "
                f"ambiguity does not change the answer. No carrier was chosen."
            ).strip()
            return est

        if len(distinct) == 1:
            return SurchargeEstimate.unknown(
                f"Seats.aero listed {listed} as possible operating carriers and "
                f"{program} has no surcharge rule for any of them. Unknown, "
                f"not zero."
            )

        detail = "; ".join(
            f"{code}={outcomes[code].render()}" for code in codes
        )
        return SurchargeEstimate.unknown(
            f"OPERATING METAL AMBIGUOUS and it changes the answer. Seats.aero "
            f"listed {listed} as possible operating carriers on this segment and "
            f"they do not share a surcharge outcome under {program} ({detail}). "
            f"Which aeroplane you end up on is not knowable from this response, "
            f"so the surcharge is unknown and the leg is scored as a break-even. "
            f"Picking the first carrier in the list would invent the answer."
        )

    # ------------------------------------------------------------------
    # Validation (runs as a test)
    # ------------------------------------------------------------------

    def check_structure(self) -> None:
        """
        Every check that needs NOTHING but the table itself. Raises on failure.

        Split out of `validate()` so it can run at load time (finding M-2). The
        checks left in `validate()` are exactly those that need programs.yaml.
        """
        seen: Dict[Tuple, SurchargeRule] = {}
        for rule in self.rules:
            if rule.key in seen:
                # FINDING M-2: `match()` builds `{rule.key: rule for ...}`, so
                # the LAST duplicate silently won and reversing two lines in the
                # CSV reversed the answer ($900 vs $100). The validator always
                # said the right thing about this - "two rows matching one lookup
                # means the answer depends on file order" - it just never ran.
                raise SurchargeTableError(
                    f"Duplicate surcharge rule at the same specificity tier: "
                    f"{rule.describe()}. Two rows matching one lookup means the "
                    f"answer depends on file order."
                )
            seen[rule.key] = rule

            if not rule.is_reachable:
                # FINDING M-1.
                raise SurchargeTableError(
                    f"{rule.describe()}: this row can NEVER be matched. "
                    f"`_tier_keys` only ever looks up patterns whose wildcards "
                    f"form a suffix of (carrier, region, cabin, "
                    f"departure_country), and this row has a wildcard followed by "
                    f"a specific value. It would sit silently in the table while "
                    f"a LESS specific row answered in its place - which is how a "
                    f"$900 ex-UK row loses to a $50 generic. Either widen the "
                    f"wildcards to a suffix, or write out the specific rows."
                )

            if not (rule.amount_low <= rule.amount_point <= rule.amount_high):
                raise SurchargeTableError(
                    f"{rule.describe()}: low <= point <= high is violated "
                    f"({rule.amount_low}, {rule.amount_point}, {rule.amount_high})."
                )
            if rule.amount_low < 0:
                raise SurchargeTableError(
                    f"{rule.describe()}: negative surcharge {rule.amount_low}."
                )
            if not rule.source:
                raise SurchargeTableError(f"{rule.describe()}: no source.")
            if rule.verified_on is None:
                raise SurchargeTableError(f"{rule.describe()}: no verified_on date.")
            if rule.confidence not in ("captured", "modeled"):
                raise SurchargeTableError(
                    f"{rule.describe()}: confidence must be 'captured' or "
                    f"'modeled', got {rule.confidence!r}. A table row can never be "
                    f"'unknown' - unknown is the ABSENCE of a row."
                )
            if rule.basis not in ("round_trip", "one_way", "per_segment"):
                raise SurchargeTableError(
                    f"{rule.describe()}: unknown basis {rule.basis!r}."
                )

    def validate(
        self,
        blanket_no_yq: Optional[Dict[str, bool]] = None,
        today: Optional[date] = None,
    ) -> List[str]:
        """
        Structural checks on the table. Raises on an error; returns warnings.

        `blanket_no_yq` maps program name -> whether that program has a structural
        no-carrier-surcharge policy. Only such a program may carry a `carrier=*`
        wildcard row: a wildcard for a program that DOES levy YQ would silently
        answer every unmatched lookup with one number.
        """
        blanket_no_yq = blanket_no_yq or {}
        today = today or date.today()
        warnings: List[str] = []

        # Everything that needs only the table. Raises. Also runs at load time.
        self.check_structure()

        for rule in self.rules:
            if rule.verified_on and rule.verified_on > today:
                raise SurchargeTableError(
                    f"{rule.describe()}: verified_on {rule.verified_on} is in the "
                    f"future. That is a typo or a fabrication."
                )

            if rule.operating_carrier == WILDCARD and not blanket_no_yq.get(
                rule.program, False
            ):
                raise SurchargeTableError(
                    f"{rule.describe()}: a carrier=* wildcard row exists for a "
                    f"program not marked blanket_no_yq in programs.yaml. A wildcard "
                    f"is only legitimate for a program-wide no-surcharge POLICY; "
                    f"otherwise it answers every unmatched metal with one number."
                )
            if rule.operating_carrier == WILDCARD and rule.amount_high != 0:
                raise SurchargeTableError(
                    f"{rule.describe()}: a carrier=* wildcard row must be a zero "
                    f"policy row, not a non-zero estimate."
                )

            if rule.verified_on and (today - rule.verified_on).days > STALE_AFTER_DAYS:
                warnings.append(
                    f"{rule.describe()}: last verified {rule.verified_on}, more than "
                    f"{STALE_AFTER_DAYS} days ago. Carrier surcharges change without "
                    f"notice and a stale figure looks more confident than it is."
                )

        return warnings


_DEFAULT_TABLE: Optional[SurchargeTable] = None


def blanket_no_yq_map(programs_yaml: Optional[Path] = None) -> Dict[str, bool]:
    """
    Which programs are declared to levy no carrier surcharge as a matter of policy.

    Read straight from programs.yaml rather than through RatioManager, so the
    surcharge table can validate itself without importing the ratio machinery.
    A file that cannot be read yields an EMPTY map, which is the strict
    direction: an unreadable policy file means no program is licensed to carry a
    `carrier=*` wildcard row, so validation fails loudly instead of passing on
    an assumption.
    """
    path = Path(programs_yaml) if programs_yaml else DATA_DIR / "programs.yaml"
    try:
        import yaml

        data = yaml.safe_load(path.read_text()) or {}
    except Exception:  # noqa: BLE001 - a missing file must not crash a lookup
        return {}
    out: Dict[str, bool] = {}
    for entry in (data.get("programs") or {}).values():
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if name:
            out[str(name)] = bool(entry.get("blanket_no_yq", False))
    return out


def default_table() -> SurchargeTable:
    """
    Process-wide cached table for the production CSV, VALIDATED ON LOAD.

    FINDING M-2: this used to construct the table and hand it back unchecked, so
    every structural guarantee `validate()` offers applied only to the test
    suite. Both halves now run before any production lookup can happen. The
    staleness warnings are attached to the table rather than raised - a stale row
    is a warning by design - and `print_live_banner`-style output can surface
    them; a structural error still raises and stops the run, which is correct:
    answering a surcharge lookup from a corrupt table is the failure this whole
    module exists to prevent.
    """
    global _DEFAULT_TABLE
    if _DEFAULT_TABLE is None:
        table = SurchargeTable()
        table.load_warnings = table.validate(blanket_no_yq_map())
        _DEFAULT_TABLE = table
    return _DEFAULT_TABLE
