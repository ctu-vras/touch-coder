# Fix C1 — No atomic writes; a crash mid-save can corrupt the source-of-truth CSV

## Problem (re-verified at HEAD)

Every on-disk writer in the app serializes **directly onto the live file** — it opens the
destination path in `"w"` mode (truncating it) and then streams bytes into it. If the process
is killed, the disk fills, or the OS crashes during that window, the file is left **truncated /
half-written**. For the unified CSV — the declared source of truth for round-trips — that means
**prior annotation work becomes unloadable**.

Confirmed non-atomic writers at HEAD (anchored by symbol):

- `save_unified_dataset` — `src/data_utils.py`: builds the union `DataFrame`, then
  `df.to_csv(csv_path, index=False)` straight onto the live unified CSV (touch source of truth).
- `save_pose_dataset` — `src/pose_mismatch_data.py`: same pattern, `df.to_csv(csv_path, index=False)`
  onto the live unified CSV. **This is the currently-active annotation mode**
  (`config.json: "annotation_mode": "pose_3d"`), so it is the highest-exposure writer.
- `export_from_unified` — `src/data_utils.py`: `df.to_csv(out_csv, index=False)` onto the live
  export CSV (the file external research pipelines read).
- `export_pose_dataset` — `src/pose_mismatch_data.py`: `df.to_csv(out_csv, index=False)`.
- `write_export_metadata` — `src/data_utils.py`: `open(meta_path, "w") … json.dump`.
- `save_config` — `src/config_utils.py`: `open(config_path, "w") … json.dump`. A half-written
  `config.json` hard-crashes the *next launch* (this is the trigger for H4).
- `save_last_position` — `src/labeling_app.py`: `open(path, "w") … json.dump`.
- `_write_video_time` — `src/labeling_app.py`: `open(path, "w") … json.dump` (labeling-time
  accumulator sidecar, `data/<video>_metadata.json`).
- `save_clothes_to_text` — `src/labeling_app.py`: `open(text_file_path, "w") … f.write(...)`.

There is **no** `atomic_write` / `os.replace` / temp-file / `fsync` anywhere in `src/`
(grep for `atomic_write|os\.replace|fsync|tempfile` returns nothing).

**How it manifests.** On a long video the unified/export write takes seconds (`export_from_unified`
and `export_pose_dataset` build a row for *every* frame `0..total_frames`; H3). Any interruption in
that window truncates the target. Because `save_data` (`labeling_app.py`) writes the **unified**
CSV first and the **export** CSV second, a crash between the two also leaves the two files
mutually inconsistent — the exact hazard fix_M4 documents (unified updated, export stale/half-written).

**Why it matters for research data.** The unified CSV is the only fully round-trippable record of
an annotation session; the export CSV is what downstream analysis consumes. A truncated unified CSV
is silent, permanent annotation loss for that video; a truncated `config.json` bricks the next
launch. This is data-integrity, not cosmetics — the review ranks it the #1 fix-first item.

## How it fits the whole app — catalogue of every writer/reader the fix touches

**In-scope live writers (route through the helper):**

| Writer | Module | Target file | Format | Notes |
|---|---|---|---|---|
| `save_unified_dataset` | `data_utils.py` | `data/<v>_unified.csv` | CSV (pandas) | Touch source of truth. Early-returns without writing when no changed rows — leave that skip path untouched. |
| `save_pose_dataset` | `pose_mismatch_data.py` | `data/<v>_unified.csv` | CSV (pandas) | **Active mode**, highest exposure. Shares the read/upsert logic C4 fixes. |
| `export_from_unified` | `data_utils.py` | `export/<v>_export.csv` | CSV (pandas) | **Schema-frozen** file. |
| `export_pose_dataset` | `pose_mismatch_data.py` | `export/<v>_export.csv` | CSV (pandas) | **Schema-frozen** file. |
| `write_export_metadata` | `data_utils.py` | `export/<v>_metadata.json` | JSON | Sidecar. |
| `save_config` | `config_utils.py` | `config.json` | JSON | Corruption bricks next launch (H4). |
| `save_last_position` | `labeling_app.py` | `data/<v>_last_position.json` | JSON | Resume position. |
| `_write_video_time` | `labeling_app.py` | `data/<v>_metadata.json` | JSON | Labeling-time accumulator. Distinct path from the export metadata (different dir). |
| `save_clothes_to_text` | `labeling_app.py` | `data/<v>_clothes.txt` | plain text | Coordinates + zones. |

**Corresponding readers (must keep reading the same bytes — the helper must not change file
content, only how it lands):**

