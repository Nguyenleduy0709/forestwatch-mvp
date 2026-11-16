import base64
import csv
import io
import json
import logging
from math import isfinite
from time import perf_counter
from urllib.parse import quote
from uuid import NAMESPACE_URL, UUID, uuid5
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse, HttpResponseNotModified, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.urls.exceptions import NoReverseMatch
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST
from PIL import Image
from .forms import UploadForm, process_pil_image, fetch_image_from_url
from .inference import (
    EVIDENCE_CONFIDENCE,
    SMOKE_LABELS,
    apply_exclusion_zones,
    current_model_name,
    draw_exclusion_zones,
    filter_excluded_detections,
    predict,
    qualifies_for_evidence,
)
from .live_cameras import (
    LiveCameraError,
    fetch_fire_weather_risk,
    fetch_live_station_frame,
    parse_exclusion_zones,
)
from .models import (
    Analysis,
    Alert,
    AlertHistory,
    CameraStation,
    MonitoringProfile,
    NotificationLog,
    SmokeCapture,
    SmokeEvent,
)
from .notifications import (
    cleanup_old_captures,
    dispatch_alert_notifications,
    send_test_notification,
)
from .smoke_events import capture_summary, event_summary, purge_source_alerts, track_smoke
from .video_sources import relay_source, resolve_source, serve_demo_stream
from .auth_views import (
    register,
    google_login,
    google_callback,
    google_set_password,
    logout_view,
)

def home(request):
    return render(request, 'home.html')

def context():
    model_name = current_model_name()
    return {'mode': settings.INFERENCE_MODE, 'model_name': model_name,
            'reference_enabled': settings.INFERENCE_MODE == 'fosp' and model_name.lower().startswith('e3_concat')}

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
    simulate = form.cleaned_data.get('simulate_smoke', False)
    try:
        detections, annotated = predict(
            form.normalized_image,
            simulate,
        )
    except RuntimeError as exc:
        messages.error(request, str(exc))
        return redirect('dashboard')
    except Exception:
        logging.getLogger(__name__).exception('Inference failed')
        messages.error(request, 'Phân tích thất bại. Kiểm tra trọng số, bộ nhớ và log trên máy chạy ứng dụng.')
        return redirect('dashboard')
    run = Analysis(owner=request.user, source=form.cleaned_data['source'], mode=settings.INFERENCE_MODE,
                   model_name=current_model_name(),
                   detections=detections, elapsed_ms=round((perf_counter()-started)*1000))
    for field, image in [('original', form.normalized_image), ('annotated', annotated)]:
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=90)
        getattr(run, field).save(f'{run.id}.jpg', ContentFile(buffer.getvalue()), save=False)
    profile = MonitoringProfile.for_user(request.user)
    created_alert = None
    with transaction.atomic():
        run.save()
        if any(
            qualifies_for_evidence(
                d,
                simulated=simulate and settings.INFERENCE_MODE == 'demo',
                min_confidence=profile.min_confidence,
                min_coverage=profile.min_smoke_coverage,
            )
            for d in detections
        ):
            created_alert = Alert.objects.create(analysis=run)
    if created_alert:
        dispatch_alert_notifications(
            created_alert,
            base_url=request.build_absolute_uri('/'),
            source_key=str(run.source),
        )
    return redirect('detail', pk=run.pk)

@login_required
def detail(request, pk):
    _ensure_captures_for_user(request.user)
    run = get_object_or_404(Analysis.objects.select_related('alert__smoke_event'), pk=pk, owner=request.user)
    alert = getattr(run, 'alert', None)
    event = getattr(alert, 'smoke_event', None)
    captures = list(SmokeCapture.objects.filter(owner=request.user, alert=alert).order_by('-created_at')[:60]) if alert else []
    screen_captures = list(
        SmokeCapture.objects.filter(
            Q(source_key=event.source_key) if event else Q(source=run.source),
            owner=request.user,
        ).order_by('-created_at')[:60]
    )
    return render(request, 'detail.html', {
        **context(), 'run': run, 'form': UploadForm(), 'event': event,
        'captures': captures, 'screen_captures': screen_captures,
    })

@login_required
def evidence(request, pk, kind):
    run = get_object_or_404(Analysis, pk=pk, owner=request.user)
    if kind in {'original', 'annotated'}:
        file = getattr(run, kind)
    elif kind in {'peak_original', 'peak_annotated'}:
        event = get_object_or_404(SmokeEvent, alert__analysis=run)
        file = getattr(event, kind)
    else:
        raise Http404
    if not file:
        raise Http404
    try:
        response = FileResponse(
            file.open('rb'), content_type='image/jpeg', as_attachment=request.GET.get('download') == '1',
            filename=f'forestwatch-{run.pk}-{kind}.jpg',
        )
    except FileNotFoundError as exc:
        raise Http404 from exc
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
def capture_image(request, pk, kind):
    if kind not in {'original', 'annotated'}:
        raise Http404
    capture = get_object_or_404(SmokeCapture, pk=pk, owner=request.user)
    file = getattr(capture, kind)
    if not file:
        raise Http404
    try:
        response = FileResponse(
            file.open('rb'), content_type='image/jpeg', as_attachment=request.GET.get('download') == '1',
            filename=f'forestwatch-capture-{capture.pk}-{kind}.jpg',
        )
    except FileNotFoundError as exc:
        raise Http404 from exc
    response['Cache-Control'] = 'private, no-store'
    return response


