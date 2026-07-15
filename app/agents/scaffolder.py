import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.state import ProjectStatus, TaskStatus
from app.database.models import Project, ProjectTask
from app.services.command_runner import (
    CommandExecutionError,
    CommandRunner,
)


class ScaffolderAgentError(RuntimeError):
    """Raised when a project cannot be scaffolded safely."""


class ProjectTemplate(StrEnum):
    NEXTJS = "nextjs"
    FASTAPI = "fastapi"
    PYTHON_CLI = "python_cli"


@dataclass
class ScaffoldResult:
    project: Project
    template: ProjectTemplate
    project_directory: Path
    first_task: ProjectTask | None
    commands_executed: list[list[str]]


class ScaffolderAgent:
    def __init__(
        self,
        *,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.command_runner = command_runner or CommandRunner()
        self.commands_executed: list[list[str]] = []

    def scaffold_active_project(
        self,
        *,
        db: Session,
        skip_install: bool = False,
    ) -> ScaffoldResult:
        project = db.scalar(
            select(Project).where(Project.is_active.is_(True))
        )

        if not project:
            raise ScaffolderAgentError(
                "No active project exists. Create a project plan first."
            )

        return self.scaffold_project(
            db=db,
            project=project,
            skip_install=skip_install,
        )

    def scaffold_project(
        self,
        *,
        db: Session,
        project: Project,
        skip_install: bool = False,
    ) -> ScaffoldResult:
        if not project.local_path:
            raise ScaffolderAgentError(
                "The project does not have a local directory."
            )

        if project.status not in {
            ProjectStatus.PLANNING.value,
            ProjectStatus.APPROVED.value,
        }:
            raise ScaffolderAgentError(
                "Only planned or approved projects can be scaffolded. "
                f"Current status: {project.status}"
            )

        project_directory = Path(project.local_path).resolve()

        if not project_directory.exists():
            raise ScaffolderAgentError(
                f"Project directory does not exist: {project_directory}"
            )

        template = self._detect_template(project)

        project.status = ProjectStatus.SCAFFOLDING.value
        project.current_phase = "Scaffolding"
        db.commit()

        try:
            self._prepare_directory(project_directory)

            if template == ProjectTemplate.NEXTJS:
                self._scaffold_nextjs(
                    project=project,
                    project_directory=project_directory,
                    skip_install=skip_install,
                )
            elif template == ProjectTemplate.FASTAPI:
                self._scaffold_fastapi(
                    project=project,
                    project_directory=project_directory,
                    skip_install=skip_install,
                )
            else:
                self._scaffold_python_cli(
                    project=project,
                    project_directory=project_directory,
                    skip_install=skip_install,
                )

            self._write_projectforge_files(
                project=project,
                project_directory=project_directory,
                template=template,
            )

            self._initialize_git_repository(project_directory)

            first_task = self._prepare_first_task(
                db=db,
                project=project,
            )

            project.status = ProjectStatus.IMPLEMENTING.value
            project.current_phase = "Implementation"
            db.commit()
            db.refresh(project)

            return ScaffoldResult(
                project=project,
                template=template,
                project_directory=project_directory,
                first_task=first_task,
                commands_executed=self.commands_executed.copy(),
            )

        except Exception as exc:
            db.rollback()

            project.status = ProjectStatus.BLOCKED.value
            project.current_phase = "Scaffolding failed"
            db.add(project)
            db.commit()

            if isinstance(exc, ScaffolderAgentError):
                raise

            raise ScaffolderAgentError(
                f"Project scaffolding failed: {exc}"
            ) from exc

    def _detect_template(
        self,
        project: Project,
    ) -> ProjectTemplate:
        stack = self._load_technology_stack(project)
        all_technologies = self._flatten_stack(stack)

        normalized = {
            technology.lower().replace(".", "").replace(" ", "")
            for technology in all_technologies
        }

        if any(
            technology in normalized
            for technology in {
                "nextjs",
                "react",
                "typescript",
                "tailwindcss",
            }
        ):
            return ProjectTemplate.NEXTJS

        if any(
            technology in normalized
            for technology in {
                "fastapi",
                "sqlalchemy",
                "pydantic",
            }
        ):
            return ProjectTemplate.FASTAPI

        return ProjectTemplate.PYTHON_CLI

    @staticmethod
    def _load_technology_stack(
        project: Project,
    ) -> dict[str, list[str]]:
        try:
            parsed = json.loads(project.technology_stack)
        except json.JSONDecodeError as exc:
            raise ScaffolderAgentError(
                "Project technology stack contains invalid JSON."
            ) from exc

        if not isinstance(parsed, dict):
            raise ScaffolderAgentError(
                "Project technology stack must be a JSON object."
            )

        return parsed

    @staticmethod
    def _flatten_stack(
        stack: dict[str, list[str]],
    ) -> list[str]:
        technologies: list[str] = []

        for values in stack.values():
            if isinstance(values, list):
                technologies.extend(
                    str(value)
                    for value in values
                    if str(value).strip()
                )

        return technologies

    @staticmethod
    def _prepare_directory(project_directory: Path) -> None:
        files_to_keep = {
            ".projectforge.json",
            "README.md",
        }

        unexpected_items = [
            item
            for item in project_directory.iterdir()
            if item.name not in files_to_keep
        ]

        if unexpected_items:
            item_names = ", ".join(
                item.name for item in unexpected_items
            )

            raise ScaffolderAgentError(
                "Project directory contains unexpected files: "
                f"{item_names}"
            )

    def _scaffold_nextjs(
        self,
        *,
        project: Project,
        project_directory: Path,
        skip_install: bool,
    ) -> None:
        temporary_directory = project_directory.parent / (
            f"{project.slug}-scaffold-temp"
        )

        if temporary_directory.exists():
            shutil.rmtree(temporary_directory)

        npx_command = "npx.cmd" if os.name == "nt" else "npx"

        command = [
            npx_command,
            "--yes",
            "create-next-app@latest",
            temporary_directory.name,
            "--typescript",
            "--tailwind",
            "--eslint",
            "--app",
            "--src-dir",
            "--import-alias",
            "@/*",
            "--use-npm",
            "--disable-git",
            "--yes",
        ]

        if skip_install:
            command.append("--skip-install")

        self._run(
            command,
            working_directory=project_directory.parent,
            timeout_seconds=1200,
        )

        self._replace_directory_contents(
            source_directory=temporary_directory,
            target_directory=project_directory,
        )

        package_json_path = project_directory / "package.json"

        if package_json_path.exists():
            package_data = json.loads(
                package_json_path.read_text(encoding="utf-8")
            )

            package_data["name"] = project.slug

            package_json_path.write_text(
                json.dumps(package_data, indent=2) + "\n",
                encoding="utf-8",
            )

    def _scaffold_fastapi(
        self,
        *,
        project: Project,
        project_directory: Path,
        skip_install: bool,
    ) -> None:
        source_directory = project_directory / "src" / project.slug.replace(
            "-",
            "_",
        )

        tests_directory = project_directory / "tests"

        source_directory.mkdir(parents=True, exist_ok=True)
        tests_directory.mkdir(parents=True, exist_ok=True)

        package_name = project.slug.replace("-", "_")

        self._write_file(
            source_directory / "__init__.py",
            "",
        )

        self._write_file(
            source_directory / "main.py",
            self._fastapi_main_content(project.title),
        )

        self._write_file(
            tests_directory / "__init__.py",
            "",
        )

        self._write_file(
            tests_directory / "test_health.py",
            self._fastapi_test_content(package_name),
        )

        self._write_file(
            project_directory / "pyproject.toml",
            self._fastapi_pyproject_content(
                project_slug=project.slug,
                package_name=package_name,
            ),
        )

        self._write_file(
            project_directory / ".gitignore",
            self._python_gitignore(),
        )

        self._write_file(
            project_directory / ".env.example",
            "APP_ENV=development\n",
        )

        if not skip_install:
            self._create_python_virtual_environment(project_directory)

            python_executable = self._project_python_executable(
                project_directory
            )

            self._run(
                [
                    str(python_executable),
                    "-m",
                    "pip",
                    "install",
                    "--upgrade",
                    "pip",
                ],
                working_directory=project_directory,
            )

            self._run(
                [
                    str(python_executable),
                    "-m",
                    "pip",
                    "install",
                    "-e",
                    ".[dev]",
                ],
                working_directory=project_directory,
                timeout_seconds=1200,
            )

    def _scaffold_python_cli(
        self,
        *,
        project: Project,
        project_directory: Path,
        skip_install: bool,
    ) -> None:
        package_name = project.slug.replace("-", "_")
        source_directory = project_directory / "src" / package_name
        tests_directory = project_directory / "tests"

        source_directory.mkdir(parents=True, exist_ok=True)
        tests_directory.mkdir(parents=True, exist_ok=True)

        self._write_file(
            source_directory / "__init__.py",
            "",
        )

        self._write_file(
            source_directory / "cli.py",
            self._python_cli_content(project.title),
        )

        self._write_file(
            tests_directory / "__init__.py",
            "",
        )

        self._write_file(
            tests_directory / "test_cli.py",
            self._python_cli_test_content(package_name),
        )

        self._write_file(
            project_directory / "pyproject.toml",
            self._python_cli_pyproject_content(
                project_slug=project.slug,
                package_name=package_name,
            ),
        )

        self._write_file(
            project_directory / ".gitignore",
            self._python_gitignore(),
        )

        if not skip_install:
            self._create_python_virtual_environment(project_directory)

            python_executable = self._project_python_executable(
                project_directory
            )

            self._run(
                [
                    str(python_executable),
                    "-m",
                    "pip",
                    "install",
                    "--upgrade",
                    "pip",
                ],
                working_directory=project_directory,
            )

            self._run(
                [
                    str(python_executable),
                    "-m",
                    "pip",
                    "install",
                    "-e",
                    ".[dev]",
                ],
                working_directory=project_directory,
                timeout_seconds=1200,
            )

    def _write_projectforge_files(
        self,
        *,
        project: Project,
        project_directory: Path,
        template: ProjectTemplate,
    ) -> None:
        metadata_path = project_directory / ".projectforge.json"

        metadata: dict[str, Any] = {}

        if metadata_path.exists():
            try:
                metadata = json.loads(
                    metadata_path.read_text(encoding="utf-8")
                )
            except json.JSONDecodeError:
                metadata = {}

        metadata.update(
            {
                "project_id": project.id,
                "title": project.title,
                "slug": project.slug,
                "template": template.value,
                "status": ProjectStatus.IMPLEMENTING.value,
                "current_task_position": 1,
                "scaffolded_at": datetime.now(
                    timezone.utc
                ).isoformat(),
            }
        )

        metadata_path.write_text(
            json.dumps(metadata, indent=2) + "\n",
            encoding="utf-8",
        )

        self._write_file(
            project_directory / "AGENTS.md",
            self._agents_file_content(project),
        )

        self._write_file(
            project_directory / "PROJECTFORGE_TASK.md",
            "# Current ProjectForge Task\n\n"
            "The first development task has not been loaded yet.\n",
        )

    def _initialize_git_repository(
        self,
        project_directory: Path,
    ) -> None:
        git_directory = project_directory / ".git"

        if not git_directory.exists():
            self._run(
                ["git", "init", "-b", "main"],
                working_directory=project_directory,
            )

        self._run(
            ["git", "add", "."],
            working_directory=project_directory,
        )

        status_result = self._run(
            ["git", "status", "--porcelain"],
            working_directory=project_directory,
        )

        if status_result.stdout:
            self._run(
                [
                    "git",
                    "commit",
                    "-m",
                    "chore: scaffold initial project structure",
                ],
                working_directory=project_directory,
            )

    def _prepare_first_task(
        self,
        *,
        db: Session,
        project: Project,
    ) -> ProjectTask | None:
        first_task = db.scalar(
            select(ProjectTask)
            .where(
                ProjectTask.project_id == project.id,
                ProjectTask.status == TaskStatus.PENDING.value,
            )
            .order_by(ProjectTask.position)
        )

        if not first_task:
            return None

        first_task.status = TaskStatus.IN_PROGRESS.value
        project_directory = Path(project.local_path)

        try:
            criteria = json.loads(first_task.acceptance_criteria)
        except json.JSONDecodeError:
            criteria = [first_task.acceptance_criteria]

        criteria_text = "\n".join(
            f"- [ ] {criterion}" for criterion in criteria
        )

        task_content = f"""# Current ProjectForge Task

## Task {first_task.position}: {first_task.title}

**Type:** {first_task.task_type}

**Status:** {first_task.status}

## Description

{first_task.description}

## Acceptance Criteria

{criteria_text}

## Agent Instructions

- Implement only the scope described in this task.
- Do not add unrelated features.
- Run all configured linting, tests, and builds.
- Do not commit secrets or local environment files.
- Update documentation when behavior changes.
"""

        self._write_file(
            project_directory / "PROJECTFORGE_TASK.md",
            task_content,
        )

        db.add(first_task)
        db.commit()
        db.refresh(first_task)

        return first_task

    def _create_python_virtual_environment(
        self,
        project_directory: Path,
    ) -> None:
        self._run(
            ["python", "-m", "venv", ".venv"],
            working_directory=project_directory,
            timeout_seconds=300,
        )

    @staticmethod
    def _project_python_executable(
        project_directory: Path,
    ) -> Path:
        if os.name == "nt":
            return project_directory / ".venv" / "Scripts" / "python.exe"

        return project_directory / ".venv" / "bin" / "python"

    def _run(
        self,
        command: list[str],
        *,
        working_directory: Path,
        timeout_seconds: int | None = None,
    ):
        self.commands_executed.append(command.copy())

        try:
            return self.command_runner.run(
                command,
                working_directory=working_directory,
                timeout_seconds=timeout_seconds,
            )
        except CommandExecutionError as exc:
            raise ScaffolderAgentError(str(exc)) from exc

    @staticmethod
    def _replace_directory_contents(
        *,
        source_directory: Path,
        target_directory: Path,
    ) -> None:
        files_to_preserve: dict[str, str] = {}

        for filename in {
            ".projectforge.json",
            "README.md",
        }:
            existing_file = target_directory / filename

            if existing_file.exists():
                files_to_preserve[filename] = existing_file.read_text(
                    encoding="utf-8"
                )

        for item in list(target_directory.iterdir()):
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

        for item in source_directory.iterdir():
            destination = target_directory / item.name

            if item.is_dir():
                shutil.move(str(item), str(destination))
            else:
                shutil.move(str(item), str(destination))

        shutil.rmtree(source_directory)

        for filename, content in files_to_preserve.items():
            preserved_file = target_directory / filename

            if not preserved_file.exists():
                preserved_file.write_text(
                    content,
                    encoding="utf-8",
                )

    @staticmethod
    def _write_file(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _fastapi_main_content(title: str) -> str:
        return f'''from fastapi import FastAPI


app = FastAPI(
    title="{title}",
    version="0.1.0",
)


@app.get("/health")
def health() -> dict[str, str]:
    return {{"status": "healthy"}}
'''

    @staticmethod
    def _fastapi_test_content(package_name: str) -> str:
        return f'''from fastapi.testclient import TestClient

from {package_name}.main import app


client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {{"status": "healthy"}}
'''

    @staticmethod
    def _fastapi_pyproject_content(
        *,
        project_slug: str,
        package_name: str,
    ) -> str:
        return f'''[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[project]
name = "{project_slug}"
version = "0.1.0"
description = "Generated by ProjectForge"
requires-python = ">=3.12"
dependencies = [
    "fastapi[standard]",
    "pydantic>=2",
    "sqlalchemy>=2",
]

[project.optional-dependencies]
dev = [
    "httpx",
    "pytest",
    "ruff",
]

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]

[tool.ruff]
line-length = 88
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
'''

    @staticmethod
    def _python_cli_content(title: str) -> str:
        return f'''import typer


app = typer.Typer(
    help="{title}",
)


@app.command()
def hello() -> None:
    """Confirm that the command-line application is working."""
    typer.echo("Application is ready.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
'''

    @staticmethod
    def _python_cli_test_content(package_name: str) -> str:
        return f'''from typer.testing import CliRunner

from {package_name}.cli import app


runner = CliRunner()


def test_hello_command() -> None:
    result = runner.invoke(app, ["hello"])

    assert result.exit_code == 0
    assert "Application is ready." in result.stdout
'''

    @staticmethod
    def _python_cli_pyproject_content(
        *,
        project_slug: str,
        package_name: str,
    ) -> str:
        return f'''[build-system]
requires = ["setuptools>=75"]
build-backend = "setuptools.build_meta"

[project]
name = "{project_slug}"
version = "0.1.0"
description = "Generated by ProjectForge"
requires-python = ">=3.12"
dependencies = [
    "rich",
    "typer",
]

[project.optional-dependencies]
dev = [
    "pytest",
    "ruff",
]

[project.scripts]
{project_slug} = "{package_name}.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
pythonpath = ["src"]
testpaths = ["tests"]

[tool.ruff]
line-length = 88
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
'''

    @staticmethod
    def _python_gitignore() -> str:
        return """# Environment variables
.env
.env.*
!.env.example

# Virtual environments
.venv/
venv/

# Python
__pycache__/
*.py[cod]
.pytest_cache/
.ruff_cache/
.mypy_cache/
*.egg-info/

# Databases
*.db
*.sqlite
*.sqlite3

# IDE
.vscode/
.idea/

# OS
.DS_Store
Thumbs.db
"""

    @staticmethod
    def _agents_file_content(project: Project) -> str:
        return f"""# ProjectForge Agent Instructions

## Project

{project.title}

## Problem

{project.problem_statement}

## Solution

{project.solution_summary}

## Development Rules

1. Read `PROJECTFORGE_TASK.md` before editing code.
2. Implement only the current task.
3. Do not add unrelated features.
4. Never commit API keys, credentials, or `.env` files.
5. Validate all external input.
6. Run linting, tests, and builds before committing.
7. Keep changes small and understandable.
8. Prefer simple architecture over unnecessary abstractions.
9. Update tests when behavior changes.
10. Do not remove existing tests to make a build pass.
"""