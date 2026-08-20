# utils/response_generator.py
"""
Professional response generator - Complete content extraction
"""
import re
from typing import List, Dict, Any


class ResponseGenerator:
    def __init__(self):
        # Exact section mappings for each topic
        self.section_mappings = {
            'void_check': {
                'sections': ['Void Check Procedure', '11.3 Void Check', '11.3 Void Check Procedure'],
                'keywords': ['void check', 'voided', 'voiding']
            },
            'serve_address': {
                'sections': ['Serve New Address', 'SNA: Serve New Address', 'Serve New Address Process', 'SNA'],
                'keywords': ['serve new address', 'sna', 'new address']
            },
            'non_compliance': {
                'sections': ['Process of Non-compliance', 'Non-Compliant', 'Non Compliance'],
                'keywords': ['non-compliance', 'non compliance', 'noncompliant']
            },
            'offsite': {
                'sections': ['Offsite', 'OFFSITE', 'Offsite Close Out', 'Offsite Process'],
                'keywords': ['offsite', 'new order']
            },
            'invoice': {
                'sections': ['Invoice', 'PAYING BY CHECK', 'INVOICE-RECEIVED'],
                'keywords': ['invoice', 'payment', 'pay']
            },
            'xray': {
                'sections': ['XRAY BREAKDOWN', 'X-Ray Breakdown', 'RADIOLOGY IMAGES'],
                'keywords': ['x-ray', 'xray', 'breakdown']
            },
            'depo': {
                'sections': ['Push Depo Date', 'Depo Date', 'Deposition'],
                'keywords': ['depo', 'deposition', 'subpoena']
            },
            'vpu': {
                'sections': ['VPU/VTC', 'VPU', 'VTC', 'Verify address for pickup'],
                'keywords': ['vpu', 'vtc', 'pickup', 'verified']
            },
            'facility_contact': {
                'sections': ['Contact Details', 'Facility Contact', 'No Contact', 'Exception Scenarios'],
                'keywords': ['contact details', 'no contact', 'unable to locate', 'poc']
            },
            'follow_up': {
                'sections': ['Follow Up', 'Follow-Up', 'Followup', 'Follow-up'],
                'keywords': ['follow up', 'follow-up', 'attempts']
            },
            'trigger': {
                'sections': ['Trigger', 'Status', 'Status Code', 'Sent -'],
                'keywords': ['trigger', 'status', 'code']
            },
            'close_order': {
                'sections': ['Closeout', 'CNR Closeout', 'Affidavit Closeout', 'Close Order'],
                'keywords': ['close order', 'closeout', 'attempts', 'close out']
            },
            'partial_records': {
                'sections': ['Partial Records', 'Medical Only', 'Billing Only', 'Records Received'],
                'keywords': ['partial records', 'only medical', 'only billing', 'incomplete']
            }
        }

    def generate_professional_response(self, query: str, chunks: List[Dict[str, Any]]) -> str:
        """Generate a professional response with complete content"""
        if not chunks:
            return "I could not find this information in the available SOP documents. Please try rephrasing your question."

        # Identify the topic
        topic = self._identify_topic(query)
        print(f"🎯 Topic: {topic}")

        # Find the best chunk
        best_chunk = self._find_best_chunk(chunks, query, topic)
        
        if not best_chunk:
            return "I could not find specific information about this topic in the available SOP documents."

        # Format the response with complete content
        response = self._format_response(best_chunk, topic, query)
        
        return response

    def _identify_topic(self, query: str) -> str:
        """Identify the topic from the query"""
        query_lower = query.lower()
        
        # Check for specific intent patterns
        if 'mark an order as offsite' in query_lower or 'when to mark' in query_lower:
            return 'offsite'
        if 'after how many attempts' in query_lower or 'close an order' in query_lower:
            return 'close_order'
        if 'partial records' in query_lower or 'only medical' in query_lower or 'only billing' in query_lower:
            return 'partial_records'
        if 'void check' in query_lower or 'voided' in query_lower:
            return 'void_check'
        if 'serve new address' in query_lower or 'sna' in query_lower:
            return 'serve_address'
        if 'non compliance' in query_lower or 'noncompliant' in query_lower:
            return 'non_compliance'
        if 'offsite' in query_lower:
            return 'offsite'
        if 'invoice' in query_lower:
            return 'invoice'
        if 'xray' in query_lower or 'x-ray' in query_lower:
            return 'xray'
        if 'depo' in query_lower:
            return 'depo'
        if 'vpu' in query_lower or 'vtc' in query_lower:
            return 'vpu'
        if 'follow up' in query_lower:
            return 'follow_up'
        if 'trigger' in query_lower or 'status' in query_lower:
            return 'trigger'
        if 'unable to locate' in query_lower or 'no contact' in query_lower:
            return 'facility_contact'
        
        return 'general'

    def _find_best_chunk(self, chunks: List[Dict[str, Any]], query: str, topic: str) -> Dict[str, Any]:
        """Find the most relevant chunk"""
        query_lower = query.lower()
        query_words = [w for w in query_lower.split() if len(w) > 2]
        
        scored_chunks = []
        for chunk in chunks:
            text = chunk['text']
            header = chunk['metadata'].get('header', '')
            doc_name = chunk['metadata'].get('document_name', '')
            
            score = 0
            
            # Check for exact section match
            if topic in self.section_mappings:
                sections = self.section_mappings[topic]['sections']
                for section in sections:
                    if section in header or section in text[:500]:
                        score += 30
                        break
            
            # Check for query words in header
            for word in query_words:
                if word in header.lower():
                    score += 5
                elif word in text.lower()[:500]:
                    score += 2
            
            # Check for section numbers
            section_match = re.search(r'(\d+\.\d+)', query)
            if section_match:
                section_num = section_match.group()
                if section_num in text or section_num in header:
                    score += 25
            
            # Specific intent bonuses
            if 'mark an order as offsite' in query_lower and 'offsite' in header.lower():
                score += 20
            if 'after how many attempts' in query_lower and 'closeout' in header.lower():
                score += 20
            if 'partial records' in query_lower and 'partial' in header.lower():
                score += 20
            
            # Penalize unrelated sections
            if 'offsite' in query_lower and 'trigger' in header.lower() and 'offsite' not in header.lower():
                score -= 15
            if 'close' in query_lower and 'qc' in header.lower():
                score -= 15
            if 'partial' in query_lower and 'status' in header.lower() and 'partial' not in header.lower():
                score -= 10
            
            scored_chunks.append((score, chunk, header))
        
        # Sort by score
        scored_chunks.sort(key=lambda x: x[0], reverse=True)
        
        # Debug output
        for score, chunk, header in scored_chunks[:3]:
            print(f"  Score: {score} - Header: {header}")
        
        # Return the best chunk
        for score, chunk, _ in scored_chunks:
            if score > 0:
                return chunk
        
        return scored_chunks[0][1] if scored_chunks else None

    def _extract_complete_content(self, text: str, query: str, topic: str) -> List[str]:
        """Extract complete content from the text"""
        lines = text.split('\n')
        content_lines = []
        seen_content = set()
        
        query_lower = query.lower()
        query_words = [w for w in query_lower.split() if len(w) > 2]
        start_index = 0
        found_section = False
        
        # Find the section start
        for i, line in enumerate(lines):
            line_clean = line.strip()
            
            # Check for specific patterns first
            if 'mark an order as offsite' in query_lower and 'offsite' in line_clean.lower():
                if 'trigger' not in line_clean.lower():
                    start_index = i
                    found_section = True
                    break
            
            if 'after how many attempts' in query_lower and ('closeout' in line_clean.lower() or 'attempt' in line_clean.lower()):
                if 'qc' not in line_clean.lower():
                    start_index = i
                    found_section = True
                    break
            
            if 'partial records' in query_lower and ('partial' in line_clean.lower() or 'only' in line_clean.lower()):
                if 'status' not in line_clean.lower():
                    start_index = i
                    found_section = True
                    break
            
            # Default section matching
            if topic in self.section_mappings:
                sections = self.section_mappings[topic]['sections']
                for section in sections:
                    if section in line_clean:
                        start_index = i
                        found_section = True
                        break
                if found_section:
                    break
            
            # Check for header pattern
            if line_clean.startswith('##') and any(word in line_clean.lower() for word in query_words):
                start_index = i
                found_section = True
                break
        
        # If no section found, start from beginning
        if not found_section:
            start_index = 0
        
        # Extract content
        for i in range(start_index, len(lines)):
            line = lines[i].strip()
            if not line:
                continue
            
            # Skip markdown artifacts
            if line.startswith('[') or line.startswith('#') or line.startswith('---'):
                continue
            if line.isdigit():
                continue
            if 'Page' in line and any(c.isdigit() for c in line):
                continue
            
            # Check if we've hit a new section
            if i > start_index:
                if line.startswith('##') or line.startswith('#'):
                    # Check if this is a continuation
                    is_continuation = False
                    for section in self.section_mappings.get(topic, {}).get('sections', []):
                        if section.lower() in line.lower():
                            is_continuation = True
                            break
                    if not is_continuation:
                        # Check if next lines are related
                        next_lines = []
                        for j in range(i+1, min(i+5, len(lines))):
                            if lines[j].strip():
                                next_lines.append(lines[j].strip())
                        next_text = ' '.join(next_lines).lower()
                        if not any(word in next_text for word in ['status', 'trigger', 'process', 'procedure', 'step', 'email', 'send']):
                            break
            
            # Clean and add the line
            clean_line = self._clean_line(line)
            if clean_line and len(clean_line) > 3:
                key = clean_line[:50]
                if key not in seen_content:
                    seen_content.add(key)
                    content_lines.append(clean_line)
        
        # If no content found, try to get from the whole text
        if not content_lines:
            for line in lines[:20]:
                clean_line = self._clean_line(line)
                if clean_line and len(clean_line) > 5:
                    content_lines.append(clean_line)
        
        return content_lines

    def _clean_line(self, line: str) -> str:
        """Clean a line"""
        line = line.replace('**', '').replace('##', '').replace('###', '')
        line = line.replace('[', '').replace(']', '')
        line = line.replace('*', '')
        line = re.sub(r'^[\s]*[-•*]\s+', '', line)
        line = re.sub(r'^[\s]*\d+[.)]\s+', '', line)
        line = ' '.join(line.split())
        return line.strip()

    def _format_response(self, chunk: Dict[str, Any], topic: str, query: str) -> str:
        """Format the response with complete content"""
        doc_name = chunk['metadata'].get('document_name', 'Unknown')
        header = chunk['metadata'].get('header', '')
        text = chunk['text'].strip()
        
        # Extract complete content
        content_lines = self._extract_complete_content(text, query, topic)
        
        # Ensure content_lines is a list
        if content_lines is None:
            content_lines = []
        
        # If no content found, try to get it from the chunk directly
        if not content_lines:
            lines = text.split('\n')
            for line in lines:
                clean_line = self._clean_line(line)
                if clean_line and len(clean_line) > 5:
                    content_lines.append(clean_line)
                if len(content_lines) > 15:
                    break
        
        # If still no content, use the first few lines
        if not content_lines:
            lines = text.split('\n')[:10]
            for line in lines:
                clean_line = self._clean_line(line)
                if clean_line and len(clean_line) > 5:
                    content_lines.append(clean_line)
        
        # Build the response
        response_parts = []
        
        # Add the header
        display_header = self._get_display_header(header, topic)
        response_parts.append(f"📋 **{display_header}**")
        response_parts.append("")
        
        # Format content
        if len(content_lines) == 1 and len(content_lines[0]) > 100:
            # Single long line - format as paragraph
            paragraph = content_lines[0]
            paragraph = re.sub(r'\s+', ' ', paragraph)
            paragraph = re.sub(r'\.{2,}', '.', paragraph)
            if paragraph and paragraph[0].islower():
                paragraph = paragraph[0].upper() + paragraph[1:]
            response_parts.append(f"  {paragraph}")
        else:
            # Multiple lines - format as steps
            step_num = 1
            for line in content_lines[:20]:
                # Remove any leading numbers
                clean_line = re.sub(r'^\d+\.\s+', '', line)
                clean_line = re.sub(r'^[\s]*[-•*]\s+', '', clean_line)
                
                # Remove duplicate section markers
                clean_line = re.sub(r'^#\s+', '', clean_line)
                
                if clean_line and len(clean_line) > 3:
                    # Capitalize first letter
                    if clean_line[0].islower():
                        clean_line = clean_line[0].upper() + clean_line[1:]
                    
                    # Add period if missing
                    if not clean_line.endswith('.') and len(clean_line) > 20 and not clean_line.endswith(':'):
                        clean_line = clean_line + '.'
                    
                    # Remove trailing commas
                    clean_line = re.sub(r',\s*$', '.', clean_line)
                    
                    response_parts.append(f"  {step_num}. {clean_line}")
                    step_num += 1
        
        # If no content, add a message
        if len(response_parts) <= 2:
            response_parts.append("  No specific information found in the documentation.")
        
        # Add source
        response_parts.append("")
        response_parts.append("---")
        response_parts.append(f"*Source: {doc_name}*")
        
        return '\n'.join(response_parts)

    def _get_display_header(self, header: str, topic: str) -> str:
        """Get a clean display header"""
        if header:
            clean_header = header.replace('**', '').replace('##', '').strip()
            if clean_header and len(clean_header) > 5:
                return clean_header
        
        topic_headers = {
            'void_check': '11.3 Void Check Procedure',
            'serve_address': 'Serve New Address Process',
            'non_compliance': '21.1 Process of Non-compliance',
            'offsite': 'Offsite Process',
            'invoice': 'Invoice Process',
            'xray': 'X-Ray Breakdown Process',
            'depo': 'Depo Date Process',
            'vpu': 'VPU/VTC Status Usage',
            'facility_contact': 'Facility Contact Procedure',
            'follow_up': 'Follow-Up Process',
            'trigger': 'Trigger and Status Codes',
            'close_order': 'Closeout Attempts Process',
            'partial_records': 'Partial Records Process'
        }
        return topic_headers.get(topic, 'SOP Information')