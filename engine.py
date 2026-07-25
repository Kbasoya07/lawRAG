import os
import json
import re
import ssl
import time
import math
import hashlib
from typing import List, Dict, Tuple, Optional, Any
import httpx
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from supabase import create_client
from qdrant_client import QdrantClient
from qdrant_client.models import SparseVector, Prefetch, FusionQuery, Fusion, Filter, FieldCondition, MatchValue

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

SUPABASE_URL       = os.getenv("SUPABASE_URL")
SUPABASE_KEY       = os.getenv("SUPABASE_KEY")
NOMIC_API_KEY      = os.getenv("NOMIC_API_KEY")
HF_TOKEN           = os.getenv("HF_TOKEN")
GROQ_API_KEY       = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY     = os.getenv("GEMINI_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
COHERE_API_KEY     = os.getenv("COHERE_API_KEY")
VOYAGE_API_KEY     = os.getenv("VOYAGE_API_KEY")
QDRANT_URL         = os.getenv("QDRANT_URL")
QDRANT_API_KEY     = os.getenv("QDRANT_API_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY) if (SUPABASE_URL and SUPABASE_KEY) else None
qdrant   = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=30) if QDRANT_URL else QdrantClient(":memory:")

_local_tokenizer: Optional[Any] = None
_local_model: Optional[Any] = None
QDRANT_COLLECTION = "law_sections_bge"

# API Endpoints
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
NOMIC_API_URL = "https://api-atlas.nomic.ai/v1/embedding/text"
HF_BGE_LARGE_URL = "https://router.huggingface.co/hf-inference/models/BAAI/bge-large-en-v1.5/pipeline/feature-extraction"
HF_RERANKER_LARGE_URL = "https://api-inference.huggingface.co/models/BAAI/bge-reranker-large"

# File Paths (kept for backward compat with cache)
STORE_DIR = os.path.join(BASE_DIR, "store")
os.makedirs(STORE_DIR, exist_ok=True)

# Bypass SSL verify issues on macOS Python sandboxes
SSL_CONTEXT = ssl._create_unverified_context()

CRITICAL_TERMS = {
    # NI Act - Cheque bounce (overrides BNS)
    'cheque': 'NI_Act',
    'cheque bounce': 'NI_Act',
    'dishonour': 'NI_Act',
    'bounced cheque': 'NI_Act',
    'promissory note': 'NI_Act',
    'negotiable instrument': 'NI_Act',
    
    # BNSS - Procedure (overrides BNS)
    'fir': 'BNSS',
    'first information report': 'BNSS',
    'arrest': 'BNSS',
    'bail': 'BNSS',
    'anticipatory bail': 'BNSS',
    'regular bail': 'BNSS',
    'warrant': 'BNSS',
    'remand': 'BNSS',
    'custody': 'BNSS',
    'chargesheet': 'BNSS',
    'trial': 'BNSS',
    'appeal': 'BNSS',
    'quashing': 'BNSS',
    'notice before arrest': 'BNSS',
    'e-fir': 'BNSS',
    'zero fir': 'BNSS',
    
    # BSA - Evidence (overrides BNS)
    'evidence': 'BSA',
    'admissible': 'BSA',
    'witness': 'BSA',
    'confession': 'BSA',
    'electronic evidence': 'BSA',
    'digital evidence': 'BSA',
    'burden of proof': 'BSA',
    'cross-examination': 'BSA',
    
    # POCSO - Child protection (overrides BNS)
    'child sexual': 'POCSO',
    'minor sexual': 'POCSO',
    'pocso': 'POCSO',
    'child abuse': 'POCSO',
    'penetrative assault': 'POCSO',
    
    # NDPS - Drugs (overrides BNS)
    'drug': 'NDPS',
    'narcotic': 'NDPS',
    'cannabis': 'NDPS',
    'heroin': 'NDPS',
    'cocaine': 'NDPS',
    
    # UAPA - Terrorism (overrides BNS)
    'terrorist': 'UAPA',
    'terrorism': 'UAPA',
    'uapa': 'UAPA',
    
    # PMLA - Money laundering (overrides BNS)
    'money laundering': 'PMLA',
    'enforcement directorate': 'PMLA',
    
    # Constitution
    'article 14': 'Constitution',
    'article 21': 'Constitution',
    'article 32': 'Constitution',
    'fundamental right': 'Constitution',
    'writ': 'Constitution',
    'habeas corpus': 'Constitution',
}

DEFENCE_SECTIONS = {
    ('BNS', 27): {'primary': ('BNS', 136), 'warning': 'Defence section - primary offence is child labour'},
    ('BNS', 88): {'primary': ('BNS', 100), 'warning': 'Defence section - primary offence is culpable homicide'},
    ('BNS', 89): {'primary': ('BNS', 115), 'warning': 'Defence section - primary offence is hurt'},
}

# ==========================================
# Pydantic Schemas
# ==========================================
class QuantitativeCheck(BaseModel):
    parameter: str = Field(description="What is being measured")
    statutory_limit: str = Field(description="The limit set by statute")
    actual_value: str = Field(description="The value in the user's scenario")
    result: str = Field(description="EXCEEDS_LIMIT | WITHIN_LIMIT | NOT_APPLICABLE")

class QualitativeCheck(BaseModel):
    condition: str = Field(description="The subjective test from the statute")
    factual_indicators: str = Field(description="Facts from the query relevant to this condition")
    assessment: str = Field(description="SATISFIED | NOT_SATISFIED | INDETERMINATE")

class StatutoryAnalysis(BaseModel):
    exceptions_found: List[str] = Field(default_factory=list, description="Exception/proviso clauses found in the section text")
    exception_applies: bool = Field(default=False, description="Whether any exception applies to these facts")
    exception_effect: str = Field(default="", description="How the exception changes the legal outcome")
    quantitative_checks: List[QuantitativeCheck] = Field(default_factory=list, description="Numerical limit comparisons")
    qualitative_checks: List[QualitativeCheck] = Field(default_factory=list, description="Subjective condition evaluations")

class LawAnalysis(BaseModel):
    law_name: str = Field(description="Full Act and Section, e.g. 'BNS 2023, Section 32'")
    confidence_score: float = Field(default=0.90, description="Relevance confidence score between 0.0 and 1.0 (e.g. 1.0 for direct match, 0.7-0.8 for conditional/defense, 0.5 for context)")
    verbatim_text: str = Field(description="Exact text from context — COPY-PASTE ONLY")
    role: str = Field(default="RELATED_PROVISION", description="PRIMARY_OFFENSE | DEFENSE_SECTION | SENTENCING_PROVISION | PROCEDURAL_PROVISION | RELATED_PROVISION")
    statutory_analysis: Optional[StatutoryAnalysis] = Field(default=None, description="Structured analysis of exceptions, limits, and conditions")
    plain_english_explanation: str = Field(description="Simple explanation a non-lawyer can understand")
    how_it_applies_to_facts: str = Field(description="Specific mapping of section elements to user's facts")

class ProceduralRemedy(BaseModel):
    recommended_action: str = Field(description="The correct legal step based on query type")
    bnss_provision: str = Field(description="The specific BNSS section for the remedy")
    court: str = Field(description="Which court has jurisdiction")

class FullResponse(BaseModel):
    status: str = Field(description="SUCCESS or INSUFFICIENT_DATA")
    conclusion: str = Field(default="", description="LAWFUL | UNLAWFUL | OFFENSE_ESTABLISHED | DEFENSE_AVAILABLE | DEFENSE_NOT_AVAILABLE | PARTIALLY_LAWFUL | REQUIRES_JUDICIAL_DETERMINATION")
    conclusion_summary: str = Field(default="", description="2-3 sentence plain-English verdict answering the user's question directly")
    legal_issue: str = Field(default="", description="The precise legal question identified")
    query_type: str = Field(default="", description="OFFENSE_APPLICABILITY | DEFENSE_EVALUATION | SENTENCING_REVIEW | PROCEDURAL_QUERY")
    applicable_laws: List[LawAnalysis] = Field(default_factory=list)
    procedural_remedy: Optional[ProceduralRemedy] = None
    mitigating_factors: List[str] = Field(default_factory=list)
    disclaimer: str = "This analysis is for educational and research purposes only. It does not constitute legal advice. Consult a certified advocate before taking any legal action."

# ==========================================
# Cloud Embeddings API client
# ==========================================
def _get_query_embedding_internal(text: str) -> List[float]:
    """Primary: local BAAI/bge-large-en-v1.5 (1024-dim) -> Fallback: Voyage AI voyage-law-2 (1024-dim)."""
    # Try local CPU model first
    global _local_tokenizer, _local_model
    try:
        if _local_model is None:
            from transformers import AutoTokenizer, AutoModel
            print("🛠️ Loading local CPU embedding model (BAAI/bge-large-en-v1.5)...")
            try:
                _local_tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-large-en-v1.5", local_files_only=True)
                _local_model = AutoModel.from_pretrained("BAAI/bge-large-en-v1.5", local_files_only=True)
            except Exception:
                _local_tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-large-en-v1.5")
                _local_model = AutoModel.from_pretrained("BAAI/bge-large-en-v1.5")
            print("✓ Local CPU embedding model loaded.")

        if _local_tokenizer is not None and _local_model is not None:
            import torch
            inputs = _local_tokenizer(text, padding=True, truncation=True, max_length=512, return_tensors="pt")
            with torch.no_grad():
                model_output = _local_model(**inputs)
                # BGE uses CLS pooling and normalization
                sentence_embeddings = model_output[0][:, 0]
                sentence_embeddings = torch.nn.functional.normalize(sentence_embeddings, p=2, dim=1)
                emb = sentence_embeddings[0].tolist()
                # BGE-large is 1024-dim natively.
                return emb[:1024]
    except Exception as e:
        print(f"WARNING: Local CPU embedding failed: {e}. Falling back to Voyage AI.")

    # Voyage AI Fallback
    if not VOYAGE_API_KEY:
        print("WARNING: VOYAGE_API_KEY not set and local model failed. Returning zero-filled list.")
        return [0.0] * 1024

    url = "https://api.voyageai.com/v1/embeddings"
    headers = {
        "Authorization": f"Bearer {VOYAGE_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "input": [text],
        "model": "voyage-law-2"
    }

    # Try Voyage AI with exponential backoff
    for attempt in range(4):
        try:
            r = httpx.post(url, headers=headers, json=payload, timeout=20.0)
            if r.status_code == 200:
                return r.json()["data"][0]["embedding"]
            elif r.status_code == 429:
                wait = 2 ** attempt
                print(f"Voyage embedding rate limited (429). Waiting {wait}s... (attempt {attempt+1}/4)")
                time.sleep(wait)
            else:
                r.raise_for_status()
        except Exception as ex:
            print(f"Voyage embedding attempt {attempt+1} failed: {ex}")
            if attempt < 3:
                time.sleep(1)
    
    raise RuntimeError("Both local embedding model and Voyage AI API failed.")

def get_query_embedding(text: str) -> List[float]:
    """Returns 1024-dim embedding vector with runtime size validation."""
    emb = _get_query_embedding_internal(text)
    if len(emb) != 1024:
        raise RuntimeError(f"Embedding dimension mismatch: got {len(emb)}-dim, expected 1024-dim for Qdrant.")
    return emb

# ==========================================
# Multi-LLM Rotation client
# ==========================================
def call_llm(messages: List[Dict], json_mode: bool = True) -> str:
    """Rotates through Gemini 2.5 Flash -> Groq -> Gemini 2.0 Flash -> OpenRouter with exponential backoff on 429s."""
    providers = [_call_gemini_25_flash, _call_groq, _call_gemini, _call_openrouter]
    last_error = None
    for fn in providers:
        for attempt in range(3):
            try:
                return fn(messages, json_mode)
            except Exception as e:
                last_error = e
                err_str = str(e).lower()
                if "429" in err_str or "resource_exhausted" in err_str or "rate limit" in err_str:
                    if attempt < 2:
                        sleep_time = 2 ** attempt  # 1s, 2s
                        print(f"LLM Provider {fn.__name__} rate limited. Retrying in {sleep_time}s... ({attempt+1}/3)")
                        time.sleep(sleep_time)
                    else:
                        # Final attempt also rate-limited — move to next provider
                        print(f"LLM Provider {fn.__name__} rate limited on all 3 attempts. Trying next provider.")
                        break
                else:
                    print(f"LLM Provider {fn.__name__} failed: {e}. Trying next provider.")
                    break
    raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")

def _call_groq(messages, json_mode):
    payload = {
        "messages": messages,
        "model": "llama-3.3-70b-versatile",
        "temperature": 0.1,
        "max_tokens": 4096
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    r = httpx.post(GROQ_API_URL, json=payload, headers=headers, timeout=40.0, verify=False)
    if r.status_code != 200:
        raise RuntimeError(f"Groq returned error {r.status_code}: {r.text}")
    return r.json()["choices"][0]["message"]["content"]

def _call_gemini(messages, json_mode):
    # Using Google AI Studio generative model API
    gemini_key = os.getenv("GEMINI_API_KEY")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={gemini_key}"
    contents = []
    system_instruction = None
    for m in messages:
        if m["role"] == "system":
            system_instruction = {"parts": [{"text": m["content"]}]}
        else:
            role = "model" if m["role"] == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m["content"]}]})
            
    payload = {"contents": contents, "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096}}
    if system_instruction:
        payload["systemInstruction"] = system_instruction
    if json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"
        
    headers = {"Content-Type": "application/json"}
    r = httpx.post(url, json=payload, headers=headers, timeout=40.0, verify=False)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini returned error {r.status_code}: {r.text}")
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]

