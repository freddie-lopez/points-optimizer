# Coder report: restyle the local UI to direction C ("Graphite")

Branch `feature/ui-restyle`, forked from `feature/ui` at 386b2fc. Plan: `docs/plans/ui-restyle.md`
(committed with step 1). Brief: `docs/design/UI-BRIEF.md` (untracked, as instructed).

Files changed against 386b2fc: `src/ui/static/app.css` (rewritten on the new token sheet),
`src/ui/static/app.js` (+26/-4 lines, the three enumerated edits), `src/ui/static/index.html`
(font link, key-source span), `tests/test_ui_static_rules.py` (the twelve token needles only),
`docs/plans/ui.md` (§4.7 rewritten, one note on the §4.8 key row), `docs/design/ui-mockup.html`
(re-skinned). `src/ui/server.py` untouched: `git diff 386b2fc -- src/ui/server.py` is empty, so the
CSP is byte-identical.

## Steps completed

Each step ran `python3 -m pytest -q -p no:cacheprovider tests/test_ui_static_rules.py` (82 passed
every time) and a headless-Chromium screenshot of the real page through the tester's own probe
server (`docs/test-reports/ui-probes/ui_probe_server.py`, transport stubbed, no network; Google
Fonts is blocked in this sandbox, so every screenshot here shows the system fallbacks - the
"offline fonts" case). Shots are under `docs/design/restyle-ref/shots/` (untracked).

1. **Tokens + fonts** (a909f9d). `:root` replaced by the §4.1 sheet; every old token name renamed
   (`grep -cE -- '--ink|--coal|--ash|--seam|--bone|--smoke|--oxblood|--ember' app.css` = 0);
   `body` gets the glow; the rgba literals for the POINTS fill, WITHHELD hatch, selected search
   cell, scrim and drawer shadow updated; index.html font link → Figtree + Red Hat Mono; the
   token test's twelve needles updated exactly as §4.5 lists. Page loads blue-black; computed
   `font-family` on body starts `Figtree`.
2. **Type and chrome** (e2a163f). `font-stretch` gone (0 occurrences); `.label` 11px/.06em; 48px
   translucent blurred bar; wordmark 16px/600 sentence case with a `::before` accent square;
   tabs 14px/500 sentence case; pills and wallet chips 999px, LIVE pill `--accent` on
   `--accent-ink`; `.btn` 36px/13px/600 sentence case, `.btn-primary` coral, `.btn-small` 28px;
   inputs `--raised` on radius 6; every `border-radius: 3px` → `--radius` / `--radius-chip`.
   Screenshot: RUN is the only coral element on the Trips screen; the focus ring is
   `2px solid var(--accent)` (probes H/K assert `outlineWidth == "2px"` and passed, see step 9).
3. **Key-source behaviour** (7d08ca2). app.js #1 and index.html line 25. Verified in the browser
   against both server states: with `SEATS_AERO_KEY` set (probe server) `key-source` is
   `{hidden: true, text: "", class: "keysrc"}`; against `python3 -m src.ui` with no key and a tmp
   HOME it is `{hidden: false, text: "key: not found", class: "keysrc warn"}`, `mode-live` is
   disabled and the `key-error` fold is present. `/api/state` is untouched (no server change).
4. **Chips and semantic colour** (5ec771f). Chip base radius 4px, padding 1px 7px; warn fills
   (`banner-error`, `no-wallet`, flags, refusal, funding) → `--warn-bg` / `--warn-line` with the
   3px `--warn` left edge kept. Trip B offline with leg B3 open, screenshot converted to
   grayscale (`shots/s4-1440-gray.png`): POINTS filled, WITHHELD hatched, UNKNOWN dashed,
   `BADGE` (cash-qualified form) with its thick left edge, `unverified` small caps - all four
   forms distinct without colour. Computed styles from the page: `.chip-unknown`
   `border-style: dashed`, `.chip-cashq` `border-left-width: 3px`, `.chip-withheld`
   `background-image: repeating-linear-gradient(...)`, `.chip-points` `rgba(95, 211, 160, 0.16)`,
   `.tag-unv` `font-variant-caps: all-small-caps`.
