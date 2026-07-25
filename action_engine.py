import os
import re
import json
import threading
from typing import Dict, List, Any
from dotenv import load_dotenv

# Load environment variables
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(ENV_PATH)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
YAML_FILE_PATH = os.path.join(os.path.dirname(__file__), "action_rules.yaml")

def evaluate_condition(condition_str: str, metadata: Dict) -> bool:
    """
    Safely parses and evaluates basic AND condition strings (e.g. 'cognizable == true AND bailable == false')
    against a metadata dictionary without using eval().
    """
    terms = re.split(r'\s+AND\s+', condition_str, flags=re.IGNORECASE)
    
    for term in terms:
        match = re.match(r'(\w+)\s*==\s*(true|false)', term.strip(), re.IGNORECASE)
        if not match:
            return False
        
        var_name = match.group(1).lower()
        expected_val_str = match.group(2).lower()
        expected_val = (expected_val_str == "true")
        
        actual_val = metadata.get(var_name, False)
        if isinstance(actual_val, str):
            actual_val = (actual_val.lower() == "true")
            
        if actual_val != expected_val:
            return False
            
    return True

def load_rules(file_path: str) -> Dict:
    """
    Loads rules from the action_rules.yaml file.
    Uses PyYAML if available; otherwise falls back to a clean custom parser.
    """
    try:
        import yaml  # type: ignore[import-untyped]
        with open(file_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except ImportError:
        # Fallback line-based YAML parser for action_rules.yaml structure
        rules: List[Dict[str, Any]] = []
        current_rule: Dict[str, Any] | None = None
        current_list = None
        
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or stripped.startswith("rules:"):
                    continue
                
                if stripped.startswith("- condition:"):
                    if current_rule:
                        rules.append(current_rule)
                    cond = stripped.split("condition:")[1].strip().strip('"').strip("'")
                    current_rule = {"condition": cond, "first_steps_citizen": [], "first_steps_lawyer": []}
                    current_list = None
                elif stripped.startswith("priority:"):
                    if current_rule:
                        current_rule["priority"] = int(stripped.split("priority:")[1].strip())
                elif stripped.startswith("first_steps_citizen:"):
                    current_list = "citizen"
                elif stripped.startswith("first_steps_lawyer:"):
                    current_list = "lawyer"
                elif stripped.startswith("-") and current_rule and current_list:
                    item = stripped[1:].strip().strip('"').strip("'")
                    if current_list == "citizen":
                        current_rule["first_steps_citizen"].append(item)
                    elif current_list == "lawyer":
                        current_rule["first_steps_lawyer"].append(item)
                        
            if current_rule:
                rules.append(current_rule)
                
        return {"rules": rules}

# Global cache for rule-base
RULES_CACHE = None
RULES_LOCK = threading.Lock()

def get_first_steps(section_metadata: Dict, user_facts: str, is_lawyer_mode: bool, force_baseline: bool = False) -> List[str]:
    """
    Hybrid Approach:
    1. Loads vetted, baseline legal actions using the static YAML rule engine.
    2. If Groq API is available, calls LLM to merge baseline actions with case-specific
       contextual recommendations.
    3. BULLETPROOF FALLBACK: If API keys are missing, network fails, or response is invalid,
       silently falls back to the vetted rule-based baseline actions (never breaks the system).
    4. force_baseline=True: Skips the LLM entirely and returns YAML baseline immediately.
       Used when all LLM providers are already known to be rate-limited/failed.
    """
    global RULES_CACHE
    if RULES_CACHE is None:
        with RULES_LOCK:
            if RULES_CACHE is None:
                if os.path.exists(YAML_FILE_PATH):
                    RULES_CACHE = load_rules(YAML_FILE_PATH)
                else:
                    RULES_CACHE = {"rules": []}

    # 1. Retrieve baseline steps from YAML rules
    baseline_steps = ["Consult a certified advocate immediately"]
    rules = RULES_CACHE.get("rules", [])
    for rule in rules:
        condition = rule.get("condition", "")
        if evaluate_condition(condition, section_metadata):
            if is_lawyer_mode:
                baseline_steps = rule.get("first_steps_lawyer", [])
            else:
                baseline_steps = rule.get("first_steps_citizen", [])
            break

    # C2 FIX: If caller signals LLM is already down, skip immediately to baseline
    if force_baseline:
        return baseline_steps

    # If no LLM keys at all, return baseline rules immediately
    gemini_key = os.getenv("GEMINI_API_KEY")
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    if not GROQ_API_KEY and not gemini_key and not openrouter_key:
        return baseline_steps

    # 2. Call LLM using the main multi-provider rotation cascade (Gemini -> Groq -> OpenRouter)
    # This replaces the old Groq-only httpx.post call so we get automatic fallback
    from engine_v2 import call_llm

    messages = [
        {"role": "system", "content": (
            "You are an expert Indian criminal law assistant. "
            "You are given a list of baseline legal action steps and a specific user incident scenario.\n\n"
            "Your task is to merge the baseline steps with 1-2 case-specific, highly contextual recommendations "
            "based on the incident facts (e.g. preserving specific digital screenshots, finding witnesses, securing CCTV, etc.).\n\n"
            "RULES:\n"
            "1. You MUST keep the baseline steps. Do not modify their core meaning.\n"
            "2. Add 1-2 contextual recommendations specifically tailored to the facts.\n"
            "3. Output MUST be a valid JSON object matching this schema:\n"
            "{\n"
            "  \"steps\": [\"step 1\", \"step 2\", ...]\n"
            "}\n"
            "Do not include any conversational filler, markdown formatting blocks, or numbering."
        )},
        {"role": "user", "content": (
            "Baseline Steps:\n"
            + "\n".join(f"- {step}" for step in baseline_steps)
            + f"\n\nUser Incident Facts:\n'{user_facts}'"
        )}
    ]

    try:
        result_json = call_llm(messages, json_mode=True)
        from engine import _extract_json
        data = _extract_json(result_json)
        steps = data.get("steps", [])
        if isinstance(steps, list) and len(steps) > 0:
            # Clean formatting to remove leading numbers/dashes/markdown
            cleaned_steps = []
            for s in steps:
                s_clean = re.sub(r'^\s*[\d\.\-\*•]+\s*', '', s).strip()
                if s_clean:
                    cleaned_steps.append(s_clean)
            return cleaned_steps
    except Exception as e:
        # Silently fall back to baseline rules on any error so the site NEVER breaks
        print(f"Warning: Action Engine LLM failed ({e}). Falling back to rule-based baseline.")

    return baseline_steps

if __name__ == "__main__":
    sample_facts = "Someone hacked my social media profile and is threatening to leak private chat screenshots unless I pay them money."
    metadata = {"cognizable": True, "bailable": False}
    
    print("Testing Hybrid Action Engine (Citizen Mode):")
    citizen_steps = get_first_steps(metadata, sample_facts, is_lawyer_mode=False)
    for idx, step in enumerate(citizen_steps, 1):
        print(f"{idx}. {step}")
        
    print("\nTesting Hybrid Action Engine (Lawyer Mode):")
    lawyer_steps = get_first_steps(metadata, sample_facts, is_lawyer_mode=True)
    for idx, step in enumerate(lawyer_steps, 1):
        print(f"{idx}. {step}")
