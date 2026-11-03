# E-33 plan: one fx3 program, five target languages

**Status:** Phase 0 (the spec) is in progress. Tick the boxes as phases land and log them in ROADMAP.md.

**Decisions (2026-11-03):**
- **Backend order:** Go, Java, C++, Rust.
- **fx3 is target-neutral:** switching targets needs no model call.
- **The `@native` / `@extern` escape hatch** exists, and it's reviewed like `@unsafe`.
- **Maps and sets** keep insertion order by default on every target; `@unordered` opts out.

**Goal:** the same `.fluid` / `.fx3` program builds to Python, Go, Java, C++ or Rust.
- Each output should be idiomatic for its language: goroutines in Go, Spring Boot in Java, ownership and `Result` in Rust, RAII and references in C++.
- Each output must behave the same as the others.

---

## 1. Principles

1. **One fx3 program, the same behaviour everywhere.**
   - "The same" is defined by a conformance suite (E-17): the same output, the same failures and the same exit codes on every target.
   - A backend isn't released until it passes.
2. **fx3 is target-neutral.**
   - The model never sees the target language, so switching from Python to Rust needs no model call: `fluid build --target rust --offline` works from the same `fluid.lock`.
   - The `target.*` settings go to the backend, not to the model.
3. **Language strengths come from portable intent, not target syntax.**
   - fx3 says what is meant: "these tasks run at the same time and all must finish", "one shared instance of this service", "this field must be 1–150".
   - Each backend lowers that to its own idiom (table in §3).
   - A program written once gets Spring annotations in Java, goroutines in Go and borrow-checked code in Rust.
4. **An escape hatch exists, and it's visible.**
   - Native code or annotations for one target (`@native`, §4.9) are allowed only where portable intent can't express something.
   - They're listed by `fx3 check --native` and reviewed like `@unsafe`.
5. **A real IR between the checker and the backends.**
   - The spec already promises `.fx3 → IR → target`. Today the Python emitter reads the checked AST directly.
   - Four more backends on top of the AST would repeat the hard work four times: copies, error flow, closures, the method bank.
   - The IR does that work once.

---

## 2. What the spec already has (no new syntax needed)

The language files were designed with these targets in mind. These existing features map directly:

| fx3 (already in `Syntax/`) | Go | Java | C++ | Rust |
|---|---|---|---|---|
| `int` (i64, overflow is an error) | `int64` + checked helpers | `long` + `Math.addExact` | `int64_t` + `__builtin_*_overflow` | `i64` + `checked_*` |
| `maybe<T>` / `T?` | `fx.Option[T]` | `Optional<T>` / nullable | `std::optional<T>` | `Option<T>` |
| `@fails` / `?` / `try` | `(T, error)` | exceptions | `fx::Result<T>` (`std::expected` on C++23) | `Result<T, E>` + `?` |
| `panic` | `panic` | `throw new FxPanic` (an `Error`) | `fx::panic` → abort | `panic!` |
| class (shared object) | `*T` | object reference | `std::shared_ptr<T>` | `Rc<RefCell<T>>` (`Arc<Mutex<T>>` if shared across tasks) |
| `@value` class | struct value | `record` | struct value | `#[derive(Clone)] struct` |
| interface | interface | interface | abstract class / concept | trait |
| `@generic(T: comparable)` | `[T cmp.Ordered]` | `<T extends Comparable<T>>` | template + concept | `<T: Ord>` |
| enum with data | sealed interface + structs | `sealed interface` + records | `std::variant` | `enum` |
| `switch` patterns | type switch | `switch` with patterns (Java 21) | `std::visit` | `match` |
| parameter default (read-only view) | value or pointer | reference | `const T&` | `&T` |
| `@ref` parameter | pointer | (objects are references) | `T&` | `&mut T` |
| `@move` / `move(x)` | value | value | `T&&` / `std::move` | by value |
| `box` / `shared` / `weak` | `*T` / `*T` / weak helper | refs / `WeakReference` | `unique_ptr` / `shared_ptr` / `weak_ptr` | `Box` / `Rc` / `Weak` |
| `ptr<T>` in `@unsafe` | `unsafe.Pointer` | not available (FX10xx) | raw pointer | `*mut T` in `unsafe {}` |
| `spawn` / `task<T>` / `await` | goroutine + result channel | virtual thread + `Future` | `std::jthread` + future | thread or tokio task + `JoinHandle` |
| `channel<T> @capacity(n)` | `chan T` (buffered) | `ArrayBlockingQueue` | runtime channel | `mpsc` / `crossbeam` / tokio `mpsc` |
| `locked<T>` + `with` | `sync.Mutex` + `defer Unlock` | `ReentrantLock` | `std::mutex` + `lock_guard` | `Mutex<T>` guard |
| `atomic<int>` | `atomic.Int64` | `AtomicLong` | `std::atomic<int64_t>` | `AtomicI64` |
| `defer` | `defer` | `try/finally` | scope guard | `Drop` guard |
| RAII resources (`File`) | `defer f.Close()` | try-with-resources | destructor | `Drop` |

