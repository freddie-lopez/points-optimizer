# Manager review: search → new trip, and a delete button

Manager, 2026-09-16. Branch `feature/search-to-trip` at 6c89c5a (over `feature/map-search`
4f879d4). Read: `docs/plans/search-to-trip.md` (D1-D17, R1-R9, the string list),
`runs/search-to-trip/coder-report.md` (round 1, fix round 1, fix round 2 + the pin cleanup,
fix round 3), `docs/test-reports/search-to-trip.md` (round 1, "Re-test: ecab878",
"Final: 23eba49", "Final: 3232e95"), the diff `4f879d4..HEAD` in full for `src/ui/api.py`,
`src/ui/engine.py`, the `app.js` hunks and the pin-cleanup commit 9dea889. Everything under
"What's solid" I ran, diffed or attacked myself; the delete security claims I re-attacked
against a live server rather than reading the tester's word for them.

## Verdict

**Ship with fixes.** The feature is built as planned, the delete route is the tightest new
surface this project has added — I could not get it to touch anything outside its own
directory — and every count claimed by both agents is real. One thing must not ship as it
stands: the coder edited the design plan to make its own class choice legal, and the
sentence it wrote there is false about the page as built.

## Must fix before ship

1. **Revert the coder's edit to `docs/plans/ui.md` §4.7** (the token paragraph: "the ONE
   primary action" → "one primary per surface", and `Score against a fare` → `Add as trip`
   in the `.btn-primary` list). Two separate reasons, either sufficient. First, the tester
   is right: a coder's edit to a plan document during a fix round is not a decision, and
   the team's rule names the plan as the authority. Second, and worse, the sentence is
   **untrue of the page it was written to describe**: I ran C5 and read the failure — the
   search surface paints two coral buttons at once, `search-run` "Run search" and
   `search-add-trip` "Add as trip". The doc now certifies a rule the code breaks.
   This is a two-minute fix that matters more than its size, because search-to-trip probe
   **E5b** reads `docs/plans/*.md` as the authority for what wording `app.js` is allowed to
   carry. A plan the coder can edit is a self-certifying wording guard. The §4.5 route rows
   and the §4.8 S5/T2/T5 additions in the same file are fine and stay: those describe what
   was built, they do not re-decide anything.

That is the only blocker. Everything else below is a choice or a soon.

## Decisions Tsuki needs to make

1. **C5 — which button on the search screen is coral.** My ruling, in two parts, because
   the coder and the tester were answering different questions.

   *Is the page right now?* Better, not right. Demoting `drawer-to-trip` to a ghost `.btn`
   was the tester's recommendation and is a genuine improvement — the drawer's button is
   now the ghost twin of a coral button that does the same thing, which is the honest
   shape. But the search surface still carries two coral buttons, which satisfies neither
   the restyle's original sentence nor the replacement sentence the coder wrote. It is the
   baseline count (before this round it was `Run search` + `Score against a fare`), so this
   round made nothing worse — it just did not make it right.

   *Should C5 be re-pinned?* **No.** It stays a documented red until you decide, and then
   it goes green because the page changed, not because the bar moved. Re-pinning C5 to
   `<= 2` would be moving a bar to meet the code — the exact failure the pin rule below
   exists to prevent — and moving it on the authority of a doc line the coder wrote. The
   tester's refusal was correct and I am upholding it.

   Your options: **(a)** `search-add-trip` becomes `.btn` (one class name). C5 goes green
   for the first time; the search surface has exactly one primary, `Run search`, which is
   the action that spends calls and the one that should carry the colour. `Add as trip`
   sits in its own named box with a note that says what is picked, so it does not need
   colour to be found. **(b)** Keep two coral, and have the Architect — not the Coder —
   write the "one primary per surface" rule into ui.md deliberately, with the search screen
   named as the surface that carries two, and re-pin C5 to count per surface with that
   sentence quoted. I recommend (a). What is not an option is leaving a red probe that
   everyone has learned to step over.

2. **Ratify the pin rule** in the Process section below. It is a process change that
   governs every future round, and it was agreed with you in conversation rather than
   written down anywhere the agents read.

3. **Hard delete, no undo** (plan D8, §9.2) is now live. A trip this page or `--new-trip`
   built is unlinked for real; if it was never committed, it is gone. That is what was
   planned and it is well-guarded, but it is the first destructive action in the app and
   you should say out loud that you want it. Reversal is one line (`data/deleted-trips/`
   instead of `os.unlink`) plus a README sentence.

4. **One award per search** (plan D1, §9.1). "Click on the flights" was read as a single
   selection, and multi-select is refused on purpose. If you meant "tick three awards, get
   a three-leg trip", say so — it is half a day and no server change.

## Should fix soon

