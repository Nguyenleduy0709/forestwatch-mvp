"""Live RTSP IP camera frame pulling and ROI exclusion validation."""
import ipaddress
import socket
from hashlib import blake2s
from urllib.parse import urlsplit

from django.conf import settings


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
    for addr in addresses:
        ip = ipaddress.ip_address(addr[4][0])
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_loopback:
            raise LiveCameraError('Không cho phép kết nối tới địa chỉ nội bộ đặc biệt / metadata.')
        if ip.is_private and not allow_lan:
            raise LiveCameraError(
                'Camera IP mạng nội bộ đang bị tắt trên máy chủ công cộng (bật ALLOW_LAN_CAMERAS=1 nếu chạy nội bộ).'
            )


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
    """Fetch latest JPEG bytes + ETag from a live RTSP camera station."""
    if station.source_type != 'rtsp':
        raise LiveCameraError('Loại nguồn này được phát trực tiếp trên trình duyệt.')
    jpeg_bytes = fetch_rtsp_snapshot(station.stream_url)
    tag = f'"{blake2s(jpeg_bytes, digest_size=12).hexdigest()}"'
    return jpeg_bytes, tag
