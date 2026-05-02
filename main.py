"""
AstrBot 插件：VTube Studio Live2D 控制
通过 LLM 工具函数让 AI 能够控制 VTube Studio 中的 Live2D 模型
"""

import asyncio
import json
import platform
import re
from typing import Any, Optional

from astrbot.api.star import Star, Context, register
from astrbot.api import llm_tool, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import logger
from astrbot.api.provider import ProviderRequest
from astrbot.core.provider.entities import LLMResponse

from .vts_client import (
    VTSClient,
    VTSClientError,
    VTSConnectionError,
    VTSTimeoutError,
)
from .vts_discovery import auto_discover, get_install_info

# 默认配置
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8001
KV_KEY_TOKEN = "vts_auth_token"
L2D_TAG_PATTERN = re.compile(
    r"<l2d\s*:\s*([^<>]+?)\s*/?>|<l2d>\s*([^<>]+?)\s*</l2d>",
    re.IGNORECASE,
)


@register(
    "astrbot_plugin_vtube_studio",
    "EterUltimate",
    "vtube_studio连接支持",
    "1.2.1",
    "https://github.com/EterUltimate/astrbot_plugin_vtube_studio",
)
class VTubeStudioPlugin(Star):
    """VTube Studio Live2D 控制插件"""

    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}

        self._auto_discover: bool = self.config.get("auto_discover", True)
        self._manual_host: Optional[str] = self.config.get("vts_host") or None

        # 安全解析端口，防止非数字字符串导致 ValueError
        port_val = self.config.get("vts_port")
        self._manual_port: Optional[int] = self._safe_parse_port(port_val)

        self._auto_connect: bool = self.config.get("auto_connect", True)
        self._debug_mode: bool = self.config.get("debug_mode", False)
        self._l2d_tasks: set[asyncio.Task] = set()

        self.vts = VTSClient(
            host=self._manual_host or DEFAULT_HOST,
            port=self._manual_port or DEFAULT_PORT,
            plugin_name="AstrBot VTS Plugin",
            plugin_developer="EterUltimate",
        )
        self._connected = False

    def _safe_parse_port(self, port_val) -> Optional[int]:
        """安全解析端口值，防止非数字字符串导致异常"""
        if port_val is None:
            return None
        try:
            return int(port_val)
        except (ValueError, TypeError):
            logger.warning(f"[VTS] 无效的端口配置值: {port_val}，将使用默认端口")
            return None

    # ------------------------------------------------------------------ #
    #  插件生命周期
    # ------------------------------------------------------------------ #

    async def initialize(self):
        """插件启动时：自动发现 VTS 位置，然后尝试认证连接"""
        try:
            host, port = await self._discover()
            self.vts.url = f"ws://{host}:{port}"
            # 使用公开方法重置连接，不直接操作私有属性
            await self.vts.reset_connection()

            if self._auto_connect:
                await self._try_connect()
            else:
                logger.info("[VTS] auto_connect 关闭，跳过自动连接")
        except Exception as e:
            logger.error(f"[VTS] 初始化失败: {e}")

    async def terminate(self):
        """插件卸载/停用时：断开 VTS 连接，清理资源"""
        try:
            for task in list(self._l2d_tasks):
                task.cancel()
            self._l2d_tasks.clear()
            await self.vts.disconnect()
            logger.info("[VTS] 插件已卸载，VTS 连接已关闭")
        except Exception as e:
            logger.warning(f"[VTS] 卸载时断开连接失败: {e}")

    async def _discover(self) -> tuple:
        """确定要连接的 host:port"""
        if self._manual_host and self._manual_port:
            logger.info(f"[VTS] 使用手动配置：{self._manual_host}:{self._manual_port}")
            return self._manual_host, self._manual_port

        if self._auto_discover:
            logger.info(f"[VTS] 开启自动发现（平台: {platform.system()}）")

        host, port = await auto_discover(host=self._manual_host or DEFAULT_HOST)
        logger.info(f"[VTS] 自动发现结果：{host}:{port}")
        return host, port

    async def _try_connect(self):
        """尝试连接并使用已保存的 Token 认证"""
        try:
            saved_token = await self._load_token()
            if saved_token:
                ok = await self.vts.authenticate(saved_token)
                if ok:
                    self._connected = True
                    logger.info("[VTS] 使用已保存 Token 认证成功")
                    return
            logger.info("[VTS] 未找到有效 Token，请发送 /vts_auth 进行认证")
        except VTSConnectionError as e:
            logger.warning(f"[VTS] 连接失败: {e}")
        except VTSTimeoutError as e:
            logger.warning(f"[VTS] 连接超时: {e}")
        except Exception as e:
            logger.warning(f"[VTS] 自动连接失败（VTube Studio 可能未启动）: {e}")

    async def _check_and_reconnect(self) -> bool:
        """检查连接状态，必要时尝试重连"""
        if self.vts.is_connected:
            return True
        try:
            saved_token = await self._load_token()
            if saved_token:
                ok = await self.vts.authenticate(saved_token)
                if ok:
                    self._connected = True
                    return True
        except Exception:
            pass
        self._connected = False
        return False

    # ------------------------------------------------------------------ #
    #  自主 Live2D 标签机制
    # ------------------------------------------------------------------ #

    def _get_l2d_entries(self) -> list[dict[str, Any]]:
        entries = self.config.get("l2d_hotkeys", [])
        if not isinstance(entries, list):
            return []

        normalized: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict) or not entry.get("enabled", True):
                continue
            tag = str(entry.get("tag", "")).strip()
            hotkey_id = str(entry.get("hotkey_id", "")).strip()
            if not tag or not hotkey_id:
                continue
            try:
                duration = max(0.0, float(entry.get("duration", 0) or 0))
            except (TypeError, ValueError):
                duration = 0.0
            normalized.append(
                {
                    "tag": tag,
                    "hotkey_id": hotkey_id,
                    "description": str(entry.get("description", "")).strip(),
                    "duration": duration,
                    "release_after_duration": bool(
                        entry.get("release_after_duration", True)
                    ),
                }
            )
        return normalized

    def _l2d_entry_map(self) -> dict[str, dict[str, Any]]:
        return {entry["tag"].lower(): entry for entry in self._get_l2d_entries()}

    def _parse_l2d_tags(self, text: str) -> tuple[list[str], str]:
        tags: list[str] = []

        def collect(match: re.Match) -> str:
            raw = (match.group(1) or match.group(2) or "").strip()
            for item in re.split(r"[\s,，、|/]+", raw):
                tag = item.strip()
                if tag:
                    tags.append(tag)
            return ""

        cleaned = L2D_TAG_PATTERN.sub(collect, text)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return tags, cleaned

    def _create_l2d_task(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._l2d_tasks.add(task)
        task.add_done_callback(self._l2d_tasks.discard)

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """在模型回复前注入可选 Live2D 标签说明。"""
        if not self.config.get("autonomous_l2d_enabled", True):
            return

        entries = self._get_l2d_entries()
        if not entries:
            return
        if not await self._check_and_reconnect():
            logger.debug("[VTS] 未连接 Live2D，跳过 L2D 标签提示词注入")
            return

        max_tags = int(self.config.get("l2d_max_tags_per_reply", 1) or 1)
        max_tags = max(1, max_tags)
        lines = [
            "## Live2D 表情控制",
            "你可以通过在回复末尾输出 Live2D 标签来控制当前 Live2D 模型表情。",
            "标签只用于控制表情，不是给用户看的内容。正常回答用户，然后在最后单独输出一行标签。",
            f"格式：<l2d:标签名>。最多选择 {max_tags} 个；多个标签可写成 <l2d:标签1,标签2>。",
            "如果本次回复不适合使用表情，输出 <l2d:none>。",
            "不要解释标签，不要编造未列出的标签。",
            "",
            "可选表情按键：",
        ]
        for entry in entries:
            desc = entry["description"] or "无额外说明"
            duration = entry["duration"]
            duration_text = f"{duration:g} 秒" if duration > 0 else "不自动结束"
            lines.append(
                f"- {entry['tag']}: {desc}；持续时间：{duration_text}；热键ID：{entry['hotkey_id']}"
            )

        req.system_prompt += "\n\n" + "\n".join(lines) + "\n"

    @filter.on_llm_response(priority=2000)
    async def on_llm_response(self, event: AstrMessageEvent, resp: LLMResponse):
        """截获模型输出的 Live2D 标签，移除标签并异步触发表情热键。"""
        if not self.config.get("autonomous_l2d_enabled", True):
            return

        completion_text = getattr(resp, "completion_text", None)
        if not isinstance(completion_text, str) or "<l2d" not in completion_text.lower():
            return

        tags, cleaned = self._parse_l2d_tags(completion_text)
        if cleaned != completion_text:
            resp.completion_text = cleaned

        tags = [tag for tag in tags if tag.lower() not in {"none", "无", "null", "no"}]
        if tags:
            max_tags = int(self.config.get("l2d_max_tags_per_reply", 1) or 1)
            self._create_l2d_task(self._trigger_l2d_tags(tags[: max(1, max_tags)]))

    async def _trigger_l2d_tags(self, tags: list[str]) -> None:
        entries = self._l2d_entry_map()
        if not entries:
            return

        if not await self._check_and_reconnect():
            logger.warning("[VTS] 收到 L2D 标签，但 VTube Studio 未连接，已跳过触发")
            return

        for tag in tags:
            entry = entries.get(tag.lower())
            if not entry:
                logger.warning(f"[VTS] 未配置的 L2D 标签: {tag}")
                continue
            await self._trigger_l2d_entry(entry)

    async def _trigger_l2d_entry(self, entry: dict[str, Any]) -> None:
        hotkey_id = entry["hotkey_id"]
        try:
            await self.vts.trigger_hotkey(hotkey_id)
            logger.info(f"[VTS] L2D 标签 {entry['tag']} 已触发热键 {hotkey_id}")
        except Exception as e:
            logger.warning(f"[VTS] L2D 标签 {entry['tag']} 触发失败: {e}")
            return

        duration = entry["duration"]
        if duration > 0 and entry["release_after_duration"]:
            self._create_l2d_task(self._release_l2d_entry(entry, duration))

    async def _release_l2d_entry(self, entry: dict[str, Any], duration: float) -> None:
        try:
            await asyncio.sleep(duration)
            if not await self._check_and_reconnect():
                return
            await self.vts.trigger_hotkey(entry["hotkey_id"])
            logger.info(
                f"[VTS] L2D 标签 {entry['tag']} 持续 {duration:g} 秒后已再次触发热键"
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"[VTS] L2D 标签 {entry['tag']} 自动结束失败: {e}")

    @filter.command("vts_l2d_list")
    async def cmd_vts_l2d_list(self, event: AstrMessageEvent):
        """列出自主 Live2D 标签配置。"""
        entries = self._get_l2d_entries()
        if not entries:
            yield event.plain_result("当前没有启用的 L2D 标签条目，请先在插件配置中添加。")
            return

        lines = ["当前启用的 L2D 标签："]
        for entry in entries:
            duration = entry["duration"]
            duration_text = f"{duration:g} 秒" if duration > 0 else "不自动结束"
            lines.append(
                f"• <l2d:{entry['tag']}> -> {entry['hotkey_id']} | {duration_text} | "
                f"{entry['description'] or '无说明'}"
            )
        yield event.plain_result("\n".join(lines))

    # ------------------------------------------------------------------ #
    #  Token 持久化（使用框架 KV 存储）
    # ------------------------------------------------------------------ #

    async def _load_token(self) -> Optional[str]:
        """从框架 KV 存储加载 Token"""
        return await self.get_kv_data(KV_KEY_TOKEN)

    async def _save_token(self, token: str):
        """保存 Token 到框架 KV 存储"""
        await self.put_kv_data(KV_KEY_TOKEN, token)

    async def _ensure_connection(self) -> str:
        """确保连接可用，返回错误消息或空字符串"""
        if not await self._check_and_reconnect():
            return "❌ 未连接到 VTube Studio，请先发送 /vts_auth 进行认证。"
        return ""

    # ------------------------------------------------------------------ #
    #  命令
    # ------------------------------------------------------------------ #

    @filter.command("vts_auth")
    async def cmd_vts_auth(self, event: AstrMessageEvent):
        """发送 /vts_auth 触发 VTube Studio 认证流程"""
        yield event.plain_result(
            "正在向 VTube Studio 申请认证 Token，请在 VTS 界面点击【允许】按钮..."
        )
        try:
            token = await self.vts.request_auth_token()
            ok = await self.vts.authenticate(token)
            if ok:
                await self._save_token(token)
                self._connected = True
                yield event.plain_result(
                    "✅ VTube Studio 认证成功！Token 已保存。\n"
                    "现在 LLM 可以控制你的 Live2D 模型了。"
                )
            else:
                yield event.plain_result("❌ 认证失败，请确认已在 VTS 界面点击允许。")
        except VTSConnectionError as e:
            yield event.plain_result(f"❌ 连接失败：{e}")
        except VTSTimeoutError as e:
            yield event.plain_result(f"❌ 连接超时：{e}")
        except Exception as e:
            yield event.plain_result(
                f"❌ 认证出错：{e}\n"
                "请确保 VTube Studio 已启动并开启了 API。\n"
                "可先发送 /vts_discover 重新扫描。"
            )

    @filter.command("vts_discover")
    async def cmd_vts_discover(self, event: AstrMessageEvent):
        """重新扫描并自动发现 VTube Studio 的运行地址"""
        yield event.plain_result(f"🔍 正在扫描 VTube Studio（{platform.system()} 平台）...")
        try:
            info = get_install_info()
            host, port = await auto_discover()

            self.vts.url = f"ws://{host}:{port}"
            await self.vts.reset_connection()

            lines = [
                f"🖥️ 操作系统：{info['os']}",
                f"📂 安装路径：{info['install_path'] or '未找到'}",
                f"⚙️ 配置文件端口：{info['config_port'] or '未读取到'}",
                f"🔄 进程运行中：{'是' if info['process_running'] else '否（需要 psutil）'}",
                "",
                f"✅ 已将连接地址更新为 ws://{host}:{port}",
                "",
                "如需认证请发送 /vts_auth",
            ]
            yield event.plain_result("\n".join(lines))

            saved_token = await self._load_token()
            if saved_token:
                ok = await self.vts.authenticate(saved_token)
                if ok:
                    self._connected = True
                    yield event.plain_result("🔗 已用保存的 Token 重新认证成功！")
        except Exception as e:
            yield event.plain_result(f"❌ 自动发现失败：{e}")

    @filter.command("vts_status")
    async def cmd_vts_status(self, event: AstrMessageEvent):
        """查询 VTube Studio 连接状态和当前模型信息"""
        if not await self._check_and_reconnect():
            yield event.plain_result(
                "❌ 未连接到 VTube Studio。\n"
                "• 发送 /vts_discover 自动扫描\n"
                "• 发送 /vts_auth 进行认证"
            )
            return
        try:
            model_info = await self.vts.get_model_info()
            hotkeys = await self.vts.get_hotkeys()
            expressions = await self.vts.get_expressions()

            hotkey_names = [h.get("name", h.get("hotkeyID", "?")) for h in hotkeys]
            expr_names = [e.get("file", "?") for e in expressions]

            msg = (
                f"✅ VTube Studio 已连接（{self.vts.url}）\n"
                f"🖥️ 平台：{platform.system()}\n"
                f"📦 当前模型：{model_info.get('modelName', '未知')}\n"
                f"🎬 可用热键（{len(hotkeys)} 个）：{', '.join(hotkey_names[:10]) or '无'}\n"
                f"😊 可用表情（{len(expressions)} 个）：{', '.join(expr_names[:10]) or '无'}"
            )
            yield event.plain_result(msg)
        except VTSConnectionError as e:
            self._connected = False
            yield event.plain_result(f"❌ 连接已断开：{e}")
        except Exception as e:
            yield event.plain_result(f"❌ 查询失败：{e}")

    @filter.command("vts_list")
    async def cmd_vts_list(self, event: AstrMessageEvent):
        """列出所有热键和表情"""
        if not await self._check_and_reconnect():
            yield event.plain_result("❌ 未连接到 VTube Studio，请先发送 /vts_auth 进行认证。")
            return
        try:
            hotkeys = await self.vts.get_hotkeys()
            expressions = await self.vts.get_expressions()

            lines = ["🎬 **热键列表**"]
            for h in hotkeys:
                lines.append(
                    f"  • {h.get('name', '?')}  "
                    f"(ID: {h.get('hotkeyID', '?')}，类型: {h.get('type', '?')})"
                )
            lines.append("\n😊 **表情列表**")
            for e in expressions:
                active_mark = "✅" if e.get("active") else "⬜"
                lines.append(f"  {active_mark} {e.get('file', '?')}")

            yield event.plain_result("\n".join(lines))
        except VTSConnectionError as e:
            self._connected = False
            yield event.plain_result(f"❌ 连接已断开：{e}")
        except Exception as e:
            yield event.plain_result(f"❌ 查询失败：{e}")

    # ------------------------------------------------------------------ #
    #  LLM 工具函数
    # ------------------------------------------------------------------ #

    @llm_tool(name="vts_trigger_hotkey")
    async def tool_trigger_hotkey(self, event: AstrMessageEvent, hotkey_id: str):
        """
        触发 VTube Studio 中的热键，可以播放动作动画、切换表情、改变待机动画等。
        使用前建议先用 vts_get_hotkeys 获取可用热键列表。

        Args:
            hotkey_id(string): 热键的名称或唯一ID，例如 "wave" 或 "Smile"
        """
        err = await self._ensure_connection()
        if err:
            return err
        try:
            result = await self.vts.trigger_hotkey(hotkey_id)
            return f"✅ 已触发热键「{hotkey_id}」。结果：{json.dumps(result, ensure_ascii=False)}"
        except VTSConnectionError as e:
            self._connected = False
            return f"❌ 连接已断开：{e}"
        except VTSTimeoutError as e:
            return f"❌ 请求超时：{e}"
        except Exception as e:
            return f"❌ 触发热键失败：{e}"

    @llm_tool(name="vts_get_hotkeys")
    async def tool_get_hotkeys(self, event: AstrMessageEvent):
        """
        获取 VTube Studio 当前模型可用的所有热键列表（包括动作、表情热键等）。
        """
        err = await self._ensure_connection()
        if err:
            return err
        try:
            hotkeys = await self.vts.get_hotkeys()
            if not hotkeys:
                return "当前模型没有可用热键。"
            lines = ["当前模型可用热键："]
            for h in hotkeys:
                lines.append(
                    f"• 名称: {h.get('name','?')}, "
                    f"ID: {h.get('hotkeyID','?')}, "
                    f"类型: {h.get('type','?')}"
                )
            return "\n".join(lines)
        except VTSConnectionError as e:
            self._connected = False
            return f"❌ 连接已断开：{e}"
        except Exception as e:
            return f"❌ 获取热键列表失败：{e}"

    @llm_tool(name="vts_set_expression")
    async def tool_set_expression(
        self,
        event: AstrMessageEvent,
        expression_file: str,
        active: bool = True,
        fade_time: float = 0.25,
    ):
        """
        激活或停用 VTube Studio 中的指定表情。
        使用前建议先用 vts_get_expressions 获取可用表情列表。

        Args:
            expression_file(string): 表情文件名，例如 "happy.exp3.json"
            active(boolean): true 表示激活表情，false 表示停用表情，默认 true
            fade_time(number): 淡入淡出时间（秒），默认 0.25
        """
        err = await self._ensure_connection()
        if err:
            return err
        try:
            result = await self.vts.set_expression(expression_file, active, fade_time)
            action = "激活" if active else "停用"
            return f"✅ 已{action}表情「{expression_file}」。结果：{json.dumps(result, ensure_ascii=False)}"
        except VTSConnectionError as e:
            self._connected = False
            return f"❌ 连接已断开：{e}"
        except VTSTimeoutError as e:
            return f"❌ 请求超时：{e}"
        except Exception as e:
            return f"❌ 设置表情失败：{e}"

    @llm_tool(name="vts_get_expressions")
    async def tool_get_expressions(self, event: AstrMessageEvent):
        """
        获取 VTube Studio 当前模型的所有可用表情列表及其激活状态。
        """
        err = await self._ensure_connection()
        if err:
            return err
        try:
            expressions = await self.vts.get_expressions()
            if not expressions:
                return "当前模型没有可用表情。"
            lines = ["当前模型可用表情："]
            for e in expressions:
                status = "✅ 激活中" if e.get("active") else "⬜ 未激活"
                lines.append(f"• {e.get('file', '?')} [{status}]")
            return "\n".join(lines)
        except VTSConnectionError as e:
            self._connected = False
            return f"❌ 连接已断开：{e}"
        except Exception as e:
            return f"❌ 获取表情列表失败：{e}"

    @llm_tool(name="vts_move_model")
    async def tool_move_model(
        self,
        event: AstrMessageEvent,
        position_x: float = 0.0,
        position_y: float = 0.0,
        rotation: float = 0.0,
        size: float = 0.0,
        duration: float = 0.5,
    ):
        """
        移动、旋转或缩放 VTube Studio 中的 Live2D 模型。

        Args:
            position_x(number): 水平位置，范围 -1.0（最左）到 1.0（最右），0 为居中
            position_y(number): 垂直位置，范围 -1.0（最下）到 1.0（最上），0 为居中
            rotation(number): 旋转角度，范围 -360 到 360 度，0 为不旋转
            size(number): 缩放大小，范围 -100 到 100，0 为不变
            duration(number): 动画持续时间（秒），默认 0.5
        """
        err = await self._ensure_connection()
        if err:
            return err
        try:
            await self.vts.move_model(
                position_x=position_x,
                position_y=position_y,
                rotation=rotation,
                size=size,
                time_in_seconds=duration,
            )
            return (
                f"✅ 已移动模型：位置({position_x:.2f}, {position_y:.2f}), "
                f"旋转{rotation}°, 大小变化{size}。"
            )
        except VTSConnectionError as e:
            self._connected = False
            return f"❌ 连接已断开：{e}"
        except VTSTimeoutError as e:
            return f"❌ 请求超时：{e}"
        except Exception as e:
            return f"❌ 移动模型失败：{e}"

    @llm_tool(name="vts_inject_parameter")
    async def tool_inject_parameter(
        self,
        event: AstrMessageEvent,
        parameter_id: str,
        value: float,
        mode: str = "set",
    ):
        """
        向 VTube Studio 注入 Live2D 参数值，可以精细控制模型的面部表情参数。
        常用参数：FaceAngleX（水平转头）、FaceAngleY（点头）、FaceAngleZ（倾头）、
        MouthOpen（开嘴）、MouthSmile（微笑）、EyeOpenLeft/Right（眼睛睁开程度）。

        Args:
            parameter_id(string): 参数名称，例如 "MouthSmile" 或 "FaceAngleX"
            value(number): 参数值（通常为 -1.0 ~ 1.0）
            mode(string): 控制模式，"set" 表示直接设置，"add" 表示叠加，默认 "set"
        """
        err = await self._ensure_connection()
        if err:
            return err
        try:
            await self.vts.inject_parameters(
                parameters=[{"id": parameter_id, "value": value}],
                mode=mode,
            )
            return f"✅ 已设置参数「{parameter_id}」= {value}（模式: {mode}）"
        except VTSConnectionError as e:
            self._connected = False
            return f"❌ 连接已断开：{e}"
        except VTSTimeoutError as e:
            return f"❌ 请求超时：{e}"
        except Exception as e:
            return f"❌ 注入参数失败：{e}"

    @llm_tool(name="vts_get_parameters")
    async def tool_get_parameters(self, event: AstrMessageEvent):
        """
        获取 VTube Studio 当前模型所有可用的 Live2D 输入参数列表。
        """
        err = await self._ensure_connection()
        if err:
            return err
        try:
            params = await self.vts.get_input_parameters()
            if not params:
                return "没有可用参数。"
            lines = [f"当前模型可用参数（共 {len(params)} 个，显示前30个）："]
            for p in params[:30]:
                lines.append(
                    f"• {p.get('name','?')} "
                    f"范围:[{p.get('min','?')}, {p.get('max','?')}] "
                    f"当前值:{p.get('value','?')}"
                )
            return "\n".join(lines)
        except VTSConnectionError as e:
            self._connected = False
            return f"❌ 连接已断开：{e}"
        except Exception as e:
            return f"❌ 获取参数列表失败：{e}"

    @llm_tool(name="vts_model_info")
    async def tool_model_info(self, event: AstrMessageEvent):
        """
        获取 VTube Studio 当前加载的 Live2D 模型的基本信息。
        """
        err = await self._ensure_connection()
        if err:
            return err
        try:
            info = await self.vts.get_model_info()
            return (
                f"当前模型信息：\n"
                f"• 名称：{info.get('modelName', '未知')}\n"
                f"• 文件：{info.get('modelFileName', '未知')}\n"
                f"• VTS模型ID：{info.get('modelID', '未知')}"
            )
        except VTSConnectionError as e:
            self._connected = False
            return f"❌ 连接已断开：{e}"
        except Exception as e:
            return f"❌ 获取模型信息失败：{e}"
