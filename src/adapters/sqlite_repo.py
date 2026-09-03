"""
adapters/sqlite_repo.py
The working-state repository: ONE SQLite database per labeled video at
`data/<video>/state/<video>.db`.

This replaced the append-only unified-CSV journal plus its five sidecars
(notes CSV, limb-parameters CSV, last-position JSON, labeling-time JSON,
clothes TXT). Their readers, and the one-time import that fed them into this
schema, were removed in 9.0 — so this is the only state format the app knows and
there is no path back from a pre-9.0 project (see ARCHITECTURE.md).

Why SQLite instead of the journal
---------------------------------
The journal was append-only, so a re-edited frame produced a duplicate `Frame`
row, loaders resolved duplicates last-writer-wins, and load-time compaction
had to bound file growth. A crash mid-append could leave a torn final row that
the loader had to detect and repair. All three mechanisms exist only because a
CSV cannot be updated in place. An UPSERT inside one transaction makes a save
idempotent (saving twice changes nothing) and atomic (a crash leaves either the
old or the new state, never half of both), which retires the whole apparatus.

Concurrency contract (enforced, not just documented)
----------------------------------------------------
ALL working-state I/O happens on the Tk thread. The export worker thread only
ever sees the `deepcopy` snapshot taken by `save_service.build_save_snapshot`
and writes through `adapters.export_writer` — it never touches this class.
The connection is therefore opened with `check_same_thread=True` and every
public method calls `_check_thread()` first, so an off-thread call fails loudly
instead of corrupting the database.

Durability choices
------------------
* `journal_mode=DELETE` — NOT WAL. Single-writer desktop app, and WAL's
  `-wal` / `-shm` sidecars confuse OneDrive / Dropbox sync clients (these
  project folders routinely live inside synced trees).
* `synchronous=FULL` — matches the fsync-per-save durability the old
  `durable_append` provided.
* `busy_timeout=5000` — a stray reader (e.g. a DB browser the researcher left
  open) makes a save wait, not fail.
* `foreign_keys=ON` — the per-frame child rows hang off `frames(frame)` with
  `ON DELETE CASCADE`, which is what makes "re-save one frame" a two-statement
  operation (delete the parent, re-insert) with no stale rows left behind.

Fidelity contract
-----------------
`export/<video>_export.csv` and `<video>_metadata.json` are frozen byte-level
contracts. `adapters.export_writer` consumes in-memory `FrameBundle` dicts, not
files, so the ONLY thing this module must guarantee is that
`save_frames` -> `load_frames` round-trips a `FrameBundle` faithfully. Every
distinction the exporter or the GUI can observe is representable here:

* `Note`: SQL NULL vs `''` vs text — three distinct states, as in memory.
* `Params` / `LimbParams`: an ABSENT row means the key is not in the dict; a
  row with `state IS NULL` means the key IS present with value `None`. The
  `has_limb_params` flag distinguishes "no LimbParams key" from `LimbParams={}`.
* `Onset`: nullable — real archived data contains `"Onset": null` alongside
  `""` and `"ON"` / `"OFF"`.
* `X` / `Y` / `Zones` alignment: ONE `clicks` row per index, where
  `click_index` IS the alignment. `x`/`y` are NULL for a zone bucket that has
  no matching click (legacy data has those, and the lifecycle test pins that
  they survive a round-trip); `zones` is NULL for a click with no bucket.
  `zones = '[]'` is therefore an EMPTY bucket, distinct from "no bucket".

Fields deliberately NOT stored (see ARCHITECTURE.md):
`Bodypart` is reconstructed from the limb key (every writer sets it to exactly
that and nothing reads it), `Touch` is always `None` (no writer, no reader, no
export column), the retired per-limb `Look` is dropped, and `Changed` is a
runtime dirty flag that has never been serialized by any backend.
"""

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Dict, Iterable, List, Optional, Tuple

from domain.model import (
    FrameBundle,
    FrameRecord,
    _normalize_param_state,
    empty_record,
)


logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

LIMB_KEYS = ("LH", "RH", "LL", "RL")

