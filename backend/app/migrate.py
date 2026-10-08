"""Run Alembic migrations from code (used for the local sqlite quick start)."""
from alembic import command
from alembic.config import Config

from app.config import _BACKEND, settings


def upgrade_head() -> None:
    cfg = Config()
    cfg.set_main_option("script_location", str(_BACKEND / "migrations"))
    command.upgrade(cfg, "head")


def auto_migrate_if_sqlite() -> bool:
    if settings.auto_migrate and settings.database_url.startswith("sqlite"):
        upgrade_head()
        return True
    return False
