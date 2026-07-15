import re
from dataclasses import dataclass
from pathlib import Path

from app.schemas.code_change import (
    FileOperation,
    PlannedFileChange,
)


class ProjectFileError(RuntimeError):
    """Raised when a generated file operation is unsafe."""


@dataclass(frozen=True)
class AppliedFileChange:
    path: Path
    operation: FileOperation


class ProjectFileService:
    BLOCKED_FILE_NAMES = {
        ".env",
        ".env.local",
        ".env.development",
        ".env.production",
        "id_rsa",
        "id_ed25519",
    }

    BLOCKED_DIRECTORIES = {
        ".git",
        ".next",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
    }

    ALLOWED_TEXT_SUFFIXES = {
        "",
        ".css",
        ".csv",
        ".html",
        ".js",
        ".json",
        ".jsx",
        ".md",
        ".mjs",
        ".py",
        ".sql",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }

    SECRET_PATTERNS = (
        re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
        re.compile(r"ghp_[A-Za-z0-9]{20,}"),
        re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
        re.compile(
            r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"
        ),
    )

    def __init__(self, *, project_directory: Path) -> None:
        self.project_directory = project_directory.resolve()

        if not self.project_directory.exists():
            raise ProjectFileError(
                f"Project directory does not exist: "
                f"{self.project_directory}"
            )

    def apply_changes(
        self,
        changes: list[PlannedFileChange],
    ) -> list[AppliedFileChange]:
        validated = [
            (change, self.resolve_safe_path(change.path))
            for change in changes
        ]

        for change, path in validated:
            self._validate_change(change=change, path=path)

        applied: list[AppliedFileChange] = []

        for change, path in validated:
            if change.operation == FileOperation.DELETE:
                if path.exists():
                    if path.is_dir():
                        raise ProjectFileError(
                            "Directory deletion is not permitted."
                        )

                    path.unlink()

            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    change.content or "",
                    encoding="utf-8",
                )

            applied.append(
                AppliedFileChange(
                    path=path,
                    operation=change.operation,
                )
            )

        return applied

    def resolve_safe_path(self, relative_path: str) -> Path:
        normalized = relative_path.replace("\\", "/")
        candidate = (self.project_directory / normalized).resolve()

        try:
            candidate.relative_to(self.project_directory)
        except ValueError as exc:
            raise ProjectFileError(
                f"Path escapes project directory: {relative_path}"
            ) from exc

        path_parts = set(candidate.relative_to(self.project_directory).parts)

        if path_parts.intersection(self.BLOCKED_DIRECTORIES):
            raise ProjectFileError(
                f"Protected directory cannot be modified: {relative_path}"
            )

        if candidate.name.lower() in self.BLOCKED_FILE_NAMES:
            raise ProjectFileError(
                f"Protected file cannot be modified: {relative_path}"
            )

        if candidate.suffix.lower() not in self.ALLOWED_TEXT_SUFFIXES:
            raise ProjectFileError(
                f"Unsupported file type: {relative_path}"
            )

        return candidate

    def read_project_context(
        self,
        *,
        maximum_files: int = 30,
        maximum_characters_per_file: int = 20_000,
        maximum_total_characters: int = 120_000,
    ) -> dict[str, str]:
        context: dict[str, str] = {}
        total_characters = 0

        priority_names = {
            "README.md",
            "PROJECTFORGE_TASK.md",
            "AGENTS.md",
            "package.json",
            "pyproject.toml",
            "tsconfig.json",
            "next.config.ts",
            "next.config.js",
        }

        candidate_files: list[Path] = []

        for path in self.project_directory.rglob("*"):
            if not path.is_file():
                continue

            relative = path.relative_to(self.project_directory)

            if set(relative.parts).intersection(self.BLOCKED_DIRECTORIES):
                continue

            if path.suffix.lower() not in self.ALLOWED_TEXT_SUFFIXES:
                continue

            if path.name in self.BLOCKED_FILE_NAMES:
                continue

            candidate_files.append(path)

        candidate_files.sort(
            key=lambda item: (
                item.name not in priority_names,
                len(item.parts),
                str(item),
            )
        )

        for path in candidate_files:
            if len(context) >= maximum_files:
                break

            try:
                content = path.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
            except OSError:
                continue

            if len(content) > maximum_characters_per_file:
                content = (
                    content[:maximum_characters_per_file]
                    + "\n\n[File truncated by ProjectForge]\n"
                )

            if total_characters + len(content) > maximum_total_characters:
                break

            relative_path = path.relative_to(
                self.project_directory
            ).as_posix()

            context[relative_path] = content
            total_characters += len(content)

        return context

    def _validate_change(
        self,
        *,
        change: PlannedFileChange,
        path: Path,
    ) -> None:
        if (
            change.operation == FileOperation.CREATE
            and path.exists()
        ):
            raise ProjectFileError(
                f"Create operation targets an existing file: "
                f"{change.path}"
            )

        if (
            change.operation == FileOperation.UPDATE
            and not path.exists()
        ):
            raise ProjectFileError(
                f"Update operation targets a missing file: "
                f"{change.path}"
            )

        if change.operation == FileOperation.DELETE:
            protected_files = {
                "package.json",
                "pyproject.toml",
                "README.md",
                "AGENTS.md",
                ".projectforge.json",
            }

            if path.name in protected_files:
                raise ProjectFileError(
                    f"Protected project file cannot be deleted: "
                    f"{change.path}"
                )

        content = change.content or ""

        for pattern in self.SECRET_PATTERNS:
            if pattern.search(content):
                raise ProjectFileError(
                    f"Potential secret detected in {change.path}"
                )