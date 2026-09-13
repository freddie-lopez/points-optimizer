# Manager review: the local web UI (feature 2 of 3)

Branch `feature/ui` at `543e75c`, based on `feature/operating-airline` (3c104b3 + the
feature-1 sign-off f7b3ec2). I did not plan, build or test this. Everything below
that is stated as fact I ran myself at `543e75c`; anything I could not check is
called unverified.

**Verdict**: **Ship.** It is a real, usable tool, the engine refactor moved CLI output
only where the plan said it would, the security gates hold against my own probes, and
the Tester's whole suite is green at this commit — which nobody had confirmed. One
line of README stands between it and honest.

---

## Must fix before ship

1. **The README's threat-model paragraph does not mention the one outbound request the
   app makes.** `src/ui/static/index.html:9` loads `fonts.googleapis.com`, and the CSP
   allows `fonts.gstatic.com`. So every page load tells Google his IP and when he is
   using this. Nothing in the README is false — it says the app binds 127.0.0.1, the
   key never leaves the server, local malware is out of scope — and a reader finishes
   that paragraph believing nothing leaves the machine. In a project whose thesis is
   that user-facing text must never read cleaner than the truth, that omission is out
   of character; `tests/test_readme_local_ui_is_accurate.py` checks the claims that are
   there, not the one that is missing. **Coder.** One sentence in the threat-model
   paragraph ("the page loads two webfonts from Google; everything else is local"), or
   self-host the two font files and drop both hosts from the CSP. It does not block him
   playing with the app today.

There are no unfixed Critical, High or Medium Tester findings. R6-1 and R6-2 are
closed at this commit — I ran the probes.

---

## Decisions Tsuki needs to make

1. **A third change to CLI arithmetic landed in round 6, outside the round's stated
   contract.** The plan said F-1 and F-3 were "the only intended changes to CLI output"
   and "no change to scoring". `12076b1` then changed `trip_totals` to sum
   **cent-rounded parts** so the printed rows always add up to the printed total. It is
   a good fix (four legs at $10.005 printed $10.01 each and totalled $40.02), it moves
   no committed golden, and the 69-invocation parity set is green over it — I checked.
   But it is scoring arithmetic changed in a fix round with no separate review.
   *Options:* (a) accept it as shipped; (b) pull it out and give it its own round.
   **My recommendation: (a).** It is tested, it makes the table self-consistent, and
   rounding the sum instead would leave the rows visibly disagreeing with it. Just know
   it happened.

2. **The FX table expires on 2026-10-08, and the UI has no way to refresh it.**
   `config.FX_RATES_AS_OF = "2026-09-08"`, `FX_STALE_AFTER_DAYS = 30`. In 26 days every
   run touching EUR/GBP/CAD — Trip B, Trip C, and any live award with foreign taxes —
   starts printing STALE RATE, and `--fx` is deliberately not offered in the UI (D11:
   it mutates global `config` across runs). The only remedy will be editing
   `src/config.py` and restarting. Nobody flagged this.
   *Options:* (a) leave it — the STALE RATE marker is honest and he can edit the file;
   (b) add a per-run FX entry to the UI; (c) let the app read a rates file.
   **My recommendation: (a) now, (c) alongside feature 3.** It is a dated problem, not
   an urgent one.

3. **T2, reopened by what it turns out to cost.** Trips built in the UI are written to
   `tests/fixtures/trips/`, which his test suite executes on every `pytest` run
   (`test_no_changelog_in_user_output.py` asserts exit 0 on every loadable trip there).
   I created one through the API and ran the suite: it passes. But it means a trip he
   builds while playing becomes part of his test corpus, and a trip that ever exits
   non-zero turns his whole suite red. `git status` also fills with untracked fixtures.
   *Options:* keep it (so `--trip-fixture NAME.json` finds them), or a gitignored
   `data/trips/` the UI also reads. **Recommendation: keep**, but know the coupling
   exists; revisit if it ever bites.

4. **T3, same shape.** LIVE runs archive every response into
   `tests/fixtures/seats_aero/live_trip_b/`, which git **tracks** (`.gitignore` ignores
   `data/cache/`, not the snapshot corpus). That is what makes REPLAY work, and it is
   the CLI default. It also means playing with the UI writes tracked files into his
   repo indefinitely. **Recommendation: keep**, and prune the corpus when it annoys him.

