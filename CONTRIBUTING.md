# Contributing

Thanks for your interest in contributing to `adk-a2a-settlement`.

This package integrates [Google ADK](https://github.com/google/adk) with the [A2A Settlement Exchange](https://github.com/a2a-settlement/a2a-settlement). It follows the same governance and contribution patterns as the core a2a-settlement org.

## Where to contribute

- **Bugs**: open an Issue with reproduction steps.
- **Feature requests**: open a Discussion first if it affects the API surface.
- **Integration improvements**: PRs welcome for `adk_a2a_settlement/`.

## How to propose a new feature

1. Open a GitHub Discussion with a description and motivation.
2. Get feedback from maintainers.
3. Submit an implementation PR with tests.
4. Link to any related a2a-settlement spec changes if applicable.

## Development setup

Use a clean Python environment (venv/conda):

```bash
pip install -e ".[dev]"
```

## Running tests

```bash
pytest -q
```

With coverage:

```bash
pytest --cov=adk_a2a_settlement --cov-report=term-missing -q
```

## Code style

- Keep code small and readable.
- Avoid heavy dependencies unless they materially improve clarity or correctness.
