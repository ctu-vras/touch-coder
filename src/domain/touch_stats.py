"""
domain/touch_stats.py

PURE touch-episode reconstruction and statistics over an export DataFrame.
This is the analysis half of the old monolithic `analysis.py`: everything that
turns `export/<name>_export.csv` rows into numbers lives here, and NOTHING that
draws, writes or reads a file does. Concretely, this module must never import
plotly, webbrowser, os-level writers, `gui.resource_utils` or `adapters.config`
(there is a test that greps `src/domain/` for exactly that). pandas is allowed
— a DataFrame is the input shape — but only as a data container.

Pipeline: `parse_export(df)` -> per-limb `Episode` lists, then
`summarize(episodes, fps, total_frames)` -> `LimbStats` and
`transitions(episodes)` -> pairwise zone counts. Episodes are the raw,
inspectable intermediate: plots and stats both consume them, so a click can
never be counted by one and dropped by the other (that WAS a real bug — the
trajectory plot and the metrics disagreed about mid-touch clicks).

=== Conventions that are contractual (do not "fix" these silently) ===

DURATION IS HALF-OPEN `[ON, OFF)`
    A closed episode's duration is `OFF_frame - ON_frame`: the touch is active
    ON the onset frame and the offset frame is EXCLUDED. The minimum closed
    duration is therefore 1 frame (ON at f, OFF at f+1). This matches what the
    labeler's timeline painters shade, so changing it would desynchronize the
    numbers from what the annotator sees.

OPEN (UNTERMINATED) EPISODES ARE CENSORED, NOT COMPLETED
    An `ON` with no matching `OFF` before the end of the file is recorded with
    `closed=False` and `end_frame=None`. It has NO duration and is excluded
    from durations, totals, percentages, means, stdev, transitions and both
    histograms. It is NOT dropped either: `LimbStats.open_touches` counts it,
    the tables surface the count and the service logs a WARN. Rationale: the
    labeler draws no timeline interval for an unterminated touch (the interval
    is only emitted on OFF), so treating it as a completed touch of length
    `last_frame - ON` let a single stray onset near the start of a video
    dominate every statistic while showing nothing in the app.

MULTI-ZONE TRANSITIONS ARE PAIRWISE (CARTESIAN)
    A frame may carry several clicks and each click several zone buckets. An
    episode whose start row resolved to 2 zones and whose end row resolved to
    2 zones contributes 2 x 2 = 4 heatmap counts. Consequence: heatmap totals
    are >= the touch count and must not be read as "number of touches". The
    heatmap subtitle says so, and a test pins it.

ZONELESS ENDS FALL BACK TO "NN" FOR TRANSITIONS ONLY
    If the start (or end) row of an episode carries no zone at all, the
    transition uses the sentinel `NN`. `zone_touch_count` deliberately does NOT
    get that sentinel — it counts observed zones only.

`minimal_touch_length` IS NOT A FILTER HERE
    The config key of that name is a VISUALIZATION threshold shown as a label
    in the GUI. Analysis has never filtered short touches by it and still does
    not: every closed episode, including 1-frame ones, is counted. Do not add
    filtering without changing the config key's documented meaning.

FRAME RATE MAY BE UNUSABLE
    `fps` can legitimately arrive as None, 0.0 or negative (some containers
    report 0 for CAP_PROP_FPS and the export path deliberately tolerates it).
    Every seconds-derived field is then `None` rather than a crash or a bogus
    number. Use `fps_is_usable()` for that test; never divide by fps directly.
"""

import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import pandas as pd

from domain.model import LIMBS
from domain.touch import NO_ZONE, is_catch_all_zone

# `NO_ZONE` ("NN") is defined in domain.touch — the labeler's hit test writes it
# and this module reuses it as the transition fallback for an episode edge that
# carries no zone at all. Re-exported here because importers (adapters.zone_masks,
# the tests) have always read it from this module.

# Columns every export must have for analysis to mean anything. `{limb}_X` /
# `{limb}_Y` are NOT required: they only feed the trajectory plot, so a file
# without them still yields correct statistics (the service warns).
BASE_COLUMNS = ("Frame", "Time_ms")
PER_LIMB_COLUMNS = ("Onset", "Zones")
PER_LIMB_OPTIONAL_COLUMNS = ("X", "Y")


