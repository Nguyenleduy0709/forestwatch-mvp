import io
import logging
from time import perf_counter
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from .forms import UploadForm
from .inference import predict
from .models import Analysis, Alert, AlertHistory
from .auth_views import (
    register,
    google_login,
    google_callback,
    logout_view,
)

def home(request):
    return render(request, 'home.html')

def context():
    return {'mode': settings.INFERENCE_MODE}

@login_required
def dashboard(request):
    runs = Analysis.objects.filter(owner=request.user)
    alerts = Alert.objects.filter(analysis__owner=request.user)
    return render(request, 'dashboard.html', {**context(), 'form': UploadForm(), 'runs': runs[:6],
        'total': runs.count(), 'pending': alerts.filter(status='PENDING').count(),
        'confirmed': alerts.filter(status='CONFIRMED').count(), 'alert_count': alerts.count()})

@login_required
@require_POST
def analyze(request):
    form = UploadForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, ' '.join(str(e) for errors in form.errors.values() for e in errors))
        return redirect('dashboard')
    started = perf_counter()
    try:
        detections, annotated = predict(form.normalized_image, form.cleaned_data['simulate_smoke'])
    except RuntimeError as exc:
        messages.error(request, str(exc))
        return redirect('dashboard')
    except Exception:
        logging.getLogger(__name__).exception('Inference failed')
        messages.error(request, 'Phân tích thất bại. Kiểm tra trọng số, bộ nhớ và log trên máy chạy ứng dụng.')
        return redirect('dashboard')
    run = Analysis(owner=request.user, source=form.cleaned_data['source'], mode=settings.INFERENCE_MODE,
                   detections=detections, elapsed_ms=round((perf_counter()-started)*1000))
    for field, image in [('original', form.normalized_image), ('annotated', annotated)]:
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=90)
        getattr(run, field).save(f'{run.id}.jpg', ContentFile(buffer.getvalue()), save=False)
    with transaction.atomic():
        run.save()
        if detections:
            Alert.objects.create(analysis=run)
    return redirect('detail', pk=run.pk)

@login_required
def detail(request, pk):
    run = get_object_or_404(Analysis, pk=pk, owner=request.user)
    return render(request, 'detail.html', {**context(), 'run': run})

@login_required
def evidence(request, pk, kind):
    run = get_object_or_404(Analysis, pk=pk, owner=request.user)
    if kind not in {'original', 'annotated'}:
        raise Http404
    file = getattr(run, kind)
    if not file:
        raise Http404
    response = FileResponse(file.open('rb'), content_type='image/jpeg')
    response['Cache-Control'] = 'private, no-store'
    return response

@login_required
def alerts(request):
    items = Alert.objects.filter(analysis__owner=request.user).select_related('analysis')
    status = request.GET.get('status', '')
    if status in Alert.Status.values:
        items = items.filter(status=status)
    return render(request, 'alerts.html', {**context(), 'alerts': items[:100], 'status': status,
                                         'statuses': Alert.Status.choices})

@login_required
@require_POST
def transition(request, pk):
    alert = get_object_or_404(Alert, pk=pk, analysis__owner=request.user)
    target = request.POST.get('status', '')
    allowed = {'PENDING': {'CONFIRMED', 'REJECTED'}, 'CONFIRMED': {'RESOLVED'}}
    if target not in allowed.get(alert.status, set()):
        messages.error(request, 'Thao tác không hợp lệ với trạng thái hiện tại.')
        return redirect('detail', pk=alert.analysis_id)
    with transaction.atomic():
        changed = Alert.objects.filter(pk=pk, status=alert.status).update(status=target, updated_at=timezone.now())
        if changed:
            AlertHistory.objects.create(alert=alert, actor=request.user, old_status=alert.status,
                                        new_status=target, note=request.POST.get('note', '')[:500])
        else:
            messages.error(request, 'Trạng thái vừa thay đổi. Hãy tải lại trước khi thao tác.')
    return redirect('detail', pk=alert.analysis_id)
