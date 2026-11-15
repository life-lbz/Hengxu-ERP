from decimal import Decimal

from sqlalchemy import select

from .domain import ZERO, exact, money, positive, require
from .schema import Company, Document, Movement
from .trade import place


def bom(post, row):
    post.master("item", row["item"])
    post.master("item", row["component"])
    require(row["item"] != row["component"], "INVALID_BOM", "成品编码与组件编码必须不同")
    row["qty"] = exact(positive(row["qty"]))


def work_order(post, row):
    item, _ = place(post, row)
    doc, source = post.source(row, "bom")
    require(source["item"] == item, "BOM_MISMATCH", "BOM 与工单成品不一致")
    components = {line["component"]: line["qty"] for line in doc.data["lines"]}
    require(
        len(components) == len(doc.data["lines"]) and all(line["item"] == item for line in doc.data["lines"]),
        "INVALID_BOM",
        "同一 BOM 应为一个成品，且组件不可重复",
    )
    row.update(qty=exact(positive(row["qty"])), components=components)
    post.allocate(doc, source, row, "work_order")


def material(post, row):
    issuing = post.doc.kind == "issue"
    if issuing:
        doc, source = post.source(row, "work_order")
        item, warehouse = place(post, row)
        require(item in source["components"], "ITEM_NOT_IN_BOM", "领料物料不在 BOM")
    else:
        receipt, receipt_line = post.source(row, "issue")
        doc = post.session.get(Document, receipt_line["work_order"])
        source = work_line(doc, receipt_line["work_line"])
        item, warehouse = receipt_line["item"], receipt_line["warehouse"]
    ensure_active(post, doc, source)
    qty = positive(row["qty"])
    if issuing:
        amount = -post.movement(row, warehouse, item, -qty)
    else:
        returned, credited = post.totals(receipt, receipt_line, "material_return")
        scrapped, _ = post.totals(receipt, receipt_line, "scrap")
        original = Decimal(receipt_line["qty"])
        require(qty + returned + scrapped <= original, "RETURN_LIMIT", "超过原领料剩余可退数量")
        amount = credited - money((qty + returned) * Decimal(receipt_line["amount"]) / original)
        post.movement(row, warehouse, item, qty, -amount)
        post.allocate(receipt, receipt_line, row, "material_return", qty, -amount)
    post.pair(row, "4001", "1405", amount)
    post.allocate(doc, source, row, "material", qty if issuing else -qty, amount)
    row.update(
        work_order=doc.id,
        work_line=source["id"],
        item=item,
        warehouse=warehouse,
        qty=exact(qty),
        amount=exact(abs(amount)),
    )


def work_line(doc, line_id):
    return next(line for line in doc.data["lines"] if line["id"] == line_id)


def scrap(post, row):
    require(post.doc.data["note"].strip(), "REASON_REQUIRED", "请在备注中填写报废原因", 422)
    issue, line = post.source(row, "issue")
    work = post.session.get(Document, line["work_order"])
    source = work_line(work, line["work_line"])
    ensure_active(post, work, source)
    qty = positive(row["qty"])
    used, _ = post.totals(issue, line, "material_return", "scrap")
    require(qty + used <= Decimal(line["qty"]), "SCRAP_LIMIT", "超过原领料剩余数量")
    post.allocate(issue, line, row, "scrap", qty)
    post.allocate(work, source, row, "scrap_work", qty)
    row.update(item=line["item"], qty=exact(qty))


def ensure_active(post, doc, source):
    _, settled = post.totals(doc, source, "work_settle")
    require(settled == 0, "WORK_SETTLED", "工单已结算")


def expense(post, row):
    doc, source = post.source(row, "work_order")
    ensure_active(post, doc, source)
    amount = positive(row["amount"], 2)
    require(row["category"] in {"labor", "overhead"}, "INVALID_EXPENSE", "请选择人工或制造费用", 422)
    post.allocate(doc, source, row, "expense", amount=amount)
    post.pair(row, "4001", "4101", amount)


def active_completions(post, item):
    docs = post.session.scalars(
        select(Document).where(
            Document.company == post.company,
            Document.kind == "completion",
            ((Document.status.in_(["posted", "confirmed"])) | (Document.id == post.doc.id)),
        )
    )
    for doc in docs:
        for line in doc.data["lines"]:
            if line.get("item") != item or "work_order" not in line:
                continue
            work = post.session.get(Document, line["work_order"])
            source = work_line(work, line["work_line"])
            _, settled = post.totals(work, source, "work_settle")
            if not settled:
                yield doc, line


def trace_stock(post, row, warehouse, item, qty, value):
    if not qty or post.doc.kind in {"completion", "work_settlement", "reversal"}:
        return
    if not post.session.get(Company, post.company).manufacturing:
        return
    lots = list(active_completions(post, item))
    if not lots:
        return
    require(post.doc.kind == "shipment", "UNSETTLED_ITEM", "该成品存在未结算批次，请先完成工单结算")
    left = -qty
    for doc, line in sorted(lots, key=lambda pair: (pair[0].date, pair[1]["movement"])):
        if warehouse != line["warehouse"]:
            continue
        used, _ = post.totals(doc, line, "lot_shipment")
        take = min(left, Decimal(line["qty"]) - used)
        if take:
            allocated = -value * (-qty - left) / -qty
            next_share = -value * (-qty - left + take) / -qty
            post.allocate(doc, line, row, "lot_shipment", take, money(next_share) - money(allocated))
            left -= take
        if left == 0:
            break
    require(left == 0, "LOT_SHORTAGE", "完工批次与发货数量无法匹配")


