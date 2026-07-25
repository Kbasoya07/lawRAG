import os
import json
import re
import ssl
import time
import math
import hashlib
from typing import List, Dict, Tuple, Optional, Any
from functools import lru_cache
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
qdrant   = QdrantClient(url=QDRANT_URL, port=6333, api_key=QDRANT_API_KEY, timeout=30) if QDRANT_URL else QdrantClient(":memory:")

_local_tokenizer: Optional[Any] = None
_local_model: Optional[Any] = None
# Primary collection uses voyage-law-2 embeddings (dense 1024-dim) + BM25 sparse
# BGE collection uses BAAI/bge-large-en-v1.5 (same 1024-dim) — used as fallback
QDRANT_COLLECTION     = os.getenv("QDRANT_COLLECTION",     "law_sections")
QDRANT_COLLECTION_BGE = os.getenv("QDRANT_COLLECTION_BGE", "law_sections_bge")

GROQ_API_URL          = "https://api.groq.com/openai/v1/chat/completions"
NOMIC_API_URL         = "https://api-atlas.nomic.ai/v1/embedding/text"
HF_BGE_LARGE_URL      = "https://router.huggingface.co/hf-inference/models/BAAI/bge-large-en-v1.5/pipeline/feature-extraction"
HF_RERANKER_LARGE_URL = "https://api-inference.huggingface.co/models/BAAI/bge-reranker-large"

STORE_DIR = os.path.join(BASE_DIR, "store")
os.makedirs(STORE_DIR, exist_ok=True)
SSL_CONTEXT = ssl._create_unverified_context()

# ==========================================
# Critical Term Routing (Special Laws Override)
# ==========================================
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
    'dying declaration': 'BSA',   # BSA §26 — statement made before death
    'statement before death': 'BSA',
    'statement in expectation of death': 'BSA',

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
    # NDPS bail composites — these override the generic 'bail' → BNSS routing
    # so NDPS bail queries retrieve NDPS §36A/§37 alongside BNSS §479
    'ndps bail': 'NDPS',
    'bail under ndps': 'NDPS',
    'default bail ndps': 'NDPS',
    'ndps default bail': 'NDPS',
    'ndps 180 days': 'NDPS',
    'ndps 90 days': 'NDPS',
    
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

# Maps defence/exception sections → their primary offence section
# Used by apply_defence_sections() to auto-fetch the primary offence when only the defence section is retrieved
DEFENCE_SECTIONS = {
    # BNS General Exceptions
    ('BNS', 20):  {'primary': ('BNS', 103), 'warning': 'Defence section — Act of child under 7 years; primary offence is culpable homicide/murder'},
    ('BNS', 21):  {'primary': ('BNS', 103), 'warning': 'Defence section — Act of child above 7 and under 12; primary offence is culpable homicide/murder'},
    ('BNS', 22):  {'primary': ('BNS', 103), 'warning': 'Defence section — Unsound mind / intoxication exception; primary offence context needed'},
    ('BNS', 25):  {'primary': ('BNS', 103), 'warning': 'Defence section — Accident exception; primary offence context needed'},
    ('BNS', 26):  {'primary': ('BNS', 103), 'warning': 'Defence section — Good faith exception; primary offence context needed'},
    ('BNS', 27):  {'primary': ('BNS', 136), 'warning': 'Defence section — Mistake of fact; primary offence is child labour'},
    ('BNS', 28):  {'primary': ('BNS', 115), 'warning': 'Defence section — Consent exception; primary offence is hurt'},
    ('BNS', 30):  {'primary': ('BNS', 103), 'warning': 'Defence section — Necessity exception; primary offence context needed'},
    ('BNS', 32):  {'primary': ('BNS', 115), 'warning': 'Defence section — Compulsion/duress exception; primary offence is hurt'},
    # BNS Right of Private Defence
    ('BNS', 34):  {'primary': ('BNS', 103), 'warning': 'Defence section — Right of private defence of body; primary offence is murder/culpable homicide'},
    ('BNS', 35):  {'primary': ('BNS', 103), 'warning': 'Defence section — Extent of right of private defence of body; primary offence is murder'},
    ('BNS', 36):  {'primary': ('BNS', 303), 'warning': 'Defence section — Right of private defence of property; primary offence is theft'},
    ('BNS', 37):  {'primary': ('BNS', 309), 'warning': 'Defence section — Right of private defence against robbery; primary offence is robbery'},
    ('BNS', 38):  {'primary': ('BNS', 310), 'warning': 'Defence section — Right of private defence against dacoity; primary offence is dacoity'},
    ('BNS', 43):  {'primary': ('BNS', 103), 'warning': 'Defence section — Act done in good faith for benefit; primary offence context needed'},
    # BNS specific exception sections
    ('BNS', 88):  {'primary': ('BNS', 100), 'warning': 'Defence section — primary offence is culpable homicide not amounting to murder'},
    ('BNS', 89):  {'primary': ('BNS', 115), 'warning': 'Defence section — primary offence is voluntarily causing hurt'},
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
# Global flag: set True whenever Voyage API is rate-limited and we fall back to BGE.
# Included in every API response so the UI can surface a notice to the user.
_voyage_rate_limited: bool = False

@lru_cache(maxsize=128)
def _get_query_embedding_internal(text: str) -> List[float]:
    """Primary: Voyage AI voyage-law-2 (1024-dim, legal-domain) -> Fallback: local BAAI/bge-large-en-v1.5 (1024-dim).

    IMPORTANT: The Qdrant corpus dense vectors were re-embedded with voyage-law-2.
    Queries MUST use the same model to remain in the same vector space.
    BGE is kept as an offline fallback only.
    """
    global _local_tokenizer, _local_model, _voyage_rate_limited

    # ── Primary: Voyage AI voyage-law-2 ──────────────────────────────────────
    if VOYAGE_API_KEY:
        url = "https://api.voyageai.com/v1/embeddings"
        headers = {
            "Authorization": f"Bearer {VOYAGE_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "input": [text],
            "model": "voyage-law-2",
            "input_type": "query",  # 'query' for retrieval queries (asymmetric embedding)
        }
        for attempt in range(4):
            try:
                r = httpx.post(url, headers=headers, json=payload, timeout=20.0)
                if r.status_code == 200:
                    _voyage_rate_limited = False
                    return r.json()["data"][0]["embedding"]
                elif r.status_code == 429:
                    wait = 2 ** attempt
                    print(f"Voyage embedding rate limited (429). Waiting {wait}s... (attempt {attempt+1}/4)")
                    time.sleep(wait)
                else:
                    print(f"Voyage embedding error {r.status_code}: {r.text[:200]}. Falling back to BGE.")
                    break
            except Exception as ex:
                print(f"Voyage embedding attempt {attempt+1} failed: {ex}")
                if attempt < 3:
                    time.sleep(1)
        _voyage_rate_limited = True
        print("WARNING: All Voyage AI attempts failed (rate limited). Falling back to local BGE model.")
    else:
        print("WARNING: VOYAGE_API_KEY not set. Falling back to local BGE model.")

    # ── Fallback: Local BAAI/bge-large-en-v1.5 ───────────────────────────────
    # NOTE: BGE vectors will have lower relevance since the corpus is now in
    # voyage-law-2 space, but results are still usable as a degraded fallback.
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
            print("✓ Local CPU BGE fallback model loaded.")

        if _local_tokenizer is not None and _local_model is not None:
            import torch
            inputs = _local_tokenizer(text, padding=True, truncation=True, max_length=512, return_tensors="pt")
            with torch.no_grad():
                model_output = _local_model(**inputs)
                sentence_embeddings = model_output[0][:, 0]
                sentence_embeddings = torch.nn.functional.normalize(sentence_embeddings, p=2, dim=1)
                emb = sentence_embeddings[0].tolist()
                return emb[:1024]
    except Exception as e:
        print(f"WARNING: Local BGE fallback also failed: {e}. Returning zero vector.")
        return [0.0] * 1024

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
        "max_tokens": 8192
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    r = httpx.post(GROQ_API_URL, json=payload, headers=headers, timeout=40.0)
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
            
    payload = {"contents": contents, "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192}}
    if system_instruction:
        payload["systemInstruction"] = system_instruction
    if json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"
        
    headers = {"Content-Type": "application/json"}
    r = httpx.post(url, json=payload, headers=headers, timeout=40.0)
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
            
    payload = {"contents": contents, "generationConfig": {"temperature": 0.1, "maxOutputTokens": 8192}}
    if system_instruction:
        payload["systemInstruction"] = system_instruction
    if json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"
        
    headers = {"Content-Type": "application/json"}
    r = httpx.post(url, json=payload, headers=headers, timeout=40.0)
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
                "max_tokens": 8192
            }
            r = httpx.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers, timeout=40.0)
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
    if act_upper in ["BNS", "BNSS", "BSA", "POCSO", "NDPS", "UAPA", "PMLA", "CONSTITUTION"]:
        if act_upper == "CONSTITUTION":
            return "Constitution"
        return act_upper
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
    'anticipatory bail': {'act': 'BNSS', 'section': 482, 'reason': 'Procedural — BNSS only (CrPC §438 → BNSS §482)'},
    'regular bail':      {'act': 'BNSS', 'section': 480, 'reason': 'Procedural — BNSS only (CrPC §437 → BNSS §480)'},
    'default bail':      {'act': 'BNSS', 'section': 479, 'reason': 'Procedural — BNSS only (CrPC §167(2) → BNSS §479)'},
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
    if not sec_match and act_target:
        # Fallback: catch "BNS 102" or "102 BNS" where section word is omitted
        sec_match = re.search(r'\b(\d+[a-z]?)\b', query_lower)

    art_match = re.search(r'\b(?:article|art\.?)\s*(\d+)\b', query_lower)


    if act_target == "Constitution" or art_match:
        num = art_match.group(1) if art_match else (sec_match.group(1) if sec_match else None)
        if num:
            try:
                active_coll = QDRANT_COLLECTION_BGE if _voyage_rate_limited else QDRANT_COLLECTION
                hits, _ = qdrant.scroll(
                    collection_name=active_coll,
                    scroll_filter=Filter(must=[
                        FieldCondition(key="act", match=MatchValue(value="Constitution"))
                    ]),
                    limit=50, with_payload=True, with_vectors=False
                )
                matching = [h for h in hits if str(h.payload.get("article", "")).strip() == str(num) or str(h.payload.get("chunk_id", "")).endswith(f"_S{num}")]
                if matching:
                    exact = dict(matching[0].payload)
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
            active_coll = QDRANT_COLLECTION_BGE if _voyage_rate_limited else QDRANT_COLLECTION
            hits, _ = qdrant.scroll(
                collection_name=active_coll,
                scroll_filter=Filter(must=[
                    FieldCondition(key="act",     match=MatchValue(value=norm_act)),
                    FieldCondition(key="section", match=MatchValue(value=str(num)))
                ]),
                limit=1, with_payload=True, with_vectors=False
            )
            if not hits:
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

