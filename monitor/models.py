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
