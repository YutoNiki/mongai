"""`.githooks/commit-msg` の動作確認（SPEC 10.1）。"""

import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[2] / ".githooks" / "commit-msg"


def run_hook(tmp_path: Path, message: str) -> subprocess.CompletedProcess[str]:
    msg_file = tmp_path / "COMMIT_EDITMSG"
    msg_file.write_text(message, encoding="utf-8")
    return subprocess.run(
        ["sh", str(HOOK), str(msg_file)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "message",
    [
        "Docker構成を追加\n",
        "Docker構成を追加\n\nネットワークを internal: true にする。\n",
        "CLAUDE.md の手順を更新\n",
        "docs: CLAUDE.md と .claude/settings.json の説明を追加\n",
        "フックを追加\n\nSigned-off-by: YutoNiki <yuto@example.com>\n",
        "テンプレート\n\n# Co-Authored-By: これはコメント行なので無視される\n",
    ],
)
def test_normal_messages_pass(tmp_path: Path, message: str) -> None:
    result = run_hook(tmp_path, message)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "message",
    [
        "変更\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n",
        "変更\n\nco-authored-by: Claude Opus <noreply@anthropic.com>\n",
        "変更\n\nCO-AUTHORED-BY: someone <someone@example.com>\n",
        "変更\n\n  Co-authored-by : Bot <bot@example.com>\n",
        "変更\n\nSigned-off-by: Claude <claude@example.com>\n",
        "変更\n\nGenerated-by: Anthropic Claude\n",
        "変更\n\nAssisted-By: claude-opus\n",
        "変更\n\n🤖 Generated with [Claude Code](https://claude.com/claude-code)\n",
        "変更\n\nGenerated with Claude Code\n",
        "変更\n\nhttps://claude.ai/code/session_0123456789\n",
        "変更\n\n連絡先 noreply@anthropic.com\n",
    ],
)
def test_attribution_messages_rejected(tmp_path: Path, message: str) -> None:
    result = run_hook(tmp_path, message)
    assert result.returncode != 0
    assert "拒否" in result.stderr


PRE_COMMIT = HOOK.parent / "pre-commit"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    return tmp_path


def run_pre_commit(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(PRE_COMMIT)], cwd=repo, capture_output=True, text=True, check=False
    )


def test_pre_commit_allows_empty_env_example(repo: Path) -> None:
    (repo / ".env.example").write_text("OITOMA_API_KEY=\nOITOMA_BASE_URL=https://x\n")
    _git(repo, "add", ".env.example")
    assert run_pre_commit(repo).returncode == 0


def test_pre_commit_rejects_key_in_env_example(repo: Path) -> None:
    (repo / ".env.example").write_text("OITOMA_API_KEY=sk-dummy-value\n")
    _git(repo, "add", ".env.example")
    result = run_pre_commit(repo)
    assert result.returncode != 0
    assert "sk-dummy-value" not in result.stderr  # 値を表示しない


@pytest.mark.parametrize("path", [".env", "data/private/paper.pdf"])
def test_pre_commit_rejects_secret_paths(repo: Path, path: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("x")
    _git(repo, "add", "-f", path)
    assert run_pre_commit(repo).returncode != 0