class ExportSchemaError(ValueError):
    """An export CSV parsed as a table but is missing columns analysis needs.

    Raised instead of silently producing a dashboard full of zeros, which is
    what the old `rec.get(f"{limb}_Onset", "")` default did for any file that
    merely happened to have a `Frame` column.
    """


def required_columns(limbs: Sequence[str] = LIMBS) -> List[str]:
    """The full list of columns `parse_export` refuses to work without."""
    cols = list(BASE_COLUMNS)
    for limb in limbs:
        cols.extend(f"{limb}_{suffix}" for suffix in PER_LIMB_COLUMNS)
    return cols


def optional_columns(limbs: Sequence[str] = LIMBS) -> List[str]:
    """Columns whose absence degrades the trajectory plot but not the stats."""
    return [f"{limb}_{suffix}" for limb in limbs for suffix in PER_LIMB_OPTIONAL_COLUMNS]


def validate_export_columns(df: pd.DataFrame, limbs: Sequence[str] = LIMBS) -> List[str]:
    """Raise `ExportSchemaError` naming every missing required column.

    Returns the list of MISSING OPTIONAL columns so the caller can warn about
    a degraded trajectory plot. Extra columns are always fine — legacy exports
    still carry the retired per-limb `Look` column and it is simply ignored.
    """
    present = set(df.columns)
    missing = [c for c in required_columns(limbs) if c not in present]
    if missing:
        raise ExportSchemaError(
            "export CSV is missing required column(s): "
            + ", ".join(missing)
            + f" (found: {', '.join(map(str, df.columns))})"
        )
    return [c for c in optional_columns(limbs) if c not in present]


# --- value parsing (tolerant by design: these files are hand-edited) --------

def normalize_onset(value) -> str:
    """`None`/NaN/whitespace -> "", anything else upper-cased and stripped."""
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip().upper()


def parse_xy_list(value) -> List[float]:
    """Parse a `{limb}_X` / `{limb}_Y` cell: comma-separated floats.

    Unparseable tokens are skipped with a WARN — a corrupt coordinate must not
    abort an otherwise-usable analysis run.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return []
    s = str(value).strip()
    if not s:
        return []
    out = []
    for token in (p.strip() for p in s.split(",")):
        if not token:
            continue
        try:
            out.append(float(token))
        except ValueError as exc:
            print(
                f"WARN: touch_stats ignored invalid coordinate token {token!r} "
                f"from {value!r}: {exc}"
            )
    return out


def parse_zones(value):
    """Parse a `{limb}_Zones` cell: a JSON list of buckets, one per click.

    A malformed cell degrades to `[]` with a WARN rather than raising.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return []
    if isinstance(value, list):
        return value
    s = str(value).strip()
    if not s:
        return []
    try:
        parsed = json.loads(s)
    except json.JSONDecodeError as exc:
        print(f"WARN: touch_stats could not parse zones {value!r}: {exc}")
        return []
    return parsed if parsed is not None else []


def flatten_zones(zones) -> List[str]:
    """Flatten a parsed Zones cell (list of buckets) to a flat list of names."""
    flat = []
    for z in zones or []:
        if isinstance(z, list):
            flat.extend([str(v) for v in z if v is not None])
        elif z is not None:
            flat.append(str(z))
    return flat


def _dedup(values) -> Tuple[str, ...]:
    """Order-preserving dedup (transition pairs must not double-count a zone)."""
    return tuple(dict.fromkeys(values))


def zone_sort_key(zone: str):
    """Zone ordering for axes/matrices: real zones first (shortest name, then
    alphabetical), then the catch-alls (`BOX*`, `OUTSIDE`, `LINE`, `NN`).

    "Catch-all" is `domain.touch.is_catch_all_zone` — the SAME predicate the
    click hit test uses to rank overlapping masks, so the axis order and the
    hit-test precedence can never disagree about what counts as anatomy.
    """
    z = str(zone)
    return (1 if is_catch_all_zone(z) else 0, len(z), z)


