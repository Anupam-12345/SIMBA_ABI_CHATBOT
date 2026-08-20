# Topic-Aware Retrieval Upgrade

This package preserves the existing login, facility search, FAQ handling, document filter, chat history, Ollama generation and fallback behavior.

## Files changed

- `ingestion/build_index.py`: hierarchical topic/subtopic chunking, tables, parent-child records, neighboring chunks and image extraction.
- `retrieval/retriever.py`: existing BM25 + vector + RRF retrieval, with parent-topic expansion and image metadata.
- `app.py`: authenticated SOP image route, image URLs in chat responses, configurable context count, cache reset after rebuild.
- `templates/index.html`: renders related SOP images below the answer.
- `static/style.css`: image display styling.
- `config.py`: topic-aware chunk and image settings.
- `requirements.txt`: compatible dependency ranges.

## Installation

From the project folder:

```powershell
python -m pip install -r requirements.txt
python -m pip check
```

Keep Ollama running and confirm the embedding model exists:

```powershell
ollama list
ollama pull nomic-embed-text
```

## Rebuild

Back up the existing `vectorstore` folder, then run:

```powershell
python -m ingestion.build_index
```

The rebuild creates:

- `vectorstore/docstore.json`: searchable child chunks
- `vectorstore/parentstore.json`: complete topic sections
- `vectorstore/sop_images/`: extracted images
- Chroma collection `sop_chunks`

## Run

```powershell
python app.py
```

## Configuration

These optional environment variables can be placed in `.env`:

```env
TARGET_CHUNK_CHARS=1200
MAX_CHUNK_CHARS=1800
MIN_CHUNK_CHARS=250
CHUNK_OVERLAP_CHARS=180
MAX_PARENT_CHARS=12000
MAX_IMAGES_PER_ANSWER=6
MAX_RELEVANT_CHUNKS=3
```

Do not increase `MAX_RELEVANT_CHUNKS` aggressively with a small local LLM. Three complete parent topics can already be a large prompt.
