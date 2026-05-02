# AstrBot VTube Studio Live2D 控制插件

当前版本：`1.2.1`

让 AstrBot 的 LLM 能够实时控制 VTube Studio 中的 Live2D 模型，包括触发动作热键、切换表情、注入参数等。

---

## 功能特性

| LLM 工具函数 | 说明 |
|---|---|
| `vts_get_hotkeys` | 获取当前模型所有热键列表 |
| `vts_trigger_hotkey` | 触发指定热键（播放动作、切换表情等） |
| `vts_get_expressions` | 获取所有表情及当前激活状态 |
| `vts_set_expression` | 激活/停用指定表情 |
| `vts_move_model` | 移动/旋转/缩放模型 |
| `vts_inject_parameter` | 直接注入 Live2D 参数值（精细控制） |
| `vts_get_parameters` | 获取所有可用 Live2D 参数 |
| `vts_model_info` | 获取当前模型基本信息 |

此外，插件支持“自主 Live2D 标签”机制：每次 Bot 回复前会把可用表情按键说明注入给 LLM，LLM 可在回复末尾输出 `<l2d:标签>`，插件会截获标签、从最终消息中移除，并触发对应 VTS 热键。

> 只有在插件确认 VTube Studio / Live2D 已连接可用时，才会向 LLM 注入这些标签说明；未连接时不会影响正常对话。

---

## 安装

1. 将本目录放入 AstrBot 的 `data/plugins/` 目录下
2. 在 AstrBot WebUI 的插件管理页面中启用插件，依赖会自动安装

---

## 使用前提

1. **启动 VTube Studio**（Steam 版或独立版均可）
2. 在 VTube Studio 中开启 API：
   - 进入 **设置 → 常规设置 → 插件 API**
   - 将 "启动 API（WebSocket）" 开关打开
   - 默认端口为 **8001**，如有修改请在插件配置中同步

---

## 首次使用：认证

插件启动后，在聊天中发送：

```
/vts_auth
```

VTube Studio 会弹出授权窗口，点击 **允许**，认证成功后 Token 会自动保存，之后重启无需重新认证。

---

## 常用命令

```
/vts_auth     认证 VTube Studio（首次使用）
/vts_status   查看连接状态和当前模型
/vts_discover  重新扫描并自动发现 VTS 地址
/vts_list     列出所有热键和表情（方便 LLM 选择）
/vts_l2d_list  查看当前启用的自主 L2D 标签
```

---

## 自主 Live2D 标签

在插件配置中开启 `autonomous_l2d_enabled`，并在 `l2d_hotkeys` 里添加表情按键条目：

| 字段 | 说明 |
|---|---|
| `tag` | 给 LLM 使用的标签名，例如 `happy`，模型会输出 `<l2d:happy>` |
| `hotkey_id` | VTube Studio 当前模型里的热键 ID，可用 `/vts_list` 查看 |
| `description` | 表情说明，LLM 会根据这里的语气和场景描述自主选择 |
| `duration` | 持续时间，设为 `0` 表示只触发一次 |
| `release_after_duration` | 持续时间结束后是否再次触发同一个热键，适合开关型表情 |

LLM 输出示例：

```text
好呀，我已经记下来了。
<l2d:happy>
```

用户最终只会看到：

```text
好呀，我已经记下来了。
```

插件会在后台触发 `happy` 对应的 VTS 热键。如果本次不适合表情，LLM 会被提示输出 `<l2d:none>`，这个标签同样会被移除且不会触发热键。

---

## LLM 工具调用示例

配置好 LLM 后，直接对话即可控制 Live2D：

> 「让模型开心地挥手」→ LLM 自动调用 `vts_trigger_hotkey` 触发对应热键  
> 「切换成害羞表情」→ LLM 调用 `vts_set_expression` 激活对应表情文件  
> 「让模型向左移动」→ LLM 调用 `vts_move_model` 控制位置  
> 「让模型微笑」→ LLM 调用 `vts_inject_parameter` 设置 `MouthSmile` 参数  

---

## 插件配置

在 AstrBot 插件配置中可设置：

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `vts_host` | `localhost` | VTube Studio 所在主机地址 |
| `vts_port` | `8001` | VTube Studio API 端口 |
| `auto_discover` | `true` | 自动发现 VTS 地址（推荐开启） |
| `auto_connect` | `true` | 插件启动时自动认证 |
| `autonomous_l2d_enabled` | `true` | 启用自主 Live2D 标签机制 |
| `l2d_max_tags_per_reply` | `1` | 每次回复最多触发的 L2D 标签数量 |
| `l2d_hotkeys` | `[]` | Live2D 表情按键条目列表 |
| `debug_mode` | `false` | 输出详细调试日志 |

---

## 更新记录

### 1.2.1

- 新增自主 Live2D 标签机制：回复前注入可选表情说明，回复后截获 `<l2d:标签>` 并触发 VTS 热键。
- 新增 `l2d_hotkeys` 条目式配置，可配置标签、热键 ID、说明、持续时间和是否自动结束。
- 新增 `/vts_l2d_list` 命令查看当前启用的标签条目。
- 标签会从最终回复中移除，用户不会看到控制标签。
- 只有在 Live2D/VTS 已连接可用时才注入标签提示词，未连接时不干扰普通聊天。

---

## 常见问题

**Q: 提示"未连接到 VTube Studio"**  
A: 确认 VTube Studio 已启动并开启了 WebSocket API，然后发送 `/vts_auth` 进行认证。

**Q: LLM 不知道有哪些热键/表情可用**  
A: 发送 `/vts_list` 查看列表，并将结果告知 LLM（或放入 System Prompt），LLM 就能精准调用。

**Q: 想让 LLM 持续控制面部参数（如眨眼动画）**  
A: 需要持续调用 `vts_inject_parameter`，建议在场景中使用。

---

## 目录结构

```
astrbot_plugin_vtube_studio/
├── __init__.py          # 包入口
├── main.py              # 插件主体（Star 类 + LLM 工具注册）
├── vts_client.py        # VTube Studio WebSocket 客户端封装
├── vts_discovery.py     # 跨平台自动发现模块
├── metadata.yaml         # 插件元数据
├── _conf_schema.json     # 插件配置 Schema
├── requirements.txt     # Python 依赖
└── README.md            # 说明文档
```

---

## License

MIT
