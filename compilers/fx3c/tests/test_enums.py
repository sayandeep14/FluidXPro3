"""Enums and patterns (Syntax/enums.fx3; ROADMAP E-33 Phase 2)."""

from __future__ import annotations

import pytest

from helpers import error_codes, run_fx3

SHAPE = """enum Shape = Enum { Circle(float radius), Rect(float w, float h), Point }
"""


def test_switch_must_cover_every_variant():
    src = SHAPE + """function[<Shape>|<int>] f = Function(Shape s) {
    return switch(@none; s) { case(Circle(r)).act(1); case(Point).act(2); };
}
"""
    assert error_codes(src) == ["FX0514"]
    # a case with `where` doesn't count as covering its variant
    guarded = src.replace("case(Point).act(2);", "case(Point).act(2); case(Rect(w, h) where w > h).act(3);")
    assert error_codes(guarded) == ["FX0514"]
    covered = src.replace("case(Point).act(2);", "case(Point).act(2); case(Rect(_, _)).act(3);")
    assert error_codes(covered) == []
    assert error_codes(src.replace("case(Point).act(2);", "case(_).act(2);")) == []


@pytest.mark.parametrize("body,code", [
    ("Shape s = Shape(1.0);", "FX0738"),                     # values are the variants
    ("Shape s = Shape.Square;", "FX0739"),                   # no such variant
    ("Shape s = Shape.Circle;", "FX0740"),                   # Circle carries a radius
    ("Shape s = Shape.Point(1.0);", "FX0740"),               # Point carries nothing
    ("string n = Shape.Point.value();", "FX0743"),           # no raw values
    ("vector<Shape> all = Shape.all();", "FX0741"),          # all() needs variants without fields
    ("Shape s = Circle(\"big\");", "FX0401"),                # payload types are checked
])
def test_enum_errors(body, code):
    src = SHAPE + "function[<>|<>] @entry main = Function() { " + body + " }\n"
    assert code in error_codes(src)


def test_patterns_name_the_right_number_of_fields():
    src = SHAPE + """function[<Shape>|<float>] f = Function(Shape s) {
    return switch(@none; s) { case(Rect(w)).act(w); default.act(0.0); };
}
"""
    assert error_codes(src) == ["FX0744"]


@pytest.mark.parametrize("decl,code", [
    ("enum Color = Enum { Red, Red }", "FX0732"),
    ("enum<int> Code = Enum { A = 1, B = 1 }", "FX0736"),
    ("enum<int> Code = Enum { A = \"x\" }", "FX0735"),
    ("enum Code = Enum { A = 1 }", "FX0734"),
    ("enum<float> Code = Enum { A }", "FX0731"),
    ("enum @flags Perm = Enum { Read, Write }", "FX1001"),
])
def test_declaration_errors(decl, code):
    assert code in error_codes(decl + "\n")


def test_enums_in_collections_and_maps():
    src = """enum Suit = Enum { Hearts, Spades }
function[<>|<>] @entry main = Function() {
    map<Suit, int> count = {Suit.Hearts: 2};
    count[Suit.Spades] = 1;
    print(count, count[Suit.Hearts]);
    Suit? none_yet = Suit.from("Clubs");
    print(none_yet ?? Suit.Hearts);
}
"""
    proc = run_fx3(src)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == "{Suit.Hearts: 2, Suit.Spades: 1} 2\nSuit.Hearts\n"
