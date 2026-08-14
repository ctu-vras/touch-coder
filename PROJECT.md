# TinyTouch

**TinyTouch** is a desktop annotation tool for behavioral researchers studying how infants learn to understand their own body through self-touch. It provides **Touch Labeling**: frame-by-frame annotation of which limb (LH / RH / LL / RL) touches which body zone, including onset/offset marking, gaze tracking, and customizable global / per-limb parameters.

## Purpose

Researchers record videos of infants and use TinyTouch to produce structured CSV datasets describing every self-contact event. These datasets feed downstream analyses of how babies develop body awareness.

## Tech Stack

- **Python 3.12** with **Tkinter** (desktop GUI; built-in, no separate install)
- **OpenCV** (`opencv-contrib-python`) -- video probing, fallback frame extraction, mask reads
- **imageio-ffmpeg** -- bundles a static `ffmpeg` binary used as the primary frame extractor (much faster than OpenCV)
- **Pillow** -- image / diagram rendering on Tk canvases
- **Pandas** -- CSV / JSON I/O and tabular handling
- **Plotly** -- interactive analysis dashboards (rendered to HTML and opened in the default browser)
- **keyboard** -- global hotkey support
- **PyInstaller** -- packaging into standalone Windows / Linux executables (see [TinyTouch.spec](TinyTouch.spec))

The full pinned list lives in [requirements.txt](requirements.txt).

## Project Structure

```
touch-coder/
├── src/                          # Application source
│   ├── main.py                   # Entry point: instantiates LabelingApp and runs mainloop
│   ├── labeling_app.py           # Main controller (~3k LOC): video, annotation, persistence,
│   │                             # background buffer thread, playback thread, save/export
│   ├── video_model.py            # Video entity (LimbView wrappers over the frames dict)
│   ├── perf_utils.py             # Optional perf timer + periodic summary logging
│   ├── generate_zone_masks.py    # Offline tool: build per-zone PNG masks from a diagram
│   ├── domain/                   # PURE rules: no I/O, no Tk, no plotting, no config
│   │   ├── model.py              # FrameRecord / FrameBundle shapes, LIMBS, LimbView
│   │   ├── project.py            # ProjectPaths -- the only place on-disk layout is built
│   │   ├── touch.py              # Touch-annotation rules (zone hit test, open-onset scan)
│   │   └── touch_stats.py        # Episode reconstruction + touch statistics (Analysis core)
│   ├── adapters/                 # I/O edges: files, cv2/ffmpeg, plotly, config.json
│   │   ├── sqlite_repo.py        # Working-state DB per video (SOURCE OF TRUTH)
│   │   ├── unified_repo.py       # Legacy state READERS (migration / recovery only)
│   │   ├── export_writer.py      # Legacy export schema + metadata sidecar (write)
│   │   ├── export_reader.py      # Export CSV read, incl. legacy 6-line preamble
│   │   ├── plotting.py           # Every plotly figure / CSV table / master HTML
│   │   ├── config.py             # config.json read / write, AppConfig snapshot
│   │   ├── zone_masks.py         # Zone-mask PNG loading + zone-name listing
│   │   ├── frame_extractor.py    # Frame extraction (ffmpeg → OpenCV fallback) + integrity
│   │   ├── frame_buffer.py       # Sliding JPEG buffer + playback advance threads
│   │   ├── video_probe.py        # cv2 frame-count / fps probe
│   │   └── atomic_io.py          # Write-temp-then-replace helper
│   ├── service_layer/            # Use cases: orchestration only
│   │   ├── save_service.py       # The save Unit of Work
│   │   ├── project_service.py    # Video load / project preparation, labeling timer
│   │   ├── annotation_service.py # Click / parameter mutations on the frames dict
│   │   ├── analysis_service.py   # Analysis: read -> validate -> compute -> write
│   │   ├── migration_service.py  # Legacy directory-layout migration
│   │   └── state_migration.py    # Legacy state CSV/JSON -> SQLite (once per project)
│   ├── gui/                      # Tkinter only
│   │   ├── ui_components.py      # Layout, widgets, key/mouse bindings
│   │   ├── theme.py              # Colors / fonts / widget styling
│   │   ├── cloth_app.py          # Clothing-zone selector dialog (Toplevel)
│   │   └── resource_utils.py     # PyInstaller-aware asset path resolution
│   └── resources/                # RUNTIME assets -- everything here ships in the exe
│       └── icons/                # Body diagrams, limb images, zone masks
│           ├── diagram0.png      # Default touch diagram (rendered on canvas)
│           ├── zones3/           # Touch-mode zone masks (one PNG per zone)
│           └── zones3_new_template/  # Alternate zone set (config: new_template = true)
├── data/                         # Output (gitignored) -- one folder per video
├── videos/                       # Source videos (gitignored except cat3.mp4 sample)
├── tests/                        # Benchmark / dev scripts (e.g. frame extraction)
├── assets/, docs/, .github/      # REPO-ONLY static assets, docs, CI workflow
│                                 # (assets/icons_unused/ = quarantined images)
├── config.json                   # User-configurable settings (see "Configuration")
├── requirements.txt              # Pinned Python dependencies
└── TinyTouch.spec                # PyInstaller build spec
```

