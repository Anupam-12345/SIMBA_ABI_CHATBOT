# SOP Chatbot — Local RAG over Ollama

A working starter system that answers employee questions from your SOP
documents, correctly distinguishing between SOPs that share identical
headers but differ in content (Central, Choice Legal, Combine, Hartford,
Liberty, Risk, Southeast, West, West_FAQ).

## What this solves

Your core problem — "many SOPs use the same section names but mean
different things" — is solved by three things working together:

1. **Header-aware chunking**: every chunk is tagged with its document name,
   header, and sub-header, and that label is embedded *inside* the chunk
   text itself. So the embedding for "Exemption Rules" under Hartford looks
   different from "Exemption Rules" under Liberty, because the surrounding
   content differs.
2. **Hybrid retrieval (vector + BM25)**: vector search catches paraphrased/
   semantic matches, BM25 catches exact terms (form numbers, client names)
   that embeddings can blur. Results are merged with Reciprocal Rank Fusion.
3. **Metadata filtering**: if a user names a client/SOP in their question
   ("according to Hartford..."), retrieval is restricted to that document
   only — no cross-contamination.

## 1. Setup

```bash
cd sop_chatbot
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
```

Make sure Ollama is running and you have the models pulled:
```bash
ollama pull nomic-embed-text
ollama pull mistral:7b-instruct
```

## 2. Add your SOPs

Copy your actual files into `docs/`:
```
docs/Central.docx
docs/Choice Legal.docx
docs/Combine.docx
docs/Hartford.docx
docs/Liberty.docx
docs/Risk.docx
docs/Southeast.docx
docs/West.pdf
docs/West_FAQ.docx
```

## 3. Build the index

```bash
python -m ingestion.build_index
```

This reads every file, chunks it, embeds every chunk via Ollama, and writes
the index into `vectorstore/`. Re-run this any time documents change (or
hit the `/rebuild_index` endpoint from the running app).

## 4. Run the chatbot

```bash
python app.py
```

Open `http://localhost:5000` in your browser.

## Key decisions and why

| Component | Choice | Why |
|---|---|---|
| Embeddings | `nomic-embed-text` | Already installed, solid general quality, fast on CPU. Upgrade path: `bge-large-en-v1.5` if you need more accuracy and don't mind slower indexing. |
| LLM | `mistral:7b-instruct` | Of your installed models, it follows strict "answer only from context" instructions most reliably. `gemma3:4b` is faster but looser about instructions; `llama2` hallucinates more on narrow domain Q&A; `tinyllama`/`phi` are too small for this task. |
| Vector DB | ChromaDB | Zero external services, file-based persistence, trivial to set up — right for a single-server internal tool. Move to **Qdrant** if you outgrow a few hundred thousand chunks or need multi-user concurrent writes. |
| Chunk size | 700 tokens, 150 overlap | Big enough to keep a rule and its conditions together, small enough that one chunk's embedding represents one idea, not three. |
| Retrieval | Hybrid (vector + BM25) via RRF | Single best lever against your "similar headers, different content" problem, more impactful than swapping LLMs. |

## What's intentionally *not* built yet (and how to add it)

Building all of these fully today would have meant a huge volume of
unverified code. Here's the practical order to add them as you grow:

1. **Reranking (bge-reranker / cross-encoder)** — add once you notice the
   top-5 retrieved chunks sometimes include an irrelevant one. Pull a
   cross-encoder reranker via `sentence-transformers`, re-score the
   `TOP_K_VECTOR`-sized candidate pool, and keep the new top-5.
2. **Query expansion / multi-query retrieval** — have the LLM generate 2-3
   paraphrased versions of the user's question first, retrieve for each,
   then fuse all result sets the same way `hybrid_retrieve` already does.
3. **Authentication & role-based SOP access** — wrap Flask with
   `Flask-Login` and add a `roles` table; filter `KNOWN_DOCUMENTS` per
   user role before retrieval.
4. **Evaluation (RAGAS)** — once you have ~20-30 real Q&A pairs with known
   correct answers, run them through RAGAS's faithfulness/context-precision
   metrics to catch regressions before they reach users.
5. **Deployment** — for an internal tool, the simplest path is: this Flask
   app behind IIS using `wfastcgi`, or containerized with Docker + a
   reverse proxy (nginx) if you want it portable across servers.
6. **Monitoring** — log every `/chat` call's latency, retrieved chunk
   confidence, and token count to the existing SQLite DB (a few columns
   added to `chat_history`) before reaching for a full dashboard tool.

## Folder structure

```
sop_chatbot/
├── app.py                  # Flask backend
├── config.py                # all tunable settings live here
├── ollama_client.py          # embeddings + generation calls
├── ingestion/
│   ├── readers.py             # docx/pdf -> header-tagged blocks
│   ├── chunker.py              # token-based chunking with overlap
│   └── build_index.py           # run this to (re)build the index
├── retrieval/
│   └── retriever.py             # hybrid vector + BM25 search
├── prompts/
│   └── system_prompt.py          # anti-hallucination system prompt
├── templates/index.html           # chat UI
├── static/{style.css, script.js}  # UI styling and behavior
├── docs/                            # put your SOP files here
├── vectorstore/                      # Chroma index + BM25 docstore
├── database/chat_history.db           # session history
└── requirements.txt
```
