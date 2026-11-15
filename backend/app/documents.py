import json
from decimal import Decimal
from hashlib import sha256

from sqlalchemy import select

from . import manufacturing, trade
from .domain import (
    Posting,
    audit,
    check_scope,
    document,
    ensure_sequence,
    exact,
    fail,
    lock_company,
    parse_day,
    period_for,
    require,
    uid,
)
from .schema import Allocation, Company, Document, Journal, Movement, Operation

# Per-kind fields are validated both when drafting and before posting.
FIELDS = {
    "opening": "category amount | item warehouse qty partner",
    "purchase_order": "item warehouse partner qty price",
    "sales_order": "item warehouse partner qty price",
    "receipt": "source source_line qty",
    "shipment": "source source_line qty",
    "purchase_return": "source source_line qty",
    "sales_return": "source source_line qty",
    "purchase_invoice": "source source_line qty | price",
    "sales_invoice": "source source_line qty | price",
    "payment": "source source_line amount",
    "collection": "source source_line amount",
    "transfer": "item warehouse to_warehouse qty",
    "count": "item warehouse qty expected_qty | price",
    "bom": "item component qty",
    "work_order": "source source_line item warehouse qty",
    "issue": "source source_line item warehouse qty",
    "material_return": "source source_line qty",
    "scrap": "source source_line qty",
    "production_expense": "source source_line category amount",
    "completion": "source source_line qty | price",
    "work_settlement": "source source_line",
}
ROLES = {
    "buyer": {"purchase_order"},
    "seller": {"sales_order"},
    "warehouse": {
        "receipt",
        "shipment",
        "purchase_return",
        "sales_return",
        "transfer",
        "count",
        "issue",
        "material_return",
        "scrap",
        "completion",
    },
    "finance": {"opening", "purchase_invoice", "sales_invoice", "production_expense", "work_settlement"},
    "cashier": {"payment", "collection"},
    "production": {"bom", "work_order"},
}
APPROVAL = {"opening", "purchase_order", "sales_order", "count", "work_order"}


def write_permission(user, kind):
    require(
        user.role == "admin" or kind in ROLES.get(user.role, set()),
        "ROLE_DENIED",
        "该岗位无权维护或确认此类单据",
        403,
    )


def view(doc):
    return {
        "id": doc.id,
        "number": doc.id[:8].upper(),
        "kind": doc.kind,
        "date": doc.date.isoformat(),
        "status": doc.status,
        "version": doc.version,
        "reverse_of": doc.reverse_of,
        "warehouses": doc.scope,
        **doc.data,
    }


def progress(doc, allocations):
    if doc.status not in {"confirmed", "posted"}:
        return []
    metric = {
        "purchase_order": ("qty", "fulfillment", "qty"),
        "sales_order": ("qty", "fulfillment", "qty"),
        "purchase_invoice": ("amount", "settlement", "amount"),
        "sales_invoice": ("amount", "settlement", "amount"),
        "work_order": ("qty", "completion", "qty"),
    }.get(doc.kind)
    rows = []
    for index, line in enumerate(doc.data["lines"], 1):
        active = [
            link
            for link in allocations
            if link["source_line"] == line["id"] and link["status"] in {"confirmed", "posted"}
        ]
        if doc.kind in {"receipt", "shipment"}:
            total = Decimal(line["qty"])
            returned = sum(Decimal(link["qty"]) for link in active if link["kind"] == "return")
            invoiced = sum(Decimal(link["qty"]) for link in active if link["kind"] == "invoice")
            rows.append(
                {
                    "row": index,
                    "item": line["item"],
                    "qty": exact(total),
                    "returned": exact(returned),
                    "invoiced": exact(invoiced),
                    "unbilled": exact(total - returned - invoiced),
                }
            )
        elif metric:
            base, kind, field = metric
            total = Decimal(line[base])
            completed = sum(Decimal(link[field]) for link in active if link["kind"] == kind)
            rows.append(
                {
                    "row": index,
                    base: exact(total),
                    "completed": exact(completed),
                    "remaining": exact(total - completed),
                }
            )
    return rows


