# TinyTouch architecture

**TinyTouch** is a desktop annotation tool for behavioral researchers studying how infants learn to understand their own body through self-touch. It provides **Touch Labeling**: frame-by-frame annotation of which limb (LH / RH / LL / RL) touches which body zone, including onset/offset marking, gaze tracking, and customizable global / per-limb parameters.

This is the developer-facing document. See also:

| Document | Audience |
| --- | --- |
| [README.md](README.md) | Users — install and quick start |
| [docs/ANNOTATION_GUIDE.md](docs/ANNOTATION_GUIDE.md) | Annotators — the coding manual |
| [docs/DATA_FORMAT.md](docs/DATA_FORMAT.md) | Data consumers — the frozen export specification |

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
│   ├── main.py                   # Entry point: builds and runs LabelingApp.mainloop()
│   ├── labeling_app.py           # Main controller (~2.3k LOC): video, annotation, persistence,
│   │                             # frame buffer + playback wiring, save/export orchestration
│   ├── app_info.py               # Application version metadata
│   ├── video_model.py            # Video entity (LimbView wrappers over frames)
│   ├── perf_utils.py             # Optional perf timer + periodic summary logging
│   ├── log_setup.py              # Session handlers, retention and exception hooks
│   ├── generate_zone_masks.py    # Offline tool: build per-zone PNG masks from a diagram
│   ├── domain/                   # PURE rules: no I/O, no Tk, no plotting, no config
│   │   ├── model.py              # FrameRecord / FrameBundle shapes, LIMBS, LimbView
│   │   ├── project.py            # ProjectPaths -- the only place on-disk layout is built
│   │   ├── touch.py              # Touch-annotation rules (zone hit test, open-onset scan)
│   │   └── touch_stats.py        # Episode reconstruction + touch statistics (Analysis core)
│   ├── adapters/                 # I/O edges: files, cv2/ffmpeg, plotly, config.json
│   │   ├── sqlite_repo.py        # Working-state DB per video (SOURCE OF TRUTH)
│   │   ├── export_writer.py      # Legacy export schema + metadata sidecar (write)
│   │   ├── export_reader.py      # Export CSV read, incl. legacy 6-line preamble
│   │   ├── plotting.py           # Every plotly figure / CSV table (ReportFigure factory)
│   │   ├── report_page.py        # master_<name>.html layout/CSS (inlines the figures)
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
│   │   └── analysis_service.py   # Analysis: read -> validate -> compute -> write
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
├── tests/                        # pytest suite: unit/ + integration/ + e2e/ (see "Testing")
├── assets/, docs/, .github/      # REPO-ONLY static assets, docs, CI workflow
├── config.json                   # User-configurable settings (see "Configuration")
├── pytest.ini                    # testpaths, the `gui` marker, default deselection
├── requirements.txt              # Pinned Python dependencies
└── TinyTouch.spec                # PyInstaller build spec
```

## Architecture

The codebase follows the layering of *Architecture Patterns with Python*: four layers with a
one-way dependency rule.

| Layer | May import | Holds | Must never |
| --- | --- | --- | --- |
| `domain/` | stdlib, pandas (as a data container only) | Data shapes and pure rules: `FrameRecord` / `FrameBundle`, `ProjectPaths`, zone hit test, episode reconstruction and statistics | Touch the filesystem, Tk, plotly, cv2, adapters or services |
| `adapters/` | `domain`, third-party I/O libraries | The I/O edges: SQLite, CSV/JSON writers and readers, cv2/ffmpeg, plotly figures, config file, atomic writes | Contain a rule that could live in `domain` |
| `service_layer/` | `domain`, `adapters` | Use cases: the save Unit of Work, project open, annotation mutations, analysis run | Import Tk or build a figure |
| `gui/` + `labeling_app.py` | everything below | Tkinter only: widgets, bindings, canvases, dialogs, threads | Reimplement a rule or a file format |

The domain rule is enforced mechanically: `tests/unit/test_touch_stats.py`
(`test_domain_layer_imports_nothing_from_the_outer_layers`) AST-parses every file under
`src/domain/` and fails on a banned import. Two conventions ride on top of it: the services
never read `config.json` (the GUI passes its `AppConfig` snapshot down as arguments), and
`adapters.plotting` never computes a statistic — it receives finished domain objects, so
the numbers are reproducible without plotly.

TinyTouch is a single-process Tkinter app organized around one controller (`LabelingApp`, a `tk.Tk` subclass) that owns:

- the `Video` model (raw video info + per-frame data dict),
- the UI built by `ui_components.build_ui(app)` (frames, canvases, buttons, key bindings),
- a `FrameBuffer` (`adapters.frame_buffer`) owning the read-ahead and playback threads,
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

[`adapters.frame_buffer.FrameBuffer`](src/adapters/frame_buffer.py) owns two daemon loops plus
an adaptive decode pool (`max(3, min(8, cpu_count // 2))` workers — PIL releases the GIL
during decode/resize, so the parallelism is real); they start lazily after a video is loaded.
The GUI is reached only through the callbacks injected at construction (`schedule_on_ui`,
`on_status_change`, `apply_play_advance`, …), so no worker ever touches a Tk widget — the
buffering loop reads a `BufferContext` snapshot instead of calling `winfo_width()`.

- **Decode pipeline** -- each frame load computes its target size from the JPEG header
  (`compute_target_size`: fit the source into `display / downscale` aspect-preserving,
  upscaling low-res sources to fill big monitors), asks libjpeg via `Image.draft` to decode
  at a reduced DCT scale when the source is larger, then does one BILINEAR resize (skipped
  when draft lands exactly on target). Frames are buffered at final display size, so the UI
  thread never resizes at paint time.
- **Buffering loop** -- keeps decoded JPEG frames around the current frame, with an
  asymmetric, velocity-aware window: base read-ahead 50 / look-back 30, widened in the
  direction of the last navigation step (at least `2 × jump_frame_count`) and halved on the
  other side. The window is additionally capped at ~half the byte budget (using the running
  average frame size) so huge upscaled frames can't make eviction fight the submission loop.
  On a jump, queued-but-not-started decodes outside the new window are cancelled via their
  `Future`s so the jump target doesn't wait behind stale work. Eviction keeps at least ±200
  frames and scales with the window; the byte budget is `BUFFER_MAX_BYTES = 1 GB`. It also
  flips the on-screen `Buffer: Loaded` / `Buffer: Loading` chip.
- **Playback loop** -- when Play is pressed, advances `current_frame` only once the next
  `PLAYBACK_BUFFER_AHEAD = 3` frames are buffered, pausing `PLAYBACK_BUFFER_PAUSE_S = 0.15` s
  when they are not, so playback stalls briefly rather than stutters.

Frame extraction (`adapters.frame_extractor.create_frames`) runs synchronously inside a small Tk progress window the first time a video is opened:

1. **Reliability mode** -- if frames already exist for the *non-reliability* original of the same video, copy them over instead of re-extracting.
2. **ffmpeg path** -- bundled via `imageio-ffmpeg`, called as `ffmpeg -i video -q:v 2 -start_number 0 frames/frame%d.jpg` (fast).
3. **OpenCV fallback** -- sequential `cv2.VideoCapture` decode + `cv2.imwrite`, used if ffmpeg is unavailable or fails.

Frame counts are sanity-checked against `cv2.CAP_PROP_FRAME_COUNT` with a 0.1% tolerance (`FRAME_COUNT_TOLERANCE_PCT`).

## Labeling Modes

Mode is chosen on every "Load Video" via a dialog (`ask_labeling_mode`):

- **Labeling mode** -- `Normal` or `Reliability` (the latter appends `_reliability` to the video name, reuses original frames, and keeps a separate dataset for inter-rater agreement).

The choice is persisted in `config.json` (`last_labeling_mode`).

### Touch Annotation

- Navigate frame-by-frame: `←` / `→`, mouse wheel, `<<` / `<` / `>` / `>>` buttons, `Space` or Play / Stop, click on either timeline. `Shift`+arrow and `<<` / `>>` jump by `jump_seconds` worth of frames. All key bindings live in `gui.ui_components._bind_navigation` and are suppressed while the note entry has focus (`_guard_key`).
- Pick a limb (RH / LH / RL / LL) via radio buttons; the diagram re-renders with that limb's overlays.
- **Left-click** on the diagram = touch-onset (green dot), **right-click** = touch-offset (red dot), **middle-click** or `d` = remove the dot nearest the pointer (within ~20 display px).
- Zones under each click are auto-detected from per-zone PNG masks under [src/resources/icons/zones3/](src/resources/icons/zones3/) (or [src/resources/icons/zones3_new_template/](src/resources/icons/zones3_new_template/) when `new_template = true`). Both directories are loaded by directory scan (`adapters.zone_masks.load_zone_masks`), so *every* PNG in them is live -- adding a file adds a zone. Masks are black-on-white; a hit is pixel `== 0` and a miss yields the `NN` sentinel (`domain.touch.zones_at`). The masks OVERLAP on 1.4% / 2.2% of the diagram (almost all of it the `LINE` mask lying over the zones it separates), so overlaps resolve by **precedence, not filename order**: real anatomical zone > `BOX1..BOX6` > `OUTSIDE` > `LINE` > `NN`. `LINE` means "the click is exactly between two zones", so it is only recorded when nothing else claims the pixel; sorted filename order is just the tie-break inside one tier (in practice only box-vs-box borders). The real/catch-all split is one predicate, `domain.touch.is_catch_all_zone`, shared with `touch_stats.zone_sort_key`. A click that resolves to `NN` is logged with a `WARN` naming the frame, limb and coordinates.
- Track infant gaze and up to 3 global + 3 per-limb parameters. Each button is a three-state toggle (`unset → ON → OFF → unset`, `domain.touch.cycle_param_state`); gaze is global `Par1`. Button labels are user-editable in Settings → persisted to `config.json` and written into the export metadata, but the export COLUMN names never change.
- Six "boxes" on the diagram act as catch-all zones (ground, prop, etc).
- Two timelines visualize all touch events; the upper, slimmer one is the global scrub bar and the taller one is the 100-frame detail view for the selected limb.

The annotator-facing version of all of this is [docs/ANNOTATION_GUIDE.md](docs/ANNOTATION_GUIDE.md).

## Data Layout on Disk

Each labeled video produces a self-contained folder:

```
data/<video_name>/
├── state/                            # Working state (load/save round-trips here)
│   └── <video>.db                    # SQLite — THE source of truth (see below)
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

### Supported data layout (no migration path)

Up to and including `v8.0.0` the same tree was named
`Labeled_data/<video_name>/data/...`, source videos lived in `Videos/`, and the
working state was a `<video>_unified.csv` journal plus five CSV/JSON/TXT
sidecars. An automatic converter for both existed during 9.0 development and was
**removed before release** — hence the major version bump. This build reads the
layout above and `state/<video>.db` only.

There is therefore no upgrade path from `8.0.x` data. Opening a pre-9.0 project
does not fail loudly: `open_state` finds no `state/<video>.db`, creates an empty
one, and the project shows **zero annotations** while the real data sits unread
in `<video>_unified.csv`. Because `save_service.run_export` rewrites the export
CSV from the in-memory store on every save, the first Save then overwrites
`export/<video>_export.csv` with empty rows. Treat 8.0.x project folders as
read-only archives and keep a copy of their export CSVs.

The export CSV format itself is unchanged across the rename, and
`adapters.export_reader` still tolerates the pre-8.x 6-line preamble, so
Analysis reads old exports fine.

### Working state: `state/<video>.db`

One SQLite database per video ([src/adapters/sqlite_repo.py](src/adapters/sqlite_repo.py))
holds everything the app used to spread over six files: the frames store, notes,
per-frame and per-limb parameters, clothes dots, resume position and the
labeling-time accumulator.

| Table | Holds |
| --- | --- |
| `meta` | `video_name`, `fps`, `last_frame`, `total_frames`, `labeling_time_seconds`, `clothes_diagram_scale` |
| `frames` | one row per frame present in the store (its EXISTENCE is data) + `note` |
| `frame_params` | global `Par1..3`; absent row = key absent, `state IS NULL` = key present but None |
| `limb_records` | per-limb `onset` + a `has_limb_params` flag; a limb with nothing to store has NO row and is rebuilt as `empty_record(limb)` |
| `clicks` | `click_index` IS the `X`/`Y`/`Zones` alignment; `x`/`y` NULL = zone bucket with no click, `zones` NULL = click with no bucket |
| `limb_params` | per-limb `Par1..3`, same NULL semantics as `frame_params` |
| `clothes_dots` | dot id, x, y, comma-joined zone string (frozen sidecar tokenization) |

Databases created during 9.0 development may also carry two now-unused tables,
`legacy_notes` and `legacy_limb_params`, plus `migrated_at` / `migrated_sources`
rows in `meta`. Nothing reads or writes them; `CREATE TABLE IF NOT EXISTS` simply
no longer lists them, so they are inert leftovers rather than schema.

Operational choices, all deliberate:

- **Saves are transactional and idempotent.** Each dirty frame (`Changed` is still the tracker) is deleted and re-inserted inside one `BEGIN IMMEDIATE`. Saving twice changes nothing; a crash leaves either the old or the new state. This retired the journal's duplicate-row resolution, load-time compaction and torn-tail repair.
- **`journal_mode=DELETE`, not WAL.** Single-writer desktop app, and WAL's `-wal`/`-shm` sidecars confuse OneDrive / Dropbox sync clients (these folders often live in synced trees). Plus `synchronous=FULL`, `foreign_keys=ON`, `busy_timeout=5000`.
- **One connection per open project, pinned to the Tk thread.** Every method asserts the owning thread; the export worker only ever sees the `deepcopy` snapshot.
- **`PRAGMA user_version`** carries the schema version. A DB from a newer TinyTouch is refused rather than silently down-converted.
- **Not persisted, by decision:** `Bodypart` (reconstructed from the limb key — every writer set it to exactly that and nothing reads it), `Touch` (no writer, no reader, no export column), the retired per-limb `Look`, and `Changed` (a runtime flag no backend ever serialized).

The split between the state DB and `export/<video>_export.csv` stays deliberate:

- **State DB** is the source of truth for round-trips, written incrementally.
- **Export CSV** is rewritten from scratch each save with one row per frame in the canonical legacy column order. Downstream consumers (Analysis, external tooling) read this file.

### Opening a project

`project_service.open_state` constructs `SqliteRepository(paths.state_db)`,
which creates the schema when the file is new, so the same call is both the
create path for a brand-new project and the open path for an existing one. It
then re-stamps the provenance meta (`video_name`, `fps`,
`created_by_version`) on every open, which is cheap and keeps an orphaned DB
self-describing. `repo.load_frames()` stays a separate step so the GUI can start
its labeling timer between the two.

`SCHEMA_VERSION` stays at 1: the removed migration never changed the shape of a
table this build reads, so no DB needs upgrading. `_upgrade_from` still refuses to
open a database written by a NEWER build rather than silently dropping fields on
the next save.

### Touch export schema

The full, normative specification — every column, every cell encoding, the metadata
sidecar, and the semantic conventions downstream analysis must follow — is
**[docs/DATA_FORMAT.md](docs/DATA_FORMAT.md)**. Summary for orientation only:

`<video>_export.csv` columns (from `export_from_unified` in [src/adapters/export_writer.py](src/adapters/export_writer.py)), one row per frame `0..total_frames` inclusive:

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

Note the limb block order is **LH, LL, RH, RL** — not the `domain.model.LIMBS` order
(`LH, RH, LL, RL`) the rest of the app uses. `{limb}_X`/`Y` are comma-separated coordinate lists (multiple clicks per frame allowed); `{limb}_Zones` is a JSON list-of-lists aligned with the click list.

**This schema and its byte-level encoding are FROZEN.** Three test files pin it:
`tests/unit/test_export_schema.py` (column set + order),
`tests/unit/test_export_golden_master.py` (a checked-in fixture compared byte for byte) and
`tests/unit/test_export_metadata.py` (sidecar keys, order and formatting). Changing any of
them means breaking published research datasets, and requires coordinating with the
downstream pipeline first.

## Configuration

[config.json](config.json) is read on startup and after every Settings dialog "Apply". It is
parsed once into an `AppConfig` snapshot ([src/adapters/config.py](src/adapters/config.py))
that the GUI holds and passes down; nothing below the GUI re-reads the file. Unknown keys
survive a Settings round-trip (`AppConfig.raw` keeps the full parsed dict). Keys:

| Key | Settings UI | Purpose |
| --- | --- | --- |
| `diagram_scale` | yes | Diagram render scale (1.0 = native). Display only — stored click coordinates are always at native scale. |
| `dot_size` | yes | Click-marker radius on the diagram. |
| `new_template` | no | Use the alternate touch zone set + diagram. |
| `minimal_touch_length` | no | Visualization threshold (ms) shown as the "Minimal Touch Length" readout, converted to frames. **Filters nothing**, in the app or in Analysis. |
| `parameter1..3` | yes | Display labels for the three global parameter buttons. |
| `limb_parameter1..3` | yes | Display labels for the three per-limb parameter buttons. |
| `video_downscale` | yes | Display-only video downscale factor (1 = full, 2 = half). Affects rendering speed only. |
| `jump_seconds` | yes | Fast-jump distance in seconds for `<<` / `>>` and Shift+Arrow; multiplied by the frame rate at load time into `jump_frame_count`. |
| `realtime_arrow_hold` | yes | Holding an arrow key plays at the video's frame rate instead of stepping. |
| `perf_enabled` / `perf_log_every_s` / `perf_log_top_n` | no | Optional `PerfLogger` (see [src/perf_utils.py](src/perf_utils.py)). When on, prints rolling averages of timed code blocks (buffer loop, click handlers, etc.). |
| `log_level_console` | no | Console threshold (`INFO` by default); `TINYTOUCH_LOG_LEVEL` overrides it for development. The session file always records `DEBUG`. |
| `log_keep_files` | no | Number of newest session log files retained (default 20). |
| `max_display_width` / `max_display_height` | no | Optional hard caps on the rendered video size (`0` = unlimited). |
| `last_labeling_mode` | no | Last-chosen `Normal` / `Reliability`; written by the mode dialog. |

Defaults for every key live in `adapters.config.CONFIG_DEFAULTS`, so a missing or corrupt
`config.json` degrades to defaults with a WARN instead of failing. When the app is run from
a PyInstaller bundle, `adapters.config._ensure_config_file()` copies the bundled default to
the install directory the first time so users get a writable copy.

## Observability

[`src/log_setup.py`](src/log_setup.py) configures stdlib `logging` once at the
application composition root. Runtime modules use `logging.getLogger(__name__)`;
annotation activity uses the dedicated `annot` logger and performance summaries use
`perf`. The root logger accepts `DEBUG`, while handler levels decide where records go:

- The console defaults to `INFO`. It shows lifecycle messages and deliberate annotation
  actions (clicks, deletions, limb/parameter selections, notes, clothes, mode and settings),
  so an annotator can see that input was registered. Automatic frame movement and buffer
  detail stay at `DEBUG`.
- A UTF-8 session file always receives `DEBUG` and above. Source runs write under
  `logs/`; frozen Windows builds prefer `%LOCALAPPDATA%/TinyTouch/logs`, and Linux builds
  prefer `$XDG_STATE_HOME/tinytouch/logs` or `~/.local/state/tinytouch/logs`. Settings ->
  **Open Logs Folder** opens the active location.
- `sys.excepthook`, `threading.excepthook` and Tk's `report_callback_exception` route
  unhandled failures to the file. Caught exceptions use `logger.exception` at their
  handling boundary.

Log records are diagnostics, not a recovery format: the state DB remains the only source
of truth. Annotation records intentionally include locally entered note text, but no log
data leaves the machine. The domain layer remains independent of outer packages; its
statistics module may emit diagnostics through stdlib logging as the one narrow side-effect
exception to otherwise pure computation.

## Application Workflow

1. **Load Video** -- pick `Normal` / `Reliability`, then select a video file (mp4/mov/avi/mkv/flv/wmv). The video is copied into `videos/` and the project tree is created under `data/<video>/` so the working set is self-contained, frames are extracted (or copied for Reliability), prior state is loaded, and the buffering thread starts. Loading **another video mid-session** is supported: `LabelingApp._unload_current_video` first persists the open project exactly like Close does (save, last position, labeling time), then detaches it in the one order that cannot mix two projects' data — video dropped (idling the worker threads) → frame buffer emptied (generation bump) → state DB closed — before the new project is opened. The new session is built against a local `Video` and published only after its frames exist; a failure mid-load falls back to the clean "no video loaded" state, and cancelling either dialog leaves the open project untouched.
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
adapters.plotting.write_*               heatmaps, trajectory, tables, histograms —
        │                               each returns a `ReportFigure` fragment
        ▼
adapters.report_page.write_master_html  arranges the fragments into
        │                               master_<name>.html (layout/CSS only)
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
| **Zoneless ends fall back to `NN`** | Only for transitions, so a touch with no zone on an edge still appears in the matrix. `zone_touch_count` never gets the sentinel — it counts observed zones only. |
| **fps provenance** | An explicitly passed, usable frame rate wins; otherwise `"Frame Rate"` from `export/<name>_metadata.json`; otherwise frame-only mode. The winning source is logged. |

These rules are also stated for external readers in
[docs/DATA_FORMAT.md](docs/DATA_FORMAT.md#3-semantic-conventions); keep the two in step.

The written artifacts and their file names are a contract too (the master page links
them and users bookmark them): `heatmap_<LIMB>.html`, `touch_trajectory.html`,
`analysis_table_frames.csv`, `analysis_table_seconds.csv`, `table.html`, `histogram.html`,
`histogram_2.html`, `master_<name>.html`.

The master page inlines every figure `<div>` into ONE scrolling document and loads a
single shared `plots/plotly.min.js` (written by plotly via `include_plotlyjs="directory"`).
The standalone figure files reference the same bundle, so a `plots/` folder must travel
as a whole when shared. Wheel-zoom is disabled on every figure (`scrollZoom: false`) —
the mouse wheel scrolls the page; zooming is an explicit drag-select.

## Testing

```bash
uv run pytest                 # 298 passed, 7 deselected
uv run pytest -m gui          # the excluded end-to-end GUI tests (needs a display)
```

[pytest.ini](pytest.ini) sets `testpaths = tests` and `addopts = -m "not gui"`, so the
default run is headless. The suite is a pyramid:

| Directory | Scope |
| --- | --- |
| `tests/unit/` | Pure rules and single adapters: export encoding, config, SQLite repository, touch statistics, zone detection, thread-boundary guards, theme. |
| `tests/integration/` | Several layers together: the analysis pipeline end to end, the load/save lifecycle. |
| `tests/e2e/` | A real Tk root driven through `tests/e2e/gui_driver.py` — smoke test, annotate/save/export, loading a second video mid-session, close during extraction. Marked `gui` and deselected by default. |

The guarantee worth knowing: the three export-lock test files described above pin the
published format. Note what is NOT pinned any more — with
`import_unified_from_export` gone there is no test that reads an export CSV back
into an equivalent store, so a change that silently drops a field from the
export will be caught by the golden-master bytes but not by a round-trip.

## Local Build

```bash
uv run pyinstaller TinyTouch.spec
```

Produces a standalone executable in `dist/`. The spec bundles two `datas` entries, and their destinations must stay in step with [src/gui/resource_utils.py](src/gui/resource_utils.py):

| Spec `datas` | Bundled at | Read via |
| --- | --- | --- |
| `config.json` | `<_MEIPASS>/config.json` | `resource_path("config.json")` |
| `src/resources` | `<_MEIPASS>/resources/…` | `asset_path("icons/…")` |

`resource_path` resolves distribution-root files; `asset_path` resolves the bundled runtime-asset tree (`src/resources/` from source, `<_MEIPASS>/resources/` when frozen). Change one and you must change the other.

## Releasing a New Version

A GitHub Actions workflow ([.github/workflows/build.yml](.github/workflows/build.yml)) builds and publishes Windows x64, Linux x64, and Linux Legacy x64 (Bullseye / Python 3.11) artifacts on every `v*` tag push, then creates a GitHub Release with the zips attached.

Quick reference:

```bash
# Bump PROGRAM_VERSION in src/app_info.py, commit, push to master, then:
git tag v9.0.0
git push origin v9.0.0
```
