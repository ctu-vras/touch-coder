# Fix H4 — Corrupt `config.json` crashes startup because only `load_config` is guarded

## Problem (re-verified at HEAD)

Every config reader in `src/config_utils.py` opens the file and calls `json.load()`
**bare**, except `load_config`, which wraps it and falls back to `{}`:

```python
def load_config():
    try:
        config_path = _ensure_config_file()
        with open(config_path, 'r') as file:
            return json.load(file)
    except Exception:
        return {}
```

The unguarded loaders (re-verified by symbol name at HEAD):

| Symbol | Line | Called from |
| --- | --- | --- |
| `load_config_flags` | `config_utils.py:49` | `LabelingApp.__init__` (`labeling_app.py:164`) |
| `load_perf_config` | `config_utils.py:58` | `LabelingApp.__init__` (`labeling_app.py:167`) |
| `load_display_limits` | `config_utils.py:68` | **defined but never called** (dead) |
| `load_video_downscale` | `config_utils.py:87` | `LabelingApp.__init__` (`labeling_app.py:174`) |
| `load_jump_seconds` | `config_utils.py:101` | `LabelingApp.__init__` (`labeling_app.py:176`) |
| `load_realtime_arrow_hold` | `config_utils.py:115` | `LabelingApp.__init__` (`labeling_app.py:183`) |
| `load_parameter_names_into` | `config_utils.py:122` | `load_new_data` (`labeling_app.py:3439`), `apply_runtime_settings` (`labeling_app.py:3861`) |

Each of these does the same unguarded pattern, e.g. `load_config_flags`:

```python
def load_config_flags():
    config_path = _ensure_config_file()
    with open(config_path, 'r') as file:
        config = json.load(file)          # <-- raises JSONDecodeError on a corrupt file
        ...
```

**Crash path.** `LabelingApp.__init__` calls `load_config_flags()` at
`labeling_app.py:164` — the *first* config read after `build_ui`. If `config.json`
is truncated/corrupt, `json.load` raises `json.JSONDecodeError`, which propagates
out of `__init__` **before `mainloop` is ever reached**. The app dies on launch with
a traceback and no recovery — the user cannot even open Settings to fix it.

**Where the corruption comes from.** `_ensure_config_file` (`config_utils.py:18`)
only guarantees the file *exists* (it copies the bundled default in when missing); it
does **not** validate contents, so an existing-but-corrupt file is handed straight to
`json.load`. The corruption itself is produced by non-atomic writes — `save_config`
(`config_utils.py:43`) does `json.dump(config, file)` directly onto the live file, so
a crash / disk-full / kill mid-write leaves a half-written `config.json`. That write
side is finding **C1** (atomic writes); H4 is the **read-side defense** that keeps the
app launchable when a corrupt file nonetheless exists (C1 miss, external edit, OS
crash). The two are complementary and independent.

## How it fits the whole app

- **All config reads flow through `config_utils`.** There is exactly one already-correct
  pattern in this module — `load_config`'s try/except-and-default — and six live loaders
  (plus one dead one) that don't follow it. The fix is to make the module internally
  consistent.
- **Callers pass through unchanged.** `__init__` unpacks tuples
  (`self.NEW_TEMPLATE, self.minimal_touch_length = load_config_flags()`), `apply_runtime_settings`
  already reads its values from a `cfg` dict it was handed (not from these loaders), and
  `open_settings`/`ask_labeling_mode` already use the guarded `load_config()`
  (`labeling_app.py:3242`, `3680`). So hardening the loaders requires **zero call-site
  changes** as long as each loader keeps its current return shape and default values.
- **Sane defaults already exist inline** in every loader as the second argument to
  `config.get(key, default)` — the fix only needs to guarantee those defaults are
  returned when parsing fails, instead of the exception escaping.
- **Observability (CLAUDE.md §0).** `load_config` currently swallows the error
  *silently* (`except Exception: return {}`). The finding asks to "catch, **warn**,
  fall back". Since every loader will now depend on `load_config`, this is the single
  place to add one `WARNING:` log so a corrupt config is visible in the console rather
  than invisibly degrading to defaults.

## Approaches considered

**A. Per-loader try/except (mirror `load_config` in each loader individually).**
Wrap each loader's `open`/`json.load` in its own `try/except` returning its own
hardcoded defaults. Correct, but duplicates the try/except + a *second copy* of every
default (once in `.get(key, default)`, once in the except branch) across seven
functions — two places to keep in sync, exactly the drift risk that produced this
finding. **Rejected** (fixes the symptom, entrenches the duplication).

