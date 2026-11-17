"""System and custom Telegram bot helpers: IPv4-safe transport, 1-click pairing, and chat discovery."""
import hashlib
import html
import http.client
import json
import logging
import re
import secrets
import socket
import urllib.error
import urllib.request

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache

from .models import MonitoringProfile

logger = logging.getLogger(__name__)

_ORIG_URLOPEN = urllib.request.urlopen
_START_COMMAND_RE = re.compile(r'^/start(?:@[A-Za-z0-9_]+)?\s+(fw_[A-Za-z0-9_]+)\b')
_PAIR_TTL_SECONDS = 600
_DONE_TTL_SECONDS = 300
_BOT_USERNAME_TTL_SECONDS = 3600


class _IPv4HTTPSConnection(http.client.HTTPSConnection):
    """Prefer IPv4 for api.telegram.org to avoid multi-second stalls on hosts without IPv6 routing."""

    def connect(self):
        try:
            infos = socket.getaddrinfo(self.host, self.port, socket.AF_INET, socket.SOCK_STREAM)
        except socket.gaierror:
            return super().connect()
        if not infos:
            return super().connect()
        last_err = None
        for af, socktype, proto, _, sa in infos:
            sock = None
            try:
                sock = socket.socket(af, socktype, proto)
                if self.timeout is not None:
                    sock.settimeout(self.timeout)
                sock.connect(sa)
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                return
            except OSError as exc:
                last_err = exc
                if sock is not None:
                    sock.close()
        if last_err is not None:
            raise last_err


class _IPv4HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_IPv4HTTPSConnection, req)


_IPV4_OPENER = urllib.request.build_opener(_IPv4HTTPSHandler())


def telegram_urlopen(req, timeout=15):
    """Call Telegram API preferring IPv4 while staying compatible with urllib urlopen mocks in tests."""
    if urllib.request.urlopen is not _ORIG_URLOPEN:
        return urllib.request.urlopen(req, timeout=timeout)
    return _IPV4_OPENER.open(req, timeout=timeout)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()[:16]


def _sanitize_error(text: str, token: str) -> str:
    cleaned = str(text or '')
    if token:
        cleaned = cleaned.replace(token, '***')
    return cleaned[:240]


def effective_telegram_token(profile: MonitoringProfile | None) -> str:
    """Return user's custom Bot Token if configured, otherwise fallback to system TELEGRAM_BOT_TOKEN."""
    custom = (getattr(profile, 'telegram_bot_token', '') or '').strip() if profile else ''
    return custom or (getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or '').strip()


def _telegram_api_request(
    token: str,
    method: str,
    payload: dict | None = None,
    *,
    timeout: int = 12,
) -> tuple[bool, dict | str]:
    clean_token = (token or '').strip()
    if ':' not in clean_token:
        return False, 'Bot Token Telegram không hợp lệ.'
    url = f'https://api.telegram.org/bot{clean_token}/{method}'
    if payload is not None:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
    else:
        req = urllib.request.Request(url, method='GET')

    try:
        with telegram_urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if data.get('ok'):
                return True, data
            return False, _sanitize_error(data.get('description') or 'Telegram từ chối yêu cầu.', clean_token)
    except urllib.error.HTTPError as exc:
        raw_body = exc.read().decode('utf-8', errors='ignore')
        desc = raw_body
        try:
            parsed = json.loads(raw_body)
            desc = str(parsed.get('description') or raw_body)
        except ValueError:
            pass
        if exc.code in (401, 404):
            return False, 'Bot Token Telegram không hợp lệ hoặc đã bị thu hồi.'
        if exc.code == 409 and 'webhook' in desc.lower():
            return False, 'Bot đang gắn Webhook ở nơi khác nên không thể lấy getUpdates song song.'
        return False, f'Telegram API HTTP {exc.code}: {_sanitize_error(desc, clean_token)}'
    except Exception as exc:
        return False, _sanitize_error(f'Không kết nối được máy chủ Telegram: {exc}', clean_token)


def resolve_bot_username(token: str, *, fetch: bool = True) -> tuple[bool, str]:
    """Resolve @username for the given Telegram bot token via settings or cached getMe."""
    clean = (token or '').strip()
    if not clean:
        return False, 'Chưa cấu hình Bot Token Telegram.'
    system_token = (getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or '').strip()
    system_username = (getattr(settings, 'TELEGRAM_BOT_USERNAME', '') or '').strip().lstrip('@')
    if system_token and clean == system_token and system_username:
        return True, system_username

    cache_key = f'tg_bot_username:{_token_hash(clean)}'
    cached = cache.get(cache_key)
    if cached:
        return True, str(cached)
    if not fetch:
        return False, ''

    ok, res = _telegram_api_request(clean, 'getMe', timeout=10)
    if not ok:
        return False, str(res)
    username = str((res.get('result') or {}).get('username') or '').strip().lstrip('@')
    if not username:
        return False, 'Bot Telegram này chưa có username công khai.'
    cache.set(cache_key, username, timeout=_BOT_USERNAME_TTL_SECONDS)
    return True, username


