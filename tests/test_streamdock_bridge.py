"""测试 macOS StreamDock 桥接层的输入编号和清屏契约。

测试只覆盖纯函数与图片尺寸，不启动 uvicorn、不连接真实 HID，也不访问用户桌面。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

_VENDOR_SDK_SRC = Path(__file__).parents[1] / "vendor" / "streamdock-python-sdk" / "src"
sys.path.insert(0, str(_VENDOR_SDK_SRC))

from agent_deck.hardware.remote_streamdock import _blank_main_key_image


_BRIDGE_PATH = Path(__file__).parents[1] / "scripts" / "streamdock-bridge.py"
_SPEC = importlib.util.spec_from_file_location("agent_deck_streamdock_bridge_test", _BRIDGE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_BRIDGE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BRIDGE)


def _button_event(key: int, state: int = 1) -> SimpleNamespace:
    """构造最小 SDK button event 替身。"""

    return SimpleNamespace(
        event_type="button",
        key=SimpleNamespace(value=key),
        state=state,
    )


def test_sdk_main_and_secondary_keys_are_zero_based_layout_indexes() -> None:
    """主键 1..10 与副屏键 11..14 都应映射到可路由的 0-based index。"""

    assert _BRIDGE._hardware_input_payload(_button_event(1))["index"] == 0
    assert _BRIDGE._hardware_input_payload(_button_event(10))["index"] == 9
    assert _BRIDGE._hardware_input_payload(_button_event(11))["index"] == 0
    assert _BRIDGE._hardware_input_payload(_button_event(14))["index"] == 3
    assert _BRIDGE._hardware_input_payload(_button_event(15)) is None


def test_blank_main_key_image_clears_stale_codex_icon() -> None:
    """未配置主键必须提供 112x112 的稳定清屏图。"""

    image = _blank_main_key_image()
    assert isinstance(image, Image.Image)
    assert image.size == (112, 112)
    assert image.getpixel((0, 0)) == (11, 15, 22)


def test_real_n4pro_sdk_decoder_uses_logical_keys_before_bridge_mapping() -> None:
    """Decode real N4 Pro hardware codes, then verify bridge layout indexes."""

    from StreamDock.Devices.StreamDockN4Pro import StreamDockN4Pro

    device = object.__new__(StreamDockN4Pro)
    device.read_thread = None
    device.heartbeat_thread = None
    device.run_read_thread = False
    device.run_heartbeat_thread = False
    device._notify_on_close = False
    device.close = lambda notify=True: None
    main = device.decode_input_event(11, 0x01)
    secondary = device.decode_input_event(1, 0x01)
    assert main.key.value == 1
    assert secondary.key.value == 11
    assert _BRIDGE._hardware_input_payload(main)["index"] == 0
    assert _BRIDGE._hardware_input_payload(secondary)["index"] == 0


def test_secondary_keys_mirror_actual_main_images(monkeypatch):
    """前四个副屏必须镜像当前绑定图，不能把 Agent 错标为 Quota/Today。"""
    import base64
    from io import BytesIO
    import httpx
    from agent_deck.hardware.remote_streamdock import RemoteStreamDockN4ProRenderer
    captured = {}

    def post(self, url, *, json):
        """截获 renderer 上传快照，不连接硬件。"""
        captured.update(json)
        return httpx.Response(200, request=httpx.Request('POST', url), json={'ok': True})

    monkeypatch.setattr(httpx.Client, 'post', post)
    colors = {1: 'red', 2: 'green', 3: 'blue', 4: 'yellow'}
    images = {key: Image.new('RGB', (112, 112), color) for key, color in colors.items()}
    result = RemoteStreamDockN4ProRenderer('http://mac')(
        background_image=None, key_frame_paths={}, duration_seconds=1, fps=1, key_images=images,
    )
    assert result.ok
    for key in colors:
        secondary = Image.open(BytesIO(base64.b64decode(captured['key_png_base64'][str(key + 10)])))
        assert secondary.size == (176, 112)
        assert secondary.getpixel((88, 56)) == images[key].getpixel((56, 56))
