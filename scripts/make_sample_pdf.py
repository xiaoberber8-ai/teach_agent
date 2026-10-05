"""生成 M1 自测用书：一本 6 页的《高中数学三角与数列小册》文字版 PDF。

内容刻意覆盖"定理 / 证明 / 例题 / 另一主题 / 书外问题"五种检索情形，
用于验证切块页码、公式保留与语义召回是否正确。

用法：
    uv run python scripts/make_sample_pdf.py
产物：
    data/samples/sample_math_booklet.pdf
"""

from __future__ import annotations

from pathlib import Path

import pymupdf as fitz  # PyMuPDF

FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
OUTPUT = (
    Path(__file__).resolve().parents[1] / "data" / "samples" / "sample_math_booklet.pdf"
)

PAGES: list[str] = [
    # 第 1 页：封面 + 目录
    """高中数学三角与数列小册

（M1 检索链路自测用书）


目录

第一章  余弦定理
    1.1 定理内容
    1.2 定理证明
    1.3 应用例题
第二章  正弦定理
第三章  等差数列

本小册子仅用于检验 teach_agent 的书籍解析、切块与语义检索能力。""",
    # 第 2 页：余弦定理内容
    """第一章  余弦定理

1.1 定理内容

余弦定理揭示了三角形三边与其中一个内角之间的关系。
设三角形 ABC 的三个内角 A、B、C 所对的边分别为 a、b、c，则有：

$a^2 = b^2 + c^2 - 2bc\\cos A$
$b^2 = a^2 + c^2 - 2ac\\cos B$
$c^2 = a^2 + b^2 - 2ab\\cos C$

也就是说，三角形任意一边的平方，等于另外两边平方的和，
减去这两边与它们夹角的余弦之积的两倍。

当角 C 为直角时，cos C = 0，余弦定理就退化为勾股定理
$c^2 = a^2 + b^2$，因此勾股定理可以看作余弦定理的特殊情形。

余弦定理可以解决两类基本问题：已知三边求内角；已知两边及其夹角求第三边。""",
    # 第 3 页：余弦定理证明
    """1.2 定理证明

下面用向量方法证明 $c^2 = a^2 + b^2 - 2ab\\cos C$。

在三角形 ABC 中，把边 CA、CB 看作两个从点 C 出发的向量，
则向量 BA = 向量 CA 减去向量 CB。

对向量 BA 取自身的数量积（即模的平方）：

$c^2 = |\\overrightarrow{BA}|^2
     = |\\overrightarrow{CA}|^2 + |\\overrightarrow{CB}|^2
       - 2\\,\\overrightarrow{CA}\\cdot\\overrightarrow{CB}$

两个向量数量积等于它们的模与夹角余弦的乘积，因此：

$c^2 = b^2 + a^2 - 2ab\\cos C$

这就是余弦定理。其余两个等式用同样的方法，
分别从顶点 A、顶点 B 出发取数量积即可得到，证明完毕。

证明过程中最关键的一步，是把"线段长度"转化为"向量模的平方"，
从而把几何问题化成向量的代数运算。""",
    # 第 4 页：例题
    """1.3 应用例题

例题：在三角形 ABC 中，已知边 a = 5，边 b = 7，
两边的夹角 C = 60°，求第三条边 c 的长度。

解：题目给出了两边以及这两边所夹的角，求第三条边，
直接使用余弦定理：

$c^2 = a^2 + b^2 - 2ab\\cos C$
$c^2 = 25 + 49 - 2\\times 5\\times 7\\times \\cos 60°$
$c^2 = 74 - 70\\times 0.5 = 74 - 35 = 39$

所以第三条边 $c = \\sqrt{39}$，约等于 6.24。

解题经验：如果题目条件是"两边及夹角"，优先考虑余弦定理；
如果条件是"两角及一边"或"两边及其中一边的对角"，则考虑正弦定理。
求出边长后应检查三角不等式，确认答案能构成合理的三角形。""",
    # 第 5 页：正弦定理
    """第二章  正弦定理

在一个三角形中，各边的长度与它所对角的正弦值之比相等，
这个比值等于三角形外接圆的直径 2R：

$\\dfrac{a}{\\sin A} = \\dfrac{b}{\\sin B} = \\dfrac{c}{\\sin C} = 2R$

正弦定理适合处理两类问题：
一是已知两个角和任意一条边，求其余边和角；
二是已知两条边和其中一条边所对的角，求其他边角。

使用第二类条件时可能出现两解、一解或无解的情况，
需要根据"大边对大角"的原则判断三角形是否存在，
这是正弦定理与余弦定理在应用上最重要的区别。

例如在三角形中已知 a = 10，A = 30°，B = 45°，
则由内角和先求出 C = 105°，再用正弦定理的比例关系求出 b 与 c。""",
    # 第 6 页：等差数列
    """第三章  等差数列

一般地，如果一个数列从第 2 项起，每一项与它前一项的差
都等于同一个常数，这个数列就叫做等差数列，
这个常数叫做公差，通常用字母 d 表示。

设首项为 $a_1$，公差为 d，则第 n 项为：

$a_n = a_1 + (n-1)d$

等差数列前 n 项的和为：

$S_n = \\dfrac{n(a_1 + a_n)}{2} = na_1 + \\dfrac{n(n-1)}{2}d$

例如首项为 2、公差为 3 的等差数列：2, 5, 8, 11, 14, …，
第 10 项等于 2 + 9×3 = 29，前 10 项之和为 155。

判断一个数列是不是等差数列，只需验证相邻两项之差
$a_{n+1} - a_n$ 是否为与 n 无关的常数。""",
]


def build() -> Path:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()

    for index, body in enumerate(PAGES, start=1):
        page = doc.new_page(width=595, height=842)  # A4
        page.insert_font(fontname="CJK", fontfile=FONT_PATH)
        # 页眉页码
        page.insert_text(
            (72, 40), f"高中数学三角与数列小册 · 第 {index} 页",
            fontname="CJK", fontsize=9, color=(0.45, 0.45, 0.45),
        )
        rect = fitz.Rect(72, 60, 523, 780)
        page.insert_textbox(
            rect,
            body,
            fontname="CJK",
            fontsize=12,
            lineheight=1.7,
            color=(0, 0, 0),
        )

    doc.save(str(OUTPUT))
    doc.close()
    print(f"已生成自测用书：{OUTPUT}（{len(PAGES)} 页）")
    return OUTPUT


if __name__ == "__main__":
    build()
