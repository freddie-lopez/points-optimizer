"""
Filter the full airportsdata CSV (docs/design/map-ref/airportsdata-20260905-
airports.csv, MIT, 3 MB, not committed) down to the rows with an IATA code and
the seven columns the map needs, as data/airportsdata_iata.csv (committed).

    python tools/filter_airportsdata.py [--src PATH] [--out PATH]

The output is sorted by IATA code and written with `\\n` line ends, so a rerun
over the same input reproduces it byte for byte; a test compares the two where
the full CSV is present. Nothing here is typed by hand: every row is a row of
the source file.
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
from pathlib import Path
from typing import Iterable, List

ROOT = Path(__file__).resolve().parent.parent
SRC_PATH = ROOT / "docs" / "design" / "map-ref" / "airportsdata-20260905-airports.csv"
OUT_PATH = ROOT / "data" / "airportsdata_iata.csv"

COLUMNS = ["iata", "icao", "name", "city", "country", "lat", "lon"]


def filter_rows(source) -> List[List[str]]:
    """Rows with an IATA code, projected to COLUMNS, sorted by code."""
    reader = csv.DictReader(source)
    missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"the source CSV lacks the column(s) {missing}")
    rows = []
    for row in reader:
        code = (row.get("iata") or "").strip()
        if not code:
            continue
        rows.append([str(row.get(c) or "").strip() for c in COLUMNS])
    rows.sort(key=lambda r: r[0])
    return rows


def render(rows: Iterable[List[str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(COLUMNS)
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument("--src", default=str(SRC_PATH))
    parser.add_argument("--out", default=str(OUT_PATH))
    args = parser.parse_args(argv)
    src = Path(args.src)
    if not src.is_file():
        print(f"no source CSV at {src}", file=sys.stderr)
        return 1
    with open(src, newline="", encoding="utf-8") as f:
        rows = filter_rows(f)
    text = render(rows)
    Path(args.out).write_text(text, encoding="utf-8")
    print(f"wrote {args.out}: {len(rows)} rows, {len(text)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