## Architecture

TinyTouch is a single-process Tkinter app organized around one controller (`LabelingApp`, a `tk.Tk` subclass) that owns:

- the `Video` model (raw video info + per-frame data dict),
- the UI built by `ui_components.build_ui(app)` (frames, canvases, buttons, key bindings),
- two daemon threads for I/O-bound work (frame buffering and playback advance),
- the working-state repository (`adapters.sqlite_repo`) plus the save / project
  services that orchestrate it.

### High-level data flow

```
User input (clicks / keys)
   └─► LabelingApp callbacks
         ├─► mutates Video.frames[frame] (the in-memory FrameBundle)
         ├─► marks bundle "Changed" = True
         └─► repaints diagram canvas + timelines
                                              ┌─► persist_state  (dirty frames -> state DB,
                                              │                   ONE transaction, Tk thread)
On Save / Close ─► LabelingApp.save_data ─────┼─► export_from_unified  (full legacy schema)
                                              └─► write_export_metadata (JSON sidecar)
```

The state write happens on the Tk thread; the two export writes run on a worker
thread against a `deepcopy` snapshot taken beforehand. The SQLite connection
never leaves the Tk thread (`SqliteRepository` raises if it does).

### In-memory data model

Touch-mode state for a frame is a **`FrameBundle`** (see [src/domain/model.py](src/domain/model.py)):

```python
FrameBundle = {
    "LH": FrameRecord,  # left hand
    "RH": FrameRecord,  # right hand
    "LL": FrameRecord,  # left leg
    "RL": FrameRecord,  # right leg
    "Note": str | None,
    "Params": {"Par1": "ON"|"OFF"|None, "Par2": ..., "Par3": ...},  # global per-frame
    "Changed": bool,   # dirty flag, cleared after save
}
```

Each `FrameRecord` holds aligned `X` / `Y` click lists, `Onset` (`"ON"`/`"OFF"`/`""`), `Bodypart`, `Zones` (one bucket per click), `Touch`, and a `LimbParams` dict (`Par1..3`). Gaze is captured by global `Params["Par1"]`, whose user-facing label is configurable (currently `Looking1`).

`Video` exposes `dataLH / dataRH / dataLL / dataRL` as lightweight `LimbView` wrappers around the same shared `frames` dict, so legacy code that indexes a single limb still works.

### Background processes

Two daemon threads start lazily after a video is loaded:

- **`background_update`** -- keeps a sliding window of decoded JPEG frames around the current frame in `self.img_buffer` (read-ahead 50, look-back 30, hard cap ±200, byte budget `BUFFER_MAX_BYTES = 1 GB`). It also flips the on-screen `Buffer Loaded` / `Buffer Loading` indicator.
- **`background_update_play`** -- when Play is pressed, advances `current_frame` only after the buffer reports ready, throttled by `PLAYBACK_BUFFER_PAUSE_S` to avoid stutter.

Frame extraction (`frame_utils.create_frames`) runs synchronously inside a small Tk progress window the first time a video is opened:

1. **Reliability mode** -- if frames already exist for the *non-reliability* original of the same video, copy them over instead of re-extracting.
2. **ffmpeg path** -- bundled via `imageio-ffmpeg`, called as `ffmpeg -i video -q:v 2 -start_number 0 frames/frame%d.jpg` (fast).
3. **OpenCV fallback** -- sequential `cv2.VideoCapture` decode + `cv2.imwrite`, used if ffmpeg is unavailable or fails.

Frame counts are sanity-checked against `cv2.CAP_PROP_FRAME_COUNT` with a 0.1% tolerance (`FRAME_COUNT_TOLERANCE_PCT`).

## Labeling Modes

Mode is chosen on every "Load Video" via a dialog (`ask_labeling_mode`):

- **Labeling mode** -- `Normal` or `Reliability` (the latter appends `_reliability` to the video name, reuses original frames, and keeps a separate dataset for inter-rater agreement).

