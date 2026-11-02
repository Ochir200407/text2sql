"""F7: resume-ийн prompt/кодын хувилбарын шалгалт — hash/файлын нэр хэвээр, зөрвөл анхааруулга (T2S_RESUME_STRICT=1 бол зогсооно)."""

from mn_text2sql.eval import runner


def _tree(root, body=b"x = 1\n"):
    pkg = root / "src" / "mn_text2sql"
    for d in ("pipeline", "training"):
        (pkg / d).mkdir(parents=True)
    (pkg / "config.py").write_bytes(b"A = 1\n")
    (pkg / "pipeline" / "orchestrator.py").write_bytes(body)
    (pkg / "training" / "sft.py").write_bytes(b"y = 2\n")
    (root / "knowledge").mkdir()
    (root / "knowledge" / "glossary.yaml").write_bytes(b"a: 1\n")
    return pkg


def test_code_digest_scope_and_line_endings(tmp_path):
    pkg = _tree(tmp_path)
    d0 = runner.code_digest(tmp_path)
    assert len(d0) == 12 and runner.code_digest(tmp_path) == d0
    (pkg / "training" / "sft.py").write_bytes(b"y = 3\n")              # хүрээнээс гадуур — өөрчлөгдөхгүй
    assert runner.code_digest(tmp_path) == d0
    (pkg / "pipeline" / "orchestrator.py").write_bytes(b"x = 1\r\n")  # CRLF — ижил
    assert runner.code_digest(tmp_path) == d0
    (pkg / "pipeline" / "orchestrator.py").write_bytes(b"x = 2\n")
    assert runner.code_digest(tmp_path) != d0
    (pkg / "pipeline" / "orchestrator.py").write_bytes(b"x = 1\n")
    (tmp_path / "knowledge" / "glossary.yaml").write_bytes(b"a: 2\n")
    assert runner.code_digest(tmp_path) != d0


def test_code_digest_real_repo():
    assert len(runner.code_digest()) == 12


def test_version_mismatch():
    meta = {"prompt_version": "omnisql:new", "code_digest": "c2"}
    assert runner.version_mismatch([], meta) == {}
    same = [{"prompt_version": "omnisql:new", "code_digest": "c2"}]
    assert runner.version_mismatch(same, meta) == {}
    # хуучин run: code_digest талбаргүй — зөвхөн prompt_version-ийг харьцуулна
    old = [{"prompt_version": "omnisql:new"}, {"prompt_version": "omnisql:new"}]
    assert runner.version_mismatch(old, meta) == {}
    stale = [{"prompt_version": "omnisql:old"}, {"prompt_version": "omnisql:new", "code_digest": "c1"}]
    assert runner.version_mismatch(stale, meta) == {"prompt_version": [["omnisql:new", "omnisql:old"], "omnisql:new"],
                                                    "code_digest": [["c1"], "c2"]}


def test_options_hash_unaffected_by_versions():
    """Хуучин run-ий opts_hash (файлын нэр/resume) хэвээр: prompt/кодын хувилбар hash-д ордоггүй."""
    base = {"options": {"a": 1}, "served_model": "m", "eval_set": {"sha1": "x"}}
    h = runner.options_hash(base, "mn", 0)
    assert runner.options_hash(dict(base, prompt_version="p:1", code_digest="abc",
                                    code_version={"commit": "z"}), "mn", 0) == h


def test_strict_resume_env(monkeypatch):
    monkeypatch.delenv("T2S_RESUME_STRICT", raising=False)
    assert runner.strict_resume() is False
    monkeypatch.setenv("T2S_RESUME_STRICT", "1")
    assert runner.strict_resume() is True
    monkeypatch.setenv("T2S_RESUME_STRICT", "0")
    assert runner.strict_resume() is False
