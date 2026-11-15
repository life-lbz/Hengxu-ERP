from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update

from .schema import (
    Allocation,
    Audit,
    Company,
    CostPool,
    Document,
    Event,
    Journal,
    Master,
    Movement,
    OpenItem,
    Period,
    Stock,
)

ZERO = Decimal(0)
ACCOUNTS = {
    "1002": "银行存款",
    "1122": "应收账款",
    "1405": "库存商品",
    "2001": "暂估应付",
    "2202": "应付账款",
    "3001": "期初权益",
    "4001": "在制品",
    "4101": "生产费用清算",
    "5001": "销售收入",
    "5401": "销售成本",
    "5402": "盘点差异",
    "5403": "采购价差",
}
FINANCE_DOCUMENTS = {
    "opening": set(),
    "purchase_invoice": {"buyer"},
    "sales_invoice": {"seller"},
    "payment": {"buyer"},
    "collection": {"seller"},
    "production_expense": {"production"},
    "work_settlement": {"production"},
}


def fail(code, message, status=409):
    raise HTTPException(status, {"code": code, "message": message})


def require(condition, code, message, status=409):
    if not condition:
        fail(code, message, status)


def decimal(value, places=6):
    quantum = Decimal(1).scaleb(-places)
    try:
        if isinstance(value, (float, bool)) or len(str(value)) > 32:
            raise ValueError
        number = Decimal(value)
        if not number.is_finite() or abs(number) >= Decimal("1000000000000"):
            raise ValueError
        result = number.quantize(quantum)
        if result != number:
            raise ValueError
        return result
    except (ValueError, TypeError, InvalidOperation):
        fail("INVALID_DECIMAL", "请以十进制字符串提交金额或数量，且精度必须符合字段要求", 422)


def positive(value, places=6):
    result = decimal(value, places)
    require(result > 0, "INVALID_QUANTITY", "金额或数量必须大于零", 422)
    return result


def money(value):
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def exact(value):
    return format(value, "f")


def uid():
    return str(uuid4())


def lock_company(session, company):
    # All company writes use the same transaction lock, including close and reversal.
    if session.bind.dialect.name == "postgresql":
        session.connection().exec_driver_sql("SET LOCAL lock_timeout = '4s'")
    session.execute(update(Company).where(Company.id == company).values(revision=Company.revision + 1))


def period_for(session, company, day):
    period = session.get(Period, (company, day.strftime("%Y-%m")))
    require(period is not None and not period.closed, "CLOSED_PERIOD", "业务日期所在期间未打开")
    return period


def can_see(user, scope):
    return user.warehouses == ["*"] or set(scope) <= set(user.warehouses)


def check_scope(user, scope):
    require(can_see(user, scope), "WAREHOUSE_DENIED", "无权访问单据涉及的仓库", 403)


def can_read(user, doc):
    kind = doc.data.get("original_kind", doc.kind)
    if kind not in FINANCE_DOCUMENTS:
        return True
    return user.role in {"admin", "supervisor", "finance", "cashier"} | FINANCE_DOCUMENTS[kind]


def document(session, user, identifier):
    doc = session.get(Document, identifier)
    require(doc is not None and doc.company == user.company, "DOCUMENT_NOT_FOUND", "单据不存在", 404)
    check_scope(user, doc.scope)
    require(can_read(user, doc), "ROLE_DENIED", "该岗位无权读取此类业务单据", 403)
    return doc


def audit(session, user, action, target, reason="", version=None, result="success", snapshot=None):
    session.add(
        Audit(
            company=user.company,
            actor=user.id,
            action=action,
            target=target,
            reason=reason,
            version=version,
            result=result,
            snapshot=snapshot,
        )
    )


def available_allocations(company, identifier, line, kinds=None, current=None):
    query = (
        select(Allocation)
        .join(Document, Allocation.target == Document.id)
        .where(
            Allocation.company == company,
            Allocation.source == identifier,
            Allocation.source_line == line,
            or_(Document.status.in_(["confirmed", "posted"]), Document.id == current),
        )
    )
    if kinds:
        query = query.where(Allocation.kind.in_(kinds))
    return query


