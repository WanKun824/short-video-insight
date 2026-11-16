# -*- coding: utf-8 -*-
"""生成文本模型的 leave-one-out 候选分布，作为视频分类的候选先验。"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import LeaveOneOut, cross_val_predict
from sklearn.pipeline import make_pipeline

import argparse

ROOT = Path(__file__).resolve().parent
_ap = argparse.ArgumentParser()
_ap.add_argument("--gt", default=str(ROOT / "data" / "ground_truth.jsonl"))
_ap.add_argument("--out", default=str(ROOT / "data" / "text_prior.json"))
_ap.add_argument("--cv", default="loo", choices=("loo", "kfold"),
                 help="loo=逐条留一（慢但严格）；kfold=5 折（快，近似）")
_ap.add_argument("--train", default="", help="训练集 jsonl（与 --predict 配合，避免测试集泄漏）")
_ap.add_argument("--predict", default="", help="待预测 jsonl")
_args = _ap.parse_args()
rows = [json.loads(l) for l in Path(_args.gt).read_text(encoding="utf-8").splitlines() if l.strip()]

# 训练集 / 测试集分离模式：用训练集拟合，预测测试集，杜绝标签泄漏
if _args.train and _args.predict:
    train = [json.loads(l) for l in Path(_args.train).read_text(encoding="utf-8").splitlines() if l.strip()]
    test = [json.loads(l) for l in Path(_args.predict).read_text(encoding="utf-8").splitlines() if l.strip()]
    model = make_pipeline(
        TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), max_features=30000, min_df=1),
        LogisticRegression(max_iter=2000, C=5, class_weight="balanced"),
    )
    model.fit([r["desc"] for r in train], [r["label"] for r in train])
    classes = list(model.classes_)
    proba = model.predict_proba([r["desc"] for r in test])
    out = {}
    for i, r in enumerate(test):
        order = np.argsort(proba[i])[::-1]
        out[r["id"]] = [{"label": classes[j], "prob": round(float(proba[i][j]), 4)} for j in order[:3]]
    Path(_args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    top1 = sum(1 for i, r in enumerate(test)
               if out[r["id"]] and out[r["id"]][0]["label"] == r["label"])
    print(f"train={len(train)} test={len(test)} top1 {top1/len(test):.2%} -> {_args.out}")
    raise SystemExit(0)

# v3 新表有 94 条无真值样本（暗区 82 + dnfm 12），若直接训练会把 "" 当成一个类别
# （体量排第三），严重污染概率分布。这里：有真值的走 LOO/KFold，无真值的
# 用全量有真值样本拟合后直接预测。
labeled = [r for r in rows if (r.get("label") or "").strip()]
unlabeled = [r for r in rows if not (r.get("label") or "").strip()]

X = [r["desc"] for r in labeled]
y = [r["label"] for r in labeled]
labels = sorted(set(y))


def _make_model():
    return make_pipeline(
        TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), max_features=30000, min_df=1),
        LogisticRegression(max_iter=2000, C=5, class_weight="balanced"),
    )


model = _make_model()
if _args.cv == "kfold":
    from sklearn.model_selection import KFold
    cv = KFold(n_splits=5, shuffle=True, random_state=42)
else:
    cv = LeaveOneOut()
proba = cross_val_predict(model, X, y, cv=cv, method="predict_proba", n_jobs=-1)
classes = sorted(set(y))

out = {}
top1 = top3 = 0
for i, r in enumerate(labeled):
    order = np.argsort(proba[i])[::-1]
    cand = [{"label": classes[j], "prob": round(float(proba[i][j]), 4)} for j in order[:3]]
    out[r["id"]] = cand
    if cand[0]["label"] == r["label"]:
        top1 += 1
    if any(c["label"] == r["label"] for c in cand):
        top3 += 1

# 无真值样本：用全量有真值样本拟合后预测（不存在泄露，它们不参与训练也不参与评测）
if unlabeled:
    full = _make_model()
    full.fit(X, y)
    fclasses = list(full.classes_)
    fproba = full.predict_proba([r["desc"] for r in unlabeled])
    for i, r in enumerate(unlabeled):
        order = np.argsort(fproba[i])[::-1]
        out[r["id"]] = [{"label": fclasses[j], "prob": round(float(fproba[i][j]), 4)}
                        for j in order[:3]]

Path(_args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"有真值 {len(labeled)} 条：top1 {top1/len(labeled):.2%}  top3 {top3/len(labeled):.2%}"
      f"｜无真值 {len(unlabeled)} 条已补全量模型预测  -> {_args.out}")
