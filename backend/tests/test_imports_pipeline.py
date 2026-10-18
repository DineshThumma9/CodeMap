import pytest

from src.models.schema import ProjectNode
from src.service.pipelines.code.tree_sitter.code_pipeline import CodePipeline
from src.service.pipelines.code.tree_sitter.complied_object import (
    CompiledObjectProcessor,
)

KNOWN_PROJECT_FILES = {
    "src/api.py",
    "src/queries.py",
    "src/models.py",
    "src/components/Header.tsx",
    "src/App.tsx",
    "src/utils/math.ts",
}


@pytest.mark.parametrize(
    "source_file,raw_import,expected_target",
    [
        # Same folder Python module import
        (
            "src/api.py",
            "from queries import REPOS_QUERY, REPO_DEP_QUERY",
            "src/queries.py",
        ),
        # Relative dot Python import
        ("src/api.py", "from .queries import REPOS_QUERY", "src/queries.py"),
        # TypeScript relative import
        (
            "src/App.tsx",
            "import Header from './components/Header'",
            "src/components/Header.tsx",
        ),
        # TypeScript parent-relative import
        (
            "src/components/Header.tsx",
            "import { math } from '../utils/math'",
            "src/utils/math.ts",
        ),
        # External library import (must be ignored)
        ("src/api.py", "import logging", None),
        ("src/App.tsx", "import React, { useState } from 'react'", None),
    ],
)
def test_resolve_import_target(
    source_file: str, raw_import: str, expected_target: str | None
):
    resolved = CodePipeline._resolve_import_target(
        source_file, raw_import, KNOWN_PROJECT_FILES
    )
    assert resolved == expected_target


def test_process_single_file_emits_imports():
    pipeline = CodePipeline.__new__(CodePipeline)
    pipeline.project_files = []
    pipeline.complied_obj = CompiledObjectProcessor()
    pipeline._known_file_paths = {"src/api.py", "src/queries.py"}

    code_content = """
from queries import REPOS_QUERY, REPO_DEP_QUERY

def run():
    pass
"""
    node = ProjectNode(
        file_path="src/api.py",
        content=code_content,
        filename="api.py",
        parent_dir="src",
        dirs=["src"],
    )

    res = pipeline._process_single_file(node)
    assert res is not None

    import_rels = [r for r in res.relations if r.rel_type == "IMPORTS"]
    assert len(import_rels) == 1
    assert import_rels[0].source_id == "src/api.py"
    assert import_rels[0].target_id == "src/queries.py"
