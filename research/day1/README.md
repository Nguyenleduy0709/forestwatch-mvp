# Day 1 — thử nghiệm thời gian FoSp

Notebook: https://www.kaggle.com/code/nguynlduyb24dckh043/forestwatch-day1-temporal-data-audit/edit

Đã nạp FoSp 14.000 trên Tesla T4. SmokeSeg có 6.144 ảnh và mặt nạ 0/1. Sau loại ngày trùng ID với SMOKE5K đã dùng: 4.087 cặp train, 272 validation, 682 test; chia theo ngày sự kiện. Chưa kiểm tra đầy đủ near-duplicate hoặc tách camera.

Pilot giữ cố định FoSp, học hai residual head cùng kiến trúc: static chỉ dùng ảnh hiện tại; temporal thêm ảnh trước và trọng số tin cậy heuristic. Chưa phải bộ nhớ nền học được đầy đủ. Chưa có negative-only sequence, chưa đánh giá giảm báo nhầm sương. Không dùng test để chọn checkpoint.

Baseline ban đầu trên 272 cặp validation, tại 128x128: F1 0.3473692357; smoke IoU 0.2595905973. Không so trực tiếp với SMOKE5K native-resolution F1 76.88%.

Giới hạn vòng train 10.200 giây, dự trù 600 giây đánh giá/đóng gói; tối đa 6.000 update. Lưu last mỗi 100 update, validation mỗi 500, early stopping 5 lần không cải thiện đáng kể. Có thể dừng sớm hơn 3 giờ.

Kết quả trên Kaggle:
- /kaggle/working/day1_pilot/best_static.pth
- /kaggle/working/day1_pilot/best_temporal.pth
- /kaggle/working/day1_pilot/last.pth
- /kaggle/working/forestwatch_day1_pilot.zip (tạo khi vòng train kết thúc)

Best head chỉ chứa bộ hiệu chỉnh; cần FoSp gốc cùng config để suy luận. ZIP cuối gồm cả FoSp base. Chưa thay model web.

Để chạy tiếp: giữ checkpoint last và manifest/config, dựng lại dữ liệu và môi trường bằng notebook, khôi phục last.pth vào day1_pilot trước cell train. Cell train tự nạp head, optimizer, update và RNG; cần sửa đường dẫn dữ liệu nếu chuyển máy. last.pth chứa trạng thái NumPy/Python, chỉ dùng weights_only=False cho checkpoint tin cậy do chính lượt chạy này tạo.

Trước khi đóng runtime Kaggle: Quick Save, bật Save output for this version và đợi Successful; kiểm tra có last/best/ZIP trong Output rồi tải về D:\ForestWatch\research\day1. Không coi Quick Save chỉ mã là đã sao lưu kết quả train.

## Cập nhật sau pilot
Pilot hoàn thành 6.000 update (~24.5 phút). Best temporal ở 3.500: F1 0.4186757349 tại 128. Chấm lại cùng best trên 512: F1 0.4240444519; static 0.4116523833; FoSp gốc 0.3558354701. Mặt nạ 128 mất hoàn toàn khói ở 2/272 ảnh, do đó giảm độ phân giải không phải lời giải thích chính. FoSp không dự đoán khói ở 63/272 ảnh validation.

Đã lưu ZIP pilot hoàn chỉnh, kiểm tra CRC không lỗi: forestwatch_day1_pilot_complete.zip. ZIP có FoSp base, hai best head, last đủ optimizer/RNG, manifest, config và history.

Đang chạy baseline fine-tune FoSp trên cùng train split SmokeSeg: ảnh/nhãn 512, batch 2 tích lũy 2, LR backbone 6e-6 và head 6e-5, MAT cố định. Tối đa 2.000 update hoặc khoảng 2 giờ; val 250 update, early stopping patience 4. Đây là thích nghi dữ liệu, chưa phải đóng góp mới của paper. Chưa chấm test và chưa có kết quả fine-tune cuối.

Notebook mới: forestwatch-day1-finetune-with-diagnostics.ipynb. Kết quả mới trên Kaggle ở /kaggle/working/day1_fosp_finetune; ZIP cuối /kaggle/working/forestwatch_day1_finetune.zip. Best fine-tune chứa full state_dict FoSp; không nhầm với residual head của pilot. Chưa thay model web.
