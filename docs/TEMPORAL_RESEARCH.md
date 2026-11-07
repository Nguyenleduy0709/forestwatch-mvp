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

Log và output gốc lưu trên máy, ngoài Git, ở `outputs/day2_gpu/`: `forestwatch-day2-temporal-fosp-gpu-pilot.log` và `fw_day2_run/per_image.json`. Hai checkpoint pilot `*_best.pth` đã xoá khỏi máy ngày 07/11/2026 vì đã có model tốt hơn; chúng vẫn còn trong Output Kaggle `forestwatch-day2-temporal-fosp-gpu-pilot`.

## 4. Hạn chế và bước tiếp

**Hạn chế của pilot**
- Chỉ train khoảng 1 epoch.
- Split chưa tách trạm.
- Test chỉ có 6 ngày, nên CI thô.
- Khung âm tính là cảnh bình thường ngay trước lúc cháy, chưa phải ngày sương/mây.

**Lượt 2 (07/11/2026): train trên máy, RTX 5070 Laptop 8 GB, torch 2.7.1+cu128, E2 kiểu ghép `residual`**

Code: commit `1d667e0` (`FW_FUSION=residual`, `FW_INIT_BIAS=0`).
- Chạy bằng notebook (papermill). Micro-batch 1 × tích lũy 6, tức cùng 6 ảnh mỗi update như pilot.
- E1 và E2 mỗi run 6000 update (khoảng 8,5 epoch), 8 lần kiểm tra val, checkpoint chọn theo val image-F1, test chỉ chấm một lần ở cuối.
- Kiểu `residual`: nhánh nền của FoSp giữ nguyên; đặc trưng nền quan sát, nhân với cổng tin cậy, được cộng vào sau conv nền của FoSp qua một conv 1×1 khởi tạo bằng 0. Tại khởi tạo mô hình trùng FoSp (sai khác lớn nhất 0,0 trên ảnh test thật).
- Bảo vệ laptop: khi GPU ≥ 90 °C trong khoảng 1 phút thì tạm dừng, chờ nguội về 75 °C rồi chạy tiếp. Cứ 75 phút train thì nghỉ 10 phút. Thời gian dừng và nghỉ không tính vào ngân sách.
- Thời gian: E1 129 phút (1,17 s/update), E2 174 phút (1,54 s/update), VRAM đỉnh 6,03 GB, GPU khoảng 81 °C khi train.

Kết quả trên test (701 ảnh, 6 ngày) và trên 174 khung không khói thật:

| | image-F1 | image-IoU | pixel-F1 | P | R | small | 0–10 phút | báo nhầm ≥ 1 / ≥ 200 px |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| E1, best ở update 4500 | 0,6921 | 0,5836 | 0,8514 | 0,9010 | 0,8070 | 0,6024 | 0,4553 | 24,7 / 12,1% |
| E2 residual, best ở update 6000 | 0,6718 | 0,5626 | 0,8239 | 0,9080 | 0,7540 | 0,5830 | 0,4396 | 32,2 / 12,6% |

Bootstrap ghép cặp theo ngày (`research/temporal_fosp/tools/compare_runs.py` cho các cặp khác lượt):

| Cặp | Chỉ số | Δ | CI 95% |
| --- | --- | ---: | --- |
| E2 residual − E1 | test image-F1 | −0,0203 | [−0,0387; −0,0113] |
| E2 residual − E1 | val image-F1 | +0,0039 | [−0,0297; +0,0201] |
| E2 residual − E1 | báo nhầm ≥ 1 px | +0,0747 | [+0,0069; +0,2174] |
| E1 lượt 2 − E1 pilot | test image-F1 | +0,0302 | [+0,0072; +0,0653] |
| E1 lượt 2 − E0 | test image-F1 | +0,1272 | [+0,0758; +0,2190] |
| E1 lượt 2 − E0 | báo nhầm ≥ 1 px | +0,1092 | [+0,0064; +0,2037] |