def _ensure_captures_for_user(user):
    """Backfill SmokeCapture records for any pre-existing Alerts so every screen's history is complete."""
    missing = list(
        Alert.objects.filter(analysis__owner=user, captures__isnull=True)
        .select_related('analysis', 'smoke_event')[:100]
    )
    if not missing:
        return
    for alert in missing:
        run = alert.analysis
        ev = getattr(alert, 'smoke_event', None)
        dets = run.detections or []
        score = max((float(d.get('mask_coverage', d.get('confidence')) or 0) for d in dets), default=0.0)
        conf = max((float(d.get('confidence') or 0) for d in dets), default=0.0)
        cap = SmokeCapture.objects.create(
            event=ev,
            alert=alert,
            owner=user,
            source=run.source,
            source_key=ev.source_key if ev else None,
            position=ev.first_position if ev else None,
            score=score,
            confidence=conf,
            detections=dets,
            elapsed_ms=run.elapsed_ms,
            original=run.original.name if run.original else '',
            annotated=run.annotated.name if run.annotated else '',
        )
        SmokeCapture.objects.filter(pk=cap.pk).update(created_at=alert.created_at)
        # Only create a peak capture if it represents a genuinely distinct frame and timestamp
        if (ev and ev.peak_annotated and ev.peak_original and
            ev.peak_original.name and run.original and ev.peak_original.name != run.original.name and
            ev.peak_position is not None and ev.first_position is not None and
            abs(ev.peak_position - ev.first_position) > 0.6):
            peak_cap = SmokeCapture.objects.create(
                event=ev,
                alert=alert,
                owner=user,
                source=run.source,
                source_key=ev.source_key,
                position=ev.peak_position,
                score=ev.peak_score or score,
                confidence=conf,
                detections=dets,
                elapsed_ms=run.elapsed_ms,
                original=ev.peak_original.name,
                annotated=ev.peak_annotated.name,
            )
            if ev.peak_at:
                SmokeCapture.objects.filter(pk=peak_cap.pk).update(created_at=ev.peak_at)


@login_required
def alerts(request):
    _ensure_captures_for_user(request.user)
    all_items = Alert.objects.filter(analysis__owner=request.user)
    all_captures = SmokeCapture.objects.filter(owner=request.user)
    status = request.GET.get('status', '')
    selected_source = request.GET.get('source', '').strip()

    alert_sources = list(all_items.order_by().values_list('analysis__source', flat=True).distinct())
    capture_sources = list(all_captures.order_by().values_list('source', flat=True).distinct())
    sources = list(dict.fromkeys(capture_sources + alert_sources))

    if not selected_source and sources:
        selected_source = sources[0]

    screen_tabs = []
    for src in sources:
        src_caps = all_captures.filter(source=src)
        if status in Alert.Status.values:
            src_caps = src_caps.filter(alert__status=status)
        cap_count = src_caps.count()
        latest_obj = src_caps.order_by('-created_at').first()
        tab_url = f"{reverse('alerts')}?source={quote(src)}" + (f"&status={status}" if status in Alert.Status.values else "")
        screen_tabs.append({
            'source': src,
            'capture_count': cap_count,
            'alert_count': all_items.filter(analysis__source=src).count(),
            'latest_at': latest_obj.created_at if latest_obj else None,
            'url': tab_url,
            'active': src == selected_source,
        })

    items = all_items.select_related('analysis', 'smoke_event')
    captures_qs = all_captures.select_related('alert', 'alert__analysis', 'event')
    if status in Alert.Status.values:
        items = items.filter(status=status)
        captures_qs = captures_qs.filter(alert__status=status)
    if selected_source:
        items = items.filter(analysis__source=selected_source)
        captures_qs = captures_qs.filter(source=selected_source)

    screen_captures = list(captures_qs.order_by('-created_at')[:150])
    try:
        delete_alerts_url = reverse('delete_alerts')
    except NoReverseMatch:
        delete_alerts_url = None

    return render(request, 'alerts.html', {
        **context(), 'alerts': items[:100], 'status': status,
        'selected_source': selected_source, 'sources': sources,
        'screen_tabs': screen_tabs,
        'screen_captures': screen_captures,
        'delete_alerts_url': delete_alerts_url,
        'pending_count': all_items.filter(status=Alert.Status.PENDING).count(),
        'statuses': Alert.Status.choices,
    })

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


