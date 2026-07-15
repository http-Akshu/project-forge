import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.agents.scaffolder import (
    ProjectTemplate,
    ScaffolderAgent,
)
from app.core.state import ProjectStatus, TaskStatus
from app.database.models import Project, ProjectTask
from app.database.session import Base
from app.services.command_runner import CommandResult


class FakeCommandRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def run(
        self,
        command,
        *,
        working_directory: Path,
        environment=None,
        timeout_seconds=None,
        check: bool = True,
    ) -> CommandResult:
        command_list = list(command)
        self.commands.append(command_list)

        if command_list[:3] == ["git", "status", "--porcelain"]:
            stdout = "A  README.md"
        else:
            stdout = ""

        return CommandResult(
            command=command_list,
            return_code=0,
            stdout=stdout,
            stderr="",
            working_directory=working_directory,
        )


def create_database_session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    Base.metadata.create_all(engine)

    return Session(engine)


def create_project(
    db: Session,
    *,
    directory: Path,
    stack: dict[str, list[str]],
) -> Project:
    directory.mkdir(parents=True)

    (directory / ".projectforge.json").write_text(
        "{}",
        encoding="utf-8",
    )

    (directory / "README.md").write_text(
        "# Test Project\n",
        encoding="utf-8",
    )

    project = Project(
        name="Test Project",
        slug=directory.name,
        title="Test Project",
        problem_statement=(
            "Users need a simple application for tracking routine work."
        ),
        solution_summary=(
            "The application provides a focused interface and local storage."
        ),
        target_users=json.dumps(["Small teams"]),
        project_size="small",
        status=ProjectStatus.PLANNING.value,
        technology_stack=json.dumps(stack),
        local_path=str(directory),
        current_phase="Planning",
        is_active=True,
    )

    db.add(project)
    db.flush()

    db.add(
        ProjectTask(
            project_id=project.id,
            position=1,
            title="Define initial project structure",
            description=(
                "Create and verify the initial application structure."
            ),
            task_type="setup",
            status=TaskStatus.PENDING.value,
            acceptance_criteria=json.dumps(
                ["The initial project structure exists"]
            ),
            estimated_days=1,
        )
    )

    db.commit()
    db.refresh(project)

    return project


def test_detects_nextjs_template(tmp_path: Path) -> None:
    db = create_database_session()

    try:
        project = create_project(
            db,
            directory=tmp_path / "web-project",
            stack={
                "frontend": ["Next.js", "TypeScript"],
                "backend": ["Next.js server actions"],
                "database": ["SQLite"],
                "testing": ["Vitest"],
            },
        )

        scaffolder = ScaffolderAgent(
            command_runner=FakeCommandRunner()
        )

        assert (
            scaffolder._detect_template(project)
            == ProjectTemplate.NEXTJS
        )
    finally:
        db.close()


def test_scaffolds_fastapi_project_without_install(
    tmp_path: Path,
) -> None:
    db = create_database_session()

    try:
        project = create_project(
            db,
            directory=tmp_path / "api-project",
            stack={
                "frontend": [],
                "backend": ["FastAPI", "Pydantic"],
                "database": ["SQLite", "SQLAlchemy"],
                "testing": ["pytest"],
            },
        )

        runner = FakeCommandRunner()
        scaffolder = ScaffolderAgent(command_runner=runner)

        result = scaffolder.scaffold_project(
            db=db,
            project=project,
            skip_install=True,
        )

        assert result.template == ProjectTemplate.FASTAPI
        assert result.project.status == ProjectStatus.IMPLEMENTING.value

        assert (
            result.project_directory
            / "src"
            / "api_project"
            / "main.py"
        ).exists()

        assert (
            result.project_directory / "pyproject.toml"
        ).exists()

        assert (
            result.project_directory / "PROJECTFORGE_TASK.md"
        ).exists()

        assert result.first_task is not None
        assert (
            result.first_task.status
            == TaskStatus.IN_PROGRESS.value
        )

        assert ["git", "init", "-b", "main"] in runner.commands
        assert ["git", "add", "."] in runner.commands
    finally:
        db.close()