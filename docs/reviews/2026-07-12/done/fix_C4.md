# Fix C4 — `save_pose_dataset` silently discards all prior rows when the existing unified CSV can't be parsed

## Problem (re-verified at HEAD)

`pose_mismatch_data.save_pose_dataset` does a **changed-only upsert**: it re-reads the
existing on-disk unified pose CSV into `existing_map`, overlays this session's changed
rows, and writes the sorted union back. The re-read is wrapped in a bare catch
(`pose_mismatch_data.py:169-190`):

```python
    if os.path.exists(csv_path) and os.path.getsize(csv_path) > 0:
        try:
            existing_df = pd.read_csv(csv_path)
            for _, row in existing_df.iterrows():
                ...
                existing_map[frame] = { ... }
        except Exception:
            existing_map = {}          # <-- silent: no log, discards ALL prior rows
```

Then (`:216-237`):

```python
    if changed_only and not changed_rows:
        return
    for row in changed_rows:
        existing_map[row["Frame"]] = row
    ...
    out_rows = [existing_map[k] for k in sorted(existing_map.keys())]
    df = pd.DataFrame(out_rows, columns=cols)
    df.to_csv(csv_path, index=False)       # overwrites the live file
```

So if the existing CSV fails to parse for *any* reason, `existing_map` is reset to `{}`,
the union collapses to **only this session's changed frames**, and `to_csv` overwrites the
source-of-truth file with that strictly-smaller set. **Every previously-saved frame is
gone.** The failure is also completely invisible: no `print`, no re-raise — a direct
violation of CLAUDE.md rule 0 ("No silent failures — always log errors, even in
best-effort/catch blocks").

This is the **currently-active annotation mode** (`config.json: "annotation_mode":
"pose_3d"`), so exposure is high — this is the save path every 3D session runs.

Contrast with the touch writer `data_utils.save_unified_dataset` (`:200-220`), which
catches **only** `pd.errors.EmptyDataError` (the benign "no data rows" case) and lets any
genuine parse error propagate — so touch mode does not silently overwrite good data with a
smaller set. The pose writer's broad `except Exception` is the specific defect.

## How it fits the whole app

- **Full save path:** `LabelingApp.save_data` (`labeling_app.py:3095`) → in pose mode calls
  `save_pose_dataset(unified_path, total_frames, self.video.frames)` (`:3117`) **first**,
  then `write_export_metadata` (`:3144`), then `export_pose_dataset` (`:3157`), then clears
  every bundle's `Changed` flag (`:3177-3180`). The unified CSV is the declared source of
  truth; the export is a full snapshot rebuilt from in-memory `frames` each save.
- **When the existing file is read:** only inside `save_pose_dataset` (the incremental
  merge). `export_pose_dataset` never reads existing data — it rebuilds every frame from
  memory — so it is unaffected. `load_pose_dataset` (`:101-159`) is the load path and is
  *not* the bug (it already logs and returns empty on read failure, `:109-111`).
- **How a corrupt/partial file reaches the catch (compounds with C1):** there are no atomic
  writes anywhere yet (finding C1). A `to_csv` that is interrupted mid-write (app killed,
  disk full, OS crash) leaves a truncated/ragged unified CSV on disk. On the *next* save,
  `save_pose_dataset` tries to read that partial file, `pd.read_csv` raises, and C4's silent
  catch then overwrites whatever survived with just the current session's changed rows.
  C1 creates the corrupt file; C4 turns it into total history loss. They are a data-loss
  amplifier pair.
- **Flag-clear coupling (important):** whatever contract we pick, `save_data` must NOT reach
  the `Changed`-flag clear (`:3177-3180`) on a failed unified write. If the pose save
  quietly returns without writing, `save_data` would still clear the flags → the session's
  edits are marked clean but were never persisted to the source of truth → silent loss on a
  different axis. The fix must therefore *signal* failure so the caller aborts before
  clearing flags.

## Approaches considered

**A. Log the exception + ABORT the save on a genuine read failure (recommended).**
Narrow the catch: treat `pd.errors.EmptyDataError` as the benign "no prior rows" case
(proceed with an empty map — nothing to lose), and on any *other* parse error log loudly
(rule 0) and **raise a dedicated exception** instead of overwriting. The live file is left
byte-for-byte untouched; `save_data` catches the exception, surfaces it, and returns early
so the `Changed` flags stay set and the corrupt file is never replaced with a smaller set.
The annotator can restore/repair the file and re-save — all dirty frames re-persist because
their flags were never cleared. **Chosen.** It is the honest contract: we cannot reliably
*recover* rows from an unparseable CSV, but we can guarantee we never *destroy* the good
rows still on disk, and the failure is now observable.

**B. Write this session's rows to a NEW timestamped file, preserve the old.**
On read failure, dump `changed_rows` to `<video>_unified.recovered-<ts>.csv`, leave the
original untouched, log loudly. Loses nothing — both the (possibly corrupt) original and the
new work survive. **Rejected as the core contract** (kept as an optional add-on): the load
path has no knowledge of the timestamped file, so it needs manual reconciliation anyway; it
adds on-disk clutter and merge complexity for a rare path. The same in-memory session data
is already preserved by Approach A (flags stay dirty; a retry re-saves), so B's extra file
buys little over A. B's idea overlaps with C1's proposed per-save `.bak`, which is the
better home for "keep a recoverable copy".

**C. Never overwrite when the union is strictly smaller than what's on disk.**
A size guard: refuse to write if `len(union_rows) < prior_row_count`. **Rejected as
primary:** on a read failure we cannot count the prior rows (that read is exactly what
failed), so it does not address this finding; it is a useful independent belt-and-braces for
*successful* reads and can be folded in later, but it is orthogonal to the parse-failure case.

## Recommended implementation

Add a dedicated exception and narrow the catch in `pose_mismatch_data.save_pose_dataset`
(`:169-190`) — smallest faithful diff:

```python
class PoseUnifiedReadError(RuntimeError):
    """Existing 3D unified CSV exists but is unparseable, so the incremental save
    cannot safely merge prior rows. Raised (instead of overwriting) to protect the
    already-saved history from being replaced with only this session's changes."""


# inside save_pose_dataset, replacing the bare try/except:
    if os.path.exists(csv_path) and os.path.getsize(csv_path) > 0:
        try:
            existing_df = pd.read_csv(csv_path)
            for _, row in existing_df.iterrows():
                try:
                    frame = int(row["Frame"])
                except Exception:
                    continue
                existing_map[frame] = { ... }          # unchanged mapping
        except pd.errors.EmptyDataError:
            # Header-only / no data rows: genuinely nothing to preserve. Safe to proceed.
            print(f"DEBUG: 3D unified has no data rows (EmptyDataError) → {csv_path}; "
                  f"treating existing as empty.")
        except Exception as e:
            # Genuine parse failure (e.g. a truncated file from an interrupted save, C1).
            # Do NOT overwrite: writing now would drop every prior frame.
            print(f"ERROR: Failed to read existing 3D unified CSV → {csv_path}: {e}. "
                  f"Aborting save to avoid overwriting previously-saved rows.")
            raise PoseUnifiedReadError(csv_path) from e
```

Recommended caller wiring in `LabelingApp.save_data` (`labeling_app.py:3116-3117`) so the
failure is observable to the annotator and the flag-clear/export steps are skipped:

```python
    if self.is_pose_mode():
        try:
            save_pose_dataset(unified_path, self.video.total_frames, self.video.frames)
        except PoseUnifiedReadError:
            traceback.print_exc()
            messagebox.showerror(
                "Save aborted",
                "The existing 3D data file could not be read and was NOT overwritten, "
                "so no annotations were lost. Please check the file, then save again.",
            )
            return   # abort save_data → Changed flags stay set; export not rewritten
        clothes_list = None
```

(`import PoseUnifiedReadError` alongside the existing pose imports at `labeling_app.py:43-45`.)

**Behavioural contract after the change:**
- Existing file parses fine → identical behaviour to today (full union written).
- Existing file is empty/header-only (`EmptyDataError`) → proceeds with empty map, logs at
  DEBUG; unchanged from today's effective outcome but now observable.
- Existing file is present but unparseable → **logs ERROR, raises `PoseUnifiedReadError`,
  writes nothing.** On-disk file untouched; in-memory `Changed` flags preserved; annotator
  notified.
- **When the fix lands after C1:** route the final `to_csv` through C1's `atomic_write`
  helper (write-temp → fsync → `os.replace`) so the abort-vs-overwrite guarantee is backed
  by a write that can't itself half-truncate the file.

## Edge cases & failure modes

- **0-byte file:** already short-circuited by the `getsize(...) > 0` guard before the read;
  never reaches the catch. Unchanged.
- **Header-only file (non-zero size, no data rows):** `pd.read_csv` raises
  `EmptyDataError` → treated as empty → save proceeds. This is the *only* "empty-like" case
  that should proceed silently-safe; it genuinely has no rows to lose.
- **Ragged / truncated file (the C1 case):** `pd.read_csv` raises `ParserError` (or similar)
  → abort + raise. Correct: this is exactly when we must not overwrite.
- **Close path (`on_close` → `save_data`):** raising surfaces the error on close instead of
  silently losing history; with the caller wiring it becomes a messagebox + early return.
  Preferable to silent loss. (Note the separate M9 close-ordering finding — independent.)
- **Row-level bad `Frame` cell:** still individually skipped by the inner
  `try: frame = int(row["Frame"])` — not escalated to an abort. Unchanged.

## Export schema impact

**NONE.** This fix touches only the *unified* (working/round-trip) pose CSV writer
`save_pose_dataset` and, optionally, the caller in `save_data`. It does not touch
`export_pose_dataset`, the export column set, or the unified column list (`cols` at
`pose_mismatch_data.py:222-233` is unchanged). No column is added/removed/renamed/reordered
in either the export or the unified CSV. The frozen export schema is unaffected and remains
guaranteed by `tests/test_export_schema.py::test_pose_export_schema_is_frozen`.

## Testing / verification plan

**(a) Automatable red/green (pytest, `tmp_path`).**
New file `tests/test_pose_save_no_silent_loss.py::test_C4_pose_save_preserves_prior_rows_on_read_error`:

1. Create a valid existing unified pose CSV by calling `save_pose_dataset` once with a few
   changed frames (e.g. frames 0,1,2). On a fresh path the file doesn't exist yet, so this
   first save never calls `read_csv` — no interference. Assert the file now has 3 data rows.
2. Snapshot the file's exact bytes (`before = path.read_bytes()`).
3. Force a read failure deterministically:
   `monkeypatch.setattr(pose_mismatch_data.pd, "read_csv", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))`.
   Build a new `frames` dict with a single *different* changed frame (e.g. frame 5).
4. Call `save_pose_dataset` again and assert the honest "logs + refuses to overwrite"
   contract:
   - `with pytest.raises(pose_mismatch_data.PoseUnifiedReadError): save_pose_dataset(...)`
     — proves the read failure is **not silently swallowed**.
   - `assert path.read_bytes() == before` — proves prior rows were **not** overwritten with
     the strictly-smaller set (this is the core data-safety assertion; comparing raw bytes
     avoids needing the monkeypatched `read_csv` for verification).
   - Optionally `capsys.readouterr().out` contains `"ERROR"` and the path — proves rule-0
     logging.

Honest framing: the assertion is **"prior data preserved + failure signalled"**, not
"corrupt data recovered". Recovering rows from an unparseable CSV is not reliably possible;
the testable guarantee is that we never destroy the good rows still on disk and never fail
silently.

Expected transition:
- **RED (current code):** the bare `except Exception: existing_map = {}` swallows the error,
  writes only frame 5 → `pytest.raises(PoseUnifiedReadError)` fails (nothing raised) **and**
  the byte-snapshot assert fails (file rewritten to 1 row). (`PoseUnifiedReadError` also
  doesn't exist yet → collection-time red.)
- **GREEN (after fix):** raises `PoseUnifiedReadError`, file bytes unchanged, ERROR logged.

Commands:
```
uv run pytest tests/ -k C4 -v          # red → green
uv run pytest tests/ -k schema -v      # must stay green (proves no schema drift)
```

**(b) Manual notes.** In the active 3D mode: load a video that already has saved
annotations, then corrupt the `data/<video>_3d_unified.csv` (e.g. truncate it mid-row or
insert ragged content), edit one frame, and press Save. Confirm: (1) the console logs the
read error, (2) the save aborts (messagebox if the caller wiring is applied) and no rows are
lost — the on-disk file still holds the prior rows, (3) restoring/repairing the file and
saving again re-persists the session's edits (because `Changed` flags were never cleared).

## Interactions with other planned fixes

- **C1 (atomic writes) — lands FIRST; they compound.** C1 removes the *cause* of the corrupt
  partial file (interrupted `to_csv`), so the C4 read failure becomes rare; C4 is
  defense-in-depth for when a file is corrupt anyway (external damage, disk error). Once C1
  exists, C4's `to_csv` should be routed through C1's `atomic_write` and can lean on C1's
  proposed `.bak` as the recovery source. Recommended order: **C1 → C4.** C4 is small and
  independent enough to also land before C1 if needed, but the atomic-write wiring note
  above should then be revisited when C1 arrives.
- **H3 ("incremental" save rewrite) — must preserve C4's guarantee; C4 lands BEFORE H3.**
  H3 will rewrite exactly the read-existing/merge block that C4 hardens (moving toward a true
  append/patch instead of full re-read + rewrite). Whatever H3 does, it must carry forward
  C4's contract: never silently drop history on a read failure. Landing C4 first pins that
  contract (and its test) so H3's rewrite is checked against it.
- **M8 (`load_pose_dataset` uses `iterrows`) / M11 (ScaleFactor not clamped on load):** same
  file, load path; independent of C4, no conflict.
- **Consistency note (optional, out of scope):** the touch writer `save_unified_dataset`
  catches only `EmptyDataError` — safer than the pose writer but it would *crash* (not
  overwrite) on a genuine parse error. Aligning it to the same log-and-raise contract would
  make both writers consistent, but that is a separate touch-mode change, not part of C4.

## Effort estimate & risk

- **Effort:** ~20-30 min (add exception + narrow the catch in `pose_mismatch_data.py`, wire
  the caller in `save_data`, write one test).
- **Risk:** Low. The happy path (existing file parses) is byte-identical to today; only the
  previously-silent failure path changes behaviour (now aborts+logs instead of overwriting).
  The main behavioural shift is that a genuinely unparseable existing file now blocks the
  save loudly — which is the intended safety trade (fail visibly, lose nothing).
- **Rollback:** revert the catch change + the caller try/except + delete the test.
- **Operational footprint:** code-only, **no version bump**; relaunch the app
  (`uv run python src/main.py`) to verify manually. This is the **active** annotation mode
  (`annotation_mode = pose_3d`), so prioritise it alongside C1 — it is on the hot save path
  of every current 3D session.
