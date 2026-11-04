# Cùng phát triển ForestWatch

`main` là bản ổn định. Mỗi thay đổi đi qua Pull Request vào `main`; chưa cần nhánh `develop`. Không commit trực tiếp lên `main`.

## Cập nhật và tạo nhánh

Commit hoặc stash công việc đang dở trước khi chuyển nhánh. Không dùng `reset --hard` để cập nhật.

```powershell
git status
git switch main
git pull --ff-only origin main
git switch -c feature/ten-chuc-nang
# Khi sửa lỗi: git switch -c fix/ten-loi
```

Thống nhất trước khi cùng sửa migration hoặc cùng một file. Commit migration cùng thay đổi model. Khi sửa TypeScript, build và commit cả `frontend/static/js/app.js`.

## Commit, push, PR và review

Chạy từ gốc repo sau khi cài môi trường theo README:

```powershell
.\scripts\check.ps1
# Có thể chỉ định: .\scripts\check.ps1 -Python .\work\venv\Scripts\python.exe
git diff
git status --short
git add <duong-dan-file-ma-nguon>
git diff --cached --stat
git diff --cached
git commit -m "feat: mo ta thay doi"
git push -u origin feature/ten-chuc-nang
```

Tạo PR trên GitHub: base `main`, compare nhánh vừa push. Điền mẫu PR, nêu vấn đề, thay đổi, cách kiểm tra, ảnh giao diện và migration/cấu hình nếu có. Nhờ ít nhất một thành viên review, giải quyết nhận xét và chờ CI xanh trước khi người có quyền merge. Không force-push hoặc ghi đè lịch sử chung.

Reviewer kiểm tra đăng nhập, upload, mặt nạ, cảnh báo, quyền dữ liệu và demo không có model. Không đưa checkpoint, dataset, ZIP, môi trường Python, SQLite, media, `.env` hay token vào Git. Xóa output notebook chứa dữ liệu riêng trước khi commit.

## Đồng bộ nhánh và xử lý xung đột

```powershell
git switch feature/ten-chuc-nang
git fetch origin
git merge origin/main
git status
```

Mở các file Git báo conflict, giữ nội dung đúng của cả hai thay đổi và xóa marker `<<<<<<<`, `=======`, `>>>>>>>`. Với migration, thống nhất thứ tự với người sửa model, không xóa migration đã dùng. Với JS được sinh tự động, sửa TypeScript rồi build lại.

```powershell
git add <file-da-xu-ly>
.\scripts\check.ps1
git commit
git push
```

Nếu chưa chắc cách giải quyết, `git merge --abort` rồi trao đổi với tác giả. Khi cần giữ việc chưa commit, dùng `git stash push -u -m "tam luu"`, cập nhật, sau đó `git stash pop` và kiểm tra xung đột.

## Bảo vệ main — đề xuất chờ quản trị viên xác nhận

Tại Settings → Rules → Rulesets hoặc Branches, áp dụng cho `main`: bắt buộc PR, tối thiểu **1 approval**, giải quyết hội thoại trước merge, chặn force push và xóa nhánh. Yêu cầu các check **Backend (ubuntu-latest)**, **Backend (windows-latest)** và **Frontend** thành công; chọn check sau khi workflow đã chạy lần đầu. Có thể yêu cầu nhánh cập nhật với main trước merge.

Kiểm tra ngày 04/11/2026: chưa có branch protection/ruleset. Đợt tái cấu trúc này không thay đổi cài đặt đó.