# Canonical values, used for observability only — never as a CHECK constraint.
# A hand-edited export or a pre-M1 blob can carry anything, and a repository
# that REFUSES to persist odd-but-real data would turn a legacy quirk into a
# failed save (i.e. lost annotations). We store it and log it instead.
CANONICAL_PARAM_KEYS = ("Par1", "Par2", "Par3")
CANONICAL_PARAM_STATES = (None, "ON", "OFF")

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- One row per frame present in the in-memory dict. The row's EXISTENCE is
-- data: a frame with no clicks, no params and no note still round-trips as a
-- present (empty) bundle, exactly as the journal's bare row did.
CREATE TABLE IF NOT EXISTS frames (
    frame INTEGER PRIMARY KEY,
    note  TEXT                       -- NULL vs '' vs text are all distinct
);

CREATE TABLE IF NOT EXISTS frame_params (
    frame INTEGER NOT NULL REFERENCES frames(frame) ON DELETE CASCADE,
    key   TEXT    NOT NULL,
    state TEXT,                      -- NULL = key present, value None
    PRIMARY KEY (frame, key)
);

CREATE TABLE IF NOT EXISTS limb_records (
    frame            INTEGER NOT NULL REFERENCES frames(frame) ON DELETE CASCADE,
    limb             TEXT    NOT NULL CHECK (limb IN ('LH','RH','LL','RL')),
    onset            TEXT,           -- NULL / '' / 'ON' / 'OFF'
    has_limb_params  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (frame, limb)
);

-- click_index IS the X/Y/Zones alignment. x/y NULL = zone bucket without a
-- click; zones NULL = click without a bucket; zones '[]' = an empty bucket.
CREATE TABLE IF NOT EXISTS clicks (
    frame       INTEGER NOT NULL REFERENCES frames(frame) ON DELETE CASCADE,
    limb        TEXT    NOT NULL CHECK (limb IN ('LH','RH','LL','RL')),
    click_index INTEGER NOT NULL,
    x           INTEGER,
    y           INTEGER,
    zones       TEXT,
    PRIMARY KEY (frame, limb, click_index)
);

CREATE TABLE IF NOT EXISTS limb_params (
    frame INTEGER NOT NULL REFERENCES frames(frame) ON DELETE CASCADE,
    limb  TEXT    NOT NULL CHECK (limb IN ('LH','RH','LL','RL')),
    key   TEXT    NOT NULL,
    state TEXT,                      -- NULL = key present, value None
    PRIMARY KEY (frame, limb, key)
);

-- Clothes dots are project-wide, not per-frame: no FK to frames.
CREATE TABLE IF NOT EXISTS clothes_dots (
    dot_id INTEGER PRIMARY KEY,
    x      REAL,
    y      REAL,
    zones  TEXT NOT NULL DEFAULT ''  -- comma-joined, frozen sidecar semantics
);

