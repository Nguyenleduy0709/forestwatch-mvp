const input = document.querySelector<HTMLInputElement>('#image-input');
const preview = document.querySelector<HTMLImageElement>('#upload-preview');
let previewURL: string | undefined;
input?.addEventListener('change', () => {
  const file = input.files?.[0];
  if (!file || !preview) return;
  if (file.size > 10 * 1024 * 1024) { alert('Ảnh tối đa 10 MB.'); input.value = ''; return; }
  if (previewURL) URL.revokeObjectURL(previewURL);
  previewURL = URL.createObjectURL(file);
  preview.src = previewURL;
  preview.hidden = false;
  const label = document.querySelector<HTMLElement>('#file-label');
  if (label) label.textContent = file.name;
});
document.querySelector<HTMLFormElement>('#upload-form')?.addEventListener('submit', () => {
  const button = document.querySelector<HTMLButtonElement>('#analyze-button');
  if (button) { button.disabled = true; button.textContent = 'Đang phân tích…'; }
});
document.querySelector<HTMLSelectElement>('#status-filter')?.addEventListener('change', (event) => {
  (event.currentTarget as HTMLSelectElement).form?.submit();
});
document.querySelectorAll<HTMLButtonElement>('[data-image-url]').forEach(button => {
  button.addEventListener('click', () => {
    const image = document.querySelector<HTMLImageElement>('#result-image');
    if (image && button.dataset.imageUrl) image.src = button.dataset.imageUrl;
    document.querySelectorAll('[data-image-url]').forEach(item => item.classList.remove('selected'));
    button.classList.add('selected');
  });
});
