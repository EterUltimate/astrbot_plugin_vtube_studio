"""
VTube Studio 跨平台自动发现模块

策略（按优先级）：
1. 扫描 VTS 默认 WebSocket 端口（8001）是否可连接
2. 扫描常见备用端口（8001-8010）
3. 检测 VTube Studio 进程是否在运行（psutil 可选依赖）
4. 读取 VTS 配置文件中记录的端口（各平台路径不同）
5. 扫描 Steam 各平台默认安装路径，确认 exe/app 存在

返回：(host, port) 或 None
"""

import asyncio
import json
import os
import platform
from pathlib import Path
from typing import List, Optional, Tuple

from astrbot.api import logger

# VTube Studio 默认/备用端口范围
VTS_DEFAULT_PORT = 8001
VTS_SCAN_PORTS = list(range(8001, 8011))  # 8001~8010

# 各平台 VTube Studio 进程名
VTS_PROCESS_NAMES = {
    "Windows": ["VTube Studio.exe", "VTubeStudio.exe"],
    "Darwin": ["VTube Studio", "VTubeStudio"],
    "Linux": ["VTube Studio", "VTubeStudio", "vtube-studio"],
}

# 各平台 Steam 默认安装路径
STEAM_PATHS = {
    "Windows": [
        r"C:\Program Files (x86)\Steam\steamapps\common\VTube Studio",
        r"C:\Program Files\Steam\steamapps\common\VTube Studio",
        r"D:\Steam\steamapps\common\VTube Studio",
        r"D:\SteamLibrary\steamapps\common\VTube Studio",
        r"E:\Steam\steamapps\common\VTube Studio",
        r"E:\SteamLibrary\steamapps\common\VTube Studio",
    ],
    "Darwin": [
        os.path.expanduser("~/Library/Application Support/Steam/steamapps/common/VTube Studio"),
        "/Applications/VTube Studio.app",
    ],
    "Linux": [
        os.path.expanduser("~/.steam/steam/steamapps/common/VTube Studio"),
        os.path.expanduser("~/.local/share/Steam/steamapps/common/VTube Studio"),
        "/opt/steam/steamapps/common/VTube Studio",
    ],
}

# VTS exe/app 相对路径（在安装目录内）
VTS_EXE_RELATIVE = {
    "Windows": ["VTube Studio.exe"],
    "Darwin": ["VTube Studio.app", "Contents/MacOS/VTube Studio"],
    "Linux": ["VTube Studio.x86_64", "VTube Studio"],
}

# VTS 配置文件路径（记录了用户设置的 API 端口）
VTS_CONFIG_PATHS = {
    "Windows": [
        os.path.expanduser(r"~\AppData\Roaming\VTube Studio\settings.json"),
        os.path.expanduser(r"~\AppData\LocalLow\Denchi\VTube Studio\settings.json"),
    ],
    "Darwin": [
        os.path.expanduser("~/Library/Application Support/VTube Studio/settings.json"),
        os.path.expanduser("~/Library/Preferences/com.denchi.vtube-studio/settings.json"),
    ],
    "Linux": [
        os.path.expanduser("~/.config/VTube Studio/settings.json"),
        os.path.expanduser("~/.local/share/VTube Studio/settings.json"),
    ],
}


# ------------------------------------------------------------------ #
#  工具函数
# ------------------------------------------------------------------ #

def _get_os() -> str:
    """返回 'Windows' / 'Darwin' / 'Linux'"""
    return platform.system()


async def _async_port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    """异步检测端口是否可连接"""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


async def _is_vts_websocket(host: str, port: int) -> bool:
    """
    检测该端口是否是 VTube Studio WebSocket API。
    发送 APIStateRequest，若响应包含 VTubeStudioPublicAPI 则确认。
    """
    try:
        import websockets as ws_lib

        url = f"ws://{host}:{port}"
        async with ws_lib.connect(url, open_timeout=2, close_timeout=1) as ws:
            payload = json.dumps({
                "apiName": "VTubeStudioPublicAPI",
                "apiVersion": "1.0",
                "requestID": "discovery",
                "messageType": "APIStateRequest",
                "data": {},
            })
            await ws.send(payload)
            resp_raw = await asyncio.wait_for(ws.recv(), timeout=3)
            resp = json.loads(resp_raw)
            return resp.get("apiName") == "VTubeStudioPublicAPI"
    except Exception:
        return False


