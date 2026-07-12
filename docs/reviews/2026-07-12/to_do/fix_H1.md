# Fix H1 — Playback/buffer daemon threads touch Tkinter directly (thread-unsafe)

## Problem (re-verified at HEAD)

Two daemon threads start after a video loads (`labeling_app.py:193-194`):
`background_thread` → `background_update`, `background_thread_play` →
`background_update_play`. Both reach live Tk widgets off the UI thread.

**Primary violation — the playback thread mutates widgets directly.**
`background_update_play` (`labeling_app.py:2649-2682`), running on the worker thread,
calls on every play tick:

```python
self.next_frame(direction, play=True)          # labeling_app.py:2675  ← worker thread
```

`next_frame` (`labeling_app.py:3893-3912`) then, still on the worker thread:

```python
self.display_first_frame()      # 3910 — widget mutation (see below)
if not play: self.draw_timeline()   # skipped under play (OK)
self.draw_timeline2()           # 3912 — canvas ops, runs EVEN under play  ← worker thread
```

`display_first_frame` (`labeling_app.py:2763-2798`) is a dense cluster of Tk calls, all
now executing on the worker thread during playback:
- `ImageTk.PhotoImage(pil_img)` (2777) — creates a Tk image object off-thread.
- `self.frame_label.configure(image=…)` / `tk.Label(…)` + `.pack()` (2778-2782).
- `self.loading_label.config(…)` (2783, 2787).
- `self.update_note_entry()` (2789), `self.update_frame_counter()` (2790),
  `self.update_limb_parameter_buttons()` (2791), `self.update_button_colors()` (2792).
- pose mode (the **currently-active** mode per `config.json`): `update_pose_scale_label()`
  (2796) and `render_pose_canvas()` (2797).

`draw_timeline2` (`labeling_app.py:2231+`) and, in pose mode, `_draw_pose_timeline2`
(`2013+`) read `winfo_width()/winfo_height()` and issue canvas `delete`/`create_*` — also on
the worker thread during playback.

**Secondary violation — the buffer thread reads widget geometry off-thread.**
`background_update` (`labeling_app.py:2550-2647`) reads `self.video_frame.winfo_width()` /
`winfo_height()` at **2566-2567**. The inline comment (2562-2564) acknowledges this and
rationalizes it as "same risk profile as before" — it is still an off-thread Tk call.

Tkinter/Tcl is single-threaded; concurrent access from a worker thread while the main loop is
also running is undefined behaviour and is the classic cause of the sporadic freezes /
`RuntimeError: main thread is not in main loop` / silent Tcl errors that appear only in long
sessions. This finding **reproduces at HEAD**.

**What is already correct (must be preserved as the template):**
- `background_update` schedules the visible-frame paint via `self.after(0, self.display_first_frame)`
  (2580) and the status pill via `_set_loading_label_async` → `self.after(0, _apply)`
  (`1126-1137`, called at 2635/2637).
- `background_update_play` already marshals its periodic timeline refresh:
  `self.after(0, self.draw_timeline)` (2677).
- The main-thread stepping path `_buffered_step_tick` (`1287-1301`) calls `next_frame` only
  after `self.after(50, …)` re-entry — i.e. on the UI thread. `_request_buffered_step`
  correctly uses only `self._priority_event`/`self._priority_frame` (thread-safe primitives)
  to talk to the buffer thread.

So the boundary is *half-enforced today*: the buffer thread is clean except for the winfo
reads; the **playback thread is the main offender** because it calls `next_frame` directly
instead of scheduling it.

## How it fits the whole app

**The thread → UI boundary that must be enforced.** Worker threads
(`background_update`, `background_update_play`) may only:
1. read decoded JPEGs from disk and build **PIL** images (Tk-free — `_resize_for_buffer` at
   2684 is already pure CPU),
2. mutate plain-Python state and the frame buffer under `self._buffer_lock`
   (`current_frame`, `_last_step_sign`, `play`, `buffer_ready`, `img_buffer`, …),
3. signal the buffer thread via `threading.Event` (`_priority_event`) / plain attributes,
4. schedule **all** widget work through `self.after(0, …)`.

