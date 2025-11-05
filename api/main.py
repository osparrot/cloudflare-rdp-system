import os
import subprocess
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from supabase import create_client, Client

# --- Configuration ---
# Supabase environment variables
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# Shell script configuration
SHELL_SCRIPT_DIR = os.path.join(os.path.dirname(__file__), "..")
CREATE_SCRIPT = os.path.join(SHELL_SCRIPT_DIR, "create-rdp-session.sh")
CLEANUP_SCRIPT = os.path.join(SHELL_SCRIPT_DIR, "cleanup-rdp-session.sh")

# Cloudflare environment variables required for the shell scripts
CF_API_TOKEN = os.environ.get("CF_API_TOKEN")
CF_ZONE_ID = os.environ.get("CF_ZONE_ID")
BASE_DOMAIN = os.environ.get("BASE_DOMAIN", "rdp.accesscontrole.com")

# --- Supabase Setup ---
def get_supabase_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
                            detail="Supabase environment variables (SUPABASE_URL, SUPABASE_KEY) are not set. Please configure them.")
    return create_client(SUPABASE_URL, SUPABASE_KEY)

# --- FastAPI App and Security ---
app = FastAPI(title="Cloudflare RDP Session Manager API (Supabase)")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Pydantic Models for API and Database
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

async def get_current_user(api_key: str = Depends(api_key_header), supabase: Client = Depends(get_supabase_client)):
    if not api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API Key missing. Please provide X-API-Key header.")
    
    try:
        # Supabase query to find user by API key
        response = supabase.table('users').select('*').eq('api_key', api_key).execute()
        user_data = response.data
    except Exception as e:
        print(f"Supabase error during user lookup: {e}")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database service unavailable.")

    if not user_data:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API Key")
    
    # Assuming 'id' is the primary key and is an integer in Supabase
    user = User(id=user_data[0]['id'], api_key=user_data[0]['api_key'], is_active=user_data[0]['is_active'])
    
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
        
        # Use a more robust subprocess call
        result = subprocess.run(
            ["sudo", script_path] + args,
            capture_output=True,
            text=True,
            check=True,
            env=env,
            timeout=60 # Timeout after 60 seconds for script execution
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
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, 
                            detail=f"RDP script execution timed out after 60 seconds.")

# --- API Endpoints ---

@app.get("/api/v1/status")
def get_status(supabase: Client = Depends(get_supabase_client)):
    """Check API health and database connection."""
    try:
        # Simple query to check database connectivity
        supabase.table('users').select('id').limit(1).execute()
        db_status = "ok"
    except Exception:
        db_status = "error"
        
    return {"status": "ok", "message": "RDP Session Manager is running.", "database_status": db_status}

@app.post("/api/v1/sessions", response_model=SessionResponse)
def create_session(session_data: SessionCreate, 
                   current_user: User = Depends(get_current_user), 
                   supabase: Client = Depends(get_supabase_client)):
    """Create a new RDP session."""
    
    # 1. Input validation and limits
    # Business Logic: Duration must be between 1 and 24 hours (maximum reliable session length)
    # Business Logic: Duration must be between 1 and 24 hours (maximum reliable session length)
    if not (1 <= session_data.duration_hours <= 24):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, 
                            detail="Duration must be between 1 and 24 hours.")
    
    # 2. Check for existing active session (Business Logic: One active session per user)
    try:
        response = supabase.table('sessions').select('session_sub').eq('user_id', current_user.id).eq('status', 'active').execute()
        active_sessions = response.data
    except Exception as e:
        print(f"Supabase error during active session check: {e}")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database service unavailable.")

    if active_sessions:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, 
                            detail=f"User already has an active session: {active_sessions[0]['session_sub']}")

    # 3. Run the creation script
    script_output = run_shell_script(CREATE_SCRIPT, [session_data.rdp_username, str(session_data.duration_hours)])
    
    # 4. Parse the script output to get session details
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

    # 5. Calculate expiry and save to Supabase
    expires_at = datetime.now() + timedelta(hours=session_data.duration_hours)
    
    session_record = {
        "user_id": current_user.id,
        "session_sub": session_sub,
        "fqdn": fqdn,
        "rdp_username": session_data.rdp_username,
        "rdp_password": rdp_password,
        "status": 'active',
        "created_at": datetime.now().isoformat(),
        "expires_at": expires_at.isoformat()
    }
    
    try:
        supabase.table('sessions').insert(session_record).execute()
    except Exception as e:
        print(f"Supabase error during session insert: {e}")
        # Attempt to clean up the successfully created tunnel before raising error
        try:
            run_shell_script(CLEANUP_SCRIPT, [session_sub])
        except:
            pass # Ignore cleanup failure
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Failed to save session to database. Tunnel cleaned up.")
    
    return SessionResponse(
        session_sub=session_sub,
        fqdn=fqdn,
        rdp_username=session_data.rdp_username,
        rdp_password=rdp_password,
        expires_at=expires_at,
        status='active'
    )

@app.delete("/api/v1/sessions/{session_sub}")
def delete_session(session_sub: str, 
                   current_user: User = Depends(get_current_user), 
                   supabase: Client = Depends(get_supabase_client)):
    """Clean up and terminate an RDP session."""
    
    try:
        response = supabase.table('sessions').select('*').eq('session_sub', session_sub).eq('user_id', current_user.id).execute()
        session_data = response.data
    except Exception as e:
        print(f"Supabase error during session lookup: {e}")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database service unavailable.")

    if not session_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found or does not belong to user.")
    
    session = session_data[0]
    
    if session['status'] != 'active':
        return {"message": f"Session {session_sub} is already {session['status']}."}

    # Run the cleanup script
    run_shell_script(CLEANUP_SCRIPT, [session_sub])
    
    # Update Supabase status
    try:
        supabase.table('sessions').update({'status': 'cleaned'}).eq('session_sub', session_sub).execute()
    except Exception as e:
        print(f"Supabase error during status update: {e}")
        # Note: The tunnel is cleaned up, but the DB status update failed. This is a minor issue.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Tunnel cleaned up, but failed to update database status.")
    
    return {"message": f"Session {session_sub} terminated and cleanup initiated."}

@app.get("/api/v1/sessions/{session_sub}", response_model=SessionResponse)
def get_session(session_sub: str, 
                current_user: User = Depends(get_current_user), 
                supabase: Client = Depends(get_supabase_client)):
    """Get details of an active session."""
    try:
        response = supabase.table('sessions').select('*').eq('session_sub', session_sub).eq('user_id', current_user.id).execute()
        session_data = response.data
    except Exception as e:
        print(f"Supabase error during session lookup: {e}")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database service unavailable.")

    if not session_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found or does not belong to user.")
        
    session = session_data[0]
    
    return SessionResponse(
        session_sub=session['session_sub'],
        fqdn=session['fqdn'],
        rdp_username=session['rdp_username'],
        rdp_password=session['rdp_password'],
        expires_at=datetime.fromisoformat(session['expires_at']),
        status=session['status']
    )
