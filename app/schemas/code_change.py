from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class FileOperation(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class PlannedFileChange(BaseModel):
    path: str = Field(min_length=1, max_length=300)
    operation: FileOperation
    content: str | None = None
    explanation: str = Field(min_length=5, max_length=1000)

    @field_validator("path")
    @classmethod
    def clean_path(cls, value: str) -> str:
        cleaned = value.strip().replace("\\", "/")

        if not cleaned:
            raise ValueError("File path cannot be empty.")

        if cleaned.startswith("/"):
            raise ValueError("Absolute paths are not allowed.")

        if ".." in cleaned.split("/"):
            raise ValueError("Parent-directory traversal is not allowed.")

        return cleaned

    @field_validator("explanation", mode="before")
    @classmethod
    def normalize_explanation(cls, value: object) -> str:
        if not isinstance(value, str):
            raise ValueError("Explanation must be text.")

        cleaned = value.strip()

        if len(cleaned) > 1000:
            cleaned = cleaned[:997].rstrip() + "..."

        return cleaned

    @field_validator("content")
    @classmethod
    def validate_content(
        cls,
        value: str | None,
        info,
    ) -> str | None:
        operation = info.data.get("operation")

        if operation in {
            FileOperation.CREATE,
            FileOperation.UPDATE,
        } and value is None:
            raise ValueError(
                "Create and update operations require file content."
            )

        return value


class DevelopmentPlan(BaseModel):
    summary: str = Field(min_length=10, max_length=1000)
    commit_message: str = Field(min_length=10, max_length=100)
    files: list[PlannedFileChange] = Field(min_length=1, max_length=20)
    validation_notes: list[str] = Field(default_factory=list)

    @field_validator("commit_message")
    @classmethod
    def validate_commit_message(cls, value: str) -> str:
        cleaned = value.strip()

        prefix_replacements = {
            "setup:": "chore:",
            "feature:": "feat:",
            "bugfix:": "fix:",
            "documentation:": "docs:",
            "testing:": "test:",
        }

        for invalid_prefix, valid_prefix in prefix_replacements.items():
            if cleaned.lower().startswith(invalid_prefix):
                cleaned = valid_prefix + cleaned[len(invalid_prefix):]
                break

        allowed_prefixes = (
            "feat:",
            "fix:",
            "docs:",
            "test:",
            "refactor:",
            "chore:",
            "style:",
            "perf:",
        )

        if not cleaned.startswith(allowed_prefixes):
            raise ValueError(
                "Commit message must use a conventional commit prefix."
            )

        return cleaned

class RepairPlan(BaseModel):
    summary: str = Field(min_length=10, max_length=1000)
    files: list[PlannedFileChange] = Field(min_length=1, max_length=10)
    diagnosis: str = Field(min_length=10, max_length=2000)