**B. Funnel every loader through the hardened `load_config()` + a central defaults dict (chosen — structural).**
`load_config()` already returns `{}` on *any* failure and never raises. Replace each
loader's bare `_ensure_config_file()` + `open` + `json.load` with `config = load_config()`,
then read keys as `config.get(key, CONFIG_DEFAULTS[key])` (keeping each loader's existing
value coercion/clamping). A corrupt file now yields `{}` from the one hardened reader, so
every `.get` falls to its default automatically. Add the `WARNING:` log inside
`load_config`'s except branch (see §Observability). Benefits: **no bare `json.load`
remains anywhere**; error handling lives in exactly one place; defaults become a single
source of truth (`CONFIG_DEFAULTS`); no caller changes. **Chosen.**

**C. Typed `Config` dataclass loaded once and cached.** A `Config` object with typed
fields + defaults, parsed once at startup and passed around. Cleanest long-term model and
would make "parse once" literal, but it rewrites every loader's signature and every call
site (tuple-returning functions → attribute access), a much larger blast radius than this
finding warrants for a single-file desktop tool. **Rejected** as over-engineering for the
scope; can be revisited if config grows.

## Recommended implementation

In `src/config_utils.py`:

1. **Add a `WARNING:` log to the existing `load_config` guard** (observability; the one
   place all reads now funnel through):

   ```python
   def load_config():
       try:
           config_path = _ensure_config_file()
           with open(config_path, 'r') as file:
               return json.load(file)
       except Exception as e:
           print(f"WARNING: config.json unreadable ({e}); using defaults")
           return {}
   ```

2. **Introduce one defaults dict** as the single source of truth:

   ```python
   CONFIG_DEFAULTS = {
       'new_template': False,
       'minimal_touch_length': '280',
       'perf_enabled': False, 'perf_log_every_s': 2.0, 'perf_log_top_n': 6,
       'max_display_width': 0, 'max_display_height': 0,
       'video_downscale': 1.0,
       'jump_seconds': 1.0,
       'realtime_arrow_hold': True,
       'parameter1': 'Parameter 1', 'parameter2': 'Parameter 2', 'parameter3': 'Parameter 3',
       'limb_parameter1': 'Limb Parameter 1', 'limb_parameter2': 'Limb Parameter 2',
       'limb_parameter3': 'Limb Parameter 3',
   }
   ```

3. **Rewrite each loader to source its dict from `load_config()`** instead of a bare
   open. The body/coercion is otherwise untouched. Example for `load_config_flags`:

   ```python
   def load_config_flags():
       config = load_config()   # never raises; {} on corrupt file
       NEW_TEMPLATE = config.get('new_template', CONFIG_DEFAULTS['new_template'])
       minimal_touch_length = config.get('minimal_touch_length', CONFIG_DEFAULTS['minimal_touch_length'])
       return NEW_TEMPLATE, minimal_touch_length
   ```

   Apply the identical transformation to `load_perf_config`, `load_display_limits`,
   `load_video_downscale`, `load_jump_seconds`, `load_realtime_arrow_hold`, and
   `load_parameter_names_into` (the last keeps its `video_obj` / button mutation logic
   verbatim — only its `_ensure_config_file`/`open`/`json.load` prologue changes to
   `config = load_config()`). After this, **no `json.load` outside `load_config`
   remains** in the module.

**Behavioural contract after the change:**
- Valid `config.json`: byte-identical behaviour to today (same values, same coercion).
- Corrupt/truncated/missing-key `config.json`: every loader returns its documented
  default; the app launches normally; one `WARNING:` line is printed.

**Export schema impact: `none`** (config-only change; touches no exporter, no CSV column,
no `data_utils` / `pose_mismatch_data` code). Independently guaranteed by
`tests/test_export_schema.py`, which must stay green.

## Edge cases & failure modes

- **Empty file (`""`)** → `json.load` raises → `{}` → defaults. Safe.
- **Valid JSON but not an object** (e.g. `[]` or `"x"`) → `load_config` returns it as-is;
  `[].get` / `"x".get` would raise `AttributeError` inside a loader. To be fully robust,
  `load_config` should coerce non-dict results to `{}` (`return data if isinstance(data, dict) else {}`).
  Include this in the guard so the funnel is airtight.
- **Missing individual key** (partial but valid JSON) → `.get(key, CONFIG_DEFAULTS[key])`
  returns the default; no crash. Already handled today for the keys read via `.get`, now
  uniform.
