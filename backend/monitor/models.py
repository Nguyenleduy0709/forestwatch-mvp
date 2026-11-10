import uuid
from django.conf import settings
from django.db import models

class Analysis(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
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


class SmokeEvent(models.Model):
    alert = models.OneToOneField(Alert, on_delete=models.CASCADE, related_name='smoke_event')
    source_key = models.UUIDField()
    source_kind = models.CharField(max_length=6)
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