Nhận xét:
1. **Cổng lại tắt.** w_obs ở tầng cuối trên test là 7·10⁻¹² ở vùng khói và 6·10⁻⁹ ở nền; E2, E2 no-ref và E2 wrong-ref trùng nhau. Loss tin cậy về khoảng 0 từ update 300. Vậy E2 kém E1 không phải do nền quan sát, mà do quỹ đạo train của phần FoSp khác đi.
2. **Nguyên nhân, áp dụng cho cả convex và residual:** `reliability_loss` chỉ đẩy cổng xuống ở pixel biết là hỏng. Lúc đầu conv của nhánh nền bằng 0 nên loss phân đoạn không cho cổng gradient nào. Không có lực kéo lên, cổng xuống 0 rồi softmax bão hòa.
3. E1 dài hơn E1 pilot (+0,030, nhưng khác cả số update lẫn máy). So với E0, fine-tune tăng F1 rõ nhưng tỉ lệ báo nhầm ≥ 1 px tăng 10,9 điểm, vì train không có mẫu âm nào.

Checkpoint E1, log, `per_image.json` và `thermal.csv` được giữ trong gói `checkpoints/temporal_fosp_2026-11-07/e1_u6000/` (ngoài Git). Checkpoint E2 residual đã xoá vì cổng tắt, model tương đương FoSp.

**Lượt 3 (07/11/2026, dừng giữa chừng): mẫu âm khi train, cổng giám sát hai chiều, kiểu ghép `delta`**

Kế hoạch là E1 và E2 cùng 6000 update, cùng dữ liệu, cùng mẫu âm. E1 chạy đủ 6000 update. Lượt chạy được dừng theo yêu cầu khi E2 mới ở update 300, để chuyển hẳn việc train sang Kaggle. Lúc đó w_obs của E2 là 0,72, tức cổng chưa tắt, nhưng chưa có kết quả E2.

| | val image-F1 | test image-F1 | image-IoU | pixel-F1 | P | R | small | 0–10 phút | báo nhầm ≥ 1 / ≥ 50 / ≥ 200 px |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| E1 lượt 2, không mẫu âm, best ở update 4500 | 0,4777 | 0,6921 | 0,5836 | 0,8514 | 0,9010 | 0,8070 | 0,6024 | 0,4553 | 24,7 / 19,0 / 12,1% |
| E1 + 20% mẫu âm, best ở update 5250 | 0,4748 | 0,6860 | 0,5726 | 0,8334 | 0,8696 | 0,8001 | 0,6028 | 0,4787 | 15,5 / 9,8 / 5,7% |

Mẫu âm giảm tỉ lệ khung báo nhầm khoảng một nửa (≥ 1 px: 24,7% → 15,5%; ≥ 200 px: 12,1% → 5,7%), còn test image-F1 chênh −0,006. **Chưa có CI** cho cặp này: lượt chạy dừng trước khi ghi `per_image.json`. Checkpoint còn trong gói `checkpoints/temporal_fosp_2026-11-07/e1_neg20_u6000/`, nên có thể chấm lại để tính bootstrap.

Thiết kế của lượt 3:
- **Mẫu âm khi train** (`FW_TRAIN_NEG=0.2`, cho cả E1 và E2): 20% mẫu train được thay bằng chính ảnh nền trước lúc cháy của ảnh đó (trung vị khung 20–40 phút trước khi cháy), mask rỗng. Ảnh nền của mẫu âm lấy từ một ảnh khác cùng chuỗi có phép nắn lệch ≤ 1,5 px; 3853/4218 ảnh train có cặp như vậy. Không dùng khung nào của val/test.
- **Cổng giám sát hai chiều** (`FW_GATE_LOSS=bce`): BCE giữa trọng số nền quan sát và bản đồ hợp lệ của ảnh nền (1 nếu hợp lệ, 0 ở vùng bị làm hỏng), chỉ tính trên mẫu có ảnh nền. Cổng thành bộ phân loại độ tin cậy; mức dùng ảnh nền hợp lệ do conv nhánh nền quyết định.
- **Kiểu ghép `delta`** (`FW_FUSION=delta`): nhánh nền nhận w·(F_quan sát − F_suy luận) thay vì w·F_quan sát. Ở nơi ảnh nền cho thấy không đổi, nhánh này có thể triệt tiêu phần nền suy luận sai của FoSp, điều mà `residual` không làm được. Tại khởi tạo vẫn trùng FoSp.
- Theo dõi thêm: w_obs trong log TRAIN và VAL; tỉ lệ báo nhầm trên 60 khung âm của val ở mỗi lần kiểm tra (chỉ để theo dõi, checkpoint vẫn chọn theo val image-F1); bootstrap theo ngày cho báo nhầm.
- Hạn chế: ảnh âm là ảnh trung vị nên mịn hơn khung thật, và cặp âm gần như trùng khít (độ lệch trung vị của cặp gần nhất 0,07 px). Lượt này đổi ba thứ cùng lúc, nên E2 − E1 cho biết tác dụng của nhánh nền khi có mẫu âm, còn từng thay đổi cần ablation riêng.

