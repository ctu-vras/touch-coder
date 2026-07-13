# Fix H3 — "Incremental" unified saves re-read + rewrite the whole file every time

## 1. Problem (re-verified at HEAD)

The "changed-only upsert" framing is misleading: every Save re-reads and rewrites the
entire on-disk state, and the export is rebuilt for every frame.

**Unified writer — `data_utils.save_unified_dataset` (`data_utils.py:153-234`).**
When *any* frame is dirty it:
1. `pd.read_csv(csv_path)` the whole existing unified file (`:204`), then walks it with
   `existing_df.iterrows()` (`:205-218`) — the exact slow path M8 flags elsewhere; `iterrows`
   builds a fresh `Series` per row.
2. Upserts the changed rows into that map (`:222-224`).
3. Rewrites the **full union** via `df.to_csv` (`:227-231`).

So the cost of persisting a single edited frame is `O(rows already on disk)` for the read +
`O(union)` for the rewrite — it grows with how much of the video has been labelled, not with
how much changed.

**Pose writer — `pose_mismatch_data.save_pose_dataset` (`pose_mismatch_data.py:162-237`).**
Identical shape: `pd.read_csv` + `iterrows()` (`:171-188`) then full-union `to_csv`
(`:234-237`). (Its `except: existing_map = {}` at `:189-190` is the separate C4 bug —
a read error silently discards every prior row.)

**Exporters — `export_from_unified` (`data_utils.py:497-558`) and `export_pose_dataset`
(`pose_mismatch_data.py:240+`).** Both loop `for f in range(total_frames + 1)`, build a dict
for *every* frame (even empty ones), and `to_csv` the whole thing. Genuinely `O(total_frames)`.

**Fan-out — `save_data` (`labeling_app.py:3095-3180`).** Calls one unified writer + one
exporter + the metadata sidecar, all synchronously on the Tk UI thread. `save_data` fires from:
the **Save** button (`ui_components.py:137`), **`load_video`** before opening a new clip
(`labeling_app.py:3275`), **`on_close`** (`:3629`), **`analysis`** (`:3188`), and
**`sort_frames`** (`:3222`). So on a long, heavily-labelled clip the read+iterrows+rewrite
stall happens on ordinary navigation-to-a-new-video and on close, not just on an explicit Save.

**Confirmed reproducing at HEAD** by all five symbol names above.

## 2. How it fits the whole app

Two on-disk artifacts, two very different contracts (PROJECT.md "Data Layout on Disk"):

- **Unified CSV** (`data/<video>_unified.csv`) — the round-trip store. Only frames that have
  ever been touched need to be present; the loader (`load_unified_dataset`,
  `load_pose_dataset`) reconstructs the in-memory `frames` dict from it. **This file can be
  patched** — its layout is ours to change.
- **Export CSV** (`export/<video>_export.csv`) — a *full snapshot*, one row per frame
  `0..total`, read by Analysis and external research pipelines. **Schema is FROZEN**
  (locked by `tests/test_export_schema.py`). Because every frame must be present and columns
  are fixed, a full rebuild is intrinsic to what the file *is*; the only lever is *where* it
  runs (thread) and *how often*.

Key observation that unlocks the unified fix: the in-memory `frames` dict is authoritative
and is always a **superset** of the unified file's contents (the loader populates `frames`
from every on-disk row at load; edits only ever add/mutate entries and set `Changed=True`).
Therefore the writer never needs to read the disk back to know the union — the disk read is
pure redundancy that also happens to be the slowest line in the function.

## 3. Approaches considered

**A. In-memory-map rewrite (drop the read-back, keep full rewrite).** Stop `pd.read_csv`-ing;
the app tracks the persisted union in memory and writes it from memory each save. Kills the
50–100× `iterrows` read but still `to_csv`s the whole union every time → still `O(union)`
writes. Simple, layout-identical, but does *not* satisfy "untouched rows preserved **without a
full rewrite**". **Rejected** as the primary (kept as the fallback if append proves fiddly).

