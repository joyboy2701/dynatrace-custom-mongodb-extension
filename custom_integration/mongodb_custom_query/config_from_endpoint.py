import os
from typing import Any, Dict

from mongodb_custom_query.collector_core import ALLOWED_FREQUENCIES_MINUTES, validate_config
from mongodb_custom_query.query_config import parse_queries_from_endpoint


def _endpoint_int(endpoint: Dict[str, Any], key: str, default: int) -> int:
    value = endpoint.get(key)
    if value is None:
        return default
    return int(value)


def _endpoint_text(endpoint: Dict[str, Any], key: str, default: str) -> str:
    value = endpoint.get(key)
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _endpoint_bool(endpoint: Dict[str, Any], key: str, default: bool) -> bool:
    value = endpoint.get(key)

    if value is None:
        return default

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.strip().lower() in ["true", "1", "yes", "on"]

    return bool(value)


def config_from_endpoint(endpoint: Dict[str, Any]) -> Dict[str, Any]:
    queries = parse_queries_from_endpoint(endpoint)
    if not queries:
        raise ValueError(
            "No MongoDB queries configured. Add rows under MongoDB queries or paste JSON in Advanced: queries JSON."
        )

    use_credential_vault = _endpoint_bool(endpoint, "use_credential_vault", True)

    if use_credential_vault:
        credential_vault_id = endpoint.get("credentialVaultId")

        if not credential_vault_id:
            raise ValueError(
                "Credential Vault is enabled but no database credential was selected."
            )

        # Dynatrace should inject these from the selected USERNAME_PASSWORD credential.
        username = endpoint.get("username")
        password = endpoint.get("password")

        if not username or not password:
            raise ValueError(
                "Database username/password were not resolved from Credential Vault. "
                "Make sure the selected credential is type USERNAME_PASSWORD and has Extension authentication scope."
            )
    else:
        # Manual fallback mode.
        username = _endpoint_text(endpoint, "username", "")
        password = endpoint.get("password")

        if not username:
            raise ValueError("Database username is required when Credential Vault is disabled.")

        if not password:
            raise ValueError("Database password is required when Credential Vault is disabled.")

    config = {
        "database": {
            "type": "mongodb",
            "host": endpoint["host"],
            "port": int(endpoint.get("port", 27017)),
            "name": endpoint["database_name"],
            "auth_source": _endpoint_text(endpoint, "auth_source", endpoint["database_name"]),
            "tls": _endpoint_bool(endpoint, "tls", False),
            "tls_ca_file": _endpoint_text(endpoint, "tls_ca_file", ""),
            "user": username,
            "password": password,
            "query_timeout_seconds": _endpoint_int(endpoint, "query_timeout_seconds", 10),
        },
        "collector": {
            "log_source": _endpoint_text(endpoint, "log_source", "mongodb_custom_query"),
            "omit_log_timestamp": True,
            "result_mode": _endpoint_text(endpoint, "result_mode", "row_per_result"),
            "max_rows_per_query": _endpoint_int(endpoint, "max_rows_per_query", 1000),
            "send_summary_log": False,
            "send_summary_log_when_zero_rows": True,
            "send_summary_log_when_all_rows_deduped": _endpoint_bool(
                endpoint,
                "send_summary_log_when_all_rows_deduped",
                True,
            ),
            "send_error_log": True,
            "run_due_only": _endpoint_bool(endpoint, "run_due_only", True),
            "allowed_frequencies_minutes": ALLOWED_FREQUENCIES_MINUTES,
            "dedup": {
                "enabled": _endpoint_bool(endpoint, "dedup_enabled", True),
                "default_mode": endpoint.get("dedup_default_mode", "send_on_change"),
                "ttl_seconds": _endpoint_int(endpoint, "dedup_ttl_seconds", 7 * 24 * 60 * 60),
            },
        },
        "queries": queries,
    }

    # Preserve the shared delay from older monitoring configurations.
    if endpoint.get("schedule_offset_minutes") is not None:
        config["collector"]["schedule_offset_minutes"] = endpoint["schedule_offset_minutes"]

    validate_config(config)
    return config
