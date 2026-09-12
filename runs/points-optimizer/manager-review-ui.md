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
