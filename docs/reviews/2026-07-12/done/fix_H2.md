# Fix H2 — Per-edit O(N) timeline rebuild stalls long videos (active 3D mode)

## Problem (re-verified at HEAD)

Every annotation edit throws away the whole cached pose-timeline state, forcing the
next draw to rescan **all** frames `0..total_frames` and re-raster the full overview.
On a 300k-frame clip this means one full-length scan per click / scale change.

Verified by symbol at HEAD:

- **`mark_bundle_changed`** (`labeling_app.py:1075-1088`) — every edit does:
  ```python
  b["Changed"] = True
  self._timeline_dirty = True
  self._timeline2_dirty = True
  self._pose_timeline_state_cache = None   # <-- nukes the entire derived state
  ```
  (It also ignores its `index` argument and always marks `self.video.current_frame`
  — the M2 note; benign today because all edits are at the current frame.)

- **`_build_pose_timeline_state`** (`labeling_app.py:1835-1884`) — when the cache is
  `None`, loops `for frame in range(self.video.total_frames + 1)`, calling
  `ensure_pose_bundle(...)` per frame and computing a **running** state
  (accumulated `active` joint set + carried body/head scale) for every frame. O(N).

- **`_draw_pose_timeline2`** (`labeling_app.py:2013-2075`) — on any dirty redraw,
  calls `_build_pose_timeline_state()` **and** rasters a per-frame PIL image looping
  `for frame in range(self.video.total_frames + 1)`. O(N) build + O(N) raster.

- **`_draw_pose_timeline`** (`labeling_app.py:1886-2011`) — draws only the *current
  zone's* sectors (bounded), but still calls `_build_pose_timeline_state()`, so it
  pays the full O(N) build cost on every dirty redraw.

- **Touch `draw_timeline2`** (`labeling_app.py:2231-2325`) — on any dirty redraw does
  `for frame in sorted(self.video.frames.keys())`. This iterates only *labeled*
  frames (K), not `0..total`, so it is O(K log K), materially less severe than the
  pose path. Touch `draw_timeline` (`:2137`) is bounded per-zone and is **not**
  affected.

**Cost model at 300k frames.** The pose path is the teeth of this finding (it is the
**active** mode — `config.json: "annotation_mode": "pose_3d"`). A single joint click
or scale nudge → `mark_bundle_changed` → cache `None` → next `draw_timeline2`
rebuilds state for 300,001 frames and rasters 300,001 columns synchronously on the UI
thread. That is a multi-second, full-length stall on **every** edit, exactly as the
finding describes.

Note there is already a *partial* incremental patch that proves the intent but is
incomplete: `_set_pose_scale_for_frame` (`labeling_app.py:311-315`) updates only the
edited frame's cached `scale_raw`/`scale_factor` when the cache survives — but (a) any
**joint** edit routes through `mark_bundle_changed`, which nukes the whole cache, and
(b) even for a scale edit it patches a single frame while the scale **carries
forward** to all subsequent frames, so the cached suffix is left stale. The correct
unit of invalidation is a *suffix*, not a single frame.

## How it fits the whole app

**The derived state is cumulative, and that is the crux.** `_build_pose_timeline_state`
produces, per frame, a *running* value: `active` joints stay ON until an explicit OFF,
and `scale_*` carries forward until the next `ScaleSet`/`HeadScaleSet` frame. Therefore
an edit at frame `F` can change the derived state of **every frame ≥ F**, not just `F`.
This rules out a naive single-frame cache patch as a *correct* fix — but it also means
the affected region is always a contiguous suffix `[F .. total]`, which is what makes an
incremental suffix recompute both correct and cheap for the common case (annotators edit
at or near the current playhead and move forward).

**Redraw path / callers.** `draw_timeline()` / `draw_timeline2()` are the single entry
points (touch or pose is chosen inside them via `is_pose_mode()`). They are called
together from ~11 sites: scale writes (`:760-761`, `:789-790`), click/edit handlers
(`:1344-1345`, `:1546-1547`, `:1706-1728`, `:2947`, `:2985`), settings/zone rebuilds
(`:3479-3480`, `:3911-3912`), and playback (`:2677`, already marshalled via
`self.after(0, self.draw_timeline)`). Every one of these is preceded (directly or via
the handler) by an edit that sets the dirty flags. So concentrating the fix inside the
build/draw functions covers all callers without touching call sites.

