"""
Tests for the rewritten UR-only ratio table (Task 2).

These assert the DATA, not the code: the previous table had Marriott at 3:1 and
IHG at 2:1, both wrong, and was missing seven partners this trip needs.
"""
from datetime import date
from pathlib import Path

import pytest

from src.ratio_manager import RatioManager

DATA = Path(__file__).parent.parent / "data"
CSR = "Chase Sapphire Reserve"
CSP = "Chase Sapphire Preferred"
INK = "Chase Ink Business Preferred"
D = date(2027, 1, 15)


@pytest.fixture
def rm():
    return RatioManager(DATA / "ratios.csv", DATA / "bonuses.csv", DATA / "programs.yaml")


REQUIRED_AIRLINES = [
    "Aer Lingus AerClub",
    "Air Canada Aeroplan",
    "Air France-KLM Flying Blue",
    "British Airways Executive Club",
    "Club Iberia Plus",
    "JetBlue TrueBlue",
    "Singapore Airlines KrisFlyer",
    "Southwest Rapid Rewards",
    "United MileagePlus",
    "Virgin Atlantic Flying Club",
]

REQUIRED_HOTELS = ["IHG One Rewards", "Marriott Bonvoy", "Wyndham Rewards", "World of Hyatt"]


@pytest.mark.parametrize("program", REQUIRED_AIRLINES)
def test_airline_partner_present_at_one_to_one(rm, program):
    r = rm.query_ratio("UR", program, D, CSR)
    assert r is not None, f"{program} missing from the ratio table"
    assert (r.ratio_numerator, r.ratio_denominator) == (1, 1)


@pytest.mark.parametrize("program", REQUIRED_HOTELS)
def test_hotel_partner_present(rm, program):
    assert rm.query_ratio("UR", program, D, CSR) is not None


def test_marriott_is_one_to_one_not_three_to_one(rm):
    """The old table said 3:1. It is 1:1."""
    r = rm.query_ratio("UR", "Marriott Bonvoy", D, CSR)
    assert (r.ratio_numerator, r.ratio_denominator) == (1, 1)


def test_ihg_is_one_to_one_not_two_to_one(rm):
    """The old table said 2:1. It is 1:1."""
    r = rm.query_ratio("UR", "IHG One Rewards", D, CSR)
    assert (r.ratio_numerator, r.ratio_denominator) == (1, 1)


def test_wyndham_added(rm):
    r = rm.query_ratio("UR", "Wyndham Rewards", D, CSR)
    assert r is not None
    assert (r.ratio_numerator, r.ratio_denominator) == (1, 1)


# ---------------------------------------------------------------------------
# The Oct 2026 4:3 change is HYATT-ONLY
# ---------------------------------------------------------------------------


def test_hyatt_reserve_stays_one_to_one_across_the_change(rm):
    for d in (date(2026, 9, 30), date(2026, 10, 1), date(2027, 1, 15)):
        r = rm.query_ratio("UR", "World of Hyatt", d, CSR)
        assert (r.ratio_numerator, r.ratio_denominator) == (1, 1), f"failed on {d}"


@pytest.mark.parametrize("card", [CSP, INK])
def test_hyatt_non_reserve_flips_on_oct_1_2026(rm, card):
    before = rm.query_ratio("UR", "World of Hyatt", date(2026, 9, 30), card)
    after = rm.query_ratio("UR", "World of Hyatt", date(2026, 10, 1), card)
    assert (before.ratio_numerator, before.ratio_denominator) == (1, 1)
    assert (after.ratio_numerator, after.ratio_denominator) == (4, 3)


def test_hyatt_pre_change_row_exists_for_non_reserve_cards(rm):
    """The gap the brief called out: no pre-2026-10-01 row for a non-Reserve card."""
    assert rm.query_ratio("UR", "World of Hyatt", date(2026, 1, 1), CSP) is not None
    assert rm.query_ratio("UR", "World of Hyatt", date(2026, 9, 30), INK) is not None


@pytest.mark.parametrize("program", REQUIRED_AIRLINES)
def test_airlines_are_unaffected_by_the_october_change(rm, program):
    """The 4:3 change is Hyatt-only. Airlines stay 1:1 on every card."""
    for card in (CSR, CSP, INK):
        for d in (date(2026, 9, 30), date(2026, 10, 1), date(2027, 1, 15)):
            r = rm.query_ratio("UR", program, d, card)
            assert (r.ratio_numerator, r.ratio_denominator) == (1, 1), (
                f"{program} on {card} at {d} is not 1:1"
            )


@pytest.mark.parametrize("program", ["IHG One Rewards", "Marriott Bonvoy", "Wyndham Rewards"])
def test_other_hotels_unaffected_by_the_october_change(rm, program):
    for d in (date(2026, 9, 30), date(2026, 10, 1)):
        r = rm.query_ratio("UR", program, d, CSP)
        assert (r.ratio_numerator, r.ratio_denominator) == (1, 1)


# ---------------------------------------------------------------------------
# Removed / out-of-scope rows
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "program", ["Emirates Skywards", "LATAM", "LATAM Pass", "Japan Airlines", "Hilton Honors"]
)
def test_non_partners_absent(rm, program):
    assert rm.query_ratio("UR", program, D, CSR) is None