- **A delete refused as `busy` burns its confirm.** `redeem_confirm` runs before the lock
  is taken, so the second press answers R6 ("carried none or one that was already used")
  rather than R5. I hit this myself. Not reachable through the page today — the dialog
  closes on Go — but it is the wrong sentence for a curl user and a trap for any future
  retry button. Move the redeem inside the lock, or re-issue on `busy`.
- **The cash hint lifts the cash input** ~20px above its neighbours on the prefilled form
  only (`.form-row` bottom-aligns; the hint makes that field taller). The coder disclosed
  it; the tester saw it. Visible, cosmetic, in shot `c10-prefilled-1440.png`.
- **The run page's focus story is now three notes long** — F3b (spend-dialog Go), F3c (a
  refused Go), and F6 was the same family before it was fixed. All correctly ruled out of a
  delete round's scope, all still open. One round on the run page closes all three.
- **A raced `GET /api/trips/{id}` answers 422 `cannot_load` with the OS sentence**
  (`FileNotFoundError: [Errno 2] …`) rather than 404. Not a delete route; pre-existing;
  now easier to reach because the page can delete files.
- **`test_st_f_retest.py::test_F4a`'s trap is worth a line in the harness notes**, not just
  a report: `wait_for_function` reads a returned Promise as truthy and returns on the first
  poll. The tester found it the hard way and the fix (a synchronous three-frame counter) is
  the pattern the next Playwright round should copy.

## Agent performance

- **Architect** — the reason this round was cheap. D7's "deletability is decidable from the
  file alone", D10's POST-routes-so-`server.py`-stays-byte-identical, and §6 step 7's
  advance enumeration of the pins the round would invalidate turned what is normally a hunt
  into a copy job. One error, in §7: `grep -c points_candidates == 0` is impossible because
  the builder's own `LIVE_ONLY_FLAG` sentence contains the word — the coder caught it and
  said so.
- **Coder** — built D1-D17 as written and disclosed ten deviations and seven known gaps
  before anyone asked; every one the tester checked held up, including the two that needed
  judgement (`link_display` naming the link rather than its target, and malformed shapes
  falling to R3 rather than passing vacuously). Fixes were minimal and in the right place:
  F5 and F6 were each one line with the reasoning for choosing the smaller of two offered
  shapes. Two process slips, both about staying inside its own lane: editing the design
  plan to justify a class choice (must-fix 1), and not flagging that the pin cleanup it was
  authorised to do removed the only check on the coder editing the tester's files.
- **Tester** — the strongest work on this branch. 36 traversal ids × 2 routes, hard links,
  the TTL boundary at 300.001 s, a UI-built file committed in a throwaway git repo, thirty
  hand-edits classified. It diagnosed its own flake honestly instead of blaming the fix,
  and it counter-checks — restoring the pre-fix `app.js` in a scratch worktree to prove six
  of seven probes were measuring the fix and not themselves is the habit I want every round
  to have. It refused the C5 bar-raise on exactly the right grounds. The one gap: its
  cleanup audit tested each converted pin against a defect that pin could catch, but never
  asked what the *deleted* A7 half was for — see Process.

## What's solid (verified by me, not taken on report)

- **Full suite: 3892 passed, 13 skipped** under `-O`, and **3892 passed, 13 skipped**
  without it. Both claims exact.
- **All four probe suites, by test id**: ui-probes **664 passed, 0 failed**; ui-restyle
  **284 passed, 2 failed, 10 skipped** — `C5[search_ok]` and `E3`, both documented;
  map-search **160 passed, 1 failed** — `F6`, its own design limit; search-to-trip
  **178 passed, 0 failed**. Every count matches both reports.
- **Byte-identity**: `src/ui/server.py`, `src/ui/serialize.py`, `src/main.py`,
  `src/formatter.py`, `src/trip_builder.py`, `src/trip_loader.py`, `src/ui/static/map.js`,
  `src/ui/static/index.html`, everything under `tests/fixtures/` and every golden are
  byte-identical to 4f879d4 (`git diff` empty for all of them). The **CSP is unchanged
  because `server.py` is** — the header is built there and the file did not move.
- **No testid renamed or dropped**: no `data-testid` attribute selector present in
  4f879d4's `app.js` is missing from HEAD's, and `index.html` is byte-identical, so its
  testids cannot have moved. Three new attribute selectors, all this round's.
- **No `innerHTML`** anywhere in `app.js` (nor `outerHTML`, `insertAdjacentHTML`,
  `document.write`); `.style.` count still exactly 2; no `|| 0`, no `?? 0`.
