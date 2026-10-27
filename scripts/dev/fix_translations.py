"""A4 орчуулгын кэшийн masking-ийн алдааг засаж шинэ кэш бичнэ (pipeline/translate_fix.py; GPU-гүй).

    python scripts/dev/fix_translations.py [--name qwen3.5-9b]   # → build/translations/<name>.fix.jsonl
Ажиллуулахад: H4/I* тохиргоонд `translation_cache: <name>.fix` (experiments.yaml-ийн I3t г.м.).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from mn_text2sql.config import load_config  # noqa: E402
from mn_text2sql.db.conn import bot_dsn  # noqa: E402
from mn_text2sql.db.executor import Executor  # noqa: E402
from mn_text2sql.pipeline.translate import ENTITY_COLUMNS  # noqa: E402
from mn_text2sql.pipeline.translate_fix import entity_keys, fix_record  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="qwen3.5-9b")
    a = ap.parse_args()
    cfg = load_config()
    src = ROOT / "build" / "translations" / f"{a.name}.jsonl"
    dst = src.with_name(f"{a.name}.fix.jsonl")
    ex = Executor(bot_dsn(cfg), statement_timeout_ms=20000, max_rows=100000)
    keys = entity_keys(ex, ENTITY_COLUMNS)
    split = {}
    for key in ("dev_file", "test_file"):
        for x in (ROOT / cfg["eval"][key]).read_text(encoding="utf-8").splitlines():
            if x.strip():
                split[json.loads(x)["id"]] = key[:-5]
    stats, out = Counter(), []
    for line in src.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        text, reasons = fix_record(rec, keys)
        rec2 = dict(rec, text=text, fix=reasons or None)
        out.append(rec2)
        if rec.get("variant") == "mn" and reasons:
            for r in reasons:
                stats[(split.get(rec["id"], "other"), r)] += 1
            stats[(split.get(rec["id"], "other"), "any")] += 1
    dst.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out), encoding="utf-8", newline="\n")
    print(json.dumps({"entity_values": len(keys), "out": str(dst),
                      "mn_fixed": {f"{s}:{r}": n for (s, r), n in sorted(stats.items())}}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