The choice is persisted in `config.json` (`last_labeling_mode`).

### Touch Annotation

- Navigate frame-by-frame: arrow keys, mouse wheel, `<<` / `<` / `>` / `>>` buttons, Play / Stop, click on Timeline 1 / Timeline 2.
- Pick a limb (RH / LH / RL / LL) via radio buttons; the diagram re-renders with that limb's overlays.
- **Left-click** on the diagram = touch-onset (green dot), **right-click** = touch-offset (red dot), **middle-click** or `d` = remove nearest dot.
- Zones under each click are auto-detected from per-zone PNG masks under [src/resources/icons/zones3/](src/resources/icons/zones3/) (or [src/resources/icons/zones3_new_template/](src/resources/icons/zones3_new_template/) when `new_template = true`). Both directories are loaded by directory scan (`adapters.zone_masks.load_zone_masks`), so *every* PNG in them is live -- adding a file adds a zone.
- Track infant gaze (`Looking: Yes / No`) and up to 3 global + 3 per-limb parameters (button labels are user-editable in Settings → persisted to `config.json`).
- Six "boxes" on the diagram act as catch-all zones (ground, prop, etc).
- Two timelines visualize all touch events; the lower one is the global scrub bar.

## Data Layout on Disk

Each labeled video produces a self-contained folder:

```
data/<video_name>/
├── state/                            # Working state (load/save round-trips here)
│   ├── <video>.db                    # SQLite — THE source of truth (see below)
│   └── *.migrated                    # Pre-SQLite CSV/JSON sources, kept forever
│                                     # after the one-time import (never deleted)
├── export/                           # Final, "publication-ready" artifacts
│   ├── <video>_export.csv            # Flat schema (see below) -- the file analysis reads
│   └── <video>_metadata.json         # Program version, FPS, mode, clothes zones, param labels,
│                                     # total labeling time (hours)
├── frames/                           # frame0.jpg ... frameN.jpg (one per video frame)
└── plots/                            # Plotly HTMLs from "Analysis"
```

Every one of these paths is derived from the `ProjectPaths` dataclass in
[src/domain/project.py](src/domain/project.py) -- no other module hand-builds
them.

### Legacy layout & automatic migration

Before v8.1 the same tree was named `Labeled_data/<video_name>/data/...` and
source videos lived in `Videos/`. Those names are gone from the app's path
logic; the only code that still knows them is
[src/service_layer/migration_service.py](src/service_layer/migration_service.py),
which runs from `main.py` before the Tk app is built (whole-tree pass) and again
from `project_service.prepare_project` for the video being opened. It performs
directory renames only -- `Labeled_data/` -> `data/`, `<video>/data/` ->
`<video>/state/`, `Videos/` -> `videos/` -- via `os.rename`, so no frames tree is
ever copied. It is idempotent, logs every move and every skip reason, and on a
name collision it leaves BOTH copies in place with a `WARN` rather than
overwriting or merging. Older TinyTouch versions cannot read the new layout; the
export CSV format itself is unchanged.

### Working state: `state/<video>.db`

One SQLite database per video ([src/adapters/sqlite_repo.py](src/adapters/sqlite_repo.py))
holds everything the app used to spread over six files: the frames store, notes,
per-frame and per-limb parameters, clothes dots, resume position and the
labeling-time accumulator.

| Table | Holds |
| --- | --- |
| `meta` | `video_name`, `fps`, `last_frame`, `total_frames`, `labeling_time_seconds`, `clothes_diagram_scale`, migration provenance |
| `frames` | one row per frame present in the store (its EXISTENCE is data) + `note` |
| `frame_params` | global `Par1..3`; absent row = key absent, `state IS NULL` = key present but None |
| `limb_records` | per-limb `onset` + a `has_limb_params` flag; a limb with nothing to store has NO row and is rebuilt as `empty_record(limb)` |
| `clicks` | `click_index` IS the `X`/`Y`/`Zones` alignment; `x`/`y` NULL = zone bucket with no click, `zones` NULL = click with no bucket |
| `limb_params` | per-limb `Par1..3`, same NULL semantics as `frame_params` |
| `clothes_dots` | dot id, x, y, comma-joined zone string (frozen sidecar tokenization) |
| `legacy_notes`, `legacy_limb_params` | quarantined pre-unified sidecar rows — stored so nothing is lost, NOT merged into the bundles (they never reached the export, and promoting them would change published datasets) |

Operational choices, all deliberate:

