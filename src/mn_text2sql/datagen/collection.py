"""Хугацаа хэтэрсэн зээлийн барагдуулалт: кейс, үйлдэл, амлалт (PTP), төлбөр, хуваарилалт, нөхөн төлүүлэлт."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from .context import Gen
from .refdata import stage_for_dpd
from .util import ZERO, D, ts, ts_rand

RESULTS = {"CONTACTED": 0.35, "NO_ANSWER": 0.25, "PROMISE_OBTAINED": 0.2, "REFUSED": 0.06, "WRONG_NUMBER": 0.04,
           "UNREACHABLE": 0.07, "DISPUTE": 0.03}


def build_collection(gen: Gen) -> None:
    rng = gen.rng.child("collection")
    st = gen.store
    ref = gen.ref
    cases = st.table("collection.collection_cases", [
        "id", "case_no", "loan_id", "customer_id", "branch_id", "stage_id", "currency_code", "opened_date",
        "dpd_at_open", "current_dpd", "outstanding_at_open", "current_outstanding", "overdue_amount",
        "collected_amount", "assigned_to", "agency_id", "priority", "status", "closed_date", "close_reason",
        "next_action_date", "notes", "created_at", "updated_at"])
    actions = st.table("collection.collection_actions", [
        "id", "case_id", "action_type_id", "action_date", "performed_by", "agency_id", "contact_person",
        "contact_result", "outcome", "cost_amount", "next_action_date", "created_at"])
    promises = st.table("collection.collection_promises", [
        "id", "case_id", "action_id", "promised_amount", "currency_code", "promised_date", "promise_made_at",
        "obtained_by", "paid_amount", "paid_date", "status", "notes", "created_at", "updated_at"])
    cpays = st.table("collection.collection_payments", [
        "id", "case_id", "promise_id", "loan_payment_id", "amount", "currency_code", "payment_date", "agency_id",
        "commission_amount", "collected_by", "created_at"])
    assigns = st.table("collection.case_assignments", [
        "id", "case_id", "assigned_to", "agency_id", "stage_id", "assigned_at", "assigned_by", "unassigned_at",
        "reason"])
    recov = st.table("collection.recovery_cases", [
        "id", "recovery_no", "loan_id", "writeoff_id", "collection_case_id", "recovery_type", "expected_amount",
        "recovered_amount", "recovery_cost", "currency_code", "court_case_ref", "bailiff_ref", "started_date",
        "completed_date", "status", "notes", "created_at", "updated_at"])

    collectors_by_branch: dict[int, list] = {}
    for e in gen.employees:
        if e.position == "COLLECTOR" and e.status != "TERMINATED":
            collectors_by_branch.setdefault(e.branch.id, []).append(e)
    ho_collectors = collectors_by_branch.get(gen.ref.branches["HO"].id, []) or \
        [e for e in gen.employees if e.position == "COLLECTOR"]
    agencies = [a for code, a in ref.agencies.items() if code != "INTERNAL"]

    for ln in gen.loans:
        if ln.disb is None or not ln.snapshots and ln.final["dpd"] == 0:
            continue
        # Хугацаа хэтрэлтийн "эпизод"-ууд — сарын эцсийн агшнаас
        episodes = []
        cur = None
        for me, dpd, state, total, grade in ln.snapshots:
            if dpd > 0:
                if cur is None:
                    cur = {"start": me, "first_dpd": dpd, "first_total": total, "max_dpd": dpd, "end": None,
                           "first_state": state}
                cur["max_dpd"] = max(cur["max_dpd"], dpd)
                cur["last"] = (me, dpd, state, total)
            elif cur is not None:
                cur["end"] = me
                episodes.append(cur)
                cur = None
        if cur is not None:
            episodes.append(cur)
        if not episodes:
            continue
        payments = [p for p in ln.ledger.payments if p.status == "POSTED"]
        collectors = collectors_by_branch.get(ln.branch.id) or ho_collectors
        for idx, ep in enumerate(episodes):
            is_last = idx == len(episodes) - 1
            opened = ep["start"] - timedelta(days=ep["first_dpd"]) + timedelta(days=rng.randint(2, 5))
            opened = min(max(opened, ln.disb + timedelta(days=1)), ep["start"])
            dpd_open = max(0, (opened - (ep["start"] - timedelta(days=ep["first_dpd"]))).days)
            ongoing = ep["end"] is None
            if ongoing and ln.status in ("CLOSED",):
                ongoing = False
                ep["end"] = ln.closed_on
            if ongoing and ln.status == "WRITTEN_OFF":
                status, close_reason, closed = "WRITTEN_OFF", "Зээл данснаас хасагдсан", ln.written_off["date"]
            elif ongoing:
                closed, close_reason = None, None
                status = "LEGAL" if ln.status == "LEGAL" else "OPEN"
            else:
                closed = ep["end"]
                cured_pay = [p.date for p in payments if opened <= p.date <= ep["end"]]
                if cured_pay:
                    closed = max(cured_pay)
                if ln.restructured and opened <= ln.restructured["date"] <= ep["end"]:
                    status, close_reason = "CLOSED", "Зээлийн бүтэц өөрчлөгдсөн"
                    closed = ln.restructured["date"]
                else:
                    status, close_reason = "RESOLVED", "Хугацаа хэтрэлт барагдсан"
                closed = max(closed, opened)
            current_dpd = ln.final["dpd"] if ongoing and status != "WRITTEN_OFF" else 0
            stage = stage_for_dpd(gen, max(current_dpd, ep["max_dpd"]) if not ongoing else max(current_dpd, 1))
            agency = rng.choice(agencies) if ep["max_dpd"] > 90 and rng.chance(0.5) else None
            collector = rng.choice(collectors) if collectors else None
            in_case = [p for p in payments if opened <= p.date <= (closed or gen.as_of)]
            collected = sum((p.amount for p in in_case), ZERO)
            overdue_now = ln.final["overdue_principal"] + ln.final["overdue_interest"] if ongoing else ZERO
            amount_mnt = float(ep["first_total"]) * (3450 if ln.currency == "USD" else 1)
            priority = "URGENT" if ep["max_dpd"] > 90 else ("HIGH" if ep["max_dpd"] > 30 or amount_mnt > 50_000_000
                                                             else ("MEDIUM" if ep["max_dpd"] > 7 else "LOW"))
            case_id = gen.uuid()
            pending_ptp = False
            # ---- үйлдлүүд
            act_rows = []
            day = opened
            end_day = closed or gen.as_of
            dpd_at = dpd_open
            step = 0
            while day <= end_day and step < 30:
                if step == 0:
                    code = "SMS_REMINDER"
                elif dpd_at < 8:
                    code = rng.choice(["AUTO_CALL", "SMS_REMINDER", "EMAIL_NOTICE"])
                elif dpd_at < 31:
                    code = rng.choice(["MANUAL_CALL", "MANUAL_CALL", "SMS_REMINDER"])
                elif dpd_at < 61:
                    code = rng.choice(["MANUAL_CALL", "FIELD_VISIT", "LETTER"])
                elif dpd_at < 181:
                    code = rng.choice(["MANUAL_CALL", "FIELD_VISIT", "LEGAL_NOTICE"])
                else:
                    code = rng.choice(["LEGAL_NOTICE", "COURT_FILING", "FIELD_VISIT"])
                at = ref.action_types[code]
                result = "CONTACTED" if code in ("SMS_REMINDER", "EMAIL_NOTICE", "LETTER", "LEGAL_NOTICE",
                                                 "COURT_FILING") else rng.weighted(RESULTS)
                gap = rng.randint(3, 12)
                nxt = day + timedelta(days=gap)
                aid = gen.next_id("collection.collection_actions")
                actions.add(id=aid, case_id=case_id, action_type_id=at.id, action_date=day,
                            performed_by=collector.id if collector and not agency else None,
                            agency_id=agency.id if agency and dpd_at > 90 else None,
                            contact_person="Зээлдэгч" if result in ("CONTACTED", "PROMISE_OBTAINED", "REFUSED",
                                                                    "DISPUTE") else None,
                            contact_result=result,
                            outcome={"PROMISE_OBTAINED": "Төлөх амлалт авсан", "REFUSED": "Төлөхөөс татгалзсан",
                                     "NO_ANSWER": "Утсаа аваагүй", "DISPUTE": "Дүнтэй маргаантай"}.get(result,
                                                                                                 "Мэдэгдэл хүргүүлсэн"),
                            cost_amount=at.cost_estimate, next_action_date=nxt, created_at=ts_rand(rng, day))
                act_rows.append((aid, day, result))
                if result == "PROMISE_OBTAINED":
                    pdate = day + timedelta(days=rng.randint(3, 14))
                    due_est = max(D(float(ep["last"][2]["overdue_principal"] + ep["last"][2]["overdue_interest"])
                                    * rng.uniform(0.3, 1.0)), D(10_000 if ln.currency == "MNT" else 10))
                    window = [p for p in payments if day <= p.date <= pdate + timedelta(days=2)]
                    paid = sum((p.amount for p in window), ZERO)
                    if pdate >= gen.as_of and not window:
                        pstatus, pdate_paid, paid_amt = "PENDING", None, ZERO
                        pending_ptp = True
                    elif paid >= due_est:
                        pstatus, pdate_paid, paid_amt = "KEPT", max(p.date for p in window), paid
                    elif paid > 0:
                        pstatus, pdate_paid, paid_amt = "PARTIALLY_KEPT", max(p.date for p in window), paid
                    else:
                        pstatus, pdate_paid, paid_amt = "BROKEN", None, ZERO
                    pid = gen.next_id("collection.collection_promises")
                    promises.add(id=pid, case_id=case_id, action_id=aid, promised_amount=due_est,
                                 currency_code=ln.currency, promised_date=pdate, promise_made_at=ts_rand(rng, day),
                                 obtained_by=collector.id if collector else None, paid_amount=paid_amt,
                                 paid_date=pdate_paid, status=pstatus, notes=None, created_at=ts(day, 15),
                                 updated_at=ts(min(gen.as_of, pdate), 18))
                    for p in window:
                        p.promise_id = pid
                day = nxt
                dpd_at += gap
                step += 1
            for p in in_case:
                commission = D(p.amount * Decimal(str(agency.commission_rate)) / 100) if agency else ZERO
                cpays.add(id=gen.next_id("collection.collection_payments"), case_id=case_id,
                          promise_id=getattr(p, "promise_id", None), loan_payment_id=p.db_id, amount=p.amount,
                          currency_code=ln.currency, payment_date=p.date, agency_id=agency.id if agency else None,
                          commission_amount=commission, collected_by=collector.id if collector else None,
                          created_at=ts(p.date, 18))
                p.promise_id = None
            if status == "OPEN":
                if pending_ptp:
                    status = "PROMISE_TO_PAY"
                elif len(act_rows) > 1:
                    status = "IN_PROGRESS"
            cases.add(id=case_id, case_no=gen.number("COLLECTION_CASE", opened), loan_id=ln.id,
                      customer_id=ln.cust.id, branch_id=ln.branch.id, stage_id=stage.id, currency_code=ln.currency,
                      opened_date=opened, dpd_at_open=dpd_open, current_dpd=current_dpd,
                      outstanding_at_open=max(ep["first_total"], ZERO),
                      current_outstanding=max(ln.final["principal_outstanding"] + ln.final["interest_outstanding"]
                                              + ln.final["penalty_outstanding"], ZERO) if ongoing else ZERO,
                      overdue_amount=overdue_now, collected_amount=collected,
                      assigned_to=collector.id if collector else None, agency_id=agency.id if agency else None,
                      priority=priority, status=status, closed_date=closed if status in
                      ("RESOLVED", "CLOSED", "WRITTEN_OFF") else None,
                      close_reason=close_reason, next_action_date=(act_rows[-1][1] + timedelta(days=5))
                      if status not in ("RESOLVED", "CLOSED", "WRITTEN_OFF") else None,
                      notes=None, created_at=ts(opened, 9), updated_at=ts(closed or gen.as_of, 18))
            first_stage = stage_for_dpd(gen, max(dpd_open, 1))
            a1 = gen.next_id("collection.case_assignments")
            esc = agency is not None
            assigns.add(id=a1, case_id=case_id, assigned_to=collector.id if collector else None,
                        agency_id=None if collector else (agency.id if agency else ref.agencies["INTERNAL"].id),
                        stage_id=first_stage.id, assigned_at=ts(opened, 9), assigned_by=gen.sys_user_id,
                        unassigned_at=ts(opened + timedelta(days=60), 9) if esc and opened + timedelta(days=60) <= end_day
                        else (ts(closed, 18) if closed else None),
                        reason="Анхны хуваарилалт")
            if esc and opened + timedelta(days=60) <= end_day:
                assigns.add(id=gen.next_id("collection.case_assignments"), case_id=case_id, assigned_to=None,
                            agency_id=agency.id, stage_id=stage_for_dpd(gen, 91).id,
                            assigned_at=ts(opened + timedelta(days=60), 9), assigned_by=gen.sys_user_id,
                            unassigned_at=ts(closed, 18) if closed else None, reason="Гэрээт байгууллагад шилжүүлэв")
            if is_last and ln.status == "WRITTEN_OFF" and ln.written_off:
                rec_pay = sum((p.amount for p in payments if p.ptype == "RECOVERY"), ZERO)
                started = ln.written_off["date"] + timedelta(days=rng.randint(5, 30))
                if started <= gen.as_of:
                    rtype = rng.weighted({"COLLATERAL_SALE": 0.3 if ln.collateral else 0.0, "GUARANTOR_CLAIM": 0.2,
                                          "LEGAL_JUDGMENT": 0.3, "VOLUNTARY_PAYMENT": 0.2})
                    done = rec_pay > 0 and rng.chance(0.3)
                    completed = min(gen.as_of, started + timedelta(days=rng.randint(30, 200))) if done else None
                    recov.add(id=gen.next_id("collection.recovery_cases"), recovery_no=gen.number("RECOVERY", started),
                              loan_id=ln.id, writeoff_id=getattr(ln, "writeoff_id", None), collection_case_id=case_id,
                              recovery_type=rtype,
                              expected_amount=D(ln.written_off["state"]["principal_outstanding"] * Decimal(str(rng.uniform(0.3, 0.8)))),
                              recovered_amount=rec_pay, recovery_cost=D(float(rec_pay) * 0.08 + 150_000),
                              currency_code=ln.currency,
                              court_case_ref=f"ИХШ-{started.year}/{rng.randint(100, 9999)}" if rtype == "LEGAL_JUDGMENT" else None,
                              bailiff_ref=f"ШШГ-{rng.randint(1000, 9999)}" if rtype == "LEGAL_JUDGMENT" and rng.chance(0.5) else None,
                              started_date=started, completed_date=completed,
                              status="COMPLETED" if done else rng.weighted({"IN_PROGRESS": 0.5, "COURT": 0.3,
                                                                            "ENFORCEMENT": 0.2}),
                              notes=None, created_at=ts(started), updated_at=ts(completed or gen.as_of))