So the C++ pointer, reference and RAII story and the Rust ownership story are already in fx3 (`memory.fx3`), and so is Go-style concurrency (`concurrency.fx3`). What's missing is in the compiler, not the spec. E-09 lists the gaps: enums, interfaces, generics, async, channels, locked and atomic aren't compiled yet. Those become prerequisites (Phase 2).

---

## 3. Portable intent → idiomatic code (what makes each target "feel native")

| Intent in fx3 | Java | Go | Rust | C++ |
|---|---|---|---|---|
| `web` block / `Server` | Spring Boot `@RestController`, `@GetMapping` (default; or the JDK `HttpServer`) | `net/http` + `ServeMux` | axum + tokio | cpp-httplib |
| `Db` | Spring `JdbcTemplate` (or plain JDBC) | `database/sql` + pure-Go SQLite | rusqlite (sqlx with async) | sqlite3 C API wrapper |
| `@transactional` (new, §4.5) | `@Transactional` | `tx` helper with `defer` rollback | `conn.transaction()` closure | RAII transaction guard |
| `@service` class (new, §4.3) | `@Service` + constructor injection | struct built in `main`, passed explicitly | field of an `AppState` passed to handlers | owned by `main`, passed by reference |
| validated fields (new, §4.4) | Jakarta Validation `@Min`, `@Size`, `@Pattern`, `@Email` + `@Valid` | generated `Validate()` | generated `validate()` (or the `validator` crate) | generated `validate()` |
| `every` / `Schedule` | `@Scheduled` | `time.Ticker` goroutine | tokio interval | thread + `sleep_until` |
| `group { spawn … }` (new, §4.1) | `StructuredTaskScope` | `errgroup.Group` | `thread::scope` / `JoinSet` | `std::jthread`s joined at scope end |
| `select` (new, §4.2) | runtime helper | `select` | `tokio::select!` / `crossbeam::select!` | runtime helper |
| `secrets` / `settings` | `application.properties` + `@Value` | env + struct | env + struct | env + struct |
| `@derive(eq, hash, print, json)` | `record` + Jackson | struct tags + `encoding/json` | `#[derive(PartialEq, Hash, Debug, Serialize)]` | generated operators + JSON |

---

## 4. New fx3 constructs (proposed)

Each one is portable: it has a meaning of its own, it runs on Python too, and it lowers to the idiom in §3. Each lands in `Syntax/*.fx3` first, then in fx3c and the Python backend, then in the fluidc reference and prompt, so the model can use it.

### 4.1 `group { }`: structured concurrency (concurrency.fx3)
```fx3
group {
    spawn fetchPrices();
    spawn fetchStock();
}                                   // waits for both; if one fails, the other is cancelled and the error goes up
```
- **Why:** this is the safe shape of concurrency. Tasks inside a `group` may borrow local variables read-only, because the group can't outlive them. In Rust that's `thread::scope`, which no other fx3 form can express. Go's `errgroup` and Java's `StructuredTaskScope` are the same idea.
- **Checks:** a task in a group may not change a captured local unless it's `locked` or `atomic` (the existing race rule).

### 4.2 `select`: wait for whichever is ready first (concurrency.fx3)
```fx3
select {
    case (string job = jobs.receive()) { handle(job); }
    case (Event e = events.receive())  { log(e); }
    case (after(5s))                    { print("idle"); }
}
```
- **Why:** this is Go's core concurrency tool, and it can't be written today without busy-waiting.

