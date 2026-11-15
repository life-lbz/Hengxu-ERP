import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from backend.app.db import make_engine, transaction
from backend.app.main import create_app
from backend.app.migrate import upgrade
from backend.app.schema import Audit, Document, Journal, Movement
from backend.app.seed import seed

DAY = "2026-11-15"
PASSWORD = "sample-erp-password"
BASE = "/api/v1"


def ok(response):
    assert response.is_success, response.text
    return response.json()


class ERP:
    def __init__(self, client, company="DEMO"):
        self.client = client
        self.company = company
        self.tokens = {}

    def headers(self, actor):
        if actor not in self.tokens:
            user, _, company = actor.partition("@")
            result = ok(
                self.client.post(
                    BASE + "/auth/login",
                    json={
                        "company": company or self.company,
                        "username": user,
                        "password": PASSWORD,
                    },
                )
            )
            self.tokens[actor] = {"Authorization": "Bearer " + result["token"]}
        return self.tokens[actor]

    def get(self, path, actor="admin", **params):
        return ok(self.client.get(BASE + path, headers=self.headers(actor), params=params))

    def draft(self, kind, rows, actor="admin", **fields):
        return ok(
            self.client.post(
                BASE + "/documents/" + kind,
                headers=self.headers(actor),
                json={"date": DAY, "lines": rows, **fields},
            )
        )

    def action(self, doc, command, actor="admin", **fields):
        return self.client.post(
            BASE + f"/documents/{doc['id']}/actions/{command}",
            headers=self.headers(actor),
            json={"version": doc["version"], "operation_id": uuid4().hex, **fields},
        )

    def ready(self, kind, rows, actor="admin", **fields):
        draft = self.draft(kind, rows, actor, **fields)
        doc = ok(self.action(draft, "submit", actor))["document"]
        if doc["status"] == "review":
            doc = ok(self.action(doc, "approve", "manager"))["document"]
        return doc

    def confirm(self, kind, rows, actor="admin", **fields):
        doc = self.ready(kind, rows, actor, **fields)
        return ok(self.action(doc, "confirm", actor))["document"]


@pytest.fixture
def system():
    url = os.getenv("ERP_TEST_DATABASE_URL")
    schema = f"erp_{uuid4().hex}"
    base = None
    local = Path(f".{schema}.sqlite3").resolve()
    if url:
        assert url.startswith("postgresql"), "测试库须为独立的 PostgreSQL 数据库"
        base = make_engine(url)
        with base.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        from sqlalchemy.engine import make_url

        target = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    else:
        target = f"sqlite:///{local.as_posix()}"
    engine = make_engine(target)
    upgrade(engine)
    upgrade(engine)
    with transaction(engine) as session:
        seed(session, PASSWORD)
    app = create_app(engine)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield ERP(client), engine, app
    engine.dispose()
    local.unlink(missing_ok=True)
    if base:
        with base.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        base.dispose()


def order_line(qty, price="10", item="P100", sales=False):
    return {
        "item": item,
        "warehouse": "MAIN",
        "partner": "CUS01" if sales else "SUP01",
        "qty": qty,
        "price": price,
    }


def linked(doc, index=0, **fields):
    return {"source": doc["id"], "source_line": doc["lines"][index]["id"], **fields}


def stock_seed(erp, item="P100", qty="10", amount="100", **extra):
    return erp.confirm(
        "opening",
        [{"category": "stock", "item": item, "warehouse": "MAIN", "qty": qty, "amount": amount}],
        "finance",
        equity=amount,
        **extra,
    )


def numbers(rows, field):
    return [Decimal(row[field]) for row in rows]


