"""2026-11-02: шинэ «fresh» хуваалт (evalset/fresh.jsonl) — runner, reselect нь config-ийн fresh_file-ыг ашиглана; dev/test
хуучнаараа."""
from pathlib import Path

from mn_text2sql.config import load_config
from mn_text2sql.eval.reselect import _split_file
from mn_text2sql.eval.runner import split_files


def test_fresh_split_maps_to_config_file():
    cfg = load_config()
    assert cfg["eval"]["fresh_file"] == "evalset/fresh.jsonl"
    assert split_files(cfg, "fresh") == ["evalset/fresh.jsonl"]
    assert split_files(cfg, "dev") == [cfg["eval"]["dev_file"]]
    assert split_files(cfg, "test") == [cfg["eval"]["test_file"]]
    assert split_files(cfg, "evalset/other.jsonl") == ["evalset/other.jsonl"]   # шууд зам хэвээр
    assert _split_file(cfg, "fresh").name == "fresh.jsonl"
    assert _split_file(cfg, "test") == Path(_split_file(cfg, "test"))
    assert _split_file(cfg, "dev").name == "dev.jsonl" and _split_file(cfg, "test").name == "test.jsonl"
