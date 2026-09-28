# FACTRAIL deployment checklist (v2.1.1-era guidance)

The repository defines a Python HTTP entry point (`python3 -m factrail.mcp_http_server`) and a stdio entry point (`python3 -m factrail.mcp_server`). It does not define a systemd unit or a hosted deployment script. Use the existing process supervisor and environment on the target host.

1. **Update the checkout.** Run `git pull --ff-only` on the intended release commit or tag. Check `python3 -m pip show factrail` and use the same virtual environment as the running process. Run `python3 -m pip install -e .` if that environment needs the updated package. Check the dependency set for the release being deployed.
2. **Back up SQLite before restart.** Use the configured `FACTRAIL_CACHE_PATH` (default `/tmp/factrail_cache.db`) and SQLite's online backup API. The source must exist; choose a durable backup destination.

   ```bash
   python3 - <<'PY'
   import os, sqlite3
   from pathlib import Path
   source = Path(os.environ.get('FACTRAIL_CACHE_PATH', '/tmp/factrail_cache.db'))
   if not source.is_file():
       raise SystemExit(f'Database not found: {source}')
   target = source.with_name(source.name + '.pre-deploy.backup')
   with sqlite3.connect(source) as original, sqlite3.connect(target) as backup:
       original.backup(backup)
   print(f'Backup written: {target}')
   PY
   ```

3. **Restart the existing HTTP process** through its current supervisor. For a manual foreground instance, stop it and run `python3 -m factrail.mcp_http_server` with the same environment. `FACTRAIL_HOST` defaults to `127.0.0.1`; `FACTRAIL_PORT` defaults to `8765`. Do not change DNS or registry settings as part of this release.
4. **Check liveness and readiness.** Run `curl -fsS https://mcp.factrail.online/healthz` and `curl -fsS https://mcp.factrail.online/readyz`. Health should say `ok`; readiness should return `{"status":"ready"}`. Readiness tests local SQLite access and does not depend on government APIs.
5. **Check the MCP surface and receipts.** The following smoke script calls `tools/list`, then verifies a French company, assesses an import, and fetches the resulting receipt. It requires the deployed service to expose the generic Evidence Core tools and a working INSEE key for the company call.

   ```python
   import asyncio
   from mcp import ClientSession
   from mcp.client.streamable_http import streamable_http_client

   URL = "https://mcp.factrail.online/mcp"

   async def main():
       async with streamable_http_client(URL) as (read, write):
           async with ClientSession(read, write) as session:
               await session.initialize()
               names = {tool.name for tool in (await session.list_tools()).tools}
               required = {"factrail_verify", "factrail_assess", "factrail_get_receipt"}
               assert required <= names, names

               verified = await session.call_tool("factrail_verify", {
                   "subject_type": "company_fr", "identifier": "<SIREN or SIRET>",
                   "fields": ["status", "legal_name"]})
               assert not verified.is_error and verified.structured_content

               assessed = await session.call_tool("factrail_assess", {
                   "assessment_type": "import", "parameters": {
                       "product": "750ml insulated stainless steel bottle",
                       "origin_country": "CN", "destination_country": "FR",
                       "quantity": 5, "goods_value": 1000, "currency": "EUR",
                       "known_hs_code": "961700"}})
               assert not assessed.is_error and assessed.structured_content

               receipt_id = assessed.structured_content["receipt_id"]
               saved = await session.call_tool("factrail_get_receipt", {"receipt_id": receipt_id})
               assert not saved.is_error
               assert saved.structured_content["receipt_id"] == receipt_id
               print("FACTRAIL MCP smoke checks passed")

   asyncio.run(main())
   ```

6. **Inspect demand telemetry.** Run `python3 -m factrail.evidence.demand --days 7` in the deployment environment. Zero traffic should return valid zero-count JSON. Check service logs for startup or readiness failures without copying request payloads into incident reports.

SQLite initialization is additive and idempotent for the documented schema and does not require deleting the existing cache or receipts database. If a check fails, investigate the existing process configuration and restore from the backup only through the operator's normal recovery procedure.