def test_v08_opening_balance_and_roles(system):
    erp, _, _ = system
    assert erp.get("/health")["schema_version"] == 1
    rows = [
        {"category": "stock", "item": "P100", "warehouse": "MAIN", "qty": "10", "amount": "100"},
        {"category": "AR", "partner": "CUS01", "amount": "50"},
        {"category": "AP", "partner": "SUP01", "amount": "20"},
    ]
    bad = erp.ready("opening", rows, "finance", equity="129")
    assert erp.action(bad, "confirm", "finance").json()["code"] == "UNBALANCED_OPENING"
    assert erp.get("/reports/inventory")["rows"] == []
    doc = erp.ready("opening", rows, "finance", equity="130")
    forbidden = erp.action(doc, "confirm", "buyer")
    assert forbidden.status_code == 403
    response = ok(erp.action(doc, "confirm", "finance", operation_id="opening-confirm"))
    assert ok(erp.action(doc, "confirm", "finance", operation_id="opening-confirm")) == response
    assert len(erp.get("/reports/journals")["rows"]) == 1
    report = erp.get("/reports/reconciliation")["rows"]
    assert numbers(report, "subledger") == [100, 50, -20, 0]
    assert numbers(report, "difference") == [0, 0, 0, 0]


def purchase_sample(erp):
    order = erp.confirm("purchase_order", [order_line("100")], "buyer")
    first = erp.confirm("receipt", [linked(order, qty="60")], "warehouse")
    second = erp.confirm("receipt", [linked(order, qty="40")], "warehouse")
    erp.confirm("purchase_return", [linked(second, qty="10")], "warehouse")
    invoice = erp.confirm("purchase_invoice", [linked(first, qty="60"), linked(second, qty="30")], "finance")
    erp.confirm("payment", [linked(invoice, amount="300")], "cashier")
    erp.confirm("payment", [linked(invoice, amount="300"), linked(invoice, 1, amount="200")], "cashier")
    return order, invoice


def test_v01_v02_order_fulfillment(system):
    erp, _, _ = system
    order, purchase_invoice = purchase_sample(erp)
    detail = erp.get("/documents/" + order["id"])
    assert sum(numbers(detail["allocations"], "qty")) == 90
    assert numbers(detail["progress"], "remaining") == [10]
    order = erp.confirm("sales_order", [order_line("30", "15", sales=True)], "seller")
    first = erp.confirm("shipment", [linked(order, qty="20")], "warehouse")
    second = erp.confirm("shipment", [linked(order, qty="10")], "warehouse")
    invoice = erp.confirm("sales_invoice", [linked(first, qty="20"), linked(second, qty="10")], "finance")
    erp.confirm("collection", [linked(invoice, amount="200")], "cashier")
    assert numbers(erp.get("/reports/inventory")["rows"], "on_hand") == [60]
    assert numbers(erp.get("/reports/valuation")["rows"], "value") == [600]
    items = erp.get("/reports/open-items")["rows"]
    assert (
        sum(numbers([row for row in items if row["document"] == purchase_invoice["id"]], "outstanding"))
        == 100
    )
    assert sum(numbers([row for row in items if row["document"] == invoice["id"]], "outstanding")) == 250
    balances = {row["account"]: row for row in erp.get("/reports/trial-balance")["rows"]}
    assert Decimal(balances["5401"]["debit"]) == 300
    assert numbers(erp.get("/reports/reconciliation")["rows"], "difference") == [0, 0, 0, 0]


def test_v03_rollback_and_uncertain_result(system):
    erp, engine, app = system
    stock_seed(erp)
    order = erp.confirm("sales_order", [order_line("10", sales=True)], "seller")
    shipment = erp.ready("shipment", [linked(order, qty="3")], "warehouse")

    def crash():
        raise RuntimeError("test rollback after stock writes")

    app.state.before_journal = crash
    failed = erp.action(shipment, "confirm", "warehouse", operation_id="retry-shipment")
    assert failed.status_code == 500
    with transaction(engine) as session:
        assert session.get(Document, shipment["id"]).status == "ready"
        assert (
            session.scalar(
                select(func.count()).select_from(Movement).where(Movement.document == shipment["id"])
            )
            == 0
        )
    app.state.before_journal = lambda: None
    response = ok(erp.action(shipment, "confirm", "warehouse", operation_id="retry-shipment"))
    for _ in range(2):
        assert ok(erp.action(shipment, "confirm", "warehouse", operation_id="retry-shipment")) == response
    assert erp.get("/operations/retry-shipment", "warehouse") == response
    conflict = erp.action(shipment, "confirm", "warehouse", operation_id="retry-shipment", reason="changed")
    assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
    with transaction(engine) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Journal).where(Journal.document == shipment["id"])
            )
            == 1
        )


