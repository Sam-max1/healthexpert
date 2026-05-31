"""Neo4j graph store — entity extraction and graph-augmented retrieval.

Falls back gracefully to no-op if Neo4j is unreachable.
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config

_driver   = None
_available = False


def _get_driver():
    global _driver, _available
    if not getattr(config, 'NEO4J_AVAILABLE', True):
        return None
    if _driver is not None:
        return _driver
    try:
        from neo4j import GraphDatabase, NotificationMinimumSeverity
        _driver = GraphDatabase.driver(
            config.NEO4J_URI,
            auth=(config.NEO4J_USER, config.NEO4J_PASSWORD),
            notifications_min_severity=NotificationMinimumSeverity.OFF,  # silence schema warnings
        )
        _driver.verify_connectivity()
        _available = True
        print(f"[GraphStore] Connected to Neo4j at {config.NEO4J_URI}")
        _init_schema()
    except Exception as e:
        _available = False
        print(f"[GraphStore] Neo4j unavailable — vector-only mode. ({e})")
        _driver = None
    return _driver


def _init_schema():
    """Create indexes for fast entity lookups."""
    queries = [
        "CREATE INDEX entity_name IF NOT EXISTS FOR (e:Entity) ON (e.name)",
        "CREATE INDEX entity_source IF NOT EXISTS FOR (e:Entity) ON (e.source)",
        "CREATE INDEX entity_session IF NOT EXISTS FOR (e:Entity) ON (e.session_token)",
    ]
    d = _get_driver()
    if not d:
        return
    with d.session() as s:
        for q in queries:
            try:
                s.run(q)
            except Exception as e:
                # Fixed: was referencing undefined 'source_name' — now logs the query
                print(f"[GraphStore] Schema init warning for query '{q[:50]}...': {e}")


def is_available() -> bool:
    _get_driver()
    return _available


def store_entities(entities: list[dict], source: str, tier: str = "extended", session_token: str = "admin") -> None:
    """
    entities: [{"name": str, "type": str, "relations": [{"target": str, "rel": str}]}]
    """
    d = _get_driver()
    if not d:
        return
    with d.session() as s:
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            ent_name = ent.get("name") or ent.get("entity") or ent.get("id")
            if not ent_name:
                continue
            s.run(
                """
                MERGE (e:Entity {name: $name})
                SET e.type = $type, e.source = $source, e.tier = $tier, e.session_token = $session_token
                """,
                name=ent_name, type=ent.get("type", "General"), source=source, tier=tier, session_token=session_token
            )
            for rel in ent.get("relations", []):
                if not isinstance(rel, dict):
                    continue
                tgt = rel.get("target") or rel.get("to")
                if not tgt:
                    continue
                s.run(
                    """
                    MERGE (a:Entity {name: $src})
                    MERGE (b:Entity {name: $tgt})
                    MERGE (a)-[r:RELATES_TO {type: $rel}]->(b)
                    SET r.source = $source, r.tier = $tier, r.session_token = $session_token
                    """,
                    src=ent_name, tgt=tgt,
                    rel=rel.get("rel", "related_to"), source=source, tier=tier, session_token=session_token
                )


def query_related(entity_names: list[str], hops: int = 2, session_token: str = "admin") -> list[str]:
    """Return text snippets of related entities within `hops` graph hops."""
    d = _get_driver()
    if not d:
        return []
    results = []
    with d.session() as s:
        for name in entity_names:
            records = s.run(
                f"""
                MATCH (e:Entity {{name: $name}})
                WHERE e.tier = 'foundation' OR e.session_token = $session_token OR $session_token = 'admin'
                OPTIONAL MATCH (e)-[r:RELATES_TO*1..{hops}]-(related)
                WHERE related IS NOT NULL AND (related.tier = 'foundation' OR related.session_token = $session_token OR $session_token = 'admin')
                RETURN DISTINCT related.name AS name, related.type AS type
                LIMIT $limit
                """,
                name=name, limit=config.TOP_K_GRAPH * 3, session_token=session_token
            ).data()
            for rec in records:
                results.append(f"{rec['name']} ({rec['type']})")
    return results[:config.TOP_K_GRAPH * 3]


def delete_source(source: str, session_token: str = "admin") -> None:
    d = _get_driver()
    if not d:
        return
    with d.session() as s:
        if session_token == "admin":
            s.run("MATCH (e:Entity {source: $source}) DETACH DELETE e", source=source)
        else:
            s.run("MATCH (e:Entity {source: $source, session_token: $session_token}) DETACH DELETE e", 
                  source=source, session_token=session_token)

def delete_by_session(session_token: str) -> None:
    if session_token in ("admin", "anonymous", ""):
        return
    d = _get_driver()
    if not d:
        return
    with d.session() as s:
        s.run("MATCH (e:Entity {session_token: $session_token}) DETACH DELETE e", session_token=session_token)


def get_stats() -> dict:
    d = _get_driver()
    if not d:
        return {"available": False}
    with d.session() as s:
        nodes = s.run("MATCH (e:Entity) RETURN count(e) AS n").single()["n"]
        # OPTIONAL MATCH avoids a Neo4j schema warning when RELATES_TO doesn't
        # exist yet (fresh / empty database — perfectly normal on first run).
        rels  = s.run(
            "OPTIONAL MATCH ()-[r:RELATES_TO]->() RETURN count(r) AS n"
        ).single()["n"]
    return {"available": True, "nodes": nodes, "relationships": rels}

def purge() -> None:
    """Wipe all nodes and relationships from Neo4j."""
    d = _get_driver()
    if not d:
        return
    with d.session() as s:
        s.run("MATCH (n) DETACH DELETE n")
