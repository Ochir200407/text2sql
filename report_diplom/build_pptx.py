"""Хамгаалалтын танилцуулга (ШУТИС-ийн CS-Thesis загвар дээр). Бүх текст Arial, хамгийн жижиг нь 20pt.

    python build_pptx.py            # tpl.pptx (загвар) → Text2SQL_hamgaalalt.pptx
"""
import copy
import re
import zipfile

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

TPL = "tpl.pptx"
OUT = "Text2SQL_hamgaalalt.pptx"
STUDIO = r"C:\Users\ochir.b\Desktop\claude\text2sql-new\report_diplom\Figures\studio.jpg"
FOOTER = "LLM-д суурилсан Text-to-SQL агент"
FONT = "Arial"
B = 20        # хамгийн жижиг (үндсэн текст)
H = 24        # картын гарчиг
BIG = 44      # том тоо
TITLE = 32

NAVY = RGBColor(0x20, 0x1B, 0x6D)
GOLD = RGBColor(0xF2, 0xB1, 0x00)
INK = RGBColor(0x1F, 0x23, 0x2B)
MUTED = RGBColor(0x4A, 0x52, 0x60)
TINT = RGBColor(0xEE, 0xED, 0xF7)
GOLDT = RGBColor(0xFD, 0xF3, 0xD6)
GRAY = RGBColor(0xB8, 0xBC, 0xC6)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
GREEN = RGBColor(0x1B, 0x7F, 0x3B)
RED = RGBColor(0xB4, 0x23, 0x18)
AMBER = RGBColor(0xA8, 0x6A, 0x00)
CODE_BG = RGBColor(0x26, 0x2B, 0x3A)

prs = Presentation(TPL)
tpl_slides = list(prs.slides)
footer_src = [sh for sh in tpl_slides[3].shapes if sh.is_placeholder and sh.placeholder_format.idx in (10, 11, 12)]
L_TITLE_ONLY = prs.slide_layouts[5]

X0 = 1.12   # агуулгын зүүн ирмэг (inch)
XW = 11.1   # агуулгын өргөн
Y0 = 1.3    # агуулгын дээд ирмэг


def fix_runs(tf, size=None, color=None):
    for p in tf.paragraphs:
        for r in p.runs:
            r.font.name = FONT
            if size:
                r.font.size = Pt(size)
            if color is not None:
                r.font.color.rgb = color


def add_footer(slide):
    for sh in footer_src:
        slide.shapes._spTree.append(copy.deepcopy(sh._element))
    for sh in slide.shapes:
        if sh.is_placeholder and sh.placeholder_format.idx == 11:
            sh.text_frame.paragraphs[0].runs[0].text = FOOTER
            for r in sh.text_frame.paragraphs[0].runs[1:]:
                r.text = ""


def new_slide(title):
    s = prs.slides.add_slide(L_TITLE_ONLY)
    s.shapes.title.text = title
    fix_runs(s.shapes.title.text_frame, TITLE, NAVY)
    add_footer(s)
    return s


def tb(slide, x, y, w, h, paras, size=B, color=INK, bold=False, align=PP_ALIGN.LEFT,
       anchor=MSO_ANCHOR.TOP, space=6):
    """paras: str | list[str] | list[list[(text, opts)]]"""
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = Inches(0.05)
    tf.margin_top = tf.margin_bottom = Inches(0.03)
    if isinstance(paras, str):
        paras = [paras]
    for i, p in enumerate(paras):
        para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        para.alignment = align
        para.space_after = Pt(space)
        for text, o in (p if isinstance(p, list) else [(p, {})]):
            r = para.add_run()
            r.text = text
            r.font.name = FONT
            r.font.size = Pt(max(B, o.get("size", size)))
            r.font.bold = o.get("bold", bold)
            r.font.color.rgb = o.get("color", color)
    return box


def rect(slide, x, y, w, h, fill, shape=MSO_SHAPE.ROUNDED_RECTANGLE, line=None, radius=0.08):
    sp = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    sp.fill.solid()
    sp.fill.fore_color.rgb = fill
    if line is None:
        sp.line.fill.background()
    else:
        sp.line.color.rgb = line
        sp.line.width = Pt(1.5)
    sp.shadow.inherit = False
    if shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        sp.adjustments[0] = radius
    sp.text_frame.text = ""
    return sp


def shape_text(sp, lines, size=B, color=INK, bold=False, align=PP_ALIGN.CENTER):
    tf = sp.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf.margin_left = tf.margin_right = Inches(0.08)
    for i, ln in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        for text, o in (ln if isinstance(ln, list) else [(ln, {})]):
            r = p.add_run()
            r.text = text
            r.font.name = FONT
            r.font.size = Pt(max(B, o.get("size", size)))
            r.font.bold = o.get("bold", bold)
            r.font.color.rgb = o.get("color", color)


