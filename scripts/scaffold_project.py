import argparse

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.agents.scaffolder import (
    ScaffolderAgent,
    ScaffolderAgentError,
)
from app.database.init_db import initialize_database
from app.database.session import SessionLocal


console = Console()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scaffold the active ProjectForge project."
    )

    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Create files without installing project dependencies.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_arguments()
    initialize_database()

    db = SessionLocal()

    try:
        console.print(
            Panel.fit(
                "Loading the active project and creating its codebase...",
                title="ProjectForge Scaffolder",
            )
        )

        scaffolder = ScaffolderAgent()

        result = scaffolder.scaffold_active_project(
            db=db,
            skip_install=args.skip_install,
        )

        details = Table(title="Scaffolding Result")
        details.add_column("Property", style="bold")
        details.add_column("Value")

        details.add_row("Project", result.project.title)
        details.add_row("Template", result.template.value)
        details.add_row(
            "Directory",
            str(result.project_directory),
        )
        details.add_row(
            "Status",
            result.project.status,
        )

        if result.first_task:
            details.add_row(
                "First task",
                (
                    f"{result.first_task.position}. "
                    f"{result.first_task.title}"
                ),
            )

        console.print(details)

        command_table = Table(title="Commands Executed")
        command_table.add_column("#", justify="right")
        command_table.add_column("Command")

        for index, command in enumerate(
            result.commands_executed,
            start=1,
        ):
            command_table.add_row(
                str(index),
                " ".join(command),
            )

        console.print(command_table)

        console.print(
            Panel.fit(
                "The generated project is ready for its first task.",
                title="Scaffolding Complete",
            )
        )

    except ScaffolderAgentError as exc:
        console.print(
            Panel.fit(
                str(exc),
                title="Scaffolding Failed",
                border_style="red",
            )
        )
        raise SystemExit(1) from exc

    finally:
        db.close()


if __name__ == "__main__":
    main()