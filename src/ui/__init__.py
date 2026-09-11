"""
A local web UI over the engine: `python -m src.ui`, then http://127.0.0.1:8777/.

Every run the UI makes is a CLI invocation. It builds a real `python -m
src.main` argument list, sends it through the CLI's own parser and `dispatch`,
and reads back the structured run object plus the exact terminal text. So every
refusal, UNKNOWN, WITHHELD, UNVERIFIED and provenance label reaches the screen
from the same code that prints it in the terminal. See docs/plans/ui.md.

It binds 127.0.0.1 only. Nothing here is meant to be reachable from another
machine, and nothing here stores anything between launches.
"""