def circle_num(slide, x, y, d, n, fill=NAVY, color=WHITE, size=B):
    c = rect(slide, x, y, d, d, fill, shape=MSO_SHAPE.OVAL)
    shape_text(c, [str(n)], size=size, color=color, bold=True)
    c.text_frame.margin_left = c.text_frame.margin_right = 0
    return c


def arrow(slide, x1, y1, x2, y2, color=NAVY, width=2.5):
    c = slide.shapes.add_connector(1, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    c.line.color.rgb = color
    c.line.width = Pt(width)
    ln = c.line._get_or_add_ln()
    ln.append(ln.makeelement(qn("a:tailEnd"), {"type": "triangle", "w": "med", "len": "med"}))


def table(slide, x, y, w, rows, col_w, row_h=0.55, hl_last=False):
    nr, nc = len(rows), len(rows[0])
    t = slide.shapes.add_table(nr, nc, Inches(x), Inches(y), Inches(w), Inches(row_h * nr)).table
    tblPr = t._tbl.tblPr
    style = tblPr.find(qn("a:tableStyleId"))
    if style is None:
        style = tblPr.makeelement(qn("a:tableStyleId"), {})
        tblPr.append(style)
    style.text = "{5940675A-B579-460E-94D1-54222C63F5DA}"
    for j, cw in enumerate(col_w):
        t.columns[j].width = Inches(cw)
    for i in range(nr):
        t.rows[i].height = Inches(row_h)
        for j in range(nc):
            c = t.cell(i, j)
            c.margin_left = c.margin_right = Inches(0.1)
            c.margin_top = c.margin_bottom = Inches(0.03)
            c.vertical_anchor = MSO_ANCHOR.MIDDLE
            val, o = rows[i][j] if isinstance(rows[i][j], tuple) else (rows[i][j], {})
            c.text = val
            head, last = i == 0, hl_last and i == nr - 1
            for r in c.text_frame.paragraphs[0].runs:
                r.font.name = FONT
                r.font.size = Pt(B)
                r.font.bold = head or last or o.get("bold", False)
                r.font.color.rgb = WHITE if head else o.get("color", NAVY if last else INK)
            c.fill.solid()
            c.fill.fore_color.rgb = NAVY if head else GOLDT if last else (WHITE if i % 2 else RGBColor(0xF6, 0xF6, 0xFA))
    return t


def style_chart(ch):
    ch.font.name = FONT
    ch.font.size = Pt(B)
    ch.font.color.rgb = INK


def bar_chart(slide, x, y, w, h, cats, vals, colors, maxv=None, fmt='0.0"%"'):
    cd = CategoryChartData()
    cd.categories = cats
    cd.add_series("s", vals)
    ch = slide.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(x), Inches(y), Inches(w), Inches(h), cd).chart
    style_chart(ch)
    ch.has_legend = False
    ch.has_title = False
    plot = ch.plots[0]
    plot.gap_width = 45
    plot.has_data_labels = True
    dl = plot.data_labels
    dl.number_format = fmt
    dl.number_format_is_linked = False
    dl.position = XL_LABEL_POSITION.OUTSIDE_END
    dl.font.size = Pt(H)
    dl.font.bold = True
    dl.font.color.rgb = INK
    va = ch.value_axis
    va.visible = False
    va.has_major_gridlines = False
    va.minimum_scale = 0
    if maxv:
        va.maximum_scale = maxv
    ca = ch.category_axis
    ca.tick_labels.font.size = Pt(B)
    ca.tick_labels.font.color.rgb = INK
    ca.format.line.color.rgb = GRAY
    for i, c in enumerate(colors):
        pt = plot.series[0].points[i]
        pt.format.fill.solid()
        pt.format.fill.fore_color.rgb = c
    return ch


def hbar(slide, x, y, label, val, maxv, barw, color, lw=3.0, unit="", vfmt="{:.1f}"):
    tb(slide, x, y, lw, 0.5, label, anchor=MSO_ANCHOR.MIDDLE)
    bw = barw * val / maxv
    rect(slide, x + lw + 0.1, y + 0.07, bw, 0.36, color, shape=MSO_SHAPE.RECTANGLE)
    tb(slide, x + lw + 0.2 + bw, y, 1.3, 0.5, vfmt.format(val) + unit, size=H, bold=True, anchor=MSO_ANCHOR.MIDDLE)


def note(slide, y, runs, h=0.7, fill=GOLDT):
    rect(slide, X0, y, XW, h, fill)
    tb(slide, X0 + 0.25, y, XW - 0.5, h, [runs], anchor=MSO_ANCHOR.MIDDLE)


# ================================================================ 1. Нүүр
title_slide = tpl_slides[1]
title_slide.shapes.title.text = "Том хэлний загварт (LLM) суурилсан\vText-to-SQL агентыг хөгжүүлэх нь"
fix_runs(title_slide.shapes.title.text_frame, 36)
sub = [sh for sh in title_slide.shapes if sh.is_placeholder and sh.placeholder_format.idx == 1][0]
ps = sub.text_frame.paragraphs
for i, txt in {3: "Илтгэгч: Батчулуун Очир (B221960026)", 4: "Хөтөлбөр: Хиймэл оюун / Компьютерын ухаан",
               5: "Удирдагч: Доктор (Ph.D) Д.Золзаяа", 6: "Зөвлөх: Б.Батзолбоо"}.items():
    ps[i].runs[0].text = txt
    for r in ps[i].runs[1:]:
        r.text = ""
