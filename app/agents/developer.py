import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.state import ProjectStatus, TaskStatus
from app.database.models import Project, ProjectTask
from app.schemas.code_change import DevelopmentPlan, RepairPlan
from app.services.command_runner import (
    CommandExecutionError,
    CommandRunner,
    ValidationFailure,
    ValidationResult,
)
from app.services.deepseek import DeepSeekService
from app.services.project_files import (
    AppliedFileChange,
    ProjectFileError,
    ProjectFileService,
)


DEVELOPMENT_SYSTEM_PROMPT = """
You are the implementation engineer inside ProjectForge.

Produce the smallest complete set of changes required for the current task.
Return complete file contents, not diffs.

Core rules:
- Implement only the current task and preserve existing working behavior.
- Do not add unrelated features, placeholders, secrets, or environment changes.
- Never modify .env files, .git, node_modules, .next, virtual environments, or
  package-lock.json manually.
- Use create only for missing files, update only for existing files, and avoid
  returning unchanged files.
- Prefer no more than 5 file operations.
- Follow the repository's current stack, conventions, aliases, and APIs.
- Every local import must resolve to an existing file or a file in the plan.
- Every identifier and named import must exist and be correctly imported.
- Use conventional commit prefixes: feat, fix, docs, test, refactor, chore,
  style, or perf.
- Add or update relevant tests when practical.

Dependencies:
- Never guess npm package versions or use prerelease versions.
- Add required packages to package.json, never directly to package-lock.json.
- Ensure requested versions exist in the npm registry.
- If using @libsql/client, do not use ^0.14.2.

Next.js client/server boundaries:
- A file containing "use client" must use fetch() to call API routes for
  persisted data.
- Never import Drizzle database instances, better-sqlite3, fs, path, or
  server-only modules into a Client Component.
- Keep database access inside route handlers, Server Components, server
  actions, or modules beginning with `import "server-only";`.
- In dynamic App Router route handlers, type params as
  `Promise<{ id: string }>` and resolve them with `const { id } = await params`.

Drizzle ORM:
- Inspect src/db/schema.ts before writing queries, inserts, updates, or joins.
- Use exact exported table names and exact TypeScript property names.
- The object key is the TypeScript property; the SQL column string may be
  snake_case. For example, `customerId: integer("customer_id")` is accessed as
  `requests.customerId`.
- Never invent table columns such as status, updatedAt, or customerId.
- Preserve existing tables and fields. Append new tables without rewriting
  unrelated schema definitions.
- When adding a table, include the required migration in the same task.
- Use eq, asc, desc, and, or, like, and sql only when imported from
  "drizzle-orm".
- Use asc(column) or desc(column), never string sort directions.
- Use the sql tagged template for raw SQL.
- For conditional query reassignment, start with `.$dynamic()`.
- Only pass fields declared by the target table to insert() or update().set().
- Build joins only through relationships that exist in the schema.

ServiceTracker context:
- Client pages must fetch customers, requests, and invoices from API routes.
- The existing requests table uses `customerId`, not `customer_id`.
- Task 6 must preserve customers and requests and append invoices if absent.
- Prefer invoices.requestId -> requests.id -> customers.id.
- Invoice payment state is represented by the existing `paid` field.
- Never update invoices with `{ status: "paid" }` unless a status column is
  explicitly present in the schema and migration.
- If `paid` uses boolean mode, mark payment with `{ paid: true }`; if it is a
  normal integer, use `{ paid: 1 }`.

Validation:
- Fix every reported build error, not only the first.
- Verify generated code against installed library APIs.
- Do not disable TypeScript, lint, tests, or validation rules to make code pass.
"""


REPAIR_SYSTEM_PROMPT = """
You are the repair engineer inside ProjectForge.

Diagnose the reported validation failure and return the smallest safe repair.
Return complete corrected file contents, not diffs.

Repair rules:
- Fix the reported failure while preserving the task and existing behavior.
- Do not rewrite unrelated files, remove tests, weaken validation, or add
  suppression comments such as @ts-ignore or eslint-disable.
- Use update for existing files, create for missing files, and keep each file
  explanation under 300 characters.
- Every local import, named export, and identifier must resolve.
- Fix all errors visible in the validation output.

Next.js repairs:
- Client Components must fetch persisted data from API routes and must not
  import database or Node-only modules.
- Dynamic route params must be typed as `Promise<{ id: string }>` and awaited.

Drizzle repairs:
- Inspect src/db/schema.ts and use exact table exports and TypeScript property
  names.
- Do not rename existing schema fields to match SQL column names.
- Restore previously working schema APIs instead of changing unrelated routes.
- Import every helper used from "drizzle-orm".
- Use asc()/desc(), sql tagged templates, and `.$dynamic()` for conditionally
  reassigned queries.
- Never update or insert fields absent from the target table.
- For unknown-property errors, replace the property with the exact declared
  field unless the task explicitly requires a schema plus migration change.
- Preserve customers and requests when adding invoices.
- ServiceTracker invoices use `paid`, not `status`, for payment state.
- Use `{ paid: true }` for boolean mode or `{ paid: 1 }` for a normal integer.
- Build invoice/customer joins through requestId when that is the declared
  relationship.

Do not modify secrets, .env files, package-lock.json, .git, node_modules, .next,
or virtual environments.
"""


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

            self._install_dependencies_if_needed(
                project_directory=project_directory,
                changed_files=changed_files,
            )

            validation_commands = self._validation_commands(
                project_directory
            )

            validation_result = self._run_validation(
                project_directory=project_directory,
                commands=validation_commands,
            )

            repair_changes: list[AppliedFileChange] = []

            if not validation_result.successful:
                if validation_result.failure is None:
                    raise DeveloperAgentError(
                        "Validation failed without failure information."
                    )

                repair_changes, validation_result = (
                    self._repair_until_valid(
                        project=project,
                        task=task,
                        project_directory=project_directory,
                        file_service=file_service,
                        validation_commands=validation_commands,
                        initial_failure=validation_result.failure,
                    )
                )

            changed_files.extend(repair_changes)

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
            task.repair_attempts = self.settings.max_repair_attempts
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

        system_prompt = DEVELOPMENT_SYSTEM_PROMPT

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

