"""Send one request to Pi or Claude Code, with no tools, and return a JSON object."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any


ENGINES = ("pi", "claude")
_TIMEOUT_SECONDS = 900


def check_engine(engine: str) -> None:
    """Fail before any work starts when the engine CLI is not installed."""
    if engine not in ENGINES:
        raise ValueError(f"unknown engine: {engine}")
    if shutil.which(engine) is None:
        name = "Pi CLI" if engine == "pi" else "Claude Code CLI (claude)"
        raise RuntimeError(f"{name} was not found")


def ask(
    engine: str,
    *,
    system: str,
    text: str,
    schema: dict[str, Any],
    images: list[tuple[str, Path]] | None = None,
    model: str | None = None,
    cwd: Path,
) -> dict[str, Any]:
    """Run one request. *images* is a list of (label, JPEG path) pairs."""
    images = images or []
    if engine == "claude":
        result = _ask_claude(system, text, schema, images, model, cwd)
    elif engine == "pi":
        result = _ask_pi(system, text, schema, images, model, cwd)
    else:
        raise ValueError(f"unknown engine: {engine}")
    problem = _schema_problem(result, schema, "$")
    if problem:
        raise RuntimeError(f"{engine} returned JSON that does not match the schema: {problem}")
    return result


def _schema_problem(value: Any, schema: dict[str, Any], path: str) -> str | None:
    """Check the JSON Schema subset that the pipeline uses. Return the first problem, or None."""
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            return f"{path} is not an object"
        for key in schema.get("required", []):
            if key not in value:
                return f"{path}.{key} is missing"
        for key, child in schema.get("properties", {}).items():
            if key in value:
                problem = _schema_problem(value[key], child, f"{path}.{key}")
                if problem:
                    return problem
    elif kind == "array":
        if not isinstance(value, list):
            return f"{path} is not an array"
        if len(value) < schema.get("minItems", 0):
            return f"{path} has too few items"
        for index, item in enumerate(value):
            problem = _schema_problem(item, schema.get("items", {}), f"{path}[{index}]")
            if problem:
                return problem
    elif kind == "string" and not isinstance(value, str):
        return f"{path} is not a string"
    elif kind == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
        return f"{path} is not an integer"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path} is not one of {schema['enum']}"
    return None


def _run(command: list[str], stdin: str, cwd: Path, name: str, environment: dict[str, str] | None = None) -> str:
    try:
        result = subprocess.run(
            command,
            input=stdin,
            text=True,
            capture_output=True,
            cwd=cwd,
            env=environment,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as error:
        raise RuntimeError(f"{name} was not found") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"{name} timed out while generating notes") from error
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{name} failed with exit code {result.returncode}: {detail}")
    return result.stdout


def _ask_claude(
    system: str,
    text: str,
    schema: dict[str, Any],
    images: list[tuple[str, Path]],
    model: str | None,
    cwd: Path,
) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for label, path in images:
        content.append({"type": "text", "text": label})
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
    message = {"type": "user", "message": {"role": "user", "content": content}}

    # --safe-mode skips CLAUDE.md, skills, plugins, hooks and MCP servers but keeps the user's sign-in.
    command = [
        "claude",
        "-p",
        "--safe-mode",
        "--tools",
        "",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--json-schema",
        json.dumps(schema),
        "--system-prompt",
        system,
    ]
    if model:
        command.extend(("--model", model))
    stdout = _run(command, json.dumps(message, ensure_ascii=False) + "\n", cwd, "Claude Code")

    final: dict[str, Any] | None = None
    for line in stdout.split("\n"):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            final = event
    if final is None:
        raise RuntimeError("Claude Code returned no result")
    if final.get("subtype") != "success" or final.get("is_error"):
        raise RuntimeError(f"Claude Code did not complete the request: {final.get('result') or final.get('subtype')}")
    output = final.get("structured_output")
    if not isinstance(output, dict):
        raise RuntimeError("Claude Code returned no structured output")
    return output


def _ask_pi(
    system: str,
    text: str,
    schema: dict[str, Any],
    images: list[tuple[str, Path]],
    model: str | None,
    cwd: Path,
) -> dict[str, Any]:
    system = (
        f"{system}\n\nReturn exactly one JSON object and nothing else. "
        f"It must match this JSON Schema:\n{json.dumps(schema, ensure_ascii=False)}"
    )
    instruction = "Follow the system prompt. The data is in this message."
    if images:
        listing = "\n".join(f"{index}. {label}" for index, (label, _) in enumerate(images, start=1))
        instruction += f"\nThe attached images are, in order:\n{listing}"

    command = [
        "pi",
        "--mode",
        "json",
        "-p",
        "--no-session",
        "--no-tools",
        "--no-extensions",
        "--no-skills",
        "--no-prompt-templates",
        "--no-context-files",
        "--no-themes",
        "--no-approve",
        "--system-prompt",
        system,
    ]
    if model:
        command.extend(("--model", model))
    elif os.environ.get("PI_PROVIDER") and os.environ.get("PI_MODEL"):
        command.extend(("--provider", os.environ["PI_PROVIDER"], "--model", os.environ["PI_MODEL"]))
    command.extend(f"@{path}" for _, path in images)
    command.append(instruction)

    environment = os.environ.copy()
    environment["PI_SKIP_VERSION_CHECK"] = "1"
    environment["PI_TELEMETRY"] = "0"
    stdout = _run(command, text, cwd, "Pi", environment)
    return _json_object(_pi_text(stdout))


def _pi_text(stdout: str) -> str:
    message: dict[str, Any] | None = None
    for line in stdout.split("\n"):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "message_end":
            continue
        candidate = event.get("message")
        if isinstance(candidate, dict) and candidate.get("role") == "assistant":
            message = candidate

    if message is None:
        raise RuntimeError("Pi returned no complete assistant message")
    if message.get("stopReason") != "stop":
        raise RuntimeError(f"Pi did not complete the notes: {message.get('stopReason', 'unknown')}")
    content = message.get("content")
    if not isinstance(content, list):
        raise RuntimeError("Pi returned invalid note content")
    return "".join(
        block["text"]
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
    ).strip()


def _json_object(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError("Pi returned no JSON object")
    try:
        value = json.loads(text[start : end + 1])
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Pi returned invalid JSON: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError("Pi returned JSON that is not an object")
    return value
