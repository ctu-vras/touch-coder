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

import pandas as pd

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
            print(f"INFO: read export CSV {export_path} ({len(df)} rows, current layout)")
            return df
        last_exc = ValueError("required 'Frame' column is missing")
        print(f"WARN: current export CSV parse failed for {export_path}: {last_exc}")
    except OSError as exc:
        print(f"ERROR: cannot open export CSV {export_path}: {exc!r}")
        raise
    except _PARSE_ERRORS as exc:
        last_exc = exc
        print(f"WARN: current export CSV parse failed for {export_path}: {exc!r}")

    # Fallback for older exports with a 6-line metadata preamble.
    try:
        df = pd.read_csv(export_path, skiprows=LEGACY_PREAMBLE_LINES)
        if "Frame" in df.columns:
            print(f"INFO: read export CSV {export_path} ({len(df)} rows, legacy preamble layout)")
            return df
        last_exc = ValueError("required 'Frame' column is missing after legacy header")
        print(f"WARN: legacy export CSV parse failed for {export_path}: {last_exc}")
    except OSError as exc:
        print(f"ERROR: cannot open export CSV {export_path}: {exc!r}")
        last_exc = exc
    except _PARSE_ERRORS as exc:
        last_exc = exc
        print(f"WARN: legacy export CSV parse failed for {export_path}: {exc!r}")

    raise ExportReadError(
        f"Could not read export CSV {export_path}; current and legacy formats failed"
    ) from last_exc
