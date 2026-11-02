"""run_plan_l4.sh / bootstrap.sh / config/kaggle_overrides*.env-ийн статик шалгалт (bash ажиллуулахгүй):
LF мөрийн төгсгөл, t2s алхмын run → кэшийн хүснэгт experiments.yaml-тай таарах, V27 override test-д хүрэхгүй,
tens/test-ийн ALLOW_TEST хамгаалалт, t2s run-ий timeout; restore_keep_files-ийн python хэсэг (задалсан dataset)."""
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_PLAN = ROOT / "scripts" / "server" / "run_plan_l4.sh"
SH_FILES = [RUN_PLAN, ROOT / "scripts" / "notebook" / "bootstrap.sh"] + sorted((ROOT / "config").glob("kaggle_overrides*.env"))


def _env(path: Path) -> dict[str, str]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"')
    return out


def _caches() -> dict[str, str]:
    s = RUN_PLAN.read_text(encoding="utf-8")
    body = re.search(r'MT_T2S_CACHES="([^"]+)"', s).group(1)
    return dict(tok.split(":", 1) for tok in body.split())


def _known_steps() -> set[str]:
    s = RUN_PLAN.read_text(encoding="utf-8")
    return set(re.search(r'case " (m0 madlad [^"]+) " in', s).group(1).split())


def test_lf_only():
    for p in SH_FILES:
        assert b"\r" not in p.read_bytes(), p


def test_t2s_cache_table_matches_experiments_yaml():
    from mn_text2sql.pipeline.options import load_experiments, resolve_run
    exp = load_experiments()
    caches = _caches()
    for need in ("M1ksf", "M1fk", "M1tc", "M1alt1", "M1alt8", "M1lx", "M1ip", "M1dec"):
        assert need in caches, need
    for rid, cache in caches.items():
        assert rid in exp["runs"], rid
        assert resolve_run(rid, exp).translation_cache == cache, rid


def test_v27_override_is_dev_only_and_known():
    env = _env(ROOT / "config" / "kaggle_overrides_v27.env")
    steps = env["MT_STEPS"].split()
    assert set(steps) <= _known_steps()
    assert "tens" not in steps and "test" not in steps and env.get("ALLOW_TEST", "0") != "1"
    assert set(env["MT_T2S"].split()) <= set(_caches())


def test_v29_override_cpu_only_preregistered():
    # V29: урьдчилан бүртгэсэн CPU reselect (ense2 → M1ensE2r, r7 → M1ens_r7, M1ensE2r_r7, afx → M1a_fx M1ensfx M1ensfxr
    # M1ensE2rfx_r7) — загвар үүсгэх алхам алга
    env = _env(ROOT / "config" / "kaggle_overrides_v29.env")
    steps = env["MT_STEPS"].split()
    assert set(steps) <= _known_steps() and set(steps) == {"secdb", "ense2", "r7", "afx"}
    s = RUN_PLAN.read_text(encoding="utf-8")
    r7 = s.split('if [[ "$STEPS" == *" r7 "* ]]; then', 1)[1].split('if [[ "$STEPS" == *" tzfix "* ]]', 1)[0]
    assert r7.count("--trunc-date") == 2 and 'ALLOW_TEST:-0}" != 1' in r7 and "serve_d2_merged" not in r7
    e2 = s.split('if [[ "$STEPS" == *" ense2 "* ]]; then', 1)[1].split('if [[ "$STEPS" == *" r7 "* ]]', 1)[0]
    assert "for DST in M1ensE2 M1ensE2r; do" in e2 and "бүрэн гаралт байна" in e2
    afx = s.split('if [[ "$STEPS" == *" afx "* ]]; then', 1)[1].split('if [[ "$STEPS" == *" v2l "* ]]', 1)[0]
    afx = "\n".join(x for x in afx.splitlines()[1:] if not x.lstrip().startswith("#"))   # тайлбаргүй
    assert afx.count("--refix afx") == 4 and afx.count("--trunc-date") == 1 and "--refix none" not in afx
    assert 'ALLOW_TEST:-0}" != 1' in afx and "serve_d2_merged" not in afx and "t2s eval" not in afx
    for d in ("M1a_rs", "M1ens_rs", "M1ensr_rs", "M1a_fx", "M1ensfx", "M1ensfxr", "M1ensE2rfx_r7"):
        assert f"--to {d} " in afx, d
    # ихэр суурь = засвартай жор --refix-гүй (ижил кодоор): M1ensfx ↔ M1ens_rs, M1ensfxr ↔ M1ensr_rs
    cmd = {m.group(1): " ".join(m.group(0).rstrip("\\ ").split())
           for m in re.finditer(r"t2s reselect --from \S+ --to (\S+) [^\n]*", afx)}
    for fx, rs in (("M1ensfx", "M1ens_rs"), ("M1ensfxr", "M1ensr_rs"), ("M1a_fx", "M1a_rs")):
        assert cmd[fx].replace(f"--to {fx} ", f"--to {rs} ").replace(" --refix afx", "") == cmd[rs], (fx, rs)


