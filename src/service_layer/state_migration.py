"""
service_layer/state_migration.py
One-time import of a project's legacy working-state FILES into its SQLite
state database (`state/<video>.db`).

This is the ONLY place that still writes state derived from the old files, and
it runs at most once per project:

    state/<video>.db exists  ->  nothing to do (open it and go)
    state/<video>.db missing  ->  read every legacy source with the EXISTING
                                  readers, write them into a fresh DB in ONE
                                  transaction, then rename each consumed
                                  source to `<name>.migrated`

Recovery ladder (unchanged from `project_service.load_frames_dataset`, which
this module reuses verbatim rather than re-implementing):

    1. state/<video>_unified.csv        load_unified_dataset
    2. export/<video>_export.csv        import_unified_from_export  (tier 1 empty)
    3. state/<video>{RH,LH,RL,LL}.csv   csv_to_dict merge           (tiers 1+2 empty)

plus the sidecars, each with its own reader:

    state/<video>_notes.csv             load_notes_csv        -> legacy_notes
    state/<video>_limb_parameters.csv   load_limb_parameters  -> legacy_limb_params
    state/<video>_clothes.txt           parse_clothes_file    -> clothes_dots
    state/<video>_last_position.json    read_last_position    -> meta.last_frame
    state/<video>_metadata.json         load_labeling_time_seconds
                                                              -> meta.labeling_time_seconds

Why the two `legacy_*` tables instead of merging
------------------------------------------------
The notes CSV was only ever loaded into `video.notes` and used as a DISPLAY
fallback for the note entry box; its text has never reached `bundle["Note"]`
and therefore never reached the export. The limb-parameters CSV has had no app
reader at all for several versions. Folding either into the bundles would
silently change exports that have been stable for years (and could resurrect a
note the user deleted), so both keep their own table: the bytes survive the
sidecars' retirement, the export stays byte-identical, and a researcher can
promote a value deliberately by re-saving the frame.

Nothing is ever DELETED. A consumed source is renamed to `<name>.migrated`, so
the pre-migration bytes stay on disk for as long as the researcher keeps the
folder, and re-running the migration is a no-op (the sources are gone from
their original names, and the DB already exists anyway).

Idempotence rules, in order of precedence:
  * DB present  -> return `already_migrated`; touch nothing.
  * DB absent, no legacy sources -> create an empty DB, rename nothing.
  * DB absent, sources present -> import, then rename. If the import raises,
    the transaction rolls back, the half-written DB file is removed and NO
    source is renamed, so the next attempt starts from the same inputs.
"""

import os
import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from adapters.sqlite_repo import (
    META_CLOTHES_SCALE,
    META_CREATED_BY_VERSION,
    META_FPS,
    META_LABELING_TIME,
    META_LAST_FRAME,
    META_MIGRATED_AT,
    META_MIGRATED_SOURCES,
    META_TOTAL_FRAMES,
    META_VIDEO_NAME,
    SqliteRepository,
)
from adapters.unified_repo import load_limb_parameters, load_notes_csv
from domain.project import ProjectPaths

MIGRATED_SUFFIX = ".migrated"

LIMB_PARAM_COLUMN_TO_KEY = {
    "Parameter_1": "Par1",
    "Parameter_2": "Par2",
    "Parameter_3": "Par3",
}


@dataclass
class MigrationResult:
    """What the migration did — logged, and asserted on by the tests."""

    db_path: str
    already_migrated: bool = False
    created_empty: bool = False
    frames_imported: int = 0
    notes_applied: int = 0
    limb_params_applied: int = 0
    clothes_dots: int = 0
    sources_migrated: List[str] = field(default_factory=list)
    sources_skipped: List[Tuple[str, str]] = field(default_factory=list)


# === clothes sidecar parsing ==================================================
_DOT_RE = re.compile(
    r"^Dot ID\s+(?P<dot>\d+)\s*:\s*X=(?P<x>[-\d.]+),\s*Y=(?P<y>[-\d.]+)"
    r"(?:,\s*Zones=(?P<zones>.*))?$"
)


def parse_clothes_file(path: str) -> Tuple[List[Tuple[int, float, float, str]], Optional[float]]:
    """Read `state/<video>_clothes.txt` into `[(dot_id, x, y, zones_str)]` plus
    its `DiagramScale`.

    `zones_str` keeps the file's raw comma-joined tail on purpose:
    `unified_repo.extract_zones_from_file` never split it, and the export
    metadata's "Zones Covered With Clothes" list is a frozen contract built on
    that exact tokenization.
    """
    rows: List[Tuple[int, float, float, str]] = []
    scale: Optional[float] = None
    if not (path and os.path.exists(path)):
        return rows, scale
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line.lower().startswith("diagramscale:"):
                try:
                    scale = float(line.split(":", 1)[1].strip())
                except ValueError:
                    print(f"WARN: state_migration: unreadable DiagramScale in {path}: {line!r}")
                continue
            match = _DOT_RE.match(line)
            if not match:
                continue
            rows.append((
                int(match.group("dot")),
                float(match.group("x")),
                float(match.group("y")),
                (match.group("zones") or "").strip(),
            ))
    return rows, scale


