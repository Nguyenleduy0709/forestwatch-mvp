"use strict";
(() => {
    const room = document.querySelector('#cctv-room');
    if (!room)
        return;
    const get = (selector) => document.querySelector(selector);
    const grid = get('#cctv-camera-grid');
    const template = get('#cctv-card-template');
    const add = get('#cctv-add');
    const auto = get('#cctv-auto');
    const sound = get('#cctv-sound');
    const log = get('#cctv-log');
    const dialog = get('#cctv-dialog');
    const form = get('#cctv-source-form');
    const nameInput = get('#cctv-source-name');
    const typeInput = get('#cctv-source-type');
    const fileInput = get('#cctv-source-file');
    const urlInput = get('#cctv-source-url');
    const submit = get('#cctv-source-submit');
    const formError = get('#cctv-form-error');
    const layouts = Array.from(room.querySelectorAll('button[data-cols]'));
    const slots = new Map();
    const maxSlots = 10;
    const capture = document.createElement('canvas');
    const captureContext = capture.getContext('2d', { willReadFrequently: true });
    const clockFormat = new Intl.DateTimeFormat('vi-VN', { timeStyle: 'medium' });
    const dateFormat = new Intl.DateTimeFormat('vi-VN', { dateStyle: 'full' });
    const csrfToken = room.dataset.csrf || '';
    const headers = { 'X-CSRFToken': csrfToken };
    const scanUrl = room.dataset.scan;
    const screenReportsUrl = room.dataset.screenReports || '';
    const stationsUrl = room.dataset.stationsUrl || '';
    const resolveUrl = room.dataset.resolve;
    const referenceEnabled = room.dataset.reference === '1';
    const preserveReplayEnabled = room.dataset.preserveReplay === '1';
    const account = room.dataset.account;
    const roiTopSkyInput = get('#cctv-roi-top-sky');
    const ACTIVE_TYPES = new Set(['url', 'video', 'rtsp', 'hls', 'image']);
    const LIVE_IMG_TYPES = new Set(['rtsp']);
    const labels = {
        image: 'Ảnh tĩnh',
        video: 'Video từ máy',
        url: 'Liên kết video',
        rtsp: 'Camera IP (RTSP)',
        hls: 'Luồng trực tiếp (HLS)',
    };
    let autoEnabled = true;
    let manualLayout = false;
    let incidentCount = Number(get('#cctv-incident-count').textContent) || 0;
    let editing = null;
    let pendingSource = null;
    let committing = false;
    let restoring = true;
    let storage = null;
    let audio = null;
    let soundEnabled = false;
    let inFlightCount = 0;
    const maxConcurrentScans = 1;
    let lastSlot = 0;
    const eventRows = new Map();
    const WATCHTOWER_PRESETS = [
        { name: 'Trạm Chòi Canh Bắc', inputUrl: 'https://www.youtube.com/watch?v=wSp-pUDcFQI', minStart: 1.0 },
        { name: 'Trạm Đồi Thông Đông', inputUrl: 'https://www.youtube.com/watch?v=RWoYegPgRF4', minStart: 1.0 },
        { name: 'Trạm Quan Trắc Nam', inputUrl: 'https://www.youtube.com/watch?v=TtUgnRqARUE', minStart: 15.0 },
        { name: 'Trạm Đỉnh Đèo Tây', inputUrl: 'https://www.youtube.com/watch?v=FZNDqZJvc5w', minStart: 1.0 },
    ];
    function getMinStartTime(saved, playback = '') {
        const ref = `${saved?.inputUrl || ''} ${playback || ''}`;
        if (ref.includes('TtUgnRqARUE'))
            return 15.0;
        return 0;
    }
    function openStorage() {
        return new Promise((resolve, reject) => {
            const request = indexedDB.open('forestwatch-monitoring', 1);
            request.onupgradeneeded = () => {
                request.result.createObjectStore('sources', { keyPath: ['account', 'id'] }).createIndex('account', 'account');
            };
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
            request.onblocked = () => reject(new Error('Không mở được bộ nhớ nguồn.'));
        });
    }
    function savedSources(database) {
        return new Promise((resolve, reject) => {
            const request = database.transaction('sources').objectStore('sources').index('account').getAll(account);
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
    }
    async function updateStorage(id, source) {
        if (!storage)
            return;
        const database = storage;
        await new Promise((resolve, reject) => {
            const transaction = database.transaction('sources', 'readwrite');
            const store = transaction.objectStore('sources');
            if (source)
                store.put({ ...source, account });
            else
                store.delete([account, id]);
            transaction.oncomplete = () => resolve();
            transaction.onabort = () => reject(transaction.error);
        });
    }
    function storageNotice(message) {
        const notice = get('#cctv-storage-notice');
        notice.textContent = message;
        notice.hidden = false;
    }
    function newSourceKey() {
        const bytes = crypto.getRandomValues(new Uint8Array(16));
        return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
    }
    function positionKey(id) {
        return `forestwatch:${account}:position:${id}`;
    }
    function rememberPosition(saved, media) {
        if (!Number.isFinite(media.duration) || media.seeking)
            return;
        try {
            localStorage.setItem(positionKey(saved.id), JSON.stringify({ key: saved.key, time: media.currentTime }));
        }
        catch { /* Playback can continue when browser storage is unavailable. */ }
    }
    function resumePosition(saved, media, playback = '') {
        if (!Number.isFinite(media.duration))
            return;
        const minStart = getMinStartTime(saved, playback);
        try {
            const previous = JSON.parse(localStorage.getItem(positionKey(saved.id)) ?? 'null');
            if (previous?.key === saved.key && previous.time > minStart && previous.time < media.duration - 0.5) {
                media.currentTime = previous.time;
                return;
            }
        }
        catch { /* Ignore unavailable or invalid playback history. */ }
        if (minStart > 0 && media.currentTime < minStart && minStart < media.duration - 1.0) {
            media.currentTime = minStart;
        }
    }
    function channel(id) {
        return `CAM ${String(id).padStart(2, '0')}`;
    }
    function setLayout(cols) {
        grid.dataset.cols = String(cols);
        for (const button of layouts) {
            button.setAttribute('aria-pressed', String(Number(button.dataset.cols) === cols));
        }
    }
    function updateSmokeCounter() {
        const smokeEl = document.querySelector('#cctv-active-smoke-count');
        if (!smokeEl)
            return;
        const activeSmoke = Array.from(slots.values()).filter(slot => slot.card.dataset.state === 'smoke').length;
        const configured = Array.from(slots.values()).filter(slot => slot.saved).length;
        smokeEl.textContent = configured > 0 ? `${activeSmoke} / ${configured} trạm` : '0 trạm';
    }
    function updateCounts() {
        const saved = Array.from(slots.values()).filter(slot => slot.saved).length;
        get('#cctv-count').textContent = `${saved} / ${maxSlots}`;
        get('#cctv-wall-count').textContent = `${slots.size} cửa sổ · ${saved} nguồn đã cấu hình`;
        add.disabled = saved >= maxSlots;
        if (!manualLayout)
            setLayout(Math.max(2, Math.ceil(slots.size / 2)));
        updateSmokeCounter();
    }
    function status(slot, state, message) {
        slot.card.dataset.state = state;
        slot.status.textContent = message;
        updateSmokeCounter();
    }
    function ready(source) {
        const media = source.element;
        if (source.error)
            return false;
        return media instanceof HTMLVideoElement
            ? !media.seeking && media.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA && media.videoWidth > 0
            : media.complete && media.naturalWidth > 0;
    }
    function updateOverlay(slot) {
        const source = slot.source;
        const media = source?.element;
        const refTime = source?.overlayTime ?? source?.lastTime ?? -1;
        const maxAge = Math.max(28.0, slots.size * 7.0);
        const hide = !source?.frame || !ready(source) || (!source.isWatchtowerStream && media instanceof HTMLVideoElement && !slot.scanning && Math.abs(media.currentTime - refTime) > maxAge) || (source.liveFrame !== null && !slot.scanning && Math.abs(source.liveFrame.sequence - source.lastTime) > 6);
        slot.overlay.toggleAttribute('hidden', hide);
    }
    function drawOverlay(slot, result) {
        slot.overlay.replaceChildren();
        slot.overlay.classList.toggle('is-suspected', Boolean(result.suspected));
        slot.overlay.setAttribute('viewBox', `0 0 ${result.image_width} ${result.image_height}`);
        for (const item of result.detections) {
            if (item.polygon?.length) {
                const polygon = document.createElementNS('http://www.w3.org/2000/svg', 'polygon');
                polygon.setAttribute('points', item.polygon.map(point => point.join(',')).join(' '));
                slot.overlay.append(polygon);
            }
            const [x, y, right, bottom] = item.box;
            const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
            rect.setAttribute('x', String(x));
            rect.setAttribute('y', String(y));
            rect.setAttribute('width', String(right - x));
            rect.setAttribute('height', String(bottom - y));
            slot.overlay.append(rect);
            const conf = item.confidence ? `${Math.round(item.confidence * 100)}%` : `${item.mask_coverage ?? ''}%`;
            const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
            text.setAttribute('x', String(Math.max(4, x)));
            text.setAttribute('y', String(Math.max(18, y - 6)));
            text.setAttribute('class', 'cctv-overlay-label');
            text.textContent = conf ? `KHÓI · ${conf}` : 'PHÁT HIỆN KHÓI';
            slot.overlay.append(text);
        }
        updateOverlay(slot);
    }
    function showMediaState(slot, source) {
        const media = source.element;
        const canScan = ready(source);
        slot.start.hidden = source.started;
        slot.start.disabled = !autoEnabled || !!source.error;
        slot.scan.hidden = !source.started;
        slot.scan.disabled = !canScan || slot.manual || slot.scanning;
        if (source.error) {
            status(slot, 'error', 'Lỗi nguồn');
            slot.detail.textContent = source.error;
            slot.detail.hidden = false;
        }
        else if (!source.started) {
            status(slot, 'waiting', autoEnabled ? 'Chờ bắt đầu quét' : 'Quét tự động đang tạm dừng');
        }
        else if (!canScan) {
            status(slot, 'waiting', 'Đang kết nối luồng hình…');
        }
        else if (source.frame?.suspected) {
            const st = source.frame.streak || 1;
            const req = source.frame.required_streak || 2;
            status(slot, 'waiting', `Nghi vấn khói (${st}/${req})`);
        }
        else if (source.frame?.has_smoke) {
            status(slot, source.frame.alert ? 'smoke' : 'waiting', source.frame.alert ? 'Có dấu hiệu khói' : 'Dưới ngưỡng lưu');
        }
        else if (media instanceof HTMLVideoElement && media.paused) {
            status(slot, 'waiting', 'Video đang dừng');
        }
        else if (!source.frame) {
            status(slot, 'ready', 'Sẵn sàng');
        }
        else {
            status(slot, 'ready', 'Chưa ghi nhận khói');
        }
        updateOverlay(slot);
    }
    function createSlot(id) {
        const card = template.content.firstElementChild.cloneNode(true);
        const find = (selector) => card.querySelector(selector);
        const slot = {
            id, card, title: find('h3'), media: find('.cctv-media'), empty: find('.cctv-empty-source'),
            overlay: find('.cctv-overlay'), badge: find('.cctv-source-badge'), status: find('.cctv-state span'),
            settings: find('.cctv-settings'), scan: find('.cctv-scan'), start: find('.cctv-start'), speed: find('.cctv-speed'),
            rate: find('.cctv-speed select'), detail: find('.cctv-card-detail'), evidence: find('.cctv-card-evidence'),
            alertsBtn: find('.cctv-screen-alerts-btn'), alertsCount: find('.cctv-screen-alerts-count'),
            referenceTools: find('.cctv-reference-tools'),
            saved: null, restore: null, source: null, manual: false, scanning: false,
            captureCount: 0, eventCount: 0, captures: [],
        };
        find('.cctv-channel').textContent = channel(id);
        slot.title.textContent = 'Chưa có nguồn';
        slot.overlay.setAttribute('hidden', '');
        status(slot, 'empty', 'Chờ cấu hình');
        slot.empty.addEventListener('click', () => {
            if (slot.saved)
                void restoreSlot(slot, slot.saved);
            else
                openDialog(id);
        });
        slot.settings.addEventListener('click', () => openDialog(id));
        const focusBtn = find('.cctv-focus');
        focusBtn?.addEventListener('click', () => {
            const willFocus = !card.classList.contains('is-focused');
            for (const item of slots.values()) {
                item.card.classList.remove('is-focused');
            }
            if (willFocus) {
                card.classList.add('is-focused');
                card.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            }
        });
        slot.alertsBtn?.addEventListener('click', () => void openScreenReport(slot));
        const remove = find('.cctv-remove');
        remove.addEventListener('click', async () => {
            if (restoring)
                return;
            remove.disabled = true;
            try {
                const stationIdToDelete = slot.saved?.stationId;
                if (slot.saved)
                    await updateStorage(id, null);
                if (stationIdToDelete) {
                    void fetch(`/api/stations/${stationIdToDelete}/`, { method: 'DELETE', headers }).catch(() => { });
                }
                slot.restore?.abort();
                slot.source?.dispose();
                slots.delete(id);
                card.remove();
                updateCounts();
                add.focus();
            }
            catch {
                storageNotice('Không thể xóa nguồn đã lưu. Hãy thử lại.');
                remove.disabled = false;
            }
        });
        slot.start.addEventListener('click', async () => {
            const source = slot.source;
            if (!autoEnabled || !source || source.started || !(source.element instanceof HTMLVideoElement))
                return;
            const media = source.element;
            source.started = true;
            media.controls = true;
            showMediaState(slot, source);
            try {
                await media.play();
            }
            catch {
                if (slot.source !== source || !autoEnabled)
                    return;
                source.started = false;
                media.controls = false;
                showMediaState(slot, source);
                if (!source.error) {
                    slot.detail.textContent = 'Chưa phát được video. Hãy thử bắt đầu lại.';
                    slot.detail.hidden = false;
                }
            }
        });
        slot.scan.addEventListener('click', () => {
            if (!slot.source?.started || !ready(slot.source))
                return;
            slot.manual = true;
            slot.scan.disabled = true;
            status(slot, 'waiting', 'Đang chờ quét');
            if (!preserveReplayEnabled && slot.source?.element instanceof HTMLVideoElement && slot.source.element.currentTime <= 2.0) {
                slot.source.videoReplayed = true;
                void resetVideoAlerts(slot);
            }
            void scanNext();
        });
        slot.rate.addEventListener('change', () => {
            if (slot.saved && slot.source?.element instanceof HTMLVideoElement) {
                const rate = Number(slot.rate.value);
                slot.source.element.playbackRate = rate;
                slot.saved.rate = rate;
                void updateStorage(id, slot.saved).catch(() => storageNotice('Không thể lưu tốc độ phát mới.'));
            }
        });
        const setReference = find('.cctv-reference-set');
        setReference.addEventListener('click', async () => {
            const { source, saved } = slot;
            if (!source || !saved || !ready(source)) {
                storageNotice('Chờ nguồn có hình rồi lưu khung nền.');
                return;
            }
            setReference.disabled = true;
            try {
                const frame = source.liveFrame?.image ?? await captureFrame(source);
                if (slot.source === source && slot.saved === saved)
                    await saveReference(slot, saved, frame);
            }
            catch {
                storageNotice('Không lưu được khung nền. Hãy thử lại khi đã có hình từ camera.');
            }
            finally {
                setReference.disabled = false;
            }
        });
        find('.cctv-reference-clear').addEventListener('click', () => {
            if (slot.saved)
                void saveReference(slot, slot.saved, undefined);
        });
        const following = Array.from(slots.values())
            .filter(existing => existing.id > id)
            .sort((a, b) => a.id - b.id)[0];
        grid.insertBefore(card, following?.card ?? null);
        slots.set(id, slot);
        return slot;
    }
    function showReference(slot) {
        const exists = !!slot.saved?.reference;
        slot.referenceTools.querySelector('.cctv-reference-set').textContent = exists ? 'Đổi khung nền' : 'Lưu khung nền';
        slot.referenceTools.querySelector('.cctv-reference-clear').hidden = !exists;
    }
    async function saveReference(slot, saved, frame) {
        const previous = saved.reference;
        saved.reference = frame;
        try {
            await updateStorage(slot.id, saved);
        }
        catch {
            saved.reference = previous;
            storageNotice('Không thể lưu thay đổi của khung nền.');
        }
        if (slot.saved === saved)
            showReference(slot);
    }
    function loadHlsPlayer(videoEl, hlsUrl, events) {
        if (videoEl.canPlayType('application/vnd.apple.mpegurl')) {
            videoEl.src = hlsUrl;
            return;
        }
        const attach = (HlsCtor) => {
            if (events.signal.aborted || !HlsCtor?.isSupported()) {
                videoEl.src = hlsUrl;
                return;
            }
            const hls = new HlsCtor({ lowLatencyMode: true });
            hls.loadSource(hlsUrl);
            hls.attachMedia(videoEl);
            events.signal.addEventListener('abort', () => hls.destroy(), { once: true });
        };
        if (window.Hls) {
            attach(window.Hls);
            return;
        }
        const script = document.createElement('script');
        script.src = 'https://cdn.jsdelivr.net/npm/hls.js@1.5.17/dist/hls.min.js';
        script.onload = () => attach(window.Hls);
        script.onerror = () => { videoEl.src = hlsUrl; };
        document.head.append(script);
    }
    function mountSource(slot, saved, label, playback) {
        const { type, name, file, rate } = saved;
        const isWatchtowerStream = typeof playback === 'string' && playback.startsWith('/api/video/demo-stream/');
        const clip = (type === 'video' || type === 'url') && !isWatchtowerStream;
        const isLiveImg = LIVE_IMG_TYPES.has(type);
        if (slot.saved?.key !== saved.key)
            slot.evidence.hidden = true;
        slot.saved = saved;
        slot.source?.dispose();
        const media = document.createElement(type === 'image' || isLiveImg ? 'img' : 'video');
        const events = new AbortController();
        const options = { signal: events.signal };
        const objectUrl = file ? URL.createObjectURL(file) : '';
        let liveFrameObjectUrl = '';
        let liveFrameTimer = 0;
        const source = {
            element: media, imageFile: type === 'image' ? file : null,
            liveFrame: null,
            isWatchtowerStream,
            error: '', lastTime: -1, nextScan: 0,
            frame: null,
            started: !clip, resumeOnScan: false,
            dispose() {
                events.abort();
                window.clearTimeout(liveFrameTimer);
                if (liveFrameObjectUrl)
                    URL.revokeObjectURL(liveFrameObjectUrl);
                if (media instanceof HTMLVideoElement) {
                    media.pause();
                    media.srcObject = null;
                    media.removeAttribute('src');
                    media.load();
                }
                if (objectUrl)
                    URL.revokeObjectURL(objectUrl);
            },
        };
        slot.source = source;
        slot.manual = false;
        slot.scanning = false;
        slot.scan.textContent = 'Quét ngay';
        slot.title.textContent = name;
        slot.badge.textContent = label;
        slot.badge.hidden = false;
        slot.empty.hidden = true;
        slot.settings.hidden = false;
        if (slot.alertsBtn)
            slot.alertsBtn.hidden = false;
        slot.speed.hidden = type !== 'video' && type !== 'url';
        slot.referenceTools.hidden = !referenceEnabled || type === 'image';
        showReference(slot);
        slot.rate.value = String(rate);
        slot.detail.hidden = true;
        slot.overlay.replaceChildren();
        slot.media.replaceChildren(media);
        void loadScreenStats(slot);
        function onReady() {
            showMediaState(slot, source);
            void scanNext();
        }
        function mediaError(message) {
            source.error = message;
            slot.manual = false;
            showMediaState(slot, source);
        }
        media.addEventListener('error', () => mediaError(type === 'url'
            ? 'Không phát được. Hãy dùng link trực tiếp MP4/WebM, video YouTube công khai hoặc mở lại nguồn.'
            : 'Không mở được nguồn. Hãy kiểm tra định dạng tệp hoặc kết nối camera.'), options);
        async function resetVideoAlerts(targetSlot) {
            if (!targetSlot?.saved) return;
            targetSlot.captureCount = 0;
            targetSlot.eventCount = 0;
            targetSlot.captures = [];
            if (targetSlot.alertsCount) targetSlot.alertsCount.textContent = '0';
            if (targetSlot.alertsBtn) {
                targetSlot.alertsBtn.hidden = true;
                targetSlot.alertsBtn.classList.remove('has-alerts');
            }
            if (targetSlot.evidence) targetSlot.evidence.hidden = true;
            if (targetSlot.source) {
                targetSlot.source.lastTime = -1;
                targetSlot.source.maxSeenPosition = 0;
                targetSlot.source.videoReplayed = true;
            }

            const channelName = channel(targetSlot.id);
            const fullSourceName = targetSlot.saved.name === channelName ? channelName : `${channelName} · ${targetSlot.saved.name}`;

            for (const [id, row] of eventRows) {
                if (row.dataset.slotId === String(targetSlot.id) || row.dataset.source === fullSourceName) {
                    eventRows.delete(id);
                    row.remove();
                }
            }
            if (eventRows.size === 0) {
                const emptyEl = document.querySelector('#cctv-log-empty');
                if (emptyEl) emptyEl.hidden = false;
            }

            if (activeReportSlotId === targetSlot.id) {
                const gridEl = reportDialog?.querySelector('#cctv-report-captures-grid');
                const emptyEl = reportDialog?.querySelector('#cctv-report-empty');
                const capCountEl = reportDialog?.querySelector('#cctv-report-total-captures');
                const alertCountEl = reportDialog?.querySelector('#cctv-report-total-alerts');
                if (gridEl) gridEl.replaceChildren();
                if (emptyEl) emptyEl.hidden = false;
                if (capCountEl) capCountEl.textContent = '0';
                if (alertCountEl) alertCountEl.textContent = '0';
            }

            try {
                const body = new FormData();
                body.append('csrfmiddlewaretoken', csrfToken);
                body.append('delete_all', '1');
                body.append('source', fullSourceName);
                if (targetSlot.saved.key) {
                    body.append('source_key', targetSlot.saved.key);
                }
                await fetch('/alerts/delete/', {
                    method: 'POST',
                    body,
                    headers: { 'X-Requested-With': 'XMLHttpRequest' }
                });
            } catch (_) {}
        }

        if (media instanceof HTMLVideoElement) {
            const minStart = getMinStartTime(saved, playback);
            media.controls = !clip;
            media.preload = clip ? 'metadata' : 'auto';
            media.muted = true;
            media.playsInline = true;
            media.defaultPlaybackRate = rate;
            media.playbackRate = rate;
            media.loop = isWatchtowerStream;
            media.addEventListener('loadedmetadata', () => resumePosition(saved, media, playback), { ...options, once: true });
            media.addEventListener('loadeddata', onReady, options);
            media.addEventListener('playing', onReady, options);
            media.addEventListener('play', () => {
                if (clip) {
                    if (!source.started || !autoEnabled)
                        media.pause();
                }
                if (minStart > 0 && media.currentTime < minStart && Number.isFinite(media.duration) && minStart < media.duration - 1.0) {
                    media.currentTime = minStart;
                }
                if (!isWatchtowerStream && !preserveReplayEnabled && (source.hasEnded || ((source.maxSeenPosition || 0) > 3.0 && media.currentTime < 1.5))) {
                    source.hasEnded = false;
                    void resetVideoAlerts(slot);
                }
            }, options);
            media.addEventListener('pause', () => showMediaState(slot, source), options);
            media.addEventListener('ended', () => {
                source.hasEnded = true;
                showMediaState(slot, source);
            }, options);
            let lastPositionWrite = 0;
            media.addEventListener('timeupdate', () => {
                if (minStart > 0 && media.currentTime < minStart - 0.5 && Number.isFinite(media.duration) && minStart < media.duration - 1.0 && !media.seeking) {
                    media.currentTime = minStart;
                    return;
                }
                if (media.currentTime > (source.maxSeenPosition || 0)) {
                    source.maxSeenPosition = media.currentTime;
                }
                updateOverlay(slot);
                void scanNext();
                if (performance.now() - lastPositionWrite >= 2500) {
                    rememberPosition(saved, media);
                    lastPositionWrite = performance.now();
                }
            }, options);
            media.addEventListener('seeking', () => {
                source.lastTime = -1;
                source.frame = null;
                updateOverlay(slot);
            }, options);
            media.addEventListener('seeked', () => {
                if (!isWatchtowerStream && !preserveReplayEnabled && (source.maxSeenPosition || 0) > 3.0 && media.currentTime < 1.0) {
                    void resetVideoAlerts(slot);
                }
                rememberPosition(saved, media);
                onReady();
            }, options);
            if (type === 'hls') {
                loadHlsPlayer(media, playback, events);
            }
            else {
                media.src = objectUrl || playback;
            }
            if (!clip)
                void media.play().catch(() => { });
        }
        else {
            media.alt = name;
            media.addEventListener('load', onReady, options);
            if (isLiveImg) {
                let etag = '';
                const pollInterval = 900;
                const pollLiveFrame = async () => {
                    try {
                        const response = await fetch(playback, {
                            cache: 'no-store', signal: events.signal,
                            headers: etag ? { 'If-None-Match': etag } : {},
                        });
                        if (response.status === 304) {
                            if (source.error) {
                                source.error = '';
                                onReady();
                            }
                            return;
                        }
                        if (response.redirected)
                            throw new Error('Phiên đăng nhập đã hết. Hãy tải lại trang.');
                        if (!response.ok || !response.headers.get('Content-Type')?.startsWith('image/jpeg')) {
                            const details = await response.json().catch(() => null);
                            throw new Error(details?.message || 'Chưa nhận được khung hình từ camera.');
                        }
                        const image = await response.blob();
                        if (events.signal.aborted)
                            return;
                        etag = response.headers.get('ETag') ?? '';
                        const previous = liveFrameObjectUrl;
                        liveFrameObjectUrl = URL.createObjectURL(image);
                        source.liveFrame = { image, sequence: (source.liveFrame?.sequence ?? 0) + 1 };
                        source.error = '';
                        media.src = liveFrameObjectUrl;
                        if (previous)
                            URL.revokeObjectURL(previous);
                    }
                    catch (error) {
                        if (!events.signal.aborted)
                            mediaError(error instanceof Error ? error.message : 'Chưa nhận được hình.');
                    }
                    finally {
                        if (!events.signal.aborted)
                            liveFrameTimer = window.setTimeout(pollLiveFrame, source.error ? 2000 : pollInterval);
                    }
                };
                void pollLiveFrame();
            }
            else {
                media.src = objectUrl;
            }
        }
        showMediaState(slot, source);
        updateCounts();
    }
    function showSourceFields() {
        const type = typeInput.value;
        const useFile = type === 'image' || type === 'video';
        const useUrl = type === 'url' || type === 'rtsp' || type === 'hls';
        const fileField = document.querySelector('#cctv-file-field');
        const urlField = document.querySelector('#cctv-url-field');
        const demoPresets = document.querySelector('#cctv-demo-video-presets');
        if (fileField) fileField.hidden = !useFile;
        if (urlField) urlField.hidden = !useUrl;
        if (demoPresets) demoPresets.hidden = type !== 'url';
        fileInput.disabled = !useFile;
        fileInput.value = '';
        const previous = editing === null ? null : slots.get(editing)?.saved;
        const keepFile = previous?.type === type ? previous.file : null;
        fileInput.required = useFile && !keepFile;
        urlInput.disabled = !useUrl;
        urlInput.required = useUrl;
        const urlLabels = {
            url: 'Liên kết video công khai (YouTube / MP4)',
            rtsp: 'Địa chỉ luồng Camera IP RTSP (rtsp://user:pass@ip:554/...)',
            hls: 'Đường dẫn luồng phát trực tiếp HLS (.m3u8)',
        };
        const urlPlaceholders = {
            url: 'https://www.youtube.com/watch?v=…',
            rtsp: 'rtsp://admin:password@192.168.1.50:554/Streaming/Channels/101',
            hls: 'https://example.com/live/forest-cam.m3u8',
        };
        get('#cctv-url-label').textContent = urlLabels[type] || 'Liên kết nguồn hình';
        urlInput.placeholder = urlPlaceholders[type] || 'https://…';
        fileInput.accept = type === 'image' ? 'image/jpeg,image/png,image/webp' : 'video/*,.mp4,.webm';
        get('#cctv-file-label').textContent = type === 'image' ? 'Chọn ảnh' : 'Chọn video';
        const hints = {
            rtsp: 'Kết nối thẳng tới Camera IP Hikvision/Dahua/Yoosee qua RTSP. Máy chủ kéo khung hình định kỳ, giám sát ngắt kết nối và quét khói 24/7.',
            hls: 'Phát trực tiếp các luồng camera công cộng hoặc MediaMTX định dạng .m3u8.',
            image: 'Ảnh JPG, PNG hoặc WebP, tối đa 10 MB. Ảnh được quét một lần khi mở nguồn.',
            video: 'Ưu tiên MP4 hoặc WebM. Dùng cài đặt "Bảo toàn lịch sử khi phát lại video kiểm thử" trong Cấu hình nếu muốn giữ toàn bộ bằng chứng cũ.',
            url: 'Bấm chọn nhanh 1 video cháy rừng ở trên hoặc dán link YouTube / URL MP4 công khai.',
        };
        get('#cctv-source-help').textContent = keepFile ? `Đang dùng ${keepFile.name}. Chọn tệp nếu muốn thay.` : (hints[type] || '');
        formError.hidden = true;
    }
    document.querySelectorAll('#cctv-demo-video-presets button[data-demo-url]').forEach(btn => {
        btn.addEventListener('click', () => {
            urlInput.value = btn.dataset.demoUrl || '';
            if (!nameInput.value.trim() && btn.dataset.demoName) {
                nameInput.value = btn.dataset.demoName;
            }
        });
    });
    function openDialog(id) {
        editing = id;
        const source = slots.get(id)?.saved;
        const defaultPreset = WATCHTOWER_PRESETS[(id - 1) % WATCHTOWER_PRESETS.length];
        get('#cctv-dialog-title').textContent = source ? 'Đổi nguồn giám sát' : 'Thêm nguồn giám sát';
        get('#cctv-dialog-channel').textContent = channel(id);
        nameInput.value = source?.name ?? (id > 4 ? `${defaultPreset.name} #${id}` : defaultPreset.name);
        typeInput.value = (source?.type && ACTIVE_TYPES.has(source.type)) ? source.type : 'url';
        urlInput.value = source?.inputUrl ?? defaultPreset.inputUrl;
        if (roiTopSkyInput)
            roiTopSkyInput.checked = Boolean(source?.exclusionZones?.some(z => z.label === 'Bầu trời / Mây'));
        submit.disabled = false;
        submit.textContent = 'Lưu nguồn';
        showSourceFields();
        dialog.showModal();
        nameInput.focus();
    }
    async function syncStationToServer(saved, signal) {
        if (!stationsUrl)
            return saved;
        const payload = {
            channel_number: saved.id,
            source_key: saved.key,
            name: saved.name,
            source_type: saved.type,
            stream_url: saved.inputUrl || '',
            exclusion_zones: saved.exclusionZones || [],
        };
        const response = await fetch(stationsUrl, {
            method: 'POST',
            headers: { ...headers, 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            signal,
        });
        const data = await response.json().catch(() => null);
        if (!response.ok || !data?.station) {
            throw new Error(data?.message || 'Không đồng bộ được cấu hình trạm camera.');
        }
        saved.stationId = data.station.id;
        saved.liveFrameUrl = data.station.live_frame_url;
        saved.exclusionZones = data.station.exclusion_zones || [];
        return saved;
    }
    async function api(url, body, signal) {
        const response = await fetch(url, { method: 'POST', headers, body, signal });
        if (response.redirected)
            throw new Error('Phiên đăng nhập đã hết. Hãy tải lại trang.');
        const result = await response.json().catch(() => null);
        if (!response.ok || !result)
            throw new Error(result?.message || 'Máy chủ không phản hồi đúng.');
        return result;
    }
    async function prepareSource(source, signal) {
        if (source.type === 'rtsp') {
            if (!source.stationId || !source.liveFrameUrl) {
                await syncStationToServer(source, signal);
            }
            return { label: labels.rtsp, playback: source.liveFrameUrl };
        }
        if (source.type === 'hls') {
            return { label: labels.hls, playback: source.inputUrl };
        }
        if (source.type === 'url') {
            const body = new FormData();
            body.append('url', source.inputUrl || '');
            const resolved = await api(resolveUrl, body, signal);
            return { label: resolved.type ?? labels.url, playback: resolved.url };
        }
        return { label: labels[source.type], playback: '' };
    }
    async function restoreSlot(slot, saved) {
        slot.restore?.abort();
        const operation = new AbortController();
        slot.restore = operation;
        slot.title.textContent = saved.name;
        slot.settings.hidden = false;
        slot.empty.disabled = true;
        slot.empty.textContent = 'Đang khôi phục…';
        status(slot, 'waiting', 'Đang mở nguồn đã lưu');
        try {
            const media = await prepareSource(saved, operation.signal);
            if (!operation.signal.aborted)
                mountSource(slot, saved, media.label, media.playback);
        }
        catch (error) {
            if (operation.signal.aborted)
                return;
            status(slot, 'error', 'Nguồn đã lưu');
            slot.empty.textContent = 'Mở lại nguồn';
            slot.detail.textContent = error instanceof Error ? error.message : 'Nguồn tạm thời chưa mở được.';
            slot.detail.hidden = false;
        }
        finally {
            if (slot.restore === operation) {
                slot.restore = null;
                slot.empty.disabled = false;
            }
        }
    }
    async function restoreSources() {
        let saved = [];
        try {
            storage = await openStorage();
            saved = await savedSources(storage);
        }
        catch {
            storage = null;
            storageNotice('Trình duyệt đang chặn bộ nhớ cục bộ. Nguồn mới chỉ dùng được trong phiên này.');
        }
        let serverStations = [];
        try {
            serverStations = JSON.parse(document.querySelector('#cctv-server-stations')?.textContent || '[]');
        }
        catch {
            serverStations = [];
        }
        const cleanSaved = [];
        for (const item of saved) {
            if (!ACTIVE_TYPES.has(item.type)) {
                void updateStorage(item.id, null).catch(() => { });
            }
            else {
                cleanSaved.push(item);
            }
        }
        saved = cleanSaved;
        const localById = new Map(saved.map(s => [s.id, s]));
        for (const st of serverStations) {
            if (!ACTIVE_TYPES.has(st.source_type))
                continue;
            const local = localById.get(st.channel_number);
            if (local) {
                local.stationId = st.id;
                local.liveFrameUrl = st.live_frame_url;
                local.exclusionZones = st.exclusion_zones || [];
                if (local.type !== 'image' && local.type !== 'video') {
                    local.type = st.source_type;
                    local.name = st.name;
                    local.inputUrl = st.stream_url || '';
                    if (st.source_key)
                        local.key = st.source_key;
                    void updateStorage(local.id, local).catch(() => { });
                }
            }
            else if (['rtsp', 'hls', 'url'].includes(st.source_type)) {
                saved.push({
                    id: st.channel_number,
                    key: st.source_key || newSourceKey(),
                    type: st.source_type,
                    name: st.name,
                    inputUrl: st.stream_url || '',
                    exclusionZones: st.exclusion_zones || [],
                    stationId: st.id,
                    liveFrameUrl: st.live_frame_url,
                    file: null,
                    rate: 1,
                });
            }
        }
        const priority = { image: 0, video: 0, rtsp: 1, hls: 1, url: 1 };
        const queue = saved.sort((a, b) => (priority[a.type] ?? 1) - (priority[b.type] ?? 1)).map(source => {
            const slot = slots.get(source.id) ?? createSlot(source.id);
            slot.saved = source;
            slot.title.textContent = source.name;
            slot.settings.hidden = false;
            slot.empty.textContent = 'Mở nguồn đã lưu';
            status(slot, 'waiting', 'Đang chờ mở nguồn');
            return { slot, source };
        });
        restoring = false;
        updateCounts();
        const restoreQueue = async () => {
            for (let item = queue.shift(); item; item = queue.shift()) {
                if (slots.get(item.slot.id) === item.slot && item.slot.saved === item.source
                    && !item.slot.source && !item.slot.restore) {
                    await restoreSlot(item.slot, item.source);
                }
            }
        };
        await Promise.all([restoreQueue(), restoreQueue()]);
    }
    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const id = editing;
        if (id === null || pendingSource)
            return;
        const type = typeInput.value;
        const usesUrlField = type === 'url' || type === 'rtsp' || type === 'hls';
        const inputUrl = usesUrlField ? urlInput.value.trim() : '';
        const exclusionZones = roiTopSkyInput?.checked
            ? [{ x: 0, y: 0, w: 1, h: 0.28, label: 'Bầu trời / Mây' }]
            : [];
        const previous = slots.get(id)?.saved;
        const newFile = fileInput.disabled ? null : fileInput.files?.[0] ?? null;
        const file = newFile ?? (previous?.type === type ? previous.file : null);
        const name = nameInput.value.trim() || file?.name.slice(0, 100) || channel(id);
        const sameSource = previous && previous.type === type && previous.inputUrl === inputUrl && !newFile;
        const saved = {
            id, key: sameSource ? previous.key : newSourceKey(), type, name, inputUrl, file,
            exclusionZones,
            stationId: sameSource ? previous.stationId : undefined,
            liveFrameUrl: undefined,
            rate: sameSource ? previous.rate : 1, reference: sameSource ? previous.reference : undefined,
        };
        const operation = new AbortController();
        pendingSource = operation;
        submit.disabled = true;
        submit.textContent = 'Đang mở…';
        formError.hidden = true;
        try {
            if (type === 'image' && file && file.size > 10 * 1024 * 1024) {
                throw new Error('Ảnh vượt quá 10 MB.');
            }
            if (stationsUrl) {
                await syncStationToServer(saved, operation.signal);
            }
            const media = await prepareSource(saved, operation.signal);
            if (operation.signal.aborted)
                return;
            committing = true;
            try {
                await updateStorage(id, saved);
            }
            catch {
                throw new Error('Không thể lưu nguồn. Hãy kiểm tra dung lượng trình duyệt hoặc chọn tệp nhỏ hơn.');
            }
            finally {
                committing = false;
            }
            const slot = slots.get(id) ?? createSlot(id);
            slot.restore?.abort();
            mountSource(slot, saved, media.label, media.playback);
            dialog.close();
            slot.settings.focus();
        }
        catch (error) {
            if (operation.signal.aborted)
                return;
            formError.textContent = error instanceof Error ? error.message : 'Không mở được nguồn.';
            formError.hidden = false;
        }
        finally {
            if (pendingSource === operation) {
                pendingSource = null;
                submit.disabled = false;
                submit.textContent = 'Lưu nguồn';
            }
        }
    });
    function displayIncident(incident, newest = false) {
        const start = incident.position === null ? '' : ` · từ giây ${incident.position.toLocaleString('vi-VN', {
            maximumFractionDigits: 1,
        })}`;
        const samplesLabel = `${incident.samples} khung có khói${start}`;
        const existing = eventRows.get(incident.id);
        if (existing) {
            existing.querySelector('.cctv-event-samples').textContent = samplesLabel;
            return;
        }
        const entry = document.createElement('li');
        entry.dataset.source = incident.source;
        const image = document.createElement('img');
        image.src = incident.thumbnail;
        image.alt = '';
        image.loading = 'lazy';
        const summary = document.createElement('div');
        const time = document.createElement('time');
        time.dateTime = incident.at;
        time.textContent = new Intl.DateTimeFormat('vi-VN', { dateStyle: 'short', timeStyle: 'medium' })
            .format(new Date(incident.at));
        const title = document.createElement('strong');
        title.textContent = incident.source;
        const link = document.createElement('a');
        link.href = incident.url;
        link.textContent = 'Xem bằng chứng ↗';
        const samples = document.createElement('span');
        samples.className = 'cctv-event-samples';
        samples.textContent = samplesLabel;
        summary.append(title, time, samples, link);
        entry.append(image, summary);
        eventRows.set(incident.id, entry);
        if (newest)
            log.prepend(entry);
        else
            log.append(entry);
        if (eventRows.size > 12) {
            const last = log.lastElementChild;
            for (const [id, row] of eventRows)
                if (row === last)
                    eventRows.delete(id);
            last?.remove();
        }
        get('#cctv-log-empty').hidden = true;
    }
    function announceIncident(incident) {
        get('#cctv-incident-count').textContent = String(++incidentCount);
        const banner = get('#cctv-incident-banner');
        get('#cctv-incident-source').textContent = incident.source;
        get('#cctv-incident-link').href = incident.url;
        banner.hidden = false;
        if (desktopAllowed() && document.hidden) {
            try {
                const notification = new Notification('ForestWatch · Có dấu hiệu khói', {
                    body: incident.source, tag: `forestwatch-${incident.id}`,
                });
                notification.onclick = () => {
                    window.focus();
                    window.location.assign(incident.url);
                };
            }
            catch {
                get('#cctv-notification-state').textContent = 'Thiết bị này hiển thị cảnh báo trong trang.';
            }
        }
        if (soundEnabled && audio?.state === 'running') {
            const oscillator = audio.createOscillator();
            const gain = audio.createGain();
            oscillator.frequency.value = 780;
            gain.gain.setValueAtTime(0.15, audio.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + 0.3);
            oscillator.connect(gain).connect(audio.destination);
            oscillator.start();
            oscillator.stop(audio.currentTime + 0.3);
        }
    }
    function desktopAllowed() {
        return 'Notification' in window && Notification.permission === 'granted';
    }
    function captureFrame(source) {
        const media = source.element;
        const video = media instanceof HTMLVideoElement;
        const width = video ? media.videoWidth : media.naturalWidth;
        const height = video ? media.videoHeight : media.naturalHeight;
        const scale = Math.min(1, 640 / Math.max(width, height));
        capture.width = Math.max(1, Math.round(width * scale));
        capture.height = Math.max(1, Math.round(height * scale));
        captureContext.drawImage(media, 0, 0, capture.width, capture.height);
        return new Promise((resolve, reject) => {
            const timeout = window.setTimeout(() => reject(new Error('Trình duyệt đọc khung hình quá lâu.')), 5000);
            capture.toBlob(blob => {
                window.clearTimeout(timeout);
                if (blob)
                    resolve(blob);
                else
                    reject(new Error('Không đọc được khung hình từ nguồn.'));
            }, 'image/jpeg', 0.85);
        });
    }
    function needsScan(slot) {
        const source = slot.source;
        if (!source?.started || slot.scanning || !ready(source))
            return false;
        if (slot.manual)
            return true;
        if (!autoEnabled || performance.now() < source.nextScan)
            return false;
        const media = source.element;
        if (media instanceof HTMLVideoElement) {
            return !media.paused && !media.ended && media.currentTime !== source.lastTime;
        }
        if (LIVE_IMG_TYPES.has(slot.saved?.type))
            return !!source.liveFrame && source.liveFrame.sequence !== source.lastTime;
        return !source.frame;
    }
    async function scanNext() {
        if (inFlightCount >= maxConcurrentScans)
            return;
        const ordered = Array.from(slots.values()).sort((a, b) => a.id - b.id);
        const activeCount = ordered.filter(item => item.source?.started && ready(item.source)).length;
        const eligible = ordered.filter(needsScan);
        if (!eligible.length)
            return;
        // Multi-screen priority scheduler:
        // 1. Manual scan clicked by user
        // 2. Screens never scanned yet (!source.frame) so all tiles get initial AI overlay fast
        // 3. Screens that have not triggered an alert yet (!eventCount && !captureCount && !source.frame?.alert)
        // 4. Fair round-robin sorted by oldest lastScanCompletedAt
        const slot = eligible.find(item => item.manual)
            ?? eligible.find(item => !item.source?.frame)
            ?? eligible.filter(item => !item.eventCount && !item.captureCount && !item.source?.frame?.alert)
                .sort((a, b) => (a.source?.lastScanCompletedAt || 0) - (b.source?.lastScanCompletedAt || 0))[0]
            ?? eligible.sort((a, b) => (a.source?.lastScanCompletedAt || 0) - (b.source?.lastScanCompletedAt || 0))[0];
        const source = slot?.source;
        const saved = slot?.saved;
        if (!slot || !source || !saved)
            return;
        lastSlot = slot.id;
        inFlightCount += 1;
        slot.manual = false;
        slot.scanning = true;
        slot.scan.disabled = true;
        slot.scan.textContent = 'Đang quét…';
        if (!source.frame?.has_smoke)
            status(slot, 'waiting', 'Đang phân tích');
        const media = source.element;
        const liveFrame = source.liveFrame;
        const mediaTime = liveFrame?.sequence ?? (media instanceof HTMLVideoElement ? media.currentTime : 0);
        const moving = (liveFrame !== null || media instanceof HTMLVideoElement && !media.paused)
            && mediaTime !== source.lastTime;
        if (media instanceof HTMLVideoElement && !source.isWatchtowerStream && !preserveReplayEnabled) {
            const isAtBeginning = mediaTime <= 2.0;
            const rewoundToBeginning = (source.maxSeenPosition || 0) > 3.0 && isAtBeginning;
            if (rewoundToBeginning || (isAtBeginning && !source.runCleaned)) {
                source.runCleaned = true;
                source.videoReplayed = true;
                void resetVideoAlerts(slot);
            }
            if (mediaTime > (source.maxSeenPosition || 0)) {
                source.maxSeenPosition = mediaTime;
                if (mediaTime > 3.0) {
                    source.runCleaned = false;
                }
            }
        }
        try {
            const body = new FormData();
            const frame = liveFrame?.image ?? source.imageFile ?? await captureFrame(source);
            body.append('image', frame, source.imageFile?.name ?? 'frame.jpg');
            const channelName = channel(slot.id);
            body.append('source', saved.name === channelName ? channelName : `${channelName} · ${saved.name}`);
            body.append('source_key', saved.key);
            body.append('source_kind', saved.type);
            body.append('moving', moving ? '1' : '0');
            body.append('preserve_replay', (preserveReplayEnabled || source.isWatchtowerStream) ? '1' : '0');
            if (saved.stationId)
                body.append('station_id', saved.stationId);
            if (saved.exclusionZones?.length)
                body.append('exclusion_zones', JSON.stringify(saved.exclusionZones));
            if (referenceEnabled && saved.reference)
                body.append('reference', saved.reference, 'reference.jpg');
            if (saved.type === 'video' || saved.type === 'url')
                body.append('position', String(mediaTime));
            if (source.videoReplayed) {
                body.append('video_replayed', '1');
                source.videoReplayed = false;
            }
            const result = await api(scanUrl, body, AbortSignal.timeout(180000));
            if (slots.get(slot.id)?.source !== source)
                return;
            source.lastTime = mediaTime;
            source.overlayTime = media instanceof HTMLVideoElement ? media.currentTime : mediaTime;
            source.lastScanCompletedAt = performance.now();
            source.frame = result;
            const othersWaitingInitial = ordered.some(s => s.id !== slot.id && s.source?.started && (!s.source.frame || (!s.eventCount && !s.captureCount && !s.source.frame?.alert)));
            const adaptiveCooldown = othersWaitingInitial
                ? Math.max(3000, activeCount * 1100)
                : Math.max(2200, activeCount * 650);
            source.nextScan = performance.now() + (LIVE_IMG_TYPES.has(saved.type) ? 2500 : (source.isWatchtowerStream ? adaptiveCooldown : Math.min(1800, adaptiveCooldown)));
            if (result.event) {
                slot.evidence.href = result.event.url;
                slot.evidence.hidden = false;
                slot.captureCount = (slot.captureCount || 0) + (result.event.latest_capture ? 1 : 0);
                if (result.event_created)
                    slot.eventCount = (slot.eventCount || 0) + 1;
                if (result.event.latest_capture) {
                    slot.captures = [result.event.latest_capture, ...(slot.captures || []).filter(c => c.id !== result.event.latest_capture.id)].slice(0, 80);
                }
                if (slot.alertsCount)
                    slot.alertsCount.textContent = String(slot.captureCount || slot.eventCount || 1);
                if (slot.alertsBtn) {
                    slot.alertsBtn.hidden = false;
                    slot.alertsBtn.classList.add('has-alerts');
                }
                if (activeReportSlotId === slot.id && result.event.latest_capture) {
                    livePrependCaptureToModal(slot, result.event.latest_capture);
                }
                displayIncident(result.event, result.event_created);
                if (result.event_created)
                    announceIncident(result.event);
            }
            const coverage = result.detections[0]?.mask_coverage;
            const confidence = Math.max(0, ...result.detections.map(detection => detection.confidence ?? 0));
            const location = media instanceof HTMLVideoElement && saved.type !== 'hls'
                ? ` · giây ${mediaTime.toLocaleString('vi-VN', { maximumFractionDigits: 1 })}` : '';
            const summary = result.suspected
                ? `Nghi vấn (${result.streak}/${result.required_streak})`
                : result.alert ? 'Có dấu hiệu khói' : result.has_smoke ? 'Dưới ngưỡng lưu' : 'Chưa ghi nhận khói';
            slot.detail.textContent = `Lần quét gần nhất${location} · ${summary} · ${result.elapsed_ms} ms`;
            if (coverage !== undefined)
                slot.detail.append(` · vùng khói ${coverage}%`);
            if (confidence > 0) {
                const percent = Math.floor(confidence * 1000) / 10;
                slot.detail.append(` · điểm nhận diện ${percent.toLocaleString('vi-VN')}%`);
            }
            slot.detail.hidden = false;
            drawOverlay(slot, result);
            showMediaState(slot, source);
        }
        catch (error) {
            if (slots.get(slot.id)?.source !== source)
                return;
            source.nextScan = performance.now() + 2000;
            status(slot, 'error', 'Lỗi quét');
            slot.detail.textContent = error instanceof Error ? error.message : 'Không quét được khung hình.';
            slot.detail.hidden = false;
        }
        finally {
            inFlightCount = Math.max(0, inFlightCount - 1);
            if (slots.get(slot.id)?.source === source) {
                slot.scanning = false;
                slot.scan.textContent = 'Quét ngay';
                slot.scan.disabled = !ready(source) || slot.manual;
            }
            window.setTimeout(() => void scanNext(), 10);
        }
    }
    let activeReportSlotId = null;
    let reportViewMode = 'annotated';
    const reportDialog = document.querySelector('#cctv-screen-report-dialog');
    function slotSourceLabel(slot) {
        const channelName = channel(slot.id);
        const saved = slot.saved;
        if (!saved)
            return channelName;
        return saved.name === channelName ? channelName : `${channelName} · ${saved.name}`;
    }
    async function loadScreenStats(slot) {
        if (!screenReportsUrl || !slot.saved)
            return null;
        try {
            const params = new URLSearchParams({
                source_key: slot.saved.key,
                source: slotSourceLabel(slot),
            });
            const response = await fetch(`${screenReportsUrl}?${params.toString()}`, {
                headers: { Accept: 'application/json' },
            });
            if (!response.ok)
                return null;
            const data = await response.json();
            slot.captureCount = data.total_captures || 0;
            slot.eventCount = data.total_alerts || 0;
            slot.captures = data.captures || [];
            if (slot.alertsCount)
                slot.alertsCount.textContent = String(slot.captureCount || slot.eventCount || 0);
            if (slot.alertsBtn) {
                slot.alertsBtn.hidden = false;
                slot.alertsBtn.classList.toggle('has-alerts', (slot.captureCount || slot.eventCount) > 0);
            }
            if (data.events?.[0]?.url) {
                slot.evidence.href = data.events[0].url;
                slot.evidence.hidden = false;
            }
            return data;
        }
        catch {
            return null;
        }
    }
    function renderCaptureCard(cap, mode = reportViewMode) {
        const card = document.createElement('article');
        card.className = 'cctv-capture-item';
        card.dataset.captureId = String(cap.id);
        card.dataset.annotated = cap.annotated_url;
        card.dataset.original = cap.original_url;
        const imgLink = document.createElement('a');
        imgLink.href = cap.event_url || cap.annotated_url;
        imgLink.className = 'cctv-capture-thumb';
        const img = document.createElement('img');
        img.src = mode === 'original' ? cap.original_url : cap.annotated_url;
        img.alt = `Ảnh chụp khói ${cap.created_at}`;
        img.loading = 'lazy';
        imgLink.append(img);
        const meta = document.createElement('div');
        meta.className = 'cctv-capture-meta';
        const top = document.createElement('div');
        top.className = 'cctv-capture-top';
        const timeEl = document.createElement('strong');
        timeEl.textContent = cap.created_at;
        const badge = document.createElement('span');
        badge.className = 'badge badge-danger';
        badge.textContent = `Khói · ${Math.round((cap.confidence || cap.score || 0) * 100)}%`;
        top.append(timeEl, badge);
        const sub = document.createElement('p');
        const pos = cap.position !== null && cap.position !== undefined
            ? `Giây ${Number(cap.position).toLocaleString('vi-VN', { maximumFractionDigits: 1 })} · `
            : '';
        sub.textContent = `${pos}Vùng khói ${Math.round((cap.score || 0) * 10) / 10}% · ${cap.elapsed_ms || 0} ms`;
        const links = document.createElement('div');
        links.className = 'cctv-capture-links';
        if (cap.event_url) {
            const detailLink = document.createElement('a');
            detailLink.href = cap.event_url;
            detailLink.textContent = 'Mở báo cáo chi tiết ↗';
            links.append(detailLink);
        }
        const rawLink = document.createElement('a');
        rawLink.href = cap.annotated_url;
        rawLink.target = '_blank';
        rawLink.rel = 'noopener';
        rawLink.textContent = 'Ảnh phóng to';
        links.append(rawLink);
        meta.append(top, sub, links);
        card.append(imgLink, meta);
        return card;
    }
    function livePrependCaptureToModal(slot, cap) {
        if (!reportDialog?.open || activeReportSlotId !== slot.id)
            return;
        const gridEl = reportDialog.querySelector('#cctv-report-captures-grid');
        const emptyEl = reportDialog.querySelector('#cctv-report-empty');
        const capCountEl = reportDialog.querySelector('#cctv-report-total-captures');
        const alertCountEl = reportDialog.querySelector('#cctv-report-total-alerts');
        if (capCountEl)
            capCountEl.textContent = String(slot.captureCount || 1);
        if (alertCountEl)
            alertCountEl.textContent = String(slot.eventCount || 1);
        if (emptyEl)
            emptyEl.hidden = true;
        if (gridEl && !gridEl.querySelector(`[data-capture-id="${cap.id}"]`)) {
            gridEl.prepend(renderCaptureCard(cap, reportViewMode));
        }
    }
    async function openScreenReport(slot) {
        if (!reportDialog)
            return;
        activeReportSlotId = slot.id;
        const label = slotSourceLabel(slot);
        const titleEl = reportDialog.querySelector('#cctv-report-dialog-title');
        const subEl = reportDialog.querySelector('#cctv-report-dialog-subtitle');
        const capCountEl = reportDialog.querySelector('#cctv-report-total-captures');
        const alertCountEl = reportDialog.querySelector('#cctv-report-total-alerts');
        const fullLinkEl = reportDialog.querySelector('#cctv-report-full-link');
        const emptyEl = reportDialog.querySelector('#cctv-report-empty');
        const gridEl = reportDialog.querySelector('#cctv-report-captures-grid');
        if (titleEl)
            titleEl.textContent = `Báo cáo khói: ${label}`;
        if (subEl)
            subEl.textContent = 'Đang tải chuỗi ảnh chụp liên tục từ màn hình này…';
        if (gridEl)
            gridEl.replaceChildren();
        if (!reportDialog.open)
            reportDialog.showModal();
        const data = await loadScreenStats(slot);
        if (activeReportSlotId !== slot.id)
            return;
        const captures = data?.captures ?? slot.captures ?? [];
        if (subEl)
            subEl.textContent = 'Tự động chụp và lưu liên tục mọi khung hình phát hiện khói trên màn hình này.';
        if (capCountEl)
            capCountEl.textContent = String(data?.total_captures ?? captures.length);
        if (alertCountEl)
            alertCountEl.textContent = String(data?.total_alerts ?? slot.eventCount ?? 0);
        if (fullLinkEl && data?.alerts_url)
            fullLinkEl.href = data.alerts_url;
        if (emptyEl)
            emptyEl.hidden = captures.length > 0;
        if (gridEl) {
            gridEl.replaceChildren(...captures.map(cap => renderCaptureCard(cap, reportViewMode)));
        }
    }
    reportDialog?.querySelector('#cctv-close-report-dialog')?.addEventListener('click', () => {
        reportDialog.close();
    });
    reportDialog?.addEventListener('close', () => {
        activeReportSlotId = null;
    });
    const modeAnnotatedBtn = reportDialog?.querySelector('#cctv-report-mode-annotated');
    const modeOriginalBtn = reportDialog?.querySelector('#cctv-report-mode-original');
    function setReportViewMode(mode) {
        reportViewMode = mode;
        modeAnnotatedBtn?.classList.toggle('is-active', mode === 'annotated');
        modeOriginalBtn?.classList.toggle('is-active', mode === 'original');
        reportDialog?.querySelectorAll('.cctv-capture-item').forEach(card => {
            const img = card.querySelector('img');
            if (img)
                img.src = (mode === 'original' ? card.dataset.original : card.dataset.annotated) || img.src;
        });
    }
    const clearReportBtn = reportDialog?.querySelector('#cctv-report-clear-btn');
    clearReportBtn?.addEventListener('click', async () => {
        if (!activeReportSlotId) return;
        const targetSlot = slots.get(activeReportSlotId);
        if (!targetSlot) return;
        if (!confirm('Bạn có chắc chắn muốn xóa toàn bộ cảnh báo của màn hình này không?'))
            return;
        clearReportBtn.disabled = true;
        clearReportBtn.textContent = 'Đang xóa…';
        try {
            await resetVideoAlerts(targetSlot);
            const gridEl = reportDialog.querySelector('#cctv-report-captures-grid');
            const emptyEl = reportDialog.querySelector('#cctv-report-empty');
            const capCountEl = reportDialog.querySelector('#cctv-report-total-captures');
            const alertCountEl = reportDialog.querySelector('#cctv-report-total-alerts');
            if (gridEl) gridEl.replaceChildren();
            if (emptyEl) emptyEl.hidden = false;
            if (capCountEl) capCountEl.textContent = '0';
            if (alertCountEl) alertCountEl.textContent = '0';
        } finally {
            clearReportBtn.disabled = false;
            clearReportBtn.textContent = '🧹 Xóa cảnh báo màn hình này';
        }
    });
    modeAnnotatedBtn?.addEventListener('click', () => setReportViewMode('annotated'));
    modeOriginalBtn?.addEventListener('click', () => setReportViewMode('original'));
    typeInput.addEventListener('change', showSourceFields);
    dialog.querySelectorAll('[data-close-dialog]').forEach(button => {
        button.addEventListener('click', () => { if (!committing)
            dialog.close(); });
    });
    dialog.addEventListener('cancel', event => { if (committing)
        event.preventDefault(); });
    dialog.addEventListener('close', () => {
        pendingSource?.abort();
        pendingSource = null;
        editing = null;
    });
    add.addEventListener('click', () => {
        const empty = Array.from(slots.values()).find(slot => !slot.saved);
        const free = Array.from({ length: maxSlots }, (_, index) => index + 1).find(id => !slots.has(id));
        const id = empty?.id ?? free;
        if (id !== undefined)
            openDialog(id);
    });
    const loadRealDemosBtn = document.querySelector('#cctv-load-real-demos');
    loadRealDemosBtn?.addEventListener('click', async () => {
        loadRealDemosBtn.disabled = true;
        const origLabel = loadRealDemosBtn.textContent;
        loadRealDemosBtn.textContent = '⏳ Đang kết nối 4 Camera Chòi Canh…';
        const watchtowerPresets = [
            { id: 1, name: 'Trạm Chòi Canh Bắc', inputUrl: 'https://www.youtube.com/watch?v=wSp-pUDcFQI' },
            { id: 2, name: 'Trạm Đồi Thông Đông', inputUrl: 'https://www.youtube.com/watch?v=RWoYegPgRF4' },
            { id: 3, name: 'Trạm Quan Trắc Nam', inputUrl: 'https://www.youtube.com/watch?v=TtUgnRqARUE' },
            { id: 4, name: 'Trạm Đỉnh Đèo Tây', inputUrl: 'https://www.youtube.com/watch?v=FZNDqZJvc5w' },
        ];
        try {
            for (const preset of watchtowerPresets) {
                const slot = slots.get(preset.id) ?? createSlot(preset.id);
                const prev = slot.saved;
                const saved = {
                    id: preset.id,
                    key: (prev && prev.inputUrl === preset.inputUrl) ? prev.key : newSourceKey(),
                    type: 'url',
                    name: preset.name,
                    inputUrl: preset.inputUrl,
                    file: null,
                    exclusionZones: [],
                    rate: 1,
                };
                if (stationsUrl) {
                    await syncStationToServer(saved).catch(() => { });
                }
                await updateStorage(preset.id, saved).catch(() => { });
                const media = await prepareSource(saved);
                mountSource(slot, saved, media.label, media.playback);
            }
        }
        finally {
            loadRealDemosBtn.disabled = false;
            loadRealDemosBtn.textContent = origLabel;
        }
    });
    auto.addEventListener('click', () => {
        autoEnabled = !autoEnabled;
        auto.textContent = autoEnabled ? 'Tạm dừng quét' : 'Tiếp tục quét';
        auto.setAttribute('aria-pressed', String(autoEnabled));
        get('#cctv-auto-state').textContent = autoEnabled ? 'Đang bật' : 'Đã tạm dừng';
        for (const slot of slots.values()) {
            const source = slot.source;
            if (!source || (slot.saved?.type !== 'video' && slot.saved?.type !== 'url')
                || !(source.element instanceof HTMLVideoElement))
                continue;
            const media = source.element;
            if (!autoEnabled) {
                source.resumeOnScan = source.started && !media.paused && !media.ended;
                if (source.resumeOnScan)
                    media.pause();
            }
            else if (source.resumeOnScan) {
                source.resumeOnScan = false;
                void media.play().catch(() => showMediaState(slot, source));
            }
            showMediaState(slot, source);
        }
        void scanNext();
    });
    sound.addEventListener('click', async () => {
        if (!soundEnabled) {
            try {
                audio ?? (audio = new AudioContext());
                await audio.resume();
            }
            catch {
                sound.textContent = 'Không mở được chuông';
                return;
            }
        }
        soundEnabled = !soundEnabled;
        sound.textContent = soundEnabled ? 'Tắt chuông' : 'Bật chuông';
        sound.setAttribute('aria-pressed', String(soundEnabled));
    });
    get('#cctv-dismiss-incident').addEventListener('click', () => {
        get('#cctv-incident-banner').hidden = true;
    });
    document.querySelector('#cctv-clear-log')?.addEventListener('click', () => {
        eventRows.clear();
        log.replaceChildren();
        const emptyEl = document.querySelector('#cctv-log-empty');
        if (emptyEl) emptyEl.hidden = false;
    });
    const notify = get('#cctv-notify');
    const notificationState = get('#cctv-notification-state');
    function updateNotificationState() {
        notify.hidden = !('Notification' in window) || desktopAllowed();
        notificationState.textContent = desktopAllowed() ? 'Thông báo nền đã bật.' : '';
    }
    notify.addEventListener('click', async () => {
        try {
            await Notification.requestPermission();
            updateNotificationState();
            if (!desktopAllowed())
                notificationState.textContent = 'Cho phép thông báo trong cài đặt trang của trình duyệt.';
        }
        catch {
            notificationState.textContent = 'Trình duyệt chưa hỗ trợ thông báo nền cho trang này.';
        }
    });
    updateNotificationState();
    for (const button of layouts) {
        button.addEventListener('click', () => {
            manualLayout = true;
            setLayout(Number(button.dataset.cols));
        });
    }
    for (let id = 1; id <= 4; id++)
        createSlot(id);
    updateCounts();
    const recent = JSON.parse(get('#cctv-recent-events').textContent || '[]');
    for (const incident of recent)
        displayIncident(incident);
    void restoreSources();
    window.addEventListener('pagehide', () => {
        for (const slot of slots.values()) {
            if (slot.saved && slot.source?.element instanceof HTMLVideoElement)
                rememberPosition(slot.saved, slot.source.element);
        }
    });
    function updateClock() {
        const now = new Date();
        get('#cctv-clock').textContent = clockFormat.format(now);
        get('#cctv-date').textContent = dateFormat.format(now);
    }
    updateClock();
    window.setInterval(updateClock, 1000);
    window.setInterval(() => void scanNext(), 50);
})();
