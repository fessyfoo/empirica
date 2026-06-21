"""
Concept Graph Schema

Database table schemas for concept co-occurrence graphs.
Part of Phase 2: Epistemic Prediction System.
"""

SCHEMAS = [
    # Concept nodes - extracted from findings/unknowns/dead_ends
    """
    CREATE TABLE IF NOT EXISTS concept_nodes (
        concept_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        concept_text TEXT NOT NULL,
        normalized_text TEXT NOT NULL,
        source_type TEXT NOT NULL,
        frequency INTEGER DEFAULT 1,
        total_impact REAL DEFAULT 0.0,
        avg_impact REAL DEFAULT 0.0,
        first_seen_timestamp REAL NOT NULL,
        last_seen_timestamp REAL NOT NULL,
        session_ids TEXT NOT NULL,
        source_ids TEXT NOT NULL,

        FOREIGN KEY (project_id) REFERENCES projects(id)
    )
    """,
    # Concept edges - co-occurrence relationships
    """
    CREATE TABLE IF NOT EXISTS concept_edges (
        edge_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        source_concept_id TEXT NOT NULL,
        target_concept_id TEXT NOT NULL,
        relationship_type TEXT DEFAULT 'co_occurs',
        weight REAL DEFAULT 0.0,
        co_occurrence_count INTEGER DEFAULT 1,
        session_ids TEXT,
        first_seen_timestamp REAL NOT NULL,
        last_seen_timestamp REAL NOT NULL,

        FOREIGN KEY (project_id) REFERENCES projects(id),
        FOREIGN KEY (source_concept_id) REFERENCES concept_nodes(concept_id),
        FOREIGN KEY (target_concept_id) REFERENCES concept_nodes(concept_id),
        UNIQUE(project_id, source_concept_id, target_concept_id, relationship_type)
    )
    """,
    # Concept clusters - groups of related concepts
    """
    CREATE TABLE IF NOT EXISTS concept_clusters (
        cluster_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        cluster_name TEXT,
        concept_ids TEXT NOT NULL,
        dominant_concepts TEXT,
        cohesion REAL DEFAULT 0.0,
        size INTEGER DEFAULT 0,
        created_timestamp REAL NOT NULL,
        updated_timestamp REAL NOT NULL,

        FOREIGN KEY (project_id) REFERENCES projects(id)
    )
    """,
    # Indices for efficient querying
    """
    CREATE INDEX IF NOT EXISTS idx_concept_nodes_project
    ON concept_nodes(project_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_concept_nodes_normalized
    ON concept_nodes(project_id, normalized_text)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_concept_edges_source
    ON concept_edges(source_concept_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_concept_edges_target
    ON concept_edges(target_concept_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_concept_edges_weight
    ON concept_edges(project_id, weight DESC)
    """,
]
