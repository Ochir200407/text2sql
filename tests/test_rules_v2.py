"""Rules v2 (selection.rule_decision_v2): AUTHORING-ийн 5 ангилал — dev-ийн жишээ ба v1-ийн хуурамч татгалзлын эсрэг жишээ.
Мөн RunOptions.rules_version-ийн анхдагч (v1) зан төлөв өөрчлөгдөөгүй, reselect --rules дамжина."""
import pytest

from mn_text2sql.pipeline.options import RunOptions
from mn_text2sql.pipeline.selection import rule_decision, rule_decision_for, rule_decision_v2


@pytest.mark.parametrize("q,en,reason", [
    ("Чанаргүй зээлийн хэмжээг бууруулах талаар ТУЗ-д танилцуулах зөвлөмж бичиж өгөөч.", None, "non_sql"),
    ("Төлбөрөө 30-аас дээш хоног хоцроосон зээлдэгчдэд илгээх сануулах мессежийн эелдэг текст бичиж өгөөч", None, "non_sql"),
    ("Харилцагчдад илгээх мэдэгдлийн загвар", "Draft a notice template for customers.", "non_sql"),
    ("Ирэх сарын эцэст ам.долларын ханш хэд болох вэ?", None, "future"),
    ("Ирэх оны эхний улиралд хэдэн зээл хугацаанаасаа өмнө бүрэн хаагдах бол?", None, "future"),
    ("Одоогийн чиг хандлагаар NPL хэд хүрэх вэ?", None, "future"),
    ("Хугацаа нь дууссан хадгаламжуудыг бүгдийг нь 12 сараар сунгаад төлөвийг нь идэвхтэй болгоорой", None,
     "write_operation"),
    ("KYC-ийн хугацаа нь дууссан бүх харилцагчийн төлөвийг түдгэлзүүлсэн болгож өөрчил.", None, "write_operation"),
    ("Нээлттэй бүх сэрэмжлүүлгийг хаагаад, хаасан огноог өнөөдрөөр тавь.", None, "write_operation"),
    ("Манай хугацаатай хадгаламжийн хүү арилжааны банкуудын дундаж хүүгээс хэр өндөр байна вэ?", None,
     "external_knowledge"),
    ("Манай NPL-ийн хувь ББСБ салбарын дунджаас өндөр үү?", None, "external_knowledge"),
    ("Монголбанкны журмаар өр орлогын харьцааны дээд хязгаар хэд вэ?", None, "external_knowledge"),
    ("Барагдуулагч нэг бүрийн сарын цалин болон урамшууллын дүнг гарга.", None, "missing_data"),
    ("Салбар бүрийн харилцагчдын сэтгэл ханамжийн дундаж оноо хэд вэ?", None, "missing_data"),
])
def test_v2_refuses_authoring_categories(q, en, reason):
    assert rule_decision_v2(q, en) == ("refuse", reason)


@pytest.mark.parametrize("q,en", [
    # v1 буюу эхний хувилбарын хуурамч татгалзал/тодруулга — хариулах боломжтой
    ("VIP цалингийн зээлээр 2025 онд ирсэн хүсэлтүүдийн зөвшөөрлийн хувь хэд байсан бэ?",
     "What was the approval rate of applications received through the VIP Salary Loan in 2025?"),
    ("2026 оны эхний хагаст цалингаас суутгах замаар хэдэн төгрөгийн эргэн төлөлт орсон бэ?",
     "How much was recovered through salary deduction in the first half of 2026?"),
    ("АТМ-ээс бэлэн мөнгө авсан амжилттай гүйлгээний дундаж дүн төгрөгөөр хэд вэ?", None),
    ("Идэвхтэй барьцааны нийт үнэлгээг гарга. Долларын барьцааг өнөөдрийн Монголбанкны ханшаар хөрвүүл.",
     "Convert USD collateral at today's Mongolian Bank exchange rate."),
    ("Ам.долларын харилцах данснуудын нийт үлдэгдлийг албан ханшаар төгрөгт шилжүүлбэл хэд болох вэ?",
     "What would the total balance be if it were converted to MNT at the official rate?"),
    ("ЖДҮ харилцагчдыг хуулийн этгээдийн хэлбэрээр нь ангилж тоолж гарга.", None),
    ("Өндөр болон маш өндөр эрсдэлтэй харилцагчийн эзлэх хувийг гарга.", None),
    ("Идэвхтэй багц нь хамгийн том 10 мэргэжилтнийг нэр, салбартай нь жагсаа.", None),
    ("Хаагдсан хадгаламжийн тоог төлөвөөр нь гарга", "Show the number of closed deposits by status."),
    ("Данснаас хассан зээлийн дүнг гарга", "Show the amount of loans written off."),
    ("Дараагийн төлөлт хэзээ, хэдэн төгрөг төлөх ёстой вэ?", "When is the next payment due and how much?"),
])
def test_v2_does_not_refuse_answerable(q, en):
    assert rule_decision_v2(q, en) is None


