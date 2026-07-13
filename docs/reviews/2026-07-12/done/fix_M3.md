# Fix M3 — `_Look` / gaze data is never exported (schema mismatch)

## Problem (re-verified against the current working tree)

> ⚠️ `labeling_app.py` carries the **uncommitted H1 fix** — line numbers WILL drift; anchor
> by **symbol names**. All claims below were re-verified against the working tree, not HEAD.

The per-limb `Look` field is written and read **asymmetrically**:

- `data_utils.export_from_unified` emits only `{limb}_X/_Y/_Onset/_Zones` per limb — there
  is **no `{limb}_Look` export column** (frozen schema, `tests/test_export_schema.py`).
- Yet `data_utils.import_unified_from_export` (the export→unified recovery path) builds
  `"Look": col_idx.get(f"{limb}_Look", -1)` and reads it into every recovered record. The
  column never exists, so the lookup is always `-1` and recovery **always yields `Look=""`**
  — dead plumbing that reads a column the writer can never produce.

**Full lifecycle traced (working tree):**

- **Set:** only two GUI handlers ever assign it, and both hardcode `"No"`:
  `LabelingApp.on_diagram_click` (fresh record `"Look": "No"`; existing record
  `rec['Look'] = "No"`) and `LabelingApp.on_middle_click` (delete path: cleared record
  `"Look": "No"`; remaining-points branch `rec['Look'] = "No"  # or keep existing`).
  Skeleton/default records use `""` (`data_utils.empty_record`, `toggle_limb_parameter`
  fallback dict) or `None` (`LabelingApp._ensure_bundle` inline bundle). **No code path in
  `src/` ever writes `"Yes"`** — the `FrameRecord` TypedDict's `Look: str  # "Yes"|"No"|""`
  comment is aspirational. There is no gaze widget: `ui_components.py` has no Look control.
- **Serialized:** the unified CSV has **no standalone Look column**. `save_unified_dataset`
  `json.dumps` each whole limb record into the `LH/RH/LL/RL` JSON-blob columns, so `Look`
  rides *inside* the JSON. Old unified CSVs therefore all carry `"Look": "No"/""` in blobs.
- **Read back:** `load_unified_dataset` → `json.loads` round-trips the key passively;
  `import_unified_from_export` → always `""` (the bug); legacy `csv_to_dict` (still-live
  migration fallback in `load_video`) reads a real `Look` column from old per-limb CSVs —
  values there can only be `"No"`/`""` because the same handlers wrote them.
- **Consumed:** nothing. Only `bundle_summary_dict` (debug preview) echoes it via `.get`.
  `analysis.py` never touches any Look column; `pose_mismatch_data.py` has **no Look analog**
  (different bundle — confirmed). No direct `["Look"]` indexing exists anywhere (all `.get`).

**Where gaze actually lives:** the global **Parameter 1** button — `toggle_parameter` /
`_param_next_state` (ON/OFF/None), stored in `Params["Par1"]`, exported into the frozen
`Parameter_1` column, with its user-editable label (`config.json` currently
`"parameter1": "Looking1"`) recorded in the metadata sidecar (`Param Labels`). PROJECT.md's
"Track infant gaze (Looking: Yes / No)" is this button. Gaze IS captured and exported there.

Historical corroboration: `merge_and_flip_export` (dead legacy path, see M5) contains an
explicit `# Drop legacy Look columns (no longer used in exports)` — Look was already
deliberately removed from exports once; the in-memory field just never followed.

## How it fits the whole app

`Look` sits in every `FrameRecord` of every touch-mode `FrameBundle` (PROJECT.md data model)
and travels: click handler → in-memory record → unified JSON blob → (recovery: export → `""`).
Because it is invisible to the export schema, to Analysis, and to the UI, its only observable
effect is noise: a constant `"Look": "No"` in every saved blob, a recovery path that silently
"loses" a value that never varied, and a TypedDict that documents a `"Yes"` state that cannot
occur. That mismatch is exactly what confused the review.

## DECISION — RESOLVED (Lucas, 2026-07-13)

**Vestigial — remove.** Lucas confirmed gaze is captured by the global Parameter 1
("Looking1") only; per-limb `_Look` is to be deleted per path (a).

**Additional requirement from Lucas:** the removal must be documented for coworkers who run
analysis pipelines *outside* this app, so they know to update anything still referencing
Look columns. Note the export itself does not change — current exports never contained
`{limb}_Look` — but *legacy* exports (old program versions) did carry Look columns, so
external scripts may still reference them. See implementation step 4.

## Approaches considered

**A. Remove the dead `_Look` handling (recommended, assumes "vestigial").** Delete the
writes and the recovery-read plumbing; keep passive tolerance for old data. **Chosen.**

**B. Keep the field, add `{limb}_Look` to the export.** **Rejected outright:** the export
schema is FROZEN (`tests/test_export_schema.py`); HANDOFF explicitly says M3 "is resolved by
removing dead code, not by extending the schema".

**C. Leave as-is, document the asymmetry.** **Rejected:** perpetuates dead code, a misleading
TypedDict, and a recovery path pretending to restore data that never existed.

**Fallback (b) — if Lucas says gaze SHOULD be per-limb:** storage must be **unified-CSV-only**
(it already round-trips inside the limb JSON blobs for free). Required work: a per-limb
Look toggle in the UI (new widget + key binding), stop hardcoding `"No"` in the two click
handlers, keep the field in `FrameRecord`. Accepted limitations: export→unified recovery
cannot restore Look (no export column — the current always-`""` behaviour becomes a
documented lossy edge), and Analysis/downstream never see it unless the frozen schema is
renegotiated with the pipeline (out of scope). Do NOT start this without the decision.

