"""
OAuth2 integration for Facebook/Instagram (Meta Graph API) and LinkedIn.

Handles OAuth flows, token encryption at rest, and token refresh.
"""

import os
import time
import json
import uuid

import requests
from cryptography.fernet import Fernet

from social.models import get_social_db

# --- Environment variables ---
FB_APP_ID = os.environ.get('FB_APP_ID', '')
FB_APP_SECRET = os.environ.get('FB_APP_SECRET', '')
LINKEDIN_CLIENT_ID = os.environ.get('LINKEDIN_CLIENT_ID', '')
LINKEDIN_CLIENT_SECRET = os.environ.get('LINKEDIN_CLIENT_SECRET', '')

# Token encryption key (derive from SECRET_KEY if not provided)
_TOKEN_KEY = os.environ.get('TOKEN_ENCRYPTION_KEY', '')

_fernet = None


def _get_fernet(app_secret_key=None):
    """Get or create a Fernet instance for token encryption."""
    global _fernet
    if _fernet is not None:
        return _fernet

    key = _TOKEN_KEY
    if not key and app_secret_key:
        # Derive a Fernet-compatible key from the Flask secret key
        import hashlib
        import base64
        digest = hashlib.sha256(app_secret_key.encode('utf-8')).digest()
        key = base64.urlsafe_b64encode(digest).decode('utf-8')
    elif not key:
        raise ValueError("No TOKEN_ENCRYPTION_KEY or SECRET_KEY available for token encryption")

    # Ensure key is valid Fernet key (32 url-safe base64 bytes)
    if len(key) != 44:
        import hashlib
        import base64
        digest = hashlib.sha256(key.encode('utf-8')).digest()
        key = base64.urlsafe_b64encode(digest).decode('utf-8')

    _fernet = Fernet(key.encode('utf-8'))
    return _fernet


def encrypt_token(token, app_secret_key=None):
    """Encrypt a token for storage."""
    if not token:
        return None
    f = _get_fernet(app_secret_key)
    return f.encrypt(token.encode('utf-8')).decode('utf-8')


def decrypt_token(encrypted_token, app_secret_key=None):
    """Decrypt a stored token."""
    if not encrypted_token:
        return None
    f = _get_fernet(app_secret_key)
    return f.decrypt(encrypted_token.encode('utf-8')).decode('utf-8')


# =============================================================================
# Facebook / Instagram (Meta Graph API)
# =============================================================================

FB_GRAPH_BASE = 'https://graph.facebook.com/v21.0'
FB_OAUTH_AUTHORIZE = 'https://www.facebook.com/v21.0/dialog/oauth'
FB_OAUTH_TOKEN = f'{FB_GRAPH_BASE}/oauth/access_token'

FB_SCOPES = 'pages_manage_posts,pages_read_engagement,instagram_basic,instagram_content_publish'


def facebook_available():
    return bool(FB_APP_ID and FB_APP_SECRET)


def get_facebook_authorize_url(redirect_uri):
    """Build the Facebook OAuth2 authorization URL."""
    return (
        f"{FB_OAUTH_AUTHORIZE}?"
        f"client_id={FB_APP_ID}&"
        f"redirect_uri={redirect_uri}&"
        f"scope={FB_SCOPES}&"
        f"response_type=code"
    )


