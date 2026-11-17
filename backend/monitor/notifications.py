"""Out-of-app real-time alert dispatching (Telegram, Email SMTP) & storage cleanup."""
import json
import logging
import threading
import urllib.error
import urllib.request
from datetime import timedelta
from email.mime.image import MIMEImage
from uuid import uuid4

from django.conf import settings
from django.core.cache import cache
from django.core.mail import EmailMultiAlternatives
from django.urls import reverse
from django.utils import timezone

from .models import Alert, MonitoringProfile, NotificationLog, SmokeCapture
from .telegram_bot import effective_telegram_token, telegram_urlopen

logger = logging.getLogger(__name__)


def _read_annotated_bytes(alert: Alert) -> bytes | None:
    field = alert.analysis.annotated or alert.analysis.original
    if not field:
        return None
    try:
        with field.open('rb') as handle:
            return handle.read()
    except OSError:
        return None


def _send_telegram_photo(*, bot_token: str, chat_id: str, caption: str, photo_bytes: bytes | None) -> tuple[bool, str]:
    token = (bot_token or '').strip()
    chat = (chat_id or '').strip()
    if not token or not chat:
        return False, 'Thiếu Bot Token hoặc Chat ID Telegram.'

    if photo_bytes:
        boundary = f'----ForestWatchTG{uuid4().hex}'
        parts = []
        for name, value in (('chat_id', chat), ('caption', caption), ('parse_mode', 'HTML')):
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode('utf-8')
            )
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="smoke_alert.jpg"\r\n'
                f'Content-Type: image/jpeg\r\n\r\n'
            ).encode('utf-8') + photo_bytes + b'\r\n'
        )
        parts.append(f'--{boundary}--\r\n'.encode('utf-8'))
        body = b''.join(parts)
        url = f'https://api.telegram.org/bot{token}/sendPhoto'
        headers = {'Content-Type': f'multipart/form-data; boundary={boundary}'}
    else:
        url = f'https://api.telegram.org/bot{token}/sendMessage'
        body = json.dumps({'chat_id': chat, 'text': caption, 'parse_mode': 'HTML'}).encode('utf-8')
        headers = {'Content-Type': 'application/json'}

    req = urllib.request.Request(url, data=body, headers=headers, method='POST')
    try:
        with telegram_urlopen(req, timeout=15) as resp:
            res_data = json.loads(resp.read().decode('utf-8'))
            if res_data.get('ok'):
                return True, 'Telegram đã gửi thành công.'
            return False, str(res_data.get('description') or 'Telegram từ chối yêu cầu.').replace(token, '***')
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode('utf-8', errors='ignore').replace(token, '***')[:240]
        return False, f'HTTP {exc.code}: {err_body}'
    except Exception as exc:
        return False, str(exc).replace(token, '***')[:240]


def _send_email_alert(*, recipients: str, subject: str, html_body: str, text_body: str, photo_bytes: bytes | None) -> tuple[bool, str]:
    emails = [e.strip() for e in (recipients or '').replace(';', ',').split(',') if '@' in e]
    if not emails:
        return False, 'Chưa cấu hình địa chỉ Email người nhận hợp lệ.'
    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=emails,
        )
        msg.attach_alternative(html_body, 'text/html')
        if photo_bytes:
            img = MIMEImage(photo_bytes, _subtype='jpeg')
            img.add_header('Content-ID', '<smoke_evidence>')
            img.add_header('Content-Disposition', 'inline', filename='smoke_evidence.jpg')
            msg.attach(img)
        sent_count = msg.send(fail_silently=False)
        return sent_count > 0, f'Đã gửi Email tới {len(emails)} địa chỉ.'
    except Exception as exc:
        return False, str(exc)[:240]


