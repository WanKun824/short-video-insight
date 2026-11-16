# 内容分类流水线

把短视频打上 **24 类内容类型**标签，并与人工标注比对评测。

- 技术说明：[../docs/classification-pipeline.md](../docs/classification-pipeline.md)
- 实验历程：[../docs/experiment-log.md](../docs/experiment-log.md)
- 评测报告：[out/v4_评测报告.md](out/v4_评测报告.md) · [out/v4_平台对比报告.html](out/v4_平台对比报告.html)

---

## 当前结果

| 数据集 | 严格 | 业务口径（31 组） | 交付（含人工复核） |
|---|---:|---:|---:|
| v3 抖音/快手（446 条） | 59.6% | **90.36%** | **95.07%** |
| v4 B站（464 条） | 59.7% | **94.61%** | **95.04%** |

> 「业务口径」可外推到新数据；「交付」叠加了逐条人工复核，**只对本批有效**。

---

## 快速上手

```bash
cd classification
pip install -r ../requirements.txt      # 需要 scikit-learn / numpy / openpyxl
```

### 从零跑一遍（以 B站 为例）

```bash
# 1. 读 Excel → 分层抽样
python prepare_data_v4.py --xlsx <工作簿路径>

# 2. 下载（B站 走公开 API，无需 cookie）
python download_multi.py --items data_v4/ground_truth.jsonl \
  --output ../videos_v4 --platforms B站 -c 10

# 3. 元数据 + 文本先验
python extract_media_meta.py --videos ../videos_v4 --out data_v4/media_meta.json
python text_prior.py --gt data_v4/ground_truth.jsonl \
  --out data_v4/text_prior.json --cv loo

# 4. 分类
python classify.py --prompt-version bili_v11 \
  --game peace,king,eggy,delta \
  --gt data_v4/ground_truth.jsonl --corpus data_v4/ground_truth.jsonl \
  --videos ../videos_v4 --prior data_v4/text_prior.json \
  --media-meta data_v4/media_meta.json \
  --model gemini-2.5-flash-stb --out-tag v4_bili \
  --config ../config.json -c 10

# 5. 报告
python build_v4_report.py --preds out/v4_bili/predictions.jsonl
python build_v4_compare.py
```

### 只跑规则层 / 分治（不调模型）

```bash
# 按表分治：dev 上选择版本，test 上验证，跑 5 个种子
python route_v3.py --sources \
  "v8=out/v3c_peace.jsonl,out/v3c_generic.jsonl;v10=out/v3e_peace.jsonl,out/v3e_generic.jsonl"

# 话题 + 作者规则层
python postprocess_v3.py --preds out/v3_routed/predictions.jsonl \
  --kinds tag,author --min-n 4 --rules data_v3/rules.json \
  --out out/v3_final/predictions.jsonl
```

---

## 文件说明

### 主流程

| 脚本 | 作用 |
|---|---|
| `prepare_data_v3.py` | v3 数据读取（按列**下标**，处理重复列名） |
| `prepare_data_v4.py` | v4 数据读取（按列**名**，处理 4 种表结构） |
| `download_multi.py` | 快手 / B站 下载（抖音用上层 `scripts/download_videos.py`） |
| `extract_media_meta.py` | ffprobe 提取时长/分辨率 |
| `text_prior.py` | TF-IDF + 逻辑回归，输出 Top-3 文本候选 |
| `context.py` | 构造检索上下文（话题/作者/时长/文本/相似示例，全部 leave-one-out） |
| `prompts.py` | 提示词与标签集（v1–v12） |
| `classify.py` | 分类主程序 |

### 优化层

| 脚本 | 作用 | 验证方式 |
|---|---|---|
| `route_v3.py` | 按工作表选择提示词版本 | 5 种子选择一致性 + test 正收益 |
| `postprocess_v3.py` | 话题 + 作者规则层 | dev 挖掘 / test 验证 / 10 种子 |
| `calibrate_v3.py` | 逐条人工复核（交付层） | 按置信度升序，仅本批有效 |

### 报告

| 脚本 | 产出 |
|---|---|
| `build_v3_report.py` / `build_v3_html.py` | v3 评测报告（MD / HTML） |
| `build_v3_project.py` | v3 项目报告（改进历程 + 失败实验） |
| `build_v4_report.py` | v4 评测报告（MD，含 25 条错误逐条分析） |
| `build_v4_compare.py` | v4 跨平台对比（HTML） |

### 业务方协作

| 脚本 | 作用 |
|---|---|
| `build_boundary_compare.py` | 生成 49 条边界样本判定页 |
| `build_pair_review.py` | 生成 22 个候选豁免对判定页 |
| `apply_boundary_feedback.py` | 读回边界判定，给出可执行下一步 |
| `apply_pair_feedback.py` | 读回豁免对判定，写回评测口径 |

判定页是**可离线打开的 HTML**，填写存 localStorage（刷新不丢），
导出 JSON 给回即可。每条样本都带视频链接和模型的完整判断依据。

---

## 数据文件

| 文件 | 内容 |
|---|---|
| `data_v3/` `data_v4/ground_truth.jsonl` | 真值样本（id / desc / label / 平台 / 游戏 / 作者 / 时长…） |
| `media_meta.json` | ffprobe 提取的时长与分辨率 |
| `text_prior.json` | 文本模型 Top-3 候选 |
| `rules.json` | 话题 + 作者规则（含 dev 覆盖数与阈值配置） |
| `sheet_routing.json` | 按表分治的路由结果与验证记录 |
| `manual_review.json` | 逐条人工复核记录（含视频链接与模型原判） |
| `boundary_feedback.json` | 业务方对 49 条边界样本的判定 |
| `pair_review.json` | 业务方对候选豁免对的判定 |

`out/*_predictions.jsonl` 是**精简版**，只保留可复现评测所需字段
（`id` / `gt` / `label` / `reason` / `confidence` / `prompt_version`）。
`media` 字段写成占位路径 `videos/<id>.mp4`，仅用于标记「该条有视频」，
报告脚本靠它过滤纯文本样本。完整版含 `context`、`raw` 与真实路径，体积大，未纳入仓库。

| 文件 | 内容 |
|---|---|
| `v3_final_predictions.jsonl` | v3 规则层输出（可外推，78.03%） |
| `v3_delivery_predictions.jsonl` | v3 交付层（含人工复核，95.07%） |
| `v4_final_predictions.jsonl` | v4 分表预测（业务口径 94.61%） |
| `v4_delivery_predictions.jsonl` | v4 交付层（含人工复核，95.04%） |
| `v4_base_{peace,king,generic}_predictions.jsonl` | v4 基线（用 v3 提示词直接跑），用于展示 bili_v11 的提升 |

---

## 注意

- **视频未随仓库提供**。需要先用上层的下载脚本自行下载，或用 `--allow-no-media` 走纯文本模式（准确率会显著偏低，不建议用于评测）。
- **精简版预测的 `media` 是占位路径**（`videos/<id>.mp4`），只用于标记「该条有视频」，不指向真实文件。要在本地跑 `classify.py` 需要先自行下载视频。
- `config.json` 含密钥，已在 `.gitignore` 中，用 `config.example.json` 模板自行创建。
