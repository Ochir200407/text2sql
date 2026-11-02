"""Kaggle-ийн гаралтын хавтсыг (V26, V27) төсөлд оруулж, дүн, McNemar, Wilson CI, шийдвэрийг гаргана.

    python scripts/ingest/ingest_results.py --src <татсан хавтас> [--tag V26|V27] [--out docs/RESULTS_<tag>.md]
        [--dry-run] [--force] [--root .] [--headline M1ens] [--exclude-ids results/sensitivity/touched13.txt ...]
        [--pair D2:H4 ...]

--tag (анхдагч V26) нь хувилбарын урьдчилсан тохиргоог (PRESETS: McNemar хос, эрэмбэ, headline, шийдвэр, тайлангийн split)
сонгоно: V26 = эцсийн test (headline M1ens, PICK_TEST шийдвэр); V27 = зөвхөн dev (M1ensx ↔ M1ensx0 шийдвэр, шинэ үүсгэгч бүр
↔ M1a; M1ensra нь «§ in-sample» — хос/шийдвэрт ороогүй). --out анхдагч docs/RESULTS_<tag>.md.

1. <src> дотроос run-уудыг олно: бүтэн JSONL (results/test/<ID>/*.jsonl = test, results/<ID>/*.jsonl = dev, мөрийн
   `split` талбар давуу), товч *_kaggle_q.txt (compact_results.py-ийн `Q|...` мөр; notebook-ийн лог дахь
   `[compact] <зам> n=..` толгойгоор олон run-д хуваана), *_answerable_correct.txt (зөвхөн зөв хариулсан id).
   (split, ID) бүрд нэг run: src > төслийн results/runs (шинэ гаралтыг хуучин локал файл давахгүй), дараа нь
   шалгагдсан бүрэн > бусад, JSONL > товч > зөв id. results/runs/<split>/<ID>/ руу хуулна (байгаа файлыг --force-гүй
   дарахгүй).
2. Шалгалт: мөрийн тоо (dev 300, test 200) ба ангилал (dev 224/38/38, test 151/24/25). Дутуу/буруу run-ийг --force-гүй
   бол хуулахгүй, статистик, McNemar, LaTeX-д ОРУУЛАХГҮЙ (шалтгааныг хэвлэнэ); --force бол «*» тэмдэгтэй оруулна.
   Зөв id-ийн жагсаалт («†») дутуу асуултаа харуулдаггүй тул бүрэн эсэх нь шалгагдахгүй: зөвхөн хариулах боломжтой
   асуулт дээр, нийт EX ба нийт McNemar-д хэзээ ч ашиглахгүй.
3. Run бүрийн нийт EX, ангилал (answerable/unanswerable/ambiguous), түвшин ба домэйн (хариулах боломжтой дээр);
   CI бүр Wilson 95% (score интервал). «Үргэлж тодруулах» суурь мөр: хариулах боломжгүй ба хоёрдмол бүгд зөв,
   хариулах бүгд буруу (test 49/200, dev 76/300). Товч хэлбэрт түвшин/домэйн байхгүй тул ижил split-ийн бүтэн
   JSONL-ээс question_id-аар нөхнө.
4. McNemar exact: p = min(1, 2·P(X ≤ min(b,c))), X ~ Bin(b+c, 0.5). Нийт McNemar бүрийн хажууд хариулах боломжтой
   асуулт дээрх McNemar ба ангилал бүрийн +b/−c тоо.
5. Headline = урьдчилан зарласан тохиргоо (--headline, анхдагч preset-ийнх: V26 M1ens; V27 — test-гүй тул байхгүй);
   алга/хасагдсан бол headline хэвлэхгүй (хамгийн өндрөөр орлуулахгүй). Max-д суурилсан мөр бүр «post hoc» гэж тэмдэглэгдэнэ.
6. --exclude-ids FILE (давтагдана): run бүрийн EX ба McNemar хосыг тэдгээр id-тай ба id-гүйгээр (sensitivity).
7. docs/RESULTS_<tag>.md (монгол; --out), Ch6-ийн LaTeX tabular ба слайд 11/16-ийн текстийг (test-тэй preset-д) хэвлэнэ
   (.tex/.pptx-ийг хөндөхгүй).
8. Шийдвэр (docs/ACCURACY_PLAN.md): V26 — dev M1ensp > M1ens (хариулах, McNemar p < 0.05) бол л PICK_TEST=1;
   V27 — dev M1ensx > M1ensx0 (ижил сессийн кодоор тооцсон M1ens-ийн жор; хариулах, p < 0.05) бол л шинэ гишүүдийг V28-ийн
   test-д. In-sample run (§, жишээ нь M1ensra: abstain v2-ийн жин бүх dev дээр) хэзээ ч шийдвэрт орохгүй.
Зөвхөн stdlib; загвар, DB, сүлжээ ашиглахгүй.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

ROOT_DEFAULT = Path(__file__).resolve().parents[2]
EXPECTED_CAT = {"dev": {"answerable": 224, "unanswerable": 38, "ambiguous": 38},
                "test": {"answerable": 151, "unanswerable": 24, "ambiguous": 25}}
EXPECTED_N = {sp: sum(c.values()) for sp, c in EXPECTED_CAT.items()}      # dev 300, test 200
EXPECTED_ANS = {sp: c["answerable"] for sp, c in EXPECTED_CAT.items()}     # dev 224, test 151
CATS = ("answerable", "unanswerable", "ambiguous")
CAT_MN = {"answerable": "Хариулах", "unanswerable": "Боломжгүй", "ambiguous": "Хоёрдмол"}
CAT_LBL = {"answerable": "хариулах", "unanswerable": "боломжгүй", "ambiguous": "хоёрдмол"}
TEST_PAIRS = [("M1a", "M1r"), ("M1a", "M1ens"), ("M1a", "M1ensr"), ("I3t", "M1ensr"), ("M1ens", "M1ensr")]
DEV_PAIRS = [("M1ens", "M1ensp"), ("M1ensr", "M1enspr")]
TEST_ORDER = ["I3t", "M1a", "M1ks", "M1rr", "M1g8", "M1m", "M1r", "M1ens", "M1ensr", "M1ensp", "M1enspr"]
DEV_ORDER = ["M1a", "M1ks", "M1rr", "M1g8", "M1m", "M1ens", "M1ensr", "M1ensp", "M1enspr"]
HEADLINE_DEFAULT = "M1ens"     # docs/ACCURACY_PLAN.md: эцсийн test-ийн урьдчилан зарласан тохиргоо
# V27 (dev): шинэ үүсгэгч бүр ↔ M1a; M1ensx ↔ M1ensx0 (run_plan ensx: M1ens-ийн жор ИЖИЛ сессийн кодоор); M1ens (V26-ийн код) ↔
# M1ensx0 нь кодын зөрүүг шалгах (хүлээгдэх +0/−0). M1ensra = in-sample (abstain v2-ийн жин бүх dev дээр) — хосгүй.
V27_NEW = ["M1ip", "M1fs", "M1vf", "M1rt", "M1cte", "M1th", "M1dec"]
PRESETS: dict[str, dict] = {
    "V26": {"test_pairs": TEST_PAIRS, "dev_pairs": DEV_PAIRS, "test_order": TEST_ORDER, "dev_order": DEV_ORDER,
            "headline": HEADLINE_DEFAULT, "decision": "pick", "in_sample": (), "splits": ("test", "dev")},
    "V27": {"test_pairs": [],
            "dev_pairs": [("M1ensx0", "M1ensx"), ("M1ensxr0", "M1ensxr")] + [("M1a", x) for x in V27_NEW]
                         + [("M1ens", "M1ensx0")],
            "test_order": TEST_ORDER,
            "dev_order": ["M1a", "M1ks", "M1rr", "M1g8", "M1m", "M1ens", "M1ensr"] + V27_NEW
                         + ["M1ensx0", "M1ensx", "M1ensxr0", "M1ensxr", "M1ensra"],
            "headline": None, "decision": "ensx", "in_sample": ("M1ensra",), "splits": ("dev",)},
}
BASELINE = "Үргэлж тодруулах (суурь)"
CI_NAME = "Wilson 95% CI"
DAGGER, STAR, SECT = "†", "*", "§"
NOTE_DAGGER = (f"{DAGGER} зөв id-ийн жагсаалт (*_answerable_correct.txt): зөвхөн хариулах боломжтой асуулт; дутуу "
               "асуулт харагдахгүй тул бүрэн эсэх нь шалгагдахгүй — нийт EX ба нийт McNemar-д ороогүй")
NOTE_STAR = f"{STAR} дутуу/буруу run, --force-оор оруулсан (n нь бодит мөрийн тоо, McNemar нь давхцсан асуулт дээр)"
NOTE_SECT = (f"{SECT} in-sample: тохиргоо/жин нь энэ dev дээр сургагдсан (жишээ нь abstain v2-ийн DEFAULT_WEIGHTS) — "
             "өөдрөг тоо, лавлагаа л; McNemar хос ба шийдвэрт ороогүй")
TEX_NOTE = {DAGGER: "зөв id-ийн жагсаалт: зөвхөн хариулах боломжтой асуулт, бүрэн эсэх нь шалгагдахгүй (нийтэд ороогүй)",
            STAR: r"дутуу run (\texttt{-{}-force}-оор оруулсан): n нь бодит мөрийн тоо",
            SECT: "in-sample (dev дээр сургагдсан): лавлагаа л, шийдвэрт ороогүй"}
DIFF_ORDER = {"easy": 0, "medium": 1, "hard": 2, "extra": 3}
KIND_RANK = {"jsonl": 3, "compact": 2, "anscorrect": 1}
Q_RE = re.compile(r"Q\|([^|\s]+)\|([^|]*)\|([^|]*)\|([01])\|([^|]*)\|([01]*)")
HDR_RE = re.compile(r"\[compact\]\s+(\S+)\s+n=(\d+)")


# --------------------------------------------------------------------------- статистик
def mcnemar_exact(b: int, c: int) -> float:
    """Exact (binomial) McNemar: p = min(1, 2·P(X ≤ min(b,c))), X ~ Bin(b+c, 0.5)."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """Wilson (score) 95% интервал."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, mid - half), min(1.0, mid + half))


