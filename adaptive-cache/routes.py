import uuid
import time
from flask import Blueprint, request, jsonify, make_response
from database import (
    db_get_record,
    db_create_record,
    db_update_record,
    db_delete_record,
    get_db_connection,
)
from cache import AdaptiveHybridCacheManager
from config import Config
import psycopg2

api_bp = Blueprint("api", __name__)

# Instantiate Adaptive Hybrid Cache Manager
adaptive_mgr = AdaptiveHybridCacheManager()

# Application-level metrics counters
metrics = {
    "requests_total": 0,
    "reads_total": 0,
    "writes_total": 0,
    "db_queries_total": 0,
    "db_query_time_ms_sum": 0.0,
    "errors_total": 0,
}


def _add_diagnostic_headers(
    response,
    req_id: str,
    cache_status: str,
    cache_layer: str,
    admission_status: str,
    adaptive_ttl: int,
    db_ms: float,
    l1_ms: float,
    l2_ms: float,
    policy_ms: float,
    total_ms: float,
):
    """Sets standard research diagnostic headers defined in Section 9."""
    response.headers["X-Cache-Status"] = cache_status
    response.headers["X-Cache-Layer"] = cache_layer
    response.headers["X-Admission-Status"] = admission_status
    response.headers["X-Adaptive-TTL"] = str(adaptive_ttl)
    response.headers["X-Request-ID"] = req_id
    response.headers["X-Database-Time-Ms"] = f"{db_ms:.2f}"
    response.headers["X-L1-Time-Ms"] = f"{l1_ms:.2f}"
    response.headers["X-L2-Time-Ms"] = f"{l2_ms:.2f}"
    response.headers["X-Policy-Time-Ms"] = f"{policy_ms:.2f}"
    response.headers["X-Total-Time-Ms"] = f"{total_ms:.2f}"
    return response


@api_bp.route("/", methods=["GET"])
def index():
    """Root endpoint for sanity check and discovery."""
    return jsonify({
        "status": "healthy",
        "config_id": Config.CONFIG_ID,
        "config_name": Config.CONFIG_NAME,
        "endpoints": {
            "docs": "/docs",
            "health": "/health",
            "metrics": "/metrics",
            "records": "/records"
        }
    }), 200


@api_bp.route("/health", methods=["GET"])
def health():
    """Health check without warming caches."""
    db_status = "unknown"
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                db_status = "connected"
    except Exception as e:
        db_status = f"error: {str(e)}"

    redis_ok, redis_ping_ms = adaptive_mgr.l2.ping()
    redis_status = f"connected ({redis_ping_ms:.2f}ms)" if redis_ok else "unreachable/unconfigured"

    is_healthy = (db_status == "connected")
    return jsonify({
        "status": "healthy" if is_healthy else "degraded",
        "config_id": Config.CONFIG_ID,
        "config_name": Config.CONFIG_NAME,
        "database": db_status,
        "redis_l2": redis_status,
        "l1_memory_entries": adaptive_mgr.l1.stats()["entry_count"],
    }), 200 if is_healthy else 503


