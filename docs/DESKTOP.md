# Windows 桌面窗口适配

`v0.4.0` 增加原生 Windows Desktop Adapter。它解决三个问题：

1. 目标应用不是全屏，截图只截取指定窗口客户区。
2. 识别结果是图像坐标，需要转换为客户区和虚拟桌面绝对坐标。
3. 会限制、隐藏或接管鼠标的应用需要相对鼠标、按下/抬起和滚轮动作。

> Desktop Adapter 必须运行在目标 Windows 交互式桌面中。Docker 容器不能抓取宿主机窗口或注入鼠标；Docker 继续只负责推理。

## 架构

```text
调用方
  ├─ HTTP :8000 / :8011  Inference API（CPU / DirectML / CUDA）
  └─ HTTP :8022          Desktop Adapter（原生 Windows）
       ├─ EnumWindows + PID/进程启动时间/HWND/窗口类绑定
       ├─ PrintWindow / 前台客户区截图
       ├─ DPI-aware 图像坐标 → 客户区 → 虚拟桌面坐标
       ├─ SendInput 绝对鼠标和相对鼠标
       └─ Session、帧几何校验、焦点守卫、按键释放
```

默认仅监听 `127.0.0.1:8022`。不要直接把这个接口暴露到公网。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/api/v1/desktop/windows` | 枚举窗口并按 PID/进程/类名/标题过滤 |
| `POST` | `/api/v1/desktop/sessions` | 绑定窗口并创建会话 |
| `GET` | `/api/v1/desktop/sessions/{id}` | 查询会话和最近帧 |
| `POST` | `/api/v1/desktop/sessions/{id}/capture` | 返回客户区 PNG 或 Base64 JSON |
| `POST` | `/api/v1/desktop/sessions/{id}/analyze` | 截图并调用模型，补充绝对坐标 |
| `POST` | `/api/v1/desktop/sessions/{id}/actions` | 执行受保护的鼠标动作 |
| `POST` | `/api/v1/desktop/sessions/{id}/release` | 强制释放所有已按下鼠标按钮 |
| `DELETE` | `/api/v1/desktop/sessions/{id}` | 关闭会话并释放输入状态 |
| `GET` | `/api/v1/desktop/capabilities` | 返回能力、选择器、动作和安全限制 |
| `GET` | `/healthz` | 进程存活 |
| `GET` | `/readyz` | Desktop Adapter 状态 |

Swagger UI 位于 `http://127.0.0.1:8022/docs`。

模型/Agent 集成可直接使用仓库内置的 MCP stdio bridge，它把窗口枚举和指定窗口解析暴露为
`list_desktop_windows` 与 `analyze_desktop_window`。启动、客户端配置和安全限制见
[MCP.md](MCP.md)。

## 启动

先启动推理服务，例如本机 DirectML：

```powershell
.\scripts\run-windows-directml.ps1 -Port 8011
```

再启动桌面适配器：

```powershell
.\scripts\run-windows-desktop-adapter.ps1 `
  -Port 8022 `
  -InferenceUrl http://127.0.0.1:8011 `
  -EnableInput `
  -EnableActivation
```

- `-EnableInput` 才能执行鼠标动作；默认只允许窗口枚举、截图、识别和 `dry_run`。
- `-EnableActivation` 允许适配器尝试把目标窗口切到前台。Windows 仍可能因前台锁策略拒绝，适配器不会伪造成成功。
- Token 复用 `.env` 中的 `LABEL_CONTROLLERS_DESKTOP_TOKEN`，未设置时回退到 `LABEL_CONTROLLERS_API_KEY`。
- Swagger：`http://127.0.0.1:8022/docs`

## 绑定应用与窗口

### 稳定窗口身份

窗口 ID 不是简单的标题字符串，而是由以下信息计算：

```text
HWND + PID + 进程启动时间 + 窗口类名
```

会话保存完整身份。窗口移动、改标题不会使会话失效；窗口关闭、PID 被复用或窗口被替换时会拒绝操作。