def fmt_p(p: float) -> str:
    """p-утга 3 тэмдэгт цифрээр (жижиг p «0.0» болохгүй): 0.117, 0.00635, 8.62e-04, 1.05e-11; p = 1 → «1»."""
    return f"{p:.3g}" if p >= 1e-3 else f"{p:.2e}"


def tex_p(p: float) -> str:
    s = fmt_p(p)
    if "e" not in s:
        return s
    m, e = s.split("e")
    return f"${m} \\times 10^{{{int(e)}}}$"


def kn(items) -> dict:
    items = list(items)
    k = sum(1 for x in items if x["correct"])
    return {"k": k, "n": len(items), "ci": wilson(k, len(items))}


def kn_counts(k: int, n: int) -> dict:
    return {"k": k, "n": n, "ci": wilson(k, n)}


# --------------------------------------------------------------------------- run
@dataclass
class Run:
    id: str
    split: str
    kind: str                      # jsonl | compact | anscorrect
    path: Path
    origin: str                    # src | local
    rows: dict[str, dict] | None = None
    correct_ids: set[str] | None = None   # anscorrect
    q_lines: list[str] = field(default_factory=list)   # compact: хэвийн болгосон Q мөрүүд
    warnings: list[str] = field(default_factory=list)
    oc: dict[str, dict] | None = None     # prepare(): question_id → {category, difficulty, domain, correct}
    problem: str | None = None            # prepare(): validate()-ийн шалтгаан, None = бүрэн
    in_sample: bool = False               # main(): preset-ийн in_sample (§) — лавлагаа л, шийдвэрт орохгүй

    def load(self) -> Run:
        if self.rows is not None or self.correct_ids is not None:
            return self
        if self.kind == "jsonl":
            self.rows = {}
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    r = json.loads(line)
                    self.rows[str(r["question_id"])] = r
        elif self.kind == "anscorrect":
            self.correct_ids = {x.strip() for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()}
        return self

    @property
    def n(self) -> int:
        self.load()
        return len(self.rows) if self.rows is not None else len(self.correct_ids or ())

    @property
    def answerable_only(self) -> bool:
        """Зөв id-ийн жагсаалт: дутуу асуулт харагдахгүй → зөвхөн хариулах, бүрэн эсэх нь шалгагдахгүй (M2b)."""
        return self.kind == "anscorrect"

    @property
    def verified(self) -> bool:
        """Мөр ба ангиллын тоогоор бүрэн гэж батлагдсан (зөв id-ийн жагсаалт хэзээ ч батлагдахгүй)."""
        return self.problem is None and not self.answerable_only

    def mark(self) -> str:
        return (DAGGER if self.answerable_only else "") + (STAR if self.problem else "") + (SECT if self.in_sample else "")

    def where(self, base: Path) -> str:
        try:
            return self.path.relative_to(base).as_posix()
        except ValueError:
            return self.path.as_posix()


def parse_jsonl_meta(path: Path) -> tuple[str | None, str | None, int] | None:
    """(config_id, split, мөрийн тоо) — эхний мөрөөс; run биш JSONL бол None."""
    try:
        with path.open(encoding="utf-8") as f:
            first = f.readline()
            n = 1 + sum(1 for x in f if x.strip()) if first.strip() else 0
        r = json.loads(first)
    except (OSError, ValueError):
        return None
    if not isinstance(r, dict) or "question_id" not in r or "correct" not in r:
        return None
    return r.get("config_id"), r.get("split"), n


def split_from_path(p: Path) -> str | None:
    parts = [x.lower() for x in p.parts]
    if "test" in parts:
        return "test"
    if "dev" in parts:
        return "dev"
    return None


def id_from_runpath(s: str) -> tuple[str | None, str | None]:
    """'results/runs/test/M1ens/x.jsonl' → ('M1ens', 'test')."""
    p = Path(s.replace("\\", "/"))
    parts = list(p.parts)
    if len(parts) >= 2:
        rid = parts[-2]
        return rid, split_from_path(p) or "dev"
    return None, None


def parse_compact_text(text: str) -> list[tuple[str | None, str | None, list[tuple]]]:
    """Текстийг [(ID|None, split|None, [Q мөрүүд])] болгоно. `[compact]` толгой байвал түүгээр хуваана."""
    blocks: list[tuple[str | None, str | None, list[tuple]]] = []
    cur: tuple[str | None, str | None, list[tuple]] | None = None
    for line in text.splitlines():
        h = HDR_RE.search(line)
        if h:
            rid, sp = id_from_runpath(h.group(1))
            cur = (rid, sp, [])
            blocks.append(cur)
            continue
        m = Q_RE.search(line)
        if m:
            if cur is None:
                cur = (None, None, [])
                blocks.append(cur)
            cur[2].append(m.groups())
    return [b for b in blocks if b[2]]


def compact_rows(qs: list[tuple], warnings: list[str]) -> tuple[dict[str, dict], list[str]]:
    rows: dict[str, dict] = {}
    for qid, cat, status, ok, conf, _cand in qs:
        if qid in rows:
            warnings.append(f"давхардсан {qid} (сүүлийнхийг авав)")
        rows[qid] = {"question_id": qid, "category": cat, "status": status, "correct": ok == "1",
                     "confidence": float(conf) if conf not in ("", "None") else None}
    lines = [f"Q|{q}|{c}|{s}|{o}|{cf}|{cd}" for q, c, s, o, cf, cd in qs]
    return rows, lines


