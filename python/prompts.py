"""Mirrors builder_agent/prompts/buildPrompt.ts."""
import json
from typing import Dict, List, Optional


def build_system_prompt(entity_name: str, schema: dict, operation: str = 'create') -> str:
    return (
        f'You generate payloads to {operation} a "{entity_name}" entity in a low-code backend generator. '
        'The user request may be a ticket-style description written in normal natural language. '
        'Extract the intent clearly and populate the emit_payload tool\'s input strictly from the request. '
        'For any field backed by a list of valid options below, you MUST pick an existing "id" from '
        'that list verbatim — never invent an id. '
        "If the user's request doesn't give you enough information for a required field, make the "
        'most reasonable inference rather than leaving it blank.\n'
        + _required_fields_block(schema)
    )


def _required_fields_block(schema: dict) -> str:
    """Spell out the required top-level fields with any enum/default hints, so the
    model never drops one (e.g. a structural "type") that the tool schema requires.
    """
    required = schema.get('required', [])
    if not required:
        return ''

    properties = schema.get('properties', {})
    lines = []
    for field in required:
        spec = properties.get(field, {})
        hint = ''
        if spec.get('enum'):
            hint += f" (one of: {', '.join(map(str, spec['enum']))})"
        if spec.get('default') is not None:
            hint += f"; use \"{spec['default']}\" unless the request says otherwise"
        lines.append(f'- {field}{hint}')

    return (
        'You MUST include every one of these required fields, even if the request '
        'does not mention them:\n' + '\n'.join(lines)
    )


def build_user_prompt(
    intent: str,
    context: Dict[str, List[dict]],
    prior_error: Optional[str] = None,
    current_object: Optional[dict] = None,
) -> str:
    sections = [f'Request: {intent}']

    if current_object is not None:
        sections.append(
            'You are UPDATING this existing entity. Here is its current full state:\n'
            + json.dumps(current_object, indent=2)
            + '\nApply the request as a change to it: return the COMPLETE object with every '
            'field, modifying only what the request asks and copying all other fields '
            '(including nested ids) through unchanged.'
        )

    options_sections = []
    for key, options in context.items():
        if not options:
            continue
        formatted = ', '.join(f"{o['id']} ({o['label']})" for o in options)
        options_sections.append(f'- {key}: {formatted}')

    if options_sections:
        sections.append('Valid options for linked fields:')
        sections.extend(options_sections)

    if prior_error:
        sections.append(
            f'Your previous attempt failed with this error: "{prior_error}". Fix it and try again.'
        )

    sections.append(
        'Treat the request as a ticket-style natural language description of the desired entity change. ' 
        'Interpret headings, wording, and context as requirements rather than literal payload field names.'
    )

    return '\n'.join(sections)
