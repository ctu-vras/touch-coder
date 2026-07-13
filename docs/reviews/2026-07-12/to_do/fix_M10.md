# Fix M10 — `parse_xy` silently drops non-digit coordinates → X/Y/Zones desync

## Problem (re-verified at working tree)

`parse_xy` is a closure inside `data_utils.import_unified_from_export` (~`data_utils.py:360`,
review cited `:361-364` — one line of drift). It is defined and used **nowhere else**
(`analysis.py` has its own, float-tolerant `_parse_xy_list`; the unified loader stores X/Y as
JSON and doesn't use it):

```python
def parse_xy(s) -> list[int]:
    if not isinstance(s, str) or not s.strip():
        return []
    return [int(x) for x in s.split(",") if x.strip().isdigit()]
```

Three failure modes, **all reproduced empirically** against the project venv:

1. **Token-level silent drop → desync.** `parse_xy("100,bad,300")` → `[100, 300]` while the
   paired Y cell `"10,20,30"` → `[10, 20, 30]`. X, Y and the JSON `Zones` list-of-lists are
   contractually index-aligned (one zones bucket per click); dropping a token from one list
   shifts every later pair.
2. **Floats and negatives are digit-filtered out.** `"12.5,13"` → `[13]`; `"-5,10"` → `[10]`.
3. **Non-string cells lose the whole click list.** When *every* non-empty row of a
   `{limb}_X` column is a single click (no commas), `pd.read_csv` infers **float64**, the
   cell arrives as `np.float64(12.0)`, `isinstance(s, str)` is False → `[]`. Every
   single-click annotation in that column is **silently discarded during recovery**. This is
   worse than the desync the review describes.

Note: the review's "whitespace-mangled" concern is *not* a real failure mode — tokens are
`.strip()`ed before `isdigit()` and `int(" 34")` parses fine. Verified.

All of it violates rule 0: not a single WARNING is logged for dropped data.

## How it fits the whole app

- **Single call path:** `LabelingApp.load_video` (~`labeling_app.py:3449-3455`, working tree
  with H1 applied — anchor by symbol) calls `import_unified_from_export(export_path)` **only**
  when the unified CSV is empty/missing — the disaster-recovery path for touch mode. `parse_xy`
  feeds `rec["X"]`/`rec["Y"]` for each limb (`:438-439`).
- **Alignment consumers** (why desync is corruption, not cosmetics):
  - `on_middle_click` (touch branch, `labeling_app.py`): finds the nearest dot via
    `zip(xs, ys)` and deletes `xs[i]`, `ys[i]`, `zones[i]` by shared index — a desynced pair
    deletes the wrong coordinate/zone.
  - `_render_diagram_dots` / `last_green`: `zip(xs, ys)` truncates to the shorter list, so
    dots render at mixed-up positions.
  - `export_from_unified` re-serializes the desynced lists on next Save (`_xy_str` +
    `json.dumps(Zones)`) — the corruption is **persisted back into the frozen export**.
- **Can negatives/floats legitimately occur?** The app itself writes
  `int(event.x * (1/diagram_scale))` (`on_left_click`, `:1588,1600`) — always non-negative
  ints, joined by `_xy_str` (`:514-515`). So a healthy export contains only digit tokens.
  Floats enter via (a) pandas dtype inference on read (mode 3 above — unavoidable, reader-side),
  (b) Excel/hand-edited files, (c) data recovered from older/legacy per-limb CSVs. Negatives
  have no legitimate source, but recovery must be lenient, not silently lossy.

## Approaches considered

**A. Tolerant per-token parse + pairwise validation (recommended).** Tokenize X and Y
*together*; convert each token with `int(float(tok))`; when either coordinate of a pair fails,
drop the **pair and its zones bucket**, with a WARNING naming frame/limb/token (rule 0).
Accept numeric scalar cells (mode 3) as one-element lists. **Chosen** — only option that
guarantees the X/Y/Zones alignment invariant while maximizing recovered data.

**B. Tolerant parse only (independent per cell).** `int(float(tok))` per token fixes
floats/scalars but a genuinely garbage token still desyncs the lists. **Rejected.**

**C. Hard error** (abort the import on first bad token). **Rejected:** this *is* the fallback
path — the unified CSV is already gone; aborting recovers nothing. Both loaders' house style
is per-field leniency plus logging, and HANDOFF forbids surprising Save/Load failures here.

## Recommended implementation

Inside `import_unified_from_export`, replace `parse_xy` with a pair-aware helper and call it
once per limb (zones are parsed from JSON *first*, then passed in):

```python
def _xy_tokens(v) -> list[str]:
    if isinstance(v, float) and v != v:          # NaN cell
        return []
    if isinstance(v, (int, float)):              # pandas inferred a numeric column
        return [str(v)]
    if not isinstance(v, str) or not v.strip():
        return []
    return [t for t in v.split(",") if t.strip()]

def parse_xy_pairs(x_cell, y_cell, zones, frame, limb):
    xt, yt = _xy_tokens(x_cell), _xy_tokens(y_cell)
    n = min(len(xt), len(yt))
    if len(xt) != len(yt):
        print(f"WARNING: import_unified_from_export: frame={frame} {limb}: "
              f"{len(xt)} X vs {len(yt)} Y tokens — keeping first {n} pairs", flush=True)
    xs, ys, zs = [], [], []
    for i in range(n):
        try:
            x, y = int(float(xt[i])), int(float(yt[i]))
        except (ValueError, TypeError):
            print(f"WARNING: import_unified_from_export: frame={frame} {limb}: "
                  f"dropping click {i} (X={xt[i]!r}, Y={yt[i]!r})", flush=True)
            continue
        xs.append(x); ys.append(y)
        zs.append(zones[i] if i < len(zones) else [])
    return xs, ys, zs
```

In the limb loop, replace the two `parse_xy(...)` calls + the zones assignment with one
`xr, yr, zones = parse_xy_pairs(_at(row, li["X"], ""), _at(row, li["Y"], ""), zones, f, limb)`
(keep the existing zones-JSON parsing above it verbatim). Add one summary line after the row
loop: total pairs dropped, if nonzero. Deliberate semantics: `int(float("12.5"))` truncates —
matching the app's own `int()` cast on click; negatives are **kept** (renderable data;
silently discarding values is exactly what M10 forbids).

