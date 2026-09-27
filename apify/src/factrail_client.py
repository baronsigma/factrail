"""Official MCP client transport; all verification stays on canonical FACTRAIL."""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

FACTRAIL_MCP_URL = 'https://mcp.factrail.online/mcp'
TRANSIENT_HTTP = {502, 503, 504}


class FactrailError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _transient(error: BaseException) -> bool:
    if isinstance(error, BaseExceptionGroup):
        return bool(error.exceptions) and all(_transient(child) for child in error.exceptions)
    if isinstance(error, (httpx.ConnectError, httpx.TimeoutException,
                          httpx2.ConnectError, httpx2.TimeoutException, TimeoutError)):
        return True
    return isinstance(error, (httpx.HTTPStatusError, httpx2.HTTPStatusError)) and error.response.status_code in TRANSIENT_HTTP


def _error_from_result(result: Any) -> FactrailError:
    for block in result.content:
        if getattr(block, 'type', None) == 'text':
            try:
                data = json.loads(block.text)
            except (ValueError, TypeError):
                continue
            if isinstance(data, dict) and isinstance(data.get('error'), str):
                code = data['error']
                message = data.get('message') if code in {'invalid_input', 'unsupported_capability', 'not_found'} and isinstance(data.get('message'), str) else 'FACTRAIL could not complete this request'
                return FactrailError(code, message)
    return FactrailError('backend_error', 'FACTRAIL returned an error without a structured code')


class FactrailClient:
    def __init__(self, *, url: str = FACTRAIL_MCP_URL, timeout: float = 30.0,
                 retries: int = 1, backoff: float = 1.0) -> None:
        self.url = url
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff

    async def _call_once(self, tool: str, arguments: dict) -> dict:
        async with asyncio.timeout(self.timeout):
            async with streamable_http_client(self.url) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.call_tool(tool, arguments)
                    if result.is_error:
                        raise _error_from_result(result)
                    if not isinstance(result.structured_content, dict):
                        raise FactrailError('invalid_backend_response', 'FACTRAIL did not return structured evidence')
                    return result.structured_content

    async def call(self, tool: str, arguments: dict) -> dict:
        for attempt in range(self.retries + 1):
            try:
                return await self._call_once(tool, arguments)
            except FactrailError:
                raise
            except Exception as exc:
                if not _transient(exc):
                    raise FactrailError('backend_error', 'FACTRAIL MCP request failed') from exc
                if attempt == self.retries:
                    raise FactrailError('backend_timeout', 'FACTRAIL MCP is temporarily unavailable') from exc
                await asyncio.sleep(self.backoff * (2 ** attempt))
        raise AssertionError('unreachable')
