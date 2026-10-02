"""Uniform API envelope helpers: success responses and pagination."""

from __future__ import annotations

from collections.abc import Sequence
from math import ceil
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageMeta(BaseModel):
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)
    total: int = Field(ge=0)
    total_pages: int = Field(ge=0)
    has_next: bool
    has_prev: bool


class Page(BaseModel, Generic[T]):
    items: list[T]
    meta: PageMeta


def build_page(
    items: Sequence[T],
    *,
    page: int,
    page_size: int,
    total: int,
) -> Page[T]:
    total_pages = ceil(total / page_size) if page_size else 0
    return Page[T](
        items=list(items),
        meta=PageMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1,
        ),
    )


def ok(data: Any = None, *, message: str | None = None) -> dict[str, Any]:
    """Wrap arbitrary payload in the standard success envelope."""
    body: dict[str, Any] = {"success": True, "data": data}
    if message:
        body["message"] = message
    return body


class PaginationParams(BaseModel):
    page: int = Field(default=1, ge=1, le=10_000)
    page_size: int = Field(default=20, ge=1, le=200)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size
