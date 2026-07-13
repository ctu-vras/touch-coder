# TinyTouch — Code Review

**Date:** 2026-07-12
**Scope:** Full `src/` tree (~7,300 LOC). Focus: correctness bugs, data-integrity risks, performance blockers, and maintainability.
**Method:** Full read of `labeling_app.py`, `data_utils.py`, `pose_mismatch_data.py`, `video_model.py`; parallel deep review of `analysis.py`, `sort_frames.py`, `frame_utils.py`, `generate_zone_masks.py`, `ui_components.py`, `cloth_app.py`, `perf_utils.py`, `resource_utils.py`, `config_utils.py`, `main.py`. All headline findings verified against source.

---

## Executive summary

The app works, but it has grown into a **~3,900-line "God controller" (`labeling_app.py`)** that mixes rendering, threading, persistence orchestration, and UI callbacks. The most urgent problems are not stylistic — they are **data-integrity and data-loss risks** in the persistence layer, **two currently-broken features** (Sort Frames), and a **note-entry input bug that silently destroys annotations**. Because this data feeds research, the persistence issues should be treated as the top priority.

**Fix-first shortlist (details below):**

1. **[C1]** No atomic writes anywhere → a crash mid-save corrupts the source-of-truth CSV.
2. **[C2]** Global key bindings (arrows / `space` / `d`) leak into the Note text field → typing a note toggles playback, jumps frames, and deletes dots.
3. **[C3]** Sort Frames is broken twice over (`skiprows=6` header mismatch **and** `set()` on list-of-lists).
4. **[C4]** `save_pose_dataset` silently discards **all** previously-saved rows if the existing CSV can't be parsed.
5. **[H*]** Tkinter is called from background threads; per-edit O(N) timeline rebuilds stall long videos in 3D mode.

---

## CRITICAL

### C1 — No atomic writes; a crash mid-save corrupts the source-of-truth data
> **Fix plan:** [fix_C1.md](done/fix_C1.md)

**Files:** `data_utils.py:231` (`save_unified_dataset`), `:558` (`export_from_unified`); `pose_mismatch_data.py:237`, `:302`; `config_utils.py` (`save_config`); plus `save_last_position` / metadata writers in `labeling_app.py`.

Every writer does `df.to_csv(path)` / `json.dump(f)` **directly onto the live file** — no write-to-temp-then-`os.replace`. On a large video the unified/export write takes seconds; if the app is killed, the disk fills, or the OS crashes during that window, the file is left truncated. Since the unified CSV is the declared "source of truth", **prior annotation work becomes unloadable**. A half-written `config.json` also hard-crashes the next launch (see H4).

**Fix:** Add a single `atomic_write(path, write_fn)` helper (write to `path.tmp`, `flush`+`os.fsync`, then `os.replace`) and route every CSV/JSON writer through it. Consider keeping one `.bak` of the unified CSV per save.

---

### C2 — Global key bindings leak into the Note entry (silent annotation loss)
> **Fix plan:** [fix_C2.md](done/fix_C2.md)

**Files:** `ui_components.py:281-287` (`_bind_navigation`); `labeling_app.py:1142` (`disable_arrow_keys`).

`<Left>`, `<Right>`, `<Shift-Left/Right>`, `<space>`, and `<KeyPress-d>` are bound on the **root window**. Tk's default `Entry` bindings don't `return "break"`, so every keystroke typed into `note_entry` also propagates to these root handlers. Result while typing a note:

- `space` → toggles Play/Stop,
- `d` → `on_middle_click` deletes the nearest dot,
- arrows → jump frames instead of moving the cursor.

Worse: `disable_arrow_keys()` exists to guard this but **is never bound to anything** (verified — only `enable_arrow_keys()` is ever called, in `save_note`). So there is no focus guard at all.

**Fix:** On `note_entry` `<FocusIn>` disable the global nav bindings (and re-enable on `<FocusOut>`), or have the note-entry handlers `return "break"`, or gate every global key handler on `self.focus_get() is not self.note_entry`. Delete the dead `disable_arrow_keys` or wire it up.

---

### C3 — Sort Frames is broken (two independent, currently-live bugs)
> **Superseded (2026-07-12):** the Sort Frames feature is unused and is being removed. No fix needed — tracked as a cleanup in `TODO.md`. Finding kept here as a record.

**File:** `sort_frames.py:11` and `:36-37`.

