import logging
import secrets
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import (
    get_user_model,
    login,
    logout as auth_logout,
    update_session_auth_hash,
)
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from .forms import RegisterForm, SetPasswordForGoogleForm
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
        return redirect(settings.LOGIN_REDIRECT_URL)

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
            login(request, user, backend='monitor.auth_backend.EmailOrUsernameModelBackend')
            messages.success(
                request,
                f'Đăng ký tài khoản thành công! Chào mừng {user.username} đến với ForestWatch.'
            )
            return redirect(settings.LOGIN_REDIRECT_URL)
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
        login(request, user, backend='monitor.auth_backend.EmailOrUsernameModelBackend')

        # If user was just created or doesn't have a usable password yet, prompt password creation
        if created or not user.has_usable_password():
            messages.success(
                request,
                f'Đăng nhập tài khoản Google ({user.email}) thành công! '
                f'Vui lòng thiết lập mật khẩu để lần sau có thể đăng nhập linh hoạt bằng tài khoản Google hoặc mật khẩu.'
            )
            return redirect('google_set_password')

        action = 'Đăng nhập'
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


@login_required
def google_set_password(request):
    """
    Allow users signed in via Google to create/set a password for their account.
    Once created, they can log in via Google OAuth OR directly by typing their username/email + password.
    """
    next_url = request.session.get('google_oauth_next') or settings.LOGIN_REDIRECT_URL
    if not next_url.startswith('/'):
        next_url = settings.LOGIN_REDIRECT_URL

    if request.method == 'POST':
        if 'skip' in request.POST:
            request.session.pop('google_oauth_next', None)
            messages.info(
                request,
                'Bạn đã bỏ qua bước tạo mật khẩu. Bạn vẫn có thể đăng nhập bằng Google hoặc thiết lập mật khẩu sau.'
            )
            return redirect(next_url)

        form = SetPasswordForGoogleForm(request.POST)
        if form.is_valid():
            new_password = form.cleaned_data['password']
            user = request.user
            user.set_password(new_password)
            user.save()
            update_session_auth_hash(request, user)
            request.session.pop('google_oauth_next', None)
            messages.success(
                request,
                f'Thiết lập mật khẩu thành công! Từ bây giờ, bạn có thể đăng nhập bằng tài khoản Google '
                f'hoặc nhập trực tiếp Tên đăng nhập ({user.username}) / Email ({user.email}) cùng mật khẩu vừa tạo.'
            )
            return redirect(next_url)
    else:
        form = SetPasswordForGoogleForm()

    return render(request, 'registration/set_password.html', {
        'form': form,
        'user': request.user,
        'has_password': request.user.has_usable_password(),
        'next_url': next_url,
    })


def logout_view(request):
    """Log out the current user and redirect to home."""
    if request.user.is_authenticated:
        auth_logout(request)
        messages.info(request, 'Bạn đã đăng xuất khỏi hệ thống.')
    return redirect(settings.LOGOUT_REDIRECT_URL)