- `load_unified_dataset`, `import_unified_from_export` (`data_utils.py`) read the unified/export CSVs.
- `load_pose_dataset` (`pose_mismatch_data.py`) reads the unified pose CSV.
- `load_config` + all the `load_*` helpers (`config_utils.py`) read `config.json`.
- `restore_last_position`, `_load_video_time` (`labeling_app.py`) read the JSON sidecars.
- `extract_zones_from_file` (`data_utils.py`) reads `_clothes.txt`.
- External research pipelines read `export/<v>_export.csv` (schema-frozen).

**Legacy / dead writers (present but not on the modern save path — out of scope, note only):**
`save_dataset`, `save_parameter_to_csv`, `save_limb_parameters`, `merge_and_flip_export`,
`_prepend_header` (`data_utils.py`). They are still *imported* in `labeling_app.py` but the live
save path uses only the unified+export+metadata writers above. Routing them is trivial if they
survive the structural cleanup (§Structural in review.md item 3), but they carry no live
data-integrity risk today. Do **not** expand scope to them here.

**Orchestrator context:** `save_data` (`labeling_app.py`) is the sequence
`save_unified_dataset|save_pose_dataset → write_export_metadata → export_from_unified|export_pose_dataset`,
then clears `Changed` flags. Per-file atomicity (this fix) makes each individual file crash-safe.
Cross-file all-or-nothing consistency across that sequence is a *separate, larger* concern — see
Approach C.

## Approaches considered

**A. One `atomic_write` helper in a new dependency-free module; route every live writer through it.
(RECOMMENDED.)**
Write to a sibling temp file (`<path>.tmp` in the same directory → same volume, so `os.replace` is
atomic on both Windows and POSIX), `flush()` + `os.fsync()` the handle, close, then `os.replace(tmp, path)`.
On any exception before the `os.replace`, the temp file is discarded and the **previous good file is
untouched**. A single callback-style helper covers both `df.to_csv(f)` and `json.dump(obj, f)` because
both accept a file object. Optional: keep one `<path>.bak` of the unified CSV per successful save for
a manual recovery point (review explicitly suggests this).
*Chosen:* smallest surface that fully removes the truncation risk, one code path to reason about, zero
schema impact, and it composes cleanly with C4/H3/H4.

**B. Inline temp-file + `os.replace` at each call site.**
Same mechanics, duplicated 9 times. Rejected: guaranteed drift (some writer will forget the `fsync`
or the cleanup), and it violates the project's DRY/observability posture. The helper in A is the
same code written once.

**C. Step-back — introduce a transactional `SaveManager` persistence layer.**
The review's structural section flags `save_data` as doing multi-file save orchestration inline. A
`SaveManager` could stage *all* of a save's files to temp and `os.replace` them as a batch, giving
**cross-file** consistency (never "unified new + export stale"). This is genuinely more correct for
the fix_M4 hazard, but it is a real refactor: it needs a staging protocol, rollback of already-replaced
files, and it entangles with the H3 off-thread-save work and the mode-controller extraction. Rejected
**for now** as over-scoped for C1. **It becomes worth doing when** either (a) the H3 "run export off
the UI thread with progress" work lands (that touch point is the natural home for staging), or (b) a
real incident shows unified/export divergence after A is in place. Until then, A's per-file atomicity
plus the optional `.bak` covers the realistic failure modes (kill/crash/disk-full mid-single-write).

## Recommended implementation

1. **New module `src/atomic_io.py`** (stdlib only — no project imports, so `config_utils`,
   `data_utils`, `pose_mismatch_data`, and `labeling_app` can all import it without cycles):

   ```python
   import os

   def atomic_write(path, write_fn, *, encoding="utf-8", newline=""):
       """Atomically (re)write `path`. `write_fn(f)` streams into a temp file;
       on success the temp is os.replace()d onto `path`. On ANY exception the
       original file is left untouched. `newline=""` matches pandas' own file
       handling so CSV bytes are identical to a direct df.to_csv(path)."""
       directory = os.path.dirname(path) or "."
       os.makedirs(directory, exist_ok=True)
       tmp = path + ".tmp"
       try:
           with open(tmp, "w", encoding=encoding, newline=newline) as f:
               write_fn(f)
               f.flush()
               os.fsync(f.fileno())
           os.replace(tmp, path)          # atomic on same volume (Win + POSIX)
       except Exception:
           try:
               if os.path.exists(tmp):
                   os.remove(tmp)          # best-effort cleanup; never mask original error
           except OSError as cleanup_err:
               print(f"WARN: atomic_write temp cleanup failed for {tmp}: {cleanup_err}")
           raise
   ```

   Add a `.bak`-keeping variant *or* an optional `keep_backup=False` flag used only by the two
   unified writers: before `os.replace`, if `path` exists, copy it to `path + ".bak"`. Keep it
   opt-in so exports/config don't accumulate backups.

