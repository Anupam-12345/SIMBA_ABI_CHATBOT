# ollama_client.py
"""
Ollama client with enhanced RAG response generation
"""
import requests
import time
import config
import re


def embed(text):
    """Returns a single embedding vector for the given text."""
    try:
        resp = requests.post(
            f"{config.OLLAMA_BASE_URL}/api/embeddings",
            json={"model": config.EMBED_MODEL, "prompt": text},
            timeout=config.OLLAMA_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]
    except requests.exceptions.ConnectionError:
        print(f"❌ Cannot connect to Ollama at {config.OLLAMA_BASE_URL}")
        print("   Please make sure Ollama is running: ollama serve")
        raise
    except requests.exceptions.Timeout:
        print(f"❌ Ollama timed out after {config.OLLAMA_TIMEOUT} seconds")
        raise


def embed_batch(texts):
    """Batch embedding helper."""
    return [embed(t) for t in texts]


# ollama_client.py - Updated validation

# def generate_rag_response(query: str, chunks: list) -> str:
#     """
#     Generate a professional RAG response using retrieved chunks.
#     """
#     if not config.USE_LLM:
#         return None
    
#     # Build context from chunks
#     context = build_context(chunks)
    
#     # Build the prompt
#     prompt = build_rag_prompt(query, context)
    
#     try:
#         # Check if Ollama is responsive
#         requests.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=5)
#     except requests.exceptions.ConnectionError:
#         return "⚠️ I'm having trouble connecting to the AI service. Please make sure Ollama is running and try again."
    
#     # Check if model exists
#     try:
#         resp = requests.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=5)
#         models = resp.json().get('models', [])
#         model_names = [m.get('name', '') for m in models]
        
#         model_exists = any(config.LLM_MODEL in name for name in model_names)
#         if not model_exists:
#             if model_names:
#                 config.LLM_MODEL = model_names[0]
#                 print(f"⚠️ Using fallback model: {config.LLM_MODEL}")
#             else:
#                 return "⚠️ No models available. Please pull a model: ollama pull deepseek-r1:1.5b"
#     except Exception as e:
#         print(f"⚠️ Could not check models: {e}")
    
#     payload = {
#         "model": config.LLM_MODEL,
#         "prompt": prompt,
#         "system": "You are a professional SOP assistant. Provide clear, concise answers based ONLY on the provided SOP documents. Do not add any information not in the documents. Format your response professionally with numbered steps when applicable.",
#         "stream": False,
#         "keep_alive": "2m",
#         "options": {
#             "temperature": config.TEMPERATURE,
#             "num_predict": config.MAX_RESPONSE_TOKENS,
#             "num_ctx": config.CONTEXT_WINDOW_TOKENS,
#         },
#     }
    
#     try:
#         print(f"⏳ Generating RAG response with {config.LLM_MODEL}...")
#         start_time = time.time()
        
#         resp = requests.post(
#             f"{config.OLLAMA_BASE_URL}/api/generate",
#             json=payload,
#             timeout=config.OLLAMA_TIMEOUT,
#         )
#         resp.raise_for_status()
        
#         elapsed = time.time() - start_time
#         print(f"✅ Response in {elapsed:.2f}s")
        
#         response = resp.json()["response"]
        
#         # Validate the response
#         if not response or len(response) < 20:
#             print("⚠️ LLM response too short")
#             return None
        
#         # Check if response is just repeating the question
#         if response.lower().startswith(query.lower()[:20]):
#             print("⚠️ LLM response is just repeating the question")
#             return None
        
#         # Check if response contains error indicators
#         error_indicators = ['❌', '⚠️', 'error', 'unable', 'cannot', "I don't have", "I don't know"]
#         if any(indicator in response.lower() for indicator in error_indicators):
#             print("⚠️ LLM response contains error indicators")
#             return None
        
#         return response
        
#     except requests.exceptions.ReadTimeout:
#         print("⏰ LLM timeout")
#         return None
#     except requests.exceptions.ConnectionError:
#         print("⚠️ Cannot connect to Ollama")
#         return None
#     except requests.exceptions.RequestException as e:
#         print(f"❌ Error: {e}")
#         return None


