import re
from collections import defaultdict
from typing import Any, List, Literal

import tree_sitter_language_pack as tlsp
from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate
from langchain_core.tools import StructuredTool
from langchain_falkordb import FalkorDBGraph
from langchain_falkordb import FalkorDBQAChain as GraphCypherQAChain
from langchain_groq import ChatGroq as Groq
from loguru import logger
from pydantic import SecretStr
from qdrant_client.http import models
from tree_sitter import Parser
from tree_sitter_language_pack import detect_language_from_path

from src.config.settings import settings
from src.db.vectordb import get_qdrant
from src.service.agent.prompts import CYPHER_GENERATION_TEMPLATE


async def _resolve_file_id(target: str, graph: Any) -> str | None:
    """Resolves a partial path or filename to a full file_id instantly."""
    if not graph or not target:
        return None

    clean_target = target.split("/")[-1].replace('"', "").strip()
    if not clean_target:
        return None

    logger.info(f"🕸️ [FALKORDB GRAPH] Resolving file ID for target='{clean_target}'...")
    result = await graph.query(
        """
        MATCH (node:CodeNode {kind: 'File'})
        WHERE node.id CONTAINS $target
        RETURN node.id AS path
        LIMIT 1
        """,
        {"target": clean_target},
    )

    if result and result.result_set:
        resolved = result.result_set[0][0]
        logger.info(f"🕸️ [FALKORDB GRAPH] Resolved '{clean_target}' -> '{resolved}'")
        return resolved

    logger.warning(f"🕸️ [FALKORDB GRAPH] Could not resolve file ID for '{clean_target}'")
    return None


def to_dict(result) -> List[dict]:
    if not result or not result.result_set:
        return []

    headers = [
        col[1] if isinstance(col, (list, tuple)) else str(col) for col in result.header
    ]
    return [dict(zip(headers, row)) for row in result.result_set]


def _extract_symbol_via_ast(
    full_text: str, symbol: str, file_path: str = ""
) -> str | None:
    """Uses Tree-sitter AST to extract compiler-accurate symbol nodes (handles JSX, TypeScript, arrow functions, nested scopes)."""
    lang_name = detect_language_from_path(file_path) if file_path else None
    if not lang_name:
        return None

    try:
        lang_obj = tlsp.get_language(lang_name)
        parser = Parser(lang_obj)
        encoded = full_text.encode("utf-8")
        tree = parser.parse(encoded)
    except Exception as e:
        logger.debug(
            f"[Tree-sitter AST] Could not parse with language '{lang_name}': {e}"
        )
        return None

    clean_sym = symbol.strip()

    def find_node(node, name):
        # 1. Standard function/method/class definitions
        if node.type in (
            "function_declaration",
            "method_definition",
            "class_declaration",
            "function_definition",
            "class_definition",
        ):
            name_node = node.child_by_field_name("name")
            if name_node and name_node.text.decode("utf-8") == name:
                # Include parent export or decorator if present
                if node.parent and node.parent.type in (
                    "export_statement",
                    "decorated_definition",
                ):
                    return node.parent
                return node

        # 2. Variable declarators: const MyComp = () => ... or export const handler = ...
        if node.type == "variable_declarator":
            name_node = node.child_by_field_name("name")
            if name_node and name_node.text.decode("utf-8") == name:
                p = node.parent
                if p and p.parent and p.parent.type == "export_statement":
                    return p.parent
                return p if p else node

        for child in node.children:
            res = find_node(child, name)
            if res:
                return res
        return None

    node = find_node(tree.root_node, clean_sym)
    if node:
        extracted = encoded[node.start_byte : node.end_byte].decode("utf-8")
        # Validate that AST node captured substantive code (not an empty header or error stub)
        if len(extracted.splitlines()) > 2 or (
            node.type in ("variable_declarator", "lexical_declaration")
            and ";" in extracted
        ):
            return extracted

    return None


def _extract_symbol_via_lexical(full_text: str, symbol: str) -> str | None:
    """Fallback lexical extractor using scope and indentation boundaries."""
    clean_sym = symbol.strip()
    if not clean_sym:
        return None

    patterns = [
        rf"^[ \t]*(?:async\s+def|def|class)\s+{re.escape(clean_sym)}\b",
        rf"^[ \t]*(?:export\s+)?(?:async\s+)?function\s+{re.escape(clean_sym)}\b",
        rf"^[ \t]*(?:export\s+)?(?:const|let|var)\s+{re.escape(clean_sym)}\s*=",
        rf"^[ \t]*(?:public|private|protected|static|\s)*(?:class|interface|enum|record)\s+{re.escape(clean_sym)}\b",
        rf"^[ \t]*(?:fn|func)\s+{re.escape(clean_sym)}\b",
        rf"^[ \t]*.*\b{re.escape(clean_sym)}\s*\(",
    ]

    start_line_idx = -1
    lines = full_text.splitlines()
    for pat in patterns:
        for idx, line in enumerate(lines):
            if re.search(pat, line):
                start_line_idx = idx
                break
        if start_line_idx != -1:
            break

    if start_line_idx == -1:
        return None

    has_braces = any(
        "{" in l for l in lines[start_line_idx : min(len(lines), start_line_idx + 10)]
    )
    def_line = lines[start_line_idx]
    base_indent = len(def_line) - len(def_line.lstrip())
    extracted = [def_line]

    if has_braces:
        brace_count = def_line.count("{") - def_line.count("}")
        opened = "{" in def_line
        for line in lines[start_line_idx + 1 :]:
            extracted.append(line)
            brace_count += line.count("{") - line.count("}")
            if "{" in line:
                opened = True
            if opened and brace_count <= 0:
                break
    else:
        # Indentation-based languages (Python)
        for line in lines[start_line_idx + 1 :]:
            stripped = line.strip()
            if not stripped:
                extracted.append(line)
                continue
            line_indent = len(line) - len(line.lstrip())
            if line_indent <= base_indent:
                if re.match(r"^(?:def\s|async\s+def\s|class\s|@)", stripped):
                    break
            extracted.append(line)

    while extracted and not extracted[-1].strip():
        extracted.pop()

    return "\n".join(extracted)


