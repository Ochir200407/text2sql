"""run_thesis_bench.sh: thesis-import хуучирсан эсэх. Шалтгааныг хэвлэнэ (хоосон = шинэчлэх шаардлагагүй).

Эх text2sql-thesis-ийн data.db / кейсийн sha1 ≠ data/thesis/MANIFEST.json, PostgreSQL DB эсвэл индекс алга бол.
"""

import hashlib
import json
import pathlib
import sys

from mn_text2sql.config import load_config, resolve_path
from mn_text2sql.db.conn import database_exists


def sha(p: pathlib.Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()[:12]


def main(src: str) -> str:
    srcp = pathlib.Path(src)
    man = resolve_path("data/thesis/MANIFEST.json")
    if not man.exists() or not resolve_path("data/thesis/dev.jsonl").exists():
        return "импорт хийгдээгүй"
    m = json.loads(man.read_text(encoding="utf-8"))["source_sha1"]
    cases = {f.name: sha(f) for f in sorted((srcp / "evals" / "cases").glob("*.json"))}
    if m.get("data.db") != sha(srcp / "build" / "data.db") or m.get("cases") != cases:
        return "эх data.db эсвэл кейс өөрчлөгдсөн"
    cfg = load_config("config/thesis.yaml")
    if not database_exists(cfg, cfg["db"]["name"]):
        return f"PostgreSQL DB {cfg['db']['name']} алга"
    if not (resolve_path(cfg["knowledge"]["index_dir"]) / "meta.json").exists():
        return "индекс алга"
    return ""


def adapter_of(run_id: str, var_args: list[str]) -> str:
    """run_thesis_bench.sh: туршилтын ID-ийн adapter (base гинж + --var орлуулсны дараа); байхгүй бол хоосон."""
    from mn_text2sql.pipeline.options import resolve_run
    vars_ = {}
    for i, a in enumerate(var_args):
        if a == "--var" and i + 1 < len(var_args):
            k, _, v = var_args[i + 1].partition("=")
            vars_[k] = v
    return resolve_run(run_id, var_overrides=vars_).adapter or ""


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--adapter":
        print(adapter_of(sys.argv[2], sys.argv[3:]))
    else:
        print(main(sys.argv[1]))
