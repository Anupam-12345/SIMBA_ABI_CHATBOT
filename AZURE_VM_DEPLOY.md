> **Reply to IT first:** the two-container setup they asked for is already in
> place and has been since the first `docker compose up`. Evidence from your
> own terminal:
>
> ```
> NAME          IMAGE                  STATUS     PORTS
> sop-chatbot   cb-app                 healthy    0.0.0.0:8080->5000/tcp
> sop-ollama    ollama/ollama:latest   healthy    11434/tcp
>
> $ docker compose exec app printenv OLLAMA_BASE_URL
> http://ollama:11434
> ```
>
> Note that `sop-ollama` has **no** `0.0.0.0:` prefix on its port — Ollama is
> not published to the host at all, so the only thing that can reach it is the
> app container over the compose network. Local Ollama is stopped and the app
> still reports `Ollama OK - 2 model(s) available.`
>
> Nothing in the architecture needs to change for Azure. What follows is
> Linux-hosting hygiene.

---

# Deploying to a single Azure VM

## Code changes required

Four changes. Two are correctness, two are hardening.

### 1. `.gitattributes` — REQUIRED

Add the supplied `.gitattributes` to the repository root and commit it.

When you ran `git add`, Git printed this for every text file:

```
warning: in the working copy of 'docker-entrypoint.sh',
         LF will be replaced by CRLF the next time Git touches it
```

Git stores LF in the repository, so a Linux clone *should* be fine today. But
`docker-entrypoint.sh` is the container's `ENTRYPOINT`. If CRLF ever reaches
it — someone edits it on Windows with a different `core.autocrlf`, or the
file is copied rather than cloned — Linux fails with:

```
/usr/bin/env: 'bash\r': No such file or directory
```

The container then never starts, and the error gives no hint about line
endings. `.gitattributes` pins `.sh`, `Dockerfile` and `.yml` to LF
permanently. Cheap insurance against a failure that is very hard to diagnose.

```powershell
git add .gitattributes
git commit -m "Pin shell scripts and Dockerfile to LF line endings"
git push
```

### 2. `INSTALL_IMAGE_TOOLS=true` in `.env` — REQUIRED

This is the one that will silently degrade the product if missed.

89 of the 99 images in `West.docx` are EMF, which no browser renders. The
indexer converts them to PNG. On Windows, Pillow does that through the
Windows GDI. **On Linux that path does not exist** — the code falls back to
LibreOffice, which is only installed when this flag is `true` at image build
time.

Build with the default `false` and the chatbot works perfectly except that
**every SOP image is missing**, with nothing in the logs to say so.

Adds about 400 MB to the image and makes indexing slower. Worth it.

*Alternative:* keep `false`, build the index on a Windows machine, and copy
`vectorstore/` onto the VM. Leaner image, but a manual step every time the
SOPs change. I would use `true` on a server.

### 3. Do not publish the app port directly — recommended

In `docker-compose.yml`, change:

```yaml
    ports:
      - "${APP_PORT:-5000}:5000"
```

to:

```yaml
    ports:
      - "127.0.0.1:${APP_PORT:-5000}:5000"
```

Without the `127.0.0.1:` prefix, Docker publishes on all interfaces **and
writes its own iptables rules that bypass some host firewall configuration**.
On a cloud VM that can expose plain HTTP to the internet even when you think
a firewall is blocking it. With the prefix, only Caddy (below) can reach the
app.

Skip this only if you are not using a reverse proxy.

### 4. `docker-compose.prod.yml` + `Caddyfile` — recommended

Adds TLS, memory limits and log rotation. Copy both to the repository root.

Caddy obtains and renews a Let's Encrypt certificate automatically when
`SITE_ADDRESS` is a public DNS name. For an internal-only VM, set
`SITE_ADDRESS=:443` and add `tls internal` to the Caddyfile.

The memory limits matter: without them, a large Ollama model can consume all
the VM's RAM and the Linux OOM killer will terminate whichever process it
chooses — often the app. Log rotation matters because a long-running VM with
unbounded JSON logs will eventually fill its disk.

### Nothing else changes

`app.py`, `config.py`, `ollama_client.py`, `retrieval/retriever.py`,
`ingestion/build_index.py`, `facility_store.py` and `serve.py` are all
platform-neutral. `facility_store` uses `os.path.join`; `build_index` uses
`pathlib` and already has the LibreOffice branch; `serve.py` runs waitress,
which is cross-platform.

---

## VM sizing

CPU-only inference, no GPU. `Standard_D4s_v5` (4 vCPU, 16 GB RAM) is a
sensible starting point:

| Consumer | Memory |
|---|---|
| Ollama + `qwen2.5:1.5b` | ~2–3 GB |
| App: Chroma, two BM25 indexes, 22k facility records | ~1–2 GB |
| OS, Docker, headroom | ~2 GB |

Disk: 64 GB Premium SSD. The image is roughly 2 GB (3 GB with LibreOffice),
models 1.2 GB, index and SOP images under 1 GB.

