"""运行真实前端函数，验证预览按硬件布局找到任务且显示标题；不启动浏览器。"""
import shutil
import subprocess
from pathlib import Path
import pytest


def test_agent_preview_uses_hardware_slot_and_displays_title():
    """存储顺序不同于硬件排序时，键面仍必须显示实际按键对应的任务。"""
    node = shutil.which('node')
    if not node:
        pytest.skip('node unavailable')
    source = (Path(__file__).parents[1] / 'src/agent_deck/web/app.js').read_text()
    functions = source[source.index('function runtimeAgents()'):source.index('function appFromBinding(')]
    face = source[source.index('function renderKeyFace('):source.index('function renderKeys(')]
    script = '''
const assert = require('node:assert/strict');
const state = {keys: [{index:0, kind:'agent', slot:1}], status:{
  agents:[{agent_key:'old', display_name:'旧任务', status:'idle'},
          {agent_key:'new', display_name:'MiraBox 配置', status:'running'}],
  layout:{keys:[{index:0, kind:'agent', agent_key:'new'}]}
}};
function escapeHtml(v) {return String(v).replaceAll('<', '&lt;');}
''' + functions + '\n' + face + '''
assert.equal(agentForSlot(1).agent_key, 'new');
const html = renderKeyFace(state.keys[0]);
assert.ok(html.includes('MiraBox 配置'), html);
assert.ok(html.includes('运行中'), html);
state.status.layout.keys[0].agent_key = null;
assert.equal(agentForSlot(1), null);
assert.ok(renderKeyFace(state.keys[0]).includes('等待任务'));
'''
    completed = subprocess.run([node, '-e', script], capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