@login_required
@require_POST
def delete_alerts(request):
    """Delete selected captures/alerts or all captures/alerts matching screen/status."""
    alert_ids = request.POST.getlist('alert_ids')
    single_id = request.POST.get('alert_id')
    capture_ids = request.POST.getlist('capture_ids')
    single_capture_id = request.POST.get('capture_id')
    delete_all = request.POST.get('delete_all') in ('true', '1', 'on')
    status_filter = request.POST.get('status', '').strip()
    source_filter = request.POST.get('source', '').strip()
    source_key_param = request.POST.get('source_key', '').strip()

    if single_id and single_id not in alert_ids:
        alert_ids.append(single_id)
    if single_capture_id and single_capture_id not in capture_ids:
        capture_ids.append(single_capture_id)

    if not alert_ids and not capture_ids and not delete_all and request.body:
        try:
            payload = json.loads(request.body)
            if isinstance(payload.get('alert_ids'), list):
                alert_ids = payload['alert_ids']
            elif payload.get('alert_id'):
                alert_ids = [payload['alert_id']]
            if isinstance(payload.get('capture_ids'), list):
                capture_ids = payload['capture_ids']
            elif payload.get('capture_id'):
                capture_ids = [payload['capture_id']]
            if payload.get('delete_all'):
                delete_all = True
            if payload.get('status'):
                status_filter = payload['status']
            if payload.get('source'):
                source_filter = payload['source']
            if payload.get('source_key'):
                source_key_param = payload['source_key']
        except (ValueError, KeyError):
            pass

    derived_source_key = None
    if source_key_param:
        try:
            derived_source_key = uuid5(NAMESPACE_URL, f'forestwatch:{request.user.pk}:{UUID(str(source_key_param))}')
        except (ValueError, TypeError):
            try:
                derived_source_key = UUID(str(source_key_param))
            except (ValueError, TypeError):
                derived_source_key = None

    if delete_all and (source_filter or derived_source_key):
        deleted_count = purge_source_alerts(request.user, source=source_filter, key=derived_source_key)
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
            return JsonResponse({'success': True, 'deleted_count': deleted_count, 'message': f'Đã xóa toàn bộ cảnh báo của nguồn này thành công.'})
        messages.success(request, f'Đã xóa toàn bộ cảnh báo của nguồn này thành công.')
        if source_filter:
            return redirect(f"{reverse('alerts')}?source={quote(source_filter)}")
        return redirect('alerts')

    if capture_ids and not alert_ids and not delete_all:
        caps_qs = SmokeCapture.objects.filter(owner=request.user, pk__in=capture_ids).select_related('alert__analysis', 'event')
        caps_to_delete = list(caps_qs)
        count = len(caps_to_delete)
        affected_alert_ids = {cap.alert_id for cap in caps_to_delete if cap.alert_id}
        with transaction.atomic():
            for cap in caps_to_delete:
                run = cap.alert.analysis if cap.alert_id else None
                shared_orig = run and run.original and cap.original and (run.original.name == cap.original.name)
                shared_anno = run and run.annotated and cap.annotated and (run.annotated.name == cap.annotated.name)
                if cap.original and not shared_orig:
                    cap.original.delete(save=False)
                if cap.annotated and not shared_anno:
                    cap.annotated.delete(save=False)
                cap.delete()
            for a_id in affected_alert_ids:
                if not SmokeCapture.objects.filter(alert_id=a_id).exists():
                    alert_obj = Alert.objects.filter(pk=a_id, analysis__owner=request.user).select_related('analysis', 'smoke_event').first()
                    if alert_obj:
                        if hasattr(alert_obj, 'smoke_event') and alert_obj.smoke_event:
                            ev = alert_obj.smoke_event
                            if ev.peak_original:
                                ev.peak_original.delete(save=False)
                            if ev.peak_annotated:
                                ev.peak_annotated.delete(save=False)
                        analysis = alert_obj.analysis
                        if analysis.original:
                            analysis.original.delete(save=False)
                        if analysis.annotated:
                            analysis.annotated.delete(save=False)
                        analysis.delete()

        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
            return JsonResponse({'success': True, 'deleted_count': count, 'message': f'Đã xóa {count} bản ghi quét thành công.'})
        messages.success(request, f'Đã xóa {count} bản ghi quét thành công.')
        if source_filter:
            return redirect(f"{reverse('alerts')}?source={quote(source_filter)}")
        referrer = request.META.get('HTTP_REFERER')
        if referrer and '/alerts/' in referrer:
            return redirect(referrer)
        return redirect('alerts')

    qs = Alert.objects.filter(analysis__owner=request.user)

    if delete_all:
        if status_filter in Alert.Status.values:
            qs = qs.filter(status=status_filter)
        if source_filter:
            qs = qs.filter(analysis__source=source_filter)
    elif alert_ids:
        qs = qs.filter(pk__in=alert_ids)
    else:
        if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
            return JsonResponse({'success': False, 'message': 'Chưa chọn cảnh báo nào để xóa.'}, status=400)
        messages.warning(request, 'Chưa chọn cảnh báo nào để xóa.')
        return redirect('alerts')

    alerts_to_delete = list(qs.select_related('analysis', 'smoke_event').prefetch_related('captures'))
    count = len(alerts_to_delete)

    with transaction.atomic():
        for alert in alerts_to_delete:
            for capture in alert.captures.all():
                if capture.original:
                    capture.original.delete(save=False)
                if capture.annotated:
                    capture.annotated.delete(save=False)
            if hasattr(alert, 'smoke_event') and alert.smoke_event:
                ev = alert.smoke_event
                if ev.peak_original:
                    ev.peak_original.delete(save=False)
                if ev.peak_annotated:
                    ev.peak_annotated.delete(save=False)
            analysis = alert.analysis
            if analysis.original:
                analysis.original.delete(save=False)
            if analysis.annotated:
                analysis.annotated.delete(save=False)
            analysis.delete()
        if delete_all and source_filter:
            orphan_caps = SmokeCapture.objects.filter(owner=request.user, source=source_filter)
            for cap in orphan_caps:
                if cap.original:
                    cap.original.delete(save=False)
                if cap.annotated:
                    cap.annotated.delete(save=False)
            orphan_caps.delete()

    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.content_type == 'application/json':
        return JsonResponse({'success': True, 'deleted_count': count, 'message': f'Đã xóa {count} cảnh báo thành công.'})

    messages.success(request, f'Đã xóa {count} cảnh báo thành công.')
    next_url = request.POST.get('next')
    if next_url:
        return redirect(next_url)
    referrer = request.META.get('HTTP_REFERER')
    if referrer and '/alerts/' in referrer:
        return redirect(referrer)
    return redirect('alerts')