5. T1 (search does not use the disk cache) and T4 (`0.00% (none)`, exit 0) are unchanged
   from the plan and I agree with both defaults.

---

## Is it actually usable, and what he hits first

**Yes. It is pleasant and it is not a wall of caveats.** I drove it in Chromium at
1440px and 400px: OFFLINE Trip B, a LIVE Trip B against the stub, a search with a
typo and then a real one, both drawers, the keyboard path. The legs table is readable,
the headline block puts the range, its label ("CARRIER SURCHARGE UNKNOWN ON B3"), the
low/high rows and the provenance chip in one place, and the leg drawer holds everything
the CLI knows about that leg without you having to read a transcript. The search view
is clean and says plainly why it has no verdict. At 400px the drawer is a proper
full-screen sheet and reads well. The caveats are present everywhere they should be,
but they are laid out, not piled up.

**The first thing he will hit:** if there is no `wallet.json` in the repo root, the top
bar says NO WALLET, the banner explains it — and the RUN button is still live, so his
first click comes back `EXIT 2 · WALLET ERROR`. That is exact CLI parity and it is
defensible, but it is the one place where parity costs a confusing first click. Tell
him: start it with `--wallet wallet.json`, or open the wallet panel before pressing Run.

**Second:** if he is offline the two Google webfonts do not load and the page falls back
to system fonts. Nothing breaks.

---

## The LIVE column is synthetic. What to do first on his Mac

Every LIVE path in the Coder's and Tester's reports is a stub. The trips parser has
still never met a real Seats.aero response. In order:

1. `.venv/bin/python -m pytest -q -p no:cacheprovider` — expect **3,568 passed, 13
   skipped**. That is what I get here, and the same inside an unpacked export.
2. `.venv/bin/python -m src.ui --wallet wallet.json`. Play OFFLINE with Trip B first:
   every number there is real code on real committed data, and the
   `2.04% – 11.03% (badge)` range is the tool working correctly, not a bug.
