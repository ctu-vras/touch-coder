# TinyTouch UI Modernization — ttkbootstrap (cosmo, light only)

**Date:** 2026-07-13
**Status:** planned, not started — visual direction approved by Lucas via the throwaway `ui_preview.py` (v2.2: flat buttons + calm palette, antialiased sprite dots, non-overlapping timeline marks, hover-text fix). That file is the reference for the final look; keep it until the migration is done.

**Approved style rules (from the v2 preview):**
- ALL buttons share ONE flat neutral style (white face, thin grey border) — no primary/success/outline mix.
- ONE accent color (`#2780e3`): playhead, selected radio, focused entry. Nothing else is blue.
- Green/red appear ONLY where they carry annotation meaning (param-button state, status dots, timeline cells/marks, diagram dots).
- No yellow anywhere: the "during touch" timeline fill is a pale green so a touch reads as one green block (dark-green onset tick → pale green run → red offset tick).
- Status indicators (Mode/Buffer) are small colored-dot + plain-text chips, not filled labels.

## Context

TinyTouch works, but the UI is a hand-rolled grey Tk look: ~100% plain `tk.*` widgets, ~90+ inline color literals (`'lightgrey'` in ~40 places, `#E57373` as the app-wide OFF red in 10, two ad-hoc font families), no theming abstraction. Lucas wants a clean, modern look. Decisions made with Lucas: **ttkbootstrap** as the widget/theming layer, **one light theme only** (no dark mode). The plan is deliberately phased so each phase leaves the app fully working, and it avoids the areas under active bug-fix churn (threads, save/export logic).

**Theme choice: `cosmo`** — its primary `#2780e3` matches the existing `dodgerblue` playhead, and its success/danger are true green/red, so the annotators' green=ON / red=OFF muscle memory survives.

**Root strategy: keep `LabelingApp(tk.Tk)`** and attach `ttkbootstrap.Style(theme="cosmo")` — do NOT switch to `ttkbootstrap.Window` (its constructor enables HiDPI awareness on Windows, which would change video-render geometry and diagram click coordinates). After `import ttkbootstrap`, plain `ttk` widgets accept `bootstyle=`.

**Pose sliders stay `tk.Scale`** — `_build_pose_scale_slider` (`src/labeling_app.py:521`) and `_build_pose_quality_slider` (`:580`) rely on `resolution=0.01` (data-bearing: ScaleFactor is exported), `troughcolor`, `bg` — none exist on `ttk.Scale`. Only their colors move to the theme module.

## Files

- **NEW `src/theme.py`** — single source of truth: palette constants, fonts, `init_style(root)` (+ `_flat_button()` recipe), `set_button_state(btn, state)`, `StatusChip`, `dot_sprite()`. No imports from app modules (avoids cycles with `config_utils`/`cloth_app`).
- `src/ui_components.py` — main window layout, toolbar, diagram panel
- `src/labeling_app.py` — Style init, state-button sites, timelines, dialogs, slider builders
- `src/config_utils.py` — `load_parameter_names_into` re-applies `bg='lightgrey'` at `:141-145` and `:150-155`
- `src/cloth_app.py` — clothes dialog
- `requirements.txt` + `TinyTouch.spec` — packaging

## `src/theme.py` design

