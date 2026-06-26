"""Generic REST client for the apiBuilderService backend.

Mirrors builder_agent/executor/BackendApiClient.ts so the two stay
interchangeable — same auth, same base URL convention, same endpoint
naming (create{Entity} / read{Entity} / list{Entity}).
"""
from typing import Any, Optional

import requests

import python.config as config


class BackendApiError(Exception):
    def __init__(self, message: str, status: Optional[int] = None, data: Any = None):
        super().__init__(message)
        self.status = status
        self.data = data


class BackendApiClient:
    def __init__(self, api_token: Optional[str] = None, base_url: Optional[str] = None):
        self.api_token = api_token or config.API_TOKEN
        if not self.api_token:
            raise RuntimeError(
                'API_TOKEN env var is required (same token used by apiBuilderService.ts).'
            )
        self.base_url = (base_url or config.BACKEND_BASE_URL).rstrip('/') + '/'

    def _headers(self) -> dict:
        # The builder panel (port 3000) authenticates via an AUTH_TOKEN cookie,
        # not a Bearer header — matching the working script.py. Sending Bearer
        # gets silently bounced to the login page (HTTP 200 HTML).
        return {
            'Cookie': f'AUTH_TOKEN={self.api_token}',
            'Content-Type': 'application/json',
        }

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = self.base_url + path
        try:
            response = requests.request(method, url, headers=self._headers(), timeout=30, **kwargs)
            response.raise_for_status()
            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError:
                return {'raw': response.text}
        except requests.HTTPError as error:
            data = None
            try:
                data = error.response.json()
            except ValueError:
                data = error.response.text
            raise BackendApiError(str(error), error.response.status_code, data) from error
        except requests.RequestException as error:
            raise BackendApiError(str(error)) from error

    def create(self, entity_name: str, payload: dict) -> dict:
        return self._request('post', f'create{entity_name}', json=payload)

    def update(self, entity_name: str, payload: dict) -> dict:
        # PUT update{Entity}; payload must be the full object including its id.
        return self._request('put', f'update{entity_name}', json=payload)

    def read(self, entity_name: str, entity_id: str) -> dict:
        return self._request('get', f'read{entity_name}/{entity_id}')

    def list(self, entity_name: str, query: Optional[dict] = None) -> dict:
        return self._request('get', f'list{entity_name}', params=query or {})
