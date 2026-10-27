"""D3-ийн сургалтын өгөгдлийг Kaggle-д: dist/t2s_train_d3.zip.partNN (≤ 9.5 MB) + dist/SHA256_train_d3.txt.

    python scripts/notebook/pack_train_d3.py --src <data/train хавтас> [--out dist] [--model arctic-r1-7b]

data/train нь кодын архивт ордоггүй (pack_code.py), бүтэн хувилбар нь ~100 MB. Энд зөвхөн d3-д хэрэгтэйг:
  * өөрийн DB-ийн эх мөр: human.jsonl, own_synth_clean.jsonl (prompt-ыг Kaggle дээр H4s-ээр дахин угсрахад);
  * plain гаргалт: <model>__plain__{human,own_synth_clean}[__cot].jsonl (бүгд — build нь drop_truncated-аар шүүнэ);
  * BIRD: <model>__plain__bird_mn[__cot].jsonl-ээс d3-ийн жорын training.formats.bird_subset (drop_truncated →
    select_bird_samples: bird_limit, max_length) — build_sft-тэй ЯГ ижил функц тул Kaggle-ийн build нь локал бүтэн
    өгөгдлийнхтэй ижил мөрүүдийг сонгоно (bird_mn.jsonl хэрэггүй: BIRD-ийн prompt хадгалагдсан, bird_prompt: stored).
Eval (dev/test)-ийн id (human-<id>, synth-human-<id>-..., extra.template) эсвэл асуулттай (хэвийн болгосон текст)
таарсан мөрийг хасаж D3_MANIFEST.json-д бичнэ. Архив детерминист (огноо тогтмол): ижил оролтод ижил SHA256.

Kaggle: Datasets → New Dataset → бүх t2s_train_d3.zip.partNN ба SHA256_train_d3.txt (zip-ийг шууд байршуулбал Kaggle
задалдаг — scripts/server/run_plan_l4.sh STAGE=train_d3 хоёр хэлбэрийг хоёуланг нь таньдаг). Notebook-д Add Input.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

ARCHIVE = "t2s_train_d3.zip"
SHA_FILE = "SHA256_train_d3.txt"
PART_SIZE = 9_500_000          # Chrome-оор Kaggle-д байршуулах 10 MB-ийн хязгаар (adapter-ын part-тай ижил)
ITEM_FILES = ("human.jsonl", "own_synth_clean.jsonl")
OWN_SOURCES = ("human", "own_synth_clean")
BIRD_SOURCE = "bird_mn"
FIXED_DATE = (2026, 1, 1, 0, 0, 0)


def norm_text(s: str | None) -> str:
    return re.sub(r"\W+", " ", (s or "").lower()).strip()


def eval_index(cfg) -> tuple[set[str], set[str]]:
    """dev + test-ийн id ба бүх хувилбарын асуулт (хэвийн болгосон)."""
    from mn_text2sql.config import resolve_path
    ids, texts = set(), set()
    for key in ("dev_file", "test_file"):
        path = resolve_path(cfg["eval"][key])
        if not path.exists():
            raise SystemExit(f"eval файл алга: {path} — eval-тэй давхардлыг шалгаж чадахгүй тул зогслоо")
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                ids.add(str(d["id"]))
                for f in ("question_mn", "question_latin", "question_en", "question_mixed"):
                    if d.get(f):
                        texts.add(norm_text(d[f]))
    return ids, texts


def related_ids(item_id: str, extra: dict | None = None) -> set[str]:
    """Мөрийн эх асуултын id-ууд: human-AX01 → AX01; synth-human-AX03-e0550de0 → AX03 (+ extra.template)."""
    out = {item_id}
    m = re.fullmatch(r"(?:synth-)?human-(.+?)(?:-[0-9a-f]{8})?", item_id)
    if m:
        out.add(m.group(1))
    tmpl = (extra or {}).get("template")
    if tmpl:
        out |= {tmpl, tmpl.removeprefix("human-")}
    return out


def eval_reason(ids: set[str], questions: list[str | None], eval_ids: set[str], eval_texts: set[str]) -> str | None:
    if ids & eval_ids:
        return "eval_id:" + ",".join(sorted(ids & eval_ids))
    for q in questions:
        if q and norm_text(q) in eval_texts:
            return "eval_question"
    return None


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _dump(rows: list[dict]) -> bytes:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows).encode("utf-8")


def select(src: Path, model: str, spec: dict, max_length: int, eval_ids: set[str], eval_texts: set[str],
           seed: int = 42) -> tuple[dict[str, bytes], dict]:
    """{архив дахь зам: агуулга} ба manifest. Зам нь data/train/... (repo-ийн үндсээс задлахад тохирно)."""
    from mn_text2sql.training.formats import bird_subset, sample_key
    files: dict[str, bytes] = {}
    excluded: list[dict] = []
    counts: dict[str, int] = {}
    good_items: set[str] = set()
    for name in ITEM_FILES:
        keep = []
        for d in _read(src / name):
            why = eval_reason(related_ids(d["id"], d.get("extra")),
                              [d.get("question_mn"), d.get("question_latin"), d.get("question_en")], eval_ids, eval_texts)
            if why:
                excluded.append({"file": name, "id": d["id"], "reason": why})
                continue
            keep.append(d)
            good_items.add(d["id"])
        if not keep:
            raise SystemExit(f"{src / name}: мөр алга")
        files[f"data/train/{name}"] = _dump(keep)
        counts[name] = len(keep)
    for srcname in OWN_SOURCES:
        for suffix in ("", "__cot"):
            name = f"{model}__plain__{srcname}{suffix}.jsonl"
            keep = []
            for s in _read(src / "samples" / name):
                why = None if s["item_id"] in good_items else "item_missing_or_excluded"
                why = why or eval_reason(related_ids(s["item_id"]), [s.get("question_en")], eval_ids, eval_texts)
                if why:
                    excluded.append({"file": name, "id": s["item_id"], "reason": why})
                    continue
                keep.append(s)
            if keep:
                files[f"data/train/samples/{name}"] = _dump(keep)
                counts[name] = len(keep)
    bird_all = []
    for suffix in ("", "__cot"):
        name = f"{model}__plain__{BIRD_SOURCE}{suffix}.jsonl"
        for s in _read(src / "samples" / name):
            why = eval_reason(related_ids(s["item_id"]), [s.get("question_en")], eval_ids, eval_texts)
            if why:
                excluded.append({"file": name, "id": s["item_id"], "reason": why})
                continue
            bird_all.append(s)
    chosen, n_pool = bird_subset(bird_all, spec, seed, max_length)
    for suffix, is_cot in (("", False), ("__cot", True)):
        name = f"{model}__plain__{BIRD_SOURCE}{suffix}.jsonl"
        part = [s for s in chosen if bool(s.get("cot")) == is_cot]
        if part:
            files[f"data/train/samples/{name}"] = _dump(sorted(part, key=sample_key))
            counts[name] = len(part)
    manifest = {"recipe": "d3", "model": model, "bird_limit": spec.get("bird_limit"), "max_length": max_length,
                "bird_max_tokens": spec.get("bird_max_tokens") or max_length,   # BIRD-ийн уртын шүүлт (сонголтод нөлөөлнө)
                "drop_truncated": bool(spec.get("drop_truncated")), "seed": seed, "bird_all": len(bird_all),
                "bird_pool_after_filters": n_pool, "bird_selected": len(chosen), "counts": counts,
                "excluded": excluded,
                "sha256": {k: hashlib.sha256(v).hexdigest() for k, v in sorted(files.items())}}
    files["data/train/D3_MANIFEST.json"] = (json.dumps(manifest, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    return files, manifest


def write_zip(files: dict[str, bytes], path: Path) -> None:
    """Детерминист zip: тогтмол огноо, эрэмбэлсэн зам."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=FIXED_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, files[name])


