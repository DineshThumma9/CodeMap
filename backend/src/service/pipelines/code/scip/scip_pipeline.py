import re
import tempfile
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from src.models.code_schema import GraphEntity, GraphRelation
from src.models.schema import GitRequest

from ...code.tree_sitter.code_pipeline import CodePipeline
from ..scip.scip_indexer import ScipIndexer, ScipIndexParser

LANG_NORMALIZATION = {
    "tsx": "typescript",
    "ts": "typescript",
    "typescript": "typescript",
    "jsx": "javascript",
    "js": "javascript",
    "javascript": "javascript",
    "py": "python",
    "python": "python",
    "java": "java",
    "go": "go",
    "rust": "rust",
    "rs": "rust",
    "c": "c",
    "cpp": "cpp",
    "c++": "cpp",
    "c#": "csharp",
    "csharp": "csharp",
    "cs": "csharp",
    "dart": "dart",
    "kotlin": "kotlin",
    "kt": "kotlin",
}


def name_from_symbol(symbol: str) -> str:
    s = symbol.rstrip(".#")
    s = re.sub(r"\(\)\.?$", "", s)
    parts = re.split(r"[/#]", s)
    last = parts[-1] if parts else symbol
    return last or symbol


class SCIPPipeline(CodePipeline):
    def __init__(
        self,
        req: GitRequest,
        kb_id: str,
        branch: Optional[str] = None,
        token: Optional[str] = None,
    ):
        super().__init__(req, kb_id, branch, token)

    def extract(self):
        # 1. Run the base Tree-sitter extraction
        super().extract()
        # 2. Run the SCIP overlay to replace heuristics with high-precision graph data
        t0 = time.time()
        logger.info("[extract] Starting SCIP combine overlay...")
        self.combine()
        logger.info(f"[extract] SCIP combine complete in {time.time() - t0:.2f}s")

    def combine(self) -> None:
        """Run SCIP CLI, write graph, supplement with Tree-sitter, write SCIP CALLS edges."""

        if not self.tmp_dir:
            logger.warning(
                "[SCIP combine] No tmp_dir available, skipping SCIP combine."
            )
            return

        path = Path(self.tmp_dir)
        index_root = path.resolve()

        # Determine majority programming language of the repository (ignoring non-code formats like json/yaml/markdown)
        from src.service.constants import EXTENSION_TO_SCIP

        supported_scip_langs = {
            mapped_lang for _, (mapped_lang, _, _, _) in EXTENSION_TO_SCIP.items()
        }
        lang_counts = {}
        for f in self.processed_code:
            norm_lang = LANG_NORMALIZATION.get(f.language.lower(), f.language.lower())
            if norm_lang in supported_scip_langs:
                lang_counts[norm_lang] = lang_counts.get(norm_lang, 0) + 1

        active_langs = sorted(
            [cand_lang for cand_lang in lang_counts if lang_counts[cand_lang] > 0],
            key=lambda k: lang_counts[k],
            reverse=True,
        )
        if not active_langs:
            active_langs = ["python"]

        ts_files_count = len(self.processed_code)
        ts_entities_count = sum(len(f.entities) for f in self.processed_code)
        ts_relations_count = sum(len(f.relations) for f in self.processed_code)

        logger.info(
            f"[SCIP combine] Initializing SCIP overlay for '{path}' | "
            f"Active candidate languages: {active_langs} | "
            f"Pre-SCIP Tree-sitter baseline: {ts_files_count} files, {ts_entities_count} entities, {ts_relations_count} relations"
        )

        files_data = {}
        try:
            for lang in active_langs:
                with tempfile.TemporaryDirectory(prefix=f"scip_{lang}_") as tmpdir:
                    scip_file = ScipIndexer().run(path, lang, Path(tmpdir))
                    if not scip_file:
                        logger.warning(
                            f"[SCIP combine] ❌ SCIP indexer produced no output for {path} (lang={lang})."
                        )
                        continue

                    logger.info(
                        f"[SCIP combine] SCIP indexer generated index for {lang} at: {scip_file}"
                    )
                    scip_data = ScipIndexParser().parse(scip_file, path)
                    if scip_data and scip_data.get("files"):
                        files_data.update(scip_data["files"])

            if not files_data:
                logger.warning(
                    "[SCIP combine] ⚠️ SCIP index parsed 0 files. "
                    "Falling back to Tree-sitter baseline."
                )
                return

            scip_file_count = len(files_data)
            logger.info(
                f"[SCIP combine] Found {scip_file_count} parsed files across SCIP index runs"
            )

            matched_files_count = 0
            unmatched_files = []
            total_scip_classes = 0
            total_scip_structures = 0
            total_scip_functions = 0
            total_scip_contains = 0
            total_scip_calls = 0

            # Map from absolute path to relative path
            for abs_path_str, file_data in files_data.items():
                abs_path = Path(abs_path_str)
                try:
                    rel_path = abs_path.relative_to(index_root).as_posix()
                except ValueError:
                    rel_path = abs_path.name

                # Find the matching FileParseResult from the tree-sitter extract phase
                target_result = next(
                    (
                        r
                        for r in self.processed_code
                        if r.file_path == rel_path
                        or Path(r.file_path).as_posix() == rel_path
                    ),
                    None,
                )
                if not target_result:
                    unmatched_files.append(rel_path)
                    logger.debug(
                        f"[SCIP combine] SCIP file '{rel_path}' not found in repo's processed files (skipping)"
                    )
                    continue

                matched_files_count += 1
                ts_file_entities = len(target_result.entities)
                ts_file_relations = len(target_result.relations)

                # Retain existing Tree-sitter function complexity metadata before overlay
                ts_fn_meta = {
                    e.name: e
                    for e in target_result.entities
                    if e.kind in ("Function", "Method")
                }
                ts_cls_meta = {
                    e.name: e for e in target_result.entities if e.kind == "Class"
                }

                # Remove existing Tree-sitter heuristic relations and sub-file entities
                target_result.entities = [
                    e for e in target_result.entities if e.kind == "File"
                ]
                target_result.relations = [
                    r
                    for r in target_result.relations
                    if r.rel_type not in ("CALLS", "CONTAINS", "INHERITS")
                ]

                file_classes = 0
                file_structures = 0
                file_functions = 0
                file_contains = 0
                file_calls = 0

                # Process SCIP Classes
                for cls in file_data.classes or []:
                    if not cls.name:
                        continue
                    node_id = f"{rel_path}:{cls.name}"
                    target_result.entities.append(
                        GraphEntity(
                            node_id=node_id,
                            name=cls.name,
                            kind="Class",
                            parent_id=rel_path,
                        )
                    )
                    target_result.relations.append(
                        GraphRelation(
                            source_id=rel_path, target_id=node_id, rel_type="CONTAINS"
                        )
                    )
                    file_classes += 1
                    file_contains += 1

                    # Inherits edges
                    bases = cls.bases or []
                    if not bases and cls.name in ts_cls_meta:
                        bases = getattr(ts_cls_meta[cls.name], "bases", []) or []
                    for base_name in bases:
                        if base_name:
                            target_result.relations.append(
                                GraphRelation(
                                    source_id=node_id,
                                    target_id=base_name,
                                    rel_type="INHERITS",
                                )
                            )

                # Process SCIP Structs, Traits, Interfaces, Enums
                for category in ["structs", "traits", "interfaces", "enums"]:
                    items = getattr(file_data, category, []) or []
                    for item in items:
                        item_name = getattr(item, "name", None)
                        if not item_name:
                            continue
                        node_id = f"{rel_path}:{item_name}"
                        # Derive kind string: e.g. "structs" -> "Struct"
                        kind_str = (
                            category.capitalize()[:-1]
                            if category.endswith("s")
                            else category.capitalize()
                        )
                        target_result.entities.append(
                            GraphEntity(
                                node_id=node_id,
                                name=item_name,
                                kind=kind_str,
                                parent_id=rel_path,
                            )
                        )
                        target_result.relations.append(
                            GraphRelation(
                                source_id=rel_path,
                                target_id=node_id,
                                rel_type="CONTAINS",
                            )
                        )
                        file_structures += 1
                        file_contains += 1

                # Process SCIP Functions
                for fn in file_data.functions or []:
                    if not fn.name:
                        continue
                    node_id = f"{rel_path}:{fn.name}"

                    # Retain Tree-sitter cyclomatic complexity if available
                    if fn.name in ts_fn_meta:
                        orig = ts_fn_meta[fn.name]
                        if getattr(orig, "cyclomatic_complexity", None) is not None:
                            fn.cyclomatic_complexity = orig.cyclomatic_complexity

                    target_result.entities.append(
                        GraphEntity(
                            node_id=node_id,
                            name=fn.name,
                            kind=fn.kind.capitalize() if fn.kind else "Function",
                            parent_id=rel_path,
                        )
                    )
                    target_result.relations.append(
                        GraphRelation(
                            source_id=rel_path, target_id=node_id, rel_type="CONTAINS"
                        )
                    )
                    file_functions += 1
                    file_contains += 1

                # Process SCIP CALLS
                all_calls = (file_data.functionCalls or []) + (
                    file_data.moduleCalls or []
                )
                for call in all_calls:
                    caller_name = (
                        name_from_symbol(call.caller) if call.caller else "<module>"
                    )
                    callee_name = (
                        name_from_symbol(call.calling) if call.calling else "<unknown>"
                    )

                    caller_file_rel = rel_path
                    if call.caller_file:
                        try:
                            caller_file_rel = (
                                Path(call.caller_file)
                                .relative_to(index_root)
                                .as_posix()
                            )
                        except ValueError:
                            caller_file_rel = Path(call.caller_file).name

                    source_node_id = f"{caller_file_rel}:{caller_name}"

                    callee_file_rel = ""
                    if getattr(call, "calle_file", None):
                        try:
                            callee_file_rel = (
                                Path(call.calle_file).relative_to(index_root).as_posix()
                            )
                        except (ValueError, TypeError):
                            callee_file_rel = (
                                Path(call.calle_file).name if call.calle_file else ""
                            )

                    target_node_id = (
                        f"{callee_file_rel}:{callee_name}"
                        if callee_file_rel
                        else callee_name
                    )

                    target_result.relations.append(
                        GraphRelation(
                            source_id=source_node_id,
                            target_id=target_node_id,
                            rel_type="CALLS",
                            properties={
                                "type": call.typeCall,
                                "caller_file": caller_file_rel,
                                "callee_file": callee_file_rel or caller_file_rel,
                                "callee_line": call.ref_line,
                            },
                        )
                    )
                    file_calls += 1

                total_scip_classes += file_classes
                total_scip_structures += file_structures
                total_scip_functions += file_functions
                total_scip_contains += file_contains
                total_scip_calls += file_calls

                file_scip_entities = file_classes + file_structures + file_functions
                file_scip_relations = file_contains + file_calls

                logger.info(
                    f"[SCIP combine] File '{rel_path}': "
                    f"+{file_scip_entities} entities (classes={file_classes}, functions={file_functions}, structures={file_structures}), "
                    f"+{file_scip_relations} relations (CONTAINS={file_contains}, CALLS={file_calls}) | "
                    f"Current totals for file: {len(target_result.entities)} entities, {len(target_result.relations)} relations "
                    f"(Tree-sitter was: {ts_file_entities} entities, {ts_file_relations} relations)"
                )

            total_scip_entities = (
                total_scip_classes + total_scip_structures + total_scip_functions
            )
            total_scip_relations = total_scip_contains + total_scip_calls

            final_total_entities = sum(len(f.entities) for f in self.processed_code)
            final_total_relations = sum(len(f.relations) for f in self.processed_code)

            if unmatched_files:
                logger.warning(
                    f"[SCIP combine] {len(unmatched_files)} SCIP file(s) had no match in self.processed_code: {unmatched_files}"
                )

            logger.info(
                f"[SCIP combine] ── SCIP Combine Summary ──\n"
                f"  • Files parsed through SCIP: {scip_file_count}\n"
                f"  • Repository files overlaid: {matched_files_count}/{len(self.processed_code)}\n"
                f"  • SCIP entities added:       {total_scip_entities} "
                f"(classes={total_scip_classes}, functions={total_scip_functions}, structures={total_scip_structures})\n"
                f"  • SCIP relations added:      {total_scip_relations} "
                f"(CONTAINS={total_scip_contains}, CALLS={total_scip_calls})\n"
                f"  • Final graph state:         {final_total_entities} entities, {final_total_relations} relations\n"
                f"  • Baseline (Tree-sitter):    {ts_entities_count} entities, {ts_relations_count} relations"
            )

            if matched_files_count == 0 or (
                total_scip_entities == 0 and total_scip_relations == 0
            ):
                logger.warning(
                    "[SCIP combine] ⚠️ SCIP executed but produced 0 entities and 0 relations. "
                    "Tree-sitter fallback remains in effect."
                )
            else:
                logger.info(
                    f"[SCIP combine] ✅ SCIP is actively working! Successfully overlaid "
                    f"{total_scip_entities} compiler-grade entities and {total_scip_relations} relations across {matched_files_count} files."
                )

        except Exception as e:
            logger.exception(f"[SCIP combine] Error in SCIPPipeline.combine: {e}")
