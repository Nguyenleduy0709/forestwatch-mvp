# Tài liệu ForestWatch

Đọc [README gốc](../README.md) để cài, cấu hình và chạy local. Sau đó xem [phạm vi sản phẩm](PRODUCT.md) và [kiến trúc](ARCHITECTURE.md). Các tài liệu này mô tả ForestWatch hiện có, cập nhật ngày 04/11/2026.

| Tài liệu | Dùng khi nào |
| --- | --- |
| [Phạm vi sản phẩm](PRODUCT.md) | Nắm chức năng đã có, giới hạn và ý tưởng chưa triển khai |
| [Kiến trúc](ARCHITECTURE.md) | Hiểu thành phần, luồng ảnh/model/dữ liệu và phân quyền |
| [Backend](../backend/README.md) | Sửa Django, route, model và migration |
| [Frontend](../frontend/README.md) | Sửa templates, CSS, TypeScript và build JS |
| [Làm việc nhóm](../CONTRIBUTING.md) | Cập nhật main, nhánh feature/fix, PR, review và conflict |
| [Train và resume](../notebooks/README.md) | Làm việc với notebook/model, độc lập với chạy web |
| [Biên bản tái cấu trúc](RESTRUCTURING.md) | Kiểm tra 12 test, HTTP demo, FoSp GPU và quyết định tổ chức repo |
| [Kiểm chứng giao diện trước đây](VALIDATION.md) | Các lượt kiểm tra và ảnh chụp ở giai đoạn trước; số test phản ánh thời điểm ghi |
| [Benchmark FoSp 14.000](fosp_smoke5k_14000.json) | Xem số liệu checkpoint và tập đánh giá đã ghi |
| [Tài nguyên và giấy phép](ASSETS.md) | Nguồn ảnh và ghi công |

[CODEX_PROMPT.md](CODEX_PROMPT.md) và [PUBLISH.md](PUBLISH.md) là tài liệu chuẩn bị/phát hành từ giai đoạn trước. Khi bắt đầu công việc hiện tại, ưu tiên README và CONTRIBUTING; không áp dụng lại quy trình khởi tạo repo lên thư mục đang làm việc.

## Quy ước cập nhật

- Ghi rõ tính năng đã triển khai hay chỉ là đề xuất; cập nhật tài liệu cùng PR thay đổi hành vi.
- Lệnh cài/chạy/kiểm tra chuẩn đặt ở README gốc; tài liệu chuyên đề liên kết tới đó để tránh hướng dẫn lệch nhau.
- Không đưa token, mật khẩu, dữ liệu người dùng hoặc output notebook nhạy cảm vào tài liệu. File model/dataset/ZIP giữ ngoài Git.
- Chỉ thao tác ghi trong `D:\ForestWatch` trên máy hiện tại; hỏi chủ máy trước khi tải xuống hoặc cài thêm. Cache/file tạm cũng cần nằm trong ổ D.

Cách chia tài liệu kiến trúc và mục lục có tham khảo [MoodPrint](https://github.com/mhiu05/MoodPrint) cùng [mục lục docs của repo đó](https://github.com/mhiu05/MoodPrint/blob/main/docs/README.md). Nội dung được viết theo code Django của ForestWatch; các thành phần triển khai và công nghệ khác không thuộc cấu trúc hiện tại.