- **Saves are transactional and idempotent.** Each dirty frame (`Changed` is still the tracker) is deleted and re-inserted inside one `BEGIN IMMEDIATE`. Saving twice changes nothing; a crash leaves either the old or the new state. This retired the journal's duplicate-row resolution, load-time compaction and torn-tail repair.
- **`journal_mode=DELETE`, not WAL.** Single-writer desktop app, and WAL's `-wal`/`-shm` sidecars confuse OneDrive / Dropbox sync clients (these folders often live in synced trees). Plus `synchronous=FULL`, `foreign_keys=ON`, `busy_timeout=5000`.
- **One connection per open project, pinned to the Tk thread.** Every method asserts the owning thread; the export worker only ever sees the `deepcopy` snapshot.
- **`PRAGMA user_version`** carries the schema version. A DB from a newer TinyTouch is refused rather than silently down-converted.
- **Not persisted, by decision:** `Bodypart` (reconstructed from the limb key — every writer set it to exactly that and nothing reads it), `Touch` (no writer, no reader, no export column), the retired per-limb `Look`, and `Changed` (a runtime flag no backend ever serialized).

The split between the state DB and `export/<video>_export.csv` stays deliberate:

- **State DB** is the source of truth for round-trips, written incrementally.
- **Export CSV** is rewritten from scratch each save with one row per frame in the canonical legacy column order. Downstream consumers (Analysis, external tooling) read this file.

### First open of a pre-SQLite project

[src/service_layer/state_migration.py](src/service_layer/state_migration.py) runs
once per project, from `project_service.open_state`. If `state/<video>.db` is
missing it reads every legacy source with the **existing readers** — which all
stay in the codebase permanently as the migration + disaster-recovery path —
writes them into a fresh DB in ONE transaction, then renames each consumed
source to `<name>.migrated`. Nothing is ever deleted; on a rename collision both
copies are kept with a `WARN`. If the import raises, the partial DB is discarded
and no source is renamed, so the next attempt starts from the same inputs. Once
the DB exists it wins: a legacy file that reappears is never re-imported.

The recovery ladder is unchanged: `state/<video>_unified.csv`
(`load_unified_dataset`, still resolving duplicate rows last-writer-wins and
tolerating a crash-torn final row) → `export/<video>_export.csv`
(`import_unified_from_export`) → legacy per-limb CSVs (`csv_to_dict`). The export
CSV is a published artifact and a recovery input, so it is never renamed.

The migration is verified end-to-end: the export produced from a migrated DB is
byte-identical to the export produced from the original CSVs
(`tests/integration/test_sqlite_migration.py`).

### Touch export schema

`<video>_export.csv` columns (from `export_from_unified` in [src/adapters/export_writer.py](src/adapters/export_writer.py)):

```
Frame, Time_ms,
LH_X, LH_Y, LH_Onset, LH_Zones,
LL_X, LL_Y, LL_Onset, LL_Zones,
RH_X, RH_Y, RH_Onset, RH_Zones,
RL_X, RL_Y, RL_Onset, RL_Zones,
Parameter_1, Parameter_2, Parameter_3,
LH_Parameter_1..3, LL_Parameter_1..3, RH_Parameter_1..3, RL_Parameter_1..3,
Note
```

`{limb}_X`/`Y` are comma-separated coordinate lists (multiple clicks per frame allowed); `{limb}_Zones` is a JSON list-of-lists aligned with the click list.

For migration notes affecting external analysis pipelines, see [docs/EXPORT_NOTES.md](docs/EXPORT_NOTES.md).

## Configuration

[config.json](config.json) is read on startup and after every Settings dialog "Apply". Keys:

| Key | Purpose |
| --- | --- |
| `diagram_scale` | Diagram render scale (1.0 = native). |
| `dot_size` | Click-marker radius on the diagram. |
| `new_template` | Use the alternate touch zone set + diagram. |
| `minimal_touch_length` | Visualization threshold (ms) for "minimal touch length" label. |
| `parameter1..3` | Display labels for the three global parameter buttons. |
| `limb_parameter1..3` | Display labels for the three per-limb parameter buttons. |
| `video_downscale` | Display-only video downscale factor (1 = full, 2 = half). Affects rendering speed only. |
| `jump_seconds` | Fast-jump distance in seconds for `<<` / `>>` and Shift+Arrow. |
| `perf_enabled` / `perf_log_every_s` / `perf_log_top_n` | Optional `PerfLogger` (see [src/perf_utils.py](src/perf_utils.py)). When on, prints rolling averages of timed code blocks (`background_update`, click handlers, etc.). |
| `last_labeling_mode` | Last-chosen `Normal` / `Reliability`. |

When the app is run from a PyInstaller bundle, `config_utils._ensure_config_file()` copies the bundled default to the install directory the first time so users get a writable copy.

