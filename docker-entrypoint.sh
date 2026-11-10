#!/bin/sh
set -e

# Apply database migrations if any are pending
echo "==> Đang kiểm tra và cập nhật Database migrations..."
python backend/manage.py migrate --noinput

# Start development / production server
echo "==> Khởi động máy chủ ForestWatch trên http://0.0.0.0:8000..."
exec python backend/manage.py runserver 0.0.0.0:8000
