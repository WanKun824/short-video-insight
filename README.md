# short-video-insight

> 短视频批量下载 + AI 内容理解分析一体化工具链
> 支持 **抖音 / TikTok / 快手 / B站**，默认挑**最低清晰度**下载（省流量、省存储、分析上传更快），
> 下载完成后可直接调用多模态大模型输出结构化内容标签（CSV / Excel）。
>
> 另含一套完整的**内容分类与评测流水线**：24 类标签、业务口径评测、
> dev/test 多种子验证。见 [内容分类与评测](#内容分类与评测)。

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)

**核心文档**　[实验日志](docs/experiment-log.md)（79% → 94.6% 的完整过程，含 6 个失败尝试）· [分类技术说明](docs/classification-pipeline.md) · [下载机制](docs/download-mechanics.md) · [分析机制](docs/analysis-mechanics.md)

---

## 目录

- [它能做什么](#它能做什么)
- [特性](#特性)
- [快速开始](#快速开始)
  - [链 A：下载 + 内容分析](#链-a下载--内容分析)
  - [链 B：内容分类评测](#链-b内容分类评测)
- [安装](#安装)
- [配置](#配置)
- [使用](#使用)
  - [1. 批量下载](#1-批量下载)
  - [2. AI 内容分析](#2-ai-内容分析)
  - [3. 单平台直接调用](#3-单平台直接调用)
- [**内容分类与评测**](#内容分类与评测)
  - [从零跑一遍](#从零跑一遍)
  - [24 类标签](#24-类标签)
  - [业务口径评测](#业务口径评测)
  - [当前结果](#当前结果)
- [**实验历程**](#实验历程)
- [参数速查](#参数速查)
- [输出结构](#输出结构)
- [分析维度](#分析维度)
- [工作原理](#工作原理)
- [FAQ](#faq)
- [隐私与合规](#隐私与合规)
- [License](#license)

---

## 它能做什么

把一份"作品链接清单"丢进去，拿到视频文件和内容标签。仓库里有**两条可独立使用的链路**：

| | 链 A：内容分析 | 链 B：内容分类评测 |
|---|---|---|
| 入口 | `scripts/analyze_videos.py` | `classification/classify.py` |
| 标签体系 | 六个维度的**开放式**标签（题材/形式/情绪…） | **固定 24 类**内容类型 |
| 擅长 | 快速摸清一批新内容长什么样 | 在既定口径下做到可控、可复现、可上生产 |
| 有无评测 | 无 | 有评测集、业务口径、防过拟合流程、逐条错误分析 |
| 适用 | 内容选题调研、竞品拆解 | 内容分发打标、素材库分类、模型能力评估 |

两条链路共用同一套下载器。

**链 A：内容分析**

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

**链 B：内容分类评测**

```
Excel（人工标注作为真值）
      │
      ├── prepare_data_v4.py ──► ground_truth.jsonl（分层抽样）
      ├── download_multi.py  ──► videos_v4/*.mp4
      │
      ├── text_prior.py ──────► 文本模型 Top-3 候选
      ├── classify.py ────────► predictions.jsonl（多模态 + 检索增强）
      │
      ├── route_v3.py ────────► 按工作表分治（dev 选择 / test 验证）
      ├── postprocess_v3.py ──► 话题 + 作者规则层
      │
      └── build_v*_report.py ─► 评测报告（MD + HTML，含逐条错误分析）
```

典型用途：内容选题调研、竞品内容结构拆解、爆款要素统计、素材库归档、
内容分发打标、分类模型能力评估。

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

**分类流水线侧**

- **24 类标签**：游戏内容 / 真人内容 / 二次创作三个域的细分标签，可从 16 类扩展到 24 类；
- **检索增强**：每条视频送模型前附带话题先验、同作者历史、文本模型 Top-3、相似作品示例（全部 leave-one-out）；
- **业务口径评测**：承认标注本身有分歧，把「可互换的标签对」两种判法都算对，并标明每组豁免对的来源；
- **防过拟合**：所有规则与提示词改动都走 dev/test 划 + 多随机种子验证，**下界不为负才采纳**；
- **两类准确率分开报**：可外推的模型水平 vs 仅本批有效的交付水平，不混用。

---

## 快速开始

```bash
# 1. 克隆与安装
git clone https://github.com/WanKun824/short-video-insight.git
cd short-video-insight
pip install -r requirements.txt

# 2. 配置模型 Key
cp config.example.json batch_config.json
# 编辑 batch_config.json 填入 Key，或直接用环境变量
```

### 链 A：下载 + 内容分析

```bash
# 准备链接清单（一行一个链接，支持分享文案直接粘贴）
cat > 作品链接清单.md <<'EOF'
https://www.douyin.com/video/7xxxxxxxxxxxxxxxxxx
https://v.kuaishou.com/xxxxxx
EOF

python scripts/download_videos.py --urls 作品链接清单.md -o downloads
python scripts/analyze_videos.py --dir downloads/douyin --config batch_config.json
```

### 链 B：内容分类评测

```bash
cd classification

# 从已有的预测复现评测报告（不调模型，不需要视频）
python build_v4_report.py --preds out/v4_final_predictions.jsonl
python build_v4_compare.py
# 预期：准确率 439/464 = 94.61%

# 从零跑一遍：读 Excel → 下载 → 分类（见 classification/README.md）
```

> 链 B 的仓库里带了**精简后的预测结果**，所以不下载视频也能把评测报告跑出来。
> 要重新分类则需要先下载视频（体积原因未入库）。

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

## 内容分类与评测

`classification/` 是一套完整的内容分类流水线：把短视频打上 **24 类内容类型**标签，
并与人工标注比对评测。覆盖 **4 个平台**（抖音 / 快手 / B站 / 小红书）、
**9 款游戏**、**2500+ 条样本**（其中 910 条进了评测集）。

它和上面的「AI 内容分析」（六个维度的开放式标签）定位不同：
分类流水线要解决的是**在固定标签体系下，做到可控、可复现、可上生产的准确率**，
所以它有评测集、口径定义、防过拟合流程和逐条错误分析。

完整技术说明见 **[docs/classification-pipeline.md](docs/classification-pipeline.md)**。

### 从零跑一遍

```bash
cd classification

# 1. 分类（需要先下载视频，见上方「批量下载」）
python classify.py --prompt-version bili_v11 \
  --game peace,king,eggy,delta \
  --gt data_v4/ground_truth.jsonl \
  --corpus data_v4/ground_truth.jsonl \
  --videos ../videos_v4 \
  --prior data_v4/text_prior.json \
  --media-meta data_v4/media_meta.json \
  --model gemini-2.5-flash-stb --out-tag v4_bili \
  --config ../config.json -c 10

# 2. 出报告
python build_v4_report.py --preds out/v4_bili/predictions.jsonl
python build_v4_compare.py
```

不想下载视频时，可以直接用仓库里带的精简预测复现报告：

```bash
python build_v4_report.py --preds out/v4_final_predictions.jsonl   # 94.61%
python build_v4_compare.py
```

### 24 类标签

```
基础 16 类
  颜值COS  创意整活  才艺展示  活动剪辑   直播切片  明星娱乐
  实况解说  游戏宣传  游戏资讯  游戏实况   游戏攻略  二创类
  真人演绎  玩家故事  创意剪辑  高光剪辑

扩展 8 类
  游戏测评  游戏吐槽  剧情剪辑  剧情解读
  角色展示  皮肤展示  电竞相关  武器展示
```

标签集随提示词版本自动切换（`_v8` 及以上用 24 类）。

### 业务口径评测

严格比对标签会严重低估实际可用性——很多内容**天然横跨两类**：

> 「吐槽游戏机制 + 给出规避技巧」——是游戏吐槽还是游戏攻略？
> 「单局内玩梗 + 连续讲解」——是游戏实况、实况解说还是创意整活？

标注员按主观侧重打标，模型按自己的判定顺序打标，两者都自洽。
做法是把这类标签对标为「业务上可互换」，两种判法都算对，**并标明每组豁免对的来源**：

| 来源 | 组数 | 可信度 |
|---|---:|---|
| 另一数据集已验证 | 4 | 高 |
| 平台特有（抽查确认） | 2 | 中 |
| 业务方逐条复核 | 3 | 高，有视频级依据 |
| 批量采纳（未逐条核对） | 22 | **低** |

报告里同时给出「只计有逐条依据的豁免」是多少，避免把口径放宽当成模型变强。

### 当前结果

| 数据集 | 严格 | 业务口径（31 组） | 交付（含人工复核） |
|---|---:|---:|---:|
| v3 抖音/快手（446 条） | 64.6% | **90.36%** | **95.07%** |
| v4 B站（464 条） | 59.7% | **94.61%** | **95.04%** |

> **这两个数字用途不同。** 「业务口径」是模型 + 泛化规则的真实水平，可外推到新数据；
> 「交付」叠加了逐条人工复核，**只对本批已复核视频有效**，换一批数据会掉回前者。
> 评估模型能力、做容量规划用业务口径；交付这批标注结果用交付口径。

分工作表（B站，业务口径）：

| 工作表 | 游戏 | 样本 | 准确率 |
|---|---|---:|---:|
| 和平精英-B站 | 和平精英 | 106 | 96.2% |
| 蛋仔-B站 | 蛋仔派对 | 128 | 96.1% |
| 三角洲-B站 | 三角洲行动 | 118 | 93.2% |
| 王者荣耀-B站 | 王者荣耀 | 112 | 92.9% |

---

## 实验历程

这套流水线从 79% 做到 94.6%，中间有 8 个有效改动和 6 个失败的尝试。
完整记录（含每次的数字、根因、教训）在 **[docs/experiment-log.md](docs/experiment-log.md)**。

这里只摘三件事。

### 1. 一个需要被质疑的数字

v1 的结果是「模型原始 79.77% → 规则纠偏 + 人工复核后 98.09%」。
这个 98.09% 是**在同一批 262 条上反复迭代规则**得到的，换一批视频会掉回 80%。

所以之后所有改动都遵循一条规则：**必须在没见过的数据上验证，否则不采纳**。

实测依据——把 dev 集 76 条错误各写一条 `ID → 标签` 规则：

| | dev（挖规则用） | test（没见过） |
|---|---:|---:|
| 规则前 | 75.48% | 78.68% |
| 逐条 ID 规则后 | **100.00%** | **78.68%** |
| 提升 | +76 条 | **+0 条** |

dev 直接到 100%，test 纹丝不动。这就是为什么报告里始终把
「可外推的模型水平」和「仅本批有效的交付水平」分开列。

### 2. 有效的改动

| 改动 | 效果 | 验证方式 |
|---|---|---|
| 修复标签泄漏（`desc` 里含答案） | 从虚高的 68.17% 回到真实的 64.01% | 泄漏检查 0 条 |
| 业务口径（承认标注分歧） | 严格 59.6% → 74.2% | 逐条归纳 |
| 按表分治（不同工作表口径相反） | 74.22% → 76.68%（+2.47pp） | 5 种子选择一致，test 5/5 |
| 话题+作者规则层 | 76.68% → 78.03%（+1.35pp） | 10 种子，下界 ≥ 0 |
| B站 专用提示词 | 73.06% → 74.57% | 同样本对比 |
| 业务方逐条判定 49 条 → +3 组口径 | 74.57% → 80.17%（+5.6pp） | 49 条逐条判定 |

关键取舍：规则阈值选 **≥4** 而不是均值最高的 ≥3。
≥3 的 test 均值 +3.09pp 但最差种子只有 +1；≥4 均值 +2.57pp 而**最差 +2**。
判断标准是下界，不是平均值。

### 3. 失败的尝试

| 改动 | 结果 | 根因 |
|---|---|---|
| 长视频全片抽帧 | −3.2pp | 抽帧稀释视频主体信号 |
| prompt v9（反转「创意整活」边界） | **−14** | 各工作表口径互相冲突 |
| prompt v10 的 AI 规则 | 本批无效 | 探针验证目标样本全是实拍真人 |
| prompt v12（加「必须有多人互动」硬性前提） | **−16** | 提示词已 7780 字符，规则不生效 |
| B站 按表分治 | test 2/5 | 种子间不一致 |
| B站 作者规则 | +0.43pp，6/10 | 作者分散，样本量不足 |

两个最有价值的教训：

**① 在错误密集子集上做 A/B 会高估收益。**
v9 在 186 条错误样本上净 **+7**，全量重跑是 **−14**。
子集没有充分代表「当前判对」的样本——改动修复错误的同时也会打坏原本对的。

**② 同一处边界，不同工作表的判法可能相反。**
v9 在「1月快手-暗区突围」+8.3pp，在「2月抖音-地铁」−10.6pp。
前者 62 条全标创意整活，后者有 22 条游戏实况。
这催生了按表分治——一套全局提示词不可能同时满足两个相反的口径。

### 4. 让业务方参与判定

有些判断是业务问题，不是技术问题。所以把混淆样本做成了**可离线打开的判定页**：

- [`classification/out/三类边界对比.html`](classification/out/三类边界对比.html) — 49 条边界样本，逐条判「Excel 对 / 模型对 / 两者都通」
- [`classification/out/候选豁免对判定.html`](classification/out/候选豁免对判定.html) — 22 个候选豁免对，每个给 3 条样本

每条带视频链接、Excel 标签、模型标签、**模型的完整判断依据**。
填写存 localStorage（刷新不丢），导出 JSON 后用 `apply_*.py` 写回口径。

实测有效：业务方判完 49 条后归纳出 3 组口径分歧对，加入后 **74.57% → 80.17%**。

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
