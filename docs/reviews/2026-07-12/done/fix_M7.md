# Fix M7 — Resource leaks + O(n²) polling in frame extraction

## Problem (re-verified at working tree)

`src/frame_utils.py` has drifted from the review's cited lines (H5 landed here: it added
`FrameExtractionError`, made `create_frames` return a frame count, and added the
zero-frame recount guard). All four sub-claims **re-verified against the current tree**,
anchored by symbol:

**(1) No `try/finally` around `cv2.VideoCapture` / ffmpeg `Popen` — CONFIRMED, 3 sites.**
- `_extract_frames_ffmpeg`: the properties probe (`cap = cv2.VideoCapture(video_path)` …
  `cap.release()`) is straight-line — any raise between open and release leaks the handle
  (minor). Critically, everything after `process = subprocess.Popen(cmd, ...)` — the
  `while process.poll() is None` loop, drainer joins, and final counting — is unprotected:
  if `progress_cb` raises (see threading note below) or `_count_jpg_files` raises anything
  but `FileNotFoundError` (e.g. `PermissionError`), the exception escapes with **ffmpeg
  still running and writing frames**, pipes open, drainer threads alive.
- `_extract_frames_opencv`: `vidcap.release()` sits *after* the decode loop with no
  `finally`; a raise from `cv2.imwrite` (`cv2.error`) or from `progress_cb` skips it.
  Bonus silent failure: `cv2.imwrite`'s **`False` return is never checked** — a full disk
  writes nothing and the loop happily continues (rule-0 violation; H5's guard only catches
  the *all*-zero case).

**(2) O(n²) polling — CONFIRMED.** The ffmpeg poll loop calls `_count_jpg_files`
(a full `os.listdir` + suffix scan) once per `progress_interval_s` (1.0 s) while the
directory grows. For a video of *n* frames extracted over *T* seconds that is *T* scans of
an average *n/2* entries; since *T* grows with *n*, total filename touches are ~O(n²).
On a 200k-frame video the per-scan cost visibly climbs toward the end (NTFS listdir of
200k entries is non-trivial), stealing time from the very process it's watching.

**(3) Reliability copy calls `progress_cb` once per file — CONFIRMED.** In
`create_frames`' Reliability branch, `progress_cb(index + 1, total_files, ...)` fires for
**every copied file**. The callback (`labeling_app._open_frame_progress_window.update`)
runs `win.update_idletasks()` + `win.update()` — a *full Tk event-loop pump* — so a 200k
frame copy performs 200k event pumps. The other two extraction paths already throttle to
`progress_interval_s`; this one doesn't.

**(4) Indiscriminate copy of non-frame files — CONFIRMED, and it undermines H5.**
`frame_files = os.listdir(original_frames_dir)` copies *everything* (`Thumbs.db`, stray
exports, …). Worse: `total_files` counts those non-frames, so a source dir holding one
stray file and **zero actual frames** returns "success" (`total_files >= 1`) and skips
H5's `FrameExtractionError` guard → app opens onto blank frames, the exact H5 scenario.
A stray *subdirectory* makes `shutil.copy2` raise `PermissionError`/`IsADirectoryError`,
which is **not** a `FrameExtractionError`, so it sails past `load_video`'s handler
(console traceback, labeling-time timer leaks — H5's `_stop_video_timer_if_any` runs only
in the typed-except branch).

