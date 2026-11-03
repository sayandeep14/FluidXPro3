"""Interfaces (Syntax/classes.fx3 section 6; ROADMAP E-33 Phase 2)."""

from __future__ import annotations

import pytest

from helpers import error_codes

DRAWABLE = """interface Drawable = Interface {
    function[<>|<string>] draw;
    function[<>|<string>] label = Function() => "shape";
}
"""


@pytest.mark.parametrize("cls,code", [
    ("class Box = Class(implements = [Drawable]) { int n; }", "FX0746"),                    # draw missing
    ("class Box = Class(implements = [Widget]) { int n; }", "FX0746"),                      # unknown
    ("class P = Class() { int n; }\nclass Box = Class(implements = [P]) { int n; }", "FX0746"),  # a class
    ("class Box = Class(implements = [Drawable]) {\n"
     "    function[<int>|<string>] draw = Function(int n) => \"x\";\n}", "FX0747"),           # other inputs
    ("class Box = Class(implements = [Drawable]) {\n"
     "    function[<>|<int>] draw = Function() => 1;\n}", "FX0747"),                         # other output
])
def test_implementing_errors(cls, code):
    assert code in error_codes(DRAWABLE + cls + "\n")


def test_an_interface_is_a_type_but_not_a_class():
    ok = DRAWABLE + """class Box = Class(implements = [Drawable]) {
    function[<>|<string>] draw = Function() => "box";
}
function[<>|<>] @entry main = Function() {
    Drawable d = Box();
    print(d.draw(), d.label());
}
"""
    assert error_codes(ok) == []
    assert "FX0748" in error_codes(ok.replace("Drawable d = Box();", "Drawable d = Drawable();"))
    # interfaces hold methods only
    assert error_codes("interface I = Interface { int n; }\n")


def test_defaults_can_be_replaced():
    src = DRAWABLE + """class Box = Class(implements = [Drawable]) {
    function[<>|<string>] draw = Function() => "box";
    function[<>|<string>] label = Function() => "my box";
}
"""
    assert error_codes(src) == []
