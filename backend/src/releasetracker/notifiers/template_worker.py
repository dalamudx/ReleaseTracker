"""Isolated JSON-only Jinja renderer. Invoked as a script, never in the API process."""

import json
import sys

try:
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
except ImportError:
    pass  # Parent still enforces a wall deadline and kills the child.

from jinja2 import StrictUndefined, nodes
from jinja2.runtime import Macro
from jinja2.sandbox import ImmutableSandboxedEnvironment


class Environment(ImmutableSandboxedEnvironment):
    intercepted_binops = frozenset({"*", "**"})

    def is_safe_attribute(self, obj, attr, value):
        return False

    def is_safe_callable(self, obj):
        return isinstance(obj, Macro)

    def call_binop(self, context, operator, left, right):
        raise ValueError("operator_not_allowed")


def render(request):
    env = Environment(
        undefined=StrictUndefined,
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        loader=None,
    )
    env.globals.clear()
    allowed = {"default", "length", "join", "upper", "lower", "trim", "replace", "escape"}
    env.filters = {key: value for key, value in env.filters.items() if key in allowed}
    compiled = {}
    for field in ("title", "body"):
        source = request[field]
        tree = env.parse(source)
        if any(tree.find_all((nodes.Include, nodes.Import, nodes.FromImport, nodes.Extends))):
            raise ValueError("template_import_not_allowed")
        compiled[field] = env.from_string(source)
    results = []
    for context in request["contexts"]:
        result = {}
        for field, template in compiled.items():
            chunks, size = [], 0
            for chunk in template.generate(**context):
                size += len(chunk.encode("utf-8"))
                if size > (512 if field == "title" else 16000):
                    raise ValueError("template_output_too_large")
                chunks.append(chunk)
            result[field] = "".join(chunks).strip()
            if field == "title" and not result[field]:
                raise ValueError("template_title_empty")
        results.append(result)
    return results


if __name__ == "__main__":
    try:
        request = json.loads(sys.stdin.buffer.read(512001))
        print(json.dumps({"results": render(request)}, ensure_ascii=False))
    except Exception as exc:
        # Never reflect template values, secrets, or rendered data in errors.
        code = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        line = getattr(exc, "lineno", None)
        trace = exc.__traceback__
        while trace:
            if trace.tb_frame.f_code.co_filename == "<template>":
                line = trace.tb_lineno
            trace = trace.tb_next
        print(json.dumps({"error": code[:80], "line": line}))
        sys.exit(1)
