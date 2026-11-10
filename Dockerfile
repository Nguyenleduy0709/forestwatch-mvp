FROM python:3.12-slim

# Prevent Python from writing .pyc and buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install essential system libraries for OpenCV, FFmpeg video handling, and C extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    ffmpeg \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install PyTorch CPU (clean, deterministic wheel install for all platforms)
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

# Install application dependencies
COPY backend/requirements.txt backend/requirements.txt
COPY backend/requirements-fosp.txt backend/requirements-fosp.txt
COPY backend/requirements-video.txt backend/requirements-video.txt

RUN pip install --no-cache-dir -r backend/requirements-fosp.txt && \
    pip install --no-cache-dir -r backend/requirements-video.txt

# Copy project source code
COPY . .

# Normalize line endings and make entrypoint executable
RUN sed -i 's/\r$//' docker-entrypoint.sh && chmod +x docker-entrypoint.sh

# Expose application port
EXPOSE 8000

# Run entrypoint script
ENTRYPOINT ["./docker-entrypoint.sh"]
