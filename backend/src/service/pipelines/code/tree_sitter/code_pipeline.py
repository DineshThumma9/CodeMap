import concurrent
import concurrent.futures
import os
import re
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

import tree_sitter_language_pack as tlsp
from loguru import logger
from qdrant_client.models import PointStruct
from tree_sitter import Parser, Query, QueryCursor
from tree_sitter_language_pack import ProcessConfig, detect_language_from_path, process

from src.db.graphdb import get_graph
from src.db.vectordb import vector_db
from src.models.code_schema import (
    FileParseResult,
    GraphEntity,
    GraphRelation,
    ProcessResult,
    StructureItem,
)
from src.models.schema import GitRequest, ProjectNode
from src.service.constants import (
    _CALL_NODE_TYPES,
    _CLASS_DEF_TYPES,
    _FUNCTION_DEF_TYPES,
    _IGNORED_CALLS_BY_LANG,
    _INTERMEDIATE_TYPES,
    _NAME_CAPTURES,
    _UNIVERSAL_IGNORED,
    ALWAYS_SKIP,
    CHUNK_SIZE_BY_LANGUAGE,
    PARSE_AS_TEXT,
    SKIP_DIRS,
    SKIP_EXTENSIONS,
)

from ..languages.grammer import LANUGUAGE_GRAMMERS
from .complied_object import CompiledObjectProcessor