### 选择方式

`GET /api/v1/desktop/windows` 支持：

| 参数 | 说明 |
|---|---|
| `query` | 在标题、进程名和窗口类名中做不区分大小写搜索 |
| `pid` | 按进程 PID 精确过滤 |
| `process_name` | 按可执行文件名精确过滤，如 `chrome.exe` |
| `class_name` | 按 Win32 窗口类精确过滤 |
| `title_regex` | 标题正则，不区分大小写 |
| `include_hidden` | 包含隐藏窗口 |
| `include_untitled` | 包含无标题窗口 |
| `include_tool_windows` | 包含工具窗口 |

推荐策略：

- 单窗口应用：优先 `pid`
- 多窗口应用/Electron/浏览器：优先 `pid + class_name` 或 `pid + title_regex`
- 自动化重连：使用上一帧返回的 `window_id`，并同时携带 `pid` 做二次校验
- 选择结果为多个时返回 `window_selector_ambiguous`，不会猜测第一个窗口

### 创建会话示例

按 PID：

```bash
curl -sS -X POST "http://127.0.0.1:8022/api/v1/desktop/sessions" \
  -H "X-API-Key: ${LABEL_CONTROLLERS_DESKTOP_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "pid": 12345,
    "title_regex": "^My App",
    "input_mode": "auto"
  }'
```

按稳定窗口 ID：

```json
{
  "window_id": "w_7f3a91c2",
  "pid": 12345,
  "input_mode": "absolute"
}
```

## 窗口截图

```bash
curl -sS -X POST \
  "http://127.0.0.1:8022/api/v1/desktop/sessions/${SESSION_ID}/capture?response_format=png&capture_mode=auto" \
  -H "X-API-Key: ${LABEL_CONTROLLERS_DESKTOP_TOKEN}" \
  -o window.png
```

响应头包含：

```text
X-Frame-ID: f_...
X-Window-ID: w_...
X-Geometry-Hash: ...
X-Capture-Backend: printwindow | screen
```

`response_format=json` 会返回 Base64 PNG 和完整几何元数据。

截图模式：

1. `auto`：先尝试 `PrintWindow`，全黑或失败时在前台安全条件下使用屏幕客户区裁剪。
2. `printwindow`：可截取被遮挡的传统窗口；GPU 合成窗口可能返回黑屏。
3. `screen`：只允许目标窗口位于前台，截取精确客户区。

截图固定是**客户区**，不包含标题栏和窗口阴影。窗口必须在创建会话时可见，最小化窗口会被拒绝。

## 坐标转换

适配器启动时启用 `PER_MONITOR_AWARE_V2`，所有坐标均为物理像素。

```text
image_px
  × frame.client_rect.size / frame.image.size
  = client_px

client_px + frame.client_rect.origin
  = screen_px
```

最终通过 `SendInput` 的绝对模式使用 `MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK` 注入，支持多显示器和负坐标。

默认保留 2 px 内边距。`geometry_hash` 包含：

```text
window_id + client origin + client size + DPI + image size
```

执行动作前会重新查询窗口并比较几何。窗口移动、缩放、DPI 改变或最小化时返回 `stale_frame`，不会按旧坐标盲点。

## 识别并直接获得绝对坐标

```bash
curl -sS -X POST \
  "http://127.0.0.1:8022/api/v1/desktop/sessions/${SESSION_ID}/analyze" \
  -H "X-API-Key: ${LABEL_CONTROLLERS_DESKTOP_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "include": "controls,texts,metadata",
    "capture_mode": "auto"
  }'
```

每个控件和元素在原有字段之外增加：

```json
{
  "text": "Save",
  "click": [346, 218],
  "image_click": [346.0, 218.0],
  "client_click": [433, 273],
  "screen_click": [553, 453],
  "coordinate_space": "screen_physical_px"
}
```

顶层 `desktop` 字段包含会话、窗口身份、帧 ID、客户区矩形、DPI、截图后端和输入模式。

