"""Dispatch demo and trained model inference."""
from functools import lru_cache
from pathlib import Path
from django.conf import settings
from PIL import ImageDraw

EVIDENCE_CONFIDENCE = 0.40
SMOKE_LABELS = {'smoke', 'khoi', 'khói'}


def qualifies_for_evidence(detection, *, simulated=False):
    if detection.get('label') not in SMOKE_LABELS:
        return False
    confidence = detection.get('confidence')
    return simulated or (isinstance(confidence, (int, float)) and EVIDENCE_CONFIDENCE <= confidence <= 1)


def current_model_name():
    if settings.INFERENCE_MODE == 'fosp':
        return settings.FOSP_MODEL_PATH.name
    if settings.INFERENCE_MODE == 'yolo':
        return Path(settings.MODEL_PATH).name
    return ''


@lru_cache(maxsize=1)
def load_model(path):
    if not Path(path).is_file():
        raise RuntimeError('Chưa có trọng số model khói. Đặt best.pt vào weights/ và khởi động lại.')
    try:
        from ultralytics import YOLO
    except ImportError:
        raise RuntimeError('Chưa cài thư viện AI. Chạy: pip install -r requirements-ai.txt')
    model = YOLO(path)
    if str(getattr(model, 'task', '')) != 'detect':
        raise RuntimeError('MVP này cần model object detection, không phải model segmentation/classification.')
    names = {str(v).strip().lower() for v in model.names.values()}
    if not names.intersection(SMOKE_LABELS):
        raise RuntimeError('Model không có lớp smoke. Cần trọng số đã fine-tune cho phát hiện khói rừng.')
    return model

def predict(image, simulate=False, **kwargs):
    mode = settings.INFERENCE_MODE
    width, height = image.size
    if mode == 'demo':
        detections = []
        if simulate:
            detections.append({'label': 'smoke', 'confidence': None,
                               'box': [int(width*.36), int(height*.17), int(width*.67), int(height*.66)]})
    elif mode == 'yolo':
        model = load_model(settings.MODEL_PATH)
        result = model.predict(image, conf=settings.MODEL_CONFIDENCE, verbose=False)[0]
        detections = []
        for box in result.boxes:
            label = str(result.names[int(box.cls.item())]).lower().strip()
            if label in SMOKE_LABELS:
                detections.append({'label': label, 'confidence': float(box.conf.item()),
                                   'box': [round(float(v)) for v in box.xyxy[0].tolist()]})
    elif mode == 'fosp':
        from .fosp import predict_fosp
        reference = kwargs.get('reference')
        return predict_fosp(image, reference) if reference is not None else predict_fosp(image)
    else:
        raise RuntimeError('INFERENCE_MODE chỉ nhận demo, yolo hoặc fosp.')
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    for item in detections:
        box_color = '#ffb44a'
        text_color = '#142b23'

        draw.rectangle(item['box'], outline=box_color, width=max(3, width//350))
        label = f"SIMULATION - {item['label']}" if mode == 'demo' else f"{item['label']} {item['confidence']:.0%}"
        x, y = item['box'][:2]
        draw.rectangle((x, max(0, y-25), min(width, x+240), y), fill=box_color)
        draw.text((x+5, max(0, y-21)), label, fill=text_color)
    return detections, annotated