They may **not**: call `next_frame`/`display_first_frame`/`draw_timeline*`/
`render_pose_canvas`/`update_*` synchronously, construct `ImageTk.PhotoImage`, or read
`winfo_*`.

**Complete catalogue of Tk-touching calls reachable from the two daemons today:**

| Reached from | Call site | Tk operation | Status |
|---|---|---|---|
| `background_update` | 2566-2567 | `video_frame.winfo_width/height()` | ❌ off-thread read |
| `background_update` | 2580 | `after(0, display_first_frame)` | ✅ marshalled |
| `background_update` | 2635/2637 → `_set_loading_label_async` | `after(0, …)` label | ✅ marshalled |
| `background_update_play` | 2675 → `next_frame(play=True)` | see below | ❌ direct |
| `background_update_play` | 2677 | `after(0, draw_timeline)` | ✅ marshalled |
| via `next_frame` | 3910 → `display_first_frame` | `ImageTk.PhotoImage`, `frame_label.configure`, `Label`/`pack`, `loading_label.config`, `update_note_entry`, `update_frame_counter`, `update_limb_parameter_buttons`, `update_button_colors` | ❌ direct |
| via `next_frame`/`display_first_frame` (pose) | 2796-2797 | `update_pose_scale_label`, `render_pose_canvas` (canvas) | ❌ direct |
| via `next_frame` | 3912 → `draw_timeline2` (→ `_draw_pose_timeline2` in pose) | `winfo_*`, canvas `delete`/`create_*` | ❌ direct |

Note `next_frame` and `display_first_frame` are **shared** with legitimate main-thread
callers (arrow keys, wheel, timeline clicks, `_buffered_step_tick`). The fix must keep those
paths synchronous on the UI thread and only change *who initiates* the playback path — not the
functions' internals — to avoid double-scheduling.

## Approaches considered

**A. Marshal each individual UI call inside `next_frame`/`display_first_frame` via
`self.after(0, …)`.** Rejected: those functions also run on the main thread for normal
navigation; wrapping their bodies in `after(0, …)` would double-defer the common case, break
the synchronous contract `_buffered_step_tick` relies on, and scatter marshalling across dozens
of call sites. Fragile and easy to regress.

**B. Playback thread advances state only and schedules ONE redraw (chosen).**
Keep the current two-daemon design (it is I/O-motivated and, for the buffer thread, already
correct). Change `background_update_play` so that instead of
`self.next_frame(direction, play=True)` it:
1. advances `self.video.current_frame` + `self._last_step_sign` itself (plain state),
2. pokes `self._priority_event` if the destination is uncached (as `next_frame` does today),
3. schedules exactly one main-thread redraw: `self.after(0, self._render_current_frame)`,
where `_render_current_frame` is a thin UI-thread method that calls
`display_first_frame()` + `draw_timeline2()` (the existing bodies, unchanged).
The buffer thread's winfo reads are removed by caching `video_frame` width/height into plain
attributes updated from a main-thread `<Configure>` binding (and once at load), so the worker
reads `self._display_w/_display_h` instead of `winfo_*`.
**Chosen** — smallest change that fully enforces the boundary, preserves the just-shipped
realtime pacing, reuses the already-correct `after(0, draw_timeline)` pattern, and leaves the
shared `next_frame`/`display_first_frame` main-thread contract intact.

**C. Replace the playback daemon with an `after()`-driven loop (no playback thread).**
Delete `background_thread_play`; drive playback from the main thread via
`self.after(interval_ms, self._play_tick)`, where `_play_tick` checks `buffer_ready`, advances
one frame, redraws, and re-arms itself. This is the *structurally cleanest* option — it removes
an entire class of "playback thread touches UI" bugs by construction, and playback code then
runs where the widgets live. **Not chosen now** because: (a) it reworks the frame-pacing and
buffer-gating logic that was just introduced (commits `026bf13`/`50836a9`, realtime arrow-hold
playback), risking behavioural regressions in timing under main-thread contention (a slow
redraw would delay the next tick, coupling render cost to playback rate); (b) the hold-play
integration (`_begin_hold_playback`, `_hold_play_active`, `play_dir`) would need re-wiring; (c)
it does not address the buffer thread, which still needs the winfo fix anyway. It is the right
*eventual* target and should be revisited if playback threading causes further trouble — B is a
strict prerequisite/subset of C (both centralize the redraw), so B does not close the door on C.

