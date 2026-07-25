import os
import hashlib
import secrets
import uuid
from datetime import datetime, timedelta
from typing import Tuple, Optional, Any
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Float, Text, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from dotenv import load_dotenv

# Load environment variables
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(ENV_PATH)

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./lawrag.db")
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,      # Test connection before use — avoids stale socket errors
        pool_recycle=60,         # Recycle connections every 60s (Supabase pooler cuts idle at ~120s)
        pool_size=3,
        max_overflow=2,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base: Any = declarative_base()

# ==========================================
# Password Hashing Helpers
# ==========================================
def hash_password(password: str) -> str:
    """Hashes a password using PBKDF2-SHA256 with a random salt."""
    salt = secrets.token_hex(16)
    pwd_bytes = password.encode('utf-8')
    salt_bytes = salt.encode('utf-8')
    key = hashlib.pbkdf2_hmac('sha256', pwd_bytes, salt_bytes, 100000)
    return f"{salt}:{key.hex()}"

def verify_password(password: str, hashed: str) -> bool:
    """Verifies a password against its PBKDF2-SHA256 hash."""
    if not hashed or ":" not in hashed:
        return False
    try:
        salt, key_hex = hashed.split(":", 1)
        pwd_bytes = password.encode('utf-8')
        salt_bytes = salt.encode('utf-8')
        key = hashlib.pbkdf2_hmac('sha256', pwd_bytes, salt_bytes, 100000)
        return secrets.compare_digest(key.hex(), key_hex)
    except Exception:
        return False

# ==========================================
# SQLAlchemy Models for Billing & Auditing
# ==========================================
class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id = Column(String, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    credits = Column(Integer, default=10, nullable=False)
    password_hash = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class SessionToken(Base):
    __tablename__ = "session_tokens"

    token = Column(String, primary_key=True, index=True)
    user_id = Column(String, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)

class CreditTransaction(Base):
    __tablename__ = "credit_transactions"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String, index=True, nullable=False)
    amount = Column(Float, nullable=False)
    type = Column(String, nullable=False)  # 'purchase', 'usage', 'bonus', 'unlimited'
    created_at = Column(DateTime, default=datetime.utcnow)

class AnonymousIpRequest(Base):
    __tablename__ = "anonymous_ip_requests"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    ip_address = Column(String, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String, index=True, nullable=True)
    incident = Column(Text, nullable=False)
    response_json = Column("response", Text, nullable=False)
    is_lawyer_mode = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


# Ensure tables exist
Base.metadata.create_all(bind=engine)

# Gracefully apply schema migration if password_hash column doesn't exist
try:
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE user_profiles ADD COLUMN password_hash VARCHAR;"))
except Exception:
    pass


# ==========================================
# Core Billing Logic Functions
# ==========================================

def get_credit_balance(user_id: str, db = None) -> int:
    """
    Computes a user's credit balance by reading directly from the user's profile.
    Returns 999999 if the user has an active unlimited monthly pass (purchased within 30 days).
    """
    local_db = db or SessionLocal()
    try:
        # 1. Check for active unlimited monthly pass
        thirty_days_ago = datetime.utcnow() - timedelta(days=30)
        unlimited_pass = local_db.query(CreditTransaction).filter(
            CreditTransaction.user_id == user_id,
            CreditTransaction.type == "unlimited",
            CreditTransaction.created_at >= thirty_days_ago
        ).first()

        if unlimited_pass:
            return 999999  # Code for unlimited access

        # 2. Get credits from profile
        profile = local_db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        return int(profile.credits) if profile is not None else 0
    finally:
        if db is None:
            local_db.close()

def add_credits(user_id: str, amount: int, type: str) -> bool:
    """Appends a new credit transaction entry to the ledger and updates user profile credits."""
    db = SessionLocal()
    try:
        profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        if profile:
            profile.credits += amount  # type: ignore[assignment]
            
        tx = CreditTransaction(
            user_id=user_id,
            amount=float(amount),
            type=type
        )
        db.add(tx)
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        print(f"Error adding transaction log entry: {e}")
        return False
    finally:
        db.close()