@login_required
@require_GET
def screen_reports_api(request):
    """Return continuous smoke captures and alert events for a specific monitoring screen."""
    _ensure_captures_for_user(request.user)
    raw_key = request.GET.get('source_key', '').strip()
    source_name = request.GET.get('source', '').strip()
    alert_id = request.GET.get('alert_id', '').strip()

    captures_qs = SmokeCapture.objects.filter(owner=request.user).select_related('alert', 'alert__analysis')
    alerts_qs = Alert.objects.filter(analysis__owner=request.user).select_related('analysis', 'smoke_event')

    key_filter = Q()
    if raw_key:
        try:
            parsed_uuid = UUID(raw_key)
            derived_key = uuid5(NAMESPACE_URL, f'forestwatch:{request.user.pk}:{parsed_uuid}')
            key_filter |= Q(source_key__in=[parsed_uuid, derived_key])
        except ValueError:
            pass

    if key_filter and source_name:
        captures_qs = captures_qs.filter(key_filter | Q(source=source_name))
        alerts_qs = alerts_qs.filter(Q(smoke_event__source_key__in=[parsed_uuid, derived_key]) | Q(analysis__source=source_name))
    elif key_filter:
        captures_qs = captures_qs.filter(key_filter)
        alerts_qs = alerts_qs.filter(smoke_event__source_key__in=[parsed_uuid, derived_key])
    elif source_name:
        captures_qs = captures_qs.filter(source=source_name)
        alerts_qs = alerts_qs.filter(analysis__source=source_name)
    elif alert_id:
        captures_qs = captures_qs.filter(alert_id=alert_id)
        alerts_qs = alerts_qs.filter(pk=alert_id)

    total_captures = captures_qs.count()
    total_alerts = alerts_qs.count()
    captures_list = list(captures_qs[:100])
    alerts_list = list(alerts_qs[:30])

    seen_caps = set()
    unique_captures = []
    for c in captures_list:
        pos_key = round(c.position, 1) if c.position is not None else None
        c_key = (pos_key, c.original.name if c.original else '', str(c.alert_id) if c.alert_id else '')
        if c_key not in seen_caps:
            seen_caps.add(c_key)
            unique_captures.append(capture_summary(c))

    resolved_source = source_name or (captures_list[0].source if captures_list else (alerts_list[0].analysis.source if alerts_list else ''))
    alerts_url = f"{reverse('alerts')}?source={quote(resolved_source)}" if resolved_source else reverse('alerts')

    return JsonResponse({
        'source': resolved_source,
        'total_captures': len(unique_captures),
        'total_alerts': total_alerts,
        'alerts_url': alerts_url,
        'captures': unique_captures,
        'events': [event_summary(a, a.smoke_event) for a in alerts_list if hasattr(a, 'smoke_event') and a.smoke_event],
    })


@login_required
def control_room(request):
    alerts = Alert.objects.filter(analysis__owner=request.user)
    recent = alerts.filter(smoke_event__isnull=False).select_related('analysis', 'smoke_event')[:12]
    profile = MonitoringProfile.for_user(request.user)
    stations = [_serialize_station(s) for s in CameraStation.objects.filter(owner=request.user)]
    return render(request, 'control_room.html', {
        **context(), 'recent_events': [event_summary(alert, alert.smoke_event) for alert in recent],
        'evidence_threshold': round(profile.min_confidence * 100),
        'pending_count': alerts.filter(status=Alert.Status.PENDING).count(),
        'profile': profile,
        'server_stations': stations,
    })


@login_required
@require_POST
def resolve_video(request):
    return resolve_source(request)


@login_required
@require_GET
def relay_video(request, token):
    return relay_source(request, token)


@login_required
@require_GET
def demo_stream(request, stream_id):
    return serve_demo_stream(request, stream_id)


