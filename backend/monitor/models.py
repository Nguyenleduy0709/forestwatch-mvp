import uuid
from urllib.parse import urlsplit, urlunsplit
from django.conf import settings
from django.db import models


class MonitoringProfile(models.Model):
    """Per-user / enterprise detection tuning, anti-false-positive filters, and alert channels."""
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='monitoring_profile')
    organization_name = models.CharField(max_length=140, blank=True, default='')

    # Anti-false-positive & storage controls
    min_confidence = models.FloatField(default=0.40)
    min_smoke_coverage = models.FloatField(default=0.05)
    persistence_frames = models.PositiveSmallIntegerField(default=1)
    capture_interval_seconds = models.PositiveSmallIntegerField(default=0)
    alert_cooldown_seconds = models.PositiveSmallIntegerField(default=60)
    preserve_replay_history = models.BooleanField(default=False)
    retention_days = models.PositiveSmallIntegerField(default=30)

    # Out-of-app real-time notification channels
    telegram_enabled = models.BooleanField(default=False)
    telegram_bot_token = models.CharField(max_length=160, blank=True, default='')
    telegram_chat_id = models.CharField(max_length=64, blank=True, default='')
    email_enabled = models.BooleanField(default=False)
    email_recipients = models.CharField(max_length=500, blank=True, default='')
    webhook_enabled = models.BooleanField(default=False)
    webhook_url = models.URLField(max_length=500, blank=True, default='')
    notify_on_offline = models.BooleanField(default=True)

    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def for_user(cls, user):
        profile, _ = cls.objects.get_or_create(owner=user)
        return profile

    def __str__(self):
        return self.organization_name or f'Cấu hình · {self.owner.username}'


class CameraStation(models.Model):
    """Server-persisted camera node with geolocation, FOV, RTSP/Simulator source, and ROI exclusion zones."""

    class SourceType(models.TextChoices):
        RTSP = 'rtsp', 'Camera IP (RTSP)'
        HLS = 'hls', 'Luồng trực tiếp (HLS .m3u8)'
        HTTP_SNAPSHOT = 'http_snapshot', 'Camera IP (HTTP JPEG)'
        SIMULATOR = 'simulator', 'Giả lập Live Cam (Test)'
        VIDEO = 'video', 'Video từ máy'
        URL = 'url', 'Liên kết video'
        IMAGE = 'image', 'Ảnh tĩnh'
        PHONE = 'phone', 'Camera điện thoại (QR)'
        WEBCAM = 'webcam', 'Webcam / USB'

    class SimulatorScenario(models.TextChoices):
        SMOKE_RISE = 'smoke_rise', 'Khói rừng bùng phát dần'
        FOG_FALSE_POSITIVE = 'fog_false_positive', 'Sương mù / Mây trôi (Test chống báo giả)'
        CLEAR_FOREST = 'clear_forest', 'Rừng yên tĩnh 24/7'

    class HealthStatus(models.TextChoices):
        IDLE = 'IDLE', 'Chờ kết nối'
        ONLINE = 'ONLINE', 'Đang trực tuyến'
        ALERTING = 'ALERTING', 'Đang cảnh báo khói'
        OFFLINE = 'OFFLINE', 'Mất kết nối'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='camera_stations')
    channel_number = models.PositiveSmallIntegerField(default=1)
    source_key = models.UUIDField(default=uuid.uuid4, db_index=True)
    name = models.CharField(max_length=120)
    location_name = models.CharField(max_length=160, blank=True, default='')
    source_type = models.CharField(max_length=20, choices=SourceType.choices, default=SourceType.VIDEO)
    stream_url = models.CharField(max_length=1000, blank=True, default='')
    simulator_scenario = models.CharField(
        max_length=32, choices=SimulatorScenario.choices, default=SimulatorScenario.SMOKE_RISE
    )

    # Geospatial telemetry for incident maps & dispatch
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    azimuth = models.FloatField(default=0.0)
    fov_degrees = models.FloatField(default=60.0)
    range_km = models.FloatField(default=5.0)

    # Normalized exclusion boxes [{"x": 0.0, "y": 0.0, "w": 1.0, "h": 0.25, "label": "Bầu trời"}]
    exclusion_zones = models.JSONField(default=list, blank=True)

    status = models.CharField(max_length=16, choices=HealthStatus.choices, default=HealthStatus.IDLE)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True, default='')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['channel_number', 'created_at']
        constraints = [
            models.UniqueConstraint(fields=['owner', 'channel_number'], name='monitor_unique_owner_channel'),
        ]

    def masked_stream_url(self):
        if not self.stream_url:
            return ''
        try:
            parts = urlsplit(self.stream_url)
            if parts.password:
                netloc = parts.netloc.replace(f':{parts.password}@', ':****@', 1)
                return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
        except ValueError:
            pass
        return self.stream_url

    def maps_url(self):
        if self.latitude is not None and self.longitude is not None:
            return f'https://maps.google.com/?q={self.latitude:.6f},{self.longitude:.6f}'
        return ''

    def __str__(self):
        return f'CAM {self.channel_number:02d} · {self.name}'


