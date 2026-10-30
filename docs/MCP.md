# MCP 服务（stdio）

服务同时提供 MCP（Model Context Protocol）入口，供支持 MCP 的主机直接调用，无需自己写 HTTP 客户端。

## 启动

```bash
# 装了包之后
label-controllers-mcp

# 或从源码
PYTHONPATH=src python -m label_controllers.mcp_server
```

stdio 传输：stdin/stdout 每行一条 JSON-RPC 2.0 消息；日志一律走 stderr。

## 配置（环境变量）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `LABEL_CONTROLLERS_BASE_URL` | `http://127.0.0.1:8000` | 已运行的服务地址 |
| `LABEL_CONTROLLERS_API_KEY` | 空 | 服务端启用鉴权时填写 |
| `LABEL_CONTROLLERS_IMAGE` | 空 | 可选默认截图路径（主机无法自行传图时用） |

MCP 服务本身不做推理，它把调用转给 REST API；因此需要先起服务（`label-controllers serve` 或容器）。

## 工具

### `analyze_ui_screenshot`

在原有参数（`include` / `annotate` / `icon_confidence` / `ocr_confidence` / `a11y`）之外，
MCP 版多了两个传图通道：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `image_base64` | string | 截图 Base64 或 data URL |
| `image_path` | string | MCP 服务所在机器上的截图路径 |

两者都没有时回退到 `LABEL_CONTROLLERS_IMAGE`。

`a11y` 为对象，与原 HTTP 字段一致：

```jsonc
{"mode": "cdp", "cdp_url": "http://127.0.0.1:9222", "target": 0}
```

### `probe_ui_accessibility`

判断某个页面/窗口的无障碍树能不能当控件源用，不需要截图。

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `cdp_url` | string | DevTools 端点，如 `http://127.0.0.1:9222` |
| `target` | integer | 页面序号，默认 0 |
| `url` | string | 可选，先导航 |
| `timeout_seconds` | number | 默认 20 |
| `elements` | array | 已采好的树（inline 模式），给了就不连 CDP |

## 交互示例

```jsonc
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"host","version":"1.0"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"probe_ui_accessibility","arguments":{"cdp_url":"http://127.0.0.1:9222"}}}
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"analyze_ui_screenshot","arguments":{"image_path":"/tmp/shot.png","include":["controls"],"a11y":{"mode":"cdp","cdp_url":"http://127.0.0.1:9222"}}}}
```

工具结果统一为 MCP 的 `content` 结构，正文是一段 JSON 文本；失败时 `isError=true`，
`content[0].text` 里是 `{"ok": false, "error": {...}}`。

## 客户端配置片段

以常见的 `mcpServers` 结构为例：

```jsonc
{
  "mcpServers": {
    "label-controllers": {
      "command": "label-controllers-mcp",
      "env": {
        "LABEL_CONTROLLERS_BASE_URL": "http://127.0.0.1:8000",
        "LABEL_CONTROLLERS_API_KEY": ""
      }
    }
  }
}
```

## 支持的协议方法

`initialize`、`notifications/initialized`、`ping`、`tools/list`、`tools/call`。
未实现 `resources/*` 与 `prompts/*`，收到未知方法回 `-32601`。