3. **Then one LIVE trip run**, on a route he can check by hand. Four things to look at:
   - the CALLS counter against the stated maximum. The confirm says "up to 110"; the
     real number should be 4–5. If it is not, the pagination shape is different from
     anything we have seen.
   - the coverage line under the result: *"single page: the response carried NO count /
     hasMore / cursor field"*. If the real endpoint paginates by another mechanism,
     that line is the only thing that will tell him.
   - the leg drawer's **OPERATING AIRLINE** section. If the trips parser's field names
     are wrong, it will say NOT KNOWN or NOT RECORDED. It is built so the failure mode
     is silence, never a wrong carrier — that is the right way round, and it means a
     blank there is data, not a bug.
   - the per-itinerary taxes line, which prints both readings verbatim ("GBP 450.00 if
     cents, GBP 45,000 if whole units").
4. **The one measurement that matters more than metal**, from the handoff: a BA- or
   VS-program award on BA metal, with the tool's taxes figure compared against the
   airline's own booking page. That settles whether Seats.aero's `TotalTaxes` already
   includes YQ — and that decides how many live awards are scoreable at all.
5. Paste back: the run transcript (COPY under "CLI OUTPUT"), and a raw trips response
   captured with `python -m src.trips_tools capture`, key redacted.

**What not to trust until then:** any operating-airline line (the
`[trips parser UNVERIFIED against a real Seats.aero response]` tag is the truth, not
decoration); the per-itinerary taxes figure; the "single page" coverage claim on a busy
route; and the 110-call number as a *prediction* — it is a ceiling, not an estimate.
REPLAY does nothing until a LIVE run has written a manifest. Also know that the first
LIVE run starts filling `tests/fixtures/seats_aero/live_trip_b/` with git-tracked files.

---

## Should fix soon

1. **The page and its own transcript disagree about money.** The legs table renders the
   full cell text (`$395.00`); the "CLI OUTPUT" block below it is rich at width 190 and
   prints `$395.…`, `50,…`, `mode…`, `WITHHELD (surch unknow…`. I confirmed the
   transcript is byte-identical to golden G1, so this is pre-existing CLI behaviour —
   but the transcript is the channel he pastes back to the team, and it is the only
   place in the page where a dollar figure is unreadable. Filed twice already as the
   "Score-column truncation"; this is the argument for actually scheduling it.
2. **M-6's column floors are conditional on a verdict label being present**
   (`src/formatter.py:645-668`). The Coder flagged it himself: it is the round's
   no-output-may-move rule encoded as layout logic, and it will confuse the next person
   to touch that table. Drop the condition in the same round that fixes (1).
3. **No cancel, and a hung run holds the app.** `seats_client` uses a 15s per-request
   timeout; the worst case for a LIVE Trip B is 110 requests, i.e. ~27 minutes with no
   way to stop it but Ctrl-C on the terminal — which kills the server mid-run. Fine in
   practice (real runs are seconds), but the first slow day will be unpleasant.
4. **The `[modeled]` word is still swallowed by rich in the CLI line**, and F-2
   (`0.00% (none)`, exit 0) is still there by decision T4. Both known, both agreed.
5. **Coverage gaps the Tester named honestly and nobody closed:** no real browser but
   headless Chromium 1194; no server run across a real midnight or against a genuinely
   six-hour-old cache; no long soak; no interleaved preflight/wallet-edit/create races.
   None is worth a round on its own; all are worth knowing.

---

## The refusal boundary: sound, or five rounds of patches?

**Sound.** I went looking for the seams and did not find them. Three things make it a
property rather than a list:

* `config.scoreable_amount` / `scoreable_count` are the only entry for an outside
  number, and they raise a **ValueError** subclass, so a bad number takes the path a
  bad date already takes;
* the three conversions are **total** — they refuse instead of raising — so any product
  of accepted numbers is a refusal without anyone having enumerated the
  multiplications. That is the part that actually closes the family;
* `dispatch` catches `ArithmeticError` and `RecursionError` alongside `ValueError`, and
  an `ast` test refuses any `int()`/`float()` call in `trip_loader.py`, so the next
  parser cannot reintroduce R4-1.

**Can a legal fixture he already has now be refused?** I tried. A `0.00` mandatory fee,
a `$0.01` fare, a fare written as the string `"395.00"`, points written as `50000.0` —
all load and score unchanged. The only refusals were semantically right (a `$0` fare, a
leg with 0 travellers), and each is one clear line naming the file, the leg and the
field. The boundary applies to **files and the wallet**, not to Seats.aero responses —
a live row with `MileageCost` of 0 or a negative becomes `none: UNREADABLE`, not a
refused run. That is the correct split.

**The one trade to state plainly:** a fixture carrying a zero/negative fare or a
zero/negative points candidate is now refused **as a whole file**, where before the bad
field was silently dropped. Dropping it is what produced "none — not a partner", so the
trade is right — but a hand-edited file that used to score will now refuse.

---

## Size and maintainability

`git diff --stat feature/operating-airline..HEAD`: **23,296 insertions across 106
files.** Roughly 14,400 of that is tests and probes, 4,200 the UI, 2,300 docs (plan,
mockup, reports), and about 1,500 lines of real engine change (`formatter.py` +555,
`config.py` +303, `main.py` +281, `optimizer.py` +129, `trip_loader.py` +149). Nothing
in it is unjustified.

**The CLI is a bit harder to change, in a specific and contained way.** Adding or
moving a column now means the builder in `formatter.py`, the 728-line
`src/ui/serialize.py`, and `app.js`. What makes that acceptable is that drift is
caught: 14 byte-identical goldens, 69 CLI invocations diffed against an extracted
pre-UI tree, and DOM-vs-JSON parity probes across every golden scenario. Keep
`test_ui_g_cli_parity.py` — it is the widest net anyone has built on this project.

---

## Agent performance

**Architect** — did the job well. 609 lines, and the two things that matter most were
decided before a line was written: that a single-route search has no cash price and
therefore cannot carry a verdict, and that F-1 (a partnership claim on a leg whose API
call failed) was a pre-existing defect the UI would otherwise have amplified. Four
decisions were put to Tsuki with defaults, and the three most-likely-wrong calls were
named. *Improve:* the plan declared a contract ("F-1 and F-3 are the only intended CLI
output changes"; "no change to scoring") and then the round shipped a third arithmetic
change and a family of new load-time refusals. State the conditions under which a
contract may be reopened, so a fix round cannot widen scope silently.

**Coder** — strong, and unusually honest about himself. He disclosed the deviations
that made him look worse: the over-scoped probe he could not keep green and handed to
the manager, the conditional column floors he called a wart in his own comment, and two
items he filed rather than fixed. Round 4's reply to "can one guard replace the next
hole?" — that the property needs three redundant mechanisms, not one guard — is the
best piece of engineering reasoning in the whole round. *Improve:* the M-6 defect was
self-inflicted. He changed a string in a fixed-width table (`PAY CASH (no path)` →
`PAY CASH (never priced)`, five characters longer) without measuring the table, and a
`$2,400.00` fare printed as `$2,400.…`. Measure the layout when you change a string
inside it.

**Tester** — the best of the three, by some distance. 546 executed probes, 69 CLI
invocations diffed against a `git archive` of the pre-UI tree, a real browser driving
the real page, a hostile page on another origin, and five escalating rounds on the
number line that each found a genuinely new hole. Every round ends with an explicit
"what is merely unbroken so far" list, and the closing sentence — *the manager should
sign off knowing the LIVE column of this report is synthetic from top to bottom* — is
exactly what I needed and exactly what a weaker tester would have left out. *Improve:*
he named "a leg id that collides with a `data-testid` selector" and "fixture shape
rather than fixture numbers" as round-4 targets and never came back to them. I ran both
in ten minutes (they hold: a leg id of `B1"] , [data-testid^="leg-row-` renders, opens
the right drawer, and returns focus correctly; duplicate leg ids are harmless). Close
your own named list, or say why you dropped it.

**Process, not an agent:** the round closed with two Tester findings open and a
coordinator's note saying the probe suite was "for the Manager to confirm". That is one
step too far down the chain. A round should not be handed up with its own test suite
unrun. I ran it: 546/546.

---

## What's solid — verified by me at `543e75c`

* Full suite **3,568 passed / 13 skipped**; identical under `-O` (1 pytest warning).
* Unpacked `git archive HEAD` into a clean directory and ran it with an interpreter
  from outside the tree: **3,564 passed / 17 skipped / 0 failed**, 302 files, no
  symlinks, no `.venv`. The delivery is portable.
* The Tester's full probe suite at this commit: **546 passed, 0 red** (12m08s). R6-1
  and R6-2 are closed, and the 69-invocation CLI parity set is green *after* the
  round-6 cents change — the one thing nobody had re-run.
* **Goldens moved only where claimed.** Since the step-1 recording (`bdf2af4`, before
  any `src/` change) only G1, G3, G5, G7 and G8 changed, and G14 was added. The diffs
  are exactly the F-1 path/verdict cells, the F-3 sentence, and one column-width reflow
  in G7 in which no value changes. `pre_f1_f3/*.txt` are byte-identical to the step-1
  recordings.
* **The normalizer cannot hide a real difference.** It is three regexes: an instant
  immediately after "fetched"/"captured", a snapshot filename stamp, and "N minutes
  ago". It masks nothing at all in G1/G2/G3/G7/G8/G12. A money figure, a percentage, a
  verdict, a date or a day count cannot pass through it.
* **Security, probed by hand against a running server, not via their tests.**
  Host `evil.com`, `127.0.0.1.evil.com:<port>`, and a Host without the port → 421.
  `/api/state` with no token and with the token in a query string → 403; with the
  header → 200. POST with a foreign Origin and with no Origin → 403. OPTIONS → 405.
  `/static/../src/config.py`, `%2e%2e`, `..%2f..%2f..%2fetc%2fpasswd` → 404. CSP,
  nosniff, no-referrer, X-Frame-Options DENY, CORP same-origin, Cache-Control no-store
  all present. It listens on loopback only: a connection to this host's own routable
  address on the same port is refused.
* **Confirm tokens hold.** A LIVE run with no `confirm_id` → 409 `confirm_required`. A
  confirm redeemed with changed options → 409 `confirm_stale`, and the id is burned.
  Reusing it → 409. The call counter did not move across any of it.
* **Hostile Seats.aero data cannot execute.** With `<img src=x onerror=…>`,
  `</pre><script>`, U+202E and a 5,000-character carrier list coming back from the
  stub, through a real LIVE run in Chromium: `window.__pwned` undefined, one `<script>`
  in the document (app.js), zero `<img>`, no page error, and no page-level horizontal
  scroll at either 1440px or 400px.
* **The boundary does not over-refuse.** See above.
* **Nothing spends without being asked.** Preflights, `/api/state`, page loads and
  refused runs all left the counter where they found it.

---

# Sign-off re-review (b9c6f03)

The must-fix is **closed**. Committed by the coordinator, since the Coder's session
was stopped.

**The wording is honest and complete.** The new paragraph is headed *"One request does
leave your machine"*, names both hosts, says what they reveal (IP address and when the
app was opened), says what is never sent anywhere but Seats.aero (no trip, no balance,
no award, no key), and says that blocking them or running with no network falls back to
system fonts with no other change. I checked all three claims rather than reading them:
`BASE_URL = "https://seats.aero/partnerapi"` is the only outbound host anywhere in
`src/` — `requests.get` appears twice, both in `seats_client.py`, both against it — and
I had already driven the whole app in Chromium with every non-loopback request aborted,
where it works on the fallback stack with nothing else changed. It does not overclaim:
it says "and it is not the API key", which is exactly right, and it does not pretend the
request is harmless.

**The new test really guards it.** I ran three negative controls at this commit:

| Control | Result |
|---|---|
| delete the paragraph from the README | **2 tests fail**, naming both hosts |
| add `https://telemetry.example.com` to the CSP in `server.py` | **fails**, naming it |
| add `https://beacon.example.net` to `app.js` | **fails**, naming it |
| add the same host over plain `http://` | **passes** — see below |

The guard is structurally right: a new host has to be in the CSP to be reachable at all
(`default-src 'none'`), and the CSP is one of the things it scans, so it fires on the
change that would have to happen. The section it compares against is the Local UI
section alone, so naming a host elsewhere in the README would not satisfy it.

**Verification at b9c6f03**: full suite **3,570 passed / 13 skipped**, identical under
`-O`. The commit touches only `README.md` and that one test file; the only two probe
files that mention the README use it incidentally (a traversal path string and a
`[:0]` read), and I re-ran both anyway: **86 passed**. The 546-probe suite cannot be
affected by a README-only change and I did not re-run it in full.

**Two one-line improvements I would take, neither of which should hold the bundle:**

1. The host regex is `https://` only, so a host declared over plain `http://` slips
   past — and the app is itself served over `http://127.0.0.1`, so such a host would
   actually load. One character: `https?://`.
2. The scan covers `src/ui/static/*` and `src/ui/server.py` — the page's reach, which
   is the right scope today. If the **server** ever fetches from a new host, the
   paragraph becomes wrong and nothing fires. That is not hypothetical: decision 2 of
   this review is about the FX table expiring on 2026-10-08, and one of the options is
   fetching rates. Widen the scan to `src/*.py` with `seats.aero` allowlisted (the
   paragraph already names it) the day that is built, if not before.

**Verdict: Ship. Nothing is outstanding before the bundle goes to Tsuki.** The
decisions in this review are his to make at leisure, not blockers; the should-fix-soon
list is a next round; and the one thing that genuinely cannot be closed here — that
every LIVE path in this whole round is a stub and the trips parser has never met a real
Seats.aero response — closes on his Mac, not in this sandbox. The "what to do first on
his Mac" list above is what I would put in front of him with the bundle.

---

# Sign-off re-review (ff33b74): the macOS round

Tsuki's Mac produced the first evidence from outside this sandbox: **5 failed /
3565 passed / 13 skipped**. Two defects were behind the five, a third and fourth
came out of the sweep, and the Tester re-scoped two of its own pins on the way.

**Verdict: Ship.** Both of his defects were real product defects — not test
noise — and both are now closed as properties rather than as shapes. I proved
each one red at `a47dcb6` and green at `ff33b74` myself. One new Low, mine, in
the coordinator's own commit; two re-scopings judged below, one of which is
sound and one of which reached the right answer for a reason that is half wrong.

## What I verified myself at ff33b74

| Check | Result |
|---|---|
| `pytest -q -p no:cacheprovider` | **3,690 passed / 13 skipped** |
| the same under `-O` | **3,690 / 13**, 1 pytest warning |
| clean `git archive HEAD` export, interpreter from outside the tree | **3,686 passed / 17 skipped / 0 failed**; 308 files, 0 symlinks, no `.venv` |
| `docs/test-reports/ui-probes` | **663 passed, 0 red** (11m02s) |
| v5 / adversarial / known-failures baselines | **19 / 40 / 0 — identical by test id** to the saved baselines |
| operating-airline | **5 red**, and I listed them: exactly the R5-1/R5-2 pair |

**Red before, green after — each run by me, against a `git archive` of `a47dcb6`:**

* **MAC-1.** A 200-character macOS-shaped path in a `yq-check` row: `console.print`
  gives 2 lines at width 190 and 6 at width 40, path broken both times;
  `print_copyable` gives 1 line with the path intact at 40, 190 and 400. That row
  is the one input that decides whether a carrier surcharge is added to a score,
  and it is pasted by hand. This was worth fixing.
* **MAC-2.** The same 1,201-level file, at recursion limits 1000 / 5000 / 30000:
  at `a47dcb6` it gives **three different answers** — "nested too deeply to
  read", then twice "its top level is a list", which means the file was *read*.
  At `ff33b74` it is one sentence at every limit: *"nested 1201 levels deep, and
  may be at most 32."* That is the defect (an interpreter accident dressed as a
  rule) and the fix, demonstrated. The limits refuse nothing real: the deepest
  JSON anywhere under `tests/fixtures` measures **7** levels against a limit of
  32, and the largest fixture is 17 KB against 4 MiB.