# ------------------------------------------------------------------ #
#  Step 1: 端口扫描
# ------------------------------------------------------------------ #

async def scan_ports(host: str = "localhost") -> Optional[int]:
    """扫描 VTS 常用端口范围，返回第一个响应 VTS API 的端口"""
    logger.info(f"[发现] 扫描端口 {VTS_SCAN_PORTS[0]}~{VTS_SCAN_PORTS[-1]} on {host} ...")

    # 先并发检测哪些端口 TCP 可达
    open_ports = []
    tasks = [_async_port_open(host, p) for p in VTS_SCAN_PORTS]
    results = await asyncio.gather(*tasks)
    for port, ok in zip(VTS_SCAN_PORTS, results):
        if ok:
            open_ports.append(port)

    if not open_ports:
        logger.debug("[发现] 端口扫描：所有端口均不可达")
        return None

    logger.debug(f"[发现] TCP 可达端口：{open_ports}")

    # 对可达端口验证是否为 VTS API
    for port in open_ports:
        if await _is_vts_websocket(host, port):
            logger.info(f"[发现] 确认 VTube Studio API 在端口 {port}")
            return port

    # 端口可达但 WebSocket 未响应（VTS 可能刚启动），退而返回第一个开放端口
    logger.info(f"[发现] 端口 {open_ports[0]} 可达但未确认为 VTS API（可能刚启动），暂用")
    return open_ports[0]


# ------------------------------------------------------------------ #
#  Step 2: 读取 VTS 配置文件中的端口
# ------------------------------------------------------------------ #

def read_port_from_config() -> Optional[int]:
    """从 VTS 配置文件中读取用户设置的 API 端口"""
    os_name = _get_os()
    config_paths = VTS_CONFIG_PATHS.get(os_name, [])

    for path_str in config_paths:
        path = Path(path_str)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # VTS 配置中端口字段名可能是 apiServerPort 或 port
                port = (
                    data.get("apiServerPort")
                    or data.get("port")
                    or data.get("websocketPort")
                )
                if port and isinstance(port, int):
                    logger.info(f"[发现] 从配置文件读取端口: {port}（{path}）")
                    return port
            except Exception as e:
                logger.debug(f"[发现] 读取配置文件失败 {path}: {e}")

    return None


# ------------------------------------------------------------------ #
#  Step 3: 进程检测
# ------------------------------------------------------------------ #

def is_vts_process_running() -> bool:
    """检测 VTube Studio 进程是否在运行（需要 psutil）"""
    try:
        import psutil  # 可选依赖

        os_name = _get_os()
        target_names = [n.lower() for n in VTS_PROCESS_NAMES.get(os_name, [])]

        for proc in psutil.process_iter(["name"]):
            try:
                if proc.info["name"] and proc.info["name"].lower() in target_names:
                    logger.info(f"[发现] 检测到 VTS 进程: {proc.info['name']}")
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False
    except ImportError:
        logger.debug("[发现] psutil 未安装，跳过进程检测")
        return False


def find_vts_executable() -> Optional[Path]:
    """在各平台 Steam 安装路径中搜索 VTube Studio 可执行文件"""
    os_name = _get_os()
    search_dirs = STEAM_PATHS.get(os_name, [])
    exe_names = VTS_EXE_RELATIVE.get(os_name, [])

    # 额外：读取 Steam 自定义库路径（libraryfolders.vdf）
    extra_dirs = _get_steam_library_dirs(os_name)
    for lib_dir in extra_dirs:
        search_dirs.append(os.path.join(lib_dir, "steamapps", "common", "VTube Studio"))

    for base_dir in search_dirs:
        base = Path(base_dir)
        if not base.exists():
            continue
        for exe_rel in exe_names:
            exe_path = base / exe_rel
            if exe_path.exists():
                logger.info(f"[发现] 找到 VTS 安装目录: {base}")
                return exe_path

    return None