**Vì sao FoSp đạt 76,88% trên SMOKE5K nhưng thấp hơn ở đây**

Con số 76,88% là image-F1 của FoSp 14k trên 400 ảnh test SMOKE5K, chấm ở độ phân giải mask gốc. Trên 468 ảnh val tách từ train SMOKE5K, F1 là 0,9024. Cách đo giống phần này: F1 trung bình theo ảnh, ngưỡng 0,4.
- Trong 400 ảnh test đó, 63 ảnh camera cố định (HPWREN/FIgLib, tên `<timestamp>_<offset>`) chỉ đạt F1 0,5076. 337 ảnh còn lại đạt 0,8176.
- Trên test SmokeSeg của mình (khói sớm, chia theo ngày), cùng FoSp đó (E0) đạt 0,5649. Số này gần với nhóm camera cố định của SMOKE5K.

Vậy khoảng cách đến từ dữ liệu, không phải do cách đo.

**Lượt 4 (07/11/2026, Kaggle T4): thêm SMOKE5K vào train**

Từ lượt này chỉ train trên Kaggle. Có hai notebook private trên T4, chạy song song, khác nhau duy nhất ở dữ liệu train:
- **A** (`forestwatch-day3-s5k-a`): chỉ SmokeSeg (4218 ảnh).
- **B** (`forestwatch-day3-s5k-b`): SmokeSeg cộng 4589 cặp train SMOKE5K, tức bộ FoSp 14k đã học. Các cặp SMOKE5K chiếm khoảng 52% số mẫu.

Điểm chung của A và B:
- Mỗi nhánh có E0 và E1, mỗi run 6000 update × 6 ảnh (micro-batch 2 × tích lũy 3), 8 lần kiểm tra val, checkpoint chọn theo val image-F1 của SmokeSeg.
- Không dùng mẫu âm khi train, để chỉ đổi một yếu tố.
- Chấm trên test SmokeSeg (701 ảnh), 174 khung không khói, và test SMOKE5K (400 ảnh ở độ phân giải gốc, cùng cách ra 76,88%). Nhóm 63 ảnh camera cố định được tách riêng.
- Không đoạn quay nào của SMOKE5K trùng train/val/test của mình (đã kiểm tra trên hai manifest).

Câu hỏi của lượt này:
1. Thêm SMOKE5K có giúp test SmokeSeg không, khi cùng ngân sách update?
2. Fine-tune trên SmokeSeg có làm mất độ chính xác trên SMOKE5K không?

Code: `FW_S5K_TRAIN`, `FW_S5K_EVAL` trong `train_temporal.py`; notebook dựng bằng `research/temporal_fosp/make_kaggle_notebook.py --preset day3-s5k-a|day3-s5k-b`.

Kết quả (log `day3_s5k_a.log`, `day3_s5k_b.log`, bản sao trong `checkpoints/temporal_fosp_2026-11-07/kaggle_day3/`). E1 train 276,6 phút ở A và 264,5 phút ở B (2,6–2,8 s/update, VRAM đỉnh 13,8 GB); cả notebook 292 và 279 phút.

| | val image-F1 | test image-F1 | image-IoU | pixel-F1 | P | R | small | 0–10 phút | báo nhầm ≥ 1 / ≥ 50 / ≥ 200 px | test SMOKE5K | SMOKE5K camera cố định |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| E0, FoSp 14k | 0,3501 | 0,5649 | 0,4523 | 0,7603 | 0,8347 | 0,6982 | 0,4713 | 0,3024 | 13,8 / 9,2 / 2,3% | 0,7694 | 0,5076 |
| A: E1 chỉ SmokeSeg, best ở update 3750 | 0,4773 | 0,6850 | 0,5732 | 0,8364 | 0,8973 | 0,7831 | 0,5959 | 0,4681 | 25,9 / 16,1 / 8,0% | 0,7270 | 0,5769 |
| B: E1 + SMOKE5K, best ở update 3000 | **0,4966** | 0,6572 | 0,5404 | 0,7783 | 0,7576 | 0,8002 | 0,5800 | 0,4137 | 19,5 / 8,0 / 2,3% | 0,7688 | 0,5714 |

