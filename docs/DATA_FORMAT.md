# TinyTouch Export Data Format

This is the specification of the files TinyTouch publishes for downstream analysis. It is
written for people who consume the data — statisticians, pipeline authors, reviewers — not
for people who modify the app.

Two files per labeled video make up the published dataset:

```
data/<video_name>/export/<video_name>_export.csv        the table, one row per frame
data/<video_name>/export/<video_name>_metadata.json     the sidecar (fps, mode, labels, ...)
```

Everything else under `data/<video_name>/` is internal: `state/<video_name>.db` is the
app's working state, `frames/` holds the extracted JPEGs, and `plots/` holds the analysis
dashboards. Do not parse those; they are free to change between versions.

**The export format is unchanged from earlier TinyTouch versions.** The column set, the
column order and the byte-level encoding of every cell are frozen and pinned by tests
(`tests/unit/test_export_schema.py` for the order, `tests/unit/test_export_golden_master.py`
for the bytes, `tests/unit/test_export_metadata.py` for the sidecar). A dataset produced by
TinyTouch 7.x parses identically to one produced by 8.x.

---

## 1. The export CSV

### File-level properties

| Property | Value |
| --- | --- |
| Encoding | UTF-8, **no** BOM. Non-ASCII notes are stored as literal UTF-8, never `\uXXXX` escapes. |
| Header | The **first line is the header row**. There is no preamble. |
| Line terminator | Whatever `os.linesep` is on the machine that wrote the file: `\r\n` on Windows, `\n` on Linux. Read with a CSV reader in universal-newline mode; do not split on `\n` naively. |
| Quoting | `QUOTE_MINIMAL` (pandas default). A cell is quoted only when it contains a comma or a quote; embedded quotes are doubled (`""`). |
| Rows | Exactly one row per frame index, `0 .. total_frames` **inclusive**, in ascending order. Frames the annotator never touched are still present, with empty cells. |
| Writes | The file is rewritten in full on every Save, atomically (temp file + `os.replace`), so a reader never sees a half-written export. |

> **Legacy preamble.** Exports written before v8 prefixed the table with a 6-line
> human-readable metadata block. TinyTouch's own reader still tolerates it
> (`src/adapters/export_reader.py`, `LEGACY_PREAMBLE_LINES = 6`). If you hit an old file
> whose first line is not `Frame,Time_ms,...`, skip 6 lines.

### Column list, in order

34 columns. The limb blocks are ordered **LH, LL, RH, RL** — alphabetical, and
deliberately *not* the order the limb selector shows in the GUI (RH, LH, RL, LL). The
per-limb parameter block repeats the same LH, LL, RH, RL order.

| # | Column | Type | Meaning |
| --- | --- | --- | --- |
| 1 | `Frame` | int | Frame index, 0-based. Matches `frames/frame<N>.jpg`. |
| 2 | `Time_ms` | float | `Frame / frame_rate * 1000`. **`0.0` for every row** when the frame rate could not be probed (see "Zero frame rate" below). |
| 3 | `LH_X` | str | Comma-joined X coordinates of every click on this frame for the left hand. |
| 4 | `LH_Y` | str | Comma-joined Y coordinates, index-aligned with `LH_X`. |
| 5 | `LH_Onset` | str | `ON`, `OFF`, or empty. Applies to the whole row/limb, not to an individual click. |
| 6 | `LH_Zones` | str | JSON list of zone buckets, one bucket per click (see "Zone buckets"). |
| 7–10 | `LL_X`, `LL_Y`, `LL_Onset`, `LL_Zones` | | Same, left leg. |
| 11–14 | `RH_X`, `RH_Y`, `RH_Onset`, `RH_Zones` | | Same, right hand. |
| 15–18 | `RL_X`, `RL_Y`, `RL_Onset`, `RL_Zones` | | Same, right leg. |
| 19 | `Parameter_1` | str | Global (whole-frame) parameter 1: `ON`, `OFF`, or empty. **This is where gaze is recorded** — see "Gaze". |
| 20 | `Parameter_2` | str | Global parameter 2: `ON`, `OFF`, or empty. |
| 21 | `Parameter_3` | str | Global parameter 3: `ON`, `OFF`, or empty. |
| 22–24 | `LH_Parameter_1`, `LH_Parameter_2`, `LH_Parameter_3` | str | Per-limb parameters for the left hand: `ON`, `OFF`, or empty. |
| 25–27 | `LL_Parameter_1..3` | str | Same, left leg. |
| 28–30 | `RH_Parameter_1..3` | str | Same, right hand. |
| 31–33 | `RL_Parameter_1..3` | str | Same, right leg. |
| 34 | `Note` | str | Free-text note for this frame, or empty. May contain commas and non-ASCII text (then quoted). |

