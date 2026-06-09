"""Kùzu graph store — entity extraction and graph-augmented retrieval.

Replaces Neo4j with Kùzu, an embedded graph DB that works natively in HF Spaces.
"""
from __future__ import annotations
import sys, os
import threading
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import config

_db = None
_conn = None
_db_lock = threading.Lock()
_schema_initialized = False


def _get_conn():
    global _db, _conn, _schema_initialized
    if not getattr(config, 'GRAPH_AVAILABLE', True):
        return None
    
    with _db_lock:
        if _conn is not None:
            return _conn
        try:
            import kuzu
            # Ensure parent path exists
            os.makedirs(os.path.dirname(config.KUZU_DB_PATH), exist_ok=True)
            _db = kuzu.Database(config.KUZU_DB_PATH)
            _conn = kuzu.Connection(_db)
            print(f"[GraphStore] Connected to Kuzu at {config.KUZU_DB_PATH}")
            if not _schema_initialized:
                _init_schema()
                _schema_initialized = True
        except Exception as e:
            print(f"[GraphStore] Kuzu initialization failed. ({e})")
            _conn = None
    return _conn


def _init_schema():
    """Create node and relationship tables for Kuzu."""
    conn = _conn
    if not conn:
        return
    
    # Check if tables exist
    try:
        tables_df = conn.execute("CALL show_tables() RETURN *;").get_as_df()
        existing_tables = set(tables_df['name'].tolist()) if not tables_df.empty else set()
    except Exception as e:
        print(f"[GraphStore] Could not check existing tables: {e}")
        existing_tables = set()

    try:
        if "Entity" not in existing_tables:
            conn.execute("CREATE NODE TABLE Entity (name STRING, type STRING, source STRING, tier STRING, session_token STRING, PRIMARY KEY (name))")
            print("[GraphStore] Created NODE TABLE Entity.")
        
        if "RELATES_TO" not in existing_tables:
            conn.execute("CREATE REL TABLE RELATES_TO (FROM Entity TO Entity, type STRING, source STRING, tier STRING, session_token STRING)")
            print("[GraphStore] Created REL TABLE RELATES_TO.")
    except Exception as e:
        print(f"[GraphStore] Schema init warning: {e}")


def is_available() -> bool:
    return _get_conn() is not None


def store_entities(entities: list[dict], source: str, tier: str = "extended", session_token: str = "admin") -> None:
    """
    entities: [{"name": str, "type": str, "relations": [{"target": str, "rel": str}]}]
    """
    conn = _get_conn()
    if not conn:
        return
    
    # Store nodes
    for ent in entities:
        if not isinstance(ent, dict):
            continue
        ent_name = ent.get("name") or ent.get("entity") or ent.get("id")
        if not ent_name:
            continue
        
        try:
            conn.execute(
                """
                MERGE (e:Entity {name: $name})
                ON CREATE SET e.type = $type, e.source = $source, e.tier = $tier, e.session_token = $session_token
                ON MATCH SET e.type = $type, e.source = $source, e.tier = $tier, e.session_token = $session_token
                """,
                {"name": str(ent_name), "type": str(ent.get("type", "General")), "source": str(source), "tier": str(tier), "session_token": str(session_token)}
            )
        except Exception as e:
            print(f"[GraphStore] Failed to store node {ent_name}: {e}")
            continue

        # Store edges
        for rel in ent.get("relations", []):
            if not isinstance(rel, dict):
                continue
            tgt = rel.get("target") or rel.get("to")
            if not tgt:
                continue
            
            try:
                # Ensure target exists
                conn.execute(
                    """
                    MERGE (e:Entity {name: $name})
                    ON CREATE SET e.type = 'General', e.source = $source, e.tier = $tier, e.session_token = $session_token
                    """,
                    {"name": str(tgt), "source": str(source), "tier": str(tier), "session_token": str(session_token)}
                )

                # Merge relationship
                conn.execute(
                    """
                    MATCH (a:Entity {name: $src}), (b:Entity {name: $tgt})
                    MERGE (a)-[r:RELATES_TO {type: $rel}]->(b)
                    ON CREATE SET r.source = $source, r.tier = $tier, r.session_token = $session_token
                    ON MATCH SET r.source = $source, r.tier = $tier, r.session_token = $session_token
                    """,
                    {
                        "src": str(ent_name), 
                        "tgt": str(tgt),
                        "rel": str(rel.get("rel", "related_to")), 
                        "source": str(source), 
                        "tier": str(tier), 
                        "session_token": str(session_token)
                    }
                )
            except Exception as e:
                print(f"[GraphStore] Failed to store relationship {ent_name} -> {tgt}: {e}")


