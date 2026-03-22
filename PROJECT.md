# TinyTouch

**TinyTouch** is a desktop annotation tool for behavioral researchers studying how infants learn to understand their own body through self-touch. It provides two labeling modes:

1. **Touch Labeling** -- frame-by-frame annotation of which limb (left/right hand/leg) touches which zone on the infant's body, including onset/offset marking, gaze tracking, and custom per-study parameters.
2. **3D Pose Quality Labeling** -- assessment of 3D pose estimation accuracy by marking missing keypoints on joint zones, and adjusting a scale slider to indicate how much the projected skeleton deviates from the actual body size.

## Purpose

Researchers record videos of infants and then use TinyTouch to produce structured CSV datasets describing every self-contact event. These datasets feed downstream analyses of how babies develop body awareness. The 3D mode extends this to evaluating skeleton-based pose estimation models, letting annotators flag joints that are missing or poorly localized and rate the overall projection scale.

## Tech Stack

- **Python 3.12** with **Tkinter** (desktop GUI)
- **OpenCV** -- video frame extraction
- **Pillow** -- image / diagram rendering
- **Pandas** -- CSV I/O and data handling
- **Plotly** -- interactive analysis visualizations
- **PyInstaller** -- packaging into standalone Windows / Linux executables

## Project Structure

```
touch-coder/
├── src/                        # Application source code
│   ├── main.py                 # Entry point
│   ├── labeling_app.py         # Main controller (video, annotation, persistence)
│   ├── ui_components.py        # Tkinter layout and bindings
│   ├── video_model.py          # Video + per-frame data model
│   ├── data_utils.py           # CSV / JSON I/O, export logic
│   ├── pose_mismatch_data.py   # 3D pose data model (joints, scale)
│   ├── analysis.py             # Plotly-based analysis dashboards
│   ├── frame_utils.py          # Frame extraction from video files
│   ├── config_utils.py         # config.json handling
│   ├── cloth_app.py            # Clothing zone selector dialog
│   ├── sort_frames.py          # Touch event grouping / analysis
│   ├── generate_zone_masks.py  # Body zone mask generation from diagrams
│   ├── perf_utils.py           # Optional performance profiling
│   └── resource_utils.py       # Asset path resolution
├── icons/                      # Body diagrams, limb images, 3D joint zone masks
├── Labeled_data/               # Output directory (frames, CSVs, plots)
├── config.json                 # User-configurable settings
├── requirements.txt            # Python dependencies
└── TinyTouch.spec              # PyInstaller build spec
```

## Labeling Modes

### Touch Mode

- Navigate video frame-by-frame (arrow keys, mouse wheel, playback)
- Select a limb (RH / LH / RL / LL) and click on the body diagram to mark touch start (left-click) and end (right-click)
- Zones are automatically detected from the click position on the diagram
- Track infant gaze (Looking: Yes / No) and up to 3 custom global + 3 per-limb parameters
- Timeline visualizes all touch events across the video
- Reliability mode for inter-rater agreement studies

### 3D Pose Mode

- Body diagram shows 13 skeleton joints (ankles, knees, hips, wrists, elbows, shoulders, neck)
- Click joint zones to mark missing or problematic keypoints
- Adjust a scale slider (0.7x -- 1.3x) to indicate whether the projected 3D skeleton is larger or smaller than the actual infant
- Separate timeline and CSV export for pose annotations

## Data Export

- **Touch CSV**: one row per frame with columns for each limb's X/Y coordinates, onset/offset, detected zones, parameters, and notes
- **Pose CSV**: one row per frame with JSON-encoded joint events, scale factor, and parameters
- **Metadata JSON**: video info, labeling mode, frame rate, clothing zones, parameter labels, total labeling time

## Local Build

```bash
pyinstaller TinyTouch.spec
```

Produces a standalone executable in `dist/` for Windows or Linux.

## Releasing a New Version

A GitHub Actions workflow ([.github/workflows/build.yml](.github/workflows/build.yml)) automatically builds and publishes releases. It produces three artifacts:

- **Windows x64** (Python 3.12, `windows-latest`)
- **Linux x64** (Python 3.12, `ubuntu-latest`)
- **Linux Legacy x64** (Python 3.11, Debian Bullseye -- for older glibc systems)

To create a release:

```bash
git tag v7.6.0
git push origin v7.6.0
```

This triggers the workflow, which builds all three variants and creates a GitHub Release named "TinyTouch v7.6.0" with the zipped binaries attached.

The tag name must start with `v` (e.g. `v7.6.0`). Tags ending with `-legacy` are skipped by the build jobs.

The workflow can also be triggered manually via `workflow_dispatch` from the Actions tab (useful for test builds without creating a tag -- no release is published in that case, only artifacts).
