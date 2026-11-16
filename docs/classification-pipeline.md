# 内容分类流水线：技术说明

本文说明 `classification/` 目录的技术实现，包括任务定义、整体架构、
上下文构造、评测口径和已知限制。实验过程见 [experiment-log.md](./experiment-log.md)。

---

## 一、任务定义

输入：短视频文件 + 作品元数据（标题 / 描述 / 话题标签 / 作者 / 时长等）
输出：24 类内容类型之一

```
LABELS（基础 16 类）
  颜值COS  创意整活  才艺展示  活动剪辑
  直播切片  明星娱乐  实况解说  游戏宣传
  游戏资讯  游戏实况  游戏攻略  二创类
  真人演绎  玩家故事  创意剪辑  高光剪辑

+ NEW_LABELS（扩展 8 类）
  游戏测评  游戏吐槽  剧情剪辑  剧情解读
  角色展示  皮肤展示  电竞相关  武器展示
```

标签集随提示词版本自动切换（见 §3.4）。
定义文本在 `prompts.py` 的 `LABEL_GUIDE` / `NEW_LABEL_GUIDE`。

---

## 二、整体架构

```
Excel
  │
  ├─ prepare_data_v3.py / prepare_data_v4.py   字段归一 → ground_truth.jsonl
  │
  ├─ download_all.py（抖音）/ download_multi.py（快手·B站）
  │
  ├─ extract_media_meta.py    ffprobe → media_meta.json
  │
  ├─ text_prior.py            TF-IDF + LR，leave-one-out → text_prior.json
  │
  ├─ classify.py              多模态分类 → predictions.jsonl
  │    ├─ 视频过大 → ffmpeg 转 360p/12fps 代理
  │    ├─ context.py 构造检索上下文
  │    └─ 送模型 → 解析 JSON
  │
  ├─ route_v3.py              按工作表分治（dev 选择 / test 验证 / 多种子）
  ├─ postprocess_v3.py        话题 + 作者规则层
  ├─ calibrate_v3.py          逐条人工复核（交付层，可选）
  │
  └─ build_v*_report.py       评测报告
```

---

## 三、关键实现

### 3.1 视频代理

超过 **6 MB** 的视频用 ffmpeg 转成 360p / 12fps / 前 120 秒再 base64 送模型：

```python
cmd = ["ffmpeg", "-y", "-i", src, "-t", "120",
       "-vf", "scale=-2:360", "-r", "12",
       "-c:v", "libx264", "-crf", "32", "-preset", "veryfast",
       "-c:a", "aac", "-b:a", "64k", dst]
```

直接传原视频会让请求体过大。代价是**只覆盖前 120 秒**——
B站 65% 的视频超过这个长度，是已知误差来源之一（见 §7）。

### 3.2 检索上下文

每条视频送模型前，`context.py` 附带五类先验，**全部严格 leave-one-out**
（构造某条视频的上下文时排除它自己）：

| 先验 | 来源 | 作用 |
|---|---|---|
| 话题标签先验 | 该话题在已标注样本中的标签分布（纯度 ≥60% 才输出） | 高纯度话题信号很强 |
| 同作者历史标签 | 同一 UP 主/主播的其他作品 | 系列内容通常同类 |
| 同时长桶分布 | 按 30s/90s/180s/600s 分桶 | 弱参考 |
| 文本模型 Top-3 | TF-IDF(char 2–4) + LogisticRegression | 描述文本的质量信号 |
| 相似作品示例 | 话题 Jaccard + 描述相似度，每标签最多 2 条 | 对齐标注口径 |

上下文末尾有一段说明，告诉模型「先验通常较准但不是绝对，与画面冲突时以画面为准」。

### 3.3 文本先验

`text_prior.py` 用 TF-IDF（char_wb, 2–4 gram, 30000 特征）+ 逻辑回归，
`leave-one-out` 输出每条视频的 Top-3 候选与概率。

结果会作为「文本模型候选」进入上下文，同时也用来判断平台的可分性：

| 数据集 | top1 | top3 |
|---|---:|---:|
| v3 抖音/快手 | 59.9% | 79.0% |
| v4 B站 | **40.8%** | 63.6% |

B站 只有 40.8%——描述中位仅 21 字、97% 无话题标签，纯文本几乎不可用。

### 3.4 提示词版本与标签集路由

`prompts.py` 里按 `名称_v数字` 组织版本。`classify.py` 用正则提版本号决定标签集：

```python
_m = re.search(r"_v(\d+)$", args.prompt_version)
_n = int(_m.group(1)) if _m else 0
labels = LABELS_V8 if _n >= 8 else (LABELS_V6 if _n >= 6 else LABELS)
print(f"提示词 {args.prompt_version}（版本号 {_n}）-> 标签集 {len(labels)} 类")
```

这样加新版本不需要改路由代码。

> 早期这里用 `endswith("_v8")`，导致 `bili_v11` 这类带平台前缀的版本名
> 静默退回 16 类标签集，整条样本判失败。详见 experiment-log §10。

### 3.5 结果缓存