1. **Header offset mismatch.** `pd.read_csv(csv_path, skiprows=6)` assumes the 5-line meta header + blank line. But `export_from_unified` has its `_prepend_header` call **commented out** (`data_utils.py:561-571`) — the modern export CSV is header-less. `skiprows=6` therefore consumes the real header row + frames 0–4, pandas promotes frame 5's data to column names, and `row['Frame']` raises `KeyError`. (Note: `import_unified_from_export` handles this correctly by *detecting* the `Program Version:` line at runtime — `sort_frames` should do the same instead of hardcoding `6`.)
2. **`set()` on a list-of-lists.** `{limb}_Zones` is serialized as a JSON **list-of-lists** (one bucket per click — see `labeling_app.py:1573,1588`). `set(zones)` on `:37` (and `set(last_zones[limb])` on `:36`) raises `TypeError: unhashable type: 'list'` on the first annotated touch. This line is outside the surrounding `try/except`.

**Fix:** Detect the header dynamically (peek at line 1); flatten zone buckets before set-diffing (e.g. `set(z for bucket in zones for z in bucket)`).

---

### C4 — `save_pose_dataset` silently discards all prior rows on a read error
> **Fix plan:** [fix_C4.md](done/fix_C4.md)

**File:** `pose_mismatch_data.py:169-190`.

When re-reading the existing unified pose CSV to upsert, a parse failure is caught by a bare `except Exception: existing_map = {}` **with no logging**. The save then proceeds and writes **only this session's changed frames**, so every previously-saved frame vanishes from the unified CSV. Combined with C1 (a corrupt/partial file is exactly when this triggers), this is a data-loss amplifier. This is the **currently-active annotation mode** (`config.json: "annotation_mode": "pose_3d"`), so it's high-exposure.

**Fix:** Log the exception; on a read failure, **abort the save** (or write a new timestamped file) rather than silently dropping history.

---

## HIGH

### H1 — Tkinter is called from background (non-UI) threads
> **Fix plan:** [fix_H1.md](done/fix_H1.md)

**File:** `labeling_app.py:2649-2682` (`background_update_play`), `:2550-2647` (`background_update`).

`background_update_play` (a daemon thread) calls `self.next_frame()` → `display_first_frame()` → `frame_label.configure(image=…)` and canvas ops **directly on the worker thread**. `background_update` reads `winfo_width()/winfo_height()` off-thread. Tkinter is not thread-safe; this is a latent source of the sporadic freezes/crashes typical of long sessions. (Some paths correctly use `self.after(0, …)` — the direct widget mutations are the problem.)

**Fix:** All widget access must be marshalled to the UI thread via `self.after(0, …)`. Let the playback thread only *advance state* and schedule the redraw.

### H2 — Per-edit O(N) timeline rebuild stalls long videos in 3D mode
> **Fix plan:** [fix_H2.md](to_do/fix_H2.md)

**File:** `labeling_app.py:1075-1088` (`mark_bundle_changed` clears `_pose_timeline_state_cache`), `:1835-1884` (`_build_pose_timeline_state`), `:2013-2075` (`_draw_pose_timeline2`).

Every pose edit sets `_pose_timeline_state_cache = None`. The next timeline draw then re-scans **all** frames `0..total_frames` to rebuild state *and* re-rasters the full overview. On a 300k-frame video, every single click/scale change triggers a full-length rescan. Same pattern in touch mode: `draw_timeline2` does `sorted(self.video.frames.keys())` on every dirty redraw.

