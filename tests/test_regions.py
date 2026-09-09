"""Step 6: IATA -> country -> region, and the UK APD departure hook."""
import pytest

from src import regions
from src.regions import UnknownAirportError, UnknownCarrierError


def test_region_pair_is_unordered():
    assert regions.classify("SFO", "MAD") == "NA-EU"
    assert regions.classify("MAD", "SFO") == "NA-EU"
    assert regions.classify("SFO", "MAD") == regions.classify("MAD", "SFO")


def test_same_region_pair_doubles():
    assert regions.classify("MAD", "AMS") == "EU-EU"
    assert regions.classify("SFO", "JFK") == "NA-NA"


def test_transpacific_and_other_pairs():
    """Ordering follows regions.REGIONS, not the alphabet - see the module docstring."""
    assert regions.classify("SFO", "NRT") == "NA-AS"
    assert regions.classify("LHR", "SIN") == "EU-AS"
    assert regions.classify("JFK", "GRU") == "NA-SA"


def test_pair_ordering_is_stable_regardless_of_argument_order():
    for a, b in [("SFO", "NRT"), ("LHR", "SIN"), ("JFK", "GRU"), ("MAD", "AMS")]:
        assert regions.classify(a, b) == regions.classify(b, a)


def test_departure_country_is_directional():
    """UK APD is levied on DEPARTURE from the UK and on nothing else."""
    assert regions.departure_country("LHR") == "GB"
    assert regions.departure_country("SFO") == "US"
    assert regions.departure_country("LHR") != regions.departure_country("SFO")


@pytest.mark.parametrize("code", ["LHR", "LGW", "LTN", "STN", "SEN", "LCY", "LON"])
def test_every_london_airport_is_gb(code):
    assert regions.departure_country(code) == "GB"


def test_unknown_airport_raises_and_names_the_code():
    with pytest.raises(UnknownAirportError, match="ZZZ"):
        regions.classify("SFO", "ZZZ")


def test_unknown_airport_does_not_default_to_a_region():
    """The failure mode this guards: a wrong region picks a wrong surcharge band."""
    with pytest.raises(UnknownAirportError):
        regions.region_of("QQQ")


def test_empty_code_raises():
    with pytest.raises(UnknownAirportError):
        regions.region_of("")


def test_every_airport_in_the_real_fixtures_is_known():
    for code in ("MRY", "SFO", "SJC", "MAD", "AMS", "LHR", "LGW", "LTN", "SEN",
                 "JFK", "PHX", "PHL", "ATL", "YUL", "DUB"):
        assert regions.region_of(code) in regions.REGIONS


def test_carrier_lookup_and_alliance():
    assert regions.carrier("BA").name == "British Airways"
    assert regions.alliance_of("BA") == "oneworld"
    assert regions.alliance_of("UA") == "star"
    assert regions.alliance_of("EI") is None, "Aer Lingus is in no alliance"


def test_unknown_carrier_raises_but_carrier_name_degrades_gracefully():
    with pytest.raises(UnknownCarrierError):
        regions.carrier("ZZ")
    assert regions.carrier_name("ZZ") == "ZZ"
    assert regions.carrier_name("") == "(unknown carrier)"


def test_ba_and_united_are_in_different_alliances():
    """
    Load-bearing for the alternatives engine, and a correction to the plan.

    Step 9 of docs/plans/v1.md asks for Aeroplan and United alternatives on a
    BA-METAL candidate. They are Star Alliance; BA is oneworld. Neither can
    ticket BA metal, so a same-metal alternative to BA is an Avios-family
    program, not a Star one. See src/alternatives.py.
    """
    assert regions.alliance_of("BA") != regions.alliance_of("UA")
    assert regions.alliance_of("AC") == regions.alliance_of("UA") == "star"