def _grounding_score(response: str, chunks: list) -> float:
    """Share of the answer's content words that also occur in the context."""
    source = " ".join((chunk.get("text") or "") for chunk in chunks).lower()
    if not source.strip():
        return 1.0
    source_words = set(re.findall(r"[a-z0-9]{4,}", source))
    answer_words = set(re.findall(r"[a-z0-9]{4,}", (response or "").lower()))
    if not answer_words:
        return 0.0
    return len(answer_words & source_words) / len(answer_words)


def generate_rag_response(query: str, chunks: list) -> str:
    """
    Generate a professional RAG response using retrieved chunks.
    """
    if not config.USE_LLM:
        return None
    
    # Build context from chunks
    context = build_context(chunks)
    
    # Build the prompt with stricter instructions
    prompt = build_rag_prompt(query, context)
    
    try:
        # Check if Ollama is responsive
        requests.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=5)
    except requests.exceptions.ConnectionError:
        return "⚠️ I'm having trouble connecting to the AI service. Please make sure Ollama is running and try again."
    
    # Check if model exists
    try:
        resp = requests.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=5)
        models = resp.json().get('models', [])
        model_names = [m.get('name', '') for m in models]
        
        model_exists = any(config.LLM_MODEL in name for name in model_names)
        if not model_exists:
            if model_names:
                config.LLM_MODEL = model_names[0]
                print(f"⚠️ Using fallback model: {config.LLM_MODEL}")
            else:
                return "⚠️ No models available. Please pull a model: ollama pull deepseek-r1:1.5b"
    except Exception as e:
        print(f"⚠️ Could not check models: {e}")
    
    # STRICTER SYSTEM PROMPT - prevents hallucination
    system_prompt = """You are an SOP documentation assistant. Your task is to EXACTLY REPRODUCE content from the provided documents.

CRITICAL RULES - YOU MUST FOLLOW THESE EXACTLY:

1. ONLY use information from the provided SOP documents.
2. NEVER add information that is not in the documents.
3. If the documents contain numbered steps, reproduce ALL of them exactly as written.
4. If the documents contain bullet points, reproduce ALL of them exactly as written.
5. DO NOT rephrase, summarize, or omit any steps, requirements, or details.
6. DO NOT add your own explanations, introductions, or conclusions.
7. Output the content EXACTLY as it appears in the documents, preserving all numbering and structure.

IMPORTANT - CONTENT MIXING PREVENTION:
- ONLY include content from the MOST RELEVANT section
- Do NOT mix content from different sections (e.g., don't mix VPU Picking Up with VPU No X-Rays)
- If the query asks about a specific section, ONLY use that section's content
- Each section has its own unique requirements - keep them separate

If the exact information is not in the documents, say: "I cannot find this information in the available SOP documents."

REMEMBER: Your job is to COPY from the documents, not to CREATE or MIX content."""
    
    payload = {
        "model": config.LLM_MODEL,
        "prompt": prompt,
        "system": system_prompt,
        "stream": False,
        "keep_alive": "2m",
        "options": {
            "temperature": 0.0,  # ZERO temperature for deterministic output
            "num_predict": config.MAX_RESPONSE_TOKENS,
            "num_ctx": config.CONTEXT_WINDOW_TOKENS,
            "repeat_penalty": 1.0,  # 1.2 dropped legitimately repeated steps ("Click OK.")
            "top_k": 10,  # More focused
            "top_p": 0.9,
        },
    }
    
    try:
        print(f"⏳ Generating RAG response with {config.LLM_MODEL}...")
        start_time = time.time()
        
        resp = requests.post(
            f"{config.OLLAMA_BASE_URL}/api/generate",
            json=payload,
            timeout=config.OLLAMA_TIMEOUT,
        )
        resp.raise_for_status()
        
        elapsed = time.time() - start_time
        print(f"✅ Response in {elapsed:.2f}s")
        
        response = resp.json()["response"]
        
        # Validate the response
        if not response or len(response) < 20:
            print("⚠️ LLM response too short")
            return None
        
        # A genuine "not found" is a correct answer - pass it through.
        if "cannot find this information" in response.lower() and len(response) < 200:
            return response

        # Only an actual error message is an error. 14% of SOP topics contain
        # "cannot"/"unable"/"error" in ordinary procedural text, and rejecting
        # those answers was routing correct output into the truncated fallback.
        if response.startswith(("⚠️", "❌")):
            print("⚠️ LLM returned an error message")
            return None

        # Reject only if the answer is nothing but the question echoed back.
        simplify = lambda value: re.sub(r"[^a-z0-9 ]", " ", value.lower()).split()
        if simplify(response) == simplify(query):
            print("⚠️ LLM response is just repeating the question")
            return None

        # Grounding replaces the old "len(response) < len(source) * 0.3" rule,
        # which discarded correct short answers such as "Passing percentage is 95%".
        threshold = float(getattr(config, "GROUNDING_THRESHOLD", 0.40))
        score = _grounding_score(response, chunks)
        if score < threshold:
            print(f"⚠️ Answer poorly grounded (overlap {score:.2f} < {threshold}) — using fallback")
            return None

        return response
        
    except requests.exceptions.ReadTimeout:
        print("⏰ LLM timeout")
        return None
    except requests.exceptions.ConnectionError:
        print("⚠️ Cannot connect to Ollama")
        return None
    except requests.exceptions.RequestException as e:
        print(f"❌ Error: {e}")
        return None