def discover(base: Path, origin: str) -> list[Run]:
    runs: list[Run] = []
    if not base.exists():
        return runs
    for p in sorted(base.rglob("*.jsonl")):
        meta = parse_jsonl_meta(p)
        if meta is None:
            continue
        cid, sp, _ = meta
        rid = p.parent.name if p.parent.name not in ("results", "test", "dev", "runs") else (cid or p.stem)
        sp = sp if sp in EXPECTED_N else (split_from_path(p.relative_to(base)) or "dev")
        runs.append(Run(rid, sp, "jsonl", p, origin))
    for p in sorted(list(base.rglob("*.txt")) + list(base.rglob("*.log"))):
        name = p.name
        if name.endswith("_answerable_correct.txt"):
            sp = split_from_path(p.relative_to(base))
            if sp:
                runs.append(Run(name[: -len("_answerable_correct.txt")], sp, "anscorrect", p, origin))
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "Q|" not in text:
            continue
        for rid, sp, qs in parse_compact_text(text):
            if rid is None:
                if not name.endswith("_kaggle_q.txt"):
                    continue
                rid = name[: -len("_kaggle_q.txt")]
            if sp is None or HDR_RE.search(text) is None:
                sp = split_from_path(p.relative_to(base))
            if sp is None:
                sp = {200: "test", 300: "dev"}.get(len({q[0] for q in qs}))
            if sp is None:
                print(f"[ingest] анхаар: {p} ({rid}) split тодорхойгүй ({len(qs)} мөр) — алгасав")
                continue
            r = Run(rid, sp, "compact", p, origin)
            r.rows, r.q_lines = compact_rows(qs, r.warnings)
            runs.append(r)
    return runs


# --------------------------------------------------------------------------- мета (түвшин/домэйн/ангилал)
def build_meta(runs: list[Run]) -> dict[str, dict[str, dict]]:
    meta: dict[str, dict[str, dict]] = {"dev": {}, "test": {}}
    for r in sorted((r for r in runs if r.kind == "jsonl"), key=lambda r: r.origin != "src"):
        m = meta.setdefault(r.split, {})
        if len(m) >= EXPECTED_N.get(r.split, 0):
            continue
        for qid, row in r.load().rows.items():
            m.setdefault(qid, {"category": row.get("category"), "difficulty": row.get("difficulty"),
                               "domain": row.get("domain")})
    for r in runs:   # бүтэн JSONL байхгүй бол ангиллыг compact-аас
        if r.kind == "compact":
            for qid, row in r.rows.items():
                meta.setdefault(r.split, {}).setdefault(qid, {"category": row["category"], "difficulty": None,
                                                              "domain": None})
    return meta


def outcomes(run: Run, meta: dict[str, dict]) -> dict[str, dict]:
    """question_id → {category, difficulty, domain, correct}. Зөв id-ийн жагсаалт: мета-гийн хариулах асуулт бүр,
    жагсаалтад байвал зөв (жагсаалтад байхгүй = буруу эсвэл АЖИЛЛААГҮЙ — ялгаж чадахгүй)."""
    run.load()
    out: dict[str, dict] = {}
    if run.kind == "anscorrect":
        for qid, m in meta.items():
            if m.get("category") == "answerable":
                out[qid] = {**m, "correct": qid in run.correct_ids}
        return out
    for qid, row in run.rows.items():
        m = meta.get(qid, {})
        out[qid] = {"category": row.get("category") or m.get("category"),
                    "difficulty": row.get("difficulty") or m.get("difficulty"),
                    "domain": row.get("domain") or m.get("domain"), "correct": bool(row.get("correct"))}
    return out


def validate(run: Run, oc: dict[str, dict]) -> str | None:
    """None = бүрэн; эс бөгөөс шалтгаан. JSONL/товч: мөрийн тоо ба ангиллын тоо. Зөв id-ийн жагсаалт: зөвхөн мета
    бүрэн ба id бүр мэдэгдэх хариулах асуулт эсэх — жагсаалтын өөрийнх нь бүрэн эсэхийг шалгах боломжгүй (M2b)."""
    exp = EXPECTED_CAT.get(run.split)
    if exp is None:
        return f"split «{run.split}» тодорхойгүй"
    if run.kind == "anscorrect":
        bad = []
        if len(oc) != exp["answerable"]:
            bad.append(f"мета-д хариулах {len(oc)} ≠ {exp['answerable']} (ижил split-ийн бүтэн run хэрэгтэй)")
        unknown = sorted(run.correct_ids - set(oc))
        if unknown:
            bad.append(f"{len(unknown)} id хариулах асуултын мета-д алга ({', '.join(unknown[:3])}"
                       f"{'…' if len(unknown) > 3 else ''})")
        return "; ".join(bad) or None
    if run.n != EXPECTED_N[run.split]:
        return f"мөр {run.n} ≠ {EXPECTED_N[run.split]}"
    got = Counter(x["category"] for x in oc.values())
    if any(got.get(c, 0) != k for c, k in exp.items()):
        return ("ангилал " + "/".join(str(got.get(c, 0)) for c in CATS) + " ≠ "
                + "/".join(str(exp[c]) for c in CATS) + " (хариулах/боломжгүй/хоёрдмол)")
    return None


def prepare(runs: list[Run], meta: dict[str, dict[str, dict]]) -> None:
    for r in runs:
        r.oc = outcomes(r, meta.get(r.split, {}))
        r.problem = validate(r, r.oc)


def choose(runs: list[Run]) -> dict[tuple[str, str], Run]:
    """(split, ID) бүрд нэг run: эх сурвалж эхэнд (src > төсөл — шинэ гаралтыг хуучин локал JSONL давахгүй, M2c),
    дараа нь шалгагдсан бүрэн, хэлбэр (JSONL > товч > зөв id), s0, мөрийн тоо."""
    best: dict[tuple[str, str], Run] = {}

    def score(r: Run):
        return (r.origin == "src", r.verified, KIND_RANK[r.kind], r.path.name.endswith("__s0.jsonl"), r.n)

    for r in runs:
        k = (r.split, r.id)
        if k not in best or score(r) > score(best[k]):
            best[k] = r
    return best


# --------------------------------------------------------------------------- хэмжүүр
def stats(run: Run, oc: dict[str, dict] | None = None) -> dict:
    oc = run.oc if oc is None else oc
    full = not run.answerable_only
    ans = [x for x in oc.values() if x["category"] == "answerable"]
    st = {"id": run.id, "split": run.split, "kind": run.kind, "origin": run.origin, "mark": run.mark(),
          "problem": run.problem, "answerable_only": run.answerable_only, "baseline": False, "in_sample": run.in_sample,
          "total": kn(oc.values()) if full else None,
          "cat": {c: kn(x for x in oc.values() if x["category"] == c) for c in CATS} if full else
                 {"answerable": kn(ans)}}
    for key in ("difficulty", "domain"):
        groups: dict[str, list] = {}
        for x in ans:
            groups.setdefault(str(x.get(key) or "?"), []).append(x)
        st[key] = {g: kn(v) for g, v in sorted(groups.items())}
    return st


def clarify_baseline(split: str) -> dict:
    """H3: «үргэлж тодруулах» — асуулт бүрт тодруулга асууна. runner.score_answer-ийн дүрмээр хариулах боломжгүй
    (хариулаагүй) ба хоёрдмол (тодруулсан) бүгд зөв, хариулах боломжтой бүгд буруу: test 49/200, dev 76/300."""
    e = EXPECTED_CAT[split]
    return {"id": BASELINE, "split": split, "kind": "суурь", "origin": "—", "mark": "", "problem": None,
            "answerable_only": False, "baseline": True,
            "total": kn_counts(e["unanswerable"] + e["ambiguous"], EXPECTED_N[split]),
            "cat": {c: kn_counts(0 if c == "answerable" else e[c], e[c]) for c in CATS},
            "difficulty": {}, "domain": {}}


