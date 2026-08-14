# Fix Implementation Handoff (TinyTouch)

You are implementing **one fix** in this repository. Alongside these instructions you were
given a **fix plan file** (`fix_<ID>.md`, from `docs/reviews/<date>/to_do/`). That plan is
your spec. These instructions define *how to work*; the plan defines *what to build*.

This is the desktop-app counterpart of the Docker-stack handoffs used in other projects —
there is **no container, no `CODE_REVISION`, no HTTP server**. TinyTouch is a single-process
Tkinter app you launch by hand. Adjust expectations accordingly.

## Read before writing any code, in this order

1. **`CLAUDE.md`** (repo root) — the working rules. Especially:
   - Python runs through **uv** only (`uv run python …`, `uv run pytest …`).
   - No CLIs/argparse (rule 3) — config file (`config.json`) or top-of-file globals.
   - Observability first (rule 0) — no silent failures; log every meaningful action with
     context. This is frequently what the review findings are *about*.
2. **`PROJECT.md`** — architecture, data model (`FrameBundle` / pose bundle), on-disk layout,
   the two modes, the save/export/recovery pipeline, the background threads.
3. **The fix plan file you were given** — problem, chosen approach, steps, edge cases,
   testing plan, interactions.
4. **The finding's section in `docs/reviews/<date>/review.md`** for surrounding context.

## Contract

- Implement the plan's **"Recommended implementation"** as written. The alternatives under
  **"Approaches considered"** were already evaluated and **rejected** — do not re-decide.
  If you find something that genuinely invalidates the approach, **stop and report back**;
  do not silently improvise a different design.
- Implement **only this fix**. Neighbouring findings have their own plans; don't fix them
  opportunistically, even trivial ones.
- The plan's line numbers reference the commit in the review header — code may have drifted.
  Re-anchor by **symbol names**, not line numbers, and note every divergence.
- Match the style, naming, and idiom of the surrounding code. No drive-by refactors, no
  reformatting of untouched code.

## Verification (red/green + manual)

The app splits cleanly into a **testable pure-function core** (I/O, parsing, data model in
`data_utils.py`, `pose_mismatch_data.py`, `config_utils.py`,
`frame_utils.check_items_count`) and a **GUI shell** (Tkinter callbacks, threads, canvas
rendering). Each finding's plan says which half it lives in.

- **Automatable half — red/green with pytest.** If the plan specifies a red test, write it
  in `tests/`, confirm it is **red before** your change and **green after**:
  ```
  uv run pytest tests/ -k <id>          # e.g. -k C3
  ```
  Tests are black-box: build inputs in a `tmp_path`, call the function, assert on the
  returned dict / written file. **Never** touch the real `data/` tree (nor a legacy `Labeled_data/` one) — use
  `tmp_path`. Never weaken a test to make it pass.
- **Manual half — checklist against the running app.** GUI-only behaviour (key bindings,
  canvas repaint, thread timing, dialogs) is verified by hand. Launch the app and walk the
  plan's manual checklist, recording what you observed:
  ```
  uv run python src/main.py
  ```
  There is no daemon to restart — just relaunch the app after a code change.

## Versioning

Do **not** bump `program_version` for a fix. Version bumps happen **only at release time**,
per [docs/RELEASING.md](RELEASING.md) (`src/video_model.py` `program_version` + a `v*` git
tag). A fix leaves the version untouched.

## Boundaries

- **The export CSV schema is FROZEN.** External research pipelines consume
  `export/<video>_export.csv` (and the 3D `_3d` variant) by column name and order. You may
  **not** add, remove, rename, or reorder any export column, or change a column's value
  encoding, unless the plan's **"Export schema impact"** section explicitly authorises it
  (which requires coordinating the downstream pipeline). The schema-lock tests
  (`tests/test_export_schema.py`) pin the current columns; if your change turns them red, you
  have broken the contract — stop. A finding that *looks* like it wants a new column (e.g. M3
  gaze/`_Look`) is resolved by removing dead code, not by extending the schema.
- Do **not** commit or push — leave all changes in the working tree. (End with a one-line
  commit suggestion; don't act on it.)
- Do **not** modify anything under `data/` (real research data; older working copies may still have it as `Labeled_data/`) or the shipped
  `src/resources/icons/` masks/diagrams. Tests create their own throwaway inputs under `tmp_path`.
- Do **not** edit the content of `review.md` or the fix plan — they are immutable snapshots.
  (Moving the plan to `done/` and updating link *paths* is the one exception.)

## Deliverable

1. The code change (working tree only).
2. Any new tests in `tests/`, left in place as regression guards.
3. `TODO.md` → `DONE.md`: move the finding's one-liner into `DONE.md` under the matching
   section with a `**Completed:** YYYY-MM-DD — <outcome>` line prepended. Partial work stays
   in `TODO.md`.
4. Move the plan file `to_do/` → `done/`, and update link paths that point at it (the
   `DONE.md`/`TODO.md` entry and the `> **Fix plan:**` line in `review.md`). Paths only —
   never change plan content. Skip for a partial fix.
5. A short final report:
   - What changed — files, functions, one-paragraph mechanism summary.
   - **Every deviation from the plan** and why.
   - Verification results with real output (pytest run showing red→green; app/log
     observations for the manual half), mapped to the plan's checklist.
   - **Export schema impact:** state explicitly `none` (and confirm
     `tests/test_export_schema.py` is still green), or — only if the plan authorised it —
     describe exactly which columns changed and how the downstream pipeline was coordinated.