@login_required
@require_POST
def scan_frame(request):
    """API endpoint to scan a single CCTV / Video / Live Camera frame for forest smoke."""
    source = request.POST.get('source', 'CAM 01 - Trạm Cột 1').strip()[:120]
    simulate_smoke = request.POST.get('simulate_smoke') in ('true', '1', 'on')
    save_alert = request.POST.get('save_alert') in ('true', '1', 'on')
    video_replayed = request.POST.get('video_replayed') in ('true', '1', 'on')
    raw_source_key = (request.POST.get('source_key') or '').strip()
    source_key = None
    raw_uuid = None
    kind = request.POST.get('source_kind')
    position = None
    valid_kinds = {'image', 'video', 'url', 'webcam', 'phone', 'simulator', 'rtsp', 'hls', 'http_snapshot'}
    if raw_source_key:
        try:
            raw_uuid = UUID(raw_source_key)
            source_key = uuid5(NAMESPACE_URL, f'forestwatch:{request.user.pk}:{raw_uuid}')
            if kind not in valid_kinds:
                raise ValueError
            if kind in {'video', 'url'} and request.POST.get('position'):
                position = float(request.POST['position'])
                if not isfinite(position) or position < 0:
                    raise ValueError
        except ValueError:
            return JsonResponse({'message': 'Thông tin nguồn hoặc vị trí video không hợp lệ.'}, status=400)

    profile = MonitoringProfile.for_user(request.user)
    station = None
    station_id = (request.POST.get('station_id') or '').strip()
    if station_id:
        station = CameraStation.objects.filter(pk=station_id, owner=request.user).first()
    if station is None and raw_uuid is not None:
        station = CameraStation.objects.filter(owner=request.user, source_key=raw_uuid).first()

    # Check simulator smoke flag in demo mode
    if not simulate_smoke and kind == 'simulator' and station and settings.INFERENCE_MODE == 'demo':
        simulate_smoke = bool(cache.get(f'sim_smoke:{station.pk}'))

    image = None
    if 'image' in request.FILES:
        try:
            image = process_pil_image(request.FILES['image'])
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': f'Lỗi ảnh: {e}'}, status=400)
    elif 'image_b64' in request.POST:
        try:
            b64_str = request.POST['image_b64']
            if ',' in b64_str:
                b64_str = b64_str.split(',', 1)[1]
            raw_bytes = base64.b64decode(b64_str)
            image = process_pil_image(io.BytesIO(raw_bytes))
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': f'Lỗi giải mã base64: {e}'}, status=400)
    elif 'image_url' in request.POST and request.POST['image_url'].strip():
        try:
            raw_bytes = fetch_image_from_url(request.POST['image_url'].strip())
            image = process_pil_image(io.BytesIO(raw_bytes))
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': f'Lỗi tải ảnh từ liên kết URL: {e}'}, status=400)

    if not image:
        return JsonResponse({'status': 'error', 'message': 'Không có dữ liệu khung hình ảnh.'}, status=400)

    reference = None
    if upload := request.FILES.get('reference'):
        try:
            if upload.size > 2 * 1024 * 1024:
                raise ValueError
            reference = process_pil_image(upload)
        except (ValueError, OSError, Image.DecompressionBombWarning, Image.DecompressionBombError):
            return JsonResponse({'message': 'Ảnh nền cần là ảnh hợp lệ và không quá 2 MB.'}, status=400)

    exclusion_zones = station.exclusion_zones if (station and station.exclusion_zones) else []
    if request.POST.get('exclusion_zones'):
        try:
            exclusion_zones = parse_exclusion_zones(json.loads(request.POST['exclusion_zones']))
        except (ValueError, TypeError):
            pass

    inference_input = apply_exclusion_zones(image, exclusion_zones) if exclusion_zones else image
    started = perf_counter()
    try:
        detections, annotated = (
            predict(inference_input, simulate_smoke, reference=reference)
            if reference else predict(inference_input, simulate_smoke)
        )
    except RuntimeError as exc:
        return JsonResponse({'status': 'error', 'message': str(exc)}, status=503)
    except Exception:
        logging.getLogger(__name__).exception('CCTV inference failed')
        return JsonResponse({'status': 'error', 'message': 'Lỗi phân tích khung hình.'}, status=500)

    # Restore clean original image behind annotations when ROI masking shaded regions
    if exclusion_zones:
        detections = filter_excluded_detections(detections, exclusion_zones, image.width, image.height)
        annotated = draw_exclusion_zones(annotated, exclusion_zones)

    elapsed_ms = round((perf_counter() - started) * 1000)
    has_smoke = any(d.get('label') in SMOKE_LABELS for d in detections)
    is_simulated_demo = simulate_smoke and settings.INFERENCE_MODE == 'demo'
    eligible = [
        detection for detection in detections
        if qualifies_for_evidence(
            detection,
            simulated=is_simulated_demo,
            min_confidence=profile.min_confidence,
            min_coverage=profile.min_smoke_coverage,
        )
    ]
    model_name = current_model_name()

    moving = kind != 'image' and request.POST.get('moving') == '1'
    required_streak = max(1, int(profile.persistence_frames or 1)) if moving else 1
    if eligible and kind in {'video', 'url'}:
        max_conf = max((float(d.get('confidence') or 0) for d in eligible), default=0.0)
        max_cov = max((float(d.get('mask_coverage') or 0) for d in eligible), default=0.0)
        if max_conf >= 0.75 and max_cov >= 1.5:
            required_streak = 1
    streak = 0
    suspected = False
    confirmed_eligible = eligible
    if source_key and required_streak > 1:
        streak_key = f'smoke_streak:{request.user.pk}:{source_key}'
        if eligible:
            streak = int(cache.get(streak_key) or 0) + 1
            cache.set(streak_key, streak, timeout=120)
            if streak < required_streak:
                suspected = True
                confirmed_eligible = []
        else:
            cache.delete(streak_key)

    # Allow UI override of preserve_replay_history via form flag if toggled in Control Room
    preserve_replay = profile.preserve_replay_history
    if request.POST.get('preserve_replay') in ('1', 'true'):
        preserve_replay = True
    elif request.POST.get('preserve_replay') in ('0', 'false'):
        preserve_replay = False

    alert_id = None
    evidence_url = None
    event = None
    event_created = False
    is_live_stream = kind in {'simulator', 'rtsp', 'http_snapshot', 'phone', 'webcam'} or (
        station is not None and any(sid in (station.stream_url or '') for sid in ('wSp-pUDcFQI', 'RWoYegPgRF4', 'TtUgnRqARUE', 'FZNDqZJvc5w'))
    )
    capture_interval = int(profile.capture_interval_seconds or (8 if is_live_stream else 0))

    if source_key:
        event, event_created = track_smoke(
            owner=request.user, source=source, key=source_key, kind=kind, position=position,
            moving=moving, mode=settings.INFERENCE_MODE,
            model_name=model_name,
            detections=confirmed_eligible, images=(image, annotated), elapsed_ms=elapsed_ms,
            video_replayed=video_replayed,
            station=station,
            preserve_replay_history=preserve_replay,
            min_capture_interval_seconds=capture_interval,
        )
        if event:
            alert_id, evidence_url = event['id'], event['url']
    elif save_alert and confirmed_eligible:
        run = Analysis(
            owner=request.user,
            station=station,
            source=source,
            mode=settings.INFERENCE_MODE,
            model_name=model_name,
            detections=confirmed_eligible,
            elapsed_ms=elapsed_ms,
        )
        for field, img in [('original', image), ('annotated', annotated)]:
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=85)
            getattr(run, field).save(f'{run.id}.jpg', ContentFile(buf.getvalue()), save=False)
        with transaction.atomic():
            run.save()
            alert = Alert.objects.create(analysis=run)
            alert_id = str(alert.id)
        evidence_url = reverse('detail', args=[run.pk])

    # Update live camera station status & dispatch Telegram/Email/Webhook alerts
    if station:
        station.last_heartbeat_at = timezone.now()
        station.last_error = ''
        station.status = CameraStation.HealthStatus.ALERTING if confirmed_eligible else CameraStation.HealthStatus.ONLINE
        station.save(update_fields=['last_heartbeat_at', 'last_error', 'status', 'updated_at'])

    if alert_id and (event_created or (save_alert and confirmed_eligible and not source_key)):
        alert_obj = Alert.objects.filter(pk=alert_id).select_related('analysis', 'analysis__owner').first()
        if alert_obj:
            dispatch_alert_notifications(
                alert_obj,
                station=station,
                base_url=request.build_absolute_uri('/'),
                source_key=str(source_key or source),
            )

    return JsonResponse({
        'status': 'success',
        'detections': detections,
        'has_smoke': has_smoke,
        'suspected': suspected,
        'streak': streak,
        'required_streak': required_streak,
        'alert': bool(confirmed_eligible),
        'alert_id': alert_id,
        'evidence_url': evidence_url,
        'event': event,
        'event_created': event_created,
        'elapsed_ms': elapsed_ms,
        'mode': settings.INFERENCE_MODE,
        'model_name': model_name,
        'image_width': image.width,
        'image_height': image.height,
    })


