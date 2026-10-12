"""日本語の判定に使う文字クラス。

ソースに不可視の文字を直接書かないよう、コードポイントから組み立てる。
"""


def _ranges(*pairs: tuple[int, int]) -> str:
    return "".join(f"{chr(a)}-{chr(b)}" for a, b in pairs)


# ひらがな・カタカナ・CJK統合漢字（拡張A を含む）
JA_LETTERS = _ranges((0x3040, 0x30FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF))
# 上記に、全角記号・句読点（U+3000-303F）と全角英数・半角カナ（U+FF00-FFEF）を加えたもの
WIDE_CHARS = _ranges((0x3000, 0x30FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xFF00, 0xFFEF))
# 日本語の姓に使われる文字（カタカナ・漢字・「々」）。ひらがなは含めない（「田中ら」の「ら」を分けるため）。
# 中黒「・」（U+30FB）は「田中・鈴木」の区切りなので除く
JA_NAME_CHARS = _ranges(
    (0x30A0, 0x30FA), (0x30FC, 0x30FF), (0x3400, 0x4DBF), (0x4E00, 0x9FFF)
) + chr(0x3005)
