"""デモ用の合成論文（日本語、問題を意図的に仕込んだもの）の PDF を作る。

本文はすべて架空（著者・自治体・データも架空）。参考文献だけは実在の公開論文の書誌情報を使い、
書誌の照合を見せるために1件の発行年をわざと誤らせている。
未知の用語の照会（承認制の外部通信）を見せるために、一般的な専門用語（アイソクローン）を定義なしで使っている。仕込んだ問題の一覧（正解）は docs/demo.md。

コンテナ内で実行する（PyMuPDF が必要）：
    make demo-paper
出力：demo/demo_paper_ja.pdf（リポジトリに含める）
"""

import sys
from dataclasses import dataclass
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo" / "demo_paper_ja.pdf"

A4 = (595.0, 842.0)
LEFT, RIGHT, TOP, BOTTOM = 72.0, 523.0, 80.0, 770.0
BODY_SIZE, LEADING = 10.5, 17.0
HEADING_SIZE = 12.5
FONT_NAME = "jp"


@dataclass(frozen=True)
class Block:
    kind: str  # title / meta / heading / para / ref
    text: str


TITLE = "需要予測に基づく小規模自治体のオンデマンド交通の配車計画"
META = [
    "門外 太郎　不出 花子（架空大学 情報学部）",
    "※ 本論文は MongAI のデモ用に作成した合成データであり、内容・著者・データはすべて架空である。",
]

