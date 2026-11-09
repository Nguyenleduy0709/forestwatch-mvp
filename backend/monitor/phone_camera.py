"""Read JPEG snapshots from a phone camera on an explicitly allowed local network."""
import http.client
import io
import ipaddress
from hashlib import blake2s
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.http import HttpResponse, HttpResponseNotModified, JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from PIL import Image

SALT = 'monitor.phone-camera'
MAX_JPEG = 1024 * 1024
LOCAL_RANGES = tuple(map(ipaddress.ip_network, ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')))
SNAPSHOT_PATHS = {'/shot.jpg', '/snapshot.jpg', '/snapshot.jpeg'}


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
            raise ValueError('Khung hình vượt quá 1 MB. Hãy giảm độ phân giải hoặc chất lượng JPEG trên điện thoại.')
        with Image.open(io.BytesIO(data)) as image:
            if image.format != 'JPEG' or image.width * image.height > 2_100_000:
                raise ValueError('Hãy đặt camera ở độ phân giải tối đa 1920×1080.')
            image.verify()
        return data
    finally:
        connection.close()


@login_required
@require_POST
def resolve_phone_camera(request):
    value = request.POST.get('url', '').strip()
    try:
        host, path = camera_target(value)
    except ValueError as exc:
        return JsonResponse({'message': str(exc)}, status=400)
    token = signing.dumps({'owner': str(request.user.pk), 'host': host, 'path': path}, salt=SALT)
    return JsonResponse({'url': reverse('phone_camera_frame', args=[token])})


@login_required
@require_GET
def phone_camera_frame(request, token):
    try:
        source = signing.loads(token, salt=SALT)
    except signing.BadSignature:
        return JsonResponse({'message': 'Nguồn camera không hợp lệ.'}, status=403)
    if source['owner'] != str(request.user.pk):
        return JsonResponse({'message': 'Nguồn camera thuộc tài khoản khác.'}, status=403)
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
