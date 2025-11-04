# Cloudflare Ephemeral RDP System

This repository contains a set of scripts and instructions for setting up a secure, ephemeral Remote Desktop Protocol (RDP) system on an Ubuntu server using **Cloudflare Tunnel** (formerly Argo Tunnel) and **Cloudflare Access**.

This modern approach provides a robust, zero-trust solution that avoids exposing RDP ports directly to the public internet, relying instead on Cloudflare's global network for secure access.

## Key Features

*   **Zero-Trust Security:** RDP access is proxied through Cloudflare, requiring authentication via Cloudflare Access before connection.
*   **Ephemeral Sessions:** Creates a temporary, unique tunnel and DNS record for each session, which can be automatically cleaned up.
*   **Automatic Cleanup:** Sessions can be configured with a Time-To-Live (TTL) and will be automatically cleaned up using `systemd` timers.
*   **Dynamic Credentials:** Generates unique, per-session RDP credentials for enhanced security.
*   **Ubuntu Server Focus:** Designed for a reliable, long-running RDP host environment (e.g., a VPS or dedicated server).

## Prerequisites

1.  **Ubuntu Server:** A running Ubuntu server (e.g., 20.04 or 22.04) with RDP installed (e.g., `xrdp`).
2.  **Cloudflare Account:** An active Cloudflare account with a domain configured.
3.  **Cloudflare Tunnel:** `cloudflared` installed on the Ubuntu server.
4.  **Cloudflare Access:** A Cloudflare Access policy configured for the RDP subdomain.
5.  **Tools:** `jq`, `openssl`, and `curl` installed on the server.

## Setup Instructions

### 1. Install Dependencies and RDP Server

On your Ubuntu server, run the following commands:

```bash
# Install necessary tools
sudo apt update
sudo apt install -y xrdp openssl jq curl

# Install cloudflared (follow official Cloudflare documentation for the latest method)
# Example for Debian/Ubuntu:
curl -L --output cloudflared-linux-amd64.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared-linux-amd64.deb
rm cloudflared-linux-amd64.deb

# Ensure cloudflared is in the PATH for the scripts
sudo ln -s /usr/local/bin/cloudflared /usr/bin/cloudflared
```

### 2. Configure Cloudflare Tunnel

You need to authenticate `cloudflared` and configure your Cloudflare Access policy.

1.  **Authenticate `cloudflared`:**
    ```bash
    cloudflared tunnel login
    ```
    This will open a browser window to authenticate and download a certificate file (`cert.pem`).

2.  **Configure Cloudflare Access:**
    *   In your Cloudflare dashboard, go to **Access** -> **Applications**.
    *   Create a new self-hosted application for your RDP subdomain (e.g., `*.rdp.yourdomain.com`).
    *   Set up an Access Policy to define who can connect (e.g., only your email address).

3.  **Obtain Cloudflare API Credentials (for DNS cleanup):**
    *   Go to **My Profile** -> **API Tokens** and create a token with **Zone:DNS Edit** permissions for the zone you are using.
    *   Note your **API Token** (`CF_API_TOKEN`) and your **Zone ID** (`CF_ZONE_ID`).

### 3. Install the Scripts

Copy the scripts to a system-wide location and make them executable:

```bash
# Clone this repository
git clone https://github.com/osparrot/cloudflare-rdp-system.git
cd cloudflare-rdp-system

# Copy scripts to /usr/local/bin
sudo cp create-rdp-session.sh /usr/local/bin/
sudo cp cleanup-rdp-session.sh /usr/local/bin/

# Make them executable
sudo chmod +x /usr/local/bin/create-rdp-session.sh
sudo chmod +x /usr/local/bin/cleanup-rdp-session.sh
```

### 4. Usage

#### Create a Session

Run the script as root, providing your Cloudflare credentials and the desired RDP users.

```bash
# Example: Create a session for user 'admin' that expires in 8 hours
sudo CF_API_TOKEN="<YOUR_CF_API_TOKEN>" \
     CF_ZONE_ID="<YOUR_CF_ZONE_ID>" \
     BASE_DOMAIN="yourdomain.com" \
     /usr/local/bin/create-rdp-session.sh "admin" 8
```

The script will output the **FQDN** (e.g., `session-a1b2c3d4.yourdomain.com`) and the **dynamically generated password** for the RDP user.

#### Connect to the Session

You must use the `cloudflared access` command to proxy the RDP connection locally:

```bash
# Run this on your local machine (where you want to connect from)
cloudflared access rdp --hostname <FQDN_FROM_OUTPUT> --url localhost:3389

# Then, open your standard RDP client (e.g., Remote Desktop Connection on Windows)
# and connect to:
# Host: localhost:3389
# Username: admin (or the user you specified)
# Password: <DYNAMIC_PASSWORD_FROM_OUTPUT>
```

#### Clean Up a Session

The session will automatically clean up after the TTL expires. To clean up manually:

```bash
# Use the session name (e.g., session-a1b2c3d4) from the output
sudo /usr/local/bin/cleanup-rdp-session.sh <SESSION_NAME>
```

## Troubleshooting

| Issue | Possible Cause | Solution |
| :--- | :--- | :--- |
| `cloudflared not found` | `cloudflared` is not installed or not in the system PATH. | Run the installation steps in **Step 1**. |
| `cloudflared tunnel create failed` | Authentication issue or network problem. | Re-run `cloudflared tunnel login`. |
| `cloudflared tunnel route dns failed` | `cloudflared` login lacks DNS permissions or the FQDN is already in use. | Ensure the `cloudflared` login has **Zone:DNS Edit** permissions. |
| `Session systemd service failed to start` | RDP service (`xrdp`) is not running or port 3389 is blocked. | Check `systemctl status xrdp` and ensure the local firewall allows traffic on port 3389. |
| `No DNS record found` during cleanup | The DNS record was manually deleted or the `CF_API_TOKEN`/`CF_ZONE_ID` are incorrect. | Manual cleanup of the tunnel may be required using `cloudflared tunnel delete`. |
