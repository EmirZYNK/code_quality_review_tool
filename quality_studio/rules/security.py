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

# SEC112: module-level functions of the (non-cryptographic) random module.
_RANDOM_FUNCS = {
    f"random.{name}" for name in (
        "choice", "choices", "randint", "randrange", "random", "sample",
        "shuffle", "getrandbits", "randbytes", "uniform",
    )
}
# Words that mark a value as security-sensitive when it is produced randomly.
_RANDOM_SECRET_WORDS = _SECRET_WORDS | {
    "passphrase", "otp", "nonce", "salt", "csrf", "pin", "session", "verification", "reset", "activation",
}
# 'session_id' or 'reset_id' generated randomly is still a secret, so 'id' does not clear it here.
_RANDOM_NOT_SECRET_LAST = _NOT_SECRET_LAST - {"id"}

# SEC113: scheme://user:password@host inside a string literal.
_URL_CREDENTIALS = re.compile(r"\b([a-z][a-z0-9+.\-]*)://([^\s/:@]+):([^\s/@]+)@([^\s/?#]+)", re.IGNORECASE)
_FSTRING_HOLE = "\x00"  # stands for a {placeholder} of an f-string
_PLACEHOLDER_START = ("{", "%", "$", "<", _FSTRING_HOLE)

# SEC114: requests functions that accept a timeout and default to waiting forever.
_REQUESTS_CALLS = {
    f"requests.{name}" for name in ("get", "post", "put", "patch", "delete", "head", "options", "request")
}

# SEC115: input() reaching a file-opening call within one scope.
_INPUT_FUNCS = {"input", "builtins.input", "raw_input"}
_OPEN_FUNCS = {"open", "builtins.open", "io.open", "os.open", "codecs.open"}
_PATH_KEYWORDS = ("file", "path", "filename")
# Calls whose result can no longer carry a traversal path.
_PATH_SANITIZERS = {"os.path.basename", "int", "float", "len", "bool", "werkzeug.utils.secure_filename"}
# Methods that, used in an if-test, indicate the path was checked against a base folder.
_PATH_CHECK_METHODS = {"startswith", "is_relative_to"}


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


def is_random_secret_name(identifier: str) -> bool:
    """True when a name says its value is a token, password, code or similar secret."""
    words = _words(identifier)
    if not words or words[-1] in _RANDOM_NOT_SECRET_LAST:
        return False
    if any(w in _RANDOM_SECRET_WORDS for w in words):
        return True
    return any(pair in _SECRET_BIGRAMS for pair in zip(words, words[1:]))


def find_url_credentials(text: str) -> re.Match | None:
    """First 'scheme://user:password@host' whose password is a literal, not a placeholder."""
    for match in _URL_CREDENTIALS.finditer(text):
        password = match.group(3)
        if password.startswith(_PLACEHOLDER_START) or _FSTRING_HOLE in password:
            continue
        if set(password) <= set("*xX"):
            continue
        return match
    return None


def _fstring_text(node: ast.JoinedStr) -> str:
    parts = []
    for value in node.values:
        parts.append(value.value if is_str_const(value) else _FSTRING_HOLE)
    return "".join(parts)


def _bound_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        return [name for element in target.elts for name in _bound_names(element)]
    if isinstance(target, ast.Starred):
        return _bound_names(target.value)
    return []


def _checked_names(test: ast.AST) -> set[str]:
    """Names validated by an if-test: allow-list membership or a base-folder prefix check."""
    checked: set[str] = set()
    for node in ast.walk(test):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name):
            if any(isinstance(op, (ast.In, ast.NotIn)) for op in node.ops):
                checked.add(node.left.id)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in _PATH_CHECK_METHODS and isinstance(node.func.value, ast.Name):
                checked.add(node.func.value.id)
    return checked


def _timeout_problem(call: ast.Call) -> str | None:
    if any(keyword.arg is None for keyword in call.keywords):
        return None  # **kwargs may carry the timeout
    value = _keyword_value(call, "timeout")
    if value is None:
        return "without a timeout"
    if isinstance(value, ast.Constant) and value.value is None:
        return "with timeout=None"
    return None