## Application Workflow

1. **Load Video** -- pick `Normal` / `Reliability`, then select a video file (mp4/mov/avi/mkv/flv/wmv). The video is copied into `videos/` and the project tree is created under `data/<video>/` so the working set is self-contained, frames are extracted (or copied for Reliability), prior state is loaded, and the buffering thread starts.
2. **Clothes** -- mark which body zones are covered with clothes; stored in the state DB's `clothes_dots` and surfaced in the export metadata.
3. **Annotate** -- pick a limb, click onsets/offsets, set gaze and parameters, type notes. Edits stay in memory until Save.
4. **Save** -- `Save` button (or auto on Close / before Load) writes the dirty frames to the state DB (one transaction), then the export CSV (full) and the metadata sidecar. The `Changed` flags are cleared.
5. **Analysis** -- runs `analysis_service.run_analysis` over the export CSV: per-limb summary stats, transition heatmaps, touch-trajectory plots, histograms, and a master HTML. Output lands in `plots/`; the service returns the master HTML path and `LabelingApp.analysis` opens it in the browser. See "Analysis conventions" below.
6. **Close** -- final save, persists labeling-time accumulator and last frame position.

### Analysis conventions

The Analysis pipeline is three layers, in this order, with all computation
finished before the first file is written (so a failure can never leave a
half-populated `plots/`):

```
adapters.export_reader.read_export_df   read export/<name>_export.csv
        │                               (tolerates the legacy 6-line preamble)
        ▼
domain.touch_stats.parse_export         validate schema, rebuild Episodes
        │  summarize / transitions      per-limb stats + zone transitions
        ▼
adapters.plotting.write_*               heatmaps, trajectory, tables, histograms,
        │                               master_<name>.html
        ▼
service_layer.analysis_service          orchestrates the above, returns the path
                                        (LabelingApp opens the browser)
```

Rules that downstream research depends on -- documented at length in
[src/domain/touch_stats.py](src/domain/touch_stats.py):

| Rule | Meaning |
| --- | --- |
| **Half-open `[ON, OFF)`** | A touch's duration is `OFF - ON`: active on the onset frame, offset frame excluded. Shortest closed touch = 1 frame. Matches what the labeler's timeline shades. |
| **Open touches are censored** | An `ON` with no `OFF` is `Episode.closed=False`: no duration, excluded from totals / percentages / means / stdev / transitions / histograms, but counted and reported as "open (unterminated)" in the tables, the master page and a `WARN` log. |
| **Transitions are pairwise** | A touch with 2 start zones and 2 end zones contributes 4 heatmap counts, so heatmap totals exceed the touch count. Stated in the heatmap subtitle. |
| **`minimal_touch_length` is not a filter** | It is a GUI display threshold only; analysis counts every closed touch, 1-frame ones included. |
| **Frame rate may be unusable** | `None` / `0` / negative fps (some containers report 0) never crashes: frame-based results are produced in full, every seconds-based value is empty, and the duration histogram switches to frame buckets. |
| **fps provenance** | An explicitly passed, usable frame rate wins; otherwise `"Frame Rate"` from `export/<name>_metadata.json`; otherwise frame-only mode. The winning source is logged. |

## Local Build

```bash
pyinstaller TinyTouch.spec
```

Produces a standalone executable in `dist/`. The spec bundles two `datas` entries, and their destinations must stay in step with [src/gui/resource_utils.py](src/gui/resource_utils.py):

| Spec `datas` | Bundled at | Read via |
| --- | --- | --- |
| `config.json` | `<_MEIPASS>/config.json` | `resource_path("config.json")` |
| `src/resources` | `<_MEIPASS>/resources/…` | `asset_path("icons/…")` |

`resource_path` resolves distribution-root files; `asset_path` resolves the bundled runtime-asset tree (`src/resources/` from source, `<_MEIPASS>/resources/` when frozen). Change one and you must change the other.

## Releasing a New Version

A GitHub Actions workflow ([.github/workflows/build.yml](.github/workflows/build.yml)) builds and publishes Windows x64, Linux x64, and Linux Legacy x64 (Bullseye / Python 3.11) artifacts on every `v*` tag push, then creates a GitHub Release with the zips attached.

**Full step-by-step instructions** (pre-release checklist, version bump, tagging, monitoring, rollback, common pitfalls): see **[docs/RELEASING.md](docs/RELEASING.md)**.

Quick reference:

```bash
# Bump src/video_model.py program_version, commit, push to master, then:
git tag v7.7.0
git push origin v7.7.0
```
