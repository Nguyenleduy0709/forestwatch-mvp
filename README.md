# ForestWatch 🌲

**MVP web giám sát rừng và xác minh cảnh báo khói**, xây bằng Django, HTML/CSS và TypeScript. Giao diện xanh rừng, ảnh toàn cảnh, tối ưu cho buổi demo nhóm.

![Trang chủ ForestWatch](docs/home.jpg)

## Cấu trúc repository

```text
forestwatch-mvp/
├── backend/                 Django và xử lý AI
│   ├── forestwatch/         Settings, URL, WSGI
│   ├── monitor/             Upload, models, views, inference, tests
│   ├── weights/             Đặt best.pt tự train (không commit)
│   ├── manage.py
│   └── requirements*.txt
├── frontend/                Giao diện
│   ├── templates/           HTML templates được Django render
│   ├── src/app.ts           TypeScript
│   ├── static/              CSS, JS đã build, ảnh rừng
│   └── package.json
├── notebooks/               Recipe train Kaggle và gắn model
├── docs/                    Hướng dẫn, prompt Codex, kiểm tra
└── .github/workflows/       Tự động kiểm tra backend và frontend
```

**Backend đã có sẵn**, không cần viết lại: xem [backend/README.md](backend/README.md).
Hai phần được tách theo thư mục; Django phục vụ cả HTML và xử lý phía máy chủ nên chỉ cần chạy một server.

| Tài liệu | Nội dung |
| --- | --- |
| [Backend](backend/README.md) | Luồng xử lý, database và route |
| [Frontend](frontend/README.md) | Bố cục dọc và cách sửa giao diện |
| [Kaggle → web](notebooks/README.md) | Train YOLO, tải best.pt, bật suy luận thật |
| [Prompt cho Codex](docs/CODEX_PROMPT.md) | Copy sau khi giải nén ZIP trên máy bạn |
| [Kiểm tra](docs/VALIDATION.md) | Những gì đã xác minh và giới hạn |

## Chạy được những gì?

- Đăng nhập bằng tài khoản Django; dữ liệu mỗi người được tách biệt.
- Tải ảnh JPG/PNG/WebP, kiểm tra định dạng/kích thước, bỏ metadata và chuẩn hóa ảnh.
- Chế độ **demo**: người dùng chủ động chọn tình huống có/không có phát hiện. **Không chạy AI**, không tạo điểm tin cậy giả.
- Chế độ **yolo**: chạy model detection đã fine-tune có nhãn smoke/fire và vẽ khung thật.
- Chế độ **fosp** chạy phân đoạn khói bằng checkpoint FoSp SMOKE5K, tô mặt nạ lên ảnh và hiển thị tỷ lệ diện tích khói.
- Một lần phân tích có đối tượng tạo một cảnh báo kèm ảnh bằng chứng.
- Xác nhận/từ chối, đóng sự kiện đã xác nhận; lưu người thực hiện, thời gian và ghi chú.
- Bố cục dọc theo phác thảo: ảnh ở trên, nguồn ảnh/thời gian/số vùng/trạng thái ở dưới, tiếp theo là xác minh và lịch sử.
- Lịch sử và bộ lọc trạng thái; xem lại ảnh gốc/kết quả.
- Ảnh bằng chứng chỉ truy cập được sau đăng nhập và kiểm tra chủ sở hữu.

**Phạm vi.** MVP phân tích ảnh tĩnh, chạy trên máy để trình diễn. Các phần camera trực tiếp, video, bản đồ, cảnh báo theo chuỗi thời gian và dự báo theo thời tiết nằm trong lộ trình tiếp theo.

## 1. Khởi động nhanh — không cần GPU

Cần Python 3.11 hoặc 3.12 và kết nối mạng ở lần cài thư viện đầu tiên.

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe backend/manage.py migrate
.\.venv\Scripts\python.exe backend/manage.py createsuperuser
.\.venv\Scripts\python.exe backend/manage.py runserver
```

### macOS / Linux

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python backend/manage.py migrate
.venv/bin/python backend/manage.py createsuperuser
.venv/bin/python backend/manage.py runserver
```

Mở **http://127.0.0.1:8000**, đăng nhập bằng tài khoản vừa tạo. Không có mật khẩu mặc định. TypeScript đã được biên dịch và lưu sẵn trong `frontend/static/js/app.js`; **không cần Node.js để chạy demo**.

Mặc định `INFERENCE_MODE=demo`. Không cần tải dataset hay model để thử quy trình web. Ảnh rừng đã có tại `frontend/static/img/forest.jpg`, có thể chọn ảnh này cho buổi demo.

## 2. Kịch bản trình diễn 3 phút

