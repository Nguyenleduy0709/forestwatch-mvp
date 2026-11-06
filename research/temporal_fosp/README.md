# FoSp-Temporal (nghiên cứu, chưa tích hợp vào web)

Thư mục này chứa thử nghiệm cải tiến FoSp cho camera cố định. Web Django hiện **không** dùng mã này. Xem kết quả và bối cảnh trong [docs/TEMPORAL_RESEARCH.md](../../docs/TEMPORAL_RESEARCH.md).

Ý tưởng: FoSp so ảnh với một nền **đoán** bằng inpainting (MAT): `10·|MAT(I, focus-mask) − MAT(I, không mask)|`.
Với camera cố định, ta so thêm với nền **quan sát**: trung vị các khung 20–40 phút trước khi có khói, đã căn theo vùng cắt của từng ảnh SmokeSeg.
Một đầu tin cậy nhỏ học trọng số softmax theo từng pixel, từng tầng, giữa hai nguồn nền.
Nó chỉ bị phạt ở chỗ chắc chắn nền tham chiếu hỏng (lệch hình, dán khói vào nền). Không có lịch sử thì mô hình trùng FoSp.

| File | Vai trò |
| --- | --- |
| `prep_temporal.py` | Notebook Kaggle chạy CPU, bật Internet, gắn Output của notebook FoSp 14.000. Dựng lại split Day-1, tải khung FIgLib từ CDN, đăng ký từng ảnh SmokeSeg vào khung gốc (SIFT + RANSAC affine, nhận khi NCC ≥ 0,90), tạo ảnh nền đã căn và khung âm tính thật cho val/test. |
| `fw_temporal.py` | `TemporalFoSp` bọc model FoSp chính thức. Gồm đầu phân xử, `reliability_loss`, hỏng hóc tổng hợp, metric, bootstrap theo sự kiện. |
| `train_temporal.py` | Notebook Kaggle GPU: E0 (FoSp SMOKE5K đóng băng), E1 (fine-tune ảnh đơn), E2 (fine-tune có nền quan sát, cùng số update), cộng hai phép tách nguồn lợi ích. Mọi kết quả in thành dòng JSON. |
| `build_kaggle_script.py` | Nhúng `fw_temporal.py` vào `train_temporal.py`, ghi `build/train_temporal_kaggle.py` để dán vào một cell. |
| `split_leakage_audit.py` | So hai danh sách ảnh SmokeSeg/FIgLib: trùng chuỗi, sự kiện, ngày, trạm, camera, và khoảng cách thời gian. |
| `tests/` | Kiểm tra CPU với FoSp + mmcv 1.6.2 thật (đặt `FOSP_DIR`, `MMCV_DIR` hoặc dùng `.fosp-runtime/`), và kiểm tra hàm đăng ký ảnh. |

## Chạy

1. Kaggle notebook CPU, bật Internet, Add Input là Output của notebook có `fosp_smoke5k_14000/manifest.json`. Dán `prep_temporal.py` vào một cell, chạy, rồi Save Version.
2. Kaggle notebook GPU T4, bật Internet. Add Input gồm: Output notebook FoSp SMOKE5K (có `FoSp-official/`, `mmcv-1.6.2-source/`, `fosp-deps/`), Output có `fosp_smoke5k_14000/best.pth`, Output của bước 1. Chạy `python build_kaggle_script.py`, dán `build/train_temporal_kaggle.py` vào một cell. Có thể đặt `FW_TOTAL_BUDGET` và `FW_E1_BUDGET` (giây).
3. Kiểm tra CPU tại máy (không cần GPU):

```powershell
$env:FOSP_DIR = 'D:\ForestWatch\.fosp-runtime\FoSp'
$env:MMCV_DIR = 'D:\ForestWatch\.fosp-runtime\mmcv'
.\.venv\Scripts\python.exe research\temporal_fosp\tests\test_fw_temporal_cpu.py
.\.venv\Scripts\python.exe research\temporal_fosp\tests\test_registration.py
```

Không commit checkpoint, ảnh nền, dataset hay output notebook. Các file đó nằm trong Output Kaggle.
