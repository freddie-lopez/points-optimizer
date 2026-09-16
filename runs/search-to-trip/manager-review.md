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

**Ship with fixes.** (Superseded — see "Re-review: 8c87d6f" at the foot of this file: **Ship**.) The feature is built as planned, the delete route is the tightest new
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

---

# Re-review: 8c87d6f

Manager, 2026-09-16. Head `8c87d6f`, four commits over my ef42d2e: **f8b9149** (the
must-fix — the ui.md §4.7 revert), **f32b2d8** (`Add as trip` demoted to a ghost `.btn`),
**2ca2693** (coder report), **8c87d6f** (the tester re-pins its F5 probe to my ruling and
fixes a J6 false positive). Everything below I ran or diffed at this head.

## Verdict

**Ship.** The must-fix is done exactly right — the Architect's sentence is back
byte-for-byte and the *page* changed to meet it rather than the bar moving to meet the
page — and C5 is green for the first time since the restyle round. Nothing is blocking.

## Must fix before ship

None. The single item from ef42d2e is closed.

## What I verified at this head

- **The ui.md revert is byte-identical where it should be.** f8b9149 is one line changed.
  The §4.7 **Tokens** paragraph is character-for-character 4f879d4's, including "the ONE
  primary action" and `Score against a fare` in the `.btn-primary` list; only its line
  number moved, because two route rows were added above it. What remains of the ui.md diff
  against 4f879d4 is seven added lines, all descriptive of what was built: the `GET
  /api/trips/{id}` shape with `deletable`, the two delete route rows, the S5 selection
  bullet, the drawer's prefill sentence, and the T2 and T5 paragraphs. Nothing in it
  re-decides anything.
- **Exactly one coral on the search surface, proven two ways.** Statically: `btn-primary`
  is constructed in exactly five places in `app.js` — `run-go` (trip detail), `search-run`
  (search), `nt-write` (new-trip form), `wallet-apply` (wallet modal) and `openConfirm`'s
  go button (the spend dialog). On the results screen that leaves `search-run` alone, in
  every state, because `search-add-trip` and `drawer-to-trip` are now both plain `.btn`.
  Dynamically: `C5[search_ok]` is **green**, and the tester's re-pinned F5 asserts
  `prim() == ["search-run"]` with the class and the `disabled` flag checked in each
  reachable state — no pick, picked with the drawer open, picked with the drawer closed.
  (The docstring says "four states"; there are three reachable ones — the drawer cannot be
  open before a pick. Harmless overcount in prose, the assertions are right.)
- **Full suite `-O`: 3892 passed, 13 skipped.** Unchanged.
- **All four probe suites**: search-to-trip **178/0**; ui-restyle **285 passed, 1 failed,
  10 skipped** — `E3` alone, the documented baseline red, with `C5[search_ok]` green;
  map-search **160/1** (`F6`, its own design limit); ui-probes **664/0**. Every claim exact.

## Judgement: is the J6 literal scan now sound?

**Sound today, and one line short of sound by construction. Keep the fix; tighten it next
round rather than narrowing it now.**

The fix is correct and, read carefully, it is mostly a *tightening*. `code_only` is applied
to both sides of the subtraction: the added-line scan (which is what gets looser — a
comment no longer counts as wording, which is right, since a comment says nothing to a
user) and the base-file set that gets subtracted from it (which gets *smaller*, so fewer
added strings are excused). No allow-list was widened — I diffed `allowed` and
`map_allowed` and both are untouched. The tester counter-checked in a worktree that a
genuinely new sentence and a lost testid both still go red. That is the right shape of fix
and the right evidence for it.

The residue is the reason I am not calling it finished. `code_only` strips comments by
line, without tracking string state, so it is only correct while no string literal in
`app.js` contains `//`, `/*` or `*/`. I checked that claim myself at HEAD and at all three
commits J6 reads (386b2fc, 56742af, 4f879d4): true everywhere, single- and double-quoted.
But it is true by luck of the current content, not by construction, and the day someone
writes a URL or a date range into a user-facing string, `code_only` will truncate that line
mid-literal, the regex will fail to match the now-unterminated string, and **a genuinely
new sentence will go unnoticed** — the probe fails open, silently.

This is the second literal-scan false positive this round: first `"2400"`, a placeholder
read as a lost testid, then a comment read as new wording. The common cause is regexes
reading JavaScript as text. Note that the team already owns the cure: the `testids()`
extractor in the same file is string-aware — it tracks quotes while bracket-matching. So:

- **Not narrowed now.** Narrowing today would mean reverting to a probe that goes red on
  every comment, which is the tax we just abolished.
- **Next round, two changes, neither urgent.** (1) Make the assumption an assertion rather
  than a docstring claim — one line inside J6 and E5b asserting that no literal in `app.js`
  contains a comment marker, so the day it stops being true the probe *says so* instead of
  going blind. (2) Then replace the line-based `code_only` in both probes with a
  string-aware pass, reusing the scanner `testids()` already has. Extract literals first,
  discard the ones that were inside comments; that is sound by construction and cannot fail
  open.

The rule from ef42d2e's Process section holds and covers this: a probe may assert absence
of an unplanned user-facing sentence. What it may not do is quietly stop being able to see
one. **A probe that can fail open is worse than a probe that fails loudly**, and the fix
for that is an assertion on its own precondition, not a narrower scope.

## Still open (unchanged, none blocking)

- **The Architect should reconcile ui.md §4.7's `.btn-primary` list.** It still names
  `Score against a fare`, which is now a ghost `.btn`, and does not name `Add as trip`,
  which is also ghost. The list is stale in the harmless direction — it over-promises
  coral rather than authorising any — and the Coder correctly flagged it instead of editing
  it, which is the flow working. One Architect line.