for sh in title_slide.shapes:
    if sh.has_text_frame and sh is not title_slide.shapes.title:
        for p in sh.text_frame.paragraphs:
            for r in p.runs:
                r.font.name = FONT
                if r.font.size is None or r.font.size.pt < B:
                    r.font.size = Pt(B)

# ================================================================ 2. Агуулга
s = new_slide("Агуулга")
for k, h in enumerate(["Асуудал ба зорилго", "Ижил төстэй системүүд", "Систем хэрхэн ажилладаг вэ",
                       "Өгөгдөл ба загварууд", "Туршилтын үр дүн", "Дүгнэлт ба цаашдын ажил"]):
    col, row = k % 2, k // 2
    x, y = X0 + col * 5.7, Y0 + 0.2 + row * 1.6
    rect(s, x, y, 5.4, 1.3, TINT)
    circle_num(s, x + 0.3, y + 0.3, 0.7, k + 1, size=H)
    tb(s, x + 1.25, y, 4.0, 1.3, h, size=H, bold=True, color=NAVY, anchor=MSO_ANCHOR.MIDDLE)

# ================================================================ 3. Асуудал (жишээгээр)
s = new_slide("Асуудал: тоо мэдээ авахын тулд SQL хэрэгтэй")
# асуулт → SQL → хариулт
q = rect(s, X0, Y0 + 0.2, 3.5, 1.5, TINT, radius=0.25)
shape_text(q, [[("Ажилтан асууна", {"bold": True, "color": NAVY})], "«Өнгөрсөн сард хэдэн зээл олгосон бэ?»"])
code = rect(s, X0 + 4.15, Y0 + 0.2, 3.9, 1.5, CODE_BG)
shape_text(code, [[("SELECT count(*)", {"color": WHITE})], [("FROM loans", {"color": WHITE})],
                  [("WHERE month = 9", {"color": GOLD})]], align=PP_ALIGN.LEFT)
a = rect(s, X0 + 8.7, Y0 + 0.2, 2.4, 1.5, GOLDT, radius=0.25)
shape_text(a, [[("1 284", {"bold": True, "size": BIG, "color": NAVY})], "зээл"])
arrow(s, X0 + 3.5, Y0 + 0.95, X0 + 4.15, Y0 + 0.95)
arrow(s, X0 + 8.05, Y0 + 0.95, X0 + 8.7, Y0 + 0.95)
tb(s, X0 + 4.15, Y0 + 1.75, 3.9, 0.5, "Үүнийг одоо шинжээч бичдэг", color=MUTED, align=PP_ALIGN.CENTER)
tb(s, X0 + 8.7, Y0 + 1.75, 2.4, 0.5, "(жишээ)", color=MUTED, align=PP_ALIGN.CENTER)
for k, (h, d) in enumerate([("Хүлээлт", "SQL мэддэг шинжээчээс хамаарна"),
                            ("Хэл", "Монгол хэлний Text2SQL өгөгдөл, загвар алга"),
                            ("Нууцлал", "Санхүүгийн өгөгдлийг гадны үйлчилгээнд илгээж болохгүй")]):
    x = X0 + k * 3.75
    rect(s, x, Y0 + 2.6, 3.5, 2.5, TINT)
    tb(s, x + 0.25, Y0 + 2.75, 3.0, 0.5, h, size=H, bold=True, color=NAVY)
    tb(s, x + 0.25, Y0 + 3.35, 3.0, 1.7, d)

# ================================================================ 4. Зорилго ба зорилтууд
s = new_slide("Зорилго ба зорилтууд")
note(s, Y0, [("Зорилго: ", {"bold": True, "color": NAVY}),
             ("монгол хэлээр асуусан асуултыг SQL болгож хариулдаг, нэг GPU-д ажиллах систем бүтээх", {})], h=1.0)
for k, (h, d) in enumerate([("Мэдлэгийн сан", "схем, нэр томьёо, жишээ"),
                            ("Загварт өгөх заавар", "алхам алхмаар ажиллах дамжлага"),
                            ("Нэмэлт сургалт", "монгол хэл, санхүүгийн хэллэг"),
                            ("Найдвартай байдал", "сонгох, засах, татгалзах"),
                            ("Үнэлгээ", "500 асуултаар хэмжих")]):
    x = X0 + k * 2.24
    rect(s, x, Y0 + 1.4, 2.06, 3.7, TINT)
    circle_num(s, x + 0.68, Y0 + 1.65, 0.7, k + 1, size=H)
    tb(s, x + 0.1, Y0 + 2.55, 1.86, 1.0, h, size=B, bold=True, color=NAVY, align=PP_ALIGN.CENTER)
    tb(s, x + 0.1, Y0 + 3.6, 1.86, 1.4, d, color=MUTED, align=PP_ALIGN.CENTER)

