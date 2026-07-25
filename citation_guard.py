import os
import re
import json
from datetime import datetime
from typing import List, Dict

AUDIT_DIR = os.path.join(os.path.dirname(__file__), "audit_logs")

def get_normalized_act(act_name: str) -> str:
    """Normalizes act names to standard abbreviations."""
    act_clean = re.sub(r'[^a-zA-Z]', '', act_name).upper()
    if "NYAYA" in act_clean or "BNS" in act_clean:
        return "BNS"
    elif "NAGARIK" in act_clean or "BNSS" in act_clean:
        return "BNSS"
    elif "SAKSHYA" in act_clean or "BSA" in act_clean or "AKSHYA" in act_clean:
        return "BSA"
    elif "POCSO" in act_clean:
        return "POCSO"
    elif "DRUG" in act_clean or "NDPS" in act_clean:
        return "NDPS"
    elif "UAPA" in act_clean:
        return "UAPA"
    elif "LAUNDERING" in act_clean or "PMLA" in act_clean:
        return "PMLA"
    elif "CONSTITUTION" in act_clean or "CONST" in act_clean:
        return "Constitution"
    elif "NEGOTIABLE" in act_clean or "NIACT" in act_clean or "NI" in act_clean:
        return "NI_Act"
    return act_clean

def is_fuzzy_match(quote_clean: str, chunk_text_clean: str, threshold: float = 0.65) -> bool:
    """Checks substring inclusion, word overlap, and SequenceMatcher similarity between clean quote excerpt and chunk text."""
    if len(quote_clean) < 10:
        return quote_clean in chunk_text_clean
    if quote_clean in chunk_text_clean:
        return True
    
    # Word-level overlap check
    q_words = set(quote_clean.split())
    c_words = set(chunk_text_clean.split())
    if not q_words:
        return False
    intersection = q_words.intersection(c_words)
    overlap = len(intersection) / len(q_words)
    if overlap >= threshold:
        return True
        
    # SequenceMatcher check on sliding window
    if overlap >= 0.35:
        from difflib import SequenceMatcher
        words_c = chunk_text_clean.split()
        words_q = quote_clean.split()
        len_q = len(words_q)
        for i in range(max(1, len(words_c) - len_q + 1)):
            sub_c = " ".join(words_c[i:i+len_q])
            if SequenceMatcher(None, quote_clean, sub_c).ratio() >= threshold:
                return True
    return False

