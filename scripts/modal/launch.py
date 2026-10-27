"""Deploy хийсэн Modal app-ийн функцийг spawn хийнэ — ажил серверт бие даан явж, локал терминал/компьютерээс хамаарахгүй.

Нэг удаа (код өөрчлөгдөх бүрт):  python -m modal deploy scripts/modal/t2s_modal.py

  # Тест (L4): run_plan_l4.sh-ийн үе шат
  python scripts/modal/launch.py --stage A --models sqrl-9b
  python scripts/modal/launch.py --cmd "STAGE=caches bash scripts/server/run_plan_l4.sh && STAGE=A MODELS=sqrl-9b bash scripts/server/run_plan_l4.sh"
  # Сургалт (A100 80 GB)
  python scripts/modal/launch.py --train --model arctic-r1-7b --steps sample --best-a A6
  # Төлөв: дууссан эсэх (call id-г spawn хэвлэнэ)
  python scripts/modal/launch.py --status fc-...

Лог: https://modal.com/apps → t2s (deployed) → функцийн дуудлага, эсвэл `python -m modal app logs t2s`.
"""

from __future__ import annotations

import argparse

import modal

APP = "t2s"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cmd", help="дурын bash команд (repo-ийн үндсэнд, env.sh-тэй)")
    ap.add_argument("--stage", help="run_plan_l4.sh-ийн STAGE (caches|A|B|F|E|G|thesis|test)")
    ap.add_argument("--train", action="store_true", help="train_a100.sh-ийг A100 дээр")
    ap.add_argument("--eval-adapters", action="store_true", help="eval_adapters.sh-ийг L4 дээр")
    ap.add_argument("--model")
    ap.add_argument("--models")
    ap.add_argument("--steps")
    ap.add_argument("--best-a")
    ap.add_argument("--best-sft")
    ap.add_argument("--best-config")
    ap.add_argument("--recipes")
    ap.add_argument("--grpo-recipes")
    ap.add_argument("--test-ids")
    ap.add_argument("--status", help="FunctionCall id — дууссан эсэхийг шалгана")
    ap.add_argument("--env", action="append", default=[], help="нэмэлт орчны хувьсагч KEY=VALUE (жишээ SAMPLE_LIMIT=2000)")
    a = ap.parse_args()

    if a.status:
        call = modal.FunctionCall.from_id(a.status)
        try:
            call.get(timeout=0)
            print("дууссан (амжилттай)")
        except TimeoutError:
            print("ажиллаж байна")
        except Exception as e:  # noqa: BLE001 — функц алдаагаар дууссан
            print(f"алдаагаар дууссан: {e}")
        return

    env = {k: v for k, v in {"STAGE": a.stage, "MODELS": a.models, "BEST_A": a.best_a, "BEST_SFT": a.best_sft,
                             "BEST_CONFIG": a.best_config, "STEPS": a.steps, "RECIPES": a.recipes,
                             "GRPO_RECIPES": a.grpo_recipes, "TEST_IDS": a.test_ids}.items() if v}
    env.update(dict(kv.split("=", 1) for kv in a.env))
    if a.train:
        if not (a.model and a.steps and a.best_a):
            raise SystemExit("--train: --model, --steps, --best-a заавал")
        fn, cmd, kw = "run_a100", f"bash scripts/server/train_a100.sh {a.model}", {"testsuite": False}
    elif a.eval_adapters:
        if not (a.model and a.best_a):
            raise SystemExit("--eval-adapters: --model ба --best-a заавал")
        fn, cmd, kw = "run_gpu", f"bash scripts/server/eval_adapters.sh {a.model}", {}
    elif a.cmd:
        fn, cmd, kw = "run_gpu", a.cmd, {}
    elif a.stage:
        fn, cmd, kw = "run_gpu", "bash scripts/server/run_plan_l4.sh", {}
    else:
        raise SystemExit("--stage, --cmd, --train, --eval-adapters эсвэл --status")
    call = modal.Function.from_name(APP, fn).spawn(cmd, env, **kw)
    print(f"spawn: {fn} → {call.object_id}")
    print(f"төлөв: python scripts/modal/launch.py --status {call.object_id}")


if __name__ == "__main__":
    main()