* **Locale.** At `a47dcb6`, `python -m src.main` under `LC_ALL=C LANG=C
  PYTHONUTF8=0` dies at **import** — `UnicodeDecodeError` reading its own source
  — exit 1, 29 lines, no report. At `ff33b74`: exit 0, 226 lines, the real
  `2.04% - 11.03%` headline. A whole class of machine went from "cannot run this
  tool at all" to "runs it".
* **MAC-A.** The relocation banner with a 122-char and a 224-char path: before,
  5 lines for two variables with the 224-char path broken mid-path; after, 2
  lines with both paths whole.

## The two re-scopings

**The contract-file pin (`test_oa_h_guards_docs`): sound, and broader than it
was.** The Tester undersells its own work. The replacement keeps
`assert set(modified) <= {"tests/test_no_changelog_in_user_output.py"}` — no
pre-existing test file may be modified *at all* beyond the one declared change —
and adds a per-file comparison of every assertion's text across the **whole**
`tests/` tree instead of four files. I checked the premise rather than taking
it: `git diff --name-status a17497d HEAD -- tests/` shows exactly one modified
`.py`, and its entire diff is the declared `encoding="utf-8"` on one
`subprocess.run`. No assertion, helper, constant or fixture was touched. The
byte pin went red for exactly the reason given, and the re-scope hides nothing.
The Tester is also right that the sixth red was its own pin being too literal,
not a regression.