def test_v04_concurrent_source_and_transfer(system):
    erp, _, _ = system
    stock_seed(erp, qty="1", amount="10")
    order = erp.confirm("sales_order", [order_line("1", sales=True)], "seller")
    attempts = [erp.ready("shipment", [linked(order, qty="1")], "warehouse") for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(lambda doc: erp.action(doc, "confirm", "warehouse"), attempts))
    assert sorted(response.status_code for response in responses) == [200, 409]
    assert numbers(erp.get("/reports/inventory")["rows"], "available") == [0]
    supply = erp.confirm("purchase_order", [order_line("2")], "buyer")
    erp.confirm("receipt", [linked(supply, qty="2")], "warehouse")
    moved = erp.confirm(
        "transfer", [{"item": "P100", "warehouse": "MAIN", "to_warehouse": "AUX", "qty": "1"}], "warehouse"
    )
    moves = [row for row in erp.get("/reports/movements")["rows"] if row["document"] == moved["id"]]
    assert len(moves) == 2
    assert sum(numbers(moves, "qty")) == sum(numbers(moves, "value")) == 0


def test_same_document_cannot_overallocate(system):
    erp, _, _ = system
    order = erp.confirm("purchase_order", [order_line("10")], "buyer")
    receipt = erp.ready("receipt", [linked(order, qty="6"), linked(order, qty="6")], "warehouse")
    assert erp.action(receipt, "confirm", "warehouse").json()["code"] == "FULFILLMENT_LIMIT"
    assert erp.get("/reports/inventory")["rows"] == []


def manufacturing_sample(erp):
    stock_seed(erp, "M100", "23", "230")
    bom = erp.confirm("bom", [{"item": "FG100", "component": "M100", "qty": "2"}], "planner")
    work = erp.confirm("work_order", [linked(bom, item="FG100", warehouse="MAIN", qty="10")], "planner")
    issue = erp.confirm("issue", [linked(work, item="M100", warehouse="MAIN", qty="23")], "warehouse")
    erp.confirm("material_return", [linked(issue, qty="1")], "warehouse")
    erp.confirm("scrap", [linked(issue, qty="1")], "warehouse", note="领料损耗")
    erp.confirm(
        "production_expense",
        [linked(work, category="labor", amount="40"), linked(work, category="overhead", amount="20")],
        "finance",
    )
    erp.confirm("completion", [linked(work, qty="6", price="25")], "warehouse")
    order = erp.confirm("sales_order", [order_line("2", "45", "FG100", True)], "seller")
    erp.confirm("shipment", [linked(order, qty="2")], "warehouse")
    erp.confirm("completion", [linked(work, qty="4")], "warehouse")
    return erp.confirm("work_settlement", [linked(work)], "finance")


def check_manufacturing(erp, settlement):
    line = settlement["lines"][0]
    assert Decimal(line["unit_cost"]) == 28 and Decimal(line["amount"]) == 280
    assert Decimal(line["usage"]["M100"]["actual"]) == 22
    assert Decimal(line["usage"]["M100"]["variance"]) == 2
    assert Decimal(line["usage"]["M100"]["scrap"]) == 1
    assert Decimal(line["adjustments"][0]["inventory"]) == 12
    assert Decimal(line["adjustments"][0]["cogs"]) == 6
    for adjustment in line["adjustments"]:
        assert erp.get("/documents/" + adjustment["completion"])["kind"] == "completion"
        for shipment in adjustment["shipments"]:
            assert erp.get("/documents/" + shipment)["kind"] == "shipment"
    value = {row["item"]: Decimal(row["value"]) for row in erp.get("/reports/valuation")["rows"]}
    assert value["FG100"] == 224
    balances = {row["account"]: row for row in erp.get("/reports/trial-balance")["rows"]}
    assert Decimal(balances["5401"]["debit"]) == 56
    assert Decimal(balances["4001"]["debit"]) == Decimal(balances["4001"]["credit"]) == 0
    assert numbers(erp.get("/reports/reconciliation")["rows"], "difference") == [0, 0, 0, 0]


