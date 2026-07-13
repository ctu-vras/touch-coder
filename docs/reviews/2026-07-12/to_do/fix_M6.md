# Fix M6 — Missing `encoding="utf-8"` on builtin `open()` calls

## Problem (re-verified at HEAD)

Builtin `open()` in text mode uses the locale encoding — **cp1252** on this Windows dev/user
base — while pandas reads/writes UTF-8 and the new `atomic_io.atomic_write` (C1, landed
since the review) hard-codes `encoding="utf-8"` (`atomic_io.py:23,39`). So every writer that
went through C1's migration now emits UTF-8, but the remaining builtin-`open` readers decode
cp1252 (and the remaining builtin-`open` writers encode cp1252). Non-ASCII content — Czech
notes ("Dívá se"), diacritics in video names or parameter labels — either mojibakes silently
or raises `UnicodeEncodeError`/`UnicodeDecodeError` mid-load/save.

**Drift since the review:** `sort_frames.py` (review cited `:106,215`) has been **deleted** —
nothing to do there. `data_utils.py:347` already got `encoding="utf-8", errors="ignore"`.
Several sites the review didn't list exist in `labeling_app.py`.

**Complete text-mode `open()` inventory at HEAD** (binary `"rb"`/`"wb"` sites are exempt):

| Site (symbol) | Mode | Verdict |
| --- | --- | --- |
| `data_utils._prepend_header` `:84` / `:96` | r / w | sweep (legacy; only `merge_and_flip_export` calls it — M5 wants that deleted) |
| `data_utils.csv_to_dict` `:626` | r | sweep (legacy per-limb fallback loader) |
| `data_utils.save_dataset` `:661` | w | sweep (legacy writer) |
| `data_utils.save_parameter_to_csv` `:687` / `load_parameter_from_csv` `:697` | w / r | sweep |
| `data_utils.save_limb_parameters` `:712` / `load_limb_parameters` `:726` | w / r | sweep (`:726` is on the active touch load path) |
| `data_utils.extract_zones_from_file` `:856` | r | sweep (active save path) |
| `config_utils.load_config` `:52` | r | sweep (`save_config` already utf-8 via `atomic_write`) |
| `analysis.py:714` (master HTML write) | w | sweep — HTML **declares** `<meta charset="UTF-8">` (`:648`) but is written in cp1252 |
| `labeling_app.load_data` — notes reader (~`:3515`) | r | sweep **+ cp1252 fallback** (user free text; see below) |
| `labeling_app.load_data` — clothes line-count (~`:3533`) | r | sweep |
| `labeling_app._load_clothes_points_from_file` (~`:3591`) | r | sweep |
| `labeling_app.restore_last_position` (~`:3686`) | r | sweep |

> ⚠️ `labeling_app.py` is being edited **concurrently** (H1). Its line numbers above WILL be
> stale — anchor by the symbol names.

Already correct (no change): `data_utils.py:347`, `labeling_app.py:~2850` (utf-8),
`analysis.py:226` + `labeling_app` frame/video copies (binary), all `atomic_write` call sites.

## How it fits the whole app

The unified/export CSVs (pandas + `atomic_write`) are already UTF-8 end-to-end. The builtin
`open()` sites cover the *sidecar* files: notes CSV (legacy read-only now), limb-parameters
CSV, clothes TXT, last-position JSON, `config.json`, and the analysis master HTML. The
cross-codec hazards are (a) UTF-8-written files read as cp1252 → silent mojibake into
research data, (b) cp1252 legacy files read as strict UTF-8 → `UnicodeDecodeError` crash on
load.

## Approaches considered

**A. Sweep `encoding="utf-8"` onto every text-mode site + one targeted cp1252 fallback
(recommended).** Explicit per-call encoding is already the codebase idiom (`atomic_io`,
`:347`, `:2850`). **Chosen.**

**B. `errors="replace"` everywhere.** **Rejected:** silently corrupts research notes with
`�` — invisible data damage violates rule 0; a *logged* fallback decode preserves content.

**C. Process-wide UTF-8 mode (`PYTHONUTF8`/`sys.flags` or `reconfigure`).** **Rejected:**
global blast radius (console, subprocesses, frozen exe launch differences); harder to reason
about than explicit encodings.

## Recommended implementation

