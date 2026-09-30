"""验证容器动作在 Mac 桥接执行；测试仅使用 HTTP 和动作替身，不访问桌面。"""
import httpx
from fastapi.testclient import TestClient

from agent_deck.actions.focus import FocusActionResult
from agent_deck.actions.local_targets import LocalTargetActionResult


def test_remote_focus_preserves_app_only_result(monkeypatch):
    """远端 focus 必须发送原目标，并保留仅激活应用的真实能力限制。"""
    from agent_deck.actions.remote import remote_focus_action_executor

    def post(url, *, json, timeout):
        """检查结构化请求并模拟 Mac 返回。"""
        assert url == 'http://mac:8767/actions/focus'
        assert json == {'focus_target': 'codex-app:thread-1'}
        return httpx.Response(200, request=httpx.Request('POST', url), json={
            'ok': True, 'status': 'app_activated_only', 'focus_target': json['focus_target'],
            'message': 'activated Codex; thread focus unsupported',
        })

    monkeypatch.setattr(httpx, 'post', post)
    result = remote_focus_action_executor('http://mac:8767')('codex-app:thread-1')
    assert result.ok and result.status == 'app_activated_only'


def test_remote_focus_network_error_is_failed(monkeypatch):
    """Mac 桥接离线必须返回失败，不得伪报成功。"""
    from agent_deck.actions.remote import remote_focus_action_executor

    def post(*args, **kwargs):
        """模拟宿主机连接失败。"""
        raise httpx.ConnectError('offline')

    monkeypatch.setattr(httpx, 'post', post)
    result = remote_focus_action_executor('http://mac:8767')('app:Codex')
    assert not result.ok and result.status == 'failed'


def test_bridge_executes_focus_and_url_on_host(monkeypatch):
    """桥接端路由必须调用宿主执行器，而不是把动作返回 Linux。"""
    from test_streamdock_bridge import _BRIDGE
    calls = []

    def focus(target):
        """记录 Mac focus 调用。"""
        calls.append(target)
        return FocusActionResult(ok=True, status='app_activated_only', focus_target=target, message='activated')

    def url(*, url):
        """记录 Mac URL 调用。"""
        calls.append(url)
        return LocalTargetActionResult(ok=True, status='succeeded', target_type='url', url=url, message='opened')

    monkeypatch.setattr(_BRIDGE, 'focus_agent_target', focus)
    monkeypatch.setattr(_BRIDGE, 'open_local_url', url)
    client = TestClient(_BRIDGE.app)
    response = client.post('/actions/focus', json={'focus_target': 'codex-app:thread-1'})
    assert response.status_code == 200 and response.json()['status'] == 'app_activated_only'
    response = client.post('/actions/url', json={'url': 'http://127.0.0.1:8766'})
    assert response.status_code == 200 and response.json()['ok']
    assert calls == ['codex-app:thread-1', 'http://127.0.0.1:8766']


def test_daemon_selects_remote_focus_when_bridge_configured(monkeypatch):
    """Docker 的默认 focus 执行器必须自动选择 Mac 桥接。"""
    from agent_deck.server.app import create_app
    monkeypatch.setenv('AGENT_DECK_CODEX_BRIDGE_URL', 'http://mac:8767')
    runtime = create_app().state.runtime
    assert runtime.focus_action_executor.__module__ == 'agent_deck.actions.remote'


def test_quota_key_opens_details_even_without_multiple_windows():
    """只有一个额度窗口时，按键也应切到额度详情，不能成功返回却没有可见变化。"""
    from datetime import datetime, UTC
    from agent_deck.server.app import create_app
    client = TestClient(create_app())
    keys = [{'index': i, 'kind': 'disabled'} for i in range(10)]
    keys[2] = {'index': 2, 'kind': 'quota_status', 'quota_window': 'auto'}
    assert client.put('/ui/key-layout', json={'keys': keys}).status_code == 200
    response = client.post('/hardware/input', json={'kind': 'key', 'index': 2, 'value': {'state': 1},
                                                  'occurred_at': datetime.now(UTC).isoformat()})
    assert response.status_code == 200
    assert client.get('/status').json()['logical_panel']['selection']['active_kind'] == 'quota'
