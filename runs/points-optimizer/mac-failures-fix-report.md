# Coder report — the five failures on Tsuki's Mac

Worktree `/home/claude/points-optimizer-ui`, branch `feature/ui`, from `a47dcb6`.
Five commits: `71f9ec9`, `5d9e906`, `534d62f`, `1185dbf`, `c52cd9a`.

His run: **5 failed, 3565 passed, 13 skipped**. Both defects are the same
shape — a rule that was really a property of the machine the suite happened to
run on. Neither was a test-only problem.

---

## MAC-1 — `yq-check` folded the row it asks him to paste

**What was wrong.** `console.print` word-wraps at the console width and folds a
word longer than the width mid-word. Every console this tool builds is 190
columns, so nothing wraps until a line passes 190 characters — and the only
lines that do are the ones carrying an absolute path. On macOS a pytest tmp path
is `/private/var/folders/9w/<hash>/T/pytest-of-<user>/pytest-4/<test>/…`, which
pushed the yq-check CSV row past 190; rich folded it in the middle of the
evidence path and the substring the test looked for was gone. In the sandbox the
tmp path is short, so the test was passing on **the length of the checkout's
temp directory**.

That row is what he pastes into `data/yq_inclusion.csv`, and a `yq_inclusion`
row is the one thing here that decides whether a carrier surcharge is added to a
score. A row pasted out of two lines is a different row. It is not enough for
the row to be correct when it is printed; it has to be copyable.

**The fix.** `formatter.print_copyable(console, text, style="")` prints one line
with rich's soft wrap — no wrapping, no cropping, at any width — escaped, so a
square bracket in a path is a bracket and not markup, with colour carried by
`style` instead of by markup inside the text. Every line of that kind now goes
through it:

| Line | Where |
|---|---|
| the `source,airline,<VERDICT>,…` row | `trips_tools.run_yq_check` |
| `wrote <capture>.json`, `wrote <capture>.raw.txt` | `trips_tools.run_capture` |
| `wrote <record> - fill the ____ blanks` | `trips_tools.run_yq_check` |
| the drift bullets and the label-check bullets (they name the capture directory) | `trips_tools.run_capture` |
| `  snapshots: <dir>` and `  manifest:  <path>` — the manifest is what `--from-snapshot` is handed next | `formatter.print_live_banner` |

**The regression test** is `tests/test_copyable_lines_are_never_wrapped.py` (63
tests). It asserts the property, not a substring: at widths 20 / 40 / 80 / 190 /
400, with a macOS-shaped record directory, each of those lines comes back
**whole and equal**; the CSV row is the only line carrying it; the same lines are
byte-identical at every width; and the row printed off a 40-column console, with
`<VERDICT>` replaced exactly as the tool instructs, still loads as a
`yq_inclusion` row. An AST check keeps a new `console.print` from interpolating a
whole path again.

**Red before the fix:** 23 of the 63, on a `git worktree` of `a47dcb6` with only
the `print_copyable` primitive added so the module imports (the call sites left
alone). The `[400]` variants pass there — 400 columns is wide enough — which is
the defect stated precisely.

### Two more found in the same class

Re-running the whole suite on `a47dcb6` under a deliberately long `--basetemp`
(81 characters, macOS-realistic; then 178) turned up two more, both fixed by the
same change and both previously passing on path length alone:

* `test_trips_capture_verdict.py::test_a_capture_outside_real_exits_5` — the
  label check's refusal names the directory it refused.
* `test_ui_api.py::test_the_live_transcript_matches_golden_g4_apart_from_key_line_and_paths`
  — the golden transcript's `manifest:` line.

### Deliberately not fixed: `src/main.py`

`src/main.py` prints three lines of this kind — the relocation banner's
directory, `Wrote PATH` from `--new-trip`, and the command `--new-trip` tells
you to run next. I changed them, and it turned a **sixth** operating-airline
probe red:
`test_oa_r5_retest.py::test_main_py_changed_only_by_the_dispatch_split_the_ui_plan_declares`
pins every top-level definition in `main.py` to round 4 and allows only the UI
plan's dispatch split. `main.py` is reverted to `a47dcb6` byte for byte
(`1185dbf`). None of those three can pass 190 columns in practice: the paths are
inside the repo or chosen by the person running it, and the command is a fixed
length. The reason is written at the top of the copyable-line tests so the next
reader finds it where they will look.

### Already right, checked

The UI's **Equivalent command** is not affected: `.cmd` is
`white-space: nowrap; overflow-x: auto`, and the Copy button copies the source
string, not the rendered text. The UI's drawer lines are recorded at width
10,000 for this exact reason (`serialize._recorded`).

---

## MAC-2 — a refusal that was an interpreter accident

