# Fix M12 — Analysis: error-swallowing reader + lossy transition metrics

## Problem (re-verified at working tree)

`src/analysis.py` is **unchanged vs HEAD** (the uncommitted H1 diff touches only
`labeling_app.py`; no hunk covers the `analysis` callback). Two halves:

**(1) Error-swallowing reader.** `_read_export_df` (`analysis.py:69-84`) tries a plain
`pd.read_csv`, silently catches **every** exception (`:74`), retries with `skiprows=6`
(legacy pre-header exports), silently catches again (`:82-83`), then raises a generic,
cause-less `ValueError("Could not read export CSV: …")`. FileNotFound/Permission/disk and
parse errors are indistinguishable and unlogged; the `skiprows` retry runs even for OS-level
failures it cannot fix. Rule-0 violation. **User-facing path today:** `LabelingApp.analysis`
(`labeling_app.py`, symbol anchor ~`:3275`) calls `save_data()` then `do_analysis(...)` with
**no try/except** — the ValueError escapes into the Tk button callback, so the default
`report_callback_exception` prints a traceback to the console and **nothing appears in the
UI**. (`do_analysis` itself guards only `frame_rate is None` via print + `return 0`.)

**(2) Lossy transition metrics** in `_compute_limb_metrics` (`analysis.py:109-173`), where
`zones = _flatten_zones(entry["Zones"])` — `{limb}_Zones` is a JSON **list-of-lists, one
bucket per click** (PROJECT.md touch export schema), flattened in click order:
- **Multi-zone loss:** `start_zone = zones[0]` (`:134`) and `end_zone = zones[0]` (`:149`)
  keep only the *first zone of the first click bucket* (bucket order = mask iteration order,
  essentially arbitrary). Other ON/OFF-frame zones and every mid-touch zone feed only
  `current_zones` / `zone_touch_count`, never transitions.
- **Fabricated self-transition (plain bug):** the still-open tail block (`:157-165`) sets
  `end_zone = start_zone` and does `transition_counts[start_zone][end_zone] += 1` — a touch
  with **no observed end** is recorded as a start→start transition, inflating the heatmap
  diagonal (`_build_transition_matrix` / `_plot_transition_heatmap`, `:176-209`).

**Other silent `except` blocks inside analysis.py** (L8 overlap, full inventory):
`_parse_xy_list:38-40` (per-token `ValueError: continue`), `_parse_zones:55-56`
(`JSONDecodeError → []`), `_read_new_template_flag:512-513` (`Exception → False`),
`_get_zone_list:529-530` (`Exception → zones = []`), plus the two in `_read_export_df`.

## How it fits the whole app

Analysis is a **pure consumer** of `export/<video>_export.csv` (PROJECT.md workflow step 5):
Analysis button → `LabelingApp.analysis` → save → `do_analysis` → Plotly HTMLs in `plots/`.
Touch mode only; it writes nothing back into the data pipeline, so metric changes affect
only the generated plots/tables — no persistence or round-trip risk.

## Approaches considered

**A. Typed, logged reader + caller messagebox; drop the unterminated-touch transition;
multi-zone semantics gated on a decision (recommended, chosen).** Smallest faithful diff;
matches the C4/H4 log-and-surface idiom; the uncontroversial bug lands alone.
**B. Delete the reader wrapper, let pandas exceptions propagate raw.** Rejected: loses the
legacy `skiprows=6` fallback (old exports still exist) and gives raw context-free tracebacks.
**C. Fix multi-zone transitions unilaterally now.** Rejected: transition counts feed
research conclusions; what a "transition" means is Lucas's call, not a code-review call.

## Recommended implementation

**Part 1 — reader observability (no decision needed):**
1. `_read_export_df`: catch `OSError` from the *first* read and re-raise immediately with
   `print(f"ERROR: cannot open export CSV {export_path}: {e!r}")` — no retry can fix it.
   For parse-shaped failures (`pd.errors.ParserError`/`EmptyDataError`,
   `UnicodeDecodeError`, missing `Frame` column) log WARN with the exception and try the
   `skiprows=6` legacy fallback as today. If both fail, `raise ExportReadError(...) from
   last_exc` — a new `class ExportReadError(ValueError)` (subclassing keeps any
   `except ValueError` caller working) with the cause chained.
2. `LabelingApp.analysis`: wrap the `do_analysis` call in `try/except Exception` →
   `traceback.print_exc()` + `messagebox.showerror("Analysis failed", ...)` — same idiom as
   the C4 save wiring. Surfaces *any* analysis failure to the annotator.
3. Rule-0 sweep of the other analysis.py silent excepts (inventory above): add one-line
   WARN prints with context; **keep every fallback behaviour unchanged**.