class Posting:
    def __init__(self, session, user, doc):
        self.session = session
        self.user = user
        self.doc = doc
        self.company = user.company
        self.entries = []

    def master(self, kind, code):
        require(
            isinstance(code, str) and self.session.get(Master, (self.company, kind, code)),
            "UNKNOWN_MASTER",
            f"档案不存在或类型错误（{kind}）",
            422,
        )
        return code

    def source(self, row, *kinds):
        doc = document(self.session, self.user, row.get("source"))
        require(
            doc.kind in kinds and doc.status in {"posted", "confirmed"},
            "INVALID_SOURCE",
            "来源单据类型或状态无效",
        )
        line = next((line for line in doc.data["lines"] if line["id"] == row.get("source_line")), None)
        require(line is not None and doc.date <= self.doc.date, "INVALID_SOURCE_LINE", "来源行或业务日期无效")
        return doc, line

    def totals(self, doc, line, *kinds):
        query = self.allocations(doc, line, *kinds)
        quantity, amount = self.session.execute(
            query.with_only_columns(func.sum(Allocation.qty), func.sum(Allocation.amount))
        ).one()
        return quantity or ZERO, amount or ZERO

    def allocations(self, doc, line, *kinds):
        return available_allocations(self.company, doc.id, line["id"], kinds, self.doc.id)

    def allocate(self, doc, source, row, kind, qty=ZERO, amount=ZERO):
        self.session.add(
            Allocation(
                company=self.company,
                source=doc.id,
                source_line=source["id"],
                target=self.doc.id,
                target_line=row["id"],
                kind=kind,
                qty=qty,
                amount=amount,
            )
        )

    def stock(self, warehouse, item):
        key = (self.company, warehouse, item)
        balance = self.session.get(Stock, key)
        if balance is None:
            balance = Stock(company=self.company, warehouse=warehouse, item=item, qty=ZERO, reserved=ZERO)
            self.session.add(balance)
        return balance

    def pool(self, item):
        pool = self.session.get(CostPool, (self.company, item))
        if pool is None:
            pool = CostPool(company=self.company, item=item, qty=ZERO, value=ZERO)
            self.session.add(pool)
        return pool

    def movement(self, row, warehouse, item, qty, value=None, reservation=ZERO, transfer=False):
        stock = self.stock(warehouse, item)
        require(
            stock.qty + qty >= stock.reserved + reservation >= 0,
            "INSUFFICIENT_STOCK",
            "可用库存不足或原预留已释放",
        )
        if not transfer:
            pool = self.pool(item)
            if value is None:
                value = -pool.value if qty == -pool.qty else money(qty * pool.value / pool.qty)
            require(
                pool.value + value >= 0 and (pool.qty + qty or pool.value + value == 0),
                "COST_CONFLICT",
                "该动作会使库存成本失衡",
            )
            pool.qty += qty
            pool.value += value
        from .manufacturing import trace_stock

        trace_stock(self, row, warehouse, item, qty, value)
        stock.qty += qty
        stock.reserved += reservation
        self.session.add(
            Movement(
                company=self.company,
                warehouse=warehouse,
                item=item,
                document=self.doc.id,
                line=row["id"],
                date=self.doc.date,
                qty=qty,
                value=value,
                reservation=reservation,
            )
        )
        return value

    def pair(self, row, debit, credit, amount, partner=None):
        if amount == 0:
            return
        if amount < 0:
            debit, credit, amount = credit, debit, -amount
        for account, signed in ((debit, amount), (credit, -amount)):
            self.entries.append(
                {
                    "account": account,
                    "amount": exact(signed),
                    "line": row["id"],
                    "partner": partner if account in {"1122", "2202"} else None,
                }
            )

    def open_item(self, row, partner, side, amount):
        self.session.add(
            OpenItem(
                company=self.company,
                document=self.doc.id,
                line=row["id"],
                partner=partner,
                side=side,
                amount=amount,
            )
        )

    def finish(self, inject_failure):
        if self.entries:
            self.session.flush()
            inject_failure()
            require(sum(Decimal(entry["amount"]) for entry in self.entries) == 0, "UNBALANCED", "借贷不平")
            self.session.add(
                Journal(company=self.company, document=self.doc.id, date=self.doc.date, entries=self.entries)
            )
        self.doc.status = "posted" if self.entries else "confirmed"
        self.session.add(
            Event(
                id=uid(),
                company=self.company,
                document=self.doc.id,
                kind=self.doc.kind,
                payload={"status": self.doc.status, "version": self.doc.version},
            )
        )
        self.session.flush()


def ensure_sequence(session, company, day):
    latest = session.scalar(
        select(func.max(Document.date)).where(
            Document.company == company, Document.status.in_(["confirmed", "posted", "reversed"])
        )
    )
    require(latest is None or day >= latest, "BACKDATED", "样板规则禁止倒序补录")


def parse_day(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        fail("INVALID_DATE", "请输入有效的业务日期", 422)
