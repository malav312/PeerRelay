import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTENT_LIMIT = 512 * 1024


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Tool(Strict):
    kind: Literal["read", "list", "search", "create_file", "propose_replace"]
    root: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,40}$")
    path: str = Field(min_length=1, max_length=1024)
    content: str | None = Field(default=None, max_length=CONTENT_LIMIT)
    expected_version: str | None = Field(default=None, max_length=128)
    query: str | None = Field(default=None, min_length=1, max_length=200)
    cursor: int = Field(default=0, ge=0, le=100000)

    @model_validator(mode="after")
    def shape(self):
        write = self.kind in ("create_file", "propose_replace")
        if write != (self.content is not None and self.expected_version is not None):
            raise ValueError("Writes require content and expected version")
        if not write and (self.content is not None or self.expected_version is not None):
            raise ValueError("Unexpected write fields")
        if self.content is not None and len(self.content.encode()) > CONTENT_LIMIT:
            raise ValueError("Content too large")
        if self.kind == "create_file" and self.expected_version != "absent":
            raise ValueError("Create requires absent version")
        if (self.kind == "search") != (self.query is not None):
            raise ValueError("Search requires literal query exclusively")
        return self


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def payload_hash(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()
