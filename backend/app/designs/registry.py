"""Internal design schema + template registry. Providers translate this to their own fields."""
from dataclasses import dataclass


@dataclass(frozen=True)
class FieldSpec:
    name: str  # internal key (lowercase)
    required: bool = True
    max_len: int = 200
    kind: str = "text"  # text | image


@dataclass(frozen=True)
class TemplateSpec:
    key: str
    width: int
    height: int
    fields: tuple[FieldSpec, ...]
    canva_template_env: str = ""  # filled via DesignTemplate mapping in DB later


REGISTRY: dict[str, TemplateSpec] = {
    "offer_square": TemplateSpec("offer_square", 1080, 1080, (
        FieldSpec("headline", max_len=60), FieldSpec("price", required=False, max_len=30),
        FieldSpec("description", required=False, max_len=140), FieldSpec("cta", max_len=40),
    )),
    "quote_square": TemplateSpec("quote_square", 1080, 1080, (
        FieldSpec("headline", max_len=60), FieldSpec("description", required=False, max_len=140),
        FieldSpec("cta", required=False, max_len=40),
    )),
    "announcement_story": TemplateSpec("announcement_story", 1080, 1920, (
        FieldSpec("headline", max_len=60), FieldSpec("description", required=False, max_len=140),
        FieldSpec("cta", max_len=40),
    )),
}


class DesignSpecError(ValueError):
    pass


def build_values(template_key: str, headline: str, cta: str, fields: dict[str, str]) -> dict[str, str]:
    """Normalise Claude's design.fields (any key case) + item headline/cta into validated values."""
    spec = REGISTRY.get(template_key)
    if spec is None:
        raise DesignSpecError(f"unknown template {template_key}")
    raw = {k.lower(): str(v) for k, v in fields.items()}
    raw.setdefault("headline", headline)
    raw.setdefault("cta", cta)
    values: dict[str, str] = {}
    for f in spec.fields:
        v = raw.get(f.name, "").strip()
        if not v:
            if f.required:
                raise DesignSpecError(f"missing required field {f.name}")
            continue
        values[f.name] = v[: f.max_len]  # trim rather than fail; design must fit
    return values