**B. Append-only unified journal + last-writer-wins load (recommended).** The unified writer
**appends** only the dirty frames' rows to the CSV (header written once for a new file); it
never reads or rewrites existing rows. The loader already does `frames[f] = {...}` in file
order (`data_utils.py:304`, `pose_mismatch_data.py:158`), so **duplicate `Frame` rows resolve
last-wins for free** — re-editing a frame just appends a newer row that overrides the older on
load. A bounded **compaction** (rewrite-deduped, via C1's atomic writer) runs only when the
duplicate ratio crosses a threshold, keeping file size in check. Save cost becomes
`O(changed frames)`, independent of clip length or how much is already labelled. **Chosen** —
it is the only option that makes the per-save cost truly proportional to the edit, and it is
directly testable ("save with `pd.read_csv` disabled still works").

**C. On-disk index / per-frame files / SQLite.** A real embedded store or one file per changed
frame. Genuinely incremental, but a large layout change, more moving parts, new failure modes,
and heavy relative to a single-user desktop annotator. **Rejected** — disproportionate.

**D. Export: leave full, move off the UI thread with progress (adopt alongside B).** The export
stays a full snapshot (schema frozen) but its build+write runs in a worker thread; progress is
surfaced by reusing the existing data-progress window pattern, marshalled to the UI thread via
`self.after(...)` (never touching Tk from the worker, so it stays compatible with H1). This is
the finding's own recommendation for the part that "may be unavoidable". Adopted as the
export-side half of the fix, but **sequenced after** the unified change (see §9) because it is
where the H1 entanglement lives.

## 4. Recommended implementation

Two independent, separately-landable changes. Land **B** first (biggest per-save win, no Tk),
then **D**.

**B — Append-only unified (touch + pose), no read-back.**

1. `save_unified_dataset` / `save_pose_dataset`: delete the `pd.read_csv` + `iterrows` +
   `existing_map` union block. Build `changed_rows` exactly as today (same columns, same JSON
   encoding, same order), then:
   - If the file does not exist (or is 0 bytes): write header + rows (current behaviour for a
     fresh file).
   - Else: **append** the changed rows with `header=False` (`df.to_csv(path, mode="a",
     header=False, index=False)`), `flush()` + `os.fsync()` for durability.
   - `changed_only and not changed_rows` → still a no-op (unchanged).
2. Loader: no correctness change needed — `frames[f] = {...}` already gives last-wins on
   duplicate `Frame`. Add a duplicate counter so the loader can trigger compaction.
3. **Compaction:** after a load (or at save time) when `rows_on_disk > distinct_frames *
   COMPACT_FACTOR` (e.g. `2`), rewrite the file deduped from the in-memory map using C1's
   atomic writer. This bounds unbounded growth from repeatedly re-editing the same frames.
4. Column order stays byte-identical to today's union columns
   (`Frame,Note,Params,LH,RH,LL,RL` / the pose column list) so existing unified files keep
   loading and Analysis/recovery are untouched.

**D — Off-thread full export.** In `save_data`, run `export_from_unified` /
`export_pose_dataset` (unchanged functions) inside a worker; report progress via
`self.after(...)`-scheduled UI updates and write the file from the worker. The metadata sidecar
(`write_export_metadata`) can stay inline or move with it. No change to the export functions
themselves.

## 5. Export schema impact

**none.** `export_from_unified` and `export_pose_dataset` and their column lists are **not
touched** — D only changes *which thread* calls them. All changes are to the **unified** file's
write mechanics (append vs read-rewrite) and its loader's compaction. Certified by
`tests/test_export_schema.py` (both touch and pose schema-lock tests must stay green,
unmodified).

## 6. Edge cases & failure modes

- **Crash mid-append:** a torn trailing row is silently skipped by the robust loaders
  (`int(row["Frame"])` in a `try/except` → `continue`; `data_utils.py:281-284`,
  `pose_mismatch_data.py:114-117`). Because appends are additive, an interrupted save never
  corrupts *previously-saved* rows — strictly safer than today's "rewrite the whole file" which
  can lose everything on a mid-write crash (that is exactly C1's concern).
- **Re-editing the same frame many times:** produces duplicate `Frame` rows; last-wins on load
  keeps it correct, compaction (§4.3) keeps the file bounded.
- **Empty/0-byte or header-only unified file:** treated as "fresh file" → write header + rows;
  matches current behaviour.
- **`changed_only` with nothing dirty:** early return, file untouched (unchanged).
- **Pose read-error path (C4):** removing the read *eliminates* the failure mode where a read
  exception wiped `existing_map` — there is no read to fail.
- **Column drift on append:** appended rows must be written with the exact same column order as
  the header; enforced by building the `DataFrame` with the fixed `cols` list before `to_csv`,
  same as today.

## 7. RESET procedure (no migration)

The unified **column layout is unchanged**, and the loaders are already last-wins, so existing
unified files keep working and **no reset is strictly required**. Per the no-backwards-compat
policy we document a clean-slate procedure rather than any migration code, in case a file is
suspected inconsistent:

1. Close the app.
2. Delete the working store for the affected clip:
   `Labeled_data/<video>/data/<video>_unified.csv`
   (pose: `<video>_3d/data/<video>_3d_unified.csv`).
3. Relaunch and load the video. If the unified file is absent the app recovers from the export
   snapshot (`import_unified_from_export`) — no data loss for anything already exported.

No schema/layout version bump and no on-load rewrite-migration is introduced.

## 8. Testing / verification plan

**(a) Automatable red/green — `tests/test_incremental_save_H3.py` (new), run with
`uv run pytest tests/ -k H3 -v`.** Black-box against `data_utils` / `pose_mismatch_data` using
`tmp_path` (reuse `conftest.py`'s `one_touch_frames`):

1. `test_H3_touch_roundtrip_fidelity` — build `frames`, `save_unified_dataset(tmp)`, then
   `load_unified_dataset(tmp)`; assert the reloaded bundles equal the saved ones (Onset, X/Y,
   Zones, Params, Note). Proves the append path preserves fidelity.
2. `test_H3_touch_no_full_reread` (the incrementality proof) — save frame A; then
   **monkeypatch `data_utils.pd.read_csv` to raise**; mark only a *different* frame B changed
   and save again; assert the save **succeeds** and a reload contains **both** A and B.
   - *Red before:* today's writer calls `pd.read_csv` → the monkeypatched raise propagates
     (touch only catches `EmptyDataError`) → save fails. Proves the full re-read exists.
   - *Green after:* append path never reads → save succeeds, A (untouched) preserved without
     any rewrite of its row.
3. `test_H3_touch_last_writer_wins` — save frame A (Onset ON), then save frame A again
   (Onset OFF); reload asserts OFF. Proves duplicate-frame resolution.
4. `test_H3_pose_*` — the same three against `save_pose_dataset` / `load_pose_dataset`
   (monkeypatch `pose_mismatch_data.pd.read_csv`).

**(b) Schema guard (must stay green, unmodified):** `uv run pytest tests/ -k schema -v` —
`tests/test_export_schema.py` proves the export columns/order are byte-for-byte unchanged.

**(c) Manual timing** via `uv run python src/main.py`: open a long, heavily-labelled clip
(the more previously-labelled frames the better), edit one frame, hit **Save**, and read the
`DEBUG: Unified →` timing / wall-clock before vs after the change; then repeat the
navigate-to-new-video and Close paths. Expectation: unified-save time flat regardless of how
much is already labelled (was growing with labelled-frame count); export no longer freezes the
UI (progress window stays responsive).

Commands:
```
uv run pytest tests/ -k H3 -v
uv run pytest tests/ -k schema -v
```

## 9. Interactions with other planned fixes

- **C1 (atomic writes — same two writers).** **C1 lands first.** C1 makes writes atomic via
  temp-file + `os.replace` (a full rewrite). H3's append fast-path is *not* a temp-rename
  (you cannot atomically append that way), but it is crash-*tolerant* by construction
  (§6: torn trailing row skipped, prior rows never touched). H3 **reuses C1's atomic writer for
  the compaction rewrite** (§4.3), so the one place that still does a full rewrite stays atomic.
  Coordinate: implement H3 on top of C1's helper; do not regress C1's guarantee on the
  compaction path.
- **C4 (pose read-fail silently discards prior rows).** H3 **removes the read entirely** from
  `save_pose_dataset`, which obviates C4's specific failure mode. If C4 lands first (add a guard
  so a read error does not wipe `existing_map`), H3 then deletes that read+guard together. Note
  this in the C4 close-out so the guard isn't left as dead code.
- **M8 (`load_pose_dataset` uses slow `iterrows`).** Independent but adjacent; H3 does not
  change the pose *loader*. If both are in flight, M8's `itertuples` switch and H3's append
  writer touch different functions — no conflict.
- **H1 (no Tk from background threads).** Only the **D** (off-thread export) half interacts:
  D must marshal all progress UI through `self.after(...)` and never call Tk from the worker,
  matching H1's contract. Sequence D after H1 if H1 is close; otherwise implement D H1-safe from
  the start. The **B** (unified append) half is pure data and has no H1 interaction.
- **H2 (per-edit O(N) timeline rebuild).** Same "scalability wall" bucket in review.md's order
  of attack, but a disjoint code path (rendering vs persistence). No shared symbols.

**Ordering:** C1 → C4 → H3-B (unified append) → H3-D (off-thread export, after/with H1).

## 10. Effort estimate & risk

- **Effort:** ~1.5–3 h. B (both writers + compaction + tests) ~1–1.5 h; D (thread + progress
  marshalling) ~1–1.5 h and carries the H1 coupling.
- **Risk:** Low–moderate. B is contained and heavily testable; the main subtlety is compaction
  correctness and column-order-on-append (covered by the round-trip + last-wins tests). D is
  the riskier half (threading + Tk) — keep it separable so B can ship alone.
- **Rollback:** revert per half; the two changes are independent commits.
- **Operational footprint:** code-only. **No version bump.** Verify with `uv run pytest`
  (`-k H3`, `-k schema`) and by relaunching `uv run python src/main.py` for the manual timing
  check.
