"""Stdlib checker for the JSON Schema subset used by this directory."""

from __future__ import annotations


def _type_ok(instance, expected: str) -> bool:
    if expected == "object":
        return isinstance(instance, dict)
    if expected == "array":
        return isinstance(instance, list)
    if expected == "string":
        return isinstance(instance, str)
    if expected == "boolean":
        return isinstance(instance, bool)
    if expected == "null":
        return instance is None
    if expected == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    raise ValueError(f"unsupported type {expected}")


def validate(instance, schema, path="$"):
    errors = []
    if not isinstance(schema, dict):
        return [f"{path}: schema is not an object"]
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} is outside the enum")
    if "type" in schema:
        types = schema["type"]
        if isinstance(types, str):
            types = [types]
        if not any(_type_ok(instance, item) for item in types):
            errors.append(f"{path}: type {types} does not accept {type(instance).__name__}")
    if isinstance(instance, dict) and (
        "properties" in schema or "required" in schema or schema.get("type") == "object"
    ):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}.{key}: missing")
        if schema.get("additionalProperties", True) is False:
            for key in instance:
                if key not in props:
                    errors.append(f"{path}.{key}: additional property")
        for key, value in instance.items():
            if key in props:
                errors.extend(validate(value, props[key], f"{path}.{key}"))
    if isinstance(instance, list) and (
        "items" in schema or "prefixItems" in schema or schema.get("type") == "array"
    ):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: shorter than minItems")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: longer than maxItems")
        for index, sub in enumerate(schema.get("prefixItems", [])):
            if index < len(instance):
                errors.extend(validate(instance[index], sub, f"{path}[{index}]"))
        if "items" in schema:
            for index, item in enumerate(instance):
                errors.extend(validate(item, schema["items"], f"{path}[{index}]"))
    return errors
