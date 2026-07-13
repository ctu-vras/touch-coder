# Fix M5 — Delete the dead-but-dangerous `merge_and_flip_export` L↔R flip path

## Problem (re-verified at working tree)

`data_utils.merge_and_flip_export` (~`:740-835`; the review cited `:828-842` — the file has
grown) is the legacy per-limb-CSV merge + left/right flip exporter. It is unreachable at
runtime (proof below) but remains a loaded gun:

- `flipped = flipped.applymap(_swap_lr_in_string)` (~`:828`) swaps `L`↔`R` in **every string
  cell** of the frame — `_swap_lr_in_string` (~`:838`) does
  `val.replace('L','§').replace('R','L').replace('§','R')`, so a free-text note
  `"Left Reaching"` becomes `"Reft Leaching"`. `DataFrame.applymap` is also deprecated since
  pandas 2.1 (FutureWarning on the pinned 2.3.2) and removed in pandas 3.x.
- If ever invoked, it would **overwrite `<video>_export.csv`** — bypassing `atomic_write`,
  with a column order different from the frozen schema, and with a 6-line preamble prepended
  by `_prepend_header` that breaks every `pd.read_csv` consumer (Analysis, schema tests,
  external pipelines).

**Reachability, verified at the working tree:**
- `labeling_app.py:35` **imports** `merge_and_flip_export` in the top-of-file `from
  data_utils import (...)` block — but a repo-wide grep finds **zero call sites** (no menu,
  button, dialog, or function invokes it; the only other data_utils imports are the
  function-local ones at `:1117/:1672/:3198/:3234/:3415`, none of which touch it).
- The live export writers are `export_from_unified` (touch) and `export_pose_dataset` (pose),
  both called from `save_data`. Reliability mode reuses frames, never flips.
- `data_utils` has no `__all__` and no other module imports these symbols
  (`video_model.py` imports only `empty_bundle`/`FrameBundle`).
- The one *modern* `_prepend_header` reference, inside `export_from_unified` (~`:559-570`),
  is **commented out** (a `'''...'''` block) — `merge_and_flip_export` (~`:821`, `:831`) is
  the sole live caller of `_prepend_header`.
- `_swap_lr_columns` (~`:843-850`) and `_swap_lr_in_string` are called **only** from
  `merge_and_flip_export`.
- **No test references any of these symbols** (grep over `tests/`: zero matches for
  `merge_and_flip|swap_lr|prepend_header|export_flipped`).

## How it fits the whole app

This is the pre-unified-CSV export pipeline (per-limb `{RH,LH,RL,LL}.csv` + parameter CSVs
merged, flipped for a mirrored-camera use case, headers prepended). The unified pipeline
(`save_unified_dataset` → `export_from_unified` → `write_export_metadata`) replaced all of
it: the metadata that `_prepend_header` used to stuff into a CSV preamble now lives in the
JSON sidecar, and the review's structural section (§3) already lists this path under "dead /
legacy code still imported and reachable-looking". Deleting it removes ~120 lines and the
only remaining `applymap` in the codebase.

## Approaches considered

**A. Delete the whole path (recommended).** Function, its two private helpers,
`_prepend_header`, the stale commented-out caller block, and the import. Matches the
review's explicit instruction ("delete it rather than leave a loaded gun") and the M4/M3
precedent of resolving findings by dead-code removal. **Chosen.**

**B. Keep but modernize** (`.map` instead of `.applymap`, restrict the swap to zone-label
columns, atomic write, no preamble). **Rejected:** zero callers, zero tests, no user-facing
entry point — modernized dead code is still dead code, and "fixed" flip output would still
violate the frozen export schema if anyone wired it up.

**C. Delete `merge_and_flip_export` only, keep `_prepend_header`** (someone might un-comment
the block in `export_from_unified`). **Rejected:** that block was retired deliberately —
"CSV remains clean (no preamble). Metadata is written separately by caller." (~`:571`) —
and a preamble would break the frozen schema; keeping the helper invites exactly that.

## Recommended implementation

Delete, in `src/data_utils.py` (anchor by symbol, not line):

1. **`merge_and_flip_export`** (~`:740-835`) — the whole function.
2. **`_swap_lr_in_string`** (~`:838-841`) — only caller is (1).
3. **`_swap_lr_columns`** (~`:843-850`) — only caller is (1).
4. **`_prepend_header`** (~`:67-103`), including its nested `_fmt_label_map` — only live
   callers are inside (1).
