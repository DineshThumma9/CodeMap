from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage, ToolMessage

from src.service.agent.middleware import CleanToolMessagesMiddleware


def test_clean_tool_messages_middleware_retains_valid_pairs():
    middleware = CleanToolMessagesMiddleware()

    # AI message made tool call 'call_abc', followed by ToolMessage with same id
    messages = [
        HumanMessage(content="Find auth logic"),
        AIMessage(
            content="",
            tool_calls=[
                {"name": "search_syntax", "args": {"query": "auth"}, "id": "call_abc"}
            ],
        ),
        ToolMessage(content="Found auth.py", tool_call_id="call_abc", id="tool_msg_1"),
    ]

    state = {"messages": messages}
    result = middleware.before_model(state, runtime=None)

    # Valid tool messages should NOT be removed
    assert result is None


def test_clean_tool_messages_middleware_prunes_orphans():
    middleware = CleanToolMessagesMiddleware()

    # Suppose context summarization truncated earlier AIMessages, leaving an orphaned ToolMessage
    messages = [
        HumanMessage(content="Continue analysis"),
        # Notice: No AIMessage with tool_calls id='call_orphaned'
        ToolMessage(
            content="Earlier search result",
            tool_call_id="call_orphaned",
            id="orphan_msg_99",
        ),
    ]

    state = {"messages": messages}
    result = middleware.before_model(state, runtime=None)

    assert result is not None
    assert "messages" in result
    removed = result["messages"]
    assert len(removed) == 1
    assert isinstance(removed[0], RemoveMessage)
    assert removed[0].id == "orphan_msg_99"


def test_clean_tool_messages_middleware_mixed_messages():
    middleware = CleanToolMessagesMiddleware()

    # Mixture of 1 valid pair and 1 orphaned tool message
    messages = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "read_code", "args": {"file": "a.py"}, "id": "valid_id"}
            ],
        ),
        ToolMessage(
            content="content of a.py", tool_call_id="valid_id", id="valid_tool_msg"
        ),
        ToolMessage(
            content="orphaned response", tool_call_id="missing_id", id="orphan_tool_msg"
        ),
    ]

    result = middleware.before_model({"messages": messages}, runtime=None)
    assert result is not None
    removed = result["messages"]
    assert len(removed) == 1
    assert removed[0].id == "orphan_tool_msg"


def test_clean_tool_messages_middleware_empty_state():
    middleware = CleanToolMessagesMiddleware()
    assert middleware.before_model({"messages": []}, runtime=None) is None
    assert middleware.before_model({}, runtime=None) is None
