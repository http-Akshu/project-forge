import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence


class CommandExecutionError(RuntimeError):
    """Raised when a command exits unsuccessfully."""


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    return_code: int
    stdout: str
    stderr: str
    working_directory: Path


class CommandRunner:
    """Safely execute approved commands without invoking a shell."""

    def __init__(
        self,
        *,
        timeout_seconds: int = 900,
    ) -> None:
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        command: Sequence[str],
        *,
        working_directory: Path,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: int | None = None,
        check: bool = True,
    ) -> CommandResult:
        if not command:
            raise ValueError("Command cannot be empty.")

        resolved_directory = working_directory.resolve()

        if not resolved_directory.exists():
            raise CommandExecutionError(
                f"Working directory does not exist: {resolved_directory}"
            )

        process_environment = os.environ.copy()

        if environment:
            process_environment.update(environment)

        command_list = [str(part) for part in command]

        try:
            completed_process = subprocess.run(
                command_list,
                cwd=resolved_directory,
                env=process_environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds or self.timeout_seconds,
                check=False,
                shell=False,
            )
        except FileNotFoundError as exc:
            raise CommandExecutionError(
                f"Command was not found: {command_list[0]}"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise CommandExecutionError(
                "Command exceeded the configured timeout: "
                f"{' '.join(command_list)}"
            ) from exc
        except OSError as exc:
            raise CommandExecutionError(
                f"Could not execute command: {exc}"
            ) from exc

        result = CommandResult(
            command=command_list,
            return_code=completed_process.returncode,
            stdout=completed_process.stdout.strip(),
            stderr=completed_process.stderr.strip(),
            working_directory=resolved_directory,
        )

        if check and result.return_code != 0:
            error_output = result.stderr or result.stdout or "No output"

            raise CommandExecutionError(
                "Command failed with exit code "
                f"{result.return_code}:\n"
                f"{' '.join(command_list)}\n\n"
                f"{error_output}"
            )

        return result

@dataclass(frozen=True)
class ValidationFailure:
    command: list[str]
    return_code: int
    stdout: str
    stderr: str

    @property
    def combined_output(self) -> str:
        parts = [
            part.strip()
            for part in (self.stdout, self.stderr)
            if part.strip()
        ]

        return "\n\n".join(parts)


@dataclass(frozen=True)
class ValidationResult:
    successful: bool
    completed_commands: list[CommandResult]
    failure: ValidationFailure | None = None