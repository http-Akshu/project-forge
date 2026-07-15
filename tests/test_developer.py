import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.agents.developer import DeveloperAgent
from app.core.state import ProjectStatus, TaskStatus
from app.database.models import Project, ProjectTask
from app.database.session import Base
from app.schemas.code_change import DevelopmentPlan
from app.services.command_runner import CommandResult


class FakeDeepSeekService:
    def generate_structured(
        self,
        *,
        schema,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> DevelopmentPlan:
        return schema.model_validate(
            {
                "summary": (
                    "Create a detailed requirements document for "
                    "the initial project."
                ),
                "commit_message": (
                    "docs: define initial project requirements"
                ),
                "validation_notes": [
                    "The project documentation should remain valid."
                ],
                "files": [
                    {
                        "path": "docs/requirements.md",
                        "operation": "create",
                        "content": (
                            "# Requirements\n\n"
                            "## Objective\n\n"
                            "Provide reliable task tracking.\n"
                        ),
                        "explanation": (
                            "Document the first project requirements."
                        ),
                    }
                ],
            }
        )


class FakeCommandRunner:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.status_calls = 0

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

        stdout = ""

        if command_list == ["git", "status", "--porcelain"]:
            self.status_calls += 1

            if self.status_calls >= 2:
                stdout = "A  docs/requirements.md"

        if command_list == ["git", "rev-parse", "HEAD"]:
            stdout = "1234567890abcdef"

        return CommandResult(
            command=command_list,
            return_code=0,
            stdout=stdout,
            stderr="",
            working_directory=working_directory,
        )


def test_developer_completes_current_task(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    Base.metadata.create_all(engine)

    project_directory = tmp_path / "project"
    project_directory.mkdir()

    vault_directory = tmp_path / "vault"
    vault_directory.mkdir()

    (vault_directory / "DAILY_LOG.md").write_text(
        "# Daily Development Log\n",
        encoding="utf-8",
    )

    (project_directory / "package.json").write_text(
        json.dumps(
            {
                "name": "test-project",
                "scripts": {
                    "lint": "eslint",
                    "build": "next build",
                },
            }
        ),
        encoding="utf-8",
    )

    (project_directory / "README.md").write_text(
        "# Test Project\n",
        encoding="utf-8",
    )

    (project_directory / ".projectforge.json").write_text(
        "{}",
        encoding="utf-8",
    )

    with Session(engine) as db:
        project = Project(
            name="Test Project",
            slug="test-project",
            title="Test Project",
            problem_statement=(
                "Teams need a reliable way to track routine work."
            ),
            solution_summary=(
                "A focused application will organize work items."
            ),
            target_users=json.dumps(["Small teams"]),
            project_size="small",
            status=ProjectStatus.IMPLEMENTING.value,
            technology_stack=json.dumps(
                {
                    "frontend": ["Next.js"],
                    "backend": ["Next.js"],
                    "database": ["SQLite"],
                    "testing": ["ESLint"],
                }
            ),
            local_path=str(project_directory),
            knowledge_vault_path=str(vault_directory),
            current_phase="Implementation",
            is_active=True,
        )

        db.add(project)
        db.flush()

        task = ProjectTask(
            project_id=project.id,
            position=1,
            title="Define requirements",
            description=(
                "Create the initial requirements documentation."
            ),
            task_type="planning",
            status=TaskStatus.IN_PROGRESS.value,
            acceptance_criteria=json.dumps(
                ["Requirements are documented"]
            ),
            estimated_days=1,
        )

        db.add(task)
        db.commit()

        developer = DeveloperAgent(
            deepseek_service=FakeDeepSeekService(),
            command_runner=FakeCommandRunner(),
        )

        result = developer.run_current_task(db=db)

        assert result.task.status == TaskStatus.COMPLETED.value
        assert result.commit_hash == "1234567890abcdef"

        assert (
            project_directory
            / "docs"
            / "requirements.md"
        ).exists()

        daily_log = (
            vault_directory / "DAILY_LOG.md"
        ).read_text(encoding="utf-8")

        assert "Define requirements" in daily_log