import os

# ── macOS semaphore / torch multiprocessing fix ──────────────────────────────
# Must be set BEFORE importing torch, transformers, or any HF library.
# Prevents the "leaked semaphore" crash that kills the server on macOS.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
# ─────────────────────────────────────────────────────────────────────────────
import time
from datetime import datetime
from typing import List, Optional, Dict
from fastapi import FastAPI, HTTPException, Request, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

# Import our RAG modules
from engine_v2 import analyze
from cases_v2 import retrieve_similar_judgments
from dotenv import load_dotenv

# Load environment variables
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(ENV_PATH)

# Database configuration
from credit_manager_v2 import (
    SessionLocal, check_and_deduct, register_user, 
    get_user_credits_by_email, get_conversations, 
    authenticate_user, validate_session, get_anonymous_remaining
)

# ==========================================
# Pydantic Schemas
# ==========================================
class QueryRequest(BaseModel):
    incident: Optional[str] = Field(default=None, description="Natural language incident facts")
    query: Optional[str] = Field(default=None, description="Alternative key for frontend compatibility")
    is_lawyer_mode: bool = Field(default=False, description="Citizen or Lawyer mode switch")
    user_id: Optional[str] = Field(default=None, description="Optional user ID for authenticated requests")
    ip_address: Optional[str] = Field(default="127.0.0.1", description="Client IP address")

class JudgmentRequest(BaseModel):
    incident: str = Field(..., description="Incident description")
    sections: List[str] = Field(..., description="List of applicable sections (e.g. ['BNS-103'])")

class FeedbackRequest(BaseModel):
    user_id: Optional[str] = Field(default=None, description="Optional user ID")
    session_id: str = Field(..., description="Session identifier")
    answer: bool = Field(..., description="Yes/No response value")

class RegisterRequest(BaseModel):
    user_id: str
    email: str
    password: Optional[str] = None

class LoginRequest(BaseModel):
    email: str
    password: str

class CreditsRequest(BaseModel):
    email: str

class HistoryRequest(BaseModel):
    user_id: str

# ==========================================
# Custom Rate Limiter (In-Memory sliding window)
# ==========================================
RATE_LIMIT_CACHE: Dict[str, List[float]] = {}

def is_rate_limited(identifier: str, limit: int) -> bool:
    """Sliding window rate limiter checking request timestamps in the last 60 seconds."""
    now = time.time()
    if identifier not in RATE_LIMIT_CACHE:
        RATE_LIMIT_CACHE[identifier] = []
    RATE_LIMIT_CACHE[identifier] = [t for t in RATE_LIMIT_CACHE[identifier] if now - t < 60]
    if len(RATE_LIMIT_CACHE[identifier]) >= limit:
        return True
    RATE_LIMIT_CACHE[identifier].append(now)
    return False

# ==========================================
# FastAPI Initialization
# ==========================================
app = FastAPI(
    title="LawRAG API",
    description="Scale-ready legal advisor backend utilizing hybrid vector RAG and element verification.",
    version="1.0.0"
)

# CORS Middleware config
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Startup event to load model indices
@app.on_event("startup")
def startup_event():
    from engine_v2 import load_indices
    print("🚀 Running startup event: Loading indices...")
    load_indices()

# Database Session Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Request Logging Middleware
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time
    print(f"Request: {request.method} {request.url.path} - Status: {response.status_code} - Duration: {duration:.4f}s")
    return response

# ==========================================
# Endpoints
# ==========================================

@app.post("/api/query")
async def query_legal_pipeline(payload: QueryRequest, request: Request, db: Session = Depends(get_db)):
    # Extract client IP if not provided
    client_ip = payload.ip_address or (request.client.host if request.client else "127.0.0.1")

    # Secure Session Validation: Extract and validate user ID from Bearer token
    user_id = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        user_id = validate_session(token)
        if not user_id:
            print("WARNING: Invalid or expired session token. Falling back to anonymous mode.")
            user_id = None

    # Rate Limiting Logic
    if user_id:
        identifier = f"auth_{user_id}"
        limit = 50  # 50 req/min for authenticated
    else:
        identifier = f"anon_{client_ip}"
        limit = 5   # 5 req/min for anonymous
        
    if is_rate_limited(identifier, limit):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please wait before submitting another request."
        )

    incident = payload.query or payload.incident
    if not incident:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incident or query field is required."
        )

    try:
        # Check and deduct credits first
        allowed, remaining_credits = check_and_deduct(user_id, client_ip, db=db)
        if not allowed:
            return {
                "status": "INSUFFICIENT_CREDITS",
                "applicable_laws": [],
                "reason": "Credit limit reached. Please sign up or purchase more credits.",
                "remaining_credits": remaining_credits,
                "disclaimer": "This is for educational research only. Consult a certified advocate."
            }

        # Run core RAG pipeline via engine.py
        result = analyze(incident, is_lawyer_mode=payload.is_lawyer_mode, user_id=user_id)
        result["remaining_credits"] = remaining_credits
        return result

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred in the query pipeline: {str(e)}"
        )