## 鼠标动作

所有动作都绑定 `frame_id`。请求示例：

```json
{
  "frame_id": "f_...",
  "activate": true,
  "release_at_end": true,
  "actions": [
    {
      "type": "click",
      "point": [346, 218],
      "space": "image_px",
      "button": "left",
      "count": 1,
      "hold_ms": 35
    }
  ]
}
```

> `click` 和 `drag` 使用绝对坐标语义。会话的 `effective_mode` 为 `relative` 时，应使用
> `aim`（图像/客户区目标经校准转换为相对增量）或 `move_relative` + `click_current`。
> 绝对点击仍可用于那些只是调用 `SetCapture`、但系统光标仍可绝对定位的窗口。

支持的坐标空间：

- `image_px`：推荐，模型输出直接使用
- `client_px`：目标窗口客户区坐标
- `screen_px`：虚拟桌面物理坐标，必须落在当前客户区内

支持的鼠标动作：

| `type` | 主要字段 | 说明 |
|---|---|---|
| `move` | `point`, `space` | 绝对移动到坐标 |
| `click` | `point`, `space`, `button`, `count`, `hold_ms` | 绝对移动后点击 |
| `drag` | `from_x/from_y`, `to_x/to_y`, `steps`, `duration_ms` | 按下、插值移动、抬起 |
| `move_relative` | `dx`, `dy`, `steps`, `interval_ms` | 发送相对鼠标增量 |
| `click_current` | `button`, `count`, `hold_ms` | 在当前位置点击，不重新定位 |
| `mouse_down` | `button` | 按下并保持 |
| `mouse_up` | `button` | 释放 |
| `wheel` | `delta_x`, `delta_y` | 水平/垂直滚轮 |
| `aim` | `point`, `space`, `click_after` | 从会话相对原点计算并发送相对移动 |
| `release_all` | 无 | 强制释放鼠标按键 |

`dry_run=true` 返回完整映射，但不移动鼠标。可以先验证坐标：

```bash
curl -sS -X POST \
  "http://127.0.0.1:8022/api/v1/desktop/sessions/${SESSION_ID}/actions" \
  -H "X-API-Key: ${LABEL_CONTROLLERS_DESKTOP_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "frame_id": "f_...",
    "dry_run": true,
    "actions": [{"type": "click", "point": [346, 218]}]
  }'
```

## 接管鼠标的应用

### 自动判断

`input_mode=auto` 只在有明确信号时选择 `relative`：

- `GetClipCursor()` 显示鼠标被限制在目标窗口内
- 或调用方按应用 Profile 明确指定 `relative`

隐藏光标本身不足以判定指针锁定。

### 应用 Profile

对于多窗口应用或已知会接管鼠标的游戏/3D 应用，建议创建会话时显式给参数：

```json
{
  "window_id": "w_7f3a91c2",
  "input_mode": "relative",
  "relative_gain_x": 0.45,
  "relative_gain_y": 0.45,
  "relative_max_step": 64,
  "relative_origin_fraction": [0.5, 0.5]
}
```

- `relative_gain_x/y`：视觉像素到相对鼠标单位的校准增益，可为负值实现反向轴。
- `relative_max_step`：单个相对移动包的最大系统鼠标单位。
- `relative_origin_fraction`：指针锁定状态下的屏幕中心/准星位置。

`aim` 示例：

```json
{
  "type": "aim",
  "point": [480, 340],
  "space": "image_px",
  "click_after": true,
  "button": "left",
  "interval_ms": 8
}
```

`aim` 计算：

```text
delta = (target_client - relative_origin) × relative_gain
```

Windows 相对鼠标可能受系统鼠标加速和应用自身 Raw Input 灵敏度影响，因此 `aim` 是**开环校准**动作，不是像素级保证。实际落地时应：

1. 在目标应用中测量一次移动响应；
2. 调整 `relative_gain_x/y`；
3. 每次动作后重新截图识别；
4. 多次小步修正，而不是发送一次巨大移动。