BLOCKS: list[Block] = [
    Block("heading", "概要"),
    Block(
        "para",
        "地方の小規模な自治体では、路線バスに代わる移動手段としてオンデマンド交通の導入が進んでいる。"
        "しかし、予約が特定の時間帯に集中すると、利用者の待ち時間が長くなるという課題がある。"
        "本研究では、予約の履歴と天気・曜日から30分ごとの予約件数を予測し、予測に基づいて車両を事前に配置する配車計画を提案する。"
        "架空の町Aの6か月分の予約データで評価したところ、平均待ち時間は14.2分から11.9分に短縮された。"
        "この結果から、提案手法は地方の公共交通が抱える課題をすべて解決できることが示された。",
    ),
    Block("heading", "1　はじめに"),
    Block(
        "para",
        "地方の路線バスは、利用者の減少により減便や廃止が続いている。"
        "その結果、自家用車を持たない高齢者にとって、通院や買い物のための移動が難しくなっている。"
        "国の調査でも、公共交通の空白地域に住む高齢者の割合は年々増えていると報告されている。",
    ),
    Block(
        "para",
        "こうした地域では、利用者の予約に応じて車両を運行するオンデマンド交通が注目されている。"
        "オンデマンド交通は、決まった路線や時刻表を持たないため、少ない車両で広い地域を運行できる。"
        "一方で、通院の多い朝の時間帯などに予約が集中すると、待ち時間が長くなりやすい。"
        "したがって、需要を正確に予測できれば、地方の交通問題はすべて解消する。",
    ),
    Block(
        "para",
        "そこで本研究では、30分ごとの予約件数を予測し、予約が入る前に車両を配置しておく配車計画を提案する。"
        "本研究の貢献は、予測と配車を組み合わせた計画の手順を示したことと、実際の運行に近い条件で効果を確かめたことである。",
    ),
    Block("heading", "2　関連研究"),
    Block(
        "para",
        "交通需要の予測には、ランダムフォレスト[1]やXGBoost[3]など、決定木に基づく手法が広く用いられてきた。"
        "Hochreiterら[2]は、LSTMが公共交通の需要予測においてランダムフォレストより高い精度を示すことを報告している。"
        "近年は、Transformer[4]を時系列の予測に応用する研究も増えている。",
    ),
    Block(
        "para",
        "町Aの中心部には、江戸時代から続く商店街があり、毎年秋には大きな祭りが開かれる。"
        "祭りの日には、町の人口を上回る数の観光客が訪れる。"
        "商店街の店主の多くは、祭りの日の売上が年間の売上の大きな割合を占めると答えている。",
    ),
    Block(
        "para",
        "配車計画については、予約を受けてから最適な車両を選ぶ方式が一般的である。"
        "しかし、この方式では予約が集中したときに対応できる車両が足りなくなることがある。",
    ),
    Block("heading", "3　提案手法"),
    Block(
        "para",
        "提案手法は、過去の予約の履歴、天気予報、曜日、祝日の情報から、地区ごとに30分単位の予約件数を予測する。"
        "予測のモデルには、少ないデータでも安定して学習できるXGBoost[3]を用いる。",
    ),
    Block(
        "para",
        "次に、予測した予約件数と各車両の現在地をもとに、車両を待機させる地区を決める。"
        # 未知の用語の照会（承認制の外部通信）の例：一般的な専門用語を定義なしで使う
        "待機の候補は、各地区の中心から10分のアイソクローンに含まれる停留所に限る。"
        "その際、それが大きい地区ほど多くの車両を割り当てる。"
        "本手法の特徴は、予約が入る前に車両を利用者の近くへ移動させておく。",
    ),
    Block("heading", "4　実験"),
    Block(
        "para",
        "評価には、架空の町A（人口約2万人）で2025年4月から9月までに記録された6,214件の予約データを用いた。"
        "4月から8月のデータで予測モデルを学習し、9月のデータで評価した。"
        "比較の対象は、予約を受けてから最も近い車両を割り当てる従来の方式である。",
    ),
    Block(
        "para",
        "予測の平均絶対誤差は、30分あたり1.8件であった。"
        "利用者の平均待ち時間は、従来の方式の14.2分に対し、提案手法では11.9分であった。"
        "また、運転手へのアンケートでは、提案手法の配車画面が見やすいという回答が多かった。"
        "このことから、提案手法によって利用者の満足度が向上したといえる。",
    ),
    Block("heading", "5　考察"),
    Block(
        "para",
        "提案手法では、朝の通院の時間帯に待ち時間が大きく短縮された。"
        "これは、前日までの予約の傾向から、病院の周辺に車両を事前に配置できたためと考えられる。"
        "実験は1つの町の6か月分のデータに限られるが、提案手法はすべての自治体でそのまま利用できる。",
    ),
    Block(
        "para",
        "一方で、祭りなどの行事がある日は、予約の傾向が普段と大きく異なるため、予測の誤差が大きくなった。"
        "行事の予定を予測の入力に加えることは、今後の課題である。",
    ),
    Block("heading", "6　おわりに"),
    Block(
        "para",
        "本研究では、予約件数の予測に基づいて車両を事前に配置する、オンデマンド交通の配車計画を提案した。"
        "架空の町Aの予約データによる評価では、平均待ち時間が2.3分短縮された。"
        "今後は、複数の自治体のデータで効果を確かめるとともに、行事の予定を考慮した予測に取り組む。",
    ),
    Block("heading", "参考文献"),
    Block("ref", "[1] L. Breiman. Random Forests. Machine Learning, 45(1):5–32, 2001."),
    Block(
        "ref",
        "[2] S. Hochreiter and J. Schmidhuber. Long Short-Term Memory. Neural Computation, "
        "9(8):1735–1780, 1997.",
    ),
    # [3] は発行年を誤らせている（正しくは 2016 年）
    Block(
        "ref",
        "[3] T. Chen and C. Guestrin. XGBoost: A Scalable Tree Boosting System. In Proceedings "
        "of the 22nd ACM SIGKDD International Conference on Knowledge Discovery and Data Mining, "
        "pp. 785–794, 2012.",
    ),
    Block(
        "ref",
        "[4] A. Vaswani, N. Shazeer, N. Parmar, J. Uszkoreit, L. Jones, A. N. Gomez, L. Kaiser, "
        "and I. Polosukhin. Attention Is All You Need. In Advances in Neural Information "
        "Processing Systems 30, pp. 5998–6008, 2017.",
    ),
]


