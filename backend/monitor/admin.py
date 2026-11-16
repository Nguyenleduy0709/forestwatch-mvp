from django.contrib import admin
from .models import (
    Alert,
    AlertHistory,
    Analysis,
    CameraStation,
    MonitoringProfile,
    NotificationLog,
    SmokeCapture,
    SmokeEvent,
)

admin.site.register([
    MonitoringProfile,
    CameraStation,
    Analysis,
    Alert,
    AlertHistory,
    SmokeEvent,
    SmokeCapture,
    NotificationLog,
])
