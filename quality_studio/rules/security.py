"""Security rules (SEC1xx)."""
from __future__ import annotations

import ast
import re

from ..astutil import is_dynamic_string, is_str_const
from .context import Context

_SECRET_WORDS = {"password", "passwd", "pwd", "secret", "token", "credential", "credentials", "apikey"}
_SECRET_BIGRAMS = {
    ("api", "key"), ("private", "key"), ("access", "key"), ("secret", "key"), ("auth", "key"),
}
# A trailing word that says the value is a name/location/setting, not a secret.
_NOT_SECRET_LAST = {
    "url", "uri", "path", "file", "name", "type", "header", "endpoint", "env", "field", "id",
    "label", "prompt", "length", "count", "ttl", "timeout", "size", "format", "kind", "mode",
    "dir", "location", "param", "params", "key_name",
}
_EXEC_NAMES = {"eval", "exec", "builtins.eval", "builtins.exec"}
_DESERIALIZE = {
    "pickle.load", "pickle.loads", "cPickle.load", "cPickle.loads",
    "marshal.load", "marshal.loads", "dill.load", "dill.loads",
}
_SHELL_FUNCS = {"os.system", "os.popen", "subprocess.getoutput", "subprocess.getstatusoutput"}
_SUBPROCESS = {
    "subprocess.run", "subprocess.Popen", "subprocess.call",
    "subprocess.check_output", "subprocess.check_call",
}
_SQL_METHODS = {"execute", "executemany", "executescript"}
_SQL_WRAPPERS = {"sqlalchemy.text"}
_WEAK_HASHES = {"md5", "sha1"}
_YAML_LOAD = {"yaml.load", "yaml.load_all"}
_YAML_UNSAFE = {"yaml.unsafe_load", "yaml.unsafe_load_all"}
_HTTP_ROOTS = ("requests.", "httpx.", "urllib3.")