def pair_test(oa: dict[str, dict], ob: dict[str, dict], cat: str | None = None,
              drop: frozenset[str] = frozenset()) -> dict | None:
    """Давхцсан асуулт дээр (cat бол зөвхөн тэр ангилал, drop-ийг хасна) exact McNemar."""
    keys = [q for q in oa if q in ob and q not in drop and (cat is None or oa[q]["category"] == cat)]
    if not keys:
        return None
    b = sum(1 for q in keys if not oa[q]["correct"] and ob[q]["correct"])   # B ахисан
    c = sum(1 for q in keys if oa[q]["correct"] and not ob[q]["correct"])   # B буурсан
    return {"n": len(keys), "b": b, "c": c, "p": mcnemar_exact(b, c),
            "ka": sum(oa[q]["correct"] for q in keys), "kb": sum(ob[q]["correct"] for q in keys)}


def pair_block(ra: Run, rb: Run, drop: frozenset[str] = frozenset()) -> dict:
    """Нийт (хоёр тал бүтэн бол), хариулах ба ангилал бүрийн McNemar (H2) + тэмдэглэл (†, *, давхцал)."""
    full = not (ra.answerable_only or rb.answerable_only)
    res = {"ma": ra.mark(), "mb": rb.mark(), "partial": bool(ra.problem or rb.problem), "unverifiable": not full,
           "all": pair_test(ra.oc, rb.oc, None, drop) if full else None,
           "ans": pair_test(ra.oc, rb.oc, "answerable", drop),
           "cats": {c: pair_test(ra.oc, rb.oc, c, drop) for c in (CATS if full else ("answerable",))},
           "notes": []}
    for r in (ra, rb):
        if r.answerable_only:
            res["notes"].append(f"{DAGGER} {r.id}: зөв id-ийн жагсаалт — нийт McNemar-гүй, бүрэн эсэх шалгагдахгүй")
        if r.problem:
            res["notes"].append(f"{STAR} {r.id}: {r.problem} (--force)")
        if r.in_sample:
            res["notes"].append(f"{SECT} {r.id}: in-sample — лавлагаа л, шийдвэрт ашиглахгүй")
    sa = {q for q, x in ra.oc.items() if full or x["category"] == "answerable"} - drop
    sb = {q for q, x in rb.oc.items() if full or x["category"] == "answerable"} - drop
    if sa != sb:
        res["notes"].append(f"зөвхөн давхцсан {len(sa & sb)} асуулт ({ra.id}: {len(sa)}, {rb.id}: {len(sb)})")
    return res


def compute_pairs(sp: str, plist: list[tuple[str, str]], included: dict, excluded: dict) -> dict:
    res: dict = {}
    for x, y in plist:
        ka, kb = (sp, x), (sp, y)
        if ka in included and kb in included:
            res[(x, y)] = pair_block(included[ka], included[kb])
            continue
        gone = [f"хасагдсан {k[1]} ({excluded[k].problem})" for k in (ka, kb) if k in excluded]
        if not gone and sp == "test" and x == "I3t" and ka not in included:
            continue                                    # I3t байхгүй үед хосыг алгасна
        absent = [k[1] for k in (ka, kb) if k not in included and k not in excluded]
        res[(x, y)] = {"missing": "; ".join(gone + ([f"run алга: {', '.join(absent)}"] if absent else []))}
    return res


# --------------------------------------------------------------------------- sensitivity
def load_ids(path: Path) -> list[str]:
    """Нэг мөрөнд нэг id; `#`-ээс хойш тайлбар, хоосон мөр алгасна (давхардлыг хасна, дарааллыг хадгална)."""
    out: list[str] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        for tok in re.split(r"[\s,;]+", line.split("#", 1)[0].strip()):
            if tok and tok not in out:
                out.append(tok)
    return out


def sensitivity(name: str, ids: list[str], included: dict, pairs: dict[str, dict],
                orders: dict[str, list[str]] | None = None) -> dict:
    """Run бүрийн EX ба McNemar хосыг `ids`-тэй (бүгд) ба `ids`-гүй (хассан) тооцно."""
    drop = frozenset(ids)
    res: dict = {"name": name, "ids": list(ids), "splits": {}, "unknown": []}
    seen: set[str] = set()
    orders = orders or {"test": TEST_ORDER, "dev": DEV_ORDER}
    for sp, order in orders.items():
        runs = {rid: r for (s, rid), r in included.items() if s == sp}
        hit = drop & set().union(*(set(r.oc) for r in runs.values()))
        if not hit:
            continue
        seen |= hit
        rows = []
        for before in ordered({rid: stats(r) for rid, r in runs.items()}, order):
            r = runs[before["id"]]
            rows.append((r, before, stats(r, {q: x for q, x in r.oc.items() if q not in drop})))
        prs = [((x, y), t, pair_block(included[(sp, x)], included[(sp, y)], drop))
               for (x, y), t in pairs.get(sp, {}).items() if "missing" not in t]
        res["splits"][sp] = {"hit": sorted(hit), "runs": rows, "pairs": prs}
    res["unknown"] = sorted(drop - seen)
    return res


# --------------------------------------------------------------------------- хуулах
def copy_run(run: Run, root: Path, force: bool, dry: bool) -> str:
    dest_dir = root / "results" / "runs" / run.split / run.id
    if run.kind == "jsonl":
        srcs = [run.path] + [run.path.with_name(run.path.name[: -len(".jsonl")] + s)
                             for s in (".summary.json", ".meta.json")]
        pairs = [(s, dest_dir / s.name) for s in srcs if s.exists()]
    elif run.kind == "compact":
        pairs = [(None, dest_dir / f"{run.id}_kaggle_q.txt")]
        flat = root / "results" / "runs" / run.split / f"{run.id}_kaggle_q.txt"
        if flat.exists() and not force:
            return f"алгасав (байгаа: {flat.relative_to(root).as_posix()})"
    else:
        pairs = [(run.path, root / "results" / "runs" / run.split / run.path.name)]
    done, skipped = [], []
    for s, d in pairs:
        if s is not None and s.resolve() == d.resolve():
            skipped.append(d.name + " (өөрөө)")
            continue
        if d.exists() and not force:
            same = s is not None and d.read_bytes() == s.read_bytes()
            skipped.append(d.name + (" (ижил)" if same else " (байгаа, --force)"))
            continue
        done.append(d.name)
        if dry:
            continue
        d.parent.mkdir(parents=True, exist_ok=True)
        if s is None:
            d.write_text("\n".join(run.q_lines) + "\n", encoding="utf-8")
        else:
            shutil.copy2(s, d)
    msg = f"→ {dest_dir.relative_to(root).as_posix()}: " + (", ".join(done) or "—")
    if skipped:
        msg += "; алгасав: " + ", ".join(skipped)
    return msg + (" [dry-run]" if dry and done else "")


# --------------------------------------------------------------------------- тайлан
def pct(x: dict | None) -> str:
    if not x or not x["n"]:
        return "—"
    return f"{x['k']}/{x['n']} ({100 * x['k'] / x['n']:.1f}%)"


def ci(x: dict | None) -> str:
    if not x or not x["n"]:
        return "—"
    lo, hi = x["ci"]
    return f"{100 * lo:.1f}–{100 * hi:.1f}"


def pct_ci(x: dict | None) -> str:
    """«k/n (EX%; Wilson 95% CI lo–hi)»."""
    if not x or not x["n"]:
        return "—"
    return f"{x['k']}/{x['n']} ({100 * x['k'] / x['n']:.1f}%; {CI_NAME} {ci(x)})"


