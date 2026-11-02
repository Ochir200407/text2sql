"""FK-гүүр (schema linking): холбогдсон хүснэгтүүдийн хооронд FK-ийн богино замын завсрын хүснэгтийг нэмнэ.

Retrieval-ийн шилдэг хүснэгтүүд (a, b) шууд FK-гүй бол хамгийн «хямд» замыг (≤ max_path ирмэг) олж, prompt-д
байхгүй завсрын хүснэгтийг нэмнэ. Хоёулаа заадаг эцэг хүснэгтээр (a → customers ← b) дамжих зам утгын хувьд
сул (олон-олон холбоо) тул торгуультай; хоёуланг нь заадаг холбоос хүснэгтийг (loans ← loan_collaterals →
collaterals) илүүд үзнэ. Нэмэлтээр шилдэг хүснэгтийн 1 алхмын FK хөршийг оноогоор нь (заавал биш) нэмнэ.
Нийт нэмэлт max_add-аар хязгаарлагдана (prompt-ийн өсөлт). Офлайн үнэлгээ: retrieval/fk_bridge_eval.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

VALLEY_PENALTY = 0.5    # a → x ← b (x нь хоёулангийнх нь эцэг) завсрын торгууль


@dataclass(frozen=True)
class BridgeParams:
    """Анхдагч утгыг dev дээр тааруулсан (fk_bridge_eval --grid; docs/ACCURACY_PLAN.md)."""
    bridge_top: int = 3      # эхний хэдэн хүснэгтийн хос бүрийг гүүрээр холбох
    max_path: int = 2        # замын дээд урт (ирмэгээр); 3 нь dev-д нэмэлт ахицгүй
    hop_top: int = 2         # 1 алхмын хөршийг хэдэн шилдэг хүснэгтээс авах (0 = зөвхөн гүүр)
    hop_k: int = 1           # 1 алхмаар нэмэх хөршийн дээд тоо
    hop_min_score: float = 0.5  # хөршийн retrieval оноо үүнээс их байх ёстой (0..1)
    max_add: int = 2         # нийт нэмэх хүснэгтийн дээд тоо


class FKGraph:
    """semantic_model-ийн FK-ээс: refs[t] = t-ийн заадаг (эцэг) хүснэгтүүд; adj = чиглэлгүй хөрш."""

    def __init__(self, refs: dict[str, set[str]]):
        self.refs = {t: set(p) - {t} for t, p in refs.items()}
        self.adj: dict[str, set[str]] = {t: set() for t in self.refs}
        for t, ps in self.refs.items():
            for p in ps:
                self.adj.setdefault(t, set()).add(p)
                self.adj.setdefault(p, set()).add(t)

    @classmethod
    def from_model(cls, model) -> "FKGraph":
        return cls({t: {target for _, target, _ in tab.fks() if target in model.tables}
                    for t, tab in model.tables.items()})

    def _cost(self, path: list[str]) -> float:
        cost = float(len(path) - 1)
        for prev, x, nxt in zip(path, path[1:], path[2:]):
            if x in self.refs.get(prev, ()) and x in self.refs.get(nxt, ()):
                cost += VALLEY_PENALTY
        return cost

    def paths(self, a: str, b: str, max_len: int) -> list[list[str]]:
        """a-аас b хүртэлх max_len хүртэл ирмэгтэй энгийн замууд (28 хүснэгтэд DFS хангалттай)."""
        out: list[list[str]] = []

        def dfs(path: list[str]) -> None:
            cur = path[-1]
            if cur == b:
                out.append(list(path))
                return
            if len(path) - 1 >= max_len:
                return
            for n in sorted(self.adj.get(cur, ())):
                if n not in path:
                    path.append(n)
                    dfs(path)
                    path.pop()

        if a in self.adj and b in self.adj:
            dfs([a])
        return out

    def best_path(self, a: str, b: str, max_len: int, present: set[str] = frozenset(),
                  scores: dict[str, float] | None = None) -> list[str] | None:
        """Хамгийн бага өртөгтэй зам; тэнцвэл аль хэдийн байгаа, дараа нь оноо өндөр завсартай."""
        scores = scores or {}
        best, key = None, None
        for p in self.paths(a, b, max_len):
            mid = p[1:-1]
            k = (self._cost(p), sum(m not in present for m in mid), -sum(scores.get(m, 0.0) for m in mid), mid)
            if key is None or k < key:
                best, key = p, k
        return best


def fk_expand(graph: FKGraph, linked: list[str], core_k: int, scores: dict[str, float] | None = None,
              params: BridgeParams = BridgeParams()) -> tuple[list[str], list[str]]:
    """linked (эрэмбэтэй; эхний core_k нь retrieval-ийн шилдэг) → (шинэ жагсаалт, нэмэгдсэн хүснэгтүүд).

    Нэмэгдсэнийг төгсгөлд нь залгана — анхны эрэмбэ, хүснэгтүүд өөрчлөгдөхгүй."""
    scores = scores or {}
    have = list(linked)
    present = set(have)
    added: list[str] = []

    def add(t: str) -> bool:
        if len(added) >= params.max_add:
            return False
        if t not in present:
            present.add(t)
            added.append(t)
        return True

    core = [t for t in linked[:core_k] if t in graph.adj]
    anchors = core[: params.bridge_top]
    for a, b in combinations(anchors, 2):
        if b in graph.adj[a]:
            continue
        path = graph.best_path(a, b, params.max_path, present, scores)
        for m in (path or [])[1:-1]:
            if not add(m):
                break
    if params.hop_top > 0 and params.hop_k > 0:
        n_hop = 0
        for t in core[: params.hop_top]:
            cand = sorted((n for n in graph.adj[t] if n not in present and scores.get(n, 0.0) > params.hop_min_score),
                          key=lambda n: (-scores.get(n, 0.0), n))
            for n in cand:
                if n_hop >= params.hop_k or not add(n):
                    break
                n_hop += 1
    return have + added, added
