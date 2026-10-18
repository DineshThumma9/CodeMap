import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.models.code_schema import (
    ClassDef,
    FileParseResult,
    FunctionCalls,
    FunctionDef,
    GraphEntity,
    GraphRelation,
    ProcessedCode,
)
from src.models.schema import GitRequest
from src.service.pipelines.code.scip.scip_pipeline import SCIPPipeline


def test_scip_combine_mock():
    """Unit test for SCIPPipeline.combine using mocked SCIP parser output."""
    pipeline = SCIPPipeline.__new__(SCIPPipeline)
    pipeline.tmp_dir = "/tmp/mock_repo"
    pipeline.processed_code = [
        FileParseResult(
            file_path="src/main.py",
            language="python",
            content="class Foo:\n    def bar(self):\n        pass",
            entities=[
                GraphEntity(node_id="src/main.py", name="main.py", kind="File"),
                GraphEntity(node_id="src/main.py:OldFoo", name="OldFoo", kind="Class"),
            ],
            relations=[
                GraphRelation(
                    source_id="src/main.py",
                    target_id="src/main.py:OldFoo",
                    rel_type="CONTAINS",
                )
            ],
        )
    ]

    mock_file_data = ProcessedCode(
        path="/tmp/mock_repo/src/main.py",
        language="python",
        classes=[ClassDef(name="Foo")],
        functions=[FunctionDef(name="bar", kind="Function")],
        functionCalls=[
            FunctionCalls(
                caller="bar",
                calling="baz",
                typeCall="Function-Level",
                ref_line=10,
            )
        ],
    )

    mock_scip_data = {
        "files": {"/tmp/mock_repo/src/main.py": mock_file_data},
        "symbol_table": {},
    }

    with (
        patch(
            "src.service.pipelines.code.scip.scip_pipeline.ScipIndexer"
        ) as mock_indexer_cls,
        patch(
            "src.service.pipelines.code.scip.scip_pipeline.ScipIndexParser"
        ) as mock_parser_cls,
    ):
        mock_indexer = MagicMock()
        mock_indexer.run.return_value = Path("/tmp/mock/index.scip")
        mock_indexer_cls.return_value = mock_indexer

        mock_parser = MagicMock()
        mock_parser.parse.return_value = mock_scip_data
        mock_parser_cls.return_value = mock_parser

        pipeline.combine()

    target = pipeline.processed_code[0]
    entity_names = {e.name for e in target.entities}
    assert "Foo" in entity_names
    assert "bar" in entity_names
    assert "OldFoo" not in entity_names  # Heuristic entity was wiped

    relation_types = {r.rel_type for r in target.relations}
    assert "CONTAINS" in relation_types
    assert "CALLS" in relation_types
    assert len(target.entities) == 3  # File + Foo (Class) + bar (Function)
    assert len(target.relations) == 3  # 2 CONTAINS + 1 CALLS


def test_scip_qualified_call_target_and_inherits():
    """Unit test verifying qualified call target IDs, INHERITS relations, and AST complexity preservation."""
    pipeline = SCIPPipeline.__new__(SCIPPipeline)
    pipeline.tmp_dir = "/tmp/mock_repo"
    pipeline.processed_code = [
        FileParseResult(
            file_path="src/main.py",
            language="python",
            content="",
            entities=[
                GraphEntity(node_id="src/main.py", name="main.py", kind="File"),
                GraphEntity(node_id="src/main.py:bar", name="bar", kind="Function"),
            ],
            relations=[],
        )
    ]

    mock_file_data = ProcessedCode(
        path="/tmp/mock_repo/src/main.py",
        language="python",
        classes=[ClassDef(name="Foo", bases=["BaseService"])],
        functions=[FunctionDef(name="bar", kind="Function")],
        functionCalls=[
            FunctionCalls(
                caller="bar",
                calling="execute",
                calle_file="/tmp/mock_repo/src/service.py",
                typeCall="Function-Level",
                ref_line=15,
            )
        ],
    )

    mock_scip_data = {
        "files": {"/tmp/mock_repo/src/main.py": mock_file_data},
        "symbol_table": {},
    }

    with (
        patch(
            "src.service.pipelines.code.scip.scip_pipeline.ScipIndexer"
        ) as mock_indexer_cls,
        patch(
            "src.service.pipelines.code.scip.scip_pipeline.ScipIndexParser"
        ) as mock_parser_cls,
    ):
        mock_indexer = MagicMock()
        mock_indexer.run.return_value = Path("/tmp/mock/index.scip")
        mock_indexer_cls.return_value = mock_indexer

        mock_parser = MagicMock()
        mock_parser.parse.return_value = mock_scip_data
        mock_parser_cls.return_value = mock_parser

        pipeline.combine()

    target = pipeline.processed_code[0]

    # 1. Verify INHERITS relation was created
    inherits_rels = [r for r in target.relations if r.rel_type == "INHERITS"]
    assert len(inherits_rels) == 1
    assert inherits_rels[0].source_id == "src/main.py:Foo"
    assert inherits_rels[0].target_id == "BaseService"

    # 2. Verify CALLS target_id was qualified with relative callee file path
    calls_rels = [r for r in target.relations if r.rel_type == "CALLS"]
    assert len(calls_rels) == 1
    assert calls_rels[0].source_id == "src/main.py:bar"
    assert calls_rels[0].target_id == "src/service.py:execute"
    assert calls_rels[0].properties.get("callee_file") == "src/service.py"


