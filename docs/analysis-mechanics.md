# 内容分析机制详解

本文档说明 `scripts/analyze_videos.py` + `scripts/video_analyzer.py` 的工作流程，便于排查"结果为空""标签解析不出来""大面积失败"等问题。

## 一、整体流程

```
视频目录
  ├─ 枚举媒体文件（.mp4/.webm/.avi/.mov/.mkv/.flv，--include-images 时加 .jpg/.png/.webp）
  ├─ extract_title() 解析文件名 → 视频ID / 标题 / 发布时间 / 作品链接
  ├─ 按输出目录已有 <视频ID>.csv 过滤（断点续传）
  ├─ asyncio.Semaphore(--concurrent) 控制并发
  │    └─ VideoAnalyzer.analyze() → 多模态模型 → parse_analysis_result()
  ├─ 每个视频写一份 <视频ID>.csv（UTF-8 BOM）
  └─ generate_summary() → _汇总_<目录名>.csv + .xlsx
```

**每个视频单独落一份 CSV** 是断点续传的基础：失败的视频不写文件，重跑时自动重试；成功的直接跳过。汇总文件以 `_` 开头，扫描时被排除，不会被当成分析结果。

## 二、两种模型后端

| 项 | `qwen` | `gemini` |
| --- | --- | --- |
| 通路 | DashScope SDK `MultiModalConversation.call()` | 兼容 `v1beta/models/{model}:generateContent` 的网关 |
| 传输方式 | `file://<绝对路径>`，SDK 内部上传 | 整个文件读入内存 → Base64 → `inlineData` 内联 |
| 体积限制 | 无 Base64 限制，大文件友好 | 建议单文件 **< 20MB** |
| 默认模型 | `qwen-vl-max` | `gemini-3-flash-preview` |
| Key 来源 | 配置项 `qwen_api_key` / 环境变量 `DASHSCOPE_API_KEY` | 配置项 `gemini_api_key` / 环境变量 `GEMINI_API_KEY` |
| 依赖 | `pip install dashscope` | 仅 `httpx` |
| 同步/异步 | SDK 同步，用 `asyncio.to_thread` 包装 | `httpx.AsyncClient`，超时 300 秒 |

因为默认下载策略是 `--quality lowest`，落盘文件体积通常很小，`gemini` 后端的 20MB 限制一般不会触碰；若切到 `--quality highest` 后出现体积超限，改用 `qwen` 后端。

### qwen 的重试策略

```
status_code == 200            → 直接返回
status_code == 429 或 >= 500  → 退避 2^attempt 秒后重试，最多 3 次
其他 4xx                      → 立即返回错误，不重试
```

4xx 不重试是有意的：`400 Access denied`、`401` 这类是 Key / 权限 / 余额问题，重试只会浪费时间和配额。

### `file://` 路径格式

```python
file_uri = f"file://{Path(media_path).absolute().as_posix()}"
```

Windows 下 `as_posix()` 会把 `X:\dir\a.mp4` 转成 `X:/dir/a.mp4`，得到 `file://X:/dir/a.mp4`。**必须是绝对路径**，相对路径 SDK 无法解析。

## 三、提示词与标签解析

### 提示词结构

`video_analyzer.py` 的 `PROMPT_TEXT` 分三段：

1. **分类维度与详细说明**：六个维度，每个枚举值都给出判定依据；
2. **输出格式要求**：先输出 `【详细分析】` 六点逐项说明，再输出标签汇总区块；
3. **标签汇总模板**：固定用 `===标签汇总=== ... ===标签结束===` 包裹。

详细分析段落的作用是**让模型先推理再给结论**，直接要标签会明显降低准确率。完整回答保存在 `原始分析` 列，便于人工复核模型的判断依据。

### 解析逻辑

```python
block = re.search(r"===标签汇总===(.*?)===标签结束===", text, re.DOTALL)
```

取出区块后逐行匹配六个字段名，用 `re.sub(r"^[^:：]+[:：]\s*", "", line)` 剥掉字段名前缀（兼容中英文冒号）。

**解析失败的表现**：六个标签字段全为空，但 `原始分析` 有内容。常见原因：

- 模型没按格式输出（换了模型、提示词被截断）；
- 模型把 `===标签汇总===` 写成了 `**===标签汇总===**` 之类的变体；
- 回答被 token 上限截断，`===标签结束===` 缺失。

此时看 `原始分析` 列就能定位。换更强的模型，或在提示词末尾再强调一次格式要求。

### 自定义维度

```python
from video_analyzer import VideoAnalyzer

analyzer = VideoAnalyzer(
    api_key="<YOUR_API_KEY>",
    model_provider="qwen",
    prompt=MY_PROMPT,
)
```

