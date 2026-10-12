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
