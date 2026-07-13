# Fix M11 — Pose `ScaleFactor` not clamped on load

## Problem (re-verified at working tree)

PROJECT.md documents the invariant `ScaleFactor ∈ [0.7, 1.3]`. In-app writes honor it —
`_set_pose_scale_for_frame` (`labeling_app.py`, `_POSE_SCALE_KEYS` machinery) always derives
`factor = scale_raw_to_factor(raw)`. But nothing enforces it against the **disk**:

- `load_pose_dataset` (`pose_mismatch_data.py:133-140`, and `:146-160` for the head scale)
  does `float(row.get("ScaleFactor", ...) or 1.0)` — an on-disk `5.0` loads as `5.0`, and a
  **NaN cell propagates as NaN** because `float(nan or 1.0)` is `nan` (NaN is truthy —
  verified against the project venv).
- `ensure_pose_bundle` (`:88-99`) clamps **only when the key is absent**; a present key is
  trusted verbatim. **`HeadScaleFactor` has the identical hole** (`:96-99`).
- Bonus, verified: **`scale_raw_to_factor(NaN)` returns `1.3`, not `1.0`** — inside
  `max(0.7, min(1.3, nan))`, both comparisons against NaN are False, so `min` keeps `1.3`.
  The clamping helper itself mishandles NaN.
- Knock-on: with a NaN `ScaleRaw`, the loader's `ScaleSet` fallback (`:141-145`) computes
  `nan != 1.0` → **True**, so the corrupt row is marked "set".

Propagation of a corrupt value once loaded: `_get_effective_pose_scale`
(`labeling_app.py:284-287`) returns bundle values verbatim when `ScaleSet` → skeleton renders
at the wrong scale (or NaN geometry); `export_pose_dataset` (`pose_mismatch_data.py:264-273`)
exports it verbatim (NaN becomes an **empty `ScaleFactor` cell** in the frozen export);
`save_pose_dataset` (`:220-221`) round-trips it forever. Only a hand-edited/corrupt/truncated
CSV can breach the invariant — which makes **load-time normalization** the right choke point.

## How it fits the whole app

`ensure_pose_bundle` is already the canonical pose-bundle normalizer, called at every
boundary: per-row on load (`load_pose_dataset:166`), per-row on save (`save_pose_dataset:214`),
per-frame on export (`export_pose_dataset:263`), and per-render
(`_get_effective_pose_scale`, `labeling_app.py:283`). It also already contains the exact
pattern we need, for joint `Opacity` (`:81-87`): `float()` + explicit `op != op` NaN guard +
`max/min` clamp + fallback. M11 extends that discipline to the four scale fields. Because the
clamp lives *outside* the loader's row loop, it is **independent of how the loop is
implemented** — which is what makes the M8 interaction safe (below).

## Approaches considered

**A. Unconditional sanitize-and-clamp in `ensure_pose_bundle` + NaN guard in
`scale_raw_to_factor` (recommended).** One choke point covers load, save, export and render;
mirrors the existing Opacity pattern; zero textual overlap with M8's loader rewrite. **Chosen.**

**B. Clamp per field inside `load_pose_dataset`.** **Rejected:** edits the exact lines M8 is
converting to `itertuples` (merge conflict by construction), must be duplicated for
body+head, and leaves save/export/render entry points unguarded.

**C. Hard error on out-of-range values.** **Rejected:** the loader is deliberately lenient
per field; a hand-edited file should load with a WARNING, not brick the video (contrast C4,
where refusing protected *unread* data — here the data is readable, just out of contract).

## Recommended implementation

1. **`scale_raw_to_factor`** — add the NaN guard (mirrors the Opacity guard `:83-84`):

   ```python
   try:
       value = float(scale_raw)
       if value != value:  # NaN guard: min/max would leak 1.3
           value = 1.0
   except Exception:
       value = 1.0
   return max(0.7, min(1.3, value))
   ```

2. **`ensure_pose_bundle`** — for both pairs `("ScaleRaw","ScaleFactor")` and
   `("HeadScaleRaw","HeadScaleFactor")`, after the existing absent-key defaults, sanitize
   present keys: coerce with `float()`; on failure or NaN set raw to `1.0`; then clamp **both
   raw and factor** through `scale_raw_to_factor` (raw is a slider position with the same
   0.7-1.3 range — in-app raw is always in range, so clamping it costs nothing and restores
   the `raw == unclamped-source-of-factor` symmetry). When a present value actually changes,
   log `WARNING: pose scale out of range/NaN on load: <key>=<old> -> <new>` (rule 0). The
   mutation is in place, so a corrupt frame warns once, not per render.

**Owning M8's deferred NaN case (the explicit hand-off in `fix_M8.md`):** a
present-but-NaN `ScaleRaw` **becomes `1.0`** (neutral) and a NaN/absent `ScaleFactor` becomes
`scale_raw_to_factor(sanitized raw)` = `1.0`. `ScaleSet` is deliberately **left untouched**:
the loader will have derived `True` from the NaN raw, so the frame shows as "set at 1.0" —
in range, harmless, and the annotator can see and clear it; resetting `ScaleSet` here would
silently discard the signal that the row was edited at all.

