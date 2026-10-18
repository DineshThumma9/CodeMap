import pytest
from pydantic import ValidationError

from src.models.code_schema import ExportInfo
from src.models.schema import GitRequest
from src.service.pipelines.code.tree_sitter.code_pipeline import CodePipeline
from src.service.pipelines.code.tree_sitter.code_utils import CodeUtils


@pytest.mark.parametrize(
    "file_path,expected_skipped",
    [
        ("node_modules/lodash/index.js", True),
        (".git/objects/abc", True),
        ("dist/assets/app.js", True),
        (".venv/lib/python3.12/site-packages/pkg.py", True),
        ("coverage/lcov.info", True),
        ("public/logo.png", True),
        ("assets/banner.jpg", True),
        ("data/backup.sqlite3", True),
        ("archive.zip", True),
        ("package-lock.json", True),
        # Valid source files must NOT be skipped
        ("src/main.py", False),
        ("src/components/Header.tsx", False),
        ("backend/src/service/agent.py", False),
    ],
)
def test_code_utils_is_skipped(file_path: str, expected_skipped: bool):
    req = GitRequest(owner="test", repo="test")
    utils = CodeUtils(req)
    assert utils._is_skipped(file_path) == expected_skipped


def test_code_utils_is_processable_filters():
    # Include filter: only .ts and .tsx allowed
    req_inc = GitRequest(
        owner="test", repo="test", file_extension_include=[".ts", ".tsx"]
    )
    utils_inc = CodeUtils(req_inc)
    assert utils_inc._is_processable("src/App.tsx") is True
    assert utils_inc._is_processable("src/index.ts") is True
    assert utils_inc._is_processable("src/server.py") is False

    # Exclude filter: block spec files
    req_exc = GitRequest(
        owner="test", repo="test", file_extension_exclude=[".spec.ts", ".test.ts"]
    )
    utils_exc = CodeUtils(req_exc)
    assert utils_exc._is_processable("src/calculator.ts") is True
    assert utils_exc._is_processable("src/calculator.spec.ts") is False

    # Directory filter: block docs and legacy
    req_dir = GitRequest(owner="test", repo="test", dir_exclude=["legacy", "docs"])
    utils_dir = CodeUtils(req_dir)
    assert utils_dir._is_processable("src/service.py") is True
    assert utils_dir._is_processable("legacy/old_service.py") is False
    assert utils_dir._is_processable("docs/guide.md") is False


def test_code_utils_build_tree_hierarchy():
    """Verify transforming a flat git tree list into a nested directory structure."""
    flat_tree = [
        {"type": "tree", "path": "src"},
        {"type": "tree", "path": "src/components"},
        {
            "type": "blob",
            "path": "src/components/Button.tsx",
            "size": 512,
            "sha": "a1b2",
        },
        {"type": "blob", "path": "src/main.ts", "size": 1024, "sha": "c3d4"},
        {"type": "blob", "path": "README.md", "size": 256, "sha": "e5f6"},
    ]

    utils = CodeUtils(GitRequest(owner="test", repo="test"))
    nested = utils.build_tree(flat_tree)

    # Top-level should have 'src' (dir) and 'README.md' (file)
    top_names = {node["name"] for node in nested}
    assert top_names == {"src", "README.md"}

    src_node = next(n for n in nested if n["name"] == "src")
    assert src_node["type"] == "tree"
    assert src_node["children"] is not None

    src_child_names = {c["name"] for c in src_node["children"]}
    assert src_child_names == {"components", "main.ts"}

    comp_node = next(c for c in src_node["children"] if c["name"] == "components")
    assert comp_node["children"] is not None
    assert comp_node["children"][0]["name"] == "Button.tsx"
    assert comp_node["children"][0]["size"] == 512


@pytest.mark.parametrize(
    "snippet,start_line,expected_name",
    [
        (
            "export const UserAvatar = ({ user }) => {\n  return <img src={user.avatar} />;\n};",
            1,
            "UserAvatar",
        ),
        (
            "import { create } from 'zustand';\nexport const useSessionStore = create<SessionState>((set) => ({\n  token: null,\n}));",
            2,
            "useSessionStore",
        ),
        (
            "import React, { memo } from 'react';\nconst NavigationBar = memo(({ links }) => <nav>{links.length}</nav>);",
            2,
            "NavigationBar",
        ),
        (
            "export const CustomInput = forwardRef<HTMLInputElement, Props>((props, ref) => <input ref={ref} />);",
            1,
            "CustomInput",
        ),
        (
            "export const MAX_RETRY_COUNT = 5;\nexport const DEFAULT_TIMEOUT_MS = 30000;",
            1,
            None,
        ),
    ],
)
def test_recover_arrow_function_names(
    snippet: str, start_line: int, expected_name: str | None
):
    assert (
        CodePipeline._recover_arrow_function_name(snippet, start_line=start_line)
        == expected_name
    )


@pytest.mark.parametrize(
    "raw_kind,expected_kind",
    [
        ("reexport", "re_export"),
        ("NAMED", "named"),
        ("Default", "default"),
        ("re_export", "re_export"),
    ],
)
def test_export_info_kind_normalization(raw_kind: str, expected_kind: str):
    info = ExportInfo(name="MyComponent", kind=raw_kind)
    assert info.kind == expected_kind


def test_export_info_rejects_invalid_kind():
    with pytest.raises(ValidationError):
        ExportInfo(name="InvalidExport", kind="invalid_type")  # type: ignore[arg-type]
