# Git API Agent

A unified system that bridges **backend payload generation** (for creating/updating entities like ValueType, Service, Action, Package, etc.) with **GitOps agent workflows** (for creating branches, cherry-picking commits, and managing git operations).

## Overview

This project combines two main components:

- **Backend Integration (`python/`)**: Generates structured payloads using LLM (Groq) and sends them to your backend API to create/update entities.
- **GitOps Agent (`agent/`)**: Plans and executes git operations (branch creation, cherry-picks, merges) based on natural language requests.

The **bridge script** (`bridge.py`) orchestrates both flows together, allowing you to create backend entities and manage git branches in a single workflow.

## Project Structure

```
git-api-agent/
├── bridge.py                    # Main entry point (orchestrates backend + git flows)
├── agent/                       # GitOps agent (LangGraph-based)
│   ├── main.py                 # Agent entry point (run_agent_query, run_agent_loop)
│   ├── graph.py                # LangGraph workflow definition
│   ├── state.py                # Agent state schema
│   ├── planner.py              # Plan todos from user request
│   ├── coordinator.py          # Coordinate execution (emit git commands)
│   ├── executor.py             # Execute git commands
│   ├── observer.py             # Evaluate command success
│   ├── human.py                # Handle human input prompts
│   ├── git_context.py          # Gather git repo context
│   ├── config.py               # Agent configuration
│   ├── langgraph.json          # LangGraph checkpoint file
│   ├── requirements.txt        # Agent dependencies
│   └── README.md               # Agent-specific documentation
├── python/                     # Backend payload generation
│   ├── agent.py               # Backend agent entry point (run_intent)
│   ├── backend_client.py       # REST client for backend API
│   ├── payload_generator.py    # LLM-based payload generation (Groq)
│   ├── prompts.py             # System/user prompts for payload generation
│   ├── grounding.py           # Fetch linked-field options from backend
│   ├── schema_loader.py       # Load entity schemas from cache
│   ├── config.py              # Backend configuration
│   ├── requirements.txt       # Backend dependencies
│   └── schema_cache/          # Cached entity schemas (JSON)
│       ├── ValueType.json
│       ├── Service.json
│       ├── Action.json
│       ├── Package.json
│       ├── Project.json
│       ├── ProjectPackage.json
│       ├── Relation.json
│       └── ...
└── README.md                  # This file
```

## Installation

### Prerequisites

- Python 3.10+
- Git (for agent operations)
- Backend API running at `http://localhost:3000/api/v3/apiBuilderService/` (or set `BACKEND_BASE_URL`)

### Setup

1. **Clone/navigate to the repository:**

   ```bash
   cd git-api-agent
   ```

2. **Install dependencies:**

   ```bash
   pip install -r agent/requirements.txt
   pip install -r python/requirements.txt
   ```

3. **Set environment variables:**

   ```bash
   # Backend API
   export API_TOKEN=<your_backend_api_token>
   export BACKEND_BASE_URL=http://localhost:3000/api/v3/apiBuilderService/

   # LLM (Groq)
   export GROQ_API_KEY=<your_groq_api_key>
   export GROQ_MODEL=llama-3.3-70b-versatile

   # Git repository
   export REPO_PATH=<path_to_git_repo>  # (optional, defaults to current dir)
   export DEFAULT_BRANCH=main           # (optional, defaults to dev-1)
   ```

## Usage

### Basic Syntax

```bash
python bridge.py \
  --flow combined \
  --entity <ENTITY_NAME> \
  --op create|update \
  [--id <ENTITY_ID>] \
  --intent "<natural language intent>" \
  --git-query "<natural language git operation>" \
  [--on-conflict abort|update|skip] \
  [--max-steps <N>] \
  [--print-full-state]
```

### Parameters

| Parameter            | Description                                                             | Default                       |
| -------------------- | ----------------------------------------------------------------------- | ----------------------------- |
| `--flow`             | Which flow to run: `backend`, `git`, or `combined`                      | `combined`                    |
| `--entity`           | Backend entity name (e.g., `ValueType`, `Service`, `Action`, `Package`) | Required for backend/combined |
| `--op`               | Operation: `create` or `update`                                         | `create`                      |
| `--id`               | Entity ID for `update` operations                                       | Optional                      |
| `--intent`           | Natural language description of what to create/update                   | Required for backend/combined |
| `--git-query`        | Natural language git operation (e.g., branch creation)                  | Required for git/combined     |
| `--on-conflict`      | How to handle backend 409 conflicts: `abort`, `update`, `skip`          | `update`                      |
| `--max-steps`        | Maximum LangGraph steps for git agent                                   | `15`                          |
| `--print-full-state` | Print full state JSON (includes repo context)                           | `false`                       |
| `--no-grounding`     | Skip fetching linked-field options                                      | `false`                       |