**The `main.py` structure pin: right conclusion, half-wrong justification — and
it did cost something.** The ruling itself is correct and is the Tester's to
make: an adversarial pin exists to catch *undeclared* restructuring, and using
it to veto a named, reviewed correctness fix inverts what it is for. But the
sentence that carries the ruling — *"What those functions output is still
pinned - byte-identically - by the CLI golden transcripts and by the 69-scenario
parity suite"* — is only half true, and I checked both halves:

* `print_relocation_banner` **is** covered. I ran a LIVE parity scenario through
  `cli_diff_runner.py` and its output carries
  `POINTS_OPTIMIZER_ENV_FILE is set: …`, so that function's output is diffed
  old-tree against new-tree. Fine.
* `run_new_trip` **is not**. There are **zero** `--new-trip` invocations in the
  14 goldens and zero in the 69 parity scenarios, and nothing anywhere asserts
  the wording of the two lines it prints. `feee113` changed its output shape —
  the "Score it with:" sentence and the command were one `console.print` with an
  embedded newline and are now two calls — and no byte-level test would have
  noticed. Widening the pin removed the only structural pin that function had.

Exposure is small: the new property tests and probe P21 do drive `run_new_trip`
end to end, and `test_trip_builder.py` still byte-pins the fixture it writes. So
this is a gap, not a hole. **Should fix soon, one scenario:** add a `--new-trip`
invocation to `test_ui_g_cli_parity.py`, which makes the Tester's sentence true
and restores the coverage the widening spent.

