import urllib.parse
from typing import Optional, Union
from uuid import UUID

from falkordb import FalkorDB
from falkordb.asyncio import FalkorDB as AsyncFalkorDB
from langchain_falkordb import FalkorDBGraph
from loguru import logger

from src.config.settings import settings

_sync_driver: Optional[FalkorDB] = None
_async_driver: Optional[AsyncFalkorDB] = None


def init_graph(force_reconnect: bool = False) -> FalkorDB:
    global _sync_driver
    if _sync_driver is None or force_reconnect:
        _sync_driver = FalkorDB.from_url(settings.falkordb_url)
    return _sync_driver


def get_graph(graph_id: Optional[Union[UUID, str]] = None):
    global _sync_driver
    if _sync_driver is None:
        init_graph()
    return (
        _sync_driver.select_graph(str(graph_id))
        if graph_id is not None
        else _sync_driver
    )


def init_async_graph(force_reconnect: bool = False) -> AsyncFalkorDB:
    global _async_driver
    if _async_driver is None or force_reconnect:
        _async_driver = AsyncFalkorDB.from_url(settings.falkordb_url)
    return _async_driver


def get_async_graph(graph_id: Optional[Union[UUID, str]] = None):
    global _async_driver
    if _async_driver is None:
        init_async_graph()
    return (
        _async_driver.select_graph(str(graph_id))
        if graph_id is not None
        else _async_driver
    )


def get_langchain_graph(
    graph_id: Optional[Union[UUID, str]] = None,
) -> Optional[FalkorDBGraph]:
    if graph_id is None:
        return None
    parsed = urllib.parse.urlparse(settings.falkordb_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 6379
    try:
        return FalkorDBGraph(
            database=str(graph_id),
            host=host,
            port=port,
            username=parsed.username,
            password=parsed.password,
        )
    except Exception as e:
        logger.warning(f"Could not initialize FalkorDBGraph for {graph_id}: {e}")
        return None


async def close_graph():
    global _sync_driver, _async_driver
    if _sync_driver is not None:
        _sync_driver.close()
        _sync_driver = None
    if _async_driver is not None:
        await _async_driver.aclose()
        _async_driver = None