def query_related(entity_names: list[str], hops: int = 2, session_token: str = "admin") -> list[str]:
    """Return text snippets of related entities within `hops` graph hops."""
    conn = _get_conn()
    if not conn:
        return []
    
    results = []
    # Limit max hops to 3 for safety
    hops = min(max(1, hops), 3)

    for name in entity_names:
        try:
            # Kuzu uses variable length paths similar to openCypher
            query = f"""
                MATCH (e:Entity {{name: $name}})
                WHERE e.tier = 'foundation' OR e.session_token = $session_token OR $session_token = 'admin'
                OPTIONAL MATCH (e)-[r:RELATES_TO*1..{hops}]-(related:Entity)
                WHERE related.tier = 'foundation' OR related.session_token = $session_token OR $session_token = 'admin'
                RETURN DISTINCT related.name AS name, related.type AS type
                LIMIT $limit
            """
            
            df = conn.execute(query, {"name": str(name), "session_token": str(session_token), "limit": config.TOP_K_GRAPH * 3}).get_as_df()
            if not df.empty:
                for idx, row in df.iterrows():
                    # Handle None values correctly
                    if row['name'] is not None:
                        type_str = row['type'] if row['type'] is not None else "General"
                        results.append(f"{row['name']} ({type_str})")
        except Exception as e:
            print(f"[GraphStore] Failed to query related for {name}: {e}")
            
    # Deduplicate and limit
    unique_results = list(dict.fromkeys(results))
    return unique_results[:config.TOP_K_GRAPH * 3]


def delete_source(source: str, session_token: str = "admin") -> None:
    conn = _get_conn()
    if not conn:
        return
    try:
        if session_token == "admin":
            conn.execute("MATCH (e:Entity {source: $source})-[r:RELATES_TO]-() DELETE r", {"source": source})
            conn.execute("MATCH (e:Entity {source: $source}) DELETE e", {"source": source})
        else:
            conn.execute("MATCH (e:Entity {source: $source, session_token: $session_token})-[r:RELATES_TO]-() DELETE r", 
                  {"source": source, "session_token": session_token})
            conn.execute("MATCH (e:Entity {source: $source, session_token: $session_token}) DELETE e", 
                  {"source": source, "session_token": session_token})
    except Exception as e:
        print(f"[GraphStore] Delete source error: {e}")

def delete_by_session(session_token: str) -> None:
    if session_token in ("admin", "anonymous", ""):
        return
    conn = _get_conn()
    if not conn:
        return
    try:
        conn.execute("MATCH (e:Entity {session_token: $session_token})-[r:RELATES_TO]-() DELETE r", {"session_token": session_token})
        conn.execute("MATCH (e:Entity {session_token: $session_token}) DELETE e", {"session_token": session_token})
    except Exception as e:
        print(f"[GraphStore] Delete session error: {e}")


def get_stats() -> dict:
    conn = _get_conn()
    if not conn:
        return {"available": False}
    try:
        nodes = 0
        rels = 0
        nodes_df = conn.execute("MATCH (e:Entity) RETURN count(e) AS n").get_as_df()
        if not nodes_df.empty:
            nodes = int(nodes_df['n'].iloc[0])
            
        rels_df = conn.execute("MATCH ()-[r:RELATES_TO]->() RETURN count(r) AS n").get_as_df()
        if not rels_df.empty:
            rels = int(rels_df['n'].iloc[0])
            
        return {"available": True, "nodes": nodes, "relationships": rels}
    except Exception as e:
        print(f"[GraphStore] Stats error: {e}")
        return {"available": True, "nodes": 0, "relationships": 0}

def purge() -> None:
    """Wipe all nodes and relationships from Kuzu."""
    conn = _get_conn()
    if not conn:
        return
    try:
        conn.execute("MATCH ()-[r:RELATES_TO]->() DELETE r")
        conn.execute("MATCH (n:Entity) DELETE n")
    except Exception as e:
        print(f"[GraphStore] Purge error: {e}")
