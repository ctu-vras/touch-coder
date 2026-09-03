"""
adapters/export_reader.py
Reading side of `export/<video>_export.csv` — the counterpart to
`adapters/export_writer.py`. All the file-format tolerance lives here so the
domain never touches a path:

  1. Current exports are plain CSVs whose first line IS the header.
  2. Legacy exports (pre-8.x) prefixed the table with a 6-line human-readable
     preamble (`Program Version: …`, `Video Name: …`, …). Those are retried
     with `skiprows=LEGACY_PREAMBLE_LINES`.

A file that yields no `Frame` column either way is unreadable and raises
`ExportReadError` (chained to the underlying pandas error). Missing FILES raise
the OSError as-is — a caller must be able to tell "no export yet" from
"corrupt export".

Column-level validation is deliberately NOT done here; it is a pure rule and
lives in `domain.touch_stats.validate_export_columns`.
"""

import logging

import pandas as pd


logger = logging.getLogger(__name__)

# Legacy exports carried metadata on the first 6 lines before the header row.
LEGACY_PREAMBLE_LINES = 6

_PARSE_ERRORS = (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError)


class ExportReadError(ValueError):
    """Raised when neither current nor legacy export CSV parsing succeeds."""


def read_export_df(export_path: str) -> pd.DataFrame:
    """Load an export CSV as a DataFrame, tolerating the legacy preamble.

    Raises the original OSError for a missing/unopenable file, and
    `ExportReadError` when the bytes cannot be parsed as either layout.
    """
    try:
        df = pd.read_csv(export_path)
        if "Frame" in df.columns:
            logger.debug("read export CSV %s (%d rows, current layout)", export_path, len(df))
            return df
        last_exc = ValueError("required 'Frame' column is missing")
        logger.warning("current export CSV parse failed for %s: %s", export_path, last_exc)
    except OSError as exc:
        logger.error("cannot open export CSV %s: %r", export_path, exc)
        raise
    except _PARSE_ERRORS as exc:
        last_exc = exc
        logger.warning("current export CSV parse failed for %s: %r", export_path, exc)

    # Fallback for older exports with a 6-line metadata preamble.
    try:
        df = pd.read_csv(export_path, skiprows=LEGACY_PREAMBLE_LINES)
        if "Frame" in df.columns:
            logger.debug(
                "read export CSV %s (%d rows, legacy preamble layout)", export_path, len(df)
            )
            return df
        last_exc = ValueError("required 'Frame' column is missing after legacy header")
        logger.warning("legacy export CSV parse failed for %s: %s", export_path, last_exc)
    except OSError as exc:
        logger.error("cannot open export CSV %s: %r", export_path, exc)
        last_exc = exc
    except _PARSE_ERRORS as exc:
        last_exc = exc
        logger.warning("legacy export CSV parse failed for %s: %r", export_path, exc)

    raise ExportReadError(
        f"Could not read export CSV {export_path}; current and legacy formats failed"
    ) from last_exc
