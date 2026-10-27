"""F3: query log-оор сургасан галиг хувиргагч (туршилтын төлөвлөгөө, 2-р санаа: "дараа нь query log-ийн хосоор
(галиг оролт → хэрэглэгчийн зөвшөөрсөн кирилл утга) сургасан жижиг хувиргагч").

Сургалт: service-ийн audit (шифрлэсэн асуулт + SQL) ба feedback (correct = true)-ийг request_id-аар холбож, галигаар
бичсэн асуултын үг ↔ зөвшөөрсөн SQL-ийн кирилл утгын хосыг гаргана (сул түлхүүр + fuzzy зэрэгцүүлэлт). Хосыг
build/translit_pairs.jsonl-д хадгалж, retrieval-ийн value_mode=learned үед Transliterator-т өндөр жинтэй нэмнэ
(дүрмийн хувиргалтаас түрүүлж хэрэглэгдэнэ). Мөн хосоос тэмдэгтийн n-gram орлуулалтын хүснэгт сурч, үгийн санд
байхгүй шинэ галиг үгэнд хэрэглэнэ.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from rapidfuzz import fuzz

from ..config import Config, resolve_path
from .normalize import CYR, latin_to_cyrillic_rule, script_of, tokens, weak_latin_key

PAIRS_FILE = "build/translit_pairs.jsonl"


def pairs_path(cfg: Config | None = None) -> Path:
    return resolve_path(PAIRS_FILE)


def extract_pairs(question: str, sql: str) -> list[tuple[str, str]]:
    """Галиг асуултын үг ↔ SQL-ийн кирилл текст утгын үг (сул латин түлхүүрийн ойролцоогоор)."""
    if script_of(question) not in ("latin", "mixed"):
        return []
    values = re.findall(r"'([^']{2,80})'", sql or "")
    cyr_tokens = [t for v in values for t in tokens(v) if any(c in CYR for c in t.lower())]
    out = []
    for lt in tokens(question):
        if any(c in CYR for c in lt.lower()) or len(lt) < 3 or not lt.isalpha():
            continue
        best, best_c = 0, None
        for ct in cyr_tokens:
            s = fuzz.ratio(weak_latin_key(lt), weak_latin_key(ct))
            if s > best:
                best, best_c = s, ct
        if best_c and best >= 80:
            out.append((lt.lower(), best_c.lower()))
    return out


def build_pairs_from_logs(cfg: Config) -> dict:
    """audit.jsonl + feedback.jsonl (service) → build/translit_pairs.jsonl. T2S_AUDIT_KEY шаардлагатай."""
    from ..service.api import AuditLog
    audit_path = resolve_path(cfg["security"]["audit_log"])
    fb_path = audit_path.with_name("feedback.jsonl")
    if not audit_path.exists() or not fb_path.exists():
        return {"pairs": 0, "note": "audit/feedback лог алга (service-ийг shadow/pilot горимд ажиллуулж хуримтлуулна)"}
    log = AuditLog(audit_path, cfg["security"]["audit_encrypt_key_env"], int(cfg["security"]["audit_retention_days"]))
    accepted = set()
    for line in fb_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            if d.get("correct"):
                accepted.add(d["request_id"])
    counts: Counter = Counter()
    for line in audit_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("request_id") not in accepted or not d.get("question") or not d.get("sql"):
            continue
        try:
            q = log.decrypt(d["question"])
        except Exception:
            continue
        for lat, cyr in extract_pairs(q, d["sql"]):
            counts[(lat, cyr)] += 1
    path = pairs_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for (lat, cyr), n in counts.most_common():
            fh.write(json.dumps({"latin": lat, "cyrillic": cyr, "count": n}, ensure_ascii=False) + "\n")
    return {"pairs": len(counts), "file": str(path), "accepted_requests": len(accepted)}


class NgramRewriter:
    """Хосоос сурсан латин n-gram → кирилл n-gram орлуулалт (дүрмийн хувиргалтын алдааг засна)."""

    def __init__(self, pairs: list[tuple[str, str]]):
        table: dict[str, Counter] = defaultdict(Counter)
        for lat, cyr in pairs:
            rule = latin_to_cyrillic_rule(lat)
            if rule == cyr or abs(len(rule) - len(cyr)) > 2:
                continue
            # дүрмийн гаралт ба зөв үгийн ялгаатай хэсгийг (эхний/сүүлийн зөрүү) бүртгэнэ
            i = 0
            while i < min(len(rule), len(cyr)) and rule[i] == cyr[i]:
                i += 1
            j = 0
            while j < min(len(rule), len(cyr)) - i and rule[-1 - j] == cyr[-1 - j]:
                j += 1
            wrong, right = rule[i: len(rule) - j], cyr[i: len(cyr) - j]
            if wrong and len(wrong) <= 3:
                table[wrong][right] += 1
        self.table = {w: c.most_common(1)[0][0] for w, c in table.items() if c.most_common(1)[0][1] >= 2}

    def apply(self, cyr_guess: str) -> str:
        for wrong, right in sorted(self.table.items(), key=lambda kv: -len(kv[0])):
            cyr_guess = cyr_guess.replace(wrong, right)
        return cyr_guess


def load_learned(cfg: Config | None = None) -> tuple[list[tuple[str, str, int]], NgramRewriter | None]:
    path = pairs_path(cfg)
    if not path.exists():
        return [], None
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    pairs = [(r["latin"], r["cyrillic"], int(r.get("count", 1))) for r in rows]
    return pairs, NgramRewriter([(lat, cyr) for lat, cyr, _ in pairs])
