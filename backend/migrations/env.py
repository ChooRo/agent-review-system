from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app.core.config import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def _alembic_url() -> str:
    url = get_settings().database_url
    if not url:
        raise RuntimeError("迁移需要配置 DATABASE_URL")
    # 迁移使用 SQLAlchemy；psycopg3 需显式驱动名
    scheme = url.split("://", 1)[0]
    return url if "+" in scheme else url.replace("postgresql://", "postgresql+psycopg://", 1)


def run_migrations_offline() -> None:
    context.configure(url=_alembic_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_alembic_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