5. **Mode cards + run strip** (6a3a3d2, plus the flex fix in the commit after step 8). app.js #2: `.runrow` holds
   the REPLAY note, the busy note and `.runacts` (Options, Run); strip column = field · key-error
   fold · manifest select · runrow. Verified with `/api/state` intercepted in the browser to carry
   a 200-char `replay_reason_detail.text` around a 120-char path, (a) with slashes and (b) one
   segment, at 1440 and 400, with and without the key-error fold open:
   `document.body.scrollWidth` = 1440 / 400; `run-go` box inside `run-strip` box in all eight
   cases (e.g. 400px, no slashes: strip 16..384 × 932..1403, Run 307.7..367 × 1350..1386;
   1440px: strip 244..1424, Run 1347.7..1407); the note's `.path` `title.length` = 120.
6. **Trip list** (011cd07). app.js #3 adds `trow-trip | trow-search | trow-broken`. With a
   `broken_file.json` (`{ not json`) dropped into the probe server's tmp trips dir, the list
   shows the three forms (warn fill + border / 3px left rail + quiet name / plain), captions
   `CANNOT LOAD`, `NOT A PER-LEG TRIP` unchanged; `shots/s6-1440-gray.png` still tells them apart
   and the selected row reads as a tinted, bordered box.
7. **Tables, headline, drawer, modal, search, forms** (b41fda0). Header band `--raised` 11px
   uppercase (also on the pinned `th`s); `.route` in `--accent` mono 500; cash figures stay
   `--text` mono; drawer sticky top 64px (48px bar + 16); inputs and selects `min-height: 36px`
   so the search strip's select lines up with its inputs. Pinned cell `td.pin-left` computed
   background `rgb(23, 29, 38)` (opaque) at 1440 and 400.
8. **Docs + mockup** (b6c15e6). `docs/plans/ui.md` §4.7 rewritten to the C system; §4.8 key row
   gets the one-line decision note; UI-BRIEF.md §4 gets its pointer line (file left untracked as
   instructed). `docs/design/ui-mockup.html`: its `<style>` is now a copy of app.css plus the four
   mockup-only rules, the font link, the run-strip markup (`.runrow`/`.runacts`), the trip-row kind
   classes and the key span follow the product. Opened from `file://` with every request but the
   file aborted: 0 page errors, `scrollWidth` 1440/400 at those widths (`shots/mockup-*.png`).
   `grep -n "docs/plans/\|docs/test-reports/" src/ui/static/*` = nothing;
   `test_no_changelog_in_anything_the_page_ships` green for all three files.
9. **Full verification.**
   - `python3 -O -m pytest -q -p no:cacheprovider`: **3692 passed, 13 skipped** (128 s).
   - `python3 -m pytest -q -p no:cacheprovider`: **3692 passed, 13 skipped** (133 s).
   - `python3 -O -m pytest -q -p no:cacheprovider docs/test-reports/ui-probes -p no:randomly`:
     **663 passed, 0 failed** (485 s). No probe or report file was edited (`git status` shows
     nothing under docs/test-reports).
   - Smoke: `python3 -m src.ui --no-open --port 8777 --wallet <tmp wallet>` (no key, tmp HOME),
     Trips view, Trip B selected and run OFFLINE, at 1440×900 and 400×800 →
     `shots/smoke-1440.png`, `shots/smoke-400.png`, `shots/smoke-top-400.png`; `body.scrollWidth`
     = 1440 / 400; the only console error is the aborted Google Fonts stylesheet.

The first full run under `-O` (before the fix below) was 3691 passed / 1 failed.

## Deviations from the plan

