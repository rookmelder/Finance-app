# Project

<!-- Fill this file in at the start of every new project (the Lead asks the user if anything is unclear).
     delegate.ps1 reads the "settings" block and pastes "Worker notes" into every worker's task. -->

**Status:** NOT FILLED IN YET

## About (for the Lead)
- **What we are making:** <one or two sentences>
- **Kind of work:** <code / writing / data / research / mixed>
- **Private?** <yes: only Claude, agy or Ollama may see the files / no: free cloud workers allowed>
- **Where the work lives:** <e.g. `src/`, `docs/`, `data/`> (always inside this folder)
- **Done means:** <how we know it works: tests pass, document reviewed, ...>

## Settings (read by delegate.ps1)
Leave a value empty when it doesn't apply. `{name}` stands for the part a worker works on (the Lead names it in the task: "Your part: shop").

```settings
check_one:
check_all:
check_dir:
```

- `check_one`: the ONLY command a worker may run, to check its own part. Empty = workers run no commands.
- `check_all`: the full check the Lead runs after every worker (all tests, a build, a linter). Empty = Lead reviews by reading.
- `check_dir`: the only folder a tester may write in. Empty = only the files its task names.

Examples:

| Project | check_one | check_all | check_dir |
|---|---|---|---|
| Python | `python -m unittest tests.test_{name} -v` | `python -m unittest discover -s tests` | `tests` |
| Node | `npx vitest run tests/{name}.test.js` | `npx vitest run` | `tests` |
| Writing / docs / research | (empty) | (empty) | (empty) |

## Worker notes (pasted into every worker's task: keep it short)
- <language and version, allowed libraries, style to match>
- <conventions: naming, units, coordinate order, tone of voice for text, ...>
- <known traps for this project>
