import os
import json
import re
import time
import numpy as np
import faiss
from typing import List, Dict, Optional
from dotenv import load_dotenv

# Load environment variables
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(ENV_PATH)

DATA_FILE_PATH = os.path.join(os.path.dirname(__file__), "cases.json")
STORE_DIR = os.path.join(os.path.dirname(__file__), "store")

CASES_FAISS_PATH = os.path.join(STORE_DIR, "cases_faiss.bin")
CASES_METADATA_PATH = os.path.join(STORE_DIR, "cases_metadata.json")

# Global memory caches for FAISS index and metadata
CASES_FAISS_INDEX = None
CASES_METADATA = None

def build_cases_indices():
    """
    Builds the FAISS index and metadata files from cases.json.
    Uses Nomic (768-dim) query embedding function from engine.py.
    """
    from engine_v2 import get_query_embedding

    if not os.path.exists(DATA_FILE_PATH):
        raise FileNotFoundError(f"Database file not found at {DATA_FILE_PATH}")

    with open(DATA_FILE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
        cases = data.get("cases", [])

    print(f"Building Case indices for {len(cases)} cases...")
    all_embeddings = []
    metadata = []

    for case in cases:
        title = case["case_title"]
        facts = case["facts_summary"]
        print(f"Generating embedding for '{title}'...")
        emb = get_query_embedding(facts)
        all_embeddings.append(emb)
        metadata.append(case)
        time.sleep(0.5)  # Rate limit safety delay

    embeddings_np = np.array(all_embeddings, dtype=np.float32)
    
    # L2 normalize embeddings so that FAISS IndexFlatIP computes Cosine Similarity
    faiss.normalize_L2(embeddings_np)
    
    # 1024 dimensions for Voyage AI voyage-law-2 (matches Qdrant collection)
    faiss_index = faiss.IndexFlatIP(1024)
    faiss_index.add(embeddings_np)

    os.makedirs(STORE_DIR, exist_ok=True)
    faiss.write_index(faiss_index, CASES_FAISS_PATH)
    
    with open(CASES_METADATA_PATH, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
        
    print("✓ Landmark judgment FAISS index built and saved!")

def load_cases_indices():
    """
    Loads FAISS index and metadata into memory cache.
    If they do not exist, builds them first.
    """
    global CASES_FAISS_INDEX, CASES_METADATA
    
    # Check memory cache first
    if CASES_FAISS_INDEX is not None and CASES_METADATA is not None:
        return

    # If not on disk, build them
    if not (os.path.exists(CASES_FAISS_PATH) and os.path.exists(CASES_METADATA_PATH)):
        print("Cases indices not found on disk. Building them now...")
        build_cases_indices()

    print("Loading cases vector index into memory cache...")
    CASES_FAISS_INDEX = faiss.read_index(CASES_FAISS_PATH)
    with open(CASES_METADATA_PATH, "r", encoding="utf-8") as f:
        CASES_METADATA = json.load(f)
    print(f"✓ Cases indices successfully loaded! ({len(CASES_METADATA)} judgments)")

def retrieve_similar_judgments(query: str, applicable_sections: List[str], k: int = 3, query_embedding: Optional[List[float]] = None) -> List[Dict]:
    """
    Retrieves the top-k similar landmark judgments using semantic similarity on facts_summary
    boosted by section citation overlaps. Uses FAISS indexing and memory caching.
    """
    from engine_v2 import get_query_embedding

    # Ensure database and embeddings are loaded into memory cache
    load_cases_indices()

    if CASES_FAISS_INDEX is None or CASES_METADATA is None:
        return []

    # 1. Embed query and normalize it
    query_emb = query_embedding if query_embedding is not None else get_query_embedding(query)
    query_emb_np = np.array([query_emb], dtype=np.float32)
    faiss.normalize_L2(query_emb_np)
    
    # 2. Search FAISS index for all cases to allow metadata boosting
    n_cases = CASES_FAISS_INDEX.ntotal
    distances, indices = CASES_FAISS_INDEX.search(query_emb_np, n_cases)
    
    scored_results = []
    clean_applicable = {re.sub(r'[^a-zA-Z0-9]', '', s).upper() for s in applicable_sections}

    for idx_in_search, (case_idx, dist) in enumerate(zip(indices[0], distances[0])):
        case_idx = int(case_idx)
        if case_idx == -1:
            continue
            
        case = CASES_METADATA[case_idx]
        title = case["case_title"]
        
        # Base similarity score is the cosine distance from FAISS search
        similarity = float(dist)
        
        # 3. Boost score by 0.1 if sections_cited overlaps with applicable_sections
        has_overlap = False
        for sec in case.get("sections_cited", []):
            clean_cited = re.sub(r'[^a-zA-Z0-9]', '', sec).upper()
            if clean_cited in clean_applicable:
                has_overlap = True
                break
                
        final_score = similarity
        if has_overlap:
            final_score += 0.1
            
        scored_results.append({
            "case_title": title,
            "court": case["court"],
            "hearing_date": case["hearing_date"],
            "verdict": case["verbatim_verdict"],
            "similarity_score": round(final_score, 4),
            "court_observation": case["structural_observations"],
            "source_url": case["source_url"]
        })
        
    # 4. Sort descending by boosted similarity score and return top-k
    scored_results.sort(key=lambda x: x["similarity_score"], reverse=True)
    return scored_results[:k]

if __name__ == "__main__":
    query_text = "A husband suspects his wife of having an affair and shoots her lover in a sudden fit of rage."
    sections = ["BNS-103", "BNS-304"]
    
    print(f"Query: '{query_text}'")
    print(f"Applicable Sections: {sections}\n")
    
    results = retrieve_similar_judgments(query_text, sections, k=3)
    for idx, r in enumerate(results, 1):
        print(f"{idx}. {r['case_title']} ({r['court']}) - Score: {r['similarity_score']}")