### Examples

#### 1. Create a ValueType and a Git Branch

```bash
python bridge.py \
  --flow combined \
  --entity ValueType \
  --op create \
  --intent "Create a ValueType named Customer with fields: id (string), name (string), email (string)" \
  --git-query "Create branch feature/customer from main"
```

**Output:**

- Backend: `Customer` ValueType created
- Git: `feature/customer` branch created from `main`

#### 2. Update a ValueType

```bash
python bridge.py \
  --flow combined \
  --entity ValueType \
  --op update \
  --id my_customer_valueType \
  --intent "Add a phone field (string) to the Customer value type" \
  --git-query "Create branch feature/customer-update from dev-1"
```

#### 3. Create a Service

```bash
python bridge.py \
  --flow combined \
  --entity Service \
  --op create \
  --intent "Create an internal Service named om_angara with title 'Om Angara' and isInternal true" \
  --git-query "Create branch feature/om-angara from main"
```

#### 4. Create an Action (requires Service and ValueType ids)

```bash
python bridge.py \
  --flow combined \
  --entity Action \
  --op create \
  --intent "Create an Action for service id <SERVICE_ID>. Name it getCustomer, title 'Get Customer', method get, path /customer/{id}, responseBodyId <VALUE_TYPE_ID>" \
  --git-query "Create branch feature/get-customer-action from main"
```

#### 5. Git-only Flow (no backend)

```bash
python bridge.py \
  --flow git \
  --git-query "Create branch feature/integration from main and cherry-pick commits 1 and 2 from feature/dev"
```

#### 6. Backend-only Flow (no git)

```bash
python bridge.py \
  --flow backend \
  --entity ValueType \
  --op create \
  --intent "Create a Config value type with fields setting (string)"
```

## Common Workflows

### Workflow 1: Create ValueType → Create Service → Create Action

**Step 1: Create ValueType**

```bash
python bridge.py --flow backend --entity ValueType --op create \
  --intent "Create Response ValueType with id (string), status (string), message (string)"
```

_Note the returned ValueType id, e.g., `resp_valueType_id`._

**Step 2: Create Service**

```bash
python bridge.py --flow backend --entity Service --op create \
  --intent "Create custom Service named myapi with title 'My API' and isInternal true"
```

_Note the returned Service id, e.g., `myapi_service_id`._

**Step 3: Create Action**

```bash
python bridge.py --flow combined --entity Action --op create \
  --intent "Create Action for service myapi_service_id named getStatus title 'Get Status' method get path /status responseBodyId resp_valueType_id" \
  --git-query "Create branch feature/get-status-action from main"
```

### Workflow 2: Handle 409 Conflict (Entity Already Exists)

If your backend returns **HTTP 409** (entity already exists), the bridge can auto-update:

```bash
python bridge.py --flow combined --entity ValueType --op create \
  --intent "Create Customer value type" \
  --git-query "Create branch feature/customer" \
  --on-conflict update
```

The bridge will:

1. Attempt to create the entity
2. If 409 conflict, prompt for the existing entity id (or attempt update if available)
3. Update the existing entity with the new intent

To skip backend on conflict:

```bash
--on-conflict skip
```

## Git Agent Features

The GitOps agent supports:

- **Branch creation**: `create branch <name> from <source>`
- **Cherry-pick**: `cherry-pick commits 1 and 2 from <branch>`
- **Merge**: `merge <branch> into <target>`
- **Auto-merge**: Automatically create merge todos after branch creation
- **Interactive prompts**: Ask for user approval on destructive operations (delete)
- **Repo context**: Automatically gathers branch list, commit history, file changes
- **Multi-step planning**: Breaks complex requests into atomic git commands

### Example Git Queries

```bash
# Simple branch creation
"create branch feature/new from main"

# Create + cherry-pick
"create branch release from main and cherry-pick commits 2 and 1 from feature-a"

# Create + auto-merge
"create branch hotfix from main"  # Auto-adds merge hotfix into main after creation
```

