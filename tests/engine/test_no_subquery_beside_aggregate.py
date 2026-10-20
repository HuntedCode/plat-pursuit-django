"""No wrapped correlated subquery beside an outer aggregate (the 2026-10 `/jobs/` outage).

When a queryset annotates an aggregate (`Count`, `Sum`, ...) it gets a GROUP BY, and Django copies any
annotation that is a subquery WRAPPED in another expression (`Coalesce(Subquery(...))`, a `Case` around an
`Exists`, arithmetic on a subquery) into that GROUP BY whole. Postgres then evaluates the subquery once per
JOINED ROW instead of once per outer row. A bare `Subquery` / `Exists` is safe: Django groups it by the
outer columns it references. Aggregates INSIDE a subquery are safe too.

The numbers stay correct, so no functional test notices, and dev's data is too small for the slowdown to
show. `/jobs/` shipped this way in August and hit prod's 15s statement timeout once the contract catalogue
grew (quadratic: 1.2s at 1,000 contracts, 10.4s at 3,000). This guard reads the source instead.

The rule: in a function that annotates a queryset, do not combine an outer aggregate with a wrapped
subquery. Count with a subquery too, or compute one side separately.

Scope (deliberately simple): it follows names assigned within ONE function. A wrapped subquery built in
one function and aggregated in another is not seen; the audit that wrote this checked the existing
helpers' callers by hand.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_NOT_APP_CODE = {'tests', 'venv', 'node_modules', 'staticfiles', 'media', 'docs', 'static', 'templates'}
AGGREGATES = {'Count', 'Sum', 'Avg', 'Max', 'Min', 'StringAgg', 'ArrayAgg', 'StdDev', 'Variance', 'BoolOr', 'BoolAnd'}
SUBQUERIES = {'Subquery', 'Exists'}
_MAX_DEPTH = 6  # how far a local name is followed back to its assignment


def _name(call):
    f = call.func
    return f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None


def _has_outer_ref(node):
    return any(isinstance(n, ast.Call) and _name(n) == 'OuterRef' for n in ast.walk(node))


def _outer_aggregates(node, assigns, depth=0):
    """Line numbers of aggregates NOT inside a Subquery/Exists."""
    if depth > _MAX_DEPTH:
        return []
    if isinstance(node, ast.Call):
        if _name(node) in SUBQUERIES:
            return []
        found = [node.lineno] if _name(node) in AGGREGATES else []
    elif isinstance(node, ast.Name) and node.id in assigns:
        return _outer_aggregates(assigns[node.id], assigns, depth + 1)
    else:
        found = []
    for child in ast.iter_child_nodes(node):
        found += _outer_aggregates(child, assigns, depth)
    return found


def _wrapped_subqueries(node, assigns, wrapped=False, depth=0):
    """Line numbers of Subquery/Exists that sit inside another expression (a call argument or arithmetic)."""
    if depth > _MAX_DEPTH:
        return []
    if isinstance(node, ast.Call):
        if _name(node) in SUBQUERIES:
            return [node.lineno] if wrapped else []
        found = []
        for child in list(node.args) + [k.value for k in node.keywords]:
            found += _wrapped_subqueries(child, assigns, True, depth)
        return found
    if isinstance(node, ast.Name) and node.id in assigns:
        return _wrapped_subqueries(assigns[node.id], assigns, wrapped, depth + 1)
    if isinstance(node, ast.BinOp):
        return (_wrapped_subqueries(node.left, assigns, True, depth)
                + _wrapped_subqueries(node.right, assigns, True, depth))
    found = []
    for child in ast.iter_child_nodes(node):
        found += _wrapped_subqueries(child, assigns, wrapped, depth)
    return found


def offenders_in(source, label='<source>'):
    tree = ast.parse(source)
    found = []
    for func in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        assigns = {st.targets[0].id: st.value for st in ast.walk(func)
                   if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name)}
        aggregates, wrapped = [], []
        for node in ast.walk(func):
            if not (isinstance(node, ast.Call) and _name(node) == 'annotate' and isinstance(node.func, ast.Attribute)):
                continue
            if _has_outer_ref(node.func.value):
                continue  # this annotate builds a subquery's own body
            for kw in node.keywords:
                if kw.arg is not None:
                    aggregates += _outer_aggregates(kw.value, assigns)
                    wrapped += _wrapped_subqueries(kw.value, assigns)
        if aggregates and wrapped:
            found.append(f'{label}:{func.lineno} {func.name}: aggregate at {sorted(set(aggregates))}, '
                         f'wrapped subquery at {sorted(set(wrapped))}')
    return found


def _app_sources():
    packages = [p.parent for p in ROOT.glob('*/__init__.py') if p.parent.name not in _NOT_APP_CODE]
    return sorted(p for pkg in packages for p in pkg.rglob('*.py')
                  if 'migrations' not in p.parts and 'tests' not in p.parts)


def test_no_wrapped_subquery_sits_beside_an_outer_aggregate():
    sources = _app_sources()
    assert len(sources) > 100, 'the guard reads no app code; did the packages move?'
    found = []
    for path in sources:
        found += offenders_in(path.read_text(encoding='utf-8'), path.relative_to(ROOT).as_posix())
    assert not found, (
        'A wrapped subquery beside an outer aggregate is evaluated once per JOINED ROW (see this file\'s '
        'docstring). Make the aggregate a subquery too, or compute it separately:\n' + '\n'.join(found))


def test_the_scanner_flags_the_outage_shape_and_nothing_safe():
    """Pin the scanner itself, on the exact shape that took `/jobs/` down and on its safe neighbours."""
    outage = (
        "def get_queryset(self):\n"
        "    return Job.objects.annotate(\n"
        "        contract_count=Count('contracts', distinct=True),\n"
        "        xp_supply=Coalesce(Subquery(_job_xp_supply(), output_field=IntegerField()), Value(0)))\n")
    assert offenders_in(outage), 'the scanner no longer sees the shape that caused the outage'

    via_name = (
        "def f():\n"
        "    supply = Subquery(q) * 2\n"
        "    return Job.objects.annotate(n=Count('x')).annotate(s=supply)\n")
    assert offenders_in(via_name), 'a wrapped subquery assigned to a name first must still be seen'

    safe = (
        "def fixed():\n"
        "    return Job.objects.annotate(\n"
        "        c=Coalesce(Subquery(count_q), Value(0)), s=Coalesce(Subquery(sum_q), Value(0)))\n"
        "def bare_subquery_beside_aggregate():\n"
        "    return Job.objects.annotate(n=Count('x'), held=Exists(q), first=Subquery(q2))\n"
        "def aggregate_inside_the_subquery():\n"
        "    inner = Contract.objects.filter(job=OuterRef('pk')).annotate(n=Count('jobs')).values('n')\n"
        "    return Job.objects.annotate(n=Coalesce(Subquery(inner), Value(0)))\n")
    assert offenders_in(safe) == []
