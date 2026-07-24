import pytest
from typing import Optional, List, Union, Any
from pydantic import BaseModel
from app.services.sut_genai_client import _pydantic_to_json_schema


class SubModel(BaseModel):
    sub_field: str


class AnotherModel(BaseModel):
    another_field: int


class ComplexModel(BaseModel):
    simple_str: str
    optional_str: Optional[str] = None
    sub_models: List[SubModel]
    union_field: Union[SubModel, AnotherModel, None] = None
    dict_field: dict[str, SubModel]
    any_field: Any


def _assert_strict_schema(schema: dict):
    """Recursively assert strict mode invariants."""
    if not isinstance(schema, dict):
        return
        
    assert any(k in schema for k in ("type", "$ref", "anyOf", "oneOf", "allOf")), f"Schema missing type/ref/anyOf: {schema}"

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        assert "object" not in schema_type, f'Raw type arrays must not contain "object" due to strict mode constraints: {schema}'

    if schema_type == "object":
        assert schema.get("additionalProperties") is False, f"Object schema must have additionalProperties: False: {schema}"
        if "properties" in schema and isinstance(schema["properties"], dict):
            expected_required = set(schema["properties"].keys())
            actual_required = set(schema.get("required", []))
            assert expected_required == actual_required, f"Required properties mismatch. Expected: {expected_required}, Got: {actual_required}"
            for prop in schema["properties"].values():
                _assert_strict_schema(prop)
                
    if "$defs" in schema and isinstance(schema["$defs"], dict):
        for def_schema in schema["$defs"].values():
            _assert_strict_schema(def_schema)

    if "items" in schema and isinstance(schema["items"], dict):
        _assert_strict_schema(schema["items"])

    for key in ["anyOf", "oneOf", "allOf"]:
        if key in schema and isinstance(schema[key], list):
            for branch in schema[key]:
                _assert_strict_schema(branch)


def test_pydantic_to_json_schema_strict_mode():
    """Test that _pydantic_to_json_schema enforces all strict mode rules."""
    schema = _pydantic_to_json_schema(ComplexModel)
    _assert_strict_schema(schema)


def test_ensure_typed_excludes_object():
    """Test that _ensure_typed produces exactly the fallback list without 'object'."""
    from app.services.sut_genai_client import _ensure_typed
    
    schema = {}
    _ensure_typed(schema)
    
    expected_schema = {
        "anyOf": [
            {"type": "string"},
            {"type": "number"},
            {"type": "boolean"},
            {"type": "null"},
            {
                "type": "array",
                "items": {
                    "anyOf": [
                        {"type": "string"},
                        {"type": "number"},
                        {"type": "boolean"},
                        {"type": "null"},
                    ]
                }
            }
        ]
    }
    assert schema == expected_schema
