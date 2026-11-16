"""Live IP camera frame pulling (RTSP / HTTP snapshot), realistic live simulator, and fire weather risk."""
import http.client
import io
import ipaddress
import json
import math
import socket
import time
import urllib.parse
import urllib.request
from functools import lru_cache
from hashlib import blake2s
from urllib.parse import urlsplit

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFilter, ImageOps

MAX_SNAPSHOT_BYTES = 4 * 1024 * 1024


class LiveCameraError(ValueError):
    pass


def parse_exclusion_zones(raw):
    """Sanitize normalized ROI exclusion boxes [{"x", "y", "w", "h", "label"}]."""
    if not isinstance(raw, list):
        return []
    zones = []
    for item in raw[:20]:
        if not isinstance(item, dict):
            continue
        try:
            x = max(0.0, min(0.98, float(item.get('x', 0))))
            y = max(0.0, min(0.98, float(item.get('y', 0))))
            w = max(0.02, min(1.0 - x, float(item.get('w', 0))))
            h = max(0.02, min(1.0 - y, float(item.get('h', 0))))
        except (TypeError, ValueError):
            continue
        label = str(item.get('label') or 'Vùng loại trừ')[:48].strip()
        zones.append({'x': round(x, 4), 'y': round(y, 4), 'w': round(w, 4), 'h': round(h, 4), 'label': label})
    return zones


def validate_stream_host(hostname: str):
    """Ensure camera hostname is valid and never targets cloud metadata or unsafe loopback."""
    if not hostname:
        raise LiveCameraError('Thiếu địa chỉ máy chủ hoặc IP camera.')
    try:
        addresses = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise LiveCameraError(f'Không phân giải được địa chỉ camera ({hostname}).') from exc

    allow_lan = getattr(settings, 'ALLOW_LAN_CAMERAS', False)
    allowed_nets = getattr(settings, 'PHONE_CAMERA_NETWORKS', ())
    for addr in addresses:
        ip = ipaddress.ip_address(addr[4][0])
        # Always block link-local / cloud metadata (169.254.x.x), multicast, unspecified, loopback in remote URLs
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_loopback:
            raise LiveCameraError('Không cho phép kết nối tới địa chỉ nội bộ đặc biệt / metadata.')
        if ip.is_private:
            in_phone_net = isinstance(ip, ipaddress.IPv4Address) and any(ip in net for net in allowed_nets)
            if not (allow_lan or in_phone_net):
                raise LiveCameraError(
                    'Camera IP mạng nội bộ đang bị tắt trên máy chủ công cộng (bật ALLOW_LAN_CAMERAS=1 nếu chạy nội bộ).'
                )


@lru_cache(maxsize=2)
def _base_forest_image(width: int = 960, height: int = 540):
    forest_path = settings.FRONTEND_DIR / 'static' / 'img' / 'forest.jpg'
    if forest_path.is_file():
        try:
            with Image.open(forest_path) as img:
                return ImageOps.fit(img.convert('RGB'), (width, height), method=Image.Resampling.BILINEAR)
        except OSError:
            pass
    # Fallback procedural forest skyline
    canvas = Image.new('RGB', (width, height), '#10241b')
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, width, int(height * 0.42)), fill='#223b47')
    draw.polygon([(0, height), (0, int(height * 0.45)), (int(width * 0.35), int(height * 0.36)),
                  (int(width * 0.72), int(height * 0.47)), (width, int(height * 0.38)), (width, height)],
                 fill='#163322')
    return canvas


