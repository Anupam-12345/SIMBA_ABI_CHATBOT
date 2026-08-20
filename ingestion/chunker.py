"""
Turns raw (header, text) blocks into overlapping chunks sized for embedding,
while keeping every chunk tagged with the metadata we need for filtering
and citation later: document name, header, sub_header, page.
"""
import tiktoken

ENCODER = tiktoken.get_encoding("cl100k_base")


def count_tokens(text):
    return len(ENCODER.encode(text))


def split_text_by_tokens(text, chunk_size, overlap):
    tokens = ENCODER.encode(text)
    if len(tokens) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        chunk_tokens = tokens[start:end]
        chunks.append(ENCODER.decode(chunk_tokens))
        if end == len(tokens):
            break
        start = end - overlap  # step back for overlap
    return chunks


def chunk_document(doc_name, blocks, chunk_size, overlap):
    """
    blocks: list of {header, sub_header, page, text} from a reader
    Returns: list of chunk dicts ready for embedding:
      {id, document_name, header, sub_header, page, text}
    """
    chunks = []
    chunk_idx = 0

    for block in blocks:
        header = block.get("header") or "General"
        sub_header = block.get("sub_header") or ""
        page = block.get("page")
        text = block["text"]

        # Prefix the header into the chunk text itself -- this helps both the
        # embedding model and the LLM understand context, since header text
        # alone is ambiguous across documents but (header + content) is not.
        pieces = split_text_by_tokens(text, chunk_size, overlap)

        for piece in pieces:
            chunk_idx += 1
            chunk_text = f"[{doc_name} | {header}{(' > ' + sub_header) if sub_header else ''}]\n{piece}"
            chunks.append({
                "id": f"{doc_name}_{chunk_idx}",
                "document_name": doc_name,
                "header": header,
                "sub_header": sub_header,
                "page": str(page) if page else "",
                "text": chunk_text,
                "raw_text": piece,
            })

    return chunks