每条结果单独落一份 `out/<tag>/items/<视频ID>.json`。
重跑时已有且 `label` 非空的直接跳过——这是断点续传的基础。
加 `--force` 强制重跑（改了提示词或修了解析 bug 时用）。

---

## 四、评测方法

### 4.1 评测集收敛

不是所有样本都该进评测集。以 v3（646 条）为例：

| 排除项 | 条数 | 原因 |
|---|---:|---|
| 小红书 | 76 | 免签不可下载，只能纯文本推断 |
| 无真值 | 12 | Excel 标签为 `/` |
| 视频未下载 | 107 | 接口未返回，模型只能看描述 |
| 缺预测 | 5 | 模型调用失败 |
| **参与评测** | **446** | |

**纯文本样本必须排除**：混在一起时整体 76.49%，
拆开看是**有视频 74.2% / 纯文本 86.0%**。
纯文本更高不是因为它更有效，而是这批样本集中在标签单一、
描述里直接带答案的工作表上（如 dnfm 的 `#dnf手游攻略`）。

### 4.2 两类准确率

报告里始终给两个数，含义完全不同：

| 指标 | 含义 | 能否外推 |
|---|---|---|
| **模型 + 泛化规则** | 模型分析视频内容，再结合话题标签与作者信息做规则校正。规则经 dev/test + 多种子验证 | ✅ 换一批新视频依然有效 |
| **交付准确率** | 加逐条人工复核 | ❌ 只对本批已复核视频有效 |

实测依据：把 dev 集 76 条错误各写一条 `ID → 标签` 规则：

| | dev（挖规则用） | test（没见过） |
|---|---:|---:|
| 规则前 | 75.48% | 78.68% |
| 逐条 ID 规则后 | **100.00%** | **78.68%** |
| 提升 | +76 条 | **+0 条** |

评估模型能力、做容量规划 → 用第一个数。
交付本批标注结果 → 用第二个数。

### 4.3 dev/test + 多随机种子

所有规则与提示词改动都走这个流程：

1. 按工作表分层划分 dev(70%) / test(30%)
2. **只在 dev 上挖掘/调整**
3. 在 test 上验证
4. **换 5–10 个随机种子重复**，看正收益种子数与最差值

取舍优先看**下界**而不是平均值。

---

## 五、口径豁免对

### 5.1 为什么需要

很多内容天然横跨两类，标注员按主观侧重打标。
把这类标签对标为「业务上可互换」，两种判法都算对。

### 5.2 当前 31 组，按来源分四档

| 来源 | 组数 | 可信度 |
|---|---:|---|
| v3 继承 | 4 | 高，已在另一数据集验证 |
| B站 平台特有 | 2 | 中，抽查样本确认 |
| 业务方逐条复核 | 3 | 高，有视频级依据 |
| 本次批量采纳 | 22 | **低，未逐条核对样本** |

报告里每组都标来源。**只计有逐条依据的豁免，B站 是 80.17%**（而非 94.61%）。

### 5.3 必须由业务方决定

豁免对的本质是业务判断（「这两类能不能互换」），不是技术判断。
按收益排序批量添加能把准确率做到任意高度——
但那样做出来的准确率不再反映分类质量，下游拿去分发会出问题。

实现上看 `build_boundary_compare.py` / `build_pair_review.py`：
生成可离线打开的 HTML 判定页，业务方填完导出 JSON，
`apply_*.py` 写回评测口径。

---

## 六、目录结构

```
classification/
├── prepare_data_v3.py        v3 读取（按列下标，处理重复列名）
├── prepare_data_v4.py        v4 读取（按列名，处理 4 种表结构）
├── download_multi.py         快手 / B站 下载
├── extract_media_meta.py     ffprobe 元数据
├── text_prior.py             TF-IDF + LR 文本先验
├── context.py                检索上下文构造
├── prompts.py                提示词与标签集（v1–v12）
├── classify.py               分类主程序
├── route_v3.py               按工作表分治
├── postprocess_v3.py         话题 + 作者规则层
├── calibrate_v3.py           逐条人工复核（交付层）
├── build_v3_report.py        v3 评测报告（MD）
├── build_v3_html.py          v3 评测报告（HTML）
├── build_v3_project.py       v3 项目报告
├── build_v4_report.py        v4 评测报告（MD）
├── build_v4_compare.py       v4 跨平台对比（HTML）
├── build_boundary_compare.py 边界样本判定页（给业务方）
├── build_pair_review.py      候选豁免对判定页（给业务方）
├── apply_boundary_feedback.py 读回边界判定
├── apply_pair_feedback.py     读回豁免对判定
├── data_v3/  data_v4/        真值、先验、元数据、规则、判定记录
└── out/                      报告与精简后的预测
```

### 数据文件说明

| 文件 | 内容 |
|---|---|
| `ground_truth.jsonl` | 真值样本（id / desc / label / 平台 / 游戏 / 作者 / 时长 …） |
| `media_meta.json` | ffprobe 提取的时长与分辨率 |
| `text_prior.json` | 文本模型 Top-3 候选 |
| `rules.json` | 话题 + 作者规则（含 dev 覆盖数与阈值配置） |
| `sheet_routing.json` | 按表分治的路由结果与验证记录 |
| `manual_review.json` | 逐条人工复核记录（含视频链接与模型原判） |
| `boundary_feedback.json` | 业务方对 49 条边界样本的判定 |
| `pair_review.json` | 业务方对候选豁免对的判定 |

