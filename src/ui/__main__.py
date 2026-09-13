"""
Launch the local UI: `.venv/bin/python -m src.ui` -> http://127.0.0.1:8777/

    --port N      default 8777; 0 picks any free port. A busy port is an error,
                  never a silent move to another one.
    --wallet PATH the wallet file (default ./wallet.json when it exists). Edits
                  made in the page are held in memory and never written.
    --no-open     do not open a browser tab.
"""
from __future__ import annotations

import argparse
import errno
import sys
import webbrowser
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.ui",
        description="Local web UI over the points optimizer. Binds 127.0.0.1 only.",
    )
    p.add_argument("--port", type=int, default=8777,
                   help="Port on 127.0.0.1 (default 8777; 0 = any free port).")
    p.add_argument("--wallet", default=None, metavar="PATH",
                   help="Wallet JSON (default ./wallet.json if it exists). Never written.")
    p.add_argument("--no-open", action="store_true", help="Do not open a browser.")
    return p


def main(argv=None) -> int:
    from src.ui.api import route
    from src.ui.engine import Engine
    from src.ui.server import UIServer

    args = build_parser().parse_args(argv)
    wallet = args.wallet
    if wallet is None and Path("wallet.json").is_file():
        wallet = "wallet.json"
    if wallet is not None and not Path(wallet).is_file():
        print(f"Wallet file not found: {wallet}", file=sys.stderr)
        return 2
    if not 0 <= args.port <= 65535:
        print(f"--port {args.port} is not a port number.", file=sys.stderr)
        return 1
    try:
        server = UIServer(Engine(wallet_path=wallet), route, port=args.port)
    except OSError as e:
        if e.errno in (errno.EADDRINUSE, errno.EACCES):
            print(
                f"Port {args.port} on 127.0.0.1 is not available ({e.strerror}). "
                f"Nothing was started. Pass --port N for another port, or --port 0 "
                f"for any free one.",
                file=sys.stderr,
            )
            return 1
        raise
    print(f"Points optimizer UI: {server.url}  (Ctrl-C to stop)", flush=True)
    if wallet:
        print(f"Wallet: {wallet} (edits in the page are held in memory, never written)",
              flush=True)
    else:
        print("No wallet file: add balances in the page, or restart with --wallet PATH.",
              flush=True)
    if not args.no_open:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    from src import config

    # MAC-A: the launcher prints the URL and the key banner to a terminal too.
    config.use_utf8_output()
    sys.exit(main())
