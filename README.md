# ForestWatch

ForestWatch dùng Django, TypeScript và FoSp để giám sát hình ảnh rừng, lưu bằng chứng và xác minh cảnh báo khói. Phòng CCTV hiển thị tối đa 10 nguồn và lấy mẫu khung hình khi video đang phát.

![Giao diện](docs/home.jpg)

## Cấu trúc

```text
D:\ForestWatch\
├── backend/                 Django, nghiệp vụ, inference và tests
│   ├── forestwatch/         Settings, URL, WSGI
│   ├── monitor/             Đăng nhập, upload, Analysis/Alert, migration
│   ├── weights/             Chỗ đặt model riêng, không commit
│   └── manage.py
├── frontend/
│   ├── templates/           HTML được Django render
│   ├── src/                 TypeScript nguồn
│   └── static/              CSS, ảnh và JS đã build
├── notebooks/               Notebook/script tiếp tục train FoSp
├── research/                Thử nghiệm nghiên cứu, web chưa dùng
│   ├── temporal_fosp/       FoSp-Temporal: chuẩn bị dữ liệu, train, đánh giá
│   └── day1/                Ghi chú và notebook pilot Day 1 (đã xóa output)
├── scripts/                 Kiểm tra dự án và FoSp web thật
├── docs/                    Kiến trúc, phạm vi sản phẩm, kết quả xác minh
│   ├── README.md            Mục lục tài liệu
│   ├── ARCHITECTURE.md      Luồng xử lý và các thành phần
│   ├── PRODUCT.md           Chức năng, giới hạn và hướng phát triển
│   └── TEMPORAL_RESEARCH.md Nghiên cứu FoSp-Temporal: giao thức và kết quả
├── .github/                 CI, mẫu PR, mẫu báo lỗi
├── .env.example             Mẫu biến môi trường, không chứa secret
├── CONTRIBUTING.md          Quy trình nhánh, PR, review và conflict
└── README.md
```

Trên máy hiện tại, `checkpoints/`, `outputs/`, `work/`, ZIP và log di chuyển là tài nguyên **cục bộ được Git bỏ qua**. Bản repo cũ tại `outputs/forestwatch-mvp` được giữ nguyên; làm việc mới ở gốc `D:\ForestWatch`. Không commit hoặc xóa chúng. Model, thuật toán FoSp, ngưỡng 0,4 và notebook train được giữ nguyên.

## Cài và chạy trên Windows PowerShell

Cần Git, Python 3.12 (hoặc 3.11 cho demo), Node.js 22 nếu sửa frontend. Với máy mới:

```powershell
git clone https://github.com/Nguyenleduy0709/forestwatch-mvp.git D:\ForestWatch
Set-Location D:\ForestWatch
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
```

Trên máy đã có repo ở `D:\ForestWatch`, chỉ `Set-Location` vào đó, **không clone chồng lên dữ liệu**. Có thể dùng môi trường sẵn có `D:\ForestWatch\work\venv\Scripts\python.exe` thay `.\.venv\Scripts\python.exe` trong mọi lệnh bên dưới.

Ứng dụng đọc `.env` ở thư mục gốc. Biến đặt trong PowerShell được ưu tiên. Danh sách biến có trong [`.env.example`](.env.example).

```powershell
$env:DJANGO_DEBUG = '1'
$env:DJANGO_ALLOWED_HOSTS = 'localhost,127.0.0.1'
$env:INFERENCE_MODE = 'demo'
.\.venv\Scripts\python.exe backend/manage.py migrate
.\.venv\Scripts\python.exe backend/manage.py createsuperuser
.\.venv\Scripts\python.exe backend/manage.py runserver 127.0.0.1:8000
```

### Chạy nhanh bằng Docker (Khuyên dùng khi demo / chấm bài)

Nếu máy đã cài **Docker Desktop**, bạn có thể khởi chạy toàn bộ hệ thống bằng đúng 1 câu lệnh mà không cần cài đặt Python hay thư viện thủ công:

```bash
docker compose up --build
```

Mở trình duyệt tại `http://localhost:8000/`. Toàn bộ dữ liệu database SQLite, ảnh chụp bằng chứng và checkpoint model AI được mount qua Volume nên không bị mất khi khởi động lại.

