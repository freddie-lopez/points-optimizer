"""Run main() on a stubbed Trip B in the tree given by argv[1]; print stdout. argv[2] = scenario."""
import copy, json, os, sys, tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
root = Path(sys.argv[1]); scen = sys.argv[2]
state = Path(tempfile.mkdtemp())
os.environ.update(HOME=str(state), POINTS_OPTIMIZER_ENV_FILE=str(state / "absent.env"),
                  POINTS_OPTIMIZER_CACHE_DIR=str(state / "cache"), POINTS_OPTIMIZER_SNAPSHOT_DIR=str(state / "snap"),
                  SEATS_AERO_KEY="k_render_0000000000")
sys.path.insert(0, str(root))
import rich.console
_C = rich.console.Console
class Wide(_C):
    def __init__(self, *a, **k):
        k["width"] = 3000; k["no_color"] = True; super().__init__(*a, **k)
import src.main as m
m.Console = Wide
REAL = json.loads((root / "tests/fixtures/seats_aero/sfo_mad_real.json").read_text())
ISO = {("SFO","MAD"):"2027-01-15",("MAD","AMS"):"2027-01-19",("AMS","LHR"):"2027-01-23",("LHR","SFO"):"2027-01-27"}
VS = "B4virxxxxxxxxxxxxxxxxxxxxxx"
def row(o, d):
    r = copy.deepcopy(REAL["data"][0]); iso = ISO[(o, d)]
    r["Route"].update(OriginAirport=o, DestinationAirport=d); r["Date"] = iso; r["ParsedDate"] = iso + "T00:00:00Z"
    r["ID"] = (o + d + "x" * 30)[:27]
    if (o, d) == ("LHR", "SFO"):
        r["ID"] = VS
        r["Route"].update(Source="virginatlantic", OriginRegion="Europe", DestinationRegion="North America")
        r.update(YAvailable=False, JAvailable=True, JMileageCost="60000", JTotalTaxes=45000, TaxesCurrency="GBP",
                 JAirlines="VS" if scen == "single" else ("VS, AF, DL" if scen == "af" else "VS, DL"), JRemainingSeats=2)
    return r
def seg(fn):
    return {"ID":"s1","FlightNumber":fn,"OriginAirport":"LHR","DestinationAirport":"SFO","Order":1,"AvailabilityID":VS,
            "DepartsAt":"2027-01-27T11:00:00Z","ArrivesAt":"2027-01-27T14:00:00Z","AircraftName":"787-9"}
def trips():
    if scen == "404": return 404, None
    fn = "AF83" if scen == "af" else "VS19"
    t = {"ID":"t1","AvailabilityID":VS,"Source":"virginatlantic","Cabin":"business","MileageCost":60000,
         "TotalTaxes":45000,"TaxesCurrency":"GBP","AvailabilitySegments":[seg(fn)],"FlightNumbers":fn,"Carriers":fn[:2]}
    return 200, {"data":[t]}
def side(url, **kw):
    r = MagicMock(); r.raise_for_status.return_value = None
    if url.endswith("/search"):
        p = kw["params"]; body = {"data":[row(p["origin_airport"], p["destination_airport"])]}; r.status_code = 200
    else:
        r.status_code, body = trips()
    r.json.return_value = body; r.text = json.dumps(body); return r
argv = ["prog","--trip-fixture","trip_b_europe.json","--balance","UR=160000","--card","Chase Sapphire Preferred",
        "--transfer-date","2026-09-15","--allow-badge-fallback"]
if scen == "off": argv += ["--trips","off"]
if scen == "cap": argv += ["--trips","all","--trips-cap","1"]
if scen == "af": pass
sys.argv = argv
with patch("src.seats_client.requests.get", side_effect=side):
    code = m.main()
print(f"EXIT {code}")
