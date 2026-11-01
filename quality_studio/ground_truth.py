"""Hand-labeled snippets used to measure per-rule precision and recall.

Each case lists the COMPLETE set of rules that should fire on the snippet, decided by
reading the code, not by running the engine. Any rule that fires but is not listed counts
as a false positive; any listed rule that does not fire counts as a false negative.
Cases marked 'known limitation' are positives the AST approach cannot see; they are kept
on purpose so recall is not inflated.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    id: str
    code: str
    expected: frozenset[str]
    note: str = ""


def _c(case_id: str, code: str, *expected: str, note: str = "") -> Case:
    return Case(case_id, textwrap.dedent(code).lstrip("\n"), frozenset(expected), note)


def _lines(template: str, count: int, indent: str = "    ") -> str:
    return "".join(f"{indent}{template.format(i=i)}\n" for i in range(count))


_LONG_FUNCTION = "def _long():\n" + _lines("print({i})", 60)
_SHORT_FUNCTION = "def _short():\n" + _lines("print({i})", 10)
_COMPLEX_FUNCTION = "def _route(x):\n" + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(16)) + "    return -1\n"
_AT_THRESHOLD_FUNCTION = "def _route(x):\n" + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(14)) + "    return -1\n"

CASES: list[Case] = [
    # ---- SEC101 -----------------------------------------------------------
    _c("sec101_eval", "def _f(s):\n    return eval(s)\n", "SEC101"),
    _c("sec101_exec", "exec(source_text)\n", "SEC101"),
    _c("sec101_literal_eval_ok", "import ast\nvalue = ast.literal_eval(text)\nprint(value)\n"),
    _c("sec101_method_named_eval_ok", "model.eval()\n"),
    _c("sec101_alias_assignment", "run = eval\nrun(user_input)\n", "SEC101", note="bilinen sınırlama: atama ile takma ad"),
    # ---- SEC102 -----------------------------------------------------------
    _c("sec102_loads", "import pickle\nobj = pickle.loads(blob)\nprint(obj)\n", "SEC102"),
    _c("sec102_alias", "import pickle as p\nobj = p.load(handle)\nprint(obj)\n", "SEC102"),
    _c("sec102_from_import", "from pickle import loads\nobj = loads(blob)\nprint(obj)\n", "SEC102"),
    _c("sec102_json_ok", "import json\nobj = json.loads(blob)\nprint(obj)\n"),
    # ---- SEC103 / SEC106 --------------------------------------------------
    _c("sec103_shell_true", "import subprocess\nsubprocess.run(command, shell=True)\n", "SEC103"),
    _c("sec103_popen_shell", "import subprocess\nsubprocess.Popen(command, shell=True)\n", "SEC103"),
    _c("sec103_list_ok", "import subprocess\nsubprocess.run(['ls', '-l'], check=True)\n"),
    _c("sec103_unrelated_run_ok", "runner.run(task, shell=True)\n"),
    _c("sec106_os_system", "import os\nos.system(command)\n", "SEC106"),
    _c("sec106_from_import", "from os import system\nsystem(command)\n", "SEC106"),
    _c("sec106_popen", "import os\nstream = os.popen(command)\nprint(stream)\n", "SEC106"),
    _c("sec106_path_join_ok", "import os\nprint(os.path.join('a', 'b'))\n"),
    _c("sec106_getattr", "import os\ngetattr(os, 'system')(command)\n", "SEC106", note="bilinen sınırlama: dinamik öznitelik arama"),
    # ---- SEC104 -----------------------------------------------------------
    _c("sec104_concat", "def _f(cur, name):\n    cur.execute('SELECT * FROM t WHERE n = ' + name)\n", "SEC104"),
    _c("sec104_fstring", "def _f(cur, name):\n    cur.execute(f'SELECT * FROM t WHERE n = {name}')\n", "SEC104"),
    _c("sec104_percent", "def _f(cur, name):\n    cur.execute('SELECT * FROM t WHERE n = %s' % name)\n", "SEC104"),
    _c("sec104_format", "def _f(cur, name):\n    cur.execute('SELECT * FROM t WHERE n = {}'.format(name))\n", "SEC104"),
    _c("sec104_variable", "def _f(cur, name):\n    query = 'SELECT * FROM t WHERE n = ' + name\n    cur.execute(query)\n", "SEC104"),
    _c("sec104_parameterized_ok", "def _f(cur, name):\n    cur.execute('SELECT * FROM t WHERE n = ?', (name,))\n"),
    _c("sec104_constant_concat_ok", "def _f(cur):\n    cur.execute('SELECT 1' + ' FROM t')\n"),
    _c("sec104_helper_call_ok", "def _f(cur):\n    cur.execute(build_query())\n", note="yardımcı fonksiyonun içi görünmez: zararsız sayıldı"),
    # ---- SEC105 -----------------------------------------------------------
    _c("sec105_assign", "password = 'hunter2'\n", "SEC105"),
    _c("sec105_attribute", "class _A:\n    def __init__(self):\n        self.password = 'abc'\n", "SEC105"),
    _c("sec105_annotated", "api_key: str = 'abc123'\n", "SEC105"),
    _c("sec105_dict_key", "config = {'password': 'abc'}\n", "SEC105"),
    _c("sec105_default_argument", "def _f(password='abc'):\n    return password\n", "SEC105"),
    _c("sec105_keyword_argument", "connect(host, password='abc')\n", "SEC105"),
    _c("sec105_subscript", "settings = {}\nsettings['secret'] = 'abc'\n", "SEC105"),
    _c("sec105_token_url_ok", "token_url = 'https://auth.example/token'\n"),
    _c("sec105_max_tokens_ok", "max_tokens = '512'\ntokenizer = 'bpe'\n"),
    _c("sec105_environment_ok", "import os\npassword = os.environ['DB_PASSWORD']\n"),
    _c("sec105_empty_ok", "password = ''\n"),
    _c("sec105_indirect_key", "KEY = 'password'\nconfig = {KEY: 'abc'}\n", "SEC105", note="bilinen sınırlama: anahtar bir değişkende tutuluyor"),
    # ---- SEC107 -----------------------------------------------------------
    _c("sec107_load", "import yaml\ndata = yaml.load(text)\nprint(data)\n", "SEC107"),
    _c("sec107_full_loader", "import yaml\ndata = yaml.load(text, Loader=yaml.Loader)\nprint(data)\n", "SEC107"),
    _c("sec107_unsafe_load", "import yaml\ndata = yaml.unsafe_load(text)\nprint(data)\n", "SEC107"),
    _c("sec107_safe_load_ok", "import yaml\ndata = yaml.safe_load(text)\nprint(data)\n"),
    _c("sec107_safe_loader_ok", "import yaml\ndata = yaml.load(text, Loader=yaml.SafeLoader)\nprint(data)\n"),
    # ---- SEC108 -----------------------------------------------------------
    _c("sec108_md5", "import hashlib\ndigest = hashlib.md5(data).hexdigest()\nprint(digest)\n", "SEC108"),
    _c("sec108_sha1", "import hashlib\ndigest = hashlib.sha1(data).hexdigest()\nprint(digest)\n", "SEC108"),
    _c("sec108_new_md5", "import hashlib\ndigest = hashlib.new('md5', data)\nprint(digest)\n", "SEC108"),
    _c("sec108_sha256_ok", "import hashlib\ndigest = hashlib.sha256(data).hexdigest()\nprint(digest)\n"),
    _c("sec108_not_for_security_ok", "import hashlib\ndigest = hashlib.md5(data, usedforsecurity=False)\nprint(digest)\n"),
    # ---- SEC109 -----------------------------------------------------------
    _c("sec109_requests", "import requests\nrequests.get(url, verify=False)\n", "SEC109"),
    _c("sec109_session", "session.get(url, verify=False)\n", "SEC109"),
    _c("sec109_verify_true_ok", "import requests\nrequests.get(url, verify=True)\n"),
    _c("sec109_ca_bundle_ok", "import requests\nrequests.get(url, verify='/etc/ssl/ca.pem')\n"),
    # ---- SEC110 / SEC111 --------------------------------------------------
    _c("sec110_assert", "assert user.is_admin\n", "SEC110"),
    _c("sec110_none_ok", "if not user.is_admin:\n    raise PermissionError('admin only')\n"),
    _c("sec111_mktemp", "import tempfile\nname = tempfile.mktemp()\nprint(name)\n", "SEC111"),
    _c("sec111_mkstemp_ok", "import tempfile\nhandle, name = tempfile.mkstemp()\nprint(handle, name)\n"),
    # ---- Code smell -------------------------------------------------------
    _c("cq101_long", _LONG_FUNCTION, "CQ101"),
    _c("cq101_short_ok", _SHORT_FUNCTION),
    _c("cq102_many_params", "def _f(a, b, c, d, e, f, g, h):\n    return a + b + c + d + e + f + g + h\n", "CQ102"),
    _c("cq102_few_params_ok", "def _f(a, b, c):\n    return a + b + c\n"),
    _c("cq102_self_not_counted_ok", "class _A:\n    def _m(self, a, b, c, d, e, f):\n        return a + b + c + d + e + f\n"),
    _c("cq107_complex", _COMPLEX_FUNCTION, "CQ107"),
    _c("cq107_at_threshold_ok", _AT_THRESHOLD_FUNCTION),
    _c("cq107_simple_ok", "def _f(x):\n    if x:\n        return 1\n    return 0\n"),
    _c("cq108_deep_nesting",
       "def _f(a, b, c, d, e):\n    if a:\n        if b:\n            if c:\n                if d:\n                    if e:\n                        return 1\n    return 0\n",
       "CQ108"),
    _c("cq108_shallow_ok", "def _f(a, b):\n    if a:\n        if b:\n            return 1\n    return 0\n"),
    # ---- Style ------------------------------------------------------------
    _c("cq103_missing_docstring", "def public():\n    return 1\n", "CQ103"),
    _c("cq103_public_method", "class _A:\n    def run(self):\n        return 1\n", "CQ103"),
    _c("cq103_documented_ok", "def public():\n    \"\"\"Return one.\"\"\"\n    return 1\n"),
    _c("cq103_private_ok", "def _helper():\n    return 1\n"),
    _c("cq103_nested_ok", "def _outer():\n    def inner():\n        return 1\n    return inner\n"),
    _c("cq104_none_eq", "if value == None:\n    print('missing')\n", "CQ104"),
    _c("cq104_none_ne", "if value != None:\n    print('present')\n", "CQ104"),
    _c("cq104_is_none_ok", "if value is None:\n    print('missing')\n"),
    _c("cq106_true_eq", "if flag == True:\n    print('on')\n", "CQ106"),
    _c("cq106_false_ne", "if flag != False:\n    print('on')\n", "CQ106"),
    _c("cq106_is_true_ok", "if flag is True:\n    print('on')\n"),
    # ---- Reliability ------------------------------------------------------
    _c("cq105_list_default", "def _f(a=[]):\n    return a\n", "CQ105"),
    _c("cq105_dict_default", "def _f(a={}):\n    return a\n", "CQ105"),
    _c("cq105_none_default_ok", "def _f(a=None):\n    return a or []\n"),
    _c("cq105_tuple_default_ok", "def _f(a=()):\n    return a\n"),
    _c("cq201_bare_except", "try:\n    risky()\nexcept:\n    cleanup()\n", "CQ201"),
    _c("cq201_cq202_bare_pass", "try:\n    risky()\nexcept:\n    pass\n", "CQ201", "CQ202"),
    _c("cq202_typed_pass", "try:\n    risky()\nexcept ValueError:\n    pass\n", "CQ202"),
    _c("cq202_handled_ok", "try:\n    risky()\nexcept ValueError:\n    recover()\n"),
    # ---- Performance ------------------------------------------------------
    _c("perf101_grid_traversal_ok", "for row in rows:\n    for cell in row:\n        print(cell)\n"),
    _c("perf101_for_while_depth2_ok", "for item in items:\n    while item:\n        item = item.next\n"),
    _c("perf101_matrix_comprehension_ok", "grid = [[i * j for j in range(width)] for i in range(height)]\nprint(grid)\n"),
    _c("perf101_dependent_triple_ok", "for a in rows:\n    for b in a:\n        for c in b:\n            print(c)\n"),
    _c("perf101_pairwise_same_iterable", "for a in items:\n    for b in items:\n        print(a, b)\n", "PERF101"),
    _c("perf101_pairwise_range_len", "for i in range(len(xs)):\n    for j in range(len(xs)):\n        print(i, j)\n", "PERF101"),
    _c("perf101_pairwise_comprehension", "pairs = [(a, b) for a in items for b in items]\nprint(pairs)\n", "PERF101"),
    _c("perf101_triple", "for a in x:\n    for b in y:\n        for c in z:\n            print(a, b, c)\n", "PERF101"),
    _c("perf101_small_constant_ok", "for i in range(3):\n    for j in range(3):\n        print(i, j)\n"),
    _c("perf101_single_loop_ok", "for item in items:\n    print(item)\n"),
    _c("perf102_list_count", "names = [1, 2, 3]\nfor n in names:\n    print(names.count(n))\n", "PERF102"),
    _c("perf102_unknown_receiver", "for x in items:\n    print(items.count(x))\n", "PERF102"),
    _c("perf102_str_count_ok", "text = 'banana'\nfor ch in letters:\n    print(text.count(ch))\n"),
    _c("perf102_outside_loop_ok", "print(items.count(3))\n"),
    _c("perf103_list_concat", "result = []\nfor n in numbers:\n    result = result + [n * 2]\nprint(result)\n", "PERF103"),
    _c("perf103_append_ok", "result = []\nfor n in numbers:\n    result.append(n * 2)\nprint(result)\n"),
    _c("perf103_int_add_ok", "total = 0\nfor n in numbers:\n    total = total + n\nprint(total)\n"),
    # ---- Redundancy -------------------------------------------------------
    _c("red001_unused_import", "import os\nimport sys\nprint(sys.argv)\n", "RED001"),
    _c("red001_unused_from_import", "from collections import OrderedDict\nprint(1)\n", "RED001"),
    _c("red001_used_ok", "import os\nprint(os.getcwd())\n"),
    _c("red001_all_export_ok", "from json import dumps\n__all__ = ['dumps']\n"),
    _c("red001_optional_import_ok", "try:\n    import ujson\nexcept ImportError:\n    ujson = None\nprint(ujson)\n"),
    _c("red001_string_annotation_ok", "from decimal import Decimal\n\ndef _f(x: 'Decimal') -> None:\n    print(x)\n"),
    _c("red002_unused_variable", "def _f():\n    unused = compute()\n    return 1\n", "RED002"),
    _c("red002_used_ok", "def _f():\n    value = compute()\n    return value + 1\n"),
    _c("red002_underscore_ok", "def _f():\n    _ignored = compute()\n    return 1\n"),
    _c("red002_closure_use_ok", "def _f():\n    factor = 2\n    def inner(x):\n        return x * factor\n    return inner\n"),
    _c("red003_duplicate_block", """\
        def _first(x):
            a = x * 2 + 1
            b = a * a - x
            c = b // 3 + a
            d = c * 2 - b
            return d * 2

        def _second(x):
            print('second')
            a = x * 2 + 1
            b = a * a - x
            c = b // 3 + a
            d = c * 2 - b
            return d + 1
        """, "RED003"),
    _c("red003_different_blocks_ok", """\
        def _first(x):
            return x * 2 + 1

        def _second(x):
            return x - 7
        """),
    _c("red004_duplicate_function", """\
        def _first(items):
            total = 0
            for item in items:
                if item > 0:
                    total += item * 2
            return total

        def _second(items):
            total = 0
            for item in items:
                if item > 0:
                    total += item * 2
            return total
        """, "RED004"),
    _c("red005_after_return", "def _f():\n    return 1\n    print('never')\n", "RED005"),
    _c("red005_after_raise", "def _f():\n    raise ValueError('x')\n    print('never')\n", "RED005"),
    _c("red005_reachable_ok", "def _f(x):\n    if x:\n        return 1\n    return 2\n"),
    _c("red006_redundant_pass", "def _f():\n    print(1)\n    pass\n", "RED006"),
    _c("red006_lone_pass_ok", "def _f():\n    pass\n"),
    # ---- AI-generation indicators ----------------------------------------
    _c("aip001_temp_return", "def _f(text):\n    result = text.strip()\n    return result\n", "AIP001"),
    _c("aip001_direct_return_ok", "def _f(text):\n    return text.strip()\n"),
    _c("aip002_defensive_try", "def _f():\n    try:\n        return risky()\n    except Exception:\n        return None\n", "AIP002"),
    _c("aip002_reraise_ok", "def _f():\n    try:\n        return risky()\n    except Exception:\n        logger.error('x')\n        raise\n"),
    _c("aip002_narrow_ok", "def _f():\n    try:\n        return risky()\n    except KeyError:\n        return None\n"),
    _c("aip003_restating_comments", """\
        # Initialize the list
        items = []
        # Loop through the data
        for row in data:
            # Append the value
            items.append(row)
        # Return the result
        print(items)
        """, "AIP003"),
    _c("aip003_intent_comments_ok", """\
        # Retry once: the upstream API drops the first request after idle.
        items = fetch()
        print(items)
        """),
    _c("aip004_boilerplate_docstring", 'def _add(a, b):\n    """This function adds two numbers."""\n    return a + b\n', "AIP004"),
    _c("aip004_args_returns_trivial", 'def _add(a, b):\n    """Add.\n\n    Args:\n        a: first\n        b: second\n\n    Returns:\n        the sum\n    """\n    return a + b\n', "AIP004"),
    _c("aip004_real_docstring_ok", 'def _add(a, b):\n    """Add with saturation at 2**31 - 1."""\n    return min(a + b, 2**31 - 1)\n'),
    _c("aip005_verbose_name", "def _calculate_total_price_with_discount_and_tax(p):\n    return p\n", "AIP005"),
    _c("aip005_short_name_ok", "def _total(p):\n    return p\n"),
    # ---- Syntax, suppression and clean controls ---------------------------
    _c("syn001_broken", "def broken(:\n    pass\n", "SYN001"),
    _c("noqa_suppresses_ok", "if value == None:  # noqa: CQ104\n    print('missing')\n"),
    _c("clean_dataclass", """\
        from dataclasses import dataclass


        @dataclass
        class Point:
            \"\"\"A 2D point.\"\"\"

            x: float
            y: float

            def norm(self) -> float:
                \"\"\"Euclidean distance from the origin.\"\"\"
                return (self.x ** 2 + self.y ** 2) ** 0.5
        """),
    _c("clean_context_manager", """\
        def read_lines(path):
            \"\"\"Return the non-empty lines of a UTF-8 file.\"\"\"
            with open(path, encoding='utf-8') as handle:
                return [line.rstrip() for line in handle if line.strip()]
        """),
    _c("clean_counter", """\
        from collections import Counter


        def top_words(text, n=3):
            \"\"\"Return the n most common words in text.\"\"\"
            return Counter(text.split()).most_common(n)
        """),
    _c("clean_cli", """\
        import argparse


        def main():
            \"\"\"Parse arguments and greet.\"\"\"
            parser = argparse.ArgumentParser()
            parser.add_argument('name')
            args = parser.parse_args()
            print(f'Hello, {args.name}')


        if __name__ == '__main__':
            main()
        """),
    _c("clean_exception_handling", """\
        import logging

        log = logging.getLogger(__name__)


        def parse_int(text):
            \"\"\"Return int(text), or None when text is not a number.\"\"\"
            try:
                return int(text)
            except ValueError:
                log.warning('not a number: %r', text)
                return None
        """),
]
