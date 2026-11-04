# Biên bản tái cấu trúc — 04/11/2026

## Khảo sát và lựa chọn

- Workspace ban đầu tại `D:\ForestWatch` chứa bản ZIP, `checkpoints/`, `outputs/`, `work/` và hồ sơ di chuyển, chưa có `.git` tại gốc.
- Repository thực tế ở `outputs/forestwatch-mvp`, main/origin/main cùng commit `66d634ee28840ef4e661bf01c75c8764cf36e33a`, working tree sạch, không có thay đổi chưa commit. GitHub chỉ có nhánh main lúc khảo sát. Không tìm thấy AGENTS.md áp dụng.
- Mã nguồn đã phân chia đúng Django/templates: giữ `backend/`, `frontend/`, `notebooks/`, `docs/`, `.github/`; bổ sung `scripts/` có nội dung thực. Không cần di chuyển các module hay tạo SPA mới.
- Khởi tạo bản làm việc Git tại gốc D:\ForestWatch từ origin/main, dùng `chore/restructure-project`; main giữ nguyên. Bản repo cũ, database, media, runtime, checkpoint, dataset, ZIP và môi trường Python được giữ nguyên trên ổ D.
- Chuyển mẫu cấu hình `backend/.env.example` về `.env.example` ở gốc, bổ sung đường dẫn FoSp và database/media. Không tự nạp file .env; README dùng biến PowerShell rõ ràng.
- Thêm `MEDIA_ROOT` tùy chọn để dùng ảnh bằng chứng ở vị trí cũ. Mặc định vẫn là backend/media. Không tự sửa hoặc di chuyển database người dùng.
- Không thay model, trọng số, preprocessing, kiến trúc, ngưỡng, thuật toán hoặc tài liệu train/notebook. Model FoSp và code inference hiện tại được giữ nguyên.
- Thêm CONTRIBUTING, mẫu PR/báo lỗi, script kiểm tra. CI tách backend Windows/Linux và frontend, dùng demo và test adapter; không cài PyTorch, không tải checkpoint và không cần GPU.

## Kết quả tại máy Windows

- Python 3.12.14, Django 5.2.19, Pillow 12.3.0.
- Django system check: đạt; makemigrations --check --dry-run: không có thay đổi.
- 12 test backend: đạt, gồm đăng nhập, static discovery, demo có/không có cảnh báo, quyền bằng chứng, CSRF, lịch sử, YOLO adapter, FoSp adapter và thiếu checkpoint.
- npm ci, TypeScript check và build: đạt. JS sau build không khác bản trong Git.
- collectstatic: đạt, 130 file vào backend/staticfiles (bỏ qua bởi Git).
- Kiểm tra HTTP server thật với SQLite/media tạm trong work/: đạt đăng nhập bằng form, dashboard, CSS/JS/ảnh, upload có/không khói mô phỏng, ảnh gốc/kết quả, xác nhận → xử lý, lịch sử và logout/phân quyền. Không dùng dữ liệu/tài khoản người dùng. Script tái sử dụng lưu ở scripts/check_demo_web.py và chạy trong CI trên Windows/Linux; không đưa dữ liệu kiểm tra vào Git.
- FoSp thật: `scripts/check_fosp_web.py`, checkpoint `checkpoints/fosp_smoke5k_14000/best.pth`, runtime `outputs/forestwatch-mvp/.fosp-runtime`, PyTorch 2.7.1+cu128, RTX 5070 Laptop GPU. Upload ảnh khói gốc trong dataset cục bộ: đạt trang kết quả, ảnh gốc/mặt nạ, cảnh báo và chặn truy cập sau logout. Diện tích khói 0,5123138428%, thời gian đo khoảng 6,25 giây trong lượt này.
- Runtime FoSp có cảnh báo MMCV về MultiScaleDeformableAttention và avg_non_ignore; đường suy luận hiện tại vẫn hoàn thành. Không sửa thư viện/thuật toán trong đợt này.
- Kiểm tra GPU một ảnh chỉ xác minh tích hợp, không phải đánh giá lại độ chính xác model. Số liệu benchmark cũ giữ tại fosp_smoke5k_14000.json.

## Git và dữ liệu

.gitignore bổ sung các vị trí cục bộ và định dạng checkpoint/archive/database/secret. Chỉ stage file mã nguồn/tài liệu cụ thể, không dùng git add . cho workspace chứa dữ liệu train. Việc ignore không xóa file trên đĩa và không loại bỏ file đã được track từ trước.

Bản gốc `outputs/forestwatch-mvp` vẫn dùng cho đối chiếu và chứa dữ liệu cũ. Sau khi đóng/mở IDE, chọn repo ở `D:\ForestWatch`, tránh commit nhầm bản cũ. Các đường dẫn đã được cấu hình từ vị trí backend/frontend hiện có; không thay runtime của notebook train.

## Việc cần quản trị viên

GitHub API trả 404 “Branch not protected” cho main và rulesets trả danh sách rỗng. Chưa thay đổi cài đặt. Đề xuất sau khi xác nhận: bắt buộc PR, một approval, check Backend (ubuntu-latest), Backend (windows-latest), Frontend thành công, giải quyết nhận xét và chặn force push/xóa main. Xem CONTRIBUTING.md.

PR cần một thành viên review và người có quyền merge. Việc merge và bảo vệ main không được tự thực hiện trong đợt này.
