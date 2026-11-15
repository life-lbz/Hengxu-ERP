from decimal import Decimal

from sqlalchemy import select

from .domain import ZERO, decimal, exact, money, positive, require
from .schema import Document, OpenItem


def place(post, row):
    item = post.master("item", row.get("item"))
    warehouse = post.master("warehouse", row.get("warehouse"))
    return item, warehouse


def order(post, row):
    item, warehouse = place(post, row)
    qty = positive(row["qty"])
    price = positive(row["price"])
    if post.doc.kind == "sales_order":
        post.master("customer", row["partner"])
        post.movement(row, warehouse, item, ZERO, ZERO, qty)
    else:
        post.master("supplier", row["partner"])
    row.update(qty=exact(qty), price=exact(price))


def fulfillment(post, row):
    purchase = post.doc.kind == "receipt"
    doc, source = post.source(row, "purchase_order" if purchase else "sales_order")
    qty = positive(row["qty"])
    used, _ = post.totals(doc, source, "fulfillment")
    require(used + qty <= Decimal(source["qty"]), "FULFILLMENT_LIMIT", "超过来源订单剩余数量")
    item, warehouse = source["item"], source["warehouse"]
    value = money(qty * Decimal(source["price"])) if purchase else None
    amount = post.movement(row, warehouse, item, qty if purchase else -qty, value, ZERO if purchase else -qty)
    if purchase:
        post.pair(row, "1405", "2001", amount)
    else:
        post.pair(row, "5401", "1405", -amount)
    post.allocate(doc, source, row, "fulfillment", qty)
    row.update(
        item=item,
        warehouse=warehouse,
        qty=exact(qty),
        stock_amount=exact(abs(amount)),
        price=source["price"],
        partner=source["partner"],
    )


def goods_return(post, row):
    purchase = post.doc.kind == "purchase_return"
    doc, source = post.source(row, "receipt" if purchase else "shipment")
    qty = positive(row["qty"])
    returned, returned_cost = post.totals(doc, source, "return")
    invoiced, invoiced_cost = post.totals(doc, source, "invoice")
    original = Decimal(source["qty"])
    require(returned + invoiced + qty <= original, "RETURN_LIMIT", "超过未开票可退数量")
    order_doc = post.session.get(Document, source["source"])
    order_line = next(line for line in order_doc.data["lines"] if line["id"] == source["source_line"])
    previous = Decimal(source["stock_amount"])
    total_qty = returned + qty + (invoiced if purchase else ZERO)
    cost = money(total_qty * previous / original) - returned_cost - (invoiced_cost if purchase else ZERO)
    place_ = (source["warehouse"], source["item"])
    if purchase:
        out_cost = -post.movement(row, *place_, -qty)
        post.pair(row, "2001", "1405", cost)
        post.pair(row, "5403", "1405", out_cost - cost)
    else:
        post.movement(row, *place_, qty, cost, qty)
        post.pair(row, "1405", "5401", cost)
    post.allocate(doc, source, row, "return", qty, cost)
    post.allocate(order_doc, order_line, row, "fulfillment", -qty)
    row.update(item=source["item"], warehouse=source["warehouse"], qty=exact(qty), partner=source["partner"])


def invoice(post, row):
    purchase = post.doc.kind == "purchase_invoice"
    doc, source = post.source(row, "receipt" if purchase else "shipment")
    qty = positive(row["qty"])
    returned, return_cost = post.totals(doc, source, "return")
    invoiced, prior_cost = post.totals(doc, source, "invoice")
    original = Decimal(source["qty"])
    require(returned + invoiced + qty <= original, "INVOICE_LIMIT", "超过来源行可开票数量")
    partner = source["partner"]
    amount = money(positive(row.get("price") or source["price"]) * qty)
    require(amount > 0, "INVALID_AMOUNT", "开票金额舍入后须大于零", 422)
    accrual = money((returned + invoiced + qty) * Decimal(source["stock_amount"]) / original)
    accrual -= prior_cost + return_cost
    if purchase:
        post.pair(row, "2001", "2202", accrual, partner)
        post.pair(row, "5403", "2202", amount - accrual, partner)
    else:
        post.pair(row, "1122", "5001", amount, partner)
    post.open_item(row, partner, "AP" if purchase else "AR", amount)
    post.allocate(doc, source, row, "invoice", qty, accrual)
    row.update(qty=exact(qty), amount=exact(amount), partner=partner)