**Fix:** Maintain the timeline state incrementally (update only the edited frame's contribution), or debounce rebuilds, or cache the rasterized overview and only invalidate affected columns.

### H3 — "Incremental" saves are full re-read + full rewrite every time
> **Fix plan:** [fix_H3.md](to_do/fix_H3.md)

**Files:** `data_utils.py:200-234`, `pose_mismatch_data.py:169-237`, and `export_from_unified` / `export_pose_dataset` (build a dict for every frame `0..total`).

Despite the "changed-only upsert" framing, each save `pd.read_csv`s the entire existing unified file (via slow `iterrows`) and rewrites the full union; the export is always rebuilt for **every** frame. Cost is O(total frames) per Save — and Save also fires on navigation-to-new-video and on close. On long videos this is a growing UI stall.

**Fix:** For the export, this may be unavoidable (it's a full snapshot) but should run off the UI thread with progress. For the unified file, a true append/patch (or an on-disk index) avoids re-serializing untouched rows.

### H4 — Config loaders (all but `load_config`) crash the app on a corrupt `config.json`
> **Fix plan:** [fix_H4.md](done/fix_H4.md)

**File:** `config_utils.py` — `load_config_flags`, `load_perf_config`, `load_parameter_names_into`, etc. call `json.load()` with no try/except, unlike `load_config` which returns `{}` gracefully.

A truncated config (see C1) raises `JSONDecodeError` on startup → hard crash with no recovery.

**Fix:** Give every loader the same defensive contract as `load_config` (catch, warn, fall back to defaults).

### H5 — OpenCV frame-extraction fallback has no failure signal
> **Fix plan:** [fix_H5.md](done/fix_H5.md)

**File:** `frame_utils.py:194-233, 267-270`.

`_extract_frames_opencv` never raises and returns nothing. If `VideoCapture` can't open the codec, the `while success:` loop simply never runs, `create_frames` returns `None`, and the app proceeds with an **empty `frames/` folder** — the user sees blank frames with no error.

**Fix:** Return/verify a frame count; raise (or surface a messagebox) when extraction produced 0 frames or the capture failed to open.

### H6 — `LimbView` model layer: create-on-read mutation + dead `UserDict` backing store
> **Fix plan:** [fix_H6.md](done/fix_H6.md)

**File:** `video_model.py:8-31`.

- `__getitem__` calls `self._frames.setdefault(frame, empty_bundle())` — **reading** `video.dataRH[f]` for an unlabeled frame permanently inserts an empty bundle, inflating `len(frames)` and leaking empties into saves. It also allocates a fresh `empty_bundle()` on every call.
- `LimbView(UserDict)` overrides only `__getitem__/__setitem__/get/setdefault`; inherited `__len__/__iter__/__contains__/keys/values` operate on the unused empty `self.data`, so `len(view)`, `f in view`, `for f in view` silently return wrong/empty results.
- `setdefault`'s `if not self._frames[frame][self._limb]:` tests a `FrameRecord` dict that is always truthy → the default is never applied.
- The captured `_frames` reference must be manually re-bound in 4 places (`labeling_app.py:3369-3372, 3394-3397`) whenever `video.frames` is reassigned; any new load path that forgets this detaches the views.

**Fix:** Make `LimbView` a thin non-`UserDict` accessor; use `.get()` semantics for reads (no mutation); resolve `self._frames` lazily from the owning `Video` (or expose limbs as methods, not dict-likes).

---

## MEDIUM

### M1 — `toggle_limb_parameter` stores the string `"None"` instead of `None`
> **Fix plan:** [fix_M1.md](done/fix_M1.md)

**File:** `labeling_app.py:2969`. The OFF→clear transition sets `new_state = "None"` (a string), whereas global params use real `None` (`_param_next_state`). The literal `"None"` is then persisted into `LimbParams` JSON and round-trips forever. Inconsistent and pollutes research data.

### M2 — `mark_bundle_changed(index=None)` ignores its argument
**File:** `labeling_app.py:1075-1088`. Always marks `self.video.current_frame`, though many callers pass a specific `idx`/`frame`. Currently benign (callers pass the current frame) but a latent trap: any future call that marks a *non-current* frame will silently mark the wrong one. Either honor the argument or drop it.

### M3 — `_Look` / gaze data is never exported
> **Fix plan:** [fix_M3.md](done/fix_M3.md)

**Files:** `data_utils.py:523-528` (export writes only `_X/_Y/_Onset/_Zones` — no `_Look`) vs `:390` (recovery reads `{limb}_Look`). The export schema has no Look column, so export→unified recovery always yields `Look=""`. `Look` also appears hardcoded to `"No"` in the click handlers, so it looks vestigial — but the mismatch is confusing and, if gaze was ever meant to live here, it's silently dropped. **Confirm whether gaze is intentionally captured only via the global "Looking" parameter**, and if so remove the dead `_Look` handling.

### M4 — Division by zero when FPS probe returns 0
> **Fix plan:** [fix_M4.md](done/fix_M4.md)

**Files:** `data_utils.py:520` (`Time_ms = (f/frame_rate)*1000`), `labeling_app.py:2341-2348` (`update_frame_counter`), `video_model.py:41,91`. OpenCV `CAP_PROP_FPS` returns `0` for some containers/VFR files. The pose exporter guards (`if frame_rate else 0.0`); the touch export and frame counter do not → `ZeroDivisionError`. `Video.get_total_frames` likewise `-1`s a failed open and does `fps:.3f` on a possibly-`None` fps.

### M5 — `_swap_lr_in_string` corrupts free text (dead-but-dangerous)
**File:** `data_utils.py:828-842` (`merge_and_flip_export`). `.applymap(_swap_lr_in_string)` swaps `L`↔`R` in **every** string cell, including notes and zone labels (`"Left Reaching"` → `"Reft Leaching"`). `.applymap` is also deprecated/removed in modern pandas. This path appears unused now (Save uses `export_from_unified`) — **delete it** rather than leave a loaded gun in the module.

### M6 — Missing `encoding="utf-8"` on builtin `open()` calls
**Files:** `data_utils.py:84,96,627,662,688,698,713,727,857`; `config_utils.py`; `analysis.py:714`; `sort_frames.py:106,215`. These default to the Windows locale (cp1252). pandas defaults to UTF-8, so the pandas-written unified CSV and the builtin-`open` readers disagree. Non-ASCII notes/names raise `UnicodeEncodeError` or round-trip to mojibake. Analysis HTML declares `<meta charset=UTF-8>` but is written without it, and interpolates the video name unescaped.

### M7 — Resource leaks + O(n²) polling in frame extraction
> **Fix plan:** [fix_M7.md](done/fix_M7.md)

**File:** `frame_utils.py:87-90,156-176,198-228,251-260`. No `try/finally` around `cv2.VideoCapture`/ffmpeg `Popen` (leaked handles / orphaned ffmpeg if `imwrite` or `progress_cb` raises). The progress loop calls `_count_jpg_files` (a full `os.listdir`) every ~second while the directory grows → roughly O(n²) on long videos. Reliability copy invokes `progress_cb` once per file (tens of thousands of Tk updates) and copies non-frame files indiscriminately.

### M8 — `load_pose_dataset` uses the slow `iterrows()` path
> **Fix plan:** [fix_M8.md](done/fix_M8.md)

**File:** `pose_mismatch_data.py:113`. The touch loaders were explicitly moved to `itertuples` to fix a "4 rows in 20s" freeze; the pose loader (the active mode) still uses `iterrows` and will reproduce that freeze on large datasets.

### M9 — `on_close` logic is inverted; Cancel leaves a half-dead app
**File:** `labeling_app.py:3626-3637` + `custom_confirm_close`. It saves, shuts down the loader pool (`cancel_futures=True`), then asks "Do you want to close?". Clicking **Cancel** keeps the window open but the loader pool is already dead → buffering silently stops working for the rest of the session. Ask first, then tear down.

### M10 — `parse_xy` silently drops non-digit coordinates → X/Y/Zones desync
> **Fix plan:** [fix_M10.md](done/fix_M10.md)

**File:** `data_utils.py:361-364`. `... if x.strip().isdigit()` discards negatives/floats/whitespace-mangled tokens, so a bad X can survive in Y (or vice-versa), misaligning the paired click lists during export→unified recovery.

### M11 — Pose `ScaleFactor` not clamped on load
> **Fix plan:** [fix_M11.md](done/fix_M11.md)

**File:** `pose_mismatch_data.py:130-132`. PROJECT.md states `ScaleFactor ∈ [0.7,1.3]`, but the loader trusts the on-disk value and `ensure_pose_bundle` only clamps when the key is absent. A hand-edited/corrupt CSV propagates an out-of-range scale into render/export.

### M12 — Analysis: error-swallowing reader + lossy transition metrics
> **Fix plan:** [fix_M12.md](done/fix_M12.md)

**File:** `analysis.py:69-84,133,149,157-165`. `_read_export_df` collapses every exception into a generic `ValueError`, hiding permission/disk/parse errors. Transition metrics use only `zones[0]` (dropping multi-zone clicks and intermediate changes), and a touch still open on the last frame fabricates a `start==end` self-transition that inflates the heatmap diagonal.

---

## LOW / cleanup

- **L1** `bind_all("<Button-1>"/"<MouseWheel>")` (`ui_components.py:99,295`) fire over the modeless Clothes dialog too — scrolling it navigates the main video. Scope bindings to the main window; the Clothes Toplevel has no `grab_set`/`transient`.
- **L2** `on_middle_click`: the `else: event.x` branch is dead (a `d`-key event is still a `tk.Event`), so the `d` shortcut always uses stale `last_mouse_x/y` (`labeling_app.py:1307-1310,1354-1357`).
- **L3** `save_note` synthesizes a global Tab keystroke via `keyboard.press_and_release('tab')` (`labeling_app.py:3071-3073`) just to defocus a widget — use `self.focus_set()`. The `keyboard` lib needs root on Linux and is caught by a bare `except`.
- **L4** `ScaleAutoCarry` / `HeadScaleAutoCarry` are in the in-memory bundle but not in the unified column list (`pose_mismatch_data.py:222-233`) → always reset to `False` on reload. Confirm intended.
- **L5** Documentation drift: pose export actually emits `HeadScaleFactor` and `{joint}_Opacity` columns not listed in PROJECT.md's pose schema.
- **L6** `render_pose_canvas`: the `with self.perf.time(...)` block is mis-indented and wraps only the early-return, so the render is never timed (`labeling_app.py:348-352`).
- **L7** `_remove_white_background` / `_apply_outline_alpha` (`labeling_app.py:325-346`) and much of `generate_zone_masks.py` use pure-Python per-pixel loops where numpy would be orders of magnitude faster (one-time cost, but slow).
- **L8** Silent `except Exception: pass` blocks (e.g. `apply_runtime_settings` label refresh `:3866`, several dialog paths) violate the project's "no silent failures" rule.
- **L9** Duplicate line (`labeling_app.py:215-216`), duplicated top-of-file imports (`data_utils.py:7-17`), scattered function-local `from data_utils import …`.
- **L10** `program_version = "7.8.0"` hardcoded in three OS branches (`video_model.py:67-72`) — release process must edit all three.
- **L11** Unguarded `int(row[0])` in notes load (`labeling_app.py:3501`) and `limb,frame,param,state = row` unpack (`data_utils.py:731`) crash on a malformed row.
- **L12** `generate_zone_masks.py` maps zone names by hardcoded 1-based index; a diagram change silently mislabels or `KeyError`s.

---

## Structural / "the mess"

This is the part you're feeling. None of it is a bug on its own, but it's why change is scary:

1. **`labeling_app.py` is a 3,900-line God class.** Rendering, two background threads, buffer eviction, persistence orchestration, pose logic, touch logic, settings UI, and lifecycle all live in one class. Suggested extraction seams that already exist as natural modules:
   - `FrameBuffer` (the `img_buffer` / `_loader_pool` / eviction machinery → its own class),
   - `PoseController` vs `TouchController` (the `is_pose_mode()` branching is pervasive — polymorphism would remove dozens of `if self.is_pose_mode():` forks),
   - `TimelineRenderer` (both timelines + pose variants),
   - `SaveManager` (the save/export/recovery orchestration in `save_data`/`load_video`).
2. **Two parallel data models** (touch `FrameBundle` vs pose bundle) share one `frames` dict and one save path via mode-branches. Formalizing a small interface (`serialize_row` / `deserialize_row` / `is_changed`) per mode would let `save_unified_dataset`/`save_pose_dataset` and the two exporters collapse toward one code path.
3. **Dead / legacy code still imported and reachable-looking:** `merge_and_flip_export`, `_prepend_header` (commented out at the one modern caller but still used by the dead flip path), `csv_to_dict`/`save_dataset` legacy CSVs, `disable_arrow_keys`, the `datavyu_*` sort variant, `Video.notes`. Deleting these removes ~several hundred lines and a lot of "wait, is this used?" friction.
4. **The dirty-flag story is split** (`Changed` bundle-level vs per-limb `changed`) and only half-wired (see H-note: `save_unified_dataset` defines but never calls `bundle_is_changed`). Pick one convention.
5. **Observability gaps** contradict the project's stated rule: several `except: pass`, the mis-timed perf block, silent frame-extraction failure, silent row-drop in pose save.

---

## Suggested order of attack

| # | Item | Why first |
|---|------|-----------|
| 1 | **C1** atomic writes + **C4/H4** stop silent row-drop & config crash | Protects the research data you already have. |
| 2 | **C2** note-entry key leak | Actively corrupts annotations during normal use. |
| 3 | **C3** fix Sort Frames (header detect + flatten zones) | A shipped feature is 100% broken. |
| 4 | **H1** move all Tk calls onto the UI thread | Root cause of sporadic long-session crashes. |
| 5 | **H2/H3** incremental timelines + off-thread export | The scalability wall for 300k-frame videos, esp. active 3D mode. |
| 6 | **M1–M4** data-correctness (`"None"` string, gaze, div-by-zero) | Cheap, prevents polluted/incorrect research output. |
| 7 | Structural extraction (`FrameBuffer`, mode controllers) + dead-code deletion | Makes everything above safer to change next time. |

Each of these can be tackled independently; nothing here requires a rewrite. Happy to start on any item — I'd suggest C1 (atomic writes) as the highest safety-per-effort win.
