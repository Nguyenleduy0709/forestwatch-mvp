from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import path
from monitor import views
from monitor.phone_camera import (
    create_phone_qr,
    phone_camera_frame,
    phone_camera_push,
    phone_camera_share,
    resolve_phone_camera,
)

urlpatterns = [
    path('', views.home, name='home'),
    path('admin/', admin.site.urls),
    path('register/', views.register, name='register'),
    path('login/', auth.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('auth/google/login/', views.google_login, name='google_login'),
    path('auth/google/callback/', views.google_callback, name='google_callback'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('analyze/', views.analyze, name='analyze'),
    path('runs/<uuid:pk>/', views.detail, name='detail'),
    path('runs/<uuid:pk>/image/<str:kind>/', views.evidence, name='evidence'),
    path('runs/captures/<uuid:pk>/<str:kind>/', views.capture_image, name='capture_image'),
    path('alerts/', views.alerts, name='alerts'),
    path('alerts/delete/', views.delete_alerts, name='delete_alerts'),
    path('alerts/<uuid:pk>/transition/', views.transition, name='transition'),
    path('control-room/', views.control_room, name='control_room'),
    path('phone-cam/<str:token>/', phone_camera_share, name='phone_camera_share'),
    path('api/scan-frame/', views.scan_frame, name='scan_frame'),
    path('api/screen-reports/', views.screen_reports_api, name='screen_reports_api'),
    path('api/video/resolve/', views.resolve_video, name='resolve_video'),
    path('api/video/media/<str:token>/', views.relay_video, name='relay_video'),
    path('api/phone-camera/qr/', create_phone_qr, name='create_phone_qr'),
    path('api/phone-camera/resolve/', resolve_phone_camera, name='resolve_phone_camera'),
    path('api/phone-camera/push/<str:token>/', phone_camera_push, name='phone_camera_push'),
    path('api/phone-camera/frame/<str:token>/', phone_camera_frame, name='phone_camera_frame'),
]
