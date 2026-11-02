"""synth_own нэр дэвшигчийг үүсгэнэ (GPU, DB, LLM шаардахгүй): build/synth_own/candidates.jsonl.

    python scripts/synth/generate.py [--out build/synth_own] [--seed 42] [--scale 1.0] [--no-neardup]

Дэлгэрэнгүй: docs/SYNTH_OWN.md, src/mn_text2sql/training/synth_own.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from mn_text2sql.training.synth_own import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
