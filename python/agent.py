"""CLI entry point. Mirrors builder_agent/cli.ts, but with no local
pre-validation — the backend already validates on create (CreateAction
calls entity.toBiz() before persisting), so a rejected payload just
feeds its error back into the next generation attempt.

Usage:
    # Interactive — prompts for operation, entity, (id if update), then intent:
    python agent.py

    # Or pass flags to skip the prompt(s):
    python agent.py --op create --entity ValueType --intent "create a Customer
    value type in package ecom with fields id, name, email"

    python agent.py --op update --entity ValueType --id hey_ecom_valueType \
        --intent "rename title to Heyy and make field a virtual"
"""
import argparse
import json
import re
import sys

import python.config as config
from python.backend_client import BackendApiClient, BackendApiError
from python.grounding import gather_context
from python.payload_generator import PayloadValidationError, generate_payload
from python.schema_loader import load_entity_schema


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Generate and create/update an entity from intent.')
    parser.add_argument(
        '--op', choices=['create', 'update'], help='Operation (prompted if omitted).'
    )
    parser.add_argument('--entity', help='Entity name, e.g. ValueType (prompted if omitted)')
    parser.add_argument(
        '--id', dest='entity_id', help='Entity id to update (update only; prompted if omitted).'
    )
    parser.add_argument('--intent', help='Natural language description (prompted if omitted)')
    parser.add_argument('--ticket', help='Ticket-style natural language description (prompted if omitted)')
    parser.add_argument('--max-attempts', type=int, default=config.MAX_ATTEMPTS)
    parser.add_argument(
        '--yes', action='store_true', help='Skip the create confirmation prompt.'
    )
    parser.add_argument(
        '--no-grounding',
        action='store_true',
        help='Skip fetching linked-field options; pass an empty context.',
    )
    return parser.parse_args()


def confirm(question: str) -> bool:
    answer = input(f'{question} (y/n) ')
    return answer.strip().lower() == 'y'


def prompt_nonempty(label: str) -> str:
    """Ask until the user types something non-blank."""
    while True:
        value = input(f'{label}: ').strip()
        if value:
            return value
        print(f'{label} cannot be empty.')


def prompt_operation() -> str:
    while True:
        value = input('Operation [create/update] (default create): ').strip().lower()
        if not value:
            return 'create'
        if value in ('create', 'update'):
            return value
        print("Please enter 'create' or 'update'.")


def normalize_ticket_intent(intent: str) -> str:
    """Normalize ticket-style or query-style natural language input."""
    cleaned = intent.strip()
    if not cleaned:
        return cleaned

    cleaned = re.sub(
        r'(?im)^\s*(summary|description|details|acceptance criteria|title)\s*[:\-]\s*',
        '',
        cleaned,
    ).strip()
    return cleaned


def run_intent(
    entity: str,
    intent: str,
    op: str = 'create',
    entity_id: str | None = None,
    no_grounding: bool = False,
    max_attempts: int | None = None,
) -> dict:
    if op == 'update' and not entity_id:
        raise ValueError('entity_id is required for update operations.')

    intent = normalize_ticket_intent(intent)
    client = BackendApiClient()

    current_object = None
    if op == 'update':
        current_object = client.read(entity, entity_id)

    schema_data = load_entity_schema(entity)

    if no_grounding:
        context = {}
    else:
        context = gather_context(schema_data['links'], client)

    prior_error = None
    attempts = max_attempts or config.MAX_ATTEMPTS
    for attempt in range(1, attempts + 1):
        try:
            payload = generate_payload(
                entity,
                schema_data['schema'],
                intent,
                context,
                prior_error,
                current_object,
                op,
            )
        except PayloadValidationError as error:
            prior_error = str(error)
            if attempt >= attempts:
                raise
            continue

        if op == 'update':
            payload['id'] = current_object.get('id', entity_id)

        try:
            if op == 'update':
                return client.update(entity, payload)
            return client.create(entity, payload)
        except BackendApiError as error:
            prior_error = str(error.data or error)
            if attempt >= attempts:
                raise

    raise RuntimeError('Failed to generate a valid payload.')


def main() -> int:
    args = parse_args()

    op = args.op or prompt_operation()
    entity = args.entity or prompt_nonempty('Entity')

    client = BackendApiClient()

    # For update we read the existing object first so the LLM modifies it
    # rather than regenerating from scratch (preserving id and all fields).
    current_object = None
    entity_id = None
    if op == 'update':
        entity_id = args.entity_id or prompt_nonempty('Id')
        print(f'Reading current {entity} {entity_id}...')
        try:
            current_object = client.read(entity, entity_id)
        except BackendApiError as error:
            print(f'Could not read {entity} {entity_id}: {error.data or error}')
            return 1

    intent = args.ticket or args.intent or prompt_nonempty('Intent')
    intent = normalize_ticket_intent(intent)

    print(f'Loading schema for {entity}...')
    schema_data = load_entity_schema(entity)

    if args.no_grounding:
        print('Skipping grounding (--no-grounding); using empty context.')
        context = {}
    else:
        print('Gathering linked-field context...')
        context = gather_context(schema_data['links'], client)

    prior_error = None

    for attempt in range(1, args.max_attempts + 1):
        print(f'Generating payload (attempt {attempt}/{args.max_attempts})...')
        try:
            payload = generate_payload(
                entity, schema_data['schema'], intent, context, prior_error,
                current_object, op,
            )
        except PayloadValidationError as error:
            prior_error = str(error)
            print(f'Payload validation failed: {prior_error}')
            if attempt >= args.max_attempts:
                print('Could not generate a valid payload after validation failures.')
                return 1
            print('Retrying generation with validation feedback...\n')
            continue

        if op == 'update':
            # The bound schema can't emit id, but the update PUT requires it.
            payload['id'] = current_object.get('id', entity_id)

        print('\nGenerated payload:')
        print(json.dumps(payload, indent=2))

        if not args.yes and not confirm(f'\n{op.capitalize()} this {entity}?'):
            print('Aborted — nothing was sent.')
            return 0

        try:
            if op == 'update':
                result = client.update(entity, payload)
            else:
                result = client.create(entity, payload)
            print(f'\n{op.capitalize()}d:')
            print(json.dumps(result, indent=2))
            return 0
        except BackendApiError as error:
            prior_error = str(error.data or error)
            print(f'Backend rejected the payload: {prior_error}')
            if attempt < args.max_attempts:
                print('Retrying generation with this error...\n')

    print(f'Could not {op} a valid {entity} after {args.max_attempts} attempts.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
