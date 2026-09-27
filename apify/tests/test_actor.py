from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from src.billing import billable_event
from src.factrail_client import FactrailClient, FactrailError
from src.service import run_action
from src.validation import InputError, validate_action

RECEIPT = 'fr_' + 'a' * 64
VERIFY = {'action': 'verify', 'input': {'subject_type': 'company_fr', 'identifier': '784671695', 'fields': ['status', 'legal_name']}}
IMPORT = {'action': 'assess_import', 'input': {'product': 'insulated steel bottle', 'origin_country': 'CN',
          'destination_country': 'FR', 'quantity': 5, 'goods_value': 1000, 'currency': 'EUR'}}


def envelope(*, status='supported', coverage='sufficient', failures=None, source_status='available'):
    return {'schema_version': '1.1', 'status': status, 'receipt_id': RECEIPT,
            'state_fingerprint': 'fs_' + 'b' * 64,
            'coverage': {'level': coverage, 'fields_requested': ['status'], 'fields_resolved': ['status'],
                         'fields_unresolved': [] if coverage == 'sufficient' else ['legal_name'],
                         'metadata': {'source_failures': failures or []}},
            'freshness': {'stale': False}, 'conflicts': [],
            'evidence': [{'id': 'source_1', 'source_status': source_status}],
            'facts': [{'field': 'status', 'value': 'active', 'evidence_ids': ['source_1']}],
            'subject': {'type': 'company_fr'}}


class Client:
    def __init__(self, result=None, error=None):
        self.result = result or envelope()
        self.error = error
        self.calls = []

    async def call(self, tool, arguments):
        self.calls.append((tool, arguments))
        if self.error:
            raise self.error
        return self.result


class Publisher:
    def __init__(self, count=1, error=None):
        self.calls = []
        self.count = count
        self.error = error

    async def __call__(self, item, event):
        self.calls.append((item, event))
        if self.error:
            raise self.error
        return SimpleNamespace(charged_count=self.count)


@pytest.mark.asyncio
async def test_supported_verify_charges_exactly_once():
    client, publisher = Client(), Publisher()
    outcome = await run_action(VERIFY, client, publisher)
    assert outcome['result'] == client.result
    assert publisher.calls[0][1] == 'factrail-verify'
    assert len(publisher.calls) == len(client.calls) == 1
    assert client.calls[0][0] == 'factrail_verify'


@pytest.mark.asyncio
@pytest.mark.parametrize('status,coverage', [('supported', 'partial'), ('insufficient_evidence', 'insufficient'),
                                         ('conflicting_sources', 'sufficient'), ('stale', 'sufficient'),
                                         ('contradicted', 'sufficient')])
async def test_degraded_verify_is_free(status, coverage):
    publisher = Publisher()
    outcome = await run_action(VERIFY, Client(envelope(status=status, coverage=coverage)), publisher)
    assert outcome['result']['status'] == status
    assert publisher.calls[0][1] is None


@pytest.mark.asyncio
async def test_verify_required_source_failure_is_free():
    publisher = Publisher()
    await run_action(VERIFY, Client(envelope(source_status='unavailable')), publisher)
    assert publisher.calls[0][1] is None


@pytest.mark.asyncio
async def test_invalid_inputs_never_call_backend_or_charge():
    invalid = [
        {'action': 'unknown', 'input': {}},
        {**VERIFY, 'extra': 1},
        {'action': 'verify', 'input': {'subject_type': 'company_fr', 'identifier': 'bad'}},
        {'action': 'verify', 'input': {'subject_type': 'company_fr', 'identifier': '784671695', 'fields': []}},
        {'action': 'assess_import', 'input': {'product': 'bottle'}},
        {'action': 'get_receipt', 'input': {'receipt_id': '../other'}},
    ]
    for payload in invalid:
        client, publisher = Client(), Publisher()
        outcome = await run_action(payload, client, publisher)
        assert outcome['error']['code'] in {'invalid_input', 'unsupported_capability'}
        assert client.calls == publisher.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize('code', ['invalid_input', 'unsupported_capability', 'not_found', 'internal'])
async def test_backend_errors_never_charge(code):
    client, publisher = Client(error=FactrailError(code, 'backend response')), Publisher()
    result = await run_action(VERIFY, client, publisher)
    assert result['error']['code'] == code
    assert publisher.calls == []


