from pathlib import Path

import pytest

from app.schemas.code_change import (
    FileOperation,
    PlannedFileChange,
)
from app.services.project_files import (
    ProjectFileError,
    ProjectFileService,
)


def test_applies_safe_file_changes(tmp_path: Path) -> None:
    existing_file = tmp_path / "README.md"
    existing_file.write_text("# Original\n", encoding="utf-8")

    service = ProjectFileService(
        project_directory=tmp_path
    )

    changes = [
        PlannedFileChange(
            path="README.md",
            operation=FileOperation.UPDATE,
            content="# Updated\n",
            explanation="Update project documentation.",
        ),
        PlannedFileChange(
            path="docs/requirements.md",
            operation=FileOperation.CREATE,
            content="# Requirements\n",
            explanation="Add requirements documentation.",
        ),
    ]

    applied = service.apply_changes(changes)

    assert len(applied) == 2
    assert existing_file.read_text(
        encoding="utf-8"
    ) == "# Updated\n"

    assert (
        tmp_path / "docs" / "requirements.md"
    ).exists()


def test_rejects_directory_traversal(tmp_path: Path) -> None:
    service = ProjectFileService(
        project_directory=tmp_path
    )

    with pytest.raises(ProjectFileError):
        service.resolve_safe_path("../outside.txt")


def test_rejects_environment_file(tmp_path: Path) -> None:
    service = ProjectFileService(
        project_directory=tmp_path
    )

    with pytest.raises(ProjectFileError):
        service.resolve_safe_path(".env")


def test_rejects_potential_secret(tmp_path: Path) -> None:
    service = ProjectFileService(
        project_directory=tmp_path
    )

    change = PlannedFileChange(
        path="config.txt",
        operation=FileOperation.CREATE,
        content="API_KEY=sk-exampleexampleexampleexample123",
        explanation="Unsafe test content.",
    )

    with pytest.raises(
        ProjectFileError,
        match="Potential secret",
    ):
        service.apply_changes([change])

def test_allows_typescript_module_config(tmp_path: Path) -> None:
    service = ProjectFileService(
        project_directory=tmp_path
    )

    change = PlannedFileChange(
        path="vitest.config.mts",
        operation=FileOperation.CREATE,
        content=(
            'import { defineConfig } from "vitest/config";\n\n'
            "export default defineConfig({});\n"
        ),
        explanation="Add Vitest configuration.",
    )

    applied = service.apply_changes([change])

    assert len(applied) == 1
    assert (tmp_path / "vitest.config.mts").exists()