# FoSp-Temporal (nghiên cứu, chưa tích hợp vào web)

Thư mục này chứa thử nghiệm cải tiến FoSp cho camera cố định. Web Django hiện **không** dùng mã này. Xem kết quả và bối cảnh trong [docs/TEMPORAL_RESEARCH.md](../../docs/TEMPORAL_RESEARCH.md).

Ý tưởng: FoSp so ảnh với một nền **đoán** bằng inpainting (MAT): `10·|MAT(I, focus-mask) − MAT(I, không mask)|`.
Với camera cố định, ta so thêm với nền **quan sát**: trung vị các khung 20–40 phút trước khi có khói, đã căn theo vùng cắt của từng ảnh SmokeSeg.
Một đầu tin cậy nhỏ học trọng số softmax theo từng pixel, từng tầng, giữa hai nguồn nền. Không có lịch sử thì mô hình trùng FoSp.

**Mọi lượt train chạy trên Kaggle (T4).** Máy local chỉ dùng để sửa code, dựng notebook và đọc kết quả.

| File | Vai trò |
| --- | --- |
| `prep_temporal.py` | Notebook Kaggle chạy CPU, bật Internet, gắn Output của notebook FoSp 14.000. Dựng lại split Day-1, tải khung FIgLib từ CDN, đăng ký từng ảnh SmokeSeg vào khung gốc (SIFT + RANSAC affine, nhận khi NCC ≥ 0,90), tạo ảnh nền đã căn và khung âm tính thật cho val/test. |
| `fw_temporal.py` | `TemporalFoSp` bọc model FoSp chính thức. Gồm đầu phân xử, các kiểu ghép `convex`/`residual`/`delta`, `reliability_loss`, `gate_bce_loss`, hỏng hóc tổng hợp, metric, bootstrap theo sự kiện. |
| `train_temporal.py` | Script train/chấm: E0 (FoSp SMOKE5K đóng băng), E1 (fine-tune ảnh đơn), E2 (fine-tune có nền quan sát, cùng số update), cộng hai phép tách nguồn lợi ích. Mọi kết quả in thành dòng JSON. Các tùy chọn `FW_*` ở đầu file. |
| `build_kaggle_script.py` | Nhúng `fw_temporal.py` vào `train_temporal.py`, ghi `build/train_temporal_kaggle.py` (một file). |
| `make_kaggle_notebook.py` | Dựng thư mục notebook Kaggle (`kernel-metadata.json` + `.ipynb`): ghi script một file, chạy thử 20 update rồi chạy thật. Có preset cho lượt 4 và tùy chọn `--env` để train tiếp từ checkpoint. |
| `tools/compare_runs.py` | Bootstrap ghép cặp giữa các run nằm ở các `per_image.json` khác nhau: test SmokeSeg, báo nhầm, test SMOKE5K và nhóm camera cố định. |
| `tools/inspect_gate.py` | Đọc trọng số cổng tin cậy và conv nhánh nền trong checkpoint E2 (không cần GPU). |
| `split_leakage_audit.py` | So hai danh sách ảnh SmokeSeg/FIgLib: trùng chuỗi, sự kiện, ngày, trạm, camera, và khoảng cách thời gian. |
| `tests/` | Kiểm tra CPU với FoSp + mmcv 1.6.2 thật (đặt `FOSP_DIR`, `MMCV_DIR`), và kiểm tra hàm đăng ký ảnh. |

## Input Kaggle

Notebook train gắn ba Output (đã có sẵn trong tài khoản `nguynlduyb24dckh043`):

| Output notebook | Chứa |
| --- | --- |
| `forestwatch-fosp-smoke5k` | `FoSp-official/`, `mmcv-1.6.2-source/`, `fosp-deps/`, SMOKE5K |
| `notebook32e4daa033` | `fosp_smoke5k_14000/best.pth` (FoSp 14k), `manifest.json` của SMOKE5K, SMOKE5K đã giải nén |
| `forestwatch-day2-temporal-prep` | `fw_day2/manifest_day2.json`, ảnh nền đã căn, khung âm tính |

SmokeSeg được script tự tải bằng `gdown` (cần bật Internet). SMOKE5K lấy từ input; nếu không thấy thì tải lại từ link gốc của tác giả.

## Chạy một lượt trên Kaggle

```powershell
.\.venv\Scripts\python.exe research\temporal_fosp\make_kaggle_notebook.py --preset day3-s5k-b --out work\kaggle_push\day3_s5k_b
.\work\uv-tools\bin\kaggle.exe kernels push -p work\kaggle_push\day3_s5k_b
```

