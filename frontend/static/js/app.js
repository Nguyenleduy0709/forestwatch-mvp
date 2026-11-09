"use strict";
// Helper to extract Google Drive file ID
function extractGoogleDriveId(url) {
    if (!url)
        return null;
    const match = url.match(/drive\.google\.com\/(?:file\/d\/|open\?id=|uc\?id=)([a-zA-Z0-9_-]+)/);
    return match ? match[1] : null;
}
// Helper to extract YouTube video ID
function extractYouTubeVideoId(url) {
    if (!url)
        return null;
    const match = url.trim().match(/(?:youtube\.com\/(?:watch\?(?:.*&)?v=|embed\/|shorts\/|live\/)|youtu\.be\/)([a-zA-Z0-9_-]{11})/);
    return match ? match[1] : null;
}
// Convert input URL to direct previewable image URL if possible
function getPreviewableUrl(url) {
    if (!url)
        return '';
    const driveId = extractGoogleDriveId(url);
    if (driveId) {
        return `https://drive.google.com/thumbnail?id=${driveId}&sz=w1200`;
    }
    const ytId = extractYouTubeVideoId(url);
    if (ytId) {
        return `https://img.youtube.com/vi/${ytId}/hqdefault.jpg`;
    }
    return url;
}
// Bind file dropzone and preview
function bindFileInput(inputId, previewId, labelId) {
    const input = document.querySelector(inputId);
    const preview = document.querySelector(previewId);
    const label = document.querySelector(labelId);
    let previewUrl;
    input?.addEventListener('change', () => {
        const file = input.files?.[0];
        if (!file || !preview)
            return;
        if (file.size > 10 * 1024 * 1024) {
            alert('Ảnh vượt quá dung lượng tối đa 10 MB.');
            input.value = '';
            return;
        }
        if (previewUrl)
            URL.revokeObjectURL(previewUrl);
        previewUrl = URL.createObjectURL(file);
        preview.src = previewUrl;
        preview.hidden = false;
        if (label)
            label.textContent = file.name;
    });
}
// Bind URL previewer
function bindUrlPreview(inputId, btnId, imgId, placeholderId) {
    const input = document.querySelector(inputId);
    const btn = document.querySelector(btnId);
    const img = document.querySelector(imgId);
    const placeholder = document.querySelector(placeholderId);
    const doPreview = () => {
        const rawUrl = (input?.value || '').trim();
        if (!rawUrl) {
            alert('Vui lòng nhập đường dẫn ảnh hoặc link Google Drive.');
            return;
        }
        const previewUrl = getPreviewableUrl(rawUrl);
        if (!img || !placeholder)
            return;
        placeholder.textContent = 'Đang tải xem trước…';
        img.hidden = true;
        const testImg = new Image();
        testImg.onload = () => {
            img.src = previewUrl;
            img.hidden = false;
            placeholder.hidden = true;
        };
        testImg.onerror = () => {
            placeholder.textContent = 'Không thể tải ảnh xem trước từ liên kết này (hoặc bị chặn CORS trên trình duyệt). Bạn vẫn có thể nhấn nút "Phân tích ảnh" để máy chủ tải về trực tiếp!';
            placeholder.hidden = false;
            img.hidden = true;
        };
        testImg.src = previewUrl;
    };
    btn?.addEventListener('click', doPreview);
    input?.addEventListener('paste', () => {
        setTimeout(doPreview, 100);
    });
}
// Bind tabs for form
function bindTabs(container) {
    const tabButtons = container.querySelectorAll('.tab-btn');
    tabButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            const targetId = btn.dataset.tab;
            const parent = btn.closest('.observation-card') || container;
            parent.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            parent.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
            btn.classList.add('active');
            const panel = parent.querySelector(`#${targetId}`);
            if (panel)
                panel.classList.add('active');
        });
    });
}
// Bind submit loading state
function bindFormSubmit(formId, buttonId) {
    const form = document.querySelector(formId);
    const button = document.querySelector(buttonId);
    form?.addEventListener('submit', () => {
        if (button) {
            button.disabled = true;
            button.textContent = 'Đang tải & phân tích dữ liệu…';
        }
    });
}
// Bind detail view mode controls (Annotated vs Original vs Side-by-Side)
function bindDetailViewControls() {
    const controls = document.querySelector('#image-view-controls');
    if (!controls)
        return;
    const btnAnnotated = document.querySelector('#btn-view-annotated');
    const btnOriginal = document.querySelector('#btn-view-original');
    const btnSplit = document.querySelector('#btn-view-split');
    const singleView = document.querySelector('#single-image-view');
    const splitView = document.querySelector('#split-image-view');
    const singleImg = document.querySelector('#result-image');
    const setView = (mode) => {
        [btnAnnotated, btnOriginal, btnSplit].forEach(b => b?.classList.remove('selected'));
        if (mode === 'split') {
            btnSplit?.classList.add('selected');
            if (singleView)
                singleView.hidden = true;
            if (splitView)
                splitView.hidden = false;
        }
        else {
            if (splitView)
                splitView.hidden = true;
            if (singleView)
                singleView.hidden = false;
            if (mode === 'annotated') {
                btnAnnotated?.classList.add('selected');
                const originalUrl = singleImg?.src;
                if (singleImg && originalUrl) {
                    singleImg.src = originalUrl.replace('/original', '/annotated');
                }
            }
            else if (mode === 'original') {
                btnOriginal?.classList.add('selected');
                const currentUrl = singleImg?.src;
                if (singleImg && currentUrl) {
                    singleImg.src = currentUrl.replace('/annotated', '/original');
                }
            }
        }
    };
    btnAnnotated?.addEventListener('click', () => setView('annotated'));
    btnOriginal?.addEventListener('click', () => setView('original'));
    btnSplit?.addEventListener('click', () => setView('split'));
}
// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => {
    // Main upload form
    bindFileInput('#image-input', '#upload-preview', '#file-label');
    bindUrlPreview('#image-url-input', '#btn-preview-url', '#url-preview-img', '#url-preview-placeholder');
    bindFormSubmit('#upload-form', '#analyze-button');
    // Quick upload in detail view
    bindFileInput('#quick-image-input', '#quick-upload-preview', '#quick-file-label');
    bindUrlPreview('#quick-image-url-input', '#quick-btn-preview-url', '#quick-url-preview-img', '#quick-url-preview-placeholder');
    bindFormSubmit('#quick-upload-form', '#quick-analyze-button');
    // Tabs on all observation cards
    document.querySelectorAll('.observation-card').forEach(bindTabs);
    // Detail view split-screen & toggle
    bindDetailViewControls();
    // Status filter auto-submit
    const statusFilter = document.querySelector('#status-filter');
    statusFilter?.addEventListener('change', () => statusFilter.form?.submit());
});