def generate_simulator_frame(station, tick=None):
    """
    Render a realistic live CCTV frame for testing without physical IP cameras.
    Returns (jpeg_bytes, etag, demo_smoke_active).
    """
    width, height = 960, 540
    base = _base_forest_image(width, height).copy()
    scenario = getattr(station, 'simulator_scenario', 'smoke_rise') or 'smoke_rise'
    if tick is None:
        tick = int(time.time() // 2)

    cycle = tick % 24
    demo_smoke_active = False

    if scenario == 'smoke_rise':
        # Clear forest ticks 0..2, evolving wildfire smoke plume ticks 3..20
        if 3 <= cycle <= 20:
            demo_smoke_active = True
            progress = min(1.0, (cycle - 2) / 10.0)
            smoke_layer = Image.new('RGBA', (width, height), (0, 0, 0, 0))
            sdraw = ImageDraw.Draw(smoke_layer)
            origin_x = int(width * 0.56)
            origin_y = int(height * 0.62)
            puffs = 8 + int(progress * 10)
            for idx in range(puffs):
                frac = idx / max(1, puffs - 1)
                drift_x = int(origin_x - frac * (90 * progress) + math.sin(tick * 0.6 + idx) * 14)
                drift_y = int(origin_y - frac * (height * 0.48 * progress))
                rx = int((18 + frac * 76) * (0.65 + 0.5 * progress))
                ry = int((14 + frac * 54) * (0.65 + 0.5 * progress))
                alpha = int((155 - frac * 65) * (0.6 + 0.4 * progress))
                shade = 212 - int(frac * 24)
                sdraw.ellipse((drift_x - rx, drift_y - ry, drift_x + rx, drift_y + ry),
                              fill=(shade, shade + 2, min(255, shade + 6), max(45, alpha)))
            # Warm ember ignition dot at base when plume rises strongly
            if progress > 0.35:
                sdraw.ellipse((origin_x - 7, origin_y - 4, origin_x + 7, origin_y + 5), fill=(255, 115, 30, 190))
            smoke_layer = smoke_layer.filter(ImageFilter.GaussianBlur(radius=9))
            base = Image.alpha_composite(base.convert('RGBA'), smoke_layer).convert('RGB')
    elif scenario == 'fog_false_positive':
        # Cloud/haze band along upper sky/horizon (y: 0.04..0.28) to validate ROI exclusion & persistence
        demo_smoke_active = True
        fog_layer = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        fdraw = ImageDraw.Draw(fog_layer)
        shift = (tick * 24) % (width + 200) - 100
        for i in range(4):
            cx = (shift + i * 240) % (width + 160) - 80
            cy = int(height * (0.14 + (i % 2) * 0.05))
            fdraw.ellipse((cx - 130, cy - 35, cx + 130, cy + 35), fill=(228, 236, 240, 165))
        fog_layer = fog_layer.filter(ImageFilter.GaussianBlur(radius=10))
        base = Image.alpha_composite(base.convert('RGBA'), fog_layer).convert('RGB')

    # Draw subtle CCTV OSD HUD banner
    draw = ImageDraw.Draw(base)
    now_str = timezone.localtime().strftime('%d/%m/%Y %H:%M:%S')
    ch = f'CAM {getattr(station, "channel_number", 1):02d}'
    name = getattr(station, 'name', 'Giả lập')[:32]
    draw.rectangle((0, 0, width, 28), fill=(8, 13, 10))
    draw.ellipse((12, 9, 22, 19), fill='#ef4444' if demo_smoke_active else '#10b981')
    draw.text((30, 8), f'LIVE SIM · {ch} · {name} · {now_str} · Kịch bản: {scenario}', fill='#ecf3ee')

    buf = io.BytesIO()
    base.save(buf, format='JPEG', quality=86)
    jpeg_bytes = buf.getvalue()
    tag = f'"{blake2s(jpeg_bytes, digest_size=12).hexdigest()}"'
    return jpeg_bytes, tag, demo_smoke_active


def _normalize_jpeg_bytes(raw_bytes: bytes) -> bytes:
    with Image.open(io.BytesIO(raw_bytes)) as img:
        img = ImageOps.exif_transpose(img).convert('RGB')
        if img.width > 1280 or img.height > 1280:
            img.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        img.save(out, format='JPEG', quality=85)
        return out.getvalue()


def fetch_http_snapshot(url: str) -> bytes:
    parts = urlsplit(url)
    if parts.scheme not in {'http', 'https'} or not parts.hostname:
        raise LiveCameraError('URL ảnh chụp camera phải bắt đầu bằng http:// hoặc https://.')
    validate_stream_host(parts.hostname)
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    path = parts.path or '/'
    if parts.query:
        path = f'{path}?{parts.query}'
    conn_cls = http.client.HTTPSConnection if parts.scheme == 'https' else http.client.HTTPConnection
    conn = conn_cls(parts.hostname, port=port, timeout=3.5)
    try:
        headers = {'Accept': 'image/jpeg,image/*', 'User-Agent': 'ForestWatch-CCTV/1.0'}
        if parts.username is not None:
            import base64
            token = base64.b64encode(f'{parts.username}:{parts.password or ""}'.encode()).decode('ascii')
            headers['Authorization'] = f'Basic {token}'
        conn.request('GET', path, headers=headers)
        resp = conn.getresponse()
        if resp.status != 200:
            raise LiveCameraError(f'Camera trả về mã lỗi HTTP {resp.status}.')
        data = resp.read(MAX_SNAPSHOT_BYTES + 1)
        if len(data) > MAX_SNAPSHOT_BYTES:
            raise LiveCameraError('Khung hình camera vượt quá 4 MB.')
        return _normalize_jpeg_bytes(data)
    except LiveCameraError:
        raise
    except Exception as exc:
        raise LiveCameraError('Không kết nối được tới camera HTTP snapshot.') from exc
    finally:
        conn.close()


def fetch_rtsp_snapshot(url: str) -> bytes:
    parts = urlsplit(url)
    if parts.scheme not in {'rtsp', 'rtsps'} or not parts.hostname:
        raise LiveCameraError('Địa chỉ RTSP không hợp lệ (ví dụ: rtsp://user:pass@192.168.1.50:554/stream1).')
    validate_stream_host(parts.hostname)
    try:
        import cv2
    except ImportError as exc:
        raise LiveCameraError(
            'Máy chủ cần thư viện OpenCV (opencv-python) để đọc trực tiếp luồng RTSP.'
        ) from exc

    cap = cv2.VideoCapture(url)
    try:
        if hasattr(cv2, 'CAP_PROP_BUFFERSIZE'):
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if hasattr(cv2, 'CAP_PROP_OPEN_TIMEOUT_MSEC'):
            cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 4000)
        if hasattr(cv2, 'CAP_PROP_READ_TIMEOUT_MSEC'):
            cap.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 4000)
        if not cap.isOpened():
            raise LiveCameraError('Không mở được luồng RTSP. Kiểm tra IP, cổng 554 hoặc mật khẩu camera.')
        ok, frame = cap.read()
        if not ok or frame is None:
            raise LiveCameraError('Camera RTSP chưa trả về khung hình.')
        h, w = frame.shape[:2]
        scale = min(1.0, 1280.0 / max(w, h, 1))
        if scale < 1.0:
            frame = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
        ok_enc, buf = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        if not ok_enc:
            raise LiveCameraError('Không nén được khung hình RTSP.')
        return buf.tobytes()
    finally:
        cap.release()


