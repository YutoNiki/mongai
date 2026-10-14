"""認証サービスの運用のコマンド（make authn-rotate-key・make authn-revoke-sessions から実行する）。

- rotate-key：主張に署名する鍵を新しくする。認証サービスを止めてから実行し、起動すると新しい鍵で署名し始める
  （core は公開鍵のファイルの更新を自動で読み直す）。動かしたまま替えると、再起動までの間、古い鍵の主張を core が
  401 にし、画面が「ログインの有効期限切れ」になる。止めている間は 503 になるだけで済む。セッションはそのまま使える
- revoke-sessions：すべてのログインのセッションを無効にする（全員がゲストに戻り、ログインし直す）
"""

import argparse
import sys

from authn.config import load_settings
from authn.keys import rotate_signing_key
from authn.store import SessionStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m authn.manage")
    parser.add_argument("command", choices=["rotate-key", "revoke-sessions"])
    args = parser.parse_args(argv)
    settings = load_settings()
    if args.command == "rotate-key":
        key = rotate_signing_key(settings.data_dir, settings.public_key_file)
        print(f"署名の鍵を新しくしました（{key.kid}）。認証サービスを起動してください")
    else:
        count = SessionStore(settings.data_dir / "sessions.db").delete_all_sessions()
        print(f"ログインのセッションを {count} 件無効にしました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