@pytest.mark.asyncio
async def test_import_sufficient_charges_once():
    client, publisher = Client(), Publisher()
    await run_action(IMPORT, client, publisher)
    assert client.calls[0] == ('factrail_assess', {'assessment_type': 'import', 'parameters': IMPORT['input']})
    assert len(publisher.calls) == 1 and publisher.calls[0][1] == 'factrail-assess-import'


@pytest.mark.asyncio
@pytest.mark.parametrize('result', [envelope(coverage='partial'), envelope(status='insufficient_evidence'),
                                        envelope(failures=['EU TARIC']), envelope(source_status='unavailable')])
async def test_import_degraded_or_source_failed_is_free(result):
    publisher = Publisher()
    await run_action(IMPORT, Client(result), publisher)
    assert publisher.calls[0][1] is None


@pytest.mark.asyncio
async def test_receipt_is_free_and_preserved():
    client, publisher = Client(), Publisher()
    result = await run_action({'action': 'get_receipt', 'input': {'receipt_id': RECEIPT}}, client, publisher)
    assert result['result'] == client.result
    assert client.calls[0] == ('factrail_get_receipt', {'receipt_id': RECEIPT})
    assert publisher.calls[0][1] is None


@pytest.mark.asyncio
async def test_budget_exhaustion_does_not_claim_charge():
    outcome = await run_action(VERIFY, Client(), Publisher(count=0))
    assert outcome['error']['code'] == 'budget_exceeded'


@pytest.mark.asyncio
async def test_charge_error_is_not_retried():
    publisher = Publisher(error=RuntimeError('charge timeout'))
    outcome = await run_action(VERIFY, Client(), publisher)
    assert outcome['error']['code'] == 'output_or_charge_uncertain'
    assert len(publisher.calls) == 1


@pytest.mark.asyncio
async def test_transient_factrail_retry_does_not_retry_charge(monkeypatch):
    client = FactrailClient(retries=1, backoff=0)
    calls = 0
    async def flaky(tool, args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout('temporary')
        return envelope()
    monkeypatch.setattr(client, '_call_once', flaky)
    publisher = Publisher()
    result = await run_action(VERIFY, client, publisher)
    assert result['result']['status'] == 'supported'
    assert calls == 2 and len(publisher.calls) == 1


@pytest.mark.asyncio
async def test_deterministic_backend_error_has_no_retry(monkeypatch):
    client = FactrailClient(retries=2, backoff=0)
    calls = 0
    async def fail(tool, args):
        nonlocal calls
        calls += 1
        raise FactrailError('invalid_input', 'bad')
    monkeypatch.setattr(client, '_call_once', fail)
    outcome = await run_action(VERIFY, client, Publisher())
    assert outcome['error']['code'] == 'invalid_input' and calls == 1


@pytest.mark.asyncio
async def test_timeout_exhausts_bounded_retry_without_charge(monkeypatch):
    client = FactrailClient(retries=1, backoff=0)
    calls = 0
    async def fail(tool, args):
        nonlocal calls
        calls += 1
        raise TimeoutError()
    monkeypatch.setattr(client, '_call_once', fail)
    publisher = Publisher()
    outcome = await run_action(VERIFY, client, publisher)
    assert outcome['error']['code'] == 'backend_timeout'
    assert calls == 2 and publisher.calls == []


@pytest.mark.asyncio
async def test_valid_format_missing_receipt_preserves_not_found():
    client = Client(error=FactrailError('not_found', 'receipt not found'))
    publisher = Publisher()
    outcome = await run_action({'action': 'get_receipt', 'input': {'receipt_id': RECEIPT}}, client, publisher)
    assert outcome['error'] == {'code': 'not_found', 'message': 'receipt not found'}
    assert publisher.calls == []


def test_pricing_plan_and_scope():
    root = Path(__file__).parents[1]
    actor = json.loads((root / '.actor/actor.json').read_text())
    pricing = json.loads((root / '.actor/pricing-plan.json').read_text())
    assert actor['usesStandbyMode'] is False
    assert pricing['model'] == 'PAY_PER_EVENT' and pricing['pass_platform_usage_to_user'] is False
    assert [(e['name'], e['price_usd']) for e in pricing['events']] == [
        ('factrail-verify', .005), ('factrail-assess-import', .03)]


def test_schema_snapshot_matches_canonical_source():
    from factrail.mcp_http_server import handle_list_tools
    import asyncio
    local = asyncio.run(handle_list_tools(None, None))
    expected = {t.name: t.input_schema for t in local.tools if t.name in {
        'factrail_verify', 'factrail_assess', 'factrail_get_receipt'}}
    from src.validation import SCHEMAS
    assert SCHEMAS == expected
