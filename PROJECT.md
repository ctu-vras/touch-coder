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
│   ├── ui_components.py          # Tkinter layout, widgets, key/mouse bindings
│   ├── video_model.py            # Video + per-frame data model (LimbView wrappers)
│   ├── data_utils.py             # Unified-CSV I/O, legacy export schema, metadata sidecar
│   ├── analysis.py               # Plotly-based analysis dashboards
│   ├── frame_utils.py            # Frame extraction (ffmpeg → OpenCV fallback) + integrity check
│   ├── config_utils.py           # config.json read / write, parameter-name binding
│   ├── cloth_app.py              # Clothing-zone selector dialog (Toplevel)
│   ├── generate_zone_masks.py    # Offline tool: build per-zone PNG masks from a diagram
│   ├── perf_utils.py             # Optional perf timer + periodic summary logging
│   └── resource_utils.py         # PyInstaller-aware asset path resolution
├── icons/                        # Body diagrams, limb images, zone masks
│   ├── diagram0.png              # Default touch diagram (rendered on canvas)
│   ├── zones3/                   # Touch-mode zone masks (one PNG per zone)
│   └── zones3_new_template/      # Alternate zone set (config: new_template = true)
├── Labeled_data/                 # Output (gitignored) -- one folder per video
├── tests/                        # Benchmark / dev scripts (e.g. frame extraction)
├── assets/, docs/, .github/      # Static assets, docs, CI workflow
├── config.json                   # User-configurable settings (see "Configuration")
├── requirements.txt              # Pinned Python dependencies
└── TinyTouch.spec                # PyInstaller build spec
```

## Architecture

TinyTouch is a single-process Tkinter app organized around one controller (`LabelingApp`, a `tk.Tk` subclass) that owns:

- the `Video` model (raw video info + per-frame data dict),
- the UI built by `ui_components.build_ui(app)` (frames, canvases, buttons, key bindings),
- two daemon threads for I/O-bound work (frame buffering and playback advance),
- persistence helpers from `data_utils`.

### High-level data flow

```
User input (clicks / keys)
   └─► LabelingApp callbacks
         ├─► mutates Video.frames[frame] (the in-memory FrameBundle)
         ├─► marks bundle "Changed" = True
         └─► repaints diagram canvas + timelines
                                              ┌─► save_unified_dataset (changed-only journal append)
On Save / Close ─► LabelingApp.save_data ─────┼─► export_from_unified  (full legacy schema)
                                              └─► write_export_metadata (JSON sidecar)
```

### In-memory data model

Touch-mode state for a frame is a **`FrameBundle`** (see [src/data_utils.py](src/data_utils.py)):

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
- Zones under each click are auto-detected from per-zone PNG masks under [icons/zones3/](icons/zones3/) (or [icons/zones3_new_template/](icons/zones3_new_template/) when `new_template = true`).
- Track infant gaze (`Looking: Yes / No`) and up to 3 global + 3 per-limb parameters (button labels are user-editable in Settings → persisted to `config.json`).
- Six "boxes" on the diagram act as catch-all zones (ground, prop, etc).
- Two timelines visualize all touch events; the lower one is the global scrub bar.

## Data Layout on Disk

Each labeled video produces a self-contained folder:

```
Labeled_data/<video_name>/
├── data/                             # Working state (load/save round-trips here)
│   ├── <video>_unified.csv           # In-memory FrameBundle dict serialized
│   │                                 # (changed-row journal; last Frame row wins)
│   ├── <video>_clothes.txt           # Coordinates + auto-detected zones from Clothes dialog
│   ├── <video>_notes.csv             # Per-frame freeform notes
│   ├── <video>_limb_parameters.csv   # Limb-specific Parameter_1..3
│   ├── <video>_last_position.json    # Resume position + per-video labeling-time accumulator
│   └── ...                           # Legacy per-limb {RH,LH,RL,LL}.csv if migrated
├── export/                           # Final, "publication-ready" artifacts
│   ├── <video>_export.csv            # Flat schema (see below) -- the file analysis reads
│   └── <video>_metadata.json         # Program version, FPS, mode, clothes zones, param labels,
│                                     # total labeling time (hours)
├── frames/                           # frame0.jpg ... frameN.jpg (one per video frame)
└── plots/                            # Plotly HTMLs from "Analysis"
```

The split between `data/<video>_unified.csv` and `export/<video>_export.csv` is deliberate:

- **Unified CSV** is the source of truth for round-trips. Saves are *incremental* -- only frames whose `Changed` flag is set are appended. Re-edited frames produce duplicate `Frame` rows; loaders resolve them last-writer-wins and atomically compact the journal when its row count exceeds twice the number of distinct frames.
- **Export CSV** is rewritten from scratch each save with one row per frame in the canonical legacy column order. Downstream consumers (Analysis, external tooling) read this file.

If a unified CSV is missing on load, the app first tries to recover from the export CSV (`import_unified_from_export`), falling back to legacy per-limb CSVs (`csv_to_dict`) if needed.

### Touch export schema

`<video>_export.csv` columns (from `export_from_unified` in [src/data_utils.py](src/data_utils.py)):

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

1. **Load Video** -- pick `Normal` / `Reliability`, then select a video file (mp4/mov/avi/mkv/flv/wmv). The video is copied into `Labeled_data/<video>/` so the working set is self-contained, frames are extracted (or copied for Reliability), prior state is loaded, and the buffering thread starts.
2. **Clothes** -- mark which body zones are covered with clothes; saved to `<video>_clothes.txt` and surfaced in the export metadata.
3. **Annotate** -- pick a limb, click onsets/offsets, set gaze and parameters, type notes. Edits stay in memory until Save.
4. **Save** -- `Save` button (or auto on Close / before Load) writes the unified CSV (incremental), the export CSV (full), and the metadata sidecar. The `Changed` flags are cleared.
5. **Analysis** -- runs `analysis.do_analysis` over the export CSV: per-limb summary stats, transition heatmaps, touch-trajectory plots, histograms, and a master HTML opened in the browser. Output lands in `plots/`.
6. **Close** -- final save, persists labeling-time accumulator and last frame position.

## Local Build

```bash
pyinstaller TinyTouch.spec
```

Produces a standalone executable in `dist/`. The spec bundles `config.json` and the `icons/` tree as data; resource paths go through `resource_utils.resource_path` so the same code works frozen and from source.

## Releasing a New Version

A GitHub Actions workflow ([.github/workflows/build.yml](.github/workflows/build.yml)) builds and publishes Windows x64, Linux x64, and Linux Legacy x64 (Bullseye / Python 3.11) artifacts on every `v*` tag push, then creates a GitHub Release with the zips attached.

**Full step-by-step instructions** (pre-release checklist, version bump, tagging, monitoring, rollback, common pitfalls): see **[docs/RELEASING.md](docs/RELEASING.md)**.

Quick reference:

```bash
# Bump src/video_model.py program_version, commit, push to master, then:
git tag v7.7.0
git push origin v7.7.0
```