E0 chấm lại trên test SMOKE5K được 0,7694, khớp con số 76,88% của notebook FoSp 14k (chênh 0,0006).

| Cặp | Chỉ số | Δ | CI 95% |
| --- | --- | ---: | --- |
| A − E0 | test image-F1 | +0,1202 | [+0,0720; +0,2027] |
| A − E0 | test SMOKE5K image-F1 | −0,0424 | [−0,0627; −0,0217] |
| A − E0 | báo nhầm ≥ 200 px | +0,0575 | [0,0000; +0,1207] |
| A − E1 lượt 2 | test image-F1 | −0,0071 | [−0,0449; +0,0124] |
| B − E0 | test image-F1 | +0,0923 | [+0,0407; +0,2085] |
| B − E0 | val image-F1 | +0,1465 | [+0,0456; +0,1979] |
| B − E0 | báo nhầm ≥ 1 px | +0,0575 | [−0,0476; +0,1364] |
| B − E0 | báo nhầm ≥ 200 px | 0,0000 | [−0,0161; +0,0125] |
| B − E0 | test SMOKE5K image-F1 | −0,0006 | [−0,0148; +0,0142] |
| B − E0 | SMOKE5K camera cố định (63 ảnh) | +0,0638 | [+0,0235; +0,1058] |
| B − A | test image-F1 | −0,0278 | [−0,0701; +0,0472] |
| B − A | báo nhầm ≥ 1 px | −0,0632 | [−0,1474; +0,0123] |
| B − A | báo nhầm ≥ 200 px | −0,0575 | [−0,1334; 0,0000] |
| B − A | test SMOKE5K image-F1 | +0,0418 | [+0,0286; +0,0549] |
| B − A | SMOKE5K camera cố định | −0,0056 | [−0,0462; +0,0339] |

Trả lời hai câu hỏi của lượt này:
1. **Thêm SMOKE5K không giúp test SmokeSeg.** B − A = −0,028, CI [−0,070; +0,047], tức chưa phân biệt được. B có val cao hơn nhưng test thấp hơn. Mask của B rộng hơn: precision theo pixel 0,7576 so với 0,8973.
2. **Fine-tune chỉ trên SmokeSeg làm FoSp quên tập gốc.** A tụt 0,042 trên test SMOKE5K (CI [−0,063; −0,022]), còn B giữ nguyên. Cả hai nhánh cùng tăng nhóm camera cố định của SMOKE5K, khoảng +0,064 đến +0,069.

Ngoài ra B báo nhầm ít hơn A: ≥ 200 px là 2,3% so với 8,0%, bằng FoSp gốc. A lặp lại được E1 lượt 2 trên laptop (−0,007, CI có 0).

**Chọn nhánh:** theo quy tắc đặt trước (chỉ nhìn val), **B thắng** (0,4966 so với 0,4773). Các lượt sau dùng cấu hình dữ liệu của B. Hai điểm cộng của B, báo nhầm ít và không quên SMOKE5K, chỉ được ghi nhận, không dùng để chọn. Checkpoint `day3_s5k_b/E1_single_best.pth` được tải về `checkpoints/temporal_fosp_2026-11-07/kaggle_day3/b/`.

**Lượt 5a (đẩy lên Kaggle 16:09, 07/11/2026), cấu hình dữ liệu của B:**
- `forestwatch-day4-final`: E0 + E1 với loss soft-F1, ô cắt phóng tới 2×, 20% mẫu âm, phép dò cách chấm và ngưỡng trên val, `FW_EXAMPLES=24`. So với B, lượt này đổi ba thứ cùng lúc.
- `forestwatch-day4-e2`: E2 (`delta`, cổng `bce`, `init_bias` 0), cùng 6000 update, kèm phép dò và hai phép tách nguồn lợi ích (no-ref, wrong-ref). Đối chứng là E1 của B: cùng dữ liệu, cùng ngân sách, cùng seed.

**Kết quả Final (xong 22:07, 07/11/2026; 323 phút, best ở update 2250):**