@app.post("/api/feedback")
async def save_feedback(payload: FeedbackRequest):
    # Inserts response to connect to lawyers into database
    from supabase import create_client
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_KEY", "")
    if not url or not key:
        raise HTTPException(status_code=500, detail="Supabase credentials not configured")
    supabase = create_client(url, key)
    try:
        supabase.table("lawyer_connect_feedback").insert({
            "user_id": payload.user_id,
            "session_id": payload.session_id,
            "answer": payload.answer
        }).execute()
        return {"status": "SUCCESS", "message": "Feedback submitted successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to submit feedback: {str(e)}")

@app.post("/api/judgments")
async def retrieve_judgments(payload: JudgmentRequest):
    try:
        judgments = retrieve_similar_judgments(payload.incident, payload.sections, k=3)
        return judgments
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Judgment retrieval failed: {str(e)}"
        )

# ==========================================
# Health Check Endpoint
# ==========================================

@app.get("/api/health")
async def health_check():
    qdrant_healthy = True
    try:
        from engine_v2 import qdrant, COLLECTION_NAME
        qdrant.get_collection(COLLECTION_NAME)
    except Exception:
        qdrant_healthy = False

    return {
        "status": "HEALTHY" if qdrant_healthy else "DEGRADED",
        "service": "LawRAG API v2",
        "qdrant_connected": qdrant_healthy,
        "timestamp": datetime.utcnow().isoformat()
    }

# ==========================================
# Authentication & User Endpoints
# ==========================================

@app.post("/api/register")
async def register_user_endpoint(payload: RegisterRequest):
    success, message = register_user(payload.user_id, payload.email, payload.password or "")
    if not success:
        return {"success": False, "error": message}
    return {"success": True, "message": message}

@app.post("/api/login")
async def login_endpoint(payload: LoginRequest):
    success, token_or_err, user_id = authenticate_user(payload.email, payload.password)
    if not success:
        return {"success": False, "error": token_or_err}
    # Retrieve credits
    credits_val, _ = get_user_credits_by_email(payload.email)
    return {
        "success": True,
        "token": token_or_err,
        "user_id": user_id,
        "email": payload.email,
        "credits": credits_val
    }

@app.post("/api/user/credits")
async def user_credits_endpoint(payload: CreditsRequest, request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session token is required to view credits."
        )
    token = auth_header.split(" ", 1)[1]
    token_user_id = validate_session(token)
    if not token_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session token."
        )
        
    credits_val, resolved_user_id = get_user_credits_by_email(payload.email)
    if resolved_user_id != token_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Token does not match requested email."
        )
        
    return {
        "success": True,
        "email": payload.email,
        "credits": credits_val,
        "user_id": resolved_user_id
    }

@app.get("/api/anonymous-credits")
@app.post("/api/anonymous-credits")
async def anonymous_credits_endpoint(request: Request):
    client_ip = request.client.host if request.client else "127.0.0.1"
    remaining = get_anonymous_remaining(client_ip)
    return {"success": True, "credits": remaining, "is_anonymous": True}

@app.post("/api/history")
async def user_history_endpoint(payload: HistoryRequest, request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session token is required to view history."
        )
    token = auth_header.split(" ", 1)[1]
    token_user_id = validate_session(token)
    if not token_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session. Please log in again."
        )
    if payload.user_id != token_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. You cannot view history of other users."
        )
    convs = get_conversations(payload.user_id)
    return {"success": True, "conversations": convs}

if __name__ == "__main__":
    import multiprocessing
    import uvicorn
    # macOS fix: use 'spawn' to avoid fork-safety issues with PyTorch/transformers
    multiprocessing.set_start_method("spawn", force=True)
    # HF Spaces expects port 7860
    uvicorn.run("main_v2:app", host="0.0.0.0", port=7861, workers=1, loop="asyncio")
