import logging
import secrets
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout as auth_logout
from django.shortcuts import redirect, render
from .forms import RegisterForm
from .oauth import (
    exchange_code_for_tokens,
    fetch_google_user_info,
    get_google_auth_url,
    get_or_create_google_user,
    get_redirect_uri,
)

logger = logging.getLogger(__name__)


def register(request):
    """User registration view with username, email, and password."""
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            username = form.cleaned_data['username']
            email = form.cleaned_data['email']
            password = form.cleaned_data['password']
            UserModel = get_user_model()
            user = UserModel.objects.create_user(
                username=username,
                email=email,
                password=password,
            )
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            messages.success(
                request,
                f'Đăng ký tài khoản thành công! Chào mừng {user.username} đến với ForestWatch.'
            )
            return redirect('dashboard')
    else:
        form = RegisterForm()

    return render(request, 'registration/register.html', {'form': form})


def google_login(request):
    """Initiate Google OAuth 2.0 flow."""
    state = secrets.token_urlsafe(32)
    request.session['google_oauth_state'] = state
    next_url = request.GET.get('next', '')
    if next_url:
        request.session['google_oauth_next'] = next_url

    has_client_id = bool(getattr(settings, 'GOOGLE_CLIENT_ID', '').strip())
    has_client_secret = bool(getattr(settings, 'GOOGLE_CLIENT_SECRET', '').strip())

    if not has_client_id or not has_client_secret:
        messages.error(
            request,
            'Tính năng đăng nhập Google chưa được cấu hình Client ID. Vui lòng liên hệ quản trị viên.'
        )
        return redirect('login')

    auth_url = get_google_auth_url(get_redirect_uri(request), state)
    return redirect(auth_url)


def google_callback(request):
    """Handle callback from Google OAuth 2.0 authorization server."""
    if 'error' in request.GET:
        err = request.GET.get('error', 'access_denied')
        messages.error(request, f'Đăng nhập Google bị hủy hoặc thất bại: {err}.')
        return redirect('login')

    saved_state = request.session.pop('google_oauth_state', None)
    incoming_state = request.GET.get('state')
    if not saved_state or saved_state != incoming_state:
        messages.error(
            request,
            'Xác thực Google thất bại do phiên làm việc không hợp lệ (state mismatch). Vui lòng thử lại.'
        )
        return redirect('login')

    code = request.GET.get('code')
    if not code:
        messages.error(request, 'Không nhận được mã xác thực từ Google.')
        return redirect('login')

    try:
        redirect_uri = get_redirect_uri(request)
        tokens = exchange_code_for_tokens(code, redirect_uri)
        access_token = tokens.get('access_token')
        if not access_token:
            raise ValueError('Google không trả về access_token hợp lệ.')

        user_info = fetch_google_user_info(access_token)
        user, created = get_or_create_google_user(user_info)
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')

        action = 'Tạo tài khoản mới và đăng nhập' if created else 'Đăng nhập'
        messages.success(
            request,
            f'{action} thành công với tài khoản Google ({user.email})!'
        )

        next_url = request.session.pop('google_oauth_next', None)
        if next_url and next_url.startswith('/'):
            return redirect(next_url)
        return redirect(settings.LOGIN_REDIRECT_URL)
    except Exception as exc:
        logger.exception('Lỗi trong quá trình xử lý Google OAuth callback')
        messages.error(request, f'Đăng nhập Google thất bại: {exc}')
        return redirect('login')


def logout_view(request):
    """Log out the current user and redirect to home."""
    if request.user.is_authenticated:
        auth_logout(request)
        messages.info(request, 'Bạn đã đăng xuất khỏi hệ thống.')
    return redirect(settings.LOGOUT_REDIRECT_URL)