def fps_is_usable(fps) -> bool:
    """True only for a strictly positive, finite frame rate.

    Guards every seconds conversion in the app. `None`, `0.0` (what some
    containers report for CAP_PROP_FPS) and negatives are all unusable.
    """
    if fps is None:
        return False
    try:
        value = float(fps)
    except (TypeError, ValueError):
        return False
    return math.isfinite(value) and value > 0


# --- episodes ---------------------------------------------------------------

@dataclass(frozen=True)
class EpisodePoint:
    """One click belonging to one episode.

    Exists so the trajectory plot and the statistics iterate the SAME clicks:
    a click is either part of an episode (counted and drawn) or of no episode
    (neither counted nor drawn). `role` drives marker styling:
    `start` (first click of the ON row -> green), `end` (last click of the OFF
    row -> red), everything else `mid` (black).
    """

    frame: int
    x: float
    y: float
    onset: str                     # "ON" | "OFF" | "" as found in the row
    zones: Tuple[str, ...]         # this click's own zone bucket
    role: str                      # "start" | "mid" | "end"
    click_index: int               # 1-based index within the frame
    clicks_in_frame: int

    @property
    def is_last_in_frame(self) -> bool:
        return self.click_index == self.clicks_in_frame


@dataclass(frozen=True)
class Episode:
    """One touch: an `ON` and, if the annotator closed it, its `OFF`.

    `closed=False` means the file ended (or nothing followed) while the touch
    was still open. Such an episode is CENSORED: `end_frame` is None, it has no
    duration, and `summarize`/`transitions` exclude it while still counting it
    under `open_touches`. `last_seen_frame` records where the data ran out, for
    logging only.

    `zones_start` / `zones_end` are the deduped zone names of the ON / OFF row;
    `zones_mid` collects zones from every click in between (extra ONs and
    zone-only rows during the touch). All three are RAW: no `NN` sentinel is
    substituted here — `transitions()` applies that fallback for its own
    purposes only.
    """

    limb: str
    start_frame: int
    end_frame: Optional[int]
    closed: bool
    zones_start: Tuple[str, ...]
    zones_mid: Tuple[str, ...]
    zones_end: Tuple[str, ...]
    n_onsets: int
    points: Tuple[EpisodePoint, ...] = ()
    last_seen_frame: Optional[int] = None

    @property
    def duration_frames(self) -> Optional[int]:
        """Half-open `[ON, OFF)` length in frames; None when censored."""
        if not self.closed or self.end_frame is None:
            return None
        return self.end_frame - self.start_frame

    def duration_seconds(self, fps) -> Optional[float]:
        """Duration in seconds, or None when censored or fps is unusable."""
        frames = self.duration_frames
        if frames is None or not fps_is_usable(fps):
            return None
        return frames / float(fps)

    @property
    def zones_touched(self) -> Tuple[str, ...]:
        """Every distinct zone this episode touched (start + mid + end)."""
        return _dedup(list(self.zones_start) + list(self.zones_mid) + list(self.zones_end))


@dataclass(frozen=True)
class ExportData:
    """Everything `parse_export` recovered from one export DataFrame."""

    episodes: Dict[str, List[Episode]]
    total_frames: int
    row_count: int
    last_frame: Optional[int] = None
    missing_optional_columns: Tuple[str, ...] = ()

    def open_episodes(self) -> List[Episode]:
        return [e for eps in self.episodes.values() for e in eps if not e.closed]


