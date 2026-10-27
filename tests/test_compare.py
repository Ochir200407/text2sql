from datetime import date
from decimal import Decimal

from mn_text2sql.eval.compare import result_hash, results_match, soft_f1, strict_match, values_equal


def test_values_equal_tolerance():
    assert values_equal(100.0, 100.004)
    assert not values_equal(100.0, 100.05)
    assert values_equal("12", 12.0)
    assert values_equal(None, None) and not values_equal(None, 0.0)


def test_column_permutation_and_order():
    g = [("Дархан салбар", Decimal("13")), ("Эрдэнэт салбар", Decimal("7"))]
    p = [(7, "Эрдэнэт салбар"), (13, "Дархан салбар")]
    assert results_match(["b", "n"], g, ["n", "b"], p)
    assert not results_match(["b", "n"], g, ["n", "b"], p, order_matters=True)
    assert not results_match(["b", "n"], g, ["n", "b", "x"], [r + (1,) for r in p])


def test_period_leniency():
    g = [(date(2025, m, 1), Decimal(10 * m)) for m in (1, 2, 3)]
    assert results_match(["m", "c"], g, ["m", "c"], [(f"2025-{m:02d}", 10 * m) for m in (1, 2, 3)])
    assert results_match(["m", "c"], g, ["m", "c"], [(Decimal(m), 10 * m) for m in (1, 2, 3)])
    assert not strict_match(["m", "c"], g, ["m", "c"], [(f"2025-{m:02d}", 10 * m) for m in (1, 2, 3)])
    # хоёр жилийг сарын дугаар руу буулгахгүй
    two = [(date(2024, 12, 1), 1), (date(2025, 1, 1), 2)]
    assert not results_match(["m", "c"], two, ["m", "c"], [(Decimal(12), 1), (Decimal(1), 2)])
    assert not results_match(["m", "c"], g, ["m", "c"], [(f"2025-{m:02d}", 11 * m) for m in (1, 2, 3)])


def test_code_name_maps():
    maps = [{"SME": "ЖДҮ", "MASS": "Энгийн", "ЖДҮ": "ЖДҮ", "Энгийн": "Энгийн"}]
    g = [("ЖДҮ", 396), ("Энгийн", 1294)]
    p = [("SME", 396), ("MASS", 1294)]
    assert results_match(["s", "n"], g, ["s", "n"], p, value_maps=maps)
    assert not results_match(["s", "n"], g, ["s", "n"], p)
    assert not results_match(["s", "n"], g, ["s", "n"], [("SME", 395), ("MASS", 1294)], value_maps=maps)


def test_hash_and_soft_f1():
    assert result_hash(["a", "b"], [(1, 2)]) == result_hash(["b", "a"], [(2, 1)])
    assert soft_f1([(1, "a")], [(1, "a")]) == 1.0
    assert 0 < soft_f1([(1, "a"), (2, "b")], [(1, "a")]) < 1
