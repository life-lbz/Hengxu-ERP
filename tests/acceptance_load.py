"""V09 load scenario. Use only an independent PostgreSQL acceptance database."""

import os
from concurrent.futures import ThreadPoolExecutor
from statistics import quantiles
from threading import Barrier
from time import perf_counter
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

from backend.app.auth import login
from backend.app.db import make_engine, transaction
from backend.app.main import create_app
from backend.app.migrate import upgrade
from backend.app.schema import User
from backend.app.seed import seed
from tests.test_erp import DAY, ERP, PASSWORD, linked, ok, order_line

LOAD_DOCUMENTS = """
INSERT INTO document (id, company, kind, date, status, version, creator, data, scope)
SELECT md5(d.n::text), 'DEMO', 'receipt', CAST(:day AS date), 'posted', 1, 'DEMO/admin',
       json_build_object('note', 'V09 synthetic load', 'lines', (
           SELECT json_agg(json_build_object('id', md5(d.n || ':' || l.n), 'qty', '5'))
           FROM generate_series(1, 200) AS l(n)
       )), '["MAIN"]'::json
FROM generate_series(1, 500) AS d(n)
"""
LOAD_MOVEMENTS = """
INSERT INTO movement (company, warehouse, item, document, line, date, qty, value, reservation)
SELECT 'DEMO', 'MAIN', 'V09-' || (n % 5000), md5((n / 1000 + 1)::text),
       md5((n / 1000 + 1) || ':' || (n % 200 + 1)), CAST(:day AS date), 1, 10, 0
FROM generate_series(0, 499999) AS s(n)
"""
LOAD_BALANCES = """
INSERT INTO stock (company, warehouse, item, qty, reserved)
SELECT company, warehouse, item, sum(qty), sum(reservation)
FROM movement GROUP BY company, warehouse, item
"""
LOAD_COSTS = """
INSERT INTO cost_pool (company, item, qty, value)
SELECT company, item, sum(qty), sum(value) FROM movement GROUP BY company, item
"""


def bootstrap(engine):
    upgrade(engine)
    with transaction(engine) as session:
        seed(session, PASSWORD, manufacturing=False)
        account = session.get(User, "DEMO/warehouse")
        headers = []
        for i in range(30):
            username = f"worker{i}"
            session.add(
                User(
                    id="DEMO/" + username,
                    company="DEMO",
                    name=username,
                    role="warehouse",
                    warehouses=["MAIN"],
                    password=account.password,
                )
            )
            session.flush()
            token, _ = login(session, "DEMO", username, PASSWORD)
            headers.append({"Authorization": "Bearer " + token})
        for statement in (LOAD_DOCUMENTS, LOAD_MOVEMENTS, LOAD_BALANCES, LOAD_COSTS):
            session.execute(text(statement), {"day": DAY})
    return headers


def exercise(engine, headers):
    timings = {"inventory": [], "posting": []}
    with TestClient(create_app(engine), raise_server_exceptions=False) as client:
        erp = ERP(client)
        order = erp.confirm("purchase_order", [order_line("90")], "buyer")
        for _ in range(3):
            drafts = [erp.ready("receipt", [linked(order, qty="1")], "warehouse") for _ in headers]
            barrier = Barrier(len(headers))

            def worker(index):
                doc = drafts[index]
                bearer = headers[index]
                barrier.wait(timeout=20)
                started = perf_counter()
                ok(
                    client.get(
                        "/api/v1/reports/inventory",
                        headers=bearer,
                        params={"limit": 50, "offset": index * 50},
                    )
                )
                read = perf_counter() - started
                started = perf_counter()
                ok(
                    client.post(
                        f"/api/v1/documents/{doc['id']}/actions/confirm",
                        headers=bearer,
                        json={
                            "operation_id": uuid4().hex,
                            "version": doc["version"],
                        },
                    )
                )
                return read, perf_counter() - started

            with ThreadPoolExecutor(max_workers=len(headers)) as workers:
                for read, write in workers.map(worker, range(len(headers))):
                    timings["inventory"].append(read)
                    timings["posting"].append(write)
        assert erp.get("/reports/inventory", item="P100")["rows"][0]["on_hand"] == "90.000000"
    for name, values in timings.items():
        p95 = quantiles(values, n=100, method="inclusive")[94]
        print(f"{name}: {len(values)} requests, P95={p95:.3f}s")
        assert p95 <= {"inventory": 1, "posting": 2}[name]


def main():
    url = os.environ["ERP_TEST_DATABASE_URL"]
    if not url.startswith("postgresql"):
        raise ValueError("V09 requires PostgreSQL")
    schema = "v09_" + uuid4().hex
    admin = make_engine(url)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = make_engine(make_url(url).update_query_dict({"options": f"-csearch_path={schema}"}))
    try:
        exercise(engine, bootstrap(engine))
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


if __name__ == "__main__":
    main()
