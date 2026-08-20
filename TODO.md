# TODO
- parameter 1 in the header or not?

## Notes / ideas (freeform)

- merge branches
- docs
- rename repo to tinytouch
- position of the version
- description in config
- how to delete .db for the videos and when?
- uninstall option?
- put info about 3d pose in docs
- flexible diagram size?


- app crashing when closed during frame generation
- Save takes long time for long video, maybe add progress bar?
- Make the app dynamic in terms of size of the UI parts
- make the timeline blue indicator smoother
- make UI pretty?
- timeline moving sootmhhly not per 100 frames
- new release

## Code Review — 2026-07-12

Findings from [docs/reviews/2026-07-12/review.md](docs/reviews/2026-07-12/review.md).
Each gets a fix plan under `to_do/` before implementation. Move to `DONE.md` when green.

> **Hard constraint — export schema is frozen.** The `export/<video>_export.csv` column
> set + order must not change; research pipelines depend on it. Any fix must certify
> "Export schema impact: none". Locked by `tests/test_export_schema.py`. See
> [docs/HANDOFF.md](docs/HANDOFF.md).

### Low / cleanup
- **L1–L12** See review.md (binding scope, dead branches, encodings, dup imports, hardcoded version, etc.).
