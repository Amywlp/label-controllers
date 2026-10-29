# 标准 Tool Calling 集成

Label Controllers 提供一个与 OpenAI Chat Completions / Responses 兼容的函数工具，同时提供
Anthropic Messages tool 格式和可直接执行的 Python 客户端。

```text
Tool name: analyze_ui_screenshot
```

LLM 负责选择输出字段；截图由工具执行器提供，不需要模型生成或传输图片 Base64。

## 发现工具定义

运行中的 API 提供：

```http
GET /api/v1/tools
```

响应包含：

```json
{
  "schema_version": "1.0",
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "analyze_ui_screenshot",
        "description": "...",
        "parameters": {
          "type": "object",
          "properties": {
            "include": {"type": "array"},
            "annotate": {"type": "boolean"},
            "icon_confidence": {"type": "number"},
            "ocr_confidence": {"type": "number"}
          },
          "additionalProperties": false
        },
        "strict": false
      }
    }
  ],
  "anthropic_tools": ["..."]
}
```

该端点和 `/api/v1/capabilities` 不需要鉴权；实际分析请求仍遵循 API Key 配置。

## 工具参数

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `include` | string array | `controls,texts,metadata` | `controls`、`elements`、`texts`、`metadata`、`raw_icons`、`raw_ocr`、`annotated_image`、`all` |
| `annotate` | boolean | `false` | 将 Base64 标注 PNG 放入结果 |
| `icon_confidence` | number | 服务默认 `0.18` | YOLO 阈值 |
| `ocr_confidence` | number | 服务默认 `0.40` | OCR 阈值 |

模型不应传入图片 Base64。工具执行器在调用时绑定当前截图：

```python
from label_controllers.tool import LabelControllersTool

tool = LabelControllersTool(
    base_url="http://127.0.0.1:8000",
    screenshot_provider=lambda: capture_current_screen_bytes(),
)

result = tool.execute_tool_call({"include": ["controls", "texts", "metadata"]})
```

`screenshot_provider` 应返回 PNG/JPEG/WEBP/BMP/TIFF 字节。也可以直接分析本地文件：

```python
result = tool.analyze("screenshot.png", include=["controls", "texts"])
```

如果集成层只有 Base64 图片，可使用：

```python
result = tool.analyze_base64(base64_string, include=["controls", "texts"])
```

该方法接受裸 Base64 或 `data:image/png;base64,...` 数据 URL；模型 tool schema 本身仍不暴露图片字节参数。

`execute_tool_call()` 会：

1. 校验模型返回的 JSON 参数；
2. 调用绑定截图 provider；
3. 向 `/api/v1/analyze` 发送标准 multipart 请求；
4. 返回原始结构化分析结果；
5. 将 API 错误转换为 `ToolCallError`。

## OpenAI 示例

安装可选 SDK：

```bash
pip install openai
```

仓库中的完整示例：

```bash
python examples/openai_tool_call.py screenshot.png
```

核心调用：

```python
tool = LabelControllersTool(
    base_url="http://127.0.0.1:8000",
    screenshot_provider=lambda: screenshot_path.read_bytes(),
)

first = client.chat.completions.create(
    model="gpt-4.1-mini",
    messages=[{"role": "user", "content": "Inspect the current UI."}],
    tools=[tool.definition],
    tool_choice={
        "type": "function",
        "function": {"name": "analyze_ui_screenshot"},
    },
)

for call in first.choices[0].message.tool_calls or []:
    result = tool.execute_tool_call(call.function.arguments)
    tool_message = {
        "role": "tool",
        "tool_call_id": call.id,
        "content": json.dumps(result, ensure_ascii=False),
    }
```

Anthropic Messages 格式通过 `tool.anthropic_definition` 获取。

## 命令行工具

重新以可编辑模式安装项目后会注册 `label-controllers-tool`：

```bash
pip install --no-deps -e .
```

输出标准 OpenAI 工具 JSON：

```bash
label-controllers-tool --schema
```

直接通过正在运行的 API 分析图片：

```bash
LABEL_CONTROLLERS_API_KEY="<key>" \
label-controllers-tool \
  --base-url http://127.0.0.1:8000 \
  --image screenshot.png \
  --include controls,texts,metadata \
  --output result.json
```

未安装 console script 时可直接运行：

```bash
python -m label_controllers.tool --schema
python -m label_controllers.tool --image screenshot.png
```

API Key 从 `LABEL_CONTROLLERS_API_KEY` 读取，不出现在命令行参数中。

## Python 错误处理

```python
from label_controllers.tool import LabelControllersTool, ToolCallError

try:
    result = tool.execute_tool_call(arguments)
except ToolCallError as exc:
    print(exc.to_dict())
```

错误结构：

```json
{
  "ok": false,
  "error": {
    "code": "invalid_arguments",
    "message": "..."
  }
}
```

常见代码：

| code | 含义 |
|---|---|
| `image_required` | 未配置截图 provider，且未显式传入图片 |
| `invalid_image` | 图片为空、Base64 非法或文件无法读取 |
| `invalid_arguments` | 模型参数不符合工具 schema |
| `connection_error` | 无法连接 Label Controllers |
| `unauthorized` | API Key 缺失或错误 |
| `invalid_include` | 请求的返回字段不合法 |
| `analysis_failed` | 服务端模型推理失败 |

## 安全边界

- 截图由受信任的工具执行器提供，避免让模型有机会生成任意图片字节。
- API Key 仅从环境变量读取，不写入 tool schema 或结果。
- `annotated_image` 体积较大，默认不返回；按需设置 `annotate=true` 或 `include=annotated_image`。
- 远端部署应使用 HTTPS；工具调用端应只连接受信任的 Label Controllers 地址。