OUTPUT SIZE RULES:
- Return no more than 5 file operations.
- Keep each generated file focused and concise.
- Do not include files that do not require changes.
IMPORT VALIDATION RULES:
- Check every import in every generated file.
- Any missing local imported module must be created in the same response.
- For the current Next.js alias, "@/..." resolves inside the src directory.

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

    def _install_dependencies_if_needed(
        self,
        *,
        project_directory: Path,
        changed_files: list[AppliedFileChange],
    ) -> None:
        changed_relative_paths = {
            change.path.relative_to(project_directory).as_posix()
            for change in changed_files
        }

        if "package.json" not in changed_relative_paths:
            return

        npm = "npm.cmd" if os.name == "nt" else "npm"
        package_json_path = project_directory / "package.json"

        self._validate_npm_dependencies(
            project_directory=project_directory,
            package_json_path=package_json_path,
            npm=npm,
        )

        self.command_runner.run(
            [npm, "install"],
            working_directory=project_directory,
            timeout_seconds=1200,
        )        

    def _validate_npm_dependencies(
        self,
        *,
        project_directory: Path,
        package_json_path: Path,
        npm: str,
    ) -> None:
        try:
            package_data: dict[str, Any] = json.loads(
                package_json_path.read_text(encoding="utf-8")
            )
        except json.JSONDecodeError as exc:
            raise DeveloperAgentError(
                "Generated package.json contains invalid JSON."
            ) from exc

        dependency_groups = (
            "dependencies",
            "devDependencies",
            "peerDependencies",
        )

        for group_name in dependency_groups:
            dependencies = package_data.get(group_name, {})

            if not isinstance(dependencies, dict):
                raise DeveloperAgentError(
                    f"{group_name} must be a JSON object."
                )

            for package_name, requested_version in list(
                dependencies.items()
            ):
                if not isinstance(requested_version, str):
                    raise DeveloperAgentError(
                        f"Invalid version for npm package {package_name}."
                    )

                if self._should_skip_registry_validation(
                    requested_version
                ):
                    continue

                result = self.command_runner.run(
                    [
                        npm,
                        "view",
                        f"{package_name}@{requested_version}",
                        "version",
                        "--json",
                    ],
                    working_directory=project_directory,
                    timeout_seconds=120,
                    check=False,
                )

                if result.return_code == 0:
                    continue

                latest_result = self.command_runner.run(
                    [
                        npm,
                        "view",
                        package_name,
                        "version",
                        "--json",
                    ],
                    working_directory=project_directory,
                    timeout_seconds=120,
                    check=False,
                )

                if latest_result.return_code != 0:
                    raise DeveloperAgentError(
                        "Could not determine a valid npm version for "
                        f"{package_name}."
                    )

                try:
                    latest_version = json.loads(
                        latest_result.stdout
                    )
                except json.JSONDecodeError as exc:
                    raise DeveloperAgentError(
                        "npm returned an invalid version response for "
                        f"{package_name}."
                    ) from exc

                if (
                    not isinstance(latest_version, str)
                    or not latest_version.strip()
                ):
                    raise DeveloperAgentError(
                        "npm did not return a usable version for "
                        f"{package_name}."
                    )

                dependencies[package_name] = f"^{latest_version}"

        package_json_path.write_text(
            json.dumps(package_data, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _should_skip_registry_validation(
        requested_version: str,
    ) -> bool:
        prefixes = (
            "file:",
            "git:",
            "git+",
            "github:",
            "http:",
            "https:",
            "workspace:",
            "link:",
        )

        return requested_version.startswith(prefixes)

    def _generate_repair_plan(
        self,
        *,
        project: Project,
        task: ProjectTask,
        project_directory: Path,
        failure: ValidationFailure,
        attempt_number: int,
    ) -> RepairPlan:
        file_service = ProjectFileService(
            project_directory=project_directory
        )

        context = file_service.read_project_context(
            maximum_files=20,
            maximum_characters_per_file=14_000,
            maximum_total_characters=70_000,
        )

        formatted_context = "\n\n".join(
            f"--- FILE: {path} ---\n{content}"
            for path, content in context.items()
        )

        failure_output = failure.combined_output

        if len(failure_output) > 20_000:
            failure_output = failure_output[-20_000:]

        system_prompt = REPAIR_SYSTEM_PROMPT

        user_prompt = f"""
PROJECT:
{project.title}

TASK:
Task {task.position}: {task.title}

TASK DESCRIPTION:
{task.description}

REPAIR ATTEMPT:
{attempt_number}

FAILED COMMAND:
{" ".join(failure.command)}

EXIT CODE:
{failure.return_code}

VALIDATION OUTPUT:
{failure_output}

CURRENT REPOSITORY:
{formatted_context}

Return this exact JSON structure:

{{
  "diagnosis": "Exact technical cause of the failure",
  "summary": "How the repair resolves the failure",
  "files": [
    {{
      "path": "relative/path/to/file",
      "operation": "update",
      "content": "Complete corrected file contents",
      "explanation": "Why this change fixes the failure"
    }}
  ]
}}

Allowed operations:
- create
- update
- delete

Return no more than 6 file operations.
Keep every explanation under 300 characters.
Do not wrap the JSON in Markdown.
"""

        try:
            return self.deepseek.generate_structured(
                schema=RepairPlan,
                system_prompt=system_prompt.strip(),
                user_prompt=user_prompt.strip(),
                model=self.settings.deepseek_default_model,
            )
        except Exception as exc:
            raise DeveloperAgentError(
                f"DeepSeek could not create a repair plan: {exc}"
            ) from exc

    def _repair_until_valid(
        self,
        *,
        project: Project,
        task: ProjectTask,
        project_directory: Path,
        file_service: ProjectFileService,
        validation_commands: list[list[str]],
        initial_failure: ValidationFailure,
    ) -> tuple[list[AppliedFileChange], ValidationResult]:
        all_repair_changes: list[AppliedFileChange] = []
        current_failure = initial_failure

        for attempt_number in range(
            1,
            self.settings.max_repair_attempts + 1,
        ):
            repair_plan = self._generate_repair_plan(
                project=project,
                task=task,
                project_directory=project_directory,
                failure=current_failure,
                attempt_number=attempt_number,
            )

            repair_changes = file_service.apply_changes(
                repair_plan.files
            )

            all_repair_changes.extend(repair_changes)

            self._install_dependencies_if_needed(
                project_directory=project_directory,
                changed_files=repair_changes,
            )

            validation_result = self._run_validation(
                project_directory=project_directory,
                commands=validation_commands,
            )

            if validation_result.successful:
                return all_repair_changes, validation_result

            if validation_result.failure is None:
                raise DeveloperAgentError(
                    "Validation failed without returning failure details."
                )

            current_failure = validation_result.failure

        raise DeveloperAgentError(
            "Validation still failed after "
            f"{self.settings.max_repair_attempts} repair attempts.\n\n"
            f"Last failed command: {' '.join(current_failure.command)}\n\n"
            f"{current_failure.combined_output}"
        )

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
                commands.append(
                    [
                        npm,
                        "test",
                        "--",
                        "--run",
                        "--passWithNoTests",
                    ]
                )

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
    ) -> ValidationResult:
        completed_commands = []

        for command in commands:
            result = self.command_runner.run(
                command,
                working_directory=project_directory,
                timeout_seconds=1200,
                check=False,
            )

            completed_commands.append(result)

            if result.return_code != 0:
                return ValidationResult(
                    successful=False,
                    completed_commands=completed_commands,
                    failure=ValidationFailure(
                        command=result.command,
                        return_code=result.return_code,
                        stdout=result.stdout,
                        stderr=result.stderr,
                    ),
                )

        return ValidationResult(
            successful=True,
            completed_commands=completed_commands,
        )

    def _ensure_clean_git_worktree(
        self,
        project_directory: Path,
    ) -> None:
        result = self.command_runner.run(
            ["git", "status", "--porcelain"],
            working_directory=project_directory,
        )

        changed_lines = [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip()
        ]

        allowed_changes = {
            "M PROJECTFORGE_TASK.md",
            "M  PROJECTFORGE_TASK.md",
            " M PROJECTFORGE_TASK.md",
        }

        unexpected_changes = [
            line
            for line in changed_lines
            if line not in allowed_changes
        ]

        if unexpected_changes:
            formatted_changes = "\n".join(unexpected_changes)

            raise DeveloperAgentError(
                "Generated project contains unexpected uncommitted changes:\n"
                f"{formatted_changes}"
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

        seen_changes: set[tuple[str, str]] = set()

        for change in plan.files:
            key = (change.path, change.operation.value)

            if key in seen_changes:
                continue

            seen_changes.add(key)
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