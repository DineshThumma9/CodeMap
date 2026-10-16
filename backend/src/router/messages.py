import json
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.messages.ai import AIMessageChunk
from loguru import logger

from ..db.dbs import get_db
from ..models.enums import SenderRole
from ..models.schema import MessageRequest
from ..router.limiter import limiter
from ..service.agent_service import AgentService
from ..service.auth_service import get_current_user
from ..service.chat_service import ChatService

router = APIRouter()


def _format_tool_summary(tool_name: str, content: Any) -> str:
    if not content:
        return "Executed successfully"
    content_str = str(content).strip()
    if tool_name == "assess_refactor_risk":
        if "CRITICAL" in content_str:
            return "CRITICAL Risk • Large blast radius detected"
        elif "HIGH" in content_str:
            return "HIGH Risk • Direct & transitive callers analyzed"
        elif "MEDIUM" in content_str:
            return "MEDIUM Risk • Dependents mapped"
        elif "LOW" in content_str:
            return "LOW Risk • Safe to modify"
        return "Calculated blast radius & risk tier"
    elif tool_name == "trace_call_graph":
        for line in content_str.split("\n"):
            line_s = line.strip(" -*#")
            if any(
                k in line_s for k in ["Callers", "Callees", "Incoming", "Total callers"]
            ):
                return line_s[:100]
        return "Traversed call graph neighborhood"
    elif tool_name == "trace_execution_path":
        for line in content_str.split("\n"):
            line_s = line.strip(" -*#")
            if "hops" in line_s or "Path:" in line_s:
                return line_s[:100]
        return "Traced multi-hop execution path"
    elif tool_name == "get_project_context":
        return "Extracted architecture, domain clusters & authority pillars"
    elif tool_name == "search_syntax":
        return "Found symbols ranked by PageRank & Betweenness"
    elif tool_name == "read_code":
        return "Retrieved source definition"
    elif tool_name == "search_code":
        return "Retrieved relevant semantic code chunks"
    elif tool_name == "ask_architecture":
        return "Executed FalkorDB GraphBLAS Cypher analysis"
    elif "create_issue" in tool_name:
        return "Created GitHub Issue"
    elif "create_pull_request" in tool_name:
        return "Opened GitHub Pull Request"
    elif "branch" in tool_name:
        return "Created Git Branch"
    elif "push" in tool_name:
        return "Pushed code changes"
    else:
        first_line = content_str.split("\n")[0].strip(" -*#`")
        return first_line[:100] if first_line else "Completed action"


