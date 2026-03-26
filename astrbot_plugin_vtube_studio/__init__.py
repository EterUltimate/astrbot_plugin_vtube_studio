"""
AstrBot VTube Studio Live2D 控制插件

让 AstrBot 的 LLM 能够实时控制 VTube Studio 中的 Live2D 模型，
包括触发动作热键、切换表情、注入参数等。
"""

from .main import VTubeStudioPlugin

__all__ = ["VTubeStudioPlugin"]
