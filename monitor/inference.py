"""Demo is explicitly synthetic. YOLO never silently falls back to demo."""
from functools import lru_cache
from pathlib import Path
from django.conf import settings
from PIL import ImageDraw

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
    if not names.intersection({'smoke', 'fire', 'khoi', 'khói', 'lua', 'lửa'}):
        raise RuntimeError('Model không có lớp smoke/fire. Cần trọng số đã fine-tune; COCO mặc định không phù hợp.')
    return model

def predict(image, simulate=False):
    mode = settings.INFERENCE_MODE
    width, height = image.size
    if mode == 'demo':
        detections = [{'label': 'smoke', 'confidence': None,
                       'box': [int(width*.36), int(height*.17), int(width*.67), int(height*.66)]}] if simulate else []
    elif mode == 'yolo':
        model = load_model(settings.MODEL_PATH)
        result = model.predict(image, conf=settings.MODEL_CONFIDENCE, verbose=False)[0]
        detections = []
        for box in result.boxes:
            label = str(result.names[int(box.cls.item())]).lower().strip()
            if label in {'smoke', 'fire', 'khoi', 'khói', 'lua', 'lửa'}:
                detections.append({'label': label, 'confidence': round(float(box.conf.item()), 4),
                                   'box': [round(float(v)) for v in box.xyxy[0].tolist()]})
    else:
        raise RuntimeError('INFERENCE_MODE chỉ nhận demo hoặc yolo.')
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)
    for item in detections:
        draw.rectangle(item['box'], outline='#ffb44a', width=max(3, width//350))
        label = 'SIMULATION - smoke' if mode == 'demo' else f"{item['label']} {item['confidence']:.0%}"
        x, y = item['box'][:2]
        draw.rectangle((x, max(0, y-25), min(width, x+230), y), fill='#ffb44a')
        draw.text((x+5, max(0, y-21)), label, fill='#142b23')
    return detections, annotated
