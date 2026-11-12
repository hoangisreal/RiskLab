"""Business DTOs: strict mutation inputs and explicit safe response fields."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    model_validator,
)

Role = Literal["ANALYST", "MANAGER", "ADMIN"]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Username = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_.-]{3,64}$")]
Password = Annotated[SecretStr, Field(min_length=12, max_length=128)]


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Envelope[T](BaseModel):
    data: T


class Page[T](BaseModel):
    data: list[T]
    pagination: dict[str, int]


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    username: str
    role: Role
    is_active: bool
    revision: int
    created_at: datetime


class LoginInput(StrictInput):
    username: Username
    password: Password


class CreateUserInput(LoginInput):
    role: Role


class UserPatch(StrictInput):
    expected_revision: int = Field(ge=1)
    reason: Reason
    role: Role | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def mutation_present(self) -> UserPatch:
        if any(
            getattr(self, field) is None for field in {"role", "is_active"} & self.model_fields_set
        ):
            raise ValueError("Role and account status cannot be null when supplied")
        if self.role is None and self.is_active is None:
            raise ValueError("Provide a role or account status")
        return self


class ResetPasswordInput(StrictInput):
    expected_revision: int = Field(ge=1)
    reason: Reason
    new_password: Password


class PaginationQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page: int = Field(default=1, ge=1, le=10000)
    page_size: int = Field(default=20, ge=1, le=100)


class UserQuery(PaginationQuery):
    q: str | None = Field(default=None, min_length=1, max_length=64)
    role: Role | None = None
    is_active: bool | None = None


class AuditQuery(PaginationQuery):
    user_id: UUID | None = None


Notes = Annotated[str, StringConstraints(max_length=10_000)]


def _coerce_uuid(value: object) -> UUID:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        return UUID(value)
    raise ValueError("expected a UUID")


# JSON transports UUIDs as strings; strict bodies still reject malformed values.
StrictUUID = Annotated[UUID, BeforeValidator(_coerce_uuid)]


class RetryImportInput(StrictInput):
    expected_revision: int = Field(ge=1)
    reason: Reason


class AssignmentInput(StrictInput):
    expected_revision: int = Field(ge=1)
    assignee_id: StrictUUID
    reason: Reason


class ReviewStartInput(StrictInput):
    expected_revision: int = Field(ge=1)


class ReviewPatchInput(StrictInput):
    expected_revision: int = Field(ge=1)
    notes: Notes
    proposal: Literal["APPROVE", "DECLINE"] | None = None


class ReturnInput(StrictInput):
    expected_revision: int = Field(ge=1)
    review_id: StrictUUID
    reason: Reason


class DecisionInput(StrictInput):
    expected_revision: int = Field(ge=1)
    review_id: StrictUUID
    outcome: Literal["APPROVED_SIMULATED", "DECLINED_SIMULATED"]
    reason: Reason


class ApplicationQuery(PaginationQuery):
    q: str | None = Field(default=None, min_length=1, max_length=64)
    review_state: (
        Literal["UNASSIGNED", "ASSIGNED", "IN_REVIEW", "PENDING_DECISION", "DECIDED"] | None
    ) = None
    scoring_state: Literal["NOT_REQUESTED", "QUEUED", "RUNNING", "SUCCEEDED", "FAILED"] | None = (
        None
    )
    assignee_id: UUID | None = None


class ImportQuery(PaginationQuery):
    state: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"] | None = None