@pytest.mark.parametrize("issuer", ["MR", "TY", "CapitalOne", "Bilt"])
def test_out_of_scope_issuers_have_no_rows(rm, issuer):
    assert rm.partners_of(issuer, D) == []


def test_schema_can_still_hold_other_issuers():
    """
    Dropping MR/TY/C1/Bilt is a data decision, not a schema restriction.

    CHANGED IN v1 (Step 3): `source` and `verified_on` were appended. Every row
    is a claim about the world and must say where the claim came from and when it
    was last checked. The from_program column is unchanged, so the schema still
    holds any issuer.
    """
    import csv

    with open(DATA / "ratios.csv") as f:
        header = next(csv.reader(f))
    assert header == [
        "from_program",
        "to_program",
        "card_dependency",
        "ratio_numerator",
        "ratio_denominator",
        "effective_from",
        "effective_to",
        "source",
        "verified_on",
    ]


def test_every_ratio_row_carries_a_source_and_a_verification_date(rm):
    """
    v1 Step 3. A row with no provenance is an unfalsifiable claim.

    Note this asserts the PRESENCE of provenance, not its truth - no test can
    verify that a transfer partner still exists.
    """
    assert rm.ratio_provenance, "no provenance loaded"
    assert len(rm.ratio_provenance) == len(rm.ratios)
    for ratio, prov in zip(rm.ratios, rm.ratio_provenance):
        label = f"{ratio.from_program}->{ratio.to_program} [{ratio.card_dependency}]"
        assert prov["source"], f"{label}: empty source"
        assert prov["verified_on"], f"{label}: empty verified_on"
        date.fromisoformat(prov["verified_on"])  # must parse


def test_ratio_verification_dates_are_recent_and_not_in_the_future(rm):
    """
    Stale data that looks confident is more dangerous than an admitted unknown.

    90 days is the plan's threshold. The dates must also not be in the future -
    a future verified_on is a typo or a fabrication.
    """
    today = date.today()
    for ratio, prov in zip(rm.ratios, rm.ratio_provenance):
        verified = date.fromisoformat(prov["verified_on"])
        label = f"{ratio.from_program}->{ratio.to_program}"
        assert verified <= today, f"{label}: verified_on {verified} is in the future"
        assert (today - verified).days <= 90, (
            f"{label}: verified_on {verified} is more than 90 days old. "
            f"Re-verify the row or remove it."
        )


def test_no_row_exists_for_any_declared_non_partner(rm):
    """
    v1 Step 3. The not_partners block exists so a removed partner is never
    silently re-added. Nothing enforced that before.
    """
    not_partners = (rm.program_doc.get("not_partners") or {}).get("UR") or []
    assert not_partners, "the UR not_partners block should not be empty"
    for entry in not_partners:
        name = entry["name"] if isinstance(entry, dict) else str(entry)
        assert rm.query_ratio("UR", name, D, CSR) is None, (
            f"{name} is recorded as a NON-partner but has a ratio row"
        )


# ---------------------------------------------------------------------------
# Other issuer currencies: declared, but deliberately unpopulated
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("issuer", ["MR", "TY", "C1", "Bilt"])
def test_other_issuers_are_declared_so_a_wallet_can_name_them(rm, issuer):
    assert issuer in rm.issuer_currencies()


@pytest.mark.parametrize("issuer", ["MR", "TY", "C1", "Bilt"])
def test_other_issuers_have_no_verified_partner_rows(rm, issuer):
    """
    v1 SHIPS THE MACHINERY, NOT THE DATA.

    The multi-currency funding engine is built and unit-tested, but no MR / TY /
    C1 / Bilt partner row could be verified from the v1 build environment (no
    network egress), and the v0 standard - a partner that cannot be verified is
    omitted, not guessed - was not lowered to fill the table.

    If this test ever fails, someone added rows. That is fine and expected - but
    they must carry a real source and verified_on, which the tests above enforce.
    """
    assert not rm.has_verified_partners(issuer)
    assert rm.meta(issuer).get("partners_verified") is False


def test_partner_count(rm):
    partners = rm.partners_of("UR", D)
    assert len(partners) == 14, partners
    assert set(partners) == set(REQUIRED_AIRLINES) | set(REQUIRED_HOTELS)


# ---------------------------------------------------------------------------
# Aliases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "alias,canonical",
    [
        ("United", "United MileagePlus"),
        ("Hyatt", "World of Hyatt"),
        ("IHG", "IHG One Rewards"),
        ("Flying Blue", "Air France-KLM Flying Blue"),
        ("BA", "British Airways Executive Club"),
        ("Iberia", "Club Iberia Plus"),
        ("Aeroplan", "Air Canada Aeroplan"),
    ],
)
def test_program_aliases_resolve(rm, alias, canonical):
    assert rm.normalize_program(alias) == canonical


def test_unknown_program_passes_through_rather_than_mismatching(rm):
    assert rm.normalize_program("Some Unknown Program") == "Some Unknown Program"
    assert rm.query_ratio("UR", "Some Unknown Program", D, CSR) is None


def test_production_bonus_table_is_empty(rm):
    """No current UR transfer bonus has been verified, so none is claimed."""
    assert rm.bonuses == []
