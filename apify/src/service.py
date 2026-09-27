"""Actor orchestration with injectable client and output sink for deterministic tests."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .billing import billable_event
from .factrail_client import FactrailError
from .validation import InputError, validate_action

Publish = Callable[[dict, str | None], Awaitable[Any]]


async def run_action(payload: object, client: Any, publish: Publish) -> dict:
    action = payload.get('action') if isinstance(payload, dict) else None
    try:
        action, tool, arguments = validate_action(payload)
        result = await client.call(tool, arguments)
    except (InputError, FactrailError) as exc:
        return {'action': action, 'error': {'code': exc.code, 'message': exc.message}}
    event = billable_event(action, result)
    item = {'action': action, 'result': result}
    try:
        charge_result = await publish(item, event)
    except Exception:
        # Never retry an ambiguous charging outcome.
        return {'action': action, 'error': {'code': 'output_or_charge_uncertain',
                'message': 'Result publication or charge failed; no retry was attempted'}}
    if event is not None and getattr(charge_result, 'charged_count', 0) != 1:
        return {'action': action, 'error': {'code': 'budget_exceeded',
                'message': 'The run budget does not permit this evidence event'}}
    return item
