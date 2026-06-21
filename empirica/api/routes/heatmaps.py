"""Uncertainty heatmap endpoints"""

import logging

from flask import Blueprint, jsonify

bp = Blueprint("heatmaps", __name__)
logger = logging.getLogger(__name__)


@bp.route("/files/<path:filepath>/uncertainty", methods=["GET"])
def get_file_uncertainty(filepath: str):
    """
    Get confidence/uncertainty metrics for a specific file.

    Returns:
    - Overall uncertainty and KNOW/DO metrics
    - List of changes with confidence scores
    - Aggregate confidence for file
    """
    return jsonify(
        {
            "ok": True,
            "filepath": filepath,
            "uncertainty_metrics": {
                "overall_uncertainty": 0.5,
                "know": 0.75,
                "do": 0.80,
                "investigated_areas": [],
                "not_investigated": [],
                "risk_level": "moderate",
            },
            "changes_made": [],
            "aggregate_confidence": 0.75,
        }
    )


@bp.route("/modules/<module_name>/epistemic", methods=["GET"])
def get_module_epistemic(module_name: str):
    """
    Get epistemic knowledge map for a module/directory.

    Returns:
    - Confidence breakdown by submodule
    - Overall coverage and risk areas
    - Testing status
    - Last modification timestamp
    """
    return jsonify(
        {
            "ok": True,
            "module": module_name,
            "epistemic_map": {
                "submodules": {},
                "overall_know": 0.75,
                "overall_uncertainty": 0.25,
                "coverage": "0%",
                "risk_areas": [],
                "tested": [],
            },
            "recent_sessions": 0,
            "last_modified": "unknown",
        }
    )
