"""
AstrBot 插件：VTube Studio Live2D 控制
通过 LLM 工具函数让 AI 能够控制 VTube Studio 中的 Live2D 模型
"""

import json
import platform
from typing import Optional

from astrbot.api.star import Star, Context, register
from astrbot.api import llm_tool, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import logger

from .vts_client import (
    VTSClient,
    VTSClientError,
    VTSConnectionError,
    VTSTimeoutError,
    VTSResponseError,
)
from .vts_discovery import auto_discover, get_install_info

# 默认配置
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 8001
# KV 存储中的 Token 键名
KV_KEY_TOKEN = "vts_auth_token"


@register(
    "astrbot_plugin_vtube_studio",
    "EterUltimate",
    "vtube_studio连接支持",
    "1.2.0",
    "https://github.com/EterUltimate/astrbot_plugin_vtube_studio",
)
class VTubeStudioPlugin(Star):
    """VTube Studio Live2D 控制插件"""

    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config or {}

        # 从 Web 界面配置读取
        self._auto_discover: bool = self.config.get("auto_discover", True)
        self._manual_host: Optional[str] = self.config.get("vts_host") or None
        
        # 安全的端口转换（容错处理）
        port_val = self.config.get("vts_port")
        self._manual_port: Optional[int] = self._safe_parse_port(port_val)
        
        self._auto_connect: bool = self.config.get("auto_connect", True)
        self._debug_mode: bool = self.config.get("debug_mode", False)

        # 初始化 VTS 客户端
        self.vts = VTSClient(
            host=self._manual_host or DEFAULT_HOST,
            port=self._manual_port or DEFAULT_PORT,
            plugin_name="AstrBot VTS Plugin",
            plugin_developer="EterUltimate",
        )
        self._connected = False

    def _safe_parse_port(self, port_val) -> Optional[int]:
        """
        安全解析端口值，防止非数字字符串导致异常。
        """
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
            # 使用公开方法重置连接，而非直接操作私有属性
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
            await self.vts.disconnect()
            logger.info("[VTS] 插件已卸载，VTS 连接已关闭")
        except Exception as e:
            logger.warning(f"[VTS] 卸载时断开连接失败: {e}")

    async def _discover(self) -> tuple:
        """
        确定要连接的 host:port。
        - 若用户在配置里指定了，直接用
        - 若 auto_discover 开启，调用自动发现
        - 否则使用默认地址
        """
        if self._manual_host and self._manual_port:
            logger.info(
                f"[VTS] 使用手动配置：{self._manual_host}:{self._manual_port}"
            )
            return self._manual_host, self._manual_port

        if self._auto_discover:
            logger.info(
                f"[VTS] 开启自动发现，开始扫描 VTube Studio "
                f"（当前平台: {platform.system()}）"
            )

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
        except VTSClientError as e:
            logger.warning(f"[VTS] 自动连接失败: {e}")
        except Exception as e:
            logger.warning(f"[VTS] 自动连接失败（VTube Studio 可能未启动）: {e}")

    async def _check_and_reconnect(self) -> bool:
        """
        检查连接状态，必要时尝试重连。
        返回：是否已连接
        """
        if self.vts.is_connected:
            return True
        
        # 尝试使用保存的 Token 重连
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
    #  Token 持久化（使用框架 KV 存储）
    # ------------------------------------------------------------------ #

    async def _load_token(self) -> Optional[str]:
        """从框架 KV 存储加载 Token"""
        return await self.get_kv_data(KV_KEY_TOKEN)

    async def _save_token(self, token: str):
        """保存 Token 到框架 KV 存储"""
        await self.put_kv_data(KV_KEY_TOKEN, token)

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
        except VTSClientError as e:
            yield event.plain_result(f"❌ 认证出错：{e}")
        except Exception as e:
            yield event.plain_result(
                f"❌ 认证出错：{e}\n"
                "请确保 VTube Studio 已启动并开启了 API。\n"
                "可先发送 /vts_discover 重新扫描。"
            )

    @filter.command("vts_discover")
    async def cmd_vts_discover(self, event: AstrMessageEvent):
        """重新扫描并自动发现 VTube Studio 的运行地址"""
        yield event.plain_result(
            f"🔍 正在扫描 VTube Studio（{platform.system()} 平台）..."
        )
        try:
            info = get_install_info()
            host, port = await auto_discover()

            self.vts.url = f"ws://{host}:{port}"
            # 使用公开方法重置连接
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
        except VTSTimeoutError as e:
            yield event.plain_result(f"❌ 请求超时：{e}")
        except Exception as e:
            yield event.plain_result(f"❌ 查询失败：{e}")

    @filter.command("vts_list")
    async def cmd_vts_list(self, event: AstrMessageEvent):
        """列出所有热键和表情"""
        if not await self._check_and_reconnect():
            yield event.plain_result(
                "❌ 未连接到 VTube Studio，请先发送 /vts_auth 进行认证。"
            )
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

    async def _ensure_connection(self) -> str:
        """
        确保连接可用，返回错误消息或空字符串。
        """
        if not await self._check_and_reconnect():
            return "❌ 未连接到 VTube Studio，请先发送 /vts_auth 进行认证。"
        return ""

    @llm_tool(name="vts_trigger_hotkey")
    async def tool_trigger_hotkey(
        self, event: AstrMessageEvent, hotkey_id: str
    ):
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
            return (
                f"✅ 已触发热键「{hotkey_id}」。"
                f"结果：{json.dumps(result, ensure_ascii=False)}"
            )
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
            result = await self.vts.set_expression(
                expression_file, active, fade_time
            )
            action = "激活" if active else "停用"
            return (
                f"✅ 已{action}表情「{expression_file}」。"
                f"结果：{json.dumps(result, ensure_ascii=False)}"
            )
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
            lines = [
                f"当前模型可用参数（共 {len(params)} 个，显示前30个）："
            ]
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
