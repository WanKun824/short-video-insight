# short-video-insight

> 短视频批量下载 + AI 内容理解分析一体化工具链
> 支持 **抖音 / TikTok / 快手**，默认挑**最低清晰度**下载（省流量、省存储、分析上传更快），
> 下载完成后可直接调用多模态大模型输出结构化内容标签（CSV / Excel）。

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)

---

## 目录

- [它能做什么](#它能做什么)
- [特性](#特性)
- [快速开始](#快速开始)
- [安装](#安装)
- [配置](#配置)
- [使用](#使用)
  - [1. 批量下载](#1-批量下载)
  - [2. AI 内容分析](#2-ai-内容分析)
  - [3. 单平台直接调用](#3-单平台直接调用)
- [参数速查](#参数速查)
- [输出结构](#输出结构)
- [分析维度](#分析维度)
- [工作原理](#工作原理)
- [FAQ](#faq)
- [隐私与合规](#隐私与合规)
- [License](#license)

---

## 它能做什么

把一份"作品链接清单"丢进去，拿到两样东西：

1. **视频文件** —— 按平台分目录落盘，支持断点续传、并发下载、多档清晰度选择；
2. **内容分析表** —— 每条视频一份 CSV，外加一份带格式的汇总 Excel，包含六个维度的结构化标签与模型的完整推理过程。

典型用途：内容选题调研、竞品内容结构拆解、爆款要素统计、素材库归档。

```
作品链接清单.md
      │
      ├── download_videos.py ──► downloads/douyin/*.mp4
      │                          downloads/kuaishou/*.mp4
      │
      └── analyze_videos.py ───► result/douyin_result/<视频ID>.csv
                                 result/douyin_result/_汇总_douyin.csv
                                 result/douyin_result/_汇总_douyin.xlsx
```

---

## 特性

**下载侧**

- **免 cookie**：抖音默认走 `iesdouyin` 分享站免签接口，快手走移动端页面内嵌状态，两者都**不需要登录态、不需要签名算法**；
- **默认最低清晰度**：`--quality lowest` 为默认值，批量几百条时体积可比原画小 5~10 倍；需要原画传 `--quality highest`；
- **编码偏好**：`--codec h264` 规避部分多模态模型无法解码 HEVC/H.265 的问题；
- **总并发自动均分**：`--concurrency 20` 表示总并发，按实际出现的平台数均分（双平台各 10，三平台 7/7/6），每个平台独立子进程，互不干扰；
- **断点续传**：已下载的作品自动跳过，中断后重跑即可；
- **多档兜底**：首选档 CDN 失效时自动降级到次选档，不会整条作品失败。

**分析侧**

- **双后端**：阿里云百炼 Qwen-VL（`file://` 直传，不受 Base64 体积限制）/ Gemini 原生 `generateContent`（Base64 内联）；
- **逐条落盘**：每个视频单独一份 CSV，失败的下次重跑自动重试，成功的直接跳过；
- **可复核**：模型的完整推理过程保存在 `原始分析` 列，标签不是黑箱；
- **带格式汇总**：自动生成蓝底表头、冻结首行、自动换行的 Excel；
- **提示词可替换**：六个维度及其判定依据都在 `PROMPT_TEXT` 里，换领域只改提示词 + 字段名。

---

## 快速开始

```bash
# 1. 克隆与安装
git clone https://github.com/WanKun824/short-video-insight.git
cd short-video-insight
pip install -r requirements.txt

# 2. 准备链接清单（一行一个链接，支持分享文案直接粘贴）
#    文件名可自定义，用 --urls 指定
cat > 作品链接清单.md <<'EOF'
https://www.douyin.com/video/7xxxxxxxxxxxxxxxxxx
https://v.kuaishou.com/xxxxxx
EOF

# 3. 下载（默认最低清晰度，总并发 20）
python scripts/download_videos.py --urls 作品链接清单.md -o downloads

# 4. 配置模型 Key 后分析
cp config.example.json batch_config.json
# 编辑 batch_config.json 填入 Key，或直接用环境变量
python scripts/analyze_videos.py --dir downloads/douyin --config batch_config.json
```

---

## 安装

**环境要求**：Python 3.10+

```bash
pip install -r requirements.txt
```

依赖说明：

| 包 | 用途 | 是否必需 |
| --- | --- | --- |
| `httpx` | 下载与 Gemini 后端请求 | 必需 |
| `dashscope` | Qwen-VL 后端 | 用 qwen 后端时必需 |
| `openpyxl` | 汇总导出 Excel | 可选，缺失时只生成 CSV |

**可选：抖音 `api` 模式与 TikTok**

本项目的默认通路（抖音 `share` + 快手）零依赖、无需 cookie。若需要抖音 Web 正式接口或 TikTok，需额外配合 [TikTokDownloader](https://github.com/JoeanAmier/TikTokDownloader) 仓库：

```bash
python scripts/download_videos.py --urls urls.txt -o downloads \
    --project-root /path/to/TikTokDownloader
# 或设置环境变量
export TIKTOK_DOWNLOADER_ROOT=/path/to/TikTokDownloader
```

---

## 配置

复制模板后填写：

```bash
cp config.example.json batch_config.json
```

```json
{
  "model_provider": "qwen",
  "qwen_api_key": "<YOUR_DASHSCOPE_API_KEY>",
  "gemini_api_key": "<YOUR_GEMINI_API_KEY>",
  "gemini_api_base": "",
  "model_name": "",
  "max_concurrent_analysis": 20,
  "cookie": "",
  "cookie_tiktok": "",
  "proxy": "",
  "proxy_tiktok": ""
}
```

| 字段 | 说明 |
| --- | --- |
| `model_provider` | `qwen` 或 `gemini` |
| `qwen_api_key` | 阿里云百炼 Key，也可用环境变量 `DASHSCOPE_API_KEY` |
| `gemini_api_key` | Gemini Key，也可用环境变量 `GEMINI_API_KEY` |
| `gemini_api_base` | 自建 / 第三方网关根地址，留空走官方；也可用环境变量 `GEMINI_API_BASE` |
| `model_name` | 留空走默认模型（qwen → `qwen-vl-max`，gemini → `gemini-3-flash-preview`） |
| `max_concurrent_analysis` | 分析并发数，被 `--concurrent` 覆盖 |
| `cookie` / `cookie_tiktok` | 仅抖音 `api` 模式 / TikTok 需要；默认通路留空 |
| `proxy` / `proxy_tiktok` | 代理地址，按需填写 |

> **`batch_config.json` 已在 `.gitignore` 中**，不会被误提交。生产环境建议只用环境变量传 Key，不落盘。

---

## 使用

### 1. 批量下载

```bash
# 最常用：默认最低清晰度，总并发 20，按平台自动分目录
python scripts/download_videos.py --urls 作品链接清单.md -o downloads

# 直接传链接，不用清单文件
python scripts/download_videos.py --no-urls \
    --link https://www.douyin.com/video/7xxxxxxxxxxxxxxxxxx \
    --link https://v.kuaishou.com/xxxxxx -o downloads

# 需要原画
python scripts/download_videos.py --urls urls.txt -o downloads --quality highest

# 为送模型分析准备素材：最低清晰度 + 强制 H.264
python scripts/download_videos.py --urls urls.txt -o downloads --codec h264

# 先试跑 20 条，输出打印到当前终端
python scripts/download_videos.py --urls urls.txt -o downloads --limit 20 --foreground

# 只看将要执行的命令，不实际下载
python scripts/download_videos.py --urls urls.txt -o downloads --dry-run

# 只处理某个平台
python scripts/download_videos.py --urls urls.txt -o downloads --platform kuaishou
```

链接清单支持直接粘贴分享文案，脚本会自行提取其中的 URL 与作品 ID。平台归属由链接域名自动判断，无法判断时归入 `--default-platform`（默认抖音）。

### 2. AI 内容分析

```bash
# 分析单个目录
python scripts/analyze_videos.py --dir downloads/douyin

# 同时分析多个目录
python scripts/analyze_videos.py --dir downloads/douyin --dir downloads/kuaishou

# 指定输出目录与并发
python scripts/analyze_videos.py --dir downloads --output result --concurrent 10

# 覆盖配置文件里的后端
python scripts/analyze_videos.py --dir downloads --provider qwen

# 顺便分析图集图片
python scripts/analyze_videos.py --dir downloads/douyin --include-images

# 补装 openpyxl 后，只重新生成汇总，不重跑分析
python scripts/analyze_videos.py --dir downloads/douyin --summary-only
```

### 3. 单平台直接调用

```bash
# 抖音免签通路
python scripts/platform_download.py --platform douyin --urls urls.txt -o downloads/douyin -c 10

# 快手内置实现
python scripts/platform_download.py --platform kuaishou --urls urls.txt -o downloads/kuaishou -c 10

# 抖音 Web 正式接口（需 cookie + TikTokDownloader 仓库）
python scripts/platform_download.py --platform douyin --douyin-mode api --urls urls.txt -o downloads
```

也可以作为库使用：

```python
from pathlib import Path
import sys

sys.path.insert(0, "scripts")
from douyin_share_download import DouyinShareDownload, extract_douyin_ids

ids = extract_douyin_ids(Path("urls.txt").read_text(encoding="utf-8").splitlines())
DouyinShareDownload(
    output=Path("downloads/douyin"),
    concurrency=10,
    quality="lowest",   # 或 "highest"
    codec="any",        # 或 "h264"
).run(ids)
```

---

## 参数速查

### `download_videos.py`（编排入口）

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--urls` | `作品链接清单.md` | 链接来源文件 |
| `--link URL` | – | 直接指定链接，可重复，与 `--urls` 合并 |
| `--no-urls` | 关 | 忽略链接文件，只用 `--link` |
| `--output` / `-o` | `downloads` | 文件保存根目录 |
| `--concurrency` / `-c` | `20` | **总**下载并发，按平台数均分 |
| **`--quality`** | **`lowest`** | **`lowest` 最低清晰度 / `highest` 最高清晰度** |
| `--codec` | `any` | `any` 不限编码 / `h264` 优先 H.264（优先级高于清晰度） |
| `--platform` | 自动 | 限定平台，可重复：`douyin` / `tiktok` / `kuaishou` |
| `--default-platform` | `douyin` | 无法判断平台时的归属 |
| `--douyin-mode` | `share` | `share` 免签接口 / `api` Web 正式接口 |
| `--merge-output` | 关 | 所有平台共用一个输出目录 |
| `--limit` | `0` | 每平台仅处理前 N 个（0 = 全部） |
| `--retry` | `3` | 取数据失败重试次数 |
| `--retry-delay` | `2.0` | 重试等待基数（秒），实际为 `基数 × 第几次` |
| `--api-concurrency` | `4` | 取作品数据的并发（抖音 api / TikTok 生效） |
| `--batch-size` | `50` | 每批处理的作品数 |
| `--no-skip-existing` | 关 | 不跳过已存在文件（默认跳过，即断点续传） |
| `--foreground` | 关 | 子进程输出打到当前终端（默认写平台日志文件） |
| `--dry-run` | 关 | 只打印命令 |
| `--config` | `batch_config.json` | 配置文件路径 |
| `--project-root` | 自动探测 | TikTokDownloader 仓库目录 |

### `analyze_videos.py`

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--dir` | 必填 | 视频目录，可重复指定 |
| `--output` | `<视频目录父目录>/result/<目录名>_result` | 结果输出目录 |
| `--config` | `batch_config.json` | 配置文件路径 |
| `--provider` | 读配置 | `gemini` / `qwen` |
| `--model` | 读配置 | 模型名 |
| `--api-key` | 读配置/环境变量 | 直接传 Key（**不推荐写进 shell 历史**） |
| `--concurrent` | `20` | 最大并发分析数 |
| `--limit` | `0` | 每目录仅分析前 N 个 |
| `--include-images` | 关 | 同时分析 jpg/png/webp |
| `--summary-only` | 关 | 跳过分析，只重新生成汇总 |

---

## 输出结构

```
downloads/
├── douyin/
│   ├── 7xxxxxxxxxxxxxxxxxx-作品标题.mp4
│   └── ...
├── kuaishou/
│   ├── 3xxxxxxxxxxxxxxx.mp4
│   └── ...
├── douyin_download.log          # 平台下载日志（--foreground 时不生成）
└── kuaishou_download.log

result/
└── douyin_result/
    ├── 7xxxxxxxxxxxxxxxxxx.csv  # 每条视频一份，断点续传的依据
    ├── ...
    ├── _汇总_douyin.csv          # 以 _ 开头，扫描时自动排除
    └── _汇总_douyin.xlsx         # 蓝底表头 / 冻结首行 / 自动换行
```

CSV 列（共 13 列，编码 `utf-8-sig`，Excel 双击不乱码）：

`视频ID` `内容类型` `视频标题` `发布时间` `视频链接` `内容主题分类` `内容节奏` `是否有3秒钩子` `3秒钩子类型` `核心叙事框架` `内容视角` `原始分析` `原始文件名`

---

## 分析维度

内置提示词面向**游戏类短视频**，输出六个维度：

| 维度 | 说明 |
| --- | --- |
| 内容主题分类 | 游戏实况与剪辑 / 游戏内资产展示 / 游戏攻略 / 游戏资讯 / 游戏测评 / 游戏宣传 / 创意整活与二创 / 创意剪辑 等 |
| 内容节奏 | 整体剪辑与信息密度的快慢 |
| 是否有 3 秒钩子 | 开场 3 秒内是否存在明确的注意力抓取设计 |
| 3 秒钩子类型 | 钩子的具体形式 |
| 核心叙事框架 | 内容的组织骨架 |
| 内容视角 | 叙述者所处的视角 |

模型被要求**先逐项推理再给结论**，完整推理保存在 `原始分析` 列。直接要标签会明显降低准确率，这是刻意的设计。

**换领域**：只改三处即可——`video_analyzer.py` 的 `PROMPT_TEXT`（提示词）与 `TAG_FIELDS`（字段名），以及 `analyze_videos.py` 的 `FIELDNAMES` / `COLUMN_WIDTHS`（表格列）。

```python
from video_analyzer import VideoAnalyzer

analyzer = VideoAnalyzer(
    api_key="<YOUR_API_KEY>",
    model_provider="qwen",
    prompt=MY_PROMPT,
)
```

---

## 工作原理

深入说明见：

- [`docs/download-mechanics.md`](./docs/download-mechanics.md) —— 清晰度选档机制（含"未知档"陷阱的处理）、并发模型、休眠点、断点续传、调优建议；
- [`docs/analysis-mechanics.md`](./docs/analysis-mechanics.md) —— 分析流程、双后端差异、提示词与标签解析、文件名解析的五种格式、并发与配额、排查清单。

核心要点速览：

**清晰度选档**。平台返回的"默认直链"既不是最高清也不一定是最低清。抖音的完整档位在 `bit_rate[].play_addr`，快手在 `manifest.adaptationSet[].representation[]`。排序键为 `(编码偏好, 宽×高, 码率, data_size)`：`highest` 取原值倒序，`lowest` 对指标**取负**后倒序（等价升序）。

关键细节：部分节点（如抖音 `download_addr`）不返回分辨率字段，缺失值为 `0`。若直接按 `0` 升序，这些"未知档"会被误判成最低清晰度。脚本统一归一化：

```python
UNKNOWN_METRIC = 1 << 40

def quality_metric(value, highest):
    if value:
        return value
    return 0 if highest else UNKNOWN_METRIC
```

两种模式下未知档都排末位，只作兜底。

**并发隔离**。每个平台起独立子进程。因为 TikTokDownloader 的 `Downloader.semaphore` 是类属性，同进程内无法按平台隔离并发。

**断点续传**。抖音按 `<作品ID>` 前缀跳过，快手按 `<photoId>.mp4` 且 > 10 KB 跳过，分析按 `<视频ID>.csv` 跳过。换清晰度重下时**必须先删除旧文件**，否则会被跳过。

---

## FAQ

**Q：为什么默认是最低清晰度？**
省流量、省存储；送多模态模型时上传更快、更不容易触碰接口体积限制；内容理解类分析（主题、节奏、钩子、叙事）对画质不敏感。需要原画传 `--quality highest`。

**Q：换了 `--quality` 重新下载，文件没变？**
被断点续传跳过了。先删除旧文件，或加 `--no-skip-existing`。

**Q：日志写的是 720p，`ffprobe` 看是 540p？**
日志打印的是**选中的首选档**。首选档 CDN 失效时会自动降级到次选档，实际落盘可能不同。核对命令：

```bash
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,codec_name -of csv=p=0 <文件>.mp4
```

**Q：抖音报"响应内容不是有效的 JSON 数据，请尝试更新 Cookie"？**
这是 `--douyin-mode api` 的风控拦截，需要**含完整 UIFID** 的 cookie（只有 `UIFID_TEMP` 会被拦）。改用默认的 `--douyin-mode share` 免签通路即可。

**Q：分析结果六个标签全空，但 `原始分析` 有内容？**
模型没按 `===标签汇总=== ... ===标签结束===` 格式输出。看 `原始分析` 定位原因，换更强的模型或强化提示词。

**Q：分析返回的内容和画面完全无关？**
大概率是 HEVC/H.265 解码失败。删除原文件后用 `--codec h264` 重新下载。

**Q：`400 Access denied`？**
Key 无效 / 模型未开通 / 账户欠费，去模型控制台确认，与本项目无关。4xx 刻意不重试，避免浪费配额。

**Q：`gemini` 后端大文件必失败？**
Gemini 通路走 Base64 内联，建议单文件 < 20MB。换 `--provider qwen`（`file://` 直传无此限制），或保持默认的 `--quality lowest`。

**Q：`429` 频繁？**
降 `--concurrent`。注意 `gemini` 后端的内存占用 ≈ 并发 × 文件大小 × 4/3，大文件时建议降到 5 以内。

**Q：每次重跑都重复分析同一批？**
文件名走了哈希兜底分支，而 `PYTHONHASHSEED` 在不同进程里不同。把文件名改成已知格式（见 `docs/analysis-mechanics.md` 第四节）即可。

**Q：没有 Excel 输出？**
缺 `openpyxl`。`pip install openpyxl` 后用 `--summary-only` 重新生成，不用重跑分析。

---

## 隐私与合规

- 仓库内**不包含任何 cookie、API Key、本机路径或个人数据**；`batch_config.json`、`downloads/`、`result/`、链接清单等均已写入 `.gitignore`；
- 推荐用环境变量传递 Key（`DASHSCOPE_API_KEY` / `GEMINI_API_KEY`），避免落盘；
- 本项目仅用于**个人学习、内容研究与数据分析**。请遵守各平台的用户协议与 `robots.txt`，尊重创作者版权，不要将下载内容用于未经授权的二次分发或商业用途；
- 请合理设置并发，避免对目标站点造成压力。使用本项目产生的一切后果由使用者自行承担。

---

## License

[MIT](./LICENSE)