Mở http://127.0.0.1:8000, đăng nhập bằng tài khoản vừa tạo. Không có tài khoản/mật khẩu mặc định. SQLite mặc định tại `backend/db.sqlite3`, ảnh tại `backend/media/`; không đưa vào Git. Nếu muốn dùng database cũ trên máy này, đặt đường dẫn **trước migrate và chạy web**:

```powershell
$env:DATABASE_PATH = 'D:\ForestWatch\outputs\forestwatch-mvp\backend\db.sqlite3'
```

Database cũ lưu đường dẫn ảnh tương đối với media cũ. Để dùng cả tài khoản, lịch sử và bằng chứng cũ, cấu hình thêm:

```powershell
$env:MEDIA_ROOT = 'D:\ForestWatch\outputs\forestwatch-mvp\backend\media'
```

### Phòng giám sát CCTV

Sau khi đăng nhập, mở **Phòng giám sát** ở `/control-room/`. Chọn **Thêm nguồn** để mở ảnh JPG/PNG/WebP, tệp video MP4/WebM, webcam hoặc liên kết video công khai. Với YouTube, cài thêm công cụ tách luồng và đặt Node.js hoặc Deno trong PATH của máy chủ.

```powershell
.\.venv\Scripts\python.exe -m pip install -U -r backend/requirements-video.txt
```

YouTube cần cung cấp luồng HTTP phát được trên trình duyệt. Video riêng tư, nguồn yêu cầu đăng nhập, playlist HLS và trang web chỉ nhúng trình phát có thể cần tệp video tải sẵn hoặc liên kết tệp trực tiếp.

### Camera điện thoại

Cho điện thoại và máy chạy ForestWatch vào cùng Wi-Fi. Bật ứng dụng camera IP có ảnh chụp JPEG, ví dụ IP Webcam trên Android. Trong **Thêm nguồn**, chọn **Camera điện thoại qua Wi-Fi** và nhập địa chỉ ứng dụng hiển thị, ví dụ `http://192.168.1.20:8080`. ForestWatch lấy ảnh từ `/shot.jpg` theo mặc định và cũng nhận `/snapshot.jpg` hoặc `/snapshot.jpeg`. Chọn độ phân giải 1280×720 nếu cần giảm dung lượng khung hình.

Đặt `PHONE_CAMERA_NETWORKS` trong `.env` thành dải mạng riêng của Wi-Fi, ví dụ `192.168.1.0/24`, rồi khởi động lại Django. ForestWatch dùng địa chỉ camera để lấy hình từ máy chủ, vì vậy trang giám sát trên máy tính vẫn có thể mở qua localhost. Với điện thoại đã xuất hiện như một webcam hệ thống, chọn **Webcam hoặc camera USB**, bấm **Lấy danh sách camera** và chọn thiết bị.

### Checkpoint E3 concat

Đặt `FOSP_MODEL_PATH` trong `.env` trỏ đến `E3_concat_best.pth`. E3 dùng bộ nạp nghiêm ngặt và ngưỡng từ checkpoint. Với camera cố định, chọn một khung chưa có khói rồi bấm **Lưu khung nền** trên ô giám sát. E3 dùng khung nền tham chiếu cho nguồn đó. Khi camera đổi hướng, chọn **Đổi khung nền** hoặc **Bỏ nền**. E3 cũng quét được ảnh và video chưa có khung nền.

### Nguồn đã lưu và bằng chứng

Nguồn được lưu theo tài khoản trong bộ nhớ của trình duyệt hiện tại, kể cả ảnh và video chọn từ máy. Tải lại trang để tự mở các nguồn đã lưu và tiếp tục video từ vị trí gần nhất. Webcam tiếp tục cần quyền camera, còn nguồn qua mạng sẽ xin lại liên kết phát. Đóng một cửa sổ sẽ xóa nguồn đó khỏi danh sách đã lưu.

Quét tự động hoạt động trong trang giám sát đang mở. Ảnh được quét một lần, còn webcam được lấy mẫu luân phiên. Với video từ máy và liên kết video, bấm **Bắt đầu quét** trên từng ô để phát và quét, kể cả sau khi tải lại trang. Tạm dừng quét tự động cũng dừng các video đang phát. Sau khi bắt đầu, chọn tốc độ phát chậm hoặc bấm **Quét ngay** để kiểm tra khung hình đang dừng.