## Troubleshooting

### Backend Errors

#### HTTP 400: "Only custom actions are allowed"

**Cause**: The `serviceId` does not reference a valid custom/internal service.

**Fix**:

- Ensure the service is marked as `isInternal: true`
- Use the actual backend Service id (not name)

#### HTTP 409: "Already exists"

**Cause**: Entity already exists.

**Fix**: Use `--op update --id <ID>` or `--on-conflict update`

#### Tool call validation failed (propTypes schema error)

**Cause**: Generated payload included invalid fields in `propTypes` (e.g., `id`, `valueTypeId`).

**Fix**: Rephrase intent to exclude helper fields:

```bash
--intent "... Do NOT include id or valueTypeId in propTypes. Only include: key, title, type, defaultValue, options, ..."
```

### Git Agent Errors

#### "Branch already exists"

**Cause**: Git branch already exists locally.

**Fix**: Use a different branch name or delete the existing branch first.

#### "Only custom actions are allowed" during Action creation

**Cause**: Service referenced by `serviceId` is not internal/custom.

**Fix**: Create or use an internal Service with `isInternal: true`.

### Environment Issues

#### `ModuleNotFoundError: No module named 'graph'`

**Cause**: Agent folder not on `sys.path`.

**Fix**: Already handled in `bridge.py` (inserts agent folder automatically).

#### `GROQ_API_KEY not set`

**Cause**: Missing LLM API key.

**Fix**: Set environment variable:

```bash
export GROQ_API_KEY=<your_groq_api_key>
```

#### Backend connection refused

**Cause**: Backend API not running or wrong `BACKEND_BASE_URL`.

**Fix**: Start backend and verify:

```bash
curl http://localhost:3000/api/v3/apiBuilderService/listService \
  -H "Cookie: AUTH_TOKEN=$API_TOKEN"
```

## Configuration

### Backend Configuration (`python/config.py`)

```python
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
BACKEND_BASE_URL = os.getenv("BACKEND_BASE_URL", "http://localhost:3000/api/v3/apiBuilderService/")
API_TOKEN = os.getenv("API_TOKEN", "")
MAX_ATTEMPTS = int(os.getenv("MAX_ATTEMPTS", "5"))
```

### Agent Configuration (`agent/config.py`)

```python
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
REPO_PATH = get_repo_path()  # Auto-detects from env or current dir
DEFAULT_BRANCH = "dev-1"
CONTEXT_MAX_BRANCHES = 20
CONTEXT_MAX_COMMITS_PER_BRANCH = 30
CONTEXT_MAX_FILES_PER_BRANCH = 200
```

## API Integration

The bridge connects to a backend API with these endpoints:

- `POST /create{Entity}` — Create entity (e.g., `createValueType`, `createService`)
- `PUT /update{Entity}` — Update entity
- `GET /read{Entity}/{id}` — Read entity
- `GET /list{Entity}` — List entities

### Supported Entities

- `ValueType` — Custom data types
- `Service` — API services
- `Action` — API actions
- `Package` — Entity packages
- `Project` — Projects
- `ProjectPackage` — Project-package relationships
- `Relation` — Data relations

## Performance Notes

- **Repo context gathering**: First run scans git history (branches, commits, files). Subsequent runs reuse cached context.
- **Payload generation**: Uses Groq LLM; typically takes 1-3 seconds per request.
- **Git operations**: Local; typically instant.
- **Max steps**: Git agent defaults to 15 steps. Increase with `--max-steps` if needed.

## Development

### Running Tests

```bash
# Test git agent only
python bridge.py --flow git --git-query "create branch feature/test from main"

# Test backend only
python bridge.py --flow backend --entity ValueType --op create \
  --intent "Create Test value type"

# Test both
python bridge.py --flow combined --entity ValueType --op create \
  --intent "Create Test value type" \
  --git-query "create branch feature/test from main"
```

### Debugging

Enable full state output:

```bash
python bridge.py ... --print-full-state
```

This prints repo context, todos, execution history, and git agent state.

## License

MIT

## Support

For issues or questions:

1. Check the **Troubleshooting** section above
2. Verify environment variables are set
3. Ensure backend API is running
4. Review git repo is valid (has `.git/`)

---

**Last Updated**: 2026-06-23