@api_bp.route("/records/<int:record_id>", methods=["GET"])
def get_record(record_id):
    """
    Adaptive Hybrid Read Path:
    1. Check L1 Memory (Hit -> return L1)
    2. Check L2 Redis (Hit -> back-populate L1, return L2)
    3. On Complete Miss: Query PostgreSQL
       -> Evaluate Admission Policy:
          - If frequency < threshold: REJECT from caching
          - If frequency >= threshold: ADMIT and calculate Dynamic TTL
    """
    start_time = time.perf_counter()
    req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    metrics["requests_total"] += 1
    metrics["reads_total"] += 1

    # 1 & 2: Check L1, then L2
    cached_record, status, layer, l1_ms, l2_ms, policy_ms = adaptive_mgr.get(record_id)

    if status == "HIT":
        total_time_ms = (time.perf_counter() - start_time) * 1000.0
        resp = make_response(jsonify(cached_record), 200)
        return _add_diagnostic_headers(
            resp, req_id, "HIT", layer, "N/A", 0, 0.0, l1_ms, l2_ms, policy_ms, total_time_ms
        )

    # 3: Miss across both layers, query PostgreSQL
    try:
        adaptive_mgr.db_misses_total += 1
        record, db_time_ms = db_get_record(record_id)
        metrics["db_queries_total"] += 1
        metrics["db_query_time_ms_sum"] += db_time_ms

        if not record:
            total_time_ms = (time.perf_counter() - start_time) * 1000.0
            resp = make_response(jsonify({"error": "Record not found"}), 404)
            return _add_diagnostic_headers(
                resp, req_id, "MISS", "DB", "REJECTED", 0, db_time_ms, l1_ms, l2_ms, policy_ms, total_time_ms
            )

        # 4: Adaptive Admission & Dynamic TTL Evaluation
        admitted, chosen_ttl, pop_l1_ms, pop_l2_ms, eval_pol_ms = adaptive_mgr.evaluate_and_populate(
            record_id, record
        )
        policy_ms += eval_pol_ms
        l1_ms += pop_l1_ms
        l2_ms += pop_l2_ms

        total_time_ms = (time.perf_counter() - start_time) * 1000.0
        adm_status = "ADMITTED" if admitted else "REJECTED"

        resp = make_response(jsonify(record), 200)
        return _add_diagnostic_headers(
            resp, req_id, "MISS", "DB", adm_status, chosen_ttl, db_time_ms, l1_ms, l2_ms, policy_ms, total_time_ms
        )

    except Exception as e:
        metrics["errors_total"] += 1
        total_time_ms = (time.perf_counter() - start_time) * 1000.0
        resp = make_response(jsonify({"error": str(e)}), 500)
        return _add_diagnostic_headers(
            resp, req_id, "MISS", "NONE", "ERROR", 0, 0.0, l1_ms, l2_ms, policy_ms, total_time_ms
        )


@api_bp.route("/records", methods=["POST"])
def create_record():
    """Create a record in PostgreSQL."""
    start_time = time.perf_counter()
    req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    metrics["requests_total"] += 1
    metrics["writes_total"] += 1

    data = request.get_json(silent=True) or {}
    record_id = data.get("id")
    category = data.get("category")
    payload = data.get("payload")

    if record_id is None or category is None or payload is None:
        return make_response(jsonify({"error": "Fields 'id', 'category', and 'payload' are required"}), 400)

    try:
        record, db_time_ms = db_create_record(int(record_id), str(category), payload)
        metrics["db_queries_total"] += 1
        metrics["db_query_time_ms_sum"] += db_time_ms
        total_time_ms = (time.perf_counter() - start_time) * 1000.0

        resp = make_response(jsonify(record), 201)
        return _add_diagnostic_headers(resp, req_id, "BYPASS", "DB", "N/A", 0, db_time_ms, 0.0, 0.0, 0.0, total_time_ms)
    except psycopg2.IntegrityError:
        metrics["errors_total"] += 1
        total_time_ms = (time.perf_counter() - start_time) * 1000.0
        resp = make_response(jsonify({"error": f"Record with ID {record_id} already exists"}), 409)
        return _add_diagnostic_headers(resp, req_id, "BYPASS", "NONE", "N/A", 0, 0.0, 0.0, 0.0, 0.0, total_time_ms)
    except Exception as e:
        metrics["errors_total"] += 1
        total_time_ms = (time.perf_counter() - start_time) * 1000.0
        resp = make_response(jsonify({"error": str(e)}), 500)
        return _add_diagnostic_headers(resp, req_id, "BYPASS", "NONE", "N/A", 0, 0.0, 0.0, 0.0, 0.0, total_time_ms)


@api_bp.route("/records/<int:record_id>", methods=["PUT"])
def update_record(record_id):
    """Update record in PostgreSQL and invalidate L2, L1, and frequency tracker."""
    start_time = time.perf_counter()
    req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    metrics["requests_total"] += 1
    metrics["writes_total"] += 1

    data = request.get_json(silent=True) or {}
    category = data.get("category")
    payload = data.get("payload")

    if category is None or payload is None:
        return make_response(jsonify({"error": "Fields 'category' and 'payload' are required"}), 400)

    try:
        record, db_time_ms = db_update_record(record_id, str(category), payload)
        metrics["db_queries_total"] += 1
        metrics["db_query_time_ms_sum"] += db_time_ms

        l1_inv_ms, l2_inv_ms = adaptive_mgr.invalidate(record_id)
        total_time_ms = (time.perf_counter() - start_time) * 1000.0

        if not record:
            resp = make_response(jsonify({"error": "Record not found"}), 404)
            return _add_diagnostic_headers(resp, req_id, "BYPASS", "DB", "N/A", 0, db_time_ms, l1_inv_ms, l2_inv_ms, 0.0, total_time_ms)

        resp = make_response(jsonify(record), 200)
        return _add_diagnostic_headers(resp, req_id, "BYPASS", "DB", "N/A", 0, db_time_ms, l1_inv_ms, l2_inv_ms, 0.0, total_time_ms)
    except Exception as e:
        metrics["errors_total"] += 1
        total_time_ms = (time.perf_counter() - start_time) * 1000.0
        resp = make_response(jsonify({"error": str(e)}), 500)
        return _add_diagnostic_headers(resp, req_id, "BYPASS", "NONE", "N/A", 0, 0.0, 0.0, 0.0, 0.0, total_time_ms)