def make_data(session, user, kind, payload, previous=None):
    require(kind in FIELDS, "UNKNOWN_KIND", "单据类型不存在", 404)
    required, _, optional = FIELDS[kind].partition("|")
    required, allowed = set(required.split()), set((required + optional).split())
    rows = payload["lines"]
    require(0 < len(rows) <= 200, "INVALID_LINES", "单据须包含 1 至 200 行", 422)
    scopes, known_ids, seen = set(), set(), set()
    if previous:
        known_ids = {row["id"] for row in previous.data["lines"]}
    normalized = []
    for row in rows:
        if kind == "opening":
            category = row.get("category")
            require(category in {"stock", "AR", "AP"}, "INVALID_OPENING_LINE", "期初类别无效", 422)
            required = {"category", "amount"} | (
                {"item", "warehouse", "qty"} if category == "stock" else {"partner"}
            )
        if kind == "count":
            from .domain import decimal

            if decimal(row.get("qty")) > decimal(row.get("expected_qty")):
                require(row.get("price"), "COUNT_PRICE_REQUIRED", "盘盈必须填写计价单价", 422)
        require(
            required <= row.keys() and row.keys() <= allowed | {"id"},
            "INVALID_FIELDS",
            "单据行字段不完整或无效",
            422,
        )
        require(
            all(isinstance(value, str) and 0 < len(value.strip()) <= 80 for value in row.values()),
            "INVALID_FIELDS",
            "单据行字段须为非空字符串",
            422,
        )
        line_id = row.get("id")
        require(
            line_id is None or line_id in known_ids and line_id not in seen,
            "INVALID_LINE_ID",
            "单据行编号无效或重复",
            422,
        )
        line_id = line_id or uid()
        seen.add(line_id)
        scopes.update(row[key] for key in ("warehouse", "to_warehouse") if key in row)
        if "source" in row:
            source = document(session, user, row["source"])
            scopes.update(source.scope)
        normalized.append({**row, "id": line_id})
    check_scope(user, scopes)
    data = {"lines": normalized, "note": payload.get("note", "")}
    if kind == "opening":
        require(payload.get("equity") is not None, "MISSING_EQUITY", "请填写期初权益", 422)
        data["equity"] = payload["equity"]
    return data, sorted(scopes)


def draft(session, user, kind, payload):
    write_permission(user, kind)
    if kind in manufacturing.HANDLERS:
        require(session.get(Company, user.company).manufacturing, "MODULE_DISABLED", "本账套未启用制造", 403)
    data, scope = make_data(session, user, kind, payload)
    doc = Document(
        id=uid(),
        company=user.company,
        kind=kind,
        date=parse_day(payload["date"]),
        data=data,
        scope=scope,
        creator=user.id,
    )
    session.add(doc)
    session.flush()
    audit(session, user, "create", doc.id, version=doc.version, snapshot=view(doc))
    return view(doc)


def edit(session, user, identifier, payload):
    doc = document(session, user, identifier)
    write_permission(user, doc.kind)
    require(
        doc.status == "draft" and doc.version == payload["version"],
        "VERSION_CONFLICT",
        "草稿状态或版本已变化",
    )
    doc.data, doc.scope = make_data(session, user, doc.kind, payload, doc)
    doc.date = parse_day(payload["date"])
    doc.version += 1
    audit(session, user, "edit", doc.id, version=doc.version, snapshot=view(doc))
    return view(doc)


def confirm(session, user, doc, inject_failure):
    period_for(session, doc.company, doc.date)
    ensure_sequence(session, doc.company, doc.date)
    post = Posting(session, user, doc)
    if doc.kind in manufacturing.HANDLERS:
        manufacturing.enabled(post)
    if doc.kind == "opening":
        trade.validate_opening(post)
    handler = (trade.HANDLERS | manufacturing.HANDLERS)[doc.kind]
    doc.data = {**doc.data, "lines": [dict(row) for row in doc.data["lines"]]}
    for row in doc.data["lines"]:
        handler(post, row)
    # JSON columns require a new assigned value after mutating nested rows.
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(doc, "data")
    post.finish(inject_failure)


