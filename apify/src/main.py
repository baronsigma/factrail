"""Apify entry point. The paid event is coupled to one visible dataset item."""
from __future__ import annotations

import asyncio

from apify import Actor

from .factrail_client import FactrailClient
from .service import run_action


async def main() -> None:
    async with Actor:
        async def publish(item: dict, event: str | None):
            if event is not None:
                return await Actor.push_data(item, charged_event_name=event)
            # OUTPUT is the normal free result channel. It avoids default-dataset
            # synthetic billing for partial evidence and receipt retrieval.
            await Actor.set_value('OUTPUT', item)
            return None

        outcome = await run_action(await Actor.get_input(), FactrailClient(), publish)
        if 'error' in outcome:
            await Actor.set_value('OUTPUT', outcome)
            Actor.log.warning('FACTRAIL action ended without a paid event: %s', outcome['error']['code'])


if __name__ == '__main__':
    asyncio.run(main())
