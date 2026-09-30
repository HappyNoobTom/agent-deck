#!/usr/bin/env python3
"""macOS StreamDock HID bridge for the Dockerized Agent Deck daemon.

The FastAPI process is intentionally tiny: Agent Deck stays in Docker, while this process is the
only component that loads the official macOS SDK and owns the N4 Pro HID session. It receives the
latest background and key images over localhost HTTP, renders them through the existing persistent
N4 Pro animator, and forwards hardware input events back to the container daemon.
"""

from __future__ import annotations

import base64
import os
import threading
import time
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from PIL import Image
from pydantic import BaseModel, Field

from agent_deck.adapters.codex_quota import read_codex_quota
from agent_deck.actions.focus import focus_agent_target
from agent_deck.actions.local_targets import open_local_url

# The macOS Codex CLI is installed by Homebrew; keep collection on the host so
# Docker never needs access to the host binary or credentials.
os.environ["PATH"] = "/opt/homebrew/bin:" + os.environ.get("PATH", "")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault(
    "AGENT_DECK_STREAMDOCK_SDK_PATH",
    str(PROJECT_ROOT / "vendor" / "streamdock-python-sdk" / "src"),
)

from agent_deck.hardware.streamdock_n4pro import (  # noqa: E402
    StreamDockN4ProAnimationResult,
    StreamDockN4ProPersistentAnimator,
)


class RenderRequest(BaseModel):
    """接收容器提交的最新 N4 Pro 显示快照。"""

    duration_seconds: float = Field(default=3.0, gt=0)
    fps: int = Field(default=1, ge=1, le=30)
    background_jpeg_base64: str | None = None
    key_png_base64: dict[str, str] = Field(default_factory=dict)


class BridgeState:
    """线程安全地保存最新帧、输入回调和硬件诊断结果。"""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.background: Image.Image | None = None
        self.keys: dict[int, Image.Image] = {}
        self.revision = 0
        self.last_result: StreamDockN4ProAnimationResult | None = None
        self.last_error: str | None = None
        self.processed_revision = 0
        self.recent_input_events: list[dict[str, Any]] = []
        self.stop = threading.Event()
        self.condition = threading.Condition(self.lock)
        self.worker: threading.Thread | None = None
        self.animator = StreamDockN4ProPersistentAnimator(input_callback=self.forward_input)
        self.animator.set_background_update_provider(self.current_background)
        self.animator.set_key_image_update_provider(self.current_keys)
        self.codex_quota_lock = threading.Lock()
        self.codex_quota_cached_at = 0.0
        self.codex_quota_cached: dict[str, Any] | None = None

    def update(self, request: RenderRequest) -> None:
        """替换最新背景和按键图快照。"""

        with self.lock:
            if request.background_jpeg_base64:
                self.background = _decode_image(request.background_jpeg_base64).convert("RGB")
            self.keys = {
                int(key): _decode_image(value).convert("RGB")
                for key, value in request.key_png_base64.items()
                if 1 <= int(key) <= 15
            }
            self.revision += 1
            self.condition.notify_all()

    def current_background(self) -> tuple[int, Image.Image | None]:
        """返回当前背景 revision 和图像副本。"""

        with self.lock:
            return self.revision, self.background.copy() if self.background is not None else None

    def current_keys(self) -> tuple[int, dict[int, Image.Image]]:
        """返回当前按键 revision 和图像副本。"""

        with self.lock:
            return self.revision, {key: image.copy() for key, image in self.keys.items()}

    def forward_input(self, _device: object, event: object) -> None:
        """将 SDK 输入事件转换为容器 `/hardware/input` 事件。"""

        payload = _hardware_input_payload(event)
        if payload is None:
            return
        target = os.environ.get("AGENT_DECK_INPUT_URL", "http://127.0.0.1:8766/hardware/input")
        diagnostic = {
            "event": _event_diagnostic(event),
            "payload": payload,
            "forwarded_at": datetime.now(UTC).isoformat(),
        }
        try:
            response = httpx.post(target, json=payload, timeout=1.5)
            diagnostic["http_status"] = response.status_code
            try:
                diagnostic["response"] = response.json()
            except ValueError:
                diagnostic["response"] = response.text[:500]
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001 - hardware callbacks must never kill SDK thread
            diagnostic["error"] = f"{type(exc).__name__}: {exc}"
            with self.lock:
                self.last_error = f"input forward: {type(exc).__name__}: {exc}"
        finally:
            with self.lock:
                self.recent_input_events.append(diagnostic)
                del self.recent_input_events[:-20]

    def run(self) -> None:
        """持续调用已有 N4 Pro animator，保持实体设备会话。"""

        while not self.stop.is_set():
            with self.lock:
                revision = self.revision
                background = self.background.copy() if self.background is not None else None
                has_frame = background is not None or bool(self.keys)
            if not has_frame:
                self.stop.wait(0.2)
                continue
            try:
                result = self.animator(
                    background_image=background,
                    key_frame_paths={},
                    key_images=None,
                    duration_seconds=3.0,
                    fps=1,
                )
                with self.condition:
                    self.last_result = result
                    self.last_error = result.error
                    self.processed_revision = max(self.processed_revision, revision)
                    self.condition.notify_all()
            except Exception as exc:  # noqa: BLE001 - bridge remains available after one failure
                with self.condition:
                    self.last_error = f"render: {type(exc).__name__}: {exc}"
                    self.processed_revision = max(self.processed_revision, revision)
                    self.condition.notify_all()
            self.stop.wait(0.2)

    def wait_for_revision(self, revision: int, timeout: float) -> bool:
        """等待指定快照完成一次真实硬件尝试。"""

        deadline = time.monotonic() + timeout
        with self.condition:
            while self.processed_revision < revision and not self.stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self.condition.wait(remaining)
            return self.processed_revision >= revision

    def close(self) -> None:
        """停止渲染循环并释放 SDK 会话。"""

        self.stop.set()
        with self.condition:
            self.condition.notify_all()
        if self.worker is not None and self.worker is not threading.current_thread():
            self.worker.join(timeout=5)
        self.animator.close()


