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


class VTSClient:
    """VTube Studio WebSocket API 客户端"""

    API_NAME = "VTubeStudioPublicAPI"
    API_VERSION = "1.0"

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
        if websockets is None:
            raise RuntimeError("请先安装 websockets 库：pip install websockets")

        async with self._lock:
            # 如果连接断开则重新建立
            if self._ws is None or self._ws.closed:
                await self._connect()

            payload = self._build_request(message_type, data)
            await self._ws.send(payload)
            response_raw = await asyncio.wait_for(self._ws.recv(), timeout=10)
            return json.loads(response_raw)

    async def _connect(self):
        """建立 WebSocket 连接"""
        logger.info(f"正在连接 VTube Studio: {self.url}")
        self._ws = await websockets.connect(self.url)
        logger.info("VTube Studio 连接成功")

    async def disconnect(self):
        """断开连接"""
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        logger.info("已断开与 VTube Studio 的连接")

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
        raise RuntimeError(f"获取 Token 失败: {resp}")

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
