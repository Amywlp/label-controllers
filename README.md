# Label Controllers

[![CI](https://github.com/Amywlp/label-controllers/actions/workflows/ci.yml/badge.svg)](https://github.com/Amywlp/label-controllers/actions/workflows/ci.yml)
[![Container](https://github.com/Amywlp/label-controllers/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/Amywlp/label-controllers/actions/workflows/docker-publish.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Label Controllers 是一个面向 Computer Use、UI 自动化和 GUI 测试的截图控件识别服务。
上传一张界面截图，服务会同时运行 OmniParser v2 YOLO11m 图标检测与 RapidOCR 文字识别，
再把图标和文字融合成可点击控件、OCR 文本块和带标注的图像。

GitHub 仓库：[Amywlp/label-controllers](https://github.com/Amywlp/label-controllers)（显示名称 **Label Controllers**）。

## 功能

- `POST /api/v1/analyze`：上传截图，按需返回不同字段。
- 可点击控件 JSON：文字、语义角色、置信度、矩形、中心点、安全点击点。
- 全量元素 JSON：包含控件、纯文字区域和不可点击容器。
- 控件文字 / OCR 文本块：独立文字、置信度和坐标。
- 标注 PNG：可通过二进制响应下载，也可作为 Base64 放入 JSON。
- 原始 YOLO 与 OCR 结果：适合调试和二次融合。
- 模型常驻内存，同一容器内无需为每次请求重新加载。
- Docker 镜像在构建阶段自动下载并校验 `icon_detect.pt`。
- CPU、NVIDIA CUDA、Windows DirectML 三种运行路径，共用同一 API 和输出格式。
- 运行设备、显卡名称、OCR Execution Provider 和分阶段耗时均写入响应元数据。
- OCR 路径默认跳过冗余角度分类，并直接复用已解码的 RGB 数组，减少重复解码。
- 提供标准 OpenAI / Anthropic Tool Calling 定义、Python 执行器和命令行工具。
- 提供 MCP stdio 服务，暴露 `list_desktop_windows` 和 `analyze_desktop_window` 两个窗口解析工具。
- 原生 Windows Desktop Adapter：按 PID/进程启动时间/HWND/窗口类绑定窗口并截取客户区。
- 控件图像坐标自动转换为客户区坐标和虚拟桌面绝对坐标，包含 DPI、多显示器和负坐标处理。
- `SendInput` 绝对鼠标与相对鼠标动作，支持指针锁定应用的按下、移动、抬起、滚轮和校准移动。
- 可选 API Key 鉴权、上传大小限制、像素限制、CORS、OpenAPI 文档。

## 快速部署

需要 Docker 24+ 与 Docker Compose v2。

```bash
git clone https://github.com/Amywlp/label-controllers.git
cd label-controllers
cp .env.example .env
# 编辑 .env，把 LABEL_CONTROLLERS_API_KEY 改成长随机值
docker compose up -d --build
```

等待模型加载和容器健康检查：

```bash
docker compose ps
curl http://127.0.0.1:8000/readyz
```

默认对外端口为 `8000`，可通过 `.env` 中的 `LABEL_CONTROLLERS_API_PORT` 修改。
接口文档：

- Swagger UI：<http://127.0.0.1:8000/docs>
- OpenAPI JSON：<http://127.0.0.1:8000/openapi.json>
- 能力清单：<http://127.0.0.1:8000/api/v1/capabilities>

以上命令使用 CPU 推理镜像。GPU 部署方式如下。

## GPU 加速

| 运行环境 | 推荐后端 | 启动方式 |
|---|---|---|
| NVIDIA GPU + Linux Docker | CUDA 12.4 + ONNX Runtime CUDA | `docker compose -f compose.yaml -f compose.gpu.yaml up -d --build` |
| Windows 原生环境（AMD/NVIDIA/Intel） | DirectML | `.\scripts\setup-windows-directml.ps1`，然后 `.\scripts\run-windows-directml.ps1` |
| CPU-only | PyTorch CPU + ONNX Runtime CPU | `docker compose up -d --build` |

NVIDIA Docker 需要 NVIDIA Container Toolkit。DirectML 方式已在 AMD Radeon RX 7800 XT
上完成速度和输出一致性测试；当前 WSL2/AMD 环境不提供 ROCm 所需的 `/dev/kfd`，因此该显卡
不能直接用于 Linux Docker GPU 推理。CUDA 镜像已完成本地构建和只读容器接口冒烟测试，但仍需在目标 NVIDIA 主机验证 CUDA EP 实例与性能。

完整配置、限制和故障排查见 [docs/GPU.md](docs/GPU.md)。

## 非全屏窗口与鼠标接管

新增原生 Windows Desktop Adapter，默认监听 `127.0.0.1:8022`。它不会替代推理 API，
而是负责窗口枚举、客户区截图、坐标系转换和受控鼠标输入：

```powershell
# 先启动推理服务
.\scripts\run-windows-directml.ps1 -Port 8011

# 再启动窗口适配器
.\scripts\run-windows-desktop-adapter.ps1 `
  -Port 8022 `
  -InferenceUrl http://127.0.0.1:8011 `
  -EnableInput `
  -EnableActivation
```

- 通过 `window_id`、`pid`、`process_name`、`class_name`、`title_regex` 唯一绑定窗口。
- 默认截取客户区，不包含标题栏、阴影或周围桌面。
- 模型输出的 `controls[].click` 会附加 `client_click` 和 `screen_click` 绝对坐标。
- `absolute` 模式使用 `MOUSEEVENTF_ABSOLUTE | VIRTUALDESK`，支持多显示器和负坐标。
- `relative` / `aim` 模式面向 ClipCursor、Raw Input、隐藏光标或指针锁定应用。
- 执行前校验窗口身份、前台焦点、客户区几何和裁剪区域，避免窗口移动后误点。
- Docker 不能操作宿主机窗口；该适配器必须在 Windows 交互式桌面中原生运行。

完整 API、动作模型、鼠标校准和安全限制见 [docs/DESKTOP.md](docs/DESKTOP.md)。

## MCP 桌面窗口服务

仓库包含一个 MCP stdio bridge，把 Windows Desktop Adapter 封装成两个模型工具：

- `list_desktop_windows`：按 PID、进程名、窗口类或标题正则枚举窗口，返回稳定 `window_id`。
- `analyze_desktop_window`：绑定指定窗口、截取客户区并调用现有推理 API，返回控件、OCR、
  `client_click`、`screen_click` 和坐标空间。每次调用都会自动关闭临时 Desktop session。
  请求标注图时，MCP 结果会同时返回 JSON 文本和标准 `image` content block。

MCP 进程只代理已有的 Desktop Adapter，不复制 Win32 和坐标逻辑，也不暴露鼠标注入工具：

```text
MCP Host -> label-controllers-mcp (stdio) -> Desktop Adapter :8022 -> Inference API :8011
```

客户端配置示例：

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

launcher 会静默读取仓库 `.env`，因此 token 不需要写入客户端配置。MCP 的 stdout 仅承载
newline-delimited JSON-RPC，日志全部写入 stderr。完整配置、工具参数、安全限制和故障排查见
[docs/MCP.md](docs/MCP.md)，可复制配置模板见
[examples/mcp.windows.json](examples/mcp.windows.json)。

## 标准 Tool Calling

服务提供与 OpenAI Chat Completions / Responses 兼容的函数工具，同时返回 Anthropic
Messages 格式：

```http
GET /api/v1/tools
```

工具名为 `analyze_ui_screenshot`。语言模型只选择输出字段、置信度和是否标注；当前截图由
工具执行器绑定，模型不需要生成图片 Base64。

Python 客户端：

```python
from label_controllers.tool import LabelControllersTool

tool = LabelControllersTool(
    base_url="http://127.0.0.1:8000",
    screenshot_provider=lambda: screenshot_bytes,
)
result = tool.execute_tool_call({
    "include": ["controls", "texts", "metadata"],
    "annotate": False,
})
```

OpenAI SDK 传递 `tools=[tool.definition]` 即可；完整示例见
[examples/openai_tool_call.py](examples/openai_tool_call.py)。

命令行：

```bash
label-controllers-tool --schema
label-controllers-tool --image screenshot.png --include controls,texts,metadata
```

完整参数、错误结构和集成方式见 [docs/TOOL.md](docs/TOOL.md)。

## 调用示例

### 1. 默认返回控件、文字和元数据

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/analyze" \
  -H "X-API-Key: ${LABEL_CONTROLLERS_API_KEY}" \
  -F "file=@screenshot.png" \
  -F "include=controls,texts,metadata" \
  -F "response_format=json" \
  -o result.json
```

### 2. 只返回可点击控件

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/analyze" \
  -H "Authorization: Bearer ${LABEL_CONTROLLERS_API_KEY}" \
  -F "file=@screenshot.png" \
  -F "include=controls" \
  -o controls.json
```

### 3. 直接获取标注后的 PNG

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/analyze" \
  -H "X-API-Key: ${LABEL_CONTROLLERS_API_KEY}" \
  -F "file=@screenshot.png" \
  -F "response_format=png" \
  -o annotated.png
```

### 4. 一次请求返回全部数据

```bash
curl -X POST "http://127.0.0.1:8000/api/v1/analyze" \
  -H "X-API-Key: ${LABEL_CONTROLLERS_API_KEY}" \
  -F "file=@screenshot.png" \
  -F "include=all" \
  -F "response_format=json" \
  -o full.json
```

## 可选返回字段

`include` 是逗号分隔字符串，默认值为 `controls,texts,metadata`。

| 字段 | 内容 |
|---|---|
| `controls` | 仅可点击控件；含 `text`、`role`、坐标和安全点击点 |
| `elements` | 所有融合元素，包括不可点击容器 |
| `texts` | OCR 文本块、置信度、矩形 |
| `metadata` | 模型信息、计数和各阶段耗时 |
| `raw_icons` | 原始 YOLO 图标检测结果 |
| `raw_ocr` | 原始 RapidOCR 检测结果 |
| `annotated_image` | Base64 PNG，嵌入 JSON |
| `all` | 上述全部字段 |

响应中的坐标均为相对上传图片左上角的像素坐标。`controls[].click` 是融合算法通过
OpenCV 距离变换得到的建议点击点；`center` 是控件矩形中心。

## 主要接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/healthz` | 进程存活检查 |
| `GET` | `/readyz` | 模型已加载与运行状态 |
| `GET` | `/api/v1/capabilities` | 字段、格式和限制说明 |
| `GET` | `/api/v1/tools` | OpenAI / Anthropic 标准工具定义 |
| `POST` | `/api/v1/analyze` | 上传截图并分析 |
| `GET` | `/docs` | Swagger UI |
| `GET` | `/openapi.json` | OpenAPI 3 文档 |

完整字段定义和响应示例见 [docs/API.md](docs/API.md)。窗口截图、PID 绑定、绝对/相对鼠标动作见 [docs/DESKTOP.md](docs/DESKTOP.md)。

## 环境变量

复制 `.env.example` 为 `.env`。`.env` 已被 `.gitignore` 排除。

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `LABEL_CONTROLLERS_API_PORT` | `8000` | Docker 对外映射端口 |
| `LABEL_CONTROLLERS_API_KEY` | 空 | 设置后启用 Bearer / X-API-Key 鉴权 |
| `LABEL_CONTROLLERS_MAX_UPLOAD_MB` | `20` | 单张图片字节上限 |
| `LABEL_CONTROLLERS_MAX_IMAGE_PIXELS` | `40000000` | 解码后的像素上限 |
| `LABEL_CONTROLLERS_IMGSZ` | `1280` | YOLO 推理尺寸 |
| `LABEL_CONTROLLERS_ICON_CONFIDENCE` | `0.18` | 默认 YOLO 阈值 |
| `LABEL_CONTROLLERS_OCR_CONFIDENCE` | `0.40` | 默认 OCR 阈值 |
| `LABEL_CONTROLLERS_DEVICE` | `auto` | `auto/cpu/cuda/rocm/directml/mps` |
| `LABEL_CONTROLLERS_DEVICE_INDEX` | `0` | GPU 序号 |
| `LABEL_CONTROLLERS_OCR_DEVICE` | `auto` | `auto/cpu/cuda/rocm/directml` |
| `LABEL_CONTROLLERS_OCR_REC_BATCH_NUM` | `0` | OCR 识别批大小；`0` 自动选择 GPU 12、CPU 6 |
| `LABEL_CONTROLLERS_OCR_USE_ANGLE_CLS` | `false` | 是否启用 RapidOCR 180° 角度分类；默认仅识别正向 UI 文本 |
| `LABEL_CONTROLLERS_CORS_ORIGINS` | 空 | 逗号分隔的允许来源 |
| `LABEL_CONTROLLERS_LOG_LEVEL` | `info` | 日志等级 |
| `LABEL_CONTROLLERS_EXPOSE_DOCS` | `true` | 是否开放 `/docs` |
| `LABEL_CONTROLLERS_DESKTOP_PORT` | `8022` | 原生 Windows Desktop Adapter 端口 |
| `LABEL_CONTROLLERS_DESKTOP_ALLOW_INPUT` | `false` | 是否允许鼠标注入 |
| `LABEL_CONTROLLERS_DESKTOP_ALLOW_ACTIVATE` | `false` | 是否允许自动激活目标窗口 |
| `LABEL_CONTROLLERS_MCP_DESKTOP_URL` | `http://127.0.0.1:8022` | MCP 连接的 Desktop Adapter 地址 |
| `LABEL_CONTROLLERS_MCP_DESKTOP_TOKEN` | 空 | MCP 专用 Desktop token；不设置时回退 Desktop/API token |
| `LABEL_CONTROLLERS_MCP_TOKEN_FILE` | 空 | 仅包含 Desktop token 的文件路径，便于 MCP 客户端安全加载 |
| `LABEL_CONTROLLERS_MCP_TIMEOUT_SECONDS` | `180` | MCP 分析请求读超时 |
| `LABEL_CONTROLLERS_MCP_LOG_LEVEL` | `info` | MCP stderr 日志级别 |

## 架构

```text
multipart screenshot
        │
        ├── Pillow 解码 / RGB 数组复用 / PNG 规范化 / 大小检查
        ├── OmniParser v2 YOLO11m → icon boxes
        ├── RapidOCR (PP-OCR ONNX) → text boxes
        ├── IoU/containment 去重
        ├── 图标—文字行/列/包含关系融合
        ├── 语义角色推断 + 安全点击点
        └── Pillow 标注渲染
                 │
       JSON / PNG response
```

源码结构：

```text
src/label_controllers/
├── api.py          # FastAPI 路由、鉴权、错误格式
├── accelerator.py  # CPU / CUDA / DirectML 设备解析
├── engine.py       # 常驻模型、图片校验、分析编排
├── detectors.py    # YOLO / RapidOCR 封装
├── contracts.py    # 公共响应字段契约
├── tool.py         # OpenAI / Anthropic 工具客户端与 CLI
├── fusion.py       # 控件与文字融合
├── annotation.py   # 标注图渲染
├── config.py       # 环境变量配置
├── desktop/        # 原生 Windows 窗口截图、坐标转换与鼠标输入
└── cli.py          # 本地图片命令行
```

## 本地开发

Python 3.10–3.12，模型权重不会提交到 Git：

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install --no-deps -e .
python scripts/download_model.py
uvicorn label_controllers.api:app --host 0.0.0.0 --port 8000
```

Windows DirectML 环境使用独立虚拟环境：

```powershell
.\scripts\setup-windows-directml.ps1
.\scripts\run-windows-directml.ps1
```

命令行方式：

```bash
label-controllers analyze --input screenshot.png --output-dir artifacts/cli --include controls,texts,metadata
```

## 性能基线（v0.2.0，OCR batch 6）

以下历史基线使用 OCR 识别批大小 6。在 12 张 2558×1377 Arknights 截图上，每张执行 3 轮（CPU 与 DirectML 各 36 次请求）：

| 指标 | CPU | RX 7800 XT DirectML | 变化 |
|---|---:|---:|---:|
| 端到端平均耗时 | 1.702 s | 0.881 s | 1.93× 加速，降低 48.2% |
| YOLO 平均耗时 | 0.419 s | 0.073 s | 5.76× 加速 |
| OCR 平均耗时 | 1.005 s | 0.536 s | 1.88× 加速 |
| 可点击控件输出 | 449 | 449 | 449/449 匹配 |
| OCR 文本块 | 429 | 429 | 429/429 匹配 |
| 缺失 / 多余控件 | — | — | 0 / 0 |

GPU 默认使用 OCR 识别批大小 12，CPU 保持 6。批大小 12 在 12 张测试图上把 DirectML
OCR 阶段从 0.550 s 降至 0.478 s，端到端从 0.901 s 降至 0.834 s；但由于 RapidOCR 1.2.3
按批内最大宽高比填充，部分文本结果会与批大小 6 不同。需要完全一致的输出时设置
`LABEL_CONTROLLERS_OCR_REC_BATCH_NUM=6`。详细结果见 [docs/BENCHMARKS.md](docs/BENCHMARKS.md)。

上表使用 batch 6，因此控件与 OCR 框保持 100% 对齐。GPU 默认改为 batch 12 后属于
速度/输出一致性的可选折中，不再是逐字节一致；对比数据和退回 batch 6 的方法见
[docs/BENCHMARKS.md](docs/BENCHMARKS.md)。这些结果衡量后端一致性，不等同于外部人工
标注数据集的绝对精度。

### OCR 预处理优化（v0.5.0）

在同一 12 张 2558×1377 截图、每配置 36 次请求的 A/B 重跑中，默认关闭角度分类并复用
Pillow 已解码的 RGB 数组后：

| 后端 | 优化前 | 优化后 | 变化 |
|---|---:|---:|---:|
| RX 7800 XT DirectML / batch 12 | 0.7625 s | **0.6056 s** | **降低 20.6%** |
| CPU / batch 6 | 1.5644 s | **1.4956 s** | **降低 4.4%** |

DirectML 的 OCR 阶段从 0.4259 s 降至 0.2630 s（降低 38.3%）。两种后端上的控件、融合元素
和 OCR 文本块均为 0 缺失、0 多余、F1 1.0。若数据包含倒置文本，可设置
`LABEL_CONTROLLERS_OCR_USE_ANGLE_CLS=true` 恢复角度分类。完整结果和复现命令见
[docs/BENCHMARKS.md](docs/BENCHMARKS.md)。

## 资源说明

CPU 镜像首次构建会下载 PyTorch CPU wheel、OCR 依赖和约 40 MB 的图标模型。
CUDA 镜像的 PyTorch 基础层压缩下载约 3.2 GB、解压后约 9.4 GB，建议预留 20 GB 磁盘；
DirectML 使用原生 Windows Python。
容器启动后模型常驻，单次推理不再加载模型。高并发场景建议在反向代理层排队，或启动
多个副本并对每个副本配置足够内存。

## 安全与数据

- 建议设置长随机 `LABEL_CONTROLLERS_API_KEY`，并通过 HTTPS 反向代理对外提供服务。
- 服务不主动持久化上传图片；请求处理完成后临时文件会被删除。
- `artifacts/`、`tests/`、模型权重、本地 `.env`、密钥证书等均已排除在 Git 之外。
- 发现安全问题请按 [SECURITY.md](SECURITY.md) 私下报告，不要提交公开 Issue。

## Docker 运维

```bash
docker compose logs -f api
docker compose restart api
docker compose down
docker compose up -d --build
```

发布到 GitHub Container Registry 的工作流位于 `.github/workflows/docker-publish.yml`。

## License

[MIT](LICENSE)