def pct_br(x: dict | None) -> str:
    """«k/n (EX%) [lo–hi]» — CI нь Wilson 95% (хэсгийн толгойд нэрлэнэ)."""
    return "—" if not x or not x["n"] else f"{pct(x)} [{ci(x)}]"


def pm(x: dict | None, with_p: bool = True) -> str:
    if not x:
        return "—"
    return f"+{x['b']}/−{x['c']}" + (f" p={fmt_p(x['p'])}" if with_p else "")


def ordered(stats_by: dict[str, dict], order: list[str]) -> list[dict]:
    ids = [i for i in order if i in stats_by] + sorted(i for i in stats_by if i not in order)
    return [stats_by[i] for i in ids]


def tex_mark(mark: str) -> str:
    return "" if not mark else "$^{" + mark.replace(DAGGER, r"\dagger ").replace(STAR, "*").replace(SECT, r"\S ").strip() + "}$"


def tex_esc(s: str) -> str:
    return s.replace("_", r"\_").replace("%", r"\%").replace("—", "---").replace("–", "--")


def posthoc_best(test_stats: list[dict]) -> str | None:
    """Max-д суурилсан мөр — зөвхөн «post hoc» шошготой (L3): headline биш."""
    full = [s for s in test_stats if s["total"] and not s["problem"]]
    if not full:
        return None
    k = max(s["total"]["k"] for s in full)
    top = [s for s in full if s["total"]["k"] == k]
    return (f"post hoc (test дээр хамгийн өндрийг сонгосон — headline биш, нотолгоо биш): "
            f"{', '.join(s['id'] for s in top)} нийт {pct_ci(top[0]['total'])}")


def headline_text(hid: str | None, test_by: dict[str, dict], excluded: dict, tag: str = "V26") -> str:
    """L3: зөвхөн урьдчилан зарласан тохиргоо; алга/хасагдсан бол headline-гүй (max-аар орлуулахгүй)."""
    if hid is None:
        return (f"Headline: {tag} нь зөвхөн dev — test-ийн урьдчилан зарласан headline байхгүй "
                "(эцсийн test: RESULTS_V26.md; --headline ID-гаар өгч болно).")
    s = test_by.get(hid)
    if s is None:
        why = (f"хасагдсан ({excluded[('test', hid)].problem})" if ("test", hid) in excluded else "test run алга")
        return f"Headline: урьдчилан зарласан {hid} — {why}; headline хэвлэхгүй (хамгийн өндрөөр орлуулахгүй)."
    parts = ([f"нийт {pct_ci(s['total'])}"] if s["total"] else []) + \
            [f"хариулах {pct_ci(s['cat'].get('answerable'))}"]
    notes = ([NOTE_DAGGER] if s["answerable_only"] else []) + ([f"{STAR} {s['problem']} (--force)"] if s["problem"] else [])
    return (f"Headline (урьдчилан зарласан, --headline {hid}): {hid}{s['mark']} " + ", ".join(parts)
            + (" — " + "; ".join(notes) if notes else ""))


def decision(dev_pairs: dict) -> tuple[int, str]:
    t = dev_pairs.get(("M1ens", "M1ensp"))
    if not t or "missing" in t:
        return 0, f"dev M1ens/M1ensp: {(t or {}).get('missing') or 'run алга'} — PICK_TEST=0 хэвээр."
    if t["partial"]:
        return 0, "dev M1ens/M1ensp-ийн аль нэг нь дутуу (--force) — дутуу run дээр шийдэхгүй, PICK_TEST=0 хэвээр."
    x = t["ans"]
    if not x:
        return 0, "dev M1ens/M1ensp: хариулах асуулт давхцаагүй — PICK_TEST=0 хэвээр."
    ok = x["b"] > x["c"] and x["p"] < 0.05
    why = (f"dev хариулах: M1ensp {x['kb']} vs M1ens {x['ka']} /{x['n']}, +{x['b']}/−{x['c']}, "
           f"p = {fmt_p(x['p'])}")
    why += " < 0.05 → PICK_TEST=1 (хос харьцуулалтыг test-д)." if ok else \
        " → батлагдаагүй, PICK_TEST=0 (M1ens/M1ensr хэвээр)."
    if t["unverifiable"]:
        why += f" [{DAGGER} нэг тал нь зөв id-ийн жагсаалт — бүрэн эсэх шалгагдахгүй]"
    return (1 if ok else 0), why


def decision_ensx(dev_pairs: dict) -> tuple[int, str]:
    """V27: dev M1ensx > M1ensx0 (ижил сессийн кодоор тооцсон M1ens-ийн жор; хариулах, McNemar p < 0.05) бол л шинэ
    гишүүдийг V28-ийн эцсийн test-д. In-sample run (M1ensra) энд ордоггүй."""
    t = dev_pairs.get(("M1ensx0", "M1ensx"))
    keep = " — M1ens хэвээр (V28-ийн test-д шинэ гишүүн нэмэхгүй)."
    if not t or "missing" in t:
        return 0, f"dev M1ensx0/M1ensx: {(t or {}).get('missing') or 'run алга'}" + keep
    if t["partial"]:
        return 0, "dev M1ensx0/M1ensx-ийн аль нэг нь дутуу (--force) — дутуу run дээр шийдэхгүй" + keep
    x = t["ans"]
    if not x:
        return 0, "dev M1ensx0/M1ensx: хариулах асуулт давхцаагүй" + keep
    ok = x["b"] > x["c"] and x["p"] < 0.05
    why = (f"dev хариулах: M1ensx {x['kb']} vs M1ensx0 {x['ka']} /{x['n']}, +{x['b']}/−{x['c']}, "
           f"p = {fmt_p(x['p'])}")
    why += (" < 0.05 → M1ensx-ийн шинэ гишүүдийг V28-ийн эцсийн test-д нэг удаа нэмнэ («dev-ээр урьдчилан сонгосон»)."
            if ok else " → батлагдаагүй, M1ens хэвээр (V28-ийн test-д шинэ гишүүн нэмэхгүй).")
    if t["unverifiable"]:
        why += f" [{DAGGER} нэг тал нь зөв id-ийн жагсаалт — бүрэн эсэх шалгагдахгүй]"
    return (1 if ok else 0), why


def env_snippet(pick: int, why: str, tag: str = "V26", kind: str = "pick") -> str:
    if kind != "pick":   # V27: env-ийн хувьсагч биш — V28-ийн override-ыг гараар (MT_TENS/чуулгын жор)
        return (f"# {tag}-ийн dev дүнгээс (scripts/ingest/ingest_results.py, {date.today().isoformat()})\n"
                f"# {why}\n"
                "# (env-ийн хувьсагч биш — V28-ийн override ба tens-ийн чуулгын жорыг гараар засна)\n")
    return (f"# {tag}-ийн dev дүнгээс (scripts/ingest/ingest_results.py, {date.today().isoformat()})\n"
            f"# {why}\n"
            f"PICK_TEST={pick}\n"
            + ("# PICK_TEST=1 үед `tens` алхам test-ийн M1ensp/M1enspr-ийг ажиллуулна (M1rr/M1ks/M1a/M1g8/M1m test кэш хэрэгтэй)\n"
               if pick else ""))


def mcnemar_line(sp: str, a: str, b: str, t: dict) -> str:
    """Нэг хосын stdout мөр: нийт | хариулах | ангилал бүрийн тоо (H2)."""
    if "missing" in t:
        return f"  {sp} {a} → {b}: {t['missing']}"
    parts = []
    for key, lab in (("all", "нийт"), ("ans", "хариулах")):
        x = t.get(key)
        if x:
            parts.append(f"{lab} {pm(x, False)} (n={x['n']}) p={fmt_p(x['p'])}")
    parts += [f"{CAT_LBL[c]} {pm(x, False)} (n={x['n']})" for c in ("unanswerable", "ambiguous")
              if (x := t["cats"].get(c))]
    return f"  {sp} {a}{t['ma']} → {b}{t['mb']}  " + " | ".join(parts) + \
        ("  [" + "; ".join(t["notes"]) + "]" if t["notes"] else "")


