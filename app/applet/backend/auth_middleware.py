import os
import sqlite3
from fastapi import Request, HTTPException, Security, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import firebase_admin
from firebase_admin import credentials, auth as firebase_auth

if not firebase_admin._apps:
    firebase_admin.initialize_app()

security = HTTPBearer()
DB_PATH = os.getenv("DB_PATH", "users.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_current_user(credentials: HTTPAuthorizationCredentials = Security(security)):
    """Verifies Firebase JWT and retrieves user record from local SQLite."""
    token = credentials.credentials
    try:
        decoded_token = firebase_auth.verify_id_token(token)
        uid = decoded_token.get("uid")
        email = decoded_token.get("email")
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid authentication credentials")

    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE uid = ?", (uid,)).fetchone()
    conn.close()

    if not user:
        raise HTTPException(status_code=401, detail="User not found in system")

    user_dict = dict(user)
    if user_dict.get("status") == "REVOKED":
        raise HTTPException(status_code=403, detail="Access revoked")

    return user_dict

def require_admin(user: dict = Depends(get_current_user)):
    """Enforces ADMIN role."""
    if user.get("role") != "ADMIN":
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user

def require_editor_or_admin(user: dict = Depends(get_current_user)):
    """Enforces VIDEO_EDITOR or ADMIN role."""
    role = user.get("role")
    if role not in ["ADMIN", "VIDEO_EDITOR"]:
        raise HTTPException(status_code=403, detail="Upload/edit privileges required")
    return user

def require_searcher(user: dict = Depends(get_current_user)):
    """Enforces any active role (Searcher, Editor, Admin) for basic access."""
    # Since revoked users are blocked in get_current_user, we just return the active user
    return user