### 4.3 `@service`: one shared instance per program, injected (classes.fx3)
```fx3
function[<>|<Db>] @provides @fails(IOError) appDb = Function() { return Db.open(DB_PATH)?; }
class @service Inventory = Class() { Db db; ... }                  // Db is injected from appDb()
class @service Orders = Class() { Inventory inventory; Db db; ... } // the one Inventory, the same Db
```
- `Class(...)` names a parent class in fx3, so injected dependencies are fields.
- Things that aren't services come from `@provides` functions (Spring's `@Bean`).
- **Why:** it's what makes Spring Boot code natural, and it gives the other targets a clean "app state" wiring.
- **How:** the compiler builds the dependency graph. A cycle is an error. Services are created in order at start and passed to `web` handlers and other services.
- **Differs from `@singleton`:** a `@singleton` is a global you reach for. A `@service` is handed to you.

### 4.4 Field constraints, checked at the boundary (classes.fx3, variables.fx3)
```fx3
class @value @derive(json) NewOrder = Class() {
    string @length(1, 80) item;
    int @range(1, 1000000) amountCents;
    string @email contact;
}
```
- **What:** `@range` already exists for variables. This extends it to class fields and adds `@length`, `@pattern`, `@email` and `@notEmpty`.
- **When the check runs:** whenever a value is built from outside: `Json` decoding, a web request body, `fromJson`. A failure is a `ValidationError` (a 400 in `web`).
- **Also fixes:** the gpt-5 build gave a 500 for a bad body. This rule makes a bad request a 400 on every target, without the model having to remember.

### 4.5 `@transactional` (stdlib.fx3, Db)
```fx3
function[<Db, string, int>|<int>] @transactional @fails(DbError) placeOrder = Function(Db db, string item, int amount) { ... }
```
- **What:** the whole body runs in one transaction. It's committed on return and rolled back on failure or panic.
- **Relation to today:** it's sugar for `db.transaction(job)`, which exists. In Java it becomes `@Transactional`.

### 4.6 References can't escape (memory.fx3, a new lock)
- `ref<T>` can be a parameter or a local, never a return value or a class field.
- Functions return values. Copies are cheap thanks to copy-on-write and moves.
- **Why:** Rust then never needs lifetime annotations, C++ never returns a dangling reference, and Go and Java are unaffected. This only writes down what "a reference can't outlive its target" already implies.

### 4.7 Closures: capture rules (constructs.fx3)
- A function value captures variables read-only, by default.
- If it changes a captured variable, that variable is stored in a shared cell. This is decided by the compiler, never written by hand.
- **Why:** this is how Rust (`Rc<RefCell>` / `move`), C++ (`[&]` vs `[=]`), Java (effectively final) and Go (free) can all express it. The Python backend's `nonlocal` hoisting (E-27) already does this.

### 4.8 Task model (concurrency.fx3, a clarified lock)
- Tasks may run in parallel on OS or virtual threads.
- There is no ordering between tasks except through `await`, channels, `locked` and `atomic`.
- `@async` is about waiting on I/O, not a different kind of function on targets without async (Go, Java): there, an `@async` function is a plain function, `spawn` starts a goroutine or virtual thread, and `await` waits for it.
- **Rust:** tokio when the program uses `Server` or `@async`, otherwise `std::thread`.
- **C++:** `std::jthread`.

### 4.9 `@native`: the escape hatch (modules.fx3) (optional; see question 3)
```fx3
function[<string>|<User?>] @native(java = "@Cacheable(\"users\")") findUser = Function(string id) { ... }
function[<bytes>|<string>] @extern hashBlake3 = Native(rust = "blake3::hash", go = "native/blake3.go:Hash");
```
- `@native(target = "...")` adds a target annotation or attribute and is ignored by other targets. `@extern` functions have a body per target.
- **Rules:**
  - A target without a body is an error when building for that target.
  - Every use counts as `@unsafe` for review.
  - The model may only write one if the `.fluid` text asks for it.

### 4.10 Spec locks that portability needs (new `Syntax/targets.fx3`, the portability contract)
- **Numbers:**
  - `int` is i64 everywhere, and overflow is an error everywhere.
  - `~/` and `%` are floor division and floor modulo. Go, Java, C++ and Rust truncate instead, so they use helpers.
  - Indexes are `int`: Rust converts to `usize` with a check.
