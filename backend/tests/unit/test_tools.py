"""Unit tests for the agent tool layer: validation, safety and the calculator."""

from __future__ import annotations

import uuid

import pytest

from app.ai.agent.tools import (
    CalculatorTool,
    ToolContext,
    ToolRegistry,
    build_default_registry,
    get_tool_registry,
    safe_eval,
)
from app.ai.types import ToolSpec
from app.core.errors import ValidationError


# --------------------------------------------------------------------------- #
# safe expression evaluation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("1+1", 2.0),
        ("(12+8)*3/2", 30.0),
        ("2 ** 10", 1024.0),
        ("17 % 5", 2.0),
        ("17 // 5", 3.0),
        ("-5 + 3", -2.0),
        ("+4 * 2", 8.0),
    ],
)
def test_safe_eval_computes_arithmetic(expression: str, expected: float) -> None:
    assert safe_eval(expression) == pytest.approx(expected)


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('ls')",
        "open('/etc/passwd').read()",
        "1 if True else 2",
        "[x for x in range(3)]",
        "lambda: 1",
        "a + b",
        "1 / 0",
        "2 ** 1000",
        "'a' * 3",
    ],
)
def test_safe_eval_rejects_dangerous_input(expression: str) -> None:
    with pytest.raises(ValidationError):
        safe_eval(expression)


def test_safe_eval_rejects_overlong_input() -> None:
    with pytest.raises(ValidationError):
        safe_eval("1+" * 200 + "1")


# --------------------------------------------------------------------------- #
# tool argument validation
# --------------------------------------------------------------------------- #
class _EchoTool(CalculatorTool):
    spec = ToolSpec(
        name="echo",
        description="test tool",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "count": {"type": "integer", "default": 3},
                "items": {"type": "array"},
                "score": {"type": "number"},
            },
            "required": ["text"],
        },
    )


def test_validate_requires_mandatory_parameters() -> None:
    with pytest.raises(ValidationError) as exc:
        _EchoTool().validate({})
    assert exc.value.details["missing"] == "text"


def test_validate_coerces_stringified_numbers() -> None:
    cleaned = _EchoTool().validate({"text": "hi", "count": "7", "score": "0.5"})
    assert cleaned["count"] == 7
    assert cleaned["score"] == 0.5


def test_validate_applies_defaults() -> None:
    assert _EchoTool().validate({"text": "hi"})["count"] == 3


def test_validate_parses_json_array_string() -> None:
    assert _EchoTool().validate({"text": "hi", "items": '["a","b"]'})["items"] == ["a", "b"]


def test_validate_ignores_hallucinated_parameters() -> None:
    cleaned = _EchoTool().validate({"text": "hi", "bogus": 1})
    assert "bogus" not in cleaned


def test_validate_rejects_wrong_type() -> None:
    with pytest.raises(ValidationError):
        _EchoTool().validate({"text": "hi", "count": "abc"})


# --------------------------------------------------------------------------- #
# registry
# --------------------------------------------------------------------------- #
def test_default_registry_exposes_the_documented_tools() -> None:
    registry = build_default_registry()
    expected = {
        "knowledge_search",
        "knowledge_document_read",
        "task_list",
        "project_list",
        "calculator",
        "task_planner",
        "task_create",
        "task_create_batch",
        "task_update",
        "note_create",
        "knowledge_link",
    }
    assert set(registry.names()) == expected


def test_registry_rejects_duplicate_registration() -> None:
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    with pytest.raises(ValueError):
        registry.register(CalculatorTool())


def test_registry_catalog_is_serialisable() -> None:
    catalog = get_tool_registry().catalog()
    assert len(catalog) == 11
    for entry in catalog:
        assert entry["name"]
        assert entry["description"]
        assert isinstance(entry["is_write"], bool)
        assert entry["parameters"]["type"] == "object"


def test_write_tools_are_marked() -> None:
    registry = get_tool_registry()
    write_names = {t["name"] for t in registry.catalog() if t["is_write"]}
    assert write_names == {
        "task_create",
        "task_create_batch",
        "task_update",
        "note_create",
        "knowledge_link",
    }


def test_read_only_specs_exclude_write_tools() -> None:
    specs = get_tool_registry().specs(include_write=False)
    names = {s.name for s in specs}
    assert "knowledge_search" in names
    assert "task_create" not in names


@pytest.mark.asyncio
async def test_unknown_tool_returns_error_not_exception() -> None:
    ctx = ToolContext(session=None, owner_id=uuid.uuid4())  # type: ignore[arg-type]
    result = await get_tool_registry().execute(ctx, "does_not_exist", {})
    assert result.ok is False
    assert "unknown_tool" in result.error


@pytest.mark.asyncio
async def test_calculator_tool_roundtrip() -> None:
    ctx = ToolContext(session=None, owner_id=uuid.uuid4())  # type: ignore[arg-type]
    result = await get_tool_registry().execute(ctx, "calculator", {"expression": "(3+4)*2"})
    assert result.ok is True
    assert result.data["result"] == 14.0
    assert result.latency_ms >= 0


@pytest.mark.asyncio
async def test_tool_errors_are_returned_as_results() -> None:
    ctx = ToolContext(session=None, owner_id=uuid.uuid4())  # type: ignore[arg-type]
    result = await get_tool_registry().execute(ctx, "calculator", {"expression": "1/0"})
    assert result.ok is False
    assert "validation_error" in result.error


# --------------------------------------------------------------------------- #
# write-operation budget
# --------------------------------------------------------------------------- #
def test_write_budget_is_enforced() -> None:
    ctx = ToolContext(session=None, owner_id=uuid.uuid4(), max_write_ops=2)  # type: ignore[arg-type]
    ctx.consume_write()
    ctx.consume_write()
    from app.core.errors import ToolExecutionError

    with pytest.raises(ToolExecutionError):
        ctx.consume_write()


def test_artifacts_are_recorded_per_kind() -> None:
    ctx = ToolContext(session=None, owner_id=uuid.uuid4())  # type: ignore[arg-type]
    ctx.register_artifact("tasks", "a")
    ctx.register_artifact("tasks", "b")
    ctx.register_artifact("notes", "c")
    assert ctx.artifacts == {"tasks": ["a", "b"], "notes": ["c"]}
