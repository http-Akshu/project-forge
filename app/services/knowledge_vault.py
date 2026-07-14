from pathlib import Path

from app.core.config import get_settings
from app.schemas.project_plan import ProjectPlan


class KnowledgeVaultService:
    def __init__(self) -> None:
        self.settings = get_settings()

    def create_project_documents(
        self,
        *,
        plan: ProjectPlan,
        slug: str,
    ) -> Path:
        project_directory = self.settings.knowledge_vault_dir / slug
        project_directory.mkdir(parents=True, exist_ok=True)

        self._write_project_explanation(
            directory=project_directory,
            plan=plan,
        )

        self._write_architecture(
            directory=project_directory,
            plan=plan,
        )

        self._write_development_roadmap(
            directory=project_directory,
            plan=plan,
        )

        self._write_learning_guide(
            directory=project_directory,
            plan=plan,
        )

        self._write_daily_log(directory=project_directory)

        return project_directory

    def _write_project_explanation(
        self,
        *,
        directory: Path,
        plan: ProjectPlan,
    ) -> None:
        primary_features = "\n".join(
            f"- {feature}" for feature in plan.primary_features
        )

        excluded_features = (
            "\n".join(
                f"- {feature}" for feature in plan.excluded_features
            )
            or "- None specified"
        )

        target_users = "\n".join(
            f"- {user}" for user in plan.target_users
        )

        content = f"""# {plan.title}

## Project Overview

**Internal name:** {plan.short_name}

**Project size:** {plan.project_size.value}

**Estimated development duration:** {plan.estimated_duration_days} days

## Real-World Problem

{plan.problem_statement}

## Proposed Solution

{plan.solution_summary}

## Target Users

{target_users}

## Primary Features

{primary_features}

## Features Excluded From the MVP

{excluded_features}

## Why This Project Was Selected

This project addresses a clearly defined real-world problem while remaining
small enough to be developed, tested, documented, and deployed as a complete
portfolio project.

## Expected Outcome

The final application should provide a usable solution for its target users,
include automated testing, and demonstrate practical software-engineering
decisions rather than functioning only as a demonstration.
"""

        self._write_file(
            directory / "PROJECT_EXPLANATION.md",
            content,
        )

    def _write_architecture(
        self,
        *,
        directory: Path,
        plan: ProjectPlan,
    ) -> None:
        frontend = self._markdown_list(plan.frontend_stack)
        backend = self._markdown_list(plan.backend_stack)
        database = self._markdown_list(plan.database_stack)
        testing = self._markdown_list(plan.testing_stack)
        security = self._markdown_list(plan.security_considerations)

        content = f"""# Architecture

## Architecture Summary

{plan.architecture_summary}

## Frontend

{frontend}

## Backend

{backend}

## Database

{database}

## Testing

{testing}

## Security Considerations

{security}

## Deployment Strategy

{plan.deployment_strategy}
"""

        self._write_file(directory / "ARCHITECTURE.md", content)

    def _write_development_roadmap(
        self,
        *,
        directory: Path,
        plan: ProjectPlan,
    ) -> None:
        task_sections: list[str] = []

        for task in plan.tasks:
            criteria = "\n".join(
                f"- [ ] {criterion}"
                for criterion in task.acceptance_criteria
            )

            task_sections.append(
                f"""## Task {task.position}: {task.title}

**Type:** {task.task_type.value}

**Estimated time:** {task.estimated_days} day(s)

### Description

{task.description}

### Acceptance Criteria

{criteria}
"""
            )

        content = "# Development Roadmap\n\n" + "\n".join(task_sections)

        self._write_file(
            directory / "DEVELOPMENT_ROADMAP.md",
            content,
        )

    def _write_learning_guide(
        self,
        *,
        directory: Path,
        plan: ProjectPlan,
    ) -> None:
        content = f"""# Learning Guide

## What This Project Teaches

- How a real-world problem is converted into software requirements
- How the application architecture supports the requirements
- How frontend, backend, database, and testing layers interact
- How individual features are divided into manageable development tasks
- How automated tests protect important behavior
- How the application is prepared for deployment

## Important Areas to Understand

1. The problem being solved
2. The target users
3. The application architecture
4. The database structure
5. The API or server-side flow
6. Validation and error handling
7. Authentication and authorization, when applicable
8. Testing strategy
9. Deployment approach
10. Major technical decisions

## Architecture Summary

{plan.architecture_summary}

## Interview Preparation

Be prepared to explain:

- Why this project was selected
- Why the chosen technology stack was appropriate
- How data moves through the application
- How errors are handled
- How important features are tested
- What compromises were made to keep the MVP manageable
- What could be added in a future version
"""

        self._write_file(
            directory / "LEARNING_GUIDE.md",
            content,
        )

    def _write_daily_log(self, *, directory: Path) -> None:
        content = """# Daily Development Log

This file will be updated automatically after every successful development run.

"""

        self._write_file(directory / "DAILY_LOG.md", content)

    @staticmethod
    def _markdown_list(values: list[str]) -> str:
        if not values:
            return "- Not required for this project"

        return "\n".join(f"- {value}" for value in values)

    @staticmethod
    def _write_file(path: Path, content: str) -> None:
        path.write_text(
            content.strip() + "\n",
            encoding="utf-8",
        )