class _EpisodeBuilder:
    """Single-pass episode reconstruction for one limb.

    State machine over rows in frame order:
      * `ON`  while closed -> opens an episode (`start`)
      * `ON`  while open   -> extra onset: bumps `n_onsets`, clicks are `mid`
      * `OFF` while open   -> closes the episode (`end`)
      * `OFF` while closed -> ignored entirely (no episode, no plotted point)
      * `""`  while open   -> `mid` clicks (zone drift without a new onset)
      * `""`  while closed -> ignored entirely
    """

    def __init__(self, limb: str):
        self.limb = limb
        self.episodes: List[Episode] = []
        self._open: Optional[dict] = None
        self._last_frame: Optional[int] = None

    def feed(self, frame: int, onset: str, clicks, row_zones: Tuple[str, ...]) -> None:
        """`clicks` is a list of (x, y, zones_tuple); `row_zones` the deduped
        flat zones of the whole row."""
        self._last_frame = frame

        if onset == "ON":
            if self._open is None:
                self._open = {
                    "start_frame": frame,
                    "zones_start": row_zones,
                    "zones_mid": [],
                    "zones_end": (),
                    "n_onsets": 1,
                    "points": [],
                }
                self._add_points(frame, onset, clicks, "start")
            else:
                self._open["n_onsets"] += 1
                self._open["zones_mid"].extend(row_zones)
                self._add_points(frame, onset, clicks, "mid")
            return

        if onset == "OFF":
            if self._open is None:
                # Stray offset: belongs to no touch. Historically the plot
                # still appended a hover label for it, which is how hover
                # texts drifted out of step with the plotted points.
                return
            self._open["zones_end"] = row_zones
            self._add_points(frame, onset, clicks, "end")
            self._flush(closed=True, end_frame=frame)
            return

        # No onset marker on this row.
        if self._open is None:
            return
        self._open["zones_mid"].extend(row_zones)
        self._add_points(frame, onset, clicks, "mid")

    def finish(self) -> List[Episode]:
        """Close the pass, emitting any still-open touch as censored."""
        if self._open is not None:
            self._flush(closed=False, end_frame=None)
        return self.episodes

    # --- internals ---------------------------------------------------------
    def _add_points(self, frame, onset, clicks, role) -> None:
        total = len(clicks)
        for idx, (x, y, zones) in enumerate(clicks, start=1):
            self._open["points"].append(
                EpisodePoint(
                    frame=frame,
                    x=float(x),
                    y=float(y),
                    onset=onset,
                    zones=tuple(zones),
                    role=role,
                    click_index=idx,
                    clicks_in_frame=total,
                )
            )

    def _flush(self, closed: bool, end_frame: Optional[int]) -> None:
        state = self._open
        self._open = None
        self.episodes.append(
            Episode(
                limb=self.limb,
                start_frame=state["start_frame"],
                end_frame=end_frame,
                closed=closed,
                zones_start=tuple(state["zones_start"]),
                zones_mid=_dedup(state["zones_mid"]),
                zones_end=tuple(state["zones_end"]),
                n_onsets=state["n_onsets"],
                points=tuple(state["points"]),
                last_seen_frame=self._last_frame,
            )
        )


def _row_clicks(rec, limb, has_xy: bool):
    """Clicks of one row for one limb as [(x, y, zones_tuple), ...] plus the
    deduped flat zones of the whole row.

    Zone buckets are click-aligned (`Zones[i]` describes click `i`); a bucket
    count that disagrees with the click count degrades to an empty bucket for
    the unmatched clicks instead of raising.
    """
    buckets = parse_zones(rec.get(f"{limb}_Zones", ""))
    row_zones = _dedup(flatten_zones(buckets))
    if not has_xy:
        return [], row_zones

    xs = parse_xy_list(rec.get(f"{limb}_X", ""))
    ys = parse_xy_list(rec.get(f"{limb}_Y", ""))
    clicks = []
    for idx, (x, y) in enumerate(zip(xs, ys)):
        bucket = buckets[idx] if idx < len(buckets) else []
        clicks.append((x, y, tuple(flatten_zones([bucket]))))
    return clicks, row_zones


