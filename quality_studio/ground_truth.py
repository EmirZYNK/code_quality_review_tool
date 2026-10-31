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
    _c("sec101_alias_assignment", "run = eval\nrun(user_input)\n", "SEC101", note="known limitation: alias via assignment"),
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
    _c("sec106_getattr", "import os\ngetattr(os, 'system')(command)\n", "SEC106", note="known limitation: dynamic attribute lookup"),
    # ---- SEC104 -----------------------------------------------------------
    _c("sec104_concat", "def _f(cur, name):\n    cur.execute('SELECT * FROM t WHERE n = ' + name)\n", "SEC104"),
    _c("sec104_fstring", "def _f(cur, name):\n    cur.execute(f'SELECT * FROM t WHERE n = {name}')\n", "SEC104"),
    _c("sec104_percent", "def _f(cur, name):\n    cur.execute('SELECT * FROM t WHERE n = %s' % name)\n", "SEC104"),
    _c("sec104_format", "def _f(cur, name):\n    cur.execute('SELECT * FROM t WHERE n = {}'.format(name))\n", "SEC104"),
    _c("sec104_variable", "def _f(cur, name):\n    query = 'SELECT * FROM t WHERE n = ' + name\n    cur.execute(query)\n", "SEC104"),
    _c("sec104_parameterized_ok", "def _f(cur, name):\n    cur.execute('SELECT * FROM t WHERE n = ?', (name,))\n"),
    _c("sec104_constant_concat_ok", "def _f(cur):\n    cur.execute('SELECT 1' + ' FROM t')\n"),
    _c("sec104_helper_call_ok", "def _f(cur):\n    cur.execute(build_query())\n", note="opaque helper: treated as benign"),
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
    _c("sec105_indirect_key", "KEY = 'password'\nconfig = {KEY: 'abc'}\n", "SEC105", note="known limitation: key held in a variable"),
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
    # ======================================================================
    # Kişi 3 (Berra): her kural için yeni örnekler. Yanlış alarm vermemesi
    # gereken (negatif, "_ok") örnekler özellikle eklendi.
    # ======================================================================
    # ---- SEC101 -----------------------------------------------------------
    _c("b_sec101_eval_literal_string", "result = eval('1 + 1')\nprint(result)\n", "SEC101"),
    _c("b_sec101_name_contains_eval_ok", "def _evaluate(expr):\n    return expr.strip()\n"),
    # ---- SEC102 -----------------------------------------------------------
    _c("b_sec102_marshal_loads", "import marshal\nobj = marshal.loads(blob)\nprint(obj)\n", "SEC102"),
    _c("b_sec102_pickle_dumps_ok", "import pickle\ndata = pickle.dumps({'a': 1})\nprint(data)\n"),
    # ---- SEC103 / SEC106 --------------------------------------------------
    _c("b_sec103_call_shell_true", "import subprocess\nsubprocess.call(command, shell=True)\n", "SEC103"),
    _c("b_sec103_shell_false_ok", "import subprocess\nsubprocess.run(['echo', 'hi'], shell=False)\n"),
    _c("b_sec106_os_system_literal", "import os\nos.system('clear')\n", "SEC106"),
    _c("b_sec106_os_getenv_ok", "import os\nprint(os.getenv('HOME'))\n"),
    # ---- SEC104 -----------------------------------------------------------
    _c("b_sec104_fstring_delete", "def _f(cur, uid):\n    cur.execute(f'DELETE FROM t WHERE id = {uid}')\n", "SEC104"),
    _c("b_sec104_placeholder_ok", "def _f(cur, uid):\n    cur.execute('DELETE FROM t WHERE id = %s', (uid,))\n"),
    # ---- SEC105 -----------------------------------------------------------
    _c("b_sec105_prefixed_name", "db_password = 'qwerty'\n", "SEC105"),
    _c("b_sec105_getenv_ok", "import os\napi_key = os.getenv('API_KEY')\nprint(api_key)\n"),
    _c("b_sec105_none_value_ok", "password = None\n"),
    # ---- SEC107 -----------------------------------------------------------
    _c("b_sec107_load_file", "import yaml\nconfig = yaml.load(handle)\nprint(config)\n", "SEC107"),
    _c("b_sec107_safe_loader_ok", "import yaml\nconfig = yaml.load(text, Loader=yaml.SafeLoader)\nprint(config)\n"),
    # ---- SEC108 -----------------------------------------------------------
    _c("b_sec108_sha1", "import hashlib\ndigest = hashlib.sha1(data).hexdigest()\nprint(digest)\n", "SEC108"),
    _c("b_sec108_sha512_ok", "import hashlib\ndigest = hashlib.sha512(data).hexdigest()\nprint(digest)\n"),
    # ---- SEC109 -----------------------------------------------------------
    _c("b_sec109_post_verify_false", "import requests\nrequests.post(url, data=payload, verify=False)\n", "SEC109"),
    _c("b_sec109_verify_true_ok", "import requests\nrequests.get(url, verify=True)\n"),
    # ---- SEC110 / SEC111 --------------------------------------------------
    _c("b_sec110_assert_in_function", "def _f(x):\n    assert x > 0\n    return x\n", "SEC110"),
    _c("b_sec110_raise_instead_ok", "def _f(x):\n    if x <= 0:\n        raise ValueError('x must be positive')\n    return x\n"),
    _c("b_sec111_from_import_mktemp", "from tempfile import mktemp\nname = mktemp()\nprint(name)\n", "SEC111"),
    _c("b_sec111_named_temporary_ok", "import tempfile\nwith tempfile.NamedTemporaryFile() as handle:\n    print(handle.name)\n"),
    # ---- CQ101 / CQ102 / CQ107 / CQ108 ------------------------------------
    _c("b_cq101_exactly_at_limit_ok", "def _f():\n" + _lines("print({i})", 49)),
    _c("b_cq101_over_limit", "def _f():\n" + _lines("print({i})", 52), "CQ101"),
    _c("b_cq102_exactly_six_params_ok", "def _f(a, b, c, d, e, f):\n    return a + b + c + d + e + f\n"),
    _c("b_cq102_seven_params", "def _f(a, b, c, d, e, f, g):\n    return a + b + c + d + e + f + g\n", "CQ102"),
    _c("b_cq107_many_branches", "def _route(x):\n" + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(20)) + "    return -1\n", "CQ107"),
    _c("b_cq107_few_branches_ok", "def _route(x):\n    if x == 1:\n        return 1\n    if x == 2:\n        return 2\n    return -1\n"),
    _c("b_cq108_depth_three_ok", "def _f(a, b, c):\n    if a:\n        if b:\n            if c:\n                return 1\n    return 0\n"),
    _c("b_cq108_loop_nesting", "def _f(a, b, c, d, e):\n    for x in a:\n        for y in b:\n            if x:\n                if y:\n                    if c:\n                        if d:\n                            return e\n    return 0\n", "CQ108"),
    # ---- CQ103 / CQ104 / CQ106 --------------------------------------------
    _c("b_cq103_async_missing_docstring", "async def fetch():\n    return 1\n", "CQ103"),
    _c("b_cq103_class_private_method_ok", "class _A:\n    def _m(self):\n        return 1\n"),
    _c("b_cq104_in_return", "def _f(x):\n    return x == None\n", "CQ104"),
    _c("b_cq104_is_not_none_ok", "if value is not None:\n    print('present')\n"),
    _c("b_cq104_eq_zero_ok", "if value == 0:\n    print('zero')\n"),
    _c("b_cq106_eq_false", "if done == False:\n    print('waiting')\n", "CQ106"),
    _c("b_cq106_plain_truthiness_ok", "if done:\n    print('finished')\n"),
    # ---- CQ105 / CQ201 / CQ202 --------------------------------------------
    _c("b_cq105_set_default", "def _f(a=set()):\n    return a\n", "CQ105"),
    _c("b_cq105_string_default_ok", "def _f(a='x'):\n    return a\n"),
    _c("b_cq201_bare_except_with_logging", "try:\n    risky()\nexcept:\n    log('failed')\n", "CQ201"),
    _c("b_cq201_tuple_of_types_ok", "try:\n    risky()\nexcept (OSError, ValueError):\n    recover()\n"),
    _c("b_cq202_oserror_pass", "try:\n    risky()\nexcept OSError:\n    pass\n", "CQ202"),
    _c("b_cq202_reraise_ok", "try:\n    risky()\nexcept OSError:\n    raise\n"),
    # ---- PERF101 / PERF102 / PERF103 --------------------------------------
    _c("b_perf101_while_inside_for_ok", "for line in lines:\n    while line.endswith(' '):\n        line = line[:-1]\n"),
    _c("b_perf101_two_independent_loops_ok", "for a in xs:\n    print(a)\nfor b in ys:\n    print(b)\n"),
    _c("b_perf101_pairwise_names", "for first in users:\n    for second in users:\n        print(first, second)\n", "PERF101"),
    _c("b_perf102_count_in_while", "i = 0\nwhile i < 3:\n    print(items.count(i))\n    i += 1\n", "PERF102"),
    _c("b_perf102_count_once_ok", "total = items.count(3)\nprint(total)\n"),
    _c("b_perf103_second_loop", "out = []\nfor n in numbers:\n    out = out + [n]\nprint(out)\n", "PERF103"),
    _c("b_perf103_concat_outside_loop_ok", "a = [1]\nb = a + [2]\nprint(b)\n"),
    _c("b_perf103_string_add_ok", "text = ''\nfor w in words:\n    text = text + w\nprint(text)\n"),
    # ---- RED001 / RED002 ---------------------------------------------------
    _c("b_red001_unused_alias", "import numpy as np\nprint(1)\n", "RED001"),
    _c("b_red001_unused_from_import", "from os import path\nprint(1)\n", "RED001"),
    _c("b_red001_decorator_use_ok", "from functools import lru_cache\n\n@lru_cache(maxsize=None)\ndef _f(n):\n    return n\n"),
    _c("b_red001_type_annotation_ok", "from decimal import Decimal\n\ndef _f(x: Decimal) -> Decimal:\n    return x\n"),
    _c("b_red002_unused_in_async", "async def _f():\n    unused = 1\n    return 2\n", "RED002"),
    _c("b_red002_augassign_ok", "def _f():\n    total = 0\n    total += 1\n    return 2\n"),
    _c("b_red002_global_ok", "def _f():\n    global counter\n    counter = 1\n"),
    # ---- RED003 / RED004 / RED005 / RED006 ---------------------------------
    _c("b_red003_short_repeat_ok", "def _a(x):\n    y = x + 1\n    return y * 2\n\ndef _b(x):\n    y = x + 1\n    return y * 3\n"),
    _c("b_red004_different_constant_ok", """\
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
                    total += item * 3
            return total
        """),
    _c("b_red005_after_continue", "for x in xs:\n    continue\n    print(x)\n", "RED005"),
    _c("b_red005_return_in_if_ok", "def _f(x):\n    if x:\n        return 1\n    print('reachable')\n    return 2\n"),
    _c("b_red006_pass_in_if", "def _f(x):\n    if x:\n        print(x)\n        pass\n    return 1\n", "RED006"),
    _c("b_red006_empty_class_ok", "class _E(Exception):\n    pass\n"),
    # ---- RED007 (yeni kural) ------------------------------------------------
    _c("b_red007_basic", "def _f(x):\n    if x:\n        return True\n    else:\n        return False\n", "RED007"),
    _c("b_red007_inverse", "def _f(x):\n    if x > 3:\n        return False\n    else:\n        return True\n", "RED007"),
    _c("b_red007_comparison_condition", "def _is_adult(age):\n    if age >= 18:\n        return True\n    else:\n        return False\n", "RED007"),
    _c("b_red007_non_bool_values_ok", "def _f(x):\n    if x:\n        return 1\n    else:\n        return 0\n"),
    _c("b_red007_same_values_ok", "def _f(x):\n    if x:\n        return True\n    else:\n        return True\n"),
    _c("b_red007_extra_statement_ok", "def _f(x):\n    if x:\n        print('yes')\n        return True\n    else:\n        return False\n"),
    _c("b_red007_assignment_ok", "def _f(x):\n    if x:\n        ok = True\n    else:\n        ok = False\n    return ok\n"),
    _c("b_red007_elif_chain_ok", "def _f(x, y):\n    if x:\n        return True\n    elif y:\n        return False\n    else:\n        return None\n"),
    _c("b_red007_fallthrough", "def _f(x):\n    if x:\n        return True\n    return False\n", "RED007", note="known limitation: only the if/else form is detected"),
    # ---- AIP001 - AIP005 ---------------------------------------------------
    _c("b_aip001_value_used_twice_ok", "def _f(text):\n    result = text.strip()\n    print(result)\n    return result\n"),
    _c("b_aip001_two_step_return", "def _f(items):\n    total = sum(items)\n    return total\n", "AIP001"),
    _c("b_aip002_broad_print", "def _f():\n    try:\n        return risky()\n    except Exception:\n        print('error')\n", "AIP002"),
    _c("b_aip002_specific_logged_ok", "def _f():\n    try:\n        return risky()\n    except OSError as exc:\n        logger.error('failed: %s', exc)\n        raise\n"),
    _c("b_aip003_single_comment_ok", "# retry once because the upstream API drops the first request\nitems = fetch()\nprint(items)\n"),
    _c("b_aip004_non_trivial_ok", 'def _f(a, b):\n    """Merge b into a, keeping a\'s keys on conflict."""\n    return {**b, **a}\n'),
    _c("b_aip005_long_class_method", "class _A:\n    def _calculate_total_price_with_discount_and_tax(self):\n        return 1\n", "AIP005"),
    _c("b_aip005_normal_names_ok", "def _total_price(items):\n    return sum(items)\n"),
    # ---- SYN001 ------------------------------------------------------------
    _c("b_syn001_unclosed_paren", "print('a'\n", "SYN001"),
    _c("b_syn001_missing_colon", "if x\n    pass\n", "SYN001"),
    # ---- Kenar durumlar ----------------------------------------------------
    _c("b_edge_empty_file_ok", ""),
    _c("b_edge_comment_only_ok", "# sadece yorum\n# bir yorum daha\n"),
    _c("b_edge_unicode_ok", "isim = 'Şükrü Çağlar 😀'\n\ndef _selamla(kişi):\n    return f'Merhaba {kişi}'\n"),
    _c("b_edge_async_def_clean_ok", "import asyncio\n\nasync def _bekle(n):\n    await asyncio.sleep(n)\n    return n\n"),
    _c("b_edge_match_clean_ok", "def _f(x):\n    match x:\n        case 1:\n            return 'a'\n        case _:\n            return 'b'\n"),
    _c("b_edge_match_dead_code", "def _f(x):\n    match x:\n        case 1:\n            return 1\n            print('never')\n        case _:\n            return 2\n", "RED005", note="match gövdeleri iki kez taranmamalı"),
    _c("b_edge_lambda_ok", "kare = lambda x: x * x\nprint(kare(3))\n"),
    _c("b_edge_decorator_ok", "import functools\n\ndef _dec(fn):\n    @functools.wraps(fn)\n    def wrapper(*args, **kwargs):\n        return fn(*args, **kwargs)\n    return wrapper\n"),

    #  ek örnekler (RED003, RED004, AIP003, AIP004 için 2. örnek) ----
    _c("b2_red003_repeat_inside_one_function", """\
        def _report(x):
            a = x * 2 + 1
            b = a * a - x
            c = b // 3 + a
            d = c * 2 - b
            print(d)
            print('middle')
            a = x * 2 + 1
            b = a * a - x
            c = b // 3 + a
            d = c * 2 - b
            print(d)
        """, "RED003"),
    _c("b2_red003_same_statements_different_order_ok", """\
        def _first(x):
            y = x + 1
            z = y * 2
            return z - 3

        def _second(x):
            z = x * 2
            y = z + 1
            return y - 3
        """),
    _c("b2_red004_duplicate_methods", """\
        class _Counter:
            def _count_positive(self, items):
                total = 0
                for item in items:
                    if item > 0:
                        total += item * 2
                return total

            def _count_again(self, items):
                total = 0
                for item in items:
                    if item > 0:
                        total += item * 2
                return total
        """, "RED004"),
    _c("b2_red004_different_parameters_ok", """\
        def _first(items):
            total = 0
            for item in items:
                if item > 0:
                    total += item * 2
            return total

        def _second(values):
            total = 0
            for item in values:
                if item > 0:
                    total += item * 2
            return total
        """),
    _c("b2_aip003_restating_comments_in_function", """\
        def _collect(rows):
            # Create the list
            out = []
            # Loop over the rows
            for row in rows:
                # Add the row
                out.append(row)
            # Return the list
            return out
        """, "AIP003"),
    _c("b2_aip003_two_restating_comments_ok", """\
        # Initialize the list
        items = []
        # Loop through the data
        for row in data:
            items.append(row)
        print(items)
        """),
    _c("b2_aip004_function_to_docstring", 'def _scale(x):\n    """Function to scale a number."""\n    return x * 3\n', "AIP004"),
    _c("b2_aip004_args_returns_but_long_body_ok", 'def _f(a, b):\n    """Combine two values.\n\n    Args:\n        a: first\n        b: second\n\n    Returns:\n        the combined value\n    """\n    first = a * 2\n    second = b * 3\n    third = first + second\n    return third - 1\n'),

]
