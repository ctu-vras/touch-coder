# Fix H5 — OpenCV frame-extraction fallback silently yields an empty `frames/` folder

## Problem (re-verified at HEAD)

Frame extraction has no failure signal. Two functions in [src/frame_utils.py](../../../../src/frame_utils.py)
cooperate to lose the error:

`_extract_frames_opencv` (`frame_utils.py:194-233`) has **no `return` and never raises**. When
`cv2.VideoCapture` cannot open the container/codec, the first `vidcap.read()` returns
`(False, None)`, so the `while success:` loop (line 214) never executes, `count` stays `0`, and
the function returns `None`:

```python
success, image = vidcap.read()   # (False, None) on a bad codec
count = 0
...
while success:                    # skipped entirely
    ...
vidcap.release()                  # count is still 0
```

`create_frames` (`frame_utils.py:235-270`) then also returns `None` on every path — the
reliability copy branch `return`s at line 262, and the extraction branch (267-269) discards the
ffmpeg boolean and falls through to an implicit `None` at line 270:

```python
if not _extract_frames_ffmpeg(...):
    print("INFO: Falling back to OpenCV extraction.")
    _extract_frames_opencv(...)        # return value ignored
print("INFO: create_frames() finished.")   # no count, no verify, no raise
```

The caller `load_video` (`labeling_app.py:3447-3461`) runs `create_frames` inside a `try/finally`
whose `finally` **only closes the progress window** and whose body ignores the (non-existent)
result:

```python
if not check_items_count(frames_dir, self.video.total_frames):
    progress_update, progress_close = self._open_frame_progress_window()
    try:
        create_frames(video_path, frames_dir, self.labeling_mode,
                      self.video_name, progress_cb=progress_update)
    finally:
        progress_close()
```