# ================================================================ 5. Таамаглал
s = new_slide("Шалгасан 4 таамаглал")
for k, (h, d) in enumerate([("H1", "Мэдлэгийн сангаас хайж өгөх нь нарийвчлалыг 10-аас дээш пунктээр өсгөнө"),
                            ("H2", "Асуултыг англи хэл рүү орчуулж өгөх нь монголоор шууд өгснөөс илүү"),
                            ("H3", "Өөрийн өгөгдлийн сангаар нэмж сургасан загвар илүү сайн"),
                            ("H4", "Олон хувилбараас санал хураавал илүү зөв хариулна")]):
    col, row = k % 2, k // 2
    x, y = X0 + col * 5.7, Y0 + 0.1 + row * 2.6
    rect(s, x, y, 5.4, 2.35, TINT)
    b = rect(s, x + 0.3, y + 0.3, 1.0, 0.7, NAVY)
    shape_text(b, [h], size=H, color=WHITE, bold=True)
    tb(s, x + 0.3, y + 1.1, 4.8, 1.2, d)

# ================================================================ 6. Ижил төстэй системүүд
s = new_slide("Ижил төстэй системүүд")
rows = [["Систем", "Хаана ажилладаг", "Нээлттэй", "Монгол хэл"],
        ["Snowflake Cortex Analyst", "Snowflake үүл", "үгүй", "хэмжээгүй"],
        ["Databricks Genie", "Databricks үүл", "үгүй", "хэмжээгүй"],
        ["Power BI Copilot", "Microsoft үүл", "үгүй", "хэмжээгүй"],
        ["Vanna", "Өөрийн сервер", "тийм", "хэмжээгүй"],
        ["Wren AI", "Өөрийн сервер", "тийм", "хэмжээгүй"],
        ["Энэ ажил", "Байгууллага дотор", "тийм", "хэмжсэн"]]
table(s, X0, Y0, XW, rows, [3.6, 3.3, 2.0, 2.2], row_h=0.56, hl_last=True)
note(s, Y0 + 4.5, [("Цоорхой: ", {"bold": True, "color": NAVY}),
                    ("монгол хэл дээр хэмжсэн, өгөгдлөө гадагш гаргадаггүй шийдэл байгаагүй", {})], h=0.85)

# ================================================================ 7. Архитектур
s = new_slide("Систем хэрхэн ажилладаг вэ")
bw, bh, gap = 2.45, 1.25, 0.43
top_y, bot_y = Y0 + 0.95, Y0 + 3.2
top = [("Асуулт", "кирилл, латин", False), ("1. Шалгах", "татгалзах дүрэм", False),
       ("2. Орчуулах", "англи хэл рүү", True), ("3. Мэдлэг хайх", "схем, жишээ", False)]
bot = [("7. Хариулах", "тоо эсвэл татгалзал", False), ("6. Санал хураах", "8 хувилбараас", False),
       ("5. Шалгах, засах", "алдааг засна", False), ("4. SQL бичих", "Arctic загвар", True)]
xs = [X0 + i * (bw + gap) for i in range(4)]
for row_y, items in ((top_y, top), (bot_y, bot)):
    for i, (h, d, hl) in enumerate(items):
        b = rect(s, xs[i], row_y, bw, bh, GOLDT if hl else TINT, line=GOLD if hl else None)
        shape_text(b, [[(h, {"bold": True, "color": NAVY})], [(d, {"color": MUTED})]])
for i in range(3):
    arrow(s, xs[i] + bw, top_y + bh / 2, xs[i + 1], top_y + bh / 2)
    arrow(s, xs[i + 1], bot_y + bh / 2, xs[i] + bw, bot_y + bh / 2)
arrow(s, xs[3] + bw / 2, top_y + bh, xs[3] + bw / 2, bot_y)
kb = rect(s, xs[3], Y0 + 0.0, bw, 0.75, WHITE, shape=MSO_SHAPE.CAN, line=NAVY)
shape_text(kb, ["Мэдлэгийн сан"], color=NAVY, bold=True)
arrow(s, xs[3] + bw / 2, Y0 + 0.75, xs[3] + bw / 2, top_y)
pg = rect(s, xs[2], bot_y + bh + 0.3, bw, 0.75, WHITE, shape=MSO_SHAPE.CAN, line=NAVY)
shape_text(pg, ["PostgreSQL"], color=NAVY, bold=True)
arrow(s, xs[2] + bw / 2, bot_y + bh + 0.3, xs[2] + bw / 2, bot_y + bh)
tb(s, X0, bot_y + bh + 0.45, 4.6, 0.5, [[("■ ", {"color": GOLD}), ("загвар ажилладаг алхам", {})]], color=MUTED)

