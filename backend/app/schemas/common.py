"""Shared API schemas — pagination envelope pieces."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, Field
from pydantic.generics import GenericModel

T = TypeVar("T")


class Page(GenericModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class PaginationQuery(BaseModel):
    """Validated pagination params; page_size is capped server-side (§43)."""

    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=25, ge=1, le=100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size