def split_parts(path: Path, part_size: int = PART_SIZE) -> list[Path]:
    """<archive>.partNN (≤ part_size) + SHA256 файл (бүтэн zip-ийн), хуучин part-уудыг устгана."""
    for old in path.parent.glob(path.name + ".part*"):
        old.unlink()
    data = path.read_bytes()
    parts = []
    for i in range(0, max(1, len(data)), part_size):
        p = path.parent / f"{path.name}.part{i // part_size:02d}"
        p.write_bytes(data[i:i + part_size])
        parts.append(p)
    (path.parent / SHA_FILE).write_text(f"{hashlib.sha256(data).hexdigest()}  {path.name}\n", encoding="utf-8",
                                        newline="\n")
    return parts


def pack(src: Path, out_dir: Path, model: str = "arctic-r1-7b", recipe: str = "d3", seed: int = 42,
         part_size: int = PART_SIZE, cfg=None, tcfg=None) -> dict:
    from mn_text2sql.config import load_config
    from mn_text2sql.training.data import load_training_cfg
    from mn_text2sql.training.formats import recipe_max_length
    cfg = cfg or load_config()
    tcfg = tcfg or load_training_cfg()
    spec = tcfg["recipes"][recipe]
    eval_ids, eval_texts = eval_index(cfg)
    files, manifest = select(src, model, spec, recipe_max_length(tcfg, recipe), eval_ids, eval_texts, seed)
    zpath = out_dir / ARCHIVE
    write_zip(files, zpath)
    parts = split_parts(zpath, part_size)
    return {"zip": str(zpath), "bytes": zpath.stat().st_size, "sha256": hashlib.sha256(zpath.read_bytes()).hexdigest(),
            "parts": [p.name for p in parts], "counts": manifest["counts"], "bird_selected": manifest["bird_selected"],
            "bird_pool_after_filters": manifest["bird_pool_after_filters"], "excluded": len(manifest["excluded"])}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default=str(ROOT / "data" / "train"), help="бүтэн data/train хавтас (samples/ ба *.jsonl)")
    ap.add_argument("--out", default=str(ROOT / "dist"))
    ap.add_argument("--model", default="arctic-r1-7b")
    ap.add_argument("--part-size", type=int, default=PART_SIZE)
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(pack(Path(a.src), Path(a.out), a.model, part_size=a.part_size), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
