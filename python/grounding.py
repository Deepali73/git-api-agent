"""Fetches real IDs for any foreign-key fields before generation, so the
model picks an existing record instead of inventing one.

Mirrors builder_agent/executor/GroundingProvider.ts.
"""
from typing import Dict, List

import python.config as config
from python.backend_client import BackendApiClient

GroundingContext = Dict[str, List[dict]]


def gather_context(links: Dict[str, dict], client: BackendApiClient) -> GroundingContext:
    context: GroundingContext = {}

    for field_key, link in links.items():
        resource = link['resource']
        try:
            page = client.list(resource, {'limit': config.GROUNDING_LIST_LIMIT})
            items = page.get('items', [])
            context[field_key] = [
                {
                    'id': str(item.get('id', '')),
                    'label': str(item.get('title') or item.get('name') or item.get('id') or ''),
                }
                for item in items
            ]
        except Exception:
            # Linked resource may not be listable from here, or may not exist yet.
            # Leave it out of the grounding context rather than failing the whole gather.
            context[field_key] = []

    return context