The human-readable names of the six parameter buttons are **not** in the CSV — they live in
the metadata sidecar under `Param Labels` / `Limb Param Labels`. The CSV column names are
fixed regardless of how the buttons are labeled in the app.

### Cell encodings

**Coordinates (`{limb}_X`, `{limb}_Y`).** Integers, comma-joined, no spaces, no brackets:
`10,20` means two clicks at x=10 and x=20. A single click is a bare number (`50`), so the
cell is unquoted; two or more clicks contain a comma and are therefore quoted (`"10,20"`).
No clicks on that frame for that limb yields an **empty cell**. `X` and `Y` are always the
same length and are positionally aligned. Coordinates are in **diagram image pixels** at
native scale — the app divides display coordinates by the current `diagram_scale` before
storing, so the numbers are independent of the user's zoom setting.

**Zone buckets (`{limb}_Zones`).** A JSON array whose length equals the number of clicks;
element *i* is the list of zone names hit by click *i*. Serialized with Python's
`json.dumps` defaults, which put a **space after each comma**:

```
[]                       no clicks (this is the value in every "empty" row, not an empty cell)
[["L"]]                  one click, one zone
[["L"], ["I"]]           two clicks, one zone each
[["L", "I"]]             one click that resolved to two zones
[["NN"]]                 one click that hit no zone mask (see "NN")
```

Because the JSON contains quotes, the cell is quoted by the CSV writer and the inner quotes
are doubled. The raw bytes for `[["L"], ["I"]]` are:

```
"[[""L""], [""I""]]"
```

An empty bucket list is the two-byte cell `[]` and is **not** quoted. Note the asymmetry:
"no clicks" is an empty string in `{limb}_X` / `{limb}_Y` but the literal `[]` in
`{limb}_Zones`.

In practice TinyTouch writes at most **one** zone name per bucket: the mask hit test
returns the first matching mask only, or the sentinel `NN`. Two-element buckets occur only
in hand-edited or legacy data; readers should still handle them.

**Onset (`{limb}_Onset`).** Exactly `ON`, `OFF`, or the empty string. The value describes
the whole limb-row: when several clicks are placed on one frame for one limb, the most
recent click's onset state overwrites the record's onset. Legacy archived data may contain
a null (empty) onset where a modern file would contain `""`; both read back as empty.

**Parameters.** `ON`, `OFF`, or empty. Empty means "not set" for this frame. There is no
third stored state: the buttons cycle *unset → ON → OFF → unset*. Historic files could
contain the literal string `None` for a per-limb parameter; the writer normalizes that to
an empty cell.

**Note.** Empty string when there is no note. Leading and trailing whitespace is stripped
before storing.

**Empty vs. null.** The exporter never writes the tokens `NaN`, `null`, `None` or `NA`.
Every "absent" value is an empty cell. When reading with pandas, pass
`keep_default_na=False` (and `dtype=str` if you want the raw tokens) so empty cells arrive
as `""` instead of `NaN`.

### Zero frame rate

Some containers report `0` for the OpenCV frame-rate property. That does not abort the
export: `Time_ms` becomes `0.0` on every row and all other cells are byte-identical to a
normal export. The sidecar then records `"Frame Rate": 0.0` (or whatever was probed), and
the analysis pipeline falls back to frame-based results only. If `Time_ms` is all zeros,
derive time from `Frame` and a frame rate you know independently.

### Gaze

There is no `Look` column and there never was one in the current format. Infant gaze is
recorded through the **global `Parameter_1`** column; the label shown on that button
(`Looking1` in the shipped `config.json`) is written to the metadata sidecar under
`Param Labels["Parameter_1"]`. See "Legacy notes" below.

---

## 2. The metadata sidecar

`data/<video_name>/export/<video_name>_metadata.json`, UTF-8, `indent=2`, LF newlines,
literal UTF-8 (no `\uXXXX` escaping), keys in this exact order:

| Key | Type | Meaning |
| --- | --- | --- |
| `Program Version` | str | The TinyTouch build that wrote the export, e.g. `"8.0.0 (Windows)"`. Matches the release tag. |
| `Video Name` | str | Project folder name. Carries the `_reliability` suffix when the file came from a Reliability pass. |
| `Labeling Mode` | str | `"Normal"` or `"Reliability"`. |
| `Frame Rate` | float | Frames per second as probed from the video, rounded to one decimal. May be `0.0` — see "Zero frame rate". |
| `Zones Covered With Clothes` | list of str \| null | Zone names the annotator marked as covered by clothing. `null` means the Clothes dialog was never used, which is **not** the same as `[]` ("opened, marked nothing"). Deduplicated but unsorted; a dot that resolved to several zones contributes one comma-joined string as a single entry. |
| `Param Labels` | object | Display labels of the three global parameter buttons, keyed `"Parameter_1"`, `"Parameter_2"`, `"Parameter_3"`. `{}` when unavailable. |
| `Limb Param Labels` | object | Display labels of the three per-limb parameter buttons, keyed `"XX_Parameter_1"`, `"XX_Parameter_2"`, `"XX_Parameter_3"`. The `XX` is a placeholder standing for any of the four limbs — the same three labels apply to all of them. `{}` when unavailable. |
| `Total Labeling Time (hours)` | float | Cumulative wall-clock time spent labeling this video, in hours, rounded to 4 decimals. Accumulated across sessions. **Present only when the app had a running labeling clock**; absent from files written without one. |