def _call_gemini_25_flash(messages, json_mode):
    """Gemini 2.5 Flash — separate quota from 2.0 Flash, useful as a fallback."""
    gemini_key = os.getenv("GEMINI_API_KEY")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_key}"
    contents = []
    system_instruction = None
    for m in messages:
        if m["role"] == "system":
            system_instruction = {"parts": [{"text": m["content"]}]}
        else:
            role = "model" if m["role"] == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m["content"]}]})
            
    payload = {"contents": contents, "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096}}
    if system_instruction:
        payload["systemInstruction"] = system_instruction
    if json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"
        
    headers = {"Content-Type": "application/json"}
    r = httpx.post(url, json=payload, headers=headers, timeout=40.0, verify=False)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini-2.5-flash returned error {r.status_code}: {r.text}")
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]

def _call_openrouter(messages, json_mode):
    models = [
        "meta-llama/llama-3.3-70b-instruct:free",
        "google/gemma-4-31b-it:free",
        "qwen/qwen3-next-80b-a3b-instruct:free",
        "nousresearch/hermes-3-llama-3.1-405b:free",
        "meta-llama/llama-3.2-3b-instruct:free"
    ]
    
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}
    
    for model in models:
        try:
            payload = {
                "model": model,
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": 4096
            }
            r = httpx.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers, timeout=40.0, verify=False)
            if r.status_code == 200:
                print(f"✓ OpenRouter call succeeded with model: {model}")
                return r.json()["choices"][0]["message"]["content"]
            else:
                print(f"OpenRouter model {model} failed with status {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"OpenRouter model {model} failed: {e}")
            
    raise RuntimeError("All OpenRouter fallback models failed.")

# ==========================================
# Qdrant Warmup (replaces BM25 index build)
# ==========================================
_models_ready = False

def load_indices():
    """Warmup: preload reranker + embedding models. Qdrant index is already live in cloud."""
    global _models_ready, _local_reranker, _local_tokenizer, _local_model
    if _models_ready:
        return

    if _local_reranker is None:
        from transformers import pipeline
        print("🛠️ Pre-loading local CPU Reranker model...")
        try:
            _local_reranker = pipeline("text-classification", model="BAAI/bge-reranker-base", device=-1, local_files_only=True)
        except Exception:
            _local_reranker = pipeline("text-classification", model="BAAI/bge-reranker-base", device=-1)
        print("✓ Local CPU Reranker model pre-loaded.")

    if _local_model is None:
        from transformers import AutoTokenizer, AutoModel
        print("🛠️ Pre-loading local CPU Embedding model (BAAI/bge-large-en-v1.5)...")
        try:
            _local_tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-large-en-v1.5", local_files_only=True)
            _local_model = AutoModel.from_pretrained("BAAI/bge-large-en-v1.5", local_files_only=True)
        except Exception:
            _local_tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-large-en-v1.5")
            _local_model = AutoModel.from_pretrained("BAAI/bge-large-en-v1.5")
        print("✓ Local CPU Embedding model pre-loaded.")

    _models_ready = True
    print("✓ Qdrant hybrid search ready (1530 sections indexed in cloud).")

# ==========================================
# Act Routing and Redirects
# ==========================================
def normalize_act_name(act):
    if not act: return act
    act_upper = act.upper()
    if act_upper in ["NI_ACT", "NI"]: return "NI"
    if act_upper in ["IT_ACT", "IT"]: return "IT"
    if act_upper in ["SC_ST_ACT", "SC_ST"]: return "SC_ST"
    if act_upper in ["DOWRY_PROHIBITION", "DOWRY"]: return "Dowry"
    if act_upper in ["PREVENTION_OF_CORRUPTION", "CORRUPTION"]: return "Corruption"
    if act_upper in ["JJ_ACT", "JJ"]: return "JJ"
    return act

def normalize_returned_act_name(meta):
    if not meta: return
    act_val = meta.get("act")
    if act_val == "NI": meta["act"] = "NI_Act"
    elif act_val == "IT": meta["act"] = "IT_Act"
    elif act_val == "SC_ST": meta["act"] = "SC_ST_Act"
    elif act_val == "JJ": meta["act"] = "JJ_Act"
    elif act_val == "Dowry": meta["act"] = "Dowry_Prohibition"
    elif act_val == "Corruption": meta["act"] = "Prevention_of_Corruption"

class ActSpecificRouter:
    ACT_KEYWORDS = {
        'BNS': ['murder', 'theft', 'rape', 'cheating', 'assault', 'extortion', 'criminal intimidation', 'hurt', 'wrongful', 'death', 'culpable homicide', 'dacoity', 'robbery', 'forgery', 'counterfeit', 'sedition', 'treason', 'burglary', 'stolen', 'burgled', 'loot', 'house breaking', 'housebreaking', 'trespassing', 'locker', 'solitary confinement', 'rigorous imprisonment', 'imprisonment', 'punishment', 'fraud', 'kidnapping', 'abduction', 'dowry', 'cruelty', 'domestic violence', 'abetment', 'conspiracy', 'attempt to murder', 'defamation', 'slander', 'mischief', 'arson', 'vandalism', 'bribery', 'corruption', 'rioting', 'unlawful assembly', 'stalking', 'voyeurism', 'outraging modesty', 'wrongful confinement', 'false imprisonment', 'cybercrime', 'hacking', 'grievous hurt', 'causing death', 'negligence', 'rash', 'intoxication', 'self defence', 'private defence', 'insanity', 'unsound mind', 'minor', 'age of criminal responsibility', 'child', 'infancy', 'infant'],
        'BNSS': ['fir', 'first information report', 'arrest', 'bail', 'anticipatory bail', 'regular bail', 'warrant', 'search', 'seizure', 'remand', 'custody', 'interrogation', 'detention', 'trial', 'appeal', 'quashing', 'chargesheet', 'police station', 'magistrate', 'court', 'notice before arrest', 'zero fir', 'e-fir', 'investigation', 'prosecution', 'forensic', 'forensics', 'fingerprint', 'finger-printing', 'investigating officer', 'officer refuses', 'procedural law', 'new law', 'dispatch', 'harvesting prints', 'scene of crime', 'officer stance', 'new procedural', 'summons', 'summon', 'plea bargaining', 'compounding'],
        'BSA': ['evidence', 'admissible', 'admissibility', 'witness', 'confession', 'electronic evidence', 'digital evidence', 'document', 'proof', 'burden of proof', 'presumption', 'examination', 'cross-examination', 'relevant', 'fact', 'opinion', 'expert', 'forensic'],
        'POCSO': ['child', 'minor', 'sexual assault', 'penetrative assault', 'pocso', 'child sexual', 'child abuse', 'juvenile', 'under 18', 'child-friendly', 'mandatory reporting', 'compensation', 'special court', 'child victim'],
        'NI_Act': ['cheque', 'cheque bounce', 'dishonour', 'bounced cheque', 'promissory note', 'negotiable instrument', 'bank', 'return memo', 'legal notice', 'magistrate complaint', 'quasi-criminal'],
        'NDPS': ['drug', 'narcotic', 'psychotropic', 'cannabis', 'heroin', 'cocaine', 'opium', 'poppy', 'manufacture', 'possession', 'consumption', 'trafficking', 'small quantity', 'commercial quantity', 'intermediate quantity'],
        'UAPA': ['terrorist', 'terrorism', 'terrorist act', 'unlawful activity', 'national security', 'banned organisation', 'uapa', 'preventive detention', 'terror funding', 'radicalisation'],
        'PMLA': ['money laundering', 'proceeds of crime', 'attachment', 'enforcement directorate', 'financial transaction', 'predicate offence'],
        'Constitution': ['article', 'fundamental right', 'constitutional', 'writ', 'habeas corpus', 'supreme court', 'high court', 'equality', 'freedom', 'liberty', 'life', 'article 14', 'article 21', 'article 32', 'article 226']
    }

    @classmethod
    def detect_acts(cls, query):
        query_lower = query.lower()
        act_scores = {}
        for term, act in CRITICAL_TERMS.items():
            pattern = r'\b' + re.escape(term) + r'\b'
            if re.search(pattern, query_lower):
                act_scores[act] = act_scores.get(act, 0.0) + 100.0
        for act, keywords in cls.ACT_KEYWORDS.items():
            score = sum(1 for kw in keywords if re.search(r'\b' + re.escape(kw) + r'\b', query_lower))
            if score > 0:
                act_scores[act] = act_scores.get(act, 0.0) + score
        sorted_acts = sorted(act_scores.items(), key=lambda x: x[1], reverse=True)

        # If top act has a CRITICAL_TERMS hit (score >= 100), it is a specialist law.
        # Only include acts within 50% of the top score to avoid diluting retrieval
        # with unrelated acts from minor keyword matches (e.g. 'cheque bounce' returning BNS).
        if sorted_acts and sorted_acts[0][1] >= 100:
            top_score = sorted_acts[0][1]
            dominant = [act for act, score in sorted_acts if score >= top_score * 0.5]
            return dominant[:2]

        # For general queries, return top 3 acts so procedural queries
        # with BNSS keywords aren't squeezed out by incidental matches
        if len(sorted_acts) >= 3:
            return [act for act, score in sorted_acts[:3]]
        elif len(sorted_acts) >= 1:
            return [act for act, score in sorted_acts]
        return ['BNS', 'BNSS', 'BSA']

HARD_REDIRECTS = {
    'cheque bounce': {'act': 'NI_Act', 'section': 138, 'reason': 'Special law — NI Act overrides BNS'},
    'bounced cheque': {'act': 'NI_Act', 'section': 138, 'reason': 'Special law — NI Act overrides BNS'},
    'cheque dishonour': {'act': 'NI_Act', 'section': 138, 'reason': 'Special law — NI Act overrides BNS'},
    'dishonoured cheque': {'act': 'NI_Act', 'section': 138, 'reason': 'Special law — NI Act overrides BNS'},
    'cheque that bounced': {'act': 'NI_Act', 'section': 138, 'reason': 'Special law — NI Act overrides BNS'},
    'gave me a cheque': {'act': 'NI_Act', 'section': 138, 'reason': 'Special law — NI Act overrides BNS'},
    'anticipatory bail': {'act': 'BNSS', 'section': 438, 'reason': 'Procedural — BNSS only'},
    'regular bail': {'act': 'BNSS', 'section': 437, 'reason': 'Procedural — BNSS only'},
    'fir': {'act': 'BNSS', 'section': 173, 'reason': 'Procedural — BNSS only'},
    'first information report': {'act': 'BNSS', 'section': 173, 'reason': 'Procedural — BNSS only'},
    'electronic evidence': {'act': 'BSA', 'section': 61, 'reason': 'Evidence — BSA only'},
    'digital evidence': {'act': 'BSA', 'section': 61, 'reason': 'Evidence — BSA only'},
    'child was sexually assaulted': {'act': 'POCSO', 'section': 4, 'reason': 'Special law — POCSO overrides BNS'},
    'punishment for murder': {'act': 'BNS', 'section': 103, 'reason': 'Substantive crime — murder'},
    'article 14': {'act': 'Constitution', 'section': 14, 'reason': 'Constitutional article'},
    'article 21': {'act': 'Constitution', 'section': 21, 'reason': 'Constitutional article'}
}

IPC_TO_BNS_MAP: Dict[str, str] = {
    "302": "103", "307": "109", "376": "64", "379": "303", "380": "305",
    "392": "309", "395": "310", "406": "316", "411": "317", "420": "318",
    "447": "329", "448": "329", "452": "331", "34": "3", "120b": "61",
    "147": "191", "148": "191", "149": "190", "279": "281", "304a": "106",
    "304b": "80", "323": "115", "324": "117", "325": "117", "326": "117",
    "336": "125", "337": "125", "338": "125", "341": "126", "342": "126",
    "354": "74", "354a": "75", "354b": "76", "354c": "77", "354d": "78",
    "363": "137", "498a": "85", "506": "351", "509": "79"
}

def apply_hard_redirect(query):
    query_lower = query.lower()
    for term, redirect in HARD_REDIRECTS.items():
        if term in query_lower:
            return {'redirect': True, 'act': redirect['act'], 'section': redirect['section'], 'reason': redirect['reason']}
            
    # Map IPC to BNS if query mentions IPC/Indian Penal Code
    is_ipc = "ipc" in query_lower or "indian penal code" in query_lower
    sec_match = re.search(r'\b(?:section|sec|s\.?)\s*(\d+[a-z]?)\b', query_lower)
    art_match = re.search(r'\b(?:article|art\.?)\s*(\d+)\b', query_lower)

    if is_ipc and sec_match:
        ipc_sec = sec_match.group(1)
        if ipc_sec in IPC_TO_BNS_MAP:
            bns_sec = IPC_TO_BNS_MAP[ipc_sec]
            try:
                hits, _ = qdrant.scroll(
                    collection_name=QDRANT_COLLECTION,
                    scroll_filter=Filter(must=[
                        FieldCondition(key="act",     match=MatchValue(value="BNS")),
                        FieldCondition(key="section", match=MatchValue(value=str(bns_sec)))
                    ]),
                    limit=1,
                    with_payload=True,
                    with_vectors=False
                )
                if hits:
                    exact = dict(hits[0].payload)
                    exact["rrf_score"] = 2.0 / 60.0
                    exact["confidence_score"] = 1.0
                    exact["redirect_reason"] = f"IPC Section {ipc_sec} mapped to BNS Section {bns_sec}"
                    normalize_returned_act_name(exact)
                    return {'redirect': True, 'pre_resolved': True, 'match': exact}
            except Exception as e:
                print(f"IPC redirect Qdrant lookup failed: {e}")

    # Try exact section regex match e.g. "BNS Section 308" or "Article 21"
    act_target = None
    if re.search(r'\bbnss\b', query_lower): act_target = "BNSS"
    elif re.search(r'\bbns\b', query_lower): act_target = "BNS"
    elif re.search(r'\bbsa\b', query_lower): act_target = "BSA"
    elif re.search(r'\bpocso\b', query_lower): act_target = "POCSO"
    elif re.search(r'\bni\b|\bnegotiable\b', query_lower): act_target = "NI"
    elif re.search(r'\bit\s+act\b|\binformation\s+technology\b|\bcyber\b', query_lower): act_target = "IT"
    elif re.search(r'\bndps\b|\bnarcotic\b|\bdrug\b', query_lower): act_target = "NDPS"
    elif re.search(r'\bsc\s*/?\s*st\b|\batrocities\b', query_lower): act_target = "SC_ST"
    elif re.search(r'\bdowry\b', query_lower): act_target = "Dowry"
    elif re.search(r'\bpmla\b|\bmoney\s+laundering\b', query_lower): act_target = "PMLA"
    elif re.search(r'\bnsa\b|\bnational\s+security\b', query_lower): act_target = "NSA"
    elif re.search(r'\bcorruption\b', query_lower): act_target = "Corruption"
    elif re.search(r'\buapa\b', query_lower): act_target = "UAPA"
    elif re.search(r'\bjj\s+act\b|\bjuvenile\b', query_lower): act_target = "JJ"
    elif re.search(r'\bconstitution\b', query_lower) or re.search(r'\barticle\s+\d+\b', query_lower): act_target = "Constitution"

    sec_match = re.search(r'\b(?:section|sec|s\.?)\s*(\d+[a-z]?)\b', query_lower)
    art_match = re.search(r'\b(?:article|art\.?)\s*(\d+)\b', query_lower)

    if act_target == "Constitution" or art_match:
        num = art_match.group(1) if art_match else (sec_match.group(1) if sec_match else None)
        if num:
            try:
                hits, _ = qdrant.scroll(
                    collection_name=QDRANT_COLLECTION,
                    scroll_filter=Filter(must=[
                        FieldCondition(key="act",     match=MatchValue(value="Constitution")),
                        FieldCondition(key="article", match=MatchValue(value=str(num)))
                    ]),
                    limit=1, with_payload=True, with_vectors=False
                )
                if hits:
                    exact = dict(hits[0].payload)
                    exact["rrf_score"] = 2.0 / 60.0
                    exact["confidence_score"] = 1.0
                    exact["redirect_reason"] = f"Exact article lookup for Article {num}"
                    normalize_returned_act_name(exact)
                    return {'redirect': True, 'pre_resolved': True, 'match': exact}
            except Exception as e:
                print(f"Qdrant article lookup failed: {e}")

    if act_target and sec_match:
        num = sec_match.group(1)
        norm_act = normalize_act_name(act_target)
        try:
            hits, _ = qdrant.scroll(
                collection_name=QDRANT_COLLECTION,
                scroll_filter=Filter(must=[
                    FieldCondition(key="act",     match=MatchValue(value=norm_act)),
                    FieldCondition(key="section", match=MatchValue(value=str(num)))
                ]),
                limit=1, with_payload=True, with_vectors=False
            )
            if hits:
                exact = dict(hits[0].payload)
                exact["rrf_score"] = 2.0 / 60.0
                exact["confidence_score"] = 1.0
                exact["redirect_reason"] = f"Exact section lookup for {norm_act} Section {num}"
                normalize_returned_act_name(exact)
                return {'redirect': True, 'pre_resolved': True, 'match': exact}
        except Exception as e:
            print(f"Qdrant section lookup failed: {e}")

    return {'redirect': False}

def apply_defence_sections(results: List[Dict]) -> List[Dict]:
    defence_results = []
    for chunk in results:
        act_val = str(chunk.get("act", ""))
        sec_raw = chunk.get("section")
        sec_val = int(sec_raw) if sec_raw is not None and str(sec_raw).isdigit() else None
        if sec_val is not None and (act_val, sec_val) in DEFENCE_SECTIONS:
            defence_cfg = DEFENCE_SECTIONS[(act_val, sec_val)]
            chunk["warning"] = defence_cfg["warning"]
            primary_act, primary_sec = defence_cfg["primary"]
            has_primary = any(c.get("act") == primary_act and str(c.get("section")) == str(primary_sec) for c in results)
            if not has_primary:
                try:
                    hits, _ = qdrant.scroll(
                        collection_name=QDRANT_COLLECTION,
                        scroll_filter=Filter(must=[
                            FieldCondition(key="act",     match=MatchValue(value=normalize_act_name(primary_act))),
                            FieldCondition(key="section", match=MatchValue(value=str(primary_sec)))
                        ]),
                        limit=1, with_payload=True, with_vectors=False
                    )
                    if hits and hits[0].payload:
                        primary_chunk = dict(hits[0].payload or {})
                        primary_chunk["rrf_score"] = chunk.get("rrf_score", 0.0)
                        primary_chunk["confidence_score"] = chunk.get("confidence_score", 1.0)
                        primary_chunk["warning"] = f"Automatically retrieved: primary offence for defence section {sec_val}"
                        normalize_returned_act_name(primary_chunk)
                        defence_results.append(primary_chunk)
                except Exception as e:
                    print(f"Defence section Qdrant lookup failed: {e}")
    results.extend(defence_results)
    return results

# ==========================================
# Hybrid Retrieval via Qdrant (BM25 sparse + BGE dense + RRF)
# ==========================================
def _build_sparse_vector(text: str) -> SparseVector:
    """Build a raw frequency term-based sparse vector for Qdrant sparse search."""
    tokens = re.findall(r'\w+', text.lower())
    freq: Dict[int, float] = {}
    for t in tokens:
        idx = abs(int(hashlib.md5(t.encode('utf-8')).hexdigest(), 16)) % 50000
        freq[idx] = freq.get(idx, 0) + 1.0
    return SparseVector(
        indices=list(freq.keys()),
        values=list(freq.values())
    )

def hybrid_retrieve(query: str, k: int = 15, act_filter: Optional[List[str]] = None) -> Tuple[List[Dict], float]:
    """Qdrant native hybrid search: dense (1024-dim) + sparse (BM25) with RRF fusion."""
    load_indices()  # warmup models

    act_filter_normalized = [normalize_act_name(a) for a in act_filter] if act_filter else None

    # ── Hard redirect / exact section lookup (unchanged logic) ──────────────
    redirect = apply_hard_redirect(query)
    if redirect.get('redirect'):
        if redirect.get('pre_resolved'):
            return apply_defence_sections([redirect['match']]), 1.0
        # Fetch exact section from Qdrant by payload filter
        act_target = normalize_act_name(redirect['act'])
        sec_target = str(redirect['section'])
        try:
            exact_hits, _ = qdrant.scroll(
                collection_name=QDRANT_COLLECTION,
                scroll_filter=Filter(must=[
                    FieldCondition(key="act",     match=MatchValue(value=act_target)),
                    FieldCondition(key="section", match=MatchValue(value=sec_target))
                ]),
                limit=1,
                with_payload=True,
                with_vectors=False
            )
            if exact_hits and exact_hits[0].payload is not None:
                exact = dict(exact_hits[0].payload)
                exact["rrf_score"] = 2.0 / 60.0
                exact["confidence_score"] = 1.0
                exact["redirect_reason"] = redirect["reason"]
                normalize_returned_act_name(exact)
                return apply_defence_sections([exact]), 1.0
        except Exception as e:
            print(f"Qdrant exact lookup failed: {e}")

    query_lower = query.lower()
    query_expanded = query
    # Heuristic expansions for common lay descriptions to bridge the semantic gap
    semantic_mappings = {
        # -- Physical offences --
        r'\bfracture\b': "BNS Section 117 Grievous Hurt bone fracture dislocation of bone or tooth",
        r'\bbroken bone\b': "BNS Section 117 Grievous Hurt bone fracture",
        r'\bbroken tooth\b': "BNS Section 117 Grievous Hurt dislocation of tooth",
        r'\bbroken teeth\b': "BNS Section 117 Grievous Hurt dislocation of tooth",
        r'\bdislocat(ed|ion)\b': "BNS Section 117 Grievous Hurt dislocation",
        r'\bhit with a rod\b': "BNS Section 117 BNS Section 118 Grievous Hurt with dangerous weapon or means",
        r'\bhit with a stick\b': "BNS Section 117 BNS Section 118 Grievous Hurt with dangerous weapon or means",
        r'\biron rod\b': "BNS Section 118 Grievous Hurt with dangerous weapon",
        r'\bsharp weapon\b': "BNS Section 118 Grievous Hurt with dangerous weapon",
        r'\bknife\b': "BNS Section 118 Grievous Hurt with dangerous weapon",
        r'\bcheated\b': "BNS Section 318 Cheating dishonestly inducing delivery of property",
        r'\bcheating\b': "BNS Section 318 Cheating",
        r'\bfraud\b': "BNS Section 318 Cheating",
        r'\bscam\b': "BNS Section 318 Cheating",
        r'\bthief\b': "BNS Section 303 Theft",
        r'\btheft\b': "BNS Section 303 Theft",
        r'\bstole\b': "BNS Section 303 Theft",
        r'\bstolen\b': "BNS Section 303 Theft",
        r'\bpickpocket\b': "BNS Section 303 Theft",
        r'\bmurder\b': "BNS Section 103 Murder",
        r'\bkilled\b': "BNS Section 103 Murder",
        r'\brobbery\b': "BNS Section 309 Robbery",
        r'\brobbed\b': "BNS Section 309 Robbery",
        r'\bdacoity\b': "BNS Section 310 Dacoity",
        r'\bextortion\b': "BNS Section 308 Extortion",
        r'\bextorted\b': "BNS Section 308 Extortion",
        r'\bkidnap\b': "BNS Section 137 Kidnapping",
        r'\bkidnapped\b': "BNS Section 137 Kidnapping",
        r'\brape\b': "BNS Section 64 Rape",
        r'\bsexual assault\b': "BNS Section 64 Rape",
        r'\bsimple hurt\b': "BNS Section 115 Voluntarily causing hurt",
        r'\bhit my head\b': "BNS Section 117 BNS Section 115 Hurt Grievous Hurt",
        # -- General Exceptions (Chapter III BNS) --
        r'\bduress\b': "BNS Section 32 compulsion threat instant death general exception",
        r'\bcompulsion\b': "BNS Section 32 compulsion threat instant death general exception",
        r'\bcoercion\b': "BNS Section 32 compulsion threat general exception",
        r'\bthreat of death\b': "BNS Section 32 compulsion threat instant death general exception",
        r'\bforced to\b': "BNS Section 32 compulsion threat general exception",
        r'\bnecessity\b': "BNS Section 30 BNS Section 31 necessity good faith general exception",
        r'\baccident\b': "BNS Section 25 accident without criminal intention general exception",
        r'\bmistake of fact\b': "BNS Section 27 mistake of fact good faith general exception",
        r'\bgood faith\b': "BNS Section 26 BNS Section 30 good faith benefit general exception",
        r'\bconsent\b': "BNS Section 28 BNS Section 29 consent not offence general exception",
        r'\binsanity\b': "BNS Section 22 unsound mind incapable knowing general exception",
        r'\bunsound mind\b': "BNS Section 22 unsound mind incapable knowing general exception",
        r'\binfancy\b': "BNS Section 20 BNS Section 21 child offence general exception",
        r'\bchild offence\b': "BNS Section 20 BNS Section 21 child offence general exception",
        r'\bself.?defen[cs]e\b': "BNS Section 34 BNS Section 37 right private defence general exception",
        r'\bprivate defen[cs]e\b': "BNS Section 34 BNS Section 37 right private defence general exception",
        r'\bintoxicat(ed|ion)\b': "BNS Section 22 BNS Section 23 intoxication involuntary general exception",
        r'\bsolitary confinement\b': "BNS Section 11 BNS Section 12 solitary confinement limit scale",
        # -- Cyber Crimes (IT Act) --
        r'\bhack(ed|ing)\b': "IT Act Section 66 Cyber Crime hacking unauthorized access computer resource",
        r'\bonline scam\b': "IT Act Section 66D cheating by personation computer resource online fraud",
        r'\bphish(ing)?\b': "IT Act Section 66D cheating by personation computer resource online fraud",
        r'\bcyber\b': "IT Act Section 66 computer resource cyber crime",
        r'\bemail\b': "IT Act Section 66 computer resource email communication device",
        # -- Narcotics (NDPS Act) --
        r'\bdrugs?\b': "NDPS Act Section 20 Section 21 cannabis psychoactive substance possession quantity narcotic",
        r'\bmarijuana\b': "NDPS Act Section 20 cannabis ganja hashish cultivation possession",
        r'\bweed\b': "NDPS Act Section 20 cannabis ganja cultivation possession",
        r'\bcocaine\b': "NDPS Act Section 21 manufactured drug psychotropic substance possession",
        # -- Cheque Bounce (NI Act) --
        r'\bcheque\b': "NI Act Section 138 cheque bounce dishonour of cheque bank debt liability",
        r'\bbounce(d)?\b': "NI Act Section 138 cheque bounce dishonour of cheque bank debt liability",
        # -- Child Abuse (POCSO Act) --
        r'\bchild(ren)?\b': "POCSO Act Section 3 Section 5 Section 9 child sexual abuse penetrative sexual assault minor",
        r'\bminor\b': "POCSO Act Section 3 Section 5 Section 9 child sexual abuse penetrative sexual assault minor",
        # -- SC/ST Act (Prevention of Atrocities) --
        r'\bcaste\b': "SC_ST Act Section 3 atrocities caste member scheduled caste tribe abuse",
        r'\bdalit\b': "SC_ST Act Section 3 atrocities caste member scheduled caste tribe abuse",
    }
    
    expansions = []
    for pattern, term in semantic_mappings.items():
        if re.search(pattern, query_lower):
            expansions.append(term)
            
    if expansions:
        query_expanded = query + " " + " ".join(expansions)
        print(f"✓ Expanded query for retrieval: {query_expanded}")

    # ── Build query vectors ──────────────────────────────────────────────────
    dense_vec  = get_query_embedding(query_expanded)
    sparse_vec = _build_sparse_vector(query_expanded)

    qdrant_filter = None
    if act_filter_normalized:
        qdrant_filter = Filter(
            should=[FieldCondition(key="act", match=MatchValue(value=a)) for a in act_filter_normalized]
        )

    # ── SLL Intent Detection and Soft-Boosting Preparation ───────────────────
    query_lower = query.lower()
    target_sll = []
    if any(w in query_lower for w in ["hack", "online", "cyber", "internet", "email", "instagram", "facebook", "whatsapp", "computer", "data"]):
        target_sll.append("IT")
    if any(w in query_lower for w in ["drug", "marijuana", "weed", "cocaine", "narcotic", "smuggle", "contraband", "possession"]):
        target_sll.append("NDPS")
    if any(w in query_lower for w in ["cheque", "check", "bounce", "dishonor", "bank"]):
        target_sll.append("NI")
    if any(w in query_lower for w in ["child", "minor", "pocso", "molest", "sexual abuse", "assault"]):
        target_sll.append("POCSO")
    if any(w in query_lower for w in ["caste", "dalit", "sc/st", "atrocit"]):
        target_sll.append("SC_ST")
    if any(w in query_lower for w in ["dowry", "dahej", "groom", "bride", "wedding"]):
        target_sll.append("Dowry")
    if any(w in query_lower for w in ["money laundering", "black money", "laundering", "pmla"]):
        target_sll.append("PMLA")

    # If SLL target detected, fetch more initial candidates to allow boosting room
    fetch_limit = max(k * 2, 35) if target_sll else k

    # ── Qdrant hybrid query (prefetch dense + sparse, then RRF fuse) ─────────
    try:
        results = qdrant.query_points(
            collection_name=QDRANT_COLLECTION,
            prefetch=[
                Prefetch(
                    query=dense_vec,
                    using="dense",
                    limit=max(fetch_limit, 35),
                    filter=qdrant_filter
                ),
                Prefetch(
                    query=sparse_vec,
                    using="sparse",
                    limit=max(fetch_limit, 35),
                    filter=qdrant_filter
                ),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=fetch_limit,
            with_payload=True,
        )
        hits: List[Any] = list(results.points)
    except Exception as e:
        print(f"Qdrant hybrid query failed: {e}")
        hits = []

    if not hits:
        return [], 0.0

    # ── Convert Qdrant hits to our dict format ───────────────────────────────
    retrieved = []
    for hit in hits:
        meta = dict(hit.payload or {})
        meta["rrf_score"]        = getattr(hit, "score", 0.0)
        meta["confidence_score"] = 0.0
        normalize_returned_act_name(meta)
        retrieved.append(meta)

    # Apply SLL Soft Boosting
    if target_sll:
        print(f"🎯 SLL Intent Detected: {target_sll}. Applying 30% RRF score boost to matching acts.")
        for r in retrieved:
            if r.get("act") in target_sll:
                r["rrf_score"] *= 1.30
        # Re-sort list based on boosted scores
        retrieved.sort(key=lambda x: x["rrf_score"], reverse=True)

    # Slice back to top k and calculate confidence scores
    retrieved = retrieved[:k]
    if retrieved:
        max_score = retrieved[0]["rrf_score"]
        for r in retrieved:
            r["confidence_score"] = min(r["rrf_score"] / max(max_score, 1e-9), 1.0)

    # Graph RAG augmentation — traverse Neo4j from top Qdrant entry points
    try:
        from graph_retriever import traverse_graph
        entry_ids = [str(r.get("chunk_id")) for r in retrieved[:3] if r.get("chunk_id")]
        if entry_ids:
            graph_results = traverse_graph(entry_ids, max_hops=2)
            # Merge: add graph results not already in retrieved
            existing_ids = {r.get("chunk_id") for r in retrieved}
            for gr in graph_results:
                if gr["chunk_id"] not in existing_ids:
                    retrieved.append(gr)
                    existing_ids.add(gr["chunk_id"])
            print(f"✓ Graph augmented: +{len(graph_results)} connected sections")
    except Exception as e:
        print(f"WARNING: Graph RAG traversal skipped: {e}")

    retrieved = apply_defence_sections(retrieved)
    top_confidence = retrieved[0]["confidence_score"] if retrieved else 0.0
    return retrieved, top_confidence

# ==========================================
# Cross-Encoder Reranking and Embedding Fallbacks
# ==========================================
_local_reranker = None
_local_tokenizer = None
_local_model = None

def rerank_candidates(query: str, candidates: List[Dict]) -> List[Dict]:
    """Primary: Cohere Rerank API -> Fallback: HF bge-reranker-large API -> Fallback: Local bge-reranker-base."""
    global _local_reranker
    if not candidates:
        return candidates
    
    reranked = False
    
    # 1. Try Cohere API reranker
    if COHERE_API_KEY:
        try:
            documents = [c.get("text", "") for c in candidates]
            r = httpx.post(
                "https://api.cohere.ai/v1/rerank",
                headers={
                    "Authorization": f"Bearer {COHERE_API_KEY}",
                    "Content-Type": "application/json",
                    "accept": "application/json"
                },
                json={
                    "model": "rerank-english-v3.0",
                    "query": query,
                    "documents": documents,
                    "top_n": len(candidates)
                },
                timeout=15.0
            )
            if r.status_code == 200:
                results = r.json().get("results", [])
                # Map scores back to candidates based on their original index
                for res in results:
                    idx = res["index"]
                    candidates[idx]["rerank_score"] = res["relevance_score"]
                # Default safety for unmatched indices
                for c in candidates:
                    c["rerank_source"] = "cohere"
                    if "rerank_score" not in c:
                        c["rerank_score"] = 0.0
                reranked = True
        except Exception as e:
            print(f"Cohere reranker failed: {e}. Trying HuggingFace API fallback.")

    # 2. Try HuggingFace Cloud API reranker
    if not reranked:
        try:
            pairs = [[query, c["text"]] for c in candidates]
            r = httpx.post(
                HF_RERANKER_LARGE_URL,
                headers={"Authorization": f"Bearer {HF_TOKEN}", "Content-Type": "application/json"},
                json={"inputs": pairs},
                timeout=15.0,
                verify=False
            )
            if r.status_code == 200:
                scores = [item["score"] for item in r.json()]
                for i, c in enumerate(candidates):
                    c["rerank_score"] = scores[i]
                    c["rerank_source"] = "hf_cloud"
                reranked = True
        except Exception as e:
            print(f"Cloud reranker failed: {e}. Using local CPU fallback.")

    # 3. Local Fallback (runs in HF Space container)
    if not reranked:
        if _local_reranker is None:
            from transformers import pipeline
            try:
                _local_reranker = pipeline("text-classification", model="BAAI/bge-reranker-base", device=-1, local_files_only=True)
            except Exception:
                _local_reranker = pipeline("text-classification", model="BAAI/bge-reranker-base", device=-1)
        
        formatted = [{"text": query, "text_pair": c["text"]} for c in candidates]
        scores_raw = [item["score"] for item in _local_reranker(formatted, truncation=True, max_length=512)]
        # Sigmoid activation to map logits (-inf, +inf) to valid probabilities [0.0, 1.0]
        scores = [1.0 / (1.0 + math.exp(-float(s))) for s in scores_raw]
        for i, c in enumerate(candidates):
            c["rerank_score"] = scores[i]
            c["rerank_source"] = "local_bge"
            
    # Normalize confidence score based on the absolute rerank relevance score
    for c in candidates:
        if "rerank_score" in c:
            # Map score to [0.0, 1.0] interval
            c["confidence_score"] = max(min(c["rerank_score"], 1.0), 0.0)

    return sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)

# ==========================================
# Response Caching
# ==========================================
def get_cached_response(query: str, is_lawyer_mode: bool = False) -> Optional[dict]:
    if supabase is None:
        return None
    key_str = f"{query.strip().lower()}_{is_lawyer_mode}"
    h = hashlib.sha256(key_str.encode()).hexdigest()
    try:
        res = supabase.table("query_cache").select("response").eq("query_hash", h).execute()
        if res.data and isinstance(res.data[0], dict):
            resp = res.data[0].get("response")
            return resp if isinstance(resp, dict) else None
        return None
    except Exception as e:
        print(f"Cache fetch failed: {e}")
        return None

def cache_response(query: str, response: dict, is_lawyer_mode: bool = False):
    if supabase is None:
        return
    key_str = f"{query.strip().lower()}_{is_lawyer_mode}"
    h = hashlib.sha256(key_str.encode()).hexdigest()
    try:
        supabase.table("query_cache").upsert({"query_hash": h, "response": response}).execute()
    except Exception as e:
        print(f"Cache write failed: {e}")

# ==========================================
# ==========================================
# No-LLM Fallback Response Builder
# ==========================================
def _build_no_llm_response(query: str, top_candidates: List[Dict]) -> dict:
    """Build a fully structured response from raw retrieved chunks with zero LLM calls.
    Used when all LLM providers are rate-limited (429)."""
    applicable_laws = []
    for idx, chunk in enumerate(top_candidates[:3]):  # Top 3 most relevant
        act = chunk.get("act", "Unknown Act")
        section = chunk.get("section") or chunk.get("article", "")
        title = chunk.get("title", "")
        text = chunk.get("text", "")
        
        # Construct a readable law_name
        law_name = f"{act} 2023, Section {section}" if section else f"{act} 2023"
        
        # Slice a meaningful verbatim citation (first 500 chars of the section text)
        verbatim = text[:500].strip()
        if len(text) > 500:
            verbatim += "..."
        
        # Generate plain-English based on title or first line
        plain_reason = title if title else f"This section of {act} may be relevant to the described situation."

        # Do NOT fake confidence when LLM synthesis is unavailable
        computed_score = 0.0

        applicable_laws.append({
            "law_name": law_name,
            "verbatim_text": verbatim,
            "role": "RELATED_PROVISION",
            "statutory_analysis": None,
            "confidence_score": computed_score,
            "plain_english_explanation": f"[RETRIEVAL ONLY — LLM UNAVAILABLE] {plain_reason}",
            "how_it_applies_to_facts": f"Based on the retrieved legal database, {law_name} is a potentially relevant provision. Full legal analysis requires LLM synthesis which is currently unavailable."
        })
    
    return {
        "status": "SUCCESS",
        "conclusion": "REQUIRES_JUDICIAL_DETERMINATION",
        "conclusion_summary": "LLM synthesis is temporarily unavailable due to rate limits. The retrieved sections below may be relevant to your query. Please retry shortly or consult a certified advocate for full analysis.",
        "legal_issue": query,
        "query_type": "",
        "applicable_laws": applicable_laws,
        "procedural_remedy": None,
        "mitigating_factors": [],
        "sources": top_candidates,
        "llm_unavailable": True,
        "disclaimer": "LLM synthesis unavailable (rate limit). Showing retrieval-only results. Consult a certified advocate before taking any legal action."
    }

# ==========================================
# LLM Synthesizer
# ==========================================
def generate_synthesis(query: str, retrieved_chunks: List[Dict]) -> str:
    context_str = ""
    for idx, chunk in enumerate(retrieved_chunks):
        context_str += (
            f"[Source {idx + 1}: {chunk['act']}, Section {chunk.get('section', chunk.get('article', ''))}]\n"
            f"Title: {chunk.get('title','')}\n"
            f"Verbatim Law Text:\n{chunk['text']}\n"
            f"---------------------------------\n\n"
        )

    system_prompt = f"""You are a Senior Legal Counsel of the Supreme Court of India. You specialize in the Bharatiya Nyaya Sanhita (BNS), Bharatiya Nagarik Suraksha Sanhita (BNSS), and Bharatiya Sakshya Adhiniyam (BSA), 2023.

CONTEXT LAW DATABASE:
---
{context_str}
---

═══════════════════════════════════════════════
CORE DIRECTIVES (ABSOLUTE — VIOLATION IS FAILURE)
═══════════════════════════════════════════════

D1. GROUNDING: You may ONLY cite sections that appear in the CONTEXT LAW DATABASE above. If a section number is not in the context, you MUST NOT mention it. If the context does not contain sections relevant to the user's specific legal question, set status to "INSUFFICIENT_DATA".

D2. NO REPEALED LAW: NEVER reference the Indian Penal Code (IPC), Code of Criminal Procedure (CrPC), or Indian Evidence Act. These statutes are repealed. Use ONLY BNS, BNSS, BSA, and special acts (POCSO, NDPS, NI Act, etc.) as found in the context.

D3. IPC MAPPING: If the user references an old IPC section (e.g. IPC 302), and the corresponding BNS section is in the Context, analyze the BNS section and explain the mapping. Set status to SUCCESS.

D4. VERBATIM CITATIONS: Quote the EXACT statutory text from the context that governs this issue. You may excerpt the specific 1-3 sentences or sub-clauses that apply to these facts. Do not paraphrase. For very long sections, quote only the operative clause(s), not the entire section.

D5. CONCLUSION IS MANDATORY: Every response MUST contain an explicit legal conclusion. You are a legal analyst, not a search engine. Answer the user's question directly with a verdict.

═══════════════════════════════════════════════
MANDATORY 5-STEP REASONING CHAIN (LOGIC GATES)
═══════════════════════════════════════════════

For EACH applicable section, execute these steps IN ORDER:

STEP 1 — IDENTIFY THE LEGAL ISSUE
State the precise legal question. Classify it as:
- OFFENSE_APPLICABILITY: "Does this act constitute offence X?"
- DEFENSE_EVALUATION: "Can the accused claim defense Y?"
- SENTENCING_REVIEW: "Is this sentence/order lawful?"
- PROCEDURAL_QUERY: "What is the correct procedure for Z?"

STEP 2 — EXTRACT THE RULE (Verbatim)
Quote the exact statutory text from the context that governs this issue.

STEP 3 — PARSE EXCEPTIONS & PROVISOS
Scan the statutory text for ALL of these patterns:
- "Except..." / "except..." = absolute exclusion (e.g. "Except murder..." in Section 32)
- "Provided that..." = conditional qualification
- "Nothing in this section applies to..." = scope limitation
- "shall not exceed..." / "not exceeding..." = quantitative ceiling
- Numbered sub-clauses with conditions (a), (b), (c)...
If the facts of the scenario trigger the exception, exclusion, or proviso, you MUST flag it:
- Set "exception_applies" to `true` (e.g., if the law says "Except murder" and the user's scenario involves murder, then the exclusion exception DOES apply to the facts).
- Set "exception_effect" to explain how this changes the outcome (e.g. "Since murder is explicitly excluded, the defense is completely barred").

STEP 4 — APPLY TO FACTS (Quantitative + Qualitative)
- QUANTITATIVE: If the section contains numerical limits (time periods, amounts, ages), extract the limit and compare it arithmetically to the facts. Show the math: "X [ordered/actual] vs Y [statutory maximum] = exceeds/within limit".
- QUALITATIVE: If the section contains subjective tests ("sufficient maturity", "reasonable apprehension", "good faith"), evaluate whether the user's described facts satisfy or fail each test, citing specific factual indicators.

STEP 5 — STATE THE CONCLUSION
Determine the overall legal outcome:
- If a defense section is relevant but the user's facts trigger an exclusion/exception (like "Except murder" under Section 32), the final verdict MUST be DEFENSE_NOT_AVAILABLE.
- If a quantitative limit is exceeded, the verdict MUST be UNLAWFUL.
- Do not let emotional circumstances or mitigating factors alter the binary legal conclusion.
Produce ONE of these verdicts:
- LAWFUL: The act/order is within statutory bounds
- UNLAWFUL: The act/order violates statutory provisions
- OFFENSE_ESTABLISHED: The described conduct constitutes the offence
- DEFENSE_AVAILABLE: The defense applies on these facts
- DEFENSE_NOT_AVAILABLE: The defense fails (state which element/exception defeats it)
- PARTIALLY_LAWFUL: Some aspects are lawful, others not (specify which)
- REQUIRES_JUDICIAL_DETERMINATION: The answer depends on factual findings a court must make

═══════════════════════════════════════════════
PROCEDURAL REMEDY RULES
═══════════════════════════════════════════════

Match the correct BNSS remedy to the query type:
- To challenge a SENTENCE = Appeal (BNSS S.373-380) or Revision (BNSS S.442)
- To file a CRIMINAL COMPLAINT = FIR (BNSS S.173) or Private Complaint before Magistrate (BNSS S.200)
- To seek BAIL = Regular Bail (BNSS S.478), Anticipatory Bail (BNSS S.480)
- To challenge an ORDER = Revision (BNSS S.442) or Writ (Constitution Art.226/32)
- For DEFENSE evaluation = No filing needed; analysis only; note mitigating factors for sentencing if defense fails

═══════════════════════════════════════════════
FEW-SHOT REASONING EXAMPLES
═══════════════════════════════════════════════

EXAMPLE 1 (SENTENCING LIMIT):
Scenario: "A Magistrate convicts an offender of theft and sentences him to 9 months imprisonment, ordering 3 months of solitary confinement as part of the sentence. Is this lawful?"
Output:
{{
  "status": "SUCCESS",
  "conclusion": "UNLAWFUL",
  "conclusion_summary": "The order of 3 months of solitary confinement is unlawful. Under BNS Section 11(b), when the term of imprisonment is between 6 months and 1 year, solitary confinement cannot exceed 2 months. Here, the sentence is 9 months, but 3 months of solitary confinement was ordered, exceeding the limit by 1 month.",
  "legal_issue": "Is the order of 3 months of solitary confinement lawful for a 9-month imprisonment sentence under the BNS?",
  "query_type": "SENTENCING_REVIEW",
  "applicable_laws": [
    {{
      "law_name": "BNS 2023, Section 11",
      "confidence_score": 1.0,
      "verbatim_text": "not exceeding two months if the term of imprisonment shall exceed six months and shall not exceed one year",
      "role": "SENTENCING_PROVISION",
      "statutory_analysis": {{
        "exceptions_found": ["not exceeding two months if the term of imprisonment shall exceed six months and shall not exceed one year"],
        "exception_applies": true,
        "exception_effect": "Caps solitary confinement at 2 months.",
        "quantitative_checks": [
          {{
            "parameter": "solitary confinement duration",
            "statutory_limit": "2 months (Section 11(b))",
            "actual_value": "3 months",
            "result": "EXCEEDS_LIMIT"
          }}
        ],
        "qualitative_checks": []
      }},
      "plain_english_explanation": "For sentences between 6 months and 1 year, solitary confinement is capped at 2 months.",
      "how_it_applies_to_facts": "The 9-month sentence falls in the 6-12 month range, so the 3-month order exceeds the 2-month maximum limit."
    }}
  ],
  "procedural_remedy": {{
    "recommended_action": "Appeal against the sentence or file Revision Petition",
    "bnss_provision": "BNSS Section 373 or Section 442",
    "court": "Sessions Court"
  }},
  "mitigating_factors": []
}}

EXAMPLE 2 (STATUTORY DEFENSE EXCLUSION):
Scenario: "A person is forced to kill a stranger because a gang holds a gun to his head and threatens to shoot him instantly if he refuses. Can he claim the defense of compulsion?"
Output:
{{
  "status": "SUCCESS",
  "conclusion": "DEFENSE_NOT_AVAILABLE",
  "conclusion_summary": "The defense of compulsion under Section 32 is not available to the accused. The statute explicitly carves out murder ('Except murder...') from the scope of this defense. Therefore, even if the accused acted under immediate threat of death, he cannot claim duress as a defense to murder.",
  "legal_issue": "Can a person claim the defense of compulsion under Section 32 of the BNS when committing murder under threat of instant death?",
  "query_type": "DEFENSE_EVALUATION",
  "applicable_laws": [
    {{
      "law_name": "BNS 2023, Section 32",
      "confidence_score": 1.0,
      "verbatim_text": "Except murder, and offences against the State punishable with death, nothing is an offence which is done by a person who is compelled to do it by threats...",
      "role": "DEFENSE_SECTION",
      "statutory_analysis": {{
        "exceptions_found": ["Except murder, and offences against the State punishable with death"],
        "exception_applies": true,
        "exception_effect": "Completely bars the duress/compulsion defense for murder, making it unavailable.",
        "quantitative_checks": [],
        "qualitative_checks": [
          {{
            "condition": "compelled by threats causing reasonable apprehension of instant death",
            "factual_indicators": "gun held to head, threatened with instant shooting",
            "assessment": "SATISFIED"
          }}
        ]
      }},
      "plain_english_explanation": "The defense of compulsion does not apply to murder. You cannot kill an innocent person to save your own life under the law.",
      "how_it_applies_to_facts": "Although the accused acted under immediate threat of instant death, the crime committed was murder. Since murder is explicitly excluded from the defense by the opening clause of Section 32, the defense fails."
    }}
  ],
  "procedural_remedy": {{
    "recommended_action": "Present mitigating circumstances during trial and sentencing",
    "bnss_provision": "N/A",
    "court": "Sessions Court"
  }},
  "mitigating_factors": [
    "Accused acted under extreme duress with a gun to his head, which can be argued during sentencing to seek a lesser punishment."
  ]
}}

═══════════════════════════════════════════════
OUTPUT JSON SCHEMA (STRICT)
═══════════════════════════════════════════════

Return a JSON object with EXACTLY this structure:
{{
  "status": "SUCCESS" or "INSUFFICIENT_DATA",
  "conclusion": "LAWFUL | UNLAWFUL | OFFENSE_ESTABLISHED | DEFENSE_AVAILABLE | DEFENSE_NOT_AVAILABLE | PARTIALLY_LAWFUL | REQUIRES_JUDICIAL_DETERMINATION",
  "conclusion_summary": "2-3 sentence plain-English verdict answering the user's question directly. State the legal outcome clearly.",
  "legal_issue": "The precise legal question identified in Step 1",
  "query_type": "OFFENSE_APPLICABILITY | DEFENSE_EVALUATION | SENTENCING_REVIEW | PROCEDURAL_QUERY",
  "applicable_laws": [
    {{
      "law_name": "Full Act and Section, e.g. BNS 2023, Section 32",
      "confidence_score": 0.95,
      "verbatim_text": "Exact text from context — COPY-PASTE ONLY",
      "role": "PRIMARY_OFFENSE | DEFENSE_SECTION | SENTENCING_PROVISION | PROCEDURAL_PROVISION | RELATED_PROVISION",
      "statutory_analysis": {{
        "exceptions_found": ["List each 'Except...', 'Provided that...' clause"],
        "exception_applies": true or false,
        "exception_effect": "How the exception changes the outcome for these facts",
        "quantitative_checks": [
          {{
            "parameter": "e.g., maximum solitary confinement period",
            "statutory_limit": "e.g., 3 months (Section 11(c))",
            "actual_value": "e.g., 4 months (as ordered)",
            "result": "EXCEEDS_LIMIT or WITHIN_LIMIT or NOT_APPLICABLE"
          }}
        ],
        "qualitative_checks": [
          {{
            "condition": "e.g., sufficient maturity of understanding",
            "factual_indicators": "e.g., top of class, handles cash",
            "assessment": "SATISFIED or NOT_SATISFIED or INDETERMINATE"
          }}
        ]
      }},
      "plain_english_explanation": "Simple explanation a non-lawyer can understand",
      "how_it_applies_to_facts": "Specific mapping of section elements to user's described situation"
    }}
  ],
  "procedural_remedy": {{
    "recommended_action": "The correct legal step based on query type",
    "bnss_provision": "The specific BNSS section for the remedy",
    "court": "Which court has jurisdiction (Magistrate / Sessions / High Court)"
  }},
  "mitigating_factors": ["Circumstances that could reduce severity if defense fails"]
}}

OUTPUT ONLY THE JSON. No markdown, no explanation text, no ```json block formatting."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Scenario: '{query}'"}
    ]
    return call_llm(messages, json_mode=True)

def _validate_citations_strict(response_data: dict, chunks: List[Dict]) -> bool:
    """Return True only if every cited (act, section) in applicable_laws exists in retrieved chunks."""
    from citation_guard import get_normalized_act
    allowed = set()
    for c in chunks:
        act = get_normalized_act(str(c.get("act", "")))
        sec = str(c.get("section") or c.get("article") or "").strip()
        if act and sec:
            allowed.add((act, sec.lower()))
    
    for law in response_data.get("applicable_laws", []):
        name = law.get("law_name", "")
        # Parse e.g. "BNS 2023, Section 11" or "Constitution Article 21"
        m = re.search(r'(BNS|BNSS|BSA|POCSO|NDPS|NI_Act|NI|IT|PMLA|UAPA|Constitution).*?(?:Section|Article|Sec|s\.?|art\.?)\s*(\d+[A-Za-z]?)', name, re.I)
        if not m:
            continue
        act_raw, sec = m.group(1), m.group(2).strip().lower()
        act = get_normalized_act(act_raw)
        
        if (act, sec) not in allowed:
            print(f"⚠️ HARD HALLUCINATION GUARD TRIGGERED: Cited ({act}, Section {sec}) is not in retrieved context chunks.")
            return False
            
    return True

# ==========================================
# Run RAG Pipeline
# ==========================================
def _extract_json(text: str) -> dict:
    """Robust JSON extractor handling raw strings, markdown codeblocks (```json), and outermost braces."""
    if not text or not isinstance(text, str):
        raise ValueError("Empty or non-string response from LLM")
    text = text.strip()
    # 1. Direct parse
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # 2. Strip markdown code block wrappers
    m = re.search(r'```(?:json)?\s*(.*?)```', text, re.DOTALL | re.IGNORECASE)
    if m:
        try:
            data = json.loads(m.group(1).strip())
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    # 3. Outermost curly braces match
    m = re.search(r'(\{.*\})', text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(1).strip())
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    raise ValueError("No valid JSON object found in response string")

# ==========================================
# Run RAG Pipeline
# ==========================================
def analyze(query: str, is_lawyer_mode: bool = False, user_id: Optional[str] = None) -> dict:
    """Core entry point for the RAG pipeline."""
    # 1. Cache Check
    cached = get_cached_response(query, is_lawyer_mode)
    if cached:
        print("✓ Returning cached query response.")
        return cached

    # 2. Detect acts & Query Expansion
    from query_expander import expand_query
    act_filter = ActSpecificRouter.detect_acts(query)
    expanded_list = expand_query(query)
    query_expanded = " ".join(expanded_list) if expanded_list else query

    # 3. Hybrid Retrieve
    retrieved, confidence = hybrid_retrieve(query_expanded, k=15, act_filter=act_filter)
    if not retrieved or confidence < 0.15:
        return {
            "status": "INSUFFICIENT_DATA",
            "applicable_laws": [],
            "reason": "Retrieved documents do not meet relevance threshold.",
            "disclaimer": "This analysis is for educational and research purposes only. Consult a certified advocate."
        }

    # 4. Rerank
    reranked = rerank_candidates(query, retrieved)
    top_candidates = reranked[:5]
    
    # 5. LLM Synthesis with full no-LLM fallback
    from action_engine import get_first_steps
    from cases import retrieve_similar_judgments

    llm_succeeded = False
    raw_response = None
    try:
        raw_response = generate_synthesis(query, top_candidates)
        response_data = _extract_json(raw_response)
        
        # Validate against Pydantic schema
        try:
            validated = FullResponse.model_validate(response_data)
            response_data = validated.model_dump()
        except Exception as ve:
            print(f"FullResponse Pydantic validation note: {ve}")
        
        # Hard Hallucination Guard Validation
        if not _validate_citations_strict(response_data, top_candidates):
            print("🚨 Hallucinated citation detected. Falling back to retrieval-only response.")
            response_data = _build_no_llm_response(query, top_candidates)
            response_data["hallucination_warning"] = True
            llm_succeeded = False
        else:
            # Verify Citations
            from citation_guard import verify_citations
            guard_res = verify_citations(raw_response, top_candidates)
            if not guard_res["valid"]:
                print(f"Citation quote warning: {guard_res}.")
                response_data["citation_warning"] = {
                    "failed_citations": guard_res["failed_citations"],
                    "failed_quotes": guard_res["failed_quotes"]
                }
            llm_succeeded = True
            print("✓ LLM synthesis succeeded and passed Hard Hallucination Guard.")
    except Exception as e:
        print(f"LLM synthesis parsing failed ({e}). Using no-LLM retrieval fallback — app will still return results.")
        if raw_response:
            print("--- RAW LLM RESPONSE THAT FAILED TO PARSE ---")
            print(raw_response)
            print("---------------------------------------------")
        response_data = _build_no_llm_response(query, top_candidates)

    # Inject remaining attributes
    if response_data.get("status") == "SUCCESS":
        # Ensure sources is always present
        if "sources" not in response_data:
            response_data["sources"] = top_candidates

        # Element verification removed: the heuristic fallback was returning fabricated 100%
        # match scores regardless of facts (DISABLE_ELEMENT_VERIFICATION_LLM defaults true).
        # The LLM's statutory_analysis field in each law already handles this correctly.

        # Inject recommended actions: use LLM's procedural_remedy if available, else fall back to static rules
        if response_data.get("procedural_remedy"):
            remedy = response_data["procedural_remedy"]
            response_data["recommended_actions"] = [
                f"{remedy.get('recommended_action', '')} ({remedy.get('bnss_provision', '')})",
                f"Court: {remedy.get('court', 'Consult advocate for jurisdiction')}"
            ]
        else:
            top_cand = top_candidates[0] if top_candidates else {}
            # C2 FIX: If LLM is already down (rate-limited/failed), skip the LLM
            # inside get_first_steps and go straight to YAML baseline. Passing
            # force_baseline=True avoids re-cycling through all failed providers.
            citizen_steps = get_first_steps(top_cand, query, is_lawyer_mode=False,
                                            force_baseline=not llm_succeeded)
            lawyer_steps = get_first_steps(top_cand, query, is_lawyer_mode=True,
                                           force_baseline=True)   # reuse baseline; LLM already ran for citizen
            lawyer_steps_tagged = [
                f"{s} (u/s BNSS)" if not any(k in s.lower() for k in ["u/s", "bnss", "crpc", "petition"]) else s
                for s in lawyer_steps
            ]
            response_data["recommended_actions"] = citizen_steps + lawyer_steps_tagged

        # Similar judgments — pure FAISS lookup, no LLM needed
        sec_list = [f"BNS-{c.get('section', '')}" for c in top_candidates if c.get('section')]
        try:
            response_data["similar_judgments"] = retrieve_similar_judgments(query, sec_list, k=3)
        except Exception as sj_err:
            print(f"Similar judgments lookup failed: {sj_err}")
            response_data["similar_judgments"] = []

    # Cache response only if it successfully retrieved legal data and LLM succeeded
    if response_data.get("status") != "INSUFFICIENT_DATA" and llm_succeeded:
        cache_response(query, response_data, is_lawyer_mode)
    
    # Save conversation (smart route: UUID to Supabase, non-UUID custom IDs to local SQLite)
    if user_id:
        is_uuid = re.match(r'^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$', user_id)
        if is_uuid and supabase is not None:
            try:
                supabase.table("conversations").insert({
                    "user_id": user_id, "incident": query, "response": response_data
                }).execute()
            except Exception as e:
                print(f"Failed to save conversation history to Supabase: {e}")
        else:
            try:
                from credit_manager import SessionLocal, Conversation
                db = SessionLocal()
                db_conv = Conversation(
                    user_id=user_id,
                    incident=query,
                    response_json=json.dumps(response_data),
                    is_lawyer_mode=is_lawyer_mode
                )
                db.add(db_conv)
                db.commit()
                db.close()
                print("✓ Saved conversation history to local SQLite database.")
            except Exception as e:
                print(f"Failed to save conversation history to local SQLite: {e}")

    return response_data
