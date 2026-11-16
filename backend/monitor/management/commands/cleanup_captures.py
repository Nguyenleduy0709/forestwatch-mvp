from django.core.management.base import BaseCommand
from monitor.notifications import cleanup_old_captures


class Command(BaseCommand):
    help = 'Dọn dẹp các ảnh quét khói cũ quá hạn lưu trữ để giải phóng dung lượng đĩa.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=None, help='Ghi đè số ngày lưu trữ (tùy chọn).')

    def handle(self, *args, **options):
        deleted = cleanup_old_captures(retention_days=options['days'])
        self.stdout.write(self.style.SUCCESS(f'Đã dọn dẹp {deleted} ảnh quét cũ hết hạn.'))