class Writer:
    def __init__(self) -> None:
        self.doc = pymupdf.open()
        self.font = pymupdf.Font("cjk")
        self.page: pymupdf.Page | None = None
        self.y = TOP
        self.pages = 0

    def new_page(self) -> None:
        self.page = self.doc.new_page(width=A4[0], height=A4[1])
        self.page.insert_font(fontname=FONT_NAME, fontbuffer=self.font.buffer)
        self.pages += 1
        footer = str(self.pages)
        width = self.font.text_length(footer, 9)
        self.page.insert_text(((A4[0] - width) / 2, 805), footer, fontname=FONT_NAME, fontsize=9)
        self.y = TOP

    def wrap(self, text: str, size: float, width: float, first_indent: float) -> list[str]:
        """文字単位で折り返す（英文は単語の途中で切らない）。"""
        lines: list[str] = []
        current = ""
        limit = width - first_indent
        i = 0
        while i < len(text):
            # 英数字の並びは1つの単位として扱う
            j = i + 1
            if text[i].isascii() and text[i] != " ":
                while j < len(text) and text[j].isascii() and text[j] != " ":
                    j += 1
            unit = text[i:j]
            if current and self.font.text_length(current + unit, size) > limit:
                lines.append(current.rstrip())
                current = unit.lstrip()
                limit = width
            else:
                current += unit
            i = j
        if current:
            lines.append(current.rstrip())
        return lines

    def write_lines(
        self, lines: list[str], *, x: float, size: float, leading: float, first_indent: float = 0.0
    ) -> None:
        for n, line in enumerate(lines):
            if self.page is None or self.y > BOTTOM:
                self.new_page()
            assert self.page is not None
            self.page.insert_text(
                (x + (first_indent if n == 0 else 0.0), self.y),
                line,
                fontname=FONT_NAME,
                fontsize=size,
            )
            self.y += leading

    def centered(self, text: str, size: float, leading: float) -> None:
        for line in self.wrap(text, size, RIGHT - LEFT, 0.0):
            width = self.font.text_length(line, size)
            if self.page is None:
                self.new_page()
            assert self.page is not None
            self.page.insert_text(
                ((A4[0] - width) / 2, self.y), line, fontname=FONT_NAME, fontsize=size
            )
            self.y += leading

    def block(self, block: Block) -> None:
        width = RIGHT - LEFT
        if block.kind == "heading":
            if self.y > BOTTOM - 3 * LEADING:
                self.new_page()
            self.y += 8
            self.write_lines([block.text], x=LEFT, size=HEADING_SIZE, leading=LEADING + 6)
        elif block.kind == "para":
            lines = self.wrap(block.text, BODY_SIZE, width, BODY_SIZE)
            self.write_lines(lines, x=LEFT, size=BODY_SIZE, leading=LEADING, first_indent=BODY_SIZE)
        elif block.kind == "ref":
            # ぶら下げインデント（2行目以降を字下げ）
            first, *rest = self.wrap(block.text, 9.5, width, 0.0)
            self.write_lines([first], x=LEFT, size=9.5, leading=14)
            if rest:
                rest = self.wrap(" ".join(rest), 9.5, width - 18, 0.0)
                self.write_lines(rest, x=LEFT + 18, size=9.5, leading=14)
            self.y += 2

    def build(self, path: Path) -> None:
        self.new_page()
        self.y = TOP + 10
        self.centered(TITLE, 16, 26)
        self.y += 6
        for line in META:
            self.centered(line, 9.5, 15)
        self.y += 14
        for block in BLOCKS:
            self.block(block)
        self.doc.set_metadata({"title": TITLE, "author": "MongAI demo (synthetic)"})
        self.doc.subset_fonts()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.doc.save(path, garbage=4, deflate=True)


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    Writer().build(out)
    print(out)


if __name__ == "__main__":
    main()
