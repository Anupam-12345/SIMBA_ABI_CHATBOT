# Running the SOP chatbot in Docker

Two containers: your Flask app, and Ollama. They talk over a private compose
network, so the app reaches Ollama at `http://ollama:11434` and you no longer
need Ollama installed on the host.

Everything is configured through a single `.env` file.

---

## One-time setup

Copy these files into your project root (`CB/`):

```
Dockerfile
docker-compose.yml
docker-entrypoint.sh
requirements.txt          (overwrites yours - adds Pillow and waitress)
.dockerignore
.env.example
serve.py
```

Then:

```powershell
cd "C:\...\Simba_Topic_Aware_Retrieval_Complete\CB"

copy .env.example .env

# generate a secret and paste it into SECRET_KEY in .env
python -c "import secrets;print(secrets.token_hex(32))"

notepad .env
```

At minimum set `SECRET_KEY`. Everything else has a working default.

---

## Start it

```powershell
docker compose up -d --build
docker compose logs -f app
```

First run takes a while: it pulls the base image, installs dependencies,
downloads `nomic-embed-text` and `qwen2.5:1.5b` into the Ollama volume, and
builds the index. Expect 10–20 minutes. Later starts take seconds.

Watch for these lines:

```
==> Waiting for Ollama at http://ollama:11434
    Ollama is up.
==> Pulling nomic-embed-text (first run only...)
==> Building the index (REBUILD_INDEX=0)
    Index built successfully
==> Starting server
Serving on http://0.0.0.0:5000 with 8 threads.
```

Open `http://localhost:8080` (the port set by `APP_PORT` in `.env`).

To share with testers on your network, give them
`http://<your-machine-ip>:8080`. Find it with `ipconfig`.

---

## Important: Word EMF images

89 of the 99 images in West.docx are EMF, which browsers cannot render. The
indexer converts them to PNG, but **Pillow can only do that on Windows** —
it needs the Windows GDI. Inside a Linux container the conversion falls back
to LibreOffice.

You have two options.

### Option A — install LibreOffice in the image (self-contained)

In `.env`:

```
INSTALL_IMAGE_TOOLS=true
```

Then `docker compose build --no-cache app`. Adds roughly 400 MB to the image,
and indexing gets slower because each EMF spawns a LibreOffice process. Every
rebuild works entirely inside Docker.

### Option B — build the index on Windows, copy it in (smaller, faster)

Keep `INSTALL_IMAGE_TOOLS=false`. Build the index on Windows where Pillow
converts EMF natively, then copy the result into the container's volume:

```powershell
.\venv\Scripts\python.exe -m ingestion.build_index

docker compose up -d
docker cp .\vectorstore\. sop-chatbot:/app/vectorstore/
docker compose restart app
```

Repeat whenever the SOPs change. Option A is less work; Option B gives a
leaner image and faster indexing.

---

## Everyday commands

```powershell
docker compose up -d              # start
docker compose down               # stop (keeps data)
docker compose logs -f app        # follow the app log
docker compose restart app        # restart after an .env change
docker compose up -d --build      # rebuild after a code change
```

Rebuild the index after editing SOPs in `docs/`:

```powershell
docker compose exec app python -m ingestion.build_index
docker compose restart app
```

Or set `REBUILD_INDEX=1` in `.env` and `docker compose up -d`, then set it
back to `0` so it doesn't rebuild on every restart.

Check the index:

```powershell
docker compose exec app python -c "import json;p=json.load(open('vectorstore/parentstore.json',encoding='utf-8'));print(len(p),'topics;',sum(1 for x in p if x.get('image_paths')),'with images')"
```

Export test results:

```powershell
docker compose exec app python -c "import sqlite3,csv;c=sqlite3.connect('database/chat_history.db');r=c.execute('SELECT created_at,session_id,role,content,confidence FROM chat_history ORDER BY id').fetchall();w=csv.writer(open('/app/database/test_results.csv','w',newline='',encoding='utf-8'));w.writerow(['created_at','session','role','content','confidence']);w.writerows(r);print(len(r),'rows')"
```

It lands in your host `database/` folder, since that directory is mounted.

---

## What persists

| Data | Where | Survives `docker compose down`? |
|---|---|---|
| SOP documents | host `./docs` | yes |
| Chat history, user accounts | host `./database` | yes |
| Index + extracted images | named volume `vectorstore` | yes |
| Ollama models | named volume `ollama_models` | yes |

`docker compose down -v` deletes the named volumes — the index and the
downloaded models. Use plain `down` unless you mean it.

---

## Azure AD

If testers log in with Azure, `AZURE_REDIRECT_URI` in `.env` must exactly
match a Redirect URI registered in the Azure portal, using the URL testers
actually visit:

```
AZURE_REDIRECT_URI=http://10.20.30.41:8080/login/azure/callback
```

For local accounts only, leave the four `AZURE_*` values blank.

---

## Notes

**Performance.** Ollama in Docker is CPU-only unless you configure GPU
passthrough. Most answers now come from the verbatim path and skip the LLM,
so this affects only prose-style questions. For GPU on Linux hosts, add a
`deploy.resources.reservations.devices` block to the `ollama` service; on
Windows this needs WSL2 with the NVIDIA container toolkit.

**Memory.** Give Docker Desktop at least 8 GB (Settings → Resources).
`qwen2.5:1.5b` plus Chroma plus two BM25 indexes fits comfortably; a 7B model
would want 12 GB or more.

**HTTPS.** The container serves plain HTTP. For an HTTPS URL, put a reverse
proxy in front (Caddy or nginx) or run a Cloudflare Tunnel against
`http://localhost:8080`, and set `SESSION_COOKIE_SECURE=1` in `.env`.

**Debug mode is off** in the container — `serve.py` runs waitress, not the
Flask development server. This matters: `app.py`'s `debug=True` would expose
a browser console capable of running arbitrary code.
