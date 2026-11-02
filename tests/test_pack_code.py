"""scripts/notebook/pack_code.py: нууц файлын denylist, report*/out, *.pptx, «diplom ajluud», KEEP_FILES, --out."""
import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("pack_code_t", ROOT / "scripts" / "notebook" / "pack_code.py")
pc = importlib.util.module_from_spec(_spec)
sys.modules["pack_code_t"] = pc
_spec.loader.exec_module(pc)


@pytest.mark.parametrize("rel", [".env", ".env.local", "config/.env.prod", "secrets.env", "scripts/x/local.env",
                                 "keys/server.pem", "a/b/c.key", "kaggle.json", "config/kaggle.json", ".netrc",
                                 "id_rsa", "home/id_ed25519.pub", "build/translations/.env",
                                 "report_diplom/out/main.toc", "report/out/x.tex", "report_diplom/Text2SQL_hamgaalalt.pptx",
                                 "diplom ajluud/schedule.jpg", "docs/deck.PPTX"])
def test_denied_or_skipped(rel):
    assert not pc.keep(Path(rel), slim=True), rel


@pytest.mark.parametrize("rel", ["config/kaggle_overrides.env", "config/kaggle_overrides_v27.env",
                                 "config/kaggle_overrides_next.env", "config/kaggle_overrides_d3.env",
                                 "scripts/server/env.sh", "scripts/server/run_plan_l4.sh", "src/mn_text2sql/cli.py",
                                 "report_diplom/main.tex", "build/synth_own/candidates.jsonl",
                                 "build/translations/m1_madlad_improved.jsonl"])
def test_kept(rel):
    assert pc.keep(Path(rel), slim=True), rel


def test_keep_caches_only_jsonl_and_keep_files_disjoint():
    assert not pc.keep(Path("build/translations/m1.meta.json"))
    assert not pc.keep(Path("build/synth_own/candidates_raw.jsonl"))       # KEEP_FILES-д байхгүй build/ файл
    # build/ эсвэл (2026-11-01) архивт байхгүй dev-ийн E2 (M1ensE2-ийн суурь); KEEP_CACHES-тай давхцахгүй
    assert all((f.startswith("build/") or f.startswith("results/runs/dev/E2/")) and not f.startswith(pc.KEEP_CACHES)
               for f in pc.KEEP_FILES)


def test_main_out_zip_ships_env_sh_not_secrets(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    files = {"scripts/server/env.sh": "export X=1\r\n", "scripts/server/run_plan_l4.sh": "echo hi\r\n",
             "config/kaggle_overrides.env": "STAGE=mt\r\n", ".env": "SECRET=1\n", "kaggle.json": "{}",
             "report_diplom/out/main.toc": "x", "report_diplom/main.tex": "x", "diplom ajluud/a.jpg": "x",
             "build/synth_own/candidates.jsonl": "{}\n", "build/other.json": "{}", "src/a.py": "print(1)\n"}
    for rel, txt in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(txt.encode())
    monkeypatch.setattr(pc, "ROOT", repo)
    out = tmp_path / "scratch" / "code.zip"
    pc.main(["--slim", "--out", str(out)])
    z = zipfile.ZipFile(out)
    names = set(z.namelist())
    assert names == {"scripts/server/env.sh", "scripts/server/run_plan_l4.sh", "config/kaggle_overrides.env",
                     "report_diplom/main.tex", "build/synth_own/candidates.jsonl", "src/a.py"}
    assert b"\r" not in z.read("scripts/server/env.sh") + z.read("config/kaggle_overrides.env")   # .sh/.env → LF
    assert not (repo / "dist").exists()                                                          # --out → dist/ хөндөхгүй
