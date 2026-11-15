"""Prepare V10 samples in an empty database, then verify a separately restored database."""

import argparse
import json
import os
from pathlib import Path
from time import perf_counter

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.db import make_engine, transaction
from backend.app.main import create_app
from backend.app.migrate import upgrade
from backend.app.schema import Company
from backend.app.seed import seed
from tests.test_erp import (
    ERP,
    PASSWORD,
    check_manufacturing,
    linked,
    manufacturing_sample,
    numbers,
    order_line,
    purchase_sample,
)


def prepare(engine, evidence):
    upgrade(engine)
    with transaction(engine) as session:
        if session.scalar(select(Company.id).limit(1)) is not None:
            raise ValueError("Use an empty, dedicated PostgreSQL database for V10 preparation")
        seed(session, PASSWORD)
        seed(session, PASSWORD, company="FACTORY")
    with TestClient(create_app(engine)) as client:
        purchase, invoice = purchase_sample(ERP(client))
        settlement = manufacturing_sample(ERP(client, company="FACTORY"))
        evidence.write_text(
            json.dumps(
                {"purchase": purchase["id"], "invoice": invoice["id"], "settlement": settlement["id"]}
            ),
            encoding="utf-8",
        )


def verify(engine, evidence):
    started = perf_counter()
    ids = json.loads(evidence.read_text(encoding="utf-8"))
    with TestClient(create_app(engine)) as client:
        erp = ERP(client)
        invoice = erp.get("/documents/" + ids["invoice"])
        for line in invoice["lines"]:
            receipt = erp.get("/documents/" + line["source"])
            assert any(source["source"] == ids["purchase"] for source in receipt["lines"])
        journals = erp.get("/reports/journals")["rows"]
        assert any(row["document"] == invoice["id"] for row in journals)
        assert numbers(erp.get("/reports/valuation")["rows"], "value") == [900]
        assert numbers(erp.get("/reports/reconciliation")["rows"], "difference") == [0, 0, 0, 0]

        factory = ERP(client, company="FACTORY")
        check_manufacturing(factory, factory.get("/documents/" + ids["settlement"]))

        order = erp.confirm("sales_order", [order_line("1", sales=True)], "seller")
        erp.confirm("shipment", [linked(order, qty="1")], "warehouse")
        assert numbers(erp.get("/reports/inventory")["rows"], "on_hand") == [89]
        assert numbers(erp.get("/reports/reconciliation")["rows"], "difference") == [0, 0, 0, 0]
    print(
        f"V10 verified V01 and V05 source links, balances, and a new shipment in {perf_counter() - started:.2f}s"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "verify"])
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    url = os.environ["ERP_TEST_DATABASE_URL"]
    if not url.startswith("postgresql"):
        parser.error("V10 requires PostgreSQL")
    engine = make_engine(url)
    try:
        scenario = prepare if args.mode == "prepare" else verify
        scenario(engine, args.evidence)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