def extract_section_numbers_from_text(text: str) -> list:
    """Extracts all section/article numbers from a chunk of text."""
    # Matches digits optionally followed by a single letter or subclauses e.g., 103, 105(a), 120B
    # Restricts against year numbers (like 2023 or 2024).
    raw_nums = re.findall(r'\b(?!20\d{2}\b)\d+[A-Z|a-z]?(?:\(\w+\))*\b', text)
    # Clean the numbers to extract only the base section number (e.g., 176 from 176(3))
    clean_nums = []
    for num in raw_nums:
        base_match = re.match(r'^(\d+[A-Z|a-z]?)', num)
        if base_match:
            clean_nums.append(base_match.group(1))
    return clean_nums

def parse_multi_law_query(query: str) -> List[Tuple[str, str]]:
    """
    Parses a query for all mentioned laws and section numbers.
    Handles singular/plural (Section/Sections), comma-separated lists, and conjunctions ('and').
    """
    query_lower = query.lower()
    
    # Acts map to check
    acts = ["bnss", "bns", "bsa", "pocso", "ndps", "ni", "negotiable", "it", "sc/st", "sc_st", "dowry", "pmla", "nsa", "uapa", "jj", "constitution"]
    
    # 1. Find all act mentions and their start positions
    act_mentions = []
    for act in acts:
        for m in re.finditer(r'\b' + re.escape(act) + r'\b', query_lower):
            act_mentions.append((m.start(), m.end(), normalize_act_name(act)))
            
    # Also find "article" or "constitution" mentions which imply Constitution
    for art_keyword in ["article", "articles", "art", "arts"]:
        for m in re.finditer(r'\b' + re.escape(art_keyword) + r'\b', query_lower):
            if not any(start <= m.start() < end for start, end, _ in act_mentions):
                act_mentions.append((m.start(), m.end(), "Constitution"))
                
    # Sort mentions by their position in the query
    act_mentions.sort(key=lambda x: x[0])
    
    results = []
    
    # Case 1: Act name is mentioned before the section numbers
    # e.g., "BNS Sections 103, 105 and 106"
    for i, (start, end, act) in enumerate(act_mentions):
        next_start = act_mentions[i+1][0] if i+1 < len(act_mentions) else len(query_lower)
        segment = query_lower[end:next_start]
        
        # Look for section indicator in this segment
        sec_indicator = re.search(r'\b(?:sections?|secs?|s\.?|articles?|arts?)\b', segment)
        if sec_indicator:
            sub_segment = segment[sec_indicator.end():]
            nums = extract_section_numbers_from_text(sub_segment)
            for num in nums:
                results.append((act, num))
        else:
            # Fallback: catch "BNS 101" where section word is omitted
            nums = extract_section_numbers_from_text(segment)
            for num in nums:
                results.append((act, num))

                
    # Case 2: Act name is mentioned after the section numbers
    # e.g., "Sections 103 and 105 of the BNS"
    for i, (start, end, act) in enumerate(act_mentions):
        prev_end = act_mentions[i-1][1] if i > 0 else 0
        segment_before = query_lower[prev_end:start]
        
        # Look for section indicator in the segment before the act
        sec_indicator = re.search(r'\b(?:sections?|secs?|s\.?|articles?|arts?)\b', segment_before)
        if sec_indicator:
            # Check if there is a preposition indicating that the sections belong to the act that follows (e.g., "of", "under", "in")
            if any(prep in segment_before[sec_indicator.end():] for prep in ["of", "under", "in"]):
                sub_segment = segment_before[sec_indicator.end():]
                nums = extract_section_numbers_from_text(sub_segment)
                for num in nums:
                    results.append((act, num))
        else:
            # Fallback: catch "101 BNS" where section word is omitted
            nums = extract_section_numbers_from_text(segment_before)
            for num in nums:
                results.append((act, num))


    # Case 3: IPC mappings
    is_ipc = "ipc" in query_lower or "indian penal code" in query_lower
    if is_ipc:
        sec_nums = re.findall(r'\b(?:sections?|secs?|s\.?)\s*(\d+[a-z]?)\b', query_lower)
        for sec in sec_nums:
            if sec in IPC_TO_BNS_MAP:
                results.append(("BNS", IPC_TO_BNS_MAP[sec]))

    # Deduplicate results
    unique_results = []
    seen = set()
    for act, num in results:
        key = (act, num)
        if key not in seen:
            seen.add(key)
            unique_results.append(key)
            
    return unique_results

