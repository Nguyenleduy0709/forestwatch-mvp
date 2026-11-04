# Phạm vi sản phẩm ForestWatch

Cập nhật theo ứng dụng hiện có ngày 04/11/2026. Tài liệu giúp nhóm thống nhất việc đang có, tiêu chí kiểm tra và phần chỉ mới dự kiến. Hướng dẫn kỹ thuật tại [kiến trúc](ARCHITECTURE.md) và [README](../README.md).

## 1. Mục tiêu và người dùng

ForestWatch hỗ trợ người giám sát tải ảnh hiện trường, xem vùng khói do model đề xuất và ghi nhận quyết định xác minh. Mục tiêu MVP là hoàn thành luồng **ảnh → phân tích → bằng chứng → cảnh báo → quyết định có lịch sử** trên máy local.

Người dùng hiện là tài khoản Django, tự quản lý ảnh và cảnh báo của mình. Có trang admin cho tài khoản đủ quyền, chưa có phân quyền nhóm giám sát, tổ chức hoặc chia sẻ sự kiện giữa các người dùng. Nhãn nguồn ảnh là văn bản người dùng nhập, không chứng minh có camera trực tiếp được kết nối.

## 2. Chức năng đã có

| Chức năng | Hành vi và tiêu chí chấp nhận |
| --- | --- |
| Đăng nhập / đăng xuất | Dùng tài khoản Django; người chưa đăng nhập được chuyển tới login khi mở trang nghiệp vụ |
| Dashboard | Hiển thị tổng quan và các lần phân tích gần đây thuộc người đang đăng nhập |
| Upload | Nhận JPG/PNG/WebP hợp lệ trong giới hạn; ảnh lỗi bị từ chối và có thông báo |
| Demo | Người dùng chọn có/không khói mô phỏng; giao diện ghi nhãn demo, không trình bày điểm AI giả |
| FoSp thật | Dùng checkpoint cấu hình để phân đoạn khói, hiển thị vùng tô màu và tỷ lệ diện tích; lỗi model được báo rõ |
| Xem bằng chứng | Xem ảnh gốc đã chuẩn hóa và ảnh kết quả; người khác không được truy cập |
| Cảnh báo | Chỉ tạo khi có phát hiện; có danh sách và bộ lọc trạng thái |
| Xác minh | Chờ xác minh → xác nhận hoặc từ chối; đã xác nhận → đã xử lý |
| Lịch sử | Lưu người thao tác, thời gian, trạng thái trước/sau và ghi chú |
| YOLO tùy chọn | Giữ adapter detection đã có, chỉ dùng trọng số được train nhãn smoke/fire phù hợp |

Hướng dẫn demo từng bước và lệnh chạy FoSp thật nằm tại README. Kết quả kiểm thử tích hợp và benchmark là hai loại bằng chứng riêng; xem [biên bản](RESTRUCTURING.md) và [số liệu model](fosp_smoke5k_14000.json).

## 3. Giới hạn phải thể hiện đúng

- Demo là mô phỏng do người dùng chọn, không nhận diện bằng AI.
- FoSp hiện phân đoạn khói từ ảnh tĩnh; không suy ra model biết phát hiện lửa chỉ vì ứng dụng tên ForestWatch.
- Diện tích khói là phần trăm ảnh chuẩn hóa bị tô màu, không phải xác suất có cháy. Không phát hiện không chứng minh hiện trường an toàn.
- Phân tích đồng bộ có thể khiến người dùng phải chờ; chưa cam kết tốc độ/độ sẵn sàng cho nhiều người dùng.
- Cảnh báo hiện hiển thị trong web; chưa gửi SMS, email, ứng dụng nhắn tin hoặc điều phối lực lượng thực địa.
- Benchmark SMOKE5K mô tả đúng checkpoint và tập đánh giá đã ghi; không dùng kết quả một ảnh demo để tuyên bố độ chính xác thực tế.

## 4. Hướng phát triển để nhóm thảo luận

Các mục dưới đây **chưa được triển khai và chưa có lịch cam kết**. Mỗi mục cần issue, người phụ trách và tiêu chí chấp nhận trước khi phát triển.

| Hạng mục | Cần thống nhất trước |
| --- | --- |
| Chất lượng FoSp trên ảnh thực địa | Tập đánh giá độc lập, lỗi khói mỏng/mây/sương, tiêu chí precision/recall và cách chọn ngưỡng |
| Camera / video | Nguồn dữ liệu, tần suất lấy khung, hàng đợi, timeout và tránh cảnh báo trùng |
| Chia sẻ theo nhóm | Vai trò, quyền ảnh/cảnh báo, cách bàn giao và lịch sử thao tác |
| Thông báo bên ngoài | Kênh gửi, điều kiện gửi, xác nhận người nhận và chống gửi trùng |
| Triển khai server | Tài nguyên GPU, storage/backup, HTTPS, vận hành và ngân sách |

## 5. Hoàn thành một thay đổi

Một thay đổi sẵn sàng review khi phạm vi rõ, chạy được theo README, có kiểm tra phù hợp, cập nhật migration/cấu hình/tài liệu nếu liên quan và không làm hỏng demo hoặc luồng FoSp hiện có. Chỉ báo “đã triển khai” khi tính năng thực sự có trong mã và đã được xác minh.

Dùng nhánh feature/fix và PR theo [CONTRIBUTING](../CONTRIBUTING.md). Main là bản ổn định; thay đổi model/thuật toán cần được mô tả và đánh giá riêng. Không tải/cài thêm tài nguyên trên máy chủ dự án khi chưa được chủ máy đồng ý.