## Recommended implementation (approach B)

1. **Add a UI-thread redraw entry point** (new method), the single scheduling target:
   ```python
   def _render_current_frame(self):
       # Runs on the Tk main thread only.
       self.display_first_frame()
       self.draw_timeline2()
   ```
2. **Rewrite the play tick** in `background_update_play` (2674-2680) to advance state on the
   worker and schedule the redraw:
   ```python
   start = time.perf_counter()
   direction_sign = 1 if direction > 0 else -1
   self.video.current_frame = next_frame          # already computed at 2666
   self._last_step_sign = direction_sign
   if self.video.current_frame not in self.img_buffer:
       self._priority_event.set()
   self.after(0, self._render_current_frame)
   if self.video.current_frame % 10 == 0:
       self.after(0, self.draw_timeline)
   interval = 1.0 / self.frame_rate if self.frame_rate else 0.04
   time.sleep(max(0.0, interval - (time.perf_counter() - start)))
   ```
   (No direct `next_frame` call remains on the worker thread. Boundary checks at 2655-2673 are
   untouched — they already read only plain state.)
3. **Cache display geometry for the buffer thread.** Add `self._display_w`, `self._display_h`
   (updated on `<Configure>` of `video_frame` and set once after load on the main thread) and
   replace the off-thread reads at 2566-2567 with those attributes. Leave `resize_frame`
   (2706-2709) as-is — it runs on the main thread and its winfo reads are legitimate.
4. **Optional dev guard** (see verification): a `_assert_ui_thread()` helper called at the top
   of `display_first_frame`, `draw_timeline`, `draw_timeline2`, `render_pose_canvas`, gated on a
   debug flag, that logs/raises when `threading.get_ident() != self._ui_thread_ident`.

Nothing in `data_utils`/`pose_mismatch_data` is touched. `next_frame`'s body is unchanged; only
its worker-thread *caller* changes.

**Export schema impact: none.** This is a UI/threading-only change; no exporter, column set,
column order, or serialized value is touched. `export_from_unified` / `export_pose_dataset` are
not in the edit set. Certified by `tests/test_export_schema.py` (must stay green).

## Edge cases & failure modes

- **Double redraw / stale paint.** Because the worker now advances `current_frame` before the
  scheduled `_render_current_frame` runs, several `after(0, …)` callbacks can queue if the UI is
  busy. `display_first_frame` already renders "whatever `current_frame` is now", so coalescing
  is naturally safe (last-writer-wins); no torn frame. If backlog is a concern, an
  `self._render_scheduled` boolean flag can debounce (schedule at most one pending redraw).
- **Close mid-playback.** On teardown `self.play` is cleared; any already-queued `after(0, …)`
  may fire after widgets are destroyed. `_set_loading_label_async` already guards with
  `getattr(self, "loading_label", None)`; `_render_current_frame` must likewise no-op if
  `self.video is None` or widgets are gone. Verify against M9 (inverted `on_close`) — do not
  regress the teardown order.
- **Pose vs touch.** The redraw path forks inside `display_first_frame`/`draw_timeline2` on
  `is_pose_mode()`; both branches must run on the UI thread. The active mode is `pose_3d`, so
  manual testing must cover pose specifically.
- **winfo cache staleness.** If the window is resized during playback, the cached
  `_display_w/_display_h` update via `<Configure>` on the next event-loop turn; a one-frame
  slightly-wrong resize is cosmetic and self-corrects. Acceptable.

## Testing / verification plan

Thread-safety is **runtime behaviour and cannot be cleanly red/green unit-tested** — Tk has no
public "assert main thread" hook and headless CI has no display. State this honestly. What *is*
checkable:

