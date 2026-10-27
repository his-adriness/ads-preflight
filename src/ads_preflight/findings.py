"""What a check reports: one finding per problem."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

Severity = Literal["error", "warning", "info"]

# Lower is more serious. Used to list errors first.
SEVERITY_RANK: dict[Severity, int] = {"error": 0, "warning": 1, "info": 2}


class Finding(BaseModel):
    """One problem found in a server or its tools."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rule: str  # Stable ID of the rule, such as "core.duplicate-name".
    severity: Severity
    tool: str | None = None  # The tool it is about, or None if it is about the whole server.
    message: str  # What is wrong, written for the person reading the report.
    fix: str | None = None  # What to do about it.
