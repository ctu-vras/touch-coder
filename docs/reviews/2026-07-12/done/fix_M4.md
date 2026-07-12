# Fix M4 — Touch export crashes (ZeroDivisionError) on a 0-FPS video

## Problem (re-verified at HEAD)

`data_utils.export_from_unified` builds each row's timestamp with an unguarded division
(~`data_utils.py:520`):

```python
row = {"Frame": f, "Time_ms": (f / frame_rate) * 1000.0}
```

Some video containers/codecs make OpenCV's `cv2.CAP_PROP_FPS` probe return `0`
(`video_model.Video.__init__` → `get_total_frames`, and `load_video` sets
`self.frame_rate = round(cap.get(cv2.CAP_PROP_FPS), 1)`, ~`labeling_app.py:3307`). When
`frame_rate == 0`, the very first frame (`f=0` → `0/0`) raises **`ZeroDivisionError`**, which
propagates out of `export_from_unified`.

Because `save_data` writes the **unified** CSV first and the **export** CSV second
(~`labeling_app.py:3116-3175`), the crash leaves the on-disk state inconsistent: the unified
file is updated but the export the research pipelines read is stale/never rewritten, and the
exception surfaces mid-Save.

The pose exporter already handles this correctly (`export_pose_dataset`,
~`pose_mismatch_data.py:261`): `"Time_ms": (frame / frame_rate) * 1000.0 if frame_rate else 0.0`.
This fix brings the touch exporter to parity.

## How it fits the whole app

- **Only writer affected:** `export_from_unified` (the export CSV consumed by Analysis, Sort
  Frames [being removed], and external pipelines).
- **Sibling division sites in the same finding** (fix opportunistically only if trivial and
  in the same function — otherwise leave for their own pass; they are GUI/constructor, not the
  export data path):
  - `update_frame_counter` (`labeling_app.py:2341-2348`) — GUI label; `current_frame/frame_rate`.
  - `Video.get_total_frames` (`video_model.py:87-91`) — `fps:.3f` on possibly-`None`/0 fps.
  These are **out of scope** for M4 (different surface, no export-data impact). Note them; do
  not fix here.
- **Downstream contract:** `Time_ms` is an existing export column. Emitting `0.0` when fps is
  unknown matches the pose exporter and keeps the column present with a numeric value.

## Approaches considered

**A. Guard the division inline (recommended).** One expression, mirrors the pose exporter
exactly, zero schema impact. **Chosen.**

**B. Refuse to export when `frame_rate == 0`** (raise a clean error / messagebox upstream).
Arguably more correct (a 0-fps `Time_ms` column of all-zeros is meaningless), but it changes
user-facing behaviour (Save would fail loudly) and doesn't match the pose exporter's lenient
approach. **Rejected** for parity + least surprise; the upstream fps probe hardening belongs
with the `get_total_frames` finding, not here.

**C. Compute a fallback fps** (e.g. assume 30). **Rejected:** fabricates timestamps; worse
than an honest 0.

## Recommended implementation

In `data_utils.export_from_unified`, change the timestamp expression to match the pose
exporter:

```python
row = {"Frame": f, "Time_ms": (f / frame_rate) * 1000.0 if frame_rate else 0.0}
```

**Behavioural contract after the change:**
- `frame_rate > 0`: identical output to before (bit-for-bit).
- `frame_rate in (0, None)`: export completes; every `Time_ms` is `0.0`; no exception.
- **Export schema impact: NONE.** No column added/removed/renamed/reordered; only a value
  fallback on an existing column. Guaranteed by `tests/test_export_schema.py`.

## Edge cases & failure modes

- `frame_rate is None` (never set because no video fully loaded) → falsy → `0.0`. Safe.
- Negative/garbage fps → out of scope (the probe clamps via `round`; a negative fps would
  still divide, just produce negative Time_ms — not this finding).
- Very large frame counts → unchanged; no overflow concern with float.

## Testing / verification plan

**Automatable (red/green):** `tests/test_export_zero_fps.py::test_M4_export_survives_zero_fps`
(already written).
- Builds a one-touch `frames` dict, calls `export_from_unified(..., frame_rate=0.0)`.
- **Red before:** `uv run pytest tests/ -k M4` → `ZeroDivisionError`, file never written.
- **Green after:** passes; `Time_ms` all `0`, 6 rows (frames 0..5).

**Schema guard (must stay green):** `uv run pytest tests/ -k schema` → both touch and pose
schema-lock tests pass unchanged (proves the fix didn't touch the format).

Commands:
```
uv run pytest tests/ -k M4 -v
uv run pytest tests/ -k schema -v
```

**Manual:** none required (pure data path). Optionally, load a known 0-fps clip and confirm
Save no longer errors in the console.

## Interactions with other planned fixes

- **Independent** of C1/C2/C4/H*. Shares the function with no other open finding.
- Do **not** bundle the `update_frame_counter` / `get_total_frames` division sites here; if
  those get their own finding, land them separately.

## Effort estimate & risk

- **Effort:** ~5 min (one expression + run two test selectors).
- **Risk:** Minimal. `frame_rate > 0` path is byte-identical; only the previously-crashing
  path changes.
- **Rollback:** revert the one line.
- **Operational footprint:** code-only, UI-side (touch export). No version bump. Verify by
  `uv run pytest`; relaunch `uv run python src/main.py` only if doing the optional manual check.
