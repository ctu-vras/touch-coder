# Fix H6 — `LimbView` model layer: kill create-on-read + dead `UserDict` backing store + rebind boilerplate

## Problem (re-verified at HEAD)

`video_model.LimbView` (`video_model.py:8-31`) is a `UserDict` subclass that wraps the shared
`Video.frames` dict so legacy per-limb code can index a single limb. Re-verified by symbol at
HEAD — the code is exactly as the finding describes:

```python
class LimbView(UserDict):
    def __init__(self, frames, limb):
        super().__init__()
        self._frames = frames
        self._limb = limb

    def __getitem__(self, frame):
        b = self._frames.setdefault(frame, empty_bundle())   # <-- create-on-read
        return b[self._limb]

    def __setitem__(self, frame, rec):
        b = self._frames.setdefault(frame, empty_bundle())
        b[self._limb] = rec

    def get(self, frame, default=None):                       # non-mutating (correct)
        b = self._frames.get(frame)
        return (b[self._limb] if b and self._limb in b else default)

    def setdefault(self, frame, rec):                         # dead + logically broken
        if frame not in self._frames:
            self._frames[frame] = empty_bundle()
        if not self._frames[frame][self._limb]:               # FrameRecord dict is always truthy
            self._frames[frame][self._limb] = rec
        return self._frames[frame][self._limb]
```

Four distinct defects, all present:

1. **Create-on-read.** `__getitem__` does `self._frames.setdefault(frame, empty_bundle())`, so
   reading `video.dataRH[f]` for an unlabeled frame **permanently inserts an empty bundle** into
   `frames` (inflates `len(frames)`, allocates a fresh `empty_bundle()` every call).
2. **Dead `UserDict` backing store.** Only `__getitem__/__setitem__/get/setdefault` are overridden.
   Inherited `__len__/__iter__/__contains__/keys/values/items` operate on the unused, always-empty
   `self.data`, so `len(view)` is always `0`, `f in view` is always `False`, and `for f in view`
   yields nothing — regardless of real content.
3. **Broken `setdefault`.** `if not self._frames[frame][self._limb]:` tests a `FrameRecord` dict
   that is always truthy (it always has keys), so the default is never applied. (Also **dead** — no
   caller invokes `.setdefault` on a view; see below.)
4. **Rebind boilerplate.** Because `_frames` captures the dict object by reference, every
   reassignment of `video.frames` must be followed by manually re-pointing all four views. This is
   done in two places — `labeling_app.py:3369-3372` and `:3394-3397` (8 lines). Any future load
   path that reassigns `video.frames` and forgets this **silently detaches the views** from the
   live data.

**Important nuance (do not overstate the live impact).** A full grep of `src/` shows that **no
call site currently reads a view with `[]`** — every read goes through `.get()`, and
`find_last_green` reads `video.frames` directly and ignores its argument. Writes use `view[f] = rec`
(`__setitem__`, which legitimately creates the bundle). Therefore `__getitem__`'s create-on-read is
**never triggered in the running app today**, and the dead `__len__/__iter__/__contains__` are never
consulted on a view. This finding is a **latent-footgun / dead-code / maintainability** fix, not a
live data-corruption fix. Framing it honestly matters for the "Export schema impact" line below.

## How it fits the whole app

Complete catalogue of every access through the four limb views (`dataRH/dataLH/dataRL/dataLL`),
verified by grep:

| Site (`labeling_app.py`) | What it does | Access kind |
| --- | --- | --- |
| `_render_diagram_dots` (1449-1459) | `data = self.video.dataRH` (etc.); `data.get(current_frame, {})` | **read via `.get()`** |
| `find_last_green` (1662) | receives the view as `_unused_data`, **ignores it**, walks `self.video.frames` directly | none (arg dead) |
| `draw_timeline` / timeline-1 (2161-2184) | `data_source = {'RH': self.video.dataRH, ...}`; `data = data_source.get(limb, self.video.data)`; `get_color` does `data.get(frame_idx, {})` | **read via `.get()`** |
| remove-dot handler (1366-1403) | `target_data = getattr(self.video, f"data{option}", {})`; `target_data.get(current_frame)`; on clear `target_data[current_frame] = {...}` | read `.get()` + **write `[]=`** |
| add-dot / click handler (1558-1576) | same `target_data`; `target_data.get(current_frame)`; new record `target_data[current_frame] = rec` | read `.get()` + **write `[]=`** |

