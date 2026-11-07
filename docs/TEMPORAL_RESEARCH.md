# Nghiên cứu FoSp-Temporal

Trạng thái: **thử nghiệm, web chưa dùng**. Mã ở [research/temporal_fosp](../research/temporal_fosp/README.md), ghi chú Day 1 ở [research/day1](../research/day1/README.md). Cập nhật 07/11/2026.

## 1. Mục tiêu và giả thuyết

Cải tiến FoSp (AAAI 2024) cho phân đoạn khói sớm từ camera cố định. Mục tiêu là giảm bỏ sót khói nhỏ hoặc mỏng và giảm nhầm sương/mây.

Ý tưởng: lấy ảnh quá khứ của cùng camera làm **nền quan sát**, đặt cạnh nền FoSp tự đoán bằng inpainting (MAT). Một đầu phân xử nhỏ đánh giá độ tin cậy của nền quan sát theo từng pixel, từng tầng. Khi không có lịch sử, mô hình trở về đúng FoSp.

Không mặc định F1 sẽ tăng. Mọi kết luận cần đối chứng cùng ngân sách train và khoảng tin cậy. Mục tiêu công bố: tạp chí Q1 (Pattern Recognition, IEEE TCSVT, EAAI, ESWA).

## 2. Dữ liệu và giao thức đánh giá

### 2.1 Split chính thức của SmokeSeg bị rò rỉ

- 100% ảnh test có cùng chuỗi (sự kiện + camera) trong train. Khoảng cách thời gian trung vị tới ảnh train gần nhất là 3,4 phút; với val là 1,0 phút.
- Không có ảnh âm tính, nên không đo được báo động giả.
- Code FoSp dùng beta = 1 (tức F1), ngưỡng 0,4, và MAT lấy latent z ngẫu nhiên mỗi lần forward. Pilot này cố định z = 0.

Kiểm tra bằng `research/temporal_fosp/split_leakage_audit.py`.

### 2.2 Căn ảnh SmokeSeg vào khung FIgLib

Ảnh SmokeSeg 512×512 là vùng cắt vuông cao bằng cả khung FIgLib, tỉ lệ 3× hoặc 4×, lệch ngang khác nhau theo từng ảnh. Đăng ký SIFT + RANSAC affine, nhận khi NCC ≥ 0,90: thành công 5200/5202 ảnh, NCC trung vị 0,995. FIgLib hiện ở `https://cdn.hpwren.ucsd.edu/HPWREN-FIgLib-Data/<chuỗi>/index.html`; dấu `+` trong tên file phải viết `%2B`.

### 2.3 Split Day 1 (băm theo ngày)

Băm theo ngày sự kiện, loại các ngày trùng SMOKE5K.

| Split | Ảnh | Ngày | Trạm |
| --- | ---: | ---: | ---: |
| train | 4218 | 63 | 26 |
| val | 283 | 5 | 9 |
| test | 701 | 6 | 14 |

Cả 14 trạm của test đều có trong train, nên đây **chưa phải** đánh giá tách camera/trạm.

### 2.4 Nền quan sát và khung âm tính

- Nền quan sát: trung vị 7 khung FIgLib từ 40 đến 20 phút trước lúc cháy, đã căn theo vùng cắt của từng ảnh.
- Khung âm tính thật: tối đa 6 khung mỗi chuỗi, từ 15 đến 1 phút trước lúc cháy, chỉ ở val/test. Tổng 174 khung (val 60, test 114).
- Các khung này là cảnh bình thường trước lúc cháy, không chọn riêng ngày sương/mây. Nền của chúng (20–40 phút trước) rất gần về thời gian, nên tỉ lệ báo động giả của mô hình có nền có thể lạc quan.

### 2.5 Thước đo

- **image-F1 / image-IoU**: trung bình F1/IoU của từng ảnh (beta = 1), ngưỡng 0,4, mask 512×512, bỏ pixel nhãn 255.
- **pixel-F1**: gộp TP/FP/FN của mọi ảnh rồi mới tính.
- **Kích thước khói** theo tỉ lệ diện tích nhãn: small < 0,5%; medium 0,5–2,5%; large > 2,5%.
- **Thời điểm**: phút sau lúc cháy, chia 0–10, 10–20, > 20.
- **Báo động giả** trên khung âm tính: tỉ lệ khung có ≥ 1, ≥ 50, ≥ 200 pixel vượt ngưỡng (trên 512×512, tức ≈ 0,0004%, 0,019%, 0,076% ảnh).
- **Δ và CI 95%**: bootstrap ghép cặp 2000 lần, lấy mẫu lại theo **ngày** (test chỉ 6 nhóm, CI thô).