2. **Route the CSV writers.** Replace `df.to_csv(<path>, index=False)` with:

   ```python
   from atomic_io import atomic_write
   atomic_write(csv_path, lambda f: df.to_csv(f, index=False))
   ```

   in `save_unified_dataset`, `save_pose_dataset` (with `keep_backup=True`), `export_from_unified`,
   `export_pose_dataset`. Keep the existing `os.makedirs(...)` calls or rely on the helper's; do not
   remove the early `if changed_only and not changed_rows: return` skip in the unified writers — that
   path must still leave the previous file in place.

3. **Route the JSON writers.** Replace `with open(path, "w") … json.dump(obj, f)` with:

   ```python
   atomic_write(path, lambda f: json.dump(obj, f, indent=2, ensure_ascii=False))
   ```

   in `write_export_metadata`, `save_config`, `save_last_position`, `_write_video_time`. Note
   `save_config` currently opens without `encoding=` — the helper standardizes it to UTF-8, which is
   an improvement consistent with M6 and does not change ASCII configs.

4. **Route the text writer.** In `save_clothes_to_text`, move the body into
   `atomic_write(text_file_path, lambda f: (_write_clothes_lines(f)))`.

5. **Observability (project rule 0).** The helper re-raises on failure so callers already logging in
   a `try/except` (`_write_video_time`, `save_last_position`) still log. For the writers that don't
   currently guard (`save_config`, the exporters), leave the exception propagating — a failed *atomic*
   write is loud and the old file is intact, which is the correct, non-silent behavior. Do not swallow.

**Behavioral contract after the change:**
- Successful save: byte-for-byte identical file content to today (same columns, same order, same CSV
  formatting — `newline=""` matches pandas' path-mode writer). Only the *mechanism* (temp → fsync →
  replace) changes.
- Interrupted/failed save (exception, kill, disk-full during the write): the destination retains its
  **previous complete** contents; a stale `<path>.tmp` may remain but is never read by any loader and
  is overwritten on the next save.
- `.bak` (unified writers only, if enabled): one previous generation of the unified CSV is retained.

**Export schema impact: NONE.** No export column is added, removed, renamed, or reordered — the fix
changes only how the bytes are committed to disk, not their content. Guaranteed by
`tests/test_export_schema.py` (both touch and pose schema-lock tests must stay green).

## Edge cases & failure modes

- **Same-volume requirement.** `<path>.tmp` sits in the same directory as `path`, so `os.replace`
  is a same-filesystem rename (atomic). Do not put the temp in `%TEMP%`/`tmp_path` of another volume.
- **Windows file lock on `os.replace`.** If the destination is open in another handle (e.g. the user
  has the CSV open in Excel), `os.replace` raises `PermissionError`; the original is still intact and
  the exception is surfaced. Acceptable — same as today it would fail, but now non-destructively.
- **`os.fsync` cost on large files.** Adds one flush-to-disk per save. On a 300k-frame export this is
  a few hundred ms; acceptable for the safety guarantee and subsumed by the H3 off-thread-save work.
  Do not fsync more than once per file.
- **Directory durability (POSIX).** After `os.replace`, a crash could still lose the rename before the
  directory entry is flushed. fsync-ing the *directory* is the POSIX-only hardening; it is a no-op/
  unsupported on Windows (the primary target). Leave it out; note as a possible follow-up.
- **Skip-write path.** `save_unified_dataset`/`save_pose_dataset` return early when there are no
  changed rows; the helper is not invoked, so the previous file is preserved exactly as today.
- **Two writers, one `_metadata.json` name.** `write_export_metadata` writes under `export/` and
  `_write_video_time` under `data/` — different paths, no collision, no shared temp.
- **Empty union.** If the union is empty but a write is still performed, `df.to_csv(f)` on an empty
  frame still writes the header row — unchanged from today.

## Testing / verification plan

**(a) Automatable red/green (pytest, black-box, `tmp_path` only — never `Labeled_data/`).**

New file `tests/test_atomic_write.py`. Primary test drives the real writer so it exercises the
routing, not just the helper:

```python
import os, pytest, pandas as pd
from data_utils import save_unified_dataset, empty_bundle

def _one_changed(frame):
    b = empty_bundle(); b["Changed"] = True; return {frame: b}

def test_C1_unified_save_preserves_prior_file_on_write_crash(tmp_path, monkeypatch):
    csv_path = str(tmp_path / "data" / "vid_unified.csv")
    # 1) baseline good save (real to_csv)
    save_unified_dataset(csv_path, total_frames=3, frames=_one_changed(1))
    good = open(csv_path, "rb").read()

    # 2) inject a write that corrupts its target then crashes
    orig = pd.DataFrame.to_csv
    def boom(self, path_or_buf=None, *a, **k):
        target = path_or_buf
        if hasattr(target, "write"):
            target.write("CORRUPT")          # -> lands in .tmp under atomic code
        else:
            open(target, "w").write("CORRUPT")  # -> corrupts live file under current code
        raise IOError("simulated disk-full mid-write")
    monkeypatch.setattr(pd.DataFrame, "to_csv", boom)

    with pytest.raises(IOError):
        save_unified_dataset(csv_path, total_frames=3, frames=_one_changed(2))

    # RED (current): live file was truncated to "CORRUPT".
    # GREEN (after): corruption went to .tmp; original bytes intact.
    assert open(csv_path, "rb").read() == good
    assert not os.path.exists(csv_path + ".tmp")
```

