import warnings
from PIL import Image, ImageOps
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

import io
import re
import urllib.parse
import urllib.request
import warnings
from PIL import Image, ImageOps
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError


def process_pil_image(image_file_or_bytes):
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        image = Image.open(image_file_or_bytes)
        if image.format not in {'JPEG', 'PNG', 'WEBP'}:
            raise ValueError('format')
        if image.width * image.height > 24_000_000:
            raise ValueError('size')
        image.load()
        normalized = ImageOps.exif_transpose(image).convert('RGB')
        normalized.thumbnail((1920, 1920))
        return normalized


def extract_google_drive_file_id(url: str):
    match = re.search(r'drive\.google\.com/(?:file/d/|open\?id=|uc\?id=)([a-zA-Z0-9_-]+)', url)
    return match.group(1) if match else None


def extract_youtube_video_id(url: str):
    if not url:
        return None
    match = re.search(r'(?:youtube\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/|live/)|youtu\.be/)([a-zA-Z0-9_-]{11})', url)
    return match.group(1) if match else None


def fetch_image_from_url(url: str, timeout: int = 15) -> bytes:
    drive_id = extract_google_drive_file_id(url)
    youtube_id = extract_youtube_video_id(url)
    target_urls = []
    if drive_id:
        target_urls.append(f'https://drive.google.com/thumbnail?id={drive_id}&sz=w2048')
        target_urls.append(f'https://drive.google.com/uc?export=download&id={drive_id}')
    elif youtube_id:
        target_urls.append(f'https://img.youtube.com/vi/{youtube_id}/maxresdefault.jpg')
        target_urls.append(f'https://img.youtube.com/vi/{youtube_id}/hqdefault.jpg')
        target_urls.append(f'https://img.youtube.com/vi/{youtube_id}/0.jpg')
    else:
        target_urls.append(url)

    last_err = None
    for target in target_urls:
        try:
            req = urllib.request.Request(
                target,
                headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) ForestWatch/1.0',
                    'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
                }
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                max_bytes = 10 * 1024 * 1024
                chunks = []
                total = 0
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise forms.ValidationError('Ảnh từ liên kết vượt quá giới hạn dung lượng 10 MB.')
                    chunks.append(chunk)
                content = b''.join(chunks)
                if len(content) < 100:
                    raise ValueError('Dữ liệu nhận được quá ngắn hoặc không phải ảnh.')
                return content
        except forms.ValidationError:
            raise
        except Exception as exc:
            last_err = exc
            continue

    raise forms.ValidationError(
        f'Không thể tải ảnh từ liên kết hoặc Google Drive. Hãy đảm bảo liên kết được chia sẻ công khai ("Bất kỳ ai có liên kết"). Lỗi: {last_err}'
    )


class UploadForm(forms.Form):
    image = forms.FileField(label='Ảnh hiện trường', required=False)
    image_url = forms.CharField(
        label='Liên kết ảnh hoặc Google Drive',
        required=False,
        widget=forms.TextInput(attrs={
            'placeholder': 'Dán link ảnh (https://...) hoặc link chia sẻ Google Drive',
            'autocomplete': 'off',
        }),
    )
    source = forms.CharField(max_length=120, initial='Camera mô phỏng 01', label='Nguồn ảnh')
    simulate_smoke = forms.BooleanField(required=False, label='Mô phỏng phát hiện khói')

    def clean(self):
        cleaned_data = super().clean()
        upload = cleaned_data.get('image')
        image_url = (cleaned_data.get('image_url') or '').strip()

        if not upload and not image_url:
            raise forms.ValidationError('Vui lòng chọn file ảnh từ máy tính hoặc dán link ảnh / Google Drive.')

        if upload:
            if upload.size > 10 * 1024 * 1024:
                self.add_error('image', 'Ảnh tối đa 10 MB.')
                return cleaned_data
            try:
                self.normalized_image = process_pil_image(upload)
            except Exception:
                self.add_error('image', 'Cần ảnh JPG, PNG hoặc WebP hợp lệ, tối đa 24 megapixel.')
                return cleaned_data
        elif image_url:
            try:
                image_bytes = fetch_image_from_url(image_url)
                self.normalized_image = process_pil_image(io.BytesIO(image_bytes))
            except forms.ValidationError as exc:
                self.add_error('image_url', exc)
                return cleaned_data
            except Exception:
                self.add_error('image_url', 'Liên kết không chứa dữ liệu ảnh JPG, PNG hoặc WebP hợp lệ.')
                return cleaned_data

        return cleaned_data


class RegisterForm(forms.Form):
    username = forms.CharField(
        max_length=150,
        label='Tên đăng nhập',
        widget=forms.TextInput(attrs={'placeholder': 'VD: hoang_nam', 'autocomplete': 'username'}),
    )
    email = forms.EmailField(
        label='Email liên hệ',
        widget=forms.EmailInput(attrs={'placeholder': 'VD: nam@example.com', 'autocomplete': 'email'}),
    )
    password = forms.CharField(
        label='Mật khẩu',
        widget=forms.PasswordInput(attrs={'placeholder': 'Tối thiểu 8 ký tự', 'autocomplete': 'new-password'}),
    )
    password_confirm = forms.CharField(
        label='Xác nhận mật khẩu',
        widget=forms.PasswordInput(attrs={'placeholder': 'Nhập lại mật khẩu', 'autocomplete': 'new-password'}),
    )

    def clean_username(self):
        username = self.cleaned_data.get('username', '').strip()
        UserModel = get_user_model()
        if UserModel.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError('Tên đăng nhập này đã được sử dụng.')
        return username

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        UserModel = get_user_model()
        if UserModel.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('Email này đã được sử dụng cho một tài khoản khác.')
        return email

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        password_confirm = cleaned_data.get('password_confirm')
        if password and password_confirm and password != password_confirm:
            self.add_error('password_confirm', 'Mật khẩu xác nhận không khớp.')
        if password:
            try:
                validate_password(password)
            except ValidationError as exc:
                self.add_error('password', exc)
        return cleaned_data
