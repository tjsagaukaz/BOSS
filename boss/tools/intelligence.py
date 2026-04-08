"""Code intelligence tools — let agents search and understand code structure."""

from __future__ import annotations

from boss.execution import ExecutionType, display_value, governed_function_tool, scope_value


@governed_function_tool(
    execution_type=ExecutionType.SEARCH,
    title="Find Symbol",
    describe_call=lambda params: f'Find symbol "{params.get("name", "")}"',
    scope_key=lambda _params: scope_value("intelligence", "symbol"),
    scope_label=lambda _params: "Code symbol lookup",
)
def find_symbol(name: str, kind: str = "", project: str = "") -> str:
    """Find a code symbol (function, class, variable, etc.) by name.

    Args:
        name: Symbol name to search for (prefix match, case-insensitive).
        kind: Optional filter: class, function, method, variable, constant, interface, etc.
        project: Optional project path to scope the search.
    """
    from boss.intelligence.index import get_code_index

    idx = get_code_index()
    results = idx.find_symbol(
        name,
        kind=kind or None,
        project_path=project or None,
        limit=15,
    )
    if not results:
        return f"No symbols found matching '{name}'."

    lines = [f"Found {len(results)} symbol(s) matching '{name}':"]
    for r in results:
        loc = f"{r.file_path}:{r.line}"
        sig = f" — {r.signature}" if r.signature else ""
        doc = f"\n  {r.docstring[:120]}" if r.docstring else ""
        lines.append(f"- [{r.kind}] {r.qualified_name} ({loc}){sig}{doc}")
    return "\n".join(lines)


@governed_function_tool(
    execution_type=ExecutionType.SEARCH,
    title="Find Definition",
    describe_call=lambda params: f'Find definition of "{params.get("name", "")}"',
    scope_key=lambda _params: scope_value("intelligence", "definition"),
    scope_label=lambda _params: "Code definition lookup",
)
def find_definition(name: str, project: str = "") -> str:
    """Find where a symbol is defined (exact name match).

    Args:
        name: Exact symbol name to look up.
        project: Optional project path to scope the search.
    """
    from boss.intelligence.index import get_code_index

    idx = get_code_index()
    results = idx.find_definition(name, project_path=project or None, limit=10)
    if not results:
        return f"No definition found for '{name}'."

    lines = [f"Found {len(results)} definition(s) for '{name}':"]
    for r in results:
        loc = f"{r.file_path}:{r.line}"
        sig = f" — {r.signature}" if r.signature else ""
        doc = f"\n  {r.docstring[:150]}" if r.docstring else ""
        exported = " (exported)" if r.exported else " (private)"
        lines.append(f"- [{r.kind}] {r.qualified_name} ({loc}){exported}{sig}{doc}")
    return "\n".join(lines)


@governed_function_tool(
    execution_type=ExecutionType.SEARCH,
    title="Search Code (Symbolic)",
    describe_call=lambda params: f'Search code symbols for "{params.get("query", "")}"',
    scope_key=lambda _params: scope_value("intelligence", "search-symbolic"),
    scope_label=lambda _params: "Symbolic code search",
)
def search_code_symbolic(query: str, project: str = "", kinds: str = "") -> str:
    """Search code by keywords across symbol names, signatures, and docstrings.

    Args:
        query: Keywords to search for in code symbols.
        project: Optional project path to scope the search.
        kinds: Comma-separated symbol kinds to filter (e.g. 'class,function').
    """
    from boss.intelligence.index import get_code_index

    kind_set = {k.strip() for k in kinds.split(",") if k.strip()} if kinds else None
    idx = get_code_index()
    results = idx.search_symbols(
        query,
        project_path=project or None,
        kinds=kind_set,
        limit=15,
    )
    if not results:
        return f"No symbols found matching '{query}'."

    lines = [f"Found {len(results)} symbol(s) matching '{query}':"]
    for r in results:
        loc = f"{r.file_path}:{r.line}"
        sig = f" — {r.signature}" if r.signature else ""
        lines.append(f"- [{r.kind}] {r.qualified_name} ({loc}){sig}")
    return "\n".join(lines)


