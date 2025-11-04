import os
import subprocess
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

# --- Configuration ---
DB_PATH = "rdp_service.db"
SHELL_SCRIPT_DIR = os.path.join(os.path.dirname(__file__), "..")
CREATE_SCRIPT = os.path.join(SHELL_SCRIPT_DIR, "create-rdp-session.sh")
CLEANUP_SCRIPT = os.path.join(SHELL_SCRIPT_DIR, "cleanup-rdp-session.sh")

# Environment variables required for the shell scripts
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")
CF_ZONE_ID = os.environ.get("CF_ZONE_ID")
BASE_DOMAIN = os.environ.get("BASE_DOMAIN", "rdp.accesscontrole.com")

# --- Database Setup ---
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Users table for API Key authentication
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            api_key TEXT UNIQUE NOT NULL,
            is_active BOOLEAN NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    
    # Sessions table for RDP session tracking
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            session_sub TEXT UNIQUE NOT NULL,
            fqdn TEXT UNIQUE NOT NULL,
            rdp_username TEXT NOT NULL,
            rdp_password TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    """)
    
    # Insert a default user for testing if none exists
    cursor.execute("SELECT COUNT(*) FROM users")
    if cursor.fetchone()[0] == 0:
        # NOTE: In a real system, this key should be generated securely
        default_key = "TEST_API_KEY_12345"
        print(f"--- WARNING: Inserting default user with API Key: {default_key} ---")
        cursor.execute("INSERT INTO users (api_key, is_active, created_at) VALUES (?, ?, ?)",
                       (default_key, True, datetime.now().isoformat()))
    
    conn.commit()
    conn.close()

# Initialize the database on startup
init_db()

# --- FastAPI App and Security ---
app = FastAPI(title="Cloudflare RDP Session Manager API")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

class User(BaseModel):
    id: int
    api_key: str
    is_active: bool

class SessionCreate(BaseModel):
    duration_hours: int = 4
    rdp_username: str = "admin"

class SessionResponse(BaseModel):
    session_sub: str
    fqdn: str
    rdp_username: str
    rdp_password: str
    expires_at: datetime
    status: str

async def get_current_user(api_key: str = Depends(api_key_header)):
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API Key missing")
    
    conn = get_db_connection()
    user_data = conn.execute("SELECT * FROM users WHERE api_key = ?", (api_key,)).fetchone()
    conn.close()
    
    if not user_data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API Key")
    
    user = User(**user_data)
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User account is inactive")
        
    return user

# --- Helper Functions ---
def run_shell_script(script_path: str, args: list) -> str:
    """Executes a shell script and returns its output."""
    try:
        # Pass required environment variables to the subprocess
        env = os.environ.copy()
        env["CF_API_TOKEN"] = CF_API_TOKEN
        env["CF_ZONE_ID"] = CF_ZONE_ID
        env["BASE_DOMAIN"] = BASE_DOMAIN
        
        result = subprocess.run(
            ["sudo", script_path] + args,
            capture_output=True,
            text=True,
            check=True,
            env=env
        )
        return result.stdout
    except subprocess.CalledProcessError as e:
        print(f"Script failed: {script_path}")
        print(f"Stdout: {e.stdout}")
        print(f"Stderr: {e.stderr}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                            detail=f"RDP script failed: {e.stderr.strip()}")
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                            detail=f"RDP script not found at {script_path}")

# --- API Endpoints ---

@app.get("/api/v1/status")
def get_status():
    """Check API health."""
    return {"status": "ok", "message": "RDP Session Manager is running."}

@app.post("/api/v1/sessions", response_model=SessionResponse)
def create_session(session_data: SessionCreate, current_user: User = Depends(get_current_user)):
    """Create a new RDP session."""
    
    # 1. Input validation and limits
    if not (1 <= session_data.duration_hours <= 24):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, 
                            detail="Duration must be between 1 and 24 hours.")
    
    # 2. Check for existing active session (simple limit for commercial service)
    conn = get_db_connection()
    active_session = conn.execute("SELECT * FROM sessions WHERE user_id = ? AND status = 'active'", 
                                  (current_user.id,)).fetchone()
    if active_session:
        conn.close()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, 
                            detail=f"User already has an active session: {active_session['session_sub']}")

    # 3. Run the creation script
    # The script will output the session details in a structured format (e.g., the final block)
    # For simplicity, we will assume the script's final output block is parsable
    
    # The script is designed to be run with a specific username and TTL
    script_output = run_shell_script(CREATE_SCRIPT, [session_data.rdp_username, str(session_data.duration_hours)])
    
    # 4. Parse the script output to get session details
    # This parsing is now more robust, relying on a dedicated machine-readable block.
    try:
        api_output_block = script_output.split("--- API_OUTPUT_START ---")[1].split("--- API_OUTPUT_END ---")[0]
        output_data = {}
        for line in api_output_block.strip().split('\n'):
            key, value = line.split('=', 1)
            output_data[key.strip()] = value.strip()
        
        session_sub = output_data["SESSION_SUB"]
        fqdn = output_data["FQDN"]
        rdp_password = output_data["RDP_PASSWORD"]
        
    except Exception as e:
        print(f"Failed to parse script output: {e}")
        # Attempt to clean up the failed session before raising error
        try:
            run_shell_script(CLEANUP_SCRIPT, [session_sub])
        except:
            pass # Ignore cleanup failure
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                            detail="Failed to parse RDP session details from script output. Cleanup attempted.")

    # 5. Calculate expiry and save to database
    expires_at = datetime.now() + timedelta(hours=session_data.duration_hours)
    
    conn.execute("""
        INSERT INTO sessions (user_id, session_sub, fqdn, rdp_username, rdp_password, status, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (current_user.id, session_sub, fqdn, session_data.rdp_username, rdp_password, 'active', 
          datetime.now().isoformat(), expires_at.isoformat()))
    conn.commit()
    conn.close()
    
    return SessionResponse(
        session_sub=session_sub,
        fqdn=fqdn,
        rdp_username=session_data.rdp_username,
        rdp_password=rdp_password,
        expires_at=expires_at,
        status='active'
    )