Example:

```json
{
  "Program Version": "8.0.0 (Windows)",
  "Video Name": "infant_042",
  "Labeling Mode": "Normal",
  "Frame Rate": 25.0,
  "Zones Covered With Clothes": ["A", "B"],
  "Param Labels": {
    "Parameter_1": "Looking1",
    "Parameter_2": "P2",
    "Parameter_3": "P3"
  },
  "Limb Param Labels": {
    "XX_Parameter_1": "LP1",
    "XX_Parameter_2": "LP2",
    "XX_Parameter_3": "LP3"
  },
  "Total Labeling Time (hours)": 3.2517
}
```

---

## 3. Semantic conventions

These are the scientifically load-bearing rules. They are implemented in
[`src/domain/touch_stats.py`](../src/domain/touch_stats.py), which is the normative
reference; anything computing statistics from the export should follow them so numbers are
comparable across studies.

### A touch episode is half-open: `[ON, OFF)`

An episode starts on the frame carrying `ON` and ends on the frame carrying the next `OFF`.
Its duration is

```
duration_frames = OFF_frame - ON_frame
```

The touch is **active on the onset frame** and the **offset frame is excluded**. The
shortest possible closed touch is therefore 1 frame (`ON` at *f*, `OFF` at *f+1*), not 0.
This matches the interval the app shades on its timeline, so the published numbers agree
with what the annotator saw.

Duration in seconds is `duration_frames / frame_rate`, and is undefined when the frame rate
is unusable (`None`, `0`, negative).

### Unterminated touches are censored, not completed

An `ON` with no matching `OFF` before the end of the file is a **censored** episode. It has
no end frame and no duration. It is excluded from durations, totals, percentages, means,
standard deviations, transition counts and both histograms — but it is **not** discarded:
TinyTouch reports it separately as an "open (unterminated) touch", with its start frame, in
the analysis tables, on the dashboard and in the log.

Treating it as a touch that runs to the end of the video would let one stray onset dominate
every statistic, so any independent reimplementation should censor it the same way. A
stray `OFF` with no preceding `ON` belongs to no episode and is ignored entirely.

### Multi-zone transitions are counted pairwise

A zone transition is (deduplicated start zones) × (deduplicated end zones). An episode whose
`ON` row resolved to 2 zones and whose `OFF` row resolved to 2 zones contributes **4**
counts to the transition matrix.

Consequence: **heatmap totals are greater than or equal to the number of touches and must
not be read as a touch count.** The heatmap subtitle in the dashboard says so.

When an episode edge carries no zone at all, the transition uses the sentinel `NN` so the
touch is still represented. The per-zone *touch count* deliberately does **not** get that
sentinel — it counts observed zones only.

### `minimal_touch_length` filters nothing

The `minimal_touch_length` key in `config.json` is a **visualization threshold in
milliseconds**. The app displays its frame equivalent as a readout for the annotator.
Analysis has never filtered by it and does not now: every closed episode, including
1-frame ones, is counted. If you want a minimum-duration criterion, apply it yourself,
downstream, and report it.

### Onsets per touch

Extra `ON` rows *inside* an already-open episode do not start a new touch; they increment
that episode's onset count and their clicks are recorded as mid-touch waypoints. The
"touch length distribution" histogram plots this onset count, which is why its x-axis is
labeled "number of onsets" rather than a duration.

---

## 4. Zone names

Zone names in `{limb}_Zones` are the **file names of the zone-mask PNGs**, minus the
extension. Adding a PNG to the mask directory adds a zone; nothing else needs to change.

| Template | Directory | Zones |
| --- | --- | --- |
| Default | [`src/resources/icons/zones3/`](../src/resources/icons/zones3/) | 38 masks: `A`–`Z`, `WB`, `XB`, `YB`, `ZB`, `BOX1`–`BOX6`, `LINE`, `OUTSIDE` |
| Alternate | [`src/resources/icons/zones3_new_template/`](../src/resources/icons/zones3_new_template/) | 32 masks: `A`–`T`, `QB`, `RB`, `SB`, `TB`, `BOX1`–`BOX6`, `LINE`, `OUTSIDE` |

The alternate set is selected by `"new_template": true` in `config.json`, which also swaps
the body diagram and the four limb images. **The zone name alone does not tell you which
template produced it** — `A` means different anatomy in the two sets. Record the template
alongside your dataset.