def completion(post, row):
    work, source = post.source(row, "work_order")
    ensure_active(post, work, source)
    item, warehouse = source["item"], source["warehouse"]
    qty = positive(row["qty"])
    completed, _ = post.totals(work, source, "completion")
    require(completed + qty <= Decimal(source["qty"]), "COMPLETION_LIMIT", "超过计划完工数量")
    lots = list(active_completions(post, item))
    require(
        all(line["work_order"] == work.id and line["work_line"] == source["id"] for _, line in lots),
        "UNSETTLED_ITEM",
        "同物料已有其他未结算工单",
    )
    require(lots or post.pool(item).qty == 0, "LOT_OPENING_STOCK", "样板规则要求首次完工前成品现存量为零")
    _, material_cost = post.totals(work, source, "material")
    _, expenses = post.totals(work, source, "expense")
    price = row.get("price")
    amount = (
        money(qty * positive(price))
        if price
        else money(qty * (material_cost + expenses) / Decimal(source["qty"]))
    )
    post.movement(row, warehouse, item, qty, amount)
    post.pair(row, "1405", "4001", amount)
    post.allocate(work, source, row, "completion", qty, amount)
    post.session.flush()
    movement = post.session.scalar(
        select(Movement.id).where(Movement.document == post.doc.id, Movement.line == row["id"])
    )
    row.update(
        item=item,
        warehouse=warehouse,
        qty=exact(qty),
        amount=exact(amount),
        work_order=work.id,
        work_line=source["id"],
        movement=movement,
    )


def work_settlement(post, row):
    work, source = post.source(row, "work_order")
    ensure_active(post, work, source)
    qty = Decimal(source["qty"])
    completed, _ = post.totals(work, source, "completion")
    require(completed == qty, "WORK_INCOMPLETE", "计划数量尚未全部完工")
    _, material_cost = post.totals(work, source, "material")
    _, expense_cost = post.totals(work, source, "expense")
    total = material_cost + expense_cost
    lots = sorted(active_completions(post, source["item"]), key=lambda pair: pair[1]["movement"])
    done = ZERO
    adjustments = []
    total_inventory_delta = ZERO
    for doc, line in lots:
        if line["work_order"] != work.id or line["work_line"] != source["id"]:
            continue
        batch_qty = Decimal(line["qty"])
        sold, sold_cost = post.totals(doc, line, "lot_shipment")
        # Reprice each traceable destination against the company-level average actually shipped.
        effective_shipments = list(post.session.scalars(post.allocations(doc, line, "lot_shipment")))
        sources = [use.target for use in effective_shipments]
        target_cost = money(total * (done + batch_qty) / qty) - money(total * done / qty)
        recognized = Decimal(line["amount"])
        sold_delta = money(target_cost * sold / batch_qty) - sold_cost
        inventory_delta = target_cost - recognized - sold_delta
        total_inventory_delta += inventory_delta
        post.pair(row, "1405", "4001", inventory_delta)
        post.pair(row, "5401", "4001", sold_delta)
        post.allocate(doc, line, row, "lot_adjustment", amount=inventory_delta + sold_delta)
        adjustments.append(
            {
                "completion": doc.id,
                "line": line["id"],
                "shipments": sources,
                "inventory": exact(inventory_delta),
                "cogs": exact(sold_delta),
            }
        )
        done += batch_qty
    require(done == qty, "LOT_INCOMPLETE", "缺少可追溯的完工批次")
    if total_inventory_delta:
        post.movement(row, source["warehouse"], source["item"], ZERO, total_inventory_delta)
    post.allocate(work, source, row, "work_settle", amount=Decimal(1))
    materials = {}
    scrap_by_item = {}
    for a in post.session.scalars(post.allocations(work, source, "material")):
        movement_doc = post.session.get(Document, a.target)
        item = work_line(movement_doc, a.target_line)["item"]
        materials[item] = materials.get(item, ZERO) + a.qty
    for a in post.session.scalars(post.allocations(work, source, "scrap_work")):
        scrap_doc = post.session.get(Document, a.target)
        item = work_line(scrap_doc, a.target_line)["item"]
        scrap_by_item[item] = scrap_by_item.get(item, ZERO) + a.qty
    row.update(
        amount=exact(total),
        unit_cost=exact(money(total / qty)),
        adjustments=adjustments,
        usage={
            item: {
                "actual": exact(materials.get(item, ZERO)),
                "scrap": exact(scrap_by_item.get(item, ZERO)),
                "variance": exact(materials.get(item, ZERO) - Decimal(rate) * qty),
            }
            for item, rate in source["components"].items()
        },
    )


HANDLERS = {
    "bom": bom,
    "work_order": work_order,
    "issue": material,
    "material_return": material,
    "production_expense": expense,
    "completion": completion,
    "work_settlement": work_settlement,
    "scrap": scrap,
}


def enabled(post):
    require(post.session.get(Company, post.company).manufacturing, "MODULE_DISABLED", "本账套未启用制造", 403)