## The weaker separation, checked harder — and what came out of it

Two commits are the coordinator's, and the Coder left an unfinished test file.
I read that file rather than counting its tests. It is not vacuous: the
C-locale tests spawn **real subprocesses** (the encoding of stdout is a property
of a process, and they say so), assert the run printed a report of more than 50
lines with box drawing in it, and then compare the C-locale run against the
UTF-8 run **character for character**. The filesystem tests apply the rule
directly instead of asking the disk, which is why they can run on ext4 at all.
Both halves are the right tests for the defects.

**New finding — MAC-C (Low), in the coordinator's own MAC-B fix.** `ff33b74`
carries the rest of the path through a mis-cased directory, which is right. It
does not check that the path it now names exists:

```
cited=docs/yq-checks/SUB/b.md        -> 'docs/yq-checks/sub/b.md'        exists=True
cited=docs/yq-checks/SUB/nothing.md  -> 'docs/yq-checks/sub/nothing.md'  exists=False
```

So a row citing a mis-cased directory **and** an absent file is told *"is spelt
`docs/yq-checks/sub/nothing.md` on disk"* — a correction to a file that is not
there. That is MAC-B's own shape one step along, and this project's recurring
failure in miniature: "I could not find it" rendered as "the one you mean is X".
Unreachable through `load()` while `docs/yq-checks/` is flat, same as MAC-B.
**Fix, one line:** return the corrected path only if it resolves; otherwise
return `""` so the caller says "does not exist". Not a blocker.

