"""Structural tests: one path, enforced.

The request path was once implemented twice — the normal flow and the
deferred-question resume each did their own cache lookup, tool call, generation
and store. Every fix then had to be made in two places, and in practice was made
in one. Three bugs came out of that in a single evening: the resume path stored
to the cache without looking in it, then reported a hit as a miss, then
generated an answer nobody saw.

Refactoring fixed it. These tests stop it coming back, by asserting the property
rather than trusting that a future edit remembers.

Pure AST inspection — no imports, no network, no server.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        failures.append(msg)


def call_sites(attr_path: tuple[str, str]) -> list[tuple[str, str, int]]:
    """Every call to `module.func` in app/, as (file, enclosing function, line)."""
    module, func = attr_path
    found = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if (isinstance(f, ast.Attribute) and f.attr == func
                    and isinstance(f.value, ast.Name) and f.value.id == module):
                # walk up to the enclosing function
                enclosing, cur = "<module>", node
                while cur in parents:
                    cur = parents[cur]
                    if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        enclosing = cur.name
                        break
                found.append((path.relative_to(ROOT).as_posix(), enclosing, node.lineno))
    return found


#: Everything expensive or stateful belongs on exactly one path, in `_answer`.
SINGLE_SITE = [
    ("weather", "fetch"),        # the tool
    ("llm", "complete"),         # the model
    ("cache", "lookup"),         # read
    ("cache", "store_answer"),   # write
    ("cache", "build_key"),      # the key both depend on
]


def test_one_call_site_each():
    for module, func in SINGLE_SITE:
        sites = call_sites((module, func))
        names = ", ".join(f"{f}:{fn}():{ln}" for f, fn, ln in sites)
        check(len(sites) == 1,
              f"{module}.{func}() has {len(sites)} call sites, expected 1 — {names}. "
              f"A second call site means a second flow, and fixes will diverge.")


def test_they_all_live_in_answer():
    """Not just once each — once each *in the same function*. Splitting lookup
    from store across two functions would satisfy the count and still allow the
    original bug."""
    for module, func in SINGLE_SITE:
        sites = call_sites((module, func))
        if len(sites) != 1:
            continue  # already reported
        _, enclosing, _ = sites[0]
        check(enclosing == "_answer",
              f"{module}.{func}() is called from {enclosing}(), expected _answer(). "
              f"The tool call, the cache and the model must stay on one path.")


def test_resume_delegates_rather_than_reimplements():
    """`_resume` should call `_answer`, not repeat it."""
    src = (ROOT / "app" / "pipeline.py").read_text()
    tree = ast.parse(src)
    resume = next((n for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and n.name == "_resume"), None)
    check(resume is not None, "_resume() not found")
    if resume is None:
        return
    calls = {n.func.id for n in ast.walk(resume)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    check("_answer" in calls,
          "_resume() does not call _answer() — it is reimplementing the path again.")


def test_intent_policy_is_data_not_conditionals():
    """Cacheability, tier and TTL come from the registry. Hard-coding an intent
    name in the pipeline to decide policy is how the registry drifts out of
    step with behaviour."""
    src = (ROOT / "app" / "pipeline.py").read_text()
    for banned in ('intent == "clothing_advice"', 'intent == "current_weather"',
                   'intent == "forecast_today"', 'intent in ("clothing_advice"'):
        check(banned not in src,
              f"pipeline.py branches on {banned!r}. Policy belongs in "
              f"app/intents.py, not in the request path.")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    for f in failures:
        print("FAIL", f)
    print(f"\n{'PASS' if not failures else 'FAILED'}: {len(tests)} structural "
          f"checks, {len(failures)} failure(s)")
    sys.exit(1 if failures else 0)
