from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import create_engine
from sqlmodel import SQLModel

from donor_bot.config import settings

# Create an async engine
engine = create_async_engine(f"sqlite+aiosqlite:///{settings.DB_PATH}")

# Create a sync engine for Alembic migrations
sync_engine = create_engine(f"sqlite:///{settings.DB_PATH}")

# Create a session factory
SessionLocal = async_sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)

async def init_db():
    """Initializes the database and creates tables if they don't exist."""
    # --- 1) run Alembic migrations (online) ---
    try:
        from alembic import command  # type: ignore
        from alembic.config import Config as AlembicConfig  # type: ignore
        import importlib.resources as pkg_resources
        import os, sys, pathlib

        # Locate alembic.ini next to project root
        root_path = pathlib.Path(__file__).resolve().parent.parent
        alembic_ini = root_path / "alembic.ini"
        if alembic_ini.exists():
            cfg = AlembicConfig(str(alembic_ini))
            cfg.set_main_option("script_location", str(root_path / "alembic"))
            # Alembic работает синхронно, поэтому используем обычный драйвер SQLite,
            # иначе появляется ошибка "greenlet_spawn has not been called".
            cfg.set_main_option("sqlalchemy.url", f"sqlite:///{settings.DB_PATH}")
            # run upgrade to head (synchronously)
            import asyncio as _aio
            loop = _aio.get_event_loop()
            await loop.run_in_executor(None, command.upgrade, cfg, "head")
    except ModuleNotFoundError:
        # alembic not installed – fallback to simple create_all
        pass
    except Exception as e:
        import logging; logging.error(f"alembic_migration_error: {e}")

    # Ensure all models are imported so SQLModel metadata includes them
    from donor_bot.models import admin  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)

        # --- lightweight migration: add pd_agreed bool column if missing ---
        from sqlalchemy import text
        res = await conn.execute(text("PRAGMA table_info(donor);"))
        columns = [row[1] for row in res.fetchall()]
        if "pd_agreed" not in columns:
            await conn.execute(text("ALTER TABLE donor ADD COLUMN pd_agreed BOOLEAN DEFAULT 0;"))
            # Коммит не нужен внутри `engine.begin()` – транзакция применится при выходе

        # --- 2025-07: add new donor statistics columns ---
        missing_alters: list[str] = []
        if "gavrilova_count" not in columns:
            missing_alters.append("ALTER TABLE donor ADD COLUMN gavrilova_count INTEGER DEFAULT 0;")
        if "fmba_count" not in columns:
            missing_alters.append("ALTER TABLE donor ADD COLUMN fmba_count INTEGER DEFAULT 0;")
        if "total_sum" not in columns:
            missing_alters.append("ALTER TABLE donor ADD COLUMN total_sum INTEGER DEFAULT 0;")
        if "last_gavrilova" not in columns:
            missing_alters.append("ALTER TABLE donor ADD COLUMN last_gavrilova DATE;")
        if "last_fmba" not in columns:
            missing_alters.append("ALTER TABLE donor ADD COLUMN last_fmba DATE;")
        if "social" not in columns:
            missing_alters.append("ALTER TABLE donor ADD COLUMN social TEXT;")
        # --- 2025-07-19: геймификация ---
        if "points" not in columns:
            missing_alters.append("ALTER TABLE donor ADD COLUMN points INTEGER DEFAULT 0;")
        if "streak" not in columns:
            missing_alters.append("ALTER TABLE donor ADD COLUMN streak INTEGER DEFAULT 0;")
        if "last_donation" not in columns:
            missing_alters.append("ALTER TABLE donor ADD COLUMN last_donation DATE;")
        # 2025-07-19: UI language
        if "lang" not in columns:
            missing_alters.append("ALTER TABLE donor ADD COLUMN lang TEXT DEFAULT 'ru';")

        for stmt in missing_alters:
            await conn.execute(text(stmt))
        # commit произойдет автоматически при выходе из контекста

        res_evt = await conn.execute(text("PRAGMA table_info(event);"))
        evt_columns = [row[1] for row in res_evt.fetchall()]
        if "external_link" not in evt_columns:
            await conn.execute(text("ALTER TABLE event ADD COLUMN external_link TEXT;"))
        if "start_time" not in evt_columns:
            await conn.execute(text("ALTER TABLE event ADD COLUMN start_time TEXT;"))
        if "end_time" not in evt_columns:
            await conn.execute(text("ALTER TABLE event ADD COLUMN end_time TEXT;"))
            # commit произойдет автоматически при выходе из контекста

        res_reg = await conn.execute(text("PRAGMA table_info(registration);"))
        reg_cols = [row[1] for row in res_reg.fetchall()]
        if "no_show_reason" not in reg_cols:
            await conn.execute(text("ALTER TABLE registration ADD COLUMN no_show_reason TEXT;"))

    # ---------- SAFEGUARD: ensure 'lang' column exists even if previous block failed ----------
    from sqlalchemy import text as _t
    async with engine.begin() as conn:
        res = await conn.execute(_t("PRAGMA table_info(donor);"))
        col_names = [row[1] for row in res.fetchall()]
        if "lang" not in col_names:
            await conn.execute(_t("ALTER TABLE donor ADD COLUMN lang TEXT DEFAULT 'ru';"))
