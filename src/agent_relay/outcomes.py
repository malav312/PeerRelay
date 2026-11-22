"""Versioned completion facts, separate from the explicitly requested narrative report."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

Limitation = Annotated[str, Field(min_length=1, max_length=300)]


class Completion(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: Literal[1]
    status: Literal["completed", "partial", "blocked"]
    verification: Literal["passed", "failed", "not_run", "not_applicable"]
    limitations: Annotated[list[Limitation], Field(max_length=4)]

    @field_validator("version", mode="before")
    @classmethod
    def integer_version(cls, value):
        # Literal[1] alone also accepts True and 1.0 through Python equality.
        if type(value) is not int:
            raise ValueError("Completion version must be an integer")
        return value

    @field_validator("limitations")
    @classmethod
    def meaningful_limitations(cls, values):
        if any(not value.strip() for value in values):
            raise ValueError("Limitations must contain text")
        return values


def completion_view(value):
    """Unknown/malformed persisted or upstream data must retain the legacy summary."""
    try:
        return Completion.model_validate(value).model_dump()
    except ValidationError:
        return None