**Part 2a — self-transition bug (land now):** in the `if ongoing and rows:` tail block,
delete the `end_zone`/`transition_counts` lines (`:164-165`); keep duration,
onset-distribution, and `zone_touch_count` accounting. Add a DEBUG print ("touch still open
at last frame; no transition recorded"). Contract: the heatmap counts only touches with an
observed end.

**Part 2b — multi-zone semantics (DECISION RESOLVED — Lucas, 2026-07-13):**
Lucas chose **(ii) pairwise cartesian** — "treat it like multiple transitions happened":
count every `(s, e)` in `start_zones × end_zones` once. Consistent with `zone_touch_count`
(which already counts every touched zone); integer counts. Implement the caveat as planned:
the matrix total exceeds the touch count for ambiguous clicks — footnote it in the heatmap
title so readers of the plots aren't misled. Rejected alternatives kept for the record:
(i) status quo first-zone (arbitrary, drops data), (iii) fractional pairwise (float heatmap),
(iv) compound states (fragments axes).
Sub-question (not explicitly decided): OFF row with no zones — apply the recommended
default, keep `"NN"` (the end is genuinely unobserved); flag it in the implementation
summary so Lucas can overrule cheaply.
2b is a follow-on diff to `:134`, `:149` and the increment at `:150` only; it may land
together with 2a or immediately after.

## Export schema impact

**NONE.** Analysis only *reads* `export/<video>_export.csv`; no writer, column, or value
encoding is touched. Guard: `uv run pytest tests/ -k schema` stays green.

## Edge cases & failure modes

- **Export missing** (normally prevented by the preceding `save_data()`): now logged ERROR +
  messagebox instead of a console-only traceback. **Locked/unreadable file:** same, distinct
  OSError message.
- **Legacy 6-line-header export:** fallback preserved; first-attempt failure now WARN-logged.
- **Header parses but no `Frame` column:** parse-shaped → fallback → typed chained error.
- **Touch open at video end:** duration/zone stats unchanged; transition row simply absent
  (diagonal no longer inflated). A file of *only* open touches yields an empty matrix —
  correct, and visible via the DEBUG line.
- **Empty zones on ON:** `start_zone = "NN"` unchanged; `_get_zone_list` force-appends "NN",
  so matrix reindexing is unaffected.

## Testing / verification plan

Reader + metrics are the **pure testable half** (no Tk). New `tests/test_analysis_m12.py`;
`uv run pytest tests/ -k M12 -v`, red before → green after.
1. `test_M12_open_touch_no_self_transition` — rows for `_compute_limb_metrics` directly: ON
   at frame 2 `[["FACE"]]`, no OFF (mirror the `one_touch_frames` conftest fixture shape).
   **Red:** `transition_counts == {"FACE": {"FACE": 1}}`. **Green:** no transitions;
   `touch_durations` and `zone_touch_count["FACE"]` intact.
2. `test_M12_closed_touch_transition_counted` — ON `[["FACE"]]` @2, OFF `[["BELLY"]]` @5 →
   `{"FACE": {"BELLY": 1}}` (guard, green both sides).
3. `test_M12_reader_missing_file_raises_oserror` — **red:** generic cause-less ValueError;
   **green:** `FileNotFoundError` propagates.
4. `test_M12_reader_parse_failure_logged_and_chained` — garbage bytes failing both attempts
   → `pytest.raises(analysis.ExportReadError)`, `__cause__ is not None`, `capsys` shows the
   WARN + path. (**Red:** class doesn't exist → collection red.)
5. `test_M12_reader_accepts_real_and_legacy_export` — schema-true CSV via
   `export_from_unified` on a small frames dict (as `test_export_schema.py` does), plus a
   copy prefixed with 6 junk lines; both load with `Frame` present (pins the fallback).
6. `test_M12_multizone_pairwise_transitions` (2b, decided): ON `[["BELLY","HIP"]]` → OFF
   `[["FACE"]]` expects `BELLY→FACE` **and** `HIP→FACE` (option (ii)); also assert the
   single-zone case still yields exactly one transition.

**Manual half:** `uv run python src/main.py`, touch mode → Analysis: plots open as before;
lock/rename the export CSV → messagebox + typed chained console error (today: traceback only).

## Interactions with other planned fixes

- **M6 (`to_do/fix_M6.md`) — adjacent, no overlap:** M6 owns the master-HTML
  `open(..., encoding="utf-8")` at `analysis.py:714` + `html.escape(name)`; this plan never
  touches the HTML writer. Different functions; either order; second lander re-anchors.
- **L8 (cleanup pass):** this plan **sweeps the analysis.py-internal silent excepts**
  (log-only) — rule-0 logging *is* half of this finding and it avoids touching the file
  twice. L8's remaining scope is `labeling_app.py` + dialog paths; note that when planning L8.
- **M9 / H1 (`labeling_app.py` churn):** the only labeling_app change here is the try/except
  in the `analysis` method — untouched by H1's landed hunks and M9's `on_close`/`save_data`
  work. Re-anchor by symbol regardless.
- **M4 (landed):** `do_analysis` already guards `frame_rate is None`; unchanged here.

## Effort estimate & risk

- **Effort:** Parts 1 + 2a ~45-60 min (reader refactor, caller wiring, log sweep, 5 tests).
  Part 2b ~20 min (two lines + one test) — decided, can land in the same pass.
- **Risk:** Low. Happy-path reads behave identically; the metric change removes only
  fabricated diagonal counts — existing heatmaps *will* shift down where end-of-video open
  touches existed (expected and correct; flag to Lucas when comparing old plots). 2b risk is
  contained by the decision gate.
- **Rollback:** revert analysis.py + the `analysis`-method try/except; delete the test file.
- **Operational footprint:** code-only, **no version bump**; touch mode only.
