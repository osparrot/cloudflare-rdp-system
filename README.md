# Cloudflare Ephemeral RDP Service Platform

This repository contains the complete system to run a scalable, commercial RDP service using an Ubuntu server, Cloudflare Tunnel, and a Python-based API (FastAPI) for session management and user authentication.

## Key Features

*   **Commercial Ready:** FastAPI backend with API Key authentication and a SQLite database for user and session tracking.
*   **Zero-Trust Security:** RDP access is proxied through Cloudflare, requiring authentication via Cloudflare Access.
*   **Ephemeral Sessions:** Automated creation and cleanup of unique Cloudflare Tunnels and DNS records.
*   **Reliable Backend:** Uses shell scripts for low-level system operations (tunnel/DNS/systemd) and a Python API for high-level business logic.
*   **Dynamic Credentials:** Generates unique, per-session RDP credentials for enhanced security.

## System Components

| Component | Technology | Purpose |
| :--- | :--- | :--- |
| **API Backend** | Python (FastAPI) | Handles user authentication (API Key), session creation/deletion requests, and database management. |
| **Database** | SQLite | Stores user API keys and active/expired RDP session metadata. |
| **Shell Scripts** | Bash | Low-level system operations: creating/cleaning up Cloudflare Tunnels, systemd services, and DNS records. |
| **RDP Host** | Ubuntu + XRDP | The server hosting the actual RDP environment. |

## Prerequisites

1.  **Ubuntu Server:** A running Ubuntu server (e.g., 20.04 or 22.04) with RDP installed (`xrdp`).
2.  **Cloudflare Account:** Active account with a domain configured.
3.  **Cloudflare Tunnel:** `cloudflared` installed and authenticated on the server.
4.  **Cloudflare Access:** Policy configured for the RDP subdomain.
5.  **API Environment:** Python 3.8+, `pip`, and `uvicorn`.

## Setup Instructions

### 1. Install Dependencies and RDP Server

On your Ubuntu server, run the following commands:

```bash
# Install necessary tools and RDP server
sudo apt update
sudo apt install -y python3 python3-pip xrdp openssl jq curl

# Install cloudflared (follow official Cloudflare documentation for the latest method)
# Example for Debian/Ubuntu:
curl -L --output cloudflared-linux-amd64.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb
rm cloudflared-linux-amd64.deb

# Ensure cloudflared is in the PATH for the scripts
sudo ln -s /usr/local/bin/cloudflared /usr/bin/cloudflared
```

### 2. Configure Cloudflare Credentials

The API and scripts require the following environment variables to be set on the server:

| Variable | Description | Source |
| :--- | :--- | :--- |
| `CF_API_TOKEN` | Bearer token with **Zone:DNS Edit** permissions. | Cloudflare Dashboard -> My Profile -> API Tokens |
| `CF_ZONE_ID` | The ID of the Cloudflare Zone (domain). | Cloudflare Dashboard -> Domain Overview |
| `BASE_DOMAIN` | The base domain for RDP sessions (e.g., `rdp.yourdomain.com`). | Your configured domain |

You should set these in your system's environment (e.g., in `/etc/environment` or the service file for the API).

### 3. Deploy the API Backend

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/osparrot/cloudflare-rdp-system.git
    cd cloudflare-rdp-system
    ```

2.  **Install Python dependencies:**
    ```bash
    pip3 install -r api/requirements.txt
    ```

3.  **Initialize the database:**
    The database is initialized when `api/main.py` is first run. A default user with `TEST_API_KEY_12345` is created for initial testing.

4.  **Make scripts executable:**
    ```bash
    sudo chmod +x create-rdp-session.sh cleanup-rdp-session.sh
    ```

5.  **Run the API (e.g., using a systemd service or screen session):**
    ```bash
    # Example for development/testing
    uvicorn api.main:app --host 0.0.0.0 --port 8000
    ```

### 4. API Usage

The API is protected by the `X-API-Key` header.

#### Create a Session

**Endpoint:** `POST /api/v1/sessions`

**Headers:** `X-API-Key: <YOUR_API_KEY>`

**Body (JSON):**
```json
{
  "duration_hours": 4,
  "rdp_username": "admin"
}
```

**Response (JSON):**
```json
{
  "session_sub": "session-a1b2c3d4",
  "fqdn": "session-a1b2c3d4.rdp.yourdomain.com",
  "rdp_username": "admin",
  "rdp_password": "DYNAMIC_PASSWORD",
  "expires_at": "2025-11-04T18:00:00"
}
```

#### Terminate a Session

**Endpoint:** `DELETE /api/v1/sessions/{session_sub}`

**Headers:** `X-API-Key: <YOUR_API_KEY>`

**Example:** `DELETE /api/v1/sessions/session-a1b2c3d4`

**Response (JSON):**
```json
{
  "message": "Session session-a1b2c3d4 terminated and cleanup initiated."
}
```

## Next Steps for Commercialization

*   **Billing Integration:** Replace the simple duration check with a system that verifies user credit/payment before calling the session creation API.
*   **User Interface:** Build a simple web frontend for users to manage their API keys and view active sessions.
*   **Session Monitoring:** Implement a background task to periodically check the status of active sessions and automatically call the cleanup script for expired ones.
