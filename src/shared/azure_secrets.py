"""
Optional Azure Key Vault lookup — one secret (KDmonk) holds every credential
that would otherwise live in .env, as a single JSON blob: {"AZURE_OPENAI_API_KEY":
"...", "FIVE_PAISA_PASSWORD": "...", ...}. Inert by default: nothing calls this
unless AZURE_KEY_VAULT_URL is set in .env, and any failure (SDK not installed,
no access, vault unreachable, malformed JSON) just logs a warning and returns
None rather than crashing startup — .env stays the source of truth if Key
Vault isn't reachable.
"""
from __future__ import annotations

import json
from typing import Optional

import structlog

log = structlog.get_logger(__name__)


def get_secret(name: str, vault_url: str) -> Optional[str]:
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError:
        log.warning(
            "azure_secrets.sdk_not_installed",
            hint="pip install azure-identity azure-keyvault-secrets",
        )
        return None

    try:
        client = SecretClient(vault_url=vault_url, credential=DefaultAzureCredential())
        return client.get_secret(name).value
    except Exception as exc:
        log.warning("azure_secrets.fetch_failed", name=name, error=str(exc))
        return None


def get_secret_json(name: str, vault_url: str) -> Optional[dict]:
    """Same as get_secret(), but parses the value as a JSON object of
    ENV_VAR_NAME -> value pairs."""
    raw = get_secret(name, vault_url)
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("azure_secrets.invalid_json", name=name, error=str(exc))
        return None
    if not isinstance(parsed, dict):
        log.warning("azure_secrets.not_an_object", name=name)
        return None
    return parsed
