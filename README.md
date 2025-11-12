# Cloudflare RDP Session Manager (Decoupled Architecture)

This repository contains the complete system to run a scalable, commercial RDP service using an Ubuntu server, Cloudflare Tunnel, and a Python-based API (FastAPI) for session management and user authentication, now powered by **Supabase** for a robust and scalable database backend.

## Key Architectural Change: Decoupled System

The system has been refactored into a decoupled architecture for better security, scalability, and easier deployment on platforms like CyberPanel:

| Component | Technology | Deployment Location | Purpose |
| :--- | :--- | :--- | :--- |
| **Web Frontend** | Static HTML/JS/CSS | **CyberPanel/OpenLiteSpeed** | Provides the user dashboard, session viewing, and credential retrieval. Communicates with the API via JavaScript. |
| **API Backend** | Python (FastAPI) | **Ubuntu Server (Systemd)** | Handles user authentication (API Key), session creation/deletion requests, and database management. Serves only JSON responses. |
| **Session Monitor** | Python Script | **Ubuntu Server (Systemd)** | Background worker to automatically clean up expired sessions. |

## Key Features

*   **Commercial Ready:** API Key authentication, **Decoupled Web Frontend**, and **Session Monitoring Worker**.
*   **Zero-Trust Security:** RDP access is proxied through Cloudflare, requiring authentication via Cloudflare Access.
*   **Performance Optimized:** Uses QUIC, BBR, and sysctl tuning for low-latency RDP.
*   **Reliable Backend:** Uses shell scripts for low-level system operations (tunnel/DNS/systemd) and a Python API for high-level business logic.

## Prerequisites

1.  **Ubuntu Server:** A running Ubuntu server (e.g., 20.04 or 22.04) with RDP installed (`xrdp`).
2.  **CyberPanel Host:** A separate host or virtual host running CyberPanel for the static frontend.
3.  **Cloudflare Account:** Active account with a domain configured.
4.  **Supabase Project:** A running Supabase project with the necessary tables created.

## Setup Instructions

### 1. Supabase Database Setup

You need to create a Supabase project and define the following two tables (`users` and `sessions`). Refer to the previous version of the README for the exact SQL schema.

### 2. Deploy the API Backend (Ubuntu Server)

This component runs the core logic and should be firewalled off from public access, only allowing connections from your CyberPanel host.

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/osparrot/cloudflare-rdp-system.git
    cd cloudflare-rdp-system
    ```

2.  **Install Python dependencies:**
    ```bash
    pip3 install -r api/requirements.txt
    ```

3.  **Configure Environment Variables:**
    Create the environment file at `/etc/rdp-service/rdp.env` with all required variables (`SUPABASE_URL`, `SUPABASE_KEY`, `CF_API_TOKEN`, `CF_ZONE_ID`, `BASE_DOMAIN`, `WORKER_SECRET`).

4.  **Deploy Systemd Services:**
    Copy the service files and start the services:
    ```bash
    sudo cp rdp-session-manager.service /etc/systemd/system/
    sudo cp rdp-session-worker.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now rdp-session-manager.service
    sudo systemctl enable --now rdp-session-worker.service
    ```
    The API will be running on `http://127.0.0.1:8000` (or your server's private IP).

### 3. Deploy the Web Frontend (CyberPanel Host)

This component is a static website that will be hosted by CyberPanel/OpenLiteSpeed.

1.  **Copy Static Files:**
    Copy the contents of the local `frontend/` directory to the public directory of your CyberPanel website (e.g., `/home/yourdomain.com/public_html/`).

2.  **Configure API Endpoint:**
    The JavaScript in `frontend/js/app.js` assumes the API is on the same domain. If your API is on a different domain (e.g., `api.yourdomain.com`), you must update the `API_BASE_URL` constant in `frontend/js/app.js` to point to the public URL of your API.

3.  **CyberPanel Setup:**
    *   Create a new website in CyberPanel for your frontend domain.
    *   Ensure the document root points to the directory containing `index.html`.
    *   Issue a Let's Encrypt SSL certificate via the CyberPanel dashboard.

## Next Steps for Commercialization

*   **Billing Integration:** The business logic is now ready to be integrated with a crypto payment gateway.
*   **Advanced Auditing:** Implement logging for password reveal events and integrate with Cloudflare's log push service for access logs.
*   **User Registration:** Implement a user registration flow instead of relying on manual API key creation.