| Run | Val | Test image-F1 | IoU | 0–10 phút | Khói nhỏ | Báo nhầm ≥ 1 / 50 / 200 px | SMOKE5K | Camera SMOKE5K |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| E0 | 0,3501 | 0,5649 | 0,4523 | 0,3024 | 0,4713 | 13,8 / 9,2 / 2,3% | 0,7694 | 0,5076 |
| B (lượt 4) | 0,4966 | 0,6572 | 0,5404 | 0,4137 | 0,5800 | 19,5 / 8,0 / 2,3% | 0,7688 | 0,5714 |
| Final, ngưỡng 0,4 | 0,4952 | 0,6523 | 0,5403 | 0,4318 | 0,5655 | 11,5 / 9,8 / 7,5% | 0,7819 | 0,5433 |
| Final + `zoom` @ 0,25 (chọn trên val) | 0,5312 | 0,6818 | 0,5682 | 0,5123 | 0,6013 | 18,4 / 16,7 / 10,9% | 0,7820 | 0,5715 |

Bootstrap theo ngày (`compare_runs.py` cho cặp khác notebook):

| Cặp | Test image-F1 | Báo nhầm ≥ 200 px (điểm %) | SMOKE5K |
| --- | --- | --- | --- |
| Final − B | −0,0049 [−0,0980; +0,0301] | +5,2 [−1,8; +15,8] | +0,0131 [+0,0003; +0,0263] |
| Final `zoom` − Final | +0,0296 [+0,0152; +0,0552] | +3,4 [0; +7,7] | +0,0001 [−0,0102; +0,0119] |
| Final `zoom` − E0 | +0,1169 [+0,0758; +0,1806] | +8,6 [−1,1; +17,6] | +0,0126 [−0,0056; +0,0308] |
| Final `zoom` − B | +0,0246 [−0,0620; +0,0605] | +8,6 [0; +17,9] | +0,0132 [−0,0019; +0,0291] |

- Ba thay đổi khi train (loss soft-F1, ô phóng to, 20% mẫu âm) không nâng test so với B ở cùng cách chấm, và val thấp hơn B 0,0014. Theo quy tắc chọn bằng val, **B vẫn là model train được chọn**.
- Phóng to khi chấm (4 ô 320 px) với ngưỡng 0,25 cộng +0,030 cho cùng model, nhiều nhất ở 10 phút đầu (0,4318 → 0,5123). Đổi lại báo nhầm ≥ 200 px tăng từ 7,5% lên 10,9%. Val 0,5312 lạc quan vì được chọn trong 24 cặp trên chính val. B chưa được dò như vậy (`day4-tta-b` chưa chạy).
- So với B, báo nhầm ≥ 1 px của Final giảm (19,5% → 11,5%) nhưng ≥ 200 px tăng (2,3% → 7,5%). Lượt này đổi ba thứ cùng lúc nên chưa biết do thứ nào.
- Checkpoint Final nằm trong Output Kaggle `forestwatch-day4-final` (`day4_final/E1_single_best.pth`), chưa tải. Log, `per_image.json` và 48 ảnh ví dụ nằm ở `checkpoints/temporal_fosp_2026-11-07/kaggle_day4/final/`.
- `forestwatch-day4-e3-concat` được đẩy lúc 22:08 vào chỗ GPU của Final: E3 với cùng cấu hình dữ liệu và cùng phép dò như E2.

**Kết quả E2 (xong khoảng 22:45, 07/11/2026; train 326 phút, best ở update 6000, tức val còn tăng khi hết ngân sách):**

| Run | Val | Test image-F1 | IoU | 0–10 phút | Khói nhỏ | Báo nhầm ≥ 1 / 50 / 200 px | SMOKE5K | Camera SMOKE5K |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| B (lượt 4) | 0,4966 | 0,6572 | 0,5404 | 0,4137 | 0,5800 | 19,5 / 8,0 / 2,3% | 0,7688 | 0,5714 |
| E2, ngưỡng 0,4 | 0,4660 | 0,6598 | 0,5423 | 0,4321 | 0,5842 | 23,6 / 18,4 / 9,8% | 0,7802 | 0,5502 |
| E2 + `zoom` @ 0,3 (chọn trên val) | 0,5033 | 0,6974 | 0,5814 | 0,4978 | 0,6349 | 30,5 / 23,6 / 16,1% | 0,8000 | 0,5953 |
| E2 bỏ ảnh nền (no-ref) | | 0,6616 | 0,5483 | 0,4433 | 0,5871 | | | |
| E2 với nền của camera khác (wrong-ref) | | 0,6633 | 0,5465 | 0,4476 | 0,5868 | | | |

