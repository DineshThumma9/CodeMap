from src.service.pipelines.code.languages.typescript import TS_QUERIES

TSX_QUERIES = {
    **TS_QUERIES,
    "calls": TS_QUERIES["calls"]
    + """
        (jsx_element open_tag: (jsx_opening_element name: (identifier) @name))
        (jsx_self_closing_element name: (identifier) @name)
    """,
    "components": """
        (class_declaration name: (type_identifier) @name)
        (variable_declarator name: (identifier) @name value: (arrow_function) @fn)
        (variable_declarator name: (identifier) @name value: (function_expression) @fn)
        (function_declaration name: (identifier) @name)
    """,
}
