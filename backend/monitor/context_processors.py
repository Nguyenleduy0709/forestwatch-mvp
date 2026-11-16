from django.conf import settings


def auth_options(request):
    """Show the Google sign-in button only when both OAuth credentials are configured."""
    return {'google_login_enabled': bool(getattr(settings, 'GOOGLE_CLIENT_ID', '').strip()
                                         and getattr(settings, 'GOOGLE_CLIENT_SECRET', '').strip())}
