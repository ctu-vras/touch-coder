# Fix M9 — `on_close` asks *after* tearing down; Cancel leaves a half-dead app

## Problem (re-verified at HEAD)

> ⚠️ **Re-anchor warning.** `labeling_app.py` is being edited **concurrently** by the H1
> threading fix, and line numbers below shifted *while this plan was being written*
> (`on_close` was observed at `:3622`, then `:3633`). The implementer MUST re-read
> `on_close` / `custom_confirm_close` / the `_loader_pool` lifecycle **after H1 lands** and
> anchor by symbol names only. If H1 introduced its own shutdown/teardown sequence (thread
> stop flags, pool shutdown), slot this fix's ordering around it: **ask first, then
> save/tear down**.

Current sequence in `LabelingApp.on_close` (`labeling_app.py`, symbol anchor; wired to the
window X via `app.protocol("WM_DELETE_WINDOW", app.on_close)`, `ui_components.py:40`):

```python
def on_close(self):
    saved = False
    if self.video is not None:
        self.save_data()                 # 1. save
        self.save_last_position()
        self._finalize_video_time()
        saved = True
    try:
        self._loader_pool.shutdown(wait=False, cancel_futures=True)   # 2. kill pool
    except Exception as e:
        print(f"WARN: loader pool shutdown failed: {e}")
    custom_confirm_close(self, saved)    # 3. THEN ask "Do you want to close?"
```

`custom_confirm_close` (`labeling_app.py:92-117`) is a modal whose **Cancel** just destroys
the dialog and returns — the root window survives, but the loader pool is already dead.
Every subsequent prefetch goes through `_maybe_submit_load`, whose
`except RuntimeError: ... discard` (comment: *"Pool was shut down (e.g. on app close)"*)
**deliberately swallows** the post-shutdown submit error — so buffering silently stops for
the rest of the session: the "Buffer Loading" pill never recovers and navigation degrades
with zero feedback. Reproduces at HEAD.

**Drift since the review** (which cited `:3626-3637`): the body gained
`save_last_position()`, `_finalize_video_time()`, and the try-wrapped pool shutdown
(C1/H5-era changes). New wrinkle from **C4 (landed)**: `save_data` can now **abort** — on
`PoseUnifiedReadError` it shows a messagebox and bare-`return`s (symbol `save_data`, pose
branch) *without signalling the caller*. `on_close` still sets `saved = True`, the dialog
claims "Progress was saved.", and clicking OK destroys the app — discarding exactly the
unsaved edits C4's abort was protecting.

## How it fits the whole app

- `on_close` is the final-save choke point (PROJECT.md workflow step 6): unified + export +
  metadata via `save_data`, then last-position and labeling-time persistence.
- All worker threads are daemons; `self.destroy()` ends the mainloop and process exit reaps
  them. The pool shutdown's only real job is cancelling queued decodes at exit — it has no
  business running before the user has consented to exit.

## Approaches considered

**A. Ask first; save and tear down only after consent; only destroy on save success
(recommended).** Matches the finding's own prescription and resolves the C4 interaction in
the same move. **Chosen.**

**B. Keep the current order but re-create the loader pool on Cancel.** **Rejected:** patches
the symptom, keeps the dishonest "Progress was saved" claim and the save-before-consent
double-work, and pool resurrection is fragile with H1 rewriting thread lifecycle.

**C. Drop the confirmation dialog (save + close immediately).** **Rejected:** the guard
against an accidental X-click on a long labeling session is deliberate UX; removing it is a
behaviour change beyond this finding.

## Recommended implementation

1. **`custom_confirm_close` → a return-value modal.** Rework to `custom_confirm_close(root)
   -> bool` using `wait_window` (OK → `True`; Cancel or dialog-X → `False`); it no longer
   destroys the root. Message becomes prospective: *"Do you want to close the application?
   Your progress will be saved."* (drop the retroactive `saved` parameter).

2. **`save_data` signals success.** `return False` in the `PoseUnifiedReadError` abort
   branch (currently a bare `return`), `return True` on the normal exit and the
   no-video early return. Existing callers (Save button, `load_video`, `analysis`) ignore
   the return value — fully compatible; do not weaken C4's abort contract.

