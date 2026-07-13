# Fix M1 — `toggle_limb_parameter` stores the string `"None"` instead of `None`

## Problem (re-verified at HEAD)

> ⚠️ `labeling_app.py` is being edited **concurrently** (H1 threading fix). Line numbers
> below were correct at plan-writing time but WILL drift — anchor by **symbol names**.

`LabelingApp.toggle_limb_parameter` (`labeling_app.py`, ~`:3033-3055`; the review cited
`:2969` — the file has grown since) hand-rolls the parameter state machine and, on the
OFF→clear transition, stores a **string**:

```python
elif prev == "OFF":
    new_state = "None"        # ~:3053 — the bug (string, not None)
```

The global-parameter twin (`toggle_parameter`, via `_param_next_state`, ~`:1028-1036`)
returns real `None` for the same transition. The string is the **only** occurrence of the
literal in `src/` (grep-verified), and every consumer (`update_limb_parameter_buttons`,
`limb_parameter_colors_at_frame`) treats `"None"` in the same `else` branch as `None` — so
the UI looks identical and the damage is **purely persisted data**:

- `data_utils.save_unified_dataset` (`data_utils.py:189-192`) serializes each limb record
  with `json.dumps(...)` → `{"Par1": "None"}` instead of `{"Par1": null}`; `load_unified_dataset`
  (`:299` `json.loads`) faithfully round-trips it **forever**.
- `data_utils.export_from_unified` (`:540-542`) writes
  `"" if (val is None or val == "") else val` → the literal text `None` lands in the frozen
  `{limb}_Parameter_1..3` export columns, polluting research data (a spurious fourth state
  next to `ON`/`OFF`/empty in downstream group-bys).
- `import_unified_from_export` (`:462-468`) `_clean` maps only NaN/`""` → `None`, so
  export→unified recovery preserves the string too.

Note the cycle still "works": stored `"None"` matches neither branch on the next click, so
the final `else` yields real `None`, then `None → "ON"`. The string persists exactly when
the annotator **stops after clearing a parameter** — the common case.

## How it fits the whole app

- `LimbParams` lives inside each limb's `FrameRecord` (PROJECT.md, in-memory data model);
  it reaches disk via two paths: the unified CSV (JSON round-trip, upsert of changed rows)
  and the export CSV (fully rebuilt from memory on every save).
- Because the unified upsert keeps the **raw JSON string** of untouched rows
  (`save_unified_dataset:210-218` stores `r.get("LH")` unparsed), a load-time normalization
  cleans unified rows only as frames get re-saved — but the **export** (what research
  consumes) is rebuilt from the normalized in-memory dict, so it is fully clean after the
  first save of any session that loaded the data.

## Approaches considered

**A. Reuse `_param_next_state` + normalize legacy `"None"` on load (recommended).** One-line
state-machine fix (deletes the duplicated if-chain) + a tiny normalization in the two unified
loaders so already-persisted strings heal lazily during normal app use. **Chosen.**

**B. Fix the toggle only; leave persisted `"None"`.** **Rejected:** existing unified CSVs and
every future export from them keep the bogus fourth state indefinitely — this is exactly the
research-data hygiene the finding is about.

**C. One-time migration script over `Labeled_data/`.** **Rejected:** out-of-band mutation of
real research data (HANDOFF forbids touching that tree in dev; users would have to run a
tool). Load-time normalization achieves the same result lazily, inside the normal pipeline.

**D. Normalize only at export time.** **Rejected:** leaves unified + in-memory state
inconsistent with the export; the loaders are the correct chokepoint.

## Recommended implementation

1. **`labeling_app.toggle_limb_parameter`** — replace the 8-line if/elif chain
   (~`:3047-3055`) with the existing helper, matching the global-param toggle exactly:

   ```python
   new_state = self._param_next_state(prev)
   ```

