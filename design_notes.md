# Commercial RDP Service Design Notes

## 1. Database Schema (SQLite)

We will use a simple SQLite database for session and user management.

### Table: `users`
| Field | Type | Description |
| :--- | :--- | :--- |
| `id` | INTEGER PRIMARY KEY | Unique user ID |
| `api_key` | TEXT UNIQUE NOT NULL | Secret key for API authentication |
| `is_active` | BOOLEAN NOT NULL | Whether the user is active (for billing/suspension) |
| `created_at` | TEXT NOT NULL | Timestamp of user creation |

### Table: `sessions`
| Field | Type | Description |
| :--- | :--- | :--- |
| `id` | INTEGER PRIMARY KEY | Unique session ID |
| `user_id` | INTEGER NOT NULL | Foreign key to `users.id` |
| `session_sub` | TEXT UNIQUE NOT NULL | The unique session identifier (e.g., `session-a1b2c3d4`) |
| `fqdn` | TEXT UNIQUE NOT NULL | The full RDP domain (e.g., `session-a1b2c3d4.yourdomain.com`) |
| `rdp_username` | TEXT NOT NULL | The RDP username (e.g., `admin`) |
| `rdp_password` | TEXT NOT NULL | The dynamically generated RDP password |
| `status` | TEXT NOT NULL | Current status (e.g., `active`, `pending`, `expired`, `cleaned`) |
| `created_at` | TEXT NOT NULL | Timestamp of session creation |
| `expires_at` | TEXT NOT NULL | Timestamp of session expiration |

## 2. API Endpoints (FastAPI)

The API will be the interface for users to manage their RDP sessions. All endpoints will require an `X-API-Key` header for authentication.

| Method | Endpoint | Description | Request Body | Response Body |
| :--- | :--- | :--- | :--- | :--- |
| `POST` | `/api/v1/sessions` | **Create a new RDP session.** | `{"duration_hours": 4, "rdp_username": "admin"}` | `{"session_sub": "...", "fqdn": "...", "rdp_username": "...", "rdp_password": "...", "expires_at": "..."}` |
| `GET` | `/api/v1/sessions/{session_sub}` | **Get details of an active session.** | None | `{"session_sub": "...", "fqdn": "...", "rdp_username": "...", "expires_at": "..."}` |
| `DELETE` | `/api/v1/sessions/{session_sub}` | **Clean up and terminate an RDP session.** | None | `{"message": "Session terminated and cleanup initiated."}` |
| `GET` | `/api/v1/status` | **Check API health.** | None | `{"status": "ok"}` |

## 3. System Flow

1.  User sends `POST /api/v1/sessions` with their `X-API-Key`.
2.  API authenticates the user and checks for active sessions/limits.
3.  API generates a unique `session_sub` and calculates `expires_at`.
4.  API calls the local shell script **`create-rdp-session.sh`** with the required parameters.
5.  Script executes, creates the tunnel, and returns the dynamic RDP credentials.
6.  API saves the session details (including the dynamic password) to the `sessions` table.
7.  API returns the connection details to the user.
8.  A separate background process (or the cleanup script itself) will handle the expiration and call **`cleanup-rdp-session.sh`**.
9.  User sends `DELETE /api/v1/sessions/{session_sub}`.
10. API calls **`cleanup-rdp-session.sh`** and updates the session status in the database.