def extract_all_exact_matches(query: str) -> List[Dict]:
    """
    Extracts ALL explicitly mentioned laws and section/article numbers from the query
    using parse_multi_law_query, then scrolls Qdrant to fetch payload data.
    """
    query_lower = query.lower()
    matches = []
    
    # 1. Check HARD_REDIRECTS first
    for term, redirect in HARD_REDIRECTS.items():
        if term in query_lower:
            act_target = normalize_act_name(redirect['act'])
            sec_target = str(redirect['section'])
            try:
                hits, _ = qdrant.scroll(
                    collection_name=QDRANT_COLLECTION,
                    scroll_filter=Filter(must=[
                        FieldCondition(key="act",     match=MatchValue(value=act_target)),
                        FieldCondition(key="section", match=MatchValue(value=sec_target))
                    ]),
                    limit=1, with_payload=True, with_vectors=False
                )
                if hits and hits[0].payload is not None:
                    exact = dict(hits[0].payload)
                    exact["rrf_score"] = 2.0 / 60.0
                    exact["confidence_score"] = 1.0
                    exact["redirect_reason"] = redirect["reason"]
                    normalize_returned_act_name(exact)
                    matches.append(exact)
            except Exception as e:
                print(f"HARD_REDIRECT lookup failed: {e}")

    # 2. Call parse_multi_law_query to find all mentioned (Act, Num) pairs
    parsed_pairs = parse_multi_law_query(query)
    
    active_coll = QDRANT_COLLECTION_BGE if _voyage_rate_limited else QDRANT_COLLECTION
    for act, num in parsed_pairs:
        norm_act = normalize_act_name(act)
        try:
            if norm_act == "Constitution":
                hits, _ = qdrant.scroll(
                    collection_name=active_coll,
                    scroll_filter=Filter(must=[
                        FieldCondition(key="act", match=MatchValue(value="Constitution"))
                    ]),
                    limit=50, with_payload=True, with_vectors=False
                )
                matching = [h for h in hits if str(h.payload.get("article", "")).strip() == str(num) or str(h.payload.get("chunk_id", "")).endswith(f"_S{num}")]
                if matching and matching[0].payload:
                    exact = dict(matching[0].payload)
                    exact["rrf_score"] = 999.0
                    exact["confidence_score"] = 1.0
                    exact["is_exact_match"] = True
                    exact["_force_injected"] = True
                    exact["redirect_reason"] = f"Exact article lookup for Constitution Article {num}"
                    normalize_returned_act_name(exact)
                    matches.append(exact)
            else:
                hits, _ = qdrant.scroll(
                    collection_name=active_coll,
                    scroll_filter=Filter(must=[
                        FieldCondition(key="act", match=MatchValue(value=norm_act)),
                        FieldCondition(key="section", match=MatchValue(value=str(num)))
                    ]),
                    limit=1, with_payload=True, with_vectors=False
                )
                if not hits:
                    # Cross-collection fallback
                    hits, _ = qdrant.scroll(
                        collection_name=QDRANT_COLLECTION,
                        scroll_filter=Filter(must=[
                            FieldCondition(key="act", match=MatchValue(value=norm_act)),
                            FieldCondition(key="section", match=MatchValue(value=str(num)))
                        ]),
                        limit=1, with_payload=True, with_vectors=False
                    )
                if hits and hits[0].payload is not None:
                    exact = dict(hits[0].payload)
                    exact["rrf_score"] = 999.0
                    exact["confidence_score"] = 1.0
                    exact["is_exact_match"] = True
                    exact["_force_injected"] = True
                    exact["redirect_reason"] = f"Exact section lookup for {norm_act} Section {num}"
                    normalize_returned_act_name(exact)
                    matches.append(exact)
        except Exception as e:
            print(f"Scroll lookup failed for {norm_act} {num}: {e}")


    # Deduplicate matches by (act, section/article)
    unique_matches = []
    seen = set()
    for m in matches:
        act_key = str(m.get("act", ""))
        sec_key = str(m.get("section") or m.get("article") or "")
        key = (act_key, sec_key)
        if key not in seen:
            seen.add(key)
            unique_matches.append(m)
            
    return unique_matches

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
                    if hits and hits[0].payload is not None:
                        primary_chunk = dict(hits[0].payload)
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

def _enrich_with_graph_context(exact_match: dict) -> List[dict]:
    retrieved = [exact_match]
    chunk_id = exact_match.get("chunk_id")
    if chunk_id:
        try:
            from graph_retriever import traverse_graph
            graph_results = traverse_graph([chunk_id], max_hops=2)
            existing_ids = {chunk_id}
            for gr in graph_results:
                if gr.get("chunk_id") not in existing_ids and gr.get("text"):
                    gr.setdefault("act", "Unknown")
                    gr.setdefault("section", "")
                    gr.setdefault("confidence_score", 0.5)
                    gr["rrf_score"] = 0.5 * exact_match.get("rrf_score", 1.0)
                    normalize_returned_act_name(gr)
                    retrieved.append(gr)
                    existing_ids.add(gr.get("chunk_id"))
        except Exception as g_err:
            print(f"Graph RAG failed for exact redirect: {g_err}")
    return retrieved

def detect_query_domain(query: str) -> str:
    """Classifies a user query into a legal domain using regex word boundaries.
    
    Uses \\b word boundaries to prevent false substring matches like:
    - 'sim' matching inside 'similar'
    - 'online' matching inside 'faultline'
    - 'hack' matching inside 'unpack'
    """
    q_lower = query.lower()
    
    # Multi-word phrases are matched as-is; single words use \b boundaries.
    domain_patterns = {
        "cyber": [
            r"\botp\b", r"\bupi\b", r"\bhack(ed|ing)?\b", r"\bpassword\b",
            r"\bphishing\b", r"\bwhatsapp\b", r"\binstagram\b", r"\bemail\b",
            r"\bnetbanking\b", r"computer resource", r"unauthorized transfer",
            r"\bcybercrime\b", r"cyber crime", r"\bserver\b", r"\bmalware\b",
            r"bank account", r"\bdigital\b"
        ],
        "drugs": [
            r"\bdrugs?\b", r"\bmarijuana\b", r"\bweed\b", r"\bcocaine\b",
            r"\bheroin\b", r"\bopium\b", r"\bnarcotic\b", r"\bpsychotropic\b",
            r"\bndps\b", r"possession of drug"
        ],
        "cheque": [
            r"\bcheque\b", r"\bbounced\b", r"\bbounce\b", r"\bdishonour\b",
            r"negotiable instrument", r"bank memo", r"cheque bounce", r"\bni act\b"
        ],
        "homicide": [
            r"\bmurder(ed)?\b", r"\bkill(ed)?\b", r"\bhomicide\b",
            r"culpable homicide", r"death penalty", r"acid attack",
            # Poisoning / toxic substance causes — map to BNS §103 / §123
            r"\bpoison(ed|ing)?\b", r"\btoxic\b", r"\btoxin\b",
            r"intoxicat", r"lethal", r"fatal"
        ],
        "procedure": [
            r"\bfir\b", r"\barrest\b", r"\bbail\b", r"\bsummons?\b",
            r"\bwarrant\b", r"\bchargesheet\b", r"\bpolice\b", r"\bmagistrate\b",
            r"\btrial\b", r"\bremand\b", r"\bforensic\b"
        ],
        "evidence": [
            r"\bconfession\b", r"\bconfess\b", r"\badmissib(le|ility)\b",
            r"\bwitness\b", r"\bevidence\b", r"cross.examination",
            r"\btestimony\b", r"\bdeposition\b", r"burden of proof",
            r"\bpresumption\b", r"\bcorroboration\b", r"\bhearsay\b",
            r"dying declaration", r"electronic record", r"document admissibility",
            r"custodial confession", r"\brecovery\b", r"fact discovered", r"\bbsa\b"
        ],
        "hurt": [
            r"\bhurt\b", r"grievous hurt", r"\bfracture\b", r"\bstabb(ing|ed)?\b",
            r"acid attack", r"\bwound(ed)?\b", r"iron rod", r"knife attack",
            r"\bbeaten\b", r"\bbeating\b", r"\bslap(ped)?\b", r"\bpunch(ed)?\b",
            r"broken bone", r"broken tooth", r"\bdislocation\b", r"\bbruise\b",
            r"bodily harm", r"grievous injury", r"sharp weapon"
        ],
        # Property crime domain — routes to BNS §303-§334 chunks
        "property": [
            r"\btheft\b", r"\bstolen\b", r"\bstole\b", r"\bsteal\b",
            r"\brobbery\b", r"\brobbed\b", r"\bdacoity\b", r"\bextortion\b",
            r"\bburglary\b", r"house.?breaking", r"house.?trespass",
            r"break.?in", r"\btrespass\b", r"\bloot\b", r"\blooted\b",
            r"\bpickpocket\b", r"stolen property", r"snatching"
        ],
    }
    
    scores = {domain: 0 for domain in domain_patterns}
    for domain, patterns in domain_patterns.items():
        for pattern in patterns:
            if re.search(pattern, q_lower):
                scores[domain] += 2
                
    best_domain = "general"
    max_score = 0
    for domain, score in scores.items():
        if score > max_score:
            max_score = score
            best_domain = domain
            
    return best_domain

