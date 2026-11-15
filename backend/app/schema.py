from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

QTY = Numeric(20, 6)
MONEY = Numeric(20, 2)


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "company"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    manufacturing: Mapped[bool] = mapped_column(Boolean, default=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class User(Base):
    __tablename__ = "erp_user"
    id: Mapped[str] = mapped_column(String(70), primary_key=True)
    company: Mapped[str] = mapped_column(ForeignKey("company.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    password: Mapped[str] = mapped_column(String(192))
    role: Mapped[str] = mapped_column(String(20))
    warehouses = mapped_column(JSON, nullable=False)


class SessionToken(Base):
    __tablename__ = "session_token"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("erp_user.id"), index=True)
    expires = mapped_column(DateTime(timezone=True), nullable=False)


class Master(Base):
    __tablename__ = "master"
    company: Mapped[str] = mapped_column(ForeignKey("company.id"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)
    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    unit: Mapped[str] = mapped_column(String(16), default="")


class Period(Base):
    __tablename__ = "period"
    company: Mapped[str] = mapped_column(ForeignKey("company.id"), primary_key=True)
    code: Mapped[str] = mapped_column(String(7), primary_key=True)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)


class Document(Base):
    __tablename__ = "document"
    __table_args__ = (
        UniqueConstraint("company", "id"),
        ForeignKeyConstraint(["company", "reverse_of"], ["document.company", "document.id"]),
        Index("ix_document_company_kind_status_date", "company", "kind", "status", "date"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company: Mapped[str] = mapped_column(ForeignKey("company.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    date = mapped_column(Date, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(22), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    creator: Mapped[str] = mapped_column(ForeignKey("erp_user.id"))
    created = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    data = mapped_column(JSON, nullable=False)
    scope = mapped_column(JSON, nullable=False)
    reverse_of = mapped_column(String(36), unique=True)


class Allocation(Base):
    __tablename__ = "allocation"
    __table_args__ = (
        ForeignKeyConstraint(["company", "source"], ["document.company", "document.id"]),
        ForeignKeyConstraint(["company", "target"], ["document.company", "document.id"]),
        Index("ix_allocation_source_line_kind", "company", "source", "source_line", "kind"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(36), index=True)
    source_line: Mapped[str] = mapped_column(String(36))
    target: Mapped[str] = mapped_column(String(36), index=True)
    target_line: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(24))
    qty = mapped_column(QTY, nullable=False)
    amount = mapped_column(MONEY, nullable=False)


class CostPool(Base):
    __tablename__ = "cost_pool"
    company: Mapped[str] = mapped_column(ForeignKey("company.id"), primary_key=True)
    item: Mapped[str] = mapped_column(String(40), primary_key=True)
    qty = mapped_column(QTY, nullable=False)
    value = mapped_column(MONEY, nullable=False)


class Stock(Base):
    __tablename__ = "stock"
    __table_args__ = (CheckConstraint("qty >= reserved AND reserved >= 0"),)
    company: Mapped[str] = mapped_column(ForeignKey("company.id"), primary_key=True)
    warehouse: Mapped[str] = mapped_column(String(40), primary_key=True)
    item: Mapped[str] = mapped_column(String(40), primary_key=True)
    qty = mapped_column(QTY, nullable=False)
    reserved = mapped_column(QTY, nullable=False)


class Movement(Base):
    __tablename__ = "movement"
    __table_args__ = (ForeignKeyConstraint(["company", "document"], ["document.company", "document.id"]),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String(40), index=True)
    warehouse: Mapped[str] = mapped_column(String(40))
    item: Mapped[str] = mapped_column(String(40))
    document: Mapped[str] = mapped_column(String(36), index=True)
    line: Mapped[str] = mapped_column(String(36))
    date = mapped_column(Date, nullable=False, index=True)
    qty = mapped_column(QTY, nullable=False)
    value = mapped_column(MONEY, nullable=False)
    reservation = mapped_column(QTY, nullable=False)


class Journal(Base):
    __tablename__ = "journal"
    __table_args__ = (ForeignKeyConstraint(["company", "document"], ["document.company", "document.id"]),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String(40), index=True)
    document: Mapped[str] = mapped_column(String(36), unique=True)
    date = mapped_column(Date, nullable=False, index=True)
    rule: Mapped[str] = mapped_column(String(24), default="sample-v1")
    entries = mapped_column(JSON, nullable=False)


class OpenItem(Base):
    __tablename__ = "open_item"
    __table_args__ = (ForeignKeyConstraint(["company", "document"], ["document.company", "document.id"]),)
    document: Mapped[str] = mapped_column(String(36), primary_key=True)
    line: Mapped[str] = mapped_column(String(36), primary_key=True)
    company: Mapped[str] = mapped_column(String(40), index=True)
    partner: Mapped[str] = mapped_column(String(40))
    side: Mapped[str] = mapped_column(String(2))
    amount = mapped_column(MONEY, nullable=False)


class Operation(Base):
    __tablename__ = "operation"
    company: Mapped[str] = mapped_column(ForeignKey("company.id"), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    actor: Mapped[str] = mapped_column(ForeignKey("erp_user.id"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    result = mapped_column(JSON, nullable=False)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    company: Mapped[str] = mapped_column(String(40), index=True)
    actor: Mapped[str] = mapped_column(String(70))
    action: Mapped[str] = mapped_column(String(40))
    target: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str] = mapped_column(String(240), default="")
    version = mapped_column(Integer, nullable=True)
    snapshot = mapped_column(JSON, nullable=True)
    result: Mapped[str] = mapped_column(String(16), default="success")
    timestamp = mapped_column(DateTime(timezone=True), server_default=func.now())


class Event(Base):
    __tablename__ = "outbox_event"
    __table_args__ = (ForeignKeyConstraint(["company", "document"], ["document.company", "document.id"]),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    company: Mapped[str] = mapped_column(String(40), index=True)
    document: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload = mapped_column(JSON, nullable=False)
    created = mapped_column(DateTime(timezone=True), server_default=func.now())