- **The spend-confirm dialog is the one place two corals still share a screen**
  (`search-run` behind the scrim plus `search-confirm-go`, and the same on the trip page
  with `run-go`). Pre-existing on every surface that has a confirm, unchanged by this round,
  and not a state C5 paints. Worth folding into the per-surface decision if it is ever taken up.
- The `busy`-refused delete burning its confirm; the lifted cash field on the prefilled
  form; the run page's three focus notes; the raced `GET /api/trips/{id}` answering 422
  rather than 404. All as written above.
- Tsuki's standing decisions: hard delete with no undo, and one award per search.

## Trying it on your Mac

From the repo root, with the interpreter you run the CLI with:

```bash
.venv/bin/python -m src.ui --wallet wallet.json
```

It prints `Points optimizer UI: http://127.0.0.1:8777/  (Ctrl-C to stop)` and opens your
browser. Ctrl-C stops it.

### Search → Add as trip

**This half spends real Seats.aero calls.** A search is live — there is no REPLAY for it —
and the confirm will tell you it can spend up to 25 calls before anything goes out. A
repeat of the same search within six hours is answered from the disk cache for free, so do
your clicking-around on one search rather than re-running it.

1. **Search** tab. From, To, a date (or a date range), then **Run search** → the confirm →
   **Spend up to 25 calls**.
2. **Click any award cell** in the results grid — a cell with a price in it, not one that
   says `no space`. Keyboard works too: Tab to a cell and press Space or Enter.
   The detail drawer opens, **the cell stays highlighted**, and the box under the table
   reads `Picked: SFO → MAD · 2027-01-15 · Air Canada Aeroplan · Y`.
3. Press **Esc** if you like. The drawer closes, the pick survives, and focus goes back to
   the cell. Switching to the Trips tab and back keeps it too. **Show map** or a new search
   clears it.
4. Press **Add as trip** under the table — or **Score against a fare →** inside the drawer;
   they are the same function. The new-trip form opens with:
   - a suggested name, `sfo-mad-2027-01-15` (left blank if the date is not `YYYY-MM-DD`);
   - the cabin of the cell you picked, and leg 1's From / To / Date filled;
   - **the cash field empty and already focused**, with the hint `required: the one thing a
     search cannot know`;
   - a note saying, in words, that the award price you just looked at is **not** written
     into the trip.
5. **Type the cash fare you actually found** — the real airline price for that flight, the
   number the whole comparison hangs on. Then **Preview** → **Write**.
6. The file lands in `tests/fixtures/trips/<name>.json`, written by the same builder
   `--new-trip` uses. It is byte-identical to the same trip typed by hand or built from the
   command line. It carries **no points price at all**: the miles and taxes from the search
   are never copied in, because only a LIVE or REPLAY run may put a points price in a file.
   Run the new trip LIVE or REPLAY to get one.

### Delete a trip, safely

**Nothing you did not make can be deleted from this page, and there is no undo.**

The rule is provenance, read out of the file itself: deletable means the file's own
contents say `--new-trip` or this page wrote it — `source` starting
`user_entered_via_new_trip`, the "no points prices" flag still present, no
`points_candidates` key on any leg, and every cash figure still sourced by the builder. In
practice: **anything you just built with Add as trip or + New trip and have not hand-edited.**

Refused, by name, with the reason printed under the greyed-out button:

| Trip | What happens | Why |
|---|---|---|
| `trip_a_mry_nyc`, `trip_b_europe`, `trip_c_lon_mry_surcharge` | `NOT DELETABLE — … was not built by --new-trip or this page (source: "Google Flights + Marriott.com, captured 2026-09-07 by Tsuki. Transcribed verbatim.")` | your own captures, committed as test data — git removes those, not this page |
| `trip_001`, `trip_002` | the same sentence with `(source: "")` | they have no `source` key at all, so nothing vouches for them |
| `trip_001_answer`, `trip_002_answer` | never listed; a direct request 404s | answer files are not trips |
| a trip you built here and then hand-edited | `NOT DELETABLE — … was built by --new-trip but has been edited since` | it may hold captures nobody can reproduce |
| a symlink dropped into the trips folder | refused, naming the link, never following it | this page deletes only the regular files it wrote |

To try it end to end without risking anything:

1. **+ New trip**, name it `delete-me`, one leg (say `SFO` → `LHR`, a date, cabin, any
   cash), **Preview**, **Write**. You are now on its page.
2. **Delete trip** — a warn-coloured button, never the coral one. It opens a dialog that
   names the exact file, says *there is no undo in this app*, adds that git can restore it
   **if it was committed** and that it is gone if it was not, and quotes the file's own
   description back to you.
3. **Delete tests/fixtures/trips/delete-me.json**. The list re-renders without it, the page
   lands on the Trips tab showing `Deleted tests/fixtures/trips/delete-me.json`, and that
   trip's run chips are gone.
4. Open one of Trip A/B/C to see the other side: the button is there but greyed, with the
   refusal sentence printed underneath saying exactly why.

Two things worth knowing while you play:

- **Delete is blocked while any run is in flight**, whichever trip the run is on. The
  button greys out, and if you get there anyway the server says `Nothing was deleted: a run
  is in progress and may be reading this trip. Wait for it to finish, then delete.` Wait a
  few seconds and try again.
- **Trips you build here live in `tests/fixtures/trips/`**, the same folder as the
  committed test data. That is exactly why the provenance gate exists: the page can only
  ever remove its own files from that folder, and I re-attacked that this round — traversal
  ids, symlinks pointing outside, and deletes raced against a running job — without getting
  it to touch a single byte it did not write.
