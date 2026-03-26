"""
VTube Studio WebSocket API 客户端
负责与 VTube Studio 建立连接、认证，并提供控制 Live2D 模型的方法
"""

import asyncio
import json
import uuid
from typing import Optional, Dict, Any, List

try:
    import websockets
    from websockets.exceptions import ConnectionClosed, WebSocketException
except ImportError:
    websockets = None

from astrbot.api import logger


class VTSClientError(Exception):
    """VTS 客户端异常基类"""
    pass


class VTSConnectionError(VTSClientError):
    """连接异常"""
    pass


class VTSTimeoutError(VTSClientError):
    """超时异常"""
    pass


class VTSResponseError(VTSClientError):
    """响应解析异常"""
    pass


class VTSClient:
    """VTube Studio WebSocket API 客户端"""

    API_NAME = "VTubeStudioPublicAPI"
    API_VERSION = "1.0"
    DEFAULT_TIMEOUT = 10.0
    CONNECT_TIMEOUT = 5.0

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8001,
        plugin_name: str = "AstrBot VTS Plugin",
        plugin_developer: str = "AstrBot",
    ):
        self.url = f"ws://{host}:{port}"
        self.plugin_name = plugin_name
        self.plugin_developer = plugin_developer
        self.auth_token: Optional[str] = None
        self._ws = None
        self._lock = asyncio.Lock()
        self._is_connected = False

    # ------------------------------------------------------------------ #
    #  底层通信
    # ------------------------------------------------------------------ #

    def _build_request(self, message_type: str, data: Dict[str, Any] = None) -> str:
        payload = {
            "apiName": self.API_NAME,
            "apiVersion": self.API_VERSION,
            "requestID": str(uuid.uuid4())[:8],
            "messageType": message_type,
            "data": data or {},
        }
        return json.dumps(payload)

    async def _send_request(
        self, message_type: str, data: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        发送请求并等待响应。
        
        异常处理：
        - 超时时强制断开连接防止状态污染
        - JSON 解析失败时抛出明确异常
        - 连接失败时自动重试一次
        """
        if websockets is None:
            raise VTSClientError("请先安装 websockets 库：pip install websockets")

        async with self._lock:
            # 如果连接断开则重新建立
            if self._ws is None or self._ws.closed:
                await self._connect()

            payload = self._build_request(message_type, data)
            
            try:
                await self._ws.send(payload)
            except Exception as e:
                # 发送失败，尝试重连一次
                logger.warning(f"[VTS] 发送失败，尝试重连: {e}")
                await self._force_disconnect()
                await self._connect()
                await self._ws.send(payload)
            
            try:
                response_raw = await asyncio.wait_for(
                    self._ws.recv(), timeout=self.DEFAULT_TIMEOUT
                )
            except asyncio.TimeoutError:
                # 超时时强制断开连接，防止脏数据污染
                logger.warning("[VTS] 请求超时，强制断开连接以防止状态污染")
                await self._force_disconnect()
                raise VTSTimeoutError(
                    f"VTube Studio API 请求超时（{self.DEFAULT_TIMEOUT}秒），"
                    "连接已重置，请检查 VTS 是否响应正常"
                )
            
            # 安全解析 JSON
            try:
                return json.loads(response_raw)
            except json.JSONDecodeError as e:
                logger.error(f"[VTS] 响应 JSON 解析失败: {e}, 原始响应: {response_raw[:200]}")
                await self._force_disconnect()
                raise VTSResponseError(
                    f"VTube Studio 返回了无效的响应格式: {e}"
                )

    async def _connect(self):
        """
        建立 WebSocket 连接。
        
        异常处理：
        - 连接超时
        - 连接拒绝
        - 其他网络异常
        """
        if websockets is None:
            raise VTSClientError("websockets 库未安装")
        
        logger.info(f"正在连接 VTube Studio: {self.url}")
        
        try:
            self._ws = await asyncio.wait_for(
                websockets.connect(self.url),
                timeout=self.CONNECT_TIMEOUT
            )
            self._is_connected = True
            logger.info("VTube Studio 连接成功")
        except asyncio.TimeoutError:
            self._is_connected = False
            raise VTSConnectionError(
                f"连接 VTube Studio 超时（{self.CONNECT_TIMEOUT}秒），"
                "请确认 VTS 已启动并开启了 API"
            )
        except ConnectionRefusedError:
            self._is_connected = False
            raise VTSConnectionError(
                f"连接被拒绝，请确认 VTube Studio 已启动并开启了 WebSocket API "
                f"（地址: {self.url}）"
            )
        except Exception as e:
            self._is_connected = False
            raise VTSConnectionError(f"连接 VTube Studio 失败: {e}")

    async def _force_disconnect(self):
        """强制断开连接（用于超时后清理）"""
        self._is_connected = False
        if self._ws and not self._ws.closed:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None

    async def disconnect(self):
        """正常断开连接"""
        await self._force_disconnect()
        logger.info("已断开与 VTube Studio 的连接")

    async def reset_connection(self):
        """重置连接状态（供外部调用的公开方法）"""
        await self._force_disconnect()
        logger.info("[VTS] 连接已重置")

    @property
    def is_connected(self) -> bool:
        """检查连接状态"""
        return self._is_connected and self._ws is not None and not self._ws.closed

    # ------------------------------------------------------------------ #
    #  认证
    # ------------------------------------------------------------------ #

    async def request_auth_token(self) -> str:
        """
        向 VTube Studio 申请认证 Token（用户需要在 VTS 界面点击允许）
        返回 token 字符串
        """
        resp = await self._send_request(
            "AuthenticationTokenRequest",
            {
                "pluginName": self.plugin_name,
                "pluginDeveloper": self.plugin_developer,
            },
        )
        if resp.get("data", {}).get("authenticationToken"):
            token = resp["data"]["authenticationToken"]
            self.auth_token = token
            logger.info("成功获取 VTS 认证 Token")
            return token
        raise VTSClientError(f"获取 Token 失败: {resp}")

    async def authenticate(self, token: str) -> bool:
        """使用已有 Token 进行认证，成功返回 True"""
        self.auth_token = token
        resp = await self._send_request(
            "AuthenticationRequest",
            {
                "pluginName": self.plugin_name,
                "pluginDeveloper": self.plugin_developer,
                "authenticationToken": token,
            },
        )
        authenticated = resp.get("data", {}).get("authenticated", False)
        if authenticated:
            logger.info("VTS 认证成功")
        else:
            logger.warning(f"VTS 认证失败: {resp}")
        return authenticated

    async def ensure_authenticated(self, token: Optional[str] = None) -> bool:
        """
        确保已认证。
        若提供 token 则直接使用；否则申请新 token（需用户在 VTS 界面授权）。
        """
        t = token or self.auth_token
        if t:
            return await self.authenticate(t)
        new_token = await self.request_auth_token()
        return bool(new_token)

    # ------------------------------------------------------------------ #
    #  查询接口
    # ------------------------------------------------------------------ #

    async def get_hotkeys(self) -> List[Dict[str, Any]]:
        """获取当前模型可用的热键列表"""
        resp = await self._send_request("HotkeysInCurrentModelRequest", {})
        return resp.get("data", {}).get("availableHotkeys", [])

    async def get_expressions(self) -> List[Dict[str, Any]]:
        """获取当前模型可用的表情列表"""
        resp = await self._send_request("ExpressionStateRequest", {"details": True})
        return resp.get("data", {}).get("expressions", [])

    async def get_input_parameters(self) -> List[Dict[str, Any]]:
        """获取所有可用的输入参数（含默认参数和自定义参数）"""
        resp = await self._send_request("InputParameterListRequest", {})
        return resp.get("data", {}).get("parameters", [])

    async def get_model_info(self) -> Dict[str, Any]:
        """获取当前加载的模型信息"""
        resp = await self._send_request("CurrentModelRequest", {})
        return resp.get("data", {})

    # ------------------------------------------------------------------ #
    #  控制接口
    # ------------------------------------------------------------------ #

    async def trigger_hotkey(self, hotkey_id: str) -> Dict[str, Any]:
        """
        触发指定热键（可触发动作、表情切换、待机动画等）
        hotkey_id: 热键的名称或唯一 ID
        """
        resp = await self._send_request(
            "HotkeyTriggerRequest", {"hotkeyID": hotkey_id}
        )
        logger.info(f"触发热键: {hotkey_id} -> {resp.get('data', {})}")
        return resp.get("data", {})

    async def set_expression(
        self, expression_file: str, active: bool = True, fade_time: float = 0.25
    ) -> Dict[str, Any]:
        """
        激活或停用指定表情
        expression_file: 表情文件名，如 "myExpression_1.exp3.json"
        active: True=激活, False=停用
        fade_time: 淡入/淡出时间（秒）
        """
        resp = await self._send_request(
            "ExpressionActivationRequest",
            {
                "expressionFile": expression_file,
                "active": active,
                "fadeTime": fade_time,
            },
        )
        logger.info(f"设置表情 {expression_file} active={active} -> {resp.get('data', {})}")
        return resp.get("data", {})

    async def inject_parameters(
        self,
        parameters: List[Dict[str, Any]],
        mode: str = "set",
        face_found: bool = True,
    ) -> Dict[str, Any]:
        """
        注入 Live2D 参数值
        parameters: [{"id": "参数名", "value": 0.5, "weight": 1.0}, ...]
        mode: "set" 覆盖 | "add" 叠加
        """
        resp = await self._send_request(
            "InjectParameterDataRequest",
            {
                "faceFound": face_found,
                "mode": mode,
                "parameterValues": parameters,
            },
        )
        return resp.get("data", {})

    async def move_model(
        self,
        position_x: float = 0.0,
        position_y: float = 0.0,
        rotation: float = 0.0,
        size: float = 0.0,
        time_in_seconds: float = 0.5,
    ) -> Dict[str, Any]:
        """
        移动/旋转/缩放模型
        position_x/y: -1.0 ~ 1.0
        rotation: -360 ~ 360 度
        size: -100 ~ 100 (相对缩放)
        time_in_seconds: 动画时长
        """
        resp = await self._send_request(
            "MoveModelRequest",
            {
                "timeInSeconds": time_in_seconds,
                "valuesAreRelativeToModel": False,
                "positionX": position_x,
                "positionY": position_y,
                "rotation": rotation,
                "size": size,
            },
        )
        logger.info(f"移动模型 pos=({position_x},{position_y}) rot={rotation} size={size}")
        return resp.get("data", {})

    async def create_custom_parameter(
        self,
        param_name: str,
        explanation: str = "",
        min_val: float = 0.0,
        max_val: float = 1.0,
        default_val: float = 0.0,
    ) -> Dict[str, Any]:
        """创建自定义 Live2D 参数"""
        resp = await self._send_request(
            "ParameterCreationRequest",
            {
                "parameterName": param_name,
                "explanation": explanation,
                "min": min_val,
                "max": max_val,
                "defaultValue": default_val,
            },
        )
        return resp.get("data", {})

    async def get_api_state(self) -> Dict[str, Any]:
        """获取 VTS API 状态"""
        resp = await self._send_request("APIStateRequest", {})
        return resp.get("data", {})