Khi vùng khói có điểm nhận diện từ 40%, hệ thống lưu bằng chứng và đưa sự kiện vào nhật ký. Điểm nhận diện tách biệt với tỷ lệ diện tích vùng khói. Có thể bật chuông trong trang và cho phép thông báo của trình duyệt khi chuyển tab. Một đợt video kết thúc sau ba mẫu đang phát liên tiếp không đạt ngưỡng lưu, hoặc khi không ghi nhận thêm khói đủ ngưỡng trong năm phút trước lần quét kế tiếp. Mỗi sự kiện giữ ảnh gốc và ảnh đánh dấu của khung đầu đạt ngưỡng, cùng một khung bổ sung nếu vùng khói lớn hơn. Mục **Cảnh báo** cho phép tải ảnh và xác minh sự kiện.

Khi nâng cấp từ bản trước, chạy `python backend/manage.py migrate` để cập nhật bảng lưu sự kiện và tên checkpoint trong bằng chứng.

### Chế độ demo

Không cần PyTorch, GPU hay checkpoint. Dùng ảnh `frontend/static/img/forest.jpg`, tick mô phỏng khói rồi upload. Xem ảnh gốc/kết quả, xác nhận cảnh báo, đánh dấu đã xử lý và xem lịch sử. Upload lần nữa không tick mô phỏng để kiểm tra trường hợp không có cảnh báo. Khung demo và nhãn mô phỏng được hiển thị rõ.

### Chế độ FoSp thật trên máy hiện tại

Máy này đã có môi trường CUDA, runtime và checkpoint; không cần sao chép model:

```powershell
Set-Location D:\ForestWatch
$env:INFERENCE_MODE = 'fosp'
$env:FOSP_MODEL_PATH = 'D:\ForestWatch\checkpoints\fosp_smoke5k_14000\best.pth'
$env:FOSP_RUNTIME_DIR = 'D:\ForestWatch\outputs\forestwatch-mvp\.fosp-runtime'
# Thêm DATABASE_PATH và MEDIA_ROOT như trên nếu muốn dùng dữ liệu cũ.
.\work\venv\Scripts\python.exe backend/manage.py migrate
# Chỉ cần tạo tài khoản nếu database đang dùng chưa có tài khoản.
.\work\venv\Scripts\python.exe backend/manage.py runserver 127.0.0.1:8000 --noreload
```

Nếu thiếu tài khoản: chạy `backend/manage.py createsuperuser` bằng cùng Python và cùng biến môi trường. Đổi model cần khởi động lại server. Thiếu model/thư viện hoặc lỗi suy luận sẽ báo lỗi, không tự chuyển sang demo.

### Cài FoSp trên máy mới có GPU NVIDIA

Dùng Python 3.12; cài backend và migrate như trên. Cấu hình dưới đây giữ phiên bản runtime đã dùng cho model hiện tại (PyTorch CUDA 12.8, đã thử trên RTX 5070):

```powershell
git -c core.symlinks=false clone https://github.com/LujianYao/FoSp.git .fosp-runtime/FoSp
git -C .fosp-runtime/FoSp checkout 02b9fd18ad1930d1081cf3b35a569d4b59e71d63
git clone --branch v1.6.2 --depth 1 https://github.com/open-mmlab/mmcv.git .fosp-runtime/mmcv
.\.venv\Scripts\python.exe -m pip install torch==2.7.1 torchvision==0.22.1 --index-url https://download.pytorch.org/whl/cu128
.\.venv\Scripts\python.exe -m pip install -r backend/requirements-fosp.txt
$env:INFERENCE_MODE = 'fosp'
$env:FOSP_MODEL_PATH = 'D:\ForestWatch\backend\weights\fosp_best.pth'
$env:FOSP_RUNTIME_DIR = 'D:\ForestWatch\.fosp-runtime'
.\.venv\Scripts\python.exe backend/manage.py runserver 127.0.0.1:8000 --noreload
```

Đặt checkpoint `best.pth` đáng tin cậy vào đường dẫn `FOSP_MODEL_PATH`, hoặc trỏ biến đến vị trí có sẵn. Không tải checkpoint vào Git. Runtime phải chứa cả `FoSp/` và `mmcv/`. FoSp dùng CUDA khi khả dụng và hỗ trợ chạy trên CPU. [Số liệu checkpoint 14.000](docs/fosp_smoke5k_14000.json): F1 trung bình theo ảnh 76,88%, IoU 68,24% trên 400 ảnh test SMOKE5K; không suy rộng thành độ chính xác mọi hiện trường.

