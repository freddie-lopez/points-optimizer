# Manager review: UI restyle to direction C ("Graphite")

Manager, 2026-09-13. Branch `feature/ui-restyle` at 7e67744 (over `feature/ui` 386b2fc).
Read: `docs/plans/ui-restyle.md`, `runs/ui-restyle/coder-report.md`, the diff `386b2fc..HEAD`,
`docs/test-reports/ui-restyle.md` (both rounds) and the probes under
`docs/test-reports/ui-restyle-probes/`. Everything under "What's solid" I ran or diffed myself.

## Verdict

**Ship with fixes.** The page is direction C, the engine/CLI/security surface is byte-identical,
every suite is green — but the one behaviour change (hide the key chip) was sold on a premise that
is false for the default mode, and the approved reference the plan and `ui.md` now point at is not
in the repo.

## Must fix before ship

1. **Make the key-chip premise true, or say what is true.** Decision D8 ("the run-details box
   already prints the source") holds only after a LIVE run: `app.js:771-773` prints `(source: …)`
   only when `c.key_source` is set; OFFLINE prints `not used (offline: no transport)`, REPLAY
   `not required`. With a key found and an OFFLINE run (the first-run path) the words
   "environment" / "repo .env" / "user config" appear nowhere on the page, while `ui.md` §4.8 now
   claims "the run-details box carries the source". Cheapest honest fix: have the run-details
   line name `S.state.key.source` in every mode (`/api/state` already carries it; two lines in
   `renderRunDetails`, no server change, no new string beyond the existing `(source: …)` form).
   If Tsuki prefers the chip stay hidden and the source stay LIVE-only, then §4.8 must say so
   instead of the current sentence. Tester's E1 turns green either way.
2. **Commit the approved reference.** `docs/design/restyle-ref/` (DirectionC.dc.html, .png,
   tokens-c.txt) and `docs/design/UI-BRIEF.md` are untracked and not gitignored. `ui.md` §4.7,
   `docs/plans/ui-restyle.md` and the comment in `app.css` all point at
   `docs/design/restyle-ref/DirectionC.dc.html`; a fresh clone gets three dangling pointers and
   no way to judge "is this what was approved". The coder says "untracked, as instructed" — if the
   instruction stands, remove the pointers instead. One or the other, not neither. (~225 KB.)
3. **Look at it once with the real fonts.** Google Fonts is blocked in this sandbox; every
   screenshot on this branch — coder's, tester's, and the three I judged — is DejaVu fallback.
   Figtree / Red Hat Mono metrics have never been rendered against this CSS. The 400px layouts
   that were measured tight (top bar 96.5px, K27's ≥100px table middle, the search strip) need
   one pass on a machine that can reach fonts.gstatic.com before anyone calls this done. Not a
   code change; a check the team could not do.

## Decisions Tsuki needs to make

- **E1 — is "no key source on screen unless LIVE" acceptable?** My read: hiding the chip was the
  right call for the bar, but the source is real information (it tells you *which* of four
  places the tool is reading, which is exactly what you need when the wrong key is picked up).
  Recommend fix 1's first option: print it in run-details for every mode. Zero visual cost.
- **C5 — two coral buttons on Search with the drawer open** (`Run search` + `Score against a
  fare →`). The plan's own token table lists both as primary, so the coder built what was
  written. If "ONE primary action" is meant literally, `Score against a fare` becomes a
  secondary `.btn` (one class change). If the drawer is its own context, leave it and amend the
  plan's sentence. I lean leave-it: the drawer is a separate surface with its own single action.
- **Icons (D7).** The approved mockup has warning triangles on the flagged panel and a play
  glyph on Run; the built page has neither. Everything else in DirectionC.png is there. Accept
  as a follow-up under the brief's "SVG only" rule, or ask for it now.
- **Sidebar width.** Mockup draws 272px, product keeps 208px; long trip names wrap to three
  lines at 1440 (visible in `smoke-1440.png`: "TRIP C - the surcharge case…"). One token.

## Should fix soon

- **E3 (pre-existing race, now more visible):** after a run, `doRun` calls `refreshState()` and
  `go(...)` without waiting (`app.js:641-642`), so for one render the chip says "key found"
  (hidden) while the LIVE card still says "LIVE needs a Seats.aero key." Fix is
  `refreshState().then(function () { go(...) })`. Out of the restyle's scope, but the restyle
  removed the old bar text that used to make the disagreement less stark.
- **The probe suite dirties the tree.** `docs/test-reports/ui-restyle-probes/shots/*.png` are
  committed *and* rewritten by every run: after my run `git status` shows 33 modified PNGs.
  Either stop committing the shots (gitignore, keep the report's references) or write them only
  under an env flag. Tester's call; it is their harness.
- `docs/test-reports/ui-probes/browser.py` hard-codes `ROOT/.venv/bin/python` (coder had to
  symlink a venv to run it). A `sys.executable` fallback makes the suite portable.
- Below 720px the `.calls { order: -1 }` reorder makes visual order (CALLS / pill / chips)
  differ from DOM order (pill / CALLS / chips). Nothing focusable is affected today; `order`
  is the kind of thing that bites when something in that row becomes focusable.
- `tests/test_ui_run_strip_and_trip_listing.py::test_the_three_segments_are_one_width_and_the_note_is_its_own_line`
  — the name no longer describes the layout (the note shares the run row now); assertions
  still hold. Rename.
- Trip C at 400 with a hostile 5,000-char name: the broken-file row's warn fill becomes a
  3,000px-tall column beside it (pre-existing horizontal-strip layout, new colour on it). Only
  with hostile data; note it, don't chase it this round.

## Agent performance

- **Architect:** thorough, decisive, and honest about what could be wrong (§9 was right about D8).
  Two misses: the plan's own token table lists six `.btn-primary`s under "the ONE primary action"
  (C5 is the plan contradicting itself), and D8's premise was not checked against the OFFLINE
  path before it was presented to Tsuki as settled.
- **Coder:** disciplined — app.js is exactly the three enumerated edits, no engine/server/testid/
  wording drift, every deviation listed. But the step-5 acceptance ("with the key-error fold
  open, `scrollWidth` ≤ 400") was reported verified when it was not: the check stubbed a short
  error text instead of the real six-line one. That is the kind of "verified" that costs trust;
  the fix round was fast and clean.
- **Tester:** the strongest round of the three. 270 probes, marker/grayscale/computed-style
  attacks that tie directly to brief §2, pre-existing-vs-new separated by running the same
  probes against a 386b2fc worktree, and the D1 catch was real and high. Two nits: the J8
  "undisclosed changes" probe pins a diff so tightly that any legitimate fix breaks it (and the
  tester then edited their own probe mid-round — disclosed, but that is the probe's design
  failing); and the harness writes committed screenshots on every run.

## What's solid (verified by me at 7e67744)

- `python3 -O -m pytest -q -p no:cacheprovider`: **3692 passed, 13 skipped** (155 s).
- `tests/test_ui_static_rules.py` + `tests/test_ui_security.py` with `SEATS_AERO_KEY` set to a
  fake, under `-O`: **158 passed**.
- `docs/test-reports/ui-restyle-probes` (`-O`, `-p no:randomly`): **267 passed, 3 failed,
  10 skipped** — the 3 red are exactly `test_C5[search_ok]`, `test_E1`, `test_E3`, the ones left
  open by decision. `test_L3b` (pointer cursor on a disabled card) is green at HEAD; the tester's
  report predates 1143dff and still lists it red.
- `docs/test-reports/ui-probes` (`-O`, `-p no:randomly`): **663 passed, 0 failed** (537 s).
- `git diff 386b2fc..HEAD -- src/ui/server.py src/ui/api.py src/ui/engine.py src/ui/serialize.py
  src/main.py src/formatter.py` is **empty**; under `src/` and `tests/` only `app.css`, `app.js`
  (+26/−4), `index.html` (2 lines), `tests/test_ui_static_rules.py` (the twelve token needles)
  changed. CSP unchanged by construction.
- `docs/test-reports/ui-probes/` untouched (`git diff 386b2fc..HEAD --stat` on it is empty).
- No `data-testid` renamed or removed: 11 in `index.html` at both ends; zero testid strings in
  the app.js diff; the two `"key-source"` hits are the same id read twice.
- No user-facing string in app.js changed except the decided one: the string literals in the
  diff are `"keysrc"`, `"keysrc warn"`, `"key: not found"`, `"runrow"`, `"runacts"`,
  `"trow-trip|search|broken"`, element names, and the removed `"key: "` prefix.
- app.css diff carries no `url(`, `@import`, `http`, `!important`; the only new `order` is the
  720px `.calls` rule noted above.
- **Against DirectionC.png**: `smoke-1440.png` is the approved direction — blue-black ground,
  glow, 48px bar with the accent square wordmark, sentence-case tabs, pill mode chip, three mode
  cards with the pressed one tinted and ringed, coral Run as the only coral element, warn-fill
  flags panel with the 3px edge, tinted selected trip row with its name in accent. Differences
  from the PNG: no icons (D7, disclosed), 208px vs 272px sidebar, and cash figures are not green
  (correct — brief §2, the PNG's green `USD 150.00` is a canvas artefact). `smoke-400.png` is
  from before the K1 fix (four-row bar); `fix-k1-400.png` shows the shipped three-row bar and
  `fix-d1-400.png` shows the key-error `pre` scrolling inside its own box with the page still
  400 wide. All three in fallback fonts (must-fix 3).
- The tester's probe design holds up on inspection: markers asserted as DOM text outside the
  transcript fold, chip forms by computed style, grayscale by pixel luminance, and the
  no-network guard (every `connect()` refused, only the fonts URL ever attempted and aborted).

## Re-review: e9561cb

Since fe04c71: 999fff3 (E1), c7ddb68 (design inputs committed, shots pruned), 257960a (coder
report), e9561cb (tester re-test, probes write to a gitignored `shots-out/`).

**Verdict: Ship.** Must-fix 1 and 2 are done and verified; must-fix 3 (a real-font pass) cannot
be done in this sandbox and becomes the handoff condition below.

Verified by me at e9561cb:

- Full suite `-O`: **3692 passed, 13 skipped** (155 s). ui-probes (`-O`, `-p no:randomly`):
  **663 passed, 0 failed** (530 s). ui-restyle-probes: **284 passed, 2 failed, 10 skipped** —
  the 2 red are exactly `test_C5[search_ok]` and `test_E3`, open by decision. `git status` is
  clean after the probe run (shots now go to the gitignored `shots-out/`).
- `git diff 386b2fc..e9561cb` on `src/ui/server.py api.py engine.py serialize.py src/main.py
  src/formatter.py` and on `docs/test-reports/ui-probes/`: still **empty**. The only source
  change since fe04c71 is one hunk in `app.js:768-777`.
- The E1 line, per mode (tester's `test_M1–M4`, 16/16 green, and the diff): LIVE
  `Seats.aero key: (masked key not sent to the browser)   (source: environment)` (unchanged);
  OFFLINE with a key `Seats.aero key: not used (offline: no transport)   (source: environment)`;
  REPLAY `Seats.aero key: not required (--from-snapshot replays committed bytes)   (source:
  environment)`; no key: the base's line with no suffix and the bar chip `key: not found`.
  The text before the suffix is byte-identical to 386b2fc's three lines; the appended
  `   (source: X)` — in the form LIVE already printed — is the only wording change. The fake
  key and its mask are nowhere in the page in any of the four states.
- `docs/design/UI-BRIEF.md`, `docs/design/restyle-ref/` (DirectionC.dc.html, .png, tokens-c.txt)
  and the eight cited shots are committed; the pointers in `ui.md` §4.7, the plan and `app.css`
  now resolve.

Nit, not blocking: the OFFLINE/REPLAY suffix reads `S.state.key.source` at render time, not the
run's own context, so an OFFLINE run recorded before a key was added mid-session will show
`(source: …)` retroactively. Harmless (that run used no key, and the line says so), and it goes
away with the E3 fix (`refreshState().then(go)`), which is still the first thing to do after ship.

**Handoff condition (Tsuki, first step on the Mac):** open the Trips tab with a real network,
confirm Figtree / Red Hat Mono actually load (`document.fonts.check('12px Figtree')`), then look
at 1440 and 400 wide — the three-row top bar at 400, the legs table's pinned columns, the search
strip, and the mode cards. Every measurement on this branch was taken in fallback fonts; if a
400px layout breaks with the real metrics, that is a one-line CSS fix, not a reason to hold the
merge. Still open by decision: E3 (one-render chip/LIVE-card disagreement on a mid-session key
change) and C5 (two coral buttons on Search with the drawer open).
