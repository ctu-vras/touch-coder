# TinyTouch

Frame-by-frame annotation of infant self-touch, for behavioral research.

![TinyTouch main window](assets/readme_images/showcase.png)

![version](https://img.shields.io/badge/version-9.0.0-blue) ![platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-lightgrey) ![python](https://img.shields.io/badge/python-3.12-blue) ![license](https://img.shields.io/badge/license-CC%20BY%204.0-green)

## What it does

TinyTouch is a desktop tool for coding self-contact in infant video: for each frame you
mark which limb (left/right hand, left/right leg) touches which body zone, together with
the onset and offset of the episode, the infant's gaze, and up to six user-defined
parameters. Contact locations are entered by clicking a body diagram and resolved to zone
names automatically. Each labeled video produces a flat CSV dataset plus a JSON metadata
sidecar, ready for statistical analysis.

## Install

Download the ZIP for your system from the
[Releases page](https://github.com/Humanoids-CTU/tiny-touch/releases), extract it anywhere, and
run the executable inside — `TinyTouch-<tag>.exe` on Windows, `TinyTouch-<tag>` on Linux.
No installation step and no Python required.

Three builds are published per release: `windows-x64` (Windows 10/11, 64-bit), `linux-x64`
(current distributions) and `linux-legacy-x64` (older glibc; built on Debian Bullseye). On
Linux, launch from a terminal so you can see the log.

> **Upgrading from 8.0.x or earlier:** this version reads only the current project
> layout (`data/<video>/state/<video>.db`). Older folders — `Labeled_data/…`, a
> `<video>_unified.csv` working file, source videos in `Videos/` — are **not
> converted**, and opening one shows an empty project rather than an error. Keep those
> folders as archives, keep a copy of their `*_export.csv` files (which this version
> still reads for Analysis), and start new labeling in a fresh project.

<details>
<summary><b>Run from source</b></summary>

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/). On Debian/Ubuntu you also need
the Tk bindings: `sudo apt install python3.12-tk`.

```bash
git clone https://github.com/Humanoids-CTU/tiny-touch.git
cd tiny-touch
uv venv
uv pip install -r requirements.txt
uv run python src/main.py
```

Build a standalone executable with `uv run pyinstaller TinyTouch.spec`; the result lands in
`dist/`. Run the test suite with `uv run pytest` (GUI end-to-end tests are excluded by
default; add `-m gui` to include them).

</details>

## Quick start

1. **Load Video** — choose `Normal` or `Reliability`, then pick the video file. TinyTouch
   copies it into `videos/` and extracts every frame into `data/<video>/frames/`. This runs
   once per video and can take several minutes.
2. **Clothes** — mark the body zones covered by clothing. Saved with the project and
   recorded in the export metadata.
3. **Select a limb** with the radio buttons under the diagram.
4. **Mark the onset** — navigate to the first frame of the contact and **left-click** the
   body diagram where the touch occurs. A green dot appears.
5. **Mark the offset** — navigate to the first frame after the contact ends and
   **right-click** the same area. A red dot appears and the episode is closed.
6. **Add gaze and parameters** with the buttons on the right; type per-frame remarks in the
   note box and click **Save Note**.
7. **Save** — writes `data/<video>/export/<video>_export.csv` and
   `data/<video>/export/<video>_metadata.json`. TinyTouch also saves on close.

| Input | Action |
| --- | --- |
| `←` / `→` | Previous / next frame |
| `Shift`+`←` / `Shift`+`→`, `<<` / `>>` | Fast jump back / forward |
| Mouse wheel | Previous / next frame |
| `Space`, **Play** / **Stop** | Toggle playback |
| Left-click on diagram | Touch onset (green dot) |
| Right-click on diagram | Touch offset (red dot) |
| Middle-click on diagram, or `d` | Delete the nearest dot |
| Click a timeline | Jump to that frame |
| **Save** | Write the working state and the export |

The full coding manual — modes, zones, parameters, and the mistakes worth avoiding — is in
[docs/ANNOTATION_GUIDE.md](docs/ANNOTATION_GUIDE.md).

## Output data

Each labeled video gets a self-contained folder:

```
data/<video>/
├── export/     <video>_export.csv + <video>_metadata.json   <- the published dataset
├── state/      <video>.db          working state, internal
├── frames/     frame0.jpg …        extracted video frames
└── plots/                          analysis dashboards
```

Only the two files under `export/` are meant to be read by anything other than TinyTouch.
The CSV has one row per frame with per-limb coordinates, onset/offset markers and zone
lists; the JSON records program version, frame rate, labeling mode, clothing zones,
parameter labels and total labeling time.

**The export format is unchanged from earlier TinyTouch versions** — same columns, same
order, same cell encoding — so existing analysis pipelines keep working. The full
specification, including the semantics analysis code must follow, is in
[docs/DATA_FORMAT.md](docs/DATA_FORMAT.md).

## Analysis

The **Analysis** button computes per-limb statistics from the export and writes an
interactive Plotly dashboard to `data/<video>/plots/`, opening it in your browser: touch
counts and durations, percentage of time in contact, touch rate, zone-transition heatmaps
per limb, a click-trajectory plot drawn over the limb diagrams, and duration/onset
histograms. Touches left without an offset are reported separately as censored and excluded
from duration statistics.

## Citing

A paper describing TinyTouch is in preparation. Until it appears, please cite the software
by its repository URL and the version string recorded in your dataset's metadata sidecar
(`Program Version`).

An example of the kind of analysis this coding scheme supports:

> Khoury, J., Popescu, S. T., Gama, F., Marcel, V. and Hoffmann, M. (2022), Self-touch and
> other spontaneous behavior patterns in early infancy, in *IEEE International Conference
> on Development and Learning (ICDL)*, pp. 148-155.
> [PDF](https://drive.google.com/file/d/1iVgMr-8eJFPH8jU31ksDNmv4xWY_4s5q/view?usp=sharing)

## License

Copyright (c) 2026 Czech Technical University in Prague.

TinyTouch -- the software, its documentation and the bundled assets -- is licensed under the
[Creative Commons Attribution 4.0 International License (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/).
You may copy, redistribute and adapt it for any purpose, including commercially, provided you
give appropriate credit (see [Citing](#citing)), link to the license, and indicate if changes
were made. The software is provided as is, without warranty of any kind. The full license text
is in [LICENSE](LICENSE).

## Contact

Developed at the Vision for Robotics and Autonomous Systems (VRAS) group, Czech Technical
University in Prague.

Maintainer: navarlu2@fel.cvut.cz

## Reporting a problem

TinyTouch writes one diagnostic log for every session. Open **Settings -> Open Logs
Folder**, then attach the newest `tinytouch_*.log` file to the bug report. The log includes
application details, errors, and the local annotation activity--including note text--needed
to understand what happened. It stays on your computer and is never transmitted
automatically.

If a log file cannot be created, TinyTouch continues running and reports diagnostics in
the terminal window instead.

## Documentation

[Annotation guide](docs/ANNOTATION_GUIDE.md) for annotators, [data format](docs/DATA_FORMAT.md)
for data consumers, [ARCHITECTURE.md](ARCHITECTURE.md) for developers, and a
[full index](docs/README.md) of everything else.