def _get_steam_library_dirs(os_name: str) -> List[str]:
    """读取 Steam libraryfolders.vdf，获取用户自定义游戏库路径"""
    vdf_paths = {
        "Windows": [
            r"C:\Program Files (x86)\Steam\steamapps\libraryfolders.vdf",
            r"C:\Program Files\Steam\steamapps\libraryfolders.vdf",
        ],
        "Darwin": [
            os.path.expanduser("~/Library/Application Support/Steam/steamapps/libraryfolders.vdf"),
        ],
        "Linux": [
            os.path.expanduser("~/.steam/steam/steamapps/libraryfolders.vdf"),
            os.path.expanduser("~/.local/share/Steam/steamapps/libraryfolders.vdf"),
        ],
    }

    dirs = []
    for vdf_path in vdf_paths.get(os_name, []):
        p = Path(vdf_path)
        if not p.exists():
            continue
        try:
            content = p.read_text(encoding="utf-8", errors="ignore")
            # 简单解析 VDF：找 "path" 字段
            for line in content.splitlines():
                line = line.strip()
                if '"path"' in line.lower():
                    parts = line.split('"')
                    # 格式: "path"  "D:\\SteamLibrary"
                    values = [p for p in parts if p.strip() and p.strip().lower() != "path"]
                    if values:
                        dirs.append(values[-1].replace("\\\\", "\\"))
        except Exception as e:
            logger.debug(f"[发现] 读取 libraryfolders.vdf 失败: {e}")

    return dirs


# ------------------------------------------------------------------ #
#  主入口：自动发现
# ------------------------------------------------------------------ #

async def auto_discover(
    host: str = "localhost",
    timeout: float = 5.0,
) -> Tuple[str, int]:
    """
    自动发现 VTube Studio 的 host 和 port。

    策略顺序：
    1. 扫描默认端口 8001（最快路径）
    2. 读取 VTS 配置文件中记录的端口，再扫描
    3. 扫描全部备用端口 8001-8010
    4. 确认 VTS 进程存在（日志提示，不影响返回值）
    5. 都找不到则返回默认值 (localhost, 8001)

    返回 (host, port)
    """
    logger.info(f"[发现] 开始自动发现 VTube Studio（系统: {_get_os()}）")

    # --- 快速路径：直接试默认端口 ---
    if await _async_port_open(host, VTS_DEFAULT_PORT, timeout=1.0):
        if await _is_vts_websocket(host, VTS_DEFAULT_PORT):
            logger.info(f"[发现] 默认端口 {VTS_DEFAULT_PORT} 命中")
            return host, VTS_DEFAULT_PORT
        # 端口开着但不是 VTS，继续找
        logger.debug(f"[发现] 端口 {VTS_DEFAULT_PORT} 开着但不是 VTS API，继续扫描")

    # --- 读配置文件端口 ---
    config_port = read_port_from_config()
    if config_port and config_port != VTS_DEFAULT_PORT:
        if await _async_port_open(host, config_port, timeout=1.0):
            if await _is_vts_websocket(host, config_port):
                logger.info(f"[发现] 配置文件端口 {config_port} 命中")
                return host, config_port

    # --- 全端口扫描 ---
    found_port = await asyncio.wait_for(scan_ports(host), timeout=timeout)
    if found_port:
        return host, found_port

    # --- 记录进程状态（供日志/调试） ---
    proc_running = is_vts_process_running()
    exe_path = find_vts_executable()

    if exe_path:
        logger.info(f"[发现] VTS 安装路径: {exe_path.parent}，但 API 端口未响应")
        logger.info("[发现] 请确认 VTube Studio 已启动，并在设置中开启了 WebSocket API")
    elif not proc_running:
        logger.info("[发现] 未检测到 VTube Studio 进程，请先启动 VTube Studio")

    logger.warning(f"[发现] 自动发现失败，回退到默认 {host}:{VTS_DEFAULT_PORT}")
    return host, VTS_DEFAULT_PORT


def get_install_info() -> dict:
    """
    返回当前平台的 VTS 安装信息（用于展示给用户）。
    包含：是否找到安装目录、进程是否运行、安装路径
    """
    os_name = _get_os()
    exe_path = find_vts_executable()
    proc_running = is_vts_process_running()

    return {
        "os": os_name,
        "process_running": proc_running,
        "install_path": str(exe_path.parent) if exe_path else None,
        "exe_path": str(exe_path) if exe_path else None,
        "config_port": read_port_from_config(),
    }
