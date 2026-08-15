"""RAG 检索质量评测脚本 —— 基于内置故障案例构造评测集

评测指标：
  - HitRate@k：相关案例出现在前 k 条结果的比例
  - MRR（Mean Reciprocal Rank）：第一个相关结果排名倒数的均值

用法：
    python scripts/rag_eval.py [--top-k 3] [--queries N]

说明：
  评测集由 BUILTIN_CASES 自动构造——每个案例的「症状」作为查询，
  对应案例本身作为 ground truth（正样本）。query 命中自身即视为命中。
  Chroma 不可用时自动降级为规则检索（关键词匹配），评测逻辑一致。
"""

import argparse
import sys
from pathlib import Path

# 允许以脚本方式直接运行（项目根目录加入 sys.path）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.knowledge_base import (
    BUILTIN_CASES,
    KnowledgeBaseService,
    HashingEmbeddingFunction,
)


def _build_queries(cases: list[dict], limit: int = 0) -> list[tuple[str, str]]:
    """构造 (query_text, ground_truth_title) 评测对"""
    pairs = [(c["symptom"], c["title"]) for c in cases if c.get("symptom")]
    if limit and limit < len(pairs):
        pairs = pairs[:limit]
    return pairs


def evaluate(service: KnowledgeBaseService, top_k: int, queries: list[tuple[str, str]]):
    hits = 0
    reciprocal_ranks: list[float] = []

    for q_text, gt_title in queries:
        results = service.query(q_text, top_k=top_k)
        ranked_titles = [r.get("title", "") for r in results]
        # 命中：ground truth 出现在前 k
        if gt_title in ranked_titles:
            hits += 1
            rank = ranked_titles.index(gt_title) + 1
            reciprocal_ranks.append(1.0 / rank)
        else:
            reciprocal_ranks.append(0.0)

    n = len(queries)
    hit_rate = hits / n if n else 0.0
    mrr = sum(reciprocal_ranks) / n if n else 0.0
    return hit_rate, mrr, n


def main():
    parser = argparse.ArgumentParser(description="MindBridge RAG 检索质量评测")
    parser.add_argument("--top-k", type=int, default=3, help="评测召回 top_k（默认 3）")
    parser.add_argument("--queries", type=int, default=0,
                        help="截取前 N 条评测对（0=全部，默认 0）")
    args = parser.parse_args()

    service = KnowledgeBaseService()
    # 触发初始化（Chroma 或规则降级）
    service._ensure()

    eval_pairs = _build_queries(BUILTIN_CASES, limit=args.queries)
    if not eval_pairs:
        print("无可用评测样本（BUILTIN_CASES 为空或缺少 symptom 字段）")
        sys.exit(1)

    hit_rate, mrr, n = evaluate(service, args.top_k, eval_pairs)

    backend = "Chroma 向量检索" if service._collection is not None else "规则检索降级"
    print("=" * 56)
    print("RAG 检索质量评测")
    print("=" * 56)
    print(f"评测后端   : {backend}")
    print(f"embedding  : {HashingEmbeddingFunction.name()}")
    print(f"评测样本数 : {n}")
    print(f"top_k      : {args.top_k}")
    print("-" * 56)
    print(f"HitRate@{args.top_k:<2}: {hit_rate:.4f}")
    print(f"MRR@{args.top_k:<2}     : {mrr:.4f}")
    print("=" * 56)
    print("\n提示：将上方 HitRate / MRR 填入简历「Engineering Harness」条目的真实指标。")


if __name__ == "__main__":
    main()