- **Printing:** one canonical format for every value: floats in their shortest round-trip form with `.0` for whole numbers, as Python does (`1.0`, `0.1`, `1e+16`), plus the existing formats for collections, `maybe`, records and enums. Each target's runtime has one `fx_print`.
- **Strings:** indexing counts characters (already locked). On UTF-8 targets that's O(n), with an ASCII fast path.
- **Maps and sets:** iterate in insertion order by default on every target. This changes today's rule, where a map is unordered unless `@ordered`; see question 4.
  - **Why:** Python already does this, so tests and printed output would otherwise differ between targets.
  - `@unordered` (or `@fast`) opts out where order doesn't matter.
- **Memory:** "no garbage collector" becomes "resources are released at a known point (end of scope / last owner)".
  - Memory itself is reclaimed the target's way: reference counting in C++, Rust and Python; a garbage collector in Go and Java.
  - `weak` keeps its meaning everywhere.
- **Panics:** the same message format (`panic: … (file.fx3:line, block "…")`) on stderr and the same exit code on every target. Panics inside tasks behave the same way too.
- **Copies:** assignment and passing keep value semantics on every target.
  - In Go, Java and Python, lists and maps are references, so the backend copies, unless the IR proves the source is never used again (then it moves).

### 4.11 The `.fluid` layer
- `target` accepts `python`, `go`, `java`, `cpp` and `rust`, with aliases such as `golang`, `c++` and `rs`. `fluid build --target go` overrides it for one build, offline from the same lock (principle 2).
- **A new block keyword, `service`.** It describes a long-lived part of the app with its own state ("service Inventory: keeps the stock counts in the database"), and fluidc turns it into a `@service` class.
- **No other new keywords.** "At the same time", "in the background", "wait for all of them" and "whichever comes first" are prose that the prompt teaches the model to turn into `group`, `spawn` and `select`.
- **New `fluid.config` sections:**

  | Section | Settings |
  |---|---|
  | `target.go` | version, module path, web (`net/http` · `chi`) |
  | `target.java` | version (21), build tool (gradle · maven), web (`spring-boot` · `jdk`), db (`jdbc-template` · `jdbc`), package |
  | `target.rust` | edition, async runtime (`tokio` · `none`), web (`axum`), db (`rusqlite` · `sqlx`) |
  | `target.cpp` | already designed; gains `web` (`httplib`) and `db` (`sqlite3`) |

  All of them stay out of the lock key.

---

## 5. Architecture

```
.fluid ──fluidc──▶ .fx3 (target-neutral) ──parse/check──▶ checked AST ──lower──▶ IR ──▶ backend ──▶ project files ──native build──▶ binary
                                                                          │                      python · go · java · cpp · rust
                                                                          └─ the same IR feeds every backend
```

**IR (`fx3c/ir/`).** It is typed, desugared and close to the targets:
- **Types:** every expression has a resolved type.
- **Method bank:** calls are resolved to intrinsic operations (`VecPush`, `MapGetOr`, `StrSplit`, …), so a backend implements a fixed list of operations, not method names.
- **Desugaring:**
  - loops: `repeat` forms → `For` / `While` / `ForEach`
  - `switch` → a decision tree
  - English conditions are already gone by this stage
  - f-strings → parts plus format specs
- **Ownership is explicit:** `Copy`, `Move` and `Borrow(read | write)` on every value transfer. Copies are removed where the source is dead.
- **Error flow is explicit:** `Try`, `Propagate` and `Handle`, with each function's set of error types.
- **Closures:** a capture list, each entry by value or through a shared cell.
- **Source map:** every node keeps its `.fx3` line and `.fluid` block (E-08), so panics point at the right place on every target.
- **Modules:** packages and files, so backends can lay out Java packages, Go packages, Rust `mod`s and C++ headers.

**Runtimes.** Each target has its own native library in `fx3c/backends/<lang>/runtime/`:
- the core: checked math, floor division, printing, panics, strings, ordered maps, copy-on-write collections
- the standard modules, in the same order Python got them: Math, Time, Json, File, Env, Log, then Http, Server, Db, then Schedule, Process, Ai, Email
- A backend says which modules it has, and using a missing one is FX10xx.

**Verification.**
- fluidc keeps verifying generated fx3 by running its tests in the Python sandbox. That's fast, and the conformance suite makes the result valid for every target.
- `fluid test --target go` also builds and runs the native tests.
- Native binaries run under the OS sandbox layer (sandbox-exec / bwrap) with rlimits. The Python audit hook doesn't apply to them, and on Windows that means a warning.