## Export schema impact

**NONE.** Read-side only; no export writer is touched — `tests/test_export_schema.py` must
stay green. Second-order value effect: a recovered-then-re-saved file now round-trips
single-click and float coordinates instead of silently truncating them (repair of corrupt
inputs only; healthy multi-click files parse bit-identically).

## Edge cases & failure modes

- **Healthy file** (object-dtype columns, digit tokens, equal counts): output identical to
  today, zero warnings.
- **All-single-click column** (float64 inference): `12.0` → `["12.0"]` → `int(float(...))` →
  `[12]`. Recovered instead of dropped.
- **Zones list shorter than click count** (already possible today): padded with `[]` per
  pair, so all three lists come out equal-length (consumers currently guard
  `closest_idx < len(zones)`; padding is strictly safer).
- **Both cells empty/NaN** → `([], [], [])`, as today.
- **Log volume:** warnings fire once per bad pair during a once-per-video-load recovery;
  bounded and intentional (rule 0).

## Testing / verification plan

**Automatable (red/green), `tests/test_import_export_xy.py`** — hand-write minimal CSVs in
`tmp_path` (`Frame` + the limb columns under test; missing columns default via `col_idx`):

- `test_M10_bad_token_drops_pair_and_zone`: `LH_X="100,oops,300"`, `LH_Y="10,20,30"`,
  `LH_Zones='[["a"],["b"],["c"]]'`. **Red today:** `X=[100,300]` vs `Y=[10,20,30]` (desync).
  **Green:** `X=[100,300]`, `Y=[10,30]`, `Zones=[["a"],["c"]]`.
- `test_M10_single_click_numeric_column_recovers`: rows where every `LH_X`/`LH_Y` is a bare
  number (forces float64). **Red today:** all `X`/`Y` empty. **Green:** `[12]`/`[34]` etc.
- `test_M10_floats_and_negatives_kept`: `"12.5,-5"` / `"1,2"` → `X=[12,-5]`, `Y=[1,2]`.
- `test_M10_token_count_mismatch_truncates`: `"1,2,3"` / `"7,8"` → 2 pairs, warning emitted.

```
uv run pytest tests/ -k M10 -v
uv run pytest tests/ -k schema -v     # must stay green
```

**Manual:** none strictly needed (pure-function half). Optional: delete a scratch copy's
unified CSV, reopen the video, confirm recovery logs and dots render in the right places.

## Interactions with other planned fixes

- **M6 (utf-8 encoding, to_do):** same file (`data_utils.py`), disjoint functions — line
  drift only; either order.
- **H3 (incremental save rewrite, to_do):** claims the save/export side of `data_utils`;
  `import_unified_from_export` is read-side. No overlap expected; re-anchor by symbol.
- **H1 (landed, uncommitted):** `labeling_app.py` only — the call-site line numbers cited
  above are working-tree values and will drift; anchor by `load_video`.
- **M12 (analysis, unplanned):** `analysis._parse_xy_list` is already float-tolerant — M10
  brings the recovery path in line with the analysis reader's expectations. No shared code.
- **M8 / M11:** different file (`pose_mismatch_data.py`); no interaction.
- **M1 / M3 (to_do, SAME FUNCTION):** both also edit `import_unified_from_export` — M1 wraps
  the `_clean(_at(row, li["P1..3"]))` param values, M3 removes the `{limb}_Look` read. No
  shared lines with the `parse_xy` closure, but whichever of the three lands later must
  re-anchor inside the function; landing them as one batch avoids repeated re-reads.

## Effort estimate & risk

- **Effort:** ~1-1.5 h (helper + limb-loop wiring + four tests).
- **Risk:** Low. Single caller, recovery-only path; healthy files parse identically, and the
  changed cases were previously data loss, so any behavior change is strictly recovery-positive.
- **Rollback:** revert the helper and restore the old closure.
- **Operational footprint:** code-only; no version bump; verify via pytest.