# def build_context(chunks: list) -> str:
#     """Build context from retrieved chunks"""
#     context_parts = []
#     for i, chunk in enumerate(chunks, 1):
#         doc_name = chunk['metadata'].get('document_name', 'Unknown')
#         header = chunk['metadata'].get('header', '')
#         text = chunk['text'].strip()
        
#         # Clean the text
#         text = text.replace('[', '').replace(']', '')
#         text = text.replace('**', '')
#         text = text.replace('##', '')
#         text = text.replace('###', '')
        
#         context_parts.append(f"DOCUMENT {i} ({doc_name}):")
#         if header:
#             context_parts.append(f"Section: {header}")
#         context_parts.append(text)
#         context_parts.append("")
    
#     return '\n'.join(context_parts)

# def build_context(chunks: list) -> str:
#     """Build context from retrieved chunks with ALL content"""
#     context_parts = []
#     for i, chunk in enumerate(chunks, 1):
#         doc_name = chunk['metadata'].get('document_name', 'Unknown')
#         header = chunk['metadata'].get('header', '')
#         sub_header = chunk['metadata'].get('sub_header', '')
#         text = chunk['text'].strip()
        
#         # Clean the text
#         text = text.replace('[', '').replace(']', '')
#         text = text.replace('**', '')
#         text = text.replace('##', '')
#         text = text.replace('###', '')
        
#         # Remove document prefix if present
#         if 'Document:' in text:
#             lines = text.split('\n')
#             cleaned_lines = []
#             for line in lines:
#                 if 'Document:' in line or 'Topic:' in line:
#                     continue
#                 cleaned_lines.append(line)
#             text = '\n'.join(cleaned_lines)
        
#         context_parts.append(f"=== DOCUMENT {i}: {doc_name} ===")
#         if header:
#             context_parts.append(f"Section: {header}")
#         if sub_header:
#             context_parts.append(f"Sub-section: {sub_header}")
#         context_parts.append("")
#         context_parts.append(text)
#         context_parts.append("")
    
#     return '\n'.join(context_parts)

def build_context(chunks: list) -> str:
    """Build context from retrieved chunks - preserving exact structure"""
    context_parts = []
    for i, chunk in enumerate(chunks, 1):
        doc_name = chunk['metadata'].get('document_name', 'Unknown')
        header = chunk['metadata'].get('header', '')
        sub_header = chunk['metadata'].get('sub_header', '')
        text = chunk['text'].strip()
        
        # Clean the text but preserve structure
        # Remove markdown artifacts but keep content
        text = text.replace('**', '').replace('`', '')
        
        # Remove document prefix if present (it's for metadata, not for content)
        if 'Document:' in text:
            lines = text.split('\n')
            cleaned_lines = []
            for line in lines:
                if line.startswith('Document:') or line.startswith('Topic:'):
                    continue
                cleaned_lines.append(line)
            text = '\n'.join(cleaned_lines)
        
        context_parts.append(f"=== DOCUMENT {i}: {doc_name} ===")
        if header:
            context_parts.append(f"Section: {header}")
        if sub_header:
            context_parts.append(f"Sub-section: {sub_header}")
        context_parts.append("")
        context_parts.append(text)
        context_parts.append("")
        context_parts.append("---")
    
    return '\n'.join(context_parts)


