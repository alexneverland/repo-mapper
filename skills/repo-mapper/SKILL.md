---
name: repo-mapper
description: Map the files and Python entry points of a local repository. Use when the user opens an unfamiliar repo, asks how it is organized, or wants to know which files to read first.
---

# Repo Mapper

1. Identify the repository folder from the user's request or the active workspace. Run the bundled CLI with Python 3.10 or newer. Resolve the script path from this skill's directory and quote both paths.

   ```sh
   python "<skill-directory>/scripts/repo_mapper.py" "<repository-folder>"
   ```

2. Summarize the major directories and suggest two or three files to open first, with a reason for each. Use relative paths and cite Python line numbers when the map provides them. Read a relevant README or likely entry point if the map alone leaves the starting point unclear.
3. Use `--depth 1` for just the top level or scan a narrower subfolder when the map reaches its output limit. Treat decorator matches as clues and mention skipped Python analysis when it affects the answer.
