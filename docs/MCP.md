# MCP 桌面窗口解析服务

`v0.5.0` 增加 MCP stdio bridge，把现有三层能力串成一个面向模型的标准工具调用入口：

```text
MCP Host（Claude Desktop / IDE / Agent）
  ⇅ newline-delimited JSON-RPC 2.0 over stdio
label-controllers-mcp
  ⇅ HTTP（仅 loopback）
Windows Desktop Adapter :8022
  ⇅ multipart PNG
Label Controllers Inference API :8000/:8011
```

MCP 进程不包含 Win32 截图、窗口绑定或坐标转换实现，只代理 Desktop Adapter。默认部署把 MCP
launcher 与 Desktop Adapter 一起放在目标 Windows 交互式桌面上；MCP 进程本身只依赖 HTTP，但
如果把 Desktop Adapter 跨主机暴露，必须自行增加 TLS、认证和网络访问控制，不能直接把 loopback
端口转发到不可信网络。推理 API 仍可运行在 Docker、DirectML 或 CUDA 后端。

## 两个工具

| 工具 | 用途 |
|---|---|
| `list_desktop_windows` | 枚举可见窗口，按 PID、进程名、窗口类或标题正则过滤，返回稳定 `window_id` |
| `analyze_desktop_window` | 按 `window_id`/PID/进程/类/标题绑定窗口，截取客户区并解析，返回控件、OCR 和坐标 |

推荐调用顺序：

1. 目标不确定时先调用 `list_desktop_windows`。
2. 用结果中的 `window_id` 调用 `analyze_desktop_window`。
3. 如果只给 PID 且匹配多个窗口，Desktop Adapter 返回 `window_selector_ambiguous` 和候选列表；不会猜测第一个窗口。

`analyze_desktop_window` 默认返回 `controls,texts,metadata`。可通过 `include` 增加 `elements`、`raw_icons`、`raw_ocr` 或 `annotated_image`。每个控件的坐标字段保持：

- `click` / `image_click`：截图图像坐标；
- `client_click`：窗口客户区坐标；
- `screen_click`：虚拟桌面物理像素坐标；
- `coordinate_space: "screen_physical_px"`。

请求 `annotated_image` 时，MCP 工具结果包含两个 content block：第一个是 JSON 文本，第二个是
标准 MCP `image` content block（`mimeType: image/png` 和 Base64 `data`）。标注图 Base64 不会在
JSON 文本中重复；JSON 里的 `annotated_image` 只保留 MIME、长度、SHA-256 和 `transport` 摘要。

MCP 服务只提供解析，不提供鼠标动作工具。需要受保护点击时，继续使用 Desktop Adapter 的 `/actions` API；不要把输入注入和模型推理放进同一个默认工具。

## 启动前检查

先启动推理服务和 Desktop Adapter：

```powershell
.\scripts\run-windows-directml.ps1 -Port 8011

.\scripts\run-windows-desktop-adapter.ps1 `
  -Port 8022 `
  -InferenceUrl http://127.0.0.1:8011
```

确认：

```powershell
Invoke-RestMethod http://127.0.0.1:8011/readyz
Invoke-RestMethod http://127.0.0.1:8022/healthz
```

Desktop Adapter 的 token 通常来自仓库根目录 `.env` 的
`LABEL_CONTROLLERS_DESKTOP_TOKEN`；未设置时回退到 `LABEL_CONTROLLERS_API_KEY`。MCP launcher 也会读取同一个 `.env`，但不会把 token 写入 stdout 或工具结果。

## MCP 客户端配置

使用 MCP launcher，让它在启动时读取 `.env`，再把 stdout 完全交给 MCP 协议：

```json
{
  "mcpServers": {
    "label-controllers-desktop": {
      "command": "C:\\path\\to\\label-controllers\\.venv-directml\\Scripts\\python.exe",
      "args": [
        "C:\\path\\to\\label-controllers\\scripts\\run_mcp_server.py",
        "--log-level",
        "warning"
      ]
    }
  }
}
```

MCP 进程可以运行在任何能访问 Desktop Adapter 的主机，但跨主机时不能沿用明文 loopback 配置。
仅需图像上传能力时可直接使用 Inference API；窗口绑定和截图必须由 Windows Desktop Adapter 完成。

如果客户端不会继承 shell 环境，可在 `env` 中设置明文 token，但更推荐设置 token 文件路径：

