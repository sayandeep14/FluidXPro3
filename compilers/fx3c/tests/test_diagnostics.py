"""The deterministic checks: each bad snippet must report its code; each good one must compile."""

from __future__ import annotations

import pytest

from helpers import codes, error_codes

BAD = {
    "const reassign": ("int @const L = 1; L = 2;", "FX0418"),
    "typesafe change": ('int @typesafe y = 5; y = "five";', "FX0419"),
    "loose change in branch": ('int x = 5; if (true) { x = "five"; } print(x);', "FX0420"),
    "u8 literal too big": ("u8 x = 300;", "FX0434"),
    "none into int": ("int x = none;", "FX0401"),
    "maybe arithmetic": ("int? x = none; int y = x + 1;", "FX0443"),
    "no truthiness": ("int c = 1; if (c) { print(c); }", "FX0428"),
    "use before assign": ("int x; print(x);", "FX0302"),
    "assigned in one branch only": ("int x; if (true) { x = 1; } print(x);", "FX0302"),
    "vector holds one type": ('vector<int> v = [1, "x"];', "FX0435"),
    "{} for a vector": ("vector<int> v = {1, 2};", "FX0436"),
    "[] for a set": ("set<int> s = [1, 2];", "FX0436"),
    "duplicate map key": ('map<string,int> m = {"a": 1, "a": 2};', "FX0438"),
    "unknown method": ("vector<int> v = [1]; v.frobnicate();", "FX0801"),
    "typo method": ("vector<int> v = [1]; print(v.lenght());", "FX0802"),
    "typo type": ('strng s = "a"; print(s);', "FX0404"),
    "unhandled @fails call": ('function[<string>|<int>] @fails(ParseError) p = Function(string s) '
                              '{ fail ParseError("x"); } int x = p("1");', "FX0625"),
    "fail not declared": ('function[<>|<>] f = Function() { fail ParseError("x"); }', "FX0620"),
    "? outside @fails": ('function[<string>|<int>] @fails(ParseError) p = Function(string s) { return 1; } '
                         'function[<>|<int>] g = Function() { return p("1")?; }', "FX0623"),
    "missing return": ("function[<int>|<int>] f = Function(int a) { if (a > 0) { return 1; } }", "FX0611"),
    "stop outside loop": ("stop;", "FX0510"),
    "switch without default": ("int x = 1; switch(@none; x) { case(1).act(print(1)); }", "FX0514"),
    "private field": ("class A = Class() { int secret = 1; } A a = A(); print(a.secret);", "FX0717"),
    "@override missing": ("class @open A = Class() { function[<>|<int>] f = Function() => 1; } "
                          "class B = Class(A) { function[<>|<int>] f = Function() => 2; }", "FX0707"),
    "parent not @open": ("class A = Class() { } class B = Class(A) { }", "FX0704"),
    "== without @derive(eq)": ("class A = Class() { int @public x = 1; } print(A() == A());", "FX0712"),
    "string + int": ('print("age: " + 5);', "FX0444"),
    "int /=": ("int x = 4; x /= 2;", "FX0422"),
    "changing a read-only parameter": ("function[<vector<int>>|<>] f = Function(vector<int> v) { v.add(1); }",
                                       "FX0426"),
    "unused query result": ("vector<int> v = [2, 1]; v.sorted();", "FX0508"),
    "English outside the grammar": ('int x = 1; if ("x looks purple") { print(x); }', "FX0901"),
    "comparing different types": ('print(5 == "5");', "FX0452"),
    "missing semicolon": ("int x = 1\nprint(x);", "FX0202"),
    "if body without braces": ("int x = 1; if (x > 0) print(x);", "FX0201"),
    "too many arguments": ("function[<int>|<int>] f = Function(int a) => a; print(f(1, 2));", "FX0464"),
    "named argument typo": ("function[<int>|<int>] f = Function(int amount) => amount; print(f(amont = 2));",
                            "FX0467"),
    "unpack count": ("auto [a, b, c] = (1, 2);", "FX0414"),
    "Action body needs the LLM": ('function[<int>|<int>] f = Function(int a) { Action: "double it" }', "FX0905"),
    "keyword as a name": ("int repeat = 3;", "FX0303"),
    "@init without @size": ("vector<int> @init(0) v;", "FX0503"),
    "unknown annotation": ("int @wibble x = 1;", "FX0502"),
    "annotation typo": ("int @typsafe x = 1;", "FX0501"),
    "@range literal": ("int @range(0, 10) x = 11;", "FX0411"),
    "^ is not power": ("print(2 ^ 3);", "FX0442"),
    "signed/unsigned mix": ("i32 a = 1; u32 b = 2; print(a + b);", "FX0447"),
    "constructor-style args": ("vector<int> a(10, 0);", "FX0201"),
}

GOOD = {
    "loose type change": 'int x = 5; x = "five"; print(x);',
    "assigned in both branches": "int x; if (true) { x = 1; } else { x = 2; } print(x);",
    "narrowed after an early return": ("function[<string>|<int>] f = Function(string s) "
                                       "{ int? n = s.toInt(); if (n == none) { return 0; } return n + 1; }"),
    "narrowed inside and": 'int? n = "3".toInt(); if (n != none and n > 2) { print(n); }',
    "?? on an @fails call": ('function[<string>|<int>] @fails(ParseError) p = Function(string s) '
                             '{ fail ParseError("x"); } int x = p("1") ?? 0; print(x);'),
    "chained comparison": "int a = 5; print(0 < a < 10);",
    "widening int to float": "float f = 3; i32 small = 4; int big = small; print(f, big);",
}


@pytest.mark.parametrize("name", list(BAD))
def test_bad_snippet_reports(name: str):
    src, code = BAD[name]
    assert code in codes(src), codes(src)


@pytest.mark.parametrize("name", list(GOOD))
def test_good_snippet_compiles(name: str):
    assert error_codes(GOOD[name]) == []
