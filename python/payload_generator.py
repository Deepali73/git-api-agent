"""Calls an LLM (via LangChain + Groq) with the entity's JSON schema bound
as a forced tool call, so the response is structured data matching that
schema.

Mirrors builder_agent/builders/PayloadGenerator.ts, but provider-agnostic
through LangChain instead of calling a provider's API directly.
"""
from typing import Dict, List, Optional

import jsonschema
from langchain_groq import ChatGroq

import python.config as config
from python.prompts import build_system_prompt, build_user_prompt


class PayloadValidationError(Exception):
    pass


def _validate_payload(payload: dict, schema: dict, operation: str) -> None:
    try:
        jsonschema.validate(instance=payload, schema=schema)
    except jsonschema.ValidationError as error:
        path = '.'.join(str(p) for p in error.absolute_path) or '<root>'
        raise PayloadValidationError(
            f'Payload validation failed for {operation} at {path}: {error.message}'
        ) from error


def generate_payload(
    entity_name: str,
    schema: dict,
    intent: str,
    context: Dict[str, List[dict]],
    prior_error: Optional[str] = None,
    current_object: Optional[dict] = None,
    operation: str = 'create',
) -> dict:
    if not config.GROQ_API_KEY:
        raise RuntimeError('GROQ_API_KEY env var is required to generate payloads.')

    llm = ChatGroq(model=config.GROQ_MODEL, api_key=config.GROQ_API_KEY, temperature=0)

    # LangChain derives the bound tool's name from the schema's "title". It must
    # match the tool name the system prompt tells the model to call ("emit_payload"),
    # or Groq's server-side tool-call validation rejects the response with a 400.
    named_schema = {**schema, 'title': 'emit_payload'}
    structured_llm = llm.with_structured_output(named_schema, method='function_calling')

    messages = [
        ('system', build_system_prompt(entity_name, schema, operation)),
        ('human', build_user_prompt(intent, context, prior_error, current_object)),
    ]

    payload = structured_llm.invoke(messages)
    _validate_payload(payload, schema, operation)
    return payload