**M8/M11 ordering:** **no required order — safe by construction.** M8 rewrites the loader
loop bug-for-bug (NaN still flows out of the loop); M11 normalizes in `ensure_pose_bundle`,
which the loader calls on every bundle it emits (`:166`), so the quirk becomes unobservable
regardless of which loader implementation produced it. M8's equivalence tests (roundtrip of
*valid* values, old-schema defaults, bad frame/JSON) do not pin NaN propagation — verified
against `fix_M8.md`'s test list — so they stay green whether M11 lands before or after.

## Export schema impact

**NONE structurally** — no column added/removed/renamed/reordered; `tests/test_export_schema.py`
(both locks) must stay green. **Value impact, corrupt inputs only:** `ScaleFactor` /
`HeadScaleFactor` cells that today could be exported as NaN (→ empty cell) or out-of-range now
always carry a value in `[0.7, 1.3]` — this *restores* the documented contract downstream
pipelines were promised, and valid inputs are numerically unchanged (clamp is identity in range).

## Edge cases & failure modes

- **Valid file** (all scales in range): every clamp is identity; zero warnings; output
  bit-identical.
- **Out-of-range finite** (`ScaleFactor=5.0`): → `1.3`, WARNING. Raw `5.0` → `1.3` likewise.
- **NaN raw + NaN factor:** both → `1.0`; `ScaleSet` stays as loader derived (see above).
- **Non-numeric garbage** (`ScaleFactor="abc"`): loader's own `except` already falls back to
  `scale_raw_to_factor(raw)`; `ensure_pose_bundle`'s `float()` failure path covers bundles
  arriving from other callers.
- **Old-schema CSV without `HeadScale*` columns:** absent-key defaults unchanged (`1.0`).
- **In-memory callers** (`empty_pose_bundle`, slider writes): always in range; no-ops.

## Testing / verification plan

**Automatable (red/green), `tests/test_pose_scale_clamp.py`** — write unified pose CSVs in
`tmp_path`, call `load_pose_dataset` / `export_pose_dataset`:

- `test_M11_out_of_range_factor_clamped_on_load`: row `ScaleRaw=5.0, ScaleFactor=5.0,
  ScaleSet=True` → **red today** (loads `5.0`); **green:** `ScaleFactor == 1.3` and
  `ScaleRaw == 1.3`.
- `test_M11_nan_scale_becomes_neutral`: row with empty `ScaleRaw`/`ScaleFactor` cells →
  **red today** (`math.isnan(bundle["ScaleFactor"])`); **green:** both `== 1.0`.
- `test_M11_scale_raw_to_factor_nan`: `scale_raw_to_factor(float("nan")) == 1.0` —
  **red today** (returns `1.3`).
- `test_M11_headscale_clamped`: `HeadScaleFactor=0.1` → `0.7`.
- `test_M11_export_stays_in_range`: load a corrupt CSV, run `export_pose_dataset`, read back:
  every `ScaleFactor`/`HeadScaleFactor` value in `[0.7, 1.3]`, none empty for set frames.

```
uv run pytest tests/ -k M11 -v
uv run pytest tests/ -k "C4 or schema" -v    # guards, must stay green
```

**Manual:** hand-edit a scratch copy's unified CSV to `ScaleFactor=9.9`, open the video
(`uv run python src/main.py`): skeleton renders at 1.3x, WARNING appears once in the console,
slider shows 1.3.

## Interactions with other planned fixes

- **M8 (loader itertuples, to_do):** same function family, **zero textual overlap** — M8
  owns the row loop, M11 owns `ensure_pose_bundle` + `scale_raw_to_factor`. Either order;
  composition analyzed above. M8 explicitly defers the NaN quirk to this plan.
- **H3 (incremental save rewrite, to_do):** `save_pose_dataset` preserves *existing* CSV rows
  verbatim (never through `ensure_pose_bundle`), so a corrupt row that was never loaded this
  session survives a save-merge and is normalized on next load — acceptable under the
  clamp-on-load contract. Flag for H3: if its rewrite starts re-encoding preserved rows, it
  must route them through `ensure_pose_bundle`, not invent a second normalizer.
- **C4 (landed):** save path untouched; `tests/test_pose_save_no_silent_loss.py` doubles as a
  tripwire. Keep `-k C4` green.
- **H2 (pose timeline cache, to_do):** reads scales via `_get_effective_pose_scale`; benefits
  from the guarantee, no code overlap.
- **L4/L5 (review, unplanned):** `ScaleAutoCarry` persistence and PROJECT.md pose-schema
  drift are adjacent but out of scope here.

## Effort estimate & risk

- **Effort:** ~1 h (two functions + five tests + manual check).
- **Risk:** Low. In-range values pass through unchanged; only contract-violating inputs
  change, and they currently produce undefined rendering/export behavior.
- **Rollback:** revert the two functions; tests document the intended contract either way.
- **Operational footprint:** code-only, active (3D) mode's load path; no version bump.
