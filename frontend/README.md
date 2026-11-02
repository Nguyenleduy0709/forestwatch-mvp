# Frontend — HTML, CSS, TypeScript

| Đường dẫn | Nội dung |
| --- | --- |
| `templates/` | Django templates: trang chủ, đăng nhập, giám sát, kết quả, cảnh báo |
| `static/css/app.css` | Màu xanh rừng và bố cục responsive |
| `src/app.ts` | Xem trước ảnh, trạng thái xử lý, chuyển ảnh gốc/kết quả |
| `static/js/app.js` | JavaScript đã build; có sẵn để demo không cần Node |
| `static/img/forest.jpg` | Ảnh rừng dùng trong trang chủ |

Màn hình giám sát và kết quả dùng bố cục dọc theo phác thảo: vùng ảnh ở trên, thông số và thao tác bên dưới. Khung quan sát rộng vừa phải trên desktop và co theo điện thoại. Đây vẫn là phân tích ảnh tĩnh; không gắn nhãn camera trực tiếp cho dữ liệu upload.

Chạy từ thư mục gốc repo khi sửa TypeScript:

```powershell
npm --prefix frontend ci
npm --prefix frontend run check
npm --prefix frontend run build
```

CSS và templates có thể sửa trực tiếp rồi tải lại trang Django. Frontend chưa phải SPA và không có lệnh `npm start`; xem giao diện qua server Django ở http://127.0.0.1:8000.