**Cache lifecycle today.** `_pose_timeline_state_cache` is set to `None` (invalidate) in
three places: `__init__` (`:139`), `_reset_zone_cache` (`:505`, on video load / template
change), and `mark_bundle_changed` (`:1085`). It is populated only in
`_build_pose_timeline_state` (`:1883`). The fix adds one field to this lifecycle
(`_pose_state_dirty_from`) and must reset it everywhere the cache is reset.

**Export schema impact: NONE.** This finding is **rendering only** — it touches the
in-memory timeline-state cache and canvas/raster drawing. No exporter
(`export_from_unified`, `export_pose_dataset`), no unified/CSV writer, and no column
set/order is involved. Guaranteed by `tests/test_export_schema.py` staying green.

## Approaches considered

**A. Incremental suffix recompute of the cached state + extract a pure builder
(chosen).** Split the state computation out of `_build_pose_timeline_state` into a
module-level *pure* function `build_pose_timeline_state(frames, total_frames)` and a
companion `update_pose_timeline_state(state, frames, total_frames, from_frame)` that
recomputes only entries `[from_frame .. total]`, seeding the running accumulators from
`state[from_frame - 1]`. `mark_bundle_changed` stops nuking the cache; instead it
records `self._pose_state_dirty_from = min(current, self.video.current_frame)`. The next
`_build_pose_timeline_state` call: full build if no cache, else suffix-update from the
recorded frame, then clears the marker. For the timeline2 raster, redraw only the pixel
columns spanning the affected suffix (bounded by `canvas_width`, not by frame count).

*Why chosen:* it is the only option that both (a) removes the per-edit full-length stall
for the realistic forward-editing workflow and (b) stays exactly correct given the
cumulative carry semantics. The suffix bound is worst-case O(N) only for an edit at
frame 0 (rare); typical edits near the playhead are O(total − F). It also makes the core
logic a pure function that a pytest can pin against the full rebuild.

