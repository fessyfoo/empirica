"""Multi-AI comparison endpoints"""

import logging

from flask import Blueprint, jsonify, request

bp = Blueprint("comparison", __name__)
logger = logging.getLogger(__name__)


@bp.route("/ai/<ai_id>/learning-curve", methods=["GET"])
def get_ai_learning_curve(ai_id: str):
    """
    Get learning trajectory for a specific AI over time.

    Returns:
    - List of sessions with epistemic vectors
    - Learning statistics and trends
    - Learning velocity
    """
    request.args.get("since")
    min(int(request.args.get("limit", 100)), 1000)

    return jsonify(
        {
            "ok": True,
            "ai_id": ai_id,
            "total_sessions": 0,
            "time_period": "unknown",
            "learning_trajectory": [],
            "statistics": {
                "average_know": 0.0,
                "average_uncertainty": 0.0,
                "learning_velocity": 0.0,
                "trend": "unknown",
            },
        }
    )


@bp.route("/compare-ais", methods=["GET"])
def compare_ais():
    """
    Compare learning curves across multiple AIs.

    Query Parameters:
    - `ai_ids`: Comma-separated AI identifiers (e.g., "copilot,gemini,claude")
    - `since`: Start date for comparison
    - `metric`: Which metric to compare (know, do, uncertainty, etc.)

    Returns:
    - Learning metrics for each AI
    - Performance rankings
    - Trend analysis
    """
    ai_ids = request.args.get("ai_ids", "")
    request.args.get("since")
    request.args.get("metric", "know")

    ai_list = [a.strip() for a in ai_ids.split(",")] if ai_ids else []

    return jsonify(
        {
            "ok": True,
            "comparison": [
                {"ai_id": ai_id, "average_know": 0.0, "average_uncertainty": 0.0, "sessions": 0, "trend": "unknown"}
                for ai_id in ai_list
            ],
            "best_performer": "unknown",
            "most_improving": "unknown",
        }
    )