Cổng mở: w_obs trung bình trên test là 0,77 ở vùng khói và 0,86 ở nền (val: 0,76 / 0,81), không tắt như lượt 1 và 2.

| Cặp (bootstrap theo ngày) | Test image-F1 | Báo nhầm ≥ 200 px (điểm %) | SMOKE5K |
| --- | --- | --- | --- |
| E2 − B | +0,0026 [−0,0466; +0,0208] | +7,5 [+0,8; +15,3] | +0,0113 [+0,0033; +0,0194] |
| E2 − E2 no-ref (cùng model) | −0,0018 [−0,0121; +0,0174] | | |
| E2 `zoom` − E2 | +0,0376 [+0,0250; +0,0670] | +6,3 [+0,7; +14,5] | +0,0198 [+0,0114; +0,0293] |
| E2 `zoom` − B (B không phóng to) | +0,0402 [+0,0182; +0,0505] | +13,8 [+5,3; +23,5] | +0,0311 [+0,0192; +0,0440] |
| E2 `zoom` − Final `zoom` | +0,0156 [−0,0189; +0,0825] | +5,2 [−5,7; +18,3] | +0,0180 [+0,0053; +0,0295] |
| E2 − E0 | +0,0949 [+0,0561; +0,1704] | +7,5 [+1,8; +14,6] | |

- **Nền quan sát vẫn chưa giúp.** Lần này cổng mở, nhưng bỏ ảnh nền hay thay bằng nền của camera khác cho cùng kết quả (0,6616 và 0,6633 so với 0,6598). Nhánh nền được ghép vào mà không làm đổi dự đoán.
- **E2 không hơn B:** test +0,003 với CI chứa 0, val thấp hơn (0,4660 so với 0,4966), báo nhầm ≥ 200 px nhiều hơn (9,8% so với 2,3%). Theo quy tắc chọn bằng val, B vẫn là model được chọn. Giả thuyết chính chưa được ủng hộ ở lần thử thứ ba.
- **E2 + phóng to khi chấm** có test cao nhất từ trước tới nay (0,6974), nhưng phần tăng đến từ phóng to và ngưỡng 0,3, không từ ảnh nền (E2 ≈ E2 no-ref). Báo nhầm ≥ 200 px lên 16,1%. So với B là so khác cách chấm, vì B chưa được dò phóng to.
- Checkpoint E2 nằm trong Output Kaggle `forestwatch-day4-e2`, chưa tải. Log, `per_image.json` và 24 ảnh ví dụ ở `checkpoints/temporal_fosp_2026-11-07/kaggle_day4/e2/`.

**Phần còn thiếu để lên image-F1 0,80 (điểm từng ảnh của E1 lượt 2, test 701 ảnh)**

Test image-F1 0,6921; còn thiếu 75,6 điểm-ảnh để lên 0,80.

| Nhóm | Số ảnh | image-F1 | Ảnh F1 = 0 | Phần của tổng (1 − F1) |
| --- | ---: | ---: | ---: | ---: |
| Khói nhỏ (< 0,5% ảnh) | 373 | 0,6024 | 46 | 69% |
| Khói vừa | 220 | 0,7511 | 5 | 25% |
| Khói lớn | 108 | 0,8817 | 0 | 6% |
| 0–10 phút | 141 | 0,4553 | 42 | 36% |
| 10–20 phút | 179 | 0,7705 | 3 | 19% |
| > 20 phút | 381 | 0,7429 | 6 | 45% |

- 51 ảnh F1 = 0: 42 ảnh không có pixel nào vượt ngưỡng (bỏ sót hẳn), 9 ảnh chỉ đoán sai chỗ.
- Khói nhỏ có trung vị 484 px trên ảnh 512×512 (0,18%); 10% nhỏ nhất ≤ 122 px. Ảnh SmokeSeg gốc đã là 512×512, còn mask của FoSp ra ở 1/4 độ phân giải.
- Nếu cả 51 ảnh F1 = 0 lên bằng trung bình các ảnh còn lại (0,7464), F1 chung chỉ đạt 0,7464. Muốn tới 0,80 phải vừa bắt được khói sớm vừa vẽ mask khói nhỏ tốt hơn.

