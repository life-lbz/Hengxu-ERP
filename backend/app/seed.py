import argparse
import os

from .auth import password_hash
from .db import make_engine, transaction
from .domain import require
from .schema import Company, Master, Period, User

DEMO_USERS = {
    "admin": ("系统管理员", "admin", ["*"]),
    "manager": ("业务主管", "supervisor", ["*"]),
    "buyer": ("采购员", "buyer", ["*"]),
    "seller": ("销售员", "seller", ["*"]),
    "warehouse": ("仓管员", "warehouse", ["MAIN", "AUX"]),
    "limited": ("主仓仓管", "warehouse", ["MAIN"]),
    "finance": ("会计", "finance", ["*"]),
    "cashier": ("出纳", "cashier", ["*"]),
    "planner": ("生产计划员", "production", ["*"]),
}
DEMO_MASTERS = [
    ("warehouse", "MAIN", "中心仓", ""),
    ("warehouse", "AUX", "周转仓", ""),
    ("item", "P100", "外购商品", "件"),
    ("item", "M100", "装配材料", "件"),
    ("item", "FG100", "装配成品", "件"),
    ("unit", "EA", "件", ""),
    ("supplier", "SUP01", "样板供应商", ""),
    ("customer", "CUS01", "样板客户", ""),
    ("cost_center", "CC01", "装配中心", ""),
]


def seed(session, password, company="DEMO", period="2026-11", manufacturing=True):
    require(session.get(Company, company) is None, "COMPANY_EXISTS", "该账套已初始化")
    session.add(Company(id=company, name="衡序样板公司", manufacturing=manufacturing))
    session.flush()
    session.add(Period(company=company, code=period))
    for code, (name, role, warehouses) in DEMO_USERS.items():
        session.add(
            User(
                id=f"{company}/{code}",
                name=name,
                company=company,
                role=role,
                warehouses=warehouses,
                password=password_hash(password),
            )
        )
    for kind, code, name, unit in DEMO_MASTERS:
        session.add(Master(company=company, kind=kind, code=code, name=name, unit=unit))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="初始化合成样板账套")
    parser.add_argument("--company", default="DEMO")
    parser.add_argument("--period", default="2026-11")
    parser.add_argument("--no-manufacturing", action="store_true")
    args = parser.parse_args()
    from datetime import date

    date.fromisoformat(args.period + "-01")
    password = os.getenv("ERP_DEMO_PASSWORD", "")
    if len(password) < 12:
        parser.error("请通过 ERP_DEMO_PASSWORD 提供 12 位以上的样板密码")
    with transaction(make_engine()) as session:
        seed(session, password, args.company, args.period, not args.no_manufacturing)
