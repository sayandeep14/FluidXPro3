"""Generics (Syntax/types.fx3 section 7; ROADMAP E-33 Phase 2)."""

from __future__ import annotations

import pytest

from helpers import error_codes

POINT = "class Point = Class() { int x; }\n"


@pytest.mark.parametrize("src,code", [
    # constraints are checked at the call...
    (POINT + "function[<T, T>|<T>] @generic(T: comparable) larger = Function(T a, T b) => if (a > b) a else b;\n"
     "function[<>|<>] @entry main = Function() { Point p = larger(Point(1), Point(2)); }", "FX0753"),
    # ... and inside the body: only what the constraints allow
    ("function[<T, T>|<bool>] @generic(T) less = Function(T a, T b) => a < b;", "FX0753"),
    ("function[<T, T>|<T>] @generic(T: comparable) add = Function(T a, T b) => a + b;", "FX0753"),
    ("function[<T, T>|<bool>] @generic(T) same = Function(T a, T b) => a == b;", "FX0753"),
    ("function[<T>|<string>] @generic(T) show = Function(T a) => a.name();", "FX0754"),
    ("function[<T>|<int>] @generic(T: sortable) f = Function(T a) => 1;", "FX0752"),
    ("class @generic(K, V) Reg = Class() { map<K, V> items; }", "FX0753"),          # K must be hashable
    # generic classes need their types
    ("class @generic(T) Box = Class() { T value; }\n"
     "function[<Box>|<int>] f = Function(Box b) => 1;", "FX0750"),
    ("class @generic(T) Box = Class() { T value; }\n"
     "function[<Box<int, int>>|<int>] f = Function(Box<int, int> b) => 1;", "FX0750"),
    (POINT + "class @generic(K: hashable) Bag = Class() { set<K> items; }\n"
     "function[<Bag<Point>>|<int>] f = Function(Bag<Point> b) => 1;", "FX0753"),
    ("function[<>|<int>] f = Function() => 1;\nclass Box = Class() { int v; }\n"
     "function[<Box<int>>|<int>] g = Function(Box<int> b) => 1;", "FX0403"),   # Box isn't generic
])
def test_generic_errors(src, code):
    assert code in error_codes(src + "\n")


def test_type_parameters_that_cant_be_inferred_need_a_type():
    gen = "function[<>|<vector<T>>] @generic(T) none_yet = Function() { vector<T> v = []; return v; }\n"
    assert "FX0751" in error_codes(gen + "function[<>|<>] @entry main = Function() { auto x = none_yet(); }\n")
    assert error_codes(gen + "function[<>|<>] @entry main = Function() { vector<int> x = none_yet(); "
                             "print(x); }\n") == []


def test_wrong_argument_types_are_still_errors():
    src = ("function[<T, T>|<T>] @generic(T: comparable) larger = Function(T a, T b) => if (a > b) a else b;\n"
           "function[<>|<>] @entry main = Function() { print(larger(1, \"x\")); }\n")
    assert "FX0401" in error_codes(src)