**B. Debounce / coalesce rebuilds.** Keep the full O(N) rebuild but run it at most once
per idle window (e.g. `self.after(150, ...)`, cancelling the prior token). *Rejected as
the primary fix:* it only helps *bursts* of edits; a **single** edit still triggers one
full-length scan, so it does not satisfy the manual acceptance ("make an edit and
confirm the timeline updates without a full-length stall"). Worth layering on top of A
later if rapid scale-slider drags prove chatty, but not load-bearing here.

**C. Cache the rasterized overview, invalidate only affected columns.** Keep the
`PhotoImage` and repaint only the columns for changed frames. *Rejected as primary:*
because the *state* is cumulative, "changed columns" = the whole suffix `[F..N]` mapped
to pixels — so you still need approach A's suffix state recompute to know what to paint.
C is therefore the *rendering half* of A (redraw suffix columns rather than
`delete("all")` + full raster) and is folded into A's implementation, not a standalone
alternative.

**Structural option (a proper `TimelineModel` / `TimelineRenderer`).** The review's
"structural" section already flags extracting a `TimelineRenderer` and a per-mode
timeline model. A first-class incremental timeline model (event-sourced: store
transitions, derive state lazily) would make both modes O(edits) and delete the
mode-branching in `draw_timeline*`. That is the *right* long-term shape but is a
multi-file refactor that overlaps the God-class extraction work. **Worth it only when
the structural pass lands** — not for this targeted performance fix. Approach A is the
minimal, self-contained step that also leaves a clean pure-function seam the future
model can absorb.

## Recommended implementation

1. **Extract the pure builder** (new module-level functions, e.g. in a small
   `pose_timeline.py` or alongside the pose model — keep it import-light so tests need no
   Tk):
   ```python
   def build_pose_timeline_state(frames: dict, total_frames: int) -> dict:
       """Full rebuild: for each frame 0..total_frames, the running active-joint set
       and carried body/head scale. Pure — no self, no Tk, no I/O."""

   def update_pose_timeline_state(state: dict, frames: dict, total_frames: int,
                                  from_frame: int) -> dict:
       """Recompute entries [from_frame..total_frames] in place, seeding the running
       accumulators from state[from_frame-1] (or the frame-0 defaults if from_frame<=0).
       Returns the same `state` dict. Equivalent to a full rebuild for any edit."""
   ```
   Move the body of the current loop (`labeling_app.py:1839-1882`) verbatim into
   `build_pose_timeline_state`; `_build_pose_timeline_state` becomes a thin wrapper that
   calls the pure function (or the suffix updater) and stores the result in
   `self._pose_timeline_state_cache`.

2. **Track the dirty suffix instead of nuking the cache.** Add
   `self._pose_state_dirty_from = None` next to `self._pose_timeline_state_cache = None`
   at `__init__` (`:139`) and `_reset_zone_cache` (`:505`). In `mark_bundle_changed`
   (`:1085`) replace `self._pose_timeline_state_cache = None` with:
   ```python
   f = self.video.current_frame          # (honor `index` too — dovetails with M2)
   if self._pose_state_dirty_from is None:
       self._pose_state_dirty_from = f
   else:
       self._pose_state_dirty_from = min(self._pose_state_dirty_from, f)
   ```
   Also fold the existing scale-write patch (`:311-315`) into this: on a scale edit set
   `_pose_state_dirty_from` to that frame (a scale change carries forward, so the whole
   suffix must recompute) instead of the single-frame patch.

3. **Make the wrapper choose full vs. suffix:**
   ```python
   def _build_pose_timeline_state(self):
       with self.perf.time("pose_build_timeline_state"):
           cache = self._pose_timeline_state_cache
           if cache is None:
               state = build_pose_timeline_state(self.video.frames, self.video.total_frames)
           elif self._pose_state_dirty_from is not None:
               state = update_pose_timeline_state(
                   cache, self.video.frames, self.video.total_frames,
                   self._pose_state_dirty_from)
           else:
               return cache
           self._pose_timeline_state_cache = state
           self._pose_state_dirty_from = None
           return state
   ```

4. **(Optional, same PR) redraw only the affected columns in `_draw_pose_timeline2`.**
   When only a suffix changed and the canvas size is unchanged, repaint the raster
   columns for `[from_frame..total]` onto the retained `PhotoImage` instead of
   `delete("all")` + full raster. If this proves fiddly with Tk `PhotoImage`
   mutation, keep the full raster for now — the state-build fix alone removes the
   dominant O(N) cost, and the raster loop is pure-Python-light per column. Gate this on
   measured benefit; do not over-engineer.

5. **Touch `draw_timeline2` (`:2259`), lower severity.** Leave the correctness alone;
   optionally cache the sorted key list (`self._sorted_frame_keys`) invalidated whenever
   a frame is first inserted, to drop the per-redraw `sorted()`. This is O(K log K) on a
   small K today — note it, do it only if trivial, and do **not** let it expand scope.

**Behavioural contract after the change:**
- Cache miss (video load, zone/template change, canvas resize) → identical full build,
  identical pixels.
- Cache hit + edit at frame F → suffix recompute `[F..total]`; the resulting `state`
  dict is **bit-identical** to a full rebuild (asserted by the automatable test).
- No edit since last build → cache returned as-is (unchanged behavior).

## Edge cases & failure modes

- **Edit at frame 0** → suffix = whole video = O(N). Correct, just no speedup; this is
  the rare case and no worse than today.
- **`from_frame > total_frames`** (defensive) → nothing to recompute; return cache.
- **`from_frame <= 0`** → seed from frame-0 defaults (active set empty, scale 1.0),
  i.e. equivalent to a full rebuild.
- **Cache reset paths must also reset `_pose_state_dirty_from`** (`__init__`,
  `_reset_zone_cache`, and any load path). If a reset clears the cache but leaves a
  stale `dirty_from`, the next build would try a suffix update against `None` — guard by
  treating `cache is None` as "full build" regardless of `dirty_from`.
- **Zone change in `_draw_pose_timeline`** does not change the *state* (only which
  sectors are drawn), so it must **not** set `_pose_state_dirty_from`; it already only
  sets `_timeline_dirty`. Keep that distinction — state invalidation is for *data*
  edits, `_timeline_dirty` is for *view* changes.
- **Non-pose (touch) mode** never touches `_pose_state_dirty_from`; the new field is
  inert there.

## Testing / verification plan

**(a) Automatable — pure-function equivalence + a rebuild-cost assertion.**
New file `tests/test_timeline_incremental_H2.py` (black-box, no Tk — imports only the
extracted pure functions, matching the `conftest.py` `sys.path` + `frames`-dict style):

1. `test_H2_incremental_equals_full_rebuild`: build a pose `frames` dict with joint
   ON/OFF events and `ScaleSet` frames scattered across e.g. 0..50; compute
   `full = build_pose_timeline_state(frames, 50)`; then simulate an edit at frame F,
   mutate `frames[F]`, run `update_pose_timeline_state(prior_state, frames, 50, F)`, and
   assert it **equals** `build_pose_timeline_state(frames, 50)` (fresh full rebuild) for
   several F (early, middle, late). This is the correctness pin.
2. `test_H2_suffix_touches_only_from_frame_onward`: wrap `frames` in a dict-like /
   counter that records which frame indices `update_pose_timeline_state` reads, edit at
   frame F, assert **no** index `< F - 1` is accessed (i.e. it does not scan from 0).
   This is the perf assertion expressed as a **scan-count / rebuild-count** bound rather
   than wall-clock, so it is deterministic in CI.

Command + expectation:
```
uv run pytest tests/ -k H2 -v
```
Expected: both tests **green** after the fix. (Before the extraction they don't exist /
the functions aren't importable — this is a new-capability test, not a red/green on the
old symbol, since the old code has no pure seam to call.)

**Schema guard (must stay green — proves rendering-only):**
```
uv run pytest tests/ -k schema -v
```
Expected: `tests/test_export_schema.py` touch + pose schema locks pass unchanged.

**Full suite sanity:**
```
uv run pytest tests/ -v
```

**(b) Manual checklist** — `uv run python src/main.py` on a **long** clip (ideally the
active 3D `pose_3d` mode; a several-thousand-frame clip is enough to feel the stall):
1. Load the clip in 3D Mismatch mode; wait for the first timeline draw.
2. Navigate near the end, left-click a joint (ON). **Confirm** the timeline updates
   promptly with **no** full-length freeze.
3. Nudge the body scale slider; confirm the overview updates and the carried scale
   propagates forward correctly (the suffix, not just one column).
4. Jump to an early frame, add an event; confirm downstream frames reflect the change
   (cumulative carry still correct) — this is the worst-case suffix, verify correctness
   over speed here.
5. Resize the window (forces a full rebuild) and confirm the overview matches what
   incremental produced (no drift).
6. Touch mode smoke: load a touch clip, add an onset/offset pair, confirm both timelines
   still render correctly.

## Interactions with other planned fixes

- **H1 (Tkinter called from background threads).** Both findings live on the redraw
  path, so ordering matters. H1 marshals widget mutations to the UI thread; H2 changes
  *what the draw costs* and adds the `_pose_state_dirty_from` bookkeeping. They are
  orthogonal surfaces (H1 = *where* the call runs, H2 = *cost* of the call). Key
  ordering constraint: `_pose_state_dirty_from` is written by edit handlers (UI thread)
  and read by the draw (UI thread; already `self.after(0, self.draw_timeline)` from the
  playback thread at `:2677`), so it stays single-threaded — **do not** let any H1
  refactor move the dirty-marker write onto a worker thread. If both land together, do
  **H1 first** (thread-safety is a correctness prerequisite), then H2 on top; if H2
  lands first, its logic is unaffected because the marker is already only touched on the
  UI thread today.
- **M2 (`mark_bundle_changed` ignores `index`).** H2 edits the same function. Honoring
  the `index` argument when computing `_pose_state_dirty_from` (falling back to
  `current_frame`) folds M2's intent in cleanly and makes the suffix marker robust to
  any future non-current edit. Coordinate so the two fixes don't conflict-edit the same
  lines — land them in one touch of `mark_bundle_changed`.
- **H3 (full re-read/rewrite on save).** Independent (persistence path, not render), but
  shares the theme "O(total) per operation on long videos". No code overlap.
- **Sort Frames removal / C3.** Independent; no timeline involvement.

## Effort estimate & risk

- **Effort:** ~2–3 h. Bulk is the pure-function extraction + the two tests; the wrapper
  and `mark_bundle_changed` change are small. The optional column-only raster (step 4)
  and touch sorted-key cache (step 5) can be deferred.
- **Risk:** Low-to-moderate. The one real hazard is the **cumulative-carry correctness**
  of the suffix recompute — fully covered by the equivalence test
  (`test_H2_incremental_equals_full_rebuild`). Secondary hazard is forgetting to reset
  `_pose_state_dirty_from` on a cache-reset path; the `cache is None → full build` guard
  neutralizes it.
- **Rollback:** revert the module-extraction + the `mark_bundle_changed` diff; the
  behavior returns to full-rebuild-on-every-edit.
- **Operational footprint:** **code-only. NO version bump.** Verify with
  `uv run pytest tests/ -k H2` (+ `-k schema`), then relaunch `uv run python src/main.py`
  for the manual long-clip check. No data migration, no config change, no on-disk format
  change.
