"""
Provenance Guard - Flask API
app.py: Entry point for the Provenance Guard REST API.
"""

import uuid
from datetime import datetime, timezone

from flask import Flask, request, jsonify

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from services.detector import analyze_text
from services.audit_log import write_log, get_log, update_entry_by_content_id
from services.stylometric import analyze_stylometry

app = Flask(__name__)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

# ---------------------------------------------------------------------------
# Health-check route
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    """Return a simple status message confirming the API is running."""
    return jsonify({"message": "Provenance Guard API is running."}), 200


# ---------------------------------------------------------------------------
# Submit endpoint
# ---------------------------------------------------------------------------

@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;1000 per day")
def submit():
    """
    Accepts a JSON payload, runs AI-detection and stylometric analysis,
    blends the signals, logs the result, and returns a structured response.

    Request body:
        {
            "creator_id": "<string>",
            "text":       "<string>"
        }

    Response body (200):
        {
            "content_id":    "<uuid>",
            "attribution":   "<likely_ai | likely_human | uncertain>",
            "confidence":    <float 0-1>,
            "label":         "<descriptive label string>",
            "signal_scores": { "llm": <float>, "stylometric": <float> }
        }

    Error responses:
        400 — missing / malformed request body
        500 — detector failure
    """

    # ------------------------------------------------------------------
    # 1. Validate the request body
    # ------------------------------------------------------------------
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"error": "Request body must be valid JSON."}), 400

    missing_fields = [field for field in ("creator_id", "text") if field not in data]
    if missing_fields:
        return jsonify({
            "error": f"Missing required field(s): {', '.join(missing_fields)}."
        }), 400

    # ------------------------------------------------------------------
    # 2. Extract fields and generate a unique content ID
    # ------------------------------------------------------------------
    creator_id = data["creator_id"]
    text       = data["text"]
    content_id = str(uuid.uuid4())

    # ------------------------------------------------------------------
    # 3. Run LLM-based AI detection
    # ------------------------------------------------------------------
    try:
        detection = analyze_text(text)
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": f"Detection failed: {exc}"}), 500

    llm_score = detection["score"]  # float [0, 1]

    # ------------------------------------------------------------------
    # 4. Run stylometric analysis
    # ------------------------------------------------------------------
    stylometric       = analyze_stylometry(text)
    stylometric_score = stylometric["score"]  # float [0, 1]
    STYLOMETRIC_WEIGHT = 0.85
    stylometric_score *= STYLOMETRIC_WEIGHT

    # ------------------------------------------------------------------
    # 5. Blend signals into a single confidence score
    # ------------------------------------------------------------------
    confidence = 0.65 * llm_score + 0.35 * stylometric_score
    confidence = confidence - 0.15 * abs(llm_score - stylometric_score)
    confidence = max(0.0, min(1.0, confidence))

    # ------------------------------------------------------------------
    # 6. Determine attribution from fixed confidence thresholds
    # ------------------------------------------------------------------
    if confidence <= 0.30:
        attribution = "likely_human"
    elif confidence <= 0.70:
        attribution = "uncertain"
    else:
        attribution = "likely_ai"

    # ------------------------------------------------------------------
    # 7. Select a descriptive label for the attribution result
    # ------------------------------------------------------------------
    labels = {
        "likely_ai": (
            "Our analysis found strong evidence that this text was generated or "
            "heavily assisted by AI. This result is based on multiple detection "
            "methods and was assigned with high confidence."
        ),
        "uncertain": (
            "Our analysis found mixed or inconclusive evidence. We cannot "
            "confidently determine whether this text was primarily written by a "
            "person or generated with AI assistance."
        ),
        "likely_human": (
            "Our analysis found strong evidence that this text was written by a "
            "person. While no automated system is perfect, this result was "
            "assigned with high confidence."
        ),
    }
    label = labels[attribution]

    # ------------------------------------------------------------------
    # 8. Build the response payload
    # ------------------------------------------------------------------
    response_payload = {
        "content_id":  content_id,
        "attribution": attribution,
        "confidence":  round(confidence, 4),
        "label":       label,
        "signal_scores": {
            "llm":         round(llm_score, 4),
            "stylometric": round(stylometric_score, 4),
        },
    }

    # ------------------------------------------------------------------
    # 9. Write an audit log entry
    # ------------------------------------------------------------------
    audit_entry = {
        "content_id":        content_id,
        "creator_id":        creator_id,
        "timestamp":         datetime.now(timezone.utc).isoformat(),
        "attribution":       attribution,
        "confidence":        round(confidence, 4),
        "llm_score":         round(llm_score, 4),
        "stylometric_score": round(stylometric_score, 4),
        "status":            "classified",
    }

    try:
        write_log(audit_entry)
    except Exception as exc:
        app.logger.error("Audit log write failed for content_id %s: %s", content_id, exc)

    # ------------------------------------------------------------------
    # 10. Return the response
    # ------------------------------------------------------------------
    return jsonify(response_payload), 200


# ---------------------------------------------------------------------------
# Audit log route
# ---------------------------------------------------------------------------

@app.route("/log", methods=["GET"])
def log():
    """Return every entry in the audit log as a JSON array under 'entries'."""
    return jsonify({"entries": get_log()}), 200


# ---------------------------------------------------------------------------
# Appeal endpoint
# ---------------------------------------------------------------------------

@app.route("/appeal", methods=["POST"])
def appeal():
    """
    Accept a creator's appeal against an AI-attribution decision.

    Request body:
        {
            "content_id":        "<uuid>",
            "creator_reasoning": "<string>"
        }

    Response body (200):
        {
            "message":    "Appeal received.",
            "content_id": "<uuid>",
            "status":     "under_review"
        }

    Error responses:
        400 — missing / malformed request body
        404 — content_id not found in the audit log
    """

    # ------------------------------------------------------------------
    # 1. Validate the request body
    # ------------------------------------------------------------------
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"error": "Request body must be valid JSON."}), 400

    missing_fields = [
        field for field in ("content_id", "creator_reasoning")
        if field not in data
    ]
    if missing_fields:
        return jsonify({
            "error": f"Missing required field(s): {', '.join(missing_fields)}."
        }), 400

    # ------------------------------------------------------------------
    # 2. Extract fields
    # ------------------------------------------------------------------
    content_id        = data["content_id"]
    creator_reasoning = data["creator_reasoning"]

    # ------------------------------------------------------------------
    # 3. Build the update payload
    # ------------------------------------------------------------------
    updates = {
        "status":           "under_review",
        "appeal_reasoning": creator_reasoning,
        "appeal_timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # ------------------------------------------------------------------
    # 4. Apply the update — returns False if content_id is not found
    # ------------------------------------------------------------------
    found = update_entry_by_content_id(content_id, updates)

    if not found:
        return jsonify({
            "error": f"No submission found with content_id '{content_id}'."
        }), 404

    # ------------------------------------------------------------------
    # 5. Return confirmation
    # ------------------------------------------------------------------
    return jsonify({
        "message":    "Appeal received.",
        "content_id": content_id,
        "status":     "under_review",
    }), 200


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # debug=True enables auto-reload and detailed error pages during development.
    # Set debug=False (or use an environment variable) before deploying.
    app.run(debug=True)
