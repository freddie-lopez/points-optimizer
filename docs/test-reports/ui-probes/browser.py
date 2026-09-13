"""
Start a probe server (ui_probe_server.py) and drive the real page in headless
Chromium (drive.js). Shared by the browser probes and by hand.

    from browser import ui_browser
    dom = ui_browser("offline_b", kind="trip", trip="trip_b_europe", mode="offline")
"""
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent.parent
PY = str(ROOT / ".venv" / "bin" / "python")
NODE = "node"
SHOTS = HERE / "shots"


class Server:
    def __init__(self, scenario):
        self.proc = subprocess.Popen(
            [PY, str(HERE / "ui_probe_server.py"), scenario],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, cwd=str(ROOT),
        )
        line = self.proc.stdout.readline()
        if not line:
            raise AssertionError("probe server did not start: " +
                                 (self.proc.stderr.read() or "")[-2000:])
        self.info = json.loads(line)
        self.port = self.info["port"]

    def attempts(self):
        self.proc.stdin.write("attempts\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def close(self):
        try:
            self.proc.stdin.write("quit\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            self.proc.kill()
        self.err = self.proc.stderr.read()


@contextlib.contextmanager
def evil_site():
    """A hostile page on ANOTHER origin (a second loopback port), served by a
    plain file server. Same browser, same machine, different origin: exactly
    what "another website open in his browser" means."""
    import functools
    import http.server
    import tempfile
    import threading

    root = Path(tempfile.mkdtemp(prefix="po-evil-"))
    (root / "evil.html").write_text(
        "<!doctype html><title>evil</title><h1>a page he did not open on purpose</h1>")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05},
                     daemon=True).start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()


def drive(port, **spec):
    spec.setdefault("name", "probe")
    spec["port"] = port
    p = subprocess.run([NODE, str(HERE / "drive.js"), json.dumps(spec)],
                       capture_output=True, text=True, timeout=300, cwd=str(HERE))
    if p.returncode != 0 and not p.stdout:
        raise AssertionError(f"drive.js failed: {p.stderr[-3000:]}")
    out = json.loads(p.stdout)
    if out.get("fatal"):
        raise AssertionError(f"drive.js: {out['fatal']}\n{p.stderr[-2000:]}")
    return out


def ui_browser(scenario, **spec):
    """One scenario, one page drive. Returns (dom, server-side facts)."""
    SHOTS.mkdir(exist_ok=True)
    srv = Server(scenario)
    try:
        dom = drive(srv.port, **spec)
        facts = srv.attempts()
    finally:
        srv.close()
    assert facts["attempts"] == [], f"a real connection was attempted: {facts['attempts']}"
    return dom, facts


if __name__ == "__main__":
    scenario = sys.argv[1]
    spec = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    dom, facts = ui_browser(scenario, **spec)
    out = os.environ.get("DUMP")
    if out:
        Path(out).write_text(json.dumps(dom, indent=1))
        print("wrote", out, "facts", facts)
    else:
        print(json.dumps(dom, indent=1)[:4000])