def parse_export(df: pd.DataFrame, limbs: Sequence[str] = LIMBS) -> ExportData:
    """Reconstruct per-limb `Episode` lists from an export DataFrame.

    Validates the schema first (raises `ExportSchemaError` naming the missing
    columns) so a file that merely has a `Frame` column can no longer produce a
    silent all-zeros dashboard. Rows are processed in ascending `Frame` order;
    an unparseable `Frame` value skips the row with a WARN.
    """
    missing_optional = validate_export_columns(df, limbs)
    builders = {limb: _EpisodeBuilder(limb) for limb in limbs}

    rows = []
    for rec in df.to_dict(orient="records"):
        try:
            frame = int(rec["Frame"])
        except (TypeError, ValueError) as exc:
            print(f"WARN: touch_stats skipped row with unusable Frame {rec.get('Frame')!r}: {exc}")
            continue
        rows.append((frame, rec))
    rows.sort(key=lambda item: item[0])

    for frame, rec in rows:
        for limb in limbs:
            has_xy = f"{limb}_X" not in missing_optional and f"{limb}_Y" not in missing_optional
            clicks, row_zones = _row_clicks(rec, limb, has_xy)
            builders[limb].feed(frame, normalize_onset(rec.get(f"{limb}_Onset", "")), clicks, row_zones)

    episodes = {limb: builders[limb].finish() for limb in limbs}
    last_frame = rows[-1][0] if rows else None
    # `total_frames` is a COUNT: the export always starts at frame 0, so the
    # highest frame index plus one.
    total_frames = (last_frame + 1) if last_frame is not None else 0

    return ExportData(
        episodes=episodes,
        total_frames=total_frames,
        row_count=len(rows),
        last_frame=last_frame,
        missing_optional_columns=tuple(missing_optional),
    )


# --- statistics -------------------------------------------------------------

@dataclass(frozen=True)
class LimbStats:
    """Per-limb summary. Frame-based fields are always numbers; every
    seconds-based field is None when the frame rate is unusable (see
    `fps_is_usable`). `mean_*` is None when there is no closed episode and
    `stdev_*` is None with fewer than two (sample stdev is undefined) —
    downstream renderers must format None, never print "nan".

    Counts and durations cover CLOSED episodes only; `open_touches` reports the
    censored ones separately.
    """

    limb: str
    total_touches: int
    open_touches: int
    durations_frames: Tuple[int, ...]
    total_duration_frames: int
    percentage_touching: float
    mean_duration_frames: Optional[float]
    stdev_duration_frames: Optional[float]
    touch_rate_per_100_frames: float
    onset_count_distribution: Dict[int, int]
    zone_touch_count: Dict[str, int]
    open_start_frames: Tuple[int, ...] = ()
    # seconds-derived (None when fps unusable)
    durations_seconds: Optional[Tuple[float, ...]] = None
    total_duration_seconds: Optional[float] = None
    mean_duration_seconds: Optional[float] = None
    stdev_duration_seconds: Optional[float] = None
    touch_rate_per_minute: Optional[float] = None
    video_seconds: Optional[float] = None


def summarize(
    episodes: Sequence[Episode],
    fps=None,
    total_frames: int = 0,
    limb: Optional[str] = None,
) -> LimbStats:
    """Aggregate one limb's episodes into a `LimbStats`.

    `total_frames` is the video length in frames, needed for `percentage_touching`
    and the touch rates; 0 makes both rates 0 rather than dividing by zero.
    `fps` may be None/0/negative — every seconds field is then None and NOTHING
    raises (this used to be a ZeroDivisionError thrown AFTER several plot files
    had already been written).

    Open (unterminated) episodes contribute ONLY to `open_touches` /
    `open_start_frames`: not to durations, totals, percentages, mean, stdev,
    rates, zone counts or the onset histogram.
    """
    episodes = list(episodes)
    if limb is None:
        limb = episodes[0].limb if episodes else ""

    closed = [e for e in episodes if e.closed]
    open_eps = [e for e in episodes if not e.closed]

    durations = tuple(e.duration_frames for e in closed)
    total_duration = int(sum(durations))
    total_touches = len(closed)

    percentage = (total_duration / total_frames) * 100 if total_frames else 0.0
    mean_frames = (total_duration / len(durations)) if durations else None
    stdev_frames = statistics.stdev(durations) if len(durations) >= 2 else None
    rate_per_100 = (total_touches / total_frames) * 100 if total_frames else 0.0

    onset_dist = dict(sorted(Counter(e.n_onsets for e in closed).items()))

    zone_counts: Dict[str, int] = defaultdict(int)
    for episode in closed:
        for zone in episode.zones_touched:
            zone_counts[zone] += 1

    usable = fps_is_usable(fps)
    if usable:
        rate = float(fps)
        video_seconds = (total_frames / rate) if total_frames else 0.0
        durations_seconds = tuple(d / rate for d in durations)
        total_seconds = total_duration / rate
        mean_seconds = (mean_frames / rate) if mean_frames is not None else None
        stdev_seconds = (stdev_frames / rate) if stdev_frames is not None else None
        rate_per_min = (total_touches / video_seconds) * 60 if video_seconds else 0.0
    else:
        video_seconds = None
        durations_seconds = None
        total_seconds = None
        mean_seconds = None
        stdev_seconds = None
        rate_per_min = None

    return LimbStats(
        limb=limb,
        total_touches=total_touches,
        open_touches=len(open_eps),
        durations_frames=durations,
        total_duration_frames=total_duration,
        percentage_touching=percentage,
        mean_duration_frames=mean_frames,
        stdev_duration_frames=stdev_frames,
        touch_rate_per_100_frames=rate_per_100,
        onset_count_distribution=onset_dist,
        zone_touch_count=dict(zone_counts),
        open_start_frames=tuple(e.start_frame for e in open_eps),
        durations_seconds=durations_seconds,
        total_duration_seconds=total_seconds,
        mean_duration_seconds=mean_seconds,
        stdev_duration_seconds=stdev_seconds,
        touch_rate_per_minute=rate_per_min,
        video_seconds=video_seconds,
    )