def _words(identifier: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", identifier)
    return [w for w in spaced.lower().split("_") if w]


def looks_like_secret_name(identifier: str) -> bool:
    """Word-based match so 'tokenizer' or 'max_tokens' are not credentials."""
    words = _words(identifier)
    if not words or words[-1] in _NOT_SECRET_LAST:
        return False
    if any(w in _SECRET_WORDS for w in words):
        return True
    return any(pair in _SECRET_BIGRAMS for pair in zip(words, words[1:]))


def _literal_secret(value: ast.AST | None) -> bool:
    return value is not None and is_str_const(value) and bool(value.value.strip())


def _target_name(target: ast.AST) -> str | None:
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    if isinstance(target, ast.Subscript) and is_str_const(target.slice):
        return target.slice.value
    return None


def _keyword_value(call: ast.Call, name: str, position: int | None = None) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    if position is not None and len(call.args) > position:
        return call.args[position]
    return None


class _Visitor(ast.NodeVisitor):
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.dynamic_sql_names: list[set[str]] = [set()]

    # -- scope handling for SQL strings assembled in a variable -------------
    def _visit_function(self, node):
        self._credential_defaults(node)
        self.dynamic_sql_names.append(set())
        self.generic_visit(node)
        self.dynamic_sql_names.pop()

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function

    def _track_string(self, targets, value) -> None:
        names = self.dynamic_sql_names[-1]
        for target in targets:
            if isinstance(target, ast.Name):
                if is_dynamic_string(value):
                    names.add(target.id)
                else:
                    names.discard(target.id)

    # -- assignments / literals ---------------------------------------------
    def visit_Assign(self, node: ast.Assign):
        self._track_string(node.targets, node.value)
        for target in node.targets:
            self._credential(target, node.value, node)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        if node.value is not None:
            self._track_string([node.target], node.value)
            self._credential(node.target, node.value, node)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign):
        if isinstance(node.target, ast.Name) and isinstance(node.op, ast.Add):
            if is_dynamic_string(ast.BinOp(left=ast.Constant(""), op=ast.Add(), right=node.value)):
                self.dynamic_sql_names[-1].add(node.target.id)
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict):
        for key, value in zip(node.keys, node.values):
            if key is not None and is_str_const(key) and looks_like_secret_name(key.value):
                if _literal_secret(value):
                    self.ctx.report("SEC105", value, f"Possible hardcoded credential in key '{key.value}'")
        self.generic_visit(node)

    def _credential(self, target: ast.AST, value: ast.AST, node: ast.AST) -> None:
        name = _target_name(target)
        if name and looks_like_secret_name(name) and _literal_secret(value):
            self.ctx.report("SEC105", node, f"Possible hardcoded credential in '{name}'")

    def _credential_defaults(self, func) -> None:
        positional = [*func.args.posonlyargs, *func.args.args]
        pairs = list(zip(positional[len(positional) - len(func.args.defaults):], func.args.defaults))
        pairs += [(a, d) for a, d in zip(func.args.kwonlyargs, func.args.kw_defaults) if d is not None]
        for arg, default in pairs:
            if looks_like_secret_name(arg.arg) and _literal_secret(default):
                self.ctx.report("SEC105", default, f"Possible hardcoded credential in default of '{arg.arg}'")

    # -- calls ---------------------------------------------------------------
    def visit_Call(self, node: ast.Call):
        name = self.ctx.dotted(node.func)
        self._exec_and_shell(node, name)
        self._sql(node, name)
        self._yaml(node, name)
        self._hash_and_tls(node, name)
        for keyword in node.keywords:
            if keyword.arg and looks_like_secret_name(keyword.arg) and _literal_secret(keyword.value):
                self.ctx.report("SEC105", keyword.value, f"Possible hardcoded credential in argument '{keyword.arg}'")
        self.generic_visit(node)

    def _exec_and_shell(self, node: ast.Call, name: str) -> None:
        report = self.ctx.report
        if name in _EXEC_NAMES:
            report("SEC101", node, f"Use of {name.split('.')[-1]}()")
        elif name in _DESERIALIZE:
            report("SEC102", node)
        elif name in _SHELL_FUNCS:
            report("SEC106", node)
        elif name == "tempfile.mktemp":
            report("SEC111", node)
        elif name in _SUBPROCESS:
            value = _keyword_value(node, "shell")
            if isinstance(value, ast.Constant) and value.value is True:
                report("SEC103", node)

    def _sql(self, node: ast.Call, name: str) -> None:
        is_method = isinstance(node.func, ast.Attribute) and node.func.attr in _SQL_METHODS
        if not (is_method or name in _SQL_WRAPPERS) or not node.args:
            return
        query = node.args[0]
        tracked = isinstance(query, ast.Name) and query.id in self.dynamic_sql_names[-1]
        if is_dynamic_string(query) or tracked:
            self.ctx.report("SEC104", node)

    def _yaml(self, node: ast.Call, name: str) -> None:
        if name in _YAML_UNSAFE:
            self.ctx.report("SEC107", node, f"Use of {name}()")
        elif name in _YAML_LOAD:
            loader = _keyword_value(node, "Loader", position=1)
            loader_name = self.ctx.dotted(loader) if loader is not None else ""
            if not loader_name.endswith(("SafeLoader", "BaseLoader")):
                self.ctx.report("SEC107", node, f"{name}() without a safe loader")

    def _hash_and_tls(self, node: ast.Call, name: str) -> None:
        if name in {f"hashlib.{h}" for h in _WEAK_HASHES} or (
            name == "hashlib.new" and node.args and is_str_const(node.args[0])
            and node.args[0].value.lower() in _WEAK_HASHES
        ):
            flag = _keyword_value(node, "usedforsecurity")
            if not (isinstance(flag, ast.Constant) and flag.value is False):
                self.ctx.report("SEC108", node)
        verify = _keyword_value(node, "verify")
        if isinstance(verify, ast.Constant) and verify.value is False:
            confidence = "high" if name.startswith(_HTTP_ROOTS) else "medium"
            self.ctx.report("SEC109", node, confidence=confidence)

    def visit_Assert(self, node: ast.Assert):
        self.ctx.report("SEC110", node)
        self.generic_visit(node)


def run(ctx: Context) -> None:
    """Run all security checks."""
    _Visitor(ctx).visit(ctx.tree)