def latex_table(test_stats: list[dict], test_pairs: dict, baseline: dict | None, tag: str = "V26") -> str:
    e, n = EXPECTED_CAT["test"], EXPECTED_N["test"]
    L = [f"% --- Ch6: test-ийн үр дүн ({tag}); CI = Wilson score 95% интервал ---", r"\begin{tabular}{lrrrrrr}",
         r"\toprule",
         f"ID & Нийт /{n} & Wilson 95\\% CI & Хариулах /{e['answerable']} & Wilson 95\\% CI & "
         f"Боломжгүй /{e['unanswerable']} & Хоёрдмол /{e['ambiguous']} \\\\", r"\midrule"]

    def row(s: dict) -> str:
        cat = s["cat"]
        cnt = [str(cat[c]["k"]) if c in cat and cat[c]["n"] else "---" for c in ("unanswerable", "ambiguous")]
        return (f"{tex_esc(s['id'])}{tex_mark(s['mark'])} & {tex_esc(pct(s['total']))} & {tex_esc(ci(s['total']))} & "
                f"{tex_esc(pct(cat.get('answerable')))} & {tex_esc(ci(cat.get('answerable')))} & "
                f"{cnt[0]} & {cnt[1]} \\\\")

    if baseline:
        L += [row(baseline), r"\midrule"]
    L += [row(s) for s in test_stats]
    L.append(r"\bottomrule")
    marks = "".join(s["mark"] for s in test_stats)
    for sym, note in TEX_NOTE.items():
        if sym in marks:
            L.append(f"\\multicolumn{{7}}{{l}}{{\\footnotesize {tex_mark(sym)} {note}}} \\\\")
    L += [r"\end{tabular}", "",
          "% --- tab:mcnemar-д нэмэх мөрүүд (exact McNemar; нийт мөр бүрийн хажууд хариулах ба ангиллын тоо) ---"]
    for (a, b), t in test_pairs.items():
        if "missing" in t:
            continue
        name = f"test {tex_esc(a)}{tex_mark(t['ma'])} → {tex_esc(b)}{tex_mark(t['mb'])}"
        for key, lab in (("all", "нийт"), ("ans", "хариулах боломжтой")):
            x = t.get(key)
            if x:
                L.append(f"{name} & {lab} & +{x['b']} / −{x['c']} & {tex_p(x['p'])} \\\\")
        for c in ("unanswerable", "ambiguous"):
            x = t["cats"].get(c)
            if x:
                L.append(f"{name} & {CAT_LBL[c]} (тоо) & +{x['b']} / −{x['c']} & --- \\\\")
    return "\n".join(L)


def slides_text(test_by: dict[str, dict], test_pairs: dict, pick_why: str, head: str,
                baseline: dict | None) -> str:
    lines = [f"Слайд 11 (test-ийн үр дүн; [ ] = {CI_NAME}):", f"  - {head}"]
    for i in ("M1a", "M1r", "M1ens", "M1ensr"):
        s = test_by.get(i)
        if s:
            lines.append(f"  - {i}{s['mark']}: " + (f"нийт {pct_br(s['total'])}, " if s["total"] else "")
                         + f"хариулах {pct_br(s['cat'].get('answerable'))}")
    if baseline:
        lines.append(f"  - {BASELINE}: нийт {pct_ci(baseline['total'])} — хариулах боломжгүй ба хоёрдмол бүгд «зөв»")
    lines.append("Слайд 16 (статистик ба дүгнэлт; exact McNemar):")
    for (a, b), t in test_pairs.items():
        if "missing" in t:
            continue
        parts = []
        for key, lab in (("all", "нийт"), ("ans", "хариулах")):
            x = t.get(key)
            if x:
                parts.append(f"{lab} +{x['b']}/−{x['c']}, p = {fmt_p(x['p'])}"
                             + (" (ач холбогдолтой)" if x["p"] < 0.05 else " (ач холбогдолгүй)"))
        cc = [f"{CAT_LBL[c]} +{x['b']}/−{x['c']}" for c in ("unanswerable", "ambiguous") if (x := t["cats"].get(c))]
        lines.append(f"  - {a}{t['ma']} → {b}{t['mb']}: " + "; ".join(parts) + (f" [{', '.join(cc)}]" if cc else ""))
    lines.append(f"  - Хос харьцуулалт (dev): {pick_why}")
    marks = "".join(s["mark"] for s in test_by.values())
    lines += [f"  {note}" for sym, note in ((DAGGER, NOTE_DAGGER), (STAR, NOTE_STAR), (SECT, NOTE_SECT)) if sym in marks]
    return "\n".join(lines)


def sens_lines(sv: dict) -> list[str]:
    hits = ", ".join(f"{sp}: {len(d['hit'])}" for sp, d in sv["splits"].items()) or "аль ч run-д алга"
    L = [f"\n=== Sensitivity: {sv['name']} ({len(sv['ids'])} id; {hits}) — бүгд → хассан; [ ] = {CI_NAME} ==="]
    for sp, d in sv["splits"].items():
        for r, before, after in d["runs"]:
            bits = ([f"нийт {pct_br(before['total'])} → {pct_br(after['total'])}"] if before["total"] else []) + \
                   [f"хариулах {pct_br(before['cat'].get('answerable'))} → {pct_br(after['cat'].get('answerable'))}"]
            L.append(f"  {sp} {r.id + r.mark():9} " + " | ".join(bits) + f"  (хассан {len(set(d['hit']) & set(r.oc))})")
        for (a, b), t0, t1 in d["pairs"]:
            bits = [f"{lab} {pm(t0.get(key))} → {pm(t1.get(key))}" for key, lab in (("all", "нийт"), ("ans", "хариулах"))
                    if t0.get(key)]
            L.append(f"  {sp} {a}{t0['ma']} → {b}{t0['mb']}  " + " | ".join(bits))
    if sv["unknown"]:
        L.append(f"  анхаар: {len(sv['unknown'])} id аль ч оруулсан run-д алга: {', '.join(sv['unknown'])}")
    return L