# def build_rag_prompt(query: str, context: str) -> str:
#     """Build RAG prompt"""
#     return f"""Based on the following SOP documents, answer the question professionally.

# SOP DOCUMENTS:
# {context}

# QUESTION: {query}

# PROFESSIONAL ANSWER (based ONLY on the documents above):"""

# def build_rag_prompt(query: str, context: str) -> str:
#     """Build RAG prompt requesting complete information"""
#     return f"""Based on the following SOP documents, answer the question professionally and COMPLETELY.

# IMPORTANT: Provide ALL the information from the documents. Do not summarize or omit any steps, requirements, or details.

# SOP DOCUMENTS:
# {context}

# QUESTION: {query}

# PROFESSIONAL ANSWER (based ONLY on the documents above, include ALL details):"""

# def build_rag_prompt(query: str, context: str) -> str:
#     """Build RAG prompt with explicit copying instructions"""
#     return f"""You are an SOP documentation assistant. Your task is to EXACTLY COPY the relevant information from the documents below.

# IMPORTANT INSTRUCTIONS:
# 1. COPY the content exactly as written - preserve all numbering, bullet points, and formatting
# 2. Include ALL steps, requirements, and details from the documents
# 3. DO NOT summarize, rephrase, or omit any information
# 4. DO NOT add any information that is not in the documents
# 5. DO NOT add explanations, introductions, or conclusions of your own

# SOP DOCUMENTS:
# {context}

# QUESTION: {query}

# Now, COPY the relevant information from the documents above exactly as written:"""

def build_rag_prompt(query: str, context: str) -> str:
    """Build RAG prompt with explicit section-specific instructions"""
    
    # Extract section number from query
    section_match = re.search(r'(\d+\.\d+)', query)
    section_hint = f"Section {section_match.group(1)}" if section_match else ""
    
    return f"""You are an SOP documentation assistant. Your task is to EXACTLY COPY information from the documents below.

IMPORTANT INSTRUCTIONS:
1. COPY the content exactly as written - preserve all numbering, bullet points, and formatting
2. Include ALL steps, requirements, and details from the documents
3. DO NOT summarize, rephrase, or omit any information
4. DO NOT add any information that is not in the documents
5. DO NOT add explanations, introductions, or conclusions of your own
6. {section_hint}: If the query asks about a specific section, ONLY use content from that section
7. DO NOT mix content from different sections - each section has its own unique content

SOP DOCUMENTS:
{context}

QUESTION: {query}

Now, COPY the relevant information from the documents above exactly as written.

IMPORTANT: If there are multiple sections in the context, ONLY use the section that matches the query. Do not mix content from different sections."""

def get_direct_response(chunks: list) -> str:
    """Fallback: Direct chunk response when LLM is disabled or fails"""
    response_parts = ["📋 **SOP Information**", ""]
    
    for i, chunk in enumerate(chunks[:3], 1):
        doc_name = chunk['metadata'].get('document_name', 'Unknown')
        header = chunk['metadata'].get('header', '')
        text = chunk['text'].strip()
        
        text = text.replace('[', '').replace(']', '')
        text = text.replace('**', '')
        text = text.replace('##', '')
        text = text.replace('###', '')
        
        if header:
            response_parts.append(f"**{i}. {doc_name} - {header}:**")
        else:
            response_parts.append(f"**{i}. {doc_name}:**")
        response_parts.append("")
        
        lines = text.split('\n')
        for line in lines[:10]:
            if line.strip():
                response_parts.append(f"  {line.strip()}")
        response_parts.append("")
    
    return '\n'.join(response_parts)


def test_ollama_connection():
    """Test if Ollama is working properly"""
    try:
        resp = requests.get(f"{config.OLLAMA_BASE_URL}/api/tags", timeout=5)
        models = resp.json().get('models', [])
        print(f"✅ Connected to Ollama. Models: {len(models)}")
        for model in models[:5]:
            print(f"   - {model.get('name')}")
        return True
    except Exception as e:
        print(f"❌ Cannot connect to Ollama: {e}")
        return False


if __name__ == "__main__":
    test_ollama_connection()