"""Сургалтын CLI:  python -m mn_text2sql.training <команд>   (docs/TRAINING.md)

Өгөгдөл:  fetch-bird → translate → synth → dedup
C1/C2:    sample (n гаргалт, зөвийг хадгална) → cot (шийдэгдээгүйд gold-guided reasoning)
Сургалт:  build (SFT мөр) → sft (LoRA) → merge → grpo
          D3 (Kaggle 2×T4): build --recipe d3 → torchrun --standalone --nproc_per_node 2 -m mn_text2sql.training sft
          --recipe d3 --model arctic-r1-7b --output-dir /kaggle/working/d3_train/d3__arctic-r1-7b
Үнэлгээ:  general-eval (Belebele khk_Cyrl); туршилтын eval-ийг python -m mn_text2sql eval --run C1 ...
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys


def _rank() -> int:
    """torchrun/accelerate-ийн процессын дугаар (DDP-д зөвхөн 0 нь мэдээлэл хэвлэнэ)."""
    return int(os.environ.get("RANK") or 0)


def _print(obj) -> None:
    if _rank() == 0:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="mn_text2sql.training", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="нэмэлт YAML (config/default.yaml-ийг дарна)")
    ap.add_argument("--training-config", default="config/training.yaml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("fetch-bird")
    p.add_argument("--n", type=int)
    for name in ("translate", "synth"):
        p = sub.add_parser(name)
        p.add_argument("--model")
        p.add_argument("--base-url")
        p.add_argument("--workers", type=int, default=16)
    p = sub.add_parser("dedup")
    p.add_argument("--no-embed", action="store_true")
    p = sub.add_parser("sample")
    p.add_argument("--model", required=True)
    p.add_argument("--source", required=True, help="bird_mn.jsonl | own_synth_clean.jsonl | human.jsonl")
    # h4s нь зөвхөн build-ийн prompt (D3: plain гаргалтыг H4s-ээр дахин угсарна) — h4s-ээр sample хийсэн __h4s__
    # файлыг build_sft цуглуулдаггүй тул энд зөвшөөрөхгүй
    p.add_argument("--prompt", default="plain", choices=["plain", "rag"])
    p.add_argument("--variants", help="жишээ нь en (C4-ийн англи гаргалт)")
    p.add_argument("--tool", action="store_true", help="F10: search_values tool-той (sqrl, --prompt rag)")
    p.add_argument("--pivot", action="store_true")
    p.add_argument("--limit", type=int)
    p.add_argument("--base-url")
    p = sub.add_parser("cot")
    p.add_argument("--model", required=True)
    p.add_argument("--source", required=True)
    # h4s нь зөвхөн build-ийн prompt (D3: plain гаргалтыг H4s-ээр дахин угсарна) — h4s-ээр sample хийсэн __h4s__
    # файлыг build_sft цуглуулдаггүй тул энд зөвшөөрөхгүй
    p.add_argument("--prompt", default="plain", choices=["plain", "rag"])
    p.add_argument("--base-url")
    p = sub.add_parser("build")
    p.add_argument("--recipe", required=True)
    p.add_argument("--model", required=True)
    p = sub.add_parser("sft")
    p.add_argument("--recipe", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--max-steps", type=int)
    p.add_argument("--force", action="store_true", help="дууссан adapter-ийг дахин сургах")
    p.add_argument("--output-dir", help="adapter ба checkpoint-ийн хавтас (анхдагч: adapters/<recipe>__<model>; "
                                        "Kaggle: /kaggle/working/... — сесс тасарсан ч үлдэнэ)")
    p.add_argument("--data-file", help="SFT мөрийн JSONL (анхдагч: data/train/sft/<recipe>__<model>.jsonl)")
    p = sub.add_parser("merge")
    p.add_argument("--model", required=True)
    p.add_argument("--base", help="суурь жин (GRPO-ийн эхлэл болсон нэгтгэсэн загварын хавтас); анхдагч: HF суурь")
    p.add_argument("--adapter", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("grpo")
    p.add_argument("--recipe", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--init", help="эхлэх (SFT нэгтгэсэн) загварын хавтас")
    p.add_argument("--max-steps", type=int)
    p.add_argument("--force", action="store_true", help="дууссан adapter-ийг дахин сургах")
    p = sub.add_parser("general-eval")
    p.add_argument("--model", required=True)
    p.add_argument("--served-name", help="adapter-ийн нэр (vLLM --lora-modules)")
    p.add_argument("--base-url")
    p.add_argument("--n", type=int, default=300)
    args = ap.parse_args(argv)
    level = logging.DEBUG if args.verbose else logging.INFO
    if _rank() > 0:
        level = logging.WARNING      # DDP-ийн бусад процесс мэдээллийн мөрийг давхар хэвлэхгүй
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    from ..config import load_config
    from .data import load_training_cfg
    cfg = load_config(args.config)
    tcfg = load_training_cfg(args.training_config)
    if args.cmd == "fetch-bird":
        from .data import fetch_bird_train
        _print(fetch_bird_train(cfg, tcfg, args.n))
    elif args.cmd == "translate":
        from .data import translate_items
        _print(translate_items(cfg, tcfg, model_name=args.model, base_url=args.base_url, workers=args.workers))
    elif args.cmd == "synth":
        from .synth import synthesize
        _print(synthesize(cfg, tcfg, args.model, args.base_url, workers=args.workers))
    elif args.cmd == "dedup":
        from .data import dedup_against_eval, human_items, read_items, train_dir, write_items
        d = train_dir(tcfg)
        report = {}
        for src, dst in (("bird_mn.jsonl", "bird_mn.jsonl"), ("own_synth.jsonl", "own_synth_clean.jsonl")):
            items = read_items(d / src)
            if not items:
                continue
            kept, dropped = dedup_against_eval(cfg, items, float(tcfg["data"]["dedup_threshold"]),
                                               use_embeddings=not args.no_embed)
            write_items(kept, d / dst)
            report[src] = {"in": len(items), "kept": len(kept), "dropped": len(dropped), "examples": dropped[:10]}
        hum = human_items(cfg)
        hum_kept, hum_drop = dedup_against_eval(cfg, hum, float(tcfg["data"]["dedup_threshold"]),
                                                use_embeddings=not args.no_embed)
        write_items(hum_kept, d / "human.jsonl")
        report["human.jsonl"] = {"in": len(hum), "kept": len(hum_kept), "dropped": len(hum_drop),
                                 "examples": hum_drop[:10]}
        (d / "dedup_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        _print(report)
    elif args.cmd == "sample":
        from .sampling import sample_file
        variants = [v for v in args.variants.split(",")] if args.variants else None
        _print(sample_file(cfg, tcfg, args.model, args.source, args.prompt, variants, args.base_url, args.limit,
                           args.pivot, args.tool))
    elif args.cmd == "cot":
        from .sampling import cot_file
        _print(cot_file(cfg, tcfg, args.model, args.source, args.prompt, args.base_url))
    elif args.cmd == "build":
        from .formats import build_sft
        _print(build_sft(cfg, tcfg, args.recipe, args.model))
    elif args.cmd == "sft":
        from .sft import run_sft
        _print(run_sft(cfg, tcfg, args.recipe, args.model, data_file=args.data_file, output_dir=args.output_dir,
                       max_steps=args.max_steps, force=args.force))
    elif args.cmd == "merge":
        from .sft import merge_adapter
        _print({"merged": merge_adapter(args.model, args.adapter, args.out, base=args.base)})
    elif args.cmd == "grpo":
        from .grpo import run_grpo
        _print(run_grpo(cfg, tcfg, args.recipe, args.model, args.init, max_steps=args.max_steps,
                            force=args.force))
    elif args.cmd == "general-eval":
        from .general_eval import belebele_eval
        _print(belebele_eval(cfg, args.model, args.n, base_url=args.base_url, served_name=args.served_name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
