"""Sets keep insertion order on every target; @unordered opts out (Syntax/targets.fx3 2d)."""

from __future__ import annotations

from helpers import error_codes, run_fx3


def test_sets_keep_insertion_order_everywhere():
    src = """function[<>|<>] @entry main = Function() {
    set<string> tags = {"new", "hot", "sale"};
    tags.add("alpha");
    tags.add("hot");                      // already there: stays where it was
    print(tags);
    tags.remove("new");
    tags.add("new");                      // removed and added again: now last
    print(tags, tags.size());
    set<string> more = {"zeta", "hot"};
    print(tags.union(more), tags.intersect(more), tags.difference(more));
    repeat(auto t in tags) { print(t); }
    vector<int> xs = [5, 3, 5, 1, 3];
    print(xs.toSet());
    set<int> @unordered fast = {3, 1, 2};
    print(fast.size(), fast.contains(2));
}
"""
    assert error_codes(src) == []
    proc = run_fx3(src)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [
        '{"new", "hot", "sale", "alpha"}',
        '{"hot", "sale", "alpha", "new"} 4',
        '{"hot", "sale", "alpha", "new", "zeta"} {"hot"} {"sale", "alpha", "new"}',
        "hot", "sale", "alpha", "new",
        "{5, 3, 1}",
        "3 true",
    ]


def test_ordered_is_accepted_and_no_longer_warns():
    src = 'function[<>|<>] @entry main = Function() { set<int> @ordered s = {2, 1}; print(s); }\n'
    from fx3c.driver import compile_source
    res = compile_source(src, "t.fx3", check_only=True)
    assert not res.diags.items