def test_v30_override_cpu_only_v2l():
    # V30 (2026-11-02): afx + rules v2 + vote_lint — CPU reselect (загвар үүсгэлтгүй)
    env = _env(ROOT / "config" / "kaggle_overrides_v30.env")
    assert set(env["MT_STEPS"].split()) == {"v2l"} <= _known_steps()
    s = RUN_PLAN.read_text(encoding="utf-8")
    blk = s.split('if [[ "$STEPS" == *" v2l "* ]]; then', 1)[1].split('if [[ "$STEPS" == *" fgold "* ]]', 1)[0]
    blk = "\n".join(x for x in blk.splitlines()[1:] if not x.lstrip().startswith("#"))
    assert 'ALLOW_TEST:-0}" != 1' in blk and "serve_d2_merged" not in blk and "t2s eval" not in blk
    assert blk.count("--refix afx") == 4 and blk.count("--rules v2") == 3 and blk.count("--lint") == 3
    assert blk.count("--kpi-fix") == 1 and blk.count("--trunc-date") == 1
    for d in ("M1ensfxv2", "M1ensfxl", "M1ensfxv2l", "M1ensfxv2lk"):
        assert f"--to {d} " in blk, d


def test_v26_template_needs_conscious_resume():
    env = _env(ROOT / "config" / "kaggle_overrides_next.env")
    assert env.get("ALLOW_TEST", "0") != "1"            # дахин хуулбал tens ажиллахгүй
    assert env["KO_UNTIL"] <= "2026-10-31"              # 11-01-ээс exit 2 (хугацаа дууссан override)


def test_test_steps_guarded_and_t2s_runs_time_bounded():
    s = RUN_PLAN.read_text(encoding="utf-8")
    assert re.search(r'for X in tens test; do\n\s+if \[\[ "\$STEPS" == \*" \$X "\* \]\] && \[ "\$\{ALLOW_TEST:-0\}" != 1 \]', s)
    t2s = s.split('if [[ "$STEPS" == *" t2s "* ]]; then', 1)[1].split('if [[ "$STEPS" == *" reselect "* ]]', 1)[0]
    assert 'timeout -k 2m "${TO}m" python -m mn_text2sql eval' in t2s and "serve_d2_merged ||" in t2s
    assert "t2s eval" not in t2s                        # shell функцийг timeout-оор ажиллуулж болохгүй
    assert "exit 2" in s.split("[override] АЛДАА", 1)[1][:600]   # хугацаа дууссан override → зогсоно


def _keep_py() -> str:
    s = RUN_PLAN.read_text(encoding="utf-8")
    body = s.split("restore_keep_files() {", 1)[1]
    return body.split("<<'PYEOF'", 1)[1].split("\n", 1)[1].split("\nPYEOF\n", 1)[0]   # `|| log ...` мөрийн үлдэгдлийг алгасна