**What was wrong.** R5-4 closed "a 400-level-deep JSON document is a refusal, not
a traceback" by catching the `RecursionError` `json.loads` raises walking it.
That is not a rule; it is whatever the interpreter does with its stack. The same
file is refused here (exit 1, CANNOT LOAD) and **read** by his macOS Python,
where the run exits 0 with no `load_error`. Reproduced on `a47dcb6` by giving
this interpreter the stack his has:

```
a47dcb6 with a macOS-sized stack -> exit 0 | 'Error' in output: False
```

**The fix — the rule, and what it does not limit.** `src/config.py` now carries
the shape half of the boundary the numbers already come through:
`json_nesting_depth(text)`, `MAX_JSON_NESTING_DEPTH`, `MAX_INPUT_FILE_BYTES`, and
the one sentence each refusal uses. The depth is measured by **scanning the
text** (string literals removed, then brackets) before anything parses it, so it
uses no stack of its own and gives the same answer on every interpreter.

* **Depth — 32.** The deepest shape the fixture schema has is five levels
  (document / `legs` / a leg / `cash_options` / an option). Every committed
  fixture measures 2 to 5. 32 is six times the deepest shape that exists.
* **File size — 4 MiB**, checked from `stat()` before the bytes are read. The
  largest committed fixture is 17 KB (`trip_b_europe.json`, seven legs); this is
  ~240× that. It exists because both the scan and `json.loads` hold the file in
  memory, the file is chosen by whoever points `--trip-fixture` at it, and the
  UI loads every file in its trips directory on every page load.
* **Number of legs, number of keys — deliberately not limited**, and the
  reasoning sits beside the constants. Both are already bounded by the size rule
  (a 4 MiB file cannot hold more than a few tens of thousands of either), and
  neither reaches recursion or any other stack — legs are a flat loop, keys are
  dict lookups — so a big-but-legal trip is slow at worst. A leg cap would be a
  number invented out of nothing that could one day refuse a real
  round-the-world itinerary.

The `RecursionError` handlers **stay as a backstop** (both of them, plus the one
in `main`), and they raise the same sentence through `_too_deep()`, so a reader
is never told which interpreter ran out first:

```
limit=1000   exit=1  Error: deeply_nested.json is nested 1201 levels deep, and may be at most 32. It is not a trip fixture.
limit=20000  exit=1  Error: deeply_nested.json is nested 1201 levels deep, and may be at most 32. It is not a trip fixture.
```

One line and exit 1 from the CLI; the same sentence in the UI's `load_error`,
`422` on the detail, exit 1 on the run.

**The regression test** is
`tests/test_a_fixtures_shape_is_a_rule_not_a_stack_depth.py` (25 tests): the scan
agrees with walking the parsed document; 5,000 levels are measured, not raised;
brackets inside strings are not nesting; a 100-deep file this interpreter reads
happily is still refused, identically at recursion limits 1000 / 3000 / 20000;
the rule's own boundary (32 loads, 35 does not); the CLI line and the UI row; and
**the limits refuse nothing real** — every committed fixture is checked against
both, and a 400-leg trip still loads.

**Red before the fix:** 9 of the 25 on `a47dcb6` with only the constants and the
scan shimmed in — including the two that are exactly his failures (the UI listing
with no `load_error`, the CLI exiting 0).

### The same accident, one file over: the wallet

`--wallet` is the second file this tool reads from outside, and it had it too.
Verified on `a47dcb6`:

```
wallet limit=1000 : RecursionError: maximum recursion depth exceeded while decoding a JSON object
wallet limit=30000: loaded {'UR': 1}
```

On a small stack the CLI reported it as "this run could not be scored:
RecursionError" (the `main` backstop) and the UI's wallet panel did not catch it
at all — `engine.wallet_state` handles `WalletError` and `SystemExit` only, so it
would have been a 500. On a large stack the same file loads. `load_wallet` now
comes through the same `config` boundary; the wallet's own shape is three levels
deep, so the shared limits are far above anything real. One line away, the wallet
was also read with the **locale's** encoding; it is UTF-8 by name now, and a file
that is not UTF-8 is a `WalletError` rather than a traceback.

---

## The rest of the platform sweep