class Analysis(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    station = models.ForeignKey(
        CameraStation, null=True, blank=True, on_delete=models.SET_NULL, related_name='analyses'
    )
    source = models.CharField(max_length=120, default='Ảnh tải lên')
    original = models.ImageField(upload_to='originals/%Y/%m/')
    annotated = models.ImageField(upload_to='results/%Y/%m/', blank=True)
    mode = models.CharField(max_length=12)
    model_name = models.CharField(max_length=128, blank=True, default='')
    detections = models.JSONField(default=list)
    elapsed_ms = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class Alert(models.Model):
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Chờ xác minh'
        CONFIRMED = 'CONFIRMED', 'Đã xác nhận'
        REJECTED = 'REJECTED', 'Đã từ chối'
        RESOLVED = 'RESOLVED', 'Đã xử lý'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    analysis = models.OneToOneField(Analysis, on_delete=models.CASCADE, related_name='alert')
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']


class AlertHistory(models.Model):
    alert = models.ForeignKey(Alert, on_delete=models.CASCADE, related_name='history')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    old_status = models.CharField(max_length=12)
    new_status = models.CharField(max_length=12)
    note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    @staticmethod
    def _label(value):
        return dict(Alert.Status.choices).get(value, value)

    @property
    def old_status_label(self):
        return self._label(self.old_status)

    @property
    def new_status_label(self):
        return self._label(self.new_status)


class SmokeEvent(models.Model):
    alert = models.OneToOneField(Alert, on_delete=models.CASCADE, related_name='smoke_event')
    station = models.ForeignKey(
        CameraStation, null=True, blank=True, on_delete=models.SET_NULL, related_name='smoke_events'
    )
    source_key = models.UUIDField()
    source_kind = models.CharField(max_length=20)
    first_position = models.FloatField(null=True)
    last_seen = models.DateTimeField()
    positive_samples = models.PositiveIntegerField(default=1)
    clear_samples = models.PositiveSmallIntegerField(default=0)
    closed = models.BooleanField(default=False)
    peak_score = models.FloatField(default=0)
    peak_position = models.FloatField(null=True)
    peak_at = models.DateTimeField(null=True)
    peak_original = models.ImageField(upload_to='events/peak-original/%Y/%m/', blank=True)
    peak_annotated = models.ImageField(upload_to='events/peak-annotated/%Y/%m/', blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['source_key'], condition=models.Q(closed=False), name='monitor_one_open_smoke_event',
            ),
        ]


class SmokeCapture(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event = models.ForeignKey(SmokeEvent, on_delete=models.CASCADE, related_name='captures', null=True, blank=True)
    alert = models.ForeignKey(Alert, on_delete=models.CASCADE, related_name='captures', null=True, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='smoke_captures')
    source = models.CharField(max_length=120, db_index=True)
    source_key = models.UUIDField(null=True, blank=True, db_index=True)
    position = models.FloatField(null=True, blank=True)
    score = models.FloatField(default=0)
    confidence = models.FloatField(default=0)
    detections = models.JSONField(default=list)
    elapsed_ms = models.PositiveIntegerField(default=0)
    original = models.ImageField(upload_to='captures/original/%Y/%m/')
    annotated = models.ImageField(upload_to='captures/annotated/%Y/%m/', blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']


class NotificationLog(models.Model):
    """Audit log of out-of-app notifications dispatched via Telegram, Email, or Webhook."""

    class Channel(models.TextChoices):
        TELEGRAM = 'telegram', 'Telegram'
        EMAIL = 'email', 'Email'
        WEBHOOK = 'webhook', 'Webhook'

    class DeliveryStatus(models.TextChoices):
        SENT = 'SENT', 'Đã gửi'
        FAILED = 'FAILED', 'Thất bại'
        SKIPPED = 'SKIPPED', 'Bỏ qua'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='notification_logs')
    alert = models.ForeignKey(Alert, null=True, blank=True, on_delete=models.CASCADE, related_name='notifications')
    channel = models.CharField(max_length=20, choices=Channel.choices)
    status = models.CharField(max_length=12, choices=DeliveryStatus.choices)
    target = models.CharField(max_length=255, blank=True, default='')
    detail = models.CharField(max_length=500, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
