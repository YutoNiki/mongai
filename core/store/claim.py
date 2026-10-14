"""移行で隔離した（所有者の分からない）データに、運用者が明示的に持ち主を決める。

使い方（コンテナ内）：`make claim-legacy ARGS="--user U-… --doc <doc_id> --rule-document RD-… --lab-document LD-…"`
持ち主にする利用者は、先に一度ログインして登録しておく。ログイン画面から自動で割り当てる経路は作らない。
"""

import argparse
import logging
import os
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from core.sample import SAMPLE_DOC_ID
from core.store.db import DEFAULT_DB_PATH
from core.store.schema import ensure_schema
from core.store.users import UserStore

logger = logging.getLogger(__name__)


class ClaimError(RuntimeError):
    pass


def claim(
    path: Path,
    user_id: str,
    *,
    docs: Sequence[str] = (),
    rule_documents: Sequence[str] = (),
    lab_documents: Sequence[str] = (),
) -> dict[str, int]:
    """隔離された論文（とその解析）・ルール資料（とそのルール）・専門知識の資料（とその指摘・ルール）を
    user_id の個人のデータにし、表ごとの件数を返す。隔離されていないデータは変えない。

    共通デモの論文（とそれに付いた解析）は引き取らせない（デモは誰の所有にもしない）。"""
    docs = [d for d in docs if d != SAMPLE_DOC_ID]
    ensure_schema(path)
    user = UserStore(path).get_user(user_id)
    if user is None or user.status != "active":
        raise ClaimError(f"利用者 {user_id} が見つからないか、無効化されています")
    counts: dict[str, int] = {}
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")

        def own(table: str, key: str, values: list[str]) -> None:
            if not values:
                return
            marks = ",".join("?" * len(values))
            cursor = conn.execute(
                f"UPDATE {table} SET owner_user_id = ?, visibility = 'private' "
                f"WHERE {key} IN ({marks}) AND visibility = 'quarantined'",
                (user_id, *values),
            )
            counts[table] = counts.get(table, 0) + cursor.rowcount

        try:
            own("papers", "doc_id", list(docs))
            own("analyses", "doc_id", list(docs))
            if docs:
                # 引き取った解析の、承認キューでの決定（照会の記録に重ねる）
                marks = ",".join("?" * len(docs))
                cursor = conn.execute(
                    "UPDATE term_decisions SET owner_user_id = ? WHERE owner_user_id IS NULL "
                    "AND request_id IN (SELECT t.request_id FROM term_lookups t "
                    "JOIN analyses a ON a.analysis_id = t.analysis_id "
                    f"WHERE a.doc_id IN ({marks}) AND a.owner_user_id = ?)",
                    (user_id, *docs, user_id),
                )
                counts["term_decisions"] = cursor.rowcount
            own("rule_documents", "document_id", list(rule_documents))
            own("lab_documents", "document_id", list(lab_documents))
            own("lab_comments", "document_id", list(lab_documents))
            own("rules", "document_id", [*rule_documents, *lab_documents])
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    logger.warning("隔離されたデータを利用者 %s に引き渡しました：%s", user_id, counts)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default=None, help="データベースのパス（既定は MONGAI_DB_PATH）")
    parser.add_argument("--user", required=True, help="持ち主にする内部ユーザーID（U-…）")
    parser.add_argument("--doc", action="append", default=[], help="論文の ID（複数指定できる）")
    parser.add_argument("--rule-document", action="append", default=[], help="ルール資料の ID")
    parser.add_argument("--lab-document", action="append", default=[], help="専門知識の資料の ID")
    args = parser.parse_args()
    path = Path(args.db or os.environ.get("MONGAI_DB_PATH") or DEFAULT_DB_PATH)
    counts = claim(
        path,
        args.user,
        docs=args.doc,
        rule_documents=args.rule_document,
        lab_documents=args.lab_document,
    )
    print(counts)


if __name__ == "__main__":
    main()