# ================================================================ 8. Өгөгдөл
s = new_slide("Өгөгдөл")
stats = [("28", "хүснэгт (view)"), ("635", "жишээ асуулт"), ("500", "үнэлгээний асуулт"), ("8 500", "орчуулгын жишээ")]
sw = (XW - 3 * 0.3) / 4
for k, (n, l) in enumerate(stats):
    x = X0 + k * (sw + 0.3)
    rect(s, x, Y0, sw, 1.8, NAVY if k == 2 else TINT)
    tb(s, x, Y0 + 0.15, sw, 0.9, n, size=BIG, bold=True, color=WHITE if k == 2 else NAVY, align=PP_ALIGN.CENTER)
    tb(s, x, Y0 + 1.1, sw, 0.6, l, color=WHITE if k == 2 else MUTED, align=PP_ALIGN.CENTER)
cd = CategoryChartData()
cd.categories = ["Хөгжүүлэлт (dev)", "Шинэ асуулт (test)"]
for name, vals in (("Хариулах боломжтой", (224, 151)), ("Боломжгүй", (38, 24)), ("Хоёрдмол", (38, 25))):
    cd.add_series(name, vals)
ch = s.shapes.add_chart(XL_CHART_TYPE.BAR_STACKED, Inches(X0), Inches(Y0 + 2.1), Inches(XW), Inches(2.6), cd).chart
style_chart(ch)
ch.has_legend = True
ch.legend.position = XL_LEGEND_POSITION.BOTTOM
ch.legend.include_in_layout = False
ch.legend.font.size = Pt(B)
plot = ch.plots[0]
plot.gap_width = 40
plot.has_data_labels = True
plot.data_labels.font.size = Pt(B)
plot.data_labels.font.bold = True
plot.data_labels.font.color.rgb = WHITE
plot.data_labels.position = XL_LABEL_POSITION.CENTER
for ser, c in zip(plot.series, (NAVY, RED, AMBER)):
    ser.format.fill.solid()
    ser.format.fill.fore_color.rgb = c
ch.value_axis.visible = False
ch.value_axis.has_major_gridlines = False
ch.category_axis.tick_labels.font.size = Pt(B)
ch.category_axis.reverse_order = True
tb(s, X0, Y0 + 4.8, XW, 0.5, "Бодит харилцагчийн мэдээлэл ашиглаагүй — синтетик өгөгдөл", color=MUTED)

# ================================================================ 9. Загварууд
s = new_slide("Ашигласан загварууд")
for k, (role, name, d, hl) in enumerate([
        ("SQL бичих", "Arctic-Text2SQL-R1-7B", "нэмэлт сургасан (LoRA)", True),
        ("Орчуулах", "MADLAD-400-3B", "санхүүгийн хэллэгээр сургасан", True),
        ("Мэдлэг хайх", "BM25 + BGE-M3", "түлхүүр үг ба утгаар хайна", False)]):
    x = X0 + k * 3.75
    rect(s, x, Y0, 3.5, 2.9, GOLDT if hl else TINT)
    tb(s, x + 0.25, Y0 + 0.2, 3.0, 0.5, role, size=H, bold=True, color=NAVY)
    tb(s, x + 0.25, Y0 + 0.85, 3.0, 1.0, name, size=H, bold=True)
    tb(s, x + 0.25, Y0 + 1.9, 3.0, 0.9, d, color=MUTED)
tb(s, X0, Y0 + 3.2, XW, 0.5, "Үнэлгээ", size=H, bold=True, color=NAVY)
for k, (h, d) in enumerate([("EX", "SQL-ийн үр дүн зөв хариутай таарсан хувь"),
                            ("McNemar", "хоёр тохиргооны ялгаа санамсаргүй эсэх")]):
    x = X0 + k * 5.7
    rect(s, x, Y0 + 3.75, 5.4, 1.35, TINT)
    tb(s, x + 0.25, Y0 + 3.75, 1.8, 1.35, h, size=H, bold=True, color=NAVY, anchor=MSO_ANCHOR.MIDDLE)
    tb(s, x + 2.0, Y0 + 3.75, 3.2, 1.35, d, anchor=MSO_ANCHOR.MIDDLE)

# ================================================================ 10. Dev ахиц
s = new_slide("Үр дүн: алхам бүр хэр нэмэр өгсөн бэ")
cats = ["Суурь", "Мэдлэг\nхайлт", "Орчуулга", "Засвар", "8 хувилбар", "Дүрэм"]
vals = [2.3, 14.3, 27.0, 36.7, 41.0, 53.0]
bar_chart(s, X0 - 0.1, Y0 - 0.1, 7.9, 4.75, cats, vals, [GRAY, NAVY, NAVY, GRAY, GRAY, GOLD], maxv=62)
tb(s, X0, Y0 + 4.7, 7.8, 0.5, "Хөгжүүлэлтийн 300 асуулт, зөв хариултын хувь", color=MUTED)
xr = X0 + 8.1
rect(s, xr, Y0, XW - 8.1, 5.2, TINT)
tb(s, xr + 0.25, Y0 + 0.15, XW - 8.6, 0.5, "Их нэмэр", size=H, bold=True, color=NAVY)
for k, (n, l) in enumerate([("+12.0", "мэдлэг хайлт"), ("+11.3", "англи хэл рүү орчуулга"), ("+1.4", "нэмэлт сургалт")]):
    y = Y0 + 0.8 + k * 1.45
    tb(s, xr + 0.25, y, XW - 8.6, 0.75, n, size=BIG, bold=True, color=NAVY if k < 2 else MUTED)
    tb(s, xr + 0.25, y + 0.75, XW - 8.6, 0.6, l)