1. **`.seg button` is `flex: 1 1 0; min-width: 200px; max-width: 420px`, not `flex: 1 1 200px`.**
   `tests/test_ui_run_strip_and_trip_listing.py::test_the_three_segments_are_one_width_and_the_note_is_its_own_line`
   pins the needle `flex: 1 1 0` in app.css and went red with the plan's literal. The plan lets
   me change only the token needles in `test_ui_static_rules.py`, so I changed the product: with
   equal basis and a 200px floor the cards are one width on a line and wrap one-per-row at 400px
   exactly as `1 1 200px` would (re-measured, step 5 numbers are from after the change). The
   test's name still says "the note is its own line"; its assertions are needles (`.mode-note`
   present, the `replay-unavailable` testid, `pathEl.title`) and all hold. The manager may want
   that test's name refreshed; I did not touch it.
2. **`th.pin-left / th.pin-right` take the `--raised` header band** (the plan's table says pinned
   backgrounds "follow `--panel`/`--raised`"; a `--panel` pinned header cell inside a `--raised`
   band would read as a hole).
3. **`.field input, .field select` are 36px tall with `padding: 0 10px`** rather than the plan's
   unstated padding; done so a `<select>` and an `<input>` in the same strip share a height.
4. **The mockup's key span is `hidden`** (the found state), matching decision D8, instead of
   showing `key: environment`.
5. The `.runrow .note` (the busy note) gets `flex: 1 1 200px; min-width: 0` so it shares the row
   with the buttons; the plan named only `.mode-note`. `run-busy`'s testid and text are unchanged.
6. `.wordmark` is `inline-block` with a `::before` square carrying `margin-right`, not a flex row:
   flex made the text node and the `<span>` two items with a gap between them.
7. A `.venv/bin/python` symlink to `/usr/bin/python3` was created (gitignored) because
   `docs/test-reports/ui-probes/browser.py` runs the probe server with `ROOT/.venv/bin/python`;
   without it the browser probes cannot start.

Not a deviation but worth stating: §4.8's G-row still says the wordmark reads `POINTS OPTIMIZER`.
The DOM text has always been `Points Optimizer` (the capitals were CSS); the plan says no other
§4.8 text moves, so I left it.

## How to run it

```bash
python3 -m src.ui --wallet wallet.json --port 8777 --no-open     # Tsuki: .venv/bin/python
python3 -m pytest -q -p no:cacheprovider tests/test_ui_static_rules.py
python3 -O -m pytest -q -p no:cacheprovider                       # and once without -O
python3 -O -m pytest -q -p no:cacheprovider docs/test-reports/ui-probes -p no:randomly
```

Screenshot harness (scratch, not committed): a Playwright script that starts
`ui_probe_server.py <scenario>` or `src.ui` itself, aborts every offsite request, and can
intercept `/api/state` to stub a long `replay_reason_detail` / no key.

## Known gaps

- Fonts were never seen: fonts.googleapis.com is refused by this sandbox's proxy, so every
  screenshot uses DejaVu fallbacks. The plan's risk 1 (Figtree/Red Hat Mono metrics at 400px)
  is unmeasured; the fallback layout has no horizontal scroll at 400px and probes K25–K27 pass.
- The busy state (`S.busy`, "Scoring… N s" beside the buttons) was not screenshotted: offline runs
  finish in ~0.1 s. It is placed by the same `.runrow` rule as the REPLAY note.
- The top bar at 400px wraps to four rows (~170px); that is the pre-existing `bar-right`
  wrap, now with 24px pills. Unchanged breakpoints per the plan.
- The translucent bar shows a faint ghost of scrolled content through the blur (decision D6);
  it stays legible in the screenshots, but it is a taste call Tsuki should look at with real fonts.
- No icons (decision D7): the mockup's warning triangles and Run glyph are not drawn.

## Out-of-scope observations