5. **The commented-out `'''_prepend_header(...)'''` block** inside `export_from_unified`
   (~`:559-570`) plus its "Keep 5-line header" lead-in comment — it references a symbol that
   no longer exists. Keep the `# CSV remains clean (no preamble)...` explanatory line. Also
   drop the stale docstring sentence "Appends label mappings to the 5th header line (keeps
   5-line header + blank)." from `export_from_unified` (~`:510`) — it describes the deleted
   behaviour.

And in `src/labeling_app.py`:

6. Remove **`merge_and_flip_export`** from the `from data_utils import (...)` list (`:35`).

**Complete deleted-symbol list:** `merge_and_flip_export`, `_swap_lr_in_string`,
`_swap_lr_columns`, `_prepend_header` (with nested `_fmt_label_map`), one import name, one
commented-out block + two stale comment/docstring lines. Nothing else is reachable only from
this path: it consumed its inputs via `pd.read_csv` directly and shares `csv_to_dict` /
`save_dataset` etc. with nothing (those legacy loaders have their own live fallback callers
and are **out of scope** here).

## Export schema impact

**NONE.** The deleted code is unreachable; the live writers (`export_from_unified`,
`export_pose_dataset`) are untouched except for one docstring sentence and a commented-out
block — no column, order, or value change. `tests/test_export_schema.py` must stay green.
(Deleting this path in fact *removes* the only code capable of writing a wrong-schema
`_export.csv`.)

## Edge cases & failure modes

- **Import breakage** is the only real hazard: the name must come out of `labeling_app.py:35`
  in the same change, or the app dies at import. No other module imports the symbols; no
  `__all__`; no star-imports; `TinyTouch.spec` bundles data files only, not symbol lists.
- Old `_export_flipped.csv` files on disk (if any user ever ran the legacy build) are inert
  outputs — nothing reads them; no migration needed.
- Grepping after deletion for each symbol name must return only `docs/` hits (review + plans
  are immutable snapshots and keep their references).

## Testing / verification plan

**Automatable.** No red test exists to write in the classic sense (the bug is *presence*,
not behaviour — you cannot black-box a function into not existing without asserting on
internals). Verification is therefore the guard suites plus an import sanity check:
- `uv run pytest tests/ -v` — full suite green (no test touches the deleted symbols;
  verified by grep).
- `uv run pytest tests/ -k schema -v` — schema locks green (proves live exporters untouched).
- `uv run python -c "import sys; sys.path.insert(0,'src'); import labeling_app"` — import
  chain intact after the import-list edit.

**Manual:** `uv run python src/main.py` — app launches; load a touch video, Save; confirm
`export/<video>_export.csv` regenerated normally and no `*_export_flipped.csv` appears.

## Interactions with other planned fixes

- **M6 (`to_do/fix_M6.md`, encoding sweep) — explicit overlap.** M6's `open()` inventory
  lists `data_utils._prepend_header` `:84`/`:96` with the verdict "sweep (legacy; only
  `merge_and_flip_export` calls it — M5 wants that deleted)". Those two sites are **removed
  from M6's inventory by this deletion**, not fixed. Land **M5 first** so M6's sweep shrinks;
  if M6 lands first anyway, its two encoding args are simply deleted along with the function
  ("do not resurrect them", per M6).
- **C1 (done, atomic writes):** `done/fix_C1.md` already classified these writers as legacy
  "still imported but not on the live save path" and skipped them — this fix completes that
  triage by deletion. No code overlap remaining.
- **H1 (uncommitted, `labeling_app.py`):** touches threading/playback, not the import block —
  one-line coexistence, re-anchor by symbol.
- **M4 (done):** fixed `Time_ms` division in `export_from_unified` only; the unguarded
  `merged_df['Frame'] / frame_rate` (~`:808`) inside `merge_and_flip_export` was left alone —
  correctly, since this fix deletes it.

## Effort estimate & risk

- **Effort:** ~20-30 min (mechanical deletion + import edit + guard-suite run).
- **Risk:** Minimal. Unreachable code; the single coupling (the import) is part of the
  change and is covered by the import sanity check + app launch.
- **Rollback:** `git checkout` the two files.
- **Operational footprint:** code-only, no version bump, no data migration; verify with
  full pytest + one manual Save round-trip.
