"""
Backward-compatible topic-aware hybrid retrieval.

Preserves the existing public function:
    hybrid_retrieve(query, top_k_final=None, document_filter=None)

Enhancements:
- BM25 + vector retrieval with Reciprocal Rank Fusion.
- Topic-path metadata support while retaining header/sub_header/page aliases.
- Parent-topic expansion so the answer receives the complete topic.
- Neighbor expansion for procedures split across child chunks.
- Image metadata returned with retrieved results.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional

import chromadb
from rank_bm25 import BM25Okapi

import config
import ollama_client

_collection = None
_bm25 = None
_docstore: List[Dict[str, Any]] = []
_parentstore: Dict[str, Dict[str, Any]] = {}
_doc_by_id: Dict[str, Dict[str, Any]] = {}
_tokenized_corpus = None
_content_index = {}
_bm25_headings = None
_heading_corpus = None
_heading_df = {}


_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]*")
_SPLIT_RE = re.compile(r"[-_/]+")
_ALNUM_SPLIT_RE = re.compile(r"^([a-z]+)(\d+)$")


def _tokens(text: str) -> List[str]:
    """
    Tokenise for BM25.

    The previous pattern kept trailing punctuation inside the token, so the SOP
    text "QC6- There is an invoice..." produced the term "qc6-" while the query
    "QC6" produced "qc6". BM25 scored the correct chunk at zero. Compound terms
    such as "FACILITY-ORDER" had the same problem against "facility order".

    Each token is emitted in normalised form, plus its parts when it is
    compound, so both phrasings match. Dotted section numbers ("2.10") are kept
    intact for the section-number boost.
    """
    out: List[str] = []
    for raw in _TOKEN_RE.findall((text or "").lower()):
        token = raw.strip("./-_")
        if not token:
            continue
        out.append(token)

        parts = [part for part in _SPLIT_RE.split(token) if part]
        if len(parts) > 1:
            out.extend(parts)

        # "qc6" also indexes as "qc" + "6" so "QC 6" and "QC6" both match.
        alnum = _ALNUM_SPLIT_RE.match(token)
        if alnum and len(token) >= 3:
            out.extend(alnum.groups())
    return out


def _heading_text(chunk: Dict[str, Any]) -> str:
    """Searchable heading for a chunk: its topic path, header and document."""
    return " ".join(str(chunk.get(field, "") or "") for field in
                    ("topic_path", "header", "topic", "sub_header", "document_name"))


def _content_hash(text: str) -> str:
    return hashlib.md5(" ".join((text or "").lower().split()).encode("utf-8")).hexdigest()


def _load() -> None:
    global _collection, _bm25, _docstore, _parentstore, _doc_by_id, _tokenized_corpus, _content_index, _bm25_headings, _heading_corpus, _heading_df

    if _collection is None:
        client = chromadb.PersistentClient(path=config.VECTORSTORE_DIR)
        _collection = client.get_collection("sop_chunks")

    if _bm25 is not None:
        return

    docstore_path = os.path.join(config.VECTORSTORE_DIR, "docstore.json")
    if not os.path.exists(docstore_path):
        raise FileNotFoundError(
            f"docstore.json not found at {docstore_path}. "
            "Run: python -m ingestion.build_index"
        )

    with open(docstore_path, "r", encoding="utf-8") as handle:
        _docstore = json.load(handle)

    parentstore_path = os.path.join(config.VECTORSTORE_DIR, "parentstore.json")
    if os.path.exists(parentstore_path):
        with open(parentstore_path, "r", encoding="utf-8") as handle:
            parents = json.load(handle)
        _parentstore = {item["id"]: item for item in parents}
    else:
        _parentstore = {}

    _doc_by_id = {item["id"]: item for item in _docstore}
    _tokenized_corpus = [_tokens(item.get("text", "")) for item in _docstore]
    _bm25 = BM25Okapi(_tokenized_corpus)

    # A separate lexical index over headings only. Body BM25 rewards long
    # chunks that repeat a word ("Invoice" appears 15x in Southeast ROI
    # Baskets), which buries the section actually named after the term
    # ("31. NOTICES OF NON-COMPLIANCE", "11.2 Verify the Invoice").
    _heading_corpus = [_tokens(_heading_text(item)) for item in _docstore]
    _bm25_headings = BM25Okapi(_heading_corpus)

    _heading_df = {}
    for tokens in _heading_corpus:
        for term in set(tokens):
            _heading_df[term] = _heading_df.get(term, 0) + 1

    # Document list for region detection, derived from the index so it can
    # never drift out of sync with what was actually ingested.
    if not getattr(config, "KNOWN_DOCUMENTS", None):
        config.KNOWN_DOCUMENTS = sorted(
            {item.get("document_name", "") for item in _docstore if item.get("document_name")}
        )

    # Hartford / Liberty / Risk share most of their text verbatim. Without this
    # map the same paragraph fills several context slots under different region
    # labels and the answer is attributed to an arbitrary one.
    _content_index = {}
    for item in _docstore:
        digest = item.get("content_hash") or _content_hash(item.get("raw_text") or item.get("text", ""))
        item["content_hash"] = digest
        names = _content_index.setdefault(digest, [])
        name = item.get("document_name", "")
        if name and name not in names:
            names.append(name)


def _metadata_from_chunk(chunk: Dict[str, Any]) -> Dict[str, Any]:
    image_paths = chunk.get("image_paths", [])
    if not image_paths and chunk.get("image_paths_json"):
        try:
            image_paths = json.loads(chunk["image_paths_json"])
        except Exception:
            image_paths = []

    return {
        "document_name": chunk.get("document_name", "Unknown"),
        "header": chunk.get("header") or chunk.get("topic", "General"),
        "sub_header": chunk.get("sub_header") or chunk.get("parent_topic", ""),
        "topic": chunk.get("topic") or chunk.get("header", "General"),
        "parent_topic": chunk.get("parent_topic") or chunk.get("sub_header", ""),
        "topic_path": chunk.get("topic_path") or chunk.get("header", "General"),
        "page": chunk.get("page") or chunk.get("page_start", 0),
        "page_start": chunk.get("page_start", 0),
        "page_end": chunk.get("page_end", 0),
        "parent_id": chunk.get("parent_id", ""),
        "chunk_index": chunk.get("chunk_index", 0),
        "chunk_count": chunk.get("chunk_count", 1),
        "previous_chunk_id": chunk.get("previous_chunk_id", ""),
        "next_chunk_id": chunk.get("next_chunk_id", ""),
        "has_images": bool(chunk.get("has_images") or image_paths),
        "image_paths": image_paths,
    }


def _normalise_filter(document_filter):
    if not document_filter:
        return None
    if isinstance(document_filter, str):
        document_filter = [document_filter]
    cleaned = [name for name in document_filter if name]
    return cleaned or None


def detect_document_filters(query):
    """
    Return the SOP documents a query explicitly names, or None.

    The previous version read config.KNOWN_DOCUMENTS, which did not exist, so
    getattr returned [] and NO region filter was ever applied to any query.
    """
    _load()
    q_lower = (query or "").lower()
    known = list(getattr(config, "KNOWN_DOCUMENTS", []) or [])
    if not known:
        return None

    aliases = {alias.lower(): list(docs)
               for alias, docs in (getattr(config, "DOCUMENT_ALIASES", {}) or {}).items()}
    for name in known:
        aliases.setdefault(name.lower(), [name])

    ambiguous = {a.lower() for a in (getattr(config, "AMBIGUOUS_ALIASES", set()) or set())}
    context_words = tuple(getattr(config, "REGION_CONTEXT_WORDS", ()) or ())

    matched = []
    for alias in sorted(aliases, key=len, reverse=True):
        if not re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", q_lower):
            continue
        if alias in ambiguous and not any(word in q_lower for word in context_words):
            continue
        for document in aliases[alias]:
            if document in known and document not in matched:
                matched.append(document)
    return matched or None


def detect_document_filter(query):
    """Backward-compatible single-value wrapper."""
    matches = detect_document_filters(query)
    return matches[0] if matches else None

def vector_search(query: str, top_k: int, document_filter: Optional[str] = None):
    _load()
    query_vec = ollama_client.embed(query)
    names = _normalise_filter(document_filter)
    if not names:
        where = None
    elif len(names) == 1:
        where = {"document_name": names[0]}
    else:
        where = {"document_name": {"$in": names}}
    results = _collection.query(
        query_embeddings=[query_vec],
        n_results=min(top_k, max(1, len(_docstore))),
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    output = []
    for cid, text, metadata, distance in zip(
        results["ids"][0],
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        stored = _doc_by_id.get(cid, {})
        merged = dict(stored)
        merged.update(metadata or {})
        output.append({
            "id": cid,
            "text": text,
            "metadata": _metadata_from_chunk(merged),
            "vector_score": max(0.0, min(1.0, 1.0 - float(distance))),
        })
    return output


def bm25_search(query: str, top_k: int, document_filter: Optional[str] = None):
    _load()
    scores = _bm25.get_scores(_tokens(query))
    names = _normalise_filter(document_filter)
    allowed = set(names) if names else None
    ranked = sorted(range(len(scores)), key=lambda idx: scores[idx], reverse=True)

    output = []
    for idx in ranked:
        chunk = _docstore[idx]
        if allowed and chunk.get("document_name") not in allowed:
            continue
        output.append({
            "id": chunk["id"],
            "text": chunk["text"],
            "metadata": _metadata_from_chunk(chunk),
            "bm25_score": float(scores[idx]),
        })
        if len(output) >= top_k:
            break
    return output


def reciprocal_rank_fusion(result_lists, k: int = 60):
    fused_scores: Dict[str, float] = {}
    item_lookup: Dict[str, Dict[str, Any]] = {}

    for results in result_lists:
        for rank, item in enumerate(results):
            cid = item["id"]
            existing = item_lookup.get(cid, {})
            merged = dict(existing)
            merged.update(item)
            item_lookup[cid] = merged
            fused_scores[cid] = fused_scores.get(cid, 0.0) + 1.0 / (k + rank + 1)

    ranked_ids = sorted(fused_scores, key=fused_scores.get, reverse=True)
    output = []
    for cid in ranked_ids:
        item = dict(item_lookup[cid])
        item["fused_score"] = fused_scores[cid]
        output.append(item)
    return output


def _expanded_text(item: Dict[str, Any]) -> str:
    """Return complete parent topic when available; otherwise include neighbors."""
    _load()
    metadata = item["metadata"]
    parent_id = metadata.get("parent_id")

    if parent_id and parent_id in _parentstore:
        parent = _parentstore[parent_id]
        parent_text = parent.get("text", "").strip()
        if parent_text:
            return (
                f"Document: {metadata['document_name']}\n"
                f"Topic: {metadata.get('topic_path') or metadata.get('header')}\n\n"
                f"{parent_text}"
            )

    texts = []
    for neighbor_id in (
        metadata.get("previous_chunk_id"),
        item.get("id"),
        metadata.get("next_chunk_id"),
    ):
        chunk = _doc_by_id.get(neighbor_id or "")
        if chunk:
            raw = chunk.get("raw_text") or chunk.get("text", "")
            if raw and raw not in texts:
                texts.append(raw)

    return "\n\n".join(texts) if texts else item.get("text", "")


def heading_search(query: str, top_k: int, document_filter=None):
    """Lexical search over headings only. Zero-scoring chunks are excluded so
    they cannot earn rank credit in the fusion step."""
    _load()
    # Drop terms that appear in a large share of headings ("is", "needed",
    # "region"). They carry no selectivity and would surface headings like
    # "EXAMPLES WHEN A STATUS LETTER IS NEEDED" for the query "notary is needed".
    total = max(1, len(_heading_corpus))
    ceiling = float(getattr(config, "HEADING_TERM_DF_CEILING", 0.12)) * total
    terms = [term for term in _tokens(query) if _heading_df.get(term, 0) <= ceiling]
    if not terms:
        return []

    scores = _bm25_headings.get_scores(terms)
    names = _normalise_filter(document_filter)
    allowed = set(names) if names else None

    ranked = sorted(range(len(scores)), key=lambda idx: scores[idx], reverse=True)
    output = []
    for idx in ranked:
        if scores[idx] <= 0:
            break
        chunk = _docstore[idx]
        if allowed and chunk.get("document_name") not in allowed:
            continue
        output.append({
            "id": chunk["id"],
            "text": chunk["text"],
            "metadata": _metadata_from_chunk(chunk),
            "heading_score": float(scores[idx]),
        })
        if len(output) >= top_k:
            break
    return output


def _dedupe_by_parent(items: List[Dict[str, Any]], limit: int):
    """One result per topic, no duplicate text across regions, budgeted size."""
    _load()
    total_budget = int(getattr(config, "MAX_CONTEXT_CHARS", 12000))

    output: List[Dict[str, Any]] = []
    seen_parents = set()
    seen_content: Dict[str, Dict[str, Any]] = {}
    used = 0

    for item in items:
        metadata = item["metadata"]
        parent_key = metadata.get("parent_id") or item["id"]
        if parent_key in seen_parents:
            continue

        stored = _doc_by_id.get(item["id"], {})
        digest = stored.get("content_hash") or _content_hash(stored.get("raw_text") or item.get("text", ""))

        # Identical text already present under a different region: record the
        # extra region rather than spending another context slot on it.
        if digest in seen_content:
            twin = seen_content[digest]
            name = metadata.get("document_name", "")
            also_in = twin["metadata"].setdefault("also_in", [])
            if name and name not in also_in:
                also_in.append(name)
            continue

        expanded = dict(item)
        expanded["metadata"] = dict(metadata)
        expanded["metadata"]["also_in"] = list(_content_index.get(digest, []))
        expanded["matched_text"] = item.get("text", "")
        expanded["text"] = _expanded_text(item)

        # The answer shows the WHOLE parent topic, so it should show the whole
        # topic's images. Child chunks only carry the pictures beside their own
        # text, so using the matched child alone loses images that belong to
        # the same procedure but fell into a neighbouring chunk.
        parent = _parentstore.get(metadata.get("parent_id") or "")
        parent_images = (parent or {}).get("image_paths") or []
        if parent_images:
            expanded["metadata"]["image_paths"] = list(parent_images)
            expanded["metadata"]["has_images"] = True

        if output and used + len(expanded["text"]) > total_budget:
            continue

        seen_parents.add(parent_key)
        seen_content[digest] = expanded
        used += len(expanded["text"])
        output.append(expanded)

        if len(output) >= limit:
            break

    return output


def _prune_weak(items):
    """Drop noise candidates; the top result is always kept."""
    if not items:
        return items
    min_vector = float(getattr(config, "MIN_VECTOR_SCORE", 0.0))
    kept = [items[0]]
    for item in items[1:]:
        if item.get("vector_score", 0.0) >= min_vector or item.get("bm25_norm", 0.0) >= 0.5:
            kept.append(item)
    return kept


def _lock_to_document(items, forced: bool = False):
    """
    Keep the answer inside one SOP so regions cannot be mixed in one reply.

    Locking unconditionally makes a single bad top-1 catastrophic: for "notary
    is needed" the lead was Hartford, so West's "9. NOTARY" section was thrown
    away before it could be considered. The lock now applies only when the user
    named a region (forced) or the lead is clearly ahead of the best candidate
    from any other document.
    """
    if not items:
        return items
    primary = items[0]["metadata"].get("document_name")
    if not primary:
        return items

    same = [item for item in items if item["metadata"].get("document_name") == primary]
    others = [item for item in items if item["metadata"].get("document_name") != primary]

    if not forced and others:
        margin = float(getattr(config, "DOCUMENT_LOCK_MARGIN", 0.10))
        lead = items[0].get("confidence", 0.0)
        best_other = max(item.get("confidence", 0.0) for item in others)
        if lead - best_other < margin:
            return items      # too close to call - let the ranker decide

    return same or items


def hybrid_retrieve(query, top_k_final=None, document_filter=None):
    """Hybrid retrieval with region isolation, heading priority, real confidence."""
    _load()
    top_k_final = top_k_final or getattr(config, "TOP_K_FINAL", 5)
    top_k_vector = getattr(config, "TOP_K_VECTOR", 12)
    top_k_bm25 = getattr(config, "TOP_K_BM25", 12)
    rrf_k = getattr(config, "RRF_K", 60)

    # An explicit UI filter always wins over auto-detection.
    doc_filter = _normalise_filter(document_filter) or detect_document_filters(query)

    vector_results = vector_search(query, top_k_vector, doc_filter)
    bm25_results = bm25_search(query, top_k_bm25, doc_filter)
    heading_results = heading_search(query, top_k_bm25, doc_filter)

    ranked_lists = [vector_results, bm25_results]
    if heading_results:
        ranked_lists.append(heading_results)
    fused = reciprocal_rank_fusion(ranked_lists, k=rrf_k)

    vector_by_id = {item["id"]: item.get("vector_score", 0.0) for item in vector_results}
    max_bm25 = max([item.get("bm25_score", 0.0) for item in bm25_results] or [0.0])

    section_match = re.search(r"\b(\d+(?:\.\d+)+)\b", query or "")
    section_number = section_match.group(1) if section_match else ""
    query_terms = {term for term in _tokens(query) if len(term) > 2}

    for item in fused:
        metadata = item.get("metadata", {})
        heading_blob = " ".join([
            str(metadata.get("topic_path", "")),
            str(metadata.get("header", "")),
            str(metadata.get("topic", "")),
        ]).lower()

        boost = 1.0
        if section_number and (
            section_number == str(metadata.get("section_number", ""))
            or section_number in heading_blob
        ):
            boost *= 2.0

        # Reward candidates whose heading genuinely contains the query terms.
        heading_terms = set(_tokens(heading_blob))
        if query_terms:
            overlap = len(query_terms & heading_terms) / len(query_terms)
            # A heading that contains every query term is almost always the
            # right section ("notary" -> "9. NOTARY"), so reward it strongly.
            boost *= 1.0 + (1.5 * overlap)

        item["fused_score"] = item.get("fused_score", 0.0) * boost
        item["vector_score"] = vector_by_id.get(item["id"], item.get("vector_score", 0.0))
        item["bm25_norm"] = (item.get("bm25_score", 0.0) / max_bm25) if max_bm25 else 0.0
        # Absolute confidence, not normalised against this result list, so the
        # value is comparable between queries and usable as a threshold.
        # (The old formula divided by the list maximum, making it always 1.0.)
        item["confidence"] = round(
            min(1.0, (0.65 * item["vector_score"]) + (0.35 * item["bm25_norm"])), 2
        )

    fused.sort(key=lambda entry: entry.get("fused_score", 0.0), reverse=True)
    fused = _prune_weak(fused)

    if getattr(config, "SINGLE_DOCUMENT_CONTEXT", True):
        fused = _lock_to_document(fused, forced=bool(doc_filter))

    return _dedupe_by_parent(fused, top_k_final)

def reset_retriever_cache():
    """Reload the Chroma collection and local stores after an index rebuild."""
    global _collection, _bm25, _docstore, _parentstore, _doc_by_id, _tokenized_corpus, _content_index, _bm25_headings, _heading_corpus, _heading_df
    _content_index = {}
    _bm25_headings = None
    _heading_corpus = None
    _heading_df = {}
    _collection = None
    _bm25 = None
    _docstore = []
    _parentstore = {}
    _doc_by_id = {}
    _tokenized_corpus = None
