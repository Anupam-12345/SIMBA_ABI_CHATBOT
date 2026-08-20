r"""
Server entrypoint for shared testing.

    .\venv\Scripts\python.exe serve.py

Differences from `python app.py`:

  * Uses waitress instead of the Flask development server. The dev server is
    single-threaded and prints a warning for a reason - with several testers
    it will queue requests and can drop connections.
  * debug=False. The Werkzeug debugger allows arbitrary code execution from
    the browser; it must never be reachable by anyone but you.
  * Warms the retriever before accepting traffic, so the first few testers
    don't race each other building the BM25 indexes.

Install once:
    .\venv\Scripts\python.exe -m pip install waitress
"""

import os
import sys

os.environ.setdefault("FLASK_ENV", "production")

HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "5000"))
THREADS = int(os.environ.get("SERVER_THREADS", "8"))


def main() -> int:
    if not os.environ.get("SECRET_KEY"):
        print("=" * 70)
        print("WARNING: SECRET_KEY is not set.")
        print("A random key is generated at startup, so every restart logs")
        print("all your testers out. Set it once and keep it:")
        print('    setx SECRET_KEY "<a long random string>"')
        print("Then open a NEW terminal before starting the server.")
        print("=" * 70)

    import app as application

    application.init_db()
    application.init_auth_db()

    # Warm the retriever: loads Chroma, docstore, parentstore and builds both
    # BM25 indexes now rather than inside the first user's request.
    try:
        from retrieval import retriever
        retriever.hybrid_retrieve("warm up", top_k_final=1)
        print("Retriever warmed up.")
    except Exception as exc:
        print(f"WARNING: retriever warm-up failed: {exc}")
        print("The index may not be built. Run: python -m ingestion.build_index")

    # Confirm Ollama is reachable - testers get useless answers if it is not.
    try:
        import requests
        import config
        resp = requests.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=5)
        names = [m.get("name") for m in resp.json().get("models", [])]
        print(f"Ollama OK - {len(names)} model(s) available.")
        if not any((config.LLM_MODEL or "") in (n or "") for n in names):
            print(f"WARNING: configured model {config.LLM_MODEL} is not installed.")
    except Exception as exc:
        print(f"WARNING: cannot reach Ollama at {os.environ.get('OLLAMA_BASE_URL', 'http://localhost:11434')}: {exc}")

    try:
        from waitress import serve
    except ImportError:
        print("waitress is not installed. Run:")
        print("    .\\venv\\Scripts\\python.exe -m pip install waitress")
        return 1

    print(f"\nServing on http://{HOST}:{PORT} with {THREADS} threads. Ctrl+C to stop.\n")
    serve(application.app, host=HOST, port=PORT, threads=THREADS,
          channel_timeout=180, ident="SOP-Chatbot")
    return 0


if __name__ == "__main__":
    sys.exit(main())
