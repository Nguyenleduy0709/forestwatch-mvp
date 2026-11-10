"""Resolve public video links and relay bytes for browser frame capture."""
import http.client
import ipaddress
import json
import re
import shutil
import socket
import subprocess
import sys
from importlib.util import find_spec
from urllib.parse import parse_qs, quote, urljoin, urlsplit

from django.core import signing
from django.http import JsonResponse, StreamingHttpResponse
from django.urls import reverse


TOKEN_SALT = 'monitor.video'
MEDIA_TYPES = {
    'mp4': 'video/mp4', 'webm': 'video/webm', 'ogv': 'video/ogg',
    'ogg': 'video/ogg', 'mov': 'video/quicktime', 'm4v': 'video/mp4',
}
YOUTUBE_HOSTS = {'youtube.com', 'www.youtube.com', 'm.youtube.com', 'youtu.be'}


class VideoSourceError(ValueError):
    pass


def parse_video_url(url):
    try:
        parts = urlsplit(url)
        expected_port = {'https': 443, 'http': 80}.get(parts.scheme)
        if (expected_port and parts.hostname and parts.username is None
                and parts.port in (None, expected_port) and not re.search(r'[\s\\]', url)):
            return parts
    except ValueError:
        pass
    raise VideoSourceError('Cần liên kết video công khai bắt đầu bằng HTTP hoặc HTTPS.')


def youtube_id(parts):
    segments = parts.path.strip('/').split('/')
    if parts.hostname == 'youtu.be':
        candidate = segments[0]
    elif segments[0] == 'watch':
        candidate = parse_qs(parts.query).get('v', [''])[0]
    elif len(segments) == 2 and segments[0] in {'embed', 'shorts', 'live'}:
        candidate = segments[1]
    else:
        candidate = ''
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', candidate):
        raise VideoSourceError('Dán liên kết tới một video YouTube cụ thể.')
    return candidate


def resolve_youtube(video_id):
    if find_spec('yt_dlp'):
        command = [sys.executable, '-m', 'yt_dlp']
    elif executable := shutil.which('yt-dlp'):
        command = [executable]
    else:
        raise VideoSourceError('Máy chủ cần cài yt-dlp và Deno hoặc Node để mở YouTube.')
    formats = (
        'best[protocol^=http][protocol!*=dash][ext=mp4][height<=720]/'
        'bestvideo[protocol^=http][protocol!*=dash][ext=mp4][vcodec^=avc1][height<=720]/'
        'bestvideo[protocol^=http][protocol!*=dash][ext=webm][height<=720]'
    )
    runtime_args = ['--js-runtimes', 'node'] if shutil.which('node') else []
    try:
        result = subprocess.run([
            *command, '--ignore-config', '--no-playlist', '--skip-download', '--dump-single-json',
            *runtime_args, '--socket-timeout', '8', '--retries', '0', '--extractor-retries', '0',
            '--format', formats, f'https://www.youtube.com/watch?v={video_id}',
        ], stdin=subprocess.DEVNULL, capture_output=True, encoding='utf-8', timeout=40, check=True)
        info = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise VideoSourceError(
            'Không lấy được video từ YouTube. Hãy thử video công khai khác hoặc chọn tệp video.'
        ) from exc
    return playable_youtube_stream(info)


def playable_youtube_stream(info):
    checked = set()
    for item in [info, *reversed(info.get('formats', []))]:
        url = item.get('url')
        extension = item.get('ext')
        if (not url or url in checked or extension not in {'mp4', 'webm'}
                or item.get('protocol') not in {'https', 'http'} or item.get('has_drm')
                or item.get('vcodec') in {None, 'none'} or (item.get('height') or 0) > 720):
            continue
        if len(checked) >= 3:
            break
        try:
            if not parse_video_url(url).hostname.endswith('.googlevideo.com'):
                continue
            checked.add(url)
            headers = {name: value for name, value in item.get('http_headers', info.get('http_headers', {})).items()
                       if name.lower() in {'user-agent', 'referer', 'origin', 'accept-language'}}
            response, connection = open_video(url, {**headers, 'Range': 'bytes=0-63'})
            status = response.status
            response.close()
            connection.close()
            if status in {200, 206}:
                return url, headers, extension
        except (OSError, ValueError, http.client.HTTPException):
            continue
    raise VideoSourceError('YouTube đang giới hạn luồng video này. Hãy thử video khác hoặc chọn tệp video.')


