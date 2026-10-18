from src.service.agent.tools.code import (
    _extract_symbol_from_code,
    _extract_symbol_via_ast,
    _extract_symbol_via_lexical,
)


def test_extract_python_function():
    code = '''import os

def helper_one():
    return 1

def target_service(user_id: str, count: int) -> dict:
    """Process user transaction data."""
    result = {"status": "pending"}
    if count > 0:
        result["multiplier"] = count * 2
    return result

def helper_two():
    return 2
'''
    extracted = _extract_symbol_from_code(code, "target_service", "src/services.py")
    assert extracted is not None
    assert "def target_service(" in extracted
    assert 'result["multiplier"] = count * 2' in extracted
    assert "helper_one" not in extracted
    assert "helper_two" not in extracted


def test_extract_python_class():
    code = """import abc

class BaseEngine(abc.ABC):
    @abc.abstractmethod
    def run(self):
        pass

class QueryOrchestrator:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def execute(self, query: str):
        return f"Executing {query}"

def standalone_func():
    return "standalone"
"""
    extracted = _extract_symbol_from_code(code, "QueryOrchestrator", "src/engine.py")
    assert extracted is not None
    assert "class QueryOrchestrator:" in extracted
    assert "def execute(self, query: str):" in extracted
    assert "BaseEngine" not in extracted
    assert "standalone_func" not in extracted


def test_extract_typescript_braces_function():
    code = """import React from 'react';

export function calculateSummary(items: Array<{ value: number }>): number {
    const total = items.reduce((acc, curr) => {
        return acc + curr.value;
    }, 0);
    return total;
}

export function unusedOther(): void {
    console.log("other");
}
"""
    extracted = _extract_symbol_from_code(code, "calculateSummary", "src/utils.ts")
    assert extracted is not None
    assert "function calculateSummary" in extracted
    assert "items.reduce" in extracted
    assert "unusedOther" not in extracted


def test_extract_typescript_arrow_component():
    code = """import React, { useState } from 'react';

export const MetricBadge: React.FC<{ label: string; score: number }> = ({ label, score }) => {
    const [active, setActive] = useState(false);
    return (
        <div className={`badge ${active ? "active" : ""}`} onClick={() => setActive(!active)}>
            <span>{label}</span>
            <span>{score.toFixed(2)}</span>
        </div>
    );
};

export const OtherBadge = () => <span />;
"""
    extracted = _extract_symbol_from_code(
        code, "MetricBadge", "src/components/Badge.tsx"
    )
    assert extracted is not None
    assert "MetricBadge" in extracted
    assert "score.toFixed(2)" in extracted
    assert "OtherBadge" not in extracted


def test_extract_non_existent_symbol():
    code = """def existing_func():
    return True
"""
    assert _extract_symbol_from_code(code, "missing_func", "test.py") is None


def test_lexical_fallback_direct():
    code = """def compute_sum(a, b):
    # compute sum of two numbers
    result = a + b
    return result

def next_item():
    pass
"""
    extracted = _extract_symbol_via_lexical(code, "compute_sum")
    assert extracted is not None
    assert "compute_sum" in extracted
    assert "return result" in extracted
    assert "next_item" not in extracted