class CodePipeline:
    def __init__(
        self,
        req: GitRequest,
        kb_id: str,
        branch: Optional[str] = None,
        token: Optional[str] = None,
    ):

        self.repo_url: str = f"https://github.com/{req.owner}/{req.repo}.git"
        self.kb_id: str = kb_id
        self.branch = branch

        self.graph: Any = get_graph(kb_id)
        self.vector_db = vector_db

        self.project_files = []
        self.processed_code = []
        self.tmp_dir_obj: tempfile.TemporaryDirectory[str] | None = None
        self.tmp_dir: str | None = None
        self.complied_obj = CompiledObjectProcessor()

    def clone(self):
        t0 = time.time()
        logger.info(f"[clone] Starting git clone: {self.repo_url}")
        self.tmp_dir_obj = tempfile.TemporaryDirectory()
        self.tmp_dir = self.tmp_dir_obj.name
        try:
            clone_cmd = ["git", "clone", "--depth", "1"]
            if self.branch:
                clone_cmd.extend(["--branch", self.branch])
            clone_cmd.extend([self.repo_url, self.tmp_dir])

            subprocess.run(
                clone_cmd,
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info(f"[clone] Done in {time.time() - t0:.2f}s")
        except subprocess.CalledProcessError as e:
            logger.error(
                f"[clone] Git clone failed after {time.time() - t0:.2f}s: {e.stderr}"
            )
            raise RuntimeError(f"Failed to clone repository: {e.stderr}")

    def build(self):
        t0 = time.time()
        logger.info("[build] Scanning repository files...")
        if self.tmp_dir is None:
            raise RuntimeError(
                "Repository has not been cloned yet. Call clone() first."
            )
        temp_path = Path(self.tmp_dir)
        for file_path in temp_path.rglob("*"):
            if file_path.is_dir() or ".git" in file_path.parts:
                continue

            rel_path = str(file_path.relative_to(self.tmp_dir))

            if self.classify_file(rel_path) == "skip":
                continue

            try:
                content = file_path.read_text(encoding="utf-8")

                dirs = [
                    str(parent)
                    for parent in reversed(file_path.parents)
                    if str(parent) != "."
                ]
                parent_dir = (
                    str(file_path.parent) if str(file_path.parent) != "." else ""
                )

                self.project_files.append(
                    ProjectNode(
                        file_path=rel_path,
                        content=content,
                        filename=file_path.name,
                        parent_dir=parent_dir,
                        dirs=dirs,
                    )
                )
            except Exception as e:
                logger.warning(f"[build] Skipping unreadable file {rel_path}: {e}")

        logger.info(
            f"[build] Found {len(self.project_files)} processable files in {time.time() - t0:.2f}s"
        )

    @staticmethod
    def classify_file(path: str) -> str:
        """Returns 'skip', 'text', or 'parse'"""
        p = Path(path)
        filename = p.name

        if filename in ALWAYS_SKIP:
            return "skip"

        # Check if any part of the path matches SKIP_DIRS
        if any(d in p.parts for d in SKIP_DIRS):
            return "skip"

        if any(filename.endswith(ext) for ext in SKIP_EXTENSIONS):
            return "skip"

        if filename in PARSE_AS_TEXT:
            return "text"

        # markdown and plain text → text chunks
        if p.suffix in [".md", ".txt"]:
            return "text"

        return "parse"

    @staticmethod
    def _recover_arrow_function_name(content: str, start_line: int) -> Optional[str]:
        """Recover name for anonymous arrow functions/components assigned to const/let/var."""
        lines = content.splitlines()
        # Look around start_line (1-indexed) and up to 35 lines ahead for multiline props/signatures
        idx = start_line - 1
        snippet = " ".join(lines[max(0, idx - 2) : min(len(lines), idx + 35)])
        pattern = re.compile(
            r"(?:export\s+)?(?:const|let|var)\s+([a-zA-Z_$][a-zA-Z0-9_$]*)\s*(?::\s*[^=]+)?\s*="
        )
        m = pattern.search(snippet)
        if m:
            after_eq = snippet[m.end() :]
            if any(
                marker in after_eq
                for marker in (
                    "=>",
                    "function",
                    "forwardRef",
                    "memo",
                    "create<",
                    "create(",
                )
            ):
                return m.group(1)
        return None

    def _process_single_file(self, project_file: ProjectNode):

        take = self.classify_file(project_file.file_path)

        if take == "skip":
            return

        file_id = project_file.file_path
        result = FileParseResult(
            file_path=file_id,
            language="text",
            content=project_file.content,
        )

        lang_name = detect_language_from_path(file_id)

        if not lang_name:
            return

        result.language = lang_name

        config = ProcessConfig(
            language=lang_name,
            structure=True,
            imports=True,
            symbols=True,
            exports=True,
            chunk_max_size=CHUNK_SIZE_BY_LANGUAGE.get(lang_name, 2000),
        )

        raw_result = process(project_file.content, config)
        tree_dict = self.complied_obj.unpack(raw_result)
        parsed = ProcessResult.model_validate(tree_dict)
        structs = self.complied_obj.flatten_structures(parsed.structure)
        result.chunks = parsed.chunks
        result.entities.append(
            GraphEntity(
                node_id=file_id,
                name=file_id,
                kind="File",
            )
        )
        for struct in structs:
            name = (
                struct.name if isinstance(struct, StructureItem) else struct.get("name")
            )
            kind_raw = (
                struct.kind if isinstance(struct, StructureItem) else struct.get("kind")
            )

            if not name:
                span = getattr(struct, "span", None) or (
                    struct.get("span") if isinstance(struct, dict) else None
                )
                start_line = getattr(span, "start_line", None) or (
                    span.get("start_line") if isinstance(span, dict) else None
                )
                if start_line and lang_name in (
                    "typescript",
                    "tsx",
                    "javascript",
                    "jsx",
                    "typescriptreact",
                    "javascriptreact",
                ):
                    name = self._recover_arrow_function_name(
                        project_file.content, start_line
                    )

            if not name or not kind_raw:
                continue

            kind = (
                kind_raw.lower() if isinstance(kind_raw, str) else str(kind_raw).lower()
            )

            if kind == "class":
                class_id = f"{file_id}:{name}"
                result.entities.append(
                    GraphEntity(
                        node_id=class_id, name=name, kind="Class", parent_id=file_id
                    )
                )
                result.relations.append(
                    GraphRelation(
                        source_id=file_id, target_id=class_id, rel_type="CONTAINS"
                    )
                )
                bases = (
                    struct.bases
                    if isinstance(struct, StructureItem)
                    else (struct.get("bases") if isinstance(struct, dict) else [])
                ) or []
                for b in bases:
                    if b:
                        result.relations.append(
                            GraphRelation(
                                source_id=class_id,
                                target_id=b,
                                rel_type="INHERITS",
                            )
                        )
            elif kind in ("function", "method"):
                func_id = f"{file_id}:{name}"
                result.entities.append(
                    GraphEntity(
                        node_id=func_id, name=name, kind="Function", parent_id=file_id
                    )
                )
                result.relations.append(
                    GraphRelation(
                        source_id=file_id, target_id=func_id, rel_type="CONTAINS"
                    )
                )

        # Process IMPORTS
        known_files = getattr(self, "_known_file_paths", set())
        for imp in parsed.imports or []:
            if not imp.source:
                continue
            resolved = self._resolve_import_target(file_id, imp.source, known_files)
            if resolved and resolved != file_id:
                result.relations.append(
                    GraphRelation(
                        source_id=file_id,
                        target_id=resolved,
                        rel_type="IMPORTS",
                        properties={
                            "raw": imp.source[:120],
                        },
                    )
                )

        call_rels = self.process_function_calls(
            source_code=project_file.content, lang_name=lang_name, file_id=file_id
        )
        result.relations.extend(call_rels)
        return result

    @staticmethod
    def _resolve_import_target(
        source_file: str, raw_import: str, known_files: set[str]
    ) -> Optional[str]:
        s = raw_import.strip()
        m = re.search(r"""(?:from|import)\s+['"]([^'"]+)['"]""", s)
        if not m:
            m = re.match(r"^from\s+([a-zA-Z0-9_\.]+)\s+import", s)
        if not m:
            m = re.match(r"^import\s+([a-zA-Z0-9_\.]+)", s)

        if not m:
            return None
        target_mod = m.group(1)

        source_dir = Path(source_file).parent
        candidates = []

        if target_mod.startswith("."):
            if target_mod.startswith("./") or target_mod.startswith("../"):
                candidates.append(os.path.normpath(str(source_dir / target_mod)))
            else:
                dot_count = len(target_mod) - len(target_mod.lstrip("."))
                mod_part = target_mod.lstrip(".").replace(".", "/")
                rel_prefix = (
                    "." if dot_count == 1 else "/".join([".."] * (dot_count - 1))
                )
                candidates.append(
                    os.path.normpath(str(source_dir / rel_prefix / mod_part))
                )
        else:
            mod_as_path = target_mod.replace(".", "/")
            candidates.append(os.path.normpath(str(source_dir / mod_as_path)))
            candidates.append(os.path.normpath(str(source_dir / target_mod)))
            candidates.append(mod_as_path)
            candidates.append(target_mod)

        extensions = [
            "",
            ".py",
            ".ts",
            ".tsx",
            ".js",
            ".jsx",
            "/__init__.py",
            "/index.ts",
            "/index.js",
            "/index.tsx",
            "/index.jsx",
        ]

        for cand in candidates:
            for ext in extensions:
                test_path = os.path.normpath(cand + ext)
                if test_path in known_files:
                    return test_path

        return None

    def process_function_calls(self, source_code: str, lang_name: str, file_id: str):
        grammar = LANUGUAGE_GRAMMERS.get(lang_name)
        if not grammar or "calls" not in grammar:
            return []

        lang_obj = tlsp.get_language(lang_name)
        parser = Parser(lang_obj)
        query = Query(lang_obj, grammar["calls"])
        tree = parser.parse(source_code.encode("utf-8"))

        cursor = QueryCursor(query)
        raw = cursor.captures(tree.root_node)

        if isinstance(raw, dict):
            captures = sorted(
                [(node, name) for name, nodes in raw.items() for node in nodes],
                key=lambda x: x[0].start_byte,
            )
        else:
            captures = sorted(raw, key=lambda x: x[0].start_byte)

        # Dynamically fetch the ignore list for this specific language, merging it with universal garbage
        ignored_calls = (
            _IGNORED_CALLS_BY_LANG.get(lang_name, set()) | _UNIVERSAL_IGNORED
        )

        relations = []
        for node, capture_name in captures:
            if capture_name not in _NAME_CAPTURES or not node.text:
                continue

            called_name = node.text.decode("utf-8")

            # Use the language-specific ignore list
            if called_name in ignored_calls or len(called_name) < 2:
                continue

            call_node = node.parent
            while call_node and call_node.type in _INTERMEDIATE_TYPES:
                call_node = call_node.parent

            if not call_node or call_node.type not in _CALL_NODE_TYPES:
                continue

            curr = call_node.parent
            caller_parts = []

            while curr:
                if curr.type in _FUNCTION_DEF_TYPES or curr.type in _CLASS_DEF_TYPES:
                    name_node = curr.child_by_field_name("name")
                    if name_node and name_node.text:
                        caller_parts.append(name_node.text.decode("utf-8"))
                curr = curr.parent

            caller_name = (
                ":".join(reversed(caller_parts)) if caller_parts else "<module>"
            )

            args = []
            argument_node = call_node.child_by_field_name(
                "arguments"
            ) or call_node.child_by_field_name("argument_list")

            if argument_node:
                for arg in argument_node.children:
                    if arg.is_named and arg.text:
                        args.append(arg.text.decode("utf-8"))

            source_node_id = f"{file_id}:{caller_name}"

            relations.append(
                GraphRelation(
                    source_id=source_node_id,
                    target_id=called_name,
                    rel_type="CALLS",
                    properties={"args": args},
                )
            )

        return relations

    def extract(self):
        t0 = time.time()
        logger.info(
            f"[extract] Parsing {len(self.project_files)} files with ThreadPoolExecutor..."
        )
        self._known_file_paths = {f.file_path for f in self.project_files}
        max_workers = min(32, (os.cpu_count() or 1) * 2 + 1)
        errors = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [
                executor.submit(self._process_single_file, file_node)
                for file_node in self.project_files
            ]

            for future in concurrent.futures.as_completed(futures):
                try:
                    result = future.result()
                    if result:
                        self.processed_code.append(result)
                except Exception as exc:
                    errors += 1
                    logger.warning(f"[extract] File processing exception: {exc}")

        total_chunks = sum(len(f.chunks) for f in self.processed_code)
        total_entities = sum(len(f.entities) for f in self.processed_code)
        total_relations = sum(len(f.relations) for f in self.processed_code)
        logger.info(
            f"[extract] Done in {time.time() - t0:.2f}s — "
            f"{len(self.processed_code)} files parsed, {errors} errors | "
            f"chunks={total_chunks}, entities={total_entities}, relations={total_relations}"
        )

    def build_vectordb(self):
        t0 = time.time()
        texts = []
        metadatas = []

        for fl in self.processed_code:
            for chunk in fl.chunks:
                metadatas.append(
                    {
                        "kb_id": self.kb_id,
                        "file_path": fl.file_path,
                        "language": fl.language,
                        "start_byte": chunk.start_byte,
                        "end_byte": chunk.end_byte,
                    }
                )
                texts.append(chunk.content)

        if not texts:
            logger.warning("[vectordb] No chunks to embed — skipping Qdrant upload")
            return

        logger.info(f"[vectordb] Embedding {len(texts)} chunks...")
        vectors = self.vector_db.embeddings.embed_documents(texts)

        points = [
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vectors[j],
                payload={"page_content": texts[j], "metadata": metadatas[j]},
            )
            for j in range(len(texts))
        ]

        logger.info(f"[vectordb] Uploading {len(points)} points to Qdrant...")
        self.vector_db.client.upload_points(
            collection_name=self.vector_db.collection_name,
            points=points,
            batch_size=500,
            max_retries=3,
            wait=False,
        )

        logger.info(
            f"[vectordb] Uploaded all {len(points)} points in {time.time() - t0:.2f}s"
        )

    def build_graph(self):
        t0 = time.time()
        all_entites = []
        all_relations = []

        for code in self.processed_code:
            all_entites.extend([e.model_dump() for e in code.entities])
            all_relations.extend([r.model_dump() for r in code.relations])

        user_defined_functions = {
            e["name"]
            for e in all_entites
            if e["kind"] in ("Function", "Method", "Class")
        }
        user_defined_node_ids = {
            e["node_id"]
            for e in all_entites
            if e["kind"] in ("Function", "Method", "Class")
        }

        user_defined_classes = {
            e["name"]: e["node_id"] for e in all_entites if e["kind"] == "Class"
        }

        filtered_relations = []
        for r in all_relations:
            if r["rel_type"] == "INHERITS":
                base_name = r["target_id"]
                if base_name in user_defined_classes:
                    r["target_id"] = user_defined_classes[base_name]
                filtered_relations.append(r)
            elif r["rel_type"] == "CALLS":
                if (
                    r["target_id"] in user_defined_functions
                    or r["target_id"] in user_defined_node_ids
                ):
                    filtered_relations.append(r)
            else:
                filtered_relations.append(r)

        all_relations = filtered_relations

        if not all_entites or not all_relations:
            logger.warning(
                "[graph] No entities or relations to write — skipping FalkorDB"
            )
            return

        logger.info(
            f"[graph] Writing {len(all_entites)} entities and {len(all_relations)} relations to FalkorDB..."
        )

        try:
            self.graph.query("CREATE INDEX FOR (n:CodeNode) ON (n.id)")
        except Exception:
            pass

        try:
            self.graph.query("CREATE FULLTEXT INDEX FOR (n:CodeNode) ON (n.name)")
        except Exception:
            pass

        self.graph.query(
            """
                UNWIND $batch as entity
                MERGE (n:CodeNode {id:entity.node_id})
                ON CREATE SET
                    n.name = entity.name,
                    n.kind = entity.kind
                ON MATCH SET
                    n.name = CASE WHEN n.name IS NULL OR n.name = '' THEN entity.name ELSE n.name END,
                    n.kind = CASE WHEN n.kind IS NULL OR n.kind = '' THEN entity.kind ELSE n.kind END
            """,
            {"batch": all_entites},
        )

        for rel_type in ["CONTAINS", "CALLS", "IMPORTS", "HAS_METHOD", "INHERITS"]:
            specific_rel = [r for r in all_relations if r["rel_type"] == rel_type]
            if specific_rel:
                self.graph.query(
                    f"""
                    UNWIND $batch as rel
                    MERGE (source:CodeNode {{id:rel.source_id}})
                    ON CREATE SET
                        source.name = CASE WHEN rel.source_id CONTAINS ':' THEN split(rel.source_id, ':')[-1] ELSE rel.source_id END,
                        source.kind = CASE WHEN '{rel_type}' = 'INHERITS' THEN 'Class' ELSE 'Function' END
                    MERGE (target:CodeNode {{id:rel.target_id}})
                    ON CREATE SET
                        target.name = CASE WHEN rel.target_id CONTAINS ':' THEN split(rel.target_id, ':')[-1] ELSE rel.target_id END,
                        target.kind = CASE WHEN '{rel_type}' = 'INHERITS' THEN 'Class' ELSE 'Function' END
                    MERGE (source)-[r:{rel_type}]->(target)
                    SET r += rel.properties
                    """,
                    {"batch": specific_rel},
                )

        logger.info(f"[graph] FalkorDB write complete in {time.time() - t0:.2f}s")

        # Offline GraphBLAS Algorithm Enrichment (Bucket A)
        try:
            t_algo = time.time()
            logger.info(
                f"[graph] Running FalkorDB GraphBLAS matrix algorithms to enrich graph '{self.kb_id}'..."
            )

            # 1. PageRank (Utility / Authority score)
            self.graph.query(
                """
                CALL algo.pageRank('CodeNode', 'CALLS') YIELD node, score
                SET node.pagerank = score
                """
            )

            # 2. Harmonic Centrality (Root Orchestrator / Entrypoint reachability)
            self.graph.query(
                """
                CALL algo.HarmonicCentrality() YIELD node, score
                SET node.harmonic = score
                """
            )

            # 3. Betweenness Centrality (Bridge / Chokepoint score)
            self.graph.query(
                """
                CALL algo.betweenness({samplingSeed: 42}) YIELD node, score
                SET node.betweenness = score
                """
            )

            # 4. CDLP (Community / Subsystem domain discovery)
            self.graph.query(
                """
                CALL algo.labelPropagation() YIELD node, communityId
                SET node.community_id = communityId
                """
            )

            # 5. WCC (Weakly Connected Components / Dead code islands)
            self.graph.query(
                """
                CALL algo.WCC(null) YIELD node, componentId
                SET node.wcc_id = componentId
                """
            )

            logger.info(
                f"[graph] GraphBLAS enrichment completed in {time.time() - t_algo:.2f}s"
            )
        except Exception as e:
            logger.warning(
                f"[graph] GraphBLAS enrichment encountered warning (skipped): {e}"
            )

    def build_kb(self):
        """
        Clones, parses, and writes all code data to Qdrant + FalkorDB in parallel.
        """
        total_start = time.time()
        logger.info(
            f"[build_kb] Starting ingestion for KB: {self.kb_id} | Repo: {self.repo_url}"
        )

        t = time.time()
        self.clone()
        logger.info(f"[build_kb] clone: {time.time() - t:.2f}s")

        t = time.time()
        self.build()
        logger.info(f"[build_kb] build: {time.time() - t:.2f}s")

        t = time.time()
        self.extract()
        logger.info(f"[build_kb] extract: {time.time() - t:.2f}s")

        t = time.time()
        with ThreadPoolExecutor(max_workers=2) as executor:
            vectordb_task = executor.submit(self.build_vectordb)
            graphdb_task = executor.submit(self.build_graph)

            concurrent.futures.wait([vectordb_task, graphdb_task])

            for task in [vectordb_task, graphdb_task]:
                exc = task.exception()
                if exc is not None:
                    logger.error(f"[build_kb] Critical error in parallel write: {exc}")
                    raise exc

        logger.info(
            f"[build_kb] vectordb + graph parallel write: {time.time() - t:.2f}s"
        )
        logger.info(
            f"[build_kb] ✅ Total ingestion time: {time.time() - total_start:.2f}s"
        )