1. Add `encoding="utf-8"` to every site in the table (keeping existing `newline=` args).
2. **Notes CSV — the one migration-sensitive site.** Old builds wrote `<video>_notes.csv`
   with builtin `open` (locale) and it holds **user free text**; a strict UTF-8 read of a
   legacy cp1252 file with diacritics raises `UnicodeDecodeError` and aborts `load_data`.
   Move the read into a small pure helper in `data_utils` (testable core, per the module's
   "pure functions where possible" charter), called from `load_data`:

   ```python
   def load_notes_csv(path) -> dict[int, str]:
       """UTF-8 first; legacy files were written in the Windows locale (cp1252)."""
       def _read(enc):
           with open(path, mode="r", newline="", encoding=enc) as fh: ...
       try:
           return _read("utf-8")
       except UnicodeDecodeError:
           print(f"WARN: {path} is not UTF-8; retrying as cp1252 (legacy notes file).")
           return _read("cp1252")
   ```

3. **Analysis HTML:** `open(file_path, "w", encoding="utf-8")` at `analysis.py:714`, and
   `html.escape(name)` for the `<title>`/`<h1>` interpolation (`:650`, `:684` — the
   unescaped-name half of the finding; iframe filenames are constants, safe).

**Why no fallback elsewhere:** clothes TXT (zone-mask filenames, `Dot ID` lines),
limb-parameters CSV (`ON`/`OFF`/ints), legacy per-limb CSVs, and last-position JSON
(`json.dump` default `ensure_ascii=True`) are ASCII-only vocabularies — strict UTF-8 reads
legacy bytes unchanged. `config.json` written by `save_config` is ASCII-escaped; a
hand-edited ANSI file with raw diacritics hits `load_config`'s existing broad `except` →
warning + defaults (behaviour pinned by `tests/test_config_corrupt.py`, which must stay green).

## Export schema impact

**NONE.** The export CSV is written by `pd.to_csv` through `atomic_write` (already UTF-8) and
is untouched; no column or value encoding changes. `tests/test_export_schema.py` stays green.

## Edge cases & failure modes

- Legacy cp1252 notes file → fallback decode + WARN (content preserved exactly).
- File that is valid in *neither* codec — cp1252 maps all 256 byte values except 5 undefined
  ones, so the fallback essentially cannot raise; if it ever does, the exception surfaces
  loudly (no silent catch).
- Mixed-encoding file (partially resaved) → decodes as whichever codec survives; unfixable
  in principle, unchanged from today.
- `_prepend_header` write path re-encodes as UTF-8 — fine, it round-trips its own read.

## Testing / verification plan

**Automatable (pytest, `tmp_path`).** Honest framing: red/green holds on a cp1252-locale
Windows machine (this project's dev environment); on UTF-8 Linux CI the "before" is already
green — state this in the test docstrings.
- `test_M6_config_reads_utf8`: write `config.json` bytes as UTF-8 with
  `"parameter1": "Dívá se"` (unescaped), monkeypatch `config_utils.get_config_path`, assert
  `load_config()["parameter1"] == "Dívá se"`. **Red on Windows** (mojibake), green after.
- `test_M6_notes_utf8_and_cp1252_fallback`: write one notes CSV as UTF-8 and one as cp1252,
  both containing `"Dívá se"`; assert `load_notes_csv` returns the exact string for both
  (fallback proves the migration path). Red (UnicodeDecodeError / mojibake) → green.
- `test_M6_limb_parameters_roundtrip_utf8`: `save_limb_parameters` → raw bytes decode as
  UTF-8 → `load_limb_parameters` round-trip.
- Guards that must stay green: `-k config` (corrupt-config behaviour), `-k schema`, `-k C4`.

**Manual:** type a Czech note, Save, restart, reload — note intact. Run Analysis on a video
whose name has diacritics — browser shows the heading correctly (charset now truthful).

Commands: `uv run pytest tests/ -k M6 -v`, then `uv run pytest tests/ -v` (full guard pass).

## Interactions with other planned fixes

- **M5** deletes `merge_and_flip_export`/`_prepend_header` — if it lands first, skip those
  two sites; do not resurrect them.
- **H1 (CONCURRENT, `labeling_app.py`):** re-anchor the four labeling_app sites by symbol.
- **H3** rewrites the unified/export writers — they already flow through `atomic_write`
  (UTF-8), so no coupling.

## Effort estimate & risk

- **Effort:** ~45-60 min (mechanical sweep + notes helper + escape + 3 tests).
- **Risk:** Low-moderate. The only behaviour change on ASCII data is none at all
  (byte-identical); the decode-crash migration risk is confined to legacy notes files and is
  covered by the fallback. Mojibake risk goes to zero for new files.
- **Rollback:** revert the encoding args + helper.
- **Operational footprint:** code-only, no version bump; verify with pytest + one manual
  note round-trip in `uv run python src/main.py`.