def _run_keep(cwd: Path, **env) -> str:
    e = {k: v for k, v in os.environ.items() if k not in ("T2S_CODE_ZIP", "T2S_KEEP_SEARCH")}
    e.update(env, PYTHONIOENCODING="utf-8")
    r = subprocess.run([sys.executable, "-c", _keep_py()], cwd=cwd, env=e, capture_output=True, text=True,
                       encoding="utf-8", check=True)
    return r.stdout


def _code_tree(root: Path, tag: str) -> None:
    (root / "scripts/notebook").mkdir(parents=True)
    shutil.copy(ROOT / "scripts/notebook/pack_code.py", root / "scripts/notebook/pack_code.py")
    for n in ("build/synth_own/candidates.jsonl", "results/runs/dev/E2/arctic-r1-7b__mn__s1__h_H4.jsonl"):
        (root / n).parent.mkdir(parents=True, exist_ok=True)
        (root / n).write_text(tag, encoding="utf-8")


def test_restore_keep_files_from_extracted_dataset_and_zip(tmp_path):
    # Kaggle нь dataset-ийн zip-ийг задалдаг (V27/V28: «t2s_code.zip олдсонгүй») — задалсан хавтаснаас хуулна
    work, inp = tmp_path / "work", tmp_path / "input" / "ds" / "text2sql"
    _code_tree(work, "OLD")
    _code_tree(inp, "NEW")
    out = _run_keep(work, T2S_KEEP_SEARCH=os.pathsep.join((str(work), str(tmp_path / "input"))))
    assert "задалсан кодын хавтаснаас" in out and "KEEP_FILES 2" in out
    assert (work / "build/synth_own/candidates.jsonl").read_text(encoding="utf-8") == "NEW"
    assert (work / "results/runs/dev/E2/arctic-r1-7b__mn__s1__h_H4.jsonl").read_text(encoding="utf-8") == "NEW"
    # zip өгсөн бол zip-ээс (хуучин зам)
    z = tmp_path / "t2s_code.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("build/synth_own/candidates.jsonl", "ZIP")
        zf.writestr("src/x.py", "x")
    out = _run_keep(work, T2S_CODE_ZIP=str(z))
    assert "кодын zip-ээс" in out and "KEEP_FILES 1" in out
    assert (work / "build/synth_own/candidates.jsonl").read_text(encoding="utf-8") == "ZIP"
    assert not (work / "src/x.py").exists()


def test_restore_keep_files_ignores_own_tree(tmp_path):
    work = tmp_path / "work"
    _code_tree(work, "OLD")
    out = _run_keep(work, T2S_KEEP_SEARCH=str(work))
    assert "олдсонгүй" in out
    assert (work / "build/synth_own/candidates.jsonl").read_text(encoding="utf-8") == "OLD"


def test_fresh_steps_guarded_and_resumable():
    # 2026-11-02: шинэ 400 асуулт (evalset/fresh.jsonl) — fgold (check-gold, CPU), fens (5 гишүүн + чуулга; test шиг ALLOW_TEST)
    assert {"fgold", "fens"} <= _known_steps()
    s = RUN_PLAN.read_text(encoding="utf-8")
    blk = s.split('if [[ "$STEPS" == *" fens "* ]]; then', 1)[1].split('if [[ "$STEPS" == *" vote "* ]]', 1)[0]
    assert 'ALLOW_TEST:-0}" != 1' in blk and "--split fresh --final-test" in blk
    assert 'timeout -k 2m "${TO}m" python -m mn_text2sql eval' in blk and "t2s eval" not in blk
    assert "--to M1ens --split fresh --no-refuse --no-clarify --extra M1ks M1a M1g8 M1m" in blk
    assert blk.count("--kpi-fix") == 2 and "--rules v2 --lint" in blk
    gold = s.split('if [[ "$STEPS" == *" fgold "* ]]; then', 1)[1].split('if [[ "$STEPS" == *" fens "* ]]', 1)[0]
    assert "check-gold evalset/fresh.jsonl" in gold