def verify_citations(response: str, retrieved_chunks: List[Dict]) -> Dict:
    """
    Validates LLM-generated citations and verbatim quotes against retrieved chunks context.
    If fails, logs the audit data and flags the action to return insufficient data.
    """
    failed_citations = []
    failed_quotes = []

    # 1. Build a set of valid retrieved (Act, Section) pairs
    valid_retrieved = set()
    for chunk in retrieved_chunks:
        act = get_normalized_act(chunk.get("act", ""))
        section = str(chunk.get("section", "")).strip()
        if act and section:
            valid_retrieved.add((act, section))

    # 2. Extract section citations using Regex
    # Pattern A: <Act> Section <Number>
    pattern_a = r'\b(BNS|BNSS|BSA|POCSO|NDPS|UAPA|PMLA|Constitution|NI_Act|IT_Act|SC_ST_Act)\s*(?:20\d{2})?\s*(?:[Ss]ection|[Aa]rticle|[Ss]ec\.?|[Aa]rt\.?)\s*(\d+[A-Z]?)\b'
    citations_a = re.findall(pattern_a, response)

    # Pattern B: Section <Number> of <Act>
    pattern_b = r'\b(?:[Ss]ection|[Aa]rticle|[Ss]ec\.?|[Aa]rt\.?)\s*(\d+[A-Z]?)\s+(?:of|u/s|under\s+section|under)\s+(BNS|BNSS|BSA|POCSO|NDPS|UAPA|PMLA|Constitution|NI_Act|IT_Act|SC_ST_Act)\b'
    citations_b = re.findall(pattern_b, response)

    citations_found = []
    for act, section in citations_a:
        citations_found.append((act, section))
    for section, act in citations_b:
        citations_found.append((act, section))

    for act, section in citations_found:
        normalized_act = get_normalized_act(act)
        section_str = str(section).strip()
        if (normalized_act, section_str) not in valid_retrieved:
            failed_citations.append(f"{act} Section {section}")

    # 3. Extract quoted text blocks from the response
    quoted_texts = []
    is_json = False
    
    try:
        from engine import _extract_json
        data = _extract_json(response)
        is_json = True
        
        # Traverse JSON recursively to find citation fields
        def extract_quotes(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in ["verbatim_text", "exact_verbatim_citation", "text"] and isinstance(v, str) and len(v.strip()) > 10:
                        quoted_texts.append(v.strip())
                    else:
                        extract_quotes(v)
            elif isinstance(obj, list):
                for item in obj:
                    extract_quotes(item)
        extract_quotes(data)
    except Exception:
        pass

    # If it is not valid JSON, fall back to regex double quote extraction
    if not is_json:
        raw_quotes = re.findall(r'"([^"]{15,})"', response)
        for q in raw_quotes:
            q_strip = q.strip()
            # Filter out obvious JSON structure fragments or direct keys
            if not q_strip.endswith(":") and not any(k in q_strip for k in ["status", "applicable_laws", "disclaimer"]):
                quoted_texts.append(q_strip)

    # 4. Check each quote exists inside any retrieved chunk's verbatim text
    for quote in quoted_texts:
        found = False
        parts = [p.strip() for p in re.split(r'\.\.\.|…', quote) if len(p.strip()) > 5]
        
        if len(parts) > 1:
            for chunk in retrieved_chunks:
                chunk_text = chunk.get("text", "")
                chunk_text_clean = re.sub(r'\s+', ' ', chunk_text.lower()).strip()
                chunk_text_clean = re.sub(r'[^\w\s]', '', chunk_text_clean)
                
                match_all = True
                for part in parts:
                    part_clean = re.sub(r'\s+', ' ', part.lower()).strip()
                    part_clean = re.sub(r'[^\w\s]', '', part_clean)
                    if not is_fuzzy_match(part_clean, chunk_text_clean, threshold=0.75):
                        match_all = False
                        break
                if match_all:
                    found = True
                    break
        else:
            quote_clean = re.sub(r'\s+', ' ', quote.lower()).strip()
            quote_clean = re.sub(r'[^\w\s]', '', quote_clean)
            for chunk in retrieved_chunks:
                chunk_text = chunk.get("text", "")
                chunk_text_clean = re.sub(r'\s+', ' ', chunk_text.lower()).strip()
                chunk_text_clean = re.sub(r'[^\w\s]', '', chunk_text_clean)
                if is_fuzzy_match(quote_clean, chunk_text_clean, threshold=0.75):
                    found = True
                    break
                    
        if not found:
            failed_quotes.append(quote if len(quote) < 80 else quote[:77] + "...")

    # Determine final action
    is_valid = (len(failed_citations) == 0 and len(failed_quotes) == 0)
    action = "PASS" if is_valid else "RETURN_INSUFFICIENT"

    if not is_valid:
        # Log to audit_logs/citation_failures_{date}.jsonl
        os.makedirs(AUDIT_DIR, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        log_file_path = os.path.join(AUDIT_DIR, f"citation_failures_{date_str}.jsonl")
        
        audit_entry = {
            "timestamp": datetime.now().isoformat(),
            "failed_citations": failed_citations,
            "failed_quotes": failed_quotes,
            "response_snippet": response[:200] + "...",
            "retrieved_context_sections": [f"{get_normalized_act(c.get('act',''))} Section {c.get('section','')}" for c in retrieved_chunks]
        }
        
        with open(log_file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(audit_entry) + "\n")

    return {
        "valid": is_valid,
        "failed_citations": list(set(failed_citations)),  # Deduplicate
        "failed_quotes": list(set(failed_quotes)),
        "action": action
    }

if __name__ == "__main__":
    # Test case
    mock_retrieved = [
        {"act": "Bharatiya Nyaya Sanhita (BNS) 2023", "section": "308", "text": "Whoever commits extortion shall be punished with imprisonment..."},
        {"act": "Bharatiya Nagarik Suraksha Sanhita (BNSS) 2023", "section": "438", "text": "Direction for grant of bail to person apprehending arrest..."}
    ]
    
    # Test 1: Successful validation
    valid_response = json.dumps({
        "status": "SUCCESS",
        "applicable_laws": [{
            "law_name": "BNS Section 308",
            "exact_verbatim_citation": "Whoever commits extortion shall be punished with imprisonment..."
        }]
    })
    print("Testing valid response:")
    print(verify_citations(valid_response, mock_retrieved))
    
    # Test 2: Hallucinated citation (BNS Section 999 not retrieved)
    invalid_response_1 = json.dumps({
        "status": "SUCCESS",
        "applicable_laws": [{
            "law_name": "BNS Section 999",
            "exact_verbatim_citation": "Whoever commits extortion shall be punished with imprisonment..."
        }]
    })
    print("\nTesting invalid response (hallucinated citation):")
    print(verify_citations(invalid_response_1, mock_retrieved))

    # Test 3: Hallucinated quote text
    invalid_response_2 = json.dumps({
        "status": "SUCCESS",
        "applicable_laws": [{
            "law_name": "BNS Section 308",
            "exact_verbatim_citation": "This is a completely fabricated quote that does not exist in bare acts."
        }]
    })
    print("\nTesting invalid response (hallucinated quote text):")
    print(verify_citations(invalid_response_2, mock_retrieved))