def reverse(session, user, doc, payload, inject_failure):
    require(user.role in {"admin", "finance"}, "ROLE_DENIED", "仅会计可冲销", 403)
    require(
        doc.status in {"confirmed", "posted"} and doc.kind not in {"opening", "reversal", "work_settlement"},
        "INVALID_REVERSAL",
        "该单据状态或类型不支持冲销",
    )
    require(payload["reason"].strip() and payload["date"], "REVERSAL_REASON", "请填写冲销原因和业务日期", 422)
    day = parse_day(payload["date"])
    require(
        day >= doc.date and day.strftime("%Y-%m") == doc.date.strftime("%Y-%m"),
        "CROSS_PERIOD",
        "样板规则仅允许同一开放期间冲销",
    )
    period_for(session, doc.company, day)
    ensure_sequence(session, doc.company, day)
    downstream = session.scalar(
        select(Allocation.id)
        .join(Document, Allocation.target == Document.id)
        .where(
            Allocation.company == doc.company,
            Allocation.source == doc.id,
            Document.status.in_(["confirmed", "posted"]),
        )
        .limit(1)
    )
    require(downstream is None, "HAS_DOWNSTREAM", "请先处理有效下游单据")
    if doc.kind == "shipment":
        completion_dependencies = select(Allocation.source).where(
            Allocation.target == doc.id, Allocation.kind == "lot_shipment"
        )
        settled_lot = session.scalar(
            select(Allocation.id)
            .where(Allocation.kind == "lot_adjustment", Allocation.source.in_(completion_dependencies))
            .limit(1)
        )
        require(settled_lot is None, "WORK_SETTLED", "该发货已计入工单结算")
    constrained = {"issue", "material_return", "production_expense", "completion", "scrap"}
    if doc.kind in constrained:
        work = select(Allocation.source).where(
            Allocation.target == doc.id,
            Allocation.kind.in_(["material", "expense", "completion", "scrap_work"]),
        )
        settled = session.scalar(
            select(Allocation.id)
            .join(Document, Allocation.target == Document.id)
            .where(
                Allocation.company == doc.company,
                Allocation.kind == "work_settle",
                Allocation.source.in_(work),
                Document.status.in_(["posted", "confirmed"]),
            )
            .limit(1)
        )
        require(settled is None, "WORK_SETTLED", "工单已结算")
    reversal = Document(
        id=uid(),
        company=doc.company,
        kind="reversal",
        date=day,
        status="draft",
        creator=user.id,
        scope=doc.scope,
        reverse_of=doc.id,
        data={"note": payload["reason"], "lines": doc.data["lines"], "original_kind": doc.kind},
    )
    session.add(reversal)
    session.flush()
    post = Posting(session, user, reversal)
    moves = list(
        session.scalars(
            select(Movement)
            .where(Movement.company == doc.company, Movement.document == doc.id)
            .order_by(Movement.id.desc())
        )
    )
    for move in moves:
        later = session.scalar(
            select(Movement.id)
            .join(Document, Movement.document == Document.id)
            .where(
                Movement.company == doc.company,
                Movement.item == move.item,
                Movement.id > move.id,
                Movement.qty != 0,
                Movement.document.not_in([doc.id, reversal.id]),
                Document.status.in_(["confirmed", "posted"]),
                Document.kind != "reversal",
            )
            .limit(1)
        )
        require(
            not later or move.qty == 0, "LATER_STOCK_MOVEMENT", "该物料存在后续库存流水，请先处理后续动作"
        )
        post.movement(
            {"id": move.line},
            move.warehouse,
            move.item,
            -move.qty,
            -move.value,
            -move.reservation,
            transfer=doc.kind == "transfer",
        )
    journal = session.scalar(
        select(Journal).where(Journal.company == doc.company, Journal.document == doc.id)
    )
    if journal:
        post.entries = [{**entry, "amount": exact(-Decimal(entry["amount"]))} for entry in journal.entries]
    post.finish(inject_failure)
    doc.status = "reversed"
    return reversal


def action(session, user, identifier, name, payload, inject_failure):
    fingerprint = sha256(json.dumps([identifier, name, payload], sort_keys=True).encode()).hexdigest()
    lock_company(session, user.company)
    doc = document(session, user, identifier)
    operation_id = payload["operation_id"]
    prior = session.get(Operation, (user.company, operation_id))
    if prior:
        require(prior.actor == user.id, "OPERATION_DENIED", "无权读取该操作", 403)
        require(prior.fingerprint == fingerprint, "IDEMPOTENCY_CONFLICT", "操作编号已用于不同的请求")
        return prior.result
    require(doc.version == payload["version"], "VERSION_CONFLICT", "单据版本已变化，请刷新")
    doc.version += 1
    result_doc = doc
    if name == "submit":
        write_permission(user, doc.kind)
        require(doc.status == "draft", "STATE_CONFLICT", "仅草稿可以提交")
        doc.status = "review" if doc.kind in APPROVAL else "ready"
    elif name in {"approve", "reject"}:
        require(
            user.role in {"admin", "supervisor"} and user.id != doc.creator,
            "APPROVAL_DENIED",
            "须由另一位具有审批权限的账号处理",
            403,
        )
        require(doc.status == "review", "STATE_CONFLICT", "单据未处于待审状态")
        require(name != "reject" or payload["reason"].strip(), "REASON_REQUIRED", "退回须填写原因", 422)
        doc.status = "ready" if name == "approve" else "draft"
    elif name == "confirm":
        write_permission(user, doc.kind)
        require(doc.status == "ready", "STATE_CONFLICT", "单据须提交并完成适用审批")
        confirm(session, user, doc, inject_failure)
    elif name == "reverse":
        result_doc = reverse(session, user, doc, payload, inject_failure)
    else:
        fail("UNKNOWN_ACTION", "未知单据动作", 404)
    result = {"operation_id": operation_id, "document": view(result_doc)}
    voucher = session.scalar(select(Journal.id).where(Journal.document == result_doc.id))
    if voucher:
        result["journal_id"] = voucher
    session.add(
        Operation(
            company=user.company, id=operation_id, actor=user.id, fingerprint=fingerprint, result=result
        )
    )
    audit(session, user, name, doc.id, payload["reason"], doc.version, snapshot=view(doc))
    return result
