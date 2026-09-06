# feature-dev-simple

A feature workflow that asks instead of guessing. One feature at a time.
Anything unclear becomes a question. Nothing gets built that isn't in the
approved spec.

## Phases

1. **Discovery** — restate the feature, confirm it.
2. **Exploration** — explorer agents map the code that matters.
3. **Questions** — every open choice, one at a time, until nothing is unclear.
4. **Design** — an architect agent designs and recommends. You decide.
5. **Spec** — a written plan of your decisions and the ordered work.
6. **Implementation** — after you approve, in a git worktree, test-first.
7. **Quality** — lint, typecheck, a reviewer agent, every check re-run.
8. **Hand over** — a summary and the merge commands for you to run.

## Agents

- `feature-dev-simple:code-explorer` — traces existing code, names the files
  worth reading
- `feature-dev-simple:code-architect` — designs to fit the codebase,
  recommends, never decides
- `feature-dev-simple:code-reviewer` — reports only what it is sure of

## Install

```
/plugin marketplace add <owner>/jako-claude-plugins
/plugin install feature-dev-simple@jako-claude-plugins
```

## License

Apache-2.0. See [LICENSE](LICENSE).

The three agents are copies of Anthropic's
[feature-dev](https://github.com/anthropics/claude-plugins-official/tree/main/plugins/feature-dev),
also Apache-2.0. `SKILL.md` is my own.