**Lượt 5 (code đã sẵn, chạy sau lượt 4)**
- `day4-tta-a` / `day4-tta-b`: không train. Nạp E1 tốt nhất của từng nhánh, chọn cách chấm (`none`, `flip`, `zoom`, `zoom+flip`) và ngưỡng (0,25–0,5) **chỉ trên val**, rồi chấm test, khung âm, SMOKE5K đúng một lần ở lựa chọn đó. Nhắm vào các ảnh bỏ sót có xác suất sát ngưỡng.
- `day4-dice-zoom`: E1 cùng 6000 update, thêm loss soft-F1 theo ảnh (`FW_DICE_WEIGHT=1`) và ô cắt phóng tới 2× quanh khói (`FW_ZOOM_MIN=256`), kèm phép dò ở trên. Cấu hình dữ liệu lấy theo nhánh thắng ở lượt 4. Lượt này đổi hai thứ cùng lúc, nên tác dụng riêng của từng thứ cần ablation sau.
- `day4-e2`, `day4-e3`, `day4-e4`: phép thử chính của bài báo, cùng dữ liệu, cùng ảnh nền, cùng hỏng hóc tổng hợp và cùng 6000 update:
  - **E2**: nền quan sát với cổng giám sát hai chiều và kiểu ghép `delta`.
  - **E3**: ghép ảnh nền vào làm kênh đầu vào (kiểu BSUV-Net).
  - **E4**: thêm kênh |ảnh − nền| (kiểu SmokeyNet).

  E3 và E4 không có bước phân xử. Ở cả hai, một conv khởi tạo 0 cộng vào đầu ra của conv đầu tiên trong backbone, nên lúc khởi tạo model trùng FoSp, và khi không có lịch sử thì vẫn trùng FoSp. Muốn chứng minh bước phân xử cần thiết thì E2 phải hơn cả E3 lẫn E4, có CI.

**Công trình liên quan (rà ngày 07/11/2026, chưa vét hết)**

Từng thành phần đã có người làm:
- [SmokeyNet / FIgLib](https://arxiv.org/html/2112.08598v3): dùng ảnh hiện tại và khung trước đó, phân loại ô ảnh. Kênh nền MOG2 tăng F1 1,94 điểm.
- [BSUV-Net](https://arxiv.org/pdf/1907.11371) và [BSUV-Net 2.0](https://arxiv.org/abs/2101.09585v1): đưa nền "trống" và nền gần đây vào làm đầu vào, cho bài toán trừ nền tổng quát.
- [SmokeCPC, WACV 2026](https://openaccess.thecvf.com/content/WACV2026/papers/Zhao_False_Alarm_Rectification_for_Early_Smoke_Segmentation_WACV_2026_paper.pdf): điểm tin cậy cấp ảnh, train thêm ảnh không khói, test trên khung FIgLib không khói. Vì vậy mẫu âm khi train **không phải đóng góp mới**.
- [TP-UAST](https://arxiv.org/html/2508.14597v1): kết hợp optical flow với ước lượng độ bất định, cho bài toán phát hiện.
- [Sinh khói trên nền camera cố định](https://arxiv.org/html/2511.16617v1): chỉ dùng để tăng dữ liệu.

Chưa thấy bài nào làm đúng tổ hợp sau:
- phân đoạn khói từng pixel, phân xử theo từng pixel và từng tầng giữa nền inpainting của FoSp và nền quan sát đã căn theo vùng cắt;
- độ tin cậy học bằng hỏng hóc tổng hợp; quay về FoSp khi không có lịch sử;
- đánh giá trên split không rò rỉ, có khung âm thật.

Loss F1, lật ảnh và phóng to khi chấm là kỹ thuật phổ biến, không tính là đóng góp. Trước khi nộp bài, cần quét các bài trích dẫn FoSp và các bài năm 2026, và so với SmokeCPC trên cùng giao thức khung âm.

**Sau lượt này**
- Đánh giá K-fold nhóm theo trạm.
- Bổ sung ảnh sương/mây thật: khung trước lúc cháy của FIgLib vào ngày có sương/mây, video PYRONEAR-2025, kho HPWREN.
- Thêm baseline đưa nền vào như kênh đầu vào và baseline dùng hiệu ảnh, cùng ngân sách update.
