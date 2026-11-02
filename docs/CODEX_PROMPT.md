# Prompt tiếp tục dự án trên máy bạn

Giải nén ZIP, mở đúng thư mục có `backend/`, `frontend/`, `README.md` trong Codex, rồi dán nội dung bên dưới:

```text
Hãy tiếp tục dự án ForestWatch trong workspace hiện tại, không tạo lại từ đầu.

Đầu tiên đọc README.md, backend/README.md, frontend/README.md,
notebooks/README.md và docs/VALIDATION.md; kiểm tra code thật trước khi sửa.

Mục tiêu: MVP demo giám sát khói rừng bằng ảnh. Giữ cấu trúc repo:
- backend/: Django, SQLite, kiểm tra upload, gọi model, cảnh báo và lịch sử.
- frontend/: Django HTML templates, CSS, TypeScript src và static assets.
- notebooks/: hướng dẫn train Kaggle và lấy trọng số.
- docs/: tài liệu sử dụng, kiểm tra và bàn giao.

Giao diện xanh rừng, ảnh một khu rừng. Màn hình giám sát và chi tiết theo
bố cục dọc: ảnh ở trên; nguồn ảnh, thời gian, số vùng phát hiện, trạng thái
và thao tác xác minh ở dưới. Responsive cho desktop và điện thoại.
Giữ ảnh gốc/kết quả, xác nhận/từ chối/đã xử lý và lịch sử.

Hãy thiết lập môi trường Python, cài backend/requirements.txt, migrate,
chạy kiểm tra Django và 9 test hiện có. Hướng dẫn tôi tạo tài khoản cá nhân,
không hardcode mật khẩu hoặc tài khoản demo vào repo. Khởi động web local.
Nếu sửa TypeScript, dùng npm --prefix frontend ci/check/build phù hợp
(check/build qua npm --prefix frontend run check hoặc run build).

Nếu backend/weights/best.pt chưa có, giữ INFERENCE_MODE=demo và nhãn
mô phỏng rõ ràng. Không giả kết quả AI hoặc bịa chỉ số đánh giá.
Nếu có best.pt tôi tự train trên Kaggle, kiểm tra đây là Ultralytics YOLO
object detection với lớp smoke; cài requirements-ai.txt, bật mode yolo,
kiểm tra upload → suy luận thật → hiển thị khung → xác minh cảnh báo.
Không dùng model COCO mặc định làm model khói. Không tự chuyển sang demo
khi model lỗi. Không gọi điểm model là xác suất cháy rừng.

Không đổi framework, không bổ sung video/stream/FoSp trước khi luồng ảnh
hoạt động. Giữ quyền sở hữu dữ liệu, CSRF và kiểm tra upload hiện có.
Không đưa .env, database, media, trọng số hay credentials lên Git.

Sửa lỗi phát hiện trong phạm vi này, kiểm tra desktop/mobile nếu có công cụ,
cập nhật README khi thay lệnh hoặc cấu trúc. Báo rõ phần nào đã chạy kiểm tra,
phần nào chưa có trọng số để kiểm tra. Chỉ push khi tôi yêu cầu.
```