3. **`on_close` — new order:**

   ```python
   def on_close(self):
       if not custom_confirm_close(self):
           return                        # nothing saved, nothing torn down — app fully alive
       if self.video is not None:
           try:
               ok = self.save_data()
           except Exception:
               traceback.print_exc()     # rule 0: never a silent failure on the close path
               ok = False
           if not ok and not messagebox.askyesno(
                   "Save failed",
                   "Saving failed — your latest changes are NOT on disk (see the console).\n"
                   "Close anyway and lose them?"):
               return                    # keep session alive; Changed flags still set
           self.save_last_position()     # best-effort, independent of annotation CSVs
           self._finalize_video_time()
       try:
           self._loader_pool.shutdown(wait=False, cancel_futures=True)
       except Exception as e:
           print(f"WARN: loader pool shutdown failed: {e}")
       self.destroy()
   ```

**Behavioural contract:** Cancel = zero side effects (pool alive, buffering intact). OK =
save; on success tear down and exit; on failure (C4 abort or unexpected exception) the user
explicitly chooses between staying (flags stay dirty → repair + re-save re-persists
everything) and closing with data loss — never silent either way. The user can never be
trapped: "close anyway" is always available.

## Export schema impact

**NONE.** No writer or column is touched; the only `save_data` change is a return value.
`tests/test_export_schema.py` stays green.

## Edge cases & failure modes

- **No video loaded:** confirm → destroy; Cancel → stay. (Old `saved=False` message variant
  disappears with the reworked dialog text.)
- **C4 abort path:** two dialogs appear (C4's showerror explaining the cause, then the
  close-anyway question). Acceptable; merging them would mean moving C4's messagebox — out
  of scope.
- **Close during playback:** unchanged from today (daemon threads die with the process);
  H1's boundary work owns any remaining `self.after`-after-destroy noise — see interactions.
- **Repeated X clicks:** the modal `grab_set`/`wait_window` serializes them.
- **`save_last_position` on a failed save:** still runs (atomic, independent, and the resume
  position is worth keeping even when the annotation save failed).

## Testing / verification plan

Per HANDOFF this finding lives in the **GUI shell** — verification is a manual checklist
(no headless-Tk pytest); the `save_data` return-value change is verified by code inspection
plus checklist item 3.

Manual checklist (`uv run python src/main.py`, 3D mode — the active one):
1. **Cancel keeps the app whole (the core regression):** load a video, X → Cancel, then step
   ~60 frames and Play. **Red today:** buffer pill sticks at "Buffer Loading", prefetch dead.
   **Green after:** buffering/prefetch keep working; console shows loads continuing.
2. **OK saves then closes:** edit a frame, X → OK → console shows the save lines, window
   closes, process exits.
3. **Failed save does not silently discard (C4 interplay):** corrupt
   `data/<video>_3d_unified.csv` (truncate mid-row), edit a frame, X → OK → C4 error box →
   "Close anyway?" → **No** → app stays alive and buffering still works; restore the file,
   Save (succeeds — flags were never cleared), X → OK closes cleanly. Repeat choosing
   **Yes** → app closes, on-disk file untouched.
4. **No-video:** X → Cancel stays; X → OK closes.
5. Confirm `last_position.json` and the labeling-time accumulator still update on a normal
   close (items they'd silently lose if the reordering broke them).

## Interactions with other planned fixes

- **H1 (CONCURRENT, same file, same lifecycle) — the critical one.** H1 is rewriting the
  thread↔UI boundary and may add explicit thread-stop/teardown steps around close. This plan
  intentionally specifies *ordering*, not exact code: **ask → save → (H1's teardown +
  pool shutdown) → destroy**. Re-anchor against post-H1 HEAD before writing a line.
- **C4 (landed):** consumed here via the new `save_data` return value; do not alter C4's
  raise/messagebox behaviour.
- **H3 (save rewrite):** changes how long `save_data` takes, not the close ordering;
  independent.
- **M2 (`mark_bundle_changed`)**: unrelated despite proximity.

## Effort estimate & risk

- **Effort:** ~45-60 min (dialog rework + return values + reorder + full manual checklist).
- **Risk:** Moderate — the close path exercises save, persistence, and teardown at once, and
  the file is under concurrent H1 churn; mitigated by the small diff, symbol anchoring, and
  the checklist. Behaviour changes are all on the previously-broken paths (Cancel, failed
  save); the confirmed-close happy path does exactly what it does today, in a safer order.
- **Rollback:** revert `on_close`, `custom_confirm_close`, and the two `return` statements.
- **Operational footprint:** code-only, no version bump; GUI-side manual verification only.
