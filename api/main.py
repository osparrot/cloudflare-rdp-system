import os
import subprocess
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from supabase import create_client, Client
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.status import HTTP_200_OK, HTTP_201_CREATED, HTTP_204_NO_CONTENT
from datetime import datetime, timezone # Added timezone import for worker
from typing import Optional, List, Dict, Any # Added typing imports
import os # CRITICAL FIX: Ensure os is imported for environment variable access
from fastapi import Form # Added Form import for login/create forms

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

# NOTE: The frontend logic (login, session tracking) has been moved to the static JavaScript files.
# The API now only serves JSON data and requires the X-API-Key header for all user-facing endpoints.

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

# --- Worker Authorization ---
WORKER_SECRET = os.environ.get("WORKER_SECRET")

def worker_auth(worker_secret: str = Depends(APIKeyHeader(name="X-Worker-Secret", auto_error=False))):
    if not WORKER_SECRET:
        # If secret is not set, allow access (e.g., for testing)
        return True
    if worker_secret != WORKER_SECRET:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Worker Secret")
    return True

# --- API Endpoints ---

@app.get("/api/v1/user", response_model=User)
async def get_user_details(current_user: User = Depends(get_current_user)):
    """Returns the details of the authenticated user."""
    return current_user

@app.get("/api/v1/sessions", response_model=List[SessionResponse])
async def get_user_sessions(current_user: User = Depends(get_current_user), supabase: Client = Depends(get_supabase_client)):
    """Returns all active and recent sessions for the authenticated user."""
    try:
        response = supabase.table('sessions').select('*').eq('user_id', current_user.id).order('created_at', desc=True).execute()
        
        # Convert string timestamps to datetime objects for Pydantic validation
        for session in response.data:
            session['expires_at'] = datetime.fromisoformat(session['expires_at'])
            session['created_at'] = datetime.fromisoformat(session['created_at'])
            
        return response.data
    except Exception as e:
        print(f"Supabase error during session fetch: {e}")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database service unavailable.")

@app.post("/api/v1/sessions", response_model=SessionResponse, status_code=HTTP_201_CREATED)
async def create_session(session_data: SessionCreate, current_user: User = Depends(get_current_user), supabase: Client = Depends(get_supabase_client)):
    """Creates a new RDP session for the authenticated user."""
    # 1. Check for existing active session
    response = supabase.table('sessions').select('id').eq('user_id', current_user.id).eq('status', 'active').execute()
    if response.data:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already has an active RDP session. Please terminate the existing one first.")

    # 2. Calculate expiry time
    expiry_time = datetime.now(timezone.utc) + timedelta(hours=session_data.duration_hours)
    
    # 3. Run the shell script to create the tunnel
    try:
        script_output = run_shell_script(CREATE_SCRIPT, [session_data.rdp_username, str(session_data.duration_hours)])
    except HTTPException as e:
        # Re-raise script errors
        raise e
    except Exception as e:
        # Catch any unexpected errors during script execution
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Unexpected error during session creation: {e}")

    # 4. Parse the machine-readable output
    output_dict = {}
    start_tag = "--- API_OUTPUT_START ---"
    end_tag = "--- API_OUTPUT_END ---"
    
    if start_tag in script_output and end_tag in script_output:
        content = script_output.split(start_tag)[1].split(end_tag)[0].strip()
        for line in content.split('\n'):
            if '=' in line:
                key, value = line.split('=', 1)
                output_dict[key.strip()] = value.strip()
    else:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="RDP script returned unparsable output.")

    # 5. Insert session into Supabase
    session_data_db = {
        "user_id": current_user.id,
        "session_sub": output_dict.get("SESSION_SUB"),
        "fqdn": output_dict.get("FQDN"),
        "rdp_username": output_dict.get("RDP_USERNAME"),
        "rdp_password": output_dict.get("RDP_PASSWORD"),
        "status": "active",
        "expires_at": expiry_time.isoformat(),
    }
    
    try:
        response = supabase.table('sessions').insert(session_data_db).execute()
        
        # 6. Return the session details
        session_response = SessionResponse(
            session_sub=session_data_db["session_sub"],
            fqdn=session_data_db["fqdn"],
            rdp_username=session_data_db["rdp_username"],
            rdp_password=session_data_db["rdp_password"],
            expires_at=expiry_time,
            status="active"
        )
        return session_response
    except Exception as e:
        print(f"Supabase error during session insert: {e}")
        # CRITICAL: If DB insert fails, we must clean up the created tunnel
        run_shell_script(CLEANUP_SCRIPT, [output_dict.get("SESSION_SUB")])
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database error. Session creation failed and tunnel was cleaned up.")