state = BridgeState()
app = FastAPI(title="Agent Deck StreamDock bridge")


@app.get("/codex/quota")
def codex_quota() -> dict[str, Any]:
    """Read a redacted Codex quota snapshot on macOS for the Docker daemon."""

    now = time.monotonic()
    with state.codex_quota_lock:
        if state.codex_quota_cached is not None and now - state.codex_quota_cached_at < 20.0:
            return {"ok": True, "source": "host-codex-cli", "snapshot": state.codex_quota_cached}
        try:
            snapshot = read_codex_quota(timeout_seconds=15.0)
            payload = snapshot.model_dump(mode="json")
            raw = payload.get("raw")
            if isinstance(raw, dict):
                raw.pop("accountId", None)
            state.codex_quota_cached = payload
            state.codex_quota_cached_at = now
            return {"ok": True, "source": "host-codex-cli", "snapshot": payload}
        except Exception as exc:  # noqa: BLE001 - report collector diagnostics to daemon
            raise HTTPException(503, detail=f"host Codex quota collector failed: {type(exc).__name__}: {exc}") from exc


@app.get("/codex/token-usage")
def codex_token_usage() -> dict[str, Any]:
    """Report the token collector capability without silently fabricating usage."""

    raise HTTPException(503, detail="host token collector unavailable: bunx/ccusage is not installed")


@app.get("/health")
def health() -> dict[str, Any]:
    """返回桥接进程和最近一次硬件结果。"""

    with state.lock:
        result = state.last_result.model_dump() if state.last_result is not None else None
        return {
            "ok": True,
            "revision": state.revision,
            "processed_revision": state.processed_revision,
            "last_result": result,
            "last_error": state.last_error,
            "recent_input_events": state.recent_input_events[-20:],
        }


@app.post("/render")
def render(request: RenderRequest) -> dict[str, Any]:
    """接收一个容器渲染快照，并交给后台持久 renderer。"""

    try:
        state.update(request)
    except Exception as exc:  # noqa: BLE001 - return a useful HTTP error to container
        raise HTTPException(status_code=400, detail=f"invalid image payload: {exc}") from exc
    with state.lock:
        revision = state.revision
        key_count = len(state.keys)
    if not state.wait_for_revision(revision, timeout=10.0):
        raise HTTPException(status_code=504, detail="hardware render timed out")
    with state.lock:
        result = state.last_result
        if result is None or not result.ok:
            raise HTTPException(status_code=503, detail=state.last_error or "hardware render failed")
        return {
            "ok": True,
            "device_type": result.device_type or "remote-n4pro",
            "path": result.path,
            "frames_rendered": result.frames_rendered,
            "key_count": result.key_count or key_count,
            "revision": revision,
        }



