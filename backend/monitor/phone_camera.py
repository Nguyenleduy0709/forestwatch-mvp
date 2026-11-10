"""Read JPEG snapshots from a phone camera via QR Code live web share or local IP camera."""
import http.client
import io
import ipaddress
import socket
import threading
import time
from hashlib import blake2s
from urllib.parse import urlsplit
from uuid import uuid4

import qrcode
import qrcode.image.svg
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.http import HttpResponse, HttpResponseNotModified, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from PIL import Image, ImageOps

SALT = 'monitor.phone-camera'
MAX_JPEG = 2 * 1024 * 1024
LOCAL_RANGES = tuple(map(ipaddress.ip_network, ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')))
SNAPSHOT_PATHS = {'/shot.jpg', '/snapshot.jpg', '/snapshot.jpeg'}

_SESSION_LOCK = threading.Lock()
PHONE_SESSIONS = {}


def get_lan_ip():
    """Return the computer's primary LAN IPv4 address so phones on the same Wi-Fi can connect."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.5)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith('127.'):
                return ip
    except OSError:
        pass
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith('127.'):
                return ip
    except OSError:
        pass
    return '127.0.0.1'


def make_qr_svg(data_url: str) -> str:
    """Generate an inline SVG string for the given URL."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(data_url)
    qr.make(fit=True)
    img = qr.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode('utf-8')


def build_share_url(request, token: str, custom_origin: str = '') -> str:
    path = reverse('phone_camera_share', args=[token])
    if custom_origin:
        origin = custom_origin.strip().rstrip('/')
        if not origin.startswith(('http://', 'https://')):
            origin = f'http://{origin}'
        return f'{origin}{path}'
    host_header = request.get_host()
    port = '8000'
    if ':' in host_header:
        _, port = host_header.rsplit(':', 1)
    lan_ip = get_lan_ip()
    if host_header.startswith(('127.0.0.1', 'localhost')):
        return f'{request.scheme}://{lan_ip}:{port}{path}'
    return f'{request.scheme}://{host_header}{path}'


def camera_target(value):
    try:
        parts = urlsplit(value)
        host = ipaddress.IPv4Address(parts.hostname)
        path = '/shot.jpg' if parts.path in {'', '/'} else parts.path
        if (parts.scheme != 'http' or parts.port != 8080 or parts.username is not None
                or parts.query or parts.fragment or path not in SNAPSHOT_PATHS
                or not any(host in network for network in LOCAL_RANGES)):
            raise ValueError
    except ValueError as exc:
        raise ValueError('Nhập địa chỉ camera HTTP có IP nội bộ và cổng 8080, ví dụ http://192.168.1.20:8080.') from exc
    if not any(host in network for network in settings.PHONE_CAMERA_NETWORKS):
        raise ValueError('Điện thoại cần nằm trong dải Wi-Fi đã cấu hình cho ForestWatch.')
    return str(host), path


def fetch_snapshot(host, path):
    connection = http.client.HTTPConnection(host, port=8080, timeout=2)
    try:
        connection.request('GET', path, headers={'Accept': 'image/jpeg', 'Accept-Encoding': 'identity'})
        response = connection.getresponse()
        content_type = (response.getheader('Content-Type') or '').split(';', 1)[0].strip().lower()
        if response.status != 200 or content_type not in {'image/jpeg', 'image/jpg'}:
            raise ValueError('Camera chưa trả về ảnh JPEG. Hãy kiểm tra địa chỉ và bật phát trên điện thoại.')
        data = response.read(MAX_JPEG + 1)
        if len(data) > MAX_JPEG:
            raise ValueError('Khung hình vượt quá giới hạn. Hãy giảm độ phân giải hoặc chất lượng JPEG trên điện thoại.')
        with Image.open(io.BytesIO(data)) as image:
            if image.format != 'JPEG' or image.width * image.height > 2_100_000:
                raise ValueError('Hãy đặt camera ở độ phân giải tối đa 1920×1080.')
            image.verify()
        return data
    finally:
        connection.close()


@login_required
@require_POST
def create_phone_qr(request):
    """Create or refresh a QR Code phone camera sharing session for a monitoring slot."""
    session_id = request.POST.get('session_id', '').strip() or uuid4().hex
    custom_origin = request.POST.get('origin', '').strip()
    token = signing.dumps({
        'owner': str(request.user.pk),
        'mode': 'push',
        'session': session_id,
    }, salt=SALT)
    share_url = build_share_url(request, token, custom_origin)
    frame_url = reverse('phone_camera_frame', args=[token])
    return JsonResponse({
        'session_id': session_id,
        'token': token,
        'input_url': f'qr:{token}',
        'share_url': share_url,
        'frame_url': frame_url,
        'lan_ip': get_lan_ip(),
        'qr_svg': make_qr_svg(share_url),
    })


@login_required
@require_POST
def resolve_phone_camera(request):
    value = request.POST.get('url', '').strip()
    if value.startswith('qr:'):
        raw_token = value[3:].strip()
        if not raw_token or raw_token == 'new':
            session_id = uuid4().hex
            token = signing.dumps({
                'owner': str(request.user.pk),
                'mode': 'push',
                'session': session_id,
            }, salt=SALT)
        else:
            try:
                source = signing.loads(raw_token, salt=SALT)
                if source.get('owner') != str(request.user.pk) or source.get('mode') != 'push':
                    return JsonResponse({'message': 'Mã phiên camera điện thoại không thuộc tài khoản này.'}, status=403)
                token = raw_token
            except signing.BadSignature:
                return JsonResponse({'message': 'Mã phiên camera điện thoại không hợp lệ.'}, status=400)
        share_url = build_share_url(request, token)
        return JsonResponse({
            'url': reverse('phone_camera_frame', args=[token]),
            'token': token,
            'input_url': f'qr:{token}',
            'share_url': share_url,
            'qr_svg': make_qr_svg(share_url),
        })

    try:
        host, path = camera_target(value)
    except ValueError as exc:
        return JsonResponse({'message': str(exc)}, status=400)
    token = signing.dumps({'owner': str(request.user.pk), 'host': host, 'path': path}, salt=SALT)
    return JsonResponse({'url': reverse('phone_camera_frame', args=[token])})


@require_GET
def phone_camera_share(request, token):
    """Mobile web interface opened when scanning the QR code on a phone."""
    try:
        source = signing.loads(token, salt=SALT)
        if source.get('mode') != 'push' or 'session' not in source:
            raise signing.BadSignature
    except signing.BadSignature:
        return HttpResponse('Liên kết chia sẻ camera không hợp lệ hoặc đã hết hạn.', status=403)
    return render(request, 'phone_share.html', {
        'token': token,
        'session_id': source['session'][:8].upper(),
        'push_url': reverse('phone_camera_push', args=[token]),
    })


@csrf_exempt
@require_POST
def phone_camera_push(request, token):
    """Receive live camera frames pushed from the phone's browser."""
    try:
        source = signing.loads(token, salt=SALT)
        if source.get('mode') != 'push' or 'session' not in source:
            raise signing.BadSignature
    except signing.BadSignature:
        return JsonResponse({'message': 'Phiên truyền camera không hợp lệ.'}, status=403)

    raw_bytes = None
    if 'frame' in request.FILES:
        upload = request.FILES['frame']
        if upload.size > 6 * 1024 * 1024:
            return JsonResponse({'message': 'Ảnh quá lớn.'}, status=400)
        raw_bytes = upload.read()
    elif request.body:
        if len(request.body) > 6 * 1024 * 1024:
            return JsonResponse({'message': 'Dữ liệu quá lớn.'}, status=400)
        raw_bytes = request.body

    if not raw_bytes:
        return JsonResponse({'message': 'Không có dữ liệu khung hình.'}, status=400)

    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            img = ImageOps.exif_transpose(img).convert('RGB')
            if img.width > 1280 or img.height > 1280:
                img.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            img.save(out, format='JPEG', quality=82)
            jpeg_bytes = out.getvalue()
    except Exception:
        return JsonResponse({'message': 'Khung hình không hợp lệ.'}, status=400)

    tag = f'"{blake2s(jpeg_bytes, digest_size=12).hexdigest()}"'
    session_id = source['session']
    with _SESSION_LOCK:
        prev = PHONE_SESSIONS.get(session_id)
        seq = (prev['sequence'] + 1) if prev else 1
        PHONE_SESSIONS[session_id] = {
            'owner': source['owner'],
            'image': jpeg_bytes,
            'etag': tag,
            'sequence': seq,
            'updated_at': time.time(),
        }
    return JsonResponse({'status': 'ok', 'sequence': seq})


@login_required
@require_GET
def phone_camera_frame(request, token):
    try:
        source = signing.loads(token, salt=SALT)
    except signing.BadSignature:
        return JsonResponse({'message': 'Nguồn camera không hợp lệ.'}, status=403)
    if source['owner'] != str(request.user.pk):
        return JsonResponse({'message': 'Nguồn camera thuộc tài khoản khác.'}, status=403)

    if source.get('mode') == 'push':
        session_id = source.get('session', '')
        with _SESSION_LOCK:
            entry = PHONE_SESSIONS.get(session_id)
        if not entry:
            return JsonResponse({
                'waiting': True,
                'message': 'Đang chờ điện thoại quét mã QR và truyền hình về máy tính…',
            }, status=425)
        tag = entry['etag']
        if request.headers.get('If-None-Match') == tag:
            response = HttpResponseNotModified()
        else:
            response = HttpResponse(entry['image'], content_type='image/jpeg')
        response['ETag'] = tag
        response['Cache-Control'] = 'private, no-store'
        return response

    try:
        host, path = camera_target(f"http://{source['host']}:8080{source['path']}")
        image = fetch_snapshot(host, path)
    except ValueError as exc:
        return JsonResponse({'message': str(exc)}, status=502)
    except (OSError, SyntaxError, http.client.HTTPException, Image.DecompressionBombError):
        return JsonResponse({'message': 'Chưa nhận được hình. Kiểm tra Wi-Fi và trạng thái phát trên điện thoại.'}, status=502)
    tag = f'"{blake2s(image, digest_size=12).hexdigest()}"'
    if request.headers.get('If-None-Match') == tag:
        response = HttpResponseNotModified()
    else:
        response = HttpResponse(image, content_type='image/jpeg')
    response['ETag'] = tag
    response['Cache-Control'] = 'private, no-store'
    return response
