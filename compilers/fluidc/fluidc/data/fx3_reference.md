# fx3 reference (the subset fx3c 0.1 compiles)

fx3 is strict. Write ONLY what this reference shows; anything else is rejected by the compiler. Use the canonical spellings shown here (fx3c accepts some aliases, but canonical code is what you produce).

## Lexical

- Statements end with `;`. Blocks always use `{ }`. Indentation has no meaning.
- Comments: `// line`, `/* block */`. `//` is ALWAYS a comment.
- Names: letters, digits, `_`; case-sensitive. Don't use these keywords as names: function class if else repeat for switch case default stop skip return try catch finally fail defer this super and or not in is true false none type move with.
- Literals: `42`, `1_000_000`, `0xFF`, `3.14`, `50%` (= 0.5), `true`/`false`, `none`, `'a'` (char), `"text"`, `"""multi-line"""`, `r"raw"`, `f"Hi {name}, {price:.2} {count:,} {ratio:%}"`.

## Variables

```
int age = 25;                      // type annotations name = value;
float price = 9.5;
string name = "Sam";
bool ready = true;
char initial = 'S';
auto total = 100;                  // type inferred
int @const MAX_USERS = 1000;       // constant
int @typesafe level = 1;           // type can never change
int @range(0, 150) personAge = 30; // checked on every assignment
int count;                         // declared, must be assigned before it is read
int? maybeAge = none;              // only maybe types (T?) can hold none
int a = 1, b = 2;
```
Simple types: `int` (64-bit), `i8 i16 i32 u8 u16 u32 u64`, `float`, `f32`, `bool`, `char`, `string`, `auto`. Widening is automatic (int -> float); narrowing needs `x as T` (checked), `x as? T` (gives T?), `x as! T` (wraps).

## Operators

`+ - * / ~/ % **` (`/` ALWAYS gives a float; `~/` is floor division and gives an int for ints; `%` is floor modulo), comparisons `== != < <= > >=` (chainable: `0 < x < 10`), `and or not`, `in` / `not in`, `??` (default for none or for a failing call), `?.` (none-safe member), `x ? a : b` and `if (c) a else b` as expressions, ranges `a..b` (end excluded), `a..=b` (included), `a..b step 2`.
- No truthiness: conditions must be bool (`if (count != 0)`, `if (not list.isEmpty())`).
- No automatic number-to-text: use f-strings, not `"age: " + 5`.
- `==` on class objects needs `@derive(eq)`; `===` is identity.
- Compound assignment: `+= -= *= ~/= %= ??=`, and `x++;` / `x--;` as statements. `/=` on an int is an error.

## Collections

```
vector<int> scores = [90, 85];            // one element type
list mixed = [1, "two", 3.5];             // mixed (avoid unless needed)
array<int> @size(3) slots;                // fixed size, zero-filled
vector<int> @size(10) @init(0) zeros;
set<string> tags = {"a", "b"};
map<string, int> ages = {"Sam": 25};
map<string, int> @default(0) counts;      // missing key reads as 0
tuple<string, int> person = ("Sam", 25);  // read with person.0, person.1
vector<vector<int>> grid = [[1, 2], [3]];
vector<int> nums = 1..=10;                // a range becomes a vector
```
Brackets are fixed: `[ ]` lists, `{k: v}` maps, `{a, b}` sets, `( )` tuples. An empty `[]` / `{}` needs a written type. Collections are COPIED on assignment; class objects are SHARED.

Indexing: `v[0]`, `v[-1]`, `v[1..3]` (slice), `m["key"]` (missing key = panic unless @default), `m["key"] = 5;`.

### Method bank (canonical names)

ACTIONS change the collection and return nothing (except pop/popFront). QUERIES never change it.

vector / list / array:
- queries: `size() isEmpty() get(i) getOr(i, default) first() last()` (first/last give T?), `slice(1..3) contains(v) indexOf(v)` (int?), `find(cond)` (T?), `count(v or cond) any(cond) all(cond) min() max()` (T?), `sum() average() filter(cond) map(newVal) reduce(newVal, start = s) sorted(by = key, desc = true) reversed() take(n) skip(n) join(sep = ", ") copy() toSet() enumerate()`
- actions: `add(v) add(v, index = 0) addAll(vs) set(i, v) removeAt(i or range) remove(v) remove(v, all = true) removeWhere(cond) pop() popFront() clear() modifyAt(index = ..., condition = ..., newVal = ...) replace(old, new) fill(v) swap(i, j) sort(by = key, desc = true) reverse() dedupe()`

map: `size isEmpty get(k) getOr(k, default) keys() values() items() contains(k) set(k, v) remove(k) removeWhere(cond) clear() modifyAt(key = k, newVal = ..., default = d) filter(cond) map(newVal) count(cond) any(cond) all(cond) copy()`

set: `size isEmpty add(v) addAll(vs) remove(v) contains(v) clear() union(s) intersect(s) difference(s) isSubset(s) filter map min max sum count any all copy sorted()`