2. **`data_utils`** — add a module-level helper and apply it where `LimbParams` enter memory:

   ```python
   def _normalize_param_state(v):
       # Legacy artifact: toggle_limb_parameter used to store the string "None" (M1).
       return None if v in (None, "", "None") else v
   ```

   - `load_unified_dataset`: after `_json_at(...)` parses each limb record (`:306-309`),
     normalize the values of `rec.get("LimbParams")` if it is a dict.
   - `import_unified_from_export`: wrap the three `_clean(_at(row, li["P1..3"]))` values
     (`:462-466`).
   - `load_limb_parameters` (`:719-738`, legacy per-`(limb, frame)` CSV fallback): pass each
     `state` through the helper (the same toggle wrote those files historically).

**Behavioural contract after the change:** OFF→clear stores real `None`; loading any CSV
containing `"None"` yields `None` in memory; the next save exports `""` in that cell.

## Export schema impact

**Columns: NONE** — no column added/removed/renamed/reordered; `tests/test_export_schema.py`
must stay green. **Values: authorized correction** — `{limb}_Parameter_1..3` cells that
previously contained the literal text `None` will export as `""` (empty), the same encoding
every other cleared parameter already uses. This is the intended data-hygiene outcome.

## Edge cases & failure modes

- `LimbParams` missing/non-dict → untouched (loaders already guard; `_ensure_limb_params`
  creates it on demand).
- Global `Params` never contained `"None"` (their toggle always used `_param_next_state`) —
  deliberately left out of the normalization to keep the diff minimal.
- A user who *typed* "None" — impossible; parameter values are only ever set by the toggles.
- Round-trip: normalized `None` serializes as JSON `null`; `_json_at`/`json.loads` handle it
  today (that is the global-param path's existing behaviour).

## Testing / verification plan

**Automatable (red/green), `tests/test_limb_param_none.py`, black-box per HANDOFF:**
- `test_M1_load_unified_normalizes_none_string`: hand-write a unified CSV in `tmp_path`
  whose `LH` JSON contains `{"LimbParams": {"Par1": "None"}, ...}`; `load_unified_dataset`;
  assert `frames[f]["LH"]["LimbParams"]["Par1"] is None`. **Red today** (returns `"None"`).
- `test_M1_export_writes_empty_for_none_string`: frames dict with `LimbParams
  {"Par1": "None"}` → `export_from_unified` → read back; assert `LH_Parameter_1` cell is
  empty/NaN, not `"None"`. **Red today.**
- `test_M1_import_export_normalizes`: export CSV row with `LH_Parameter_1 = "None"` →
  `import_unified_from_export` → assert `None`. **Red today.**

**Manual (GUI half — the toggle itself is a Tk method):** click a limb parameter 3× (ON →
OFF → clear), Save, open `data/<video>_unified.csv` and confirm the limb JSON has `null`;
`grep '"None"'` over the file finds nothing new.

Commands: `uv run pytest tests/ -k M1 -v` (red→green) and `uv run pytest tests/ -k schema -v`
(stays green).

## Interactions with other planned fixes

- **H1 (CONCURRENT, same file):** only a one-line change inside `toggle_limb_parameter`,
  which H1 does not touch — merge risk minimal, but re-anchor by symbol, not line.
- **H3 (save rewrite):** normalization lives in the *loaders*, not the writers — compatible.
- **M2:** neighbouring finding, no shared lines.
- **M3 / M10 (to_do, SAME FUNCTION):** both also edit `import_unified_from_export` — M3
  removes the `{limb}_Look` read, M10 replaces the `parse_xy` closure. No shared lines with
  this fix's `_clean(...)` wrapping, but whichever of the three lands later must re-anchor
  inside the function; landing them as one batch avoids repeated re-reads.

## Effort estimate & risk

- **Effort:** ~30-45 min (one-line toggle fix, one helper + three call sites, three tests).
- **Risk:** Low. `"None"` and `None` are already behaviour-identical in the UI; only
  serialization output changes, and only toward the already-canonical encoding.
- **Rollback:** revert the toggle line + helper applications.
- **Operational footprint:** code-only, no version bump; existing CSVs heal lazily on
  load→save, no data migration required.