def _extract_symbol_from_code(
    full_text: str, symbol: str, file_path: str = ""
) -> str | None:
    """Hybrid symbol extractor: attempts Tree-sitter AST extraction first, falling back to lexical scope."""
    # 1. Compiler-grade Tree-sitter AST (handles TSX, React, JSX, nested arrow functions, types)
    ast_extracted = _extract_symbol_via_ast(full_text, symbol, file_path)
    if ast_extracted:
        return ast_extracted

    # 2. Lexical scope & indentation fallback (handles Python chunk-join artifacts or unparseable files)
    return _extract_symbol_via_lexical(full_text, symbol)


class CodeTools:
    def __init__(
        self,
        user_id: str,
        vector_db: Any | None = None,
        falkordb_ns: str | None = None,
        graph_obj: FalkorDBGraph | None = None,
        async_falkordb: Any = None,
        llm: BaseChatModel | None = None,
    ):
        self.user_id = user_id
        self.vector_db = vector_db
        self.falkordb_ns = falkordb_ns
        self.graph_obj = graph_obj
        self.async_falkordb: Any = async_falkordb
        self.llm = llm
        self.tools: list[Any] = []
        self.cypher_chain = None
        if self.graph_obj:
            self.cypher_chain = GraphCypherQAChain.from_llm(
                llm=Groq(
                    model=settings.cypher_llm, api_key=SecretStr(settings.groq_api_key)
                ),
                graph=self.graph_obj,
                cypher_prompt=PromptTemplate(
                    template=CYPHER_GENERATION_TEMPLATE,
                    input_variables=["schema", "question"],
                ),
                allow_dangerous_requests=True,
                return_direct=True,
            )

    @classmethod
    async def build(
        cls,
        user_id: str,
        vector_db: Any | None = None,
        falkordb_ns: str | None = None,
        graph_obj: FalkorDBGraph | None = None,
        async_falkordb: Any = None,
        llm: BaseChatModel | None = None,
    ) -> "CodeTools":
        instance = cls(user_id, vector_db, falkordb_ns, graph_obj, async_falkordb, llm)

        instance.tools = [
            StructuredTool.from_function(
                coroutine=instance.get_project_context,
                name="get_project_context",
                description=(
                    "Returns the project README, architectural overview, and file tree. "
                    "Use this when starting an analysis of a repository or when asked broad questions "
                    "about how the system works or its high-level design."
                ),
            ),
            StructuredTool.from_function(
                coroutine=instance.explore_codebase,
                name="explore_codebase",
                description=(
                    "Explore the contents of a directory or inspect symbol outlines in a file. "
                    "Pass a directory path (e.g. 'backend/src/service') to list files and subdirectories, "
                    "or a file path to view its declared classes and functions."
                ),
            ),
            StructuredTool.from_function(
                coroutine=instance.search_syntax,
                name="search_syntax",
                description=(
                    "Searches for EXACT declared function names, class names, or file names in the code graph. "
                    "DO NOT pass natural language phrases here (e.g. do NOT search for 'how does auth work'). "
                    "Only pass specific code identifier keywords (e.g. 'validate_token', 'process_payment', 'User')."
                ),
            ),
            StructuredTool.from_function(
                coroutine=instance.read_code,
                name="read_code",
                description=(
                    "Reads the source code of a file or a specific symbol (class or function). "
                    "Provide 'path' (file path) and optionally 'symbol' (function or class name to extract just that definition). "
                    "Omit 'symbol' to read the full file."
                ),
            ),
            StructuredTool.from_function(
                coroutine=instance.trace_call_graph,
                name="trace_call_graph",
                description=(
                    "Traces the local call graph neighborhood around a specific function or symbol. "
                    "Parameter 'direction' controls traversal mode: 'upstream' (callers that depend on this symbol), "
                    "'downstream' (callees that this symbol executes), or 'both' (360-degree local neighborhood view). "
                    "Parameter 'depth' controls traversal depth (1 to 4 hops, default 1). "
                    "Returns structured callers/callees with hop counts and a Mermaid flowchart."
                ),
            ),
            StructuredTool.from_function(
                coroutine=instance.trace_execution_path,
                name="trace_execution_path",
                description=(
                    "Finds the shortest execution call path between two functions or symbols ('source_symbol' and 'target_symbol') "
                    "using FalkorDB shortestPath graph traversal. Returns exact hops, intermediate functions, and a visual Mermaid flowchart."
                ),
            ),
            StructuredTool.from_function(
                coroutine=instance.assess_refactor_risk,
                name="assess_refactor_risk",
                description=(
                    "Assesses multi-hop refactoring risk and blast radius for a given 'symbol' (function, method, or class). "
                    "Calculates direct callers, transitive dependents (up to 4 hops), risk tier (CRITICAL/HIGH/MEDIUM/LOW), "
                    "and recommended refactoring action."
                ),
            ),
            StructuredTool.from_function(
                coroutine=instance.search_code,
                name="search_code",
                description=(
                    "Semantically searches code chunks and comments for concepts, features, or logic by meaning. "
                    "Best tool for conceptual, architectural, or behavioral queries (e.g. 'authentication flow', 'caching strategy', 'error handling')."
                ),
            ),
            StructuredTool.from_function(
                coroutine=instance.ask_architecture,
                name="ask_architecture",
                description=(
                    "Answers complex architecture questions using natural language Cypher queries — "
                    "cross-module dependencies, topological structure, dead code, or custom graph algorithms. "
                    "Use when standard tools would require too many manual hops."
                ),
            ),
        ]

        return instance

    def _require_graph(self) -> bool:
        return bool(self.async_falkordb and self.falkordb_ns)

    def _require_vector(self) -> bool:
        return bool(self.vector_db)

    async def get_project_context(self) -> str:
        """Returns the project README, high-level overview, and the full file hierarchy."""
        if not self._require_vector() or not self._require_graph():
            return "<error>No codebase is currently loaded.</error>"

        logger.info(
            f"🕸️ [FALKORDB GRAPH: get_project_context] Fetching hierarchy & README for graph='{self.falkordb_ns}'..."
        )
        context_str = "<project_context>\n"

        results = await self.async_falkordb.query(
            "MATCH (n:CodeNode {kind: 'File'}) WHERE toLower(n.id) CONTAINS 'readme' RETURN n.id AS path LIMIT 1"
        )
        readme_records = to_dict(results)

        if readme_records:
            r = readme_records[0]
            content = await self.read_code(r["path"])
            context_str += f"<readme path='{r['path']}'>\n{content}\n</readme>\n"
        else:
            context_str += "<readme>No project README available.</readme>\n"

        results = await self.async_falkordb.query(
            "MATCH (n:CodeNode {kind: 'File'}) RETURN n.id AS path ORDER BY n.id"
        )

        records = to_dict(results)
        logger.info(
            f"🕸️ [FALKORDB GRAPH: get_project_context] Found {len(records)} files in graph"
        )

        if records:
            hierarchy = "\n".join(f"<file path='{r['path']}'/>" for r in records)
            if len(hierarchy) > 3000:
                hierarchy = hierarchy[:3000] + "\n<!-- [truncated to save tokens] -->"
            context_str += f"\n<file_hierarchy>\n{hierarchy}\n</file_hierarchy>"
        else:
            context_str += "\n<file_hierarchy>No files found in the knowledge base.</file_hierarchy>"

        # FalkorDB GraphBLAS Architectural Context (Entrypoints, Pillars, Subsystems)
        try:
            # 1. Root Orchestrators & Entrypoints (Harmonic Centrality)
            hc_res = await self.async_falkordb.query(
                """
                MATCH (n:CodeNode)
                WHERE n.harmonic IS NOT NULL AND n.harmonic > 0
                RETURN coalesce(n.name, split(n.id, ':')[-1]) AS name,
                       n.id AS id,
                       coalesce(n.kind, 'Symbol') AS kind,
                       n.harmonic AS score
                ORDER BY score DESC LIMIT 5
                """
            )
            hc_records = to_dict(hc_res)
            if not hc_records:
                hc_res = await self.async_falkordb.query(
                    """
                    CALL algo.HarmonicCentrality() YIELD node, score
                    WHERE score > 0
                    RETURN coalesce(node.name, split(node.id, ':')[-1]) AS name,
                           node.id AS id,
                           coalesce(node.kind, 'Symbol') AS kind,
                           score
                    ORDER BY score DESC LIMIT 5
                    """
                )
                hc_records = to_dict(hc_res)

            if hc_records:
                entrypoints_xml = "\n".join(
                    f"  <entrypoint name='{r.get('name')}' id='{r.get('id')}' kind='{r.get('kind')}' harmonic_score='{round(float(r.get('score', 0)), 2)}' />"
                    for r in hc_records
                )
                context_str += f"\n<root_orchestrators (harmonic_centrality)>\n{entrypoints_xml}\n</root_orchestrators>"

            # 2. Foundational Utilities & Sinks (PageRank)
            pr_res = await self.async_falkordb.query(
                """
                MATCH (n:CodeNode)
                WHERE n.pagerank IS NOT NULL
                RETURN coalesce(n.name, split(n.id, ':')[-1]) AS name,
                       n.id AS id,
                       coalesce(n.kind, 'Symbol') AS kind,
                       n.pagerank AS score
                ORDER BY score DESC LIMIT 5
                """
            )
            pr_records = to_dict(pr_res)
            if not pr_records:
                pr_res = await self.async_falkordb.query(
                    """
                    CALL algo.pageRank('CodeNode', 'CALLS') YIELD node, score
                    RETURN coalesce(node.name, split(node.id, ':')[-1]) AS name,
                           node.id AS id,
                           coalesce(node.kind, 'Symbol') AS kind,
                           score
                    ORDER BY score DESC LIMIT 5
                    """
                )
                pr_records = to_dict(pr_res)

            if pr_records:
                pillars_xml = "\n".join(
                    f"  <pillar name='{r.get('name')}' id='{r.get('id')}' kind='{r.get('kind')}' pagerank='{round(float(r.get('score', 0)), 4)}' />"
                    for r in pr_records
                )
                context_str += f"\n<foundational_pillars (falkordb_pagerank)>\n{pillars_xml}\n</foundational_pillars>"

            # 3. Subsystem Domains (Label Propagation) - Domain clusters with entrypoint & core utility
            comm_res = await self.async_falkordb.query(
                """
                MATCH (n:CodeNode)
                WHERE n.community_id IS NOT NULL
                RETURN n.community_id AS community,
                       coalesce(n.name, split(n.id, ':')[-1]) AS name,
                       n.id AS id,
                       coalesce(n.kind, 'Symbol') AS kind,
                       coalesce(n.harmonic, 0.0) AS harmonic,
                       coalesce(n.pagerank, 0.0) AS pagerank
                """
            )
            comm_raw = to_dict(comm_res)
            if not comm_raw:
                lp_res = await self.async_falkordb.query(
                    """
                    CALL algo.labelPropagation() YIELD node, communityId
                    RETURN communityId AS community,
                           coalesce(node.name, split(node.id, ':')[-1]) AS name,
                           node.id AS id,
                           coalesce(node.kind, 'Symbol') AS kind,
                           coalesce(node.harmonic, 0.0) AS harmonic,
                           coalesce(node.pagerank, 0.0) AS pagerank
                    """
                )
                comm_raw = to_dict(lp_res)

            if comm_raw:
                comm_map: dict[Any, list[dict[str, Any]]] = defaultdict(list)
                for item in comm_raw:
                    comm_map[item["community"]].append(item)

                sorted_domains = sorted(
                    comm_map.items(), key=lambda kv: len(kv[1]), reverse=True
                )[:5]
                domain_lines = []
                for cid, members in sorted_domains:
                    best_entry = max(
                        members, key=lambda m: float(m.get("harmonic") or 0.0)
                    )
                    best_util = max(
                        members, key=lambda m: float(m.get("pagerank") or 0.0)
                    )
                    samples = ", ".join([str(m["name"]) for m in members[:5]])
                    e_name = best_entry.get("name")
                    e_score = round(float(best_entry.get("harmonic") or 0.0), 2)
                    u_name = best_util.get("name")
                    u_score = round(float(best_util.get("pagerank") or 0.0), 4)
                    domain_lines.append(
                        f"  <domain id='{cid}' size='{len(members)}' "
                        f"main_entrypoint='{e_name} (harmonic={e_score})' "
                        f"core_utility='{u_name} (pagerank={u_score})' "
                        f"key_symbols='{samples}' />"
                    )
                context_str += (
                    "\n<subsystem_domains (architectural_clusters)>\n"
                    + "\n".join(domain_lines)
                    + "\n</subsystem_domains>"
                )

        except Exception as e:
            logger.debug(
                f"[FALKORDB GRAPH: get_project_context] GraphBLAS enrichment query skipped: {e}"
            )

        context_str += "\n</project_context>"
        return context_str

    async def explore_codebase(self, path: str = "", limit: int = 20) -> str:
        """Explore the contents of a directory or a specific file to see its structure(Functions,Classes and Methods it contain)."""
        if not self._require_graph():
            return "<error>No codebase is currently loaded.</error>"

        limit = min(limit, 50)
        logger.info(
            f"🕸️ [FALKORDB GRAPH: explore_codebase] Exploring path='{path}', limit={limit}..."
        )

        results = await self.async_falkordb.query(
            """
            MATCH (file:CodeNode {kind: 'File'}) WHERE file.id CONTAINS $path
            OPTIONAL MATCH (file)-[:CONTAINS]->(symbol:CodeNode) WHERE symbol.kind IN ['Class', 'Function']
            RETURN file.id AS path, collect('[' + symbol.kind + '] ' + symbol.name) AS symbols LIMIT $limit
            """,
            {"path": path, "limit": limit},
        )
        records = to_dict(results)
        logger.info(
            f"🕸️ [FALKORDB GRAPH: explore_codebase] Returned {len(records)} entries from FalkorDB graph"
        )

        if not records:
            return f"<result>No files found matching path '{path}'.</result>"

        output_lines = ["<directory_exploration>"]
        for r in records:
            file_path = r["path"]
            symbols = r["symbols"]
            output_lines.append(f"  <file path='{file_path}'>")
            if symbols and any(symbols):
                for s in symbols:
                    if s:
                        output_lines.append(f"    <symbol>{s}</symbol>")
            output_lines.append("  </file>")
        output_lines.append("</directory_exploration>")

        return "\n".join(output_lines)

    async def search_syntax(self, keyword: str, limit: int = 15) -> str:
        if not self._require_graph():
            return "<error>No codebase is currently loaded.</error>"

        clean_kw = keyword.replace('"', "").strip()
        limit = min(limit, 50)
        records = []
        logger.info(
            f"🕸️ [FALKORDB GRAPH: search_syntax] Searching syntax for '{clean_kw}', limit={limit}..."
        )

        try:
            result = await self.async_falkordb.query(
                """
                CALL db.idx.fulltext.queryNodes('CodeNode', $query)
                YIELD node, score
                WITH node, score, (coalesce(node.pagerank, 0.0) * log(2.0 + coalesce(node.betweenness, 0.0))) AS authority
                RETURN node.kind AS kind, node.name AS name, node.id AS id, authority
                ORDER BY authority DESC, score DESC LIMIT $limit
                """,
                {"query": f"{clean_kw}*", "limit": limit},
            )
            records = to_dict(result)
        except Exception as e:
            logger.debug(
                f"[FALKORDB GRAPH: search_syntax] Fulltext query fallback: {e}"
            )
            records = []

        if not records:
            result = await self.async_falkordb.query(
                """
                MATCH (node:CodeNode)
                WHERE toLower(node.name) CONTAINS toLower($kw)
                   OR toLower(node.id) CONTAINS toLower($kw)
                WITH node, (coalesce(node.pagerank, 0.0) * log(2.0 + coalesce(node.betweenness, 0.0))) AS authority
                RETURN node.kind AS kind, node.name AS name, node.id AS id, authority
                ORDER BY authority DESC, node.id ASC
                LIMIT $limit
                """,
                {"kw": clean_kw, "limit": limit},
            )
            records = to_dict(result)

        logger.info(
            f"🕸️ [FALKORDB GRAPH: search_syntax] Found {len(records)} syntax matches in FalkorDB graph"
        )

        if not records:
            return f"<result>No exact or close syntax matches found for '{keyword}'.</result>"

        output = ["<syntax_search_results>"]
        for r in records:
            auth_val = r.get("authority")
            auth_str = f" authority='{round(float(auth_val), 4)}'" if auth_val else ""
            output.append(
                f"  <match kind='{r['kind']}' name='{r['name']}' path='{r['id']}'{auth_str} />"
            )
        output.append("</syntax_search_results>")

        return "\n".join(output)

    async def read_code(self, path: str, symbol: str | None = None) -> str:
        """Reads the complete content of a file or retrieves exact source code for a specific class/function."""
        if not self._require_vector():
            return "<error>No codebase is currently loaded.</error>"
        if not path or not path.strip():
            return "<error>'path' must not be empty.</error>"

        actual = await _resolve_file_id(path, self.async_falkordb)
        if not actual:
            return f"<error>File not found: '{path}'. Use explore_codebase to browse available files.</error>"

        q_client = get_qdrant()

        file_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="metadata.file_path",
                    match=models.MatchValue(value=actual),
                ),
                models.FieldCondition(
                    key="metadata.kb_id",
                    match=models.MatchValue(value=self.falkordb_ns or ""),
                ),
            ]
        )

        if not self.vector_db:
            return f"<error>Vector database not initialized for '{actual}'.</error>"

        records, _ = await q_client.scroll(
            collection_name=self.vector_db.collection_name,
            scroll_filter=file_filter,
            limit=500,
            with_payload=True,
        )

        if not records:
            # Check if file exists in FalkorDB to differentiate between "missing" and "empty/unparseable"
            res = await self.async_falkordb.query(
                "MATCH (n:CodeNode {id: $id, kind: 'File'}) RETURN n.id LIMIT 1",
                {"id": actual},
            )
            if res and res.result_set:
                return (
                    f"<error>File '{actual}' exists (verified via graph) but has no content indexed in Qdrant. "
                    "This typically means the file is empty, contains only unparseable types, or was skipped.</error>"
                )
            return f"<error>No content found for '{actual}'.</error>"

        sorted_records = sorted(
            records,
            key=lambda r: (r.payload or {}).get("metadata", {}).get("start_byte") or 0,
        )
        full_content = "\n".join(
            (r.payload or {}).get("page_content", "") for r in sorted_records
        )

        if symbol:
            symbol_pattern = symbol.strip()
            extracted_code = _extract_symbol_from_code(
                full_content, symbol_pattern, file_path=actual
            )
            if extracted_code:
                return (
                    f"<code_content file='{actual}' symbol='{symbol_pattern}'>\n"
                    f"<![CDATA[\n{extracted_code}\n]]>\n"
                    f"</code_content>"
                )

            # Fallback: locate chunk containing symbol and include surrounding context
            matching_indices = [
                i
                for i, r in enumerate(sorted_records)
                if symbol_pattern in (r.payload or {}).get("page_content", "")
            ]
            if matching_indices:
                min_idx = max(0, min(matching_indices))
                max_idx = min(len(sorted_records), max(matching_indices) + 2)
                fallback_content = "\n".join(
                    (r.payload or {}).get("page_content", "")
                    for r in sorted_records[min_idx:max_idx]
                )
                return (
                    f"<code_content file='{actual}' symbol='{symbol_pattern}'>\n"
                    f"<![CDATA[\n{fallback_content}\n]]>\n"
                    f"</code_content>"
                )

            return (
                f"<error>No code found for '{symbol}' in '{actual}'. "
                "Try explore_codebase to confirm the symbol name.</error>"
            )

        # Whole file read
        if len(full_content) <= 35000:
            return (
                f"<code_content file='{actual}'>\n"
                f"<![CDATA[\n{full_content}\n]]>\n"
                f"</code_content>"
            )

        truncated = full_content[:35000]
        return (
            f"<code_content file='{actual}'>\n"
            f"<![CDATA[\n{truncated}\n]]>\n\n"
            f"<!-- NOTE: File truncated at 35,000 characters (total {len(full_content)} chars). "
            f"Use 'symbol' parameter in read_code to extract specific classes or functions. -->\n"
            f"</code_content>"
        )

    async def trace_call_graph(
        self,
        symbol: str,
        direction: Literal["upstream", "downstream", "both"] = "upstream",
        depth: int = 1,
    ) -> str:
        """Traces the local call graph neighborhood around a specific symbol (function, method, or class).
        - direction='upstream': Discovers callers that depend on this symbol up to 'depth' hops.
        - direction='downstream': Discovers callees that this symbol executes up to 'depth' hops.
        - direction='both': Discovers both callers and callees for a complete 360-degree local neighborhood view.
        Parameter 'depth' controls traversal depth (1 to 4 hops, default 1).
        """
        if not self._require_graph():
            return "<error>No codebase is currently loaded.</error>"

        clean_sym = symbol.strip()
        if not clean_sym:
            return "<error>'symbol' must not be empty.</error>"

        depth = min(max(int(depth), 1), 4)
        dir_norm = direction.strip().lower()
        if dir_norm in ("caller", "callers", "incoming", "in"):
            dir_norm = "upstream"
        elif dir_norm in ("callee", "callees", "outgoing", "out"):
            dir_norm = "downstream"
        elif dir_norm in ("all", "neighborhood", "around"):
            dir_norm = "both"

        if dir_norm not in ("upstream", "downstream", "both"):
            dir_norm = "upstream"

        logger.info(
            f"🕸️ [FALKORDB GRAPH: trace_call_graph] Tracing '{clean_sym}' direction={dir_norm} depth={depth}..."
        )

        callers: list[dict[str, Any]] = []
        callees: list[dict[str, Any]] = []

        if dir_norm in ("upstream", "both"):
            if depth == 1:
                q_up = """
                MATCH (caller:CodeNode)-[:CALLS]->(target:CodeNode)
                WHERE (target.name = $sym OR target.id = $sym OR target.id ENDS WITH (":" + $sym))
                RETURN coalesce(caller.name, split(caller.id, ":")[-1]) AS name,
                       caller.id AS id,
                       coalesce(caller.kind, "Function") AS kind,
                       1 AS hops
                ORDER BY id ASC
                LIMIT 40
                """
            else:
                q_up = f"""
                MATCH p = (caller:CodeNode)-[:CALLS*1..{depth}]->(target:CodeNode)
                WHERE (target.name = $sym OR target.id = $sym OR target.id ENDS WITH (":" + $sym))
                RETURN coalesce(caller.name, split(caller.id, ":")[-1]) AS name,
                       caller.id AS id,
                       coalesce(caller.kind, "Function") AS kind,
                       length(p) AS hops
                ORDER BY hops ASC, id ASC
                LIMIT 50
                """
            up_res = await self.async_falkordb.query(q_up, {"sym": clean_sym})
            callers = to_dict(up_res)

        if dir_norm in ("downstream", "both"):
            if depth == 1:
                q_down = """
                MATCH (caller:CodeNode)-[:CALLS]->(target:CodeNode)
                WHERE (caller.name = $sym OR caller.id = $sym OR caller.id ENDS WITH (":" + $sym))
                RETURN coalesce(target.name, split(target.id, ":")[-1]) AS name,
                       target.id AS id,
                       coalesce(target.kind, "Function") AS kind,
                       1 AS hops
                ORDER BY name ASC
                LIMIT 40
                """
            else:
                q_down = f"""
                MATCH p = (caller:CodeNode)-[:CALLS*1..{depth}]->(target:CodeNode)
                WHERE (caller.name = $sym OR caller.id = $sym OR caller.id ENDS WITH (":" + $sym))
                RETURN coalesce(target.name, split(target.id, ":")[-1]) AS name,
                       target.id AS id,
                       coalesce(target.kind, "Function") AS kind,
                       length(p) AS hops
                ORDER BY hops ASC, name ASC
                LIMIT 50
                """
            down_res = await self.async_falkordb.query(q_down, {"sym": clean_sym})
            callees = to_dict(down_res)

        if not callers and not callees:
            return f"<result>No {dir_norm} call relationships found for '{clean_sym}' up to depth {depth}.</result>"

        lines: list[str] = []
        if dir_norm == "upstream":
            lines.append(
                f"<callers_of symbol='{clean_sym}' depth='{depth}' total='{len(callers)}'>"
            )
            for r in callers:
                lines.append(
                    f"  <caller name='{r.get('name')}' id='{r.get('id')}' kind='{r.get('kind')}' hops='{r.get('hops', 1)}' />"
                )
            lines.append("</callers_of>")
        elif dir_norm == "downstream":
            lines.append(
                f"<callees_of symbol='{clean_sym}' depth='{depth}' total='{len(callees)}'>"
            )
            for r in callees:
                lines.append(
                    f"  <callee name='{r.get('name')}' id='{r.get('id')}' kind='{r.get('kind')}' hops='{r.get('hops', 1)}' />"
                )
            lines.append("</callees_of>")
        else:
            lines.append(
                f"<call_neighborhood symbol='{clean_sym}' depth='{depth}' callers_count='{len(callers)}' callees_count='{len(callees)}'>"
            )
            lines.append("  <upstream_callers>")
            for r in callers:
                lines.append(
                    f"    <caller name='{r.get('name')}' id='{r.get('id')}' kind='{r.get('kind')}' hops='{r.get('hops', 1)}' />"
                )
            lines.append("  </upstream_callers>")
            lines.append("  <downstream_callees>")
            for r in callees:
                lines.append(
                    f"    <callee name='{r.get('name')}' id='{r.get('id')}' kind='{r.get('kind')}' hops='{r.get('hops', 1)}' />"
                )
            lines.append("  </downstream_callees>")
            lines.append("</call_neighborhood>")

        # Mermaid diagram for visual explainability
        def clean_id(raw: str) -> str:
            return re.sub(r"[^a-zA-Z0-9_]", "_", raw)

        def clean_lbl(raw: str) -> str:
            return raw.replace('"', "'")

        mermaid_lines = ["\n```mermaid\ngraph LR"]
        center_id = f"node_{clean_id(clean_sym)}"
        mermaid_lines.append(f'  {center_id}["{clean_lbl(clean_sym)} (Target)"]')

        # Render top callers (up to 8)
        for r in callers[:8]:
            cid = f"up_{clean_id(str(r.get('id', r.get('name'))))}"
            mermaid_lines.append(
                f'  {cid}["{clean_lbl(str(r.get("name")))}"] -->|CALLS| {center_id}'
            )

        # Render top callees (up to 8)
        for r in callees[:8]:
            did = f"down_{clean_id(str(r.get('id', r.get('name'))))}"
            mermaid_lines.append(
                f'  {center_id} -->|CALLS| {did}["{clean_lbl(str(r.get("name")))}"]'
            )

        mermaid_lines.append("```")
        lines.append("\n".join(mermaid_lines))

        return "\n".join(lines)

    async def search_code(self, query: str) -> str:
        """Semantic search over all indexed code chunks."""
        if not self.vector_db:
            return "<error>No knowledge base is currently loaded.</error>"
        if not query or not query.strip():
            return "<error>query must not be empty.</error>"

        docs = await self.vector_db.asimilarity_search(query.strip(), k=5)
        if not docs:
            return "<result>No relevant code found.</result>"

        results = []
        for d in docs:
            results.append(
                f"  <chunk file='{d.metadata.get('file_path', '?')}'>\n<![CDATA[\n{d.page_content}\n]]>\n  </chunk>"
            )

        return (
            "<semantic_search_results>\n"
            + "\n".join(results)
            + "\n</semantic_search_results>"
        )

    async def ask_architecture(self, query: str) -> str:
        """Natural-language architecture queries backed by GraphCypherQAChain."""
        if not self._require_graph():
            return "<error>No codebase is currently loaded.</error>"
        if not query:
            return "<error>Please provide a query.</error>"
        if not self.cypher_chain:
            return "<error>Architecture query is unavailable (graph not configured).</error>"

        logger.info(
            f"🕸️ [FALKORDB GRAPH: ask_architecture] Invoking Cypher QA chain for query='{query}'..."
        )
        try:
            result = await self.cypher_chain.ainvoke({"query": query})
            logger.info(
                "🕸️ [FALKORDB GRAPH: ask_architecture] Cypher QA chain answered successfully"
            )
            return f"<architecture_result>\n{result.get('result', result)}\n</architecture_result>"
        except Exception as e:
            error_str = str(e)
            if "SyntaxError" in error_str and "Invalid input" in error_str:
                logger.warning("Cypher generation failed — query too vague.")
                return "<error>Could not query the architecture: the query was too vague. Try asking specifically about imports, dependencies, or call relationships.</error>"
            logger.error(f"Error in ask_architecture: {e}")
            return f"<error>Error querying architecture: {e}</error>"

    async def trace_execution_path(self, source_symbol: str, target_symbol: str) -> str:
        """Finds the shortest call path between source_symbol and target_symbol using FalkorDB shortestPath."""
        if not self._require_graph():
            return "<error>No codebase is currently loaded.</error>"

        src = source_symbol.strip()
        tgt = target_symbol.strip()
        if not src or not tgt:
            return (
                "<error>'source_symbol' and 'target_symbol' must not be empty.</error>"
            )

        logger.info(
            f"🕸️ [FALKORDB GRAPH: trace_execution_path] Tracing shortest path from '{src}' to '{tgt}'..."
        )

        # 1. Try directed CALLS path
        query_directed = """
        MATCH (a:CodeNode), (b:CodeNode)
        WHERE (a.name = $src OR a.id = $src OR a.id ENDS WITH (":" + $src))
          AND (b.name = $tgt OR b.id = $tgt OR b.id ENDS WITH (":" + $tgt))
        WITH a, b, shortestPath((a)-[:CALLS*]->(b)) AS p
        WHERE p IS NOT NULL
        RETURN length(p) AS hops,
               [n IN nodes(p) | coalesce(n.name, split(n.id, ":")[-1])] AS path_names,
               [n IN nodes(p) | n.id] AS path_ids,
               [n IN nodes(p) | coalesce(n.kind, "Function")] AS path_kinds
        ORDER BY hops ASC
        LIMIT 1
        """
        res = await self.async_falkordb.query(query_directed, {"src": src, "tgt": tgt})
        records = to_dict(res)

        direction = "forward"
        # 2. If no forward path, check reverse path
        if not records:
            query_reverse = """
            MATCH (a:CodeNode), (b:CodeNode)
            WHERE (a.name = $src OR a.id = $src OR a.id ENDS WITH (":" + $src))
              AND (b.name = $tgt OR b.id = $tgt OR b.id ENDS WITH (":" + $tgt))
            WITH a, b, shortestPath((b)-[:CALLS*]->(a)) AS p
            WHERE p IS NOT NULL
            RETURN length(p) AS hops,
                   [n IN nodes(p) | coalesce(n.name, split(n.id, ":")[-1])] AS path_names,
                   [n IN nodes(p) | n.id] AS path_ids,
                   [n IN nodes(p) | coalesce(n.kind, "Function")] AS path_kinds
            ORDER BY hops ASC
            LIMIT 1
            """
            res_rev = await self.async_falkordb.query(
                query_reverse, {"src": src, "tgt": tgt}
            )
            records = to_dict(res_rev)
            if records:
                direction = "reverse"

        # 3. If still no path, check undirected structural relationship
        if not records:
            query_undirected = """
            MATCH (a:CodeNode), (b:CodeNode)
            WHERE (a.name = $src OR a.id = $src OR a.id ENDS WITH (":" + $src))
              AND (b.name = $tgt OR b.id = $tgt OR b.id ENDS WITH (":" + $tgt))
            WITH a, b, shortestPath((a)-[*]-(b)) AS p
            WHERE p IS NOT NULL
            RETURN length(p) AS hops,
                   [n IN nodes(p) | coalesce(n.name, split(n.id, ":")[-1])] AS path_names,
                   [n IN nodes(p) | n.id] AS path_ids,
                   [n IN nodes(p) | coalesce(n.kind, "Symbol")] AS path_kinds
            ORDER BY hops ASC
            LIMIT 1
            """
            res_undir = await self.async_falkordb.query(
                query_undirected, {"src": src, "tgt": tgt}
            )
            records = to_dict(res_undir)
            if records:
                direction = "structural"

        if not records:
            logger.info(
                f"🕸️ [FALKORDB GRAPH: trace_execution_path] No path found between '{src}' and '{tgt}'"
            )
            return f"<result>No connected path found between '{src}' and '{tgt}' in the graph.</result>"

        r = records[0]
        hops = r.get("hops", 0)
        names = r.get("path_names", [])
        ids = r.get("path_ids", [])
        kinds = r.get("path_kinds", [])

        # Generate Mermaid diagram
        mermaid_lines = ["```mermaid", "graph LR"]
        for i in range(len(names)):
            clean_name = str(names[i]).replace('"', "'")
            kind_str = kinds[i] if i < len(kinds) else "Symbol"
            mermaid_lines.append(f'  n{i}["{clean_name}\\n({kind_str})"]')
        for i in range(len(names) - 1):
            rel_label = "CALLS" if direction != "structural" else "CONNECTED"
            mermaid_lines.append(f"  n{i} -->|{rel_label}| n{i + 1}")
        mermaid_lines.append("```")
        mermaid_block = "\n".join(mermaid_lines)

        # Build explainable trace
        path_steps = []
        for i in range(len(names)):
            kind_str = kinds[i] if i < len(kinds) else "Symbol"
            id_str = ids[i] if i < len(ids) else names[i]
            path_steps.append(f"  {i + 1}. [{kind_str}] {names[i]} ({id_str})")

        note = ""
        if direction == "reverse":
            note = f" (Note: Reverse call chain found from '{tgt}' to '{src}')"
        elif direction == "structural":
            note = " (Note: No direct CALLS path found; showing closest structural containment path)"

        output = (
            f"<execution_path source='{src}' target='{tgt}' hops='{hops}' direction='{direction}'>\n"
            f"FalkorDB verified path with {hops} hops{note}:\n\n"
            f"{mermaid_block}\n\n"
            f"Step-by-step trace:\n" + "\n".join(path_steps) + "\n</execution_path>"
        )
        logger.info(
            f"🕸️ [FALKORDB GRAPH: trace_execution_path] Found {hops}-hop path ({direction})"
        )
        return output

    async def assess_refactor_risk(self, symbol: str) -> str:
        """Assesses multi-hop refactoring risk and blast radius for a given 'symbol' (function, method, or class).
        Calculates direct callers, transitive dependents (up to 4 hops), risk tier (CRITICAL/HIGH/MEDIUM/LOW),
        and recommended refactoring action.
        """
        if not self._require_graph():
            return "<error>No codebase is currently loaded.</error>"

        clean_sym = symbol.strip()
        if not clean_sym:
            return "<error>'symbol' must not be empty.</error>"

        logger.info(
            f"🕸️ [FALKORDB GRAPH: assess_refactor_risk] Assessing blast radius for '{clean_sym}'..."
        )

        # 1. Direct callers count & sample
        q_direct = """
        MATCH (c:CodeNode)-[:CALLS]->(t:CodeNode)
        WHERE (t.name = $sym OR t.id = $sym OR t.id ENDS WITH (":" + $sym))
        RETURN count(DISTINCT c) AS direct_callers,
               collect(DISTINCT coalesce(c.name, split(c.id, ":")[-1]))[0..10] AS direct_list
        """
        res_dir = await self.async_falkordb.query(q_direct, {"sym": clean_sym})
        dir_recs = to_dict(res_dir)
        direct_count = dir_recs[0].get("direct_callers", 0) if dir_recs else 0
        direct_list = dir_recs[0].get("direct_list", []) if dir_recs else []

        # 2. Transitive blast radius (1..4 hops)
        q_trans = """
        MATCH (c:CodeNode)-[:CALLS*1..4]->(t:CodeNode)
        WHERE (t.name = $sym OR t.id = $sym OR t.id ENDS WITH (":" + $sym))
        RETURN count(DISTINCT c) AS blast_radius,
               collect(DISTINCT coalesce(c.name, split(c.id, ":")[-1]))[0..12] AS dependents
        """
        res_trans = await self.async_falkordb.query(q_trans, {"sym": clean_sym})
        trans_recs = to_dict(res_trans)
        blast_radius = trans_recs[0].get("blast_radius", 0) if trans_recs else 0
        dependents = trans_recs[0].get("dependents", []) if trans_recs else []

        # 3. Categorize Risk
        if blast_radius >= 12 or direct_count >= 6:
            risk_tier = "CRITICAL"
            action_advice = "Modifying this symbol will break multiple modules. Comprehensive regression test required."
        elif blast_radius >= 6 or direct_count >= 3:
            risk_tier = "HIGH"
            action_advice = (
                "Heavy downstream dependency. Coordinate changes across caller files."
            )
        elif blast_radius >= 2:
            risk_tier = "MEDIUM"
            action_advice = "Moderate caller footprint. Verify direct callers before altering signature."
        elif blast_radius == 1:
            risk_tier = "LOW"
            action_advice = "Isolated symbol with 1 dependent caller."
        else:
            risk_tier = "MINIMAL"
            action_advice = "Zero upstream callers detected in graph. Safe to refactor."

        dep_sample = ", ".join(dependents) if dependents else "None"
        dir_sample = ", ".join(direct_list) if direct_list else "None"

        return (
            f"<refactor_risk_assessment symbol='{clean_sym}'>\n"
            f"  <risk_level>{risk_tier}</risk_level>\n"
            f"  <direct_callers count='{direct_count}'>{dir_sample}</direct_callers>\n"
            f"  <transitive_blast_radius count='{blast_radius}'>{dep_sample}</transitive_blast_radius>\n"
            f"  <recommendation>{action_advice}</recommendation>\n"
            f"</refactor_risk_assessment>"
        )
