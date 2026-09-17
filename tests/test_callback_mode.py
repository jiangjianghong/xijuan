"""callback_mode 简洁回调粒度测试。"""

from utils.callback import (
    CALLBACK_MODE_FULL,
    CALLBACK_MODE_SIMPLE,
    is_simple_callback,
)


def test_is_simple_callback_defaults_to_full():
    assert is_simple_callback(None) is False
    assert is_simple_callback("") is False
    assert is_simple_callback(CALLBACK_MODE_FULL) is False
    assert is_simple_callback(CALLBACK_MODE_SIMPLE) is True
