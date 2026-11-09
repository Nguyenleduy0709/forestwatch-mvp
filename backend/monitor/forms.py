import warnings
from PIL import Image, ImageOps
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

class UploadForm(forms.Form):
    image = forms.FileField(label='Ảnh hiện trường')
    source = forms.CharField(max_length=120, initial='Camera mô phỏng 01', label='Nguồn ảnh')
    simulate_smoke = forms.BooleanField(required=False, label='Mô phỏng phát hiện khói')

    def clean_image(self):
        upload = self.cleaned_data['image']
        if upload.size > 10 * 1024 * 1024:
            raise forms.ValidationError('Ảnh tối đa 10 MB.')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                image = Image.open(upload)
                if image.format not in {'JPEG', 'PNG', 'WEBP'}:
                    raise ValueError('format')
                if image.width * image.height > 24_000_000:
                    raise ValueError('size')
                image.load()
                self.normalized_image = ImageOps.exif_transpose(image).convert('RGB')
                self.normalized_image.thumbnail((1920, 1920))
        except Exception:
            raise forms.ValidationError('Cần ảnh JPG, PNG hoặc WebP hợp lệ, tối đa 24 megapixel.')
        return upload


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
