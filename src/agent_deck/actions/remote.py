"""容器动作执行适配器：向 Mac 桥接提交结构化目标，保留真实结果和失败诊断。

不运行 shell、不访问 HID；调用方仍通过 runtime 的动作开关控制执行。
"""
from __future__ import annotations

from collections.abc import Callable
import httpx

from agent_deck.actions.focus import FocusActionResult
from agent_deck.actions.local_targets import LocalTargetActionResult


def remote_focus_action_executor(base_url: str) -> Callable[[str], FocusActionResult]:
    """构造 Mac focus 执行器；调用时发送目标，网络或协议异常转为 failed。"""
    def execute(focus_target: str) -> FocusActionResult:
        """转发 focus 目标并保留 app_activated_only 等宿主结果；不伪报线程切换成功。"""
        try:
            response = httpx.post(base_url.rstrip('/') + '/actions/focus',
                                  json={'focus_target': focus_target}, timeout=10.0)
            response.raise_for_status()
            return FocusActionResult.model_validate(response.json())
        except Exception as exc:
            return FocusActionResult(ok=False, status='failed', focus_target=focus_target,
                                     message=f'Mac focus bridge failed: {exc}')
    return execute


def remote_url_action_executor(base_url: str) -> Callable[..., LocalTargetActionResult]:
    """构造 Mac URL 执行器；调用时由宿主校验并打开 http/https，失败返回诊断。"""
    def execute(*, url: str | None = None) -> LocalTargetActionResult:
        """转发 URL；网络和协议异常转为 failed，不在 Linux 中执行 open。"""
        try:
            response = httpx.post(base_url.rstrip('/') + '/actions/url', json={'url': url}, timeout=10.0)
            response.raise_for_status()
            return LocalTargetActionResult.model_validate(response.json())
        except Exception as exc:
            return LocalTargetActionResult(ok=False, status='failed', target_type='url', url=url,
                                           message=f'Mac URL bridge failed: {exc}')
    return execute
