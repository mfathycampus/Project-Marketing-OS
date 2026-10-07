from dataclasses import dataclass
from typing import Protocol


@dataclass
class DesignRequest:
    template_key: str
    values: dict[str, str]
    primary_color: str = "#0F766E"
    secondary_color: str = "#F59E0B"
    brand_name: str = ""
    rtl: bool = True
    idempotency_key: str = ""


@dataclass
class DesignResult:
    png: bytes
    width: int
    height: int
    external_job_id: str | None = None
    external_url: str | None = None


class DesignProvider(Protocol):
    name: str

    def render(self, req: DesignRequest) -> DesignResult: ...