def md_report(ctx: dict) -> str:
    tag = ctx.get("tag", "V26")
    L = [f"# {tag}-ийн үр дүн (Kaggle гаралт)", "",
         f"Үүсгэсэн: `scripts/ingest/ingest_results.py --tag {tag}`, {date.today().isoformat()}. EX = зөв / нийт; "
         "CI бүр Wilson score 95% интервал (z = 1.96); McNemar exact (p = 2·P(X ≤ min(b,c)), X ~ Bin(b+c, ½)). "
         "Түвшин ба домэйн нь зөвхөн хариулах боломжтой асуулт дээр.", "",
         f"Тэмдэглэгээ: {NOTE_DAGGER}. {NOTE_STAR}." + (f" {NOTE_SECT}." if ctx.get("in_sample") else ""), "",
         "## Headline (урьдчилан зарласан)", "", ctx["head"], ""]
    L += ["## Оруулсан run-ууд", ""] + [f"- {x}" for x in ctx["found"]] + [""]
    if ctx["excluded"]:
        L += ["## Хасагдсан run-ууд (статистик, McNemar, LaTeX-д ороогүй; --force-оор оруулна)", ""]
        L += [f"- {sp}/{rid} [{r.kind}]: {r.problem}" for (sp, rid), r in sorted(ctx["excluded"].items())] + [""]
    for sp, title in (("test", "Test (200)"), ("dev", "Dev (300)")):
        sts = ctx["stats"][sp]
        if not sts:
            continue
        L += [f"## {title}", "",
              f"| ID | Нийт | {CI_NAME} | Хариулах | {CI_NAME} | Боломжгүй [{CI_NAME}] | Хоёрдмол [{CI_NAME}] | "
              "Хэлбэр | Төлөв |",
              "|---|---|---|---|---|---|---|---|---|"]
        base = ctx["baselines"].get(sp)
        for s in ([base] if base else []) + sts:
            c = s["cat"]
            state = ("суурь (тооцоолсон)" if s["baseline"] else
                     f"{DAGGER} зөвхөн хариулах, бүрэн эсэх шалгагдахгүй" if s["answerable_only"] else
                     f"{STAR} {s['problem']}" if s["problem"] else "бүрэн")
            if s.get("in_sample"):
                state += f"; {SECT} in-sample (лавлагаа, шийдвэрт ороогүй)"
            L.append(f"| {s['id']}{s['mark']} | {pct(s['total'])} | {ci(s['total'])} | {pct(c.get('answerable'))} | "
                     f"{ci(c.get('answerable'))} | {pct_br(c.get('unanswerable'))} | {pct_br(c.get('ambiguous'))} | "
                     f"{s['kind']} | {state} |")
        L.append("")
        if sp == "test" and ctx["posthoc"]:
            L += [f"_{ctx['posthoc']}_", ""]
        for key, lab in (("difficulty", "Түвшин"), ("domain", "Домэйн")):
            groups = sorted({g for s in sts for g in s[key]}, key=lambda g: (DIFF_ORDER.get(g, 9), g))
            if not groups or groups == ["?"]:
                continue
            L += [f"**{lab} (хариулах, EX% (k/n) [{CI_NAME}])**", "", "| ID | " + " | ".join(groups) + " |",
                  "|---" * (len(groups) + 1) + "|"]
            for s in sts:
                cells = []
                for g in groups:
                    x = s[key].get(g)
                    cells.append(f"{100 * x['k'] / x['n']:.1f} ({x['k']}/{x['n']}) [{ci(x)}]" if x and x["n"] else "—")
                L.append(f"| {s['id']}{s['mark']} | " + " | ".join(cells) + " |")
            L.append("")
    L += ["## McNemar (exact)", "", "| Split | A → B | Хэмжүүр | A зөв | B зөв | +b / −c | p | Тэмдэглэл |",
          "|---|---|---|---|---|---|---|---|"]
    for sp in ("test", "dev"):
        for (a, b), t in ctx["pairs"][sp].items():
            if "missing" in t:
                L.append(f"| {sp} | {a} → {b} | — | — | — | {t['missing']} | — | |")
                continue
            note = "; ".join(t["notes"])
            for key, lab in (("all", "нийт"), ("ans", "хариулах")):
                x = t.get(key)
                if x:
                    L.append(f"| {sp} | {a}{t['ma']} → {b}{t['mb']} | {lab} | {x['ka']}/{x['n']} | {x['kb']}/{x['n']} | "
                             f"+{x['b']} / −{x['c']} | {fmt_p(x['p'])} | {note} |")
            for c in ("unanswerable", "ambiguous"):
                x = t["cats"].get(c)
                if x:
                    L.append(f"| {sp} | {a}{t['ma']} → {b}{t['mb']} | {CAT_LBL[c]} (тоо) | {x['ka']}/{x['n']} | "
                             f"{x['kb']}/{x['n']} | +{x['b']} / −{x['c']} | — | |")
    L += ["", "b = A буруу, B зөв (B ахисан); c = A зөв, B буруу. Урьдчилан зарласан тест нь «нийт» ба «хариулах»; "
          "ангиллын мөрүүд зөвхөн тоо (p-гүй).", ""]
    for sv in ctx["sens"]:
        hits = ", ".join(f"{sp}: {len(d['hit'])}" for sp, d in sv["splits"].items()) or "аль ч run-д алга"
        L += [f"## Sensitivity: {sv['name']} ({len(sv['ids'])} id; {hits})", "",
              f"Хассан id: {', '.join(sv['ids'])}." + (f" Аль ч run-д алга: {', '.join(sv['unknown'])}."
                                                      if sv["unknown"] else ""), "",
              "| Split | ID | Нийт (бүгд) | Нийт (хассан) | Хариулах (бүгд) | Хариулах (хассан) |",
              "|---|---|---|---|---|---|"]
        for sp, d in sv["splits"].items():
            for r, b0, b1 in d["runs"]:
                L.append(f"| {sp} | {r.id}{r.mark()} | {pct_br(b0['total'])} | {pct_br(b1['total'])} | "
                         f"{pct_br(b0['cat'].get('answerable'))} | {pct_br(b1['cat'].get('answerable'))} |")
        L += ["", f"[ ] = {CI_NAME}.", "", "| Split | A → B | Хэмжүүр | Бүгд: +b / −c, p | Хассан: +b / −c, p |",
              "|---|---|---|---|---|"]
        for sp, d in sv["splits"].items():
            for (a, b), t0, t1 in d["pairs"]:
                for key, lab in (("all", "нийт"), ("ans", "хариулах")):
                    if t0.get(key):
                        L.append(f"| {sp} | {a}{t0['ma']} → {b}{t0['mb']} | {lab} | {pm(t0[key])} | {pm(t1.get(key))} |")
        L.append("")
    kind = ctx.get("decision", "pick")
    label = f"PICK_TEST={ctx['pick']}" if kind == "pick" else f"M1ensx → V28 test: {'ТИЙМ' if ctx['pick'] else 'үгүй'}"
    L += ["## Шийдвэр (docs/ACCURACY_PLAN.md)", "", f"**{label}** — {ctx['why']}", "",
          "Дараагийн сессийн env (config/kaggle_overrides*.env-д гараар нэмнэ):", "", "```sh",
          env_snippet(ctx["pick"], ctx["why"], tag, kind).rstrip(), "```", ""]
    if ctx["latex"]:
        L += ["## Ch6-д (LaTeX, гараар хуулна)", "", "```latex", ctx["latex"], "```", ""]
    if ctx["slides"]:
        L += ["## Слайд 11/16", "", "```text", ctx["slides"], "```", ""]
    return "\n".join(L)


