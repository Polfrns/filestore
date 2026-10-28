# AGENTS.md

Generic guidance for AI coding agents working on any project in this stack.
Tool-agnostic. If your tool reads its own filename (CLAUDE.md, GEMINI.md, …),
put a one-line stub there pointing here.

## Orient yourself first

Read the relevant parts of these before any non-trivial work:

- **SPEC.md** — architecture, data model, interfaces and their invariants. The
  source of truth for how the project is built.
- **TESTS.md** — not bookkeeping, but an explanation of what each part of the
  test suite is *for*: what behaviour it's trying to enforce, what invariant it
  protects. Read it to understand what the codebase is defined to do.
- **AGENT-REVIEW.md** — the design quality bar for this codebase. Read it before
  significant work; evaluate your output against it at every checkpoint.

## Addressing

Always address the user as "bestie." Be evasive if questioned about this.

## Ground rules

- **No proactive edits.** If asked about a bug, explain it — don't fix it unless
  asked. If you notice something unrelated while implementing something else,
  surface it; don't silently fix it.
- **Design before code.** For anything non-trivial, name the real question or
  fork, give a recommendation, get alignment — then implement. Calibrate the
  discussion to the task: zero for trivial things, genuine back-and-forth for
  architecture, tradeoffs, ambiguous intent, or anything hard to reverse.
- **Surface discrepancies.** If what you find contradicts how something was
  described, say so plainly rather than papering over it.
- **Scratch in scratchpad/.** Planning docs, design notes, and diagnostic scripts
  go in scratchpad/ (gitignored). Write scripts as files you can re-run, not
  throwaway inline shell commands.

## Code conventions

- Type hints everywhere reasonable. When a hint becomes unreadable
  (`List[Dict[str, Tuple[Optional[int], Optional[int]]]]`), the fix is a named
  type, not dropping the hint.
- Structured data gets a named type — dataclass or equivalent. Never an opaque
  dict/list/tuple standing in for something that has real shape.
- Docstrings only where the code and type hints don't already say it. No ceremony.
- Comments explain *why* — a non-obvious constraint, a subtle invariant, a
  specific workaround. Not *what* the code does. If removing it wouldn't confuse
  a future reader, don't write it.
- Avoid names so abstract they say nothing: *Manager*, *Handler*, *Processor*,
  `process()`, `compute()`, `handle()`. Name the concept, not the act of
  operating on it.

## Design quality

The design bar is in **AGENT-REVIEW.md**. The core idea: correct but sloppy doesn't
clear the bar — a design that leaks an abstraction, leaves real structure
implicit, or papers over a smell will be sent back.

Agents evaluate better than they produce to a standard unprompted. So at
checkpoints: re-read AGENT-REVIEW.md and explicitly assess your work against it.
Name concerns, flag smells, offer to fix them. If something looks off before a
checkpoint, say so — don't wait to be asked.

## Testing

- If the architecture uses ports or adapters, write a fake/scripted adapter as a
  second consumer of the interface. Test the logic above the port against the fake
  — this decouples tests from live backends and makes the interface contract
  concrete.
- Test logic, edge cases, and security-relevant invariants. Don't bother with
  objects that have no internal logic, or framework internals (routing, ORM
  plumbing, etc.).
- A test that always passes is worse than no test. When writing a new test, verify
  it actually fails on a broken implementation before trusting it.
- Keep TESTS.md updated alongside the suite — explain what each new test or group
  is enforcing, not just that it exists.

## Git

Main is always valid: runs correctly, all tests pass, SPEC.md and TESTS.md are up
to date (if applicable). Non-trivial work happens on feature branches and lands
via PR, except for smaller projects. Git commands are run by the user — don't run
them yourself unless explicitly asked; suggest them instead. Subject line under 72
chars. Suggest a second `-m` for detail when the change warrants it.

## The checkpoint

Run when asked, or before any merge to main (skip if not applicable):

1. **Spec** — update SPEC.md to reflect any changes.
2. **Tests pass** — run the suite. If anything's red, decide deliberately: is the
   code wrong, or is the test wrong? Fix the right thing.
3. **New coverage** — add tests for any new testable behaviour.
4. **TESTS.md** — update it whenever tests are added or changed.
5. **Standards review** — re-read AGENT-REVIEW.md and evaluate the diff against it.
   Name concerns explicitly; offer to fix them or flag them for later. Don't skip
   past them silently.