def test_v05_v06_manufacturing_cost_and_mixed_sales(system):
    erp, _, _ = system
    check_manufacturing(erp, manufacturing_sample(erp))
    order = erp.confirm("purchase_order", [order_line("5")], "buyer")
    erp.confirm("receipt", [linked(order, qty="5")], "warehouse")
    mixed = erp.confirm(
        "sales_order", [order_line("2", "45", "FG100", True), order_line("2", "15", "P100", True)], "seller"
    )
    erp.confirm("shipment", [linked(mixed, qty="1"), linked(mixed, 1, qty="2")], "warehouse")
    erp.confirm("shipment", [linked(mixed, qty="1")], "warehouse")
    assert all(value == 0 for value in numbers(erp.get("/reports/inventory")["rows"], "reserved"))


def test_v07_reversal_close_and_isolation(system):
    erp, engine, _ = system
    order = erp.confirm("purchase_order", [order_line("3")], "buyer")
    receipt = erp.confirm("receipt", [linked(order, qty="3")], "warehouse")
    reversal = ok(erp.action(receipt, "reverse", "finance", date=DAY, reason="样本更正"))["document"]
    assert reversal["reverse_of"] == receipt["id"]
    assert numbers(erp.get("/reports/inventory")["rows"], "on_hand") == [0]
    pending = erp.ready("receipt", [linked(order, qty="1")], "warehouse")
    denied = erp.client.get(
        BASE + "/reports/inventory", params={"warehouse": "AUX"}, headers=erp.headers("limited")
    )
    assert denied.status_code == 403
    with transaction(engine) as session:
        seed(session, PASSWORD, company="OTHER", manufacturing=False)
        assert session.scalar(select(func.count()).select_from(Audit).where(Audit.result == "denied")) == 1
    denied = erp.client.get(BASE + "/documents/" + receipt["id"], headers=erp.headers("admin@OTHER"))
    assert denied.status_code == 404
    assert "bom" not in erp.get("/masters", "admin@OTHER")["document_kinds"]
    trading_only = ERP(erp.client, company="OTHER")
    trade_order = trading_only.confirm("purchase_order", [order_line("1")], "buyer")
    trading_only.confirm("receipt", [linked(trade_order, qty="1")], "warehouse")
    trade_order = trading_only.confirm("sales_order", [order_line("1", sales=True)], "seller")
    trading_only.confirm("shipment", [linked(trade_order, qty="1")], "warehouse")
    assert numbers(trading_only.get("/reports/inventory")["rows"], "on_hand") == [0]
    ok(erp.client.post(BASE + "/periods/2026-11/close", headers=erp.headers("finance")))
    assert erp.action(pending, "confirm", "warehouse").json()["code"] == "CLOSED_PERIOD"
    assert numbers(erp.get("/reports/reconciliation")["rows"], "difference") == [0, 0, 0, 0]


def test_close_and_post_share_transaction_lock(system):
    erp, _, _ = system
    order = erp.confirm("purchase_order", [order_line("3")], "buyer")
    draft = erp.ready("receipt", [linked(order, qty="3")], "warehouse")
    finance = erp.headers("finance")
    with ThreadPoolExecutor(max_workers=2) as workers:
        close = workers.submit(erp.client.post, BASE + "/periods/2026-11/close", headers=finance)
        post = workers.submit(erp.action, draft, "confirm", "warehouse")
        ok(close.result())
        result = post.result()
    if result.is_success:
        assert erp.get("/reports/journals")["rows"][0]["document"] == draft["id"]
    else:
        assert result.json()["code"] == "CLOSED_PERIOD"
        assert not erp.get("/reports/journals")["rows"]
