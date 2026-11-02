"use strict";
const input = document.querySelector('#image-input');
const preview = document.querySelector('#upload-preview');
let previewURL;
input?.addEventListener('change', () => {
    const file = input.files?.[0];
    if (!file || !preview)
        return;
    if (file.size > 10 * 1024 * 1024) {
        alert('Ảnh tối đa 10 MB.');
        input.value = '';
        return;
    }
    if (previewURL)
        URL.revokeObjectURL(previewURL);
    previewURL = URL.createObjectURL(file);
    preview.src = previewURL;
    preview.hidden = false;
    const label = document.querySelector('#file-label');
    if (label)
        label.textContent = file.name;
});
document.querySelector('#upload-form')?.addEventListener('submit', () => {
    const button = document.querySelector('#analyze-button');
    if (button) {
        button.disabled = true;
        button.textContent = 'Đang phân tích…';
    }
});
document.querySelector('#status-filter')?.addEventListener('change', (event) => {
    event.currentTarget.form?.submit();
});
document.querySelectorAll('[data-image-url]').forEach(button => {
    button.addEventListener('click', () => {
        const image = document.querySelector('#result-image');
        if (image && button.dataset.imageUrl)
            image.src = button.dataset.imageUrl;
        document.querySelectorAll('[data-image-url]').forEach(item => item.classList.remove('selected'));
        button.classList.add('selected');
    });
});
