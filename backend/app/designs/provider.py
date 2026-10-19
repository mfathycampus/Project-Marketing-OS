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
    logo: str = ""  # path of the brand logo (used by videos)
    video_style: str = "modern"  # brand's ready-made video look
    website: str = ""  # brand link for the video end card
    offer: str = ""


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