def get_anonymous_remaining(ip: str, db = None) -> int:
    """Counts IP requests in the last 24 hours. Returns remaining out of 5 free daily queries."""
    local_db = db or SessionLocal()
    try:
        one_day_ago = datetime.utcnow() - timedelta(hours=24)
        count = local_db.query(AnonymousIpRequest).filter(
            AnonymousIpRequest.ip_address == ip,
            AnonymousIpRequest.created_at >= one_day_ago
        ).count()
        return max(0, 5 - count)
    finally:
        if db is None:
            local_db.close()

def check_and_deduct(user_id: Optional[str], ip_address: Optional[str] = None, db = None) -> Tuple[bool, int]:
    """
    Validates and deducts credits for an incoming request.
    If user_id is provided, deducts 1 credit from user profile credits (unless unlimited).
    If anonymous (user_id is None/empty), checks and deducts daily free IP allowance.
    Returns: (allowed: bool, remaining_balance: int)
    """
    local_db = db or SessionLocal()
    try:
        # Case A: Authenticated user
        if user_id and user_id.strip():
            user_id = user_id.strip()
            profile = local_db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
            if not profile:
                return False, 0
            
            # Check balance (passing existing db session to avoid second connection)
            balance = get_credit_balance(user_id, db=local_db)
            if balance == 999999:
                return True, 999999  # Unlimited pass, query allowed for free
                
            if profile.credits >= 1:
                profile.credits -= 1
                # Add debit log transaction to the database
                tx = CreditTransaction(
                    user_id=user_id,
                    amount=-1.0,
                    type="usage"
                )
                local_db.add(tx)
                local_db.commit()
                return True, int(profile.credits)
            else:
                # Insufficient balance
                return False, int(profile.credits)

        # Case B: Anonymous user
        else:
            # Passing existing db session to avoid second connection
            remaining = get_anonymous_remaining(ip_address or "127.0.0.1", db=local_db)
            if remaining > 0:
                # Insert IP request log entry
                req = AnonymousIpRequest(ip_address=ip_address or "127.0.0.1")
                local_db.add(req)
                local_db.commit()
                return True, remaining - 1
            else:
                return False, 0
    except Exception as e:
        local_db.rollback()
        print(f"Error processing transaction: {e}")
        return False, 0
    finally:
        if db is None:
            local_db.close()


def register_user(user_id: str, email: str, password: Optional[str] = None) -> Tuple[bool, str]:
    """
    Registers a new user profile with their Gmail ID/Email and a password.
    If the user is new, automatically awards them 10 signup credits.
    """
    db = SessionLocal()
    try:
        user_id = user_id.strip()
        email = email.strip().lower()
        
        # Check if user already exists
        existing = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        if existing:
            return True, "User already registered"
            
        existing_email = db.query(UserProfile).filter(UserProfile.email == email).first()
        if existing_email:
            return False, "Email already registered under a different ID"
            
        hashed_pw = hash_password(password) if password else None
        
        # Register new profile
        new_profile = UserProfile(user_id=user_id, email=email, credits=0, password_hash=hashed_pw)
        db.add(new_profile)
        db.commit()
        
        # Award 10 signup credits
        add_credits(user_id, 10, "bonus")
        return True, "Successfully registered and awarded 10 signup credits"
    except Exception as e:
        db.rollback()
        return False, f"Failed to register user: {str(e)}"
    finally:
        db.close()

def authenticate_user(email: str, password: str) -> Tuple[bool, str, str]:
    """
    Verifies user credentials. If valid, generates and saves a 7-day UUID session token.
    Returns: (success, token_or_error_msg, user_id)
    """
    db = SessionLocal()
    try:
        email = email.strip().lower()
        profile = db.query(UserProfile).filter(UserProfile.email == email).first()
        if not profile:
            return False, "User not found", ""
            
        # Verify password
        if not verify_password(password, str(profile.password_hash or "")):
            return False, "Invalid password", ""
            
        # Generate session token
        token = str(uuid.uuid4())
        expiry = datetime.utcnow() + timedelta(days=7)
        session = SessionToken(token=token, user_id=profile.user_id, expires_at=expiry)
        
        db.add(session)
        db.commit()
        return True, token, str(profile.user_id)
    except Exception as e:
        db.rollback()
        return False, f"Auth error: {str(e)}", ""
    finally:
        db.close()

