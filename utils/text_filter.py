# utils/text_filter.py

def extract_relevant_sections(text, query, doc_name=""):
    """
    Extract only the most relevant sections from the text based on the query.
    Returns a list of relevant sections with titles and content.
    """
    lines = text.split('\n')
    query_lower = query.lower()
    query_words = [w for w in query_lower.split() if len(w) > 2]
    
    if not query_words:
        return [{'title': 'Content', 'content': text[:500]}]
    
    relevant_sections = []
    current_section = []
    section_title = ""
    section_found = False
    relevance_score = 0
    
    for line in lines:
        line_stripped = line.strip()
        
        # Check if this is a section header
        is_header = (
            line_stripped.startswith('##') or 
            (line_stripped and line_stripped[0].isdigit() and '.' in line_stripped[:5]) or
            line_stripped.upper().startswith('SECTION') or
            line_stripped.upper().startswith('STEP')
        )
        
        if is_header:
            # Save previous section if it was relevant
            if current_section and section_found and relevance_score > 0:
                relevant_sections.append({
                    'title': section_title or 'Relevant Content',
                    'content': '\n'.join(current_section),
                    'relevance': relevance_score
                })
            
            # Start new section
            section_title = line_stripped
            current_section = [line]
            section_found = False
            relevance_score = 0
            
            # Check if this section header contains query terms
            for word in query_words[:5]:
                if word in line.lower():
                    section_found = True
                    relevance_score += 2
        else:
            current_section.append(line)
            
            # Check if this line contains query terms
            if not section_found:
                for word in query_words[:5]:
                    if word in line.lower():
                        section_found = True
                        relevance_score += 1
            
            # Also check for partial matches
            if not section_found:
                for word in query_words[:5]:
                    if word[:3] in line.lower() or word in line.lower():
                        section_found = True
                        relevance_score += 0.5
    
    # Save the last section
    if current_section and section_found and relevance_score > 0:
        relevant_sections.append({
            'title': section_title or 'Relevant Content',
            'content': '\n'.join(current_section),
            'relevance': relevance_score
        })
    
    # Sort by relevance
    relevant_sections.sort(key=lambda x: x.get('relevance', 0), reverse=True)
    
    # If no sections found, try to find paragraphs with query terms
    if not relevant_sections:
        paragraphs = text.split('\n\n')
        for para in paragraphs:
            score = 0
            for word in query_words[:5]:
                if word in para.lower():
                    score += 1
            if score > 0:
                relevant_sections.append({
                    'title': 'Relevant Content',
                    'content': para[:500] + ('...' if len(para) > 500 else ''),
                    'relevance': score
                })
                if len(relevant_sections) >= 3:
                    break
    
    return relevant_sections[:3]  # Return top 3 most relevant sections