@app.delete("/api/v1/sessions/{session_sub}")
def delete_session(session_sub: str, current_user: User = Depends(get_current_user)):
    """Clean up and terminate an RDP session."""
    
    conn = get_db_connection()
    session_data = conn.execute("SELECT * FROM sessions WHERE session_sub = ? AND user_id = ?", 
                                (session_sub, current_user.id)).fetchone()
    
    if not session_data:
        conn.close()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found or does not belong to user.")
    
    if session_data['status'] != 'active':
        conn.close()
        return {"message": f"Session {session_sub} is already {session_data['status']}."}

    # Run the cleanup script
    run_shell_script(CLEANUP_SCRIPT, [session_sub])
    
    # Update database status
    conn.execute("UPDATE sessions SET status = 'cleaned' WHERE session_sub = ?", (session_sub,))
    conn.commit()
    conn.close()
    
    return {"message": f"Session {session_sub} terminated and cleanup initiated."}

@app.get("/api/v1/sessions/{session_sub}", response_model=SessionResponse)
def get_session(session_sub: str, current_user: User = Depends(get_current_user)):
    """Get details of an active session."""
    conn = get_db_connection()
    session_data = conn.execute("SELECT * FROM sessions WHERE session_sub = ? AND user_id = ?", 
                                (session_sub, current_user.id)).fetchone()
    conn.close()
    
    if not session_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found or does not belong to user.")
        
    return SessionResponse(
        session_sub=session_data['session_sub'],
        fqdn=session_data['fqdn'],
        rdp_username=session_data['rdp_username'],
        rdp_password=session_data['rdp_password'],
        expires_at=datetime.fromisoformat(session_data['expires_at']),
        status=session_data['status']
    )
