from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from conference_scout_mcp.config import Settings
from conference_scout_mcp.storage import metadata

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = metadata


def run_migrations_offline() -> None:
    context.configure(
        url=Settings().storage_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(Settings().storage_url, poolclass=pool.NullPool)
    with connectable.connect() as connection:
        if connection.dialect.name == "postgresql":
            schema = Settings().validated_database_schema
            connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
            connection.exec_driver_sql(f'SET search_path TO "{schema}", public')
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