@app.delete("/api/v1/sessions/{session_sub}", status_code=HTTP_204_NO_CONTENT)
async def delete_session(session_sub: str, current_user: User = Depends(get_current_user), supabase: Client = Depends(get_supabase_client)):
    """Terminates and cleans up an active RDP session."""
    # 1. Find the session and verify ownership
    response = supabase.table('sessions').select('*').eq('session_sub', session_sub).eq('user_id', current_user.id).eq('status', 'active').execute()
    session_data = response.data
    
    if not session_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Active session not found or does not belong to user.")

    # 2. Run the shell script to clean up the tunnel
    try:
        run_shell_script(CLEANUP_SCRIPT, [session_sub])
    except HTTPException as e:
        # Log the error but proceed to update DB status
        print(f"Warning: Cleanup script failed for {session_sub}. Error: {e.detail}")
        # We still update the DB to prevent the user from trying again
        pass

    # 3. Update session status in Supabase
    try:
        supabase.table('sessions').update({"status": "terminated"}).eq('session_sub', session_sub).execute()
    except Exception as e:
        print(f"Supabase error during session update: {e}")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database service unavailable. Session terminated but status update failed.")

    return JSONResponse(status_code=HTTP_204_NO_CONTENT)

@app.get("/api/v1/admin/sessions", response_model=List[SessionResponse])
async def admin_get_all_sessions(supabase: Client = Depends(get_supabase_client)):
    """ADMIN: Returns all sessions for auditing purposes."""
    # NOTE: In a real app, this should have a separate, stronger admin authentication layer.
    try:
        response = supabase.table('sessions').select('*').order('created_at', desc=True).execute()
        
        # Convert string timestamps to datetime objects for Pydantic validation
        for session in response.data:
            session['expires_at'] = datetime.fromisoformat(session['expires_at'])
            session['created_at'] = datetime.fromisoformat(session['created_at'])
            
        return response.data
    except Exception as e:
        print(f"Supabase error during admin session fetch: {e}")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database service unavailable.")

@app.post("/api/v1/admin/revoke/{session_sub}", status_code=HTTP_204_NO_CONTENT)
async def admin_revoke_session(session_sub: str, supabase: Client = Depends(get_supabase_client)):
    """ADMIN: Revokes and cleans up any session by its sub."""
    # NOTE: In a real app, this should have a separate, stronger admin authentication layer.
    
    # 1. Run the shell script to clean up the tunnel
    try:
        run_shell_script(CLEANUP_SCRIPT, [session_sub])
    except HTTPException as e:
        print(f"Warning: Cleanup script failed for {session_sub}. Error: {e.detail}")
        pass

    # 2. Update session status in Supabase
    try:
        supabase.table('sessions').update({"status": "revoked"}).eq('session_sub', session_sub).execute()
    except Exception as e:
        print(f"Supabase error during session update: {e}")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database service unavailable. Session terminated but status update failed.")

    return JSONResponse(status_code=HTTP_204_NO_CONTENT)

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

@app.post("/api/v1/worker/cleanup")
def worker_cleanup(worker_auth: bool = Depends(worker_auth), supabase: Client = Depends(get_supabase_client)):
    """
    Worker endpoint to check for expired sessions and trigger cleanup.
    This is an alternative to the session_monitor.py script, allowing the worker
    to be run as a simple HTTP request (e.g., from a cron job or external service).
    """
    
    now_utc = datetime.now(timezone.utc).isoformat()
    
    try:
        # 1. Query for active sessions where expires_at is in the past
        response = supabase.table('sessions').select('session_sub, id').eq('status', 'active').lt('expires_at', now_utc).execute()
        expired_sessions = response.data
        
        if not expired_sessions:
            return {"status": "ok", "message": "No expired sessions found."}

        cleanup_results = []
        
        for session in expired_sessions:
            session_sub = session['session_sub']
            session_id = session['id']
            
            # 2. Run cleanup script
            try:
                run_shell_script(CLEANUP_SCRIPT, [session_sub])
                cleanup_success = True
            except HTTPException:
                cleanup_success = False
            
            # 3. Update database status
            new_status = 'expired_cleaned' if cleanup_success else 'cleanup_failed'
            
            try:
                supabase.table('sessions').update({'status': new_status}).eq('id', session_id).execute()
                cleanup_results.append({"session_sub": session_sub, "status": new_status, "success": cleanup_success})
            except Exception as e:
                cleanup_results.append({"session_sub": session_sub, "status": "db_update_failed", "error": str(e)})
                
        return {"status": "ok", "message": f"Processed {len(expired_sessions)} expired sessions.", "results": cleanup_results}
    
    except Exception as e:
        print(f"FATAL ERROR during worker cleanup: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Worker cleanup failed: {e}")

@app.get("/admin/sessions", response_class=HTMLResponse)
async def admin_dashboard(request: Request, supabase: Client = Depends(get_supabase_client)):
    # NOTE: In a real system, this endpoint would be protected by a dedicated Admin API Key or role check.
    # For simplicity, we will assume the user accessing this endpoint is an Admin.
    
    try:
        # Fetch all sessions, ordered by creation date
        response = supabase.table('sessions').select('*').order('created_at', desc=True).limit(50).execute()
        all_sessions = response.data
        
        # Convert string timestamps to datetime objects for Jinja2
        for session in all_sessions:
            session['expires_at'] = datetime.fromisoformat(session['expires_at'])
            session['created_at'] = datetime.fromisoformat(session['created_at'])

        return templates.TemplateResponse("admin.html", {
            "request": request, 
            "all_sessions": all_sessions
        })
    except Exception as e:
        return templates.TemplateResponse("admin.html", {"request": request, "error": f"Database connection failed: {e}"})

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
