"""Context Engine: builds the minimal Claude context for a request.

V1 is structured retrieval (no embeddings). The result is split into a *stable*
part (brand identity, rules: cacheable) and a *volatile* part (request, relevant products).
"""
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Project

MAX_PRODUCTS = 8


@dataclass
class ContextPackage:
    stable: str
    volatile: str


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "-"


def build_campaign_context(session: Session, project: Project, brief: str) -> ContextPackage:
    brand = project.brand_profile
    audience = project.audience_profile
    stable_parts = [f"<project>{project.name}</project>"]
    if brand:
        stable_parts.append(
            "<brand>\n"
            f"name: {brand.name}\ndescription: {brand.description}\nmission: {brand.mission}\n"
            f"tone: {brand.tone}\nlanguage: {brand.language}\ndialect: {brand.dialect}\n</brand>"
        )
    if audience:
        stable_parts.append(
            "<audience>\n"
            f"demographics: {audience.demographics}\n"
            f"interests:\n{_bullets(audience.interests)}\n"
            f"pain_points:\n{_bullets(audience.pain_points)}\n"
            f"goals:\n{_bullets(audience.goals)}\n</audience>"
        )
    rules = project.rules
    if rules:
        stable_parts.append(
            "<brand_rules>\n"
            + "\n".join(f"[{r.kind}] {r.text}" for r in rules)
            + "\n</brand_rules>"
        )

    products = _rank_products(project, brief)[:MAX_PRODUCTS]
    volatile = ""
    if products:
        volatile = "<products>\n" + "\n".join(
            f"- {p.name} | price: {p.price} | {p.description} | features: {', '.join(p.features)}"
            for p in products
        ) + "\n</products>"
    return ContextPackage(stable="\n\n".join(stable_parts), volatile=volatile)


def _rank_products(project: Project, brief: str):
    """Products mentioned in the brief first, then the rest."""
    text = brief.lower()
    return sorted(project.products, key=lambda p: 0 if p.name.lower() in text else 1)
