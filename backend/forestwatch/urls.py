from django.contrib import admin
from django.contrib.auth import views as auth
from django.urls import path
from monitor import views

urlpatterns = [
    path('', views.home, name='home'), path('admin/', admin.site.urls),
    path('login/', auth.LoginView.as_view(template_name='registration/login.html'), name='login'),
    path('logout/', auth.LogoutView.as_view(), name='logout'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('analyze/', views.analyze, name='analyze'),
    path('runs/<uuid:pk>/', views.detail, name='detail'),
    path('runs/<uuid:pk>/image/<str:kind>/', views.evidence, name='evidence'),
    path('alerts/', views.alerts, name='alerts'),
    path('alerts/<uuid:pk>/transition/', views.transition, name='transition'),
]