@governed_function_tool(
    execution_type=ExecutionType.SEARCH,
    title="Search Code (Semantic)",
    describe_call=lambda params: f'Hybrid search for "{params.get("query", "")}"',
    scope_key=lambda _params: scope_value("intelligence", "search-semantic"),
    scope_label=lambda _params: "Semantic code search",
)
def search_code_semantic(query: str, project: str = "") -> str:
    """Search code using hybrid retrieval: symbol lookup, keyword search,
    memory/file chunks, and optional semantic embeddings.

    Args:
        query: Natural language or keyword query about code.
        project: Optional project path to scope the search.
    """
    from boss.intelligence.retrieval import hybrid_search

    results, caps = hybrid_search(
        query,
        project_path=project or None,
        limit=12,
    )
    if not results:
        return f"No results found for '{query}'."

    backend_info = []
    if caps.symbol_search:
        backend_info.append("symbols")
    if caps.keyword_search:
        backend_info.append("keywords")
    if caps.memory_search:
        backend_info.append("memory")
    if caps.semantic_search:
        backend_info.append("embeddings")

    lines = [f"Found {len(results)} result(s) via [{', '.join(backend_info)}]:"]
    for r in results:
        loc = f"{r.file_path}:{r.line}" if r.file_path else "(memory)"
        kind_label = r.kind.value
        meta_parts = []
        if r.metadata.get("kind"):
            meta_parts.append(r.metadata["kind"])
        if r.metadata.get("qualified_name"):
            meta_parts.append(r.metadata["qualified_name"])
        meta_str = f" [{', '.join(meta_parts)}]" if meta_parts else ""
        lines.append(f"- ({kind_label}, {r.score:.2f}) {loc}{meta_str}")
        if r.content:
            lines.append(f"  {r.content[:200]}")
    return "\n".join(lines)


@governed_function_tool(
    execution_type=ExecutionType.READ,
    title="Project Code Graph",
    describe_call=lambda params: f'Code graph for {params.get("project", "project")}',
    scope_key=lambda _params: scope_value("intelligence", "graph"),
    scope_label=lambda _params: "Project code graph",
)
def project_graph(project: str) -> str:
    """Get a structural overview of a project's code: languages, symbol counts,
    entry points, test files, top symbols, and external dependencies.

    Args:
        project: Project path to analyze.
    """
    from boss.intelligence.index import get_code_index

    idx = get_code_index()
    graph = idx.project_graph(project)

    if not graph["files_indexed"]:
        return f"No files indexed for project '{project}'. Run a scan first."

    lines = [
        f"Project: {graph['project_path']}",
        f"Files indexed: {graph['files_indexed']} ({graph['total_lines']:,} lines)",
    ]

    if graph["languages"]:
        lang_parts = [f"{lang}: {count}" for lang, count in sorted(graph["languages"].items(), key=lambda x: -x[1])]
        lines.append(f"Languages: {', '.join(lang_parts)}")

    if graph["symbol_counts"]:
        sym_parts = [f"{kind}: {count}" for kind, count in sorted(graph["symbol_counts"].items(), key=lambda x: -x[1])]
        lines.append(f"Symbols: {', '.join(sym_parts)}")

    if graph["entry_points"]:
        lines.append(f"Entry points: {', '.join(graph['entry_points'][:8])}")
    if graph["test_files"]:
        lines.append(f"Test files: {', '.join(graph['test_files'][:8])}")

    if graph["top_symbols"]:
        lines.append("Top exported symbols:")
        for sym in graph["top_symbols"][:20]:
            sig = f" — {sym['signature']}" if sym.get("signature") else ""
            lines.append(f"  [{sym['kind']}] {sym['name']} ({sym['file_path']}){sig}")

    if graph["external_dependencies"]:
        dep_parts = [f"{d['module']} ({d['import_count']})" for d in graph["external_dependencies"][:15]]
        lines.append(f"External deps: {', '.join(dep_parts)}")

    return "\n".join(lines)


@governed_function_tool(
    execution_type=ExecutionType.SEARCH,
    title="Find Importers",
    describe_call=lambda params: f'Find importers of "{params.get("module_or_symbol", "")}"',
    scope_key=lambda _params: scope_value("intelligence", "importers"),
    scope_label=lambda _params: "Import graph lookup",
)
def find_importers(module_or_symbol: str, project: str = "") -> str:
    """Find files that import a given module or symbol.

    Args:
        module_or_symbol: Module name or symbol name to search for in imports.
        project: Optional project path to scope the search.
    """
    from boss.intelligence.index import get_code_index

    idx = get_code_index()
    results = idx.find_importers(
        module_or_symbol, project_path=project or None, limit=20,
    )
    if not results:
        return f"No files found importing '{module_or_symbol}'."

    lines = [f"Found {len(results)} import(s) of '{module_or_symbol}':"]
    for r in results:
        names_str = f" ({', '.join(r.names)})" if r.names else ""
        lines.append(f"- {r.file_path}:{r.line} — import {r.module}{names_str}")
    return "\n".join(lines)