def test_occurrence_range_typed():
    """Unit test verifying that _occurrence_range handles typed_range oneof correctly."""
    from src.service.pipelines.code.scip.scip_indexer import _occurrence_range

    # Single-line range mock
    mock_single = MagicMock()
    mock_single.line = 12
    mock_single.start_character = 4
    mock_single.end_character = 18

    mock_occ = MagicMock()
    mock_occ.WhichOneof.return_value = "single_line_range"
    mock_occ.single_line_range = mock_single

    res = _occurrence_range(mock_occ)
    assert res == [12, 4, 18]

    # Legacy repeated int range fallback
    mock_legacy_occ = MagicMock()
    mock_legacy_occ.WhichOneof.return_value = None
    mock_legacy_occ.range = [5, 1, 5, 20]

    res_legacy = _occurrence_range(mock_legacy_occ)
    assert res_legacy == [5, 1, 5, 20]


def test_scip_combine_fallback_when_indexer_fails():
    """Unit test verifying that SCIP combine safely falls back if indexer produces no output."""
    pipeline = SCIPPipeline.__new__(SCIPPipeline)
    pipeline.tmp_dir = "/tmp/mock_repo"
    baseline_entity = GraphEntity(node_id="src/main.py:Foo", name="Foo", kind="Class")
    pipeline.processed_code = [
        FileParseResult(
            file_path="src/main.py",
            language="python",
            content="",
            entities=[baseline_entity],
            relations=[],
        )
    ]

    with patch(
        "src.service.pipelines.code.scip.scip_pipeline.ScipIndexer"
    ) as mock_indexer_cls:
        mock_indexer = MagicMock()
        mock_indexer.run.return_value = None  # Indexer failed
        mock_indexer_cls.return_value = mock_indexer

        pipeline.combine()

    # Baseline entities preserved
    assert len(pipeline.processed_code[0].entities) == 1
    assert pipeline.processed_code[0].entities[0].name == "Foo"


@pytest.mark.skipif(
    shutil.which("docker") is None, reason="Docker is required for live SCIP tests"
)
def test_live_scip_with_user_repo_chatbot_agent():
    """Live integration test: tests SCIP against user repo DineshThumma9/chatbot-agent (Python)."""
    req = GitRequest(owner="DineshThumma9", repo="chatbot-agent", branch="main")
    pipeline = SCIPPipeline(req=req, kb_id="test-live-chatbot-agent")

    pipeline.clone()
    try:
        pipeline.build()
        assert len(pipeline.project_files) > 0, "No files found in cloned repository"

        pipeline.extract()
        assert len(pipeline.processed_code) > 0, "No files processed in extract phase"

        total_entities = sum(len(f.entities) for f in pipeline.processed_code)
        total_relations = sum(len(f.relations) for f in pipeline.processed_code)

        # SCIP must have produced entities and relations
        assert total_entities > 0, "SCIP produced 0 entities"
        assert total_relations > 0, "SCIP produced 0 relations"

        # Check for specific expected functions in chatbot-agent
        all_entity_names = {e.name for f in pipeline.processed_code for e in f.entities}
        assert any(
            name in all_entity_names
            for name in ["ChatState", "calculator_tool", "chat_node", "load_session"]
        ), f"Expected symbols not found in entities: {all_entity_names}"

    finally:
        if pipeline.tmp_dir_obj:
            pipeline.tmp_dir_obj.cleanup()


