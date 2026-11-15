import os
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session


def make_engine(url=None):
    url = url or os.getenv("DATABASE_URL")
    if not url and os.getenv("POSTGRES_HOST"):
        url = URL.create(
            "postgresql+psycopg2",
            username=os.environ["POSTGRES_USER"],
            password=os.environ["POSTGRES_PASSWORD"],
            host=os.environ["POSTGRES_HOST"],
            database=os.environ["POSTGRES_DB"],
        )
    url = url or "sqlite:///hengxu.sqlite3"
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    if make_url(url).get_backend_name() == "sqlite":

        @event.listens_for(engine, "connect")
        def configure(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=10000")

    return engine


@contextmanager
def transaction(engine):
    with Session(engine, expire_on_commit=False) as session, session.begin():
        yield session
