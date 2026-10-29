# Label Controllers

[![CI](https://github.com/OWNER/label-controllers/actions/workflows/ci.yml/badge.svg)](https://github.com/OWNER/label-controllers/actions/workflows/ci.yml)
[![Container](https://github.com/OWNER/label-controllers/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/OWNER/label-controllers/actions/workflows/docker-publish.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Label Controllers 是一个面向 Computer Use、UI 自动化和 GUI 测试的截图控件识别服务。
上传一张界面截图，服务会同时运行 OmniParser v2 YOLO11m 图标检测与 RapidOCR 文字识别，
再把图标和文字融合成可点击控件、OCR 文本块和带标注的图像。

仓库建议名称：`label-controllers`（GitHub 仓库名不能包含空格；显示名称可写为 **Label Controllers**）。

## 功能

- `POST /api/v1/analyze`：上传截图，按需返回不同字段。
- 可点击控件 JSON：文字、语义角色、置信度、矩形、中心点、安全点击点。
- 全量元素 JSON：包含控件、纯文字区域和不可点击容器。
- 控件文字 / OCR 文本块：独立文字、置信度和坐标。
- 标注 PNG：可通过二进制响应下载，也可作为 Base64 放入 JSON。
- 原始 YOLO 与 OCR 结果：适合调试和二次融合。
- 模型常驻内存，同一容器内无需为每次请求重新加载。
- Docker 镜像在构建阶段自动下载并校验 `icon_detect.pt`。
- 可选 API Key 鉴权、上传大小限制、像素限制、CORS、OpenAPI 文档。

## 快速部署

需要 Docker 24+ 与 Docker Compose v2。

```bash
git clone https://github.com/OWNER/label-controllers.git
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
| `POST` | `/api/v1/analyze` | 上传截图并分析 |
| `GET` | `/docs` | Swagger UI |
| `GET` | `/openapi.json` | OpenAPI 3 文档 |

完整字段定义和响应示例见 [docs/API.md](docs/API.md)。

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
| `LABEL_CONTROLLERS_CORS_ORIGINS` | 空 | 逗号分隔的允许来源 |
| `LABEL_CONTROLLERS_LOG_LEVEL` | `info` | 日志等级 |
| `LABEL_CONTROLLERS_EXPOSE_DOCS` | `true` | 是否开放 `/docs` |

## 架构

```text
multipart screenshot
        │
        ├── Pillow 解码 / PNG 规范化 / 大小检查
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
├── engine.py       # 常驻模型、图片校验、分析编排
├── detectors.py    # YOLO / RapidOCR 封装
├── fusion.py       # 控件与文字融合
├── annotation.py   # 标注图渲染
├── config.py       # 环境变量配置
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

命令行方式：

```bash
label-controllers analyze --input screenshot.png --output-dir artifacts/cli --include controls,texts,metadata
```

## 资源说明

这是 CPU 推理镜像。首次构建会下载 PyTorch CPU wheel、OCR 依赖和约 40 MB 的图标模型，
构建时间和镜像体积都会明显高于普通 Web 服务。容器启动后模型常驻，单次推理不再加载模型。
高并发场景建议在反向代理层排队，或启动多个副本并对每个副本配置足够内存。

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