Other observations that constrain the fix:
- **No `[]` reads on views** anywhere (only the two write-side `[]=`).
- **No `len(view)` / `f in view` / `for f in view` / `.keys()/.values()/.items()`** on any view —
  all such calls target `self.video.frames` directly. So fixing the dead backing store changes no
  current behaviour; it only removes a trap.
- **No `.setdefault` on a view** (`.setdefault` is only called on `self.video.frames` and on inner
  `rec`). `LimbView.setdefault` is dead.
- **Only construction site** is `video_model.py:48-51`; no `isinstance(..., UserDict)` or other
  reliance on the `UserDict` base anywhere in `src/`.
- **`video.frames` reassignments** live in the load path: `labeling_app.py:3354, 3357, 3366`
  (initial load / empty reset) and `3383, 3391` (export-recovery / its failure reset). The two
  rebind blocks (3369-3372, 3394-3397) exist solely to re-couple the views after those.

**Save/export path (the money check).**
- `export_from_unified` (`data_utils.py:518-519`) iterates `for f in range(total_frames + 1)` and
  does `b = frames.get(f, empty_bundle())` — it emits **one row for every frame `0..total_frames`
  regardless of what is (or isn't) in `frames`**. Leaked empties neither add nor remove rows.
- `save_unified_dataset` with the default `changed_only=True` (`data_utils.py:175-179`) skips any
  bundle without a truthy `"Changed"` flag. A create-on-read empty bundle has no `"Changed"` flag,
  so it is **not serialized** even if it did leak. (Full-write mode also iterates `0..total` and
  fabricates empties for gaps, so it is likewise row-set-invariant.)

## Approaches considered

**A. Thin non-`UserDict` accessor; lazy `_frames` resolved from the owning `Video`; `.get()` read
semantics; non-mutating `__getitem__`. (Chosen.)**
Drop the `UserDict` base. `LimbView` holds a back-reference to its `Video` and resolves the frames
dict lazily via a property, so reassigning `video.frames` is automatically reflected — the 8 rebind
lines are deleted. `__getitem__` reads without inserting (raises `KeyError` on a truly-missing
frame, standard mapping semantics); `__setitem__` keeps creating the bundle (the real, used write
path); `get()` stays non-mutating; `setdefault` is removed (dead). `__len__/__iter__/__contains__/
keys/values/items` delegate to the live frames dict so they reflect real content.
- *Pros:* zero churn at the five usage sites (they use `.get()` and `[]=`, both preserved);
  removes the rebind footgun entirely; smallest, most local change; keeps the ergonomic per-limb
  API. *Cons:* constructor signature changes to `LimbView(video, limb)` — but the only caller is
  `Video.__init__`.

**B. Drop `LimbView` entirely; callers use `video.frames[f][limb]` / `.get()`.**
Rewrite all five sites (render, timeline `data_source`, both click handlers) to index `frames`
directly and pass `limb` through helpers (`get_color`, `find_last_green`). *Rejected:* removes an
abstraction that reads cleanly at the call sites, and touches ~5 handlers plus a helper signature —
more churn and more regression surface than A, for no behavioural gain (all reads are already
non-mutating `.get()`).

**C. Expose limbs as methods (`video.limb("RH").get(f)`).**
*Rejected:* churns every call site and offers nothing over A.

**D. Keep constructor, avoid rebinds by mutating `frames` in place** (`frames.clear(); frames.update(...)`
instead of `video.frames = ...`). *Rejected:* converts 5 reassignment sites to in-place ops and
stays fragile — any future `frames = ...` silently reintroduces the detach bug. A's lazy resolution
is structurally immune.

**Confirmation that nothing depends on create-on-read side effects:** verified above — the only
mutating access through views is `[]=` (a deliberate write), which approach A preserves. No code
relies on a bare read inserting a bundle.

## Recommended implementation

In `video_model.py`, replace the `LimbView` class (currently `:6-31`, including the `UserDict`
import at `:6`) with a plain accessor bound to the owning `Video`:

```python
from typing import Dict, Iterator
from data_utils import empty_bundle, FrameBundle   # (empty_bundle no longer needed here if unused)

class LimbView:
    """Read/write view onto a single limb ('RH'/'LH'/'RL'/'LL') across the owning
    Video's live `frames` dict. Reads never mutate; writes create the bundle on demand.
    `frames` is resolved lazily from the owner, so reassigning `video.frames` needs no rebind."""
    def __init__(self, video, limb: str):
        self._video = video
        self._limb = limb

    @property
    def _frames(self) -> Dict[int, FrameBundle]:
        return self._video.frames

    # --- reads: never insert ---
    def __getitem__(self, frame: int):
        return self._frames[frame][self._limb]          # KeyError if frame absent; no mutation

    def get(self, frame, default=None):
        b = self._frames.get(frame)
        return (b[self._limb] if b and self._limb in b else default)

    def __contains__(self, frame) -> bool:
        return frame in self._frames

    def __len__(self) -> int:
        return len(self._frames)

    def __iter__(self) -> Iterator[int]:
        return iter(self._frames)

    def keys(self):   return self._frames.keys()
    def values(self): return (b[self._limb] for b in self._frames.values() if self._limb in b)
    def items(self):  return ((f, b[self._limb]) for f, b in self._frames.items() if self._limb in b)

    # --- write: creating the bundle here is intended ---
    def __setitem__(self, frame: int, rec):
        b = self._frames.setdefault(frame, empty_bundle())
        b[self._limb] = rec
```

Then in `Video.__init__` (`video_model.py:48-51`) pass `self` instead of the dict:

```python
self.dataRH = LimbView(self, "RH")
self.dataLH = LimbView(self, "LH")
self.dataRL = LimbView(self, "RL")
self.dataLL = LimbView(self, "LL")
```

And **delete both rebind blocks** in `labeling_app.py` — `:3369-3372` and `:3394-3397` (plus the
`# Rebind LimbViews...` comments) — since the views now follow `video.frames` automatically.

**Behavioural contract after the change:**
- `_render_diagram_dots`, `draw_timeline`, both click handlers: **identical** behaviour (they call
  only `.get()` and `[]=`, both unchanged in effect).
- `view[f]` on a missing frame now raises `KeyError` instead of silently inserting an empty bundle —
  no current caller does this, so it is a hardening-only change.
- `len(view)` / `f in view` / iteration now reflect the real `frames` content instead of `0`/`False`/
  empty.
- `video.frames = ...` in any load path no longer needs a rebind.

**Export schema impact: NONE.** `export_from_unified` writes one row for every frame `0..total_frames`
via `frames.get(f, empty_bundle())` regardless of dict contents, and `save_unified_dataset` gates on
the `"Changed"` flag — so the set of serialized/exported rows is invariant under this change.
(Create-on-read never wrote to disk in the first place: leaked empties carry no `"Changed"` flag and
were skipped by the incremental save; and it was never even triggered, since no caller reads a view
with `[]`.) Guaranteed unchanged by `tests/test_export_schema.py`.

## Edge cases & failure modes

- **Frame present but limb empty:** `empty_record` shape is present, so `view[f]` / `view.get(f)`
  return the (empty) `FrameRecord` dict without mutating — same as today via `.get`.
- **`values()`/`items()` over frames missing a limb key:** guarded with `if self._limb in b`; a
  well-formed `empty_bundle()` always has all four limb keys, so in practice every frame yields.
- **Pose mode:** pose frames are pose bundles (no `LH/RH/RL/LL`), but pose mode never touches the
  limb views (touch-only handlers). `values()/items()`'s `if self._limb in b` guard makes them
  no-op-safe even if a pose bundle appeared. Reads via `[]` would `KeyError`, but no pose code path
  reads a limb view.
- **`self._video.frames` is `None`/not a dict:** only ever a dict (`Video.__init__` sets `{}`; load
  paths coerce with `or {}`). Property just forwards; no extra guard needed (matches current code,
  which assumed a dict).

## Testing / verification plan

**(a) Automatable red/green — `tests/test_limbview_h6.py`** (new; black-box against `video_model`,
no cv2/Tk). To be **signature-agnostic** so the same file is RED at HEAD and GREEN after, construct
the view with a `dict` subclass that also exposes itself as `.frames`:

```python
from video_model import LimbView
from data_utils import empty_bundle

class _Owner(dict):
    """Doubles as the frames dict (HEAD's ctor) AND as the Video owner (fixed ctor)."""
    @property
    def frames(self):
        return self

def _labeled_frame(limb):
    b = empty_bundle(); b[limb]["Onset"] = "ON"; b["Changed"] = True
    return b

def test_H6_read_does_not_insert():
    owner = _Owner({2: _labeled_frame("RH")})
    view = LimbView(owner, "RH")
    before = len(owner)
    _ = view.get(999)                 # non-mutating read of an unlabeled frame
    assert len(owner) == before        # RED at HEAD only if .get inserted (it doesn't) ...
    try:
        _ = view[999]                  # HEAD: setdefault inserts -> len grows; FIXED: KeyError
    except KeyError:
        pass
    assert len(owner) == before        # RED at HEAD: create-on-read left an empty bundle behind

def test_H6_len_in_iter_reflect_content():
    owner = _Owner({2: _labeled_frame("RH"), 5: _labeled_frame("RH")})
    view = LimbView(owner, "RH")
    assert len(view) == 2              # RED at HEAD: UserDict.__len__ -> 0
    assert 2 in view and 999 not in view   # RED at HEAD: __contains__ -> always False
    assert sorted(view) == [2, 5]      # RED at HEAD: __iter__ -> empty

def test_H6_write_creates_bundle_and_sets_only_that_limb():
    owner = _Owner()
    view = LimbView(owner, "LH")
    view[3] = {"X": [1], "Y": [2], "Onset": "ON", "Bodypart": "LH",
               "Look": "No", "Zones": [["FACE"]], "Touch": None}
    assert 3 in owner and owner[3]["LH"]["Onset"] == "ON"
    assert owner[3]["RH"] == empty_bundle()["RH"]   # other limbs untouched
```

Commands + expected:
```
uv run pytest tests/ -k H6 -v
```
- **RED at HEAD:** `test_H6_read_does_not_insert` fails (create-on-read grows `len(owner)`);
  `test_H6_len_in_iter_reflect_content` fails (`len(view)==0`, `in`/iteration empty via dead
  `self.data`). (`test_H6_write_...` already passes at HEAD — the write path is correct.)
- **GREEN after fix:** all pass.

**Schema guard (must stay green):**
```
uv run pytest tests/ -k schema -v
```
Both touch + pose schema-lock tests unchanged (proves no format drift).

**(b) Manual (`uv run python src/main.py`):** load a video, navigate across many frames **without
labeling anything**, then Save. Confirm the saved `data/<video>_unified.csv` contains **only
truly-labeled frames** (row count == number of frames you actually annotated), i.e. mere navigation
never materializes empty rows. (This is already the case today because reads use `.get()`; the check
is a regression guard confirming the refactor didn't introduce a new create-on-read.)

## Interactions with other planned fixes

- **H3 ("incremental" saves are full re-read + rewrite):** operates in the persistence layer
  (`data_utils` / `pose_mismatch_data` writers). H6 is purely the in-memory view accessor in
  `video_model.py`. H6 does not change **what** is serialized (still gated by `"Changed"`, still one
  export row per frame `0..total`); it only guarantees `frames` won't accumulate empties if future
  code adds a `[]` read. No overlap, either order.
- **C1 (atomic writes) / C4 (pose save discards prior rows):** both live in the file-writer layer
  (`data_utils.py`, `pose_mismatch_data.py`, config/metadata writers). H6 touches no writer and no
  file. Fully independent; no shared lines.
- **M2 (`mark_bundle_changed` ignores its arg):** tangential — both concern the `frames` bundle
  lifecycle, but M2 is a separate `labeling_app` bug. Not required by, and does not block, H6.

## Effort estimate & risk

- **Effort:** ~20-30 min — rewrite one small class, change 4 constructor lines, delete 8 rebind
  lines, add one test file; run two pytest selectors.
- **Risk:** Low. The five live usage sites use only `.get()` and `[]=`, both preserved with
  identical effect; create-on-read is currently unreachable, so removing it changes no runtime
  behaviour. The one behavioural change (`view[missing]` → `KeyError` instead of insert) has no
  caller. Main regression surface is the rebind-deletion: mitigated because the lazy property makes
  views structurally track `video.frames`.
- **Rollback:** revert `video_model.py` and re-add the two rebind blocks in `labeling_app.py`.
- **No-backwards-compat / reset:** **No on-disk effect** — change is in-memory only; unified/export
  formats and row sets are unchanged. **No data reset required.**
- **Operational footprint:** code-only. **No version bump.** Verify with `uv run pytest tests/`;
  relaunch `uv run python src/main.py` for the manual navigation-without-labeling check.
