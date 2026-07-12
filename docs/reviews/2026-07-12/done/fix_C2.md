# Fix C2 — Global nav keys (arrows / space / `d`) leak into the Note entry and corrupt annotations

## Problem (re-verified at HEAD)

`_bind_navigation` (`ui_components.py`, ~lines 281-287) binds the navigation keys on the
**root Tk window** (`app` is `LabelingApp(tk.Tk)`, i.e. the toplevel `.`):

```python
app.bind("<KeyPress-d>", app.on_middle_click)   # remove nearest dot
app.bind("<Left>",  app.navigate_left)          # step -1
app.bind("<Right>", app.navigate_right)         # step +1
app.bind("<Shift-Left>",  lambda e: app.next_frame(-app.jump_frame_count))
app.bind("<Shift-Right>", lambda e: app.next_frame( app.jump_frame_count))
app.bind("<space>", app.toggle_play)            # Play/Stop
```

`note_entry` is a `tk.Entry` child of `diagram_frame` (`ui_components.py:273`). Its Tk bindtags
are `(<instance>, "Entry", ".", "all")` — the toplevel `.` is the same window the nav keys are
bound on. When the entry has focus, a keystroke fires bindings in bindtag order: the `Entry`
**class** binding inserts the character but does **not** `return "break"`, so the event
continues to the toplevel `.` binding and the nav handler **also** runs. Result while typing a
note:

- `space` → `toggle_play` starts/stops playback (and the space is still inserted),
- `d`     → `on_middle_click` deletes the nearest dot on the current frame,
- `Left`/`Right` → `navigate_left/right` jump frames instead of only moving the text cursor,
- `Shift+Left`/`Shift+Right` → fast frame jumps.

Because `on_middle_click` mutates the current frame's data and calls `mark_bundle_changed`,
typing a note containing `d` silently destroys annotation dots — a data-loss bug, not just an
annoyance.

**Confirmed dead guard.** `disable_arrow_keys` (`labeling_app.py:1142-1149`) exists to tear
down these bindings but is **never bound or called anywhere** (grep: only its definition and
the sibling `enable_arrow_keys`; the only *call* is `enable_arrow_keys()` at the top of
`save_note`, `labeling_app.py:3048`). So there is **no focus guard in effect today**.
Additionally, even if it were wired up it is **incomplete**: `disable_arrow_keys` only unbinds
`<Left>/<Right>/<Shift-Left>/<Shift-Right>` — it never touches `<space>` or `<KeyPress-d>`, and
`enable_arrow_keys` never re-binds them either. So the arrow-only guard the original author
sketched would still leak `space` and `d`.

**Shared-handler trap (important for the fix).** `on_middle_click` is bound in **two** places:
`<Button-2>` on the `diagram_canvas` instance (`ui_components.py:225`) **and** `<KeyPress-d>`
on the toplevel (`ui_components.py:282`). Any guard must therefore sit at the **key-binding
site**, not inside `on_middle_click`, or it would also suppress legitimate middle-mouse dot
removal whenever the note entry happens to retain focus.

**Scope of the leak is exactly the six toplevel `app.bind(...)` key bindings.** The canvas
mouse bindings (`Button-1/2/3` on `diagram_canvas`) are on the canvas *instance* bindtag, not
the toplevel, so they do not reach `note_entry`. The `bind_all` wheel/`global_click` bindings
live on the `"all"` tag and are mouse-only.

## How it fits the whole app

- **UI-only.** No persistence, model, or export code is touched. `note_entry` is dual-purpose
  (freeform notes via `save_note`, and frame jump via `select_frame`); neither reads the key
  bindings, so both keep working.
- **Existing precedent for a focus guard:** `global_click` (`labeling_app.py:1118-1124`)
  already does `self.focus_get()` and compares against `self.note_entry` to bounce stray
  clicks. The fix reuses that exact idiom.
- **Existing precedent for `"break"`:** the quality/opacity slider widgets already swallow
  stray events with `widget.bind("<Key>", lambda _e: "break")` (`labeling_app.py:555, 613`).
- **Latent inconsistency noticed (not this fix):** the initial Shift bindings in
  `_bind_navigation` call `next_frame(...)`, while the dead `enable_arrow_keys` re-binds Shift
  to `_request_buffered_step(...)` — two different handlers for the same key. Deleting the dead
  path (below) removes the divergence for free.

## Approaches considered