def _format_chat_label(chat: dict) -> str:
    chat_type = str(chat.get('type') or '')
    username = str(chat.get('username') or '').strip()
    if chat_type == 'private':
        full_name = ' '.join(p for p in (chat.get('first_name'), chat.get('last_name')) if p).strip()
        if full_name and username:
            return f'{full_name} (@{username})'
        return full_name or (f'@{username}' if username else f"Chat {chat.get('id')}")
    title = str(chat.get('title') or '').strip()
    return title or (f'@{username}' if username else f"Nhóm {chat.get('id')}")


def _chat_type_label(chat_type: str) -> str:
    return {
        'private': 'Cá nhân',
        'group': 'Nhóm',
        'supergroup': 'Siêu nhóm',
        'channel': 'Kênh',
    }.get(chat_type, chat_type or 'Chat')


def _extract_start_code(text: str) -> str:
    match = _START_COMMAND_RE.match((text or '').strip())
    return match.group(1) if match else ''


def _select_token_for_pairing(user, *, mode: str = 'auto', custom_token: str = '') -> tuple[str, bool]:
    profile = MonitoringProfile.for_user(user)
    system_token = (getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or '').strip()
    saved_custom = (profile.telegram_bot_token or '').strip()
    clean_custom = (custom_token or '').strip()

    if mode == 'system':
        return system_token, False
    if mode == 'custom':
        if clean_custom and not clean_custom.startswith('****'):
            return clean_custom, True
        return (saved_custom if saved_custom != system_token else ''), True
    if clean_custom and not clean_custom.startswith('****'):
        is_custom = bool(not system_token or clean_custom != system_token)
        return clean_custom, is_custom
    effective = effective_telegram_token(profile)
    return effective, bool(effective and effective != system_token)


def start_pairing(user, *, mode: str = 'auto', custom_token: str = '') -> tuple[bool, dict]:
    """Create a short-lived single-use /start pairing link & group command for the logged-in user."""
    token, using_custom = _select_token_for_pairing(user, mode=mode, custom_token=custom_token)
    if not token:
        return False, {'error': 'Hệ thống chưa cấu hình Telegram Bot Token mặc định và bạn cũng chưa nhập Bot Token riêng.'}

    ok, username_or_err = resolve_bot_username(token, fetch=True)
    if not ok:
        return False, {'error': username_or_err}

    code = f'fw_{secrets.token_hex(8)}'
    bot_id = token.split(':', 1)[0].strip()
    cache.set(
        f'tg_pair:{code}',
        {
            'user_id': user.pk,
            'token': token if using_custom else '',
            'token_hash': _token_hash(token),
            'using_custom': using_custom,
            'bot_username': username_or_err,
        },
        timeout=_PAIR_TTL_SECONDS,
    )
    return True, {
        'ok': True,
        'code': code,
        'bot_username': username_or_err,
        'using_custom': using_custom,
        'web_url': f'https://web.telegram.org/a/#{bot_id}' if bot_id.isdigit() else f'https://t.me/{username_or_err}',
        'personal_url': f'https://t.me/{username_or_err}?start={code}',
        'group_url': f'https://t.me/{username_or_err}?startgroup={code}',
        'group_command': f'/start {code}',
        'expires_seconds': _PAIR_TTL_SECONDS,
    }


def _apply_updates_batch(updates: list[dict], token: str) -> None:
    if not updates:
        return
    token_hash = _token_hash(token)
    system_token = (getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or '').strip()
    User = get_user_model()
    max_update_id = None

    for upd in updates:
        upd_id = upd.get('update_id')
        if isinstance(upd_id, int):
            max_update_id = upd_id if max_update_id is None else max(max_update_id, upd_id)

        msg = upd.get('message') or upd.get('edited_message') or upd.get('channel_post') or {}
        chat = msg.get('chat') or {}
        chat_id = chat.get('id')
        if chat_id is None:
            continue

        code = _extract_start_code(msg.get('text') or '')
        if not code:
            continue

        pending = cache.get(f'tg_pair:{code}')
        if not pending or pending.get('token_hash') != token_hash:
            continue

        target_user = User.objects.filter(pk=pending.get('user_id')).first()
        if not target_user:
            continue

        profile = MonitoringProfile.for_user(target_user)
        profile.telegram_chat_id = str(chat_id)
        profile.telegram_enabled = True
        if pending.get('using_custom') and pending.get('token'):
            profile.telegram_bot_token = pending['token']
        elif not pending.get('using_custom'):
            # Using shared system bot (env TELEGRAM_BOT_TOKEN); keep per-user override blank.
            profile.telegram_bot_token = ''
        profile.save()

        chat_label = _format_chat_label(chat)
        cache.delete(f'tg_pair:{code}')
        cache.set(
            f'tg_pair_done:{code}',
            {
                'user_id': target_user.pk,
                'chat_id': str(chat_id),
                'chat_label': chat_label,
                'chat_type_label': _chat_type_label(str(chat.get('type') or '')),
                'bot_username': pending.get('bot_username') or '',
            },
            timeout=_DONE_TTL_SECONDS,
        )

        _telegram_api_request(
            token,
            'sendMessage',
            {
                'chat_id': chat_id,
                'text': (
                    f'✅ <b>Kết nối ForestWatch thành công!</b>\n'
                    f'👤 Tài khoản web: <code>{html.escape(target_user.username)}</code>\n'
                    f'💬 Kênh nhận cảnh báo: <b>{html.escape(chat_label)}</b>\n\n'
                    f'Từ giờ các cảnh báo phát hiện khói rừng của tài khoản này sẽ tự động gửi về đây.'
                ),
                'parse_mode': 'HTML',
            },
            timeout=10,
        )

    if max_update_id is not None:
        cache.set(f'tg_offset:{token_hash}', max_update_id + 1, timeout=86400)


