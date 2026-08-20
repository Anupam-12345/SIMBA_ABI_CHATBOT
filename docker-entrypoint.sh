#!/usr/bin/env bash
set -euo pipefail

OLLAMA_URL="${OLLAMA_BASE_URL:-http://ollama:11434}"
EMBED="${EMBED_MODEL:-nomic-embed-text}"
LLM="${LLM_MODEL:-qwen2.5:1.5b}"

echo "==> Waiting for Ollama at ${OLLAMA_URL}"
for i in $(seq 1 60); do
  if curl -fsS "${OLLAMA_URL}/api/tags" >/dev/null 2>&1; then
    echo "    Ollama is up."
    break
  fi
  [ "$i" = "60" ] && { echo "    ERROR: Ollama did not become ready."; exit 1; }
  sleep 2
done

pull_if_missing() {
  local model="$1"
  if curl -fsS "${OLLAMA_URL}/api/tags" | grep -q "\"${model%%:*}"; then
    echo "    Model ${model} already present."
  else
    echo "==> Pulling ${model} (first run only, this can take a few minutes)"
    curl -fsS -X POST "${OLLAMA_URL}/api/pull" \
      -H 'Content-Type: application/json' \
      -d "{\"name\":\"${model}\"}" | tail -c 200 || true
    echo
  fi
}

pull_if_missing "${EMBED}"
pull_if_missing "${LLM}"

if [ ! -f /app/vectorstore/docstore.json ] || [ "${REBUILD_INDEX:-0}" = "1" ]; then
  echo "==> Building the index (REBUILD_INDEX=${REBUILD_INDEX:-0})"
  python -m ingestion.build_index || {
    echo "    ERROR: index build failed. The app will start but cannot answer."
  }
else
  echo "==> Existing index found, skipping build. Set REBUILD_INDEX=1 to force."
fi

echo "==> Starting server"
exec python serve.py