## Recommended implementation (path a)

All in the pure-function half except the handler edits; smallest faithful diff:

1. **`labeling_app.py`** — remove the four `Look` assignments in `on_diagram_click` (2) and
   `on_middle_click` (2); drop the `"Look": None` keys from `_ensure_bundle`'s inline bundle
   and the `"Look": ""` key from `toggle_limb_parameter`'s fallback record.
2. **`data_utils.py`**:
   - `FrameRecord`: delete the `Look` field; `empty_record`: drop `Look=""`.
   - `import_unified_from_export`: delete the `"Look"` entry in `limb_field_i`, the
     `look = _at(...)` + NaN-guard lines, and `"Look": look` in the constructed record.
   - `bundle_summary_dict`: drop the `"Look"` line (debug-only).
   - **Leave untouched:** `csv_to_dict` (legacy reader may keep tolerating the column — or
     drop the key from its output record; either is safe, prefer dropping for consistency),
     `save_dataset` + `merge_and_flip_export` (dead code, owned by M5 — do not edit).
3. **No load-time stripping of old data:** old unified CSVs carry `"Look"` inside JSON blobs;
   `json.loads` keeps unknown keys and every reader uses `.get`, so loading is unaffected.
   Stray keys persist harmlessly in blobs and matter to nothing.
4. **Coworker notice (required by Lucas):** create `docs/EXPORT_NOTES.md` — a short "notes
   for external pipeline consumers" doc — with an entry stating: per-limb gaze (`Look`) is
   fully retired from the app as of this change; current-format exports never contained
   `{limb}_Look` columns; any external script still referencing Look columns from *legacy*
   exports must switch to the `Parameter_1` column (user label "Looking1" in the metadata
   sidecar's `Param Labels`), which is where gaze is captured. Link the file from
   PROJECT.md's export-schema section so it's discoverable. Future export-visible changes
   (e.g. M12's transition-metric semantics in the in-app Analysis plots) should append here.

## Export schema impact

**NONE.** No export column is added, removed, renamed, or reordered — the fix deletes code
that never produced or consumed an export column. The unified CSV column set
(`Frame, Note, Params, LH, RH, LL, RL`) is also unchanged (Look was never a column there).
`tests/test_export_schema.py` must stay green throughout.

## Edge cases & failure modes

- **Old unified CSVs with `"Look"` in blobs:** load fine (see step 3); re-saving a loaded
  frame re-serializes whatever dict is in memory — with or without the stray key, harmless.
- **Legacy per-limb CSVs (`csv_to_dict` migration):** column still present in old files;
  reader tolerates it whether or not the key is propagated.
- **Test fixtures** (`tests/conftest.py`, `tests/test_limbview_h6.py`) build records with
  `"Look": "No"` — plain dicts, extra key is inert; no test edit required (don't weaken them).
- **Fallback (b) chosen later:** path (a) loses nothing real — every persisted value was
  `"No"/""`; re-adding the field means re-adding writes, not recovering data.

## Testing / verification plan

**Automatable (pytest, `tmp_path`), `tests/test_look_vestigial.py`:**
- `test_M3_recovery_records_have_no_look`: write a minimal export CSV (frozen columns) →
  `import_unified_from_export` → assert recovered limb records contain no `"Look"` key.
  **Red today** (every record has `Look=""`).
- `test_M3_old_unified_blob_with_look_still_loads`: hand-write a unified CSV whose `LH` JSON
  contains `"Look": "No"` → `load_unified_dataset` → assert X/Y/Onset/Zones load correctly
  (guards the old-file compatibility promise). **Green today and after** — regression guard.
- `uv run pytest tests/ -k M3 -v` (red→green); `uv run pytest tests/ -k schema -v` (stays
  green); full `uv run pytest tests/` (fixtures with stray `Look` keys must not break).

**Manual (GUI half):** `uv run python src/main.py` → touch mode → left/right/middle-click on
the diagram, Save; confirm clicks/deletes behave identically, `data/<video>_unified.csv` new
rows have no `"Look"` in limb JSON, and export is byte-identical in schema.

## Interactions with other planned fixes

- **M1 (`to_do`, same function):** fix_M1 edits `import_unified_from_export` (`_clean` around
  the `P1..3` reads) a few lines below M3's deleted Look lines, and `toggle_limb_parameter`
  just below M3's fallback-record edit. No shared lines, but land sequentially and re-anchor
  by symbol, whichever goes second.
- **M5 (`to_do`):** deletes `merge_and_flip_export` wholesale, including its `_Look`-drop
  line — M3 must not touch that function so M5's deletion stays clean.
- **H3 (`to_do`, save rewrite):** rewrites `save_unified_dataset` merge logic; M3 never
  touches the writer (Look rides inside blobs) — compatible in either order. H3's roundtrip
  test should be written against post-M3 records if M3 lands first.
- **H1 (done, uncommitted in working tree):** source of `labeling_app.py` line drift — this
  plan is anchored to the working tree.
- **M10 (`to_do`):** `parse_xy` lives in the same recovery function; no shared lines — land
  M1/M3/M10 as one batch and re-anchor between them.

## Effort estimate & risk

- **Effort:** ~30-40 min once the decision lands (mechanical deletions + two tests).
- **Risk:** Low. Every deleted write stored a constant; every deleted read produced a
  constant; nothing consumes the field. Old-file compatibility is structural (JSON blobs,
  `.get` readers) and pinned by the new regression test.
- **Rollback:** revert the deletions; no data migration in either direction.
- **Operational footprint:** code-only plus the `docs/EXPORT_NOTES.md` coworker notice,
  **no version bump**. Decision resolved 2026-07-13 — unblocked for implementation.
