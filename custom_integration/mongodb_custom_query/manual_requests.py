"""Durable, consume-before-execution requests, separate from scheduler state."""

import hashlib
import json
import sqlite3

from mongodb_custom_query.collector_core import get_state_file


def fingerprint(settings):
    return hashlib.sha256(json.dumps(
        settings, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()


def activation_fingerprint(activation):
    # ActivationConfig inherits dict but stores settings in its public config property.
    return fingerprint({
        "settings": activation.config,
        "version": activation.version,
        "enabled": activation.enabled,
        "description": activation.description,
        "featureSets": activation.feature_sets,
    })


def consume_requests(collector, monitoring_id, endpoint_id, queries, revision):
    if not monitoring_id:
        raise ValueError("Run immediately requires the SDK monitoring_config_id")
    state_file = get_state_file(collector)
    pending = set()
    with sqlite3.connect(state_file) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS manual_request_state (
            identity TEXT PRIMARY KEY, fingerprint TEXT NOT NULL)""")
        # Serialize read/claim across processes. Commit before any MongoDB operation.
        conn.execute("BEGIN IMMEDIATE")
        for query in queries:
            identity = fingerprint([monitoring_id, endpoint_id, query["name"]])
            row = conn.execute(
                "SELECT fingerprint FROM manual_request_state WHERE identity = ?",
                (identity,),
            ).fetchone()
            if (query.get("enabled", True) and query.get("run_immediately", False)
                    and (row is None or row[0] != revision)):
                pending.add(query["name"])
            # Observe OFF and disabled states too, including a return to earlier settings.
            conn.execute("""INSERT INTO manual_request_state VALUES (?, ?)
                ON CONFLICT(identity) DO UPDATE SET fingerprint=excluded.fingerprint""",
                (identity, revision))
    return pending
