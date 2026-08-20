# config.py
import os

# Load .env before anything reads os.environ. app.py already calls this, but
# `python -m ingestion.build_index` imports config directly, so without it the
# indexer would silently ignore every .env setting.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
VECTORSTORE_DIR = os.path.join(BASE_DIR, "vectorstore")
DOCS_DIR = os.path.join(BASE_DIR, "docs")

# Ollama Configuration
OLLAMA_BASE_URL = os.environ.get('OLLAMA_BASE_URL', 'http://localhost:11434')

# ============================================================
# MODEL CONFIGURATION
# ============================================================

# Embedding model - for semantic search
EMBED_MODEL = os.environ.get('EMBED_MODEL', 'nomic-embed-text')

# LLM Model for response generation
# Options: deepseek-r1:1.5b, qwen2.5:1.5b, smollm2:1.7b
LLM_MODEL = os.environ.get('LLM_MODEL', 'qwen2.5:1.5b')

# Set to True to use LLM for response generation
USE_LLM = True  # Change to False for direct chunks only

# Model parameters
MAX_RESPONSE_TOKENS = 1800   # was 6000: reserved most of num_ctx for output
CONTEXT_WINDOW_TOKENS = 8192
TEMPERATURE = 0.0

# Confidence thresholds
CONFIDENCE_HIGH = 0.7
CONFIDENCE_MEDIUM = 0.4

# Timeouts
OLLAMA_TIMEOUT = 60
OLLAMA_CONNECT_TIMEOUT = 10

# Retrieval settings
TOP_K_FINAL = 6
TOP_K_VECTOR = 20
TOP_K_BM25 = 20
RRF_K = 60

# Vector store settings
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100

# Relevance scoring
MAX_RELEVANT_CHUNKS = 6
# Topic-aware indexing and image display
SOP_IMAGE_DIR = os.environ.get('SOP_IMAGE_DIR', os.path.join(VECTORSTORE_DIR, 'sop_images'))
TARGET_CHUNK_CHARS = int(os.environ.get('TARGET_CHUNK_CHARS', '2200'))
MAX_CHUNK_CHARS = int(os.environ.get('MAX_CHUNK_CHARS', '3500'))
MIN_CHUNK_CHARS = int(os.environ.get('MIN_CHUNK_CHARS', '200'))
CHUNK_OVERLAP_CHARS = int(os.environ.get('CHUNK_OVERLAP_CHARS', '0'))
MAX_PARENT_CHARS = int(os.environ.get('MAX_PARENT_CHARS', '4000'))
MAX_IMAGES_PER_ANSWER = int(os.environ.get('MAX_IMAGES_PER_ANSWER', '6'))

# ============================================================
# ACCURACY CONTROLS  (added — safe defaults, nothing removed)
# ============================================================

# Ingestion: headings at this level or shallower start a new topic.
# Level 1 = document title, 2 = numbered section ("20. ROI'S"),
# 3 = numbered subsection ("2.10 Offsite Processing"),
# 4 = bold in-section label ("Step 9 - ...", "Required Documents").
# Level 3 keeps a whole procedure in one topic.
PARENT_TOPIC_LEVEL = int(os.environ.get('PARENT_TOPIC_LEVEL', '3'))
STRIP_MARKDOWN_INLINE = os.environ.get('STRIP_MARKDOWN_INLINE', '1') == '1'

# Retrieval / region isolation
KNOWN_DOCUMENTS = []          # auto-filled from docstore.json at load time
DOCUMENT_ALIASES = {
    'hartford': ['Hartford'],
    'liberty': ['Liberty'],
    'risk': ['Risk'],
    'central': ['Central'],
    'southeast': ['Southeast'],
    'south east': ['Southeast'],
    'choice legal': ['Choice Legal'],
    'choice': ['Choice Legal'],
    'west': ['West', 'West_FAQ'],
    'west region': ['West', 'West_FAQ'],
    'west faq': ['West_FAQ'],
}
AMBIGUOUS_ALIASES = {'risk', 'central', 'choice'}
REGION_CONTEXT_WORDS = ('region', 'sop', 'client', 'team', 'account',
                        'procedure', 'procedures', 'manual', 'taskbook')

MIN_VECTOR_SCORE = float(os.environ.get('MIN_VECTOR_SCORE', '0.35'))
MIN_ANSWER_CONFIDENCE = float(os.environ.get('MIN_ANSWER_CONFIDENCE', '0.30'))
SINGLE_DOCUMENT_CONTEXT = os.environ.get('SINGLE_DOCUMENT_CONTEXT', '1') == '1'

# Context budget
MAX_CONTEXT_CHARS = int(os.environ.get('MAX_CONTEXT_CHARS', '12000'))
MAX_PARENT_INLINE_CHARS = int(os.environ.get('MAX_PARENT_INLINE_CHARS', '4500'))
MIN_TOPIC_CONTEXT_CHARS = int(os.environ.get('MIN_TOPIC_CONTEXT_CHARS', '400'))

# Generation
GROUNDING_THRESHOLD = float(os.environ.get('GROUNDING_THRESHOLD', '0.40'))
LLM_FALLBACK_MODELS = ['qwen2.5:7b-instruct', 'qwen2.5:3b', 'qwen2.5:1.5b', 'mistral:7b-instruct']
# Serve numbered/bulleted procedures verbatim instead of letting a small model
# paraphrase them. Set to '0' to route everything through the LLM.
VERBATIM_PROCEDURE_ANSWERS = os.environ.get('VERBATIM_PROCEDURE_ANSWERS', '1') == '1'

# Images: show only pictures belonging to the topic the answer came from.
IMAGES_FROM_ANSWER_ONLY = os.environ.get('IMAGES_FROM_ANSWER_ONLY', '1') == '1'
# Rasterisation quality when converting Word EMF/WMF images to PNG.
IMAGE_RASTER_DPI = int(os.environ.get('IMAGE_RASTER_DPI', '200'))

# How far ahead the top result must be before the answer is locked to its SOP.
# 0 = always lock (a single bad top-1 then discards every other document).
DOCUMENT_LOCK_MARGIN = float(os.environ.get('DOCUMENT_LOCK_MARGIN', '0.10'))

# Heading-search terms appearing in more than this share of headings are
# ignored as non-selective ("is", "needed", "region").
HEADING_TERM_DF_CEILING = float(os.environ.get('HEADING_TERM_DF_CEILING', '0.12'))