## 3. Kết quả pilot GPU Day 2

Kernel Kaggle `forestwatch-day2-temporal-fosp-gpu-pilot`, Tesla T4, torch 2.11.0+cu130, chạy 06/11/2026, tổng 98,8 phút.

**Đây là pilot khoảng 1 epoch:** E1 và E2 mỗi run 694 update × 6 ảnh, tức khoảng 0,99 epoch của 4218 ảnh train. Số update lấy từ ngân sách 35 phút của E1 (3,025 s/update). Checkpoint chọn theo image-F1 trên val ở update 347 và 694; cả E1 lẫn E2 đều chọn update 694.

**Giới hạn:** split chưa tách trạm (14/14 trạm test có trong train). Test chỉ có 6 ngày, val 5 ngày. Có 2/283 ảnh val không có nền quan sát.

**Bảng 3.1. Test, 701 ảnh** (P/R tính theo pixel, gộp mọi ảnh)

| Run | image-F1 | image-IoU | pixel-F1 | P | R |
| --- | ---: | ---: | ---: | ---: | ---: |
| E0 FoSp-SMOKE5K 14k, đóng băng | 0,5649 | 0,4523 | 0,7603 | 0,8347 | 0,6982 |
| E1 fine-tune ảnh đơn | 0,6619 | 0,5509 | 0,8357 | 0,8553 | 0,8169 |
| E2 nền quan sát + phân xử (convex) | 0,6648 | 0,5529 | 0,7954 | 0,8422 | 0,7536 |
| E2, tắt nền (no-ref) | 0,6648 | 0,5529 | 0,7954 | 0,8422 | 0,7536 |
| E2, nền của camera khác (wrong-ref) | 0,6648 | 0,5529 | 0,7954 | 0,8422 | 0,7536 |

**Bảng 3.2. image-F1 theo kích thước khói và thời điểm** (test, n trong ngoặc)

| Run | small (373) | medium (220) | large (108) | 0–10 phút (141) | 10–20 phút (179) | > 20 phút (381) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| E0 | 0,4713 | 0,6197 | 0,7765 | 0,3024 | 0,6675 | 0,6138 |
| E1 | 0,5767 | 0,7110 | 0,8562 | 0,4117 | 0,7398 | 0,7179 |
| E2 | 0,5847 | 0,7193 | 0,8301 | 0,4011 | 0,7524 | 0,7212 |

**Bảng 3.3. Báo động giả trên 174 khung không khói thật** (val + test, 1–15 phút trước lúc cháy). Tỉ lệ khung có ít nhất N pixel vượt ngưỡng 0,4 trên mask 512×512.

| Run | ≥ 1 px | ≥ 50 px | ≥ 200 px |
| --- | ---: | ---: | ---: |
| E0 | 13,8% | 9,2% | 2,3% |
| E1 | 26,4% | 21,8% | 11,5% |
| E2 | 21,3% | 14,4% | 8,0% |

**Bảng 3.4. Δ và CI 95%** (bootstrap ghép cặp 2000 lần, lấy mẫu lại theo ngày)

| So sánh | Split | Thước đo | Δ | CI 95% | P(Δ ≤ 0) |
| --- | --- | --- | ---: | --- | ---: |
| E1 − E0 | test (6 ngày) | image-F1 | +0,0970 | [+0,0645; +0,1603] | 0,000 |
| E2 − E1 | test (6 ngày) | image-F1 | +0,0028 | [−0,0119; +0,0383] | 0,409 |
| E2 − E1 | test (6 ngày) | image-IoU | +0,0021 | [−0,0131; +0,0326] | 0,420 |
| E2 − E1 | val (5 ngày) | image-F1 | +0,0045 | [−0,0111; +0,0365] | 0,329 |
| E2 − E2 no-ref | test (6 ngày) | image-F1 | 0,0000 | [0; 0] | 1,000 |

**Nhận định**

