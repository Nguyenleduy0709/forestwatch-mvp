# Backend — Django

Backend đã có đầy đủ trong thư mục này. Django nhận yêu cầu từ trình duyệt, xử lý ảnh, gọi model, lưu dữ liệu và trả về HTML từ `../frontend/templates/`. Đây là ứng dụng dùng Django templates, chưa có REST API riêng cho một SPA.

## Thành phần

| Đường dẫn | Vai trò |
| --- | --- |
| `manage.py` | Chạy server, tạo database và tài khoản, chạy test |
| `forestwatch/settings.py` | Cấu hình database, frontend, chế độ AI |
| `forestwatch/urls.py` | Định tuyến yêu cầu |
| `monitor/forms.py` | Kiểm tra và chuẩn hóa ảnh tải lên |
| `monitor/views.py` | Xử lý upload, kết quả, quyền truy cập, cảnh báo |
| `monitor/inference.py` | Chọn chế độ mô phỏng, YOLO hoặc FoSp |
| `monitor/fosp.py` | Nạp FoSp, tạo mặt nạ khói và ảnh tô màu |
| `monitor/models.py` | Analysis, Alert, AlertHistory |
| `monitor/migrations/` | Cấu trúc database |
| `monitor/tests.py` | Kiểm tra nghiệp vụ và quyền truy cập |
| `weights/` | Đặt `best.pt` cho YOLO hoặc `fosp_best.pth` cho FoSp |

## Chạy từ thư mục gốc repo (Windows)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe backend/manage.py migrate
.\.venv\Scripts\python.exe backend/manage.py createsuperuser
.\.venv\Scripts\python.exe backend/manage.py runserver
```

Mở http://127.0.0.1:8000. Không cần một server frontend riêng. Không mở trực tiếp file HTML bằng cách nhấp đúp: template cần Django để xử lý.

## Các route đang có

| Phương thức | Route | Chức năng |
| --- | --- | --- |
| GET / POST | `/register/` | Đăng ký tài khoản mới (tên đăng nhập, email, mật khẩu) |
| GET / POST | `/login/` | Đăng nhập bằng session hoặc tài khoản hệ thống |
| POST / GET | `/logout/` | Đăng xuất |
| GET | `/auth/google/login/` | Chuyển hướng đăng nhập bằng tài khoản Google (OAuth 2.0) |
| GET | `/auth/google/callback/` | Nhận mã xác thực Google OAuth và đăng nhập session |
| GET | `/dashboard/` | Tải ảnh, xem lịch sử và tổng quan |
| POST | `/analyze/` | Multipart: `image`, `source`, `simulate_smoke` tùy chọn |
| GET | `/runs/<uuid>/` | Ảnh và thông số, thao tác xác minh |
| GET | `/runs/<uuid>/image/<kind>/` | `original` hoặc `annotated`, kiểm tra chủ sở hữu |
| GET | `/alerts/` | Lọc cảnh báo bằng `?status=PENDING` |
| POST | `/alerts/<uuid>/transition/` | `status`, `note`, CSRF bắt buộc |

Các route nghiệp vụ yêu cầu đăng nhập; POST dùng CSRF. Upload thành công chuyển hướng tới trang kết quả, không trả JSON.

## Cấu hình Đăng nhập bằng Google (Google OAuth 2.0)

1. Vào [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials).
2. Tạo **OAuth 2.0 Client ID**, chọn loại ứng dụng là **Web application**.
3. Tại mục **Authorized redirect URIs**, thêm URL callback của ứng dụng:
   - `http://127.0.0.1:8000/auth/google/callback/`
   - `http://localhost:8000/auth/google/callback/`
4. Cài đặt biến môi trường trong shell (hoặc thiết lập biến môi trường hệ thống):
   ```powershell
   $env:GOOGLE_CLIENT_ID = "your-client-id.apps.googleusercontent.com"
   $env:GOOGLE_CLIENT_SECRET = "your-client-secret"
   ```

## Dữ liệu và vận hành

- SQLite được tạo tại `backend/db.sqlite3`, ảnh tại `backend/media/`; không kèm trong ZIP hoặc Git.
- Một lần phân tích lưu một Analysis; chỉ tạo Alert khi có phát hiện.
- Trạng thái: PENDING → CONFIRMED → RESOLVED, hoặc PENDING → REJECTED. Lưu lịch sử và người thao tác.
- [`.env.example` ở gốc repo](../.env.example) là mẫu tham khảo; hiện ứng dụng đọc biến môi trường của shell, không tự tải `.env`.
- Model được giữ trong bộ nhớ; phải khởi động lại khi thay `best.pt`.
- MVP xử lý đồng bộ một ảnh mỗi yêu cầu. Video, stream và nhiều người dùng cần worker/hàng đợi sau này.
- `runserver` chỉ dùng phát triển. Triển khai công khai cần DEBUG=0, secret riêng, HTTPS và cấu hình static/media phù hợp.

Xem [train và tích hợp model](../notebooks/README.md) và [kiểm tra](../docs/VALIDATION.md).