```json
{
  "mcpServers": {
    "label-controllers-desktop": {
      "command": "C:\\path\\to\\label-controllers\\.venv-directml\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\label-controllers\\scripts\\run_mcp_server.py"],
      "env": {
        "LABEL_CONTROLLERS_MCP_TOKEN_FILE": "C:\\path\\to\\label-controllers\\.env.mcp"
      }
    }
  }
}
```

`.env.mcp` 只放一行 token，已被 `.gitignore` 的 `.env.*` 规则排除：

```powershell
Set-Content .env.mcp "replace-with-your-desktop-token" -Encoding utf8
```

不要提交 token、其他 `.env` 文件或包含真实凭据的 MCP 配置。

## 协议与命令行

安装项目后可直接使用 console script：

```powershell
label-controllers-mcp --print-tools
label-controllers-mcp
```

也可以直接用 Python 模块启动：

```powershell
$env:LABEL_CONTROLLERS_DESKTOP_TOKEN = "...desktop token..."
$env:PYTHONPATH = "src"
python -m label_controllers.mcp_server
```

不打印 tool schema 时，`stdout` 只输出每行一个 JSON-RPC 消息；所有日志和异常诊断都写入 `stderr`。如果客户端报 JSON 解析错误，先检查是否有 proxy、PowerShell `Write-Host` 或其他包装层污染了 stdout。

支持协商的协议版本：

- `2025-06-18`
- `2025-03-26`
- `2024-11-05`

服务声明 `tools` capability，工具列表不会动态变化。

## 调用示例

### 枚举窗口

工具：`list_desktop_windows`

```json
{
  "pid": 12345,
  "process_name": "app.exe",
  "limit": 20
}
```

关键返回字段：

```json
{
  "ok": true,
  "count": 1,
  "returned": 1,
  "windows": [
    {
      "window_id": "w_7f3a91c2",
      "pid": 12345,
      "process": "app.exe",
      "class": "Chrome_WidgetWin_1",
      "title": "My App"
    }
  ]
}
```

### 解析窗口

工具：`analyze_desktop_window`

```json
{
  "window_id": "w_7f3a91c2",
  "include": ["controls", "texts", "metadata"],
  "ocr_confidence": 0.4
}
```

结果在推理 API 的字段之外增加：

```json
{
  "ok": true,
  "desktop": {
    "window": {"window_id": "w_7f3a91c2", "pid": 12345},
    "frame": {"frame_id": "f_...", "geometry_hash": "..."},
    "input": {"mode": "absolute", "mapped_control_count": 12}
  },
  "mcp": {
    "tool": "analyze_desktop_window",
    "session_closed": true
  }
}
```

每次工具调用都会新建一个 Desktop session；无论推理成功还是失败，最后都会调用 `DELETE /sessions/{id}` 释放 session。关闭失败会在 `mcp.cleanup_error` 中报告，但不会遮蔽主推理结果。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `LABEL_CONTROLLERS_MCP_DESKTOP_URL` | `http://127.0.0.1:8022` | Desktop Adapter 地址 |
| `LABEL_CONTROLLERS_MCP_DESKTOP_TOKEN` | 空 | MCP 专用 token；优先级最高 |
| `LABEL_CONTROLLERS_DESKTOP_TOKEN` | 空 | Desktop Adapter token 回退 |
| `LABEL_CONTROLLERS_API_KEY` | 空 | 再回退到统一 API key |
| `LABEL_CONTROLLERS_MCP_TOKEN_FILE` | 空 | 仅包含 token 的文件路径 |
| `LABEL_CONTROLLERS_MCP_TIMEOUT_SECONDS` | `180` | 分析 HTTP 读超时 |
| `LABEL_CONTROLLERS_MCP_LOG_LEVEL` | `info` | `critical/error/warning/info/debug` |

## 故障排查

| 现象 | 检查 |
|---|---|
| `desktop_adapter_unavailable` | Desktop Adapter 是否启动在 `127.0.0.1:8022` |
| `unauthorized` | MCP 与 Desktop Adapter 是否使用同一 token |
| `window_not_found` | 重新调用 `list_desktop_windows`，窗口可能已关闭 |
| `window_selector_ambiguous` | 使用返回候选中的 `window_id`，或增加 `class_name`/`title_regex` |
| `window_minimized` | 先恢复目标窗口；解析不会自动激活或还原窗口 |
| `inference_unavailable` | 推理 API 是否在 `8011`/`8000` 运行，Desktop Adapter 的 `INFERENCE_URL` 是否正确 |
| 客户端 JSON parse error | 确认 stdout 没有日志、PowerShell 提示或代理包装层 |
