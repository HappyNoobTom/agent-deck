"""通过 HTTP 把 N4 Pro 渲染帧转发给 macOS 硬件桥接层。

本模块只负责容器侧的网络适配，不加载官方 StreamDock SDK，也不直接访问 HID。容器把
当前触屏背景和按键静态图提交给宿主机桥接层，桥接层再使用 macOS 官方 SDK 写入实体设备。
网络不可达、响应超时或桥接层返回错误均转换为 `StreamDockN4ProAnimationResult`，避免硬件
故障终止 Agent Deck daemon。
"""

from __future__ import annotations

import base64
import io
import time
from collections.abc import Mapping
from pathlib import Path

import httpx
from PIL import Image

from agent_deck.hardware.streamdock_n4pro import (
    StreamDockN4ProAnimationResult,
    StreamDockN4ProKeyImageSource,
)


class RemoteStreamDockN4ProRenderer:
    """把 N4 Pro 当前显示快照提交给宿主机桥接服务。"""

    def __init__(self, base_url: str, *, timeout_seconds: float = 8.0) -> None:
        """初始化远端 renderer。

        入参：`base_url` 是宿主机桥接服务的 HTTP 根地址；`timeout_seconds` 必须为正数。
        返回：不访问网络的 renderer 实例。
        错误处理：参数非法时抛出 `ValueError`；网络错误在调用时转为失败结果。
        副作用：仅保存配置，不连接设备。
        """

        normalized = base_url.rstrip("/")
        if not normalized:
            raise ValueError("base_url must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.base_url = normalized
        self.timeout_seconds = timeout_seconds

    def __call__(
        self,
        *,
        background_image: Image.Image | None,
        key_frame_paths: Mapping[int, tuple[Path, ...]],
        duration_seconds: float,
        fps: int,
        key_images: Mapping[int, StreamDockN4ProKeyImageSource] | None = None,
    ) -> StreamDockN4ProAnimationResult:
        """提交一次显示快照。

        入参：背景图、按键静态图和已有动画帧遵循本地 N4 Pro sink 契约；远端桥接当前使用每个
        按键的首帧或静态图，持续刷新由桥接层维护。返回：兼容本地 sink 的结果模型。
        错误处理：序列化、HTTP、非 2xx 和非法响应均转为 `ok=False`。
        副作用：向 macOS 桥接层上传图片，可能触发实体 N4 Pro 更新。
        """

        encoded_background = (
            base64.b64encode(_image_bytes(background_image, "JPEG")).decode("ascii")
            if background_image is not None
            else None
        )
        merged: dict[int, StreamDockN4ProKeyImageSource] = dict(key_images or {})
        for key, paths in key_frame_paths.items():
            if paths and key not in merged:
                merged[key] = paths[0]
        # 空键也显式下发，清除设备持久保存的旧图标。
        for key in range(1, 16):
            merged.setdefault(key, _blank_main_key_image())
        # 副屏输入复用前四个 binding，画面必须镜像同一 binding 的实际内容。
        for key in range(11, 15):
            source = merged[key - 10]
            if isinstance(source, Image.Image):
                image = source
            else:
                with Image.open(source) as opened:
                    image = opened.convert("RGB")
            secondary = Image.new("RGB", (176, 112), image.getpixel((0, 0)))
            secondary.paste(image.resize((112, 112), Image.Resampling.LANCZOS), (32, 0))
            merged[key] = secondary
        encoded_keys = {
            str(int(key)): base64.b64encode(_source_bytes(source)[0]).decode("ascii")
            for key, source in merged.items()
        }
        started = time.monotonic()
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    f"{self.base_url}/render",
                    json={
                        "duration_seconds": duration_seconds,
                        "fps": fps,
                        "background_jpeg_base64": encoded_background,
                        "key_png_base64": encoded_keys,
                    },
                )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("ok") is False:
                error = payload.get("error", "bridge returned an unsuccessful result") if isinstance(payload, dict) else "invalid bridge response"
                return StreamDockN4ProAnimationResult(ok=False, error=str(error))
            return StreamDockN4ProAnimationResult(
                ok=True,
                device_type=str(payload.get("device_type")) if payload.get("device_type") else "remote-n4pro",
                path=str(payload.get("path")) if payload.get("path") else self.base_url,
                frames_rendered=int(payload.get("frames_rendered", 1)),
                key_count=int(payload.get("key_count", len(merged))),
                timing_seconds={"bridge": time.monotonic() - started},
            )
        except Exception as exc:  # noqa: BLE001 - renderer failure is recorded by daemon
            return StreamDockN4ProAnimationResult(
                ok=False,
                device_type="remote-n4pro",
                path=self.base_url,
                key_count=len(merged),
                timing_seconds={"bridge": time.monotonic() - started},
                error=f"{type(exc).__name__}: {exc}",
            )


def _blank_main_key_image() -> Image.Image:
    """Return the stable blank image used to clear an unused 112x112 key."""

    return Image.new("RGB", (112, 112), (11, 15, 22))


def _image_bytes(image: Image.Image, image_format: str) -> bytes:
    """将 Pillow 图像编码为内存字节。"""

    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format=image_format, quality=88)
    return buffer.getvalue()


def _source_bytes(source: StreamDockN4ProKeyImageSource) -> tuple[bytes, str, str]:
    """读取一个按键图源并返回上传字节、文件名和 MIME。"""

    if isinstance(source, Image.Image):
        return _image_bytes(source, "PNG"), "key.png", "image/png"
    path = Path(source)
    return path.read_bytes(), path.name, "image/png"
