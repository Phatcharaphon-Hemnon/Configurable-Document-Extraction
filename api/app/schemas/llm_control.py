"""Per-generation request accounting shared by all output modes."""

from pydantic import BaseModel


class RequestBudget(BaseModel):
    attempts: int = 0
    timeout_retries: int = 0