**Automatable slice (small, honest):**
- Extract the pure play-step decision from `background_update_play` into a side-effect-free
  helper, e.g. `_compute_play_step(current, total, direction) -> (next_frame, stop: bool)`, and
  unit-test boundary stop (at 0 going back, at `total` going forward) and normal advance. No Tk.
- Test the thread-identity guard helper directly: `_ui_thread_ident` is recorded at construction
  on the main thread; from a `threading.Thread`, `_is_ui_thread()` returns `False`; from the
  main thread, `True`. This locks in the guard that catches future off-thread regressions.
- (Do **not** attempt to unit-test `after(0, …)` scheduling end-to-end; it needs a live main
  loop and is covered by the manual pass below.)

**Dev-time guard (the real safety net):** enable `_assert_ui_thread()` (raises `RuntimeError`
with the offending thread name) during development and run the manual checklist — any residual
off-thread widget access fails loudly at the exact call site instead of intermittently.

**Schema guard (must stay green):**
```
uv run pytest tests/ -k schema -v
```

**Manual checklist** — `uv run python src/main.py`, console visible, watch for `Tcl_`/
`RuntimeError: main thread is not in main loop`/`invalid command name` errors:
1. Load a **long** clip in **3D Mismatch** mode (active mode). Press **Play** and let it run for
   several minutes; confirm smooth advance, live frame + timeline updates, no console errors.
2. **Hold** the right/left arrow (realtime hold-playback) for a sustained burst, release, repeat.
3. **Scrub** by clicking Timeline 2 while playing and while stopped.
4. **Switch modes / limbs / radio buttons** during and right after playback.
5. **Resize** the window during playback (exercises the winfo cache).
6. **Close the window mid-playback** (X button) — confirm clean shutdown, no post-destroy Tk
   errors, save still fires.
7. Repeat 1 in **Touch** mode to cover the non-pose redraw branch.
Pass = a multi-minute session across both modes with **zero** Tcl/threading errors in the
console.

## Interactions with other planned fixes

- **H2 (per-edit O(N) timeline rebuild) — shares the redraw path.** H1 funnels playback redraws
  through the single main-thread entry point (`_render_current_frame` → `draw_timeline2` /
  `_draw_pose_timeline2`); H2 then makes that redraw incremental/debounced. **Land H1 first** so
  H2 optimizes one well-defined UI-thread method rather than a function that is sometimes called
  off-thread. No conflict; H1 defines the seam H2 improves.
- **H3 (off-thread export).** Related threading theme but disjoint surface (save/export path, not
  playback). If H3 later moves export to a worker, it must obey the *same* boundary established
  here (schedule any progress-UI via `after(0, …)`). Coordinate the pattern, not the code.
- **M9 (inverted `on_close`).** H1's teardown-safety guard in `_render_current_frame` overlaps
  the close path; verify the two together so a Cancel/close mid-playback leaves no queued
  callback firing against destroyed widgets.
- **Independent** of C1/C2/C4/H4/M4 (different surfaces).

## Effort estimate & risk

- **Effort:** ~1.5-3 h. Add `_render_current_frame`, rewrite the ~7-line play tick, add the
  winfo cache + `<Configure>` binding, optional guard + two small unit tests, then the manual
  soak (bulk of the time is the multi-minute manual verification in both modes).
- **Risk:** Medium. Logic change to a hot, recently-touched loop; main risks are (a) a redraw
  backlog under load (mitigated by natural coalescing / optional debounce flag) and (b) teardown
  callbacks firing post-destroy (mitigated by the `self.video is None`/widget guards). The
  `frame_rate > 0` pacing math is preserved verbatim. Non-playback navigation is unaffected
  (untouched code path).
- **Rollback:** revert the `background_update_play` tick to the direct `next_frame(play=True)`
  call and drop `_render_current_frame` + the winfo cache; single-file change in
  `labeling_app.py`.
- **Operational footprint:** code-only, single file (`labeling_app.py`), no dependency change.
  **No version bump.** Verify by `uv run pytest tests/ -k schema` plus the manual soak via
  `uv run python src/main.py`; relaunch the app to confirm.