@api_bp.route("/records/<int:record_id>", methods=["DELETE"])
def delete_record(record_id):
    """Delete record from PostgreSQL and invalidate L2, L1, and frequency tracker."""
    start_time = time.perf_counter()
    req_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    metrics["requests_total"] += 1
    metrics["writes_total"] += 1

    try:
        deleted, db_time_ms = db_delete_record(record_id)
        metrics["db_queries_total"] += 1
        metrics["db_query_time_ms_sum"] += db_time_ms

        l1_inv_ms, l2_inv_ms = adaptive_mgr.invalidate(record_id)
        total_time_ms = (time.perf_counter() - start_time) * 1000.0

        if not deleted:
            resp = make_response(jsonify({"error": "Record not found"}), 404)
            return _add_diagnostic_headers(resp, req_id, "BYPASS", "DB", "N/A", 0, db_time_ms, l1_inv_ms, l2_inv_ms, 0.0, total_time_ms)

        resp = make_response(jsonify({"message": f"Record {record_id} deleted successfully"}), 200)
        return _add_diagnostic_headers(resp, req_id, "BYPASS", "DB", "N/A", 0, db_time_ms, l1_inv_ms, l2_inv_ms, 0.0, total_time_ms)
    except Exception as e:
        metrics["errors_total"] += 1
        total_time_ms = (time.perf_counter() - start_time) * 1000.0
        resp = make_response(jsonify({"error": str(e)}), 500)
        return _add_diagnostic_headers(resp, req_id, "BYPASS", "NONE", "N/A", 0, 0.0, 0.0, 0.0, 0.0, total_time_ms)


@api_bp.route("/metrics", methods=["GET"])
def get_metrics():
    """Returns application counters, adaptive admission stats, TTL stats, and hybrid ratios."""
    db_avg_ms = (
        metrics["db_query_time_ms_sum"] / metrics["db_queries_total"]
        if metrics["db_queries_total"] > 0
        else 0.0
    )
    return jsonify({
        "config_id": Config.CONFIG_ID,
        "config_name": Config.CONFIG_NAME,
        "requests_total": metrics["requests_total"],
        "reads_total": metrics["reads_total"],
        "writes_total": metrics["writes_total"],
        "db_queries_total": metrics["db_queries_total"],
        "db_avg_latency_ms": round(db_avg_ms, 2),
        "errors_total": metrics["errors_total"],
        "adaptive_cache": adaptive_mgr.stats(),
    }), 200


