# Encrypted facility database

Replaces `Facility Data.xlsx` with `database/facilities.enc` so the
spreadsheet never has to be committed or shipped.

The two API endpoints (`/api/facilities/search` and
`/api/facilities/details`) are **unchanged**. Only the loader changed, and it
still falls back to the Excel file if the encrypted database is absent — so
nothing breaks on a machine that still has the spreadsheet.

---

## New files

| File | Purpose |
|---|---|
| `facility_store.py` | encrypt / decrypt / load, in the project root |
| `tools/build_facility_db.py` | one-off CLI to build the database from Excel |
| `database/facilities.enc` | the encrypted database you will ship |

`requirements.txt` gains `cryptography`.

---

## One-time setup

### 1. Generate a key

```powershell
cd C:\Users\AS222\apps\CB
.\venv\Scripts\python.exe tools\build_facility_db.py --new-key
```

Copy the 44-character key into `.env`:

```
FACILITY_DB_KEY=paste-the-key-here
```

**Store a copy somewhere safe** — a password manager or your team's secret
store. Without it the database cannot be read, and the only way to rebuild is
from the original spreadsheet.

### 2. Build the database

```powershell
.\venv\Scripts\python.exe tools\build_facility_db.py --excel "C:\Users\AS222\OneDrive - Annova Solutions\Desktop\Simba_Topic_Aware_Retrieval_Complete\Facility Data.xlsx"
```

Output:

```
Reading ...Facility Data.xlsx ...
   22282 records, columns: ['Srl No', 'Facility Name', ...]
✅ Wrote ...\database\facilities.enc (0.79 MB, encrypted)
✅ Verified: 22282 records decrypt correctly.
```

It reads the file back immediately, so a bad write is caught here rather than
in production.

### 3. Remove the spreadsheet from the project

```powershell
Remove-Item ".\Facility Data.xlsx" -ErrorAction SilentlyContinue
```

Keep the original outside the repo — it is the only way to rebuild.

### 4. Restart and confirm

```powershell
docker compose restart app
docker compose logs app | Select-String "facility"
```

Expect:

```
✅ Loaded 22282 facility records from encrypted database
```

If it says *"from Excel file"*, the encrypted database was not found and the
spreadsheet is still being used.

---

## What ships to a deployment

Include `database/facilities.enc`. Do **not** include the `.xlsx`, and do not
put `FACILITY_DB_KEY` in the repo — deliver it separately (secret store, or a
message on a different channel from the code).

`.dockerignore` now excludes `*.xlsx` and `*.xls`, so the spreadsheet cannot
be baked into an image by accident even if it is sitting in the folder.

---

## Updating the data later

When the facility list changes, rebuild on a machine that has the new
spreadsheet and the key:

```powershell
.\venv\Scripts\python.exe tools\build_facility_db.py --excel "C:\path\to\updated.xlsx"
docker compose restart app
```

Ship the new `.enc`. The key does not change unless you want it to.

To confirm a database without touching Excel:

```powershell
.\venv\Scripts\python.exe tools\build_facility_db.py --verify
```

---

## What the encryption gives you

The file is `Fernet(gzip(CSV))`. Fernet is AES-128-CBC with an HMAC-SHA256
tag, from the `cryptography` library.

Verified behaviour:

| Check | Result |
|---|---|
| Facility names visible in the raw file | no |
| Decrypted output identical to Excel, cell for cell | yes (22,282 rows) |
| Wrong key | refuses to load, clear error |
| One byte flipped in the file | refuses to load (HMAC catches it) |
| Endpoints return identical JSON from either source | yes |
| Decrypt + load at startup | 0.30 s |
| Search latency | unchanged |
| File size | 1.47 MB xlsx → 0.79 MB encrypted |

Decryption happens **in memory only**. Nothing plaintext is written to disk,
so there is no temp file to clean up or leak.

`facility_store.to_sqlite()` is also available if you would rather query the
records with SQL than filter a DataFrame; it builds an in-memory SQLite
database with an index on `Facility Name`. Nothing currently uses it — the
existing endpoints were left alone deliberately.

---

## Honest limits

This protects the data **at rest**: in the repo, in a backup, on a laptop, in
an image layer. It is not protection against someone who already has both the
key and the running server — the app must decrypt to serve searches, so an
administrator on that machine can read the records.

For stronger separation you would move the facility data to a real database
server with its own access control. That is a larger change and, for a
read-only reference list served behind login, likely more than you need.

---

## Rollback

Restore `Facility Data.xlsx` to the project root and delete (or rename)
`database/facilities.enc`. The loader falls back automatically. No code
change and no restart flag needed beyond restarting the app.
