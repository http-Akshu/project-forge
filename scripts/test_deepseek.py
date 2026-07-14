from rich.console import Console
from rich.panel import Panel

from app.services.deepseek import DeepSeekService, DeepSeekServiceError


console = Console()


def main() -> None:
    service = DeepSeekService()

    try:
        models = service.list_models()

        console.print(
            Panel.fit(
                "\n".join(models),
                title="Available DeepSeek Models",
            )
        )

        result = service.generate_json(
            system_prompt=(
                "You are the planning component of an autonomous "
                "software-development agent."
            ),
            user_prompt=(
                "Return JSON with these fields: "
                "status, agent_name, and message. "
                "Set status to success, agent_name to ProjectForge, "
                "and write a short connection confirmation."
            ),
        )

        console.print(
            Panel.fit(
                str(result),
                title="DeepSeek Connection Successful",
            )
        )

    except DeepSeekServiceError as exc:
        console.print(
            Panel.fit(
                str(exc),
                title="DeepSeek Connection Failed",
            )
        )
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()