- **Delete security, re-attacked against a live server on a tmp copy of the trips
  directory** (the three the brief asked for, plus the tree walk):
  - *Traversal*: eight ids — `../trip_b_europe`, `..%2f..%2fREADME`,
    `%2e%2e%2ftrip_b_europe`, `..\trip_b_europe`, `trip_b_europe%00`, `trip_b_europe.json`,
    `../../src/config`, `.` — against **both** routes: 404 `not_found` every time, sixteen
    for sixteen. No file in the tmp tree changed in bytes, mode or mtime, and
    `tests/fixtures/trips/` in the repo is identical before and after.
  - *Symlink*: a link inside the trips directory pointing at a genuinely **deletable**
    UI-built file outside it — the case where the provenance rule alone would let it
    through. Preflight 409, and the delete refused 409 even when I handed it a valid
    bytes-bound confirm issued from the engine itself. The refusal names the **link's own
    path**, never the target. Link intact, target intact and byte-identical, an unrelated
    file outside the directory untouched.
  - *Delete while a run holds the lock*: holding the engine's real `run_slot()` — the same
    lock a LIVE run takes — preflight and delete both answer 409 with R5 verbatim and the
    file is still there. Released, fresh confirm, delete returns `Deleted <file>` and the
    **only** change in the entire tree is that one file gone.
- **The engine reads the raw JSON and joins nothing from the URL**: `trip_path` is a
  lookup in the current listing, the digest binds `kind` + `trip_id` + the file's sha256,
  and there is exactly one `os.unlink`, under the lock, after a second provenance check, a
  symlink check and a resolve-inside-the-directory check.

## Process: what a pin may and may not assert, from now on

The cleanup was right and I am keeping it. Those pins cost a re-pin round on every change
and, across four rounds, never caught a defect — the tester proved the point the honest way
by planting a real defect in `engine.py` (one word added to the R2 refusal) and showing the
retired byte pins stayed green while six behaviour probes went red naming the sentence. The
byte pins only ever owned the bytes; the behaviour probes own the meaning.

**A pin may assert:**
- behaviour — what a route answers, what a panel says, a refusal sentence verbatim, what
  survives a re-render, where focus lands;
- absence — a banned construct, a lost `data-testid`, a golden or fixture that moved, a
  user-facing sentence no plan writes;
- byte-identity **only** of files the round's own plan lists as untouched (`server.py`,
  `serialize.py`, the goldens, `tests/fixtures/`).

**A pin may not assert:** a line count; a diff-hunk count; byte-identity of a file the
round is expected to edit (`api.py`, `engine.py`, `app.js`); or a commit hash of anyone's
future work. **If an assertion's only failure mode is "someone did some work", it is not a
pin — it is a tax.**

Three consequences, all accepted, all recorded so the next round inherits them and not the
argument:

1. **J6's one genuine loss stands.** A literal that already existed in `app.js` at the
   left side of the diff range, re-emitted on an added line, is no longer flagged. The
   tester is right that this is placement, not new wording, that the rule J6 stands for —
   the page may not start saying something no plan wrote — is intact, and that restoring
   the old form re-introduces exactly the false positive we removed. It is narrower than it
   sounds: only literals present at the range's base are exempt, so this round's and the
   map round's wording is still checked. Not restored. Recorded.
2. **A7's deleted half is the real loss, and the audit did not name it.** "The other probe
   trees are unchanged since `<commit>`" was the only thing in the repository that noticed
   a coder editing the tester's probe files — and it was removed in the same commit in
   which a coder edited four of them. Everything else the audit examined was replaced by a
   stronger assertion; this one was simply dropped. It was still right to drop it (it also
   pinned the tester's own future commits, which is unworkable), but the replacement is a
   rule rather than a probe: **probe files under `docs/test-reports/` belong to the Tester.
   The Coder does not edit them without my explicit authorisation; when authorised, the
   conversion goes in its own commit and the Tester audits it in a scratch worktree, one
   change at a time, before anyone believes it.** That is precisely what happened here, so
   the rule is already the practice — it just was not written down.
3. **The matching rule for plans, which now has teeth.** `docs/plans/` belongs to the
   Architect. **A coder does not edit a plan to make a choice legal.** This stopped being a
   matter of etiquette the moment E5b started reading `docs/plans/*.md` to decide what the
   page is allowed to say: a coder who can edit a plan can authorise the page to say
   anything. See must-fix 1.

One thing to watch rather than fix: nothing now bounds the *size* of an `app.js` change.
The line count and both hunk counts are gone, which is correct — but it means a large
unannounced restructuring of that file would pass every probe. The coder report's
enumeration of new strings, CSS rules and functions is the only remaining account of what
changed, and E5's "the report's bullet list equals the diff" check is gone too. That check
was bookkeeping on a document and I do not want it back, but it means the coder report is
now trusted rather than verified. Keep the reports as good as this one has been.
