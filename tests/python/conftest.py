"""Cho pytest tìm được module trong server/.

Hiện CI KHÔNG chạy pytest mà chạy từng file như script (`python tests/python/test_x.py`),
vì 61 trên 76 file gọi `sys.exit()` ngay ở mức module - dưới pytest thì bước collect import
từng file là chạy luôn assertion rồi thoát, huỷ cả lượt chạy. Chuyển dần sang pytest thật là
việc nền, không nằm trên đường găng.

File này tồn tại để khi chạy `pytest tests/python/test_abc.py` với một file ĐÃ chuyển sang
kiểu pytest thì nó import được module server. Import `_paths` là đủ: chính nó nạp server/
vào sys.path, và trỏ thư mục tạm của tiến trình vào một thư mục riêng tự xoá khi thoát.
"""
import os

import _paths


def pytest_configure(config):
    # tmp_path nằm trong thư mục tạm của _paths (xoá cùng lúc), và NGẮN hơn mặc định
    # `pytest-of-<user>/pytest-N/`: Windows chưa bật long path thì vượt 260 ký tự là ghi hỏng.
    if not config.option.basetemp:
        config.option.basetemp = os.path.join(_paths.TEST_TMP, "p")