string: `size isEmpty upper lower capitalize trim split(sep) lines words contains startsWith endsWith indexOf(s)` (int?) `replace(a, b) slice(range) repeat(n) reversed padLeft(n, ch) padRight(n, ch) isNumber isAlpha toInt toFloat toBool` (all three give T?) `chars equalsIgnoreCase(s) count(s)`, and `", ".join(names)`. String methods return a NEW string.

maybe (T?): `isNone() isSome() getOr(default) unwrap(msg)`; numbers/bool/char: `toString()`.

### Implicit variables (no lambdas)

Arguments named `condition`, `newVal` and `by` (and a `contains` / `indexOf` / `count` argument) can use `currVal` (the element), `index` (its position), `key` (map key), `prevVal` / `nextVal` (neighbours, may be none) and `acc` (in reduce):
```
scores.modifyAt(index = 2..4, newVal = currVal + 5);
vector<int> passed = scores.filter(currVal >= 40);
int total = scores.reduce(acc + currVal, start = 0);
names.sort(by = currVal.size());
counts.modifyAt(key = word, newVal = currVal + 1);   // with @default(0)
```

### English conditions (deterministic mini-grammar)

Any condition may be a quoted sentence: `if ("x is greater than 10 and x is even")`, `filter("currVal is at least 80")`. Only this grammar is understood: subject (a variable or currVal/index/key) + `is [not]` + one of: a value, `greater than / more than / above / over / less than / below / under / at least / at most / equal to` N, `between A and B`, `even`, `odd`, `positive`, `negative`, `zero`, `empty`, `none`, `divisible by N`; or `contains / starts with / ends with` X; joined by `and` / `or` / `not`. Prefer plain expressions in generated code.

## Control flow

```
if (x > 10) { ... } else if (x > 5) { ... } else { ... }
repeat(5) { ... }                         // 5 times
repeat(int i in 0..10) { ... }            // i = 0..9
repeat(auto s in scores) { ... }
repeat([int i, auto s] in scores.enumerate()) { ... }
repeat([string name, int age] in ages) { ... }   // map: key and value
repeat(count < 10) { ... }                // while
repeat @atleastonce (count < 10) { ... }  // do-while
repeat() { ... stop; }                    // forever, until stop
for (int i = 0; i < n; i++) { ... }       // C style (also repeat(@none; init; cond; step))
stop;   skip;                             // break / continue
switch(@none; code) {
    case(200).act(print("ok"));
    case(301, 302).act(print("moved"));
    case(400..=499).act(
        print("client error");
        retries += 1;
    );
    default.act(print("other"));          // default is REQUIRED (unless the cases are true and false)
}
string label = switch(@none; x) { case(0).act("zero"); default.act("other"); };
```

## Functions

```
function[<int, int>|<int>] add = Function(int m, int n) {
    return m + n;
}
function[<int>|<int>] square = Function(int x) => x * x;
function[<string, int>|<string>] repeatText = Function(string s, int times = 2) { return s * times; }
function[<int, int>|<int, int>] divmod = Function(int a, int b) { return a ~/ b, a % b; }
int [q, r] = divmod(17, 5);                 // unpack several results
function[<>|<>] greet = Function() { print("hi"); }
function[<int...>|<int>] total = Function(int ...nums) { return nums.sum(); }
function[<vector<int> @ref>|<>] addZero = Function(vector<int> @ref v) { v.add(0); }  // may change the caller's list
function[<int>|<int>] @memo fib = Function(int n) { if (n < 2) { return n; } return fib(n - 1) + fib(n - 2); }
function[<int>|<int>] @example(3 -> 9) @example(-2 -> 4) sq = Function(int x) => x * x;   // examples are tests
function[<>|<>] @test addsUp = Function() { ensure(add(2, 3) == 5, "2 + 3 should be 5"); }
function[<>|<>] @entry main = Function() { ... }      // program entry (at most one)
```
- The signature `[<inputs>|<outputs>]` must match the parameters; every path must return.
- Calls: positional then named: `repeatText("ha", times = 3)`.
- Parameters are read-only (use `@ref` on a collection parameter to change it).
- A function may change a top-level variable.
- Functions can be used before they are written. Top-level statements run in order.
- `@example(input -> output)`: for several inputs write `@example((2, 3) -> 5)`.
- A function can be passed as a value: by name (`s.get("/", hello)`), or written in place, either as
  `Function(int x) => x * 2` or with a body: `db.transaction(Function() { ... })`. A function written in
  place may change the variables around it.

## Classes