改维度需要同步改三处：`PROMPT_TEXT`（提示词）、`TAG_FIELDS`（要解析的字段名）、`analyze_videos.py` 的 `FIELDNAMES` 与 `COLUMN_WIDTHS`（CSV / Excel 列）。

## 四、文件名解析

`extract_title()` 按顺序尝试五种格式，**先命中先返回**：

| 顺序 | 正则 / 规则 | 示例 | 产出 |
| --- | --- | --- | --- |
| 1 | 含 `-----` 分隔符 | `<19位ID>-----标题-----2026-03-01 12.00.00` | ID + 标题 + 时间 + 抖音链接 |
| 2 | `^(\d{19})(?:-(.*?))?(?:-(\d{2}))?$` | `<19位ID>-标题` | ID + 标题 + 抖音链接 |
| 3 | `^(.+)_(BV[a-zA-Z0-9]{10,12})$` | `标题_BV1xx411c7mD` | BV 号 + 标题 + B 站链接 |
| 4 | `^(?:(.+)_)?([a-z0-9]{15,20})$` | `<photoId>` | photoId + 快手链接 |
| 5 | 兜底 | `随便一个名字 #话题1` | 哈希 ID + 去标签标题，链接为空 |

处理细节：

- 入口先 `re.sub(r"_\d+$", "", name)` 去掉图集的 `_1` / `_2` 后缀；
- 格式 1 中 `【图集】` 前缀 → `内容类型 = 图集`；
- 格式 2 中两位数字后缀（`-01`）→ `内容类型 = 图集`；
- 格式 5 的 ID 由 `abs(hash(name)) % 10**10` 生成。**同一文件名在不同 Python 进程里哈希值不同**（`PYTHONHASHSEED` 随机化），因此兜底分支的断点续传不可靠，会重复分析。要稳定就把文件名改成已知格式。

`safe_name()` 会把 Windows 非法字符 `<>:"/\|?*` 替换成 `_`，避免 ID 带特殊字符时写文件失败。

## 五、输出文件

### 单条 CSV

编码 `utf-8-sig`（带 BOM），Excel 双击打开不乱码。列顺序固定为 `FIELDNAMES`。

### 汇总 CSV + Excel

`generate_summary()` 读取目录下所有非 `_` 开头的 CSV，按 `视频ID` 排序后合并。Excel 部分：

- 表头：`4472C4` 蓝底白字加粗居中；
- 列宽：按 `COLUMN_WIDTHS` 设置（`原始分析` 60 字符最宽）；
- 单元格：顶端对齐 + 自动换行；
- 冻结首行（`sheet.freeze_panes = "A2"`）；
- sheet 名取目录名前 31 字符（Excel 上限）。

`openpyxl` 缺失时只跳过 Excel，CSV 照常生成。补装后用 `--summary-only` 重新生成即可，不用重跑分析。

汇总后终端打印内容主题分类 Top 5，用于快速判断这批视频的内容结构。

## 六、并发与配额

`--concurrent` 直接控制同时在飞的模型请求数，默认 20。注意：

- **这是模型 API 的 QPS 压力来源**，不是本地 CPU 负载。429 频繁就往下调；
- `gemini` 后端每个请求要把整个视频读进内存做 Base64，**并发 × 文件大小 = 内存占用**，Base64 本身还有 4/3 膨胀。大文件时建议降到 5 以内或换 `qwen`；
- `qwen` 后端由 SDK 上传，内存压力小，可以开高。

## 七、排查清单

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 六个标签全空，`原始分析` 有内容 | 模型没按格式输出 | 看 `原始分析` 确认；换模型或强化提示词 |
| `Error: API Key 未配置` | Key 没传到 | 检查配置文件字段名 / `--api-key` / 环境变量 |
| `Error: 未安装 dashscope` | 缺依赖 | `pip install dashscope` |
| `400 Access denied` | Key 无效 / 模型未开通 / 欠费 | 控制台确认，与脚本无关 |
| `429` 退避后仍失败 | 并发过高 | 降 `--concurrent` |
| 返回内容与画面无关 | HEVC 解码失败 | 删除原文件后 `--codec h264` 重新下载 |
| `gemini` 后端大文件必失败 | Base64 体积超限 | 换 `--provider qwen`，或保持 `--quality lowest` |
| 每次重跑都重复分析同一批 | 文件名走了哈希兜底分支 | 改成已知命名格式 |
| 结果目录找不到 | 默认输出在 `<视频目录父目录>/result/<目录名>_result` | 用 `--output` 显式指定 |
