import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.state import ProjectStatus, TaskStatus
from app.database.models import Project, ProjectTask
from app.schemas.code_change import DevelopmentPlan
from app.services.command_runner import (
    CommandExecutionError,
    CommandRunner,
)
from app.services.deepseek import DeepSeekService
from app.services.project_files import (
    AppliedFileChange,
    ProjectFileError,
    ProjectFileService,
)


class DeveloperAgentError(RuntimeError):
    """Raised when the developer agent cannot complete a task."""


@dataclass
class DeveloperResult:
    project: Project
    task: ProjectTask
    plan: DevelopmentPlan
    changed_files: list[AppliedFileChange]
    validation_commands: list[list[str]]
    commit_hash: str


class DeveloperAgent:
    def __init__(
        self,
        *,
        deepseek_service: DeepSeekService | None = None,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.settings = get_settings()
        self.deepseek = deepseek_service or DeepSeekService()
        self.command_runner = command_runner or CommandRunner(
            timeout_seconds=1200
        )

    def run_current_task(
        self,
        *,
        db: Session,
    ) -> DeveloperResult:
        project = db.scalar(
            select(Project).where(Project.is_active.is_(True))
        )

        if not project:
            raise DeveloperAgentError(
                "No active project exists."
            )

        if project.status != ProjectStatus.IMPLEMENTING.value:
            raise DeveloperAgentError(
                "The active project is not ready for implementation. "
                f"Current status: {project.status}"
            )

        task = db.scalar(
            select(ProjectTask)
            .where(
                ProjectTask.project_id == project.id,
                ProjectTask.status == TaskStatus.IN_PROGRESS.value,
            )
            .order_by(ProjectTask.position)
        )

        if not task:
            raise DeveloperAgentError(
                "No task is currently marked as in progress."
            )

        if not project.local_path:
            raise DeveloperAgentError(
                "The project has no local working directory."
            )

        project_directory = Path(project.local_path).resolve()
        file_service = ProjectFileService(
            project_directory=project_directory
        )

        self._ensure_clean_git_worktree(project_directory)

        context = file_service.read_project_context()

        plan = self._generate_development_plan(
            project=project,
            task=task,
            project_context=context,
        )

        try:
            changed_files = file_service.apply_changes(plan.files)

            validation_commands = self._validation_commands(
                project_directory
            )

            self._run_validation(
                project_directory=project_directory,
                commands=validation_commands,
            )

            task.status = TaskStatus.COMPLETED.value
            task.completed_at = datetime.now(timezone.utc)
            task.last_error = None

            self._update_project_metadata(
                project_directory=project_directory,
                task=task,
            )

            commit_hash = self._commit_changes(
                project_directory=project_directory,
                commit_message=plan.commit_message,
            )

            self._update_daily_log(
                project=project,
                task=task,
                plan=plan,
                commit_hash=commit_hash,
            )

            next_task = db.scalar(
                select(ProjectTask)
                .where(
                    ProjectTask.project_id == project.id,
                    ProjectTask.status == TaskStatus.PENDING.value,
                )
                .order_by(ProjectTask.position)
            )

            if next_task:
                project.current_phase = (
                    f"Completed task {task.position}; "
                    f"next task {next_task.position} is pending"
                )
            else:
                project.current_phase = "All planned tasks completed"
                project.status = ProjectStatus.REVIEWING.value

            db.add(task)
            db.add(project)
            db.commit()
            db.refresh(task)
            db.refresh(project)

            return DeveloperResult(
                project=project,
                task=task,
                plan=plan,
                changed_files=changed_files,
                validation_commands=validation_commands,
                commit_hash=commit_hash,
            )

        except Exception as exc:
            task.status = TaskStatus.FAILED.value
            task.repair_attempts += 1
            task.last_error = str(exc)

            project.current_phase = (
                f"Task {task.position} failed validation"
            )

            db.add(task)
            db.add(project)
            db.commit()

            self._restore_git_worktree(project_directory)

            if isinstance(
                exc,
                (
                    DeveloperAgentError,
                    ProjectFileError,
                    CommandExecutionError,
                ),
            ):
                raise DeveloperAgentError(str(exc)) from exc

            raise DeveloperAgentError(
                f"Task implementation failed: {exc}"
            ) from exc

    def _generate_development_plan(
        self,
        *,
        project: Project,
        task: ProjectTask,
        project_context: dict[str, str],
    ) -> DevelopmentPlan:
        try:
            acceptance_criteria = json.loads(
                task.acceptance_criteria
            )
        except json.JSONDecodeError:
            acceptance_criteria = [task.acceptance_criteria]

        formatted_context = "\n\n".join(
            f"--- FILE: {path} ---\n{content}"
            for path, content in project_context.items()
        )

        system_prompt = """
You are the implementation engineer inside ProjectForge.

You receive one narrowly scoped software-development task and the current
repository files. Produce the smallest complete set of file changes required
to satisfy the task.

Rules:
- Implement only the current task.
- Do not invent unrelated features.
- Preserve working behavior.
- Do not include secrets, tokens, passwords, or API keys.
- Never modify .env files, .git, node_modules, .next, or virtual environments.
- Use create only for files that do not exist.
- Use update only for files that already exist.
- Return complete file contents, not diffs.
- Do not use placeholder comments such as TODO for required functionality.
- Keep code simple and understandable.
- Follow the repository's existing stack and conventions.
- Update documentation when the task is documentation or planning work.
- Do not modify package-lock.json manually.
- The commit message must use conventional commit format.
"""

        user_prompt = f"""
PROJECT TITLE:
{project.title}

PROBLEM:
{project.problem_statement}

SOLUTION:
{project.solution_summary}

CURRENT TASK:
Task {task.position}: {task.title}

TASK TYPE:
{task.task_type}

TASK DESCRIPTION:
{task.description}

ACCEPTANCE CRITERIA:
{json.dumps(acceptance_criteria, indent=2)}

CURRENT REPOSITORY FILES:
{formatted_context}

Return this exact JSON structure:

{{
  "summary": "What will be implemented and why",
  "commit_message": "docs: define project requirements",
  "validation_notes": [
    "Explanation of expected validation"
  ],
  "files": [
    {{
      "path": "relative/path/to/file",
      "operation": "create",
      "content": "Complete file contents",
      "explanation": "Why this file is needed"
    }}
  ]
}}

Allowed operations:
- create
- update
- delete

Do not wrap the JSON in Markdown.
"""

        try:
            return self.deepseek.generate_structured(
                schema=DevelopmentPlan,
                system_prompt=system_prompt.strip(),
                user_prompt=user_prompt.strip(),
                model=self.settings.deepseek_default_model,
            )
        except Exception as exc:
            raise DeveloperAgentError(
                f"DeepSeek could not create a valid development plan: "
                f"{exc}"
            ) from exc

    def _validation_commands(
        self,
        project_directory: Path,
    ) -> list[list[str]]:
        package_json = project_directory / "package.json"
        pyproject = project_directory / "pyproject.toml"

        if package_json.exists():
            npm = "npm.cmd" if os.name == "nt" else "npm"

            package_data = json.loads(
                package_json.read_text(encoding="utf-8")
            )

            scripts = package_data.get("scripts", {})

            commands: list[list[str]] = []

            if "lint" in scripts:
                commands.append([npm, "run", "lint"])

            if "test" in scripts:
                commands.append([npm, "test", "--", "--run"])

            if "build" in scripts:
                commands.append([npm, "run", "build"])

            if not commands:
                raise DeveloperAgentError(
                    "No validation scripts were found in package.json."
                )

            return commands

        if pyproject.exists():
            python_executable = self._project_python_executable(
                project_directory
            )

            return [
                [
                    str(python_executable),
                    "-m",
                    "ruff",
                    "check",
                    ".",
                ],
                [
                    str(python_executable),
                    "-m",
                    "pytest",
                    "-q",
                ],
            ]

        raise DeveloperAgentError(
            "Could not determine project validation commands."
        )

    def _run_validation(
        self,
        *,
        project_directory: Path,
        commands: list[list[str]],
    ) -> None:
        for command in commands:
            self.command_runner.run(
                command,
                working_directory=project_directory,
                timeout_seconds=1200,
            )

    def _ensure_clean_git_worktree(
        self,
        project_directory: Path,
    ) -> None:
        result = self.command_runner.run(
            ["git", "status", "--porcelain"],
            working_directory=project_directory,
        )

        if result.stdout.strip():
            raise DeveloperAgentError(
                "Generated project has uncommitted changes. "
                "Commit, discard, or review them before running the agent."
            )

    def _commit_changes(
        self,
        *,
        project_directory: Path,
        commit_message: str,
    ) -> str:
        self.command_runner.run(
            ["git", "add", "."],
            working_directory=project_directory,
        )

        status = self.command_runner.run(
            ["git", "status", "--porcelain"],
            working_directory=project_directory,
        )

        if not status.stdout.strip():
            raise DeveloperAgentError(
                "The development plan produced no Git changes."
            )

        self.command_runner.run(
            ["git", "commit", "-m", commit_message],
            working_directory=project_directory,
        )

        result = self.command_runner.run(
            ["git", "rev-parse", "HEAD"],
            working_directory=project_directory,
        )

        return result.stdout.strip()

    def _restore_git_worktree(
        self,
        project_directory: Path,
    ) -> None:
        try:
            self.command_runner.run(
                ["git", "reset", "--hard", "HEAD"],
                working_directory=project_directory,
                check=False,
            )

            self.command_runner.run(
                ["git", "clean", "-fd"],
                working_directory=project_directory,
                check=False,
            )
        except Exception:
            pass

    def _update_project_metadata(
        self,
        *,
        project_directory: Path,
        task: ProjectTask,
    ) -> None:
        metadata_path = project_directory / ".projectforge.json"

        metadata: dict[str, object] = {}

        if metadata_path.exists():
            try:
                metadata = json.loads(
                    metadata_path.read_text(encoding="utf-8")
                )
            except json.JSONDecodeError:
                metadata = {}

        metadata.update(
            {
                "last_completed_task": task.position,
                "last_run_at": datetime.now(
                    timezone.utc
                ).isoformat(),
            }
        )

        metadata_path.write_text(
            json.dumps(metadata, indent=2) + "\n",
            encoding="utf-8",
        )

    def _update_daily_log(
        self,
        *,
        project: Project,
        task: ProjectTask,
        plan: DevelopmentPlan,
        commit_hash: str,
    ) -> None:
        if not project.knowledge_vault_path:
            return

        daily_log = (
            Path(project.knowledge_vault_path) / "DAILY_LOG.md"
        )

        timestamp = datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )

        entry = f"""
## {timestamp}

### Task {task.position}: {task.title}

**Status:** Completed

**Summary:** {plan.summary}

**Commit:** `{commit_hash[:12]}`

**Commit message:** `{plan.commit_message}`

### Files Changed

"""

        for change in plan.files:
            entry += (
                f"- `{change.path}` — "
                f"{change.operation.value}: "
                f"{change.explanation}\n"
            )

        entry += "\n"

        with daily_log.open(
            "a",
            encoding="utf-8",
        ) as file:
            file.write(entry)

    @staticmethod
    def _project_python_executable(
        project_directory: Path,
    ) -> Path:
        if os.name == "nt":
            return (
                project_directory
                / ".venv"
                / "Scripts"
                / "python.exe"
            )

        return project_directory / ".venv" / "bin" / "python"