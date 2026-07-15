import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.developer import DeveloperAgent, DeveloperResult
from app.core.state import ProjectStatus, TaskStatus
from app.database.models import Project, ProjectTask


class OrchestratorError(RuntimeError):
    """Raised when the daily project workflow cannot continue."""


@dataclass
class OrchestratorResult:
    project: Project
    selected_task: ProjectTask | None
    developer_result: DeveloperResult | None
    project_completed: bool
    message: str


class TaskOrchestrator:
    def __init__(
        self,
        *,
        developer_agent: DeveloperAgent | None = None,
    ) -> None:
        self.developer_agent = developer_agent or DeveloperAgent()

    def run_one_task(
        self,
        *,
        db: Session,
    ) -> OrchestratorResult:
        project = db.scalar(
            select(Project).where(Project.is_active.is_(True))
        )

        if not project:
            raise OrchestratorError(
                "No active project exists."
            )

        if project.status not in {
            ProjectStatus.IMPLEMENTING.value,
            ProjectStatus.REVIEWING.value,
        }:
            raise OrchestratorError(
                "The active project cannot currently run tasks. "
                f"Status: {project.status}"
            )

        current_task = self._get_in_progress_task(
            db=db,
            project=project,
        )

        if not current_task:
            current_task = self._start_next_pending_task(
                db=db,
                project=project,
            )

        if not current_task:
            project.status = ProjectStatus.REVIEWING.value
            project.current_phase = "All planned tasks completed"
            db.add(project)
            db.commit()
            db.refresh(project)

            return OrchestratorResult(
                project=project,
                selected_task=None,
                developer_result=None,
                project_completed=True,
                message=(
                    "All planned tasks are completed. "
                    "The project is ready for final review."
                ),
            )

        self._write_current_task_file(
            project=project,
            task=current_task,
        )

        developer_result = self.developer_agent.run_current_task(
            db=db
        )

        remaining_task = db.scalar(
            select(ProjectTask)
            .where(
                ProjectTask.project_id == project.id,
                ProjectTask.status == TaskStatus.PENDING.value,
            )
            .order_by(ProjectTask.position)
        )

        project_completed = remaining_task is None

        if project_completed:
            project.status = ProjectStatus.REVIEWING.value
            project.current_phase = "All planned tasks completed"
            message = (
                f"Task {current_task.position} completed. "
                "All planned project tasks are finished."
            )
        else:
            project.status = ProjectStatus.IMPLEMENTING.value
            project.current_phase = (
                f"Task {current_task.position} completed; "
                f"task {remaining_task.position} is next"
            )
            message = (
                f"Task {current_task.position} completed successfully. "
                f"Task {remaining_task.position} will run next."
            )

        db.add(project)
        db.commit()
        db.refresh(project)

        return OrchestratorResult(
            project=project,
            selected_task=current_task,
            developer_result=developer_result,
            project_completed=project_completed,
            message=message,
        )

    @staticmethod
    def _get_in_progress_task(
        *,
        db: Session,
        project: Project,
    ) -> ProjectTask | None:
        return db.scalar(
            select(ProjectTask)
            .where(
                ProjectTask.project_id == project.id,
                ProjectTask.status == TaskStatus.IN_PROGRESS.value,
            )
            .order_by(ProjectTask.position)
        )

    @staticmethod
    def _start_next_pending_task(
        *,
        db: Session,
        project: Project,
    ) -> ProjectTask | None:
        task = db.scalar(
            select(ProjectTask)
            .where(
                ProjectTask.project_id == project.id,
                ProjectTask.status == TaskStatus.PENDING.value,
            )
            .order_by(ProjectTask.position)
        )

        if not task:
            return None

        task.status = TaskStatus.IN_PROGRESS.value
        project.current_phase = (
            f"Implementing task {task.position}: {task.title}"
        )

        db.add(task)
        db.add(project)
        db.commit()
        db.refresh(task)

        return task

    @staticmethod
    def _write_current_task_file(
        *,
        project: Project,
        task: ProjectTask,
    ) -> None:
        if not project.local_path:
            raise OrchestratorError(
                "The active project has no local directory."
            )

        project_directory = Path(project.local_path)
        task_path = project_directory / "PROJECTFORGE_TASK.md"

        try:
            criteria = json.loads(task.acceptance_criteria)
        except json.JSONDecodeError:
            criteria = [task.acceptance_criteria]

        criteria_text = "\n".join(
            f"- [ ] {criterion}" for criterion in criteria
        )

        content = f"""# Current ProjectForge Task

## Task {task.position}: {task.title}

**Type:** {task.task_type}

**Status:** {task.status}

## Description

{task.description}

## Acceptance Criteria

{criteria_text}

## Agent Instructions

- Implement only this task.
- Do not add unrelated features.
- Preserve all passing tests and existing functionality.
- Run linting, tests, and production builds.
- Never commit credentials, secrets, or `.env` files.
- Update documentation when behavior or architecture changes.
"""

        task_path.write_text(
            content,
            encoding="utf-8",
        )