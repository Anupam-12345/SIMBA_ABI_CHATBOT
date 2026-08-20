# utils/semantic_scorer.py
"""
Semantic scorer for filtering chunks with intent matching
"""
import re
from typing import List, Dict, Any


class SemanticScorer:
    def __init__(self):
        # Intent patterns for different query types
        self.intent_patterns = {
            'close_order_attempts': {
                'keywords': ['how many', 'attempts', 'close order', 'closeout', 'close out', 'should we close'],
                'sections': ['CNR Closeout', 'Affidavit Closeout', 'Closeout Attempts', 'attempts to close']
            },
            'mark_offsite': {
                'keywords': ['mark order as offsite', 'when to mark', 'offsite condition', 'offsite trigger'],
                'sections': ['Offsite', 'OFFSITE', 'Offsite Close Out', 'New Order']
            },
            'partial_records': {
                'keywords': ['partial records', 'only medical', 'only billing', 'incomplete records', 'partial'],
                'sections': ['Partial Records', 'Medical Only', 'Billing Only', 'Records Received']
            },
            'serve_address': {
                'keywords': ['serve new address', 'sna', 'new address', 'serve address'],
                'sections': ['Serve New Address', 'SNA:', 'Serve New Address Process']
            },
            'void_check': {
                'keywords': ['void check', 'voided check', 'voiding check'],
                'sections': ['Void Check Procedure', '11.3 Void Check']
            },
            'non_compliance': {
                'keywords': ['non-compliance', 'non compliance', 'noncompliant', 'compliance notice'],
                'sections': ['Process of Non-compliance', 'Non-Compliant']
            },
            'facility_contact': {
                'keywords': ['unable to locate', 'no contact', 'contact details', 'facility contact', 'poc'],
                'sections': ['Exception Scenarios', 'Contact', 'No Contact']
            },
            'trigger': {
                'keywords': ['trigger after', 'status code', 'trigger code', 'sent-', 'status:'],
                'sections': ['Trigger', 'Status Code', 'Sent -']
            },
            'follow_up': {
                'keywords': ['follow-up', 'follow up', 'followup', 'how to process follow'],
                'sections': ['Follow-Up', 'Follow Up', 'Followup']
            }
        }

    def filter_and_rank_chunks(self, chunks: List[Dict[str, Any]], query: str, max_chunks: int = 5) -> List[Dict[str, Any]]:
        """
        Filter and rank chunks by relevance with intent matching
        """
        if not chunks:
            return []
        
        query_lower = query.lower()
        query_words = [w for w in query_lower.split() if len(w) > 2]
        
        # Identify the intent
        intent = self._identify_intent(query_lower)
        print(f"🎯 Intent: {intent}")
        
        scored_chunks = []
        for chunk in chunks:
            text = chunk['text']
            header = chunk['metadata'].get('header', '')
            doc_name = chunk['metadata'].get('document_name', '')
            
            score = 0
            
            # 1. Intent-based scoring (highest priority)
            if intent and intent in self.intent_patterns:
                intent_config = self.intent_patterns[intent]
                
                # Check for section matches
                for section in intent_config['sections']:
                    if section in header or section in text[:300]:
                        score += 40
                        break
                
                # Check for keyword matches in header
                for keyword in intent_config['keywords']:
                    if keyword in header.lower():
                        score += 20
                    elif keyword in text.lower()[:500]:
                        score += 10
            
            # 2. Exact section number matching
            section_match = re.search(r'(\d+\.\d+)', query)
            if section_match:
                section_num = section_match.group()
                if section_num in text or section_num in header:
                    score += 30
            
            # 3. Query word matching in header
            for word in query_words:
                if word in header.lower():
                    score += 5
                elif word in text.lower()[:300]:
                    score += 2
            
            # 4. Document-specific bonuses
            if 'void' in query_lower and 'hartford' in doc_name.lower():
                score += 10
            if 'serve new address' in query_lower and 'hartford' in doc_name.lower():
                score += 5
            if 'offsite' in query_lower and 'west' in doc_name.lower():
                score += 5
            
            # 5. Penalize unrelated sections
            if intent == 'close_order_attempts':
                if 'qc1' in header.lower() or 'post-close' in header.lower():
                    score -= 20
                if 'order statuses' in header.lower():
                    score -= 15
            
            if intent == 'mark_offsite':
                if 'trigger' in header.lower() and 'offsite' not in header.lower():
                    score -= 15
                if 'vpv' in header.lower() or 'vtc' in header.lower():
                    score -= 10
            
            if intent == 'partial_records':
                if 'order statuses' in header.lower():
                    score -= 15
                if 'status code' in header.lower():
                    score -= 10
            
            scored_chunks.append((score, chunk, header))
        
        # Sort by score
        scored_chunks.sort(key=lambda x: x[0], reverse=True)
        
        # Debug output
        print("📊 Top chunks:")
        for score, chunk, header in scored_chunks[:3]:
            print(f"  Score: {score} - Header: {header}")
        
        # Return chunks with positive scores
        result = []
        for score, chunk, _ in scored_chunks:
            if score > 0:
                result.append(chunk)
                if len(result) >= max_chunks:
                    break
        
        # If no positive scores, return top chunks
        if not result:
            result = [chunk for _, chunk, _ in scored_chunks[:2]]
        
        return result

    def _identify_intent(self, query: str) -> str:
        """Identify the intent of the query"""
        for intent, config in self.intent_patterns.items():
            for keyword in config['keywords']:
                if keyword in query:
                    return intent
        return None