def settle(post, row):
    paying = post.doc.kind == "payment"
    doc, source = post.source(row, "purchase_invoice" if paying else "sales_invoice", "opening")
    item = post.session.get(OpenItem, (doc.id, source["id"]))
    require(item and item.side == ("AP" if paying else "AR"), "INVALID_OPEN_ITEM", "来源往来方向不匹配")
    _, used = post.totals(doc, source, "settlement")
    amount = positive(row["amount"], 2)
    require(used + amount <= item.amount, "SETTLEMENT_LIMIT", "超过往来明细未结金额")
    accounts = ("2202", "1002") if paying else ("1002", "1122")
    post.pair(row, *accounts, amount, item.partner)
    post.allocate(doc, source, row, "settlement", amount=amount)
    row.update(amount=exact(amount), partner=item.partner)


def opening(post, row):
    category = row["category"]
    if category == "stock":
        item, warehouse = place(post, row)
        qty, amount = positive(row["qty"]), positive(row["amount"], 2)
        post.movement(row, warehouse, item, qty, amount)
        post.pair(row, "1405", "3001", amount)
    elif category in {"AR", "AP"}:
        receivable = category == "AR"
        partner = post.master("customer" if receivable else "supplier", row["partner"])
        amount = positive(row["amount"], 2)
        post.open_item(row, partner, category, amount)
        post.pair(row, *("1122", "3001") if receivable else ("3001", "2202"), amount, partner)
    else:
        require(False, "INVALID_OPENING_LINE", "请选择库存、应收或应付期初类型", 422)


def validate_opening(post):
    existing = post.session.scalar(
        select(Document.id)
        .where(
            Document.company == post.company,
            Document.id != post.doc.id,
            Document.status.in_(["posted", "confirmed", "reversed"]),
        )
        .limit(1)
    )
    require(existing is None, "ALREADY_OPEN", "期初只能在首笔业务前启用一次")
    equity = ZERO
    for row in post.doc.data["lines"]:
        amount = positive(row["amount"], 2)
        equity += -amount if row["category"] == "AP" else amount
    require(
        equity == decimal(post.doc.data["equity"], 2), "UNBALANCED_OPENING", "期初子账与录入的权益余额不平"
    )


def transfer(post, row):
    item, warehouse = place(post, row)
    target = post.master("warehouse", row["to_warehouse"])
    require(warehouse != target, "SAME_WAREHOUSE", "调出和调入仓库必须不同", 422)
    qty = positive(row["qty"])
    stock = post.stock(warehouse, item)
    require(stock.qty - stock.reserved >= qty, "INSUFFICIENT_STOCK", "调拨数量超过可用库存")
    pool = post.pool(item)
    amount = money(qty * pool.value / pool.qty)
    post.movement(row, warehouse, item, -qty, -amount, transfer=True)
    post.movement(row, target, item, qty, amount, transfer=True)


def count(post, row):
    item, warehouse = place(post, row)
    stock = post.stock(warehouse, item)
    counted = decimal(row["qty"])
    require(counted >= 0, "INVALID_COUNT", "盘点数量不得为负数", 422)
    require(stock.qty == decimal(row["expected_qty"]), "COUNT_STALE", "库存已变化，请重新核对账面数量")
    delta = counted - stock.qty
    require(delta != 0, "NO_COUNT_DIFFERENCE", "盘点无差异")
    value = money(delta * positive(row["price"])) if delta > 0 else None
    amount = post.movement(row, warehouse, item, delta, value)
    post.pair(row, "1405", "5402", amount)


HANDLERS = {
    "purchase_order": order,
    "sales_order": order,
    "receipt": fulfillment,
    "shipment": fulfillment,
    "purchase_return": goods_return,
    "sales_return": goods_return,
    "purchase_invoice": invoice,
    "sales_invoice": invoice,
    "payment": settle,
    "collection": settle,
    "opening": opening,
    "transfer": transfer,
    "count": count,
}