## Two corrections to the reports themselves

1. **`LANG=C` — both reports are wrong, in opposite directions.** The Coder's
   report says it is "broken end to end… 65 tests still fail"; the Tester's
   ledger says "Closed for everyone". Neither is right at this head. The
   **product** is closed — I ran it. The **test suite** is not: under
   `LC_ALL=C LANG=C PYTHONUTF8=0` pytest gives **5 collection errors and runs
   nothing**, because five test files read repo text (README, the UI's static
   assets) with the locale's encoding at module level. Not his machine, and one
   line per file — the same line MAC-A already added to
   `test_no_changelog_in_user_output.py`. **Should fix soon.**
2. **A stale ledger row.** The Tester's updated ledger lists R6-1 (Medium) and
   R6-2 (Low) as still open. They were closed in `12076b1` and I verified them
   green at `543e75c`; the 663/0 run above confirms it again. A stale ledger is
   how a real open item eventually gets lost — correct it.

## Does this change what Tsuki should do on his Mac?

**The order does not move. One step in it became trustworthy that was not.**

* Re-run the suite first and expect **3,690 passed / 13 skipped, 0 failed**. If
  any of his five comes back, that is new information worth pasting.
* Then, unchanged: OFFLINE Trip B; one LIVE trip run, watching the calls
  counter, the coverage line, the OPERATING AIRLINE section and the
  per-itinerary taxes line; then the YQ measurement.
* **What changed:** that YQ measurement ends with him pasting a `yq-check` CSV
  row into `data/yq_inclusion.csv`, and before this round that row could arrive
  folded across two lines on his Mac and nowhere else. It is the single input
  that decides whether a carrier surcharge is added to a score. Pasting a broken
  one would have been a wrong answer with no symptom. That step is now safe, and
  the evidence filename is checked by name rather than by asking APFS, so a row
  that loads for him loads in CI.
* Everything I said not to trust is still not to be trusted. Nothing in this
  round spoke to the real Seats.aero, and the trips parser is still UNVERIFIED.

## Is the two-bundle delivery still right?

**Yes — and this round makes the second bundle matter more, not less.**
`feature/ui` contains `feature/operating-airline` in its history, so the second
bundle carries everything, these fixes included.

One caveat I verified rather than assumed: **the macOS fixes are engine fixes,
not UI fixes, and two of them repair defects that are on `feature/operating-airline`
too.** I extracted that branch and ran it: `import src.models` under `LC_ALL=C`
dies with `UnicodeDecodeError`, and `print_copyable` does not exist there at all.
So:

* he should not go back to `feature/operating-airline` on his Mac — `feature/ui`
  supersedes it in every respect;
* if feature 1 is ever merged to master on its own, `71f9ec9`, `c52cd9a` and the
  `use_utf8_output` half of `feee113` have to go with it, or master ships the
  bugs his Mac already found.

## Verdict

**Ship.** Nothing here blocks the bundle. Four items for the next round, none
urgent: MAC-C's one-line correction, a `--new-trip` parity scenario, the five
C-locale collection errors, and the stale ledger row — alongside what was
already filed (the Score-column truncation, the swallowed `[modeled]`, F-2). The
standing qualification is unchanged and is still the only thing worth saying
twice: every LIVE path in every round of this feature is a stub, and the first
real Seats.aero response this code sees will be on his machine.
