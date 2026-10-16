from typing import Any, Literal
from langchain_core.language_models import BaseChatModel

from src.service.agent.tools.generic import GenericTools
from src.service.agent.tools.document import DocumentTools
from src.service.agent.tools.code import CodeTools
from src.service.agent.tools.mcp import MCPTools


class Tools:
    def __init__(
        self,
        user_id: str,
        vector_db: Any | None = None,
        falkordb_ns: str | None = None,
        graph_obj: Any | None = None,
        async_falkordb: Any = None,
        llm: BaseChatModel | None = None,
    ):
        self.user_id = user_id
        self.vector_db = vector_db
        self.falkordb_ns = falkordb_ns
        self.graph_obj = graph_obj
        self.async_falkordb = async_falkordb
        self.llm = llm

        self.mcp_tools = None
        self.code_tools = None
        self.doc_tools = None
        self.general_tools = None
        self.tools = []

    @classmethod
    async def build(
        cls,
        user_id: str,
        vector_db: Any | None = None,
        falkordb_ns: str | None = None,
        graph_obj: Any | None = None,
        async_falkordb: Any = None,
        llm: BaseChatModel | None = None,
        mcp_enabled: bool = True,
        kb_type: Literal["GITHUB", "DOCUMENT"] | str | None = None,
    ) -> "Tools":
        instance = cls(user_id, vector_db, falkordb_ns, graph_obj, async_falkordb, llm)

        instance.general_tools = await GenericTools.build(user_id, llm)
        instance.tools.extend(instance.general_tools.tools)

        if mcp_enabled:
            instance.mcp_tools = await MCPTools.build(user_id, mcp_enabled)
            instance.tools.extend(instance.mcp_tools.tools)

        if kb_type:
            if kb_type.upper() == "GITHUB" and falkordb_ns:
                instance.code_tools = await CodeTools.build(
                    user_id, vector_db, falkordb_ns, graph_obj, async_falkordb, llm
                )
                instance.tools.extend(instance.code_tools.tools)
            elif kb_type.upper() == "DOCUMENT" and vector_db:
                instance.doc_tools = await DocumentTools.build(user_id, vector_db, llm)
                instance.tools.extend(instance.doc_tools.tools)

        return instance