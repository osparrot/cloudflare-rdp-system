# Cloudflare Ephemeral RDP Service Platform (Supabase Edition)

This repository contains the complete system to run a scalable, commercial RDP service using an Ubuntu server, Cloudflare Tunnel, and a Python-based API (FastAPI) for session management and user authentication, now powered by **Supabase** for a robust and scalable database backend.

## Key Features

*   **Commercial Ready:** FastAPI backend with API Key authentication, **Web Frontend**, and **Session Monitoring Worker**.
*   **Zero-Trust Security:** RDP access is proxied through Cloudflare, requiring authentication via Cloudflare Access.
*   **Ephemeral Sessions:** Automated creation and cleanup of unique Cloudflare Tunnels and DNS records.
*   **Reliable Backend:** Uses shell scripts for low-level system operations (tunnel/DNS/systemd) and a Python API for high-level business logic.
*   **Dynamic Credentials:** Generates unique, per-session RDP credentials for enhanced security.

## System Components

| Component | Technology | Purpose |
| :--- | :--- | :--- |
| **API Backend** | Python (FastAPI) | Handles user authentication (API Key), session creation/deletion requests, and database management. |
| **Web Frontend** | FastAPI + Jinja2 | Provides the user dashboard, session viewing, and credential retrieval. |
| **Session Monitor** | Python Script | Background worker to automatically clean up expired sessions. |
| **Database** | **Supabase (PostgreSQL)** | Stores user API keys and active/expired RDP session metadata. |
| **Shell Scripts** | Bash | Low-level system operations: creating/cleaning up Cloudflare Tunnels, systemd services, and DNS records. |
| **RDP Host** | Ubuntu + XRDP | The server hosting the actual RDP environment. |

## Prerequisites

1.  **Ubuntu Server:** A running Ubuntu server (e.g., 20.04 or 22.04) with RDP installed (`xrdp`).
2.  **Cloudflare Account:** Active account with a domain configured.
3.  **Cloudflare Tunnel:** `cloudflared` installed and authenticated on the server.
4.  **Cloudflare Access:** Policy configured for the RDP subdomain.
5.  **API Environment:** Python 3.8+, `pip`, and `uvicorn`.
6.  **Supabase Project:** A running Supabase project with the necessary tables created.

## Setup Instructions

### 1. Supabase Database Setup

You need to create a Supabase project and define the following two tables.

#### Table: `users`
| Column | Type | Default Value | Description |
| :--- | :--- | :--- | :--- |
| `id` | `bigint` | `auto-increment` | Unique user ID |
| `api_key` | `text` | | Secret key for API authentication |
| `is_active` | `boolean` | `true` | Whether the user is active |
| `created_at` | `timestamp with time zone` | `now()` | Timestamp of user creation |

#### Table: `sessions`
| Column | Type | Default Value | Description |
| :--- | :--- | :--- | :--- |
| `id` | `bigint` | `auto-increment` | Unique session ID |
| `user_id` | `bigint` | | Foreign key to `users.id` |
| `session_sub` | `text` | | The unique session identifier (e.g., `session-a1b2c3d4`) |
| `fqdn` | `text` | | The full RDP domain |
| `rdp_username` | `text` | | The RDP username (e.g., `admin`) |
| `rdp_password` | `text` | | The dynamically generated RDP password |
| `status` | `text` | | Current status (e.g., `active`, `pending`, `expired`, `cleaned`) |
| `created_at` | `timestamp with time zone` | `now()` | Timestamp of session creation |
| `expires_at` | `timestamp with time zone` | | Timestamp of session expiration |

### 2. Install Dependencies and RDP Server

On your Ubuntu server, run the following commands:

```bash
# Install necessary tools and RDP server
sudo apt update
sudo apt install -y python3 python3-pip xrdp openssl jq curl

# Install cloudflared (follow official Cloudflare documentation for the latest method)
# ... (Installation steps remain the same)
```

### 3. Configure Environment Variables

The API, Worker, and scripts require the following environment variables to be set on the server:

| Variable | Description | Source |
| :--- | :--- | :--- |
| `SUPABASE_URL` | The URL of your Supabase project (e.g., `https://<project-ref>.supabase.co`). | Supabase Settings -> API |
| `SUPABASE_KEY` | The **Service Role Key** (or an equivalent key with full table access). | Supabase Settings -> API |
| `CF_API_TOKEN` | Bearer token with **Zone:DNS Edit** permissions. | Cloudflare Dashboard -> My Profile -> API Tokens |
| `CF_ZONE_ID` | The ID of the Cloudflare Zone (domain). | Cloudflare Dashboard -> Domain Overview |
| `BASE_DOMAIN` | The base domain for RDP sessions (e.g., `rdp.yourdomain.com`). | Your configured domain |
| `WORKER_SECRET` | A secret key for the worker to authenticate with the API (optional, but recommended). | Choose a strong secret string |

### 4. Deploy the System

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/osparrot/cloudflare-rdp-system.git
    cd cloudflare-rdp-system
    ```

2.  **Install Python dependencies:**
    ```bash
    # Install dependencies for both API and Worker
    pip3 install -r api/requirements.txt
    ```

3.  **Make scripts executable:**
    ```bash
    sudo chmod +x create-rdp-session.sh cleanup-rdp-session.sh
    ```

4.  **Run the API (Web Frontend):**
    ```bash
    # Ensure all environment variables are set before running
    uvicorn api.main:app --host 0.0.0.0 --port 8000
    ```
    The Web Frontend will be accessible at `http://<your-server-ip>:8000`.

5.  **Run the Session Monitor Worker:**
    The worker should be run as a persistent background process (e.g., using `systemd` or `screen`).

    ```bash
    # Example of running the worker
    python3 worker/session_monitor.py
    ```

### 5. User Journey & Access

#### User Dashboard
*   **Access:** Navigate to your deployed URL (e.g., `http://<your-server-ip>:8000`).
*   **Login:** Use your `api_key` to log in.
*   **Session Management:** Create a new session or terminate an active one.
*   **Credentials:** View the dynamically generated **Computer Name (FQDN)**, **Username**, and **Password** for connection.

#### Admin Dashboard
*   **Access:** Navigate to `/admin/sessions` (e.g., `http://<your-server-ip>:8000/admin/sessions`).
*   **Auditing:** View all sessions, their status, and have the option to manually revoke active sessions.

## Next Steps for Commercialization

*   **Billing Integration:** The business logic is now ready to be integrated with a billing system (e.g., a crypto payment gateway).
*   **Advanced Auditing:** Implement logging for password reveal events and integrate with Cloudflare's log push service for access logs.
