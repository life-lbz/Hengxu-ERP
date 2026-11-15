from sqlalchemy import Column, Integer, MetaData, Table, select

from .db import make_engine
from .schema import Base

metadata = MetaData()
versions = Table("schema_version", metadata, Column("version", Integer, primary_key=True))


def upgrade(engine):
    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.exec_driver_sql("SELECT pg_advisory_xact_lock(739012780)")
        metadata.create_all(conn)
        applied = set(conn.scalars(select(versions.c.version)))
        if 1 not in applied:
            # Keep version 1 tables compatible with this baseline when adding migrations.
            Base.metadata.create_all(conn)
            conn.execute(versions.insert().values(version=1))


if __name__ == "__main__":
    upgrade(make_engine())