@router.post("/simple-stream")
@limiter.limit("20/minute")
async def message_stream(
    request: Request,
    body: MessageRequest = Body(...),
    db=Depends(get_db),
    user=Depends(get_current_user),
):

    agent_object = AgentService(db=db, user=user, session_id=body.session_id)
    agent = await agent_object.build(
        msg=body.msg, mcp_enabled=body.mcp_enabled, thinking_level=body.thinking_level
    )

    config = defaultdict(dict)
    config["configurable"]["thread_id"] = body.session_id
    config["metadata"]["user_id"] = user.userid
    config["metadata"]["session_id"] = body.session_id

    return StreamingResponse(
        _stream(agent, body.msg, config, request, body.session_id, db),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream(
    agent, message: str, config: dict, request: Request, session_id: str, db
):

    chat_obj = ChatService(db=db)
    await chat_obj.add_message(session_id, SenderRole.USER, message)
    yield f"data: {json.dumps({'type': 'start', 'content': ''})}\n\n"
    full = ""
    usage_data = None
    known_tools_by_id: dict[str, str] = {}
    pending_tool_chunks: dict[int | str, dict] = {}

    try:
        async for chunk in agent.astream(
            {"messages": [HumanMessage(content=message)]},
            config=config,
            stream_mode="messages",
        ):
            if await request.is_disconnected():
                break

            msg_chunk, _meta = chunk

            if hasattr(msg_chunk, "usage_metadata") and msg_chunk.usage_metadata:
                new_usage = msg_chunk.usage_metadata
                if usage_data is None:
                    usage_data = dict(new_usage)
                else:
                    usage_data["input_tokens"] = usage_data.get(
                        "input_tokens", 0
                    ) + new_usage.get("input_tokens", 0)
                    usage_data["output_tokens"] = usage_data.get(
                        "output_tokens", 0
                    ) + new_usage.get("output_tokens", 0)
                    usage_data["total_tokens"] = usage_data.get(
                        "total_tokens", 0
                    ) + new_usage.get("total_tokens", 0)

                    for detail_key in ["input_token_details", "output_token_details"]:
                        if new_usage.get(detail_key):
                            if not usage_data.get(detail_key):
                                usage_data[detail_key] = dict(new_usage[detail_key])
                            else:
                                for k, v in new_usage[detail_key].items():
                                    usage_data[detail_key][k] = (
                                        usage_data[detail_key].get(k, 0) + v
                                    )

            # Stream tool calls
            if hasattr(msg_chunk, "tool_call_chunks") and msg_chunk.tool_call_chunks:
                for tc in msg_chunk.tool_call_chunks:
                    idx = tc.get("index", 0)
                    if idx not in pending_tool_chunks:
                        pending_tool_chunks[idx] = {
                            "id": tc.get("id") or f"call_{idx}",
                            "name": "",
                            "emitted": False,
                        }
                    if tc.get("id"):
                        pending_tool_chunks[idx]["id"] = tc["id"]
                    if tc.get("name"):
                        pending_tool_chunks[idx]["name"] += tc["name"]

                    call_id = pending_tool_chunks[idx]["id"]
                    tool_name = pending_tool_chunks[idx]["name"]
                    if tool_name and not pending_tool_chunks[idx]["emitted"]:
                        pending_tool_chunks[idx]["emitted"] = True
                        known_tools_by_id[call_id] = tool_name
                        yield f"data: {json.dumps({'type': 'tool_call', 'id': call_id, 'tool': tool_name})}\n\n"
                continue

            if hasattr(msg_chunk, "tool_calls") and msg_chunk.tool_calls:
                for tc in msg_chunk.tool_calls:
                    call_id = tc.get("id") or f"call_{len(known_tools_by_id)}"
                    tool_name = tc.get("name")
                    if tool_name and call_id not in known_tools_by_id:
                        known_tools_by_id[call_id] = tool_name
                        yield f"data: {json.dumps({'type': 'tool_call', 'id': call_id, 'tool': tool_name, 'args': tc.get('args', {})})}\n\n"

            # Stream tool results
            if isinstance(msg_chunk, ToolMessage):
                call_id = getattr(msg_chunk, "tool_call_id", "")
                tool_name = getattr(msg_chunk, "name", "") or known_tools_by_id.get(
                    call_id, "tool"
                )
                status = (
                    "error"
                    if getattr(msg_chunk, "status", "") == "error"
                    else "completed"
                )
                raw_content = getattr(msg_chunk, "content", "")
                summary = _format_tool_summary(tool_name, raw_content)
                yield f"data: {json.dumps({'type': 'tool_result', 'id': call_id, 'tool': tool_name, 'summary': summary, 'status': status})}\n\n"
                continue

            if hasattr(msg_chunk, "content") and msg_chunk.content:
                if isinstance(msg_chunk, AIMessageChunk):
                    if (
                        _meta.get("langgraph_node") == "model"
                    ):  # Only stream output from the main agent, ignoring background middleware/summarizers
                        # Gemini / Claude return content as a list of typed blocks
                        if isinstance(msg_chunk.content, list):
                            token = "".join(
                                block.get("text", "")
                                for block in msg_chunk.content
                                if isinstance(block, dict)
                                and block.get("type") == "text"
                            )
                        else:
                            token = msg_chunk.content  # plain string (OpenAI-style)

                        if token:
                            full += token
                            yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"

    except Exception as e:
        logger.error(f"Stream error: {e}")
        yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
        return

    await chat_obj.add_message(session_id, SenderRole.ASSISTANT, full)

    if usage_data:
        try:
            from sqlalchemy import update
            from sqlmodel import col

            from ..db.redisdb import aredis as redis
            from ..models.models import Session as SessionModel

            stmt = (
                update(SessionModel)
                .where(col(SessionModel.session_id) == session_id)
                .values(
                    input_tokens=SessionModel.input_tokens
                    + usage_data.get("input_tokens", 0),
                    output_tokens=SessionModel.output_tokens
                    + usage_data.get("output_tokens", 0),
                    total_tokens=SessionModel.total_tokens
                    + usage_data.get("total_tokens", 0),
                    cached_input_tokens=SessionModel.cached_input_tokens
                    + usage_data.get("input_token_details", {}).get("cache_read", 0),
                    reasoning_tokens=SessionModel.reasoning_tokens
                    + usage_data.get("output_token_details", {}).get("reasoning", 0),
                )
            )
            await db.execute(stmt)
            await db.commit()

            user_id = config.get("metadata", {}).get("user_id")
            if user_id:
                keys_pattern = f"sessions_all:{user_id}:*"
                async for key in redis.scan_iter(keys_pattern):
                    await redis.delete(key)
        except Exception as e:
            logger.error(f"Error updating session tokens: {e}")

        yield f"data: {json.dumps({'type': 'usage', 'content': usage_data})}\n\n"

    yield f"data: {json.dumps({'type': 'done', 'content': full})}\n\n"
