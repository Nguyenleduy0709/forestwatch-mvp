# Đưa lên GitHub

Repo hiện có: https://github.com/Nguyenleduy0709/forestwatch-mvp

Tên repo: **forestwatch-mvp**, công khai theo lựa chọn của chủ dự án.

Cài Git và GitHub CLI từ nguồn chính thức nếu máy chưa có. Đăng nhập bằng tài khoản của bạn; không dán token vào chat hoặc commit token vào repo.

```powershell
gh auth login
```

Nếu mở bản ZIP chưa có Git history:

```powershell
git init -b main
git add .
git commit -m "Build ForestWatch Django MVP"
```

Sau đó, từ thư mục dự án:

```powershell
gh repo create forestwatch-mvp --public --source . --remote origin --push
```

Nếu đã có repo cùng tên, không ghi đè hoặc force-push. Chọn tên khác hoặc kiểm tra nội dung repo hiện có trước khi nối remote.

Repo có GitHub Actions ở `.github/workflows/check.yml`. Tài khoản đăng nhập cần có quyền push file workflow; nếu GitHub yêu cầu cấp quyền bổ sung, chủ tài khoản tự xem và quyết định trên GitHub.

Không đưa `backend/media/`, database, `.env`, `backend/weights/best.pt` hay tài khoản thử nghiệm lên GitHub. Các mục này đã nằm trong `.gitignore`.