@api_bp.route("/openapi.json", methods=["GET"])
def openapi_spec():
    """Serves OpenAPI 3.0 specification per Section 4.1."""
    standard_headers = {
        "X-Cache-Status": {"schema": {"type": "string", "example": "HIT"}, "description": "Cache evaluation status (HIT, MISS, BYPASS)"},
        "X-Cache-Layer": {"schema": {"type": "string", "example": "L1"}, "description": "Layer that served request (L1, L2, DB, NONE)"},
        "X-Admission-Status": {"schema": {"type": "string", "example": "ADMITTED"}, "description": "Adaptive admission decision (ADMITTED, REJECTED, N/A)"},
        "X-Adaptive-TTL": {"schema": {"type": "string", "example": "35"}, "description": "Calculated adaptive TTL in seconds"},
        "X-Request-ID": {"schema": {"type": "string"}, "description": "Unique request tracing UUID"},
        "X-Database-Time-Ms": {"schema": {"type": "string", "example": "0.00"}, "description": "PostgreSQL query execution time in ms"},
        "X-L1-Time-Ms": {"schema": {"type": "string", "example": "0.15"}, "description": "L1 Memory operation time in ms"},
        "X-L2-Time-Ms": {"schema": {"type": "string", "example": "0.00"}, "description": "L2 Redis operation time in ms"},
        "X-Policy-Time-Ms": {"schema": {"type": "string", "example": "0.08"}, "description": "Admission and TTL policy computation time in ms"},
        "X-Total-Time-Ms": {"schema": {"type": "string", "example": "0.45"}, "description": "Total server response time in ms"}
    }

    spec = {
        "openapi": "3.0.0",
        "info": {
            "title": f"Adaptive Serverless Caching API - {Config.CONFIG_NAME} ({Config.CONFIG_ID})",
            "version": "1.0.0",
            "description": "Adaptive Hybrid Cache configuration E5 (Frequency-based admission & dynamic TTL scaling)."
        },
        "paths": {
            "/health": {
                "get": {
                    "summary": "Health check",
                    "description": "Checks DB and Redis connectivity without warming caches.",
                    "responses": {"200": {"description": "Healthy"}, "503": {"description": "Degraded"}}
                }
            },
            "/records": {
                "post": {
                    "summary": "Create record",
                    "description": "Inserts a new record into PostgreSQL.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["id", "category", "payload"],
                                    "properties": {
                                        "id": {"type": "integer"},
                                        "category": {"type": "string"},
                                        "payload": {"type": "object"}
                                    }
                                },
                                "example": {
                                    "id": 1,
                                    "category": "electronics",
                                    "payload": {
                                        "name": "Synthetic Record 1",
                                        "size_bytes": 256
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "201": {"description": "Created", "headers": standard_headers},
                        "400": {"description": "Validation error"},
                        "409": {"description": "Record already exists"}
                    }
                }
            },
            "/records/{record_id}": {
                "get": {
                    "summary": "Get record by ID",
                    "description": "Fetches record via adaptive path: checks L1/L2, on miss evaluates admission frequency and dynamic TTL.",
                    "parameters": [
                        {
                            "name": "record_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer", "default": 1},
                            "example": 1
                        }
                    ],
                    "responses": {
                        "200": {"description": "Success", "headers": standard_headers},
                        "404": {"description": "Not Found", "headers": standard_headers}
                    }
                },
                "put": {
                    "summary": "Update record",
                    "description": "Updates PostgreSQL and invalidates caches and frequency history.",
                    "parameters": [
                        {
                            "name": "record_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer", "default": 1},
                            "example": 1
                        }
                    ],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["category", "payload"],
                                    "properties": {
                                        "category": {"type": "string"},
                                        "payload": {"type": "object"}
                                    }
                                },
                                "example": {
                                    "category": "electronics",
                                    "payload": {
                                        "name": "Updated Record 1",
                                        "size_bytes": 256
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Updated", "headers": standard_headers},
                        "404": {"description": "Not Found", "headers": standard_headers}
                    }
                },
                "delete": {
                    "summary": "Delete record",
                    "description": "Deletes from PostgreSQL and invalidates caches and frequency history.",
                    "parameters": [
                        {
                            "name": "record_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "integer", "default": 1},
                            "example": 1
                        }
                    ],
                    "responses": {
                        "200": {"description": "Deleted", "headers": standard_headers},
                        "404": {"description": "Not Found", "headers": standard_headers}
                    }
                }
            },
            "/metrics": {
                "get": {
                    "summary": "Adaptive Cache metrics",
                    "description": "Returns application counters, adaptive admission stats, TTL stats, and hybrid ratios.",
                    "responses": {"200": {"description": "Success"}}
                }
            }
        }
    }
    resp = make_response(jsonify(spec), 200)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


@api_bp.route("/docs", methods=["GET"])
def swagger_ui():
    """Serves Swagger UI pointing to /openapi.json per Section 4.1 & 9."""
    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Swagger UI - E5 Adaptive Cache</title>
        <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5.11.0/swagger-ui.css" />
    </head>
    <body>
        <div id="swagger-ui"></div>
        <script src="https://unpkg.com/swagger-ui-dist@5.11.0/swagger-ui-bundle.js"></script>
        <script>
            window.onload = () => {
                SwaggerUIBundle({
                    url: '/openapi.json',
                    dom_id: '#swagger-ui',
                });
            };
        </script>
    </body>
    </html>
    """
    return html, 200