Net effect: if both extractors produce zero files (unreadable file, unsupported codec, ffmpeg
binary missing *and* OpenCV can't open the stream), the app proceeds to `display_first_frame()`
(`labeling_app.py:3478`) and starts the buffer thread over an **empty `frames/` folder**. The
user sees blank frames with **no error dialog and no log-level ERROR** — a silent failure that
directly violates CLAUDE.md rule 0 ("No silent failures — always log errors").

Note the failure is currently *un-recheckable* downstream: `create_frames` returns nothing, and
`check_items_count` is only consulted **before** extraction (line 3447), never after.

## How it fits the whole app

- **Single caller.** `create_frames` is imported and called in exactly one place —
  `labeling_app.load_video` (`labeling_app.py:37` import, `:3451` call). No other module or test
  depends on its return contract, so widening the contract is low-blast-radius.
- **`create_frames` contract today:** `-> None` on all paths (reliability copy, ffmpeg success,
  OpenCV fallback, and total failure are indistinguishable to the caller).
- **Both extraction paths:**
  - `_extract_frames_ffmpeg` (`:94-191`) already returns a **bool** (`False` = unavailable or
    `rc != 0`, triggering fallback; `True` = ran to completion). It computes `frame_count` via
    `_count_jpg_files` and only *warns* on a shortfall (`:188-189`) — it never fails hard, and a
    `True` return does not guarantee non-zero output.
  - `_extract_frames_opencv` (`:194-233`) returns **nothing** and swallows the open failure
    (`is_opened` is logged at `:204` but never acted on).
- **`check_items_count` (`:20-72`) and the tolerance.** It returns a bool and is the natural
  post-extraction verifier. With `expected_count = total_frames`, `expected_files = total+1`,
  `allowed_missing = max(1, int(expected_files * 0.001))`. For the empty-folder case it returns
  `False` (`frame_count 0 < min_ok`). Crucially it *also* catches the doubly-broken case where
  the fps/frame-count probe itself returns `0`: then `expected_files = 1`, `allowed_missing = 0`,
  `min_ok = 1`, and `found 0 < 1 → False`. So `check_items_count` is a sound cross-check, but it
  is not currently run after extraction.
- **Downstream:** nothing reads a return value from `create_frames`; the fix adds a signal that
  only `load_video` consumes. The buffer thread (`background_update`) and `display_first_frame`
  are the victims of the empty folder, not participants in extraction.

## Approaches considered

**A. `create_frames` returns the produced frame count and raises a dedicated
`FrameExtractionError` on zero frames; `load_video` catches it, shows a `messagebox.showerror`,
and aborts the load cleanly. (Recommended — Chosen.)**
Directly unit-testable against `frame_utils` (monkeypatch the capture / force fallback → assert
it raises and leaves the folder empty), satisfies rule 0 (ERROR log + user-visible dialog, no
silent proceed), and is minimal: one new exception class, one recount + guard, one `except`
branch upstream. `messagebox` is already imported (`labeling_app.py:16`).

**B. Leave `create_frames` returning `None`; re-run `check_items_count(frames_dir, total_frames)`
in `load_video` after extraction and show the messagebox if it fails.**
Smallest diff to `frame_utils.py` and reuses the existing tolerance logic. **Rejected as the
primary mechanism** because the acceptance criterion asks for a red/green pytest asserting that
*`create_frames` itself* signals failure; with this approach `frame_utils` stays silent and only
the GUI layer (hard to unit-test) knows. It also couples the failure signal to a possibly-bogus
probe (`total_frames`). We do, however, **fold its idea in as defence-in-depth**: after a
*successful* `create_frames`, `load_video` can still trust the count it returns without a second
`listdir`.

**C. Restructure `create_frames` to return a result object/dataclass
(`ExtractionResult(count, method, ok, error)`).**
More self-documenting, but introduces a new type where an `int` return + an exception already
carry all the information the single caller needs. **Rejected** as unnecessary complexity /
technical debt (CLAUDE.md: "Do not introduce unnecessary complexity").

## Recommended implementation

All changes are extraction-only; **no data path, no CSV writer, no schema touched.**

**1. `src/frame_utils.py` — add a typed failure and make the count authoritative.**

- Add a module-level exception near the top:
  ```python
  class FrameExtractionError(RuntimeError):
      """Raised when frame extraction produced zero usable frames."""
  ```
- `_extract_frames_opencv`: `return count` at the end (make the count observable; also lets a
  future caller distinguish "opened but 0 frames" from a crash). Keep the existing `is_opened`
  log, but this alone does not raise — `create_frames` owns the hard failure so both paths funnel
  through one guard.
- `create_frames`: return an `int` frame count on success and raise on zero. Recount with the
  existing `_count_jpg_files(frames_dir)` **after** extraction so the guard covers *both* the
  ffmpeg and OpenCV paths uniformly:
  ```python
  # reliability copy branch:
  print(f"INFO: Frames copied successfully ({total_files} files ...).")
  if total_files == 0:
      raise FrameExtractionError(
          f"Reliability copy produced 0 frames from {original_frames_dir}")
  return total_files
  ...
  if not _extract_frames_ffmpeg(...):
      print("INFO: Falling back to OpenCV extraction.")
      _extract_frames_opencv(...)
  frame_count = _count_jpg_files(frames_dir)
  if frame_count == 0:
      msg = (f"Frame extraction produced 0 frames for {video_path!r}. "
             f"The file may be unreadable or use an unsupported codec.")
      print(f"ERROR: {msg}")          # rule 0: never fail silently
      raise FrameExtractionError(msg)
  print(f"INFO: create_frames() finished ({frame_count} frames).")
  return frame_count
  ```
  A non-zero-but-below-tolerance result stays a **warning** (unchanged behaviour): H5 is about
  the *zero-frame silent* case, not partial extraction, and the existing tolerance/`WARN` logic
  already covers shortfalls.

**2. `src/labeling_app.py` — surface the failure and abort the load.**

Wrap the call (`:3449-3459`) so the exception becomes a dialog instead of blank frames:
```python
progress_update, progress_close = self._open_frame_progress_window()
try:
    create_frames(video_path, frames_dir, self.labeling_mode,
                  self.video_name, progress_cb=progress_update)
except FrameExtractionError as exc:
    print(f"ERROR: load_video: frame extraction failed: {exc}", flush=True)
    messagebox.showerror(
        "Frame Extraction Failed",
        f"Could not extract frames from this video:\n\n{exc}\n\n"
        "The file may be unreadable or use an unsupported codec. "
        "The video was not loaded.",
    )
    self._stop_video_timer_if_any()   # see edge cases; undo the timer started at :3331
    return
finally:
    progress_close()
```
Import `FrameExtractionError` alongside the existing symbols (`labeling_app.py:37`:
`from frame_utils import check_items_count, create_frames, FrameExtractionError`). `return`ing
before `display_first_frame()` / the buffer-thread start prevents any downstream code from
running against an empty folder.

**Export schema impact: none.** This finding is extraction-only; it does not touch
`export_from_unified`, `export_pose_dataset`, or any CSV column/order. `tests/test_export_schema.py`
stays green unchanged and is included in the run below as proof.

## Edge cases & failure modes

- **Doubly-broken probe (fps/frame-count = 0) *and* successful decode of a few frames:** ffmpeg
  or OpenCV may still write N>0 files even when the probe reported 0. `frame_count > 0` → no
  raise; load proceeds. Correct — we only fail on a genuinely empty folder.
- **Reliability copy of an empty original `frames/`:** now raises instead of copying nothing and
  proceeding blank. Guarded in the copy branch above.
- **Partial extraction within tolerance:** unchanged (WARN only) — not this finding.
- **Timer already started.** `load_video` calls `self._start_video_timer(data_dir, video_name)`
  at `:3331` *before* extraction. On abort we should stop/cancel it so a failed load doesn't leak
  an accumulating labeling-time timer. If no cheap stop hook exists, at minimum note it; the
  simplest safe move is to reorder so extraction (and its guard) runs *before* the timer starts,
  or add a small guard. Flag for the implementer; do not over-engineer.
- **Progress window on Windows.** `messagebox.showerror` must fire *after* `progress_close()`;
  placing the dialog in `except` with `progress_close()` in `finally` guarantees the modal shows
  over the main window, not the (destroyed) progress Toplevel.
- **App-close during extraction** (open TODO note) is orthogonal — this fix does not change the
  synchronous-in-progress-window model, only its failure signalling.

## Testing / verification plan

**(a) Automatable red/green — `tests/test_frame_extraction_h5.py` (new).** Black-box against
`frame_utils` using `tmp_path`; deterministic (no real codec/ffmpeg dependency) via monkeypatch.

- `test_H5_zero_frames_raises`: monkeypatch `frame_utils._extract_frames_ffmpeg` to return
  `False` (force the fallback) and `frame_utils.cv2.VideoCapture` to a fake whose `isOpened()`
  returns `False` and `read()` returns `(False, None)`. Call
  `create_frames(bogus_video, tmp_frames_dir, "Normal", "vid")` and assert it raises
  `FrameExtractionError` **and** the folder holds 0 `frame*.jpg`.
  - **Red before:** `create_frames` returns `None` (no raise) → `pytest.raises` fails
    (`DID NOT RAISE`).
  - **Green after:** raises `FrameExtractionError`.
- `test_H5_returns_count_on_success`: monkeypatch `_extract_frames_ffmpeg` to write 3 placeholder
  `frame{0,1,2}.jpg` (reuse the `make_frame_jpgs` fixture in `tests/conftest.py`) and return
  `True`. Assert `create_frames(...) == 3` and no exception.
  - **Red before:** returns `None` → `assert == 3` fails.
  - **Green after:** returns `3`.

Commands:
```
uv run pytest tests/ -k H5 -v          # red -> green (this fix)
uv run pytest tests/ -k schema -v      # must stay green (schema untouched)
```

**(b) Manual.** With the app running (`uv run python src/main.py`): Load Video → pick a
deliberately unreadable/corrupt file (e.g. a `.mp4` that is actually a renamed text file, or a
clip in a codec neither ffmpeg nor OpenCV can decode). Confirm a **"Frame Extraction Failed"**
error dialog appears and the app returns to idle **instead of** opening onto blank frames. Check
the console for the `ERROR:` line naming the file. Relaunch to verify (no packaging step needed).

## Interactions with other planned fixes

- **M7 (resource leaks + O(n²) polling in frame extraction)** — same file, adjacent code. M7 will
  likely restructure `_extract_frames_opencv`'s capture handling and the ffmpeg progress polling.
  H5 only *adds* a `return count` and a post-extraction guard/raise; the two are compatible, but
  land them in a known order (recommend H5 first: small, adds the failure contract M7 can then
  preserve). Whoever lands second re-runs `pytest -k H5`.
- **H4 (config loaders crash on corrupt config)** — independent; different subsystem.
- **C1 (atomic writes)** — independent; save path, not extraction.
- **TODO note "app crashing when closed during frame generation"** — same UI region (progress
  window during extraction) but a distinct concern (teardown vs. failure signal). H5 does not
  address it and does not make it worse.
- **No overlap with the export-schema lock or the Sort Frames removal.**

## Effort estimate & risk

- **Effort:** ~30-45 min (one exception class + recount/guard in `frame_utils.py`, one
  `except`/`return` branch + import in `labeling_app.py`, one new test module).
- **Risk:** Low. The happy path is behaviour-preserving — successful extraction now merely
  *returns an int* the old caller ignored; the only new user-visible behaviour is on the
  previously-silent zero-frame path (dialog + clean abort instead of blank frames). Watch the
  timer-teardown edge case on abort.
- **Rollback:** revert both files; delete the new test module.
- **Operational footprint:** code-only. **No version bump.** Verify with `uv run pytest tests/ -k H5`
  and a relaunch of `uv run python src/main.py` for the manual check.