def _path_argument(call: ast.Call) -> ast.AST | None:
    if call.args:
        return call.args[0]
    for keyword in call.keywords:
        if keyword.arg in _PATH_KEYWORDS:
            return keyword.value
    return None


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
        self.input_names: list[set[str]] = [set()]  # SEC115: names holding input() data, per scope
        self.secret_context: list[str] = []  # SEC112: enclosing secret-like names

    # -- scope handling: SQL strings and input() values tracked per function --
    def _visit_function(self, node):
        self._credential_defaults(node)
        self.dynamic_sql_names.append(set())
        self.input_names.append(set())
        self._visit_as_secret([node.name], self.generic_visit, node)
        self.input_names.pop()
        self.dynamic_sql_names.pop()

    def _visit_as_secret(self, names, visit, node) -> None:
        """Run visit(node) with SEC112 context set when one of names is secret-like."""
        label = next((n for n in names if n and is_random_secret_name(n)), None)
        if label:
            self.secret_context.append(label)
        visit(node)
        if label:
            self.secret_context.pop()

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

    # -- SEC115: values that come from input() -----------------------------
    def _from_input(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Call):
            name = self.ctx.dotted(node.func)
            if name in _PATH_SANITIZERS:
                return False
            if name in _INPUT_FUNCS:
                return True
        if isinstance(node, ast.Name):
            return node.id in self.input_names[-1]
        return any(self._from_input(child) for child in ast.iter_child_nodes(node))

    def _track_input(self, targets, value) -> None:
        tainted = self._from_input(value)
        names = self.input_names[-1]
        for target in targets:
            for name in _bound_names(target):
                if tainted:
                    names.add(name)
                else:
                    names.discard(name)

    def visit_If(self, node: ast.If):
        self.input_names[-1].difference_update(_checked_names(node.test))
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr):
        self._track_input([node.target], node.value)
        self.generic_visit(node)

    # -- assignments / literals ---------------------------------------------
    def visit_Assign(self, node: ast.Assign):
        self._track_string(node.targets, node.value)
        self._track_input(node.targets, node.value)
        for target in node.targets:
            self._credential(target, node.value, node)
        self._visit_as_secret([_target_name(t) for t in node.targets], self.generic_visit, node)

    def visit_AnnAssign(self, node: ast.AnnAssign):
        if node.value is not None:
            self._track_string([node.target], node.value)
            self._track_input([node.target], node.value)
            self._credential(node.target, node.value, node)
        self._visit_as_secret([_target_name(node.target)], self.generic_visit, node)

    def visit_AugAssign(self, node: ast.AugAssign):
        if isinstance(node.target, ast.Name) and isinstance(node.op, ast.Add):
            if is_dynamic_string(ast.BinOp(left=ast.Constant(""), op=ast.Add(), right=node.value)):
                self.dynamic_sql_names[-1].add(node.target.id)
            if self._from_input(node.value):
                self.input_names[-1].add(node.target.id)
        self._visit_as_secret([_target_name(node.target)], self.generic_visit, node)

    def visit_keyword(self, node: ast.keyword):
        self._visit_as_secret([node.arg], self.generic_visit, node)

    def visit_Dict(self, node: ast.Dict):
        for key, value in zip(node.keys, node.values):
            label = key.value if key is not None and is_str_const(key) else None
            if label and looks_like_secret_name(label) and _literal_secret(value):
                self.ctx.report("SEC105", value, f"Possible hardcoded credential in key '{label}'")
            if key is not None:
                self.visit(key)
            self._visit_as_secret([label], self.visit, value)

    def visit_Expr(self, node: ast.Expr):
        # A bare string statement (docstring) does nothing, so URLs in it are documentation.
        if not isinstance(node.value, (ast.Constant, ast.JoinedStr)):
            self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, str):
            self._url_credentials(node.value, node)

    def visit_JoinedStr(self, node: ast.JoinedStr):
        self._url_credentials(_fstring_text(node), node)
        for value in node.values:
            if isinstance(value, ast.FormattedValue):
                self.visit(value)

    def _url_credentials(self, text: str, node: ast.AST) -> None:
        match = find_url_credentials(text)
        if match:
            scheme, user = match.group(1), match.group(2).replace(_FSTRING_HOLE, "{...}")
            self.ctx.report("SEC113", node, f"Password embedded in {scheme}:// URL for user '{user}'")

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
        self._random_http_and_paths(node, name)
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

    def _random_http_and_paths(self, node: ast.Call, name: str) -> None:
        if name in _RANDOM_FUNCS and self.secret_context:
            self.ctx.report("SEC112", node, f"{name}() used to produce '{self.secret_context[-1]}'")
        elif name in _REQUESTS_CALLS:
            problem = _timeout_problem(node)
            if problem:
                self.ctx.report("SEC114", node, f"{name}() called {problem}")
        elif name in _OPEN_FUNCS:
            path = _path_argument(node)
            if path is not None and self._from_input(path):
                self.ctx.report("SEC115", node, f"Path passed to {name}() comes from input()")

    def visit_Assert(self, node: ast.Assert):
        self.ctx.report("SEC110", node)
        self.generic_visit(node)


def run(ctx: Context) -> None:
    """Run all security checks."""
    _Visitor(ctx).visit(ctx.tree)


