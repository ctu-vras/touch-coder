# TODO

## Notes / ideas (freeform)

- app crashing when closed during frame generation
- Save takes long time for long video, maybe add progress bar?
- Make the app dynamic in terms of size of the UI parts
- make the timeline blue indicator smoother

## Code Review — 2026-07-12

Findings from [docs/reviews/2026-07-12/review.md](docs/reviews/2026-07-12/review.md).
Each gets a fix plan under `to_do/` before implementation. Move to `DONE.md` when green.

> **Hard constraint — export schema is frozen.** The `export/<video>_export.csv` column
> set + order must not change; research pipelines depend on it. Any fix must certify
> "Export schema impact: none". Locked by `tests/test_export_schema.py`. See
> [docs/HANDOFF.md](docs/HANDOFF.md).

### Critical
- **C1** No atomic writes → a crash mid-save corrupts the source-of-truth CSV. — plan: [fix_C1.md](docs/reviews/2026-07-12/to_do/fix_C1.md)
- **C2** Global key bindings (arrows/space/`d`) leak into the Note entry → destroys annotations. — plan: [fix_C2.md](docs/reviews/2026-07-12/to_do/fix_C2.md)
- **C3** ~~Sort Frames broken~~ — **superseded:** feature unused, being removed (see Cleanup below).
- **C4** `save_pose_dataset` silently discards all prior rows on a read error (active mode). — plan: [fix_C4.md](docs/reviews/2026-07-12/to_do/fix_C4.md)

### High
- **H1** Tkinter called from background threads (playback/buffer) → sporadic crashes. — plan: [fix_H1.md](docs/reviews/2026-07-12/to_do/fix_H1.md)
- **H2** Per-edit O(N) timeline rebuild stalls long videos in 3D mode. — plan: [fix_H2.md](docs/reviews/2026-07-12/to_do/fix_H2.md)
- **H3** "Incremental" saves are full re-read + full rewrite every time. — plan: [fix_H3.md](docs/reviews/2026-07-12/to_do/fix_H3.md)
- **H4** Config loaders (all but `load_config`) crash the app on a corrupt `config.json`. — plan: [fix_H4.md](docs/reviews/2026-07-12/to_do/fix_H4.md)
- **H5** OpenCV frame-extraction fallback has no failure signal (silent empty frames). — plan: [fix_H5.md](docs/reviews/2026-07-12/to_do/fix_H5.md)
- **H6** `LimbView`: create-on-read mutation + dead `UserDict` backing store. — plan: [fix_H6.md](docs/reviews/2026-07-12/to_do/fix_H6.md)

### Medium
- **M1** `toggle_limb_parameter` stores the string `"None"` instead of `None`.
- **M2** `mark_bundle_changed(index=None)` ignores its argument.
- **M3** `_Look`/gaze data is never exported (schema mismatch).
- **M4** Division by zero when FPS probe returns 0. — plan: [fix_M4.md](docs/reviews/2026-07-12/to_do/fix_M4.md)
- **M5** `_swap_lr_in_string` corrupts free text (dead-but-dangerous).
- **M6** Missing `encoding="utf-8"` on builtin `open()` calls.
- **M7** Resource leaks + O(n²) polling in frame extraction.
- **M8** `load_pose_dataset` uses the slow `iterrows()` path.
- **M9** `on_close` logic inverted; Cancel leaves a half-dead app.
- **M10** `parse_xy` silently drops non-digit coordinates → X/Y/Zones desync.
- **M11** Pose `ScaleFactor` not clamped on load.
- **M12** Analysis: error-swallowing reader + lossy transition metrics.

### Low / cleanup
- **L1–L12** See review.md (binding scope, dead branches, encodings, dup imports, hardcoded version, etc.).

### Cleanup — feature removals
- **Remove Sort Frames** (unused). Delete `src/sort_frames.py`; remove the Sort button + wiring
  in `ui_components.py` / `labeling_app.py` (`sort_frames()`, `sort_btn`, `_set_sort_analysis_state`,
  the `process_touch_data_strict_transitions` import); drop the `sorted_frames/` references in
  PROJECT.md. Resolves C3 as a side effect. Note: `sort_frames` currently reads the export CSV, so
  the export-schema lock is unaffected by its removal.
