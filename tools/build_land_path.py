"""
Build src/ui/static/land.json - the one committed <path> the map draws - from
the world-atlas 2.0.2 "50m" land TopoJSON kept under docs/design/map-ref/.

    python tools/build_land_path.py            # writes src/ui/static/land.json
    python tools/build_land_path.py --check    # exit 1 when the committed file differs

The output is `{"w", "h", "lat_min", "lat_max", "d"}`: the four projection
numbers and the path. map.js implements the same projection from the same four
numbers, so an airport plotted by the browser lands on the land drawn here.

Projection: Miller cylindrical, longitude linear across `w`, latitude clamped to
[lat_min, lat_max] and scaled to `h`. Antarctica (every ring whose highest
point is south of lat_min) is dropped; rings under MIN_RING_POINTS are dropped;
coordinates are integers in a 4000x2080 box, written as one absolute move and
relative line-tos (`M x,y l dx,dy dx,dy … Z`), so the file stays well under the
400 KB budget while the rounding error is at most one unit - 2.2 px at the
maximum 8x zoom in a 1,080 px pane, against a 0.5 px stroke.

A test compares the committed file byte for byte with `build()`'s output.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
TOPO_PATH = ROOT / "docs" / "design" / "map-ref" / "land-50m.json"
OUT_PATH = ROOT / "src" / "ui" / "static" / "land.json"

W, H = 4000, 2080
LAT_MIN, LAT_MAX = -60.0, 83.0
MIN_RING_POINTS = 8


def _miller(lat: float) -> float:
    return 1.25 * math.log(math.tan(math.pi / 4 + 0.4 * math.radians(lat)))


def project(lon: float, lat: float, w: int = W, h: int = H,
            lat_min: float = LAT_MIN, lat_max: float = LAT_MAX) -> Tuple[float, float]:
    """Map units for a lon/lat. The same formula map.js applies to each hub."""
    lat = max(min(lat, lat_max), lat_min)
    x = (lon + 180.0) / 360.0 * w
    y_top, y_bot = _miller(lat_max), _miller(lat_min)
    y = (y_top - _miller(lat)) / (y_top - y_bot) * h
    return x, y


def _decode_arcs(topo: dict) -> List[List[Tuple[float, float]]]:
    sx, sy = topo["transform"]["scale"]
    tx, ty = topo["transform"]["translate"]
    arcs = []
    for arc in topo["arcs"]:
        x = y = 0
        pts = []
        for dx, dy in arc:
            x += dx
            y += dy
            pts.append((x * sx + tx, y * sy + ty))
        arcs.append(pts)
    return arcs


def _ring(arcs, indexes) -> List[Tuple[float, float]]:
    pts: List[Tuple[float, float]] = []
    for i in indexes:
        a = arcs[~i][::-1] if i < 0 else arcs[i]
        pts.extend(a if not pts else a[1:])
    return pts


def _ring_path(points: List[Tuple[int, int]]) -> str:
    """One closed ring, integer absolute move then relative line-tos."""
    x0, y0 = points[0]
    parts = [f"M{x0},{y0}l"]
    px, py = x0, y0
    steps = []
    for x, y in points[1:]:
        steps.append(f"{x - px},{y - py}")
        px, py = x, y
    parts.append(" ".join(steps))
    parts.append("Z")
    return "".join(parts)


def _int_ring(raw: List[Tuple[float, float]]) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    for lon, lat in raw:
        x, y = project(lon, lat)
        p = (int(round(x)), int(round(y)))
        if not out or p != out[-1]:
            out.append(p)
    # The ring's last point repeats its first in TopoJSON; the Z closes it.
    if len(out) > 1 and out[-1] == out[0]:
        out.pop()
    return out


def build(topojson_path: Path = TOPO_PATH) -> Dict[str, object]:
    topo = json.loads(Path(topojson_path).read_text(encoding="utf-8"))
    arcs = _decode_arcs(topo)
    rings: List[str] = []
    for geom in topo["objects"]["land"]["geometries"]:
        polys = geom["arcs"] if geom["type"] == "MultiPolygon" else [geom["arcs"]]
        for poly in polys:
            for ring_arcs in poly:
                raw = _ring(arcs, ring_arcs)
                if len(raw) < 3 or max(la for _, la in raw) < LAT_MIN:
                    continue  # Antarctica, or nothing to draw
                # Split at antimeridian jumps so no edge runs across the map.
                segs: List[List[Tuple[float, float]]] = [[]]
                for i, (lo, la) in enumerate(raw):
                    if i and abs(lo - raw[i - 1][0]) > 180:
                        segs.append([])
                    segs[-1].append((lo, la))
                for seg in segs:
                    pts = _int_ring(seg)
                    if len(pts) < MIN_RING_POINTS:
                        continue
                    rings.append(_ring_path(pts))
    return {"w": W, "h": H, "lat_min": LAT_MIN, "lat_max": LAT_MAX, "d": "".join(rings)}


def render(data: Dict[str, object]) -> str:
    return json.dumps(data, separators=(",", ":"), sort_keys=True) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="exit 1 when the committed land.json differs from the build")
    parser.add_argument("--topo", default=str(TOPO_PATH))
    parser.add_argument("--out", default=str(OUT_PATH))
    args = parser.parse_args(argv)
    text = render(build(Path(args.topo)))
    out = Path(args.out)
    if args.check:
        current = out.read_text(encoding="utf-8") if out.is_file() else None
        if current != text:
            print(f"{out} differs from the generator's output ({len(text)} bytes)", file=sys.stderr)
            return 1
        print(f"{out} matches the generator ({len(text)} bytes)")
        return 0
    out.write_text(text, encoding="utf-8")
    d = text.count("M")
    print(f"wrote {out}: {len(text)} bytes, {d} rings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
