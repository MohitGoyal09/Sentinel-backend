from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Any, Optional, Generic, TypeVar

T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):
    success: bool = True
    data: Optional[T] = None
    error: Optional[dict] = None
    meta: Optional[dict] = None


class ErrorDetail(BaseModel):
    code: str
    message: str


def success_response(data: Any, meta: dict = None) -> dict:
    return {"success": True, "data": data, "meta": meta}


def error_response(code: str, message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "error": {"code": code, "message": message}},
    )
