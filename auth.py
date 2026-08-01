import sqlite3
import hashlib
import secrets
import pandas as pd
import streamlit as st
from datetime import datetime, timezone
from db import StoragePaths, get_setting

def hash_password(password: str, salt: str) -> str:
    # Salted hash for password storage.
    return hashlib.sha256((salt + password).encode("utf-8")).hexdigest()



def verify_password(password: str, salt: str, password_hash: str) -> bool:
    # Check a plaintext password against the stored hash.
    return hash_password(password, salt) == password_hash



def get_user_by_username(username: str) -> dict | None:
    # Retrieve user credentials and profile by username.
    with sqlite3.connect(StoragePaths.current().db_path) as conn:
        row = conn.execute(
            "SELECT id, username, display_name, role, password_hash, password_salt, enabled FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "username": row[1],
        "display_name": row[2],
        "role": row[3],
        "password_hash": row[4],
        "password_salt": row[5],
        "enabled": bool(row[6]),
    }



def list_users() -> pd.DataFrame:
    # List user accounts for the admin view.
    with sqlite3.connect(StoragePaths.current().db_path) as conn:
        return pd.read_sql_query(
            "SELECT id, username, display_name, role, enabled, created_at, updated_at FROM users ORDER BY username",
            conn,
        )



def create_user(username: str, display_name: str, role: str, password: str, enabled: bool = True) -> None:
    # Create a new user with salted password hash.
    salt = secrets.token_hex(16)
    password_hash = hash_password(password, salt)
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(StoragePaths.current().db_path) as conn:
        conn.execute(
            """
            INSERT INTO users (username, display_name, role, password_hash, password_salt, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (username, display_name, role, password_hash, salt, 1 if enabled else 0, now, now),
        )



def update_user(user_id: int, display_name: str, role: str, enabled: bool) -> None:
    # Update profile metadata and enabled state.
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(StoragePaths.current().db_path) as conn:
        conn.execute(
            """
            UPDATE users SET display_name = ?, role = ?, enabled = ?, updated_at = ? WHERE id = ?
            """,
            (display_name, role, 1 if enabled else 0, now, user_id),
        )



def reset_password(user_id: int, new_password: str) -> None:
    # Reset a user password with a new salt.
    salt = secrets.token_hex(16)
    password_hash = hash_password(new_password, salt)
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(StoragePaths.current().db_path) as conn:
        conn.execute(
            """
            UPDATE users SET password_hash = ?, password_salt = ?, updated_at = ? WHERE id = ?
            """,
            (password_hash, salt, now, user_id),
        )



def delete_user(user_id: int) -> None:
    # Reassign records to the default admin user and delete the user.
    with sqlite3.connect(StoragePaths.current().db_path) as conn:
        admin_row = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
        if admin_row:
            admin_id = admin_row[0]
            conn.execute("UPDATE ecg_records SET uploader_id = ? WHERE uploader_id = ?", (admin_id, user_id))
        
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))




def log_audit(event_type: str, outcome: str, user: dict | None = None, details: str | None = None) -> None:
    # Insert a row into audit_logs for security and traceability.
    with sqlite3.connect(StoragePaths.current().db_path) as conn:
        conn.execute(
            """
            INSERT INTO audit_logs (timestamp, event_type, user_id, username, outcome, details)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                event_type,
                user.get("id") if user else None,
                user.get("username") if user else None,
                outcome,
                details,
            ),
        )



def list_audit_logs(limit: int = 200) -> pd.DataFrame:
    # Return the latest audit rows for the admin view.
    with sqlite3.connect(StoragePaths.current().db_path) as conn:
        return pd.read_sql_query(
            "SELECT timestamp, event_type, username, outcome, details FROM audit_logs ORDER BY id DESC LIMIT ?",
            conn,
            params=(limit,),
        )



def authenticate_user(username: str, password: str) -> dict | None:
    # Validate username/password against stored credentials.
    user = get_user_by_username(username)
    if not user or not user.get("enabled"):
        return None
    if verify_password(password, user["password_salt"], user["password_hash"]):
        return user
    return None



def is_user_management_enabled() -> bool:
    # Feature flag for access control and auditing.
    return get_setting("user_management_enabled", "false") == "true"



def get_session_timeout_minutes() -> int:
    # Defensive parsing of the session timeout setting.
    value = get_setting("session_timeout_minutes", "30")
    try:
        return int(value)
    except ValueError:
        return 30



def user_has_role(roles: list[str]) -> bool:
    # Helper for checking the current session's role.
    role = st.session_state.get("role")
    return role in roles



def require_roles(roles: list[str]):
    # Gate UI sections based on authentication and role.
    if not is_user_management_enabled():
        return
    if not st.session_state.get("authenticated"):
        st.warning("Please log in to continue.")
        st.stop()
    if not user_has_role(roles):
        st.error("You do not have permission to access this area.")
        st.stop()



def enforce_session_timeout():
    # Clear authentication when idle time exceeds configured threshold.
    if not is_user_management_enabled():
        return
    if not st.session_state.get("authenticated"):
        return
    last_activity = st.session_state.get("last_activity")
    if not last_activity:
        st.session_state["last_activity"] = datetime.now(timezone.utc).isoformat()
        return
    try:
        last_ts = datetime.fromisoformat(last_activity)
    except ValueError:
        last_ts = datetime.now(timezone.utc)
    minutes = get_session_timeout_minutes()
    if (datetime.now(timezone.utc) - last_ts).total_seconds() > minutes * 60:
        user = {
            "id": st.session_state.get("user_id"),
            "username": st.session_state.get("username"),
        }
        log_audit("session_timeout", "success", user)
        st.session_state.clear()
        st.warning("Session timed out. Please log in again.")
        st.stop()
    st.session_state["last_activity"] = datetime.now(timezone.utc).isoformat()