def check_pairing_status(user, code: str) -> tuple[bool, dict]:
    """Poll getUpdates safely on backend to complete /start pairing without exposing Bot Token."""
    clean_code = (code or '').strip()
    if not clean_code:
        return False, {'ok': False, 'paired': False, 'error': 'Thiếu mã kết nối.'}

    done = cache.get(f'tg_pair_done:{clean_code}')
    if done and done.get('user_id') == user.pk:
        return True, {'ok': True, 'paired': True, **done}

    pending = cache.get(f'tg_pair:{clean_code}')
    if not pending or pending.get('user_id') != user.pk:
        return False, {
            'ok': False,
            'paired': False,
            'expired': True,
            'error': 'Mã kết nối đã hết hạn hoặc không hợp lệ. Vui lòng tạo lại mã mới.',
        }

    if pending.get('using_custom'):
        token = (pending.get('token') or MonitoringProfile.for_user(user).telegram_bot_token or '').strip()
    else:
        token = (getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or '').strip()
    if not token:
        return False, {'ok': False, 'paired': False, 'error': 'Không tìm thấy Bot Token hợp lệ.'}

    token_hash = _token_hash(token)
    offset = cache.get(f'tg_offset:{token_hash}')
    params = {'limit': 50, 'timeout': 0, 'allowed_updates': ['message', 'edited_message', 'channel_post']}
    if isinstance(offset, int):
        params['offset'] = offset

    ok, res = _telegram_api_request(token, 'getUpdates', params, timeout=12)
    if not ok:
        return False, {'ok': False, 'paired': False, 'error': str(res)}

    _apply_updates_batch(list(res.get('result') or []), token)

    done = cache.get(f'tg_pair_done:{clean_code}')
    if done and done.get('user_id') == user.pk:
        return True, {'ok': True, 'paired': True, **done}
    return True, {'ok': True, 'paired': False}


def discover_custom_chats(user, custom_token: str = '') -> tuple[bool, dict]:
    """Discover recent chats/groups for a user-owned custom Bot Token (never on shared system bot)."""
    profile = MonitoringProfile.for_user(user)
    raw = (custom_token or '').strip()
    if raw and not raw.startswith('****'):
        token = raw
    else:
        token = (profile.telegram_bot_token or '').strip()

    if not token:
        return False, {'error': 'Vui lòng nhập Bot Token riêng của bạn trước khi tìm Chat ID.'}

    system_token = (getattr(settings, 'TELEGRAM_BOT_TOKEN', '') or '').strip()
    if system_token and token == system_token:
        return False, {
            'error': 'Bot mặc định của hệ thống dùng nút Kết nối 1 chạm (/start) để bảo vệ riêng tư giữa các tài khoản.',
        }

    ok, res = _telegram_api_request(token, 'getUpdates', {'limit': 50, 'timeout': 0}, timeout=12)
    if not ok:
        return False, {'error': str(res)}

    seen = {}
    for upd in reversed(list(res.get('result') or [])):
        msg = upd.get('message') or upd.get('edited_message') or upd.get('channel_post') or upd.get('my_chat_member') or {}
        chat = msg.get('chat') or {}
        chat_id = chat.get('id')
        if chat_id is None:
            continue
        cid = str(chat_id)
        if cid in seen:
            continue
        chat_type = str(chat.get('type') or '')
        seen[cid] = {
            'chat_id': cid,
            'label': _format_chat_label(chat),
            'type': chat_type,
            'type_label': _chat_type_label(chat_type),
        }
        if len(seen) >= 20:
            break

    return True, {'ok': True, 'chats': list(seen.values())}
