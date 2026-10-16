from typing import Any, Union, cast
from uuid import UUID

from loguru import logger
from qdrant_client.http import models as rest_models

from src.db.graphdb import get_graph
from src.db.vectordb import COLLECTION, COLLECTION_CODE, _get_sync_client


def wipe_kb_data(kb_id: Union[UUID, str]) -> None:
    """Remove all stored data for a given kb_id from Qdrant and FalkorDB.

    Safe to call at the start of an ingest task to make re-ingestion idempotent.
    Used both for crash recovery (worker died mid-task) and for normal
    re-dispatch (user re-submits the same repo).
    """
    ns = str(kb_id)
    client = _get_sync_client()

    for collection in (COLLECTION, COLLECTION_CODE):
        try:
            client.delete(
                collection_name=collection,
                points_selector=rest_models.Filter(
                    must=[
                        rest_models.FieldCondition(
                            key="metadata.kb_id",
                            match=rest_models.MatchValue(value=ns),
                        )
                    ]
                ),
            )
        except Exception as e:
            logger.warning(f"Qdrant wipe failed for {collection} kb={ns}: {e}")

    try:
        graph = cast(Any, get_graph(str(kb_id)))
        if graph is not None and hasattr(graph, "delete"):
            graph.delete()
    except Exception as e:
        logger.warning(f"FalkorDB wipe failed kb={ns}: {e}")
