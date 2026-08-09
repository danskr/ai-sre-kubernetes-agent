import logging
import time
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings
from app.demo_faults import DB_LEAK_FAULT

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout_seconds,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def wait_for_database() -> None:
    for attempt in range(1, settings.database_startup_retries + 1):
        try:
            with engine.connect() as connection:
                connection.execute(text('SELECT 1'))
            logger.info('database_connection_ready attempt=%s', attempt)
            return
        except SQLAlchemyError:
            if attempt == settings.database_startup_retries:
                logger.exception('database_connection_failed attempts=%s', settings.database_startup_retries)
                raise
            logger.warning(
                'database_connection_retry attempt=%s max_attempts=%s',
                attempt,
                settings.database_startup_retries,
            )
            time.sleep(settings.database_startup_retry_seconds)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        if settings.demo_faults_enabled and DB_LEAK_FAULT.should_leak():
            DB_LEAK_FAULT.retain_session(db)
        else:
            db.close()
