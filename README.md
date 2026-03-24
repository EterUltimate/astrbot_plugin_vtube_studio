# AstrBot VTube Studio Live2D 控制插件

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
/vts_list     列出所有热键和表情（方便 LLM 选择）
```

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

---

## 常见问题

**Q: 提示"未连接到 VTube Studio"**  
A: 确认 VTube Studio 已启动并开启了 WebSocket API，然后发送 `/vts_auth` 进行认证。

**Q: 认证后重启 AstrBot 又需要重新认证**  
A: Token 保存在插件目录下的 `.vts_token` 文件中，只要文件存在就会自动复用。

**Q: LLM 不知道有哪些热键/表情可用**  
A: 发送 `/vts_list` 查看列表，并将结果告知 LLM（或放入 System Prompt），LLM 就能精准调用。

**Q: 想让 LLM 持续控制面部参数（如眨眼动画）**  
A: 需要持续调用 `vts_inject_parameter`，每秒至少一次才能保持效果。可在插件中扩展实现循环注入逻辑。

---

## 目录结构

```
astrbot_plugin_vtube_studio/
├── main.py          # 插件主体（Star 类 + LLM 工具注册）
├── vts_client.py    # VTube Studio WebSocket 客户端封装
├── metadata.yaml    # 插件元数据
├── requirements.txt # Python 依赖
└── README.md        # 说明文档
```

---

## License

MIT
