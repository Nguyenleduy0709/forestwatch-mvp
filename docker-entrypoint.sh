#!/bin/sh
set -e

mkdir -p /app/work/cache \
  /app/backend/db \
  /app/backend/weights \
  /app/backend/media/captures \
  /app/backend/media/events \
  /app/backend/media/originals \
  /app/backend/media/results \
  /app/backend/media/demo_streams

# Resolve runtime directory
if [ ! -d "${FOSP_RUNTIME_DIR:-}" ] && [ -d "/opt/fosp-runtime/FoSp" ]; then
  export FOSP_RUNTIME_DIR="/opt/fosp-runtime"
fi

# Resolve model checkpoint or gracefully fall back to demo mode on fresh clones without weights
if [ ! -f "${FOSP_MODEL_PATH:-}" ]; then
  if [ -f "/app/backend/weights/best.pth" ]; then
    export FOSP_MODEL_PATH="/app/backend/weights/best.pth"
  elif [ -f "/app/best.pth" ]; then
    export FOSP_MODEL_PATH="/app/best.pth"
  elif [ "${INFERENCE_MODE:-fosp}" = "fosp" ]; then
    echo "==> Lưu ý: Chưa tìm thấy checkpoint best.pth (/app/backend/weights/best.pth hoặc /app/best.pth)."
    echo "==> Tạm chuyển sang INFERENCE_MODE=demo để web vẫn khởi động sẵn sàng."
    export INFERENCE_MODE="demo"
  fi
fi

echo "==> Kiểm tra và áp dụng Database migrations..."
python backend/manage.py migrate --noinput

echo "==> Kiểm tra cấu hình hệ thống (${INFERENCE_MODE})..."
python backend/manage.py check

echo "==> Khởi động máy chủ ForestWatch trên http://0.0.0.0:8000 ..."
exec python -u backend/manage.py runserver 0.0.0.0:8000 --noreload