def fetch_live_station_frame(station):
    """Fetch latest JPEG bytes + ETag from a live station (simulator, HTTP snapshot, or RTSP)."""
    if station.source_type == 'simulator':
        jpeg_bytes, tag, demo_active = generate_simulator_frame(station)
        cache.set(f'sim_smoke:{station.pk}', demo_active, timeout=30)
        return jpeg_bytes, tag
    if station.source_type == 'http_snapshot':
        jpeg_bytes = fetch_http_snapshot(station.stream_url)
    elif station.source_type == 'rtsp':
        jpeg_bytes = fetch_rtsp_snapshot(station.stream_url)
    else:
        raise LiveCameraError('Loại nguồn này được phát trực tiếp trên trình duyệt.')
    tag = f'"{blake2s(jpeg_bytes, digest_size=12).hexdigest()}"'
    return jpeg_bytes, tag


def compute_fire_danger(temp_c: float, humidity: float, wind_kmh: float):
    """Compute Vietnam-adapted 5-tier wildfire danger index from weather telemetry."""
    # Simple interpretable fire weather score combining dryness, heat, and wind spread factor
    dryness = max(0.0, 100.0 - humidity)
    heat = max(0.0, temp_c - 15.0) * 1.4
    wind = min(40.0, wind_kmh) * 0.7
    index = round(dryness * 0.55 + heat + wind, 1)
    if index >= 78:
        return index, 5, 'Cấp V · Cực kỳ nguy hiểm', 'danger'
    if index >= 60:
        return index, 4, 'Cấp IV · Nguy hiểm', 'danger'
    if index >= 45:
        return index, 3, 'Cấp III · Cao', 'warning'
    if index >= 28:
        return index, 2, 'Cấp II · Trung bình', 'ok'
    return index, 1, 'Cấp I · Thấp', 'ok'


def fetch_fire_weather_risk(lat: float, lon: float):
    """Fetch live weather from Open-Meteo (no API key) with 10-minute cache and graceful offline fallback."""
    cache_key = f'fire_weather:{round(lat, 2)}:{round(lon, 2)}'
    cached = cache.get(cache_key)
    if cached:
        return cached
    url = (
        f'https://api.open-meteo.com/v1/forecast?'
        f'latitude={lat:.4f}&longitude={lon:.4f}&'
        f'current=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m&timezone=auto'
    )
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'ForestWatch-Weather/1.0'})
        with urllib.request.urlopen(req, timeout=4) as resp:
            payload = json.loads(resp.read().decode('utf-8'))
        cur = payload.get('current') or {}
        temp = float(cur.get('temperature_2m', 28.0))
        rh = float(cur.get('relative_humidity_2m', 65.0))
        wind = float(cur.get('wind_speed_10m', 8.0))
        wind_dir = float(cur.get('wind_direction_10m', 120.0))
        score, level, label, severity = compute_fire_danger(temp, rh, wind)
        data = {
            'available': True,
            'source': 'Open-Meteo Live',
            'temperature_c': round(temp, 1),
            'humidity_pct': round(rh),
            'wind_speed_kmh': round(wind, 1),
            'wind_direction_deg': round(wind_dir),
            'fire_index': score,
            'danger_level': level,
            'danger_label': label,
            'severity': severity,
        }
        cache.set(cache_key, data, timeout=600)
        return data
    except Exception:
        score, level, label, severity = compute_fire_danger(29.5, 62.0, 11.0)
        return {
            'available': False,
            'source': 'Ước lượng ngoại tuyến',
            'temperature_c': 29.5,
            'humidity_pct': 62,
            'wind_speed_kmh': 11.0,
            'wind_direction_deg': 135,
            'fire_index': score,
            'danger_level': level,
            'danger_label': label,
            'severity': severity,
        }
