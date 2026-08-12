"""Small JSON Schema subset validator used by dependency-free contract checks."""

from __future__ import annotations

import re
from typing import Any


class SchemaViolation(AssertionError):
    pass


def validate(instance: Any, schema: dict[str, Any], path: str = "$") -> None:
    if "oneOf" in schema:
        matches: list[int] = []
        failures: list[str] = []
        for index, branch in enumerate(schema["oneOf"]):
            try:
                validate(instance, branch, path)
            except SchemaViolation as exc:
                failures.append(f"branch {index}: {exc}")
            else:
                matches.append(index)
        if len(matches) != 1:
            detail = (
                f"matching branches {matches}"
                if matches
                else "; ".join(failures)
            )
            raise SchemaViolation(
                f"{path}: oneOf expected exactly one matching branch, matched {len(matches)} ({detail})"
            )

    expected_type = schema.get("type")
    if expected_type is not None:
        predicates = {
            "object": lambda value: isinstance(value, dict),
            "array": lambda value: isinstance(value, list),
            "string": lambda value: isinstance(value, str),
            "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
            "boolean": lambda value: isinstance(value, bool),
        }
        if expected_type not in predicates or not predicates[expected_type](instance):
            raise SchemaViolation(f"{path}: expected {expected_type}")

    if "const" in schema and instance != schema["const"]:
        raise SchemaViolation(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        raise SchemaViolation(f"{path}: value is not in enum")

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            raise SchemaViolation(f"{path}: string is too short")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise SchemaViolation(f"{path}: string is too long")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            raise SchemaViolation(f"{path}: string does not match pattern")

    if isinstance(instance, int) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise SchemaViolation(f"{path}: integer is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise SchemaViolation(f"{path}: integer is above maximum")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in instance]
        if missing:
            raise SchemaViolation(f"{path}: missing required properties {missing}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extras = sorted(set(instance) - set(properties))
            if extras:
                raise SchemaViolation(f"{path}: unexpected properties {extras}")
        for key, value in instance.items():
            if key in properties:
                validate(value, properties[key], f"{path}.{key}")

    if isinstance(instance, list) and "items" in schema:
        for index, item in enumerate(instance):
            validate(item, schema["items"], f"{path}[{index}]")