# ================================================================ 11. Test
s = new_slide("Шинэ асуулт дээр: 21.0% → 46.5%")
cats = ["Сургасан\nзагвар", "+ Орчуулга", "+ 8 хувилбар", "+ Дүрэм", "+ Тодруулга"]
vals = [21.0, 34.0, 38.5, 45.0, 46.5]
bar_chart(s, X0 - 0.1, Y0 - 0.1, 7.3, 4.75, cats, vals, [GRAY, NAVY, GRAY, GRAY, GOLD], maxv=55)
tb(s, X0, Y0 + 4.7, 7.2, 0.5, "Шинэ 200 асуулт (test), зөв хариултын хувь", color=MUTED)
xr = X0 + 7.5
rect(s, xr, Y0, XW - 7.5, 5.2, TINT)
tb(s, xr + 0.25, Y0 + 0.15, XW - 8.0, 0.5, "Ялгаа бодит уу?", size=H, bold=True, color=NAVY)
for k, (h, p) in enumerate([("+ Орчуулга", "p = 0.0009"), ("+ Дүрэм", "p = 0.001"), ("+ 8 хувилбар", "p = 0.12")]):
    y = Y0 + 0.9 + k * 1.15
    tb(s, xr + 0.25, y, XW - 8.0, 0.5, h, bold=True)
    tb(s, xr + 0.25, y + 0.45, XW - 8.0, 0.5, p, size=H, color=GREEN if k < 2 else AMBER, bold=True)
tb(s, xr + 0.25, Y0 + 4.35, XW - 8.0, 0.8, "p < 0.05 бол ялгаа санамсаргүй биш", color=MUTED)

# ================================================================ 12. Алдааны шалтгаан
s = new_slide("Алдааны гол шалтгаан — орчуулга")
cd = CategoryChartData()
cd.categories = ["Орчуулга", "Бусад"]
cd.add_series("s", (38, 62))
ch = s.shapes.add_chart(XL_CHART_TYPE.DOUGHNUT, Inches(X0), Inches(Y0), Inches(4.6), Inches(4.4), cd).chart
style_chart(ch)
ch.has_title = False
ch.has_legend = True
ch.legend.position = XL_LEGEND_POSITION.BOTTOM
ch.legend.include_in_layout = False
ch.legend.font.size = Pt(B)
plot = ch.plots[0]
plot.has_data_labels = True
plot.data_labels.show_value = True
plot.data_labels.number_format = '0"%"'
plot.data_labels.number_format_is_linked = False
plot.data_labels.font.size = Pt(H)
plot.data_labels.font.bold = True
plot.data_labels.font.color.rgb = WHITE
for i, c in enumerate((RED, GRAY)):
    pt = plot.series[0].points[i]
    pt.format.fill.solid()
    pt.format.fill.fore_color.rgb = c
xr = X0 + 5.0
tb(s, xr, Y0, XW - 5.0, 0.5, "Жишээ", size=H, bold=True, color=NAVY)
for k, (src, bad, good) in enumerate([("«төгрөгийн»", "Tenge", "MNT"),
                                      ("«барагдаж хаагдсан»", "closed", "resolved")]):
    y = Y0 + 0.7 + k * 1.6
    rect(s, xr, y, XW - 5.0, 1.35, TINT)
    tb(s, xr + 0.25, y + 0.1, 2.8, 1.15, src, bold=True, anchor=MSO_ANCHOR.MIDDLE)
    tb(s, xr + 3.1, y + 0.1, 1.5, 1.15, [[("✗ ", {"color": RED, "bold": True}), (bad, {})]], anchor=MSO_ANCHOR.MIDDLE)
    tb(s, xr + 4.5, y + 0.1, 1.6, 1.15, [[("✓ ", {"color": GREEN, "bold": True}), (good, {})]], anchor=MSO_ANCHOR.MIDDLE)
rect(s, xr, Y0 + 3.95, XW - 5.0, 1.2, GOLDT)
tb(s, xr + 0.25, Y0 + 3.95, XW - 5.5, 1.2, "Шийдвэр: орчуулагчийг санхүүгийн хэллэгээр нэмж сургах",
   bold=True, color=NAVY, anchor=MSO_ANCHOR.MIDDLE)