def _serialize_station(station: CameraStation):
    open_event = station.smoke_events.filter(closed=False).select_related('alert').first()
    return {
        'id': str(station.pk),
        'channel_number': station.channel_number,
        'source_key': station.source_key.hex,
        'name': station.name,
        'location_name': station.location_name,
        'source_type': station.source_type,
        'stream_url': station.masked_stream_url(),
        'simulator_scenario': station.simulator_scenario,
        'latitude': station.latitude,
        'longitude': station.longitude,
        'azimuth': station.azimuth,
        'fov_degrees': station.fov_degrees,
        'range_km': station.range_km,
        'exclusion_zones': station.exclusion_zones or [],
        'status': station.status,
        'last_heartbeat': timezone.localtime(station.last_heartbeat_at).strftime('%H:%M:%S %d/%m') if station.last_heartbeat_at else None,
        'last_error': station.last_error,
        'maps_url': station.maps_url(),
        'live_frame_url': reverse('live_camera_frame', args=[station.pk]),
        'active_alert_url': reverse('detail', args=[open_event.alert.analysis_id]) if open_event else None,
    }


@login_required
def stations_api(request):
    """List all user CameraStations or upsert a station configuration."""
    if request.method == 'GET':
        stations = CameraStation.objects.filter(owner=request.user).order_by('channel_number')
        return JsonResponse({'stations': [_serialize_station(s) for s in stations]})
    if request.method != 'POST':
        return JsonResponse({'message': 'Phương thức không hỗ trợ.'}, status=405)

    try:
        payload = json.loads(request.body.decode('utf-8')) if request.content_type == 'application/json' else request.POST
    except ValueError:
        return JsonResponse({'message': 'Dữ liệu JSON không hợp lệ.'}, status=400)

    try:
        channel_number = max(1, min(10, int(payload.get('channel_number', 1))))
    except (TypeError, ValueError):
        return JsonResponse({'message': 'Số kênh camera không hợp lệ.'}, status=400)

    name = str(payload.get('name') or f'CAM {channel_number:02d}').strip()[:120]
    source_type = str(payload.get('source_type') or 'video').strip()
    if source_type not in CameraStation.SourceType.values:
        return JsonResponse({'message': 'Loại nguồn camera không hợp lệ.'}, status=400)

    station = CameraStation.objects.filter(owner=request.user, channel_number=channel_number).first()
    if station is None:
        station = CameraStation(owner=request.user, channel_number=channel_number)

    station.name = name
    station.source_type = source_type
    station.location_name = str(payload.get('location_name', station.location_name or '')).strip()[:160]
    scenario = str(payload.get('simulator_scenario', station.simulator_scenario or 'smoke_rise')).strip()
    if scenario in CameraStation.SimulatorScenario.values:
        station.simulator_scenario = scenario

    if 'source_key' in payload and payload.get('source_key'):
        try:
            station.source_key = UUID(str(payload.get('source_key')))
        except ValueError:
            pass

    raw_stream = str(payload.get('stream_url', '')).strip()
    if raw_stream and ':****@' not in raw_stream:
        station.stream_url = raw_stream[:1000]
    elif source_type not in ('rtsp', 'hls', 'http_snapshot', 'url'):
        station.stream_url = ''

    for coord_field, min_v, max_v in (('latitude', -90.0, 90.0), ('longitude', -180.0, 180.0)):
        if coord_field in payload:
            raw_c = str(payload.get(coord_field, '')).strip()
            if raw_c == '':
                setattr(station, coord_field, None)
            else:
                try:
                    val = float(raw_c)
                    if not isfinite(val) or val < min_v or val > max_v:
                        raise ValueError
                    setattr(station, coord_field, round(val, 6))
                except ValueError:
                    return JsonResponse({'message': f'Giá trị {coord_field} không hợp lệ.'}, status=400)

    for num_field, min_v, max_v, default_v in (('azimuth', 0.0, 360.0, 0.0), ('fov_degrees', 10.0, 360.0, 60.0), ('range_km', 0.2, 50.0, 5.0)):
        if num_field in payload and str(payload.get(num_field, '')).strip() != '':
            try:
                val = float(payload.get(num_field))
                if isfinite(val):
                    setattr(station, num_field, max(min_v, min(max_v, val)))
            except (TypeError, ValueError):
                setattr(station, num_field, default_v)

    if 'exclusion_zones' in payload:
        zones_raw = payload.get('exclusion_zones')
        if isinstance(zones_raw, str):
            try:
                zones_raw = json.loads(zones_raw)
            except ValueError:
                zones_raw = []
        station.exclusion_zones = parse_exclusion_zones(zones_raw)

    station.is_active = True
    station.save()
    return JsonResponse({'station': _serialize_station(station)})


