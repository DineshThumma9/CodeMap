import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.service.agent.tools.code import CodeTools
from src.service.agent.tools.document import DocumentTools
from src.service.agent.tools.mcp import MCPTools


def test_code_tools_graceful_failures_without_graph():
    """Sanity check: All tools must gracefully return error XML when no graph is loaded."""
    tools = CodeTools(user_id="test-user")

    assert "<error>No codebase is currently loaded.</error>" in asyncio.run(
        tools.explore_codebase()
    )
    assert "<error>No codebase is currently loaded.</error>" in asyncio.run(
        tools.search_syntax("User")
    )
    assert "<error>No codebase is currently loaded.</error>" in asyncio.run(
        tools.trace_call_graph("validate_token")
    )
    assert "<error>No codebase is currently loaded.</error>" in asyncio.run(
        tools.trace_execution_path("login", "verify_password")
    )
    assert "<error>No codebase is currently loaded.</error>" in asyncio.run(
        tools.assess_refactor_risk("delete_user")
    )
    assert "<error>No codebase is currently loaded.</error>" in asyncio.run(
        tools.ask_architecture("What are the main subsystems?")
    )


def test_code_tools_empty_input_validation():
    """Sanity check: Tools reject empty or whitespace arguments with clean messages."""
    mock_graph = MagicMock()
    mock_async_graph = AsyncMock()
    mock_vector = MagicMock()

    tools = CodeTools(
        user_id="test-user",
        vector_db=mock_vector,
        falkordb_ns="test-kb",
        graph_obj=mock_graph,
        async_falkordb=mock_async_graph,
    )

    # Empty path in read_code
    res = asyncio.run(tools.read_code(path=""))
    assert "<error>'path' must not be empty.</error>" in res

    # Empty symbol in trace_call_graph
    res = asyncio.run(tools.trace_call_graph(symbol="   "))
    assert "<error>'symbol' must not be empty.</error>" in res

    # Empty symbols in trace_execution_path
    res = asyncio.run(
        tools.trace_execution_path(source_symbol="", target_symbol="target")
    )
    assert (
        "<error>'source_symbol' and 'target_symbol' must not be empty.</error>" in res
    )

    # Empty symbol in assess_refactor_risk
    res = asyncio.run(tools.assess_refactor_risk(symbol=""))
    assert "<error>'symbol' must not be empty.</error>" in res

    # Empty query in search_code
    res = asyncio.run(tools.search_code(query="  "))
    assert "<error>query must not be empty.</error>" in res

    # Empty query in ask_architecture
    tools.cypher_chain = MagicMock()
    res = asyncio.run(tools.ask_architecture(query=""))
    assert "<error>Please provide a query.</error>" in res


def test_trace_call_graph_direction_and_depth_clamping():
    """Sanity check: Direction aliases (caller/callee/all) and depth limits (1..4) are normalized."""
    mock_async_graph = AsyncMock()
    mock_res = MagicMock()
    mock_res.result_set = []
    mock_res.header = []
    mock_async_graph.query.return_value = mock_res

    tools = CodeTools(
        user_id="test-user",
        falkordb_ns="test-kb",
        graph_obj=MagicMock(),
        async_falkordb=mock_async_graph,
    )

    # Clamps depth > 4 down to 4
    asyncio.run(tools.trace_call_graph("my_func", direction="caller", depth=10))
    called_query = mock_async_graph.query.call_args[0][0]
    assert "[:CALLS*1..4]" in called_query

    # Clamps depth < 1 up to 1 (depth=1 uses single [:CALLS]->)
    mock_async_graph.reset_mock()
    asyncio.run(tools.trace_call_graph("my_func", direction="incoming", depth=-2))
    called_query_d1 = mock_async_graph.query.call_args[0][0]
    assert "[:CALLS]->" in called_query_d1


def test_assess_refactor_risk_risk_tier_classification():
    """Sanity check: Refactor blast radius correctly assigns CRITICAL, HIGH, MEDIUM, LOW, MINIMAL."""
    mock_async_graph = AsyncMock()

    tools = CodeTools(
        user_id="test-user",
        falkordb_ns="test-kb",
        graph_obj=MagicMock(),
        async_falkordb=mock_async_graph,
    )

    # Test CRITICAL: >= 12 dependents
    mock_direct = MagicMock(
        header=[(None, "direct_callers"), (None, "direct_list")],
        result_set=[[2, ["a", "b"]]],
    )
    mock_trans_crit = MagicMock(
        header=[(None, "blast_radius"), (None, "dependents")],
        result_set=[[15, ["d1", "d2"]]],
    )
    mock_async_graph.query.side_effect = [mock_direct, mock_trans_crit]

    res_crit = asyncio.run(tools.assess_refactor_risk("core_engine"))
    assert "<risk_level>CRITICAL</risk_level>" in res_crit
    assert "count='15'" in res_crit

    # Test HIGH: >= 6 dependents
    mock_trans_high = MagicMock(
        header=[(None, "blast_radius"), (None, "dependents")], result_set=[[7, ["d1"]]]
    )
    mock_async_graph.query.side_effect = [mock_direct, mock_trans_high]

    res_high = asyncio.run(tools.assess_refactor_risk("middle_service"))
    assert "<risk_level>HIGH</risk_level>" in res_high

    # Test MINIMAL: 0 callers
    mock_direct_zero = MagicMock(
        header=[(None, "direct_callers"), (None, "direct_list")], result_set=[[0, []]]
    )
    mock_trans_zero = MagicMock(
        header=[(None, "blast_radius"), (None, "dependents")], result_set=[[0, []]]
    )
    mock_async_graph.query.side_effect = [mock_direct_zero, mock_trans_zero]

    res_min = asyncio.run(tools.assess_refactor_risk("unused_leaf"))
    assert "<risk_level>MINIMAL</risk_level>" in res_min


def test_document_tools_sanity():
    """Sanity check: DocumentTools requires vector_db and query."""
    doc_tools = DocumentTools(user_id="test-user")
    assert (
        asyncio.run(doc_tools.search_documents("test"))
        == "<error>No document knowledge base is currently loaded.</error>"
    )

    mock_vdb = AsyncMock()
    mock_vdb.asimilarity_search.return_value = []
    active_doc_tools = DocumentTools(user_id="test-user", vector_db=mock_vdb)

    assert (
        asyncio.run(active_doc_tools.search_documents(""))
        == "<error>Please provide a search query.</error>"
    )
    assert (
        asyncio.run(active_doc_tools.search_documents("foo"))
        == "<result>No relevant document context found.</result>"
    )


def test_mcp_tools_registration():
    """Sanity check: MCPTools registers standard management tools."""
    mcp = asyncio.run(MCPTools.build(user_id="test-user", mcp_enabled=False))
    tool_names = {t.name for t in mcp.tools}
    assert "list_mcp_resources" in tool_names
    assert "read_mcp_resource" in tool_names
    assert "load_mcp_prompts" in tool_names