# ================================================================ 13. Сургасан орчуулагч
s = new_slide("Сургасан орчуулагч: 46.9% → 56.7%")
pw = (XW - 0.3) / 2
for k, (h, rowsb, mx, unit) in enumerate([
        ("Орчуулгын чанар (chrF++)", [("Qwen", 58.0, GRAY), ("MADLAD", 53.5, GRAY), ("MADLAD, сургасан", 74.8, NAVY)], 80, ""),
        ("Зөв хариулт (dev, 224)", [("Qwen", 46.9, GRAY), ("MADLAD", 36.2, GRAY), ("MADLAD, сургасан", 56.7, NAVY)], 60, "%")]):
    x = X0 + k * (pw + 0.3)
    rect(s, x, Y0, pw, 3.55, TINT)
    tb(s, x + 0.25, Y0 + 0.15, pw - 0.5, 0.5, h, size=H, bold=True, color=NAVY)
    for i, (lbl, v, c) in enumerate(rowsb):
        hbar(s, x + 0.25, Y0 + 0.95 + i * 0.8, lbl, v, mx, 1.4, c, lw=2.45, unit=unit)
note(s, Y0 + 3.85, [("+22 асуулт зөв болсон. ", {"bold": True, "color": NAVY}),
                    ("Хөгжүүлэлтийн асуулт дээрх дүн — шинэ асуулт дээр шалгаж байна.", {})], h=1.3)

# ================================================================ 14. Таамаглалын биелэлт
s = new_slide("Таамаглалын биелэлт")
for k, (h, d, verdict, c) in enumerate([
        ("H1", "Мэдлэг хайлт: +12.0 пункт", "Батлагдсан", GREEN),
        ("H2", "Англи хэл рүү орчуулга: +11.3 пункт", "Батлагдсан", GREEN),
        ("H3", "Нэмэлт сургалт: +1.4 пункт", "Батлагдаагүй", RED),
        ("H4", "Санал хураалт: зөвхөн хариулах асуултад ахисан", "Хэсэгчлэн", AMBER)]):
    y = Y0 + k * 1.3
    rect(s, X0, y, XW, 1.1, TINT)
    b = rect(s, X0 + 0.25, y + 0.2, 1.0, 0.7, NAVY)
    shape_text(b, [h], size=H, color=WHITE, bold=True)
    tb(s, X0 + 1.5, y, 6.6, 1.1, d, size=H, anchor=MSO_ANCHOR.MIDDLE)
    v = rect(s, X0 + XW - 2.9, y + 0.2, 2.65, 0.7, c)
    shape_text(v, [verdict], color=WHITE, bold=True)

# ================================================================ 15. Studio
s = new_slide("Text2SQL Studio — туршилтын систем")
iw = 7.9
ih = iw * 812 / 1510
s.shapes.add_picture(STUDIO, Inches(X0), Inches(Y0), Inches(iw), Inches(ih))
xr = X0 + iw + 0.35
for k, (h, d) in enumerate([("Хариулт", "тоо ба SQL"), ("Мэдлэг", "схем, жишээ"),
                            ("Алхам бүр", "хугацаа, алдаа")]):
    y = Y0 + k * 1.45
    circle_num(s, xr, y + 0.1, 0.6, k + 1)
    tb(s, xr + 0.75, y, XW - iw - 1.1, 0.55, h, size=B, bold=True, color=NAVY)
    tb(s, xr + 0.75, y + 0.55, XW - iw - 1.1, 0.6, d, color=MUTED)

# ================================================================ 16. Дүгнэлт
s = new_slide("Дүгнэлт")
bw3 = (XW - 0.6) / 3
for k, (n, l) in enumerate([("46.5%", "шинэ асуултын зөв хариулт (21.0%-иас)"),
                            ("+12 / +11", "мэдлэг хайлт ба орчуулгын нэмэр"),
                            ("74.8", "сургасан орчуулагчийн оноо (58.0-аас)")]):
    x = X0 + k * (bw3 + 0.3)
    rect(s, x, Y0, bw3, 2.2, NAVY if k == 0 else TINT)
    tb(s, x, Y0 + 0.2, bw3, 0.9, n, size=BIG, bold=True, color=WHITE if k == 0 else NAVY, align=PP_ALIGN.CENTER)
    tb(s, x + 0.2, Y0 + 1.15, bw3 - 0.4, 1.0, l, color=WHITE if k == 0 else MUTED, align=PP_ALIGN.CENTER)
for k, c in enumerate(["Монгол хэлний Text2SQL-ийн 500 асуултын үнэлгээний сан",
                       "Нөлөө бүрийг статистикаар хэмжсэн: мэдлэг хайлт, орчуулга хамгийн чухал",
                       "Санхүүгийн хэллэгт сургасан орчуулагч ба туршилтын систем"]):
    y = Y0 + 2.55 + k * 0.85
    circle_num(s, X0, y + 0.08, 0.55, k + 1, fill=GOLD, color=NAVY)
    tb(s, X0 + 0.8, y, XW - 0.8, 0.7, c, anchor=MSO_ANCHOR.MIDDLE)

