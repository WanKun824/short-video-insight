# short-video-insight

> **短视频内容分类（24 类）——怎么把准确率从 79% 做到 94.6%，以及为什么不能更快。**
>
> 这份仓库的重点不是「又一个调大模型的脚本」，而是**一套能让优化结果站得住的流程**：
> 先让评测可信，再让每次改动证明自己有效，最后才谈提升。
> 过程中有 8 个改动被采纳、6 个被否决——否决的那些同样记录在案。

[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)

**核心文档**　[实验日志](docs/experiment-log.md)（完整过程，含 6 个失败尝试的根因）· [技术说明](docs/classification-pipeline.md) · [下载机制](docs/download-mechanics.md) · [分析机制](docs/analysis-mechanics.md)

---

## 目录

- [一、问题与结论](#一问题与结论)
- [二、优化的起点：一个不能用的数字](#二优化的起点一个不能用的数字)
- [三、优化思路](#三优化思路)
  - [3.1 先让评测可信，再谈优化](#31-先让评测可信再谈优化)
  - [3.2 每个改动都必须证明自己有效](#32-每个改动都必须证明自己有效)
  - [3.3 优化顺序：从便宜到贵](#33-优化顺序从便宜到贵)
  - [3.4 有些判断必须交给业务方](#34-有些判断必须交给业务方)
- [四、优化历程：8 个采纳，6 个否决](#四优化历程8-个采纳6-个否决)
- [五、三个代价最贵的教训](#五三个代价最贵的教训)
- [六、当前瓶颈](#六当前瓶颈)
- [工程实现](#工程实现)
- [安装](#安装)
- [配置](#配置)
- [使用](#使用)
- [参数速查](#参数速查)
- [输出结构](#输出结构)
- [分析维度](#分析维度)
- [工作原理](#工作原理)
- [FAQ](#faq)
- [隐私与合规](#隐私与合规)
- [License](#license)

---

## 一、问题与结论

**任务**：给短视频打上 **24 类内容类型**标签（游戏实况 / 创意整活 / 颜值COS / 直播切片 …），
与人工标注比对。覆盖 4 个平台（抖音 / 快手 / B站 / 小红书）、9 款游戏、2500+ 条样本。

**结果**：

| 数据集 | 严格 | 业务口径（31 组） | 交付（含人工复核） |
|---|---:|---:|---:|
| 抖音/快手（446 条） | 64.6% | **90.36%** | **95.07%** |
| B站（464 条） | 59.7% | **94.61%** | **95.04%** |

> 三列是三种口径，用途不同，**不能混用**：
> - 「严格」= 标签完全一致
> - 「业务口径」= 承认标注本身有分歧（「业务上可互换」的标签对两种判法都算对）。
>   31 组豁免对里有 22 组是批量采纳、**未逐条核对**；只计有逐条依据的部分，B站 是 **80.17%**
> - 「交付」= 再叠加逐条人工复核，**只对本批有效，换数据会掉回业务口径**
>
> 详细定义与实测依据见 [3.1](#31-先让评测可信再谈优化)。

**一句话方法论**：

> 准确率不是「调出来的」，是「先定义清楚什么算对、再让每次改动在没见过的数据上
> 证明自己」之后剩下的那个数。凡是跳得太快的结果，先怀疑泄漏。

---

## 二、优化的起点：一个不能用的数字

最初的版本报告「规则纠偏 + 人工复核后 **98.09%**」。这个数字不能用，原因是：

- 48 条纠偏规则是在**同一批 262 条**上反复迭代出来的；
- 换一批视频，规则大部分失效；
- 模型本身的泛化水平只有约 80%。

**98.09% − 80% = 18 个百分点，全部来自「在这批数据上试出来的巧合」。**

这类数字最危险的地方不是错，而是**它看起来对**：
如果拿它去做容量规划、定验收标准，下一批数据上线时会直接掉回 80%。

所以后面所有工作都围绕一件事：**把「真的变好了」和「只是背下了这批数据」分开。**

---

## 三、优化思路

整个流程可以概括成四步，顺序不能颠倒：

1. **先让评测可信**（§3.1）—— 否则所有优化数字都是幻觉
2. **再让改动自证有效**（§3.2）—— dev/test + 多种子，看下界不看均值
3. **按成本从低到高动手**（§3.3）—— 数据 → 口径 → 规则 → 分治 → 提示词
4. **该交给人的交给人的**（§3.4）—— 业务判断不代劳

### 3.1 先让评测可信，再谈优化

优化前必须先回答「怎么算对」。这一步做不好，后面所有数字都没有意义。
做了三件事：

**① 评测集要干净**

不是所有样本都该进评测集。646 条原始数据最后只有 446 条能用于评测：

| 排除项 | 条数 | 原因 |
|---|---:|---|
| 小红书 | 76 | 免签不可下载 |
| 无真值 | 12 | Excel 标签为 `/` |
| 视频未下载 | 107 | 接口未返回 |
| 缺预测 | 5 | 模型调用失败 |

其中**纯文本样本必须排除**：它们视频没下到，模型只能看描述猜。
混在一起时整体 76.49%，拆开看是**有视频 74.2% / 纯文本 86.0%**——
纯文本更高不是因为它更有效，而是这批样本恰好集中在标签单一、
描述里直接带答案的工作表上（如 dnfm 的 `#dnf手游攻略`）。

**② 承认标注本身有分歧**

很多内容天然横跨两类：

> 「吐槽游戏机制 + 给出规避技巧」——是游戏吐槽还是游戏攻略？
> 「单局内玩梗 + 连续讲解」——是游戏实况、实况解说还是创意整活？

标注员按主观侧重打标，模型按自己的判定顺序打标，两者都自洽。
把这类的标签对标为「业务上可互换」，两种判法都算对。
这一改动没有碰模型任何行为，准确率从 59.6% 到 74.2%。

但**豁免对必须标明来源**，否则就成了任意放宽：

| 来源 | 组数 | 可信度 |
|---|---:|---|
| 另一数据集已验证 | 4 | 高 |
| 平台特有（抽查确认） | 2 | 中 |
| 业务方逐条复核 | 3 | 高，有视频级依据 |
| 批量采纳（未逐条核对） | 22 | **低** |

报告里同时给出「只计有逐条依据的豁免」是多少——B站 是 **80.17%** 而非 94.61%。

**③ 两类准确率分开报**

| 指标 | 含义 | 能否外推 |
|---|---|---|
| **模型 + 泛化规则** | 模型看视频判断，再结合话题/作者信息做规则校正 | ✅ 换一批新数据依然有效 |
| **交付准确率** | 叠加逐条人工复核 | ❌ 只对本批有效 |

实测依据——把 dev 集 76 条错误各写一条 `ID → 标签` 规则：

| | dev（挖规则用） | test（没见过） |
|---|---:|---:|
| 规则前 | 75.48% | 78.68% |
| 逐条 ID 规则后 | **100.00%** | **78.68%** |
| 提升 | +76 条 | **+0 条** |

dev 直接到 100%，test 纹丝不动。**逐条复核对新视频零贡献**，这是它的定义决定的。

### 3.2 每个改动都必须证明自己有效

标准流程：

1. 按工作表分层划分 dev(70%) / test(30%)
2. **只在 dev 上挖掘/调整**
3. 在 test 上验证
4. **换 5–10 个随机种子重复**，看正收益种子数与最差值

**关键：判断标准看下界，不看平均值。**

规则层阈值的实际取舍：

| 阈值 | test 平均提升 | 正收益种子 | 最差 |
|---|---:|---:|---:|
| ≥3 | +3.09pp | 10/10 | +1 |
| **≥4（采纳）** | **+2.57pp** | **10/10** | **+2** |
| ≥5 | +2.06pp | 10/10 | +1 |
| ≥6 | +1.54pp | 9/10 | 0 |

≥3 的均值最高，但最差种子只有 +1，且 dev/test 差距 0.97pp；
≥4 最差 +2，差距 0.72pp。**下界更好、过拟合更少，更适合上生产。**

这套流程最终拦下了 4 个看起来能涨但实际不稳的改动（见第四节）。

### 3.3 优化顺序：从便宜到贵

踩过坑之后总结的优先级，越靠前越便宜、越可靠：

| 顺序 | 动什么 | 成本 | 可靠性 |
|---|---|---|---|
| 1 | **修数据 / 修泄漏** | 低 | 必须做，否则后面全白费 |
| 2 | **统一评测口径** | 低 | 高，只是承认现实 |
| 3 | **规则层**（后处理） | 中 | 高，确定性的，不依赖模型「听话」 |
| 4 | **按表分治**（局部化） | 中 | 中，需要多种子验证选择本身 |
| 5 | **改提示词** | 高 | 低，试了三次全失败 |

为什么把提示词排在最后：**规则是确定性的，提示词不是。**
把业务方给的明确判据写成「最高优先级规则」后，模型并没有执行它，
只是输出分布被扰动了（精确率 60.6% → 60.4%）。而同样的判据做成后处理规则，
在 v3 上稳定拿到 +1.35pp。

### 3.4 有些判断必须交给业务方

**豁免对不能由实现方自己加。** 它的本质是业务判断（「这两类能不能互换」），
按收益排序批量添加能把准确率做到任意高度，但那个数字就不再反映分类质量。

所以把混淆样本做成**可离线打开的判定页**，由业务方逐条判：

- [`三类边界对比.html`](classification/out/三类边界对比.html) — 49 条边界样本，判「Excel 对 / 模型对 / 两者都通」
- [`候选豁免对判定.html`](classification/out/候选豁免对判定.html) — 22 个候选豁免对，每个给 3 条样本

每条带视频链接、Excel 标签、模型标签、**模型的完整判断依据**。
填写存 localStorage（刷新不丢），导出 JSON 后用 `apply_*.py` 写回评测口径。

实测有效：业务方判完 49 条后归纳出 3 组口径分歧对，加入后 **74.57% → 80.17%**。
而且他们在「为什么」里反复写同一句话——「整活要有活，单个玩家没有和队友的强互动」，
这直接解释了 26 条真实错误里的 8 条（见 [第六节](#六当前瓶颈)）。

---

## 四、优化历程：8 个采纳，6 个否决

完整记录（含每次的数字、根因、教训）见 **[docs/experiment-log.md](docs/experiment-log.md)**。

### 采纳的

| # | 改动 | 效果 | 验证方式 |
|---|---|---|---|
| 1 | 修数据读取（重复列名 / 标签列错位） | 让后续评测有意义 | — |
| 2 | 修 3 个评测 bug | 准确率从虚低的 57% 校正到 66.85% | — |
| 3 | **修复标签泄漏** | 从虚高的 68.17% 回到真实的 64.01% | 泄漏检查 0 条 |
| 4 | 业务口径（承认标注分歧） | 严格 59.6% → 74.2% | 逐条归纳 |
| 5 | 按表分治 | 74.22% → 76.68%（+2.47pp） | 5 种子选择一致，test 5/5 |
| 6 | 话题+作者规则层 | 76.68% → 78.03%（+1.35pp） | 10 种子，下界 ≥ 0 |
| 7 | B站 专用提示词 | 73.06% → 74.57% | 同样本对比 |
| 8 | 业务方逐条判定 49 条 → 归纳 3 组口径 | 74.57% → 80.17%（+5.6pp） | 49 条逐条判定 |

### 否决的

| 改动 | 结果 | 根因 |
|---|---|---|
| 长视频全片抽帧 | −3.2pp | 抽帧稀释视频主体信号 |
| prompt v9（反转「创意整活」边界） | **−14** | 各工作表口径互相冲突 |
| prompt v10 的 AI 规则 | 本批无效 | 探针验证目标样本全是实拍真人 |
| prompt v12（加「必须有多人互动」硬性前提） | **−16** | 提示词过长，规则不生效 |
| B站 按表分治 | test 2/5 | 种子间不一致 |
| B站 作者规则 | +0.43pp，6/10 | 作者分散，样本量不足 |

---

## 五、三个代价最贵的教训

### ① 结果异常好 → 先查泄漏

给「暗区」表补标签后，准确率从 52.4% 跳到 75.6%，宽口径样本通过率从 15.4% 跳到 59.0%。

**跳得太漂亮，去查了送给模型的 context**：

```
相似作品参考：
- 描述：...#暗区突围PC 游戏宣传：11.20国服测试下载开启 → 标签：游戏宣传
```

把「内容精炼（人工)」整段拼进 `desc` 时，**它首行就是 `游戏宣传：`——标签本身**。
加上这些样本进入检索语料后，相似示例直接展示了「标签 → 标签」的对应关系，
模型只需做字符串匹配。

修复后：整体 68.17% → 64.01%，文本先验 61.36% → 59.94%。

### ② 在错误密集子集上做 A/B 会高估收益

v9 在 186 条「错误密集」样本上净 **+7**，看起来可用。
**全量重跑是 −14。**

原因：子集没有充分代表「当前判对」的样本——改动在修复错误的同时也会打坏原本对的，
子集看不到后者。

现在只把子集 A/B 用来快速排除方向，**任何要采纳的改动都必须全量重跑**。

### ③ 同一处边界，不同工作表的判法可能相反

v9 的全局数字是 −14，但分表看：

| 工作表 | v8 | v9 | 变化 |
|---|---:|---:|---:|
| 1月快手-暗区突围（62 条全标创意整活） | 86.7% | 95.0% | **+8.3pp** |
| 2月抖音-地铁（22 条游戏实况） | 77.3% | 66.7% | **−10.6pp** |

**全局净值为 0 可能是两个 +8 和两个 −8 抵消。** 这次不是 0，但同样被掩盖了。
发现这点之后才有了「按表分治」——一套全局提示词不可能同时满足两个相反的口径。

---

## 六、当前瓶颈

最后阶段发现一个重要事实：**25 条错误分布在 25 个不同的混淆方向上**
（基本每个方向只有 1 条）。这意味着再加豁免对每组只能救 1 条，已经没有意义。

但按**成因**拆，它们有 4 类：

| 成因 | 条数 | 能不能修 |
|---|---:|---|
| 真人出镜盖掉了更贴切的类 | 8 | **可以修**：要求「真人是主要看点」才判 |
| 内容确实横跨两类 | 10 | **需要定口径**：业务方决定「吐槽 vs 攻略」谁优先 |
| 长视频只看了前段 | 3 | 技术限制（代理只取前 120 秒） |
| 模型判法更符合定义 | 4 | **建议改 Excel** |

代表性案例：

> `玩家故事 → 直播切片`｜三角洲行动 0:36
> 模型依据：*出现真人（手部）出镜，正在进行游戏内的抽奖，伴随激动的实时语音反应*
>
> 一只手入镜就触发了直播判定——判定规程里「主播出镜」优先级最高。

> `游戏吐槽 → 游戏攻略`｜三角洲行动 2:09
> 模型依据：*讲解了「非法抱团」判定机制，并提供规避技巧（互相攻击一下、打一枪队友），属于明确的技巧教学*
>
> 视频既在吐槽又给了方法。**这个需要业务方定**：吐槽里带技巧，算哪个？

其余案例（25 条全部带视频链接和模型依据）见
**[out/v4_平台对比报告.html](classification/out/v4_平台对比报告.html)** 第三节。

**再往上走需要统一的标注规范**——两个数据集、五次提示词实验都指向同一结论：
「创意整活」的边界在不同工作表里判法相反，这不是模型能力问题。

---

## 工程实现

这套流水线包含两条可独立使用的链路：

| | 链 A：内容分析 | 链 B：内容分类评测 |
|---|---|---|
| 入口 | `scripts/analyze_videos.py` | `classification/classify.py` |
| 标签体系 | 六个维度的**开放式**标签（题材/形式/情绪…） | **固定 24 类**内容类型 |
| 擅长 | 快速摸清一批新内容长什么样 | 在既定口径下做到可控、可复现 |
| 有无评测 | 无 | 有评测集、业务口径、防过拟合流程、逐条错误分析 |
| 适用 | 内容选题调研、竞品拆解 | 内容分发打标、模型能力评估 |

两条链路共用同一套下载器。

```
作品链接清单.md
      │
      ├── download_videos.py ──► downloads/douyin/*.mp4
      │                          downloads/kuaishou/*.mp4
      │
      ├── analyze_videos.py ───► result/<视频ID>.csv + _汇总.xlsx   （链 A）
      │
      └── classification/
           ├── prepare_data_v4.py ──► ground_truth.jsonl（分层抽样）
           ├── text_prior.py ──────► 文本模型 Top-3 候选
           ├── classify.py ────────► predictions.jsonl（多模态 + 检索增强）
           ├── route_v3.py ────────► 按表分治（dev 选择 / test 验证）
           ├── postprocess_v3.py ──► 话题 + 作者规则层
           └── build_v*_report.py ─► 评测报告（含逐条错误分析）    （链 B）
```

### 快速开始

```bash
git clone https://github.com/WanKun824/short-video-insight.git
cd short-video-insight
pip install -r requirements.txt
cp config.example.json batch_config.json   # 填入模型 Key
```

**跑分类评测（链 B）** —— 不需要视频，仓库里带了精简后的预测：

```bash
cd classification
python build_v4_report.py --preds out/v4_final_predictions.jsonl   # 94.61%
python build_v4_compare.py
```

**跑下载 + 内容分析（链 A）**：

```bash
python scripts/download_videos.py --urls 作品链接清单.md -o downloads
python scripts/analyze_videos.py --dir downloads/douyin --config batch_config.json
```

链 B 从零跑一遍（读 Excel → 下载 → 分类 → 报告）见
[classification/README.md](classification/README.md)。

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