**A. Focus-guard the global key handlers at the binding site (recommended). Chosen.**
Add one helper on the app, `_entry_has_focus()`, reusing the `global_click` idiom. In
`_bind_navigation`, wrap each of the six key bindings in a tiny guard that early-returns when
the note entry has focus, otherwise delegates to the real handler:

```python
def _guard_key(app, cb):
    def wrapped(event):
        if app._entry_has_focus():
            return            # let the Entry handle the keystroke; run no nav action
        return cb(event)
    return wrapped
```

- Stateless — no binding is ever added/removed at runtime, so there is no focus-desync failure
  mode (window-manager focus loss, dialogs, `grab_set`, etc. cannot leave the app in a
  "permanently disabled arrows" state).
- Handlers stay pure and reusable: `on_middle_click` is untouched, so the `<Button-2>`
  middle-mouse path is unaffected even while `note_entry` is focused.
- Localized to `_bind_navigation` + one small helper method. Enables deleting the dead
  `disable_arrow_keys`/`enable_arrow_keys` machinery.

**B. FocusIn/FocusOut toggling — wire up the dead `disable_arrow_keys`.**
Bind `note_entry <FocusIn>` → disable nav bindings, `<FocusOut>` → re-enable. Rejected:
(1) it is **stateful** — a missed `<FocusOut>` (focus stolen by a dialog/`grab_set`, or the
window deactivating) can strand the app with navigation permanently dead; (2) it requires
*extending* `disable_arrow_keys`/`enable_arrow_keys` to also cover `<space>` and `<KeyPress-d>`
(they cover only arrows today), plus reconciling the two divergent rebinding paths noted above;
(3) more moving parts than A for no robustness gain.

**C. Bind on `note_entry` with `return "break"`.**
Rejected as primarily written. Bindtag order is instance → class → toplevel → all. A `"break"`
on the **instance** tag fires *before* the `Entry` class insertion handler, so it would
**suppress the character** (typing space/`d` would insert nothing). Making it work correctly
would require either manually re-implementing insertion, or reordering `note_entry.bindtags()`
to drop/replace the toplevel `.` tag — a more surgical but more surprising change than A, and
one that silently also removes any *future* legitimate toplevel binding from the entry.
(The `bindtags()`-drop variant is viable but strictly more fragile than the stateless guard.)

## Recommended implementation

1. **Add the helper** on `LabelingApp` (next to `global_click`, ~`labeling_app.py:1118`):

   ```python
   def _entry_has_focus(self):
       """True when the Note entry currently holds keyboard focus."""
       try:
           return getattr(self, "note_entry", None) is not None \
               and self.focus_get() is self.note_entry
       except Exception:
           return False
   ```

2. **Wrap the six key bindings** in `_bind_navigation` (`ui_components.py`) with the guard
   shown in Approach A, so each nav action is skipped while the note entry is focused. The
   character still lands in the entry (the `Entry` class binding ran first). Return `None`
   (not `"break"`) so nothing else is suppressed — there are no key bindings on the `"all"`
   tag to worry about.

3. **Delete the now-dead guard machinery** (cleanup that this fix makes safe):
   - Remove `disable_arrow_keys` (`labeling_app.py:1142-1149`) — confirmed never bound/called.
   - Remove the `self.enable_arrow_keys()` call at the top of `save_note`
     (`labeling_app.py:3048`); with the focus guard the bindings are never disabled, so there
     is nothing to re-enable. Whether to also delete `enable_arrow_keys` itself is optional —
     if kept, leave a comment that it is unused; deleting it also erases the Shift-binding
     divergence noted above. Keep this step minimal and clearly separable in the diff.

   *(Out of scope, note only:* `save_note`'s `keyboard.press_and_release('tab')` defocus hack
   is finding **L3**; it is no longer needed for *correctness* once the leak is guarded, but
   changing it is a separate cleanup — do not bundle.)*

**Behavioural contract after the change:**
- Note entry focused: arrows/Shift-arrows move only the text cursor; `space` and `d` insert
  characters; playback does not toggle, the frame does not jump, and no dot is deleted.
- Note entry *not* focused (canvas/root focus): arrows/Shift-arrows/`space`/`d` behave exactly
  as before, bit-for-bit (the guard early-returns only on the focused-entry branch).
- Middle-mouse (`<Button-2>`) dot removal is unaffected in all focus states.

## Edge cases & failure modes

