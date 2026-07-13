# TODO

## Notes / ideas (freeform)

- app crashing when closed during frame generation
- Save takes long time for long video, maybe add progress bar?
- Make the app dynamic in terms of size of the UI parts
- make the timeline blue indicator smoother
- make UI pretty?

## Code Review — 2026-07-12

Findings from [docs/reviews/2026-07-12/review.md](docs/reviews/2026-07-12/review.md).
Each gets a fix plan under `to_do/` before implementation. Move to `DONE.md` when green.

> **Hard constraint — export schema is frozen.** The `export/<video>_export.csv` column
> set + order must not change; research pipelines depend on it. Any fix must certify
> "Export schema impact: none". Locked by `tests/test_export_schema.py`. See
> [docs/HANDOFF.md](docs/HANDOFF.md).

### High
- **H2** Per-edit O(N) timeline rebuild stalls long videos in 3D mode. — plan: [fix_H2.md](docs/reviews/2026-07-12/to_do/fix_H2.md)
- **H3** "Incremental" saves are full re-read + full rewrite every time. — plan: [fix_H3.md](docs/reviews/2026-07-12/to_do/fix_H3.md)

### Medium
- **M9** `on_close` logic inverted; Cancel leaves a half-dead app. — plan: [fix_M9.md](docs/reviews/2026-07-12/to_do/fix_M9.md)

### Low / cleanup
- **L1–L12** See review.md (binding scope, dead branches, encodings, dup imports, hardcoded version, etc.).
