from typing import Iterable, Optional

from google.protobuf.internal.containers import (
    RepeatedCompositeFieldContainer,
    RepeatedScalarFieldContainer,
)
from google.protobuf.message import Message

class ToolInfo(Message):
    name: str
    version: str
    arguments: RepeatedScalarFieldContainer[str]
    def __init__(
        self,
        name: Optional[str] = ...,
        version: Optional[str] = ...,
        arguments: Optional[Iterable[str]] = ...,
    ) -> None: ...

class Metadata(Message):
    version: int
    tool_info: ToolInfo
    project_root: str
    text_document_encoding: int
    def __init__(
        self,
        version: Optional[int] = ...,
        tool_info: Optional[ToolInfo] = ...,
        project_root: Optional[str] = ...,
        text_document_encoding: Optional[int] = ...,
    ) -> None: ...

class Relationship(Message):
    symbol: str
    is_reference: bool
    is_implementation: bool
    is_type_definition: bool
    is_definition: bool
    def __init__(
        self,
        symbol: Optional[str] = ...,
        is_reference: Optional[bool] = ...,
        is_implementation: Optional[bool] = ...,
        is_type_definition: Optional[bool] = ...,
        is_definition: Optional[bool] = ...,
    ) -> None: ...

class Signature(Message):
    language: str
    text: str
    occurrences: RepeatedCompositeFieldContainer[Occurrence]
    def __init__(
        self,
        language: Optional[str] = ...,
        text: Optional[str] = ...,
        occurrences: Optional[Iterable[Occurrence]] = ...,
    ) -> None: ...

class SymbolInformation(Message):
    symbol: str
    documentation: RepeatedScalarFieldContainer[str]
    relationships: RepeatedCompositeFieldContainer[Relationship]
    kind: int
    display_name: str
    signature_documentation: Signature
    enclosing_symbol: str
    def __init__(
        self,
        symbol: Optional[str] = ...,
        documentation: Optional[Iterable[str]] = ...,
        relationships: Optional[Iterable[Relationship]] = ...,
        kind: Optional[int] = ...,
        display_name: Optional[str] = ...,
        signature_documentation: Optional[Signature] = ...,
        enclosing_symbol: Optional[str] = ...,
    ) -> None: ...

class SingleLineRange(Message):
    line: int
    start_character: int
    end_character: int
    def __init__(
        self,
        line: Optional[int] = ...,
        start_character: Optional[int] = ...,
        end_character: Optional[int] = ...,
    ) -> None: ...

class MultiLineRange(Message):
    start_line: int
    start_character: int
    end_line: int
    end_character: int
    def __init__(
        self,
        start_line: Optional[int] = ...,
        start_character: Optional[int] = ...,
        end_line: Optional[int] = ...,
        end_character: Optional[int] = ...,
    ) -> None: ...

class Diagnostic(Message):
    severity: int
    code: str
    message: str
    source: str
    tags: RepeatedScalarFieldContainer[int]
    def __init__(
        self,
        severity: Optional[int] = ...,
        code: Optional[str] = ...,
        message: Optional[str] = ...,
        source: Optional[str] = ...,
        tags: Optional[Iterable[int]] = ...,
    ) -> None: ...

class Occurrence(Message):
    range: RepeatedScalarFieldContainer[int]
    symbol: str
    symbol_roles: int
    override_documentation: RepeatedScalarFieldContainer[str]
    syntax_kind: int
    diagnostics: RepeatedCompositeFieldContainer[Diagnostic]
    enclosing_range: RepeatedScalarFieldContainer[int]
    single_line_range: SingleLineRange
    multi_line_range: MultiLineRange
    def __init__(
        self,
        range: Optional[Iterable[int]] = ...,
        symbol: Optional[str] = ...,
        symbol_roles: Optional[int] = ...,
        override_documentation: Optional[Iterable[str]] = ...,
        syntax_kind: Optional[int] = ...,
        diagnostics: Optional[Iterable[Diagnostic]] = ...,
        enclosing_range: Optional[Iterable[int]] = ...,
        single_line_range: Optional[SingleLineRange] = ...,
        multi_line_range: Optional[MultiLineRange] = ...,
    ) -> None: ...

class Document(Message):
    language: str
    relative_path: str
    occurrences: RepeatedCompositeFieldContainer[Occurrence]
    symbols: RepeatedCompositeFieldContainer[SymbolInformation]
    text: str
    position_encoding: int
    def __init__(
        self,
        language: Optional[str] = ...,
        relative_path: Optional[str] = ...,
        occurrences: Optional[Iterable[Occurrence]] = ...,
        symbols: Optional[Iterable[SymbolInformation]] = ...,
        text: Optional[str] = ...,
        position_encoding: Optional[int] = ...,
    ) -> None: ...

class Index(Message):
    metadata: Metadata
    documents: RepeatedCompositeFieldContainer[Document]
    external_symbols: RepeatedCompositeFieldContainer[SymbolInformation]
    def __init__(
        self,
        metadata: Optional[Metadata] = ...,
        documents: Optional[Iterable[Document]] = ...,
        external_symbols: Optional[Iterable[SymbolInformation]] = ...,
    ) -> None: ...
    def ParseFromString(self, serialized: bytes) -> int: ...
    def SerializeToString(self, *, deterministic: bool = ...) -> bytes: ...
