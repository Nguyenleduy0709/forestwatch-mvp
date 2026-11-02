import warnings
from PIL import Image, ImageOps
from django import forms

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
