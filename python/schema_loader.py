"""Runs builder_agent/dumpSchema.ts to get an entity's JSON schema, sample
payload, and foreign-key link map — the one piece that has to come from
the live TypeScript domain entities rather than being reimplemented here.

If config.SCHEMA_CACHE_DIR/<EntityName>.json exists, it's read instead of
shelling out to ts-node. Generate entries by hand while iterating:
    ts-node --project builder_agent/tsconfig.json -r tsconfig-paths/register \\
        builder_agent/dumpSchema.ts ValueType \\
        > builder_agent/python/schema_cache/ValueType.json
"""
import json
import subprocess

import python.config as config


def load_entity_schema(entity_name: str) -> dict:
    cache_file = config.SCHEMA_CACHE_DIR / f'{entity_name}.json'
    if cache_file.exists():
        with open(cache_file) as f:
            return json.load(f)

    if not config.TS_NODE_BIN.exists():
        raise RuntimeError(
            f'ts-node not found at {config.TS_NODE_BIN}. '
            'Set TS_NODE_BIN if node_modules lives somewhere else.'
        )

    result = subprocess.run(
        [
            str(config.TS_NODE_BIN),
            '--project',
            str(config.TS_PROJECT),
            '-r',
            'tsconfig-paths/register',
            str(config.DUMP_SCHEMA_SCRIPT),
            entity_name,
        ],
        cwd=str(config.REPO_ROOT),
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(f'dumpSchema.ts failed: {result.stderr.strip()}')

    return json.loads(result.stdout)