def transitions(episodes: Sequence[Episode]) -> Dict[str, Dict[str, int]]:
    """Start-zone -> end-zone counts over CLOSED episodes.

    PAIRWISE / CARTESIAN by design: an episode with `zones_start=(A, B)` and
    `zones_end=(C, D)` yields A->C, A->D, B->C, B->D — four counts for one
    touch. Heatmap totals therefore exceed the touch count whenever multi-zone
    clicks occur; that is the documented behavior researchers rely on.

    An edge with no zone at all uses the `NN` sentinel so the touch is still
    represented in the matrix. Open episodes contribute nothing (there is no
    end zone to pair with).
    """
    counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for episode in episodes:
        if not episode.closed:
            continue
        starts = episode.zones_start or (NO_ZONE,)
        ends = episode.zones_end or (NO_ZONE,)
        for start_zone in starts:
            for end_zone in ends:
                counts[start_zone][end_zone] += 1
    return {start: dict(ends) for start, ends in counts.items()}


def transition_zone_axis(
    transition_counts: Dict[str, Dict[str, int]],
    zone_touch_count: Dict[str, int],
    base_zones: Sequence[str] = (),
) -> List[str]:
    """Sorted zone axis for one limb's heatmap: the template's zone list plus
    any zone that actually occurs in the data (so a zone from an older template
    is still visible instead of being dropped from the matrix)."""
    seen = set(base_zones) | set(zone_touch_count)
    for start_zone, ends in transition_counts.items():
        seen.add(start_zone)
        seen.update(ends)
    return sorted(seen, key=zone_sort_key)


def transition_matrix(
    transition_counts: Dict[str, Dict[str, int]],
    zones: Sequence[str],
) -> pd.DataFrame:
    """Dense start x end count matrix over `zones` (pure pandas, no plotting).

    Counts for zones outside `zones` are dropped — build the axis with
    `transition_zone_axis` to keep that from happening silently.
    """
    zones = list(zones)
    matrix = pd.DataFrame(0, index=zones, columns=zones)
    for start_zone, ends in transition_counts.items():
        if start_zone not in matrix.index:
            print(f"WARN: touch_stats dropped transitions from unknown start zone {start_zone!r}")
            continue
        for end_zone, count in ends.items():
            if end_zone not in matrix.columns:
                print(f"WARN: touch_stats dropped transitions to unknown end zone {end_zone!r}")
                continue
            matrix.at[start_zone, end_zone] += count
    return matrix


def duration_histogram_buckets(stats: LimbStats, fps=None) -> Dict[int, int]:
    """`{bucket: number_of_touches}` for the duration histogram.

    Buckets are WHOLE SECONDS, rounded UP (`ceil`), matching the original
    histogram; when `fps` is unusable there are no seconds to bucket by, so the
    caller gets raw FRAME durations instead and must relabel its axis (see
    `adapters.plotting.write_duration_histogram`).
    """
    if fps_is_usable(fps):
        values = [math.ceil(d / float(fps)) for d in stats.durations_frames]
    else:
        values = list(stats.durations_frames)
    return dict(sorted(Counter(values).items()))
