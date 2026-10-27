"""MT SFT өгөгдөл рендерлэх: pairs.jsonl (+ terms.jsonl) → загварын формат (docs/MT_INVARIANT_PLAN.md §5.4).

* qwen  : {"messages": [system, user, assistant], "meta": {...}} — заавар дагадаг загвар (Qwen3.5-9B, Gemma ...)
* madlad: {"src": "<2en> [T: …] текст", "tgt": "…"} — MADLAD-400 (system байхгүй; hint нь угтвар)

Сэжүүр (hint) = pipeline/translate_terms.term_hints() (inference-тэй ижил эх сурвалж). Сургалтад 30% hint-гүй, 10% хуурамч (хамааралгүй) hint
нэмнэ — загвар hint-ийг сохроор хуулахгүй, асуулттай таарвал л ашиглана. Латин эх: hint-ийг кирилл эхээс гаргана (inference-д
Retriever.to_cyrillic-аар гаргахаар төлөвлөсөн; §5.4 №6).

Ажиллуулах:  PYTHONPATH=src python scripts/mt/render_sft.py [--pairs data/mt/v0/pairs.jsonl] [--out data/mt/v0/sft] [--seed 1]
Зөвхөн split=train мөрийг рендерлэнэ (val-ийг хэмжилтэд үлдээнэ).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from mn_text2sql.pipeline.translate import (
    SFT_HINT_HEAD as HINT_HEAD,  # noqa: E402  (inference-тэй нэг эх сурвалж)
)
from mn_text2sql.pipeline.translate import SFT_SYSTEM as SYSTEM  # noqa: E402
from mn_text2sql.pipeline.translate_terms import load_lexicon, term_hints  # noqa: E402

EN2MN_SYSTEM = ("Translate a question about a non-bank financial institution's database into natural Mongolian (Cyrillic). "
                "Direction: en→mn(cyrillic). Keep abbreviations such as NPL, PAR30, DPD, KYC, AML. Output only the translation.")


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def hints_for(cyr: str, rng: random.Random, lexicon_lines: list[str]) -> tuple[list[str], str]:
    """(hint мөрүүд, төрөл: real | none | distractor)"""
    r = rng.random()
    if r < 0.30:
        return [], "none"
    real = term_hints(cyr)
    if r > 0.90:   # 10%: хуурамч hint (хамааралгүй)
        pool = [x for x in lexicon_lines if x not in real]
        return rng.sample(pool, k=min(2, len(pool))), "distractor"
    return real, "real"


def qwen_sample(direction: str, src: str, tgt: str, hints: list[str], meta: dict) -> dict:
    system = SYSTEM.format(direction=direction) + (HINT_HEAD + "\n".join(f"- {h}" for h in hints) if hints else "")
    return {"messages": [{"role": "system", "content": system}, {"role": "user", "content": src},
                         {"role": "assistant", "content": tgt}], "meta": meta}


def madlad_sample(tag: str, src: str, tgt: str, hints: list[str]) -> dict:
    prefix = f"[T: {'; '.join(hints)}] " if hints else ""
    return {"src": f"<2{tag}> {prefix}{src}", "tgt": tgt}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", default="data/mt/v0/pairs.jsonl")
    ap.add_argument("--out", default="data/mt/v0/sft")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--en2mn-share", type=float, default=0.15)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    pairs = [p for p in read_jsonl(ROOT / args.pairs) if p.get("split", "train") == "train"]
    lex_lines = [line for _, line in load_lexicon()]
    qwen, mad, stats = [], [], Counter()

    def emit(direction: str, tag: str, src: str, tgt: str, cyr_for_hints: str, p: dict, kind: str) -> None:
        hints, htype = hints_for(cyr_for_hints, rng, lex_lines)
        meta = {"id": p["id"], "kind": kind, "direction": direction, "hint": htype, "n_hints": len(hints)}
        qwen.append(qwen_sample(direction, src, tgt, hints, meta))
        mad.append(madlad_sample(tag, src, tgt, hints))
        stats[kind] += 1
        stats[f"hint:{htype}"] += 1

    for p in pairs:
        if p.get("register") == "R4":   # агентын холимог (англи үг + монгол дагавар) мөр: mn нь аль хэдийн холимог
            emit("mn(mixed)→en", "en", p["mn"], p["en"], p["mn"], p, "mixed→en")
        else:
            emit("mn(cyrillic)→en", "en", p["mn"], p["en"], p["mn"], p, "cyr→en")
        if p.get("latin"):
            emit("mn(latin)→en", "en", p["latin"], p["en"], p["mn"], p, "latin→en")
        if p.get("latin_synth"):
            emit("mn(latin)→en", "en", rng.choice(p["latin_synth"]), p["en"], p["mn"], p, "latin_synth→en")
        if p.get("mixed") and p.get("register") != "R4":
            emit("mn(mixed)→en", "en", p["mixed"], p["en"], p["mixed"], p, "mixed→en")
        if p.get("mn_marked") and p.get("en_marked"):
            emit("mn(cyrillic)→en", "en", p["mn_marked"], p["en_marked"], p["mn"], p, "marked→en")
        if rng.random() < args.en2mn_share:
            s = {"messages": [{"role": "system", "content": EN2MN_SYSTEM}, {"role": "user", "content": p["en"]},
                              {"role": "assistant", "content": p["mn"]}],
                 "meta": {"id": p["id"], "kind": "en→mn", "direction": "en→mn(cyrillic)", "hint": "none", "n_hints": 0}}
            qwen.append(s)
            mad.append(madlad_sample("mn", p["en"], p["mn"], []))
            stats["en→mn"] += 1

    # нэр томьёо: зөвхөн curated, богино «нэр томьёо → англи» хос (нийт 10% орчим)
    terms = [t for t in read_jsonl(ROOT / "data/mt/v0/terms.jsonl") if t.get("quality") == "curated"]
    for t in terms:
        if rng.random() < 0.5:
            qwen.append({"messages": [{"role": "system", "content": SYSTEM.format(direction="mn(cyrillic)→en")},
                                      {"role": "user", "content": t["mn"]}, {"role": "assistant", "content": t["en"]}],
                         "meta": {"id": t["id"], "kind": "term", "direction": "mn(cyrillic)→en", "hint": "none",
                                  "n_hints": 0}})
            mad.append(madlad_sample("en", t["mn"], t["en"], []))
            stats["term"] += 1

    # санхүүгийн хэллэгийн толь (v2, merge_drafts.py → phrases.jsonl): «хэллэг (эсвэл хувилбар) → каноник англи» богино хос,
    # кирилл ба латин. Хэллэгийн жишээ асуулт нь pairs.jsonl-д аль хэдийн (source = phrase_draft)
    ph_path = (ROOT / args.pairs).parent / "phrases.jsonl"
    if ph_path.exists():
        from mn_text2sql.text.normalize import STYLE_VUW, translit
        for e in read_jsonl(ph_path):
            forms = [e["mn"]] + [v for v in e.get("mn_variants") or [] if v]
            src = rng.choice(forms)
            for direction, text in (("mn(cyrillic)→en", src), ("mn(latin)→en", translit(src, STYLE_VUW))):
                qwen.append({"messages": [{"role": "system", "content": SYSTEM.format(direction=direction)},
                                          {"role": "user", "content": text}, {"role": "assistant", "content": e["en"]}],
                             "meta": {"id": e["id"], "kind": "phrase", "direction": direction, "hint": "none", "n_hints": 0}})
                mad.append(madlad_sample("en", text, e["en"], []))
                stats["phrase"] += 1

    order = list(range(len(qwen)))
    rng.shuffle(order)
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    for name, rows in (("sft_qwen.jsonl", qwen), ("sft_madlad.jsonl", mad)):
        with open(out / name, "w", encoding="utf-8", newline="\n") as fh:
            for i in order:
                fh.write(json.dumps(rows[i], ensure_ascii=False) + "\n")
    (out / "STATS.json").write_text(json.dumps({"samples": len(qwen), "pairs_train": len(pairs), **stats},
                                               ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"samples": len(qwen), "pairs_train": len(pairs), **stats}, ensure_ascii=False, indent=1))
    ex = qwen[order[0]]
    print("\n--- жишээ ---")
    for m in ex["messages"]:
        print(f"[{m['role']}] {m['content']}")


if __name__ == "__main__":
    main()
