# Repo Mapper

The portable skill lives in `skills/repo-mapper/`. Its Python CLI, manifest, and
license must work when that directory is copied into a target repository.

## Review priorities

- Keep the CLI on the Python 3.10+ standard library. Inspect target Python with
  AST parsing and never import or execute it.
- Preserve secret-name filtering, virtual environment detection, and link
  handling. Review filesystem changes for paths that could be read through a
  symbolic link or Windows junction.
- Keep output bounded and deterministic. A Python parse failure in one file
  must let the scan continue. Keep the README demo synchronized with the CLI.

## Checks

```sh
python -B -m unittest discover -s tests -v
python skills/repo-mapper/scripts/repo_mapper.py examples/demo_repo --depth 2
git diff --check
```

The syntax error in `examples/demo_repo/broken.py` is a deliberate fixture.
Use synthetic repositories for regression tests.
