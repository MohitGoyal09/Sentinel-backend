from pydantic import BaseModel
from typing import Any, Optional, Generic, TypeVar

T = TypeVar("T")


class PaginationParams(BaseModel):
    limit: int = 50
    offset: int = 0


class SortParams(BaseModel):
    sort_by: str = "created_at"
    sort_order: str = "desc"  # asc or desc


class FilterParams(BaseModel):
    search: Optional[str] = None
    status: Optional[str] = None
    role: Optional[str] = None