# === source discovery =========================================================
def legacy_state_sources(paths: ProjectPaths) -> List[str]:
    """Every legacy working-state file this project still has on disk, in the
    order they are consumed. The export CSV is NOT here: it is a recovery
    input, not a state file, and it must survive untouched."""
    candidates = [
        paths.unified_csv,
        paths.notes_csv,
        paths.limb_params_csv,
        paths.clothes_txt,
        paths.last_position_json,
        paths.video_time_json,
    ]
    candidates += [paths.limb_csv(limb) for limb in ("RH", "LH", "RL", "LL")]
    return [p for p in candidates if os.path.exists(p)]


def needs_migration(paths: ProjectPaths) -> bool:
    return not os.path.exists(paths.state_db)


# === the migration ============================================================
def migrate_state_to_sqlite(paths: ProjectPaths, *, fps=None,
                            program_version=None,
                            progress_cb=None) -> Tuple[SqliteRepository, MigrationResult]:
    """Open (or build) this project's state DB and return it with a report.

    Always returns an OPEN repository — the caller owns closing it.
    """
    result = MigrationResult(db_path=paths.state_db)

    if not needs_migration(paths):
        print(f"INFO: state_migration: {paths.state_db} already exists — no import needed")
        result.already_migrated = True
        repo = SqliteRepository(paths.state_db)
        _stamp_open_meta(repo, paths, fps, program_version)
        return repo, result

    sources = legacy_state_sources(paths)
    print(
        f"INFO: state_migration: no state DB for {paths.video_name!r}; "
        f"legacy sources found={len(sources)}"
        + ("".join(f"\nINFO: state_migration:   source {p}" for p in sources))
    )

    # --- read EVERYTHING first, with the existing readers, before we write ----
    from service_layer import project_service  # local: avoids an import cycle

    frames: Dict[int, dict] = {}
    if os.path.exists(paths.unified_csv) or os.path.exists(paths.export_csv):
        frames = project_service.load_frames_dataset(paths, progress_cb=progress_cb) or {}
    if not frames:
        # Tier 3 of the ladder, same guard as load_video.
        project_service.migrate_legacy_limb_csvs(frames, paths)
    print(f"INFO: state_migration: recovered frames={len(frames)} from legacy sources")

    legacy_notes = _read_legacy_notes(paths)
    legacy_limb_params = _read_legacy_limb_params(paths)

    clothes_rows, clothes_scale = parse_clothes_file(paths.clothes_txt)
    total_frames = _read_total_frames(paths)
    clamp_to = total_frames if total_frames is not None else _max_frame(frames)
    last_frame = project_service.read_last_position(paths, clamp_to)
    labeling_time = project_service.load_labeling_time_seconds(paths.video_time_json)
    print(
        f"INFO: state_migration: sidecars read — legacy_notes={len(legacy_notes)} "
        f"legacy_limb_param_states={len(legacy_limb_params)} "
        f"clothes_dots={len(clothes_rows)} clothes_scale={clothes_scale} "
        f"last_frame={last_frame} total_frames={total_frames} "
        f"labeling_time_s={labeling_time}"
    )

    # --- write: one transaction, then (and only then) rename the sources -----
    repo = SqliteRepository(paths.state_db)
    try:
        with repo.transaction():
            frames_imported = repo.import_frames(frames, clamp_to)
            repo.import_clothes(clothes_rows)
            repo.import_legacy_notes(legacy_notes)
            repo.import_legacy_limb_params(legacy_limb_params)
            meta = {
                META_VIDEO_NAME: paths.video_name,
                META_LABELING_TIME: round(float(labeling_time or 0.0), 3),
                META_MIGRATED_AT: time.strftime("%Y-%m-%dT%H:%M:%S"),
                META_MIGRATED_SOURCES: ";".join(os.path.basename(p) for p in sources),
            }
            if program_version is not None:
                meta[META_CREATED_BY_VERSION] = program_version
            if fps is not None:
                meta[META_FPS] = fps
            if last_frame is not None:
                meta[META_LAST_FRAME] = int(last_frame)
            if total_frames is not None:
                meta[META_TOTAL_FRAMES] = int(total_frames)
            if clothes_scale is not None:
                meta[META_CLOTHES_SCALE] = float(clothes_scale)
            repo.stage_meta(meta)
    except Exception as exc:
        print(f"ERROR: state_migration: import failed ({exc}) — rolling back and "
              f"discarding the partial DB; no source file was renamed")
        try:
            repo.close()
        finally:
            _discard_partial_db(paths.state_db)
        raise

    result.frames_imported = frames_imported
    result.notes_applied = len(legacy_notes)
    result.limb_params_applied = len(legacy_limb_params)
    result.clothes_dots = len(clothes_rows)

    for source in sources:
        renamed, reason = _rename_migrated(source)
        if renamed:
            result.sources_migrated.append(source)
        else:
            result.sources_skipped.append((source, reason))

    if not sources:
        result.created_empty = True
        print(
            f"INFO: state_migration: no legacy sources — created an empty state DB "
            f"-> {paths.state_db}"
        )
    print(
        f"INFO: state_migration: DONE frames={result.frames_imported} "
        f"clothes_dots={result.clothes_dots} renamed={len(result.sources_migrated)} "
        f"skipped={len(result.sources_skipped)} -> {paths.state_db}"
    )
    return repo, result


