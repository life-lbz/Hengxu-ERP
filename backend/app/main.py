import logging
import os
import re
from collections import deque
from datetime import date
from datetime import date as BusinessDate
from pathlib import Path
from threading import Lock
from time import monotonic

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from . import auth, documents, reports
from .db import make_engine, transaction
from .domain import audit, can_read, can_see, document, lock_company, require, uid
from .schema import Allocation, Audit, Company, Document, Master, Operation, Period, SessionToken, User

log = logging.getLogger("uvicorn.error")
bearer = HTTPBearer(auto_error=False)
PREFIX = "/api/v1"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Input):
    company: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    username: str = Field(pattern=r"^[A-Za-z0-9_-]{1,28}$")
    password: str = Field(min_length=1, max_length=200)


class Draft(Input):
    date: date
    note: str = Field(default="", max_length=240)
    lines: list[dict[str, str]] = Field(min_length=1, max_length=200)
    equity: str | None = Field(default=None, max_length=32)


class Edit(Draft):
    version: int = Field(gt=0)


class Action(Input):
    version: int = Field(gt=0)
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,64}$")
    reason: str = Field(default="", max_length=240)
    date: BusinessDate | None = None


class Record(Input):
    name: str = Field(min_length=1, max_length=100)
    unit: str = Field(default="", max_length=16)


class UserInput(Input):
    name: str = Field(min_length=1, max_length=80)
    role: str
    warehouses: list[str] = Field(max_length=100)
    password: str = Field(min_length=12, max_length=200)