@pytest.mark.skipif(
    shutil.which("docker") is None, reason="Docker is required for live SCIP tests"
)
def test_live_scip_with_user_repo_redhideit():
    """Live integration test: tests SCIP against user repo DineshThumma9/RedHideit (JavaScript)."""
    req = GitRequest(owner="DineshThumma9", repo="RedHideit", branch="main")
    pipeline = SCIPPipeline(req=req, kb_id="test-live-redhideit")

    pipeline.clone()
    try:
        pipeline.build()
        assert len(pipeline.project_files) > 0, "No files found in cloned repository"

        pipeline.extract()
        assert len(pipeline.processed_code) > 0, "No files processed in extract phase"

        total_entities = sum(len(f.entities) for f in pipeline.processed_code)
        total_relations = sum(len(f.relations) for f in pipeline.processed_code)

        assert total_entities > 0, "SCIP produced 0 entities"
        assert total_relations > 0, "SCIP produced 0 relations"

        # Check that CALLS relations were resolved by SCIP
        calls_relations = [
            r
            for f in pipeline.processed_code
            for r in f.relations
            if r.rel_type == "CALLS"
        ]
        assert len(calls_relations) > 0, "SCIP produced no CALLS relations"

    finally:
        if pipeline.tmp_dir_obj:
            pipeline.tmp_dir_obj.cleanup()


@pytest.mark.skipif(
    shutil.which("docker") is None, reason="Docker is required for live SCIP tests"
)
def test_live_scip_with_user_repo_centralgpt_frontend():
    """Live integration test: tests SCIP against user repo DineshThumma9/centralGPT-frontend (TypeScript)."""
    req = GitRequest(owner="DineshThumma9", repo="centralGPT-frontend", branch="main")
    pipeline = SCIPPipeline(req=req, kb_id="test-live-centralgpt-frontend")

    pipeline.clone()
    try:
        pipeline.build()
        assert len(pipeline.project_files) > 0, "No files found in cloned repository"

        pipeline.extract()
        assert len(pipeline.processed_code) > 0, "No files processed in extract phase"

        total_entities = sum(len(f.entities) for f in pipeline.processed_code)
        total_relations = sum(len(f.relations) for f in pipeline.processed_code)

        assert total_entities > 0, "SCIP produced 0 entities"
        assert total_relations > 0, "SCIP produced 0 relations"

        # Check that CALLS relations were resolved by SCIP
        calls_relations = [
            r
            for f in pipeline.processed_code
            for r in f.relations
            if r.rel_type == "CALLS"
        ]
        assert len(calls_relations) > 0, "SCIP produced no CALLS relations"

    finally:
        if pipeline.tmp_dir_obj:
            pipeline.tmp_dir_obj.cleanup()


def test_typescript_arrow_function_recovery():
    """Verify that arrow functions and React components are recovered as functions with names."""
    from src.service.pipelines.code.scip.scip_indexer import ScipIndexParser
    from src.service.pipelines.code.tree_sitter.code_pipeline import CodePipeline

    content = """
    export const login = (data: { username: string }) => {
        return api.post("/login", data);
    };
    export const register = async (email: string) => {
        return api.post("/register", { email });
    };
    export const StatsBar: React.FC<Props> = ({ title }) => {
        return <div>{title}</div>;
    };
    const Carousel = React.forwardRef<HTMLDivElement>((props, ref) => {
        return <div ref={ref} />;
    });
    const { login: ignoredLogin } = useAuth();
    const [active, setActive] = useState(false);
    const count = 42;
    """

    # 1. Test CodePipeline._recover_arrow_function_name
    lines = content.splitlines()
    assert CodePipeline._recover_arrow_function_name(content, 2) == "login"
    assert CodePipeline._recover_arrow_function_name(content, 5) == "register"
    assert CodePipeline._recover_arrow_function_name(content, 8) == "StatsBar"
    assert CodePipeline._recover_arrow_function_name(content, 11) == "Carousel"
    # Ensure destructuring and primitive variables are NOT matched as functions
    assert CodePipeline._recover_arrow_function_name(content, 14) is None
    assert CodePipeline._recover_arrow_function_name(content, 15) is None
    assert CodePipeline._recover_arrow_function_name(content, 16) is None

    # 2. Test ScipIndexParser._infer_ts_zero_kind
    parser = ScipIndexParser()
    assert (
        parser._infer_ts_zero_kind(
            "scip-typescript npm def 0.0.0 src/api/auth.ts/login.", 2, lines
        )
        == 17
    )
    assert (
        parser._infer_ts_zero_kind(
            "scip-typescript npm def 0.0.0 src/api/auth.ts/register.", 5, lines
        )
        == 17
    )
    assert (
        parser._infer_ts_zero_kind(
            "scip-typescript npm def 0.0.0 src/components/StatsBar.", 8, lines
        )
        == 17
    )
    # File/package symbols ending in / must return 0
    assert (
        parser._infer_ts_zero_kind(
            "scip-typescript npm def 0.0.0 src/api/auth.ts/", 1, lines
        )
        == 0
    )
    # Non-functions must return 0
    assert (
        parser._infer_ts_zero_kind(
            "scip-typescript npm def 0.0.0 src/api/count.", 16, lines
        )
        == 0
    )
