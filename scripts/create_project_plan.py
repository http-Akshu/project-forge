import argparse

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.agents.planner import PlannerAgent, PlannerAgentError
from app.database.init_db import initialize_database
from app.database.session import SessionLocal


console = Console()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a new ProjectForge project plan."
    )

    parser.add_argument(
        "--problem",
        required=True,
        help="The real-world problem the project should solve.",
    )

    parser.add_argument(
        "--size",
        choices=["small", "medium"],
        default="small",
        help="Preferred project size.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    initialize_database()

    db = SessionLocal()

    try:
        planner = PlannerAgent()

        console.print(
            Panel.fit(
                "Generating and validating the project plan...",
                title="ProjectForge Planner",
            )
        )

        result = planner.create_project(
            db=db,
            problem=args.problem,
            preferred_project_size=args.size,
        )

        table = Table(title=result.plan.title)
        table.add_column("Task", style="bold")
        table.add_column("Type")
        table.add_column("Days", justify="right")

        for task in result.plan.tasks:
            table.add_row(
                f"{task.position}. {task.title}",
                task.task_type.value,
                str(task.estimated_days),
            )

        console.print(table)

        console.print(
            Panel.fit(
                "\n".join(
                    [
                        f"Database project ID: {result.project.id}",
                        f"Slug: {result.project.slug}",
                        f"Project folder: {result.project_directory}",
                        (
                            "Private documentation: "
                            f"{result.knowledge_vault_directory}"
                        ),
                    ]
                ),
                title="Project Created Successfully",
            )
        )

    except PlannerAgentError as exc:
        console.print(
            Panel.fit(
                str(exc),
                title="Planner Failed",
                border_style="red",
            )
        )
        raise SystemExit(1) from exc

    finally:
        db.close()


if __name__ == "__main__":
    main()