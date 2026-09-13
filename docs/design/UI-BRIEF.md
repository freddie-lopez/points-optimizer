Brief: before you touch the points-optimizer UI
Written 2026-09-13, at feature/ui head 386b2fc, for a session whose job is to make the
local web UI prettier and nicer to use. Read this first. It is the shortest thing that will stop you
breaking something expensive.




1. What this app is
A decision layer over award availability, not an award search engine. Seats.aero, PointsYeah
and Roame already do search. This tool answers one question per leg: should Tsuki pay cash,
or burn points? It takes his balances and the cards he holds, the cash fare he found, and real
award availability, and it ranks the ways to pay — transfer program, ratio, points spent, cash still
owed.

Scope today: Chase UR only, 160,000 points, Chase Sapphire Preferred. One traveller per
flight leg (couple trips are entered as 1 traveller with per-person cash; 2+ travellers are
deliberately refused, not modelled). Hotels are read-only in fixtures; hotel redemptions are
feature 3.

It runs locally on his Mac — .venv/bin/python -m src.ui — because the Seats.aero
API is blocked from the Claude sandbox and his key is on his machine. The browser talks to a
stdlib Python server on 127.0.0.1:8777.




2. The one rule that governs every UI decision
This project exists because of one failure, and it has committed it in every round:

      Reporting a failure as a finding — turning "I could not find out" into a confident
      value: $0, "no availability", "no path", "not a partner", a carrier name, a single
      percentage.

Real examples from this codebase's history: a parser that read invented field names returned
zero awards and that emptiness was reported as "no award availability"; a missing tax figure
was scored as $0; a leg whose API call failed printed none - not a partner; a verdict
sentence quoted a figure that left out the duty the score included.
In a UI this failure wears a new costume: a clean, pretty number where the engine says it
does not know. So:

   -​   Everything the engine marks stays visible and stays in the user's face. The markers:
        UNKNOWN (and the phrase NOT $0), WITHHELD, a range and its label (e.g. 2.04% –
        11.03% + carrier SURCHARGE unknown on B3), UNVERIFIED, NOT LOOKED UP,
        NOT RECORDED, NEVER PRICED, API FAILED, UNREADABLE, badge, snapshot,
        CONFIRM BEFORE TRUSTING, REPARSED, and every exit N label.
   -​   A headline percentage is never rendered without its qualifier ((badge), (live),
        (snapshot mh_…)). They are one element on purpose.
   -​   Never substitute a dash, a blank, a zero or a spinner for an unknown. UNKNOWN is a chip
        with a dashed border and the literal word in it. That dashed border is information, not
        decoration — don't tidy it into a neutral grey pill.
   -​   Colour never carries meaning alone. The accent (oxblood) is brand; green means points
        win; amber means withheld/caution; the dashed border means unknown. Keep the
        shape as well as the colour.

The tester enforces this: probes assert that every marker the CLI prints for a leg appears in the
rendered DOM, not merely in the transcript. If your restyle drops one, the probe suite goes red
and it should.




