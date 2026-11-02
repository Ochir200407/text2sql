"""Abstain v2 (pipeline/abstain.py): шинж, LR, шийдвэр, family CV — DB, GPU шаардахгүй."""
from mn_text2sql.pipeline import abstain as A
from mn_text2sql.text.normalize import cyr_key


def test_lexical_cue_families():
    t = A.text_features
    assert t("Ажилтнуудын регистрийн дугаарыг гарга")["pii"] == 1
    assert t("Бүх зээлийн хүүг 2 хувь болгож өөрчил")["write"] == 1
    assert t("Захиралд илгээх имэйл бичиж өгөөч")["non_sql"] == 1
    assert t("Ирэх жил NPL хэд болох вэ?")["future"] == 1
    assert t("Монголбанкны бодлогын хүү хэд вэ?")["external"] == 1
    assert t("Ажилтнуудын дундаж цалин хэд вэ?")["missing"] == 1
    assert t("Цалингийн зээлийн тоо хэд вэ?")["missing"] == 0
    assert t("Ирэх сард хуваарийн дагуу хэдэн төгрөг төлөгдөх ёстой вэ?")["schedule"] == 1
    plain = t("2025 онд олгосон зээлийн тоо хэд вэ?")
    assert not any(plain[k] for k in ("pii", "write", "non_sql", "future", "external", "missing"))


def test_future_date_beyond_as_of():
    t = A.text_features
    assert t("2027 оны зээлийн олголт хэд байх вэ")["future"] == 1
    assert t("2026 оны 9-р сарын төлбөр хэд")["future"] == 1
    assert t("2026 оны 3-р сарын төлбөр хэд")["future"] == 0
    assert t("2026 онд олгосон зээл хэд")["future"] == 0


def test_ambiguity_cues_and_resolution():
    t = A.text_features
    assert t("Сүүлийн 6 сард хэдэн данс нээгдсэн бэ?")["vague_time"] == 1
    assert t("Сүүлийн 6 сард буюу 2026-01 сараас хойш хэдэн данс нээгдсэн бэ?")["vague_time"] == 0
    assert t("Хамгийн сайн салбар аль вэ?")["superl"] == 1
    assert t("Зээлийн дүнгээр хамгийн том салбар аль вэ?")["superl"] == 0
    kn = A.Knowledge(prefixes=set(), amb_terms=[{"triggers": [cyr_key("дундаж хүү")],
                                                 "qualifiers": [cyr_key("жигнэсэн")]}])
    assert A.text_features("Зээлийн дундаж хүү хэд вэ?", kn)["gloss_amb"] == 1
    assert A.text_features("Зээлийн жигнэсэн дундаж хүү хэд вэ?", kn)["gloss_amb"] == 0


def test_oov_against_knowledge_vocab():
    kn = A.Knowledge(prefixes={"зээли", "салба", "тоо"})
    assert A.text_features("Салбарын зээлийн тоо", kn)["oov"] == 0
    assert A.text_features("Салбарын ажилчдын урамшууллын хэмжээ", kn)["oov"] == 1.0


def test_vote_features():
    v = A.vote_features(0.5, ["a", "a", "b", None], 0, 4, "SELECT 3")
    assert v["low_conf"] == 0.5 and v["empty"] == 1.0 and v["fromless"] == 1.0
    assert abs(v["dispersion"] - 1 / 3) < 1e-9 and v["err_frac"] == 0.25
    v1 = A.vote_features(1.0, None, 5, 1, "SELECT count(*) FROM t2s.loans")
    assert v1 == {"low_conf": 0.0, "dispersion": 0.0, "err_frac": 0.0, "empty": 0.0, "fromless": 0.0}


def test_utility_matches_scoring_rules():
    assert A.utility("answer", "answerable", True) == 1
    assert A.utility("refuse", "answerable", True) == 0
    assert A.utility("refuse", "unanswerable", False) == 1
    assert A.utility("clarify", "unanswerable", False) == 1     # аль ч татгалзал зөв (score_answer)
    assert A.utility("clarify", "ambiguous", False) == 1
    assert A.utility("refuse", "ambiguous", False) == 0


def test_logreg_and_train_learn_simple_rule():
    # шинж 0 → хариулах боломжгүй, шинж 1 → хоёрдмол, бусад → хариулах боломжтой
    X, cats, ok = [], [], []
    for i in range(60):
        k = i % 6
        X.append([1.0 if k == 0 else 0.0, 1.0 if k == 1 else 0.0])
        cats.append("unanswerable" if k == 0 else "ambiguous" if k == 1 else "answerable")
        ok.append(k > 1)
    w, b = A.fit_logreg(X, [int(c == "unanswerable") for c in cats])
    assert w[0] > 0 and w[1] < 0.5
    m = A.train(X, cats, ok, features=["f_u", "f_a"])
    assert m.decide([1.0, 0.0])[0] == "refuse" and m.decide([1.0, 0.0])[1] == "f_u"
    assert m.decide([0.0, 1.0])[0] == "clarify"
    assert m.decide([0.0, 0.0])[0] == "answer"
    assert m.decide({"f_u": 1.0})[0] == "refuse"                  # dict оролт
    assert A.model_from_dict(m.to_dict()).decide([0.0, 1.0])[0] == "clarify"


def test_family_folds_keep_families_together():
    fams = ["a", "a", "b", "c", "c", "c", "d", "e"]
    folds = A._family_folds(fams, 3, seed=1)
    for f in set(fams):
        assert len({fo for fo, g in zip(folds, fams) if g == f}) == 1


def test_default_model_on_unseen_phrasings():
    # dev-д байхгүй хэллэг: шинж ерөнхийлж байгаа эсэх
    d = A.abstain_decision
    assert d("Зээлийн мэргэжилтнүүдийн дундаж цалин хэд вэ?")[0] == "refuse"
    assert d("Бүх харилцагчийн утасны дугаарыг жагсаа")[0] == "refuse"
    assert d("Хамгийн том 5 салбар аль вэ?")[0] == "clarify"
    assert d("2025 онд Дархан салбараас олгосон зээлийн тоо хэд вэ?") is None
    assert d("Скорингийн оноо 700-аас дээш хүсэлт хэд вэ?", 1.0, None, 1, 1,
             "SELECT count(*) FROM t2s.credit_scores WHERE score > 700") is None


def test_option_registered_and_late_default():
    from mn_text2sql.pipeline.options import resolve_run
    o = resolve_run("M1ensa")
    assert o.abstain_v2 and o.vote_filter and not o.rule_refuse and not o.rule_clarify
    assert "abstain_v2" not in resolve_run("E4").fingerprint()      # хуучин run-ийн hash өөрчлөгдөхгүй
