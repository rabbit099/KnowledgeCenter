"""Offset pagination for admin lists (lists are small: users, roles, mappings)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

PAGE_SIZE = 25


@dataclass(frozen=True)
class Page[T]:
    items: list[T]
    page: int
    total: int
    size: int = PAGE_SIZE

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.size))

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages


def paginate(db: Session, query: Select[Any], page: int, size: int = PAGE_SIZE) -> Page[Any]:
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    page = max(1, min(page, max(1, -(-total // size))))
    items = list(db.scalars(query.limit(size).offset((page - 1) * size)))
    return Page(items=items, page=page, total=total, size=size)
