""".env.example に秘密情報の値が入っていないことの確認。"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECRET_KEY = re.compile(r"^\s*([A-Z_]*(API_KEY|SECRET|TOKEN|PASSWORD)[A-Z_]*)\s*=\s*(\S*)")


def test_env_example_has_no_secret_values() -> None:
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        match = SECRET_KEY.match(line)
        if match:
            # 失敗時に値そのものが表示されないよう、真偽値だけを比較する
            has_value = bool(match.group(3))
            assert not has_value, f"{match.group(1)} に値が入っています（値は .env に書く）"
