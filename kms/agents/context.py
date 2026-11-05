"""Per-request identity, kept OUT of graph state so tokens are never checkpointed to the database."""
from contextvars import ContextVar

from kms.auth import User

current_user: ContextVar[User] = ContextVar("current_user")
current_request_id: ContextVar[str] = ContextVar("current_request_id", default="")
