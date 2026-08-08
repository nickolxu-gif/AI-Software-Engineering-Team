import shutil
import subprocess
from pathlib import Path


def run(argv, cwd, check=True):
    return subprocess.run(
        argv,
        cwd=str(cwd),
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def make_repo(path):
    path.mkdir()
    run(["git", "init", "-b", "main"], path)
    run(["git", "config", "user.name", "MVP0 Test"], path)
    run(["git", "config", "user.email", "mvp0@example.invalid"], path)
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    (path / ".gitignore").write_text("/.worktrees/\n", encoding="utf-8")
    scripts = path / "scripts"
    scripts.mkdir()
    source_script = Path(__file__).resolve().parents[1] / "scripts" / "new-agent-worktree.sh"
    shutil.copy2(source_script, scripts / "new-agent-worktree.sh")
    run(["git", "add", "README.md", ".gitignore", "scripts/new-agent-worktree.sh"], path)
    run(["git", "commit", "-m", "test: initialize fixture"], path)
    return path
