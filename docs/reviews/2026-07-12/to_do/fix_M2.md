# Fix M2 — `mark_bundle_changed(index=None)` ignores its argument

## Problem (re-verified at working tree)

> ⚠️ `labeling_app.py` carries the uncommitted **H1** fix — line numbers below are
> working-tree positions and WILL drift; anchor by **symbol names**.

`LabelingApp.mark_bundle_changed` (`labeling_app.py`, ~`:1084`) accepts `index=None` and
then ignores it:

```python
def mark_bundle_changed(self, index=None):
    if self.video is None:
        return
    idx = self.video.current_frame        # <-- `index` never consulted
    b = self.video.frames.get(idx)
    if isinstance(b, dict):
        b["Changed"] = True
        self._timeline_dirty = True
        self._timeline2_dirty = True
        self._pose_timeline_state_cache = None
        if hasattr(self, "notify_bundle_changed"):
            self.notify_bundle_changed(idx)
```

`notify_bundle_changed(index=None)` (~`:1099`) has the **identical** flaw (`idx =
self.video.current_frame`), so even the honored value passed to it would be discarded.

**Complete call-site inventory (working tree):**

| # | Caller (symbol) | ~line | Passes | Equals `current_frame` at call time? |
|---|---|---|---|---|
| 1 | `on_pose_quality_changed` | :935 | `self.video.current_frame` | yes (literal) |
| 2 | `reset_pose_quality` | :955 | `self.video.current_frame` | yes (literal) |
| 3 | `on_note_changed` | :1058 | `self.frame_index` | **dead code** — see below |
| 4 | `on_middle_click` (pose branch) | :1361 | `self.video.current_frame` | yes (literal) |
| 5 | `on_middle_click` (touch branch) | :1441 | *(no arg)* | n/a — default |
| 6 | `on_diagram_click` (pose branch) | :1563 | `self.video.current_frame` | yes (literal) |
| 7 | `on_diagram_click` (touch branch) | :1615 | *(no arg)* | n/a — default |
| 8 | `parameter_dic_insert` | :3030 | `idx` | yes — `idx = self.video.current_frame` (:3010) |
| 9 | `toggle_limb_parameter` | :3068 | `frame` | yes — `frame = self.video.current_frame` (:3037) |
| 10 | `save_note` | :3146 | `idx` | yes — `idx = self.video.current_frame` (:3133); also calls `notify_bundle_changed(idx)` directly (:3150) |

Site 3 (`on_note_changed`) is dead **and broken**: it is bound to no widget/key (grep — the
only note entry point is `save_note`, wired in `ui_components.py:265`), and `self.frame_index`
is **never defined anywhere** in the class, so calling it would raise `AttributeError`.

So today the bug is benign — every *live* caller marks the current frame anyway. It becomes a
real trap the moment invalidation stops being global (see Interactions: **H2**).

## How it fits the whole app

`mark_bundle_changed` is the single chokepoint where an edit becomes persistent + visible:
`b["Changed"] = True` feeds the changed-only unified upsert (`save_unified_dataset`), and the
three invalidation flags (`_timeline_dirty`, `_timeline2_dirty`,
`_pose_timeline_state_cache = None`) drive the next timeline redraw. Because all three are
**frame-agnostic** (global nuke), ignoring `index` currently costs nothing — the masking is
structural, not accidental.

The app already contains a dirty-marking path for **non-current** frames:
`_set_pose_scale_for_frame(frame, ...)` (~`:296`) sets `bundle["Changed"] = True` directly and
patches `_pose_timeline_state_cache[frame]` per-frame — bypassing `mark_bundle_changed`
precisely because the chokepoint cannot target a frame. Honoring the argument is what allows
future consolidation of such paths.

## Approaches considered

**A. Honor the argument (recommended).** `idx = self.video.current_frame if index is None
else index`, in both `mark_bundle_changed` and `notify_bundle_changed`. Zero call-site churn
(all live callers already pass the current frame or nothing), zero behaviour change today,
and it makes the *correct* frame available the moment H2 turns invalidation per-frame.
**Chosen.**

**B. Drop the argument** (`def mark_bundle_changed(self):` + edit 8 call sites). Honest about
today's behaviour, but: (a) touches 8 sites in a file under concurrent H1 edits, (b) shrinks
the API exactly when H2 *needs* a frame-addressed marker, forcing H2 to re-widen it, and
(c) leaves `_set_pose_scale_for_frame`-style bypasses permanently un-consolidatable.
**Rejected.**