### 预测文件说明

`out/*_predictions.jsonl` 是**精简版**，只保留可复现评测所需字段：

```json
{"id": "BV1...", "gt": "游戏实况", "label": "创意整活",
 "reason": "视频...", "confidence": 0.95, "prompt_version": "bili_v11",
 "media": "videos/BV1....mp4"}
```

`media` 是占位路径，仅用于标记「该条有视频」——报告脚本靠它过滤纯文本样本。
完整版（含 `context`、`raw` 与真实绝对路径）未纳入仓库。

| 文件 | 用途 |
|---|---|
| `v3_final_predictions.jsonl` | v3 规则层输出 |
| `v3_delivery_predictions.jsonl` | v3 交付层（含人工复核标记） |
| `v4_final_predictions.jsonl` | v4 分表预测 |
| `v4_delivery_predictions.jsonl` | v4 交付层 |
| `v4_base_{peace,king,generic}_predictions.jsonl` | v4 基线，用于对比 bili_v11 |

---

## 七、已知限制

### 7.1 视频代理只覆盖前 120 秒

B站 65% 的视频超过 120 秒。全片抽帧实验验证为负收益（−3.2pp），
因为抽帧稀释了视频主体信号。这是当前未解决的技术限制。

### 7.2 话题规则层在无标签平台失效

B站 97% 的视频没有话题标签，规则挖掘返回 0 条。
作者规则也因作者分散（500 条 / 335 人）达不到样本量门槛。

### 7.3 剩余错误的成因分布（B站 25 条）

| 成因 | 条数 | 能不能修 |
|---|---:|---|
| 真人出镜盖掉更贴切的类 | 8 | 可以修：要求「真人是主要看点」才判 |
| 内容确实横跨两类 | 10 | 需要业务方定优先级 |
| 长视频只看了前段 | 3 | 技术限制 |
| 模型判法更符合定义 | 4 | 建议改 Excel |

### 7.4 数据质量

- `dnfm`（v3）88 条里 80 条视频已失效（2024 年数据），排除后只剩 8 条
- `1月快手-暗区突围`（v3）62 条真值全是「创意整活」，标签无区分度
- 小红书免签不可下载（页面 `__INITIAL_STATE__` 已剥离 `noteDetailMap`），需 cookie

---

## 八、复现

```bash
pip install -r requirements.txt          # Python 3.10+，另需 ffmpeg 在 PATH
cp config.example.json config.json       # 填入 llmproxy_api_key
```

### 从预测复现报告（不调模型）

```bash
cd classification

python build_v4_report.py --preds out/v4_final_predictions.jsonl
python build_v4_compare.py
```

预期输出：

```
准确率 439/464 = 94.61%（口径生效 162，剩余错误 25）
同口径（31 组豁免对）对比：
  抖音/快手 90.36%（446 条）
  B站       94.61%（464 条）
```

`build_v4_compare.py` 会自动识别两种目录布局：
仓库里的 `out/v4_base_*_predictions.jsonl`，或工作目录里的 `out/v4_*/predictions.jsonl`。

### 从零跑一遍分类

```bash
python prepare_data_v4.py --xlsx <工作簿路径>          # 读 Excel、分层抽样
python download_multi.py --items data_v4/ground_truth.jsonl \
  --output videos_v4 --platforms B站 -c 10            # 下载
python extract_media_meta.py --videos videos_v4 \
  --out data_v4/media_meta.json
python text_prior.py --gt data_v4/ground_truth.jsonl \
  --out data_v4/text_prior.json --cv loo              # 文本先验
python classify.py --prompt-version bili_v11 \
  --game peace,king,eggy,delta \
  --gt data_v4/ground_truth.jsonl --corpus data_v4/ground_truth.jsonl \
  --videos videos_v4 --prior data_v4/text_prior.json \
  --media-meta data_v4/media_meta.json \
  --model gemini-2.5-flash-stb --out-tag v4_bili \
  --config ../config.json -c 10
python build_v4_report.py --preds out/v4_bili/predictions.jsonl
```

### 规则层与分治

```bash
# 按表分治（dev 选择 / test 验证 / 5 个种子）
python route_v3.py --sources \
  "v8=out/v3c_peace.jsonl,out/v3c_generic.jsonl;v10=out/v3e_peace.jsonl,out/v3e_generic.jsonl"

# 规则层（dev 挖掘 / test 验证）
python postprocess_v3.py --preds out/v3_routed/predictions.jsonl \
  --kinds tag,author --min-n 4 --rules data_v3/rules.json \
  --out out/v3_final/predictions.jsonl

# 交付层（逐条复核，仅在需要交付本批结果时用）
python calibrate_v3.py --target 0.95 \
  --gt data_v3/ground_truth.jsonl --preds out/v3_final/predictions.jsonl \
  --review data_v3/manual_review.json \
  --out out/v3_delivery/predictions.jsonl
```
