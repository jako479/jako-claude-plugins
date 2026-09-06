---
name: feature-dev-simple
description: Use when the user hands over a feature to implement — before any exploration, design, or code. Also use when a spec turns ambiguous or a surprise appears mid-implementation.
---

<!-- Original work by Brian Jacobs. Not derived from another plugin. -->

# Dev Feature

One feature at a time. Every decision is the user's. "Do what you think is
best" counts only when the user explicitly says it — then recommend and get
their confirmation.

All text — docs, code comments, project meta, specs, commit messages,
responses — very simple, clear, and as short as possible. No fluff. Plain
everyday words, no jargon or slang. One caveat max.

A message that asks questions gets answers only — no phase advance until
the user says start. A delivered report ends the turn: no prep for the next
step until the user says go.

## Phases

1. **Discovery** — restate the feature; confirm understanding with the user.
2. **Exploration** — feature-dev-simple:code-explorer subagents, each given
   an angle. Default to 1. Use 2 only when the second angle reads different
   files or different sources — say the existing code, and how other tools
   solve this. If the second angle would read the same files, it is the
   same angle: use 1. Say which angle each agent got. Read the key files
   they name.
3. **Questions** — flush out the requirements and every open choice:
   approach, naming, structure, behavior, file layout, scope, edge cases.
   Ask until nothing is ambiguous. Never fill a gap with an assumption.
   One question per message, in the AskUserQuestion widget with concrete
   choices; all context goes inside the widget text (text before a widget
   doesn't display). "Other" or a question back: answer it, then re-ask
   the choice until decided. Obvious variants of a supported term are in
   scope even with no sample showing them.
   Requirements come first; design and the plan only after they're settled.
4. **Design** — feature-dev-simple:code-architect subagents, one design
   each. Default to 1. Use 2 only when you can name two approaches that
   would touch different files or give the code a different shape. If both
   would change the same files the same way, it is one design: use 1. Say
   what each agent was asked for.
   Present with a recommendation backed by measurement on real data, not
   assertion.
5. **Spec** — write the plan file (plan mode). It records the user's decisions
   and the answers to every question he asked, then the work as ordered
   tasks: each task's exact files, the interfaces later tasks rely on, its
   slice of the test list, and steps for the docs it touches. Name every
   new function, class and variable in the spec — a name that doesn't say
   what the thing is gets fixed here, not after it's written. No
   placeholders.
   Gate: never request approval
   while any question of the user's is unanswered — answers go in the visible
   end-of-turn message AND the spec. The file survives the session; a fresh
   session can implement from it.
6. **Implementation** — only after explicit approval; exactly the spec.
   Open with superpowers:using-git-worktrees — fresh worktree + branch
   (setup is `uv sync`, then baseline tests); the user's checkout never
   switches branches. Commit per task on the worktree branch, never on
   any branch of the user's.
   Build with superpowers:test-driven-development, starting from a written
   test list: every behavior, every limit at it and past it, error paths,
   unit through system/golden level, with mocks / fake files / fake records
   when appropriate. Red-green through the list, growing it as surprises
   appear.
   Anything the spec doesn't cover: stop and ask. Comments state current
   behavior only — never history ("now", "no longer", "used to").
7. **Quality** — ruff (check and format) + pyright checked and fixed
   (commands: project CLAUDE.md), then
   1 feature-dev-simple:code-reviewer subagent; it also
   confirms no touched doc went unupdated. Findings go to the user:
   fix now / later / skip. Close with
   superpowers:verification-before-completion — every check re-run and its
   output shown before anything is called done.
8. **Record & hand over** — update STATUS, CHANGELOG, TODO, committed on
   the worktree branch with the rest. Then a short summary: what was
   built, decisions made, files touched.
   The spec file is scratch — never committed; delete it here myself.
   End with the commands for the user to run — never run them myself, from
   their checkout: `git merge --squash <worktree branch>` and a single-line
   `git commit -m` with the project's usual prefix (e.g. `<component>:`)
   and a simple, clear, short message — their branch gets exactly one
   commit. After they confirm the merge: remove the worktree and delete
   its branch.

Any phase, any bug, failing test, or unexpected behavior:
superpowers:systematic-debugging before proposing a fix.

## Red flags — stop and ask

- "It probably means…" / "The obvious choice is…" / "I'll just pick…"
- Any similar assumption-shaped thought about the user's intent.
- Implementing anything not written in the approved spec.
