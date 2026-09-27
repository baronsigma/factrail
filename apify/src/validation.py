"""Validate Actor inputs against a snapshot of FACTRAIL's live MCP schemas."""
from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

SCHEMAS = json.loads((Path(__file__).parents[1] / '.actor' / 'factrail_tool_schemas.json').read_text())
TOOLS = {'verify': 'factrail_verify', 'assess_import': 'factrail_assess', 'get_receipt': 'factrail_get_receipt'}


class InputError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_action(payload: object) -> tuple[str, str, dict]:
    if not isinstance(payload, dict):
        raise InputError('invalid_input', 'Actor input must be an object')
    if set(payload) != {'action', 'input'}:
        raise InputError('invalid_input', 'Only action and input are allowed and both are required')
    action = payload['action']
    if not isinstance(action, str) or action not in TOOLS:
        raise InputError('unsupported_capability', 'Unsupported action')
    value = payload['input']
    if not isinstance(value, dict):
        raise InputError('invalid_input', 'input must be an object')
    tool = TOOLS[action]
    arguments = {'assessment_type': 'import', 'parameters': value} if action == 'assess_import' else value
    errors = sorted(Draft202012Validator(SCHEMAS[tool], format_checker=FormatChecker()).iter_errors(arguments),
                    key=lambda error: (list(map(str, error.path)), error.message))
    if errors:
        error = errors[0]
        path = '.'.join(map(str, error.path)) or 'input'
        raise InputError('invalid_input', f'{path}: {error.message}')
    return action, tool, arguments