| Category | Result |
|---|---|
| **path length / tmp path shape** | Swept by re-running the whole suite under an 81-char and a 178-char `--basetemp`. Three failures, all fixed (MAC-1). Clean at both lengths now. |
| **stack depth / recursion limits** | Swept by running loaders at limits 1000–30000. Two found (fixture, wallet), both fixed. Remaining JSON readers (`response_cache`, `snapshot_replay`, `seats_trips`) read files **this tool wrote or the repo committed**, not files a person points it at, and every one of them is behind `main`'s `RecursionError` backstop. Listed, not changed. |
| **console width** | Every console the product builds has an explicit width (190 for output, 10,000 for the UI drawer), so `COLUMNS` and the terminal cannot change what is printed. The only width-sensitive lines were the folding ones. |
| **line endings** | Checked: a CRLF trip fixture, a CRLF wallet and a CRLF `yq_inclusion.csv` all read identically (`read_text` translates, the CSV is opened `newline=""`). No change needed. |
| **filesystem case-sensitivity** | The rules that matter are string rules, not filesystem rules: the evidence-path prefix check (`docs/yq-checks`) and the trip-id lookup are case-sensitive comparisons on both platforms. **One divergence, not fixed:** a `yq_inclusion` row whose evidence *filename* is mis-cased passes `is_file()` on his Mac and fails in CI. It cannot launder a verdict — the record's own source and verdict lines still have to match — but it is a row that loads on one machine and not the other. |
| **locale** | One product defect found and fixed (`c52cd9a`): `src/models.py` reads this package's own source at **import** to collect reason codes, with the locale's encoding, and seven files in `src/` contain non-ASCII bytes — so under `LANG=C` `import src.models` raised `UnicodeDecodeError` and the whole package failed to import (98 collection errors). **Not fixed, and it does not work under `LANG=C` even so:** `python -m src.main` cannot write its output there at all (rich emits U+2026 to an ASCII stdout), and 65 tests still fail reading repo text files (README, the UI's static assets, docs) the same way. Closing that is a ~30-file sweep plus a decision about forcing the process's output encoding. Reproduce with `LC_ALL=C LANG=C PYTHONUTF8=0 .venv/bin/python -m pytest -q`. Not his environment — macOS is UTF-8. |
| **directory iteration order** | Checked: every `glob`/`iterdir` in `src/` is already `sorted()`. Nothing to do. |
| **timezone** | `yq-check`'s record filename is the LOCAL date while the capture's `_meta.captured_at` is UTC, so a run after ~17:00 PDT names the record one day ahead of the capture it cites. That is a design choice rather than an accident, and no rule reads the filename. Listed only. |

---

## What I ran

All from the worktree, `.venv/bin/python`, after the last commit.

| Run | Result |
|---|---|
| `pytest -q -p no:cacheprovider` | **3,656 passed, 13 skipped** (3,570 + 86 new) |
| the same with an 81-char `--basetemp` (macOS-realistic) | 3,656 / 13 |
| the same with a 178-char `--basetemp` | 3,656 / 13 |
| the same under `python -O` | 3,656 / 13 |
| `docs/test-reports/v5-probes` | 19 red — **identical by test id** to the baseline |
| `docs/test-reports/adversarial-probes` | 40 red — **identical by test id** to the baseline |
| `docs/test-reports/known-failures-probes` | 0 red — identical to the baseline |
| `docs/test-reports/operating-airline-probes` | 569 passed, **5 red**, exactly the R5-1/R5-2 five |
| `docs/test-reports/ui-probes` | **546 passed, 0 red** (8m44s) |
| unpacked `git archive HEAD` export, interpreter from outside the tree | **3,652 passed, 17 skipped, 0 failed**; 305 files, 0 symlinks, no `.venv` |

Red-before-fix was proved on a `git worktree add` of `a47dcb6` in the scratchpad
(no stash), as described under each defect. No Tester file was touched:
`docs/test-reports/*-probes/`, `docs/test-reports/ui.md` and
`docs/test-reports/operating-airline.md` are unchanged.

## Known gaps

1. **`src/main.py`'s three copyable lines are still ordinary prints.** See above:
   the operating-airline probe pins the file, and none of the three can fold at
   190 columns. If the Manager would rather have them, the probe's
   `SPLIT_BY_THE_UI_PLAN` set needs widening in the same change.
2. **`LANG=C` is broken end to end**, and my one-line fix does not change that.
   Detail and reproduction in the table above.
3. **Mis-cased evidence filenames** load on a case-insensitive filesystem and not
   on a case-sensitive one.
4. **Nothing here was run on macOS.** Everything I could not test directly I
   simulated on this Linux box and said how: the path length by `--basetemp`, the
   stack by `sys.setrecursionlimit`, the locale by `LC_ALL=C`. What genuinely
   needs his machine is the case-insensitive filesystem (item 3), the actual
   `/private/var` tmp shape, and confirmation that his five failures are now
   green. The two that were his are now asserted as properties that hold at every
   width and every recursion limit, so a pass here should mean a pass there.

## Out of scope, noticed

* `seats_trips.schema_verification_problems` compares the `.raw.txt` body it
  wrote against the page it parsed. `write_text`/`read_text` translate newlines,
  so a Seats.aero body containing `\r\n` would round-trip as `\n` and the check
  would refuse its own capture. Not platform-dependent (it depends on the API
  response) and not reachable from any fixture we have, so I left it.
* `config.py`, `response_cache.py`, `snapshot_replay.py`, `regions.py`,
  `ratio_manager.py`, `surcharge.py` and `yq_inclusion.py` all read committed
  data files with the locale's encoding. Every one of those files is pure ASCII
  today, so it is latent — but a `yq-check` record a person fills in with `£450`
  would not be, and that one is user-written.