**C. A + delete the dead `on_note_changed`.** Tempting (site 3 is broken), but dead-code
removal is the review's structural/L9-style cleanup, not M2; HANDOFF forbids opportunistic
neighbours. **Rejected** — documented here, left in place.

## Recommended implementation

1. **`mark_bundle_changed`** — replace the assignment:
   ```python
   idx = self.video.current_frame if index is None else index
   ```
   Must be `is None`, **not** `if not index` — frame `0` is a valid index.
2. **`notify_bundle_changed`** — same one-line change (it receives the honored `idx` from
   step 1 and from `save_note`; leaving it arg-ignoring would re-break the debug print).
3. No call-site changes. Leave `on_note_changed` untouched (documented dead, see C).

**Behavioural contract after the change:** every live call today passes `current_frame` or
nothing → output identical, bit-for-bit. A future caller passing frame `F` marks `frames[F]`
and (post-H2) invalidates the timeline suffix from `F`.

## Export schema impact

**NONE.** Purely in-memory dirty flags and a render cache; no exporter, writer, column, or
value encoding is touched. `tests/test_export_schema.py` must stay green.

## Edge cases & failure modes

- `index=0` — valid; handled by the explicit `is None` check (the one real footgun here).
- `index` with no bundle (`frames.get(idx)` not a dict) → silently no-ops, exactly as the
  current-frame path behaves today for an untouched frame. Unchanged.
- Out-of-range / negative `index` → same no-op guard; no new failure mode.
- `self.video is None` → early return, unchanged.

## Testing / verification plan

**Automatable** — unbound-method-on-stub pattern already established in
`tests/test_h1_thread_boundary.py` ("same pattern as C2"; no Tk root needed). New
`tests/test_mark_bundle_changed_m2.py`:
- `test_M2_honors_explicit_index`: stub `self` (`SimpleNamespace`) with
  `video.current_frame=10`, `video.frames={10: {...}, 5: {...}}`; call
  `LabelingApp.mark_bundle_changed(stub, 5)`; assert `frames[5]["Changed"] is True` and
  `frames[10]` has no `"Changed"`. **Red today** (marks 10), green after.
- `test_M2_index_zero_is_honored`: same with `index=0` — pins the falsy-zero trap.
- `test_M2_default_marks_current_frame`: no arg → `frames[current]` marked (regression pin).
Assert on `Changed`/dirty flags only — **not** on `_pose_timeline_state_cache = None` — so
the tests survive H2's rework of the invalidation line.

**Manual:** in touch mode, click an onset, toggle a global + a limb parameter, save a note —
timeline updates and Save persists exactly as before (all default/current-frame paths).

Commands: `uv run pytest tests/ -k M2 -v` (red→green), `uv run pytest tests/ -k schema -v`.

## Interactions with other planned fixes

- **H2 (`to_do/fix_H2.md`) — the critical one; M2 must land FIRST.** H2 reworks exactly the
  line M2 sits next to: it replaces `self._pose_timeline_state_cache = None` with a per-frame
  suffix marker `_pose_state_dirty_from = min(marker, edited_frame)`. Today the ignored
  `index` is masked by the global nuke; under H2 it becomes a **silent stale-timeline
  correctness bug** — a wrong frame yields a suffix that starts too late and never recomputes
  the truly-edited region. H2's plan already anticipates this ("honor `index` too — dovetails
  with M2"). Landing order: **M2 first** — it is a two-line, behaviour-preserving change, and
  H2's step 2 then reads the already-honored `idx` for its marker. If one implementer does
  both, apply M2's assignment before layering H2 in the same touch of the function.
- **H1 (uncommitted, same file):** no shared lines (`mark_bundle_changed` is untouched by
  H1); re-anchor by symbol.
- **M9, H6:** flagged as "unrelated despite proximity" in their plans — confirmed, no overlap.

## Effort estimate & risk

- **Effort:** ~20-30 min (two one-line edits + three stub tests).
- **Risk:** Minimal. All live callers pass the current frame or nothing, so the honored path
  is exercised with identical values; only future callers gain new (correct) behaviour.
- **Rollback:** revert the two assignments.
- **Operational footprint:** code-only, no version bump; verify with pytest, optional app
  relaunch (`uv run python src/main.py`) for the manual smoke.
