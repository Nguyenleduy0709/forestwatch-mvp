# Kaggle → trọng số → ForestWatch

**Có thể train trên Kaggle rồi chạy model trên máy dùng web.** Kaggle dùng để huấn luyện; backend Django dùng trọng số để suy luận từng ảnh. Không cần giữ notebook Kaggle chạy sau khi tải trọng số về.

Hướng dẫn này là recipe chưa chạy trên GPU trong dự án. Chưa có trọng số hoặc số liệu độ chính xác kèm repo. Dùng Ultralytics YOLO detection cho nhãn bounding box của Pyro-SDIS; không cần mặt nạ khói để hoàn thành MVP. FoSp/segmentation không cắm trực tiếp vào adapter hiện tại.

## 1. Chuẩn bị notebook

Tạo notebook trên Kaggle, thêm [dataset Pyro](https://www.kaggle.com/datasets/emilienpaganelli/pyro-challenge), chọn GPU nếu tài khoản có quyền dùng; bật Internet nếu cần tải thư viện/trọng số ban đầu. Chạy từng cell dưới đây. Tốc độ phụ thuộc GPU và dữ liệu; không cam kết train xong trong một thời gian cố định.

```python
%pip install 'ultralytics>=8.3,<9'
```

```python
from pathlib import Path
import yaml
import ultralytics

print('Ultralytics:', ultralytics.__version__)
configs = list(Path('/kaggle/input').rglob('data.yaml'))
print(*configs, sep='\n')
```

Chọn đúng file của Pyro trong danh sách, không đoán tên thư mục mount. Đọc YAML và kiểm tra các thư mục ảnh/nhãn trước khi train:

```python
# Sửa bằng đường dẫn in ra ở cell trên.
DATA_YAML = Path('/kaggle/input/THAY_BANG_DUONG_DAN_THAT/data.yaml')
config = yaml.safe_load(DATA_YAML.read_text())
print(config)

# Đặt thành thư mục thực sự chứa images/train và images/val.
# Nếu data.yaml cùng cấp images/ thì dùng DATA_YAML.parent.
DATA_ROOT = DATA_YAML.parent
assert (DATA_ROOT / 'images/train').is_dir(), 'Hãy sửa DATA_ROOT'
assert (DATA_ROOT / 'images/val').is_dir(), 'Hãy sửa DATA_ROOT'
assert (DATA_ROOT / 'labels/train').is_dir(), 'Kiểm tra nhãn YOLO'
names = config['names']
labels = list(names.values()) if isinstance(names, dict) else names
assert [str(x).lower() for x in labels] == ['smoke'], labels

# Ghi cấu hình riêng vì /kaggle/input chỉ đọc.
local_config = {
    'path': str(DATA_ROOT), 'train': 'images/train',
    'val': 'images/val', 'names': {0: 'smoke'}, 'nc': 1,
}
if (DATA_ROOT / 'images/test').is_dir():
    local_config['test'] = 'images/test'
Path('/kaggle/working/smoke.yaml').write_text(yaml.safe_dump(local_config))
```

Dataset có ảnh không khói; cần giữ chúng để học nền. Kiểm tra ảnh/nhãn mẫu. Không trộn các khung gần giống nhau của cùng camera/video giữa train và test nếu có metadata để phân nhóm. Không dùng test để chọn ngưỡng hoặc tinh chỉnh model.

## 2. Chạy thử một baseline

```python
from ultralytics import YOLO

model = YOLO('yolo11n.pt')  # Khởi tạo, chưa phải model khói.
model.train(
    data='/kaggle/working/smoke.yaml',
    epochs=10, imgsz=640, batch=8, device=0,
    workers=2, seed=42,
    project='/kaggle/working/runs', name='smoke_baseline',
)
run_dir = Path(model.trainer.save_dir)
best = run_dir / 'weights/best.pt'
assert best.is_file()
print('Tải trọng số tại:', best)

trained = YOLO(str(best))
print('Task:', trained.task, 'Classes:', trained.names)
metrics = trained.val(data='/kaggle/working/smoke.yaml', split='val')
print(metrics.results_dict)
Path('/kaggle/working/ultralytics-version.txt').write_text(ultralytics.__version__)
```

10 epochs chỉ là thử đường đi dữ liệu, chưa đủ để kết luận model tốt. Nếu hết VRAM, giảm batch. Dùng precision/recall/mAP và kiểm tra thủ công ảnh mây, sương, khói nhỏ, ảnh không khói. Chọn confidence trên validation. Sau khi chốt cấu hình mới đánh giá test độc lập nếu có nhãn.

Tải `best.pt`, `ultralytics-version.txt` và các báo cáo trong thư mục run về máy từ output notebook. Giữ tên phiên bản thư viện để tái lập. Không chỉ tải `results.csv` hoặc ảnh biểu đồ: chúng không chứa trọng số.

## 3. Gắn vào web trên Windows

Đặt trọng số tại `backend/weights/best.pt`. Từ thư mục gốc repo, sau khi đã thiết lập môi trường theo README:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-ai.txt
$env:INFERENCE_MODE = 'yolo'
$env:MODEL_PATH = (Resolve-Path '.\backend\weights\best.pt').Path
$env:MODEL_CONFIDENCE = '0.35'
.\.venv\Scripts\python.exe backend/manage.py runserver --noreload
```

Nên dùng cùng phiên bản Ultralytics đã ghi lại trên Kaggle. Nếu cần, cài `ultralytics==<phiên_bản_đã_ghi>` trong môi trường local trước khi chạy. Ngưỡng 0.35 chỉ để khởi động thử, phải đánh giá trên validation trước khi chốt.

Mở dashboard, kiểm tra nhãn **YOLO · MODEL THẬT**, tải một ảnh kiểm tra độc lập. Backend nạp trọng số, trả các khung và điểm model. Khởi động lại server mỗi khi thay trọng số. Máy không có GPU vẫn có thể suy luận CPU nhưng cần đo độ trễ thực tế.

Model chỉ học `smoke` thì chỉ nhận diện khói; đây không phải dự báo cháy trước khi có dấu hiệu nhìn thấy. Điểm model không phải xác suất cháy. Không phát hiện khói không bảo đảm hiện trường an toàn.

Nguồn API: [Ultralytics Train](https://docs.ultralytics.com/modes/train/), [Ultralytics Predict](https://docs.ultralytics.com/modes/predict/). Chỉ nạp trọng số từ nguồn tin cậy; kiểm tra giấy phép thư viện, dữ liệu và trọng số khi phân phối.