1. Giới thiệu trang chủ, chọn **Mở trung tâm giám sát**, đăng nhập.
2. Chọn ảnh rừng, tích **Mô phỏng phát hiện khói**, bấm chạy.
3. Chỉ rõ banner mô phỏng: vùng khung là dựng sẵn, không phải AI nhận diện.
4. Chuyển qua ảnh gốc/kết quả, nhập ghi chú và **Xác nhận**.
5. Bấm **Đánh dấu đã xử lý**, xem lịch sử thay đổi.
6. Tải ảnh lần nữa, bỏ tích ô mô phỏng: lần này không sinh cảnh báo.
7. Mở trang **Cảnh báo**, lọc trạng thái và xem lại bằng chứng.

Nếu có trọng số hợp lệ, dùng hướng dẫn dưới để trình diễn kết quả AI thật.

## 3. Chạy FoSp hoặc YOLO

### FoSp phân đoạn khói

Web chạy FoSp MiT-B3 và tô màu cam lên các pixel nhận diện là khói. Checkpoint tại update 14.000 được chọn bằng F1 trên 468 ảnh validation SMOKE5K. Trên 400 ảnh test giữ riêng, F1 trung bình theo ảnh là **76,88%**, IoU là **68,24%**, precision là **80,86%** và recall là **77,53%**. Ngưỡng tạo mặt nạ là 0,4. So với bản 10.000, F1 tăng 0,87 điểm phần trăm. Xem số liệu đầy đủ trong [kết quả đánh giá](docs/fosp_smoke5k_14000.json).

