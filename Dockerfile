FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    DJANGO_DEBUG=1 \
    DJANGO_ALLOWED_HOSTS=* \
    INFERENCE_MODE=fosp \
    FOSP_MODEL_PATH=/app/best.pth \
    FOSP_RUNTIME_DIR=/opt/fosp-runtime \
    DATABASE_PATH=/app/backend/db/db.sqlite3 \
    MEDIA_ROOT=/app/backend/media \
    CACHE_DIR=/app/work/cache \
    TIME_ZONE=Asia/Ho_Chi_Minh \
    LOGIN_REDIRECT_URL=dashboard

WORKDIR /app

# Essential system packages (OpenCV, FFmpeg, YouTube JS runtime, Vietnamese font, git for FoSp runtime, curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    ffmpeg \
    nodejs \
    fonts-dejavu-core \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Bake FoSp & MMCV runtime into image so fresh git-cloned hosts don't need manual runtime cloning
RUN git -c core.symlinks=false clone https://github.com/LujianYao/FoSp.git /opt/fosp-runtime/FoSp && \
    git -C /opt/fosp-runtime/FoSp checkout 02b9fd18ad1930d1081cf3b35a569d4b59e71d63 && \
    rm -rf /opt/fosp-runtime/FoSp/.git && \
    git clone --branch v1.6.2 --depth 1 https://github.com/open-mmlab/mmcv.git /opt/fosp-runtime/mmcv && \
    rm -rf /opt/fosp-runtime/mmcv/.git /opt/fosp-runtime/mmcv/tests

# Install PyTorch CPU
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Install Python application dependencies
COPY backend/requirements.txt backend/requirements.txt
COPY backend/requirements-fosp.txt backend/requirements-fosp.txt
COPY backend/requirements-video.txt backend/requirements-video.txt

RUN pip install --no-cache-dir -r backend/requirements-fosp.txt && \
    pip install --no-cache-dir -r backend/requirements-video.txt

# Patch upstream FoSp hardcoded .cuda()/device('cuda') calls so PyTorch CPU works seamlessly
RUN python3 -c "from pathlib import Path; \
root = Path('/opt/fosp-runtime/FoSp'); \
patches = [ \
    ('mmseg/models/decode_heads/FOSP_main.py', \"torch.device('cuda')\", \"torch.device('cuda' if torch.cuda.is_available() else 'cpu')\"), \
    ('mmseg/models/decode_heads/FOSP_main.py', '.unsqueeze(1).cuda()', '.unsqueeze(1).to(seg.device)'), \
    ('mmseg/models/decode_heads/base_decode_head.py', '.unsqueeze(1).cuda()', '.unsqueeze(1).to(seg.device)'), \
    ('mmseg/models/segmentors/encoder_decoder_fosp.py', '.float().cuda()', '.float().to(image.device)'), \
]; \
[p.write_text(p.read_text(encoding='utf-8').replace(old, new), encoding='utf-8') for p, old, new in ((root / rel, o, n) for rel, o, n in patches) if (old in p.read_text(encoding='utf-8')) or (_ for _ in ()).throw(RuntimeError(f'Missing {old} in {p}'))]"

# Copy runtime application code (and host checkpoint/DB/demo_streams if present)
COPY . .

RUN mkdir -p /app/work/cache \
    /app/backend/db \
    /app/backend/weights \
    /app/backend/media/captures \
    /app/backend/media/events \
    /app/backend/media/originals \
    /app/backend/media/results \
    /app/backend/media/demo_streams && \
    sed -i 's/\r$//' docker-entrypoint.sh && \
    chmod +x docker-entrypoint.sh

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/ || exit 1

ENTRYPOINT ["./docker-entrypoint.sh"]