@login_required
def station_detail_api(request, pk):
    station = get_object_or_404(CameraStation, pk=pk, owner=request.user)
    if request.method == 'DELETE' or (request.method == 'POST' and request.POST.get('_method') == 'DELETE'):
        station.delete()
        return JsonResponse({'deleted': True})
    if request.method == 'GET':
        return JsonResponse({'station': _serialize_station(station)})
    return JsonResponse({'message': 'Phương thức không hỗ trợ.'}, status=405)


@login_required
@require_GET
def live_camera_frame(request, pk):
    """Fetch a live JPEG frame from a CameraStation (Simulator, RTSP, or HTTP Snapshot)."""
    station = get_object_or_404(CameraStation, pk=pk, owner=request.user)
    try:
        jpeg_bytes, tag = fetch_live_station_frame(station)
    except LiveCameraError as exc:
        station.status = CameraStation.HealthStatus.OFFLINE
        station.last_error = str(exc)[:300]
        station.save(update_fields=['status', 'last_error', 'updated_at'])
        return JsonResponse({'message': str(exc)}, status=502)

    station.last_heartbeat_at = timezone.now()
    station.last_error = ''
    if station.status != CameraStation.HealthStatus.ALERTING:
        station.status = CameraStation.HealthStatus.ONLINE
    station.save(update_fields=['last_heartbeat_at', 'last_error', 'status', 'updated_at'])

    if request.headers.get('If-None-Match') == tag:
        resp = HttpResponseNotModified()
    else:
        resp = HttpResponse(jpeg_bytes, content_type='image/jpeg')
    resp['ETag'] = tag
    resp['Cache-Control'] = 'private, no-store'
    return resp


@login_required
def map_view(request):
    """Tactical GIS Wildfire Map & Fire Weather Danger Risk view."""
    stations = list(CameraStation.objects.filter(owner=request.user).order_by('channel_number'))
    serialized = [_serialize_station(s) for s in stations]
    geo_stations = [s for s in stations if s.latitude is not None and s.longitude is not None]
    if geo_stations:
        center_lat = sum(s.latitude for s in geo_stations) / len(geo_stations)
        center_lon = sum(s.longitude for s in geo_stations) / len(geo_stations)
    else:
        # Default central Vietnam forest coordinates (Phong Nha / Bach Ma region)
        center_lat, center_lon = 16.20, 107.85
    weather = fetch_fire_weather_risk(center_lat, center_lon)
    recent_alerts = Alert.objects.filter(analysis__owner=request.user).select_related('analysis', 'analysis__station')[:15]
    return render(request, 'map.html', {
        **context(),
        'stations': serialized,
        'weather': weather,
        'center_lat': center_lat,
        'center_lon': center_lon,
        'recent_alerts': recent_alerts,
    })


@login_required
@require_GET
def weather_risk_api(request):
    try:
        lat = float(request.GET.get('lat', 16.20))
        lon = float(request.GET.get('lon', 107.85))
    except (TypeError, ValueError):
        return JsonResponse({'message': 'Tọa độ không hợp lệ.'}, status=400)
    return JsonResponse(fetch_fire_weather_risk(lat, lon))


