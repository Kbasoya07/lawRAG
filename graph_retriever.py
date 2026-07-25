"""
Graph retriever: given a list of entry-point section IDs from Qdrant,
traverses Neo4j up to 2 hops to pull all connected law sections.
"""
import os
from typing import List, Dict
from neo4j import GraphDatabase
from dotenv import load_dotenv

# Safe loading of environment variables relative to this file
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

_driver = None

def get_driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            os.getenv("NEO4J_URI"),
            auth=(os.getenv("NEO4J_USERNAME"), os.getenv("NEO4J_PASSWORD"))
        )
    return _driver

def traverse_graph(entry_section_ids: List[str], max_hops: int = 2) -> List[Dict]:
    """
    Given Qdrant entry-point section IDs, traverse Neo4j graph
    up to max_hops to find all legally connected sections.
    Returns a deduplicated list of section dicts with relationship context.
    """
    # Defensive checks on inputs
    if not entry_section_ids:
        return []

    driver = get_driver()
    results = {}
    
    entry_ids = [str(sid).strip() for sid in entry_section_ids if sid]
    if not entry_ids:
        return []
    
    try:
        with driver.session() as session:
            # Cypher query: find all nodes within max_hops for all start IDs in a single batch
            query = f"""
                MATCH path = (start:Section)-[*1..{max_hops}]->(connected:Section)
                WHERE start.id IN $entry_ids
                RETURN start.id AS start_id,
                       connected,
                       [rel in relationships(path) | type(rel)] AS rel_types,
                       length(path) AS hops
                ORDER BY hops ASC
                LIMIT 50
            """
            records = session.run(query, entry_ids=entry_ids)
            for record in records:
                node = record["connected"]
                if not node:
                    continue
                node_id = node.get("id")
                if not node_id:
                    continue
                    
                # Deduplicate and build final payload structure
                if node_id not in results:
                    results[node_id] = {
                        "chunk_id": node_id,
                        "act": node.get("act", ""),
                        "section": str(node.get("section_num", "")),
                        "article": node.get("article", ""),
                        "title": node.get("title", ""),
                        "text": node.get("text", ""),
                        "bailable": bool(node.get("bailable", False)),
                        "cognizable": bool(node.get("cognizable", False)),
                        "punishment": node.get("punishment", ""),
                        "graph_relationship": record["rel_types"],
                        "graph_hops": record["hops"],
                        "confidence_score": max(0.0, 1.0 - (record["hops"] * 0.3)),
                        "source": "graph"
                    }
    except Exception as e:
        # Log error and return whatever we got so the entire query never crashes
        print(f"Error traversing graph for entry points {entry_ids}: {e}")
                
    return list(results.values())
