r"""
Build the encrypted facility database from the Excel sheet.

Run this once on a machine that has the spreadsheet. Ship the resulting
database/facilities.enc with the code; keep the spreadsheet off the repo.

Generate a key (once, ever):

    python tools/build_facility_db.py --new-key

Put the printed key in .env as FACILITY_DB_KEY, then build:

    python tools/build_facility_db.py --excel "C:\path\to\Facility Data.xlsx"

Verify an existing database without touching the Excel:

    python tools/build_facility_db.py --verify
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import config  # noqa: E402
import facility_store as store  # noqa: E402

DEFAULT_ENC = os.path.join(config.BASE_DIR, "database", "facilities.enc")
DEFAULT_EXCEL = os.path.join(config.BASE_DIR, "Facility Data.xlsx")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--new-key", action="store_true",
                        help="print a fresh FACILITY_DB_KEY and exit")
    parser.add_argument("--excel", default=DEFAULT_EXCEL,
                        help="path to the source spreadsheet")
    parser.add_argument("--out", default=DEFAULT_ENC,
                        help="path of the encrypted database to write")
    parser.add_argument("--verify", action="store_true",
                        help="decrypt an existing database and report on it")
    args = parser.parse_args()

    if args.new_key:
        print(store.generate_key())
        print("\nAdd this to .env as:  FACILITY_DB_KEY=<the line above>")
        print("Keep a copy somewhere safe. Lose it and the database cannot be read.")
        return 0

    if args.verify:
        frame, source = store.load_dataframe(args.out, excel_path=None)
        if frame is None:
            print(f"❌ Could not read {args.out}")
            return 1
        print(f"✅ Decrypted {args.out} ({source})")
        print(f"   {len(frame)} records, {len(frame.columns)} columns")
        print(f"   Columns: {list(frame.columns)}")
        name_column = "Facility Name"
        if name_column in frame.columns and len(frame):
            print(f"   First record: {frame.iloc[0][name_column]}")
        return 0

    if not os.path.exists(args.excel):
        print(f"❌ Spreadsheet not found: {args.excel}")
        print("   Pass the path explicitly with --excel \"C:\\path\\to\\file.xlsx\"")
        return 1

    if not os.environ.get(store.ENV_KEY):
        print(f"❌ {store.ENV_KEY} is not set.")
        print("   Run: python tools/build_facility_db.py --new-key")
        print("   then put the key in .env and try again.")
        return 1

    import pandas as pd
    print(f"Reading {args.excel} ...")
    frame = store.normalise(pd.read_excel(args.excel))
    print(f"   {len(frame)} records, columns: {list(frame.columns)}")

    blob = store.encrypt_dataframe(frame)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "wb") as handle:
        handle.write(blob)

    size_mb = len(blob) / (1024 * 1024)
    print(f"✅ Wrote {args.out} ({size_mb:.2f} MB, encrypted)")

    # Read it straight back so a broken file is caught here, not in production.
    check, _ = store.load_dataframe(args.out, excel_path=None)
    if check is None or len(check) != len(frame):
        print("❌ Verification failed - the file did not read back correctly.")
        return 1
    print(f"✅ Verified: {len(check)} records decrypt correctly.")
    print("\nYou can now remove the spreadsheet from the project folder.")
    print("Keep the original somewhere safe - it is the only way to rebuild.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