---

## 6. Phases

- [ ] **Phase 0: spec (S–M).** Done so far:
  - `Syntax/targets.fx3`
  - `group`, `select` and the task model in `concurrency.fx3`
  - `@service` / `@provides` and field constraints in `classes.fx3`
  - references never escape and the resources lock in `memory.fx3`
  - capture rules in `constructs.fx3`
  - `@unordered` in `collections.fx3`
  - `@transactional` in `stdlib.fx3`
  - `ValidationError` in `errors.fx3`
  - `fluid.config` sections and the FLUID_SPEC changes
  - Write `Syntax/targets.fx3` (§4.10) and the §4.1–4.9 additions to the existing `Syntax/` files.
  - Update FLUID_SPEC (`target` values, `service` keyword, §8 config) and `fluid.config.schema.json` (`target.go`, `target.java`, `target.rust`).
  - Lock the decisions.
- [x] **Phase 1: target-neutral fx3 (S).** Done 2026-11-03.
  - Remove the target from the fluidc prompt and the lock key; older keys stay valid, as was done for `--ask`.
  - Move `target.*` to the backend; add `fluid build --target X`.
- [ ] **Phase 2: the language features backends rely on (L).** From E-09, in fx3c and the Python backend:
  - [x] enums + patterns (2026-11-03):
    - simple enums, raw values (`enum<int>` / `enum<string>`), variants that carry fields, methods, `@ordered`
    - `Name.Variant` or just `Variant` where the type is known
    - `all` / `from` / `name` / `index` / `value`
    - variant patterns with bindings, `_` and `where`; a switch must cover every variant
    - printing per `targets.fx3` 2b; enums work across files
    - Not yet: `@flags`, `is` patterns, tuple / list patterns
  - interfaces, generics
  - Python sets keep insertion order (today they're Python `set`s; §4.10); `@unordered` keeps the plain `set`
  - spawn / task / await, channels, `locked`, `atomic`
  - `group`, `select`, `@service`, field constraints, `@transactional`, closure capture rules, `@native` / `@extern`
  - Each feature comes with fluidc reference examples so the model uses it.
- [ ] **Phase 3: IR (L).**
  - Lowering for everything fx3c checks, plus an IR printer (`fx3 show --ir`) and tests.
  - The Python backend stays on the AST for now. Moving it to the IR later is a separate, measured step.
- [ ] **Phase 4: conformance suite, E-17 (M).**
  - About 80 programs in `tests/conformance/` with expected stdout, stderr and exit code, covering every feature from Phase 2.
  - Python must pass all of them.
  - CI installs Go, a JDK, Rust and CMake.
- [ ] **Phase 5: first backend (L).**
  1. The core language, then Math, Time, Json, File, Env and Log, until the conformance suite passes.
  2. Then Http, Server, Db, Schedule and Process.
  3. Then the playground apps (`grades`, `notes`, `shop`) build and run on it offline from their existing locks.
- [ ] **Phase 6: the second, third and fourth backends.** The same steps for each; see question 1 for the order.
- [ ] **Phase 7: the tooling around targets.**
  - `fluid doctor` / `fluid setup` check each toolchain (E-34).
  - `fluid run` builds natively; `fluid package` writes a Dockerfile per target.

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| Rust: shared mutable objects fight the borrow checker | Objects are `Rc<RefCell<T>>`, and methods take the handle and borrow one field at a time (never for a whole method), so reentrant calls don't panic. `group` gives safe borrowing for concurrency. |
| C++: undefined behaviour and lifetimes | No raw pointers outside `@unsafe`, shared objects are `shared_ptr`, references never escape (§4.6), and sanitizers run in the debug and conformance builds. |
| Behaviour drifts between targets | Conformance on every change, in CI, for every target with a toolchain. |
| Heavy frameworks (Spring Boot start-up, Cargo build times) | The framework is a setting (`target.java.web = jdk` for a light build); build caches in `build/`. |
| Too much new syntax for beginners | Every addition is optional, and `.fluid` users never write it: the model does, from prose. Only `service` is new in `.fluid`. |
| The scope is XL | Phases are independent and shippable. Each backend is useful on its own once it passes conformance. |