对于只接受物理设备或主动拒绝注入输入的应用，不做 Hook、驱动或反作弊绕过；接口会表现为应用无响应或返回输入失败。

### 按下保持和拖拽

跨请求保持鼠标按下时设置 `release_at_end=false`：

```json
{
  "frame_id": "f_...",
  "release_at_end": false,
  "actions": [{"type": "mouse_down", "button": "left"}]
}
```

完成后再释放：

```json
{
  "frame_id": "f_...",
  "actions": [{"type": "mouse_up", "button": "left"}]
}
```

异常、会话关闭或会话超时都会执行安全释放。也可以随时调用：

```bash
curl -sS -X POST \
  "http://127.0.0.1:8022/api/v1/desktop/sessions/${SESSION_ID}/release" \
  -H "X-API-Key: ${LABEL_CONTROLLERS_DESKTOP_TOKEN}"
```

## 安全守卫

- 默认 `ALLOW_INPUT=false`，动作请求返回 `input_disabled`。
- 默认不自动激活窗口。
- 绝对点击前验证映射点最顶层窗口属于目标窗口或其 owned popup。
- 目标窗口失去前台焦点时中止。
- 鼠标按下状态未释放时发生异常，会强制发送左/中/右键抬起。
- 路径和坐标被限制在当前窗口客户区内。
- 会话默认 300 秒过期。
- 每个桌面输入请求串行执行，避免多个 Agent 交错按键。
- UAC 安全桌面、锁屏、受 DRM 保护内容和反作弊限制不支持。

## 主要错误码

| Code | 含义 |
|---|---|
| `window_not_found` | 没有窗口匹配 |
| `window_selector_ambiguous` | 选择器匹配多个窗口 |
| `window_gone` | 会话绑定后窗口关闭 |
| `window_identity_changed` | HWND/PID/进程启动时间/窗口类变化 |
| `window_minimized` | 窗口最小化 |
| `foreground_required` | 需要目标窗口在前台 |
| `foreground_activation_failed` | Windows 拒绝激活请求 |
| `stale_frame` | 截图后窗口几何发生变化 |
| `point_obstructed` | 映射点被其他窗口覆盖 |
| `input_disabled` | 服务未启用输入注入 |
| `send_input_failed` | Win32 拒绝输入事件 |
| `inference_unavailable` | 模型服务不可访问 |

## 配置

| 环境变量 | 默认值 | 说明 |
|---|---:|---|
| `LABEL_CONTROLLERS_DESKTOP_HOST` | `127.0.0.1` | 监听地址 |
| `LABEL_CONTROLLERS_DESKTOP_PORT` | `8022` | 监听端口 |
| `LABEL_CONTROLLERS_DESKTOP_TOKEN` | 必填 | 独立 Token；未设置时回退到 `LABEL_CONTROLLERS_API_KEY` |
| `LABEL_CONTROLLERS_DESKTOP_ALLOW_INPUT` | `false` | 允许鼠标注入 |
| `LABEL_CONTROLLERS_DESKTOP_ALLOW_ACTIVATE` | `false` | 允许自动激活窗口 |
| `LABEL_CONTROLLERS_DESKTOP_INFERENCE_URL` | `http://127.0.0.1:8011` | 模型 API |
| `LABEL_CONTROLLERS_DESKTOP_INFERENCE_API_KEY` | 回退到 API Key | 调用模型 API 的密钥 |
| `LABEL_CONTROLLERS_DESKTOP_CAPTURE_MODE` | `auto` | 默认截图模式 |
| `LABEL_CONTROLLERS_DESKTOP_SESSION_TTL_SECONDS` | `300` | 会话 TTL |
| `LABEL_CONTROLLERS_DESKTOP_MAX_ACTIONS` | `32` | 单请求动作上限 |
| `LABEL_CONTROLLERS_DESKTOP_SAFE_MARGIN_PX` | `2` | 客户区安全边距 |