**Threading / crash-on-close note (TODO.md: "app crashing when closed during frame
generation").** Extraction runs **synchronously on the Tk main thread**
(`load_video` → `_open_frame_progress_window` → `create_frames`), so H1's
worker-thread rule is *not* violated. But the progress callback's `win.update()` pumps
the full event queue, making the app **re-entrant mid-extraction**: an X-click on the main
window runs `on_close` → `custom_confirm_close` → OK destroys the root *while
`create_frames` is still on the stack*. The next progress tick then hits
`win.winfo_exists()` — which sits **outside** the callback's `try/except tk.TclError` —
raising `TclError: application has been destroyed`, which escapes the ffmpeg poll loop
(**orphaning a live ffmpeg.exe** that keeps writing frames), is not a
`FrameExtractionError` so bypasses `load_video`'s handler, and finally re-raises inside
`progress_close()`. That is the reported crash, and M7's missing cleanup is exactly why
it leaves an orphaned ffmpeg behind. This plan fixes the **resource half** (ffmpeg always
reaped, handles always released, failure typed and logged); the **teardown-ordering half**
(should close even be possible mid-extraction?) is M9-adjacent — see DECISION D1.

## How it fits the whole app

Frame extraction is a one-off cost on first open of a video (PROJECT.md "Background
processes"): `load_video` calls `check_items_count`, and only on mismatch runs
`create_frames` synchronously behind a progress Toplevel. Three paths: Reliability copy →
ffmpeg (primary) → OpenCV (fallback). Single caller (`labeling_app.py`, `load_video`,
`except FrameExtractionError` since H5). Nothing downstream reads anything from this
module except the produced `frames/frameN.jpg` files and the returned count (ignored).
`tests/bench_frame_extraction.py` keeps private copies of the extractors — no dependency
on these internals.

## Approaches considered

**A. Targeted hardening inside `frame_utils.py` only: `try/finally` around every
handle/process, an O(1)-amortized sequential-probe progress counter, filtered + throttled
reliability copy, and a funnel so unexpected extraction errors reach the caller as
`FrameExtractionError` (recommended, chosen).** Smallest faithful diff; every piece is
pure-python testable; caller untouched.

**B. Parse ffmpeg `-progress pipe:1` for exact progress.** Rejected: changes the spawn
contract and adds shared-state parsing in the drainer thread — more moving parts than a
progress bar warrants; the sequential probe achieves the same O(n) total cost without
touching the command line. Keep in the back pocket if the probe ever misbehaves.

**C. Move extraction to a worker thread with real cancel support.** Would also fully fix
crash-on-close, but reshapes `load_video`'s synchronous flow and the H1 thread↔UI
boundary — far beyond this finding. Rejected here; noted under DECISION D1 / M9.

**D. Keep `os.listdir` polling but back the interval off adaptively.** Rejected: still
rescans the whole directory, just less often; strictly worse than the probe.

## Recommended implementation

All changes confined to `src/frame_utils.py`.

1. **`_extract_frames_ffmpeg` — reap ffmpeg on every exit.**
   - Props probe: `cap = cv2.VideoCapture(video_path)` → `try:` reads `finally:
     cap.release()`.
   - Wrap everything after `Popen` in `try/finally`. In the `finally`: if
     `process.poll() is None`, log
     `ERROR: terminating ffmpeg (pid=..., video=...) after extraction error`, then
     `process.kill()` + `process.wait(timeout=5)` (wait wrapped, WARN on timeout);
     best-effort close `process.stdout`/`stderr`; join both drainers with the existing
     5 s timeout. The normal path reaches the same `finally` with the process already
     exited → no-op besides the joins it already does.
2. **Kill the O(n²) poll.** New pure helper
   `_advance_sequential_count(frames_dir, count)`: advance `count` while
   `os.path.exists(frames_dir/frame{count}.jpg)` and return it (ffmpeg writes
   `-start_number 0`, strictly sequential, so this is O(1) amortized per frame — one
   `exists` miss per poll, ~O(n) total instead of O(n²)). The poll loop keeps a running
   `count` and feeds it to `progress_cb`. The **final** authoritative count after exit
   stays the single full `_count_jpg_files` (and H5's recount in `create_frames` is
   untouched).
3. **`_extract_frames_opencv` — release + no silent write failures.** Wrap the decode
   loop in `try/finally: vidcap.release()` (drop the trailing release). Check
   `cv2.imwrite`'s return: on `False`, log
   `ERROR: cv2.imwrite failed at frame {count} -> {frame_path} (disk full / unwritable?)`
   and raise `FrameExtractionError` — continuing would silently drop frames (rule 0).
   *(Slightly beyond the finding's letter; see DECISION D3 — default applied.)*
4. **Reliability copy in `create_frames` — filter, throttle, type the failure.**
   - Filter: `frame_files = [f for f in os.listdir(original_frames_dir) if
     _FRAME_RE.match(f)]` (regex already module-level). WARN-log skipped count + a small
     sample. With filtering, a frames-free source dir yields `total_files == 0` → H5's
     existing zero-guard now fires correctly (fixes the false-success hole in (4)).
   - Throttle `progress_cb` to `progress_interval_s` via the same
     `last_progress_ts` idiom the other two paths use, plus one guaranteed final
     `progress_cb(total_files, total_files, ...)` call. *(UX change — DECISION D2,
     default applied.)*
   - Wrap the `shutil.copy2` loop in `try/except OSError as exc:` → log
     `ERROR: reliability frame copy failed: {src} -> {dst}: {exc!r}` → raise
     `FrameExtractionError(...) from exc`, so the caller's existing handler
     (messagebox + timer cleanup) applies.
5. **Funnel unexpected extraction errors (completes H5's contract).** In
   `create_frames`, wrap the `_extract_frames_ffmpeg` / `_extract_frames_opencv` calls:
   `except FrameExtractionError: raise`; `except Exception as exc:` → log
   `ERROR: unexpected failure while extracting frames from {video_path!r}: {exc!r}` +
   `traceback.print_exc()` → `raise FrameExtractionError(...) from exc`. Result: a
   raising `progress_cb` (the crash-on-close trigger) or a `PermissionError` from
   counting now reaches `load_video`'s typed handler with ffmpeg already reaped, instead
   of an unhandled traceback + orphaned process.

No changes to `labeling_app.py`, the progress-window callbacks, `check_items_count`, or
the extraction command line.

## DECISION (Lucas)

- **D1 — close-during-extraction UX (open).** With this fix, closing mid-generation
  reliably kills ffmpeg, releases handles, and produces a logged, typed error — but the
  session still ends ungracefully (the root is already destroyed, so the caller's
  "Frame Extraction Failed" messagebox may itself fail; see edge cases). Options:
  (a) accept that and let **M9**'s ask-first `on_close` rework own the remaining
  teardown ordering (recommended default — no extra code in M7);
  (b) temporarily block/defer app close while the extraction progress window is up
  (swap the root's `WM_DELETE_WINDOW` handler for the duration);
  (c) real cancellation (worker thread — approach C). Pick one; this plan implements the
  resource guarantees regardless.
- **D2 — reliability-copy progress granularity (default applied unless overruled).**
  Per-file → throttled ~1/s. The bar updates less often but the copy itself gets
  dramatically faster (no 200k event pumps). Chosen default matches the ffmpeg/OpenCV
  paths' existing 1 s cadence; overrule cheaply if you want a smaller interval.
- **D3 — `cv2.imwrite` returning `False` becomes a hard failure (default applied unless
  overruled).** Today a mid-video write failure (disk full) silently continues and may
  finish "within tolerance" with frames missing. Default: log + raise
  `FrameExtractionError`. Overrule to WARN-and-continue if partial extractions are ever
  preferable.

## Export schema impact

**NONE.** `frame_utils.py` writes only `frames/frameN.jpg`; no CSV writer, column, or
value encoding is touched anywhere in this plan, and the single caller is unchanged.
Guard: `uv run pytest tests/ -k schema` stays green.

## Edge cases & failure modes

- **ffmpeg binary missing:** `_get_ffmpeg_exe` returns `None` before any `Popen` —
  unchanged early `return False`.
- **Stale frames from a previous partial extraction:** inflate the sequential probe
  exactly as they inflate today's `os.listdir` count — no regression; the final count and
  H5 recount stay authoritative.
- **Numbering gap (hypothetically non-sequential output):** the probe would freeze the
  *progress bar* only; extraction, final count, and the tolerance check are unaffected.
- **`process.kill()` races a natural exit:** guarded by the `poll() is None` check;
  `wait(timeout=5)` failure logs WARN, never raises out of the `finally`.
- **Reliability source dir with only non-frame files:** filtered `total_files == 0` →
  `FrameExtractionError` (today: false success → blank-frame session).
- **Directory entry named like a frame (`frame1.jpg/`):** passes the regex, `copy2`
  raises `OSError` → typed, logged failure (today: raw traceback + timer leak).
- **Root destroyed mid-extraction (TODO crash):** ffmpeg reaped + typed error; the
  caller's `messagebox.showerror` can still `TclError` on a dead root — accepted residual
  until D1/M9. Net change: no orphaned `ffmpeg.exe`, everything logged.
- **`progress_cb=None`** (unit tests, headless): all new throttle/final-call code is
  behind `if progress_cb:` — unchanged behavior.

## Testing / verification plan

New `tests/test_frame_utils_m7.py` — pure-python, black-box against `frame_utils`,
deterministic (monkeypatched `cv2` / `Popen` / clock; no real codecs — same style as
`tests/test_frame_extraction_h5.py`). Run: `uv run pytest tests/ -k M7 -v`, red before →
green after. The Reliability tests `monkeypatch.chdir(tmp_path)` and build
`Labeled_data/<vid>/frames` **inside `tmp_path`** (the copy path hardcodes the relative
`Labeled_data` root; never touch the real tree — HANDOFF rule). Reuse conftest's
`make_frame_jpgs`.

1. `test_M7_reliability_copy_filters_non_frames` — source dir: `frame0..4.jpg` +
   `Thumbs.db` + `notes.txt`. **Red:** return is 7 and `Thumbs.db` lands in the
   destination. **Green:** return 5, destination holds exactly the 5 frames, `capsys`
   shows the skipped-files WARN.
2. `test_M7_reliability_nonframes_only_raises` — source dir with *only* `Thumbs.db`.
   **Red:** returns 1 (false success past H5's guard). **Green:**
   `pytest.raises(FrameExtractionError)`.
3. `test_M7_reliability_progress_throttled` — 100 frame files, recording `progress_cb`,
   monkeypatched `frame_utils.time.time` (controlled clock). **Red:** 100 calls.
   **Green:** calls bounded by the fake clock's ticks and the last call is
   `(100, 100, ...)`.
4. `test_M7_sequential_probe` — `_advance_sequential_count` on a tmp dir:
   `frame0..4.jpg` → returns 5 from 0; add `frame5..9.jpg` → returns 10 resuming *from 5*;
   a lone `frame11.jpg` beyond the gap is not counted. (**Red:** helper doesn't exist →
   `AttributeError` at collection.)
5. `test_M7_opencv_capture_released_on_error` — fake `VideoCapture` (opened, yields
   frames, records `release()`), `frame_utils.cv2.imwrite` monkeypatched to raise.
   **Red:** exception propagates with `released is False`. **Green:** raises, and
   `released is True`.
6. `test_M7_opencv_imwrite_false_raises` — `imwrite` returns `False`. **Red:** loop runs
   to completion silently. **Green:** `FrameExtractionError` + ERROR line in `capsys`
   (D3).
7. `test_M7_ffmpeg_process_killed_on_progress_error` — monkeypatch `_get_ffmpeg_exe` →
   dummy path, `VideoCapture` → fake with `total_frames=10`, `subprocess.Popen` → a
   `FakeProcess` (`poll()` → `None`, records `kill()`, fake pipes), `time.sleep` → no-op;
   `progress_cb` raises `RuntimeError` on first call; call `_extract_frames_ffmpeg`
   directly. **Red:** raises with `FakeProcess.killed is False` (orphan). **Green:**
   raises, `killed is True`.
8. `test_M7_unexpected_error_funneled` — monkeypatch `_extract_frames_ffmpeg` to raise
   `OSError`; `create_frames` must raise `FrameExtractionError` with
   `__cause__` the `OSError`. **Red:** raw `OSError` escapes.

Regression guards: `uv run pytest tests/ -k H5 -v` (same file — contract preserved) and
`uv run pytest tests/ -k schema -v` must stay green.

**Manual checklist** (`uv run python src/main.py` — GUI half):
1. Fresh long video: progress window updates ~1/s with sane count/ETA; console shows no
   growing per-poll stalls late in extraction (previously each listdir got slower).
2. Reliability mode on an already-extracted video: copy is visibly faster; progress
   updates ~1/s and finishes at N/N (D2 behavior).
3. Close the app mid-generation (the TODO crash): console shows the
   `terminating ffmpeg (pid=...)` ERROR; `tasklist | findstr ffmpeg` shows **no** orphaned
   process; frames dir stops growing. (App exit is still ungraceful pending D1/M9 —
   record what you see.)
4. H5 re-check: renamed-text-file "video" still yields the "Frame Extraction Failed"
   dialog and clean abort.

## Interactions with other planned fixes

- **H5 (landed, same file — the critical one):** this plan *preserves* H5's contract
  (`create_frames` returns count; zero-frame raise; reliability zero-guard) and extends
  the same exception type to newly-surfaced failure paths (funnel, copy OSError, imwrite).
  Re-run `-k H5` after landing; test 2 above actually *strengthens* H5's guard.
- **M9 (planned, `on_close`):** the TODO note "app crashing when closed during frame
  generation" is **split between M7 and M9**: M7 delivers the resource half (no orphaned
  ffmpeg / leaked handles, typed logged failure), M9's ask-first reordering removes the
  accidental mid-extraction root-destroy. No code overlap (M7 stays in `frame_utils.py`).
  Carry DECISION D1's outcome into the M9 implementation notes.
- **H1 (landed):** extraction runs on the main thread, so H1's worker→Tk rule is not
  implicated; no `after`-marshalling needed. The `win.update()` re-entrancy documented
  above is inherent to the synchronous-progress design (D1/M9 territory), not an H1
  regression.
- **M6 (landed):** its encoding sweep touched no `frame_utils.py` site (the module has no
  builtin text-mode `open()`), verified — no overlap.
- **H2 / H3 / M8 / M11 / M12 (planned):** timelines, save path, pose loader, analysis —
  different subsystems, zero overlap.
- **`tests/bench_frame_extraction.py`:** keeps private extractor copies; unaffected.

## Effort estimate & risk

- **Effort:** ~1.5–2 h (three `try/finally` sites + probe helper + reliability-branch
  rework + funnel; 8 tests; manual checklist incl. the close-during-generation repro).
- **Risk:** Low–moderate. The happy paths are behavior-preserving (same commands, same
  files, same return values); changes bite only on failure paths and in progress-bar
  *sourcing*/cadence. Riskiest piece is the `finally` reaper — kept trivially small and
  covered by test 7. D2/D3 defaults are flagged for cheap overrule.
- **Rollback:** revert `src/frame_utils.py`; delete `tests/test_frame_utils_m7.py`.
- **Operational footprint:** code-only, single module, **no version bump**; caller and
  export pipeline untouched.
