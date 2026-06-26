"""Central place for paths and env vars so this folder can be moved or
pointed at a different repo checkout without touching the other modules.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

PYTHON_DIR = Path(__file__).resolve().parent

load_dotenv(PYTHON_DIR / '.env', override=False)

REPO_ROOT = Path(os.getenv('REPO_ROOT', PYTHON_DIR.parent.parent)).resolve()


BUILDER_AGENT_DIR = Path(os.getenv('BUILDER_AGENT_DIR', REPO_ROOT / 'builder_agent')).resolve()

TS_NODE_BIN = Path(
    os.getenv('TS_NODE_BIN', REPO_ROOT / 'node_modules' / '.bin' / 'ts-node'),
).resolve()
TS_PROJECT = Path(os.getenv('TS_PROJECT', BUILDER_AGENT_DIR / 'tsconfig.json')).resolve()
DUMP_SCHEMA_SCRIPT = Path(
    os.getenv('DUMP_SCHEMA_SCRIPT', BUILDER_AGENT_DIR / 'dumpSchema.ts'),
).resolve()

SCHEMA_CACHE_DIR = Path(
    os.getenv('SCHEMA_CACHE_DIR', PYTHON_DIR / 'schema_cache'),
).resolve()

API_TOKEN = os.getenv('API_TOKEN')
LOCAL_SERVICE_HOST = os.getenv('LOCAL_SERVICE_HOST', 'http://localhost')
BACKEND_PORT = os.getenv('BACKEND_PORT', '8881')

BACKEND_BASE_URL = os.getenv(
    'BACKEND_BASE_URL', f'{LOCAL_SERVICE_HOST}:{BACKEND_PORT}/api/v3/'
)

GROQ_API_KEY = os.getenv('GROQ_API_KEY')
GROQ_MODEL = os.getenv('GROQ_MODEL', 'openai/gpt-oss-120b')

GROUNDING_LIST_LIMIT = int(os.getenv('GROUNDING_LIST_LIMIT', '50'))
MAX_ATTEMPTS = int(os.getenv('MAX_ATTEMPTS', '1'))
