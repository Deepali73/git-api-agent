import os
from pathlib import Path


def get_repo_path() -> str:
    env_path = os.getenv("REPO_PATH", "").strip()
    if env_path:
        resolved = Path(env_path).expanduser().resolve()
        if resolved.exists():
            return str(resolved)

    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".git").exists():
            return str(candidate)

    return str(cwd)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
REPO_PATH = get_repo_path()
DEFAULT_BRANCH = "dev-1"

# How much repository intelligence to load before each request
CONTEXT_MAX_BRANCHES = _int_env("CONTEXT_MAX_BRANCHES", 20)
CONTEXT_MAX_COMMITS_PER_BRANCH = _int_env("CONTEXT_MAX_COMMITS_PER_BRANCH", 30)
CONTEXT_MAX_FILES_PER_BRANCH = _int_env("CONTEXT_MAX_FILES_PER_BRANCH", 200)