Only move up to `D8s_v5` if you adopt a 7B model. Most answers now take the
verbatim path and never call the LLM at all, so measure before paying for
more. Check the Azure pricing calculator for current rates in your region.

---

## Deployment

### 1. Create the VM

Ubuntu 22.04 LTS or 24.04 LTS, `Standard_D4s_v5`, SSH key authentication.

**Network security group:** allow 22 (SSH, ideally from your office IP only),
80 and 443. **Do not open 8080 or 11434.**

### 2. Install Docker

```bash
ssh azureuser@<vm-ip>

curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
newgrp docker

sudo systemctl enable docker    # survives a VM reboot
docker --version
docker compose version
```

### 3. Clone

```bash
cd ~
git clone https://github.com/Anupam-12345/SIMBA_ABI_CHATBOT.git
cd SIMBA_ABI_CHATBOT

# Confirm the entrypoint is LF, not CRLF. Expect: ASCII text executable
file docker-entrypoint.sh
```

If that says `with CRLF line terminators`, fix it before going further:

```bash
sudo apt-get install -y dos2unix && dos2unix docker-entrypoint.sh
```

### 4. Configure

```bash
cp .env.example .env
nano .env
```

```
SECRET_KEY=<from your password manager>
FACILITY_DB_KEY=<from your password manager>
APP_PORT=8080
SITE_ADDRESS=sop-chatbot.yourcompany.com
SESSION_COOKIE_SECURE=1
INSTALL_IMAGE_TOOLS=true

AZURE_TENANT_ID=...
AZURE_CLIENT_ID=...
AZURE_CLIENT_SECRET=...
AZURE_REDIRECT_URI=https://sop-chatbot.yourcompany.com/login/azure/callback
```

```bash
chmod 600 .env
```

`AZURE_REDIRECT_URI` must match a Redirect URI registered in the Azure app
registration **exactly**, including `https` and any trailing path. Add it in
the portal before testing login.

### 5. Apply change 3

```bash
nano docker-compose.yml
# change  - "${APP_PORT:-5000}:5000"
# to      - "127.0.0.1:${APP_PORT:-5000}:5000"
```

### 6. Start

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose logs -f app
```

First start takes 20–40 minutes: image build with LibreOffice, model
downloads, and the index build with EMF conversion. Later starts take
seconds.

Ready when you see:

```
Loaded 22282 facility records from encrypted database
Retriever warmed up.
Ollama OK - 2 model(s) available.
Serving on http://0.0.0.0:5000 with 8 threads.
```

### 7. Verify

```bash
# both containers up, ollama NOT published to the host
docker compose ps

# index built, and images survived the EMF conversion
docker compose exec app python -c "import json;p=json.load(open('vectorstore/parentstore.json',encoding='utf-8'));print(len(p),'topics;',sum(1 for x in p if x.get('image_paths')),'with images')"
```

The second number is the one to watch. **If it prints `0 with images`,
`INSTALL_IMAGE_TOOLS` was not `true` when the image was built.** Fix it and
rebuild:

```bash
docker compose build --no-cache app
docker compose exec app python -m ingestion.build_index
```

Then browse to `https://sop-chatbot.yourcompany.com`, log in, and ask a
question whose answer has a picture — "29. FEES" or "no breakdown".

---

## Operations

```bash
docker compose logs -f app                  # follow
docker compose restart app                  # after an .env change
docker compose pull && docker compose up -d # update images

# deploy new code
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

# rebuild the index after editing docs/
docker compose exec app python -m ingestion.build_index
docker compose restart app
```

**Back up these three, on a schedule:**

```bash
tar czf backup-$(date +%F).tgz database/ .env
docker run --rm -v cb_vectorstore:/v -v $PWD:/b alpine \
    tar czf /b/vectorstore-$(date +%F).tgz -C /v .
```

`database/` holds user accounts, chat history and `facilities.enc`. `.env`
holds the keys. `vectorstore` is rebuildable from `docs/`, so it is
convenience rather than necessity. Azure Backup on the VM disk covers all of
it if you prefer.

**Enable unattended security updates:**

```bash
sudo apt-get install -y unattended-upgrades
sudo dpkg-reconfigure --priority=low unattended-upgrades
```

**Docker starts on boot** thanks to `systemctl enable docker`, and both
services use `restart: unless-stopped`, so the stack comes back after a VM
reboot without intervention.

---

## Known limits of this setup

**One VM is a single point of failure.** Fine for an internal tool; not a
highly available architecture. Azure Backup and a documented rebuild is the
proportionate mitigation.

**SQLite constrains scaling.** `auth.db` and `chat_history.db` are files. One
container writing to them is safe; two would corrupt them. If you later need
multiple app replicas, those must move to Azure Database for PostgreSQL
first. Not a concern at your current scale.

**CPU inference is slower than GPU.** Measure before spending: the verbatim
path skips the LLM entirely for structured procedures, which is most of your
traffic.