def validate_session(token: str) -> Optional[str]:
    """
    Validates a session token. Returns user_id if valid, otherwise None.
    """
    if not token or not token.strip():
        return None
    db = SessionLocal()
    try:
        token = token.strip()
        session = db.query(SessionToken).filter(SessionToken.token == token).first()
        if session and session.expires_at > datetime.utcnow():
            return str(session.user_id)
        return None
    except Exception:
        return None
    finally:
        db.close()

def get_user_credits_by_email(email: str) -> Tuple[int, str]:
    """
    Retrieves the remaining credit balance using a Gmail ID/Email.
    """
    db = SessionLocal()
    try:
        email = email.strip().lower()
        profile = db.query(UserProfile).filter(UserProfile.email == email).first()
        if not profile:
            return 0, "User profile not found for this email"
        balance = get_credit_balance(str(profile.user_id))
        return balance, str(profile.user_id)
    except Exception as e:
        return 0, f"Error: {str(e)}"
    finally:
        db.close()


def save_conversation(user_id: str, incident: str, response_json: str, is_lawyer_mode: bool) -> bool:
    """Saves a new conversation entry to the user's history."""
    db = SessionLocal()
    try:
        conv = Conversation(
            user_id=user_id,
            incident=incident,
            response_json=response_json,
            is_lawyer_mode=is_lawyer_mode
        )
        db.add(conv)
        db.commit()
        return True
    except Exception as e:
        db.rollback()
        print(f"Error saving conversation: {e}")
        return False
    finally:
        db.close()

def get_conversations(user_id: str) -> list:
    """Retrieves all past conversations for a user, sorted by date descending."""
    db = SessionLocal()
    try:
        results = db.query(Conversation).filter(
            Conversation.user_id == user_id
        ).order_by(Conversation.created_at.desc()).all()
        
        return [
            {
                "id": c.id,
                "user_id": c.user_id,
                "incident": c.incident,
                "response_json": c.response_json,
                "is_lawyer_mode": c.is_lawyer_mode,
                "created_at": c.created_at.isoformat()
            }
            for c in results
        ]
    except Exception as e:
        print(f"Error fetching history: {e}")
        return []
    finally:
        db.close()

# ==========================================

# Local testing verification
# ==========================================
if __name__ == "__main__":
    test_user = "user_test_999"
    test_ip = "192.168.1.50"
    
    # 1. Clean old test logs
    db = SessionLocal()
    db.query(CreditTransaction).filter(CreditTransaction.user_id == test_user).delete()
    db.query(AnonymousIpRequest).filter(AnonymousIpRequest.ip_address == test_ip).delete()
    db.commit()
    db.close()
    
    print("--- Billing System Verification Test ---")
    
    # 2. Test Anonymous IP Logic (5 daily limit)
    print("\nTesting Anonymous IP rate limit (5 queries/day):")
    for i in range(1, 7):
        allowed, remaining = check_and_deduct(user_id=None, ip_address=test_ip)
        print(f"Request {i}: Allowed: {allowed}, Remaining daily: {remaining}")
        
    # 3. Test New Signup (Add 10 bonus credits)
    print("\nTesting User Signup bonus:")
    print("Initial balance:", get_credit_balance(test_user))
    add_credits(test_user, 10, "bonus")
    print("Balance after signup bonus:", get_credit_balance(test_user))
    
    # 4. Test Authenticated usages
    print("\nTesting authenticated usages (-1 credit per call):")
    for i in range(1, 4):
        allowed, remaining = check_and_deduct(test_user, ip_address=test_ip)
        print(f"Usage {i}: Allowed: {allowed}, Remaining credits: {remaining}")
        
    # 5. Test Unlimited Monthly Pass
    print("\nTesting purchase and usage of Unlimited Monthly Pass:")
    add_credits(test_user, 0, "unlimited")  # Buy unlimited pass
    print("Balance with unlimited pass:", get_credit_balance(test_user))
    allowed, remaining = check_and_deduct(test_user, ip_address=test_ip)
    print(f"Usage with unlimited pass: Allowed: {allowed}, Remaining: {remaining}")
