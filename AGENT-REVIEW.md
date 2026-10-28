# AGENT-REVIEW.md

Design and code quality reference. Agents: read this before significant work and
evaluate your output against it at every checkpoint (see AGENTS.md). These are
the standards the codebase is held to.

---

## Design principles

**Depend on interfaces; minimize what each part knows.**
A component's collaborators should be expressed as interfaces, not implementations. Internals stay internal. The test: can you understand, test, and replace this component without touching anything outside its boundary? If not, either the boundary is wrong or something has leaked through it.

**Model real structure; don't leave it implicit.**
If something has shape, give it a type. No opaque dicts, tuples, or stringly-typed
blobs standing in for structured data. The test: could you name the thing? Then
name it.

**Separate computation from effects.**
A function that computes something should not also write, mutate, or cause side effects — and vice versa. Pure logic belongs in functions that take values and return values; I/O and mutation belong in functions that are explicitly effectful. This keeps logic testable in isolation and makes the effectful surface of the system visible and narrow.

**Make illegal states unrepresentable.**
Reach for types, not runtime checks or conventions. If the wrong thing simply
can't be expressed, it can't be silently introduced.

**Match structure to need.**
The counterweight to the above: abstractions earn their keep. A seam is a seam,
not a cathedral. Don't add indirection without a concrete payoff — three similar
lines are better than a premature abstraction.

**Loud failures, never silent drift.**
Surface the unknown — a loud error or a visible fallback. Never drop, swallow, or
guess past a failure. A legitimately empty result is a clean no-op; an actual
error is not.

**Single source of truth.**
No two places that silently have to agree. Cross-cutting concerns (an access
check, a codec, a config value) belong in one place; callers defer to it.

**Refactors are behaviour-preserving.**
A refactor changes shape, not behaviour. When restructuring, verify the output is
identical across real cases — don't infer it.

**Named constants for tuning knobs.**
No scattered magic values, no two files encoding the same constant independently.

---

## Anti-patterns — red flags for review

- **Concrete coupling** — depending on an implementation rather than an interface; reaching into another component's internals; code that cannot be swapped without rippling changes into callers. The goal is a seam you can cut cleanly.
- **Ad-hoc dict/tuple/list** where a named type belongs. Ask: could this be a
  dataclass? If yes, it should be.
- **Computation buried in an effectful function** — logic that derives a value tangled into a function that also writes, mutates, or calls out. The logic can't be tested without triggering the effect; the effect can't be reasoned about without understanding the computation.
- **Two places that have to silently agree** — duplicated logic, duplicated
  constants, two encodings of the same fact.
- **Swallowed exception or silent fallback** — `except: pass`, a `None` return
  that downstream code doesn't check, a default that papers over an error.
- **Leaked abstraction** — a layer that has to know too much about the layer below
  it, or a type that escapes its intended boundary.
- **Over-engineering** — an abstraction or indirection with no concrete current
  payoff. If you can't name the second use case, you probably don't need it yet.
- **Under-engineering** — implicit structure that should be explicit; a convention
  that should be a type; a runtime check that should be a compile-time constraint.
- **Stringly-typed data** — an enum that's a free string, a config key that's a
  magic literal, a role encoded as an arbitrary string.
- **Refactor that silently changed behaviour** — diff the externally-observable
  outputs before and after; don't assume shape-changes are neutral.