- `docs/test-reports/ui-probes/browser.py` hard-codes `ROOT/.venv/bin/python`; on a machine
  without a venv the whole browser suite fails to start. A fallback to `sys.executable` would
  make the suite portable (tester's file; not touched).
- `.trips-grid` keeps its 208px sidebar; the approved mockup draws 272px. At 208px the new
  12px 14px row padding leaves long trip names wrapping to three lines. Widening the column is a
  one-token change if wanted.
- The mockup re-skin copies app.css wholesale; it will drift again on the next CSS change unless
  it is generated from app.css.

## Fix round 1

Tester's report: `docs/test-reports/ui-restyle.md`; probes `docs/test-reports/ui-restyle-probes/`
(neither edited). Four commits over 7fba6e0; E1, E3 and C5 left alone per the coordinator.

1. **D1 (high)** — bb8ffc9. `.runstrip` gets `grid-template-columns: minmax(0, 1fr)`,
   `.runstrip > * { min-width: 0 }` and `.runstrip details.fold { max-width: 100% }`, so the
   key-error fold's `pre.transcript` scrolls inside its own box instead of widening the grid to
   the engine's 133-char line. Wording untouched. `test_D1[...]` 8/8 green (the two `nokey-*-400`
   reds included); re-screenshot at 400 with the fold open: `bodySW` 400, RUN inside the strip
   (`docs/design/restyle-ref/shots/fix-d1-400.png`, cropped from the tester's own
   `shots/d1-400-slash-nokey.png`).
2. **K1 (medium)** — 86e1715. In the `@media (max-width: 720px)` block only: `.topbar-in`
   `min-height: 40px; gap: 2px 14px; padding-block: 0 4px`; `.tab min-height: 38px`;
   `.bar-right gap: 2px 8px`; `.calls { order: -1; flex: 1 1 100%; font-size: 11px;
   line-height: 1.25 }` (its own two-line row, first, so the mode pill, wallet chips and key chip
   share the row under it); pills/wallet/key chip 22px tall. Nothing dropped; DOM and tab order
   unchanged (`order` is visual only). Bar height at 400: **96.5px** in both the no-key-after-a-run
   and the with-key states (was 149). `test_K1[offline_b|no_key|no_wallet]` 3/3 green.
3. **J2 (low)** — da6405d. ui.md §4.7 now says `flex: 1 1 0; min-width: 200px; max-width: 420px`.
   `test_J2` green.
4. **K4 (low)** — da6405d. `.seg button:disabled { opacity: .8 }` with the title in `--muted`;
   the caption is `--muted` on the card at .8 = **4.18:1** (computed by the probe; was 2.69).
   Not dashed (dashed means UNKNOWN). `test_K4` green.
5. **D5 (low)** — 94ebb0c. `@media (max-width: 1180px)` → `@media (max-width: 1179.98px)`: the
   drawer is docked at exactly 1180, as ui.md §4.7/§5 and the brief say. `test_D5[1179|1180|1200|
   1280|1440]` and `test_D6` green.

Runs after the fixes:
- `tests/test_ui_static_rules.py`: 82 passed.
- Full suite `-O`: **3692 passed, 13 skipped** (127 s).
- Tester's suite (`-O`, `-p no:randomly`): **253 passed, 4 failed, 10 skipped** (196 s; was
  256/11/10). Green now: the 8 reds for D1 (2), K1 (3), D5 (1), J2 (1), K4 (1). Still red,
  untouched by instruction:
  `test_E1`, `test_E3`, `test_C5`. **One test moved red that was green: `test_J8_undisclosed_css_changes_the_coder_did_not_list`.**
  It pins the exact list of rules removed since 386b2fc (`[".seg button:last-child"]`) by parsing
  the diff; the D5 fix necessarily rewrites the `@media (max-width: 1180px) {` line, which the
  probe reads as a removed rule (`['.seg button:last-child', '@media (max-width: 1180px)']`).
  The probe is informational ("each one is checked by hand"); the coordinator asked for D5 to be
  fixed in the media query, so the two cannot both hold. Not edited; the tester should refresh
  that needle (or the manager can choose the docs-only route for D5, which would put J8 back and
  D5[1180] red again).
- ui-probes (`-O`, `-p no:randomly`): **663 passed, 0 failed** (487 s).
6. **L3b (tester #9)** — `.seg button:disabled { cursor: not-allowed }` (the card's own
   `cursor: pointer` outranked the global disabled rule). `test_L3b` and `test_J8` as run
   together: 2 passed (J8's state is as described above); static rules 82 passed.
