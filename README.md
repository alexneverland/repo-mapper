# Repo Mapper

**A local repository map for Codex and Claude Code.** Ask your agent how an
unfamiliar repo is organized and which files to open first.

- See a compact file tree and Python classes, functions, API routes, and
  declared tools, with source line numbers.
- Run the bundled CLI with Python 3.10 or newer and the standard library.
- Inspect Python code with AST parsing. The CLI runs offline and needs no API key.

## Quick start

```sh
git clone https://github.com/alexneverland/repo-mapper.git
cd repo-mapper
python skills/repo-mapper/scripts/repo_mapper.py examples/demo_repo --depth 2
```

On systems where Python 3 is available as `python3`, use it in place of
`python`. To map your own repository, pass its folder as the first argument.

## Install the skill

From the repository you want to explore, copy the entire `skills/repo-mapper`
directory from this checkout into the appropriate project folder.

| Agent | Project skill destination | Explicit invocation |
| --- | --- | --- |
| Codex | `.agents/skills/repo-mapper/` | `$repo-mapper` |
| Claude Code | `.claude/skills/repo-mapper/` | `/repo-mapper` |

In PowerShell, set `$repoMapper` to your Repo Mapper checkout and run the following
from the target repository. Use the destination for your agent.

```powershell
$repoMapper = 'C:\path\to\repo-mapper'
$destination = '.agents/skills' # For Claude Code, use '.claude/skills'
New-Item -ItemType Directory -Path $destination -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $repoMapper 'skills/repo-mapper') -Destination $destination -Recurse
```

On macOS or Linux, run these commands from the target repository.

```sh
repo_mapper="/path/to/repo-mapper"
destination=".agents/skills" # For Claude Code, use ".claude/skills"
mkdir -p "$destination"
cp -R "$repo_mapper/skills/repo-mapper" "$destination/"
```

Open a new session in the target repository and ask “I just opened this repo.
How is it organized, and which files should I look at first?” You can also use
the explicit invocation above.

[Codex skill locations](https://learn.chatgpt.com/docs/build-skills) ·
[Claude Code skill locations](https://code.claude.com/docs/en/skills)

## Example output

In the bundled demo, `api.py` contains the HTTP routes and `inventory/tools.py`
declares the lookup tool. The scan continues past the deliberate syntax error.

```text
Repo map: demo_repo (depth 2)
|- api.py
   async def list_items() L12  [GET /items]
   def add_item() L17  [POST /items]
   class Inventory L6
   def Inventory.items() L7
|- broken.py
   SyntaxError L1
|- README.md
|- inventory/
   |- tools.py
      def lookup_item() L5  [tool]
```

## Scan behavior

Use `--depth 1` for just the top level. The default is 3 and the accepted range is
1–8. Output is capped at 80 lines of up to 100 characters, with up to six
definitions per Python file. The scanner skips hidden entries (including
`.git` and `.env`), common cache and virtual environment directories, Python
virtual environments marked by `pyvenv.cfg`, common secret file names and
extensions, symbolic links, and Windows reparse points such as junctions. The
secret filter uses filenames. The CLI notes when directory entries become
unavailable during a scan. Python files over 256 KiB appear in the tree with an
AST skip message.

The AST scan covers top-level functions and classes and their direct methods.
Route and tool labels are based on conventional decorator spellings such as
`@router.get(...)`, `@app.route(...)`, `@tool`, `@function_tool`, and
`@mcp.tool()`. Route paths appear when expressed as string literals.

## Tests

```powershell
python -B -m unittest discover -s tests -v
```

## License

MIT. Based on the repository mapper from Astakos AI Agent by Lazaros Avramidis
(alexneverland). See [LICENSE](LICENSE).