Kaggle CLI đọc thông tin đăng nhập trong `work\kaggle-home\.kaggle`. Trước khi gọi, đặt `$env:USERPROFILE = 'D:\ForestWatch\work\kaggle-home'` để không ghi gì vào ổ C. Kết quả nằm trong Output của notebook: `<tag>/per_image.json`, `<tag>.log`, `<tag>/E1_single_best.pth`.

Tùy chọn hay dùng (`--env FW_KEY=VALUE`):

| Biến | Ý nghĩa |
| --- | --- |
| `FW_RUNS` | `E0,E1`, `E1`, `E0,E1,E2`, ... |
| `FW_UPDATES` | Số update cố định cho E1 và E2 (mặc định của notebook: 6000) |
| `FW_S5K_TRAIN=1` / `FW_S5K_EVAL=1` | Thêm SMOKE5K vào train / chấm thêm test SMOKE5K ở độ phân giải gốc |
| `FW_TRAIN_NEG=0.2` | 20% mẫu train thay bằng ảnh nền không khói (mask rỗng) |
| `FW_FUSION`, `FW_GATE_LOSS` | Kiểu ghép của E2 (`convex`, `residual`, `delta`) và loss của cổng (`one_sided`, `bce`) |
| `FW_INIT_E1`, `FW_INIT_E2` | Khởi tạo từ checkpoint `*_best.pth`: đường dẫn, hoặc mẫu tìm trong `/kaggle/input` như `day3_s5k_b/E1_single_best.pth` |
| `FW_DICE_WEIGHT=1` | Thêm loss soft-F1 tính theo từng ảnh vào loss của FoSp, để khói nhỏ có trọng lượng ngang khói to |
| `FW_ZOOM_MIN=256` | Ô cắt ngẫu nhiên khi train nhỏ tới 256 px (phóng tới 2×), một nửa số ô đặt quanh một pixel khói. Mặc định 410, giống các lượt trước |
| `FW_TTA_SWEEP=1` | Sau khi train, chấm val ở mọi cặp (cách chấm, ngưỡng). Cách chấm gồm `none`, `flip`, `zoom` (4 ô 320 px phóng lên 512), `zoom+flip`; ngưỡng lấy từ `FW_TTA_THRESHOLDS`. Giữ cặp tốt nhất trên val rồi chấm test, khung âm, SMOKE5K ở cặp đó, ghi thành run `E1_tta` |
| `FW_EVAL_ONLY=1` | Không train: nạp `FW_INIT_E1`/`FW_INIT_E2`, chấm lại và dò như `FW_TTA_SWEEP` |

Preset lượt 5, chạy sau khi lượt 4 xong:

```powershell
.\.venv\Scripts\python.exe research\temporal_fosp\make_kaggle_notebook.py --preset day4-tta-a --out work\kaggle_push\day4_tta_a
.\.venv\Scripts\python.exe research\temporal_fosp\make_kaggle_notebook.py --preset day4-dice-zoom --env FW_S5K_TRAIN=1 --out work\kaggle_push\day4_dice_zoom
```

## Train tiếp từ model tốt nhất

Gắn Output của notebook có checkpoint, ví dụ `forestwatch-day3-s5k-b`, rồi đặt `FW_INIT_E1`:

```powershell
.\.venv\Scripts\python.exe research\temporal_fosp\make_kaggle_notebook.py --title "ForestWatch Day4 E1 continue" `
    --source nguynlduyb24dckh043/forestwatch-day3-s5k-b `
    --env FW_RUNS=E1 --env FW_S5K_TRAIN=1 --env FW_INIT_E1=day3_s5k_b/E1_single_best.pth --out work\kaggle_push\day4
```

Đây là khởi tạo từ trọng số. Optimizer và lịch learning rate (warmup 100 update rồi giảm tuyến tính) bắt đầu lại. So sánh với run khác thì phải cùng số update, cùng cách chọn checkpoint theo val.

Checkpoint train trên laptop không có trên Kaggle. Chúng nằm ở `checkpoints/temporal_fosp_2026-11-07/` (ngoài Git). Muốn dùng trên Kaggle thì tải thư mục đó lên thành dataset private, rồi thêm `--dataset <user>/<tên>`.

## Kiểm tra CPU tại máy

```powershell
$env:FOSP_DIR = 'D:\ForestWatch\outputs\forestwatch-mvp\.fosp-runtime\FoSp'
$env:MMCV_DIR = 'D:\ForestWatch\outputs\forestwatch-mvp\.fosp-runtime\mmcv'
$env:CUDA_VISIBLE_DEVICES = ''
.\work\venv\Scripts\python.exe -I research\temporal_fosp\tests\test_fw_temporal_cpu.py
.\work\venv\Scripts\python.exe -I research\temporal_fosp\tests\test_registration.py
```

Không commit checkpoint, ảnh nền, dataset hay output notebook. Các file đó nằm trong Output Kaggle hoặc trong `checkpoints/` (ngoài Git).