```python
THEME_NAME = "cosmo"
# Surfaces / text
SURFACE = "#f8f9fa"      # replaces 'lightgrey' (~40 sites)
SURFACE_ALT = "#eef0f2"  # replaces 'grey'; also tk.Scale trough color
VIDEO_BG = "#dee2e6"     # replaces '#bcbcbc'
BORDER = "#ced4da"; TEXT = "#212529"; TEXT_MUTED = "#6c757d"
ACCENT = "#2780e3"       # THE one accent: playhead, selection; nothing else is blue
# Semantic state (the ONLY place green/red may appear on widgets)
ON_GREEN = "#9fdca4"     # param button ON (replaces 'lightgreen')
OFF_RED = "#e57373"      # KEEP exact value — annotators know this red
NEUTRAL = "#ffffff"      # unset param button == normal button face (no third look)
# Touch timelines — yellow eliminated
TL_EMPTY = "#e9ecef"; TL_OUTLINE = "#d5d9dd"   # outline was 'black'
TL_ON = ON_GREEN
TL_DURING = "#d9f0db"    # pale green (was 'yellow'); becomes app.color_during
TL_OFF = OFF_RED
TL_ONSET_MARK = "#2f8f57"; TL_OFFSET_MARK = "#c0392b"
PLAYHEAD = ACCENT        # was 'dodgerblue'
# Pose timeline — moved verbatim from labeling_app.py:85-90 + inline literals
POSE_CELL = "#f1f1f1"; POSE_CELL_BORDER = "#d4d4d4"
POSE_TICK_ON = "#2f8f57"; POSE_TICK_OFF = "#c56262"
POSE_BODY_SCALE_COLOR = "#446a8a"; POSE_BODY_SCALE_OVERLAY_COLOR = "#113a5c"
POSE_HEAD_SCALE_COLOR = "#d18a3a"; POSE_HEAD_SCALE_OVERLAY_COLOR = "#8a4413"
POSE_QUALITY_COLOR = "#3a9d5d"
# Diagram dots — semantics unchanged (green=onset, red=offset), refined hues,
# rendered as antialiased PIL sprites (see "Prettier dots" below)
DOT_ONSET = "#21a453"; DOT_OFFSET = "#e2483d"; CLOTH_DOT = DOT_OFFSET
# Status dots (Mode/Buffer chips): ok = TL_ONSET_MARK, bad = TL_OFFSET_MARK,
# reliability-mode warn = "#d39e00" (small dot only — no yellow fills)
# Fonts
FONT_BASE = ("Segoe UI", 10); FONT_SMALL = ("Segoe UI", 9)
FONT_BOLD = ("Segoe UI", 10, "bold"); FONT_TITLE = ("Segoe UI", 11)
```

