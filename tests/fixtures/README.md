# テスト用の論文（公開OA論文のみ）

CLAUDE.md の規則により、ここには公開済みのオープンアクセス論文か合成データだけを置く。

| ファイル | 論文 | 出典 | ライセンス |
|---|---|---|---|
| `en_extraphrase_naacl2022.pdf` | Mengsay Loem, Sho Takase, Masahiro Kaneko, Naoaki Okazaki. "ExtraPhrase: Efficient Data Augmentation for Abstractive Summarization." NAACL 2022 Student Research Workshop, pp. 16–24. | ACL Anthology 2022.naacl-srw.3（doi:10.18653/v1/2022.naacl-srw.3） | CC BY 4.0 |
| `ja_extraphrase_jnlp2023.pdf` | Mengsay Loem, 高瀬 翔, 金子 正弘, 岡崎 直観. 「ExtraPhrase: 生成型要約のための効率的データ拡張」. 自然言語処理, 30(2), pp. 489–506, 2023. | J-STAGE（doi:10.5715/jnlp.30.489） | CC BY 4.0 |

同じ研究の英語版と日本語版で、レイアウト（英語は2段組・ACL 形式、日本語は1段組・JNLP 形式）と引用形式（著者年）の違いを確認できる。

合成PDF（レイアウトの単体テスト用）は `tests/unit/pdf_factory.py` でテスト中に生成する。
