"""RAG 检索质量评测 —— 哈希 embedding vs BGE 语义 embedding 对比实验

评测口径：
  - exact:      query = 案例症状原文。同分布自评（query 即入库文档本身），
                历史口径，必然偏高，仅作为基线参考。
  - paraphrase: query = 独立改写的运维口语化描述，刻意规避案例原文关键词，
                模拟真实告警与知识库措辞不一致的场景，考察语义泛化能力。

评测指标：
  - HitRate@k：相关案例出现在前 k 条结果的比例
  - MRR（Mean Reciprocal Rank）：第一个相关结果排名倒数的均值

用法：
    # 全量对比（推荐，输出四象限对比表）
    python scripts/rag_eval.py

    # 只测某个 backend / 口径
    python scripts/rag_eval.py --backends hashing --mode paraphrase
    python scripts/rag_eval.py --backends bge --mode exact --top-k 3

说明：
  - 评测在独立临时目录建库，不污染生产 data/chroma。
  - bge backend 需要 sentence-transformers（pip install sentence-transformers），
    首次运行会下载 bge-small-zh-v1.5 模型（约 100MB，CPU 可跑）。
"""

import argparse
import sys
import tempfile
from pathlib import Path

# 允许以脚本方式直接运行（项目根目录加入 sys.path）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.services.knowledge_base import (
    BUILTIN_CASES,
    KnowledgeBaseService,
)


# ── 评测集 ──────────────────────────────────────────────────
# exact 口径：(query=症状原文, ground_truth_title)，排除兜底案例「通用故障排查」
EXACT_EVAL_SET = [
    (c["symptom"], c["title"])
    for c in BUILTIN_CASES
    if c.get("symptom") and c["title"] != "通用故障排查"
]

# paraphrase 口径：人工独立改写（语义同、字面异，规避原文关键词），
# 模拟真实告警描述与知识库措辞不一致的场景
PARAPHRASE_EVAL_SET = [
    ("应用报数据库拒绝建立新会话，提示已达连接上限，业务大面积登录失败", "MySQL 连接数打满"),
    ("读写分离架构里读实例的数据一直追不上主库，堆积越拉越大", "MySQL 主从延迟"),
    ("缓存集群内存用光，写命令全部失败", "Redis OOM 拒绝写入"),
    ("主机剩余容量告急，日志文件写不进去", "磁盘空间不足"),
    ("服务器负载飙到顶，接口耗时明显拉长，页面半天刷不出来", "应用 CPU 使用率过高"),
]


def evaluate(service: KnowledgeBaseService, queries: list[tuple[str, str]], top_k: int):
    """返回 (hit_rate, mrr, n, miss_detail)"""
    hits = 0
    reciprocal_ranks: list[float] = []
    misses: list[tuple[str, str, list[str]]] = []

    for q_text, gt_title in queries:
        results = service.query(q_text, top_k=top_k)
        ranked_titles = [r.get("title", "") for r in results]
        if gt_title in ranked_titles:
            hits += 1
            rank = ranked_titles.index(gt_title) + 1
            reciprocal_ranks.append(1.0 / rank)
        else:
            reciprocal_ranks.append(0.0)
            misses.append((q_text, gt_title, ranked_titles))

    n = len(queries)
    hit_rate = hits / n if n else 0.0
    mrr = sum(reciprocal_ranks) / n if n else 0.0
    return hit_rate, mrr, n, misses


def main():
    parser = argparse.ArgumentParser(description="MindBridge RAG 检索质量对比评测")
    parser.add_argument("--top-k", type=int, default=3, help="评测召回 top_k（默认 3）")
    parser.add_argument(
        "--backends", type=str, default="hashing,bge",
        help="参与对比的 backend，逗号分隔（默认 hashing,bge）",
    )
    parser.add_argument(
        "--mode", type=str, default="both", choices=["exact", "paraphrase", "both"],
        help="评测口径（默认 both：两种口径都测）",
    )
    parser.add_argument("--show-miss", action="store_true", help="打印未命中的明细")
    args = parser.parse_args()

    backends = [b.strip() for b in args.backends.split(",") if b.strip()]
    modes = ["exact", "paraphrase"] if args.mode == "both" else [args.mode]

    eval_sets = {
        "exact": EXACT_EVAL_SET,
        "paraphrase": PARAPHRASE_EVAL_SET,
    }

    # 每个backend在独立临时目录建库，互不污染、可重复执行
    tmp_root = Path(tempfile.mkdtemp(prefix="rag_eval_"))
    rows: list[tuple[str, str, int, float, float]] = []
    all_misses: list[tuple[str, str, str, list[str]]] = []

    for backend in backends:
        svc = KnowledgeBaseService(
            persist_dir=str(tmp_root / backend),
            embedding_backend=backend,
        )
        svc._ensure()
        effective = svc.backend_name
        if svc._collection is None:
            print(f"[跳过] backend={backend} 初始化失败: {svc._init_error}")
            continue
        if effective != backend:
            print(f"[降级] 请求 backend={backend}，实际生效={effective}"
                  f"（bge 模型不可用？）")

        for mode in modes:
            hit_rate, mrr, n, misses = evaluate(svc, eval_sets[mode], args.top_k)
            rows.append((effective, mode, n, hit_rate, mrr))
            for q, gt, got in misses:
                all_misses.append((effective, mode, q, gt, got))  # type: ignore[arg-type]

    # ── 输出对比表 ──
    print("=" * 64)
    print("RAG 检索质量对比实验（哈希 embedding vs BGE 语义 embedding）")
    print("=" * 64)
    print(f"{'backend':<10} {'口径':<12} {'样本':<4} {'HitRate@%d':<14} {'MRR@%d':<10}"
          % (args.top_k, args.top_k))
    print("-" * 64)
    for backend, mode, n, hit_rate, mrr in rows:
        print(f"{backend:<10} {mode:<12} {n:<4} {hit_rate:<14.4f} {mrr:<10.4f}")
    print("=" * 64)
    print("口径说明:")
    print("  exact      = query 为案例症状原文（同分布，基线参考，必然偏高）")
    print("  paraphrase = query 为独立改写的运维描述（规避原文关键词，考察语义泛化）")

    if all_misses:
        print(f"\n未命中明细（共 {len(all_misses)} 条）:")
        for backend, mode, q, gt, got in all_misses:
            print(f"  [{backend}/{mode}] query: {q}")
            print(f"    期望: {gt}  实际 top{args.top_k}: {got}")

    print("\n结论提示：对比 paraphrase 口径下两种 backend 的差距，")
    print("即为语义向量相对词法哈希的泛化增益（可直接写入简历/报告）。")


if __name__ == "__main__":
    main()