# === helpers ==================================================================
def _stamp_open_meta(repo: SqliteRepository, paths: ProjectPaths, fps,
                     program_version) -> None:
    """Keep the provenance columns current on every open (cheap, and it makes
    an orphaned DB self-describing)."""
    meta = {META_VIDEO_NAME: paths.video_name}
    if fps is not None:
        meta[META_FPS] = fps
    if program_version is not None:
        meta[META_CREATED_BY_VERSION] = program_version
    repo.set_meta_many(meta)


def _read_legacy_notes(paths: ProjectPaths) -> Dict[int, str]:
    """`state/<video>_notes.csv` via the existing (UTF-8 / cp1252) reader.

    Goes to the `legacy_notes` table, NOT into `bundle["Note"]` — see the
    module docstring. This is exactly the dict `project_service.load_notes`
    used to hand to `video.notes`.
    """
    path = paths.notes_csv
    if not os.path.exists(path):
        return {}
    notes = load_notes_csv(path)
    print(f"INFO: state_migration: notes CSV rows={len(notes)} <- {path}")
    return notes


def _read_legacy_limb_params(paths: ProjectPaths) -> List[Tuple[int, str, str, Optional[str]]]:
    """`state/<video>_limb_parameters.csv` via the existing reader, flattened to
    `(frame, limb, ParN, state)` rows for the `legacy_limb_params` table."""
    path = paths.limb_params_csv
    if not os.path.exists(path):
        return []
    per_column = dict(zip(("Parameter_1", "Parameter_2", "Parameter_3"),
                          load_limb_parameters(path)))
    rows: List[Tuple[int, str, str, Optional[str]]] = []
    for column, states in per_column.items():
        key = LIMB_PARAM_COLUMN_TO_KEY[column]
        for (limb, frame), state in states.items():
            rows.append((int(frame), str(limb), key, state))
    print(f"INFO: state_migration: limb-parameter CSV states={len(rows)} <- {path}")
    return rows


def _read_total_frames(paths: ProjectPaths) -> Optional[int]:
    """`total_frames` was only ever recorded in the last-position sidecar."""
    import json

    path = paths.last_position_json
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh) or {}
        value = payload.get("total_frames")
        return None if value is None else int(value)
    except Exception as exc:
        print(f"WARN: state_migration: could not read total_frames from {path}: {exc}")
        return None


def _max_frame(frames: Dict[int, dict]) -> int:
    """Upper bound for clamping the restored position when the sidecar has no
    `total_frames` — the highest labeled frame is the best available guess.
    The real bound is re-stamped from the probe on the next save."""
    return max(frames) if frames else 0


def _rename_migrated(path: str) -> Tuple[bool, str]:
    """`<name>` -> `<name>.migrated`. NEVER deletes: on a collision the source
    is left exactly where it is with a WARN, mirroring how
    `migration_service` handles a colliding directory rename."""
    target = path + MIGRATED_SUFFIX
    if os.path.exists(target):
        print(
            f"WARN: state_migration: {target} already exists — leaving {path} in "
            "place (nothing deleted, nothing overwritten)"
        )
        return False, "target exists"
    try:
        os.rename(path, target)
        print(f"INFO: state_migration: renamed {path} -> {target}")
        return True, ""
    except OSError as exc:
        print(f"WARN: state_migration: could not rename {path}: {exc}")
        return False, str(exc)


def _discard_partial_db(db_path: str) -> None:
    for suffix in ("", "-journal"):
        candidate = db_path + suffix
        try:
            if os.path.exists(candidate):
                os.remove(candidate)
                print(f"INFO: state_migration: removed partial {candidate}")
        except OSError as exc:
            print(f"WARN: state_migration: could not remove {candidate}: {exc}")
