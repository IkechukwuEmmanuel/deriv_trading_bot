#!/usr/bin/env python3
"""
auth_oauth.py — Professional OAuth2/PKCE Automation
────────────────────────────────────────────────────
Architecture Upgrades:
  1. Local Loopback Server: Automatically catches the OAuth redirect (no copy-pasting).
  2. Safe .env Updates: Uses dotenv.set_key to overwrite existing tokens, preventing duplicates.
  3. Threading: Runs the server in the background without blocking the CLI.
  4. Robust UI: Provides a clean HTML success page to the user's browser.
"""

import argparse
import base64
import hashlib
import logging
import os
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import requests

try:
    import dotenv
except ImportError:
    print("Error: The python-dotenv library is missing. Install with: pip install python-dotenv")
    sys.exit(1)

# ── Professional Logging ──────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("oauth")

AUTH_BASE = "https://oauth.deriv.com/oauth2/authorize" # Standard Deriv OAuth endpoint
TOKEN_URL = "https://oauth.deriv.com/oauth2/token"

# Global state to pass the code from the web server back to the main thread
AUTHORIZATION_CODE = None

# ── Local Web Server for Auto-Capture ─────────────────────────────────────
class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Handles the redirect from Deriv and extracts the auth code."""
    
    def do_GET(self):
        global AUTHORIZATION_CODE
        parsed_url = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed_url.query)

        if "code" in params:
            AUTHORIZATION_CODE = params["code"][0]
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            
            # Send a clean success page to the user
            success_html = """
            <html><body style="font-family: sans-serif; text-align: center; padding-top: 50px;">
                <h2 style="color: #4CAF50;">Authentication Successful!</h2>
                <p>You can close this tab and return to your terminal.</p>
                <script>setTimeout(window.close, 3000);</script>
            </body></html>
            """
            self.wfile.write(success_html.encode("utf-8"))
        else:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Authorization code not found in URL.")

    def log_message(self, format, *args):
        # Suppress default HTTP server logging to keep terminal clean
        pass


def start_local_server(port=8080):
    """Spins up a temporary server to catch the callback."""
    server = HTTPServer(("localhost", port), OAuthCallbackHandler)
    thread = threading.Thread(target=server.serve_forever)
    thread.daemon = True
    thread.start()
    return server


# ── PKCE & Token Logic ────────────────────────────────────────────────────
def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")

def make_pkce():
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = b64url(hashlib.sha256(code_verifier.encode()).digest())
    return code_verifier, code_challenge

def get_auth_url(client_id, redirect_uri, scope, state, code_challenge):
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return AUTH_BASE + "?" + urllib.parse.urlencode(params)

def exchange_code(client_id, code, redirect_uri, code_verifier):
    data = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "code": code,
        "code_verifier": code_verifier,
        "redirect_uri": redirect_uri,
    }
    log.info("Exchanging authorization code for access token...")
    try:
        r = requests.post(TOKEN_URL, data=data, timeout=20)
        r.raise_for_status()
        return r.json()
    except requests.exceptions.HTTPError as e:
        log.error(f"Token exchange failed: {e.response.text}")
        sys.exit(1)

# ── CLI Execution ─────────────────────────────────────────────────────────
def main():
    global AUTHORIZATION_CODE
    
    parser = argparse.ArgumentParser(description="Automated Deriv PKCE OAuth helper")
    parser.add_argument("--client-id", required=True, help="Your OAuth App ID")
    parser.add_argument("--port", type=int, default=8080, help="Local port for callback (default: 8080)")
    parser.add_argument("--scope", default="trade", help="Permissions requested")
    parser.add_argument("--save-env", choices=["DERIV_PAT","DERIV_OAUTH_TOKEN"], default="DERIV_OAUTH_TOKEN",
                        help="Variable name to update in .env file")
    args = parser.parse_args()

    # The redirect URI must match exactly what is registered in the Deriv dashboard
    redirect_uri = f"http://localhost:{args.port}/callback"

    state = secrets.token_urlsafe(16)
    code_verifier, code_challenge = make_pkce()
    auth_url = get_auth_url(args.client_id, redirect_uri, args.scope, state, code_challenge)

    # Start the background server to listen for the redirect
    server = start_local_server(args.port)

    print("\n" + "="*50)
    print("🔐 Deriv Automated OAuth Flow")
    print("="*50)
    print(f"Waiting for authorization on {redirect_uri}...\n")
    
    try:
        webbrowser.open(auth_url)
        print("Your browser has been opened. Please log in.")
    except Exception:
        print("Could not open browser automatically. Please click this link:")
        print(auth_url)

    # Wait until the web server thread updates the global variable
    try:
        while AUTHORIZATION_CODE is None:
            pass
    except KeyboardInterrupt:
        print("\nFlow cancelled by user.")
        server.shutdown()
        sys.exit(0)

    server.shutdown()
    log.info("Code successfully captured from browser!")

    # Exchange for token
    token_data = exchange_code(args.client_id, AUTHORIZATION_CODE, redirect_uri, code_verifier)
    access_token = token_data.get("access_token")

    if not access_token:
        log.error("Failed to extract access_token from response payload.")
        sys.exit(1)

    print("\n✅ Authentication Successful!")
    
    # Safely update the .env file
    env_path = Path(".env")
    if not env_path.exists():
        env_path.touch()
        
    dotenv.set_key(dotenv_path=env_path, key_to_set=args.save_env, value_to_set=access_token)
    log.info(f"Securely updated {args.save_env} in .env file.")


if __name__ == '__main__':
    main()