def test_v2_clarify_and_resolved():
    assert rule_decision_v2("Сүүлийн 3 сард хэдэн гүйлгээ хийгдсэн бэ?") == ("clarify", "vague")
    assert rule_decision_v2("Хамгийн шилдэг зээлийн мэргэжилтэн хэн бэ?") == ("clarify", "vague")
    assert rule_decision_v2("Эрсдэлтэй харилцагч хэд байна?") == ("clarify", "vague")
    assert rule_decision_v2("Зээлийн дүнгээр хамгийн том 10 харилцагчийг гарга.") is None   # хэмжүүр заасан
    assert rule_decision_v2("Сүүлийн 3 сард", None, refuse=True, clarify=False) is None


def test_english_only_signal_and_switches():
    assert rule_decision_v2("Ирэх онд хэдэн шинэ харилцагч бүртгүүлэх төлөвтэй вэ?") == ("refuse", "future")
    q = "Энэ тоогоор цааш нь гаргаж өгөөч"
    assert rule_decision_v2(q, "Forecast the number of new customers for next year.") == ("refuse", "future")
    assert rule_decision_v2(q, "Forecast the number of new customers.", refuse=False) is None


def test_default_is_v1_and_dispatch():
    assert RunOptions().rules_version == "v1"
    q = "Ирэх сарын эцэст ам.долларын ханш хэд болох вэ?"
    assert rule_decision_for("v1", q, "x") == rule_decision(q)
    assert rule_decision_for("v2", q) == rule_decision_v2(q)


def test_reselect_cli_passes_rules(monkeypatch):
    import mn_text2sql.eval.reselect as rs
    from mn_text2sql import cli
    seen = {}
    monkeypatch.setattr(rs, "reselect_run", lambda *a, **k: seen.update(k) or [])
    assert cli.main(["reselect", "--from", "M1rr", "--to", "X", "--rules", "v2"]) == 0
    assert seen["rules"] == "v2"
    seen.clear()
    assert cli.main(["reselect", "--from", "M1rr", "--to", "X"]) == 0
    assert seen["rules"] == "v1"


@pytest.mark.parametrize("q,expected", [
    ("Хадгаламжаа хугацаанаас нь өмнө татсан шалтгааныг тоолж гарга", ("refuse", "missing_data")),
    ("Татгалзсан хүсэлтүүдийг шалтгаанаар нь тоол", None),                    # rejection_reason бий
    ("Энэ оны зээл олголтын төлөвлөгөөний биелэлт хэд вэ?", ("refuse", "missing_data")),
    ("АТМ бүрийн байршлыг гарга", None),                                         # «байршил» хувилсан — refuse биш
    ("АТМ тус бүрийн байршил болон бэлэн мөнгөний үлдэгдлийг харуул", ("refuse", "missing_data")),
    ("АТМ-ээс бэлэн мөнгө авсан гүйлгээний тоо хэд вэ?", None),
    ("Хугацаа хэтэрсэн зээлтэй харилцагч хэд вэ?", ("clarify", "ambiguous_term")),
    ("Хугацаа хэтэрсэн (1+ хоног) зээлтэй харилцагч хэд вэ?", None),
    ("Хэдэн идэвхтэй зээл байна?", ("clarify", "ambiguous_term")),
    ("Идэвхтэй багцын зээл хэд байна?", None),
    ("Хүүгийн орлого хэдэн хувиар өссөн бэ?", ("clarify", "ambiguous_term")),
    ("Хүүгийн орлого өмнөх улирлаас хэдэн хувиар өссөн бэ?", None),
    ("Барьцааны нийт үнэ цэнэ хэд вэ?", ("clarify", "ambiguous_term")),
    ("Барьцааны зах зээлийн нийт үнэлгээ хэд вэ?", None),
])
def test_v2_missing_data_and_ambiguous_terms(q, expected):
    assert rule_decision_v2(q) == expected