3. How the pieces fit
browser (app.js) --GET/POST /api/*, X-PO-Token, same-origin--> src/ui/server.py

                                          ├── src/ui/api.py    routes

                                          └── src/ui/engine.py argv, run lock,

                                               confirm tokens, session wallet

                                               │

                                 argv -> src.main.build_parser() -> dispatch()

                                               │

                             FixtureRun / SearchRun / RunRefusal + full CLI transcript

                                               │
                                       src/ui/serialize.py -> JSON -> app.js

Every UI run is a real CLI run. The page builds an argv, hands it to the CLI's own parser and
dispatch, and renders the result objects; the words come from the same shared builders the
terminal prints. That is why the UI cannot say something cleaner than the CLI: it is the CLI's own
sentence. The page also shows the equivalent command and the full transcript.

Files (line counts at 386b2fc):


 File                             Lines                             What

 src/ui/static/app.css            282                               all the styling — the file you
                                                                    will spend most time in

 src/ui/static/app.js             1,671                             renders every view; builds
                                                                    DOM with
                                                                    createElement/textCont
                                                                    ent

 src/ui/static/index.ht 54                                          shell, token meta, no inline
 ml                                                                 script (CSP)

 src/ui/serialize.py              728                               engine objects → JSON
                                                                    (TripRunJSON, LegJSON,
                                                                    SearchRunJSON)

 src/ui/engine.py                 961                               argv builder, run lock, per-run
                                                                    reset, confirm tokens, session
                                                                    wallet

 src/ui/server.py                 291                               Host/token/Origin gates, CSP,
                                                                    static serving, key egress
                                                                    filter

 src/ui/api.py                    99                                the route table


API routes: GET /api/state, POST /api/wallet, GET /api/trips, POST
/api/trips/draft, POST /api/trips/create, GET /api/trips/{id}, POST
/api/trips/{id}/preflight, POST /api/trips/{id}/run, POST
/api/search/preflight, POST /api/search/run, GET /api/runs/{id}.

The JSON shapes are specified in docs/plans/ui.md §4.6 and the screen inventory — every
field, every state, with the exact user-facing words — is §4.8. Read §4.7 and §4.8 before
restyling anything. The design reference mockup is docs/design/ui-mockup.html (open
it in a browser).




4. The design system as built
Superseded by direction C ("Graphite") - see docs/plans/ui-restyle.md and docs/plans/ui.md §4.7; the tokens and type below are the pre-restyle set.
Deliberately single-theme dark: a departure-board instrument in black and oxblood. Tsuki's
brief was "minimalistic dark theme, almost like seats.aero, but dark red and black". Tokens live
at the top of app.css:

--ink #0A0809 page ground (black with a red bias)      --bone #ECE4E3 primary text

--coal #121011 panels, table body                --smoke #9C8F8F secondary, labels

--ash #1B1718 inputs, hover rows, raised            --oxblood #7A1620 brand fill, active tab

--seam #2B2325 1px hairlines                    --ember #B4323C focus ring, hover, links

semantic, never the accent: --win #5E9E73 · --warn #C8923A · --unknown #7D7475

Type: Archivo (variable width/weight) for UI text; the wordmark and section labels at
font-stretch:125%, 600, uppercase, letter-spacing:.08em; IBM Plex Mono with
tabular-nums for IATA codes, flight numbers, miles, dollars, dates, leg ids. Both from Google
Fonts with a full system fallback — the app must look right offline. Scale 12/13/14/16/20, plus 28
for the headline value only. Radius ≤ 3px. No shadows except the detail drawer. No gradients
except the WITHHELD hatch. No emoji.

Layout: 40px top bar (wordmark, tabs, mode pill, calls counter, wallet chips, key source); content
max 1280–1440px with 16px gutters; dense tables with 32px rows; the detail drawer docked as
a third column ≥1180px and a full-screen sheet below that; LEG and VERDICT columns pinned
(position: sticky) so the answer stays on screen while the middle scrolls; everything
works down to 400px with no page-level horizontal scroll.


What "prettier" may change, and what it may not
Fair game: spacing, rhythm and density; type scale and weights; the shape and finish of
panels, chips and buttons; table legibility (zebra, hairlines, alignment, sticky behaviour); empty
and error states; the run strip and mode selector, which is the clumsiest thing on the page; the
drawer's section order and hierarchy; loading and in-flight feedback; iconography (SVG only, no
icon font, no emoji); the search view, which is the plainest.
Not fair game without the team cycle: any user-facing wording (it comes from shared
builders the CLI also prints, and is pinned by goldens and a 69-scenario parity suite); the
markers in §2; the data-testid attributes (the tester drives the page by them — add freely,
rename nothing); the security model in §5; dependencies; anything that turns an engine refusal
into a friendlier-looking number.




5. What is pinned, and will bite you
   -​   Full suite: 3,692 passed / 13 skipped, and the same under -O (.venv/bin/python
        -m pytest -q -p no:cacheprovider). -O matters: structural guards refuse at
        import there.
   -​   Four probe suites, compared by test id, not by count (they live outside testpaths):
        docs/test-reports/v5-probes 19 red · adversarial-probes 40 red ·
        known-failures-probes 0 red · operating-airline-probes exactly 5 red (the
        known R5-1/R5-2 pair) · ui-probes 0 red (~11 minutes; it drives a real server and a
        real headless Chromium). A red test in the first two suites asserts a defect that was fixed
        — never "fix" one by changing the probe.
   -​   CLI output parity: 14 byte-identical goldens plus 69 CLI invocations diffed against a
        pre-UI tree. Three CLI output changes have ever been allowed, each named and
        reviewed. If your change moves CLI text, that is a decision, not an accident.
   -​   Security (tests/test_ui_security.py, and the tester's own probes): binds
        127.0.0.1 only; Host allowlist; a per-launch token on every /api/* request; Origin
        checked on POST; strict CSP with no inline script; single-use server-side confirm tokens
        for anything that can spend Seats.aero calls; the API key never reaches the browser, the
        transcript, a log or an error body. app.js builds DOM with textContent — a test bans
        innerHTML with data, and Seats.aero strings are hostile input by assumption.
   -​   No new dependencies. PyPI is blocked in the sandbox and his venv is pinned (pytest
        7.4.0, requests 2.31.0, rich 13.5.0, pyyaml 6.0.1). No build step, no npm, no CDN script.
        Google Fonts is the single allowed external request and the README says so.
   -​   Never call seats.aero from the sandbox. Every test stubs the transport; a socket canary
        proves it.
   -​   Tests must never write into the repo tree or Tsuki's paths, and never spend API quota.




6. How to work
The team: architect → coder → adversarial tester → manager, definitions in the Project
under agents/, run as subagents with the role text embedded. Tsuki's standing rules:

   1.​ Never skip the adversarial round. Hold the line on it.
   2.​ Nothing is delivered without manager sign-off.
   3.​ Work a to-do list end to end without check-ins; ask only when information is genuinely
       needed.
   4.​ Direct and brief; honest caveats over optimistic framing.

Commits end with:

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>

Claude-Session: <this session's URL>

The sandbox cannot push to freddie-lopez/points-optimizer (the proxy refuses it), so
work is delivered as a git bundle and Tsuki pushes. His clone is
~/Downloads/points-optimizer-git with a .venv; he runs .venv/bin/python -m
src.ui --wallet wallet.json.




7. The to-do list this round inherits
Real, already-filed, and roughly in the order a user notices them:

   1.​ The wallet banner corners you. With a broken wallet.json the banner names the
       JSON error — accurately — and says nothing about the wallet panel two inches to the
       right, which works even then. Tsuki hit exactly this and fixed it by hand before he found
       the panel. Signpost the way out, without ever guessing what he holds.
   2.​ No way to try a trip without saving one. "+ NEW TRIP" writes a permanent fixture into
       tests/fixtures/trips/, which his test suite then runs over. A five-minute
       experiment left 2yrAni.json in his repo. A scratch trip (and a delete button) is the
       most-wanted missing thing. It needs an architect: the CLI takes --trip-fixture
       PATH, so where a scratch trip lives is a real design question.
   3.​ The run strip breaks its own layout when REPLAY is unavailable and its reason is a
       long path. It was partly fixed; look at it again at 1440px and at 400px.
   4.​ The trip list mixes kinds — per-leg trips, single-route search requests, fixtures that
       cannot load — and they all look alike apart from a small caption.
   5.​ The transcript truncates money ($2,400.…) because it is rich output at 190 columns,
       and the transcript is what he pastes back. Pre-existing CLI behaviour; needs a round
       where CLI output may move. Same round should drop the conditional column floors in
       src/formatter.py and the [modeled] that rich swallows.
   6.​ No cancel for a run in flight, and a LIVE trip can in principle take minutes.
   7.​ First-run friction: with no wallet.json the RUN button is live and the first click
       returns exit 2 · WALLET ERROR. Exact CLI parity, still a bad first minute.
   8.​ Smaller: five test files read repo text with the locale's encoding, so pytest under
       LC_ALL=C gives collection errors (the product is fine); a --new-trip scenario is
       missing from the parity suite; F-2 (0.00% (none), exit 0) is kept by decision T4.




8. What you must not do, in one list
   -​   Do not make an unknown look like a number, a blank, a dash, or a zero.
   -​   Do not render a percentage without its qualifier.
   -​   Do not reword user-facing text as part of a restyle.
   -​   Do not rename or drop a data-testid.
   -​   Do not add a dependency, a build step, or an external request.
   -​   Do not weaken the local-server gates, or send the key to the browser.
   -​   Do not edit the tester's probes or reports to make something pass.
   -​   Do not ship without the adversarial round and manager sign-off.




9. Where everything is
In the repo: docs/plans/ui.md (the UI plan: decisions, JSON shapes, design system §4.7,
screen inventory §4.8, testing strategy) · docs/plans/operating-airline.md ·
docs/design/ui-mockup.html (visual reference) · docs/test-reports/ui.md (six
adversarial rounds plus two macOS rounds) · docs/test-reports/ui-probes/ ·
runs/points-optimizer/* (coder and manager reports) · README.md "Local UI" section.

In the Dev Team project: plans/points-optimizer-handoff.md (read this too — the
state of the whole build, and what Tsuki must do on his Mac next) ·
plans/points-optimizer-ui.md ·
runs/points-optimizer/manager-review-ui.md · agents/*.md.

The standing qualification, which has not moved: nothing in this codebase has ever spoken
to the real Seats.aero. Every LIVE path in every report is a stub, and the trips parser is still
UNVERIFIED against a real response. The first real answer will arrive on Tsuki's Mac.
