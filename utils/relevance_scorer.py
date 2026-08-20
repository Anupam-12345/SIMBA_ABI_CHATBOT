# utils/relevance_scorer.py
"""
Dynamic relevance scoring for SOP documents.
Tuned for accurate section identification.
"""
import re
from typing import Dict, List, Any, Tuple

class RelevanceScorer:
    def __init__(self):
        # Stop words to ignore
        self.stop_words = {
            'the', 'a', 'an', 'of', 'for', 'to', 'in', 'on', 'at', 'with', 
            'by', 'from', 'up', 'about', 'into', 'through', 'during', 
            'including', 'without', 'against', 'between', 'during',
            'within', 'upon', 'towards', 'under', 'over', 'after', 'before'
        }
        
        # Common SOP section headers that are often generic
        self.generic_headers = {
            'introduction', 'overview', 'purpose', 'scope', 'definitions',
            'abbreviations', 'general', 'table of contents', 'index'
        }

    def extract_key_terms(self, query: str) -> List[str]:
        """Extract key terms from the query"""
        words = query.lower().split()
        key_terms = []
        
        # Extract multi-word phrases first (2-3 word phrases)
        for i in range(len(words)):
            for j in range(i+1, min(i+4, len(words)+1)):
                phrase = ' '.join(words[i:j])
                if len(phrase) > 5 and phrase not in self.stop_words:
                    key_terms.append(phrase)
        
        # Add individual meaningful words
        for word in words:
            if word not in self.stop_words and len(word) > 2:
                key_terms.append(word)
        
        # Remove duplicates and sort by length (longest first - phrases first)
        key_terms = list(set(key_terms))
        key_terms.sort(key=len, reverse=True)
        
        return key_terms

    def find_exact_section(self, text: str, query: str) -> bool:
        """Check if this is the exact section the user is asking about"""
        query_lower = query.lower()
        text_lower = text.lower()
        
        # Look for section headers that match the query
        # Pattern: "21.1 Process of Non-compliance:" etc.
        section_pattern = r'^(\d+\.\d+)\s+([^.]+)'
        lines = text.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            match = re.match(section_pattern, line)
            if match:
                section_text = match.group(2).lower()
                # Check if query terms appear in the section header
                query_words = [w for w in query_lower.split() if len(w) > 3]
                match_count = sum(1 for word in query_words if word in section_text)
                if match_count >= 2:  # At least 2 words match
                    return True
        return False

    def calculate_relevance(self, chunk: Dict[str, Any], query: str) -> Tuple[float, Dict[str, float]]:
        """
        Calculate relevance score for a chunk
        """
        text = chunk['text']
        header = chunk['metadata'].get('header', '')
        doc_name = chunk['metadata'].get('document_name', '')
        
        query_lower = query.lower()
        query_words = [w for w in query_lower.split() if len(w) > 2]
        
        # ============================================================
        # 1. HEADER SCORING (High weight)
        # ============================================================
        header_score = 0
        header_lower = header.lower()
        
        for word in query_words:
            if word in header_lower:
                header_score += 3
        
        # Check for exact section number match (e.g., "21.1")
        for word in query_words:
            if '.' in word and word.replace('.', '').isdigit():
                if word in text or word in header:
                    header_score += 5
        
        # Check if this is the exact section
        if self.find_exact_section(text, query):
            header_score += 8
        
        # ============================================================
        # 2. TEXT CONTENT SCORING
        # ============================================================
        text_score = 0
        text_lower = text.lower()
        
        # Check for phrase matches
        for word in query_words:
            if len(word) > 3 and word in text_lower:
                # Higher weight if in first 300 chars
                if word in text_lower[:300]:
                    text_score += 2
                else:
                    text_score += 0.5
        
        # Check for multi-word phrases
        for i in range(len(query_words)):
            if i < len(query_words) - 1:
                phrase = f"{query_words[i]} {query_words[i+1]}"
                if phrase in text_lower:
                    text_score += 3
                    if phrase in text_lower[:300]:
                        text_score += 2
        
        # ============================================================
        # 3. STRUCTURAL SCORING
        # ============================================================
        structure_score = 0
        
        # Check if the chunk has bullet points or numbered lists
        lines = text.split('\n')
        bullet_count = 0
        numbered_count = 0
        
        for line in lines:
            line = line.strip()
            if line.startswith('-') or line.startswith('•') or line.startswith('*'):
                bullet_count += 1
            elif line and line[0].isdigit() and '.' in line[:5]:
                numbered_count += 1
        
        # Content with structure is more valuable
        if bullet_count > 0:
            structure_score += min(bullet_count * 0.5, 3)
        if numbered_count > 0:
            structure_score += min(numbered_count * 0.3, 2)
        
        # ============================================================
        # 4. PENALTIES
        # ============================================================
        penalty = 0
        
        # Penalize generic content
        for word in self.generic_headers:
            if word in header_lower and not any(q in header_lower for q in query_words[:2]):
                penalty -= 1
        
        # Penalize if the header doesn't match but text does
        if header_score < 2 and text_score > 5:
            penalty -= 0.5
        
        # ============================================================
        # 5. CALCULATE TOTAL
        # ============================================================
        total_score = header_score + text_score + structure_score + penalty
        
        breakdown = {
            'header_score': header_score,
            'text_score': text_score,
            'structure_score': structure_score,
            'penalty': penalty,
            'total': total_score
        }
        
        return total_score, breakdown

    def filter_and_rank_chunks(self, chunks: List[Dict[str, Any]], query: str, max_chunks: int = 2) -> List[Dict[str, Any]]:
        """
        Filter and rank chunks by relevance
        """
        if not chunks:
            return []
        
        scored_chunks = []
        
        for chunk in chunks:
            score, breakdown = self.calculate_relevance(chunk, query)
            
            # Debug output (optional - can be removed in production)
            # print(f"Score: {score:.2f} - Header: {chunk['metadata'].get('header', 'Unknown')}")
            
            if score > 0:
                scored_chunks.append({
                    'chunk': chunk,
                    'score': score,
                    'breakdown': breakdown
                })
        
        # Sort by score (highest first)
        scored_chunks.sort(key=lambda x: x['score'], reverse=True)
        
        # Remove duplicate sections (keep the one with highest score)
        unique_chunks = []
        seen_sections = set()
        
        for item in scored_chunks:
            chunk = item['chunk']
            doc_name = chunk['metadata'].get('document_name', '')
            header = chunk['metadata'].get('header', '')
            
            # Create a key based on document and header
            key = f"{doc_name}_{header}"
            
            # If this is a new section, or has higher score than existing
            if key not in seen_sections:
                seen_sections.add(key)
                unique_chunks.append(item)
            else:
                # Replace if this chunk has higher score
                for i, existing in enumerate(unique_chunks):
                    existing_key = f"{existing['chunk']['metadata'].get('document_name', '')}_{existing['chunk']['metadata'].get('header', '')}"
                    if existing_key == key and item['score'] > existing['score']:
                        unique_chunks[i] = item
                        break
        
        # Return top chunks
        result = [item['chunk'] for item in unique_chunks[:max_chunks]]
        
        # If we got less than max_chunks, add the next best ones
        if len(result) < max_chunks and len(scored_chunks) > len(result):
            for item in scored_chunks:
                if item['chunk'] not in result:
                    result.append(item['chunk'])
                    if len(result) >= max_chunks:
                        break
        
        return result