Special names:

- `BOX1` … `BOX6` — the six catch-all boxes drawn beside the body diagram. They have no
  fixed anatomical meaning; teams assign them (ground, prop, caregiver, ...) by convention.
- `OUTSIDE` — the click fell in the region masked as outside the body.
- `LINE` — the click fell on a boundary line between zones.
- `NN` — **no mask matched**. This is a sentinel produced by the hit test, not a mask file.
  It appears in `{limb}_Zones` cells and, for transitions only, stands in for an episode
  edge that carries no zone at all.

Masks are grayscale PNGs where the zone is drawn in **black on white**; a click hits a zone
when the mask pixel under it is 0. Masks are loaded in sorted filename order and the
**first** match wins, so overlapping masks resolve deterministically.

---

## 5. Reading the export

### Python (pandas)

```python
import json
import pandas as pd

df = pd.read_csv(
    "data/infant_042/export/infant_042_export.csv",
    keep_default_na=False,   # empty cells stay "" instead of becoming NaN
)

with open("data/infant_042/export/infant_042_metadata.json", encoding="utf-8") as fh:
    meta = json.load(fh)
fps = meta["Frame Rate"] or None

# Coordinates: comma-joined -> list of ints
def coords(cell):
    return [int(t) for t in cell.split(",") if t.strip()]

# Zone buckets: JSON -> list of lists, one bucket per click
def zones(cell):
    return json.loads(cell) if cell else []

# Reconstruct closed episodes for one limb, half-open [ON, OFF).
def episodes(df, limb):
    out, start = [], None
    for frame, onset in zip(df["Frame"], df[f"{limb}_Onset"]):
        if onset == "ON" and start is None:
            start = frame
        elif onset == "OFF" and start is not None:
            out.append((start, frame, frame - start))   # (on, off, duration_frames)
            start = None
    if start is not None:
        print(f"WARN: {limb} has an unterminated touch starting at frame {start}")
    return out

for limb in ("LH", "LL", "RH", "RL"):
    eps = episodes(df, limb)
    total = sum(d for _, _, d in eps)
    print(limb, len(eps), "touches,", total, "frames",
          f"({total / fps:.2f} s)" if fps else "(no frame rate)")
```

`{limb}_Zones` is the one column that always needs `json.loads`; everything else is a plain
scalar or a comma-joined list.

### R

```r
df <- read.csv(
  "data/infant_042/export/infant_042_export.csv",
  colClasses = "character",     # keep ON/OFF and the JSON cells verbatim
  na.strings = character(0)     # empty cells stay "", not NA
)
df$Frame   <- as.integer(df$Frame)
df$Time_ms <- as.numeric(df$Time_ms)

# Zone buckets need a JSON parser, e.g. jsonlite::fromJSON(df$LH_Zones[i])
```

---

## 6. Legacy notes and version history

### Per-limb `Look` retired (2026-07-13)

Per-limb gaze (`Look`) has been fully retired from TinyTouch. Current-format exports never
contained `{limb}_Look` columns, so this did not change the export schema.

External scripts that still reference `Look` columns from legacy exports should use the
global `Parameter_1` column instead. That is where gaze is captured; its user-facing label
(currently `Looking1`) is recorded under `Param Labels` in the metadata sidecar.

### Metadata moved out of the CSV (v8)

Exports written before v8 carried a 6-line human-readable metadata preamble ahead of the
header row. That metadata now lives in `<video>_metadata.json` and the CSV starts with its
header. Readers that need to handle both layouts should skip 6 lines when the first line is
not the header — this is what `src/adapters/export_reader.py` does.

### Folder layout renamed (after v8.0.0)

The output root was renamed `Labeled_data/` → `data/`, the per-video working-state folder
`<video>/data/` → `<video>/state/`, and the source-video folder `Videos/` → `videos/`.
TinyTouch migrates an old tree automatically on startup (directory renames only). Scripts
with hardcoded `Labeled_data/...` paths need updating; the export file **name** and its
position inside `export/` are unchanged.

### 3D pose mode removed (after v8.0.0)

An experimental 3D pose-labeling mode existed in earlier versions. It kept its own project
folder, suffixed `_3d`, alongside the touch dataset. It was removed entirely; the last
version containing it is the `v8.0.0` tag. The touch export described above is unaffected.

---

## See also

- [ANNOTATION_GUIDE.md](ANNOTATION_GUIDE.md) — how the data is produced, for annotators.
- [../PROJECT.md](../PROJECT.md) — architecture, on-disk layout, internal state database.
- [`src/adapters/export_writer.py`](../src/adapters/export_writer.py) — the writer; this
  document describes its output.
- [`src/domain/touch_stats.py`](../src/domain/touch_stats.py) — the normative
  implementation of the semantic conventions in section 3.