def create_app(engine=None):
    engine = engine or make_engine()
    api = FastAPI(title="衡序 ERP", version="0.1.0")
    api.state.before_journal = lambda: None
    traffic = {}
    throttle_lock = Lock()

    def throttle(key, maximum):
        now = monotonic()
        with throttle_lock:
            if len(traffic) > 2048:
                expired = [name for name, times in traffic.items() if now - times[-1] >= 60]
                for name in expired:
                    del traffic[name]
                require(key in traffic or len(traffic) <= 2048, "RATE_LIMIT", "访问过于频繁", 429)
            times = traffic.setdefault(key, deque())
            while times and now - times[0] >= 60:
                times.popleft()
            require(len(times) < maximum, "RATE_LIMIT", "访问过于频繁，请稍后重试", 429)
            times.append(now)

    @api.middleware("http")
    async def trace(request, call_next):
        request.state.trace_id = uid()
        start = monotonic()
        response = await call_next(request)
        response.headers["X-Trace-ID"] = request.state.trace_id
        trace_path = request.url.path
        if trace_path.startswith(PREFIX):
            log.info(
                "trace=%s status=%s method=%s path=%s duration_ms=%.1f",
                request.state.trace_id,
                response.status_code,
                request.method,
                trace_path,
                (monotonic() - start) * 1000,
            )
        return response

    def error(request, status, code, message, details=None):
        payload = {
            "code": code,
            "message": message,
            "retryable": status in {429, 503},
            "trace_id": request.state.trace_id,
        }
        if details is not None:
            payload["fields"] = details
        return JSONResponse(status_code=status, content=payload)

    @api.exception_handler(HTTPException)
    def http_error(request, exc):
        detail = exc.detail if isinstance(exc.detail, dict) else {"message": exc.detail}
        user = getattr(request.state, "user", None)
        if exc.status_code == 403 and user is not None:
            with transaction(engine) as session:
                audit(
                    session,
                    user,
                    request.method,
                    request.url.path[:120],
                    detail.get("code", ""),
                    result="denied",
                )
        return error(request, exc.status_code, detail.get("code", "HTTP_ERROR"), detail["message"])

    @api.exception_handler(RequestValidationError)
    def validation_error(request, exc):
        fields = [{"location": list(entry["loc"]), "message": entry["msg"]} for entry in exc.errors()]
        return error(request, 422, "VALIDATION_ERROR", "请求参数校验失败", fields)

    @api.exception_handler(OperationalError)
    def database_error(request, exc):
        log.error("trace=%s database_error=%s", request.state.trace_id, type(exc.orig).__name__)
        if getattr(exc.orig, "pgcode", None) in {"55P03", "40P01", "40001"}:
            return error(request, 503, "WRITE_RETRY", "业务写入正在竞争，请使用原操作编号重试")
        return error(
            request, 503, "DATABASE_UNAVAILABLE", "数据库暂不可用，请查询操作状态或使用原操作编号重试"
        )

    @api.exception_handler(Exception)
    def unexpected_error(request, exc):
        log.error("trace=%s unhandled exception", request.state.trace_id, exc_info=exc)
        return error(request, 500, "INTERNAL_ERROR", "服务处理失败，请使用追踪编号定位错误")

    def current_user(request: Request, credentials: HTTPAuthorizationCredentials = Depends(bearer)):
        require(credentials is not None, "AUTH_REQUIRED", "请登录", 401)
        with transaction(engine) as session:
            user = auth.identify(session, credentials.credentials)
        request.state.user = user
        throttle(user.id, int(os.getenv("ERP_REQUESTS_PER_MINUTE", "240")))
        return user

    def admin(user):
        require(user.role == "admin", "ADMIN_REQUIRED", "仅系统管理员可以修改档案和岗位", 403)

    @api.get(PREFIX + "/health")
    def health():
        with engine.connect() as conn:
            version = conn.scalar(text("SELECT MAX(version) FROM schema_version"))
        return {"status": "ok", "schema_version": version}

    @api.post(PREFIX + "/auth/login")
    def login(payload: Login, request: Request):
        throttle("login/" + (request.client.host if request.client else "unknown"), 20)
        with transaction(engine) as session:
            token, user = auth.login(session, payload.company, payload.username, payload.password)
            audit(session, user, "login", user.id)
        return {"token": token, "user": auth.account(user)}

    @api.get(PREFIX + "/auth/me")
    def me(user=Depends(current_user)):
        return auth.account(user)

    @api.post(PREFIX + "/auth/logout")
    def logout(credentials: HTTPAuthorizationCredentials = Depends(bearer), user=Depends(current_user)):
        with transaction(engine) as session:
            record = session.get(SessionToken, auth.token_hash(credentials.credentials))
            if record is not None:
                session.delete(record)
            audit(session, user, "logout", user.id)
        return {"status": "signed_out"}

    @api.get(PREFIX + "/masters")
    def masters(user=Depends(current_user)):
        with transaction(engine) as session:
            company = session.get(Company, user.company)
            records = session.scalars(
                select(Master).where(Master.company == user.company).order_by(Master.kind, Master.code)
            )
            return {
                "company": company.name,
                "manufacturing": company.manufacturing,
                "records": [
                    {"kind": r.kind, "code": r.code, "name": r.name, "unit": r.unit}
                    for r in records
                    if r.kind != "warehouse" or can_see(user, [r.code])
                ],
                "document_kinds": [
                    kind
                    for kind in documents.FIELDS
                    if company.manufacturing or kind not in documents.manufacturing.HANDLERS
                ],
                "writable": list(
                    documents.FIELDS if user.role == "admin" else sorted(documents.ROLES.get(user.role, []))
                ),
            }

    @api.put(PREFIX + "/masters/{kind}/{code}")
    def set_master(kind: str, code: str, payload: Record, user=Depends(current_user)):
        admin(user)
        require(
            kind in {"warehouse", "item", "unit", "customer", "supplier", "cost_center"}
            and re.fullmatch(r"[A-Za-z0-9_-]{1,40}", code),
            "INVALID_MASTER",
            "档案类型或编码无效",
            422,
        )
        with transaction(engine) as session:
            lock_company(session, user.company)
            entry = session.get(Master, (user.company, kind, code))
            if entry is None:
                session.add(
                    Master(company=user.company, kind=kind, code=code, name=payload.name, unit=payload.unit)
                )
            else:
                require(entry.unit == payload.unit, "UNIT_IMMUTABLE", "现有档案的基础单位不可修改")
                entry.name = payload.name
            audit(session, user, "master.save", f"{kind}/{code}")
        return {"code": code, "kind": kind}

    @api.put(PREFIX + "/users/{username}")
    def set_user(username: str, payload: UserInput, user=Depends(current_user)):
        admin(user)
        require(
            re.fullmatch(r"[A-Za-z0-9_-]{1,28}", username)
            and payload.role in {"admin", "supervisor", *documents.ROLES},
            "INVALID_USER",
            "账号或岗位无效",
            422,
        )
        identifier = f"{user.company}/{username}"
        require(identifier != user.id, "SELF_CHANGE", "请由其他管理员维护此管理员账号")
        with transaction(engine) as session:
            lock_company(session, user.company)
            require(
                payload.warehouses == ["*"]
                or all(session.get(Master, (user.company, "warehouse", code)) for code in payload.warehouses),
                "UNKNOWN_WAREHOUSE",
                "仓库权限包含无效编码",
                422,
            )
            record = session.get(User, identifier)
            values = dict(
                name=payload.name,
                role=payload.role,
                warehouses=payload.warehouses,
                password=auth.password_hash(payload.password),
            )
            if record is None:
                session.add(User(id=identifier, company=user.company, **values))
            else:
                for field, value in values.items():
                    setattr(record, field, value)
                for token in session.scalars(select(SessionToken).where(SessionToken.user_id == identifier)):
                    session.delete(token)
            audit(session, user, "user.save", identifier)
        return {"username": username}

    @api.get(PREFIX + "/periods")
    def periods(user=Depends(current_user)):
        with transaction(engine) as session:
            rows = session.scalars(select(Period).where(Period.company == user.company).order_by(Period.code))
            return [{"code": row.code, "closed": row.closed} for row in rows]

    @api.post(PREFIX + "/periods/{code}/{command}")
    def change_period(code: str, command: str, user=Depends(current_user)):
        require(
            user.role in {"admin", "finance"} and user.warehouses == ["*"],
            "ROLE_DENIED",
            "仅公司级财务可维护期间",
            403,
        )
        require(
            re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", code) and command in {"open", "close"},
            "INVALID_PERIOD",
            "期间或动作无效",
            422,
        )
        with transaction(engine) as session:
            lock_company(session, user.company)
            period = session.get(Period, (user.company, code))
            if command == "open":
                require(period is None, "PERIOD_EXISTS", "样板规则禁止重开已有期间")
                session.add(Period(company=user.company, code=code))
            else:
                require(period is not None and not period.closed, "PERIOD_NOT_OPEN", "期间未打开")
                prior_open = session.scalar(
                    select(Period.code)
                    .where(Period.company == user.company, Period.code < code, Period.closed.is_(False))
                    .limit(1)
                )
                require(prior_open is None, "PRIOR_PERIOD_OPEN", "请先关闭更早的期间")
                period.closed = True
            audit(session, user, f"period.{command}", code)
        return {"code": code, "closed": command == "close"}

    @api.get(PREFIX + "/documents")
    def listing(
        kind: str | None = None,
        status: str | None = None,
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
        user=Depends(current_user),
    ):
        with transaction(engine) as session:
            query = select(Document).where(Document.company == user.company)
            if kind:
                query = query.where(Document.kind == kind)
            if status:
                query = query.where(Document.status == status)
            rows = session.scalars(
                query.order_by(Document.date.desc(), Document.created.desc(), Document.id.desc())
            ).yield_per(200)
            from itertools import islice

            return list(
                islice(
                    (documents.view(row) for row in rows if can_see(user, row.scope) and can_read(user, row)),
                    offset,
                    offset + limit,
                )
            )

    @api.get(PREFIX + "/documents/{identifier}")
    def detail(identifier: str, user=Depends(current_user)):
        with transaction(engine) as session:
            doc = document(session, user, identifier)
            rows = session.execute(
                select(Allocation, Document)
                .join(Document, Allocation.target == Document.id)
                .where(Allocation.company == user.company, Allocation.source == doc.id)
            )
            links = [
                {
                    "document": a.target,
                    "line": a.target_line,
                    "source_line": a.source_line,
                    "kind": a.kind,
                    "qty": str(a.qty),
                    "amount": str(a.amount),
                    "status": target.status,
                }
                for a, target in rows
                if can_see(user, target.scope) and can_read(user, target)
            ]
            return {**documents.view(doc), "allocations": links, "progress": documents.progress(doc, links)}

    @api.get(PREFIX + "/source-lines")
    def source_lines(kinds: str = Query(max_length=250), user=Depends(current_user)):
        from itertools import islice

        with transaction(engine) as session:
            rows = session.scalars(
                select(Document)
                .where(
                    Document.company == user.company,
                    Document.kind.in_(kinds.split(",")),
                    Document.status.in_(["confirmed", "posted"]),
                )
                .order_by(Document.date.desc(), Document.created.desc(), Document.id.desc())
            ).yield_per(200)
            return list(
                islice(
                    (documents.view(row) for row in rows if can_see(user, row.scope) and can_read(user, row)),
                    100,
                )
            )

    @api.post(PREFIX + "/documents/{kind}", status_code=201)
    def create(kind: str, payload: Draft, user=Depends(current_user)):
        with transaction(engine) as session:
            lock_company(session, user.company)
            return documents.draft(session, user, kind, payload.model_dump(mode="json"))

    @api.patch(PREFIX + "/documents/{identifier}")
    def edit(identifier: str, payload: Edit, user=Depends(current_user)):
        with transaction(engine) as session:
            lock_company(session, user.company)
            return documents.edit(session, user, identifier, payload.model_dump(mode="json"))

    @api.post(PREFIX + "/documents/{identifier}/actions/{command}")
    def command(identifier: str, command: str, payload: Action, user=Depends(current_user)):
        with transaction(engine) as session:
            return documents.action(
                session, user, identifier, command, payload.model_dump(mode="json"), api.state.before_journal
            )

    @api.get(PREFIX + "/operations/{identifier}")
    def operation(identifier: str, user=Depends(current_user)):
        with transaction(engine) as session:
            row = session.get(Operation, (user.company, identifier))
            require(row is not None, "OPERATION_NOT_FOUND", "该操作尚无提交记录", 404)
            require(row.actor == user.id, "OPERATION_DENIED", "无权读取该操作", 403)
            document(session, user, row.result["document"]["id"])
            return row.result

    @api.get(PREFIX + "/reports/{report}")
    def report(
        report: str,
        cutoff: date | None = None,
        warehouse: str | None = None,
        item: str | None = None,
        offset: int = Query(0, ge=0),
        limit: int = Query(50, ge=1, le=200),
        user=Depends(current_user),
    ):
        with transaction(engine) as session:
            timestamp = reports.snapshot(session)
            day = cutoff or date.max
            if report == "inventory":
                data = reports.inventory(session, user, cutoff, warehouse, offset, limit, item)
            elif report == "movements":
                data = reports.movement_details(session, user, day, warehouse, offset, limit, item)
            else:
                data = reports.finance_report(
                    session, user, report, cutoff or date.today(), False, offset, limit
                )
            return {"cutoff": cutoff.isoformat() if cutoff else None, "snapshot": timestamp, "rows": data}

    @api.get(PREFIX + "/audit")
    def audit_log(user=Depends(current_user)):
        admin(user)
        with transaction(engine) as session:
            return [
                {
                    "actor": a.actor,
                    "action": a.action,
                    "target": a.target,
                    "version": a.version,
                    "result": a.result,
                    "reason": a.reason,
                    "timestamp": a.timestamp.isoformat(),
                }
                for a in session.scalars(
                    select(Audit).where(Audit.company == user.company).order_by(Audit.id.desc()).limit(200)
                )
            ]

    frontend = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if frontend.is_dir():
        api.mount("/", StaticFiles(directory=frontend, html=True), name="workbench")
    return api


app = create_app()
