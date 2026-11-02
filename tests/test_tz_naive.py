"""compare.norm_value: T2S_TZ_NAIVE (2026-11-01) — UTC сессийн date_trunc(DATE) 00:00+00-ийг огноо болгож хэвийн болгоно."""
from datetime import date, datetime, timedelta, timezone

from mn_text2sql.eval import compare


def test_default_converts_to_ub(monkeypatch):
    monkeypatch.setattr(compare, "TZ_NAIVE", False)
    v = datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)          # UTC сесст date_trunc('month', DATE)
    assert compare.norm_value(v) == "2026-05-01 08:00:00"          # хуучин зан төлөв: gold-ийн '2026-05-01'-тэй таарахгүй
    assert compare.norm_value(date(2026, 5, 1)) == "2026-05-01"


def test_tz_naive_keeps_session_wall_clock(monkeypatch):
    monkeypatch.setattr(compare, "TZ_NAIVE", True)
    assert compare.norm_value(datetime(2026, 5, 1, tzinfo=timezone.utc)) == "2026-05-01"
    ub = timezone(timedelta(hours=8))
    assert compare.norm_value(datetime(2026, 5, 1, 9, 30, tzinfo=ub)) == "2026-05-01 09:30:00"
    assert compare.norm_value(datetime(2026, 5, 1, 9, 30)) == "2026-05-01 09:30:00"   # naive хэвээр