def _execute_dispatch(alert_id, station_id, base_url, is_test=False, profile_id=None, owner_id=None):
    from .models import CameraStation

    alert = Alert.objects.filter(pk=alert_id).select_related('analysis', 'analysis__owner').first() if alert_id else None
    if alert:
        owner = alert.analysis.owner
        profile = MonitoringProfile.for_user(owner)
        station = alert.analysis.station or (CameraStation.objects.filter(pk=station_id).first() if station_id else None)
    elif profile_id:
        profile = MonitoringProfile.objects.filter(pk=profile_id).select_related('owner').first()
        if not profile:
            return []
        owner = profile.owner
        station = None
    else:
        return []

    results = []
    timestamp_str = timezone.localtime(alert.created_at if alert else timezone.now()).strftime('%H:%M:%S %d/%m/%Y')
    source_name = alert.analysis.source if alert else (station.name if station else 'Kiểm thử hệ thống')
    dets = (alert.analysis.detections or []) if alert else [{'confidence': 0.88, 'mask_coverage': 4.5}]
    max_conf = max((float(d.get('confidence') or 0) for d in dets), default=0.0)
    max_cov = max((float(d.get('mask_coverage') or 0) for d in dets), default=0.0)
    effective_base = (getattr(settings, 'PUBLIC_BASE_URL', '') or base_url or '').strip().rstrip('/')
    detail_link = f"{effective_base}{reverse('detail', args=[alert.analysis_id])}" if (alert and effective_base) else ''
    link_line = f'🔗 Xem & Xác minh: {detail_link}\n' if detail_link else ''
    test_tag = '[KIỂM THỬ] ' if is_test else ''

    photo_bytes = _read_annotated_bytes(alert) if alert else None

    if profile.telegram_enabled:
        caption = (
            f'🚨 <b>{test_tag}CẢNH BÁO PHÁT HIỆN KHÓI RỪNG</b>\n'
            f'📷 <b>Nguồn:</b> {source_name}\n'
            f'📊 <b>Độ tin cậy:</b> {round(max_conf * 100, 1)}% · <b>Vùng khói:</b> {round(max_cov, 2)}%\n'
            f'⏰ <b>Thời gian:</b> {timestamp_str}\n'
            f'{link_line}'
        )
        ok, detail = _send_telegram_photo(
            bot_token=effective_telegram_token(profile),
            chat_id=profile.telegram_chat_id,
            caption=caption,
            photo_bytes=photo_bytes,
        )
        NotificationLog.objects.create(
            owner=owner, alert=alert, channel=NotificationLog.Channel.TELEGRAM,
            status=NotificationLog.DeliveryStatus.SENT if ok else NotificationLog.DeliveryStatus.FAILED,
            target=profile.telegram_chat_id[:64], detail=detail,
        )
        results.append({'channel': 'Telegram', 'ok': ok, 'detail': detail})

    if profile.email_enabled:
        subject = f'🚨 {test_tag}[ForestWatch] Phát hiện khói tại {source_name} ({timestamp_str})'
        text_body = (
            f'{test_tag}Cảnh báo phát hiện khói rừng\n'
            f'Nguồn: {source_name}\nThời gian: {timestamp_str}\n'
            f'Độ tin cậy: {round(max_conf * 100, 1)}% | Vùng khói: {round(max_cov, 2)}%\n'
            f'{link_line}'
        )
        img_tag = '<p><img src="cid:smoke_evidence" style="max-width:640px;border-radius:8px;"></p>' if photo_bytes else ''
        html_body = (
            f'<div style="font-family:Arial,sans-serif;background:#0b120e;color:#eef3ef;padding:24px;border-radius:10px;">'
            f'<h2 style="color:#ef4444;margin-top:0;">🚨 {test_tag}Cảnh báo phát hiện khói</h2>'
            f'<p><b>Nguồn giám sát:</b> {source_name}</p>'
            f'<p><b>Thời gian:</b> {timestamp_str}</p>'
            f'<p><b>Độ tin cậy AI:</b> {round(max_conf * 100, 1)}% · <b>Độ phủ khói:</b> {round(max_cov, 2)}%</p>'
            f'{f"<p><a href={detail_link} style=color:#10b981;font-weight:bold;>Mở trang xác minh cảnh báo ↗</a></p>" if detail_link else ""}'
            f'{img_tag}</div>'
        )
        ok, detail = _send_email_alert(
            recipients=profile.email_recipients, subject=subject,
            html_body=html_body, text_body=text_body, photo_bytes=photo_bytes,
        )
        NotificationLog.objects.create(
            owner=owner, alert=alert, channel=NotificationLog.Channel.EMAIL,
            status=NotificationLog.DeliveryStatus.SENT if ok else NotificationLog.DeliveryStatus.FAILED,
            target=profile.email_recipients[:255], detail=detail,
        )
        results.append({'channel': 'Email', 'ok': ok, 'detail': detail})

    return results


def dispatch_alert_notifications(alert: Alert, *, station=None, base_url: str = '', source_key: str = '', sync: bool = False):
    """Queue asynchronous out-of-app alerts while respecting per-source cooldown."""
    profile = MonitoringProfile.for_user(alert.analysis.owner)
    if not (profile.telegram_enabled or profile.email_enabled):
        return False

    cooldown = max(5, int(profile.alert_cooldown_seconds or 60))
    cd_key = f'alert_cooldown:{alert.analysis.owner_id}:{source_key or alert.analysis.source}'
    if not sync and cache.get(cd_key):
        return False
    cache.set(cd_key, True, timeout=cooldown)

    station_id = station.pk if station else None
    if sync:
        return _execute_dispatch(alert.pk, station_id, base_url, is_test=False)

    worker = threading.Thread(
        target=_execute_dispatch,
        args=(alert.pk, station_id, base_url),
        kwargs={'is_test': False},
        daemon=True,
    )
    worker.start()
    return True


def send_test_notification(profile: MonitoringProfile, base_url: str = ''):
    """Synchronously test all enabled notification channels and return delivery statuses."""
    return _execute_dispatch(None, None, base_url, is_test=True, profile_id=profile.pk, owner_id=profile.owner_id)


def cleanup_old_captures(owner=None, retention_days=None):
    """Delete expired non-confirmed SmokeCapture rows and image files to prevent disk exhaustion."""
    profiles = MonitoringProfile.objects.filter(owner=owner) if owner else MonitoringProfile.objects.all()
    if owner and not profiles.exists():
        profiles = [MonitoringProfile.for_user(owner)]

    deleted = 0
    now = timezone.now()
    for prof in profiles:
        days = int(retention_days if retention_days is not None else prof.retention_days)
        if days <= 0:
            continue
        cutoff = now - timedelta(days=days)
        qs = SmokeCapture.objects.filter(owner=prof.owner, created_at__lt=cutoff).exclude(alert__status=Alert.Status.CONFIRMED)
        for cap in qs.select_related('alert__analysis'):
            run = cap.alert.analysis if cap.alert_id else None
            shared_orig = run and run.original and cap.original and (run.original.name == cap.original.name)
            shared_anno = run and run.annotated and cap.annotated and (run.annotated.name == cap.annotated.name)
            if cap.original and not shared_orig:
                cap.original.delete(save=False)
            if cap.annotated and not shared_anno:
                cap.annotated.delete(save=False)
            cap.delete()
            deleted += 1
    return deleted
