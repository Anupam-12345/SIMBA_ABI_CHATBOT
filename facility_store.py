"""
Encrypted facility data store.

Replaces "Facility Data.xlsx" with an encrypted file so the spreadsheet never
has to be committed, shipped or copied to a deployment machine.

  Facility Data.xlsx  --(tools/build_facility_db.py)-->  database/facilities.enc

At runtime the file is decrypted in memory only. Nothing is written to disk in
plaintext, and there is no temporary file to leak.

Format of facilities.enc:
    Fernet( gzip( UTF-8 CSV ) )
Fernet is AES-128-CBC with an HMAC-SHA256 authentication tag, so the file is
both encrypted and tamper-evident: a modified file fails to decrypt rather
than silently returning wrong data.

The key lives in the FACILITY_DB_KEY environment variable (put it in .env),
never in the repository.

Backward compatible: if facilities.enc is absent, the loader falls back to the
original Excel file, so existing installs keep working untouched.
"""

from __future__ import annotations

import gzip
import io
import os
import sqlite3
from typing import Optional, Tuple

import pandas as pd

REQUIRED_COLUMNS = [
    "Facility Name",
    "Time Zone",
    "Address",
    "Primary City",
    "Primary State",
    "Primary Phone",
    "Serve City",
    "Serve State",
    "Serve Phone",
]

ENV_KEY = "FACILITY_DB_KEY"


class FacilityStoreError(RuntimeError):
    """Raised when the encrypted store exists but cannot be opened."""


# ----------------------------------------------------------------- utilities

def _fernet(key: Optional[str] = None):
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise FacilityStoreError(
            "The 'cryptography' package is required for the encrypted facility "
            "store. Install it with: pip install cryptography"
        ) from exc

    key = key or os.environ.get(ENV_KEY, "").strip()
    if not key:
        raise FacilityStoreError(
            f"{ENV_KEY} is not set. Add it to your .env file. "
            "Generate one with: python tools/build_facility_db.py --new-key"
        )
    try:
        return Fernet(key.encode("utf-8"))
    except Exception as exc:
        raise FacilityStoreError(
            f"{ENV_KEY} is not a valid Fernet key. It should be 44 characters of "
            "url-safe base64. Generate one with: "
            "python tools/build_facility_db.py --new-key"
        ) from exc


def generate_key() -> str:
    """Return a fresh Fernet key as a string."""
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode("utf-8")


def normalise(frame: pd.DataFrame) -> pd.DataFrame:
    """
    Apply exactly the cleaning the app used to do inline: strip column names,
    add any missing required columns, fill NaN, cast everything to str.
    """
    frame = frame.copy()
    frame.columns = [str(column).strip() for column in frame.columns]

    for column in REQUIRED_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
            print(f"⚠️ Column '{column}' not found, created empty column")

    frame = frame.fillna("")
    for column in frame.columns:
        frame[column] = frame[column].astype(str)
    return frame


# ------------------------------------------------------------------ encrypt

def encrypt_dataframe(frame: pd.DataFrame, key: Optional[str] = None) -> bytes:
    """Serialise a DataFrame to the encrypted on-disk representation."""
    buffer = io.StringIO()
    frame.to_csv(buffer, index=False)
    compressed = gzip.compress(buffer.getvalue().encode("utf-8"), compresslevel=9)
    return _fernet(key).encrypt(compressed)


def decrypt_to_dataframe(blob: bytes, key: Optional[str] = None) -> pd.DataFrame:
    """Reverse of encrypt_dataframe. Raises if the key is wrong or data altered."""
    fernet = _fernet(key)
    try:
        compressed = fernet.decrypt(blob)
    except Exception as exc:
        raise FacilityStoreError(
            "Could not decrypt the facility database. Either FACILITY_DB_KEY is "
            "wrong, or the file was modified or corrupted in transfer."
        ) from exc
    csv_text = gzip.decompress(compressed).decode("utf-8")
    return pd.read_csv(io.StringIO(csv_text), dtype=str).fillna("")


# --------------------------------------------------------------------- load

def load_dataframe(
    encrypted_path: str,
    excel_path: Optional[str] = None,
    key: Optional[str] = None,
) -> Tuple[Optional[pd.DataFrame], str]:
    """
    Load facility records, preferring the encrypted store.

    Returns (dataframe, source) where source is "encrypted", "excel" or "none".
    Never raises for a missing file - the app must still start so the chatbot
    works even when facility search is unavailable.
    """
    if encrypted_path and os.path.exists(encrypted_path):
        try:
            with open(encrypted_path, "rb") as handle:
                blob = handle.read()
            frame = normalise(decrypt_to_dataframe(blob, key))
            return frame, "encrypted"
        except FacilityStoreError as exc:
            print(f"❌ {exc}")
            # Fall through to Excel so a bad key does not take the feature down
            # on a machine that still has the spreadsheet.
        except Exception as exc:
            print(f"❌ Unexpected error reading {encrypted_path}: {exc}")

    if excel_path and os.path.exists(excel_path):
        try:
            frame = normalise(pd.read_excel(excel_path))
            return frame, "excel"
        except Exception as exc:
            print(f"❌ Error reading {excel_path}: {exc}")

    return None, "none"


def to_sqlite(frame: pd.DataFrame) -> sqlite3.Connection:
    """
    Build an in-memory SQLite database from the records.

    Provided for callers that would rather query with SQL than filter a
    DataFrame. The database exists only in this process's memory; it is never
    written to disk, so the plaintext data has no on-disk footprint.
    """
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    frame.to_sql("facilities", connection, index=False, if_exists="replace")
    connection.execute(
        'CREATE INDEX IF NOT EXISTS idx_facility_name '
        'ON facilities ("Facility Name")'
    )
    connection.commit()
    return connection