Giữ nguyên `last.pth`, `best.pth`, `manifest.json`, `run_config.json`, kết quả và mã train cùng đợt trong ổ D để tiếp tục train. `last.pth` chứa optimizer/trạng thái train; không thay bằng `best.pth` khi resume. Xem [hướng dẫn notebook](notebooks/README.md) và [notebook tiếp tục FoSp](notebooks/fosp_continue.ipynb). Không thay tập chia dữ liệu hoặc lịch train khi chỉ tái cấu trúc web.

Chế độ YOLO đã có vẫn được giữ: cài `backend/requirements-ai.txt`, đặt `INFERENCE_MODE=yolo`, `MODEL_PATH` là đường dẫn tuyệt đối đến model detection đã train smoke/fire, `MODEL_CONFIDENCE=0.35`. Đây là tùy chọn riêng, không thay FoSp.

## Sửa frontend

```powershell
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run check
npm.cmd --prefix frontend run build
```

Commit cả TypeScript và JS đã build. JS có sẵn trong repo nên chạy demo không cần Node. CSS/templates sửa trực tiếp rồi tải lại Django; không có `npm start` hay server frontend riêng. Font ngoài mạng là tùy chọn, có font hệ thống dự phòng.

## Kiểm tra

Chạy toàn bộ backend + frontend, không cần GPU hoặc model:

```powershell
.\scripts\check.ps1
# Môi trường hiện có trên máy này:
.\scripts\check.ps1 -Python .\work\venv\Scripts\python.exe
```

Nếu PowerShell chặn script, có thể chạy các lệnh tương đương:

```powershell
$env:INFERENCE_MODE = 'demo'
.\.venv\Scripts\python.exe backend/manage.py check
.\.venv\Scripts\python.exe backend/manage.py makemigrations --check --dry-run
Push-Location backend
..\.venv\Scripts\python.exe manage.py test monitor --noinput
Pop-Location
.\.venv\Scripts\python.exe scripts/check_demo_web.py
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run check
npm.cmd --prefix frontend run build
git diff --exit-code -- frontend/static/js/app.js
.\.venv\Scripts\python.exe backend/manage.py collectstatic --noinput
```

Test kiểm tra đăng nhập, dashboard, upload, cảnh báo/lịch sử, phân quyền bằng chứng, CSRF, ảnh lỗi và dispatch FoSp/YOLO. Các test adapter dùng kết quả giả lập, không đánh giá độ chính xác AI. `scripts/check_demo_web.py` kiểm tra luồng demo qua server HTTP thật với database/media tạm. CI chạy backend trên Windows/Linux và frontend trên Linux, chỉ cài `backend/requirements.txt` và TypeScript.

Kiểm tra web FoSp thật (ngoài CI), sau khi đặt `FOSP_MODEL_PATH` và `FOSP_RUNTIME_DIR`:

```powershell
.\work\venv\Scripts\python.exe scripts/check_fosp_web.py --image <duong-dan-anh-khoi-goc>
```

Nên chọn ảnh khói gốc, không phải ảnh đã tô màu. Script dùng SQLite trong RAM và media tạm dưới `work/`, không ghi vào database/ảnh người dùng; kiểm tra upload, ảnh mặt nạ, trang kết quả và cảnh báo nếu có khói. Xem [biên bản tái cấu trúc](docs/RESTRUCTURING.md) để biết kiểm tra thực tế và giới hạn.

## Làm việc nhóm và tài liệu

Dùng `main` ổn định, nhánh `feature/<ten-chuc-nang>` hoặc `fix/<ten-loi>`, PR và review theo [CONTRIBUTING.md](CONTRIBUTING.md). Không tạo `develop` khi chưa có nhu cầu phát hành riêng. Bảo vệ main là đề xuất chờ quản trị viên xác nhận.

- [Mục lục tài liệu](docs/README.md), [kiến trúc](docs/ARCHITECTURE.md), [phạm vi sản phẩm](docs/PRODUCT.md).
- [Backend và các route](backend/README.md), [Frontend](frontend/README.md).
- [Kết quả xác minh trước đây](docs/VALIDATION.md), [tài nguyên và giấy phép](docs/ASSETS.md).
- Code MIT: [LICENSE](LICENSE). Model, dữ liệu, ảnh và thư viện ngoài giữ giấy phép riêng.