# ================================================================ 17. Цаашид
s = new_slide("Цаашдын ажил")
for k, (h, d) in enumerate([("Шинэ асуулт дээр батлах", "сургасан орчуулагчийн үр дүнг"),
                            ("Утгыг хадгалах", "салбар, кодыг орчуулгад алдахгүй байх"),
                            ("Өөрийн өгөгдлөөр сургах", "схем өөрчлөгдөхөд тогтвортой"),
                            ("Бодит орчинд турших", "хамгаалалттай хуулбар DB дээр")]):
    y = Y0 + k * 1.3
    rect(s, X0, y, XW, 1.1, GOLDT if k == 0 else TINT)
    circle_num(s, X0 + 0.25, y + 0.2, 0.7, k + 1, size=H)
    tb(s, X0 + 1.2, y, 4.8, 1.1, h, size=H, bold=True, color=NAVY, anchor=MSO_ANCHOR.MIDDLE)
    tb(s, X0 + 6.1, y, XW - 6.3, 1.1, d, anchor=MSO_ANCHOR.MIDDLE)

# ================================================================ 18. Ашигласан материал
s = new_slide("Гол эх сурвалжууд")
refs = ["Yao нар, 2025 — Arctic-Text2SQL-R1 (SQL бичих загвар)",
        "Li нар, 2023 — BIRD (Text-to-SQL үнэлгээ)",
        "Gao нар, 2024 — DAIL-SQL (жишээгээр заах)",
        "Hu нар, 2022 — LoRA (нэмэлт сургалт)",
        "Dettmers нар, 2023 — QLoRA (4-bit сургалт)",
        "Kudugunta нар, 2023 — MADLAD-400 (орчуулга)"]
tb(s, X0, Y0, XW, 5.2, [[(f"[{i + 1}]  ", {"bold": True, "color": NAVY}), (r, {})] for i, r in enumerate(refs)], space=14)

# ================================================================ 19. Талархал (загварын)
thanks = tpl_slides[8]

# ---------------------------------------------------------------- дараалал
sldIdLst = prs.slides._sldIdLst
ids = list(sldIdLst)
keep_first, keep_last, new_ids = ids[1], ids[8], ids[len(tpl_slides):]
for el in ids:
    sldIdLst.remove(el)
for el in [keep_first] + new_ids + [keep_last]:
    sldIdLst.append(el)

# Хөл (огноо, нэр, дугаар), талархлын хуудас: Arial, ≥ 20pt
for sl in [title_slide, thanks] + [prs.slides.get(int(e.get("id"))) for e in new_ids]:
    for sh in sl.shapes:
        if sh.is_placeholder and sh.placeholder_format.idx == 11:
            sh.text_frame.paragraphs[0].runs[0].text = FOOTER
            for r in sh.text_frame.paragraphs[0].runs[1:]:
                r.text = ""
        if not sh.has_text_frame:
            continue
        for p in sh.text_frame.paragraphs:
            for r in p.runs:
                r.font.name = FONT
                if r.font.size is None or r.font.size.pt < B:
                    r.font.size = Pt(B)
            # талбарын (огноо, дугаар) хэмжээг endParaRPr/fld-д ч тавина
            for el in p._p.iter():
                if el.tag in (qn("a:rPr"), qn("a:endParaRPr")):
                    sz = el.get("sz")
                    if sz is None or int(sz) < B * 100:
                        el.set("sz", str(B * 100))
                    if el.find(qn("a:latin")) is None:
                        el.append(el.makeelement(qn("a:latin"), {"typeface": FONT}))

prs.save(OUT)

# Загварын theme-ийн font (Calibri / Calibri Light) → Arial: бүх текст нэг font
z = zipfile.ZipFile(OUT)
items = {n: z.read(n) for n in z.namelist()}
z.close()
for n in list(items):
    if not (n.startswith("ppt/") and n.endswith(".xml")):
        continue
    t = items[n].decode("utf-8")
    if n.startswith("ppt/theme/"):
        t = re.sub(r'(<a:(?:major|minor)Font>\s*<a:latin typeface=")[^"]+(")', r"\1" + FONT + r"\2", t)
    t = t.replace('typeface="Montserrat"', f'typeface="{FONT}"')   # загварын сургуулийн нэр (мастер, layout)
    if n.startswith("ppt/slides/"):   # нүүрний list style-ийн анхдагч хэмжээ ч ≥ 20pt
        t = re.sub(r'(<a:defRPr\b[^>]*\bsz=")(\d+)"', lambda m: m.group(1) + str(max(int(m.group(2)), B * 100)) + '"', t)
    items[n] = t.encode("utf-8")
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zo:
    zo.writestr("[Content_Types].xml", items.pop("[Content_Types].xml"))
    for n, d in items.items():
        zo.writestr(n, d)
print("saved", OUT, len(sldIdLst))
