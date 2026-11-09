import json
import logging
import re
import urllib.parse
import urllib.request
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse

logger = logging.getLogger(__name__)

GOOGLE_AUTH_ENDPOINT = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_ENDPOINT = 'https://oauth2.googleapis.com/token'
GOOGLE_USERINFO_ENDPOINT = 'https://openidconnect.googleapis.com/v1/userinfo'


def get_redirect_uri(request):
    """Return explicit GOOGLE_OAUTH_REDIRECT_URI if set, otherwise build dynamically."""
    configured = getattr(settings, 'GOOGLE_OAUTH_REDIRECT_URI', '').strip()
    if configured:
        return configured
    return request.build_absolute_uri(reverse('google_callback'))


def get_google_auth_url(redirect_uri, state):
    """Build the Google OAuth 2.0 authorization URL."""
    params = {
        'client_id': settings.GOOGLE_CLIENT_ID,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': state,
        'access_type': 'online',
        'prompt': 'select_account',
    }
    return f"{GOOGLE_AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"


def exchange_code_for_tokens(code, redirect_uri):
    """Exchange authorization code for access and ID tokens at Google OAuth endpoint."""
    payload = {
        'code': code,
        'client_id': settings.GOOGLE_CLIENT_ID,
        'client_secret': settings.GOOGLE_CLIENT_SECRET,
        'redirect_uri': redirect_uri,
        'grant_type': 'authorization_code',
    }
    data = urllib.parse.urlencode(payload).encode('utf-8')
    req = urllib.request.Request(
        GOOGLE_TOKEN_ENDPOINT,
        data=data,
        headers={
            'Content-Type': 'application/x-www-form-urlencoded',
            'Accept': 'application/json',
            'User-Agent': 'ForestWatch-OAuth/1.0',
        },
        method='POST',
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode('utf-8'))


def fetch_google_user_info(access_token):
    """Fetch user profile information from Google OpenID Connect endpoint."""
    req = urllib.request.Request(
        GOOGLE_USERINFO_ENDPOINT,
        headers={
            'Authorization': f'Bearer {access_token}',
            'Accept': 'application/json',
            'User-Agent': 'ForestWatch-OAuth/1.0',
        },
        method='GET',
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode('utf-8'))


def get_or_create_google_user(user_info):
    """
    Find existing user by email or create a new Django user with sanitized unique username.
    Returns (user, created: bool).
    """
    email = user_info.get('email', '').strip().lower()
    if not email:
        raise ValueError('Tài khoản Google không có email hợp lệ.')

    UserModel = get_user_model()
    existing_user = UserModel.objects.filter(email__iexact=email).first()
    if existing_user:
        return existing_user, False

    # Generate a clean, unique username derived from email or name
    raw_base = email.split('@')[0]
    cleaned = re.sub(r'[^\w.@+-]', '', raw_base)
    base_username = cleaned if cleaned else 'google_user'

    username = base_username
    counter = 1
    while UserModel.objects.filter(username__iexact=username).exists():
        username = f"{base_username}_{counter}"
        counter += 1

    first_name = (user_info.get('given_name') or user_info.get('name') or '')[:30]
    last_name = (user_info.get('family_name') or '')[:150]

    user = UserModel.objects.create_user(
        username=username,
        email=email,
        first_name=first_name,
        last_name=last_name,
    )
    user.set_unusable_password()
    user.save()
    return user, True