Plus a direct helper unit test (`test_C1_atomic_write_rolls_back_on_exception`) asserting a raising
`write_fn` leaves a pre-existing file unchanged and removes the temp.

Commands:
```
uv run pytest tests/ -k C1 -v      # RED before, GREEN after
uv run pytest tests/ -k schema -v  # MUST stay green (proves zero schema drift)
uv run pytest tests/ -v            # full suite (M4 + schema + C1) stays green
```
- **Red before:** on current code `boom` writes `"CORRUPT"` directly to `csv_path` (path arg) then
  raises → the source-of-truth file is destroyed → the `== good` assertion fails.
- **Green after:** `save_unified_dataset` calls `df.to_csv(f)` with the temp-file handle → `"CORRUPT"`
  lands in `.tmp`, the exception skips `os.replace`, the original file is intact → passes.

**(b) Manual checklist (GUI-side, `uv run python src/main.py` — no daemon to restart).**
1. Load a video in **3D mismatch** mode (the active mode), make a few edits, hit **Save**. Confirm the
   console shows the save completing and `data/<v>_unified.csv` + `export/<v>_export.csv` are valid.
2. Reload the video; confirm all annotations round-trip (proves byte-identical content).
3. Repeat in **Touch** mode.
4. Corruption-resilience spot check (optional): while a *large* video is saving, note that a mid-save
   kill now leaves the prior `_unified.csv` loadable (previously it could be truncated). Confirm no
   `.tmp` file is read on the next load.
5. Edit **Settings → Apply** (writes `config.json`) and relaunch; confirm the app starts (proves
   config writes are atomic; complements H4).

## Interactions with other planned fixes

- **C4** (`save_pose_dataset` silently drops prior rows on a read error): same function. C1 makes the
  *write* atomic; C4 fixes the *read/upsert* so a parse failure aborts instead of dropping history.
  Complementary and additive — the C1 `.bak` of the unified CSV also gives C4 a concrete recovery
  artifact. **Ordering:** land C1's helper first (infra), then C4 on top; or independently — no code
  conflict beyond both editing `save_pose_dataset`.
- **H3** (saves are full re-read + full rewrite; move export off the UI thread): C1 adds one temp
  write + one `fsync` per save. When H3 moves the export off-thread with progress, that thread is the
  natural home for the eventual transactional staging (Approach C). No conflict; sequence C1 before H3.
- **H4** (config loaders crash on corrupt `config.json`): C1 removes the *cause* (half-written config)
  by making `save_config` atomic; H4 adds defensive *loading*. Land C1 first — it shrinks H4's trigger
  surface to genuinely externally-corrupted files.
- **M4** (touch export ZeroDivisionError on 0-fps): related failure geometry — M4 stops the export from
  crashing mid-write; C1 ensures that *if* any writer still crashes, the previous export/unified file
  survives. Independent one-liner; either order.
- **M6** (missing `encoding="utf-8"`): C1's helper standardizes UTF-8 on the writers it routes,
  partially overlapping M6 on the write side. No conflict.

## Effort estimate & risk

- **Effort:** ~1.5–2 h. New `atomic_io.py` (~30 lines) + routing 9 call sites + two tests. Straight
  mechanical change once the helper exists.
- **Risk:** Low–Medium. The one real risk is CSV byte-formatting drift when switching `to_csv(path)` →
  `to_csv(file_object)`; mitigated by opening the temp with `newline=""` (matches pandas' own path-mode
  handling) and guarded by the schema-lock + round-trip manual check. `os.replace` semantics are
  identical on Windows and POSIX for same-volume renames.
- **Rollback:** revert `atomic_io.py` and the per-writer one-line edits; no on-disk format changed, so
  no data migration needed either way.
- **On-disk shape change:** none for committed files. The only new artifacts are transient `<path>.tmp`
  (auto-cleaned / overwritten) and the optional `<path>.bak`. No RESET procedure required. (If the
  `.bak` option is dropped later, deleting stray `*.bak`/`*.tmp` under `Labeled_data/` is safe.)
- **Operational footprint:** code-only. **No version bump.** No container/daemon. Verify via
  `uv run pytest`; relaunch `uv run python src/main.py` only for the manual checklist.
