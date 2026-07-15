import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.agents.developer import DeveloperAgent
from app.core.state import ProjectStatus, TaskStatus
from app.database.models import Project, ProjectTask
from app.database.session import Base
from app.schemas.code_change import DevelopmentPlan, RepairPlan
from app.services.command_runner import CommandResult


class RepairingDeepSeekService:
    def generate_structured(
        self,
        *,
        schema,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ):
        if schema is DevelopmentPlan:
            return schema.model_validate(
                {
                    "summary": "Create an initially invalid module.",
                    "commit_message": "feat: add greeting module",
                    "validation_notes": [
                        "The project should pass validation."
                    ],
                    "files": [
                        {
                            "path": "src/greeting.ts",
                            "operation": "create",
                            "content": (
                                'import { message } from "./missing";\n'
                                "export const greeting = message;\n"
                            ),
                            "explanation": (
                                "Add the initial greeting implementation."
                            ),
                        }
                    ],
                }
            )

        if schema is RepairPlan:
            return schema.model_validate(
                {
                    "diagnosis": (
                        "The greeting module imports a missing local file."
                    ),
                    "summary": (
                        "Create the missing module expected by the import."
                    ),
                    "files": [
                        {
                            "path": "src/missing.ts",
                            "operation": "create",
                            "content": (
                                'export const message = "Hello";\n'
                            ),
                            "explanation": (
                                "Provide the missing imported value."
                            ),
                        }
                    ],
                }
            )

        raise AssertionError("Unexpected schema requested.")


class RepairCommandRunner:
    def __init__(self) -> None:
        self.validation_calls = 0
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
        stdout = ""
        stderr = ""
        return_code = 0

        if command_list == ["git", "status", "--porcelain"]:
            self.status_calls += 1

            if self.status_calls >= 2:
                stdout = "A  src/greeting.ts\nA  src/missing.ts"

        elif command_list[:3] == ["npm.cmd", "run", "lint"]:
            self.validation_calls += 1

            if self.validation_calls == 1:
                return_code = 1
                stderr = (
                    "Cannot find module './missing' "
                    "or its corresponding type declarations."
                )

        elif command_list == ["git", "rev-parse", "HEAD"]:
            stdout = "repaircommit123456"

        return CommandResult(
            command=command_list,
            return_code=return_code,
            stdout=stdout,
            stderr=stderr,
            working_directory=working_directory,
        )


def test_developer_repairs_failed_validation(
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
                "name": "repair-test",
                "scripts": {
                    "lint": "eslint",
                },
            }
        ),
        encoding="utf-8",
    )

    (project_directory / "README.md").write_text(
        "# Repair Test\n",
        encoding="utf-8",
    )

    (project_directory / ".projectforge.json").write_text(
        "{}",
        encoding="utf-8",
    )

    with Session(engine) as db:
        project = Project(
            name="Repair Test",
            slug="repair-test",
            title="Repair Test",
            problem_statement=(
                "Developers need validation failures repaired."
            ),
            solution_summary=(
                "The agent automatically diagnoses and repairs failures."
            ),
            target_users=json.dumps(["Developers"]),
            project_size="small",
            status=ProjectStatus.IMPLEMENTING.value,
            technology_stack=json.dumps(
                {
                    "frontend": ["Next.js"],
                    "backend": ["Next.js"],
                    "database": [],
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
            title="Add greeting module",
            description=(
                "Create and validate the greeting module."
            ),
            task_type="feature",
            status=TaskStatus.IN_PROGRESS.value,
            acceptance_criteria=json.dumps(
                ["The greeting module passes validation"]
            ),
            estimated_days=1,
        )

        db.add(task)
        db.commit()

        developer = DeveloperAgent(
            deepseek_service=RepairingDeepSeekService(),
            command_runner=RepairCommandRunner(),
        )

        result = developer.run_current_task(db=db)

        assert result.task.status == TaskStatus.COMPLETED.value
        assert (
            project_directory / "src" / "missing.ts"
        ).exists()

        assert result.commit_hash == "repaircommit123456"