```
class @derive(eq, print) Point = Class() {
    int @public x;                  // fields are PRIVATE unless @public
    int @public y = 0;              // has a default -> optional constructor argument
    function[<Point>|<Point>] plus = Function(Point o) => Point(this.x + o.x, this.y + o.y);
}
Point p = Point(1, 2);              // generated constructor: the fields in order

class Account = Class() {
    string @public owner;
    float balance = 0.0;
    vector<string> history;         // collections start empty
    int @static @public count = 0;  // shared by the class: Account.count
    function[<float>|<>] deposit = Function(float amount) {
        ensure(amount > 0, "deposits must be positive");
        this.balance += amount;
        this.history.add(f"+{amount}");
        Account.count += 1;
    }
    function[<>|<float>] getBalance = Function() { return this.balance; }
    function[<>|<float>] @property doubled = Function() => this.balance * 2;   // read as acc.doubled
}

class Temperature = Class() {
    float @public celsius;
    function[<float>|<>] @constructor @style('c') fromC = Function(float c) { this.celsius = c; }
    function[<float>|<>] @constructor @style('f') fromF = Function(float f) { this.celsius = (f - 32) * 5 / 9; }
}
Temperature @using('f') t = Temperature(98.6);    // or Temperature.fromC(37.0)

class @open @abstract Animal = Class() {          // @open = can be inherited from
    string @public name;
    function[<>|<string>] @abstract sound;
}
class Dog = Class(Animal) {
    function[<>|<string>] @override sound = Function() => "woof";
}
```
`@derive(...)`: eq, hash, order, print. `this` is never a parameter. No interfaces, no generics.

## Errors

```
function[<string>|<int>] @fails(ParseError) parseAge = Function(string s) {
    int? n = s.toInt();
    if (n == none) { fail ParseError(f"'{s}' is not a number"); }
    return n;                               // n is narrowed to int after the check
}
int a = parseAge("42") ?? 0;                // default on failure
try { int b = parseAge(x); } catch (ParseError e) { print(e.message); } finally { ... }
function[<string>|<int>] @fails(ParseError) twice = Function(string s) { return parseAge(s)? * 2; }  // pass it on
result<int> r = parseAge("x");              // r.isOk() r.isError() r.value r.error.message
class @error NotEnoughMoney = Class(Error) { float @public needed; }
fail NotEnoughMoney(message = "balance too low", needed = 20.0);
ensure(cond, "message");   panic("message");     // bugs: stop the program
defer { ... }                               // runs when the block ends
```
Every call to an `@fails` function MUST be handled (try/catch, `??`, `?` inside an @fails function, or `result<T>`). Built-in errors: Error, ValueError, ParseError, IOError, FileNotFound, NetworkError, TimeoutError, FormatError.

## none-safety

```
int? m = "12".toInt();                     // (m["k"] on a missing map key panics: use getOr)
int v = m ?? 0;
if (m != none) { print(m + 1); }             // narrowed inside
if (auto found = m) { print(found); }        // bind if present
if (m != none and m > 3) { ... }             // narrowed on the right of `and`
string up = nick?.upper() ?? "none";
```

## Built-in functions

`print(a, b, sep = ", ", end = "")` (prints true/false/none and collections readably), `input("prompt")` (string), `len(x)`, `min(a, b)`, `max(a, b)`, `abs(x)`, `round(x)` (half away from zero), `round(x, 2)`, `floor(x)`, `ceil(x)`, `range(a, b)`, `string(x)`, `panic(msg)`, `ensure(cond, msg)`, `exit(code)`.

## Durations

`500ms  2s  5m  1h  1d` have type `duration`: `d + d`, `d * 2`, `d / 2`, compare with `<`; `.seconds() .millis() .minutes() .hours()`; `sleep(2s);`. Printed as `1m 30s`.

## Using the standard library

Import modules at the very top of the file (`import Server;`). The full list of functions is in the next section. Typical patterns:
```
import Server;
import Db;
import Env;

function[<Request>|<Response>] listItems = Function(Request req) {   // a handler
    return Response.json(["a", "b"]);
}
function[<Request>|<Response>] @fails(DbError) addItem = Function(Request req) {
    Json body = req.json() ?? Json.of(none);
    string name = body.get("name").asString() ?? "";
    if (name == "") { return Response.error("name is required", status = 400); }
    return Response.json({"added": name}, status = 201);
}
function[<>|<>] @entry main = Function() {
    string key = Env.get("API_KEY") ?? "";                // secrets come from the environment / secrets.env
    Server s = Server.create(port = 8080);
    s.get("/items", listItems);
    s.post("/items", addItem);                            // a handler that fails answers 500 with {"error": ...}
    s.keepRunning();
}
```
- A failing call used as a statement still needs handling: `File.write("x.txt", text)?;` inside an @fails function, or try/catch.
- `Db`: `Db db = Db.open("app.db")?;`, then `db.exec("insert into t (a) values (?)", [x])?;` and `vector<map<string, Json>> rows = db.query("select a from t")?;`, reading cells with `rows[0]["a"].asInt() ?? 0`.
- Std values are made with functions (`Time.now()`, `Server.create()`), never `Time()`.

## NOT available in fx3c 0.1 (never write these)

scheduling (`every ...`), async / await, Process; `Action:` bodies; loopers / checkers / pipelines (`repeat(x; ...)`, `switch(x; ...)`); enums; interfaces; generics; type aliases; `@unsafe`, pointers; regex; lambdas `x => ...` (use implicit variables, or `Function(int x) => x * 2` as a value); `@label`; `repeat @parallel`; inner functions.