- `init_style(root)`: creates `ttkbootstrap.Style(theme=THEME_NAME)`, registers the flat button styles via the `_flat_button()` recipe **copied from `ui_preview.py`** (sets `background`/`bordercolor`/`lightcolor`/`darkcolor` + hover map — this is what kills cosmo's default blue button border). **The recipe MUST also map `foreground` for `active`/`pressed`** — cosmo's stock TButton turns text white on hover (its buttons are blue-filled), which makes text invisible on our light buttons (bug found in preview: hovered buttons rendered blank). Sets root bg. Styles: `Tool.TButton` (every normal button), `StateOn/StateOff/StateNeutral.TButton` (param states; Neutral is visually identical to Tool). **Keep the `import ttkbootstrap` inside `init_style`** so importing `labeling_app` stays side-effect-free for headless pytest stubs.
- `set_button_state(btn, state)`: maps `"ON"/"OFF"/None/"None"/""` → the three state styles (must handle the string `"None"` — see review M1).
- `StatusChip` (small ttk.Frame: colored `●` label + text label) with `.set(color, text)` — replaces the old filled `bg=` mode/buffer labels. `mode_label` and `loading_label` become chips; see `status_chip()` in `ui_preview.py` for the approved look.

## Phases (each independently committable + working)

### Phase A — theme.py + mechanical literal swap (visual no-op)
1. Create `src/theme.py` with constants **temporarily set to today's values** (`SURFACE="lightgrey"`, `VIDEO_BG="#bcbcbc"`, `TL_DURING="yellow"`, ...).
2. Replace every color/font literal in `ui_components.py`, `labeling_app.py`, `cloth_app.py`, `config_utils.py` with `theme.*`. Move the 5 `POSE_*` constants (`labeling_app.py:85-90`) into theme.py (import back under the same names).
3. Exit gate: `grep -rn "lightgrey|lightgray|#E57373|lightgreen|dodgerblue|#bcbcbc" src/` hits only `theme.py`. App pixel-identical; pytest green.

### Phase B — Style init + real palette + toolbar
1. `requirements.txt`: **done** — `ttkbootstrap==1.20.4` already added and installed (during the preview work).
2. In `LabelingApp.__init__` right after `super().__init__()`, before `build_ui(self)`: `self.style = theme.init_style(self)`. Flip theme.py constants to the approved v2 values above.
3. Convert `_build_controls` (`ui_components.py:114-195`): frames→`ttk.Frame`, labels→`ttk.Label`, buttons→`ttk.Button` — **every button uses the same `Tool.TButton` style** (no primary/success/outline mix; approved rule). **`takefocus=0` on every button** (see Risks).
4. Migrate dynamic-color sites in the toolbar: Load Video disabled state (`labeling_app.py:3600` — drop `bg`/`fg`, ttk renders disabled itself), buffer `loading_label` (`:1152, :2862, :2866`) and mode label (`:3334-3336`) become `theme.StatusChip`s (dot + text; ok/bad/warn dot colors).

### Phase C — right panel + state buttons (highest-stakes phase)
1. Convert `_build_diagram_panel` (`ui_components.py:198-274`): ttk frames/labels/buttons, sunken-Frame separators → `ttk.Separator`, note entry → `ttk.Entry`. Drop `height=1` from buttons (not a ttk option — TclError). Convert `rebuild_annotation_controls` (`labeling_app.py:634`) incl. limb `ttk.Radiobutton`s with `takefocus=0` (arrow keys must not move radio selection). Sliders stay `tk.Scale`, colors from theme.
2. Migrate all state-button sites to `theme.set_button_state`:
   - `update_button_colors` `labeling_app.py:2996-3004`
   - `parameter_dic_insert` `:3021-3027`
   - `toggle_limb_parameter` `:3059-3065` (handles the `"None"` string)
   - `update_limb_parameter_buttons` `:3080-3088`
   - Clothes button ON `:3598, :3683` (+ its reset site)
   - `config_utils.load_parameter_names_into` `:141-145, :150-155` → `.config(text=...)` only + `set_button_state(btn, None)`
3. `limb_parameter_colors_at_frame` (`:3104-3106`) returns timeline tick colors → `TL_ONSET_MARK`/`TL_OFF` (these are canvas fills, not widget styles).
4. **Prettier dots (antialiased sprites).** Tk `create_oval` has no antialiasing → jagged dots (Lucas flagged this). Replace oval dots with **pre-rendered PIL sprites**: draw the circle 4× oversized on an RGBA image, downscale with LANCZOS, wrap in `ImageTk.PhotoImage`, cache per `(color, radius, hollow)` — helper `theme.dot_sprite()` (reference implementation in `ui_preview.py`; approved look: filled dot with a 2px white halo ring; hollow variant for the "last onset" marker).
   - `_render_diagram_dots` (`labeling_app.py:1482-1496`): both `create_oval` calls → `create_image(x*scale, y*scale, image=theme.dot_sprite(...))`; radius = `dot_size` (config), hollow sprite replaces the `outline='green', fill=''` last-onset marker.
   - `cloth_app.py:84` dot → same sprite helper.
   - The cache must live at module level in theme.py (PhotoImages are garbage-collected if unreferenced). Cache stays tiny (a few color/size combos). `dot_size` changes via Settings → new cache key, old entries harmless.
   - Click hit-testing is unaffected (dots are visual only; clicks map through `diagram_scale` math, not canvas items).

### Phase D — dialogs (7 Toplevels)
Pattern: `win.configure(bg=theme.SURFACE)` + a `ttk.Frame(padding=16)` as sole child + ttk widgets.
1. `custom_confirm_close` (`labeling_app.py:96-121`): FONT_TITLE, both buttons `Tool.TButton` (uniform-button rule applies in dialogs too), shrink 600x300 → ~420x180.
2. `ask_labeling_mode` (`:3311-3345`): keep layout order identical (appears on every video load).
3. Settings (`:3760-3906`): ttk widgets, `round-toggle` Checkbutton, `ttk.Separator` between sections, uniform `padx=8, pady=4`; buttons all `Tool.TButton`.
4. The 3 near-identical progress popups (`:2394-2400, 2439-2445, 2484-2490`): extract one `_open_progress_window(title, heading)` builder (bar `bootstyle="info-striped"` — blue, matches the single accent) — only structural refactor in the plan, removes ~80 duplicated lines.
5. `ClothApp` (`cloth_app.py`): ttk frames, buttons `Tool.TButton`, `SURFACE` bg; dot stays red.

### Phase E — canvas polish + packaging
1. Timeline colors already routed via theme.py in Phase A — confirm all flipped to new values: `draw_timeline` (`:2158`, `get_color` `:2189-2200`, outline `:2207`, playhead `:2248`), `draw_timeline2` (`:2318, :2330-2332, :2345`), `_draw_pose_timeline` (`:1942, :1967-1969, :1985, :2108`).
   Approved drawing refinements (reference: `draw_mock_timelines` in `ui_preview.py`):
   - **No overlapping marks**: in the scrub bar, the interval fill is inset ~3px from the onset/offset tick lines so ticks never sit on top of the fill.
   - **Single-line grid**: zoomed-timeline cells are drawn as borderless fills with the grid (outer box + one line per cell boundary) drawn once on top — no doubled shared edges.
   - **Inset edges**: all timeline drawing insets 1-2px from canvas edges so the right/bottom border isn't clipped.
   - **Playhead cap**: playhead is a 2px line with a small triangle cap at the top.
2. Spacing pass: consistent padx/pady in toolbar and right panel; `highlightthickness=0` on the 4 canvases; optional `app.minsize(1100, 800)`.
3. `TinyTouch.spec`: after the `datas` list (line 13) add:
   ```python
   from PyInstaller.utils.hooks import collect_data_files
   datas += collect_data_files("ttkbootstrap")   # localization .msg files
   ```
4. Build `pyinstaller TinyTouch.spec`, smoke-test `dist/TinyTouch.exe`.

## Out of scope (follow-up plan, per Lucas)

**Diagram panel flexibility.** Known issue: on smaller displays the fixed-size diagram canvas (450×696 × `diagram_scale`, packed first/top in `_build_diagram_panel`) squeezes out the note entry and Save Note/Select Frame buttons (packed last, `side="bottom"` — pack clips last-packed widgets first). Lucas decided this gets its **own plan after the restyle** is done and the looks are approved. Likely shape of that plan: pack-priority reorder + startup auto-fit of the effective diagram scale to screen height (the click→data coordinate conversion at `labeling_app.py:1374-1380` and the runtime rescale path at `:3938-3940` already support arbitrary scales). During Phase C of THIS plan: preserve the existing pack order/behavior exactly — do not make the clipping worse, do not fix it either.

## Do NOT touch
Frame render path (`render_frame`/PhotoImage/`img_buffer`), `background_update`/`background_update_play` threads, save/export in `data_utils.py`/`pose_mismatch_data.py`, PIL pose overlay (`labeling_app.py:417-434`), diagram dot semantics (green onset / red offset), `_bind_navigation`/`global_click` binding structure.

## Risks & mitigations
- **R1 Space/arrows on focused ttk widgets**: root binds `<space>`→play-toggle and arrows→frame nav (`ui_components.py:294+`). A focused `ttk.Button` also fires on Space; `ttk.Radiobutton` moves selection on arrows. Mitigation: `takefocus=0` on every converted button/radiobutton.
- **R2 Leftover `bg=`/`fg=`/`height=` on a ttk widget** → TclError at the moment that code path runs, not at startup. Exit gate for B/C: `grep -n "config(bg=|configure(bg=" src/` empty; exercise every toggle.
- **R3 `color == self.color_during` equality logic** (`labeling_app.py:2223-2225`) propagates touch state across zones. Keep exactly ONE source for the "during" color: `app.color_during` set from `theme.TL_DURING` at `ui_components.py:49`.
- **R4 Frozen build**: `collect_data_files("ttkbootstrap")` covers the `.msg` localization files; verify with the Phase E build.
- **R5 Conflict with ongoing bug-fix work**: Phase A is a wide mechanical diff over `labeling_app.py` — land as one isolated commit; later phases touch narrow regions.
- **R6 Headless pytest**: `ttkbootstrap` import stays inside `theme.init_style()`.
- The 12×12 color-swatch legend frames in slider headers (`labeling_app.py:536, 590`) stay `tk.Frame` (bg IS the legend).
- **R7 TL_DURING vs TL_EMPTY contrast**: the pale-green "during" fill (`#d9f0db`) sits close to the empty-cell grey (`#e9ecef`). Looked fine in the approved preview, but verify on the annotators' actual monitors during Phase E; if too subtle, deepen TL_DURING only — never re-introduce a separate hue family.

## Verification (per phase)
- `uv run python -m pytest tests/` after every phase.
- Launch app (`uv run python src/main.py`), load a video in **touch mode**: toggle all 3 global + 3 limb parameter buttons through None→ON→OFF→None (button color + timeline ticks), switch limbs, click onset/offset dots on diagram, type a note (keys must not leak — C2 fix interplay), Play/Stop + Space (exactly one toggle per press), scrub both timelines.
- **Pose mode**: drag body/head/quality sliders (0.01 stepping preserved), verify ScaleFactor round-trips save→reload.
- Dialogs: close-confirm both paths, mode dialog persists to config.json, Settings validation error path, clothes add/remove/save, 3 progress popups on fresh video load.
- Phase E: frozen exe smoke test (load + annotate + save in both modes).
