from sqlalchemy.orm import Session

from app.config import settings
from app.designs.html_renderer import HtmlRenderer
from app.designs.provider import DesignProvider


def get_design_provider(session: Session | None = None) -> DesignProvider:
    if settings.design_provider == "canva":
        from app.designs.canva.service import canva_provider_for_owner

        return canva_provider_for_owner(session)
    return HtmlRenderer()
