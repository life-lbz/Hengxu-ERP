from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from .domain import ACCOUNTS, ZERO, can_see, check_scope, exact, require
from .schema import Allocation, CostPool, Document, Journal, Movement, OpenItem, Stock


def inventory(session, user, day, warehouse, offset, limit, item=None):
    company = user.company
    if warehouse:
        check_scope(user, [warehouse])
    if day is None:
        query = select(Stock.warehouse, Stock.item, Stock.qty, Stock.reserved).where(Stock.company == company)
        code, material = Stock.warehouse, Stock.item
    else:
        code, material = Movement.warehouse, Movement.item
        query = (
            select(code, material, func.sum(Movement.qty), func.sum(Movement.reservation))
            .where(Movement.company == company, Movement.date <= day)
            .group_by(code, material)
        )
    if warehouse:
        query = query.where(code == warehouse)
    elif user.warehouses != ["*"]:
        query = query.where(code.in_(user.warehouses))
    if item:
        query = query.where(material == item)
    results = session.execute(query.order_by(code, material).offset(offset).limit(limit))
    return [
        {
            "warehouse": warehouse,
            "item": item,
            "on_hand": exact(qty),
            "reserved": exact(reserved),
            "available": exact(qty - reserved),
        }
        for warehouse, item, qty, reserved in results
    ]


def movement_details(session, user, day, warehouse, offset, limit, item=None):
    query = select(Movement).where(Movement.company == user.company, Movement.date <= day)
    if warehouse:
        check_scope(user, [warehouse])
        query = query.where(Movement.warehouse == warehouse)
    elif user.warehouses != ["*"]:
        query = query.where(Movement.warehouse.in_(user.warehouses))
    if item:
        query = query.where(Movement.item == item)
    rows = session.scalars(query.order_by(Movement.id.desc()).offset(offset).limit(limit))
    return [
        {
            "id": m.id,
            "date": m.date.isoformat(),
            "warehouse": m.warehouse,
            "item": m.item,
            "document": m.document,
            "line": m.line,
            "qty": exact(m.qty),
            "value": exact(m.value),
            "reservation": exact(m.reservation),
        }
        for m in rows
    ]


def active_at(query, day):
    reversal = aliased(Document)
    return query.outerjoin(reversal, reversal.reverse_of == Document.id).where(
        Document.status.in_(["confirmed", "posted", "reversed"]),
        Document.date <= day,
        or_(reversal.date.is_(None), reversal.date > day),
    )


def journals(session, user, day):
    return session.scalars(
        select(Journal)
        .where(Journal.company == user.company, Journal.date <= day)
        .order_by(Journal.date, Journal.id)
    )


def open_items(session, user, day):
    query = active_at(select(OpenItem, Document.date).join(Document, OpenItem.document == Document.id), day)
    settled_query = active_at(
        select(Allocation.source, Allocation.source_line, func.sum(Allocation.amount)).join(
            Document, Allocation.target == Document.id
        ),
        day,
    )
    settled = {
        (source, line): amount
        for source, line, amount in session.execute(
            settled_query.where(Allocation.company == user.company, Allocation.kind == "settlement").group_by(
                Allocation.source, Allocation.source_line
            )
        )
    }
    for item, issued in session.execute(
        query.where(OpenItem.company == user.company).order_by(
            Document.date, OpenItem.document, OpenItem.line
        )
    ):
        cleared = settled.get((item.document, item.line), ZERO)
        yield {
            "document": item.document,
            "line": item.line,
            "date": issued.isoformat(),
            "side": item.side,
            "partner": item.partner,
            "amount": exact(item.amount),
            "settled": exact(cleared),
            "outstanding": exact(item.amount - cleared),
            "age_days": (day - issued).days,
        }


def valued(session, user, day, current):
    if current:
        query = select(CostPool.item, CostPool.qty, CostPool.value).where(CostPool.company == user.company)
    else:
        query = (
            select(Movement.item, func.sum(Movement.qty), func.sum(Movement.value))
            .where(Movement.company == user.company, Movement.date <= day)
            .group_by(Movement.item)
        )
    return [
        {"item": item, "qty": exact(qty), "value": exact(value)}
        for item, qty, value in session.execute(query.order_by(query.selected_columns[0]))
    ]


def finance_report(session, user, report, day, current, offset, limit):
    require(
        user.role in {"admin", "supervisor", "finance", "cashier"} and can_see(user, ["*"]),
        "FINANCE_DENIED",
        "无权读取公司级财务报表",
        403,
    )
    if report == "journals":
        return [
            {
                "id": j.id,
                "document": j.document,
                "date": j.date.isoformat(),
                "rule": j.rule,
                "entries": j.entries,
            }
            for j in journals(session, user, day).yield_per(200)
        ][offset : offset + limit]
    if report == "open-items":
        return list(open_items(session, user, day))[offset : offset + limit]
    if report == "valuation":
        return valued(session, user, day, current)[offset : offset + limit]
    require(report in {"trial-balance", "reconciliation"}, "UNKNOWN_REPORT", "报表不存在", 404)
    balances = dict.fromkeys(ACCOUNTS, ZERO)
    for journal in journals(session, user, day):
        for entry in journal.entries:
            balances[entry["account"]] += Decimal(entry["amount"])
    if report == "trial-balance":
        return [
            {
                "account": account,
                "name": ACCOUNTS[account],
                "debit": exact(max(amount, ZERO)),
                "credit": exact(max(-amount, ZERO)),
            }
            for account, amount in balances.items()
        ][offset : offset + limit]
    subledgers = {
        "1405": sum((Decimal(row["value"]) for row in valued(session, user, day, current)), ZERO),
        "1122": ZERO,
        "2202": ZERO,
    }
    for row in open_items(session, user, day):
        amount = Decimal(row["outstanding"])
        if row["side"] == "AR":
            subledgers["1122"] += amount
        else:
            subledgers["2202"] -= amount
    # Every production movement and settlement has a WIP leg.
    wip = ZERO
    for allocation, kind in session.execute(
        active_at(
            select(Allocation, Document.kind).join(Document, Allocation.target == Document.id), day
        ).where(
            Allocation.company == user.company,
            Allocation.kind.in_(["material", "expense", "completion", "lot_adjustment"]),
        )
    ):
        wip += allocation.amount if allocation.kind in {"material", "expense"} else -allocation.amount
    subledgers["4001"] = wip
    return [
        {
            "account": code,
            "name": ACCOUNTS[code],
            "ledger": exact(balances[code]),
            "subledger": exact(value),
            "difference": exact(balances[code] - value),
        }
        for code, value in subledgers.items()
    ]


def snapshot(session):
    if session.bind.dialect.name == "postgresql":
        session.connection().exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
    elif session.bind.dialect.name == "sqlite":
        session.connection().exec_driver_sql("BEGIN")
    return datetime.now(timezone.utc).isoformat()
