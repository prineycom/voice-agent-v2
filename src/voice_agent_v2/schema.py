"""Small JSON Schema subset validator used by dependency-free contract checks."""

from __future__ import annotations

import math
import re
from typing import Any


class SchemaViolation(AssertionError):
    pass


def _json_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is bool and type(right) is bool and left == right
    if isinstance(left, (int, float)) or isinstance(right, (int, float)):
        return (
            (type(left) is int or type(left) is float and math.isfinite(left))
            and (type(right) is int or type(right) is float and math.isfinite(right))
            and left == right
        )
    if isinstance(left, str) or isinstance(right, str):
        return type(left) is str and type(right) is str and left == right
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, list) or isinstance(right, list):
        return (
            isinstance(left, list)
            and isinstance(right, list)
            and len(left) == len(right)
            and all(_json_equal(a, b) for a, b in zip(left, right))
        )
    if isinstance(left, dict) or isinstance(right, dict):
        return (
            isinstance(left, dict)
            and isinstance(right, dict)
            and set(left) == set(right)
            and all(_json_equal(left[key], right[key]) for key in left)
        )
    return False


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
            "number": lambda value: (
                type(value) is int
                or type(value) is float and math.isfinite(value)
            ),
            "boolean": lambda value: isinstance(value, bool),
            "null": lambda value: value is None,
        }
        if expected_type not in predicates or not predicates[expected_type](instance):
            raise SchemaViolation(f"{path}: expected {expected_type}")

    if "const" in schema and not _json_equal(instance, schema["const"]):
        raise SchemaViolation(f"{path}: expected constant {schema['const']!r}")
    if "enum" in schema and not any(
        _json_equal(instance, candidate) for candidate in schema["enum"]
    ):
        raise SchemaViolation(f"{path}: value is not in enum")

    if isinstance(instance, str):
        if len(instance) < schema.get("minLength", 0):
            raise SchemaViolation(f"{path}: string is too short")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            raise SchemaViolation(f"{path}: string is too long")
        if "pattern" in schema and re.search(schema["pattern"], instance) is None:
            raise SchemaViolation(f"{path}: string does not match pattern")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            raise SchemaViolation(f"{path}: number is below minimum")
        if "exclusiveMinimum" in schema and instance <= schema["exclusiveMinimum"]:
            raise SchemaViolation(f"{path}: number is not above exclusive minimum")
        if "maximum" in schema and instance > schema["maximum"]:
            raise SchemaViolation(f"{path}: number is above maximum")
        if "multipleOf" in schema:
            divisor = schema["multipleOf"]
            quotient = instance / divisor
            if not math.isclose(quotient, round(quotient), rel_tol=0.0, abs_tol=1e-12):
                raise SchemaViolation(f"{path}: number is not a multiple")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in instance]
        if missing:
            raise SchemaViolation(f"{path}: missing required properties {missing}")
        if "maxProperties" in schema and len(instance) > schema["maxProperties"]:
            raise SchemaViolation(f"{path}: object has too many properties")
        if "propertyNames" in schema:
            for key in instance:
                validate(key, schema["propertyNames"], f"{path}.{key}")
        properties = schema.get("properties", {})
        pattern_properties = schema.get("patternProperties", {})
        evaluated: set[str] = set()
        for key, value in instance.items():
            if key in properties:
                validate(value, properties[key], f"{path}.{key}")
                evaluated.add(key)
            for pattern, subschema in pattern_properties.items():
                if re.search(pattern, key) is not None:
                    validate(value, subschema, f"{path}.{key}")
                    evaluated.add(key)
        extras = sorted(set(instance) - evaluated)
        additional = schema.get("additionalProperties", True)
        if additional is False and extras:
            raise SchemaViolation(f"{path}: unexpected properties {extras}")
        if isinstance(additional, dict):
            for key in extras:
                validate(instance[key], additional, f"{path}.{key}")

    if isinstance(instance, list):
        if len(instance) < schema.get("minItems", 0):
            raise SchemaViolation(f"{path}: array is too short")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            raise SchemaViolation(f"{path}: array is too long")
        if "items" in schema:
            for index, item in enumerate(instance):
                validate(item, schema["items"], f"{path}[{index}]")