def get_section_domain(act: str, section: str) -> str:
    """Helper to determine the domain of a section based on its Act and section number."""
    if not act: return "general"
    act_upper = act.upper()
    sec_str = str(section).strip()
    
    if "IT" in act_upper:
        return "cyber"
    if "NDPS" in act_upper:
        return "drugs"
    if "NI" in act_upper:
        return "cheque"
    if "POCSO" in act_upper:
        return "child_abuse"
    
    # BNS-specific domain classification
    if "BNS" in act_upper:
        try:
            digits_match = re.search(r'\d+', sec_str)
            if digits_match:
                sec_num = int(digits_match.group(0))
                if 100 <= sec_num <= 113:
                    return "homicide"
                if 114 <= sec_num <= 125:
                    return "hurt"
                if 303 <= sec_num <= 334:
                    if 308 <= sec_num <= 324:
                        return "cyber"
                    return "property"
        except Exception:
            pass
            
    if "BNSS" in act_upper:
        return "procedure"
    if "BSA" in act_upper:
        return "evidence"
        
    return "general"

def hybrid_retrieve(query: str, k: int = 15, act_filter: Optional[List[str]] = None) -> Tuple[List[Dict], float]:
    """Qdrant native hybrid search: dense (1024-dim) + sparse (BM25) with RRF fusion, domain boosting, and act diversity filtering."""
    load_indices()  # warmup models

    act_filter_normalized = [normalize_act_name(a) for a in act_filter] if act_filter else None

    # ── Multi-redirect / exact section lookup ─────────────────────────────────
    exact_matches = extract_all_exact_matches(query)
    
    is_simple_lookup = False
    clean_q = re.sub(r'[^\w\s]', '', query.lower()).strip()
    words = clean_q.split()
    if len(words) <= 5 or (len(words) <= 7 and any(w in clean_q for w in ["what", "explain", "describe", "show"])):
        is_simple_lookup = True
        
    if exact_matches and is_simple_lookup:
        enriched_results = []
        for match in exact_matches:
            enriched_results.extend(_enrich_with_graph_context(match))
            
        unique_enriched = []
        seen_enriched = set()
        for r in enriched_results:
            key = (r.get("act"), r.get("section") or r.get("article"))
            if key not in seen_enriched:
                seen_enriched.add(key)
                unique_enriched.append(r)
        print(f"✓ Resolved exact matches for simple lookup: {[str(e.get('act', ''))+':'+str(e.get('section') or e.get('article') or '') for e in exact_matches]}")
        return apply_defence_sections(unique_enriched), 1.0

    # 1. Domain Detection
    best_domain = detect_query_domain(query)
    print(f"✓ Detected query domain: {best_domain}")

    query_expanded = query
    query_lower = query.lower()
    
    # ── Semantic synonym expansion for laying terms ──────────────────────────
    semantic_mappings = {
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
        r'\bduress\b': "BNS Section 32 compulsion threat general exception",
        r'\bnecessity\b': "BNS Section 30 BNS Section 31 general exception",
        r'\baccident\b': "BNS Section 25 accident general exception",
        r'\bmistake of fact\b': "BNS Section 27 general exception",
        r'\bgood faith\b': "BNS Section 26 general exception",
        r'\bconsent\b': "BNS Section 28 general exception",
        r'\binsanity\b': "BNS Section 22 general exception",
        r'\bunsound mind\b': "BNS Section 22 general exception",
        r'\binfancy\b': "BNS Section 20 child general exception",
        r'\bself.?defen[cs]e\b': "BNS Section 34 right private defence",
        r'\bintoxicat(ed|ion)\b': "BNS Section 22 general exception",
        r'\bsolitary confinement\b': "BNS Section 11 solitary confinement",
        r'\bhack(ed|ing)\b': "IT Act Section 66 Cyber Crime hacking unauthorized access computer resource",
        r'\bonline scam\b': "IT Act Section 66D cheating by personation computer resource online fraud",
        r'\bphish(ing)?\b': "IT Act Section 66D cheating by personation computer resource online fraud",
        r'\bcyber\b': "IT Act Section 66 computer resource cyber crime",
        r'\bemail\b': "IT Act Section 66 computer resource email communication device",
        r'\bdrugs?\b': "NDPS Act Section 20 Section 21 cannabis psychoactive substance possession narcotic",
        r'\bmarijuana\b': "NDPS Act Section 20 cannabis cultivation possession",
        r'\bweed\b': "NDPS Act Section 20 cannabis cultivation possession",
        r'\bcocaine\b': "NDPS Act Section 21 manufactured drug possession",
        r'\bcheque\b': "NI Act Section 138 cheque bounce dishonour of cheque bank debt",
        r'\bbounce(d)?\b': "NI Act Section 138 cheque bounce dishonour of cheque bank debt",
        # Default bail / statutory bail — expand to BNSS §479 for general procedural bail
        r'\bdefault bail\b': "BNSS Section 479 statutory bail bail by default chargesheet not filed mandatory release",
        r'\bstatutory bail\b': "BNSS Section 479 default bail chargesheet mandatory release",
        # REMOVED: unconditional child/minor → POCSO mapping.
        # Context-aware child/minor routing is handled below (after the loop).
        r'\bcaste\b': "SC_ST Act scheduled caste scheduled tribe atrocities",
        r'\bdalit\b': "SC_ST Act scheduled caste scheduled tribe atrocities",
        # Poisoning / toxic substance — route to BNS §103 (murder) and §123 (poisoning)
        r'\bpoison(ed|ing)?\b': "BNS Section 103 Murder culpable homicide death BNS Section 123 poisoning with intent to kill toxic substance",
        r'\btoxic\b': "BNS Section 123 poisoning toxic substance intent to cause death BNS Section 103 murder",
        # Dying declaration — explicitly pulls BSA §26
        r'dying declaration': "BSA Section 26 dying declaration statement made in expectation of death deponent death admissibility",
        # House-breaking / break-in — route to BNS §331 (house-breaking by night) and §303 theft
        r'house.?breaking': "BNS Section 331 house-breaking by night trespass with intent to commit theft",
        r'break.?in': "BNS Section 331 BNS Section 303 house-breaking by night breaking entry theft",
        r'\bburglary\b': "BNS Section 331 BNS Section 303 house-breaking by night theft stolen property",
    }
    
    expansions = []
    for pattern, term in semantic_mappings.items():
        if re.search(pattern, query_lower):
            expansions.append(term)

    # ── Context-aware NDPS bail routing ─────────────────────────────────────
    # Only inject NDPS §36A into expanded query if drug/NDPS context is present.
    _has_ndps_kw = re.search(r'\b(ndps|drug|drugs|narcotic|cannabis|heroin|cocaine|psychotropic)\b', query_lower)
    _has_bail_kw = re.search(r'\b(bail|default\s+bail|statutory\s+bail|180\s+days)\b', query_lower)
    if _has_ndps_kw and _has_bail_kw:
        expansions.append("NDPS Act Section 36A Section 37 Special Court 180 days default bail commercial quantity")

    # ── Guard: When query is bail-focused, remove offense-only expansions ────
    # E.g. "Rape accused bail" should retrieve BNSS§483 (bail), not BNS§64 (rape offense)
    # Remove rape/murder/hurt-only expansions when query explicitly asks about bail.
    _is_bail_query = bool(re.search(r'\b(bail|anticipatory bail|regular bail|default bail|statutory bail)\b', query_lower))
    if _is_bail_query:
        # Keep only bail/procedure expansions — remove offense-section-only entries
        bail_safe_expansions = []
        for exp in expansions:
            # Drop expansions that ONLY reference offense sections without any BNSS/bail context
            if re.search(r'BNSS|bail|Section 479|Section 480|Section 482|Section 483|Section 437|Section 439', exp, re.I):
                bail_safe_expansions.append(exp)
            elif re.search(r'NDPS|Section 36A|Section 37', exp, re.I):
                bail_safe_expansions.append(exp)  # keep NDPS bail expansions
            elif not re.search(r'BNS Section (64|103|117|118|303|309|310|308|318)', exp, re.I):
                bail_safe_expansions.append(exp)  # keep non-offense expansions
        if bail_safe_expansions != expansions:
            print(f"⚖️ Bail query guard: filtered {len(expansions)-len(bail_safe_expansions)} offense-only expansions")
        expansions = bail_safe_expansions

    # ── Context-aware child/minor routing ────────────────────────────────────
    # Only route to POCSO if sexual-assault context words are present.
    # Without this guard, "7 year old child poisoned" gets pulled toward POCSO
    # sexual assault chunks instead of BNS murder/hurt sections.
    _has_child_word = re.search(r'\b(child|children|minor|juvenile|under\s*18|boy|girl)\b', query_lower)
    _has_sexual_word = re.search(
        r'\b(sexual|molest|rape|penetrat|pocso|indecen|obscen|pornograph|grope|assault\s+on\s+child|child\s+sexual|child\s+abuse)\b',
        query_lower
    )
    if _has_child_word and _has_sexual_word:
        expansions.append("POCSO Act child sexual abuse minor penetrative assault")
    # else: BNS semantic_mappings (murder, hurt, etc.) handle non-sexual child queries
            
    # Domain-specific keyword expansions (forces dense vectors to correct domain)
    if best_domain == "cyber":
        expansions.append("IT Act Section 66 BNS Section 318 Section 319 Section 308 cyber fraud identity theft unauthorized access computer resource financial transaction digital hacking OTP fraud online banking fraud electronic record")
    elif best_domain == "drugs":
        expansions.append("NDPS Act cannabis manufactured drug psychotropic substance narcotic possession commercial quantity small quantity")
    elif best_domain == "cheque":
        expansions.append("NI Act Section 138 cheque bounce bank debt liability dishonour of cheque")
    elif best_domain == "homicide":
        expansions.append("BNS Section 103 Murder culpable homicide Section 105 culpable homicide not amounting to murder Section 109 attempt to murder Section 123 poisoning with intent to kill death penalty")
    elif best_domain == "procedure":
        expansions.append("BNSS procedure summons warrant arrest bail remand trial chargesheet police report")
    elif best_domain == "evidence":
        expansions.append("BSA Bharatiya Sakshya Adhiniyam Section 26 dying declaration Section 23 Section 24 confession admissibility witness statement burden of proof electronic record dying declaration fact discovered recovery proviso")
    elif best_domain == "hurt":
        expansions.append("BNS Section 115 Section 117 Section 118 voluntarily causing hurt grievous hurt dangerous weapon bodily injury fracture dislocation bone tooth")
    elif best_domain == "property":
        expansions.append("BNS Section 303 Theft Section 305 House-trespass Section 307 House-trespass in order to commit offence Section 331 House-breaking after sunset before sunrise by night Section 309 Robbery Section 310 Dacoity Section 317 dishonestly receiving stolen property")

    # ── NDPS bail dual-boost: when query has BOTH drug+bail keywords, boost NDPS sections ──
    # The NDPS §36A chunk contains the 180-day special court provision for drug offences.
    # Normal procedure domain boost would not cover NDPS. So we explicitly boost NDPS here.
    if _has_ndps_kw and _has_bail_kw:
        print("⚡ NDPS+bail detected: will apply dual boost to NDPS sections in domain boost phase")

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

    # ── Qdrant Hybrid Search ──────────────────────────────────────────────────
    # Switch to BGE collection when Voyage is rate-limited so query and corpus
    # embeddings are in the same vector space (prevents near-random dense retrieval).
    active_collection = QDRANT_COLLECTION_BGE if _voyage_rate_limited else QDRANT_COLLECTION
    print(f"✓ Using Qdrant collection: {active_collection} (voyage_limited={_voyage_rate_limited})")
    try:
        results = qdrant.query_points(
            collection_name=active_collection,
            prefetch=[
                Prefetch(query=dense_vec,  using="dense",  limit=k * 3),
                Prefetch(query=sparse_vec, using="sparse", limit=k * 3),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=k * 2,
            query_filter=qdrant_filter,
            with_payload=True,
        )
        retrieved = []
        for point in results.points:
            payload = dict(point.payload) if point.payload else {}
            payload["rrf_score"] = float(point.score)
            payload["confidence_score"] = float(point.score)
            normalize_returned_act_name(payload)
            retrieved.append(payload)
        print(f"✓ Qdrant hybrid search returned {len(retrieved)} candidates")
    except Exception as e:
        print(f"Qdrant hybrid search failed: {e}. Returning empty.")
        retrieved = []

    # ── Hard Redirect / Exact Section Lookup Guard ────────────────────────────
    # If the user typed an explicit citation like "section 102 BNS" or "BNS 102",
    # fetch the exact section from Qdrant and inject it at Rank 1.
    try:
        redirect_res = apply_hard_redirect(query)
        if redirect_res.get("redirect") and redirect_res.get("match"):
            exact_match = redirect_res["match"]
            max_r = max((r["rrf_score"] for r in retrieved), default=1.0)
            exact_match["rrf_score"] = max_r * 1.10
            exact_match["confidence_score"] = 1.0
            exact_match["_force_injected"] = True
            exact_match["is_exact_match"] = True
            exact_match["rerank_score"] = 999.0
            # Prepend exact match to retrieved candidates
            retrieved.insert(0, exact_match)
            print(f"🎯 Exact section hard-redirect applied: {exact_match.get('act')} §{exact_match.get('section')}")

    except Exception as _e_hdr:
        print(f"Hard redirect check failed: {_e_hdr}")


    # ── Similarity Floor — drop chunks with negligible RRF scores ────────────
    # Chunks scoring below 15% of the top result are garbage from the tail of
    # the search and will only cause LLM hallucination if passed forward.
    if retrieved:
        max_rrf = max(r["rrf_score"] for r in retrieved)
        floor   = max_rrf * 0.15
        before  = len(retrieved)
        retrieved = [r for r in retrieved if r["rrf_score"] >= floor]
        print(f"✓ Similarity floor {floor:.4f} applied: {before} → {len(retrieved)} candidates")

    # ── Domain Boost (AFTER search — retrieved now exists) ───────────────────
    # The domain field is NOT stored in Qdrant payloads (they are all MISSING).
    # Derive domain on-the-fly from act + section number so the boost works correctly.
    def _infer_domain_from_payload(r: dict) -> str:
        """Derive domain from act name + section number since payload.domain is not set."""
        act = str(r.get("act", "")).upper()
        if "IT" in act:
            return "cyber"
        if "NDPS" in act:
            return "drugs"
        if "NI" in act:
            return "cheque"
        if "POCSO" in act:
            return "child_abuse"
        if "BSA" in act:
            return "evidence"
        if "BNSS" in act:
            return "procedure"
        if "BNS" in act:
            try:
                sec_str = str(r.get("section") or r.get("article") or "")
                digits = re.search(r'\d+', sec_str)
                if digits:
                    n = int(digits.group(0))
                    if 100 <= n <= 113: return "homicide"
                    if 114 <= n <= 125: return "hurt"
                    if 303 <= n <= 334:
                        if 308 <= n <= 324: return "cyber"
                        return "property"
                    if 1 <= n <= 99: return "general"  # general exceptions
            except Exception:
                pass
        return "general"

    if retrieved and best_domain != "general":
        print(f"🎯 Domain detected: {best_domain}. Applying 30% soft score boost to matching domains.")
        for r in retrieved:
            cand_domain = _infer_domain_from_payload(r)
            if cand_domain == best_domain:
                r["rrf_score"] *= 1.30
            elif best_domain == "cyber" and cand_domain == "cheque":
                r["rrf_score"] *= 0.30
        # NDPS dual-boost: if query has both ndps+bail keywords, also boost NDPS §36A/§37
        # regardless of whether the primary domain is procedure or drugs.
        if _has_ndps_kw and _has_bail_kw:
            for r in retrieved:
                act_u = str(r.get("act", "")).upper()
                sec_s = str(r.get("section") or "").upper()
                if "NDPS" in act_u and sec_s in ("36A", "37", "36B"):
                    r["rrf_score"] *= 1.50  # Strong boost for NDPS special court default bail
                    print(f"⚡ NDPS §{sec_s} dual-boosted (ndps+bail query)")
        
        # Suppress BNS §43 (right of private defence of property) when it appears
        # in property-theft queries — it's a defence section, not the primary offence.
        _query_is_property = (best_domain == "property")
        if _query_is_property:
            for r in retrieved:
                act_u = str(r.get("act", "")).upper()
                sec_s = str(r.get("section") or "").strip()
                # BNS §43 is the defence of property section — penalise it
                if "BNS" in act_u and sec_s in ("43", "36", "37", "38"):
                    r["rrf_score"] *= 0.25
                    print(f"⚠️ BNS §{sec_s} (defence section) suppressed in property query")
        retrieved.sort(key=lambda x: x["rrf_score"], reverse=True)

    # ── NDPS §36A / §36B Noise Guard ─────────────────────────────────────────
    # These two chunks score high via BM25 in almost every query because their
    # text contains highly common procedural words. Penalise them heavily when
    # the query has NO drug/narcotic context.
    _query_has_drug_ctx = bool(re.search(
        r'\b(ndps|drug|drugs|narcotic|cannabis|heroin|cocaine|psychotropic|opium|ganja|charas|smack|contraband)\b',
        query.lower()
    ))
    if not _query_has_drug_ctx:
        for r in retrieved:
            act_u = str(r.get("act", "")).upper()
            sec_s = str(r.get("section") or "").upper()
            if "NDPS" in act_u and sec_s in ("36A", "36B", "36C", "37", "39"):
                r["rrf_score"] *= 0.05  # crush to noise level
                print(f"⚠️ NDPS §{sec_s} suppressed (no drug context in query)")
        retrieved.sort(key=lambda x: x["rrf_score"], reverse=True)

    # ── Semantic Force-Inject Guard ───────────────────────────────────────────
    # BM25/dense retrieval consistently misses it because the section's stored
    # text uses different vocabulary than the query (e.g. BSA§26 text says
    # "person who is dead" not "dying declaration"). Force-fetch and inject.
    #
    # Two-tier scoring:
    #   guarantee=True  → inject at 102% of max_rrf  → guaranteed rank-1
    #   guarantee=False → inject at 88% of max_rrf   → guaranteed top-3
    #
    # Check only top-5 (not all 24 results) so sections at rank 6+ still get injected.
    _FORCE_INJECT_RULES = [
        # BSA§26 — dying declaration (chunk text says "person who is dead", not "dying declaration")
        (r'dying\s+declaration',   'BSA',    '26',  True),
        # BNSS§483 — bail by High Court/Sessions Court in serious offences incl. rape
        (r'\brape\b.{0,40}\bbail\b', 'BNSS', '483', True),
        (r'\bbail\b.{0,40}\brape\b', 'BNSS', '483', True),
        # IT Act §66 — hacking/cyber offence (BNSS procedural chunks dominate in BGE fallback)
        # Use .* (no char limit) instead of {0,50} which was silently failing.
        # NOTE: Qdrant stores IT Act as act='IT' (not 'IT_Act')
        (r'\bhacking?\b.*(?:netbanking|net.?bank|bank\b|otp\b)',  'IT', '66', True),
        (r'(?:netbanking|net.?bank|otp\b).*\bhacking?\b',         'IT', '66', True),
        (r'\bhacking?\b.*(?:transfer|fraud|money|account)',        'IT', '66', True),
        # IT Act §66C — identity theft / OTP misuse
        (r'\botp\b.{0,40}(?:fraud|steal|stolen|misuse|transfer)', 'IT', '66C', False),
        (r'(?:identity\s+theft|impersonat)',                        'IT', '66C', False),
        # BNS§303 — Theft as PRIMARY offence with break-in context (guarantee rank-1)
        (r'\b(?:break.?in|house.?breaking|burglary|housebreak)\b', 'BNS', '303', True),
        # BNS§303 — Theft as top-3 fallback for general theft/stolen queries
        (r'\b(?:theft|stole|steal|thieves?)\b',                    'BNS', '303', False),
        # BNSS§173 — FIR registration
        (r'\bfir\b.{0,40}(?:register|refus|deny|denied|not\s+register)', 'BNSS', '173', False),
    ]

    if retrieved:
        max_rrf = max(r["rrf_score"] for r in retrieved)
        # Only check TOP-5 — sections at rank 6+ should still be injected to rank 2-3
        _top5_keys = {
            (str(r.get("act","")).upper(), str(r.get("section") or "").upper())
            for r in retrieved[:5]
        }
        _injected = []
        _injected_keys: set = set()
        for pattern, force_act, force_sec, guarantee in _FORCE_INJECT_RULES:
            if re.search(pattern, query_lower):
                key = (force_act.upper(), force_sec.upper())
                if key in _top5_keys or key in _injected_keys:
                    # If section is already in top-5 but guarantee=True, boost score in-place
                    # (e.g. BNSS§483 naturally at rank-3 → promote to rank-1)
                    if guarantee and key not in _injected_keys:
                        for _r in retrieved:
                            _rkey = (str(_r.get("act","")).upper(), str(_r.get("section") or "").upper())
                            if _rkey == key:
                                _r["rrf_score"] = max_rrf * 1.02
                                _injected_keys.add(key)
                                print(f"⬆️ Score-boosted {force_act}§{force_sec} to rank-1 [was in top-5]")
                                break
                        retrieved.sort(key=lambda x: x["rrf_score"], reverse=True)
                    continue

                try:
                    # Try active collection first; fall back to primary if section not found.
                    # IT_Act§66 may exist only in law_sections, not law_sections_bge.
                    _hits, _ = qdrant.scroll(
                        collection_name=active_collection,
                        scroll_filter=Filter(must=[
                            FieldCondition(key="act",     match=MatchValue(value=force_act)),
                            FieldCondition(key="section", match=MatchValue(value=force_sec))
                        ]),
                        limit=1, with_payload=True, with_vectors=False
                    )
                    if not _hits or not _hits[0].payload:
                        _hits, _ = qdrant.scroll(
                            collection_name=QDRANT_COLLECTION,
                            scroll_filter=Filter(must=[
                                FieldCondition(key="act",     match=MatchValue(value=force_act)),
                                FieldCondition(key="section", match=MatchValue(value=force_sec))
                            ]),
                            limit=1, with_payload=True, with_vectors=False
                        )
                        if _hits:
                            print(f"ℹ️  Force-inject cross-collection fallback for {force_act}§{force_sec}")
                    if _hits and _hits[0].payload:
                        forced = dict(_hits[0].payload)
                        forced["rrf_score"] = max_rrf * (1.02 if guarantee else 0.88)
                        forced["confidence_score"] = 0.95 if guarantee else 0.85
                        forced["_force_injected"] = True
                        normalize_returned_act_name(forced)
                        _injected.append(forced)
                        _injected_keys.add(key)
                        rank_label = "rank-1 guaranteed" if guarantee else "top-3 guaranteed"
                        print(f"💉 Force-injected {force_act}§{force_sec} [{rank_label}]")
                except Exception as _e:
                    print(f"Force-inject failed for {force_act}§{force_sec}: {_e}")
        if _injected:
            retrieved.extend(_injected)
            retrieved.sort(key=lambda x: x["rrf_score"], reverse=True)


    # ── Deduplicate by (act, section) — keep highest-scoring copy ───────────
    # The same section can appear from dense retrieval, sparse retrieval, AND
    # graph traversal, wasting valuable top-k slots.
    _seen_keys: Dict[tuple, Dict] = {}
    for r in retrieved:
        key = (str(r.get("act", "")), str(r.get("section") or r.get("article") or ""))
        if key not in _seen_keys or r["rrf_score"] > _seen_keys[key]["rrf_score"]:
            _seen_keys[key] = r
    retrieved = sorted(_seen_keys.values(), key=lambda x: x["rrf_score"], reverse=True)
    print(f"✓ Deduplication: {len(_seen_keys)} unique (act, section) pairs remain")

    # ── Act Diversity Filter (Max 5 candidates per Act) ─────────────────────
    diverse_retrieved = []
    act_counts: Dict[str, int] = {}
    for r in retrieved:
        act_name = str(r.get("act", ""))
        act_counts[act_name] = act_counts.get(act_name, 0) + 1
        if act_counts[act_name] <= 5:
            diverse_retrieved.append(r)
    retrieved = diverse_retrieved

    # ── Merge exact matches & their graph neighbors ──────────────────────────
    if exact_matches:
        exact_enriched = []
        for match in exact_matches:
            exact_enriched.extend(_enrich_with_graph_context(match))
            
        existing_keys = set()
        merged_list = []
        
        # Prepend exact matches first with high score
        for match in exact_enriched:
            is_direct_match = any(
                match.get("act") == em.get("act") and 
                str(match.get("section") or match.get("article")) == str(em.get("section") or em.get("article"))
                for em in exact_matches
            )
            if is_direct_match:
                match["is_exact_match"] = True
                match["rrf_score"] = max(match.get("rrf_score", 0.0), 10.0)
            else:
                match["rrf_score"] = max(match.get("rrf_score", 0.0), 5.0)
                
            key = (match.get("act"), str(match.get("section") or match.get("article")))
            existing_keys.add(key)
            merged_list.append(match)
            
        # Append general hybrid results if not already present
        for r in retrieved:
            key = (r.get("act"), str(r.get("section") or r.get("article")))
            if key not in existing_keys:
                existing_keys.add(key)
                merged_list.append(r)
                
        retrieved = merged_list

    # Slice back to top k (guaranteeing exact matches survive)
    exacts = [r for r in retrieved if r.get("is_exact_match")]
    others = [r for r in retrieved if not r.get("is_exact_match")]
    allowed_others_count = max(0, k - len(exacts))
    retrieved = exacts + others[:allowed_others_count]

    if retrieved:
        max_score = retrieved[0]["rrf_score"]
        for r in retrieved:
            r["confidence_score"] = min(r["rrf_score"] / max(max_score, 1e-9), 1.0)

    # Graph RAG augmentation — traverse Neo4j from top entry points
    try:
        from graph_retriever import traverse_graph
        entry_ids = [str(r.get("chunk_id")) for r in retrieved[:3] if r.get("chunk_id")]
        if entry_ids:
            graph_results = traverse_graph(entry_ids, max_hops=2)
            existing_ids = {r.get("chunk_id") for r in retrieved}
            for gr in graph_results:
                if gr.get("chunk_id") not in existing_ids and gr.get("text"):
                    gr.setdefault("act", "Unknown")
                    gr.setdefault("section", "")
                    gr.setdefault("confidence_score", 0.5)
                    retrieved.append(gr)
                    existing_ids.add(gr.get("chunk_id"))
            print(f"✓ Graph augmented: +{len(graph_results)} connected sections")
    except Exception as e:
        print(f"WARNING: Graph RAG traversal skipped: {e}")

    retrieved = apply_defence_sections(retrieved)
    top_confidence = retrieved[0]["confidence_score"] if retrieved else 0.0
    return retrieved, top_confidence

# ==========================================
# Cross-Encoder Reranking
# ==========================================
_local_reranker = None

def rerank_candidates(query: str, candidates: List[Dict]) -> List[Dict]:
    """
    Reranks retrieved candidates using:
    1. Primary: HuggingFace API BGE reranker (cloud)
    2. Fallback 1: Local CPU BGE reranker
    3. Fallback 2: Cosine similarity fallback (zero additional API calls)
    Also applies Special Law Library (SLL) boosting post-rerank.
    """
    if not candidates:
        return candidates

    query_lower = query.lower()
    reranked = False

    # ── Attempt 1: HuggingFace Cloud Reranker ───────────────────────────────
    if HF_TOKEN:
        try:
            pairs = [[query, c.get("text", "")] for c in candidates]
            headers = {
                "Authorization": f"Bearer {HF_TOKEN}",
                "Content-Type": "application/json"
            }
            r = httpx.post(
                HF_RERANKER_LARGE_URL,
                headers=headers,
                json={"inputs": pairs},
                timeout=30.0
            )
            if r.status_code == 200:
                scores_raw = r.json()
                if isinstance(scores_raw, list) and len(scores_raw) == len(candidates):
                    for i, c in enumerate(candidates):
                        if c.get("is_exact_match") or c.get("_force_injected"):
                            c["rerank_score"] = 999.0
                        else:
                            score_item = scores_raw[i]
                            if isinstance(score_item, dict):
                                c["rerank_score"] = score_item.get("score", 0.0)
                            elif isinstance(score_item, list) and score_item:
                                best = max(score_item, key=lambda x: x.get("score", 0.0))
                                c["rerank_score"] = best.get("score", 0.0)
                            else:
                                c["rerank_score"] = float(score_item) if isinstance(score_item, (int, float)) else 0.0

                    # Default safety for unmatched indices
                    for c in candidates:
                        if "rerank_score" not in c:
                            c["rerank_score"] = 0.0
                    reranked = True
        except Exception as e:
            print(f"HF cloud reranker failed: {e}. Trying local reranker.")

    # ── Attempt 2: Local CPU Reranker ────────────────────────────────────────
    if not reranked and _local_reranker is not None:
        try:
            pairs = [{"text": query, "text_pair": c.get("text", "")} for c in candidates]
            results = _local_reranker(pairs, truncation=True, max_length=512)
            scores = [r["score"] if isinstance(r, dict) else float(r) for r in results]
            for i, c in enumerate(candidates):
                if c.get("is_exact_match") or c.get("_force_injected"):
                    c["rerank_score"] = 999.0
                else:
                    c["rerank_score"] = scores[i]

            reranked = True
            print(f"✓ Local CPU cross-encoder reranker succeeded for {len(candidates)} candidates.")
        except Exception as e:
            print(f"Local reranker failed: {e}. Falling back to cosine similarity.")

    # ── Attempt 3: Cosine similarity fallback ────────────────────────────────
    if not reranked:
        try:
            query_emb = get_query_embedding(query)
            for c in candidates:
                text_emb_raw = c.get("embedding")
                if text_emb_raw and len(text_emb_raw) == 1024:
                    dot = sum(a * b for a, b in zip(query_emb, text_emb_raw))
                    norm_q = math.sqrt(sum(x * x for x in query_emb))
                    norm_t = math.sqrt(sum(x * x for x in text_emb_raw))
                    c["rerank_score"] = dot / (norm_q * norm_t + 1e-9)
                else:
                    # Sigmoid activation to map logits (-inf, +inf) to valid probabilities [0.0, 1.0]
                    rrf = c.get("rrf_score", 0.0)
                    c["rerank_score"] = 1.0 / (1.0 + math.exp(-rrf))
                c["rerank_source"] = "cosine_fallback"
        except Exception as e2:
            print(f"Cosine fallback also failed: {e2}. Using raw rrf_score.")
            for c in candidates:
                c["rerank_score"] = c.get("rrf_score", 0.0)


    # ── Special Law Library (SLL) Boosting ───────────────────────────────────
    target_sll = []
    if any(w in query_lower for w in ["hack", "online", "cyber", "internet", "email", "instagram", "facebook", "whatsapp", "computer", "data"]):
        target_sll.append("IT")
    if any(w in query_lower for w in ["drug", "marijuana", "weed", "cocaine", "narcotic", "smuggle", "contraband", "drug possession", "narcotic possession"]):
        target_sll.append("NDPS")
    if any(w in query_lower for w in ["cheque", "check", "bounce", "dishonor", "bank"]):
        target_sll.append("NI")
    # Only boost POCSO when both a child word AND a sexual context word are present,
    # or when an explicit POCSO trigger is found. Bare "child" or "assault" alone
    # must NOT trigger POCSO — they appear in murder, hurt, and custody queries.
    _sll_has_child = any(w in query_lower for w in ["child", "minor", "juvenile"])
    _sll_has_sexual = any(w in query_lower for w in ["sexual", "molest", "rape", "penetrat", "indecen", "obscen", "pornograph"])
    _sll_explicit_pocso = any(w in query_lower for w in ["pocso", "child sexual", "child abuse", "penetrative assault"])
    if _sll_explicit_pocso or (_sll_has_child and _sll_has_sexual):
        target_sll.append("POCSO")
    if any(w in query_lower for w in ["caste", "dalit", "sc/st", "atrocit"]):
        target_sll.append("SC_ST")
    if any(w in query_lower for w in ["dowry", "dahej", "groom", "bride", "wedding"]):
        target_sll.append("Dowry")
    if any(w in query_lower for w in ["money laundering", "black money", "laundering", "pmla"]):
        target_sll.append("PMLA")

    if target_sll:
        print(f"🚀 SLL Intent Detected (Post-Rerank): {target_sll}. Applying 30% score boost to matching acts.")
        for c in candidates:
            if c.get("act") in target_sll and "rerank_score" in c:
                c["rerank_score"] *= 1.30

    # Normalize confidence score based on the absolute rerank relevance score
    for c in candidates:
        if c.get("is_exact_match") or c.get("_force_injected"):
            c["rerank_score"] = 999.0
            c["confidence_score"] = 1.0
        elif "rerank_score" in c:
            # Map score to [0.0, 1.0] interval
            # Sigmoid maps BGE logits (-inf,+inf) -> (0,1); clamp discards valid negative logits
            c["confidence_score"] = 1.0 / (1.0 + math.exp(-c["rerank_score"]))

    return sorted(candidates, key=lambda x: x["rerank_score"], reverse=True)


# ==========================================
# Response Caching
# ==========================================
def _normalize_query_for_cache(query: str) -> str:
    # Lowercase and remove punctuation
    text = query.lower()
    text = re.sub(r'[^\w\s]', '', text)
    # Split into words, remove common stop words/phrases, sort alphabetically, and join
    words = text.split()
    stop_words = {
        "a", "an", "the", "is", "of", "in", "on", "for", "with", "about", "what", "which", 
        "under", "section", "sec", "explain", "describe", "tell", "me", "how", "why", "are", 
        "to", "and", "please", "advise", "analyse", "analysis"
    }
    filtered_words = [w for w in words if w not in stop_words and len(w) > 1]
    # DO NOT sort — word order preserves legal query intent
    # e.g. "bail for murder" != "murder for bail"
    return " ".join(filtered_words)

def get_cached_response(query: str, is_lawyer_mode: bool = False) -> Optional[dict]:
    if supabase is None:
        return None
    normalized = _normalize_query_for_cache(query)
    key_str = f"{normalized}_{is_lawyer_mode}"
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
    normalized = _normalize_query_for_cache(query)
    key_str = f"{normalized}_{is_lawyer_mode}"
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
        computed_score = float(chunk.get("confidence_score", 0.5))

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
- To seek BAIL = Regular Bail (BNSS S.480), Anticipatory Bail (BNSS S.482), Default/Statutory Bail (BNSS S.479)
- For NDPS bail = NDPS S.36A (Special Court jurisdiction + default bail proviso) and NDPS S.37 (bail conditions); cite alongside BNSS S.479
- To challenge an ORDER = Revision (BNSS S.442) or Writ (Constitution Art.226/32)
- For DEFENSE evaluation = No filing needed; analysis only; note mitigating factors for sentencing if defense fails

═══════════════════════════════════════════════
FEW-SHOT REASONING EXAMPLES (CONCISE)
═══════════════════════════════════════════════

EXAMPLE 1 (SENTENCING_REVIEW):
Scenario: "9 months sentence with 3 months solitary confinement."
Output: {{"status":"SUCCESS","conclusion":"UNLAWFUL","conclusion_summary":"Solitary confinement exceeds 2-month cap under BNS Sec 11 for a 9-month sentence.","legal_issue":"Is 3 months solitary confinement lawful for a 9-month sentence?","query_type":"SENTENCING_REVIEW","applicable_laws":[{"law_name":"BNS 2023, Section 11","confidence_score":1.0,"verbatim_text":"not exceeding two months...","role":"SENTENCING_PROVISION","statutory_analysis":{{"exceptions_found":["not exceeding 2 months"],"exception_applies":true,"exception_effect":"Caps solitary confinement at 2 months","quantitative_checks":[{"parameter":"solitary confinement","statutory_limit":"2 months","actual_value":"3 months","result":"EXCEEDS_LIMIT"}],"qualitative_checks":[]}},"plain_english_explanation":"Solitary confinement capped at 2 months for 9-month sentence.","how_it_applies_to_facts":"3 months exceeds 2 month limit."}],"procedural_remedy":{{"recommended_action":"Appeal sentence","bnss_provision":"BNSS Section 373","court":"Sessions Court"}},"mitigating_factors":[]}}

EXAMPLE 2 (DEFENSE_EVALUATION):
Scenario: "Forced to commit murder at gunpoint."
Output: {{"status":"SUCCESS","conclusion":"DEFENSE_NOT_AVAILABLE","conclusion_summary":"Compulsion defense under Section 32 excludes murder.","legal_issue":"Is compulsion defense available for murder?","query_type":"DEFENSE_EVALUATION","applicable_laws":[{"law_name":"BNS 2023, Section 32","confidence_score":1.0,"verbatim_text":"Except murder...","role":"DEFENSE_SECTION","statutory_analysis":{{"exceptions_found":["Except murder"],"exception_applies":true,"exception_effect":"Bars compulsion defense for murder","quantitative_checks":[],"qualitative_checks":[{"condition":"duress threat","factual_indicators":"gunpoint","assessment":"SATISFIED"}]}},"plain_english_explanation":"Compulsion defense does not apply to murder.","how_it_applies_to_facts":"Murder is excluded from Section 32."}],"procedural_remedy":{{"recommended_action":"Present mitigating factors at trial","bnss_provision":"N/A","court":"Sessions Court"}},"mitigating_factors":["Extreme duress"]}}

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

def _extract_json(text: str) -> dict:
    """Robust JSON extractor handling raw strings, markdown codeblocks, and JS-style booleans.
    
    Handles:
    - Strict JSON (standard)
    - Markdown ```json ... ``` code blocks
    - Python/JS-style literals: True/False/None -> true/false/null
    - Outermost brace extraction as last resort
    """
    if not text or not isinstance(text, str):
        raise ValueError("Empty or non-string response from LLM")
    text = text.strip()

    def _try_parse(s: str):
        # First try strict JSON
        try:
            d = json.loads(s)
            if isinstance(d, dict):
                return d
        except (json.JSONDecodeError, ValueError):
            pass
        # Then fix Python/JS literals (True -> true, False -> false, None -> null)
        fixed = re.sub(r'\bTrue\b', 'true', s)
        fixed = re.sub(r'\bFalse\b', 'false', fixed)
        fixed = re.sub(r'\bNone\b', 'null', fixed)
        try:
            d = json.loads(fixed)
            if isinstance(d, dict):
                return d
        except (json.JSONDecodeError, ValueError):
            pass
        return None

    # 1. Direct parse
    result = _try_parse(text)
    if result is not None:
        return result

    # 2. Strip markdown code block wrappers
    m = re.search(r'```(?:json)?\s*(.*?)```', text, re.DOTALL | re.IGNORECASE)
    if m:
        result = _try_parse(m.group(1).strip())
        if result is not None:
            return result

    # 3. Outermost curly braces match
    m = re.search(r'(\{.*\})', text, re.DOTALL)
    if m:
        result = _try_parse(m.group(1).strip())
        if result is not None:
            return result

    raise ValueError("No valid JSON object found in response string")

# ==========================================
# Run RAG Pipeline
# ==========================================
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
def analyze(query: str, is_lawyer_mode: bool = False, user_id: Optional[str] = None) -> dict:
    """Core entry point for the RAG pipeline."""
    # 1. Cache Check (bypass cache for exact section citations so they always run fresh & update)
    is_exact_sec = False
    try:
        is_exact_sec = apply_hard_redirect(query).get("redirect", False)
    except Exception:
        pass

    if os.getenv("DISABLE_CACHE") != "true" and not is_exact_sec:
        cached = get_cached_response(query, is_lawyer_mode)
        if cached:
            print("✓ Returning cached query response.")
            return cached


    # 2. Domain Detection
    best_domain = detect_query_domain(query)
    print(f"✓ Detected query domain: {best_domain}")

    query_expanded = query
    query_lower = query.lower()

    # 3. Hybrid Retrieve
    retrieved, confidence = hybrid_retrieve(query_expanded, k=15)
    if not retrieved or confidence < 0.15:
        return {
            "status": "INSUFFICIENT_DATA",
            "applicable_laws": [],
            "reason": "Retrieved documents do not meet relevance threshold.",
            "disclaimer": "This analysis is for educational and research purposes only. Consult a certified advocate."
        }

    # 4. Rerank
    reranked = rerank_candidates(query, retrieved)
    exacts_reranked = [r for r in reranked if r.get("is_exact_match")]
    others_reranked = [r for r in reranked if not r.get("is_exact_match")]
    top_candidates = exacts_reranked + others_reranked[:max(0, 8 - len(exacts_reranked))]
    
    # 5. LLM Synthesis with full no-LLM fallback
    from action_engine import get_first_steps
    from cases_v2 import retrieve_similar_judgments

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
            raw_emb = get_query_embedding(query)
            response_data["similar_judgments"] = retrieve_similar_judgments(query, sec_list, k=3, query_embedding=raw_emb)
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
                from credit_manager_v2 import SessionLocal, Conversation
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

    # Inject local fallback warning if Voyage AI was rate-limited
    global _voyage_rate_limited
    if _voyage_rate_limited:
        response_data["embedding_notice"] = (
            "⚠️ Primary embedding API (Voyage AI) is rate-limited. "
            "Using local BGE fallback model — results may have reduced accuracy. "
            "Add a payment method at dashboard.voyageai.com to unlock standard rate limits."
        )

    return response_data
