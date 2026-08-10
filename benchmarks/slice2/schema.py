"""Small dependency-free JSON Schema validator for committed Slice 2 evidence."""

from __future__ import annotations

import math
import re
from typing import Any


class SchemaViolation(AssertionError):
    """Raised when committed benchmark evidence violates its schema."""


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate(instance: Any, schema: dict[str, Any], path: str = "$") -> None:
    expected_type = schema.get("type")
    if expected_type is not None:
        predicates = {
            "object": lambda value: isinstance(value, dict),
            "array": lambda value: isinstance(value, list),
            "string": lambda value: isinstance(value, str),
            "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
            "number": _is_number,
            "boolean": lambda value: isinstance(value, bool),
            "null": lambda value: value is None,
        }
        expected_types = [expected_type] if isinstance(expected_type, str) else expected_type
        if not any(kind in predicates and predicates[kind](instance) for kind in expected_types):
            raise SchemaViolation(f"{path}: expected {expected_types!r}")

    if "const" in schema and instance != schema["const"]:
        raise SchemaViolation(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        raise SchemaViolation(f"{path}: value is not in enum")

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            raise SchemaViolation(f"{path}: string is too short")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise SchemaViolation(f"{path}: string is too long")
        if "pattern" in schema and re.fullmatch(schema["pattern"], instance) is None:
            raise SchemaViolation(f"{path}: string does not match pattern")

    if _is_number(instance):
        if "minimum" in schema and instance < schema["minimum"]:
            raise SchemaViolation(f"{path}: number is below minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise SchemaViolation(f"{path}: number is above maximum")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            raise SchemaViolation(f"{path}: number is not above exclusive minimum")

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

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            raise SchemaViolation(f"{path}: too few items")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise SchemaViolation(f"{path}: too many items")
        if schema.get("uniqueItems"):
            representations = [repr(item) for item in instance]
            if len(representations) != len(set(representations)):
                raise SchemaViolation(f"{path}: items are not unique")
        if "items" in schema:
            for index, item in enumerate(instance):
                validate(item, schema["items"], f"{path}[{index}]")