def resolve_source(request):
    url = request.POST.get('url', '').strip()
    try:
        if len(url) > 2048:
            raise VideoSourceError('Liên kết quá dài.')
        parts = parse_video_url(url)
        is_youtube = parts.hostname in YOUTUBE_HOSTS
        headers = {}
        extension = parts.path.rsplit('.', 1)[-1].lower()
        if is_youtube:
            url, headers, extension = resolve_youtube(youtube_id(parts))
        else:
            public_addresses(parts)
    except (VideoSourceError, OSError) as exc:
        message = str(exc) if isinstance(exc, VideoSourceError) else 'Không tìm thấy máy chủ video.'
        return JsonResponse({'message': message}, status=400)
    token = signing.dumps({
        'owner': str(request.user.pk), 'url': url, 'headers': headers, 'ext': extension,
    }, salt=TOKEN_SALT, compress=True)
    return JsonResponse({
        'url': reverse('relay_video', args=[token]),
        'type': 'YouTube' if is_youtube else 'Liên kết video',
    })


def public_addresses(parts):
    port = 443 if parts.scheme == 'https' else 80
    addresses = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global or ip.is_multicast:
            raise VideoSourceError('Chỉ hỗ trợ liên kết video trên máy chủ công khai.')
    return addresses


def open_video(url, headers):
    for _ in range(5):
        parts = parse_video_url(url)
        addresses = public_addresses(parts)
        connection_type = http.client.HTTPSConnection if parts.scheme == 'https' else http.client.HTTPConnection
        connection = connection_type(parts.hostname, timeout=8)

        def connect_public(*_):
            # Use the validated IP for TCP while keeping the hostname for Host and TLS.
            for family, kind, protocol, _, sockaddr in addresses:
                sock = socket.socket(family, kind, protocol)
                try:
                    sock.settimeout(8)
                    sock.connect(sockaddr)
                    return sock
                except OSError:
                    sock.close()
            raise OSError('Public video host is unreachable')

        connection._create_connection = connect_public
        target = quote(parts.path or '/', safe="/!$&'()*+,;=:@%")
        if parts.query:
            target += '?' + quote(parts.query, safe="/?!$&'()*+,;=:@%[]")
        try:
            connection.request('GET', target, headers=headers)
            response = connection.getresponse()
        except Exception:
            connection.close()
            raise
        location = response.getheader('Location')
        if response.status in {301, 302, 303, 307, 308} and location:
            response.close()
            connection.close()
            url = urljoin(url, location)
        else:
            return response, connection
    raise VideoSourceError('Liên kết video chuyển hướng quá nhiều lần.')


class VideoBody:
    def __init__(self, response, connection):
        self.response = response
        self.connection = connection

    def __iter__(self):
        try:
            while chunk := self.response.read(128 * 1024):
                yield chunk
        finally:
            self.close()

    def close(self):
        self.response.close()
        self.connection.close()


def relay_source(request, token):
    try:
        source = signing.loads(token, salt=TOKEN_SALT, max_age=6 * 60 * 60)
        if source['owner'] != str(request.user.pk):
            return JsonResponse({'message': 'Nguồn video không thuộc phiên này.'}, status=403)
    except signing.BadSignature:
        return JsonResponse({'message': 'Liên kết phát đã hết hạn. Hãy mở lại nguồn.'}, status=403)

    headers = {**source['headers'], 'Accept-Encoding': 'identity'}
    if byte_range := request.headers.get('Range'):
        if not re.fullmatch(r'bytes=(?:\d{1,18}-\d{0,18}|-\d{1,18})', byte_range):
            return JsonResponse({'message': 'Khoảng byte không hợp lệ.'}, status=416)
        headers['Range'] = byte_range
    try:
        upstream, connection = open_video(source['url'], headers)
    except (OSError, ValueError, http.client.HTTPException):
        return JsonResponse({'message': 'Không kết nối được nguồn video.'}, status=502)

    body = VideoBody(upstream, connection)
    content_type = (upstream.getheader('Content-Type') or '').split(';', 1)[0].strip().lower()
    if content_type in {'', 'application/octet-stream', 'binary/octet-stream'}:
        content_type = MEDIA_TYPES.get(source['ext'], '')
    if upstream.status == 416:
        body.close()
        result = JsonResponse({'message': 'Khoảng byte ngoài dữ liệu video.'}, status=416)
        if content_range := upstream.getheader('Content-Range'):
            result['Content-Range'] = content_range
        return result
    if upstream.status not in {200, 206} or content_type not in MEDIA_TYPES.values():
        body.close()
        return JsonResponse({'message': 'Nguồn không trả về video hỗ trợ hoặc đã giới hạn truy cập.'}, status=415)

    result = StreamingHttpResponse(body, status=upstream.status, content_type=content_type)
    for name in ('Content-Length', 'Content-Range', 'Accept-Ranges'):
        if value := upstream.getheader(name):
            result[name] = value
    result['Cache-Control'] = 'private, no-store'
    result['Content-Security-Policy'] = "sandbox; default-src 'none'"
    return result