class FocusRequest(BaseModel):
    """接收受限的结构化 focus 目标，不接受脚本或 shell 命令。"""
    focus_target: str = Field(min_length=1, max_length=512)


class UrlRequest(BaseModel):
    """接收 URL 目标；宿主执行器只允许 http/https。"""
    url: str | None = Field(default=None, max_length=4096)


@app.post('/actions/focus')
def execute_focus(request: FocusRequest) -> dict[str, Any]:
    """在 Mac 上执行应用激活，返回实际结果，包括仅激活 App 的能力限制。"""
    return focus_agent_target(request.focus_target).model_dump(mode='json')


@app.post('/actions/url')
def execute_url(request: UrlRequest) -> dict[str, Any]:
    """在 Mac 上校验并打开 http/https URL，系统失败原样返回诊断。"""
    return open_local_url(url=request.url).model_dump(mode='json')

def _decode_image(encoded: str) -> Image.Image:
    """解码一个 base64 图片。"""

    return Image.open(BytesIO(base64.b64decode(encoded, validate=True)))


def _hardware_input_payload(event: object) -> dict[str, Any] | None:
    """将官方 SDK InputEvent 映射为 Agent Deck 的 HardwareInput JSON。"""

    event_type = _enum_value(getattr(event, "event_type", None))
    now = datetime.now(UTC).isoformat()
    if event_type == "button":
        key = getattr(event, "key", None)
        logical_key = int(getattr(key, "value", key or 0))
        # Agent Deck's /hardware/input endpoint consumes a zero-based layout index.
        # The SDK uses logical keys 1..10 for the ten main keys and 11..14 for
        # the four N4 Pro secondary touch-bar keys.  The latter intentionally
        # mirror layout slots 0..3, so both physical surfaces activate the same
        # configured action instead of being dropped as out-of-range indexes.
        if 1 <= logical_key <= 10:
            index = logical_key - 1
        elif 11 <= logical_key <= 14:
            index = logical_key - 11
        else:
            return None
        return {
            "kind": "key",
            "index": index,
            "value": {"state": int(getattr(event, "state", 0)), "sdk_key": logical_key},
            "occurred_at": now,
        }
    if event_type == "knob_rotate":
        knob = _enum_value(getattr(event, "knob_id", None)) or "knob_1"
        index = int(knob.rsplit("_", 1)[-1])
        return {"kind": "knob", "index": index, "value": {"action": "rotate", "direction": _enum_value(getattr(event, "direction", None))}, "occurred_at": now}
    if event_type == "knob_press":
        knob = _enum_value(getattr(event, "knob_id", None)) or "knob_1"
        index = int(knob.rsplit("_", 1)[-1])
        return {"kind": "knob", "index": index, "value": {"action": "press", "state": int(getattr(event, "state", 0))}, "occurred_at": now}
    if event_type == "swipe":
        return {"kind": "swipe", "index": 0, "value": {"direction": _enum_value(getattr(event, "direction", None))}, "occurred_at": now}
    if event_type == "touch_point":
        return {"kind": "touch", "index": 0, "value": {"x": getattr(event, "x", None), "y": getattr(event, "y", None)}, "occurred_at": now}
    return None


def _event_diagnostic(event: object) -> dict[str, Any]:
    """Return JSON-safe SDK event fields for physical-key diagnosis."""

    key = getattr(event, "key", None)
    knob = getattr(event, "knob_id", None)
    return {
        "event_type": _enum_value(getattr(event, "event_type", None)),
        "key_value": getattr(key, "value", key),
        "state": getattr(event, "state", None),
        "knob_id": _enum_value(knob),
        "direction": _enum_value(getattr(event, "direction", None)),
        "x": getattr(event, "x", None),
        "y": getattr(event, "y", None),
    }


def _enum_value(value: object) -> str | None:
    """读取 enum-like 对象的稳定字符串值。"""

    if isinstance(value, str):
        return value
    candidate = getattr(value, "value", None)
    return candidate if isinstance(candidate, str) else None


def main() -> None:
    """启动 macOS 本地桥接 HTTP 服务。"""

    thread = threading.Thread(target=state.run, name="streamdock-renderer", daemon=True)
    state.worker = thread
    thread.start()
    host = os.environ.get("AGENT_DECK_BRIDGE_HOST", "127.0.0.1")
    port = int(os.environ.get("AGENT_DECK_BRIDGE_PORT", "8767"))
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    finally:
        state.close()


if __name__ == "__main__":
    main()