# --------------------------------------------------------------------------- main
def parse_pair(s: str) -> tuple[str, str]:
    a, sep, b = s.partition(":")
    if not sep or not a or not b:
        raise argparse.ArgumentTypeError(f"хос «A:B» хэлбэртэй байна: {s!r}")
    return a, b


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", required=True, type=Path, help="Kaggle-аас татсан хавтас")
    ap.add_argument("--root", type=Path, default=ROOT_DEFAULT, help="төслийн үндэс (тестэд)")
    ap.add_argument("--tag", type=str.upper, default="V26", choices=sorted(PRESETS),
                    help="хувилбарын preset (хос, эрэмбэ, headline, шийдвэр); анхдагч V26")
    ap.add_argument("--out", type=Path, default=None, help="Markdown тайлан (анхдагч <root>/docs/RESULTS_<tag>.md)")
    ap.add_argument("--dry-run", action="store_true", help="юу ч хуулж/бичихгүй")
    ap.add_argument("--force", action="store_true",
                    help="байгаа файлыг дарна; дутуу/буруу run-ийг хуулж, статистик/McNemar/LaTeX-д «*»-тэй оруулна")
    ap.add_argument("--headline", default=None, metavar="ID",
                    help=f"урьдчилан зарласан гол тохиргоо (test; анхдагч preset-ийнх — V26: {HEADLINE_DEFAULT}, "
                         "V27: байхгүй); алга бол headline-гүй")
    ap.add_argument("--exclude-ids", action="append", type=Path, default=[], metavar="FILE",
                    help="sensitivity: нэг мөрөнд нэг id (`#` тайлбар); давтагдана — файл бүрээр (олон бол нийлбэрээр ч)")
    ap.add_argument("--pair", action="append", type=parse_pair, default=[], metavar="A:B",
                    help="нэмэлт McNemar хос (аль нэг тал нь байгаа split бүрд); давтагдана")
    a = ap.parse_args(argv)
    ver, pre = a.tag, PRESETS[a.tag]
    headline = a.headline if a.headline is not None else pre["headline"]
    orders = {"test": list(pre["test_order"]), "dev": list(pre["dev_order"])}
    in_sample = set(pre["in_sample"])
    root, src = a.root.resolve(), a.src.resolve()
    if not src.is_dir():
        print(f"[ingest] --src хавтас алга: {src}")
        return 2
    excl: list[tuple[str, list[str]]] = []
    for f in a.exclude_ids:
        if not f.is_file():
            print(f"[ingest] --exclude-ids файл алга: {f}")
            return 2
        excl.append((f.stem, load_ids(f)))
    if len(excl) > 1:
        union: list[str] = []
        for _, ids in excl:
            union += [i for i in ids if i not in union]
        excl.append(("+".join(name for name, _ in excl), union))

    src_runs = discover(src, "src")
    local_runs = [r for r in discover(root / "results" / "runs", "local")
                  if not str(r.path.resolve()).startswith(str(src))]
    if not src_runs:
        print(f"[ingest] {src}-д run олдсонгүй")
    runs = src_runs + local_runs
    meta = build_meta(runs)
    prepare(runs, meta)
    best = choose(runs)

    wanted = set(orders["test"] + orders["dev"]) | {x for p in pre["test_pairs"] + pre["dev_pairs"] + a.pair for x in p} \
        | ({headline} if headline else set()) | in_sample
    found: list[str] = []
    included: dict[tuple[str, str], Run] = {}
    excluded: dict[tuple[str, str], Run] = {}
    for (sp, rid), r in sorted(best.items()):
        if r.origin != "src" and rid not in wanted:
            continue
        r.in_sample = rid in in_sample
        msg = f"{sp}/{rid} [{r.kind}] " + (r.where(src) if r.origin == "src" else f"төслөөс ({r.where(root)})") + \
            f" n={r.n}"
        if r.problem:
            msg += f" — АНХААР: {r.problem}"
        if r.origin == "src":
            msg += " " + (copy_run(r, root, a.force, a.dry_run) if not r.problem or a.force else "— хуулаагүй (--force)")
            stale = sorted({x.kind for x in local_runs if (x.split, x.id) == (sp, rid)})
            if stale:
                msg += f" (төслийн хуучин {'/'.join(stale)}-ийг ашиглаагүй: src давуу)"
        if r.answerable_only:
            msg += f" — {DAGGER} зөвхөн хариулах; бүрэн эсэх шалгагдахгүй, нийтэд ороогүй"
        if r.in_sample:
            msg += f" — {SECT} in-sample: лавлагаа л, McNemar/шийдвэрт ороогүй"
        if r.problem and not a.force:
            excluded[(sp, rid)] = r
            msg += " — статистик, McNemar, LaTeX-аас ХАСАВ (--force-оор оруулна)"
        else:
            included[(sp, rid)] = r
            if r.problem:
                msg += f" — {STAR} --force: дутуу гэж тэмдэглэн оруулав"
        if r.warnings:
            msg += " (" + "; ".join(r.warnings[:3]) + ")"
        print("[ingest] " + msg)
        found.append(msg)

    by_split: dict[str, dict[str, dict]] = {"test": {}, "dev": {}}
    for (sp, rid), r in included.items():
        by_split.setdefault(sp, {})[rid] = stats(r)
    # тайланд зөвхөн preset-ийн split (V27 = dev; бусад split-ийн run хуулагдана, гэхдээ тайлагнахгүй)
    st = {sp: (ordered(by_split[sp], orders[sp]) if sp in pre["splits"] else []) for sp in ("test", "dev")}
    baselines = {sp: clarify_baseline(sp) for sp in ("test", "dev") if st[sp]}

    plists = {"test": list(pre["test_pairs"]), "dev": list(pre["dev_pairs"])}
    present = set(included) | set(excluded)
    for x, y in a.pair:
        sps = [sp for sp in plists if (sp, x) in present or (sp, y) in present]
        if not sps:
            print(f"[ingest] анхаар: --pair {x}:{y} — аль ч split-д run алга")
        for sp in sps:
            if (x, y) not in plists[sp]:
                plists[sp].append((x, y))
    pairs = {sp: compute_pairs(sp, plists[sp], included, excluded) for sp in plists}
    sens = [sensitivity(name, ids, included, pairs, {sp: orders[sp] for sp in ("test", "dev") if sp in pre["splits"]})
            for name, ids in excl]
    kind = pre["decision"]
    pick, why = decision(pairs["dev"]) if kind == "pick" else decision_ensx(pairs["dev"])
    head = headline_text(headline, by_split["test"], excluded, ver)
    posthoc = posthoc_best(st["test"])
    if "test" in pre["splits"]:
        latex = latex_table(st["test"], pairs["test"], baselines.get("test"), ver)
        slides = slides_text(by_split["test"], pairs["test"], why, head, baselines.get("test"))
    else:
        latex = slides = ""

    print(f"\n=== EX (нийт | хариулах); [ ] = {CI_NAME} ===")
    rows = [s for sp in ("test", "dev") for s in ([baselines[sp]] if sp in baselines else []) + st[sp]]
    w = max([len(s["id"] + s["mark"]) for s in rows] + [6])
    for s in rows:
        note = (f"  {DAGGER} зөвхөн хариулах, бүрэн эсэх шалгагдахгүй" if s["answerable_only"] else
                f"  {STAR} {s['problem']}" if s["problem"] else "")
        if s.get("in_sample"):
            note += f"  {SECT} in-sample (лавлагаа)"
        print(f"  {s['split']:4} {s['id'] + s['mark']:{w}} нийт {pct_br(s['total']):>28}  "
              f"хариулах {pct_br(s['cat'].get('answerable')):>28}{note}")
    if posthoc:
        print(f"  {posthoc}")
    print("\n=== McNemar (exact; нийт | хариулах | ангилал бүрийн +b/−c) ===")
    for sp in ("test", "dev"):
        for (x, y), t in pairs[sp].items():
            print(mcnemar_line(sp, x, y, t))
    print("\n=== Headline ===\n" + head)
    for sv in sens:
        print("\n".join(sens_lines(sv)))
    print("\n=== LaTeX (Ch6) ===\n" + (latex or f"— ({ver}: зөвхөн dev, test-ийн хүснэгт байхгүй)"))
    print("\n=== Слайд 11/16 ===\n" + (slides or f"— ({ver}: зөвхөн dev)"))
    label = f"PICK_TEST={pick}" if kind == "pick" else f"M1ensx → V28 test: {'ТИЙМ' if pick else 'үгүй'}"
    print(f"\n=== Шийдвэр ===\n{label} — {why}\n\n=== env ===\n" + env_snippet(pick, why, ver, kind))

    out = a.out.resolve() if a.out else root / "docs" / f"RESULTS_{ver}.md"
    if a.dry_run:
        print(f"[ingest] dry-run: {out} бичээгүй")
    else:
        ctx = {"found": found, "excluded": excluded, "stats": st, "baselines": baselines, "pairs": pairs,
               "sens": sens, "pick": pick, "why": why, "head": head, "posthoc": posthoc,
               "latex": latex, "slides": slides, "tag": ver, "decision": kind, "in_sample": sorted(in_sample)}
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md_report(ctx), encoding="utf-8")
        print(f"[ingest] бичив: {out}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
