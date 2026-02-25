"""
Google OAuth2 authentication for the DabbahWala dashboard.

Only @dabbahwala.com Google accounts are allowed.
Sessions are stored as HMAC-signed cookies (no database required).

Required env vars:
  GOOGLE_CLIENT_ID     — from Google Cloud Console OAuth2 credentials
  GOOGLE_CLIENT_SECRET — from Google Cloud Console OAuth2 credentials
  SESSION_SECRET       — any long random string (openssl rand -hex 32)
  BASE_URL             — e.g. https://dabbahwala-latest.onrender.com
"""

import base64
import hashlib
import hmac as _hmac
import logging
import os
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-change-me-in-production")
ALLOWED_DOMAIN = "dabbahwala.com"
SESSION_COOKIE = "dw_session"
SESSION_MAX_AGE = 7 * 86400  # 7 days


# ---------------------------------------------------------------------------
# Session cookie helpers
# ---------------------------------------------------------------------------

def _sign(payload: str) -> str:
    return _hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def create_session(email: str) -> str:
    ts = str(int(time.time()))
    payload = f"{email}|{ts}"
    sig = _sign(payload)
    return base64.urlsafe_b64encode(f"{payload}|{sig}".encode()).decode()


def decode_session(cookie: str) -> str | None:
    """Return the email from a valid session cookie, or None."""
    try:
        raw = base64.urlsafe_b64decode(cookie.encode()).decode()
        # Split from right so emails with | in them (unusual but safe) don't break
        parts = raw.rsplit("|", 2)
        if len(parts) != 3:
            return None
        email, ts, sig = parts
        payload = f"{email}|{ts}"
        if not _hmac.compare_digest(sig, _sign(payload)):
            return None
        if int(time.time()) - int(ts) > SESSION_MAX_AGE:
            return None
        return email
    except Exception:
        return None


def get_current_user(request: Request) -> str | None:
    """Return the logged-in user's email, or None if not authenticated."""
    return decode_session(request.cookies.get(SESSION_COOKIE, ""))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/login", response_class=HTMLResponse)
def login_page():
    path = os.path.join(os.path.dirname(__file__), "..", "login.html")
    with open(path) as f:
        return f.read()


@router.get("/auth/google")
def google_auth(request: Request):
    """Redirect the browser to Google's OAuth2 consent screen."""
    if not GOOGLE_CLIENT_ID:
        return HTMLResponse(
            "<h2>Google OAuth not configured.</h2>"
            "<p>Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET env vars.</p>",
            status_code=503,
        )
    base_url = os.environ.get("BASE_URL", str(request.base_url).rstrip("/"))
    redirect_uri = f"{base_url}/auth/callback"
    params = "&".join([
        f"client_id={GOOGLE_CLIENT_ID}",
        f"redirect_uri={redirect_uri}",
        "response_type=code",
        "scope=openid%20email%20profile",
        "access_type=offline",
        "prompt=select_account",
    ])
    return RedirectResponse(url=f"https://accounts.google.com/o/oauth2/v2/auth?{params}")


@router.get("/auth/callback")
async def google_callback(request: Request, code: str = "", error: str = ""):
    """Handle the OAuth2 redirect from Google."""
    if error or not code:
        logger.warning("Google OAuth cancelled or errored: %s", error)
        return RedirectResponse(url="/login?error=cancelled")

    base_url = os.environ.get("BASE_URL", str(request.base_url).rstrip("/"))
    redirect_uri = f"{base_url}/auth/callback"

    async with httpx.AsyncClient() as client:
        # Exchange authorization code for access token
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=15,
        )
        if token_resp.status_code != 200:
            logger.error("Google token exchange failed: %s", token_resp.text)
            return RedirectResponse(url="/login?error=token_failed")

        access_token = token_resp.json().get("access_token", "")

        # Fetch user profile
        user_resp = await client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        if user_resp.status_code != 200:
            logger.error("Google userinfo failed: %s", user_resp.text)
            return RedirectResponse(url="/login?error=userinfo_failed")

        email = user_resp.json().get("email", "").lower()

    if not email.endswith(f"@{ALLOWED_DOMAIN}"):
        logger.warning("Unauthorized login attempt from: %s", email)
        return RedirectResponse(url="/login?error=unauthorized")

    logger.info("Dashboard login: %s", email)
    secure = base_url.startswith("https")
    resp = RedirectResponse(url="/dashboard", status_code=302)
    resp.set_cookie(
        SESSION_COOKIE,
        create_session(email),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=secure,
        samesite="lax",
    )
    return resp


@router.get("/auth/me")
def auth_me(request: Request):
    """Return the current user's email (used by the dashboard JS)."""
    user = get_current_user(request)
    if not user:
        return JSONResponse(status_code=401, content={"error": "not_authenticated"})
    return {"email": user}


@router.get("/auth/logout")
def logout():
    resp = RedirectResponse(url="/login")
    resp.delete_cookie(SESSION_COOKIE)
    return resp
