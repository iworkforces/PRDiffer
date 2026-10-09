#!/usr/bin/env python3
"""Import-safe, stdlib AST engine for the package's Clean Architecture gate."""

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ImportViolation:
    """A forbidden dependency at its source import statement."""

    path: str
    lineno: int
    module: str
    target: str
    rule: str


@dataclass(frozen=True, slots=True)
class ParseFailure:
    """Source that cannot be read, parsed, or resolved safely."""

    path: str
    error: str
    lineno: int = 1
    rule: str = "parse-failure"


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Internal dependency graph and all failures from a package scan."""

    dependencies: dict[str, set[str]]
    violations: list[ImportViolation]
    parse_failures: list[ParseFailure]

    @property
    def is_clean(self) -> bool:
        return not self.violations and not self.parse_failures


def _in_namespace(module: str, namespace: str) -> bool:
    return module == namespace or module.startswith(namespace + ".")


def _import_targets(node: ast.Import | ast.ImportFrom, anchor: str) -> list[str]:
    """Resolve all syntactic dependency targets without importing any code."""
    match node:
        case ast.Import():
            return [alias.name for alias in node.names]
        case ast.ImportFrom():
            base = node.module or ""
            if node.level:
                parts = anchor.split(".")
                if node.level > len(parts):
                    raise ValueError("relative import climbs above the top package")
                base = ".".join(parts[: len(parts) - node.level + 1])
                if node.module:
                    base += "." + node.module
            return [base] + [f"{base}.{alias.name}" for alias in node.names if alias.name != "*"]


def scan_package(package_dir: Path) -> ScanResult:
    """Scan every Python AST in a top package; unreadable source fails closed.

    Paths are relative to the package's parent. Dependencies include internal
    base and alias targets, even for imports in nested or unreachable code.
    The sole layer exemption is application/factory.py, the composition root.
    """
    package_dir = package_dir.resolve()
    if not package_dir.is_dir():
        return ScanResult({}, [], [ParseFailure(str(package_dir), "package directory not found")])
    package = package_dir.name
    dependencies: dict[str, set[str]] = {}
    violations: list[ImportViolation] = []
    failures: list[ParseFailure] = []
    rules = (("domain", "application"), ("domain", "infrastructure"), ("application", "infrastructure"))

    for source_path in sorted(package_dir.rglob("*.py")):
        relative = source_path.relative_to(package_dir.parent)
        if "__pycache__" in relative.parts:
            continue
        path = relative.as_posix()
        module_parts = relative.with_suffix("").parts
        is_package = source_path.name == "__init__.py"
        module = ".".join(module_parts[:-1] if is_package else module_parts)
        anchor = module if is_package else module.rpartition(".")[0]
        dependencies[module] = set()
        try:
            tree = ast.parse(source_path.read_bytes(), filename=path)
        except SyntaxError as error:
            failures.append(ParseFailure(path, error.msg, error.lineno or 1))
            continue
        except (UnicodeDecodeError, ValueError, OSError) as error:
            failures.append(ParseFailure(path, str(error)))
            continue

        imports = (node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)))
        for node in sorted(imports, key=lambda item: (item.lineno, item.col_offset)):
            try:
                targets = _import_targets(node, anchor)
            except ValueError as error:
                failures.append(ParseFailure(path, str(error), node.lineno, "relative-import"))
                continue
            for target in targets:
                if not _in_namespace(target, package):
                    continue
                dependencies[module].add(target)
                for source_layer, target_layer in rules:
                    if not (_in_namespace(module, f"{package}.{source_layer}") and _in_namespace(target, f"{package}.{target_layer}")):
                        continue
                    if source_layer == "application" and path == f"{package}/application/factory.py":
                        continue
                    rule = f"{source_layer}->{target_layer}"
                    violations.append(ImportViolation(path, node.lineno, module, target, rule))
    return ScanResult(dependencies, violations, failures)


def format_failures(result: ScanResult) -> list[str]:
    """Shared readable diagnostics for CLI output and pytest assertions."""
    return [f"{v.path}:{v.lineno}: {v.rule}: {v.module} imports {v.target}" for v in result.violations] + [
        f"{f.path}:{f.lineno}: {f.rule}: {f.error}" for f in result.parse_failures
    ]


def main() -> None:
    """Print the dependency graph, failures and statistics; exit nonzero on failure."""
    parser = argparse.ArgumentParser(description="Analyze package dependencies and enforce Clean Architecture")
    parser.add_argument("--path", type=Path, default=Path("prdiffer"), help="Top package directory (default: prdiffer)")
    args = parser.parse_args()
    root = args.path
    if not root.is_dir():
        print(f"Error: Directory {root} not found", file=sys.stderr)
        sys.exit(1)

    print(f"Analyzing dependencies in {root}...")
    result = scan_package(root)
    package = root.resolve().name
    print("\n" + "=" * 80 + "\nDEPENDENCY GRAPH\n" + "=" * 80)
    layer_counts: dict[str, int] = {}
    for layer in ("domain", "application", "infrastructure"):
        modules = [module for module in result.dependencies if _in_namespace(module, f"{package}.{layer}")]
        layer_counts[layer] = len(modules)
        print(f"\n{layer.upper()} LAYER:\n" + "-" * 80)
        for module in modules:
            deps = sorted(result.dependencies[module])
            print(f"  {module.removeprefix(package + '.')}")
            print("    → " + (", ".join(dep.removeprefix(package + ".") for dep in deps) or "(no internal deps)"))

    print("\n" + "=" * 80 + "\nLAYER VIOLATIONS / PARSE FAILURES\n" + "=" * 80)
    messages = format_failures(result)
    for message in messages:
        print(message)
    print("\n" + "=" * 80 + "\nARCHITECTURE STATISTICS\n" + "=" * 80)
    print(f"Total Modules:          {len(result.dependencies)}")
    print(f"Total Dependencies:     {sum(len(deps) for deps in result.dependencies.values())}")
    print(f"Layer Violations:       {len(result.violations)}")
    print(f"Parse Failures:         {len(result.parse_failures)}")
    print("\nModules per Layer:")
    for layer, count in layer_counts.items():
        print(f"  {layer.title():20} {count:3}")
    print("\nTop 5 Most Connected Modules:")
    for module, deps in sorted(result.dependencies.items(), key=lambda item: len(item[1]), reverse=True)[:5]:
        print(f"  {module.removeprefix(package + '.'):40} {len(deps):2} deps")
    print("\n✅ Architecture is clean!" if result.is_clean else f"\n❌ Found {len(messages)} architecture failure(s). Fix before proceeding.")
    sys.exit(0 if result.is_clean else 1)


if __name__ == "__main__":
    main()
