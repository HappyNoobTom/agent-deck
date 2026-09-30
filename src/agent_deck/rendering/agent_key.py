"""任务身份卡片：用任务标题、键号和状态区分 Codex 会话，不访问硬件或执行动作。"""
from __future__ import annotations

from functools import lru_cache
from PIL import Image, ImageDraw, ImageFont
from agent_deck.rendering.appearance import DeckAppearanceSettings, resolve_render_palette

_STATUS = {
    'idle': ('空闲', '#6fd5ff'), 'running': ('运行中', '#6fd5ff'),
    'thinking': ('思考中', '#6fd5ff'), 'tool_running': ('执行中', '#6fd5ff'),
    'waiting_user': ('待输入', '#ffc857'), 'approval_needed': ('待审批', '#ffc857'),
    'error': ('出错', '#ff7070'), 'completed_recently': ('已完成', '#83e7b4'),
}


@lru_cache(maxsize=16)
def _font(size: int) -> ImageFont.ImageFont:
    """读取 Mac 或 Linux 中文字体；缺失时回退 Pillow 默认字体，无硬件副作用。"""
    for path in (
        '/System/Library/Fonts/PingFang.ttc',
        '/System/Library/Fonts/STHeiti Light.ttc',
        '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    ):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


@lru_cache(maxsize=128)
def render_agent_key_image(index: int, title: str, status: str | None, *,
                           appearance: DeckAppearanceSettings | None = None) -> Image.Image:
    """返回缓存的 112x112 任务卡片，调用方只读使用；空任务明确标为未分配。

    index 是 0-based 键号；title/status 为当前 LayoutPlan 数据；缓存避免重复下发相同图。
    """
    palette = resolve_render_palette(appearance, default_background=(11, 15, 22))
    image = Image.new('RGB', (224, 224), palette.background)
    draw = ImageDraw.Draw(image)
    label, accent = _STATUS.get(status or '', ('未分配' if not title else status or '任务', '#7e8d9d'))
    draw.rounded_rectangle((8, 8, 216, 216), radius=16, fill=palette.surface)
    draw.text((20, 17), f'{index + 1:02d} · TASK', font=_font(21), fill=accent)
    text = ' '.join(title.split()) or '等待任务'
    # 按实际像素宽度分两行，避免英文和中文混排时超出键面。
    remaining = text
    for row in range(2):
        line = ''
        while remaining and draw.textlength(line + remaining[0], font=_font(26)) < 188:
            line += remaining[0]
            remaining = remaining[1:]
        if row == 1 and remaining:
            line = line[:-1] + '…'
        draw.text((18, 62 + row * 38), line, font=_font(26), fill=palette.foreground)
    draw.ellipse((19, 171, 33, 185), fill=accent)
    draw.text((44, 161), label, font=_font(24), fill=accent)
    return image.resize((112, 112), Image.Resampling.LANCZOS)
