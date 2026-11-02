from django.contrib import admin
from .models import Analysis, Alert, AlertHistory
admin.site.register([Analysis, Alert, AlertHistory])
