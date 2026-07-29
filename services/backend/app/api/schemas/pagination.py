"""Simple bounded pagination contracts."""

from typing import Generic, Self, TypeVar

from pydantic import Field

from app.api.schemas.common import ApiSchema

T = TypeVar("T")


class PageParams(ApiSchema):
    """Validated query parameters shared by list endpoints."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)

    @property
    def offset(self) -> int:
        """Translate page numbering to a repository offset."""

        return (self.page - 1) * self.page_size


class PageMeta(ApiSchema):
    """Metadata needed by clients to navigate a result set."""

    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    total_pages: int = Field(ge=0)

    @classmethod
    def from_total(cls, *, page: int, page_size: int, total: int) -> Self:
        """Build consistent page metadata from a repository count."""

        total_pages = 0
        if page_size > 0 and total >= 0:
            total_pages = (total + page_size - 1) // page_size
        return cls(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
        )


class PaginatedResponse(ApiSchema, Generic[T]):
    """Generic list response with stable pagination metadata."""

    items: list[T]
    pagination: PageMeta