PRAGMA user_version = {SCHEMA_VERSION};
"""

# meta keys (all values stored as TEXT; typed accessors coerce)
META_VIDEO_NAME = "video_name"
META_CREATED_BY_VERSION = "created_by_version"
META_FPS = "fps"
META_LAST_FRAME = "last_frame"
META_TOTAL_FRAMES = "total_frames"
META_LABELING_TIME = "labeling_time_seconds"
META_CLOTHES_SCALE = "clothes_diagram_scale"


class SchemaVersionError(RuntimeError):
    """The database was written by a NEWER TinyTouch than this one.

    Refusing to open is deliberate: a forward-compatible guess would silently
    drop columns this build does not know about on the next save.
    """


class SqliteRepository:
    """Working state for exactly one labeled video, in one SQLite file.

    Open it once when the project opens, keep it for the app's lifetime, close
    it when the project closes. Never hand the instance (or its connection) to
    a worker thread.
    """

    def __init__(self, db_path: str):
        self._path = db_path
        self._owner_ident = threading.get_ident()
        self._owner_name = threading.current_thread().name

        directory = os.path.dirname(db_path) or "."
        os.makedirs(directory, exist_ok=True)
        is_new = not os.path.exists(db_path)

        # isolation_level=None -> autocommit; we issue BEGIN IMMEDIATE ourselves
        # so every save is exactly one explicit transaction.
        self._conn = sqlite3.connect(
            db_path, isolation_level=None, check_same_thread=True
        )
        self._conn.row_factory = sqlite3.Row
        self._apply_pragmas()

        version = self._user_version()
        if is_new or version == 0:
            self._create_schema()
            logger.info(
                "sqlite_repo: created state DB (schema v%d) -> %s", SCHEMA_VERSION, db_path
            )
        else:
            self._upgrade_from(version)
            logger.debug(
                "sqlite_repo: opened state DB (schema v%d) -> %s (%d bytes)",
                self._user_version(),
                db_path,
                os.path.getsize(db_path),
            )

    # === plumbing =============================================================
    @property
    def path(self) -> str:
        return self._path

    def _check_thread(self) -> None:
        if threading.get_ident() != self._owner_ident:
            raise RuntimeError(
                "SqliteRepository was accessed from thread "
                f"{threading.current_thread().name!r} but is owned by "
                f"{self._owner_name!r}. ALL working-state I/O must stay on the "
                "Tk thread; worker threads get the deepcopy snapshot instead "
                "(see save_service.build_save_snapshot)."
            )

    def _apply_pragmas(self) -> None:
        cur = self._conn.cursor()
        # DELETE (not WAL): see module docstring — WAL sidecars break cloud sync.
        cur.execute("PRAGMA journal_mode=DELETE")
        cur.execute("PRAGMA synchronous=FULL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    def _user_version(self) -> int:
        return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def _create_schema(self) -> None:
        # NOT wrapped in our own transaction: sqlite3.executescript COMMITs any
        # pending transaction first, which would leave our explicit COMMIT with
        # nothing to commit. Every statement is `IF NOT EXISTS` plus the
        # user_version pragma, so re-running it is a no-op.
        self._conn.executescript(_SCHEMA)

    def _upgrade_from(self, version: int) -> None:
        """Bring an existing DB up to SCHEMA_VERSION. No migrations exist yet;
        the ladder is here so v2 has an obvious home and so a FUTURE file is
        rejected instead of silently down-converted."""
        if version > SCHEMA_VERSION:
            raise SchemaVersionError(
                f"{self._path} has schema v{version} but this TinyTouch build "
                f"understands at most v{SCHEMA_VERSION}. Update TinyTouch — "
                "opening it here would drop the newer fields on the next save."
            )
        if version < SCHEMA_VERSION:
            # v0 is handled by the caller (fresh create). No v1-> path yet.
            logger.info(
                "sqlite_repo: upgrading %s from schema v%d to v%d",
                self._path,
                version,
                SCHEMA_VERSION,
            )
            self._create_schema()

    class _Transaction:
        def __init__(self, conn):
            self._conn = conn

        def __enter__(self):
            # IMMEDIATE takes the write lock up front, so a concurrent writer
            # fails/waits at BEGIN rather than halfway through the save.
            self._conn.execute("BEGIN IMMEDIATE")
            return self._conn

        def __exit__(self, exc_type, exc, tb):
            if exc_type is None:
                self._conn.execute("COMMIT")
            else:
                self._conn.execute("ROLLBACK")
            return False

    def transaction(self) -> "SqliteRepository._Transaction":
        """One explicit `BEGIN IMMEDIATE ... COMMIT`, rolled back on any
        exception. Public so a caller can wrap several writes (e.g. clothes
        rows plus their scale) in a single transaction."""
        self._check_thread()
        return self._Transaction(self._conn)

    def close(self) -> None:
        self._check_thread()
        try:
            self._conn.close()
            logger.debug("sqlite_repo: closed state DB -> %s", self._path)
        except Exception:  # never block app shutdown on this
            logger.warning("sqlite_repo: close failed for %s", self._path, exc_info=True)

    # === meta =================================================================
    def get_meta(self, key: str, default=None) -> Optional[str]:
        self._check_thread()
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return default if row is None else row["value"]

    def set_meta(self, key: str, value) -> None:
        self._check_thread()
        with self.transaction():
            self._set_meta_unlocked(key, value)

    def set_meta_many(self, mapping: Dict[str, object]) -> None:
        self._check_thread()
        with self.transaction():
            for key, value in mapping.items():
                self._set_meta_unlocked(key, value)

    def _set_meta_unlocked(self, key: str, value) -> None:
        stored = None if value is None else str(value)
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, stored),
        )

    def _get_meta_float(self, key: str, default: Optional[float]) -> Optional[float]:
        raw = self.get_meta(key)
        if raw is None:
            return default
        try:
            return float(raw)
        except (TypeError, ValueError):
            logger.warning(
                "sqlite_repo: meta[%s]=%r is not a number; using %r", key, raw, default
            )
            return default

    def _get_meta_int(self, key: str, default: Optional[int]) -> Optional[int]:
        value = self._get_meta_float(key, None)
        return default if value is None else int(value)

    # === frames: load =========================================================
    def load_frames(self, progress_cb=None) -> Dict[int, FrameBundle]:
        """Rebuild the whole in-memory `{frame: FrameBundle}` store.

        One query per table (not per frame): the row counts here are
        frames x limbs x clicks, and per-frame queries made the old loader the
        slowest part of opening a long video.
        """
        self._check_thread()
        t0 = time.time()

        frames: Dict[int, FrameBundle] = {}
        rows = self._conn.execute(
            "SELECT frame, note FROM frames ORDER BY frame"
        ).fetchall()
        total = len(rows)
        report_every = max(5000, total // 20) if total else 5000
        for index, row in enumerate(rows):
            frames[row["frame"]] = {
                "LH": None, "RH": None, "LL": None, "RL": None,  # filled below
                "Note": row["note"],
                "Params": {},
            }
            if progress_cb and (index + 1) % report_every == 0:
                try:
                    progress_cb(index + 1, total, "Loading state database", time.time() - t0)
                except Exception:
                    logger.warning("sqlite_repo: progress_cb failed", exc_info=True)

        params_n = 0
        for row in self._conn.execute(
            "SELECT frame, key, state FROM frame_params ORDER BY frame, key"
        ):
            bundle = frames.get(row["frame"])
            if bundle is None:
                continue
            bundle["Params"][row["key"]] = row["state"]
            params_n += 1

        # limb records first (they own Onset + the LimbParams-presence flag)
        records: Dict[Tuple[int, str], FrameRecord] = {}
        recs_n = 0
        for row in self._conn.execute(
            "SELECT frame, limb, onset, has_limb_params FROM limb_records "
            "ORDER BY frame, limb"
        ):
            frame, limb = row["frame"], row["limb"]
            if frame not in frames:
                continue
            rec: FrameRecord = {
                "X": [], "Y": [],
                "Onset": row["onset"],
                # Bodypart is RECONSTRUCTED, never stored (see docstring).
                "Bodypart": limb,
                "Zones": [],
                "Touch": None,
            }
            if row["has_limb_params"]:
                rec["LimbParams"] = {}
            records[(frame, limb)] = rec
            recs_n += 1

        clicks_n = 0
        for row in self._conn.execute(
            "SELECT frame, limb, click_index, x, y, zones FROM clicks "
            "ORDER BY frame, limb, click_index"
        ):
            rec = records.get((row["frame"], row["limb"]))
            if rec is None:
                # Defensive: a limb row is always written alongside its clicks,
                # but never drop annotations just because it went missing.
                rec = {
                    "X": [], "Y": [], "Onset": "", "Bodypart": row["limb"],
                    "Zones": [], "Touch": None,
                }
                records[(row["frame"], row["limb"])] = rec
                logger.warning(
                    "sqlite_repo: clicks row without limb_records parent "
                    "(frame=%s limb=%s) — record rebuilt",
                    row["frame"],
                    row["limb"],
                )
            if row["x"] is not None:
                rec["X"].append(row["x"])
            if row["y"] is not None:
                rec["Y"].append(row["y"])
            if row["zones"] is not None:
                rec["Zones"].append(_loads_bucket(row["zones"], row["frame"], row["limb"]))
            clicks_n += 1

        lp_n = 0
        for row in self._conn.execute(
            "SELECT frame, limb, key, state FROM limb_params ORDER BY frame, limb, key"
        ):
            rec = records.get((row["frame"], row["limb"]))
            if rec is None:
                continue
            rec.setdefault("LimbParams", {})[row["key"]] = _normalize_param_state(
                row["state"]
            )
            lp_n += 1

        # Attach records; limbs with no row are the canonical empty record.
        for frame, bundle in frames.items():
            for limb in LIMB_KEYS:
                bundle[limb] = records.get((frame, limb)) or empty_record(limb)

        if progress_cb and total:
            try:
                progress_cb(total, total, "Loading state database", time.time() - t0)
            except Exception:
                logger.warning("sqlite_repo: progress_cb failed", exc_info=True)

        logger.debug(
            "sqlite_repo: loaded frames=%d limb_records=%d clicks=%d "
            "frame_params=%d limb_params=%d in %.2fs <- %s",
            total,
            recs_n,
            clicks_n,
            params_n,
            lp_n,
            time.time() - t0,
            self._path,
        )
        return frames

    def frame_count(self) -> int:
        self._check_thread()
        return int(self._conn.execute("SELECT COUNT(*) FROM frames").fetchone()[0])

    # === frames: save =========================================================
    def save_frames(self, frames: Dict[int, FrameBundle], total_frames: int) -> int:
        """Persist every DIRTY frame (`Changed` is still the dirty tracker) in
        ONE transaction. Idempotent: a second identical save rewrites the same
        rows to the same values, so row counts and contents are stable.

        Returns the number of frames written.
        """
        self._check_thread()
        dirty = sorted(
            f for f, b in frames.items()
            if isinstance(b, dict) and b.get("Changed")
        )
        if not dirty:
            logger.debug(
                "sqlite_repo: save skipped — no dirty frames (total_frames=%d) -> %s",
                total_frames,
                self._path,
            )
            self.set_meta(META_TOTAL_FRAMES, total_frames)
            return 0

        t0 = time.time()
        anomalies: List[str] = []
        with self.transaction():
            for frame in dirty:
                self._write_frame_unlocked(frame, frames[frame], anomalies)
            self._set_meta_unlocked(META_TOTAL_FRAMES, total_frames)

        for message in anomalies[:20]:
            logger.warning("sqlite_repo: %s", message)
        if len(anomalies) > 20:
            logger.warning("sqlite_repo: … and %d more anomalies", len(anomalies) - 20)
        logger.debug(
            "sqlite_repo: saved dirty frames=%d (total_frames=%d) in %.3fs -> %s",
            len(dirty),
            total_frames,
            time.time() - t0,
            self._path,
        )
        return len(dirty)

    def _write_frame_unlocked(self, frame: int, bundle: FrameBundle,
                              anomalies: List[str]) -> None:
        """Replace one frame's rows. DELETE cascades to every child table, so
        removed clicks / cleared params leave nothing stale behind — that is
        what makes a re-save idempotent rather than additive."""
        conn = self._conn
        conn.execute("DELETE FROM frames WHERE frame = ?", (frame,))
        conn.execute(
            "INSERT INTO frames (frame, note) VALUES (?, ?)",
            (frame, bundle.get("Note")),
        )

        params = bundle.get("Params")
        if isinstance(params, dict):
            for key, state in params.items():
                if key not in CANONICAL_PARAM_KEYS or state not in CANONICAL_PARAM_STATES:
                    anomalies.append(
                        f"frame {frame}: non-canonical global param {key!r}={state!r} "
                        "(stored verbatim)"
                    )
                conn.execute(
                    "INSERT INTO frame_params (frame, key, state) VALUES (?, ?, ?)",
                    (frame, str(key), state),
                )

        for limb in LIMB_KEYS:
            rec = bundle.get(limb)
            if not isinstance(rec, dict) or _record_is_empty(rec):
                # Nothing to store: `load_frames` rebuilds the canonical
                # `empty_record(limb)` for any limb without a row.
                continue
            limb_params = rec.get("LimbParams")
            conn.execute(
                "INSERT INTO limb_records (frame, limb, onset, has_limb_params) "
                "VALUES (?, ?, ?, ?)",
                (frame, limb, rec.get("Onset"), 1 if isinstance(limb_params, dict) else 0),
            )

            xs = rec.get("X") or []
            ys = rec.get("Y") or []
            zones = rec.get("Zones") or []
            if len(xs) != len(ys):
                anomalies.append(
                    f"frame {frame} {limb}: {len(xs)} X vs {len(ys)} Y values — "
                    "stored as-is (indices past the shorter list get NULL)"
                )
            for index in range(max(len(xs), len(ys), len(zones))):
                x = xs[index] if index < len(xs) else None
                y = ys[index] if index < len(ys) else None
                if (x is not None and not isinstance(x, int)) or (
                    y is not None and not isinstance(y, int)
                ):
                    anomalies.append(
                        f"frame {frame} {limb} click {index}: non-integer "
                        f"coordinate ({x!r}, {y!r}) — INTEGER affinity may round it"
                    )
                bucket = (
                    json.dumps(zones[index], ensure_ascii=False)
                    if index < len(zones)
                    else None
                )
                conn.execute(
                    "INSERT INTO clicks (frame, limb, click_index, x, y, zones) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (frame, limb, index, x, y, bucket),
                )

            if isinstance(limb_params, dict):
                for key, state in limb_params.items():
                    if key not in CANONICAL_PARAM_KEYS or state not in CANONICAL_PARAM_STATES:
                        anomalies.append(
                            f"frame {frame} {limb}: non-canonical limb param "
                            f"{key!r}={state!r} (stored verbatim)"
                        )
                    conn.execute(
                        "INSERT INTO limb_params (frame, limb, key, state) "
                        "VALUES (?, ?, ?, ?)",
                        (frame, limb, str(key), state),
                    )

    # === last position (was state/<name>_last_position.json) ==================
    def save_last_position(self, frame: int, total_frames: int) -> None:
        self._check_thread()
        self.set_meta_many(
            {META_LAST_FRAME: int(frame), META_TOTAL_FRAMES: int(total_frames)}
        )
        logger.debug("sqlite_repo: saved last position frame=%d -> %s", frame, self._path)

    def read_last_position(self, total_frames: int) -> Optional[int]:
        """Clamped resume frame, or None when the DB has never stored one."""
        self._check_thread()
        frame = self._get_meta_int(META_LAST_FRAME, None)
        if frame is None:
            return None
        return max(0, min(total_frames, frame))

    # === labeling time (was state/<name>_metadata.json) =======================
    def load_labeling_time_seconds(self) -> float:
        self._check_thread()
        return self._get_meta_float(META_LABELING_TIME, 0.0) or 0.0

    def save_labeling_time_seconds(self, total_seconds: float) -> None:
        self._check_thread()
        self.set_meta(META_LABELING_TIME, round(float(total_seconds), 3))

    # === clothes (was state/<name>_clothes.txt) ===============================
    def save_clothes(self, rows: Iterable[Tuple[int, float, float, str]],
                     diagram_scale: float) -> None:
        """Full replace of the clothes dots plus the scale they were drawn at.

        `rows` are `(dot_id, x, y, zones_str)`. `zones_str` keeps the frozen
        sidecar semantics: the comma-JOINED zone names for that dot, because
        `extract_zones_from_file` treated the whole `Zones=` tail as a single
        token and the export metadata contract is built on that.
        """
        self._check_thread()
        rows = list(rows)
        with self.transaction():
            self._conn.execute("DELETE FROM clothes_dots")
            self._conn.executemany(
                "INSERT INTO clothes_dots (dot_id, x, y, zones) VALUES (?, ?, ?, ?)",
                [(int(d), float(x), float(y), str(z or "")) for d, x, y, z in rows],
            )
            self._set_meta_unlocked(META_CLOTHES_SCALE, float(diagram_scale))
        logger.debug(
            "sqlite_repo: saved clothes dots=%d scale=%s -> %s",
            len(rows),
            diagram_scale,
            self._path,
        )

    def load_clothes_rows(self) -> List[Tuple[int, float, float, str]]:
        self._check_thread()
        return [
            (row["dot_id"], row["x"], row["y"], row["zones"])
            for row in self._conn.execute(
                "SELECT dot_id, x, y, zones FROM clothes_dots ORDER BY dot_id"
            )
        ]

    def clothes_diagram_scale(self) -> Optional[float]:
        self._check_thread()
        return self._get_meta_float(META_CLOTHES_SCALE, None)

    def has_clothes(self) -> bool:
        self._check_thread()
        return bool(
            self._conn.execute("SELECT 1 FROM clothes_dots LIMIT 1").fetchone()
        )

    def clothes_zone_list(self) -> Optional[List[str]]:
        """Zone names for the export metadata's "Zones Covered With Clothes".

        Deliberately reproduces the retired sidecar reader
        (`extract_zones_from_file`) byte-for-byte: de-duplicated via a set,
        returned as an UNSORTED list, and a multi-zone dot contributing its
        comma-joined string as ONE entry. The export metadata JSON is a frozen
        contract — sorting or splitting here would change published sidecars.
        """
        self._check_thread()
        zones = {row[3] for row in self.load_clothes_rows()}
        return list(zones) if zones else None

# === helpers ==================================================================
def _record_is_empty(rec: dict) -> bool:
    """True when `rec` carries nothing `load_frames` cannot rebuild from
    `empty_record(limb)` — i.e. no clicks, no zone buckets, no LimbParams key,
    and an Onset of exactly `''`. `Onset=None` is NOT empty (archived data has
    it, and a NULL must come back as None)."""
    return (
        not rec.get("X")
        and not rec.get("Y")
        and not rec.get("Zones")
        and rec.get("Onset", "") == ""
        and "LimbParams" not in rec
    )


def _loads_bucket(raw: str, frame: int, limb: str):
    """A zone bucket is normally `list[str]`, but legacy flat-shaped data
    stores a bare string per bucket; `json.dumps`/`loads` round-trips either."""
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "sqlite_repo: unreadable zones bucket for frame=%s limb=%s: %r (%s) — using []",
            frame,
            limb,
            raw,
            exc,
        )
        return []
