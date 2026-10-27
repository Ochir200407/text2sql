from collections import Counter

from mn_text2sql.eval.dataset import EvalItem
from mn_text2sql.eval.split import assign_splits
from mn_text2sql.pipeline.router import SemanticRouter, load_metrics
from mn_text2sql.retrieval.indexes import ValueEntry


def _items():
    out = []
    for d in ("loans", "deposits"):
        for i in range(50):
            fam = f"{d}-f{i // 2}" if i < 20 else f"{d}-f{i}"
            cat = "unanswerable" if i % 10 == 0 else ("ambiguous" if i % 10 == 1 else "answerable")
            out.append(EvalItem(id=f"{d}{i}", family=fam, category=cat, difficulty=["easy", "medium", "hard"][i % 3],
                                question_mn="q", domain=d))
    return out


def test_split_keeps_families_and_size():
    items = _items()
    sp = assign_splits(items, n_dev=60)
    assert Counter(sp.values()) == {"dev": 60, "test": 40}
    fam = {}
    for it in items:
        assert fam.setdefault(it.family, sp[it.id]) == sp[it.id]
    dev_un = sum(1 for it in items if it.category == "unanswerable" and sp[it.id] == "dev")
    assert 4 <= dev_un <= 8


class _R(SemanticRouter):
    def __init__(self, cfg):
        self.cfg, self.spec, self.cache, self.today, self.llm = cfg, load_metrics(), {}, "2026-06-30", None
        self._values = [ValueEntry(table="branches", column="branch_name", value="Эрдэнэт салбар"),
                        ValueEntry(table="branches", column="branch_name", value="Дархан салбар")]


def test_router_compile(cfg):
    r = _R(cfg)
    sql, why = r.compile({"metric": "par30", "dimensions": ["branch"]})
    assert why == "ok" and "GROUP BY 1" in sql and "is_active_portfolio" in sql
    sql, why = r.compile({"metric": "disbursement_count", "filters": [{"dimension": "branch", "value": "erdenet"}],
                          "time": {"from": "2025-01-01", "to": "2025-12-31"}})
    assert "'Эрдэнэт салбар'" in sql and "BETWEEN DATE '2025-01-01'" in sql
    assert r.compile({"metric": "par30", "dimensions": ["month"]})[0] is None        # агшин metric-д сар биш
    assert r.compile({"metric": "par30", "filters": [{"dimension": "branch", "value": "Хятад"}]})[0] is None