@login_required
def settings_view(request):
    """Enterprise configuration: AI sensitivity, false-positive persistence, notifications, and retention."""
    profile = MonitoringProfile.for_user(request.user)
    test_results = None

    if request.method == 'POST':
        action = request.POST.get('action', 'save')
        if action == 'preset_enterprise':
            profile.min_confidence = 0.45
            profile.min_smoke_coverage = 0.25
            profile.persistence_frames = 3
            profile.capture_interval_seconds = 3
            profile.alert_cooldown_seconds = 60
            profile.preserve_replay_history = True
            profile.save()
            messages.success(request, 'Đã áp dụng cấu hình Khuyến nghị Doanh nghiệp (Chống báo giả 3 khung liên tiếp).')
            return redirect('settings')
        if action == 'cleanup_now':
            deleted = cleanup_old_captures(owner=request.user, retention_days=profile.retention_days)
            messages.success(request, f'Đã dọn dẹp {deleted} ảnh quét cũ quá {profile.retention_days} ngày.')
            return redirect('settings')

        # Save standard settings fields
        profile.organization_name = request.POST.get('organization_name', '').strip()[:140]
        try:
            profile.min_confidence = max(0.15, min(0.95, float(request.POST.get('min_confidence', profile.min_confidence))))
            profile.min_smoke_coverage = max(0.0, min(50.0, float(request.POST.get('min_smoke_coverage', profile.min_smoke_coverage))))
            profile.persistence_frames = max(1, min(10, int(request.POST.get('persistence_frames', profile.persistence_frames))))
            profile.capture_interval_seconds = max(0, min(60, int(request.POST.get('capture_interval_seconds', profile.capture_interval_seconds))))
            profile.alert_cooldown_seconds = max(5, min(3600, int(request.POST.get('alert_cooldown_seconds', profile.alert_cooldown_seconds))))
            profile.retention_days = max(0, min(365, int(request.POST.get('retention_days', profile.retention_days))))
        except (TypeError, ValueError):
            messages.error(request, 'Một số tham số ngưỡng AI không hợp lệ.')
            return redirect('settings')

        profile.preserve_replay_history = request.POST.get('preserve_replay_history') in ('on', '1', 'true')
        profile.telegram_enabled = request.POST.get('telegram_enabled') in ('on', '1', 'true')
        bot_token = request.POST.get('telegram_bot_token', '').strip()[:160]
        if bot_token and not bot_token.startswith('****'):
            profile.telegram_bot_token = bot_token
        elif request.POST.get('clear_telegram_token') == '1':
            profile.telegram_bot_token = ''
        profile.telegram_chat_id = request.POST.get('telegram_chat_id', '').strip()[:64]

        profile.email_enabled = request.POST.get('email_enabled') in ('on', '1', 'true')
        profile.email_recipients = request.POST.get('email_recipients', '').strip()[:500]

        profile.webhook_enabled = request.POST.get('webhook_enabled') in ('on', '1', 'true')
        profile.webhook_url = request.POST.get('webhook_url', '').strip()[:500]
        profile.notify_on_offline = request.POST.get('notify_on_offline') in ('on', '1', 'true')
        profile.save()

        if action == 'test_notify':
            test_results = send_test_notification(profile, base_url=request.build_absolute_uri('/'))
            if not test_results:
                messages.warning(request, 'Vui lòng bật ít nhất một kênh (Telegram, Email hoặc Webhook) trước khi gửi thử.')
            else:
                ok_count = sum(1 for r in test_results if r['ok'])
                messages.info(request, f'Đã kiểm tra {len(test_results)} kênh thông báo: {ok_count}/{len(test_results)} thành công.')
        else:
            messages.success(request, 'Đã lưu cấu hình hệ thống & bộ lọc cảnh báo.')
            return redirect('settings')

    logs = NotificationLog.objects.filter(owner=request.user)[:20]
    capture_total = SmokeCapture.objects.filter(owner=request.user).count()
    stations = CameraStation.objects.filter(owner=request.user).order_by('channel_number')
    masked_token = ('****' + profile.telegram_bot_token[-6:]) if len(profile.telegram_bot_token) > 8 else profile.telegram_bot_token
    return render(request, 'settings.html', {
        **context(),
        'profile': profile,
        'masked_token': masked_token,
        'logs': logs,
        'capture_total': capture_total,
        'stations': stations,
        'test_results': test_results,
    })


@login_required
@require_GET
def export_alerts_csv(request):
    """Export verified alerts and evidence metadata to UTF-8 BOM CSV for Excel/compliance audit."""
    status_filter = request.GET.get('status', '').strip()
    source_filter = request.GET.get('source', '').strip()
    qs = Alert.objects.filter(analysis__owner=request.user).select_related('analysis', 'analysis__station', 'smoke_event').prefetch_related('history__actor')
    if status_filter in Alert.Status.values:
        qs = qs.filter(status=status_filter)
    if source_filter:
        qs = qs.filter(analysis__source=source_filter)

    response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
    stamp = timezone.localtime().strftime('%Y%m%d-%H%M')
    response['Content-Disposition'] = f'attachment; filename="forestwatch-alerts-{stamp}.csv"'
    writer = csv.writer(response)
    writer.writerow([
        'Mã cảnh báo', 'Thời gian phát hiện', 'Nguồn / Camera', 'Vị trí / Tọa độ GPS',
        'Trạng thái', 'Số khung có khói', 'Độ tin cậy tối đa (%)', 'Độ phủ khói (%)',
        'Lịch sử xác minh', 'Liên kết chi tiết',
    ])
    base = request.build_absolute_uri('/').rstrip('/')
    for alert in qs[:1000]:
        run = alert.analysis
        station = run.station
        ev = getattr(alert, 'smoke_event', None)
        dets = run.detections or []
        max_conf = round(max((float(d.get('confidence') or 0) for d in dets), default=0.0) * 100, 1)
        max_cov = round(max((float(d.get('mask_coverage') or 0) for d in dets), default=0.0), 2)
        loc = ''
        if station:
            loc = station.location_name or ''
            if station.latitude is not None and station.longitude is not None:
                loc = f'{loc} ({station.latitude:.5f}, {station.longitude:.5f})'.strip()
        notes = ' | '.join(
            f'{h.actor.username}: {h.old_status}->{h.new_status}' + (f' ({h.note})' if h.note else '')
            for h in alert.history.all()
        )
        writer.writerow([
            str(alert.pk),
            timezone.localtime(alert.created_at).strftime('%d/%m/%Y %H:%M:%S'),
            run.source,
            loc or 'Chưa gắn tọa độ',
            alert.get_status_display(),
            ev.positive_samples if ev else 1,
            max_conf,
            max_cov,
            notes,
            f"{base}{reverse('detail', args=[run.pk])}",
        ])
    return response