Trong [Kaggle Output](https://www.kaggle.com/code/nguynlduyb24dckh043/notebook32e4daa033/output) của Version 4, tải `forestwatch_fosp_14000.zip` hoặc mở thư mục `fosp_smoke5k_14000`. Đặt `best.pth` vào `backend/weights/fosp_best.pth`. Trọng số được lưu riêng trên Kaggle. Web cần GPU NVIDIA và PyTorch CUDA.

Ví dụ cho Windows với Python 3.12 và RTX 5070, chạy từ thư mục repo sau khi tạo `.venv` và migrate database:

```powershell
git -c core.symlinks=false clone https://github.com/LujianYao/FoSp.git .fosp-runtime/FoSp
git -C .fosp-runtime/FoSp checkout 02b9fd18ad1930d1081cf3b35a569d4b59e71d63
git clone --branch v1.6.2 --depth 1 https://github.com/open-mmlab/mmcv.git .fosp-runtime/mmcv
.\.venv\Scripts\python.exe -m pip install torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cu128
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-fosp.txt
$env:INFERENCE_MODE = 'fosp'
.\.venv\Scripts\python.exe backend/manage.py runserver --noreload
```

Có thể đặt `FOSP_MODEL_PATH` để trỏ đến trọng số khác và `FOSP_RUNTIME_DIR` để chọn thư mục chứa mã nguồn. Sau khi thay trọng số, khởi động lại web.

**Lưu để train tiếp.** Giữ `last.pth` (trọng số, optimizer và trạng thái ngẫu nhiên), `best.pth`, `manifest.json` (giữ nguyên nội dung), `run_config.json` và `results_14000.json` từ cùng thư mục Kaggle. Dùng [notebook nối tiếp 14.000](notebooks/fosp_continue.ipynb), gắn Output Version 4 vào Input rồi bật GPU và Internet. Notebook kiểm tra checkpoint 14.000 trước khi chạy. Lịch gốc được giữ ở `--max-updates 40000 --micro-batch 2 --accumulate 3`. Script trong ZIP cũ có giới hạn dừng 14.000, vì vậy lượt tiếp theo dùng script được notebook này chuẩn bị. Các đường dẫn dữ liệu thuộc môi trường Kaggle, chạy trên Windows cần điều chỉnh cách đọc đường dẫn và kiểm tra bộ nhớ GPU.

### YOLO phát hiện bằng khung

Cài thêm thư viện và đặt file trọng số **đã huấn luyện nhận diện khói** tại `backend/weights/best.pt`. File này không được kèm ZIP/repo. Không dùng `yolo11n.pt`/COCO mặc định rồi coi đó là model khói. MVP hỗ trợ model object detection có nhãn `smoke`, `fire`, `khói`, `lửa`, `khoi`, `lua`.

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-ai.txt
$env:INFERENCE_MODE = 'yolo'
$env:MODEL_PATH = (Resolve-Path '.\backend\weights\best.pt').Path
$env:MODEL_CONFIDENCE = '0.35'
.\.venv\Scripts\python.exe backend/manage.py runserver --noreload
```

macOS/Linux: `INFERENCE_MODE=yolo MODEL_PATH="$PWD/backend/weights/best.pt" .venv/bin/python backend/manage.py runserver --noreload`.

- Khởi động lại khi thay trọng số; model được giữ trong bộ nhớ sau lần tải đầu.
- Nếu thiếu model/thư viện hoặc lỗi suy luận, web báo lỗi; **không tự chuyển sang mô phỏng**.
- Ngưỡng 0.35 chỉ là điểm khởi đầu để thử, cần chọn theo validation thực tế.
- Điểm model không đồng nghĩa xác suất cháy. Không phát hiện không đồng nghĩa an toàn.
- Pyro-SDIS có một lớp khói. Web hỗ trợ nhãn lửa nếu model có lớp lửa; nó không tự tạo năng lực nhận diện lửa cho model chỉ học khói.
- Chỉ nạp `.pt` từ nguồn bạn tin cậy. Kiểm tra license của Ultralytics và trọng số trước khi phân phối/sử dụng thương mại.

Repo có [recipe huấn luyện YOLO trên Kaggle](notebooks/README.md) để copy vào notebook. Khi đánh giá YOLO, giữ test độc lập theo camera hoặc chuỗi ảnh nếu có thể và báo precision, recall, mAP.

## 4. Sửa giao diện

```bash
npm --prefix frontend ci
npm --prefix frontend run check
npm --prefix frontend run build
```

- HTML: `frontend/templates/`
- CSS: `frontend/static/css/app.css`
- TypeScript: `frontend/src/app.ts`
- JavaScript được tạo: `frontend/static/js/app.js`
- Ảnh đầu trang: `frontend/static/img/forest.jpg`

Google Fonts là tùy chọn: không có mạng thì dùng font hệ thống. Ảnh rừng được lưu sẵn nên demo không phụ thuộc tải ảnh từ Internet.

## 5. Kiểm tra

```powershell
.\.venv\Scripts\python.exe backend/manage.py check
Push-Location backend
..\.venv\Scripts\python.exe manage.py test monitor
Pop-Location
```

Test bao gồm: luồng mô phỏng có/không có cảnh báo, lịch sử trạng thái, chống cập nhật sai trạng thái, quyền sở hữu ảnh/cảnh báo, yêu cầu đăng nhập, CSRF, ảnh không hợp lệ, thiếu model không fallback và adapter YOLO với kết quả giả lập. Test adapter không chứng minh model thật chính xác.

Database và ảnh được tạo tại `backend/db.sqlite3`, `backend/media/`, không đưa vào Git. `backend/.env.example` chỉ là tài liệu mẫu; ứng dụng đọc biến môi trường của shell, **không tự tải file .env**.

## Kiến trúc MVP

```mermaid
flowchart LR
 A[Trình duyệt] --> B[Django: đăng nhập và upload]
 B --> C{Chế độ}
 C --> D[Mô phỏng có ghi nhãn]
 C --> E[YOLO + trọng số của bạn]
 C --> S[FoSp + checkpoint SMOKE5K]
 D --> F[Ảnh kết quả + dữ liệu phân tích]
 E --> F
 S --> F
 F --> G[SQLite + file bằng chứng]
 G --> H[Xác minh và lịch sử cảnh báo]
```

MVP suy luận đồng bộ một ảnh mỗi yêu cầu để dễ chạy tối nay. Khi thêm video hoặc nhiều người dùng, tách suy luận sang worker có hàng đợi, giới hạn dung lượng và tần suất, xử lý timeout; không chạy vòng lặp video trong request Django. Không dùng `runserver` cho môi trường công khai. Trước triển khai cần thiết lập secret riêng, DEBUG=0, HTTPS, static/media storage và cấu hình vận hành phù hợp.

## Lộ trình sau buổi demo

1. Gắn và đánh giá model khói thật, chọn ngưỡng validation.
2. Worker đọc video, lấy mẫu khung hình; xét nhiều lần quan sát và chống cảnh báo trùng.
3. Quản lý camera, bản đồ vị trí camera, dashboard thống kê.
4. Phân tích các lỗi của FoSp trên khói mỏng, mây và sương để chọn dữ liệu bổ sung.

## Tài nguyên và ghi công

- Django: https://www.djangoproject.com/
- Ultralytics: https://docs.ultralytics.com/
- Pyro-SDIS: https://huggingface.co/datasets/pyronear/pyro-sdis
- Ảnh rừng: Unsplash, ảnh `photo-1448375240586-882707db888b`; xem `docs/ASSETS.md`. Ảnh và font giữ license của tác giả, không thuộc license code.

Code dự án được cấp phép MIT, xem `LICENSE`.
