from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import path
from monitor import views

urlpatterns = [
    path('', views.home, name='home'),
    path('admin/', admin.site.urls),
    path('register/', views.register, name='register'),
    path('login/', auth.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('auth/google/login/', views.google_login, name='google_login'),
    path('auth/google/callback/', views.google_callback, name='google_callback'),
    path('auth/google/set-password/', views.google_set_password, name='google_set_password'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('analyze/', views.analyze, name='analyze'),
    path('runs/<uuid:pk>/', views.detail, name='detail'),
    path('runs/<uuid:pk>/image/<str:kind>/', views.evidence, name='evidence'),
    path('runs/captures/<uuid:pk>/<str:kind>/', views.capture_image, name='capture_image'),
    path('alerts/', views.alerts, name='alerts'),
    path('alerts/export/', views.export_alerts_csv, name='export_alerts_csv'),
    path('alerts/delete/', views.delete_alerts, name='delete_alerts'),
    path('alerts/<uuid:pk>/transition/', views.transition, name='transition'),
    path('control-room/', views.control_room, name='control_room'),
    path('settings/', views.settings_view, name='settings'),
    path('api/telegram/pair/start/', views.telegram_pair_start_api, name='telegram_pair_start_api'),
    path('api/telegram/pair/status/', views.telegram_pair_status_api, name='telegram_pair_status_api'),
    path('api/telegram/discover-chats/', views.telegram_discover_chats_api, name='telegram_discover_chats_api'),
    path('api/scan-frame/', views.scan_frame, name='scan_frame'),
    path('api/screen-reports/', views.screen_reports_api, name='screen_reports_api'),
    path('api/stations/', views.stations_api, name='stations_api'),
    path('api/stations/<uuid:pk>/', views.station_detail_api, name='station_detail_api'),
    path('api/live-camera/frame/<uuid:pk>/', views.live_camera_frame, name='live_camera_frame'),
    path('api/video/resolve/', views.resolve_video, name='resolve_video'),
    path('api/video/media/<str:token>/', views.relay_video, name='relay_video'),
    path('api/video/demo-stream/<str:stream_id>/', views.demo_stream, name='demo_stream'),
]
