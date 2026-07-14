import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.state import ProjectStatus, TaskStatus
from app.database.models import Project, ProjectTask
from app.schemas.project_plan import ProjectPlan
from app.services.deepseek import DeepSeekService
from app.services.knowledge_vault import KnowledgeVaultService
from app.utils.slug import create_slug


class PlannerAgentError(RuntimeError):
    """Raised when the planner cannot safely create a project."""


@dataclass
class PlannerResult:
    project: Project
    plan: ProjectPlan
    project_directory: Path
    knowledge_vault_directory: Path


class PlannerAgent:
    def __init__(
        self,
        *,
        deepseek_service: DeepSeekService | None = None,
        knowledge_vault_service: KnowledgeVaultService | None = None,
    ) -> None:
        self.settings = get_settings()
        self.deepseek = deepseek_service or DeepSeekService()
        self.knowledge_vault = (
            knowledge_vault_service or KnowledgeVaultService()
        )

    def create_project(
        self,
        *,
        db: Session,
        problem: str,
        preferred_project_size: str = "small",
    ) -> PlannerResult:
        cleaned_problem = problem.strip()

        if len(cleaned_problem) < 20:
            raise PlannerAgentError(
                "The problem description must contain at least 20 characters."
            )

        active_project = db.scalar(
            select(Project).where(Project.is_active.is_(True))
        )

        if active_project:
            raise PlannerAgentError(
                "An active project already exists. Complete or archive it "
                "before creating another project."
            )

        plan = self._generate_plan(
            problem=cleaned_problem,
            preferred_project_size=preferred_project_size,
        )

        slug = self._create_unique_slug(db=db, title=plan.title)

        project_directory = self._create_generated_project_directory(
            slug=slug,
            plan=plan,
        )

        knowledge_vault_directory = (
            self.knowledge_vault.create_project_documents(
                plan=plan,
                slug=slug,
            )
        )

        project = self._save_project(
            db=db,
            plan=plan,
            slug=slug,
            project_directory=project_directory,
            knowledge_vault_directory=knowledge_vault_directory,
        )

        return PlannerResult(
            project=project,
            plan=plan,
            project_directory=project_directory,
            knowledge_vault_directory=knowledge_vault_directory,
        )

    def _generate_plan(
        self,
        *,
        problem: str,
        preferred_project_size: str,
    ) -> ProjectPlan:
        system_prompt = """
You are the senior product manager and software architect inside ProjectForge,
an autonomous software-development system.

Convert a real-world problem into a practical, complete, portfolio-quality
software project.

The project must:
- solve a clear real-world problem;
- be achievable by one autonomous coding agent;
- avoid medical, legal, financial-advice, weapons, gambling, or high-risk uses;
- avoid paid APIs unless absolutely necessary;
- use free or inexpensive open-source technology;
- have a focused MVP;
- have meaningful daily development tasks;
- include testing, documentation, and deployment work;
- avoid unnecessary microservices;
- avoid unrealistic enterprise complexity.

Preferred generated-project stacks:

For full-stack web projects:
- Next.js
- TypeScript
- Tailwind CSS
- shadcn/ui
- Drizzle ORM
- SQLite locally
- PostgreSQL only when deployment requires it
- Zod
- Vitest
- Playwright

For Python API projects:
- FastAPI
- Pydantic
- SQLAlchemy
- SQLite or PostgreSQL
- pytest
- Ruff

For command-line or automation projects:
- Python
- Typer
- Rich
- pytest
- Ruff

Return every field required by the supplied JSON structure.
Tasks must be sequential and individually implementable.
Each task should normally require one day.
"""

        user_prompt = f"""
Create a {preferred_project_size} software project for this real-world problem:

{problem}

Return this exact JSON structure:

{{
  "title": "Project title",
  "short_name": "Short internal name",
  "problem_statement": "Detailed problem description",
  "solution_summary": "Detailed solution description",
  "target_users": ["User group"],
  "project_size": "small",
  "estimated_duration_days": 10,
  "primary_features": ["Feature one", "Feature two", "Feature three"],
  "excluded_features": ["Feature intentionally excluded"],
  "frontend_stack": ["Technology"],
  "backend_stack": ["Technology"],
  "database_stack": ["Technology"],
  "testing_stack": ["Technology"],
  "architecture_summary": "Detailed architecture explanation",
  "security_considerations": ["Security consideration"],
  "deployment_strategy": "Deployment explanation",
  "tasks": [
    {{
      "position": 1,
      "title": "Task title",
      "description": "Detailed task description",
      "task_type": "planning",
      "acceptance_criteria": [
        "Measurable requirement"
      ],
      "estimated_days": 1
    }}
  ]
}}

Allowed project_size values:
- small
- medium

Allowed task_type values:
- research
- planning
- setup
- feature
- bug_fix
- testing
- documentation
- deployment

For a small project, produce approximately 8 to 14 tasks.
For a medium project, produce approximately 15 to 30 tasks.
"""

        try:
            return self.deepseek.generate_structured(
                schema=ProjectPlan,
                system_prompt=system_prompt.strip(),
                user_prompt=user_prompt.strip(),
                model=self.settings.deepseek_reasoning_model,
            )
        except Exception as exc:
            raise PlannerAgentError(
                f"DeepSeek could not produce a valid project plan: {exc}"
            ) from exc

    def _create_unique_slug(
        self,
        *,
        db: Session,
        title: str,
    ) -> str:
        base_slug = create_slug(title)
        slug = base_slug
        suffix = 2

        while db.scalar(
            select(Project.id).where(Project.slug == slug)
        ):
            slug = f"{base_slug}-{suffix}"
            suffix += 1

        return slug

    def _create_generated_project_directory(
        self,
        *,
        slug: str,
        plan: ProjectPlan,
    ) -> Path:
        directory = self.settings.generated_projects_dir / slug
        directory.mkdir(parents=True, exist_ok=False)

        project_metadata = {
            "title": plan.title,
            "slug": slug,
            "status": ProjectStatus.PLANNING.value,
            "estimated_duration_days": plan.estimated_duration_days,
            "current_task_position": 1,
        }

        metadata_path = directory / ".projectforge.json"
        metadata_path.write_text(
            json.dumps(project_metadata, indent=2),
            encoding="utf-8",
        )

        readme_path = directory / "README.md"
        readme_path.write_text(
            self._create_initial_readme(plan),
            encoding="utf-8",
        )

        return directory

    def _save_project(
        self,
        *,
        db: Session,
        plan: ProjectPlan,
        slug: str,
        project_directory: Path,
        knowledge_vault_directory: Path,
    ) -> Project:
        stack = {
            "frontend": plan.frontend_stack,
            "backend": plan.backend_stack,
            "database": plan.database_stack,
            "testing": plan.testing_stack,
        }

        project = Project(
            name=plan.short_name,
            slug=slug,
            title=plan.title,
            problem_statement=plan.problem_statement,
            solution_summary=plan.solution_summary,
            target_users=json.dumps(plan.target_users),
            project_size=plan.project_size.value,
            status=ProjectStatus.PLANNING.value,
            technology_stack=json.dumps(stack),
            local_path=str(project_directory.resolve()),
            knowledge_vault_path=str(
                knowledge_vault_directory.resolve()
            ),
            current_phase="Planning",
            is_active=True,
        )

        db.add(project)
        db.flush()

        for task in plan.tasks:
            db.add(
                ProjectTask(
                    project_id=project.id,
                    position=task.position,
                    title=task.title,
                    description=task.description,
                    task_type=task.task_type.value,
                    status=TaskStatus.PENDING.value,
                    acceptance_criteria=json.dumps(
                        task.acceptance_criteria
                    ),
                    estimated_days=task.estimated_days,
                )
            )

        try:
            db.commit()
            db.refresh(project)
        except Exception:
            db.rollback()
            raise

        return project

    @staticmethod
    def _create_initial_readme(plan: ProjectPlan) -> str:
        features = "\n".join(
            f"- {feature}" for feature in plan.primary_features
        )

        stack = (
            plan.frontend_stack
            + plan.backend_stack
            + plan.database_stack
            + plan.testing_stack
        )

        stack_text = "\n".join(
            f"- {technology}" for technology in dict.fromkeys(stack)
        )

        return f"""# {plan.title}

## Problem

{plan.problem_statement}

## Solution

{plan.solution_summary}

## Planned Features

{features}

## Technology Stack

{stack_text}

## Development Status

Planning in progress.
"""