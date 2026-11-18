import json
import time
import uuid
import hashlib
import sqlite3
import decimal
import os
import datetime
from typing import Any, Dict, List, Tuple
from itertools import islice
from pymongo import MongoClient
from bson import json_util
from bson.decimal128 import Decimal128
from bson.objectid import ObjectId

COLLECTOR_VERSION = "0.5.2"
WEEKLY_FREQUENCY = 10080
MONTHLY_FREQUENCY = 43200
ALLOWED_FREQUENCIES_MINUTES = [
    1, 5, 10, 15, 30, 60, 120, 180, 240, 360, 480, 720, 1440,
    WEEKLY_FREQUENCY, MONTHLY_FREQUENCY,
]

# Dynatrace SDK RFC3339 (no fractional seconds); matches extension metric/event timestamps.
_LOG_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def format_log_timestamp() -> str:
    """UTC RFC3339 timestamp accepted by Dynatrace log ingest."""
    return datetime.datetime.now(datetime.timezone.utc).strftime(_LOG_TIMESTAMP_FORMAT)


def resolve_log_timestamp(collector_config: Dict[str, Any]) -> str | None:
    """
    Return a log event timestamp, or None to let ingest assign cluster time.

    Extensions on ActiveGate should omit timestamps when the host clock may
    drift from cluster time (avoids 'out of correct time range' rejections).
    """
    if collector_config.get("omit_log_timestamp", False):
        return None
    return format_log_timestamp()


# -----------------------------
# JSON / value helpers
# -----------------------------

def json_serializer(value: Any):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()

    if isinstance(value, Decimal128):
        return str(value.to_decimal())

    if isinstance(value, decimal.Decimal):
        return float(value)

    return str(value)


def canonical_json(value: Any) -> str:
    """
    Stable JSON string used for hashing/dedup.
    """
    return json.dumps(
        value,
        default=json_serializer,
        sort_keys=True,
        separators=(",", ":"),
    )


def sha256_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_config(config: Dict[str, Any]):
    if not config:
        raise ValueError("configuration is empty")

    if "database" not in config:
        raise ValueError("configuration is missing required 'database' section")

    if "queries" not in config:
        raise ValueError("configuration is missing required 'queries' section")

    if not isinstance(config["queries"], list) or len(config["queries"]) == 0:
        raise ValueError("configuration must define at least one query in 'queries'")

    db_config = config["database"]
    required_db_fields = ["type", "host", "port", "name", "user"]

    for field in required_db_fields:
        if field not in db_config:
            raise ValueError(f"database.{field} is required in config")

    if not db_config.get("password") and not db_config.get("password_env"):
        raise ValueError("database.password or database.password_env is required in config")

    query_names = set()

    for index, query in enumerate(config["queries"], start=1):
        if "name" not in query:
            raise ValueError(f"queries[{index}] is missing required 'name'")

        if not query.get("collection"):
            raise ValueError(f"queries[{index}] requires collection")
        validate_pipeline(query.get("pipeline", []))

        query_name = query["name"]

        if query_name in query_names:
            raise ValueError(f"Duplicate query name found: {query_name}")

        query_names.add(query_name)


def normalize_field_name(field_name: str) -> str:
    """
    Convert document field names into safe Dynatrace field names.

    Examples:
      "Order Count"  -> "order_count"
      "total-amount" -> "total_amount"
      "1 value"      -> "field_1_value"
    """
    normalized = field_name.strip().lower()

    safe_chars = []
    for char in normalized:
        if char.isalnum() or char == "_":
            safe_chars.append(char)
        else:
            safe_chars.append("_")

    normalized = "".join(safe_chars)

    while "__" in normalized:
        normalized = normalized.replace("__", "_")

    normalized = normalized.strip("_")

    if not normalized:
        normalized = "field"

    if normalized[0].isdigit():
        normalized = f"field_{normalized}"

    return normalized


def normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize document field names and values.
    """
    normalized_row = {}
    used_keys = set()

    for key, value in dict(row).items():
        safe_key = normalize_field_name(str(key))
        final_key = safe_key

        counter = 2
        while final_key in used_keys:
            final_key = f"{safe_key}_{counter}"
            counter += 1

        used_keys.add(final_key)

        if isinstance(value, (datetime.datetime, datetime.date)):
            normalized_row[final_key] = value.isoformat()
        elif isinstance(value, ObjectId):
            normalized_row[final_key] = str(value)
        elif isinstance(value, Decimal128):
            normalized_row[final_key] = str(value.to_decimal())
        elif isinstance(value, decimal.Decimal):
            normalized_row[final_key] = float(value)
        elif isinstance(value, (dict, list, tuple)):
            normalized_row[final_key] = json.dumps(value, default=json_serializer)
        elif value is None or isinstance(value, (str, int, float, bool)):
            normalized_row[final_key] = value
        else:
            normalized_row[final_key] = json_serializer(value)

    return normalized_row


# -----------------------------
# MongoDB read-only query validation
# -----------------------------


def validate_pipeline(pipeline):
    """Parse an aggregation pipeline, rejecting write stages at any depth."""
    if isinstance(pipeline, str):
        pipeline = json_util.loads(pipeline)
    if not isinstance(pipeline, list):
        raise ValueError("pipeline must be a JSON array")

    def check(value):
        if isinstance(value, dict):
            if "$out" in value or "$merge" in value:
                raise ValueError("Only read-only pipelines are allowed; $out/$merge are blocked")
            for item in value.values():
                check(item)
        elif isinstance(value, list):
            for item in value:
                check(item)

    for stage in pipeline:
        if not isinstance(stage, dict) or len(stage) != 1:
            raise ValueError("Each pipeline stage must be a single-key object")
        check(stage)
    return pipeline


# -----------------------------
# Database connection
# -----------------------------

def connect_to_mongodb(db_config: Dict[str, Any]):
    password = db_config.get("password")

    if not password:
        password_env = db_config.get("password_env")
        password = os.getenv(password_env) if password_env else None
        if not password:
            if password_env:
                raise ValueError(
                    f"Missing database password in environment variable: {password_env}"
                )
            raise ValueError("Missing database password in config")

    timeout_ms = int(db_config.get("connect_timeout_seconds", 10)) * 1000
    options = {
        "username": db_config["user"],
        "password": password,
        "authSource": db_config.get("auth_source") or db_config["name"],
        "connectTimeoutMS": timeout_ms,
        "serverSelectionTimeoutMS": timeout_ms,
        "socketTimeoutMS": int(db_config.get("query_timeout_seconds", 10)) * 1000,
        "tls": bool(db_config.get("tls", False)),
    }
    if db_config.get("tls_ca_file"):
        options["tlsCAFile"] = db_config["tls_ca_file"]
    client = MongoClient(db_config["host"], int(db_config.get("port", 27017)), **options)
    try:
        client.admin.command("ping")
    except Exception:
        client.close()
        raise
    return client


# -----------------------------
# Frequency logic
# -----------------------------

def should_run_query(query_config: Dict[str, Any], collector_config: Dict[str, Any]) -> bool:
    """
    If run_due_only=false:
      every enabled query runs every time the script runs.

    If run_due_only=true:
      hourly and calendar frequencies run during minute 05 of their scheduled UTC hour.
      shorter frequencies use elapsed time since the last run.
    """

    allowed_frequencies = collector_config.get(
        "allowed_frequencies_minutes",
        ALLOWED_FREQUENCIES_MINUTES,
    )

    raw_frequency = query_config.get("frequency_minutes", 1)

    try:
        frequency_minutes = int(str(raw_frequency).strip())
    except (TypeError, ValueError):
        raise ValueError(
            f"Invalid frequency_minutes='{raw_frequency}' for query "
            f"'{query_config.get('name', 'unknown')}'. "
            f"Allowed values are only: {allowed_frequencies}"
        )

    if frequency_minutes not in allowed_frequencies:
        raise ValueError(
            f"Invalid frequency_minutes={frequency_minutes} for query "
            f"'{query_config.get('name', 'unknown')}'. "
            f"Allowed values are only: {allowed_frequencies}"
        )

    run_due_only = collector_config.get("run_due_only", False)

    if not run_due_only:
        return True

    query_name = query_config["name"]

    if frequency_minutes >= 60 and frequency_minutes % 60 == 0:
        now = datetime.datetime.now(datetime.timezone.utc)
        if now.minute != 5:
            return False

        if frequency_minutes == WEEKLY_FREQUENCY:
            scheduled_hour = now.weekday() == 0 and now.hour == 0
        elif frequency_minutes == MONTHLY_FREQUENCY:
            scheduled_hour = now.day == 1 and now.hour == 0
        else:
            scheduled_hour = now.hour % (frequency_minutes // 60) == 0

        if not scheduled_hour:
            return False

        scheduled_at = now.replace(second=0, microsecond=0)
        last_ran_at = get_query_last_ran_at(collector_config, query_name)
        return last_ran_at is None or last_ran_at < scheduled_at

    last_ran_at = get_query_last_ran_at(collector_config, query_name)

    if last_ran_at is None:
        return True

    now = datetime.datetime.now(datetime.timezone.utc)
    elapsed_seconds = (now - last_ran_at).total_seconds()
    scheduler_grace_seconds = int(
    collector_config.get("scheduler_grace_seconds", 5)
    )

    due_after_seconds = max(
    0,
    (frequency_minutes * 60) - scheduler_grace_seconds,
    )

    return elapsed_seconds >= due_after_seconds
# -----------------------------
# Dedup state: SQLite
# -----------------------------

def get_state_file(collector_config: Dict[str, Any]) -> str:
    """
    Return persistent SQLite dedup state file path.

    Default path is under /var/lib so it survives:
    - extensionsmodule restart
    - ActiveGate restart
    - EC2 reboot
    - EC2 stop/start

    It can still be overridden from collector_config.dedup.state_file.
    """
    dedup_config = collector_config.get("dedup", {})

    default_state_file = (
        "/var/lib/dynatrace/remotepluginmodule/agent/state/"
        "mongodb_custom_query/dedup_state.db"
    )

    state_file = dedup_config.get("state_file", default_state_file)

    state_dir = os.path.dirname(state_file)

    if state_dir:
        os.makedirs(state_dir, exist_ok=True)

    return state_file


def init_dedup_db(state_file: str):
    state_dir = os.path.dirname(state_file)

    if state_dir:
        os.makedirs(state_dir, exist_ok=True)

    with sqlite3.connect(state_file) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dedup_state (
                query_name TEXT NOT NULL,
                dedup_key TEXT NOT NULL,
                row_hash TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                PRIMARY KEY (query_name, dedup_key)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS query_run_state (
                query_name TEXT NOT NULL PRIMARY KEY,
                last_ran_at TEXT NOT NULL
            )
            """
        )

        conn.commit()

def utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_utc_iso(value: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def get_query_last_ran_at(
    collector_config: Dict[str, Any],
    query_name: str,
) -> datetime.datetime | None:
    state_file = get_state_file(collector_config)
    init_dedup_db(state_file)

    with sqlite3.connect(state_file) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT last_ran_at
            FROM query_run_state
            WHERE query_name = ?
            """,
            (query_name,),
        )
        row = cursor.fetchone()

    if row is None:
        return None

    return parse_utc_iso(row[0])


def mark_query_ran(
    collector_config: Dict[str, Any],
    query_config: Dict[str, Any],
) -> None:
    state_file = get_state_file(collector_config)
    init_dedup_db(state_file)

    query_name = query_config["name"]
    now = utc_now_iso()

    with sqlite3.connect(state_file) as conn:
        conn.execute(
            """
            INSERT INTO query_run_state (query_name, last_ran_at)
            VALUES (?, ?)
            ON CONFLICT(query_name)
            DO UPDATE SET last_ran_at = excluded.last_ran_at
            """,
            (query_name, now),
        )
        conn.commit()

def cleanup_old_dedup_entries(state_file: str, ttl_seconds: int):
    """
    Remove old dedup entries so the SQLite DB does not grow forever.
    """
    if ttl_seconds <= 0:
        return

    cutoff = (
        datetime.datetime.utcnow()
        - datetime.timedelta(seconds=ttl_seconds)
    ).isoformat() + "Z"

    with sqlite3.connect(state_file) as conn:
        conn.execute(
            """
            DELETE FROM dedup_state
            WHERE last_seen_at < ?
            """,
            (cutoff,),
        )
        conn.commit()

def get_effective_dedup_config(
    collector_config: Dict[str, Any],
    query_config: Dict[str, Any],
) -> Dict[str, Any]:
    global_dedup = collector_config.get("dedup", {})
    query_dedup = query_config.get("dedup", {})

    enabled = query_dedup.get("enabled", global_dedup.get("enabled", False))

    mode = query_dedup.get("mode", global_dedup.get("default_mode", "send_on_change"))
    key_fields = query_dedup.get("key_fields", [])

    normalized_key_fields = [normalize_field_name(str(field)) for field in key_fields]

    if mode not in ["send_once", "send_on_change"]:
        raise ValueError(
            f"Invalid dedup mode: {mode}. "
            f"Allowed values: send_once, send_on_change"
        )

    return {
        "enabled": bool(enabled),
        "mode": mode,
        "key_fields": normalized_key_fields,
    }


def build_dedup_key(
    query_name: str,
    normalized_row: Dict[str, Any],
    key_fields: List[str],
) -> str:
    """
    Build dedup key from configured key fields.

    If no key_fields are provided, fallback to the full row.
    For production queries, key_fields are strongly recommended.
    """
    if key_fields:
        key_data = {}

        for field in key_fields:
            if field not in normalized_row:
                raise ValueError(
                    f"Dedup key field '{field}' not found in result document. "
                    f"Available fields: {list(normalized_row.keys())}"
                )

            key_data[field] = normalized_row.get(field)

        return sha256_hash(
            {
                "query_name": query_name,
                "key_data": key_data,
            }
        )

    return sha256_hash(
        {
            "query_name": query_name,
            "full_row": normalized_row,
        }
    )


def should_send_by_dedup(
    state_file: str,
    query_name: str,
    dedup_key: str,
    row_hash: str,
    mode: str,
) -> bool:
    """
    Returns True if row/result should be sent.
    Updates SQLite state.
    """
    now = datetime.datetime.utcnow().isoformat() + "Z"

    with sqlite3.connect(state_file) as conn:
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT row_hash
            FROM dedup_state
            WHERE query_name = ?
              AND dedup_key = ?
            """,
            (query_name, dedup_key),
        )

        existing = cursor.fetchone()

        if existing is None:
            cursor.execute(
                """
                INSERT INTO dedup_state (
                    query_name,
                    dedup_key,
                    row_hash,
                    first_seen_at,
                    last_seen_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (query_name, dedup_key, row_hash, now, now),
            )
            conn.commit()
            return True

        existing_hash = existing[0]

        if mode == "send_once":
            cursor.execute(
                """
                UPDATE dedup_state
                SET last_seen_at = ?
                WHERE query_name = ?
                  AND dedup_key = ?
                """,
                (now, query_name, dedup_key),
            )
            conn.commit()
            return False

        if mode == "send_on_change":
            if existing_hash == row_hash:
                cursor.execute(
                    """
                    UPDATE dedup_state
                    SET last_seen_at = ?
                    WHERE query_name = ?
                      AND dedup_key = ?
                    """,
                    (now, query_name, dedup_key),
                )
                conn.commit()
                return False

            cursor.execute(
                """
                UPDATE dedup_state
                SET row_hash = ?,
                    last_seen_at = ?
                WHERE query_name = ?
                  AND dedup_key = ?
                """,
                (row_hash, now, query_name, dedup_key),
            )
            conn.commit()
            return True

    return True


def apply_row_dedup(
    collector_config: Dict[str, Any],
    query_config: Dict[str, Any],
    normalized_rows: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int]:
    dedup_config = get_effective_dedup_config(collector_config, query_config)

    if not dedup_config["enabled"]:
        return normalized_rows, 0

    state_file = get_state_file(collector_config)
    init_dedup_db(state_file)

    ttl_seconds = int(
    collector_config.get("dedup", {}).get("ttl_seconds", 7 * 24 * 60 * 60)
    )
    cleanup_old_dedup_entries(state_file, ttl_seconds)

    query_name = query_config["name"]
    mode = dedup_config["mode"]
    key_fields = dedup_config["key_fields"]

    rows_to_send = []
    skipped_count = 0

    for row in normalized_rows:
        dedup_key = build_dedup_key(query_name, row, key_fields)
        row_hash = sha256_hash(row)

        should_send = should_send_by_dedup(
            state_file=state_file,
            query_name=query_name,
            dedup_key=dedup_key,
            row_hash=row_hash,
            mode=mode,
        )

        if should_send:
            rows_to_send.append(row)
        else:
            skipped_count += 1

    return rows_to_send, skipped_count


def apply_result_set_dedup(
    collector_config: Dict[str, Any],
    query_config: Dict[str, Any],
    normalized_rows: List[Dict[str, Any]],
) -> Tuple[bool, int]:
    """
    Dedup for nested_json mode.
    If the whole result set is unchanged, skip sending the nested JSON payload.
    """
    dedup_config = get_effective_dedup_config(collector_config, query_config)

    if not dedup_config["enabled"]:
        return True, 0

    state_file = get_state_file(collector_config)
    init_dedup_db(state_file)

    ttl_seconds = int(
    collector_config.get("dedup", {}).get("ttl_seconds", 7 * 24 * 60 * 60)
    )
    cleanup_old_dedup_entries(state_file, ttl_seconds)

    query_name = query_config["name"]
    mode = dedup_config["mode"]

    dedup_key = "__result_set__"
    row_hash = sha256_hash(normalized_rows)

    should_send = should_send_by_dedup(
        state_file=state_file,
        query_name=query_name,
        dedup_key=dedup_key,
        row_hash=row_hash,
        mode=mode,
    )

    skipped_count = 0 if should_send else len(normalized_rows)

    return should_send, skipped_count


# -----------------------------
# Payload builders
# -----------------------------

def get_effective_result_mode(
    collector_config: Dict[str, Any],
    query_config: Dict[str, Any],
) -> str:
    result_mode = query_config.get(
        "result_mode",
        collector_config.get("result_mode", "row_per_result"),
    )

    allowed_result_modes = ["row_per_result", "nested_json"]

    if result_mode not in allowed_result_modes:
        raise ValueError(
            f"Invalid result_mode={result_mode}. "
            f"Allowed values: {allowed_result_modes}"
        )

    return result_mode


def build_common_fields(
    db_config: Dict[str, Any],
    collector_config: Dict[str, Any],
    query_config: Dict[str, Any],
    run_id: str,
    timestamp: str | None,
):
    log_source = collector_config.get("log_source", "mongodb_custom_query")
    raw_frequency = query_config.get("frequency_minutes", 1)

    fields = {
        "loglevel": "INFO",
        "log.source": log_source,
        "collector.name": "custom_mongodb_collector",
        "collector.version": COLLECTOR_VERSION,
        "query.run_id": run_id,
        "db.type": db_config.get("type"),
        "db.name": db_config.get("name"),
        "query.name": query_config.get("name"),
    }

    try:
        fields["query.frequency_minutes"] = int(str(raw_frequency).strip())
    except (TypeError, ValueError):
        fields["query.frequency_minutes.raw"] = str(raw_frequency)

    if timestamp is not None:
        fields["timestamp"] = timestamp

    return fields

def build_summary_payload(
    common_fields: Dict[str, Any],
    row_count: int,
    sent_row_count: int,
    dedup_skipped_count: int,
    rows_truncated: bool,
    max_rows: int,
    duration_ms: float,
):
    return {
        **common_fields,
        "content": "custom mongodb query execution summary",
        "event.type": "summary",
        "query.status": "success",
        "row_count": row_count,
        "sent_row_count": sent_row_count,
        "dedup_skipped_count": dedup_skipped_count,
        "rows_truncated": rows_truncated,
        "max_rows_per_query": max_rows,
        "duration_ms": duration_ms,
    }


def build_row_payloads(
    common_fields: Dict[str, Any],
    rows_to_send: List[Dict[str, Any]],
    original_row_count: int,
    dedup_skipped_count: int,
    rows_truncated: bool,
    duration_ms: float,
) -> List[Dict[str, Any]]:
    payloads = []

    for index, row in enumerate(rows_to_send, start=1):
        row_payload = {
            **common_fields,
            "content": "custom mongodb query result row",
            "event.type": "row",
            "query.status": "success",
            "result.mode": "row_per_result",
            "row_index": index,
            "row_count": original_row_count,
            "sent_row_count": len(rows_to_send),
            "dedup_skipped_count": dedup_skipped_count,
            "rows_truncated": rows_truncated,
            "duration_ms": duration_ms,
        }

        for key, value in row.items():
            row_payload[f"result.{key}"] = value

        payloads.append(row_payload)

    return payloads


def build_nested_json_payload(
    common_fields: Dict[str, Any],
    normalized_rows: List[Dict[str, Any]],
    dedup_skipped_count: int,
    rows_truncated: bool,
    max_rows: int,
    duration_ms: float,
):
    return {
        **common_fields,
        "content": "custom mongodb query result set",
        "event.type": "result_set",
        "query.status": "success",
        "result.mode": "nested_json",
        "row_count": len(normalized_rows),
        "sent_row_count": len(normalized_rows),
        "dedup_skipped_count": dedup_skipped_count,
        "rows_truncated": rows_truncated,
        "max_rows_per_query": max_rows,
        "duration_ms": duration_ms,
        "result.rows_json": json.dumps(normalized_rows, default=json_serializer),
    }


# -----------------------------
# Query execution
# -----------------------------

def run_query(
    connection,
    db_config: Dict[str, Any],
    collector_config: Dict[str, Any],
    query_config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    pipeline = validate_pipeline(query_config.get("pipeline", []))

    timeout_seconds = int(db_config.get("query_timeout_seconds", 10))

    max_rows = int(collector_config.get("max_rows_per_query", 1000))
    result_mode = get_effective_result_mode(collector_config, query_config)

    send_summary_log = bool(collector_config.get("send_summary_log", False))
    send_summary_log_when_zero_rows = bool(
        collector_config.get("send_summary_log_when_zero_rows", True)
    )
    send_summary_log_when_all_rows_deduped = bool(
        collector_config.get("send_summary_log_when_all_rows_deduped", True)
    )

    run_id = str(uuid.uuid4())
    timestamp = resolve_log_timestamp(collector_config)

    started_at = time.time()

    if timeout_seconds <= 0:
        raise ValueError("query_timeout_seconds must be positive")
    if max_rows <= 0:
        raise ValueError("max_rows_per_query must be positive")

    collection = connection[db_config["name"]][query_config["collection"]]
    cursor = collection.aggregate(
        pipeline + [{"$limit": max_rows + 1}],
        maxTimeMS=timeout_seconds * 1000,
    )
    try:
        fetched_rows = list(islice(cursor, max_rows + 1))
    finally:
        cursor.close()

    duration_ms = round((time.time() - started_at) * 1000, 2)
    rows_truncated = len(fetched_rows) > max_rows
    row_dicts = fetched_rows[:max_rows]
    normalized_rows = [normalize_row(row) for row in row_dicts]

    original_row_count = len(normalized_rows)

    common_fields = build_common_fields(
        db_config=db_config,
        collector_config=collector_config,
        query_config=query_config,
        run_id=run_id,
        timestamp=timestamp,
    )

    payloads: List[Dict[str, Any]] = []

    if result_mode == "row_per_result":
        rows_to_send, dedup_skipped_count = apply_row_dedup(
            collector_config=collector_config,
            query_config=query_config,
            normalized_rows=normalized_rows,
        )

        payloads.extend(
            build_row_payloads(
                common_fields=common_fields,
                rows_to_send=rows_to_send,
                original_row_count=original_row_count,
                dedup_skipped_count=dedup_skipped_count,
                rows_truncated=rows_truncated,
                duration_ms=duration_ms,
            )
        )

        sent_row_count = len(rows_to_send)

    else:
        should_send_result_set, dedup_skipped_count = apply_result_set_dedup(
            collector_config=collector_config,
            query_config=query_config,
            normalized_rows=normalized_rows,
        )

        sent_row_count = original_row_count if should_send_result_set and original_row_count > 0 else 0

        if should_send_result_set and original_row_count > 0:
            payloads.append(
                build_nested_json_payload(
                    common_fields=common_fields,
                    normalized_rows=normalized_rows,
                    dedup_skipped_count=dedup_skipped_count,
                    rows_truncated=rows_truncated,
                    max_rows=max_rows,
                    duration_ms=duration_ms,
                )
            )

    should_send_summary = (
        send_summary_log
        or (original_row_count == 0 and send_summary_log_when_zero_rows)
        or (
            original_row_count > 0
            and sent_row_count == 0
            and send_summary_log_when_all_rows_deduped
        )
    )

    if should_send_summary:
        payloads.append(
            build_summary_payload(
                common_fields=common_fields,
                row_count=original_row_count,
                sent_row_count=sent_row_count,
                dedup_skipped_count=dedup_skipped_count,
                rows_truncated=rows_truncated,
                max_rows=max_rows,
                duration_ms=duration_ms,
            )
        )

    return payloads

def format_error_message(error: Exception) -> str:
    """
    Return a clean error message for Dynatrace logs.
    Format MongoDB driver errors with the numeric MongoDB error code when available.
    """

    code = getattr(error, "code", None)
    if code is not None:
        return f"{error} (MongoDB error {code})"

    return str(error)

def build_error_payloads(
    db_config: Dict[str, Any],
    collector_config: Dict[str, Any],
    query_config: Dict[str, Any],
    error: Exception,
) -> List[Dict[str, Any]]:
    send_error_log = bool(collector_config.get("send_error_log", True))

    if not send_error_log:
        return []

    run_id = str(uuid.uuid4())
    timestamp = resolve_log_timestamp(collector_config)

    common_fields = build_common_fields(
        db_config=db_config,
        collector_config=collector_config,
        query_config=query_config,
        run_id=run_id,
        timestamp=timestamp,
    )

    return [
        {
            **common_fields,
            "content": "custom mongodb query execution error",
            "loglevel": "ERROR",
            "event.type": "error",
            "query.status": "error",
            "error.message": format_error_message(error),
            "error.raw": str(error),
        }
    ]
