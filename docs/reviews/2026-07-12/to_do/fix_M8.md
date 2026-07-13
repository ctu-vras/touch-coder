# Fix M8 — `load_pose_dataset` uses the slow `iterrows()` path

## Problem (re-verified at HEAD)

`pose_mismatch_data.load_pose_dataset` walks the unified 3D CSV with `iterrows()`
(`pose_mismatch_data.py:121`; the review cited `:113` — the module has since grown
`HeadScale*` fields and per-joint `Opacity`, C4's `PoseUnifiedReadError`, and `atomic_write`).
`iterrows` materializes a fresh `pandas.Series` per row; on the touch side this exact pattern
caused the documented **"4 rows in 20 seconds"** load freeze and was fixed by converting both
touch loaders to positional `itertuples` (`data_utils.load_unified_dataset:263-278`,
`import_unified_from_export:375-412` — the comments there record the incident). The pose
loader is the **currently-active mode's** load path (`config.json: annotation_mode =
"pose_3d"`), called from `LabelingApp.load_data` (`labeling_app.py` ~`:3426`,
`self.video.frames = load_pose_dataset(unified_path) or {}`) on every video open — so a
heavily-labelled 3D clip will reproduce the freeze, synchronously on the Tk thread.

**Scope decision — `save_pose_dataset`'s existing-row re-read (`:180`) also uses `iterrows`
but is deliberately EXCLUDED.** `fix_H3.md` (to_do) explicitly claims that read+merge+rewrite
block for its incremental-save rewrite ("the exact slow path M8 flags elsewhere"), and the
block is additionally pinned by C4's regression test
(`tests/test_pose_save_no_silent_loss.py`). Converting it now would be throwaway work that
double-edits H3's target. **If H3 is dropped or long-deferred, apply the identical mechanical
conversion there as a follow-up.**

## How it fits the whole app

- Load path only: `load_pose_dataset` → per-row bundle build → `ensure_pose_bundle` →
  `Video.frames`. Nothing downstream changes.
- The loader is **lenient per field**: every field has its own `try/except`/NaN fallback
  (`:127-165`). The conversion must preserve those fallbacks *bug-for-bug* — including the
  quirk that a present-but-NaN `ScaleRaw` cell currently propagates NaN (because
  `float(nan or 1.0)` is `nan`). Validation/clamping is finding **M11**, not this one; M8 is
  a pure performance refactor with identical value semantics.
- `pd.Series.get(col, default)` returns the default only when the **column is missing**
  (older CSVs predating `HeadScale*`), and NaN when the column exists but the cell is empty.
  The replacement must reproduce both behaviours.

## Approaches considered

**A. Positional `itertuples(index=False, name=None)` + column-index map (recommended).**
The established house pattern from `load_unified_dataset` — proven ~50-100x faster, and a
thin `_get(name, default)` shim keeps every field expression literally identical to today.
**Chosen.**

**B. Named `itertuples` attribute access.** **Rejected:** missing-column handling (old files
without `HeadScale*`) becomes `hasattr` gymnastics, and it diverges from the touch loaders'
idiom for no gain.

**C. `df.to_dict("records")`.** **Rejected:** still builds a dict per row (~2-4x, not ~50x),
and `dict.get` vs `Series.get` NaN semantics differ subtly anyway.

**D. Vectorized pandas (column-wise apply/json parsing).** **Rejected:** over-engineered for
a load-once path and would obliterate the readable per-field fallback structure.

## Recommended implementation

In `load_pose_dataset`, after the existing `pd.read_csv` (`:116`):

```python
col_idx = {c: i for i, c in enumerate(df.columns)}
if "Frame" not in col_idx:
    print("ERROR: load_pose_dataset: 'Frame' column missing — aborting", flush=True)
    return frames

for row in df.itertuples(index=False, name=None):
    def _get(name, default=None):
        i = col_idx.get(name, -1)
        return default if i < 0 else row[i]
    try:
        frame = int(_get("Frame"))
    except Exception:
        continue
    ...  # every existing field expression, with row.get(X, d) -> _get(X, d)
```

Then mechanically replace each `row.get("Col", default)` with `_get("Col", default)` and
each `"HeadScaleRaw" in row` with `"HeadScaleRaw" in col_idx` — the field bodies (`:127-165`)
otherwise stay verbatim, which is what guarantees value equivalence:
- `pd.isna(note)` unchanged (works on scalars).
- `json.loads(v or "{}")` for `Params`/`Joints`: NaN is truthy → `json.loads(nan)` raises →
  existing `except` → `{}` — identical to today.
- `ScaleSet`/`HeadScaleSet` missing-column → `None` default → the existing `is None` branch.

Optional, in-scope (rule 0 parity with the touch loader): one DEBUG line logging row count
and elapsed time after the loop.

## Export schema impact

**NONE.** Loader only; `export_pose_dataset` and `save_pose_dataset` are untouched. No
column or value encoding changes anywhere. `tests/test_export_schema.py` (both the touch and
pose locks) must stay green.

## Edge cases & failure modes

- **Old-schema CSV without `HeadScale*` columns** → `_get` returns defaults → `1.0`/`False`
  paths, same as `Series.get` today.
- **Ragged `Frame` cell** → `int()` raises → row skipped, unchanged.
- **Present-but-NaN numeric cells** → NaN propagates exactly as today (M11's problem).
- **Empty file / read failure** → handled before the loop (`:111-119`), untouched.
- **Duplicate `Frame` rows** → last one wins (dict overwrite), unchanged.

## Testing / verification plan

Honest framing: this is a behaviour-preserving performance refactor, so the pytest suite is a
**correctness-equivalence guard** (written *before* the change, green before AND after — it
characterizes today's semantics), not a red/green pair. The "red" is the freeze itself, which
is a benchmark observation, not a CI assertion.

**Automatable (pytest, `tmp_path`), `tests/test_pose_load_itertuples.py`:**
- `test_M8_load_pose_dataset_roundtrip`: build frames with a note, `Params`, joints with
  `ON`/`OFF` events + non-default `Opacity`, set/unset body and head scales; write via
  `save_pose_dataset(..., changed_only=False)`; `load_pose_dataset`; assert every field of
  every bundle matches expectations.
- `test_M8_old_schema_without_headscale_columns`: hand-write a CSV **without** the
  `HeadScale*` columns; assert `HeadScaleRaw == 1.0`, `HeadScaleFactor == 1.0`,
  `HeadScaleSet is False`.
- `test_M8_skips_bad_frame_and_bad_json`: a row with `Frame="x"` is skipped; a row with
  garbage `Joints` JSON yields the canonical empty joint map (via `ensure_pose_bundle`).
- Guards that must stay green: `uv run pytest tests/ -k "C4 or schema" -v`.

**Benchmark note (manual, not CI-asserted):** generate a synthetic ~50k-row unified pose CSV
in the scratch area and time `load_pose_dataset` before/after (`iterrows` is expected in the
tens of seconds; `itertuples` well under a second — mirroring the touch-loader incident).
Record both numbers in the fix report.

**Manual:** open an existing labelled 3D video (`uv run python src/main.py`); annotations,
scales and opacities render identically; load is not slower.

## Interactions with other planned fixes

- **H3 (save rewrite):** owns the `save_pose_dataset` `iterrows` block excluded above; M8
  being load-only means zero overlap. If H3's rewrite also touches the loader, this
  conversion lands first and H3 re-anchors on it.
- **M11 (ScaleFactor clamp on load):** same function. M8 stays semantics-neutral precisely
  so M11's diff remains a clean, reviewable behaviour change; land in either order (trivial
  textual merge).
- **C4 (landed):** untouched; its regression test doubles as a tripwire that M8 didn't stray
  into the save path.
- **H1:** different file (`labeling_app.py`); only the call-site line number cited above may
  drift — anchor by `load_data`.

## Effort estimate & risk

- **Effort:** ~45 min (mechanical conversion + three tests + benchmark run).
- **Risk:** Low. The `_get` shim keeps field logic verbatim; equivalence tests pin the
  semantics; the touched function has a single caller.
- **Rollback:** revert the loop; tests remain valid against either implementation.
- **Operational footprint:** code-only, no version bump; this is the active mode's load path,
  so the payoff is felt on every 3D session open.
