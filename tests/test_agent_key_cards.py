"""验证任务卡片包含任务身份、状态和空槽位，测试不访问 HID。"""
from agent_deck.rendering.layout import KeyPlan, LayoutPlan, TouchscreenPlan
from agent_deck.core.state import AgentStatus


def test_remote_cards_show_distinct_tasks_and_empty_slots():
    """两个同状态任务必须显示不同标题，空 Agent 槽位也必须有明确图像。"""
    from agent_deck.server.app import _key_images_from_layout
    layout = LayoutPlan(mode='overview', led_color='blue', keys=(
        KeyPlan(index=0, kind='agent', label='MiraBox 配置', agent_key='a', status=AgentStatus.IDLE),
        KeyPlan(index=1, kind='agent', label='数学作业登记', agent_key='b', status=AgentStatus.IDLE),
        KeyPlan(index=2, kind='agent'),
    ), touchscreen=TouchscreenPlan(title='test'))
    images = _key_images_from_layout(layout, agent_cards=True)
    assert set(images) == {1, 2, 3}
    assert images[1].size == (112, 112)
    assert images[1].tobytes() != images[2].tobytes()
    assert images[3].tobytes() != images[1].tobytes()


def test_disabled_keys_are_blank_without_error_badge():
    """关闭的键必须真正清空，不能用仍带 ERROR 徽标的错误占位图冒充空键。"""
    from agent_deck.server.app import _key_images_from_layout
    layout = LayoutPlan(mode='overview', led_color='blue', keys=(KeyPlan(index=5, kind='disabled'),),
                        touchscreen=TouchscreenPlan(title='test'))
    image = _key_images_from_layout(layout)[6]
    assert len(image.getcolors(112 * 112)) == 1
