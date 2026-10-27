"""Барьцаа хөрөнгө, үнэлгээ, даатгал, зээл↔барьцаа, батлан даагч."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from .context import Gen
from .people import _person_name
from .util import INSURERS, ZERO, D, add_months, ts

COLLATERAL_BY_PRODUCT = {
    "MORT_STD": {"APARTMENT": 1.0},
    "AUTO_NEW": {"VEHICLE_NEW": 0.75, "VEHICLE_USED": 0.25},
    "PLEDGE_GOLD": {"GOLD": 1.0},
    "AGRO_HERD": {"LIVESTOCK": 0.85, "VEHICLE_USED": 0.15},
    "BIZ_WC": {"APARTMENT": 0.25, "HOUSE": 0.15, "LAND": 0.15, "EQUIPMENT": 0.2, "VEHICLE_USED": 0.15,
               "DEPOSIT_PLG": 0.1},
    "BIZ_INV": {"APARTMENT": 0.25, "LAND": 0.25, "EQUIPMENT": 0.35, "HOUSE": 0.15},
    "BIZ_USD": {"APARTMENT": 0.4, "LAND": 0.3, "EQUIPMENT": 0.3},
}
DESCRIPTIONS = {
    "APARTMENT": ["{n} өрөө орон сууц, {a} м²", "Шинэ байрны {n} өрөө, {a} м²"],
    "HOUSE": ["Хашаа байшин, {a} м²", "Амины сууц, {a} м²"],
    "LAND": ["Газар эзэмших эрх, {a}0 м²", "Үйлдвэрийн зориулалттай газар, {a}0 м²"],
    "VEHICLE_NEW": ["Toyota Land Cruiser 300, {y} он", "Lexus RX 350, {y} он", "Hyundai Santa Fe, {y} он"],
    "VEHICLE_USED": ["Toyota Prius 30, {y} он", "Toyota Prius 41, {y} он", "Hyundai Porter, {y} он",
                     "Toyota Hilux, {y} он"],
    "EQUIPMENT": ["Үйлдвэрийн тоног төхөөрөмж", "Барилгын техник (экскаватор)", "Хүнсний үйлдвэрийн шугам"],
    "GOLD": ["Алтан бөгж, гинж ({a} гр)", "Алтан эдлэл ({a} гр)"],
    "LIVESTOCK": ["Хонь ямаа {a}0 толгой", "Үхэр {a} толгой, адуу {n}0 толгой"],
    "DEPOSIT_PLG": ["Хугацаатай хадгаламж барьцаа"],
}


def build_collateral(gen: Gen) -> None:
    rng = gen.rng.child("collateral")
    st = gen.store
    ref = gen.ref
    coll = st.table("collateral.collaterals", [
        "id", "collateral_number", "collateral_type_id", "owner_customer_id", "branch_id", "description",
        "registration_number", "registration_authority", "registered_date", "location_address", "market_value",
        "forced_sale_value", "accepted_value", "currency_code", "valuation_date", "next_valuation_date",
        "is_insured", "encumbrance_status", "pledge_rank", "status", "attributes", "created_at", "updated_at"])
    val = st.table("collateral.collateral_valuations", [
        "id", "collateral_id", "valuation_no", "valuation_type", "market_value", "forced_sale_value",
        "currency_code", "method", "valuator_name", "valuator_license", "is_independent", "valuation_date",
        "valid_until", "notes", "created_at"])
    ins = st.table("collateral.collateral_insurances", [
        "id", "collateral_id", "insurer_name", "policy_number", "insured_amount", "currency_code", "premium_amount",
        "beneficiary", "is_lender_beneficiary", "start_date", "end_date", "status", "created_at", "updated_at"])
    lc = st.table("collateral.loan_collaterals", [
        "id", "loan_id", "collateral_id", "currency_code", "allocated_amount", "pledge_rank", "ltv_ratio",
        "pledge_date", "release_date", "release_reason", "registered_ref", "status", "created_at", "updated_at"])
    guar = st.table("collateral.guarantors", [
        "id", "guarantor_number", "customer_id", "external_name", "external_register_hash", "external_phone",
        "guarantor_type", "monthly_income", "net_worth", "max_guarantee_amount", "status", "created_at",
        "updated_at"])
    lg = st.table("collateral.loan_guarantors", [
        "id", "loan_id", "guarantor_id", "currency_code", "guarantee_amount", "guarantee_pct", "liability_type",
        "signed_date", "valid_from", "valid_to", "called_date", "called_amount", "recovered_amount", "status",
        "created_at", "updated_at"])

    individuals = [c for c in gen.customers if not c.is_org and c.status == "ACTIVE"]
    for ln in gen.loans:
        if ln.disb is None:
            continue
        prod = ln.prod
        closed = ln.status == "CLOSED"
        bad = ln.status in ("WRITTEN_OFF", "LEGAL")
        # ---- барьцаа
        if prod.requires_collateral and prod.product_code in COLLATERAL_BY_PRODUCT:
            ctype_code = rng.weighted(COLLATERAL_BY_PRODUCT[prod.product_code])
            ctype = ref.collateral_types[ctype_code]
            ltv_target = min(float(ctype.max_ltv_ratio), float(prod.max_ltv_ratio or ctype.max_ltv_ratio)) / 100
            ltv = rng.uniform(ltv_target * 0.55, ltv_target * 0.98)
            market = D(float(ln.principal) / ltv)
            if ln.currency == "MNT":
                market = D(gen.money_round(float(market), 100_000))
            haircut = Decimal(str(ctype.haircut_pct)) / 100
            accepted = D(market * (1 - haircut))
            if accepted < ln.principal:
                accepted = ln.principal
                market = max(market, D(accepted / (1 - haircut)) + 1)
            vdate = ln.disb - timedelta(days=rng.randint(3, 20))
            next_val = add_months(vdate, int(ctype.revaluation_months)) if ctype.revaluation_months else None
            cid = gen.uuid()
            insured = bool(ctype.requires_insurance) and rng.chance(0.85)
            enc = "RELEASED" if closed else ("SEIZED" if bad and rng.chance(0.4) else "PLEDGED")
            cstatus = "RELEASED" if closed else ("LIQUIDATED" if ln.status == "WRITTEN_OFF" and rng.chance(0.3)
                                                  else "ACTIVE")
            desc = rng.choice(DESCRIPTIONS[ctype_code]).format(n=rng.randint(1, 4), a=rng.randint(30, 140),
                                                               y=rng.randint(2012, 2025))
            coll.add(id=cid, collateral_number=gen.number("COLLATERAL", vdate), collateral_type_id=ctype.id,
                     owner_customer_id=ln.cust.id, branch_id=ln.branch.id, description=desc,
                     registration_number=f"Ү-{rng.randint(1000000, 9999999)}" if ctype.requires_registration else None,
                     registration_authority="Улсын бүртгэлийн ерөнхий газар" if ctype.requires_registration else None,
                     registered_date=vdate if ctype.requires_registration else None,
                     location_address=f"{ln.cust.province}, {ln.cust.district}",
                     market_value=market, forced_sale_value=D(market * Decimal("0.7")), accepted_value=accepted,
                     currency_code=ln.currency, valuation_date=vdate, next_valuation_date=next_val, is_insured=insured,
                     encumbrance_status=enc, pledge_rank=1, status=cstatus,
                     attributes={"category": ctype.category}, created_at=ts(vdate), updated_at=ts(ln.closed_on or vdate))
            val.add(id=gen.next_id("collateral.collateral_valuations"), collateral_id=cid, valuation_no=1,
                    valuation_type="INITIAL", market_value=market, forced_sale_value=D(market * Decimal("0.7")),
                    currency_code=ln.currency, method=rng.choice(["MARKET_COMPARISON", "COST", "EXPERT_OPINION"]),
                    valuator_name=rng.choice(["Үнэлгээ Консалтинг ХХК", "Мэргэжлийн Үнэлгээ ХХК", "Дотоод үнэлгээчин"]),
                    valuator_license=f"ҮЛ-{rng.randint(100, 999)}", is_independent=rng.chance(0.7),
                    valuation_date=vdate, valid_until=next_val, notes=None, created_at=ts(vdate))
            if next_val and next_val <= gen.as_of and not closed and rng.chance(0.6):
                new_market = D(market * Decimal(str(rng.uniform(0.85, 1.12))))
                val.add(id=gen.next_id("collateral.collateral_valuations"), collateral_id=cid, valuation_no=2,
                        valuation_type="PERIODIC", market_value=new_market, forced_sale_value=D(new_market * Decimal("0.7")),
                        currency_code=ln.currency, method="MARKET_COMPARISON", valuator_name="Дотоод үнэлгээчин",
                        valuator_license=None, is_independent=False, valuation_date=next_val,
                        valid_until=add_months(next_val, 12), notes="Давтан үнэлгээ", created_at=ts(next_val))
            if insured:
                start = vdate
                ins_id = gen.next_id("collateral.collateral_insurances")
                ins.add(id=ins_id, collateral_id=cid,
                        insurer_name=rng.choice(INSURERS), policy_number=f"POL-{start.year}-{ins_id:06d}",
                        insured_amount=market, currency_code=ln.currency,
                        premium_amount=D(market * Decimal("0.008")), beneficiary="ББСБ",
                        is_lender_beneficiary=True, start_date=start, end_date=add_months(start, 12),
                        status="ACTIVE" if add_months(start, 12) > gen.as_of and not closed else "EXPIRED",
                        created_at=ts(start), updated_at=ts(start))
            allocated = min(ln.principal, accepted)
            lc.add(id=gen.next_id("collateral.loan_collaterals"), loan_id=ln.id, collateral_id=cid,
                   currency_code=ln.currency, allocated_amount=allocated, pledge_rank=1,
                   ltv_ratio=round(float(ln.principal) / float(market) * 100, 2), pledge_date=ln.disb,
                   release_date=ln.closed_on if closed else None,
                   release_reason="Зээл хаагдсан" if closed else None,
                   registered_ref=f"БҮ-{rng.randint(100000, 999999)}" if ctype.requires_registration else None,
                   status="RELEASED" if closed else ("LIQUIDATED" if cstatus == "LIQUIDATED" else "ACTIVE"),
                   created_at=ts(ln.disb), updated_at=ts(ln.closed_on or ln.disb))
            ln.collateral = {"id": cid, "type": ctype_code, "market": market, "accepted": accepted}
        else:
            ln.collateral = None
        # ---- батлан даагч
        if prod.requires_guarantor:
            for k in range(1 if rng.chance(0.85) else 2):
                gid = gen.uuid()
                internal = individuals and rng.chance(0.35)
                gcust = rng.choice(individuals) if internal else None
                if gcust is not None and gcust.id == ln.cust.id:
                    gcust = None
                gtype = "CORPORATE" if ln.cust.is_org and rng.chance(0.3) else "PERSONAL"
                income = D(gcust.income if gcust else rng.lognormal(2_000_000, 0.5))
                gstatus = "RELEASED" if closed else ("CALLED" if bad else "ACTIVE")
                guar.add(id=gid, guarantor_number=gen.number("GUARANTOR", ln.disb), customer_id=gcust.id if gcust else None,
                         external_name=None if gcust else " ".join(_person_name(rng)[:2]),
                         external_register_hash=None if gcust else gen.hmac_like(f"G{ln.n}-{k}"),
                         external_phone=None, guarantor_type=gtype, monthly_income=income,
                         net_worth=D(float(income) * rng.uniform(20, 120)),
                         max_guarantee_amount=ln.principal, status=gstatus, created_at=ts(ln.disb),
                         updated_at=ts(ln.closed_on or ln.disb))
                called = bad
                called_amount = D(ln.final["overdue_principal"] + ln.final["overdue_interest"]) if called else ZERO
                if called and called_amount <= 0:
                    called_amount = D(ln.principal * Decimal("0.1"))
                recovered = D(called_amount * Decimal(str(rng.uniform(0, 0.5)))) if called else ZERO
                lg.add(id=gen.next_id("collateral.loan_guarantors"), loan_id=ln.id, guarantor_id=gid,
                       currency_code=ln.currency, guarantee_amount=ln.principal if k == 0 else D(ln.principal / 2),
                       guarantee_pct=100 if k == 0 else 50, liability_type=rng.choice(["JOINT", "JOINT_AND_SEVERAL"]),
                       signed_date=ln.contract, valid_from=ln.disb, valid_to=ln.maturity,
                       called_date=(gen.as_of - timedelta(days=rng.randint(5, 120))) if called else None,
                       called_amount=called_amount, recovered_amount=recovered,
                       status="RELEASED" if closed else ("CALLED" if called else "ACTIVE"),
                       created_at=ts(ln.disb), updated_at=ts(ln.closed_on or ln.disb))
