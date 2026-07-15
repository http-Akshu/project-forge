import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.agents.orchestrator import TaskOrchestrator
from app.core.state import ProjectStatus, TaskStatus
from app.database.models import Project, ProjectTask
from app.database.session import Base


@dataclass
class FakeDeveloperResult:
    task: ProjectTask
    commit_hash: str = "abcdef123456"
    plan: object = None


class FakeDeveloperAgent:
    def run_current_task(
        self,
        *,
        db: Session,
    ) -> FakeDeveloperResult:
        task = db.query(ProjectTask).filter(
            ProjectTask.status == TaskStatus.IN_PROGRESS.value
        ).first()

        task.status = TaskStatus.COMPLETED.value
        db.add(task)
        db.commit()
        db.refresh(task)

        return FakeDeveloperResult(task=task)


def test_orchestrator_starts_next_pending_task(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    Base.metadata.create_all(engine)

    project_directory = tmp_path / "project"
    project_directory.mkdir()

    with Session(engine) as db:
        project = Project(
            name="Test Project",
            slug="test-project",
            title="Test Project",
            problem_statement=(
                "Teams need a simple way to manage recurring work."
            ),
            solution_summary=(
                "A small application will track and organize work."
            ),
            target_users=json.dumps(["Small teams"]),
            project_size="small",
            status=ProjectStatus.IMPLEMENTING.value,
            technology_stack=json.dumps(
                {
                    "frontend": ["Next.js"],
                    "backend": ["Next.js"],
                    "database": ["SQLite"],
                    "testing": ["Vitest"],
                }
            ),
            local_path=str(project_directory),
            current_phase="Implementation",
            is_active=True,
        )

        db.add(project)
        db.flush()

        first_task = ProjectTask(
            project_id=project.id,
            position=1,
            title="Create task model",
            description=(
                "Create the initial task model and validation rules."
            ),
            task_type="feature",
            status=TaskStatus.PENDING.value,
            acceptance_criteria=json.dumps(
                ["The task model is defined"]
            ),
            estimated_days=1,
        )

        second_task = ProjectTask(
            project_id=project.id,
            position=2,
            title="Create task interface",
            description=(
                "Create an interface for viewing project tasks."
            ),
            task_type="feature",
            status=TaskStatus.PENDING.value,
            acceptance_criteria=json.dumps(
                ["The task interface is available"]
            ),
            estimated_days=1,
        )

        db.add_all([first_task, second_task])
        db.commit()

        orchestrator = TaskOrchestrator(
            developer_agent=FakeDeveloperAgent()
        )

        result = orchestrator.run_one_task(db=db)

        assert result.selected_task is not None
        assert result.selected_task.position == 1
        assert result.selected_task.status == TaskStatus.COMPLETED.value
        assert result.project_completed is False

        db.refresh(second_task)

        assert second_task.status == TaskStatus.PENDING.value

        task_file = (
            project_directory / "PROJECTFORGE_TASK.md"
        ).read_text(encoding="utf-8")

        assert "Task 1: Create task model" in task_file


def test_orchestrator_marks_project_ready_for_review(
    tmp_path: Path,
) -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )

    Base.metadata.create_all(engine)

    project_directory = tmp_path / "project"
    project_directory.mkdir()

    with Session(engine) as db:
        project = Project(
            name="Test Project",
            slug="test-project",
            title="Test Project",
            problem_statement=(
                "Users need a reliable small application."
            ),
            solution_summary=(
                "The application provides the required workflow."
            ),
            target_users=json.dumps(["Users"]),
            project_size="small",
            status=ProjectStatus.IMPLEMENTING.value,
            technology_stack=json.dumps(
                {
                    "frontend": ["Next.js"],
                    "backend": ["Next.js"],
                    "database": ["SQLite"],
                    "testing": ["Vitest"],
                }
            ),
            local_path=str(project_directory),
            current_phase="Implementation",
            is_active=True,
        )

        db.add(project)
        db.flush()

        task = ProjectTask(
            project_id=project.id,
            position=1,
            title="Complete final feature",
            description=(
                "Implement and verify the final planned feature."
            ),
            task_type="feature",
            status=TaskStatus.PENDING.value,
            acceptance_criteria=json.dumps(
                ["The final feature is complete"]
            ),
            estimated_days=1,
        )

        db.add(task)
        db.commit()

        orchestrator = TaskOrchestrator(
            developer_agent=FakeDeveloperAgent()
        )

        result = orchestrator.run_one_task(db=db)

        assert result.project_completed is True
        assert (
            result.project.status
            == ProjectStatus.REVIEWING.value
        )