- **`focus_get()` raising / returning `None`** (transient during window teardown or before the
  entry exists): the `try/except` in `_entry_has_focus` returns `False` → keys behave normally.
  Fail-open is correct here (nav works; the only risk that matters — leaking *into* the entry —
  cannot happen when the entry isn't the focus).
- **Focus on another Entry/widget** (e.g. Settings dialog fields): those are separate toplevels
  / not `self.note_entry`, so `_entry_has_focus()` is `False` and the main-window nav keys are
  irrelevant there anyway.
- **`d` via middle mouse while note focused:** unaffected — the guard is only on the
  `<KeyPress-d>` binding, not on `<Button-2>` or on `on_middle_click` itself.
- **`realtime_arrow_hold` playback:** `navigate_left/right` → `_on_arrow_press` is only reached
  when the entry is *not* focused, so hold-to-play is unchanged; no arrow-hold state can be
  started from inside the entry (removing a subtle way to strand `_hold_play_active`).

## Export schema impact

**NONE.** This is a pure UI/event-binding change; no exporter, column, or CSV value is touched.
`tests/test_export_schema.py` (touch + pose schema locks) must remain green and is unaffected.

## Testing / verification plan

This is a **GUI focus/event-routing fix and is not cleanly red/green-testable via pytest.**
Tk keyboard focus and `event_generate` are unreliable headless (especially the `<space>`/`d`
insertion + propagation ordering), so there is no robust automated regression here.

**Primary: manual checklist** — launch with `uv run python src/main.py`, load any video, click
into the Note entry, then verify:

1. Type a note **containing spaces** (e.g. `left hand near mouth`) → text is entered verbatim;
   **playback does NOT start/stop** (watch the Play/Stop state).
2. Type a note **containing the letter `d`** (e.g. `dot drifts down`) → all `d`s appear in the
   text; **no dot is deleted** on the current frame (dot count on the diagram/timeline
   unchanged).
3. Press **Left/Right and Shift+Left/Right** inside the entry → the **text cursor** moves;
   the **frame does NOT change** (frame counter stays put).
4. Click **Save Note** → note persists for the frame (reopen the frame / re-focus to confirm).
5. **Click out of the entry** (onto the canvas/root) so it loses focus, then confirm nav still
   works: `space` toggles Play/Stop, `d` removes the nearest dot, arrows step frames,
   Shift+arrows fast-jump.
6. Confirm **middle-mouse** dot removal still works both when the entry is and isn't focused.

**Schema guard (must stay green):**
```
uv run pytest tests/ -k schema -v
```

**Optional / fragile automatable slice:** a pure-logic unit test of `_entry_has_focus` by
monkeypatching `app.focus_get` to return `note_entry` vs another widget and asserting the
boolean — this validates the guard predicate without exercising real Tk focus. Low value; note
only. A full `event_generate("<space>")`-into-entry test is **not** recommended (flaky on
Windows CI).

## Interactions with other planned fixes

- **Independent of C1 / C4 / M4 / H*** — no shared file region beyond the same source files.
- **C3 / Sort-Frames removal:** unrelated (that path reads the export CSV; no key bindings).
- **L2** (`on_middle_click`'s dead `else: event.x` branch → `d` uses stale `last_mouse_x/y`):
  unchanged by this fix. The guarded `d` binding still delegates to `on_middle_click`, so if
  L2 is fixed later the two do not conflict.
- **L3** (`save_note` Tab-defocus via the `keyboard` lib): adjacent — this fix removes the
  *need* for the arrow re-enable in `save_note` but does not require touching the Tab hack.
  Recommend landing L3 separately (swap to `self.focus_set()`).
- **Structural note #3 in the review** lists `disable_arrow_keys` among dead code to delete;
  this fix performs exactly that deletion, so the two are consistent (do it here, tick it there).

## Effort estimate & risk

- **Effort:** ~15-20 min (one helper method, wrap six bindings, delete the dead guard, run the
  manual checklist + schema test).
- **Risk:** Low. The non-focused path is byte-identical (guard early-returns only when the note
  entry is focused); the fix is stateless so it cannot strand navigation; handlers are untouched
  so mouse paths are preserved.
- **Rollback:** revert `_bind_navigation` to the unguarded bindings and restore
  `disable_arrow_keys` / the `enable_arrow_keys()` call.
- **Operational footprint:** code-only. **No version bump.** Verify by relaunching
  `uv run python src/main.py` and running the manual checklist; run `uv run pytest tests/ -k
  schema` to confirm the frozen export schema is untouched.
