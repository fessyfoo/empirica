"""
Persona Profile Validation

Validates persona profiles against the JSON schema and additional business logic.
"""

import json
import logging
from pathlib import Path
from typing import Any

import jsonschema

logger = logging.getLogger(__name__)


class ValidationError(Exception):
    """Raised when persona profile validation fails"""

    pass


def load_schema() -> dict:
    """Load the persona JSON schema"""
    schema_path = Path(__file__).parent / "schemas" / "persona_schema.json"

    if not schema_path.exists():
        raise FileNotFoundError(f"Schema not found: {schema_path}")

    with open(schema_path) as f:
        return json.load(f)


def validate_persona_profile(profile_data: dict[str, Any]) -> None:
    """
    Validate persona profile against JSON schema

    Args:
        profile_data: Persona profile as dictionary

    Raises:
        ValidationError: If validation fails
    """
    try:
        schema = load_schema()
        jsonschema.validate(instance=profile_data, schema=schema)
        logger.info(f"✓ Schema validation passed for persona: {profile_data.get('persona_id')}")

    except jsonschema.ValidationError as e:
        error_path = " -> ".join(str(p) for p in e.path)
        raise ValidationError(f"Schema validation failed at {error_path}: {e.message}") from e
    except jsonschema.SchemaError as e:
        raise ValidationError(f"Invalid schema: {e.message}") from e

    # Additional business logic validation
    _validate_business_logic(profile_data)


def _validate_business_logic(profile_data: dict) -> None:
    """
    Additional validation beyond JSON schema

    Checks:
    - Weights sum to 1.0
    - Thresholds are reasonable
    - Focus domains not empty
    - Escalation triggers are valid
    """

    # Check weights sum to 1.0
    weights = profile_data["epistemic_config"]["weights"]
    weight_sum = sum(weights.values())

    if not (0.99 <= weight_sum <= 1.01):
        raise ValidationError(f"Epistemic weights must sum to 1.0, got {weight_sum:.4f}")

    # Check thresholds are reasonable
    thresholds = profile_data["epistemic_config"]["thresholds"]

    if thresholds.get("uncertainty_trigger", 0.5) > thresholds.get("confidence_to_proceed", 0.75):
        logger.warning("⚠️ uncertainty_trigger > confidence_to_proceed may cause investigation loops")

    # Check focus domains not empty
    if not profile_data["epistemic_config"]["focus_domains"]:
        raise ValidationError("focus_domains cannot be empty")

    # Validate escalation triggers
    for trigger in profile_data.get("sentinel_config", {}).get("escalation_triggers", []):
        _validate_escalation_trigger(trigger)


def _validate_escalation_trigger(trigger: dict) -> None:
    """Validate escalation trigger condition syntax"""
    condition = trigger["condition"]

    # Basic syntax check (just verify it's a string with expected operators)
    valid_operators = [">", "<", ">=", "<=", "==", "!="]

    if not any(op in condition for op in valid_operators):
        raise ValidationError(f"Escalation trigger condition invalid: {condition}")

    # Could add more sophisticated parsing here
    logger.debug(f"Escalation trigger validated: {condition} -> {trigger['action']}")
