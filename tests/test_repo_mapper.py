import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


PROJECT = Path(__file__).resolve().parents[1]
SKILL = PROJECT / "skills" / "repo-mapper"
CLI = SKILL / "scripts" / "repo_mapper.py"
DEMO = PROJECT / "examples" / "demo_repo"
sys.path.insert(0, str(CLI.parent))
from scan_paths import open_directory, read_source


def run_cli(folder, *args, cli=CLI):
    return subprocess.run(
        [sys.executable, str(cli), str(folder), *args],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=10,
    )


class RepoMapperTests(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory(dir=PROJECT)
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name) / "test repo"
        self.root.mkdir()

    def write(self, relative_path, contents=""):
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    def map(self, *args):
        result = run_cli(self.root, *args)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_demo_survives_syntax_error(self):
        result = run_cli(DEMO, "--depth", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        output = result.stdout
        for expected in (
            "class Inventory L6", "def Inventory.items() L7",
            "async def list_items() L12  [GET /items]", "[POST /items]", "[tool]",
        ):
            self.assertIn(expected, output)
        self.assertLess(output.index("SyntaxError L1"), output.index("lookup_item"))

    def test_ast_does_not_execute_source(self):
        marker = self.root / "executed.txt"
        self.write("module.py", f"open({str(marker)!r}, 'w').close()\ndef main(): pass\n")
        self.assertIn("def main()", self.map())
        self.assertFalse(marker.exists())

    def test_route_and_tool_patterns(self):
        self.write("module.py", """
@router.api_route(path="/status", methods=["get", "HEAD"])
def status(): pass

@mcp.tool()
async def inspect_repo(): pass

@app.get(STATUS_PATH)
def dynamic(): pass

@config.get("settings")
def unrelated(): pass
""")
        output = self.map()
        self.assertIn("[GET,HEAD /status]", output)
        self.assertIn("async def inspect_repo() L6  [tool]", output)
        self.assertIn("def dynamic() L9  [GET]", output)
        self.assertIn("def unrelated() L12\n", output)

    def test_skips_sensitive_names_and_environments(self):
        excluded = (
            ".git/config", ".env", "prod.env", "credentials.py", "client_secret.json",
            "my-service-account.json", "private.pem", "id_ed25519", ".ruff_cache/index",
            ".venv/pkg.py", "venv/pkg.py", "tools-env/pyvenv.cfg", "tools-env/pkg.py",
            "custom-runtime/pyvenv.cfg", "custom-runtime/pkg.py", "__pycache__/app.pyc",
            "app.pyc", "id_rsa.bak",
        )
        for relative_path in excluded:
            self.write(relative_path, "def excluded(): pass")
        self.write("visible.py")
        self.write("id_lookup.py")
        output = self.map()
        self.assertEqual(output.splitlines()[1:], ["|- id_lookup.py", "|- visible.py"])

    def test_does_not_follow_outside_links(self):
        outside = self.root.parent / "outside"
        outside.mkdir()
        target = outside / "outside.py"
        target.write_text("def outside(): pass\n", encoding="utf-8")
        self.write("visible.py")
        try:
            (self.root / "alias.py").symlink_to(target)
            (self.root / "linked").symlink_to(outside, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                raise
        if os.name == "nt":
            subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(self.root / "junction"), str(outside)],
                check=True, capture_output=True, timeout=10,
            )
        self.assertEqual(self.map().splitlines()[1:], ["|- visible.py"])

    def test_depth_and_bad_arguments(self):
        self.write("src/nested/module.py")
        self.assertNotIn("nested", self.map("--depth", "1"))
        self.assertIn("nested/", self.map("-d", "2"))
        self.assertIn("module.py", self.map("--depth", "3"))
        self.assertEqual(run_cli(self.root, "--depth", "0").returncode, 2)
        self.assertEqual(run_cli(self.root / "missing").returncode, 2)

    def test_file_at_output_boundary(self):
        for index in range(79):
            self.write(f"item_{index:02}.txt")
        lines = self.map().splitlines()
        self.assertEqual(len(lines), 80)
        self.assertEqual(lines[-1], "|- item_78.txt")
        self.write("item_79.txt")
        self.assertIn("output limit reached", self.map().splitlines()[-1])

    def test_wide_repo_keeps_folders_entry_points_and_routes_visible(self):
        for index in range(90):
            self.write(f"00_state_{index:02}.json")
        helpers = "\n".join(f"def helper_{index}(): pass" for index in range(10))
        self.write("main.py", helpers + "\ndef main(): pass\nclass Application: pass\n")
        self.write("api/server.py", '@app.get("/status")\ndef status(): pass\n')
        self.write("core/engine.py", "class Engine: pass\n")
        self.write("tests/test_engine.py")
        output = self.map("--depth", "3")
        for expected in ("|- api/", "|- core/", "|- tests/", "def main() L11", "[GET /status]",
                         "class Application L12", "class Engine L1", "more files or definitions"):
            self.assertIn(expected, output)
        self.assertLess(output.index("|- core/"), output.index("|- 00_state_00.json"))

    def test_skips_database_files_but_keeps_database_named_folders(self):
        databases = (
            "application.db", "cache.sqlite", "store.sqlite3-wal", "work.db-journal",
            "state.sqlite3-shm",
        )
        for name in databases:
            self.write(name)
        self.write("fixtures.db/schema.py")
        output = self.map()
        self.assertIn("|- fixtures.db/", output)
        self.assertIn("|- schema.py", output)
        for name in databases:
            self.assertNotIn(name, output)

    def test_many_folders_leave_room_for_a_source_folder_expansion(self):
        for index in range(85):
            (self.root / f"folder_{index:02}").mkdir()
        self.write("api/server.py", '@app.get("/status")\ndef status(): pass\n')
        output = self.map("--depth", "2")
        self.assertIn("... more folders.", output)
        self.assertIn("|- api/", output)
        self.assertIn("[GET /status]", output)

    def test_long_folder_heading_keeps_the_deepest_name(self):
        parent = "long_parent_" + "x" * 68
        leaf = "important_subfolder_" + "y" * 30
        self.write(f"{parent}/{leaf}/engine.py")
        lines = self.map("--depth", "3").splitlines()
        heading = next(line for line in lines if line.startswith("...") and line.endswith(f"/{leaf}/"))
        self.assertLessEqual(len(heading), 100)
        self.assertEqual(lines[lines.index(heading) + 1], "   |- engine.py")

    def test_read_rejects_a_file_replaced_by_a_link(self):
        outside = self.root.parent / "outside.py"
        outside.touch()
        self.write("entry.py")
        with open_directory(self.root) as directory:
            path = self.root / "entry.py"
            path.unlink()
            try:
                path.symlink_to(outside)
            except OSError as error:
                if os.name == "nt" and error.winerror in {5, 1314}:
                    self.skipTest("Windows file symlinks require Developer Mode or equivalent privileges")
                raise
            with self.assertRaises(OSError):
                read_source(directory, path.name, 10)

    def test_open_rejects_a_directory_replaced_by_a_link(self):
        outside = self.root.parent / "outside"
        outside.mkdir()
        path = self.root / "entry"
        path.mkdir()
        with open_directory(self.root) as directory:
            path.rmdir()
            if os.name == "nt":
                subprocess.run(
                    ["cmd.exe", "/d", "/c", "mklink", "/J", str(path), str(outside)],
                    check=True, capture_output=True, timeout=10,
                )
            else:
                path.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(OSError):
                with open_directory(path.name, directory):
                    self.fail("A directory link was followed")

    def test_output_is_bounded(self):
        self.write("00.py", "\n".join(f"def helper_{i}(): pass" for i in range(12)))
        self.write("01.py", '@app.get("/' + "x" * 200 + '")\ndef view(): pass')
        self.write("02.py", "#" * (256 * 1024 + 1))
        for index in range(100):
            self.write(f"file_{index:03}.txt")
        lines = self.map().splitlines()
        self.assertLessEqual(len(lines), 80)
        self.assertLessEqual(max(map(len, lines)), 100)
        self.assertIn("   ... 6 more definitions", lines)
        self.assertIn("   AST skipped (file exceeds 256 KiB)", lines)
        self.assertIn("output limit reached", lines[-1])

    def test_installed_skill_paths(self):
        expected = run_cli(DEMO, "--depth", "2").stdout
        for folder in (".agents", ".claude"):
            with self.subTest(folder=folder):
                installed = self.root / folder / "skills" / SKILL.name
                shutil.copytree(SKILL, installed)
                self.assertTrue((installed / "SKILL.md").is_file())
                self.assertTrue((installed / "LICENSE").is_file())
                result = run_cli(DEMO, "--depth", "2", cli=installed / "scripts" / CLI.name)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)

    def test_chained_decorators_cli_commands_and_main_guard(self):
        self.write("service.py", """
@project.api.v1.get("/users")
def list_users(): pass

@app.websocket("/ws")
async def stream(): pass

@cli.command()
def sync(): pass

if __name__ == "__main__":
    sync()
""")
        output = self.map()
        self.assertIn("def list_users() L3  [GET /users]", output)
        self.assertIn("async def stream() L6  [WEBSOCKET /ws]", output)
        self.assertIn("def sync() L9  [command]", output)
        self.assertIn("if __name__ == '__main__' L11", output)

    def test_route_receiver_does_not_match_unrelated_attributes(self):
        self.write("callbacks.py", """
@app.callbacks.get("/ready")
def on_ready(): pass

@project.router.get("/status")
def status(): pass
""")
        output = self.map()
        self.assertIn("def on_ready() L3\n", output)
        self.assertNotIn("[GET /ready]", output)
        self.assertIn("def status() L6  [GET /status]", output)

    def test_reads_python_file_open_for_writing(self):
        self.write("live.py", "def active(): pass\n")
        with open(self.root / "live.py", "a", encoding="utf-8"):
            output = self.map()
        self.assertIn("def active() L1", output)

    def test_omits_expansion_for_folders_beyond_preview_limit(self):
        for index in range(25):
            self.write(f"z_{index:02}/inside.py", "def hidden(): pass\n")
        output = self.map("--depth", "2")
        self.assertIn("... more folders.", output)
        self.assertNotIn("z_24/", output)


if __name__ == "__main__":
    unittest.main()