- **Corrupt file mid-session** (Settings "Apply" re-reads): `apply_runtime_settings`
  reads from the `new_cfg` dict it built, not from disk, so an on-disk corruption between
  saves cannot crash Apply; `load_parameter_names_into` (called from Apply) is now guarded.
- **`_ensure_config_file` copy failure** (returns bundled path or original path): already
  caught by the surrounding `try/except` in `load_config`. Safe.

## Testing / verification plan

**(a) Automatable — red/green (pytest against `config_utils`, `tmp_path`).**
New file `tests/test_config_corrupt.py`, functions named `test_H4_*` so `-k H4` selects
them. Each test writes a **corrupt** `config.json` into `tmp_path`, monkeypatches
`config_utils.get_config_path` to return it (so `_ensure_config_file` sees an existing
file and does not overwrite it), then calls each loader and asserts it returns the
documented default instead of raising.

```python
import types, pytest, config_utils
from config_utils import (
    load_config_flags, load_perf_config, load_display_limits,
    load_video_downscale, load_jump_seconds, load_realtime_arrow_hold,
    load_parameter_names_into,
)

@pytest.fixture
def corrupt_config(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text('{"new_template": true, "jump_sec')   # truncated -> JSONDecodeError
    monkeypatch.setattr(config_utils, "get_config_path", lambda: str(p))
    return p

def test_H4_flags_fall_back(corrupt_config):
    assert load_config_flags() == (False, '280')

def test_H4_perf_falls_back(corrupt_config):
    assert load_perf_config() == (False, 2.0, 6)

def test_H4_downscale_falls_back(corrupt_config):
    assert load_video_downscale() == 1.0

def test_H4_jump_seconds_falls_back(corrupt_config):
    assert load_jump_seconds() == 1.0

def test_H4_arrow_hold_falls_back(corrupt_config):
    assert load_realtime_arrow_hold() is True

def test_H4_display_limits_falls_back(corrupt_config):
    assert load_display_limits() == (None, None)

def test_H4_parameter_names_fall_back(corrupt_config):
    vid = types.SimpleNamespace()
    load_parameter_names_into(vid, {}, {})   # empty button dicts -> .get(n) is None -> skipped
    assert vid.parameter1_name == 'Parameter 1'
    assert vid.limb_parameter1_name == 'Limb Parameter 1'
```

Commands and expected transition:
```
uv run pytest tests/ -k H4 -v        # RED today, GREEN after
uv run pytest tests/ -k schema -v    # stays GREEN (proves zero export-schema impact)
```
- **RED before:** every `test_H4_*` errors with `json.decoder.JSONDecodeError` (the bare
  `json.load` in each loader).
- **GREEN after:** all pass; loaders return defaults; one `WARNING:` printed per call.

**(b) Manual — real launch with a deliberately corrupted config.**
1. Back up the real file, then corrupt it:
   `cp config.json config.json.bak && printf '{"new_template": tr' > config.json`
2. Launch: `uv run python src/main.py`.
3. Confirm the app **window opens** (does not crash), the console shows the
   `WARNING: config.json unreadable ...` line followed by the normal
   `INFO: Loaded new template: False` / `INFO: Video downscale: 1.0` / etc. startup logs
   (proving defaults were applied).
4. Restore: `mv config.json.bak config.json`.

## Interactions with other planned fixes

- **C1 (atomic writes) — root cause / complementary.** C1 makes `save_config` (and the
  CSV writers) write-to-temp-then-`os.replace`, which prevents the half-written
  `config.json` in the first place. H4 is the independent read-side guarantee that a
  corrupt file (from any source, not just an interrupted save) can't brick startup. Land
  them in either order; neither blocks the other. H4 explicitly does **not** touch
  `save_config`'s non-atomicity — that stays with C1.
- **C4 (silent row-drop), H5 (extraction), M-series** — no shared code; independent.
- Scope guard: do **not** widen H4 into a config-write or schema change; it is a
  read-path hardening of `config_utils` only.

## Effort estimate & risk

- **Effort:** ~20–30 min (one dict + a mechanical rewrite of seven small loaders + one
  new test file).
- **Risk:** Low. The valid-config path is behaviour-identical (same keys, same defaults,
  same coercion); only the previously-crashing corrupt-file path changes — from a hard
  crash to graceful defaults. All reads now share the single tested `load_config` guard.
- **Rollback:** revert `config_utils.py` (single file) and delete the new test.
- **Operational footprint:** code-only. **No version bump.** Verify with
  `uv run pytest tests/ -k H4` and a relaunch of `uv run python src/main.py` against a
  deliberately corrupted config.