def exchange_facebook_code(code, redirect_uri, app_secret_key=None):
    """Exchange authorization code for access tokens and fetch connected pages/IG accounts."""
    # Exchange code for short-lived token
    resp = requests.get(FB_OAUTH_TOKEN, params={
        'client_id': FB_APP_ID,
        'client_secret': FB_APP_SECRET,
        'redirect_uri': redirect_uri,
        'code': code,
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    short_token = data['access_token']

    # Exchange for long-lived token (60 days)
    resp = requests.get(FB_OAUTH_TOKEN, params={
        'grant_type': 'fb_exchange_token',
        'client_id': FB_APP_ID,
        'client_secret': FB_APP_SECRET,
        'fb_exchange_token': short_token,
    }, timeout=15)
    resp.raise_for_status()
    long_data = resp.json()
    user_token = long_data['access_token']
    expires_in = long_data.get('expires_in', 5184000)  # Default 60 days

    # Fetch pages the user manages
    pages_resp = requests.get(f'{FB_GRAPH_BASE}/me/accounts', params={
        'access_token': user_token,
        'fields': 'id,name,access_token,instagram_business_account',
    }, timeout=15)
    pages_resp.raise_for_status()
    pages_data = pages_resp.json().get('data', [])

    accounts = []
    now = time.time()

    for page in pages_data:
        # Facebook page account
        accounts.append({
            'platform': 'facebook',
            'platform_account_id': page['id'],
            'account_name': page['name'],
            'account_type': 'page',
            'access_token': encrypt_token(page['access_token'], app_secret_key),
            'token_expires_at': now + expires_in,
            'scopes': json.dumps(FB_SCOPES.split(',')),
        })

        # Instagram business account (if connected)
        ig = page.get('instagram_business_account')
        if ig:
            ig_id = ig['id']
            # Fetch IG account name
            ig_resp = requests.get(f'{FB_GRAPH_BASE}/{ig_id}', params={
                'access_token': page['access_token'],
                'fields': 'name,username',
            }, timeout=15)
            ig_data = ig_resp.json() if ig_resp.ok else {}
            ig_name = ig_data.get('username') or ig_data.get('name') or f'IG-{ig_id}'

            accounts.append({
                'platform': 'instagram',
                'platform_account_id': ig_id,
                'account_name': ig_name,
                'account_type': 'page',
                'access_token': encrypt_token(page['access_token'], app_secret_key),
                'token_expires_at': now + expires_in,
                'scopes': json.dumps(['instagram_basic', 'instagram_content_publish']),
            })

    return accounts


# =============================================================================
# LinkedIn
# =============================================================================

LI_AUTHORIZE_URL = 'https://www.linkedin.com/oauth/v2/authorization'
LI_TOKEN_URL = 'https://www.linkedin.com/oauth/v2/accessToken'
LI_API_BASE = 'https://api.linkedin.com/v2'

LI_SCOPES = 'w_member_social'


def linkedin_available():
    return bool(LINKEDIN_CLIENT_ID and LINKEDIN_CLIENT_SECRET)


def get_linkedin_authorize_url(redirect_uri, state=None):
    """Build the LinkedIn OAuth2 authorization URL."""
    url = (
        f"{LI_AUTHORIZE_URL}?"
        f"response_type=code&"
        f"client_id={LINKEDIN_CLIENT_ID}&"
        f"redirect_uri={redirect_uri}&"
        f"scope={LI_SCOPES}"
    )
    if state:
        url += f"&state={state}"
    return url


def exchange_linkedin_code(code, redirect_uri, app_secret_key=None):
    """Exchange authorization code for LinkedIn access token and fetch profile."""
    resp = requests.post(LI_TOKEN_URL, data={
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': redirect_uri,
        'client_id': LINKEDIN_CLIENT_ID,
        'client_secret': LINKEDIN_CLIENT_SECRET,
    }, headers={'Content-Type': 'application/x-www-form-urlencoded'}, timeout=15)
    resp.raise_for_status()
    token_data = resp.json()

    access_token = token_data['access_token']
    expires_in = token_data.get('expires_in', 5184000)

    # Fetch profile info
    profile_resp = requests.get(f'{LI_API_BASE}/me', headers={
        'Authorization': f'Bearer {access_token}',
    }, timeout=15)
    profile = profile_resp.json() if profile_resp.ok else {}
    first = profile.get('localizedFirstName', '')
    last = profile.get('localizedLastName', '')
    profile_name = f'{first} {last}'.strip() or 'LinkedIn Profile'
    profile_id = profile.get('id', '')

    return {
        'platform': 'linkedin',
        'platform_account_id': profile_id,
        'account_name': profile_name,
        'account_type': 'profile',
        'access_token': encrypt_token(access_token, app_secret_key),
        'refresh_token': encrypt_token(token_data.get('refresh_token'), app_secret_key),
        'token_expires_at': time.time() + expires_in,
        'scopes': json.dumps(LI_SCOPES.split(',')),
    }


def refresh_facebook_token(account_row, app_secret_key=None):
    """Refresh a Facebook/Instagram long-lived page token. Page tokens don't expire
    if obtained from a long-lived user token, but we still attempt refresh."""
    # Page tokens obtained via long-lived user tokens are already long-lived
    # This is a no-op for page tokens but kept for interface consistency
    return False


def refresh_linkedin_token(account_row, app_secret_key=None):
    """Refresh a LinkedIn access token using the refresh token."""
    refresh_token = decrypt_token(account_row['refresh_token'], app_secret_key)
    if not refresh_token:
        return False

    resp = requests.post(LI_TOKEN_URL, data={
        'grant_type': 'refresh_token',
        'refresh_token': refresh_token,
        'client_id': LINKEDIN_CLIENT_ID,
        'client_secret': LINKEDIN_CLIENT_SECRET,
    }, headers={'Content-Type': 'application/x-www-form-urlencoded'}, timeout=15)

    if not resp.ok:
        return False

    token_data = resp.json()
    new_token = encrypt_token(token_data['access_token'], app_secret_key)
    new_refresh = encrypt_token(token_data.get('refresh_token', refresh_token), app_secret_key)
    expires_in = token_data.get('expires_in', 5184000)

    return {
        'access_token': new_token,
        'refresh_token': new_refresh,
        'token_expires_at': time.time() + expires_in,
    }


def save_social_account(db_path, user_id, account_data):
    """Save or update a social account in the database."""
    conn = get_social_db(db_path)
    try:
        # Check if account already exists for this platform + account_id
        existing = conn.execute(
            "SELECT id FROM social_accounts WHERE platform = ? AND platform_account_id = ?",
            (account_data['platform'], account_data['platform_account_id'])
        ).fetchone()

        now = time.time()

        if existing:
            conn.execute(
                "UPDATE social_accounts SET access_token=?, refresh_token=?, "
                "token_expires_at=?, scopes=?, account_name=?, is_active=1, updated_at=? "
                "WHERE id=?",
                (
                    account_data.get('access_token'),
                    account_data.get('refresh_token'),
                    account_data.get('token_expires_at'),
                    account_data.get('scopes', '[]'),
                    account_data.get('account_name'),
                    now,
                    existing['id'],
                )
            )
        else:
            account_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO social_accounts "
                "(id, user_id, platform, platform_account_id, account_name, account_type, "
                "access_token, refresh_token, token_expires_at, scopes, is_active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    account_id, user_id,
                    account_data['platform'],
                    account_data.get('platform_account_id', ''),
                    account_data.get('account_name', ''),
                    account_data.get('account_type', 'page'),
                    account_data.get('access_token'),
                    account_data.get('refresh_token'),
                    account_data.get('token_expires_at'),
                    account_data.get('scopes', '[]'),
                    now, now,
                )
            )
        conn.commit()
    finally:
        conn.close()
