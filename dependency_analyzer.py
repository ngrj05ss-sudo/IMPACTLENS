
import ast
import csv
import json
import argparse
from pathlib import Path
from collections import defaultdict


class DependencyIntelligenceEngine:

    def __init__(self, repository_path):
        self.root = Path(repository_path).resolve()

        self.files = []
        self.trees = {}
        self.module_files = {}

        self.definitions = []
        self.imports = []
        self.calls = []
        self.edges = []

        self.symbol_index = defaultdict(list)
        self.import_context = {}

        self.ignored_dirs = {
            ".git", ".venv", "venv", "__pycache__",
            "site-packages", "node_modules", "build",
            "dist", "impactlens_reports"
        }

    def discover_files(self):
        """Discover Python files in the repository."""

        self.files = sorted(
            path for path in self.root.rglob("*.py")
            if not any(part in self.ignored_dirs for part in path.parts)
        )

        print(f"[INFO] Discovered {len(self.files)} Python files.")

    def module_name(self, path):
        relative = path.relative_to(self.root)
        parts = list(relative.with_suffix("").parts)

        if parts[-1] == "__init__":
            parts.pop()

        return ".".join(parts) if parts else "__root__"

    def parse_files(self):
        """Parse Python files without executing them."""

        for path in self.files:
            try:
                source = path.read_text(encoding="utf-8-sig")
                tree = ast.parse(source, filename=str(path))

                self.trees[path] = tree
                self.module_files[self.module_name(path)] = path

            except (SyntaxError, UnicodeDecodeError, OSError) as error:
                print(f"[WARNING] Skipping {path}: {error}")

        print(f"[INFO] Successfully parsed {len(self.trees)} files.")

    def resolve_module(self, name):
        """Find a module inside the analyzed repository."""

        if not name:
            return None

        if name in self.module_files:
            return self.module_files[name]

        package_path = self.root.joinpath(
            *name.split("."), "__init__.py"
        )

        if package_path.is_file():
            return package_path

        return None

    def relative_import_base(self, node, current_module, path):
        """Resolve the base name of a relative import."""

        parts = current_module.split(".")

        if path.name != "__init__.py":
            parts = parts[:-1]

        remove_count = node.level - 1

        if remove_count >= len(parts):
            return ""

        if remove_count:
            parts = parts[:-remove_count]

        if node.module:
            parts.extend(node.module.split("."))

        return ".".join(parts)

    def collect_definitions(self):
        """Find function and class definitions."""

        for path, tree in self.trees.items():
            module = self.module_name(path)

            for node in ast.walk(tree):

                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    kind = "function"
                elif isinstance(node, ast.ClassDef):
                    kind = "class"
                else:
                    continue

                item = {
                    "file": path,
                    "module": module,
                    "name": node.name,
                    "kind": kind,
                    "line": node.lineno
                }

                self.definitions.append(item)

                # Index top-level symbols for conservative call resolution.
                if node in tree.body:
                    self.symbol_index[(module, node.name)].append(item)

        print(f"[INFO] Found {len(self.definitions)} definitions.")

    def collect_imports(self):
        """Find imports and map imported names to local aliases."""

        for path, tree in self.trees.items():

            current_module = self.module_name(path)
            module_aliases = {}
            symbol_aliases = {}

            for node in ast.walk(tree):

                if isinstance(node, ast.Import):

                    for alias in node.names:
                        target_module = alias.name
                        target_file = self.resolve_module(target_module)

                        if target_file is None:
                            continue

                        self.imports.append({
                            "source": path,
                            "target": target_file,
                            "type": "module_import",
                            "details": target_module,
                            "line": node.lineno
                        })

                        local_name = alias.asname or target_module.split(".")[0]

                        module_aliases[local_name] = (
                            target_module if alias.asname
                            else target_module.split(".")[0]
                        )

                elif isinstance(node, ast.ImportFrom):

                    base = self.relative_import_base(
                        node, current_module, path
                    )

                    base_file = self.resolve_module(base)

                    if base_file:
                        self.imports.append({
                            "source": path,
                            "target": base_file,
                            "type": "from_import",
                            "details": base,
                            "line": node.lineno
                        })

                    for alias in node.names:
                        if alias.name == "*":
                            continue

                        local_name = alias.asname or alias.name
                        submodule = (
                            f"{base}.{alias.name}" if base else alias.name
                        )
                        submodule_file = self.resolve_module(submodule)

                        if submodule_file:
                            self.imports.append({
                                "source": path,
                                "target": submodule_file,
                                "type": "submodule_import",
                                "details": submodule,
                                "line": node.lineno
                            })
                            module_aliases[local_name] = submodule

                        elif base_file:
                            symbol_aliases[local_name] = (
                                base, alias.name
                            )

            self.import_context[path] = (
                module_aliases, symbol_aliases
            )

        print(f"[INFO] Found {len(self.imports)} local import relationships.")

    def resolve_call(self, node, module_aliases, symbol_aliases):
        """Resolve calls only when there is sufficient evidence."""

        if isinstance(node, ast.Name):

            name = node.id

            if name in symbol_aliases:
                module, symbol = symbol_aliases[name]
                matches = self.symbol_index.get((module, symbol), [])

                if len(matches) == 1:
                    return matches[0], "imported_symbol"

            matches = [
                definition
                for (module, symbol), definitions in self.symbol_index.items()
                if symbol == name
                for definition in definitions
            ]

            if len(matches) == 1:
                return matches[0], "unique_symbol_match"

        elif isinstance(node, ast.Attribute):

            if isinstance(node.value, ast.Name):
                alias = node.value.id

                if alias in module_aliases:
                    module = module_aliases[alias]
                    matches = self.symbol_index.get(
                        (module, node.attr), []
                    )

                    if len(matches) == 1:
                        return matches[0], "module_qualified_call"

        return None, "unresolved"

    def collect_calls(self):
        """Extract calls and resolve their possible definitions."""

        for path, tree in self.trees.items():

            module_aliases, symbol_aliases = self.import_context.get(
                path, ({}, {})
            )

            engine = self

            class CallVisitor(ast.NodeVisitor):

                def __init__(self):
                    self.scope = "<module>"

                def visit_FunctionDef(self, node):
                    previous = self.scope
                    self.scope = node.name
                    self.generic_visit(node)
                    self.scope = previous

                visit_AsyncFunctionDef = visit_FunctionDef

                def visit_ClassDef(self, node):
                    previous = self.scope
                    self.scope = node.name
                    self.generic_visit(node)
                    self.scope = previous

                def visit_Call(self, node):

                    target, resolution = engine.resolve_call(
                        node.func,
                        module_aliases,
                        symbol_aliases
                    )

                    call_name = ast.unparse(node.func)

                    record = {
                        "source": path,
                        "caller_scope": self.scope,
                        "call_name": call_name,
                        "target_file": target["file"] if target else None,
                        "target_symbol": target["name"] if target else None,
                        "line": node.lineno,
                        "resolution": resolution
                    }

                    engine.calls.append(record)

                    if target and target["file"] != path:
                        engine.edges.append({
                            "source": path,
                            "target": target["file"],
                            "type": "function_call",
                            "details": call_name,
                            "line": node.lineno
                        })

                    self.generic_visit(node)

            CallVisitor().visit(tree)

        print(f"[INFO] Found {len(self.calls)} function calls.")

    def build_graph(self):
        """Combine import and function-call edges."""

        self.edges.extend(self.imports)

        unique = {}

        for edge in self.edges:
            key = (
                edge["source"],
                edge["target"],
                edge["type"],
                edge["details"],
                edge["line"]
            )
            unique[key] = edge

        self.edges = list(unique.values())

    def export_csv(self, path, rows, headers):
        with open(
            path, "w", newline="", encoding="utf-8-sig"
        ) as file:
            writer = csv.DictWriter(file, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)

    def generate_reports(self):
        """Generate CSV and JSON reports."""

        output = self.root / "impactlens_reports"
        output.mkdir(exist_ok=True)

        def relative(path):
            return str(path.relative_to(self.root))

        dependency_rows = [
            {
                "source_file": relative(edge["source"]),
                "target_file": relative(edge["target"]),
                "dependency_type": edge["type"],
                "details": edge["details"],
                "line": edge["line"]
            }
            for edge in self.edges
        ]

        definition_rows = [
            {
                "file": relative(item["file"]),
                "module": item["module"],
                "name": item["name"],
                "kind": item["kind"],
                "line": item["line"]
            }
            for item in self.definitions
        ]

        call_rows = [
            {
                "caller_file": relative(item["source"]),
                "caller_scope": item["caller_scope"],
                "call_name": item["call_name"],
                "resolved_file": (
                    relative(item["target_file"])
                    if item["target_file"] else ""
                ),
                "resolved_symbol": item["target_symbol"] or "",
                "line": item["line"],
                "resolution": item["resolution"]
            }
            for item in self.calls
        ]

        self.export_csv(
            output / "structural_dependencies.csv",
            dependency_rows,
            [
                "source_file", "target_file",
                "dependency_type", "details", "line"
            ]
        )

        self.export_csv(
            output / "symbol_definitions.csv",
            definition_rows,
            ["file", "module", "name", "kind", "line"]
        )

        self.export_csv(
            output / "function_calls.csv",
            call_rows,
            [
                "caller_file", "caller_scope", "call_name",
                "resolved_file", "resolved_symbol",
                "line", "resolution"
            ]
        )

        summary = {
            "repository": str(self.root),
            "python_files_parsed": len(self.trees),
            "definitions_found": len(self.definitions),
            "function_calls_found": len(self.calls),
            "resolved_function_calls": sum(
                call["target_file"] is not None
                for call in self.calls
            ),
            "structural_relationships": len(self.edges),
            "relationship_types": {
                kind: sum(edge["type"] == kind for edge in self.edges)
                for kind in sorted({edge["type"] for edge in self.edges})
            }
        }

        with open(
            output / "dependency_summary.json",
            "w",
            encoding="utf-8"
        ) as file:
            json.dump(summary, file, indent=4)

        print("\n[SUCCESS] Dependency analysis completed!")
        print(f"[REPORTS] {output}")
        print(json.dumps(summary, indent=4))

    def run(self):

        print("\n====================================")
        print("          IMPACTLENS")
        print("  DEPENDENCY INTELLIGENCE ENGINE")
        print("====================================\n")

        if not self.root.is_dir():
            raise NotADirectoryError(
                f"Repository not found: {self.root}"
            )

        self.discover_files()
        self.parse_files()
        self.collect_definitions()
        self.collect_imports()
        self.collect_calls()
        self.build_graph()
        self.generate_reports()


def main():

    parser = argparse.ArgumentParser(
        description="IMPACTLENS Dependency Analyzer"
    )

    parser.add_argument(
        "--root",
        default=".",
        help="Path to Python repository"
    )

    args = parser.parse_args()

    engine = DependencyIntelligenceEngine(args.root)
    engine.run()


if __name__ == "__main__":
    main()