1. Fine-tune trên SmokeSeg-train tăng image-F1 test thêm 0,097, CI không chứa 0. Đổi lại, báo động giả tăng gần gấp đôi: tỉ lệ khung có ≥ 1 px tăng từ 13,8% lên 26,4%.
2. **E2 không dùng nền quan sát.** Ở tầng cuối trên test, trọng số trung bình dành cho nền quan sát là 3,1·10⁻⁶ ở vùng nền và 4,6·10⁻⁸ ở vùng khói. E2, E2 no-ref và E2 wrong-ref trùng nhau đến chữ số cuối, và Δ(E2 − E2 no-ref) = 0.
   - Vì vậy chênh lệch E2 − E1 (+0,0028, CI chứa 0) và chênh lệch báo động giả giữa E2 và E1 đều do khác biệt ngẫu nhiên khi train (thứ tự dữ liệu, tăng cường), không phải do nền quan sát.
3. **Giả thuyết về nguyên nhân (chưa kiểm chứng riêng):**
   - Softmax khởi tạo nghiêng về FoSp (bias 2,5, nền quan sát chỉ được trọng số khoảng 0,08), và conv nền của FoSp vốn được train cho đặc trưng inpainting. Giảm trọng số nền quan sát là cách giảm loss nhanh nhất.
   - Khi softmax đã bão hòa, gradient tỉ lệ với w(1 − w) ≈ 0, nên nhánh này không bật lại được.
   - Dấu hiệu ủng hộ: pixel-F1 trên val của E2 ở update 347 tụt còn 0,5155 (E1 lúc đó 0,7032), rồi hồi lại 0,6885 ở update 694.
4. **Theo quy tắc quyết định:** CI của Δ(E2 − E1) chứa 0, nên không train dài thiết kế convex.

Log và output gốc lưu trên máy, ngoài Git, ở `outputs/day2_gpu/`: `forestwatch-day2-temporal-fosp-gpu-pilot.log`, `fw_day2_run/per_image.json`, và hai checkpoint `*_best.pth`.

## 4. Hạn chế và bước tiếp

**Hạn chế của pilot**
- Chỉ train khoảng 1 epoch.
- Split chưa tách trạm.
- Test chỉ có 6 ngày, nên CI thô.
- Khung âm tính là cảnh bình thường ngay trước lúc cháy, chưa phải ngày sương/mây.

**Đang chạy (07/11/2026): train trên máy, RTX 5070 Laptop 8 GB, torch 2.7.1+cu128**

Code của lượt này nằm trong `research/temporal_fosp/` (`fw_temporal.py`, `train_temporal.py`, `train_local.ipynb`), commit ngay sau `cfe1665`.
- Chạy bằng notebook (papermill). Micro-batch 1 × tích lũy 6, tức cùng 6 ảnh mỗi update như pilot.
- Bước chạy thử, với E2 kiểu convex: 1,31 s/update cho E1 và 1,53 s/update cho E2, VRAM đỉnh 6,03 GB.
- E1 và E2 mỗi run 6000 update (khoảng 8,5 epoch), 8 lần kiểm tra val, checkpoint chọn theo val, test chỉ chấm một lần ở cuối.
- **E2 dùng kiểu ghép `residual`** (`FW_FUSION=residual`, `FW_INIT_BIAS=0`):
  - Nhánh nền của FoSp giữ nguyên.
  - Đặc trưng nền quan sát, nhân với cổng tin cậy, được cộng vào sau conv nền của FoSp qua một conv 1×1 khởi tạo bằng 0.
  - Đã kiểm trên GPU: tại khởi tạo, mô hình trùng FoSp (sai khác lớn nhất 0,0 trên ảnh test thật), cổng bằng 0,5 và conv mới nhận gradient ở cả 4 tầng.
  - Kiểu `convex` của pilot vẫn giữ để tái lập.
- Bảo vệ laptop: khi GPU ≥ 90 °C trong khoảng 1 phút thì tạm dừng, chờ nguội về 75 °C rồi chạy tiếp. Cứ 75 phút train thì nghỉ 10 phút. Thời gian dừng và nghỉ không tính vào ngân sách.

**Sau lượt này**
- Đánh giá K-fold nhóm theo trạm.
- Bổ sung ảnh sương/mây thật: khung trước lúc cháy của FIgLib vào ngày có sương/mây, video PYRONEAR-2025, kho HPWREN.
- Thêm baseline đưa nền vào như kênh đầu vào và baseline dùng hiệu ảnh, cùng ngân sách update.
