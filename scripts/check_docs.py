#!/usr/bin/env python3
"""ตรวจชุดเอกสารออกแบบระบบ (README.md, docs/, plans/) ด้วยกฎที่เครื่องตรวจได้

วิธีใช้:
    python check_docs.py <โฟลเดอร์โครงการ> [--render]

--render  ลองวาดผัง Mermaid ทุกผังด้วย Chrome หรือ Edge ในเครื่อง (ต้องมีอินเทอร์เน็ต)

สคริปต์รู้จักไฟล์จากคำในชื่อไฟล์: flows, decisions, screens, api, data-model ชุดเอกสารต้องใช้ชื่อเหล่านี้
คำที่สคริปต์ใช้ตรวจมีทั้งไทยและอังกฤษ: ข้อ/section, Flow, ประเด็นที่/Decision, (รอยืนยัน)/(to be confirmed),
(ลูกค้ากำหนด)/(client-specified), **สถานะ:**/**Status:** เอกสารภาษาอื่นให้ใช้คำอังกฤษเหล่านี้

รหัสออก: 0 = ไม่มีรายการระดับผิดพลาด, 1 = มีรายการระดับผิดพลาด, 2 = เรียกใช้ผิด
"""
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

errors = []    # (file, line, message)
warnings = []  # (file, line, message)
notes = []     # ข้อความแจ้งทั่วไป


def err(f, line, msg):
    errors.append((str(f), line, msg))


def warn(f, line, msg):
    warnings.append((str(f), line, msg))


# ---------- อ่านไฟล์และแยกส่วนที่เป็นโค้ด ----------

FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
NUM_TOKEN = re.compile(r"^(\d+(?:\.\d+)*)\.?(?=\s|$|:)")
LIST_NUM = re.compile(r"^\s*[-*]\s+(\d+(?:\.\d+)+)\s")


class Doc:
    def __init__(self, path, root):
        self.path = path
        self.rel = path.relative_to(root).as_posix()
        self.text = path.read_text(encoding="utf-8-sig", errors="replace")  # utf-8-sig ตัด BOM ออก เพื่อให้หัวข้อบรรทัดแรกถูกอ่าน
        self.lines = self.text.splitlines()
        self.in_code = []          # แต่ละบรรทัดอยู่ในบล็อกโค้ดหรือไม่
        self.headings = []         # (line_no, level, text)
        self.mermaid = []          # (start_line, code)
        self.fence_count = 0
        self._scan()

    def _scan(self):
        inside = False
        lang = ""
        marker = ""
        buf = []
        start = 0
        for i, line in enumerate(self.lines, 1):
            fm = FENCE.match(line)
            # รั้วปิดต้องเป็นอักขระชนิดเดียวกับรั้วเปิดและยาวไม่น้อยกว่า ตามกฎ Markdown เพื่อให้ ``` ซ้อนใน ```` ไม่สลับสถานะผิด
            if fm and (not inside or (fm.group(1)[0] == marker[0] and len(fm.group(1)) >= len(marker) and not line.strip()[len(fm.group(1)):].strip())):
                self.fence_count += 1
                if not inside:
                    inside = True
                    marker = fm.group(1)
                    lang = line.strip()[len(marker):].strip().lower()
                    buf = []
                    start = i
                else:
                    if lang == "mermaid":
                        self.mermaid.append((start, "\n".join(buf)))
                    inside = False
                    lang = ""
                    marker = ""
                self.in_code.append(True)
                continue
            self.in_code.append(inside)
            if inside:
                buf.append(line)
            else:
                m = HEADING.match(line)
                if m:
                    self.headings.append((i, len(m.group(1)), m.group(2)))

    def prose_lines(self):
        """บรรทัดที่ไม่ได้อยู่ในบล็อกโค้ด"""
        for i, line in enumerate(self.lines, 1):
            if not self.in_code[i - 1]:
                yield i, line

    def heading_numbers(self):
        nums = set()
        for _, _, text in self.headings:
            m = NUM_TOKEN.match(text)
            if m:
                nums.add(m.group(1))
        return nums

    def referable_numbers(self):
        """เลขที่ไฟล์อื่นอ้างถึงได้: เลขหัวข้อ และเลขข้อของรายการย่อย เช่น '- 1.2.5 ...'"""
        nums = self.heading_numbers()
        for _, line in self.prose_lines():
            m = LIST_NUM.match(line)
            if m:
                nums.add(m.group(1))
        return nums


def name_has(path, *words):
    """ชื่อไฟล์มีคำนี้เป็นชิ้นที่คั่นด้วยขีดหรือไม่ เช่น 03-flows-booking.md มี flows แต่ workflow.md ไม่มี"""
    parts = re.split(r"[-_.]", path.name.lower())
    return any(w in parts for w in words)


def is_flows(d): return name_has(d.path, "flows", "flow")
def is_decisions(d): return name_has(d.path, "decisions", "decision")
def is_screens(d): return name_has(d.path, "screens", "screen")
def is_api(d): return name_has(d.path, "api")
def is_data_model(d): return "data-model" in d.path.name.lower() or "data_model" in d.path.name.lower()


def strip_inline_code(line):
    return re.sub(r"`[^`]*`", " ", line)


# ---------- การตรวจแต่ละเรื่อง ----------

def check_structure(root):
    readme = root / "README.md"
    docs = root / "docs"
    plans = root / "plans"
    if not readme.is_file():
        err("README.md", 0, "ไม่พบ README.md")
    if not docs.is_dir():
        err("docs/", 0, "ไม่พบโฟลเดอร์ docs/")
    elif not [x for x in docs.glob("*.md") if x.is_file()]:
        err("docs/", 0, "โฟลเดอร์ docs/ ไม่มีไฟล์ .md")
    if not plans.is_dir():
        err("plans/", 0, "ไม่พบโฟลเดอร์ plans/")
    elif not [x for x in plans.glob("*.md") if x.is_file()]:
        err("plans/", 0, "โฟลเดอร์ plans/ ไม่มีไฟล์ .md")
    # สิ่งอื่นในโฟลเดอร์โครงการนอกจาก 3 อย่างนี้ รายงานเป็นคำเตือนเพื่อให้รู้ว่ามี ไม่ได้บอกให้ลบ เพราะมักเป็นไฟล์ข้อกำหนดของผู้ใช้
    allowed = {"README.md", "docs", "plans", ".git", ".gitignore", ".gitattributes"}
    for item in sorted(root.iterdir()):
        if item.name in allowed or item.name.startswith("."):
            continue
        warn(item.name, 0, "ไม่ใช่ส่วนของชุดเอกสาร ห้ามลบหรือย้ายถ้าคุณไม่ได้สร้างเอง เช่น ไฟล์ข้อกำหนดที่ผู้ใช้วางไว้ ลบได้เฉพาะไฟล์ชั่วคราวที่คุณสร้างเองในการทำงานครั้งนี้")
    for sub in (docs, plans):
        if sub.is_dir():
            for item in sorted(sub.iterdir()):
                if item.name.startswith("."):
                    continue
                if item.is_dir():
                    warn(f"{sub.name}/{item.name}/", 0, "เป็นโฟลเดอร์ย่อย ชุดเอกสารไม่ใช้โฟลเดอร์ย่อยใน docs/ หรือ plans/")
                    continue
                if item.suffix != ".md":  # นามสกุลต้องเป็น .md ตัวเล็ก เพราะ glob ของสคริปต์แยกตัวพิมพ์
                    warn(f"{sub.name}/{item.name}", 0, "ไม่ใช่ไฟล์ .md ชุดเอกสารมีเฉพาะไฟล์ Markdown ห้ามลบหรือย้ายถ้าคุณไม่ได้สร้างเอง")


def check_numbering(root):
    docs = root / "docs"
    if not docs.is_dir():
        return
    seen = {}
    for p in sorted(x for x in docs.glob("*.md") if x.is_file()):
        m = re.match(r"^(\d\d)-[a-z0-9][a-z0-9-]*\.md$", p.name)
        if not m:
            warn(f"docs/{p.name}", 0, "ชื่อไฟล์ไม่ได้อยู่ในรูปแบบ เลข 2 หลัก-ชื่อตัวพิมพ์เล็กคั่นด้วยขีดกลาง.md")
            continue
        seen.setdefault(int(m.group(1)), []).append(p.name)
    if not seen:
        return
    for n, names in sorted(seen.items()):
        if len(names) > 1:
            err("docs/", 0, f"เลขนำหน้า {n:02d} ซ้ำกัน: {', '.join(names)}")
    nums = sorted(seen)
    if nums[0] not in (0, 1):
        err("docs/", 0, f"เลขนำหน้าไฟล์ควรเริ่มที่ 00 หรือ 01 แต่เริ่มที่ {nums[0]:02d}")
    for a, b in zip(nums, nums[1:]):
        if b != a + 1:
            err("docs/", 0, f"เลขนำหน้าไฟล์ข้ามจาก {a:02d} ไป {b:02d}")


# ตรวจข้อความในโหนดของ flowchart: อักขระที่ทำให้ Mermaid 11 วาดไม่ได้เมื่ออยู่ในข้อความที่ไม่ได้ครอบด้วยเครื่องหมายคำพูดทั้งข้อความ
# คือ วงเล็บ วงเล็บเหลี่ยม ปีกกา ขีดตั้ง เครื่องหมายคำพูด และ @ ที่ติดกับตัวอักษร ส่วนโคลอนและทับใช้ได้ (ยืนยันกับ mermaid@11 ด้วย mermaid.parse)
LABEL_BREAKERS = re.compile(r"[()\[\]{}|\"]|(?<!\s)@|@(?!\s)")
EDGE_BREAKERS = re.compile(r"[()\[\]{}\"]|(?<!\s)@|@(?!\s)")
QUOTED = re.compile(r"\"[^\"\n]*\"")
THAI = re.compile(r"[฀-๿]")
# ตัวเปิดของแต่ละรูปทรง เรียงยาวก่อนสั้น และตัวปิดที่เป็นไปได้ (สี่เหลี่ยมคางหมูใช้ [/ กับ \] หรือ [\ กับ /] ได้)
SHAPES = [("(((", [")))"]), ("([", ["])"]), ("[(", [")]"]), ("[[", ["]]"]), ("((", ["))"]), ("{{", ["}}"]),
          ("[/", ["/]", "\\]"]), ("[\\", ["\\]", "/]"]), ("[", ["]"]), ("(", [")"]), ("{", ["}"]), (">", ["]"])]
# รหัสโหนดต้องติดกับตัวเปิดรูปทรง Mermaid ไม่รับ "A [x]"
NODE_ID = re.compile(r"[A-Za-z0-9_฀-๿]+")
SPACED_SHAPE = re.compile(r"(?<![A-Za-z0-9_฀-๿\]\)\}])[A-Za-z0-9_]+\s+[\[({]")
AFTER_CLOSE = re.compile(r"(?:\s|$|[-;&:=.~<])")  # สิ่งที่ตามหลังโหนดได้ เช่น ช่องว่าง ลูกศร ; & :::class ~~~ <-->
EDGE_LABEL = re.compile(r"\|([^|\n]*)\|")  # ข้อความบนเส้น -->|...| ใช้กฎเดียวกับโหนด เว้นแต่ครอบด้วยเครื่องหมายคำพูดทั้งข้อความ
EDGE_TEXT = re.compile(r"(--|-\.|==)(?![>.=-]?>)[^|\n]*?(-{2,}>|-{3,}|--[ox]|={2,}>|={3,}|==[ox]|\.+->|\.-)")  # ข้อความบนเส้นแบบ A -- ข้อความ --> B ใช้หลังตัดโหนดออกแล้ว
SHAPE_SPEC = re.compile(r"@\{[^}\n]*\}")  # A@{ shape: rect, label: ... } ข้อความใน label ใช้เครื่องหมายคำพูดเดี่ยวหรือไม่ครอบก็ได้
# คำสงวนของ flowchart ที่ใช้เป็นรหัสโหนดไม่ได้ (แยกตัวพิมพ์ Class หรือ END ใช้ได้)
RESERVED = re.compile(r"(?<![A-Za-z0-9_-])(?<![A-Za-z0-9_]-)(end|class|style|click|href|call|interpolate|linkStyle|classDef|subgraph|flowchart|graph)(?![A-Za-z0-9_])")  # end-user ก็ไม่ได้ แต่ x-end ได้
LEADING_COMMAND = re.compile(r"^\s*(?:(?:class|style|click|linkStyle|classDef|subgraph|direction)(?![A-Za-z0-9_-])|end\s*$)")  # end เป็นคำสั่งเฉพาะเมื่ออยู่เดี่ยว ๆ
ER_ENTITY = r"[A-Za-z0-9_-]+"
ER_REL_LINE = re.compile(r"^\s*(\S+)\s+[|}o{]+(?:--|\.\.)[|}o{]+\s+(\S+)\s*(?::\s*(.*?))?\s*$")  # USER ||--o{ ORDER : label
# ป้ายที่ไม่ครอบเครื่องหมายคำพูดต้องเป็นคำเดียว ขึ้นต้นด้วยตัวอักษร มีได้แค่ตัวอักษร ตัวเลข _ - . และไม่ใช่คำสงวน one many to (ยืนยันกับ mermaid@11)
ER_PLAIN = re.compile(r"^[A-Za-z_฀-๿][A-Za-z0-9_.\-฀-๿]*$")
ER_RESERVED = {"one", "many", "to", "only", "zero", "or", "more", "optionally"}


def mask_quotes(line):
    """แทนข้อความในเครื่องหมายคำพูดด้วย "Q" (ถ้ามีเนื้อหา) หรือคง "" ไว้ (ถ้าว่าง ซึ่ง Mermaid ไม่รับ)"""
    return QUOTED.sub(lambda m: '"Q"' if len(m.group(0)) > 2 else '""', line)


def mermaid_body(code):
    """บรรทัดของผัง ไม่รวม frontmatter --- ... --- ที่หัวผัง คอมเมนต์ และ directive %%{ ... }%% ที่นำหน้าชนิดผัง"""
    lines = code.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].strip() == "---":
        j = i + 1
        while j < len(lines) and lines[j].strip() != "---":
            j += 1
        i = j + 1
    while i < len(lines):
        st = lines[i].strip()
        if not st:
            i += 1
        elif st.startswith("%%{") and "}%%" not in st:
            j = i
            while j < len(lines) and "}%%" not in lines[j]:
                j += 1
            i = j + 1
        elif st.startswith("%%"):
            i += 1
        else:
            break
    return [(k, l) for k, l in enumerate(lines) if k >= i]


def node_labels(line):
    """คืน (ข้อความในโหนด, ตำแหน่งเริ่ม, ตำแหน่งจบ, ตัวเปิด) ทุกตัวของบรรทัด โดยจับคู่ตัวเปิดกับตัวปิดตามรูปทรง"""
    out = []
    pos = 0
    while True:
        m = NODE_ID.search(line, pos)
        if not m:
            break
        rest = line[m.end():]
        opener = next((o for o, _ in SHAPES if rest.startswith(o)), None)
        if not opener:
            pos = m.end() if m.end() > pos else pos + 1
            continue
        body_start = m.end() + len(opener)
        found = -1
        found_closer = ""
        for closer in dict(SHAPES)[opener]:
            search = body_start
            while True:
                k = line.find(closer, search)
                if k < 0:
                    break
                if AFTER_CLOSE.match(line, k + len(closer)):
                    if found < 0 or k < found:
                        found, found_closer = k, closer
                    break
                search = k + 1
        if found < 0:
            pos = body_start
            continue
        out.append((line[body_start:found], body_start, found + len(found_closer), opener))
        pos = found + len(found_closer)
    return out


def check_er_labels(d, start, body):
    for k, line in body:
        stripped = line.strip()
        if not stripped or stripped.startswith("%%") or stripped.startswith("erDiagram"):
            continue
        m = ER_REL_LINE.match(mask_quotes(line))  # ปิดบังข้อความในเครื่องหมายคำพูดก่อน เพื่อให้ชื่อแสดงที่มีช่องว่างไม่ทำให้บรรทัดไม่ตรงรูป
        if not m:
            continue
        left, right, label = m.group(1), m.group(2), m.group(3)
        if "[" in left or "[" in right:
            warn(d.rel, start + 1 + k, f"ชื่อ Entity ในบรรทัดความสัมพันธ์ของ erDiagram ใส่ [ชื่อแสดง] ไม่ได้ ให้ใส่ชื่อแสดงในบล็อกนิยาม Entity แทน: {stripped[:40]}")
            continue
        if label is None or not label:
            warn(d.rel, start + 1 + k, f"บรรทัดความสัมพันธ์ของ erDiagram ต้องมี : ป้าย ต่อท้ายเสมอ: {stripped[:40]}")
            continue
        if label == '"Q"':
            continue
        if not ER_PLAIN.match(label) or label.lower() in ER_RESERVED:
            warn(d.rel, start + 1 + k, f"ป้ายความสัมพันธ์ใน erDiagram ให้ครอบด้วยเครื่องหมายคำพูดทั้งป้าย ป้ายที่ไม่ครอบรับได้เฉพาะคำเดียวที่ไม่มีเครื่องหมายพิเศษและไม่ใช่คำสงวนเช่น one many to: {label[:40]}")


def check_mermaid_labels(d, start, code):
    body = mermaid_body(code)
    first = next((l.strip() for _, l in body if l.strip() and not l.strip().startswith("%%")), "")
    if first.startswith("erDiagram"):
        check_er_labels(d, start, body)
        return
    if not (first.startswith("flowchart") or first.startswith("graph")):
        return
    in_quote = -1  # บรรทัดที่เปิดเครื่องหมายคำพูดแล้วยังไม่ปิด (ข้อความหลายบรรทัด)
    in_acc = False  # บล็อก accDescr { ... }
    for k, line in body:
        stripped = line.strip()
        if in_acc:
            if "}" in stripped:
                in_acc = False
            continue
        if stripped.lower().startswith("accdescr") and "{" in stripped and "}" not in stripped:
            in_acc = True
            continue
        if in_quote >= 0:
            if line.count('"') % 2 == 0:
                continue
            in_quote = -1
            line = 'Q["Q' + line[line.index('"'):]  # ตรวจเฉพาะส่วนหลังเครื่องหมายคำพูดปิดของข้อความหลายบรรทัด
            stripped = line.strip()
        if not stripped or stripped.startswith("%%") or stripped.lower().startswith(("acctitle", "accdescr")):
            continue
        if line.count('"') % 2 == 1:
            # เครื่องหมายคำพูดเดี่ยวเป็นการเปิดข้อความหลายบรรทัดได้เฉพาะเมื่ออยู่ติดตัวเปิดรูปทรงหรือป้ายเส้น และไม่มีอะไรตามหลังที่ปิด
            if re.search(r"(?:[\[({>|]|--|-\.|==)\s*\"[^\"]*$", line):
                in_quote = k
                continue
            warn(d.rel, start + 1 + k, "เครื่องหมายคำพูดในผัง Mermaid ไม่ครบคู่ ถ้าตั้งใจใช้สัญลักษณ์นิ้ว ให้ครอบข้อความทั้งหมดและเขียนเป็น #quot;")
            continue
        if "%%" in mask_quotes(line):
            warn(d.rel, start + 1 + k, "คอมเมนต์ %% ของผัง Mermaid ต้องอยู่บรรทัดของตัวเอง เมื่อต่อท้ายบรรทัดอื่น Mermaid 11 มักวาดไม่ได้ ให้ย้ายไปบรรทัดใหม่")
            continue
        line = SHAPE_SPEC.sub("@{}", line)
        masked = mask_quotes(line)
        # 1) ข้อความบนเส้นแบบ |...| ตรวจแล้วตัดออกก่อน เพื่อไม่ให้ > ในข้อความถูกอ่านเป็นตัวเปิดโหนดรูปธง
        warned = False
        for m in EDGE_LABEL.finditer(masked):
            t = m.group(1).strip()
            if t and t != '"Q"' and EDGE_BREAKERS.search(t):
                warn(d.rel, start + 1 + k, f"ข้อความบนเส้นของผัง Mermaid มีวงเล็บ วงเล็บเหลี่ยม ปีกกา @ หรือเครื่องหมายคำพูด แต่ไม่ได้ครอบด้วยเครื่องหมายคำพูดทั้งข้อความ เช่น -->|\"ใช่ (ต่อ)\"|: {t[:40]}")
                warned = True
                break
        if warned:
            continue
        masked = EDGE_LABEL.sub("||", masked)
        # 2) โหนด
        labels = node_labels(masked)
        for t, _, _, opener in labels:
            t = re.sub(r"<br\s*/?>", " ", t).strip()  # <br/> คือการขึ้นบรรทัดใหม่ของ Mermaid ใช้ได้โดยไม่ต้องครอบ
            if t == '""':
                warn(d.rel, start + 1 + k, "ข้อความโหนดในผัง Mermaid เป็นเครื่องหมายคำพูดว่าง Mermaid ไม่รับ ให้ใส่ข้อความ")
                warned = True
                break
            if not t or t == '"Q"':
                continue  # ว่าง หรือครอบเครื่องหมายคำพูดทั้งข้อความแล้ว
            breakers = LABEL_BREAKERS.search(t)
            if breakers and opener in ("[/", "[\\") and breakers.group(0) == '"':
                continue  # สี่เหลี่ยมคางหมูรับเครื่องหมายคำพูดภายในได้
            if breakers:
                warn(d.rel, start + 1 + k, f"ข้อความในผัง Mermaid มีวงเล็บ วงเล็บเหลี่ยม ปีกกา ขีดตั้ง @ หรือเครื่องหมายคำพูด แต่ไม่ได้ครอบด้วยเครื่องหมายคำพูดทั้งข้อความ (เครื่องหมายคำพูดภายในใช้ #quot;): {t[:40]}")
                warned = True
                break
        if warned or stripped.lower().startswith(("graph", "flowchart")):
            continue
        # 3) ส่วนที่เหลือนอกโหนด: ข้อความบนเส้นแบบ -- ข้อความ --> รหัสโหนด และคำสั่ง
        outside = masked
        for _, a, b, _ in sorted(labels, key=lambda x: -x[1]):
            outside = outside[:a] + outside[b:]
        for m in EDGE_TEXT.finditer(outside):
            inner = outside[m.start() + len(m.group(1)):m.end() - len(m.group(2))].strip()
            if inner == '"Q"' or not inner:
                continue
            if '"' in inner:
                warn(d.rel, start + 1 + k, f"ข้อความบนเส้นแบบ -- ข้อความ --> มีเครื่องหมายคำพูดแต่ไม่ได้ครอบทั้งข้อความ ให้เขียน -- \"ข้อความ\" -->: {inner[:40]}")
                warned = True
                break
            if (m.group(1) == "-." and "." in inner) or (m.group(1) == "==" and "=" in inner):
                warn(d.rel, start + 1 + k, f"ข้อความบนเส้นประหรือเส้นหนาแบบ -. ข้อความ .-> หรือ == ข้อความ ==> มีจุดหรือเครื่องหมายเท่ากับ ซึ่ง Mermaid อ่านเป็นตัวเส้น ให้ใช้ -.->|\"ข้อความ\"| หรือ ==>|\"ข้อความ\"| แทน: {inner[:40]}")
                warned = True
                break
        if warned:
            continue
        outside = EDGE_TEXT.sub(r"\1 \2", outside)
        if stripped.lower().startswith("subgraph"):
            outside = outside.split("[", 1)[0]
        if SPACED_SHAPE.search(outside) and not LEADING_COMMAND.match(stripped):
            warn(d.rel, start + 1 + k, f"มีช่องว่างระหว่างรหัสโหนดกับรูปทรง เช่น A [x] Mermaid ไม่รับ ให้เขียนติดกัน A[x]: {stripped[:40]}")
            continue
        if THAI.search(outside):
            warn(d.rel, start + 1 + k, f"รหัสโหนดหรือชื่อ subgraph ในผัง flowchart เป็นภาษาไทย ซึ่ง Mermaid 11 ปฏิเสธเมื่ออยู่ต้นคำสั่งและไม่รับประกันตำแหน่งอื่น ให้ใช้รหัสละตินแล้วใส่ชื่อไทยในข้อความ เช่น A[เริ่ม] หรือ subgraph S1[ชื่อ]: {stripped[:40]}")
            continue
        rest = LEADING_COMMAND.sub("", outside, count=1)
        m = RESERVED.search(rest)
        if m:
            warn(d.rel, start + 1 + k, f"ใช้คำสงวน {m.group(1)} เป็นรหัสโหนดไม่ได้ ให้เปลี่ยนรหัส เช่น {m.group(1).capitalize()} หรือ {m.group(1).upper()}: {stripped[:40]}")
    if in_quote >= 0:
        warn(d.rel, start + 1 + in_quote, "เครื่องหมายคำพูดในผัง Mermaid เปิดแล้วไม่ปิด ถ้าตั้งใจใช้สัญลักษณ์นิ้ว ให้ครอบข้อความทั้งหมดและเขียนเป็น #quot;")


def check_fences(docs):
    # ชนิดผังของ Mermaid 11 ตัวพิมพ์ต้องตรง เพราะ Mermaid แยกตัวพิมพ์ เช่น Flowchart หรือ sequencediagram ใช้ไม่ได้
    known = ("flowchart", "graph", "sequenceDiagram", "stateDiagram", "erDiagram", "classDiagram",
             "gantt", "journey", "pie", "mindmap", "timeline", "quadrantChart", "gitGraph",
             "C4Context", "C4Container", "C4Component", "C4Dynamic", "C4Deployment", "requirementDiagram",
             "block", "sankey", "xychart", "architecture-beta", "packet", "kanban", "treemap", "radar-beta",
             "venn-beta", "ishikawa-beta", "wardley-beta", "info", "zenuml")
    for d in docs:
        if d.fence_count % 2 != 0:
            err(d.rel, 0, "บล็อกโค้ด (```) เปิดปิดไม่ครบคู่")
        for start, code in d.mermaid:
            first = next((l.strip() for _, l in mermaid_body(code) if l.strip() and not l.strip().startswith("%%")), "")
            if not first:
                err(d.rel, start, "ผัง Mermaid ว่างเปล่า")
            elif not first.startswith(known):
                warn(d.rel, start, f"บรรทัดแรกของผัง Mermaid ไม่ใช่ชนิดผังที่รู้จัก หรือตัวพิมพ์ไม่ตรง เช่น ต้องเป็น sequenceDiagram ไม่ใช่ sequencediagram: {first[:40]}")
            else:
                check_mermaid_labels(d, start, code)


# ใช้เฉพาะอักษรละติน เพื่อไม่ให้คำไทยที่ติดกับชื่อไฟล์ถูกกลืนเข้าไปในชื่อไฟล์
FILE_TOKEN = re.compile(r"(?<![A-Za-z0-9_/.-])((?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.md)(?![A-Za-z0-9_-])")
MD_LINK = re.compile(r"\]\(([^)\s]+)\)")
MD_LINK_FULL = re.compile(r"\[[^\]\n]*\]\([^)\s]*\)")  # ลิงก์ทั้งก้อน ตัดออกก่อนหา FILE_TOKEN เพราะปลายทางตรวจแยกแล้ว


def norm_token(tok):
    return re.sub(r"^\./", "", tok)


def resolve(root, doc, token):
    token = token.split("#")[0]
    if not token:
        return True
    bases = [root, doc.path.parent, root / "docs", root / "plans"]
    return any((b / token).exists() for b in bases)


def is_source_doc(d):
    return d.path.name.startswith("00-")


def customer_file_names(docs):
    """ชื่อไฟล์ต้นทางของลูกค้าที่บันทึกไว้ใน 00-source.md ไม่ต้องมีอยู่ในโครงการ ยกเว้นชื่อรูปแบบ NN-ชื่อ.md ซึ่งเป็นชื่อของชุดเอกสารนี้"""
    names = set()
    for d in docs:
        if is_source_doc(d):
            for m in FILE_TOKEN.finditer(d.text):
                if not re.match(r"^\d\d-[a-z0-9-]+\.md$", Path(m.group(1)).name):
                    names.add(Path(m.group(1)).name)
    return names


def check_file_refs(root, docs):
    source_names = customer_file_names(docs)
    for d in docs:
        if is_source_doc(d) or d.rel == "README.md":
            continue  # README ตรวจแยกใน check_readme_lists_files รวมบล็อกโค้ด
        for i, line in d.prose_lines():
            for m in FILE_TOKEN.finditer(MD_LINK_FULL.sub(" ", line)):  # ปลายทางของลิงก์ตรวจแยกด้านล่าง ไม่รายงานซ้ำ
                tok = norm_token(m.group(1))
                if any(c in tok for c in "<>*{}"):
                    continue
                if Path(tok).name in source_names and not re.match(r"^(docs|plans)/", tok):
                    continue
                if not resolve(root, d, tok):
                    err(d.rel, i, f"อ้างถึงไฟล์ที่ไม่มีอยู่: {tok}")
            for m in MD_LINK.finditer(line):
                target = norm_token(m.group(1))
                if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                    continue
                if Path(target).name in source_names and not re.match(r"^(docs|plans)/", target):
                    continue
                if not resolve(root, d, target):
                    err(d.rel, i, f"ลิงก์ชี้ไปยังไฟล์ที่ไม่มีอยู่: {target}")


# ยอมให้ชื่อไฟล์ถูกคร่อมด้วย ` ** " หรือเป็นลิงก์ [ชื่อ](ชื่อ) ก่อนคำว่า ข้อ
WRAP = r"[`*\"'\u201c\u201d\u2018\u2019)]*"  # เครื่องหมายที่คร่อมชื่อไฟล์ได้: ` * \" ' “ ” ‘ ’ )
# ระหว่างชื่อไฟล์กับคำว่า ข้อ ยอมให้มีช่องว่าง วงเล็บเปิด โคลอน จุลภาค หรือขีด เช่น "02-features.md (ข้อ 1.2)"
SECTION_REF = re.compile(r"((?:[A-Za-z0-9_.-]+/)*\d\d-[a-z0-9-]+\.md)" + WRAP + r"(?:\]\([^)\s]*\))?" + WRAP + r"[\s(:,;\u2013\u2014-]*(?:ข้อที่|ข้อ|หัวข้อที่|หัวข้อ|หมวดที่|หมวด|ส่วนที่|section)\s+", re.IGNORECASE)
# Decision ต้อง D ตัวใหญ่ เพื่อไม่ให้ "the decision 30 days" ในประโยคอังกฤษถูกอ่านเป็นการอ้างประเด็น
DECISION_REF = re.compile(r"(?:ประเด็นที่|(?<![A-Za-z0-9_])Decisions?)\s+#?(?=\d)")
CLOSE_LINE = re.compile(r"^\*\*[^*\n]*(?:ประเด็นที่ต้องปิด|Decisions to close)[^*\n]*:\*\*\s*(.*)$", re.IGNORECASE)
FLOW_WORD = r"(?<![A-Za-z0-9_])Flow(?:\s*ที่)?"
# เลขที่ตามด้วยขีดและตัวอักษร เช่น 03-flows.md เป็นชื่อไฟล์ ไม่ใช่เลขข้อ
NUMBER = re.compile(r"(\d+(?:\.\d+)*)(?![\d.]*-[A-Za-z])")
SEP = re.compile(r"\s*(,|และ|and)\s*")
RANGE = re.compile(r"\s*(?:-|–|ถึง|to)\s*")


def expand_range(a, b):
    """ขยายช่วงเลข เช่น 4-6 → 4,5,6 และ 2.1-2.3 → 2.1,2.2,2.3 ถ้าช่วงยาวเกินหรือรูปแบบไม่ตรงกัน คืนเฉพาะปลายทั้งสอง"""
    pa, pb = a.split("."), b.split(".")
    if len(pa) != len(pb) or pa[:-1] != pb[:-1]:
        return [a, b]
    lo, hi = int(pa[-1]), int(pb[-1])
    if lo > hi or hi - lo > 50:
        return [a, b]
    prefix = ".".join(pa[:-1])
    return [(prefix + "." if prefix else "") + str(n) for n in range(lo, hi + 1)]


def ends_phrase(text, pos):
    """เลขที่ตำแหน่ง pos จบวลีหรือไม่ คือตามด้วยจบบรรทัด เครื่องหมายวรรคตอน หรือตัวคั่นเลขถัดไป"""
    return re.match(r"\s*(?:$|[,;:()\]}.|]|และ|and\b|ถึง|to\b|[-–](?=\s*\d))", text[pos:]) is not None


def parse_numbers(text):
    """อ่านเลขหัวข้อที่ตามหลังคำว่า ข้อ, Flow, ประเด็นที่ เช่น '3, 4 และ 6' หรือ '1 ถึง 3' หรือ '4-6'

    เลขแรกนับเสมอ เลขหลังจุลภาคหรือในช่วง (ถึง, ขีด) นับเสมอ เพราะเป็นสัญญาณชัดว่าเป็นเลขข้อ
    เลขหลังคำว่า "และ" นับต่อเมื่อเลขนั้นจบวลี เพื่อไม่ให้ "ข้อ 1.1 และ 3 บทบาท" ถูกอ่านว่าอ้างข้อ 3
    ข้อแลกคือ "ข้อ 4 และ 6 ซึ่ง..." จะตรวจเฉพาะข้อ 4 ให้เขียนหลายข้อด้วยจุลภาค เช่น "ข้อ 4, 6" เพื่อให้ตรวจครบ
    """
    out = []
    m = NUMBER.match(text)
    while m:
        n = m.group(1)
        pos = m.end()
        r = RANGE.match(text, pos)
        if r and NUMBER.match(text, r.end()):
            m2 = NUMBER.match(text, r.end())
            out.extend(expand_range(n, m2.group(1)))
            pos = m2.end()
        else:
            out.append(n)
        sep = SEP.match(text, pos)
        if not sep:
            break
        m = NUMBER.match(text, sep.end())
        if not m:
            break
        if sep.group(1) != "," and not ends_phrase(text, m.end()):
            break
    return out


def check_section_refs(root, docs):
    by_name = {}
    for d in docs:
        by_name.setdefault(d.path.name, d)
    flows = [d for d in docs if is_flows(d)]
    decisions = [d for d in docs if is_decisions(d)]
    flow_nums = set()
    flow_where = {}
    for d in flows:
        for line_no, _, text in d.headings:
            for n in re.findall(r"^Flow\s+(\d+)(?!\d)", text):
                if n in flow_where:
                    err(d.rel, line_no, f"หัวข้อ Flow {n} ซ้ำกับ {flow_where[n]} เลข Flow ต้องไม่ซ้ำกันแม้แยกหลายไฟล์")
                flow_where.setdefault(n, f"{d.rel}:{line_no}")
                flow_nums.add(n)
    decision_nums = set()
    for d in decisions:
        decision_nums |= d.heading_numbers()
    if any(d.fence_count % 2 for d in flows):
        flows = []  # รั้วโค้ดไม่ปิดทำให้หัวข้อ Flow ถูกกลืน ข้ามการตรวจอ้าง Flow จนกว่าจะแก้รั้ว
    if any(d.fence_count % 2 for d in decisions):
        decisions = []
    if not flows and not any(is_flows(d) for d in docs):
        err("docs/", 0, "ไม่พบไฟล์ลำดับการทำงาน (ชื่อไฟล์ต้องมีคำว่า flows) จึงตรวจการอ้าง Flow ไม่ได้")
    if not decisions and not any(is_decisions(d) for d in docs):
        err("docs/", 0, "ไม่พบไฟล์ประเด็นตัดสินใจ (ชื่อไฟล์ต้องมีคำว่า decisions) จึงตรวจการอ้างประเด็นไม่ได้")

    for d in docs:
        if is_source_doc(d):
            continue  # บันทึกข้อความของลูกค้าตามเดิม เลข Flow หรือประเด็นในนั้นเป็นของลูกค้า
        for i, line in d.prose_lines():
            for m in SECTION_REF.finditer(line):
                target = by_name.get(Path(m.group(1)).name)
                if not target:
                    continue  # ไฟล์ไม่มีอยู่ ถูกรายงานแล้วในการตรวจไฟล์
                if target.fence_count % 2 != 0:
                    continue  # ไฟล์ปลายทางมีบล็อกโค้ดไม่ปิด หัวข้อถูกกลืน รายงานเรื่องรั้วไว้แล้ว แก้รั้วก่อน
                have = target.referable_numbers()
                for n in parse_numbers(line[m.end():]):
                    if n not in have:
                        err(d.rel, i, f"อ้างถึง {target.path.name} ข้อ {n} แต่ไฟล์นั้นไม่มีหัวข้อเลข {n}")
            if flows:
                for m in re.finditer(FLOW_WORD + r"\s+(?=\d)", line):
                    for n in parse_numbers(line[m.end():]):
                        if len(n) > 3:
                            continue  # เลข 4 หลักขึ้นไป เช่น ปี ไม่ใช่เลข Flow
                        if n not in flow_nums:
                            err(d.rel, i, f"อ้างถึง Flow {n} แต่ไฟล์ลำดับการทำงานไม่มีหัวข้อ Flow {n}")
            if decisions:
                for m in DECISION_REF.finditer(line):
                    for n in parse_numbers(line[m.end():]):
                        if n not in decision_nums:
                            err(d.rel, i, f"อ้างถึงประเด็นที่ {n} แต่ไฟล์ประเด็นตัดสินใจไม่มีหัวข้อเลข {n}")


def check_readme_lists_files(root, docs):
    readme = next((d for d in docs if d.rel == "README.md"), None)
    if not readme:
        return
    for sub in ("docs", "plans"):
        folder = root / sub
        if not folder.is_dir():
            continue
        for p in sorted(x for x in folder.glob("*.md") if x.is_file()):
            if p.name not in readme.text:
                err("README.md", 0, f"README ไม่ได้กล่าวถึงไฟล์ {sub}/{p.name}")
    # ชื่อไฟล์ที่ README กล่าวถึง รวมในแผนผังต้นไม้ที่อยู่ในบล็อกโค้ด ต้องมีจริง
    source_names = customer_file_names(docs)
    for i, line in enumerate(readme.lines, 1):
        for m in FILE_TOKEN.finditer(MD_LINK_FULL.sub(" ", line)):
            tok = norm_token(m.group(1))
            if any(c in tok for c in "<>*{}"):
                continue
            if Path(tok).name in source_names and not re.match(r"^(docs|plans)/", tok):
                continue
            if not resolve(root, readme, tok):
                err("README.md", i, f"README กล่าวถึงไฟล์ที่ไม่มีอยู่: {tok}")
        if not readme.in_code[i - 1]:
            for m in MD_LINK.finditer(line):
                target = norm_token(m.group(1))
                if re.match(r"^[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
                    continue
                if Path(target).name in source_names and not re.match(r"^(docs|plans)/", target):
                    continue
                if not resolve(root, readme, target):
                    err("README.md", i, f"ลิงก์ชี้ไปยังไฟล์ที่ไม่มีอยู่: {target}")


# lookaround ใช้เฉพาะอักษรละติน เพื่อให้ "รหัสF-01" ที่ติดคำไทยถูกตรวจด้วย
CODE = re.compile(r"(?<![A-Za-z0-9_-])([A-Z]{1,4}(?:-[A-Z]{1,3})?)-([A-Z]?\d{1,4})(?![A-Za-z0-9_-])")


def check_invented_codes(docs):
    found = {}  # prefix -> {code: (file, line)}
    for d in docs:
        for i, line in d.prose_lines():
            for m in CODE.finditer(strip_inline_code(line)):
                found.setdefault(m.group(1), {}).setdefault(m.group(0), (d.rel, i))
    for prefix, codes in sorted(found.items()):
        if len(codes) >= 3:
            sample = sorted(codes)[:4]
            f, line = codes[sample[0]]
            warn(f, line, f"พบรหัสขึ้นต้นด้วย {prefix}- จำนวน {len(codes)} รหัส เช่น {', '.join(sample)} "
                          f"ถ้าเป็นรหัสที่ตั้งขึ้นเอง ให้เปลี่ยนเป็นเรียกด้วยชื่อ")


LEFTOVER = re.compile(r"(?<![A-Za-z0-9_])(TODO|TBD|FIXME|Lorem ipsum)(?![A-Za-z0-9_])")
# ไม่นับ List<TYPE> (มีอักษรละตินติดหน้า <) และเครื่องหมายเปรียบเทียบ เช่น "< 1000 บาท และ >" (หลัง < เป็นช่องว่าง)
# รวม <...> และ <…> ที่คัดมาจากโครงแล้วยังไม่ได้แทน
PLACEHOLDER = re.compile(r"(?<![A-Za-z0-9_])<((?:[A-Za-z\u0E00-\u0E7F][^<>\n@]{0,60})|(?:(?:\.{2,}|\u2026)[^<>\n]{0,60}))>")
HTML_TAGS = {"br", "b", "i", "u", "em", "strong", "sub", "sup", "kbd", "span", "div", "p", "a", "img",
             "h1", "h2", "h3", "h4", "h5", "h6", "center", "font", "s", "del", "ins", "mark", "small", "blockquote",
             "details", "summary", "table", "tr", "td", "th", "thead", "tbody", "ul", "ol", "li", "hr", "code", "pre"}


def check_leftovers(docs):
    for d in docs:
        for i, line in d.prose_lines():
            clean = strip_inline_code(line)
            m = LEFTOVER.search(clean)
            if m:
                warn(d.rel, i, f"พบข้อความค้าง: {m.group(1)}")
            for p in PLACEHOLDER.finditer(clean):
                parts = p.group(1).split()
                name = parts[0].lower().rstrip("/") if parts else ""
                if name in HTML_TAGS or re.match(r"^[a-z][a-z0-9+.-]*:", p.group(1)):  # แท็ก HTML หรือ autolink เช่น <https://...>
                    continue
                warn(d.rel, i, f"พบช่องที่อาจยังไม่ได้แทนค่า: <{p.group(1)}>")


def check_empty_sections(docs):
    for d in docs:
        # ไฟล์ที่ไม่มีเนื้อหาเลยนอกจากหัวข้อ
        body_lines = [l for i, l in d.prose_lines() if l.strip() and l.strip() != "---" and not HEADING.match(l)]
        has_code = any(d.in_code)
        if not body_lines and not has_code:
            err(d.rel, 0, "ไฟล์ไม่มีเนื้อหา มีแต่หัวข้อหรือว่างเปล่า")
            continue
        hs = d.headings
        for idx, (line_no, level, text) in enumerate(hs):
            end = hs[idx + 1][0] - 1 if idx + 1 < len(hs) else len(d.lines)
            body = [l for l in d.lines[line_no:end] if l.strip() and l.strip() != "---"]
            if body:
                continue
            if idx + 1 < len(hs) and hs[idx + 1][1] > level:
                continue  # หัวข้อที่มีแต่หัวข้อย่อย ถือว่ามีเนื้อหา
            if line_no <= 3 and idx + 1 < len(hs) and (level == 1 or (level == 2 and idx == 1 and hs[0][1] == 1 and not NUM_TOKEN.match(text))):
                continue  # ชื่อเอกสาร (H1) และชื่อรอง (H2 ไม่มีเลข ถัดจาก H1) ที่ต้นไฟล์ ยกเว้นเมื่อเป็นหัวข้อสุดท้ายของไฟล์
            warn(d.rel, line_no, f"หัวข้อไม่มีเนื้อหา: {text}")


# (รอยืนยัน) หรือ (จำนวนรูปรอยืนยัน) ไม่รวมชื่อสถานะเช่น รอยืนยันผล และโหมดกลับด้าน (ลูกค้ากำหนด)
PENDING_MARK = re.compile(r"\([^()\n]*(?:รอยืนยัน|to be confirmed)\)|\((?:ลูกค้ากำหนด|client-specified)\)", re.IGNORECASE)
CLIENT_MARK = re.compile(r"\((?:ลูกค้ากำหนด|client-specified)\)", re.IGNORECASE)


def check_pending_marks(docs):
    decisions = [d for d in docs if is_decisions(d)]
    marked = [d for d in docs if PENDING_MARK.search(d.text) and d.rel != "README.md" and d not in decisions]
    for d in marked:
        if CLIENT_MARK.search(d.text) and PENDING_MARK.search(CLIENT_MARK.sub("", d.text)):
            warn(d.rel, 0, "ไฟล์นี้ใช้ทั้ง (รอยืนยัน) และ (ลูกค้ากำหนด) ให้เลือกแบบเดียวต่อไฟล์")
    if marked and not decisions:
        return  # รายงานไว้แล้วว่าไม่พบไฟล์ประเด็นตัดสินใจ
    names = [d.path.name for d in decisions]
    for d in marked:
        if not any(n in d.text for n in names) and not DECISION_REF.search(d.text):
            warn(d.rel, 0, "ไฟล์นี้มีเครื่องหมาย (รอยืนยัน) หรือ (ลูกค้ากำหนด) แต่ไม่ได้ชี้ไปที่ไฟล์ประเด็นตัดสินใจหรือเลขประเด็น ผู้อ่านจะหารายละเอียดไม่เจอ")


ROW = re.compile(r"^\s*\|(.+)\|\s*$")
SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}")


def table_rows(doc, start, end):
    """แถวข้อมูลของตารางในช่วงบรรทัด ไม่รวมหัวตารางและเส้นคั่น"""
    rows = []
    lines = doc.lines[start:end]
    for i, line in enumerate(lines):
        if doc.in_code[start + i] or not ROW.match(line) or SEPARATOR.match(line):
            continue
        is_header = i + 1 < len(lines) and SEPARATOR.match(lines[i + 1])
        if not is_header:
            rows.append([c.strip() for c in ROW.match(line).group(1).split("|")])
    return rows


def check_decision_summary(docs):
    """ตารางสรุปประเด็นต้องมีเลขตรงกับหัวข้อประเด็นจริง"""
    for d in docs:
        if not is_decisions(d) or d.fence_count % 2 != 0:
            continue  # รั้วโค้ดไม่ปิดทำให้หัวข้อถูกกลืน รายงานเรื่องรั้วไว้แล้ว
        all_nums = [NUM_TOKEN.match(t).group(1) for _, _, t in d.headings if NUM_TOKEN.match(t)]
        digit_nums = [n for n in all_nums if n.isdigit()]
        for n in sorted({x for x in digit_nums if digit_nums.count(x) > 1}, key=int):
            err(d.rel, 0, f"หัวข้อประเด็นที่ {n} ซ้ำกัน {digit_nums.count(n)} ครั้ง การอ้างประเด็นที่ {n} จะกำกวม")
        heading_nums = set(digit_nums)
        if not heading_nums:
            continue
        first_numbered = min(l for l, _, t in d.headings if NUM_TOKEN.match(t))
        summary_nums = {r[0] for r in table_rows(d, 0, first_numbered - 1) if r and r[0].isdigit()}
        if not summary_nums:
            err(d.rel, 0, "ไม่พบตารางสรุปประเด็น (ตารางที่คอลัมน์แรกเป็นเลขประเด็น) ก่อนหัวข้อประเด็นแรก")
            continue
        for n in sorted(heading_nums - summary_nums, key=int):
            err(d.rel, 0, f"ประเด็นที่ {n} มีหัวข้อ แต่ไม่อยู่ในตารางสรุปประเด็น")
        for n in sorted(summary_nums - heading_nums, key=int):
            err(d.rel, 0, f"ตารางสรุปประเด็นมีประเด็นที่ {n} แต่ไม่มีหัวข้อของประเด็นนั้น")
        # บรรทัด "ประเด็นที่ต้องปิดก่อน...: 1, 9" เลขต้องมีหัวข้อจริง
        for i, line in d.prose_lines():
            m = CLOSE_LINE.match(line.strip())
            if m:
                for n in parse_numbers(m.group(1)):
                    if n not in heading_nums:
                        err(d.rel, i, f"บรรทัดประเด็นที่ต้องปิดอ้างประเด็นที่ {n} แต่ไม่มีหัวข้อของประเด็นนั้น")


def actual_counts(docs):
    """นับของจริงให้ผู้เขียนเทียบกับตัวเลขที่สรุปไว้ในเอกสาร"""
    out = []
    all_flows = {}
    for d in docs:
        if is_flows(d):
            flows = {n for _, _, t in d.headings for n in re.findall(r"^Flow\s+(\d+)(?!\d)", t)}
            if flows:
                out.append(f"{d.rel}: Flow {len(flows)} เรื่อง")
                all_flows[d.rel] = flows
        if is_decisions(d) and d.fence_count % 2 != 0:
            out.append(f"{d.rel}: มีบล็อกโค้ดไม่ปิด จำนวนประเด็นนับไม่ได้จนกว่าจะแก้")
        elif is_decisions(d):
            nums = [n for n in d.heading_numbers() if n.isdigit()]
            statuses = {}
            for line in d.lines:
                m = re.match(r"^\*\*(?:สถานะ|Status):\*\*\s*(.+?)(?:\s*\(|$)", line.strip(), re.IGNORECASE)
                if m:
                    key = m.group(1).strip()
                    statuses[key] = statuses.get(key, 0) + 1
            if nums:
                detail = ", ".join(f"{k} {v}" for k, v in sorted(statuses.items()))
                out.append(f"{d.rel}: ประเด็น {len(nums)} เรื่อง" + (f" ({detail})" if detail else ""))
        if is_screens(d):
            hs = d.headings
            total = 0
            parts = []
            for idx, (line_no, level, text) in enumerate(hs):
                end = hs[idx + 1][0] - 1 if idx + 1 < len(hs) else len(d.lines)
                rows = [r for r in table_rows(d, line_no, end) if r and r[0].isdigit()]
                if rows:
                    parts.append(f"{text[:30]} {len(rows)}")
                    total += len(rows)
            if total:
                out.append(f"{d.rel}: แถวที่มีเลขลำดับ {total} แถว ({'; '.join(parts)})")
        if is_api(d):
            methods = sum(1 for line in d.lines if re.match(r"^\s*\|\s*(GET|POST|PUT|PATCH|DELETE)\b", line))
            if methods:
                out.append(f"{d.rel}: แถว API {methods} แถว")
        if is_data_model(d):
            entities = [t for _, level, t in d.headings if level == 3 and re.match(r"^[A-Za-z][A-Za-z0-9_]*(?=\s|$)", t)]
            if entities:
                out.append(f"{d.rel}: Entity {len(entities)} รายการ")
    if len(all_flows) > 1:
        union = set().union(*all_flows.values())
        total = sum(len(v) for v in all_flows.values())
        dup = f" (มีเลขซ้ำ {total - len(union)} เลข ดูรายการผิดพลาด)" if total != len(union) else ""
        out.append(f"รวมทุกไฟล์ลำดับการทำงาน: Flow {len(union)} เรื่อง{dup}")
    return out


# ---------- ลองวาดผัง Mermaid ----------

def find_browser():
    candidates = [
        os.environ.get("CHROME_PATH", ""),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "msedge", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    return None


def render_mermaid(docs):
    blocks = [(d.rel, start, code) for d in docs for start, code in d.mermaid]
    if not blocks:
        notes.append("ไม่มีผัง Mermaid ให้วาด")
        return
    browser = find_browser()
    if not browser:
        notes.append("ข้ามการวาดผัง Mermaid: ไม่พบ Chrome หรือ Edge ในเครื่อง")
        return
    parts = ['<!doctype html><meta charset="utf-8"><body>']
    for n, (_, _, code) in enumerate(blocks):
        parts.append(f'<pre class="m" id="d{n}">{html.escape(code)}</pre>')
    parts.append("""<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<script>
(async () => {
  const out = document.createElement('div'); out.id = 'result';
  if (typeof mermaid === 'undefined') { out.textContent = 'NOLIB'; document.body.prepend(out); return; }
  mermaid.initialize({ startOnLoad: false });
  const res = [];
  for (const el of document.querySelectorAll('pre.m')) {
    try { await mermaid.parse(el.textContent); await mermaid.render('g' + el.id, el.textContent); res.push(el.id + '=ok'); }
    catch (e) { res.push(el.id + '=ERR ' + String(e).replace(/\\s+/g, ' ').replace(/\\|\\|/g, '|').slice(0, 160)); }
  }
  out.textContent = res.join('||'); document.body.prepend(out);
})();
</script>""")
    tmp = Path(tempfile.mkdtemp(prefix="check_docs_"))
    page = tmp / "mermaid.html"
    page.write_text("\n".join(parts), encoding="utf-8")
    try:
        proc = subprocess.run(
            [browser, "--headless=new", "--disable-gpu", "--virtual-time-budget=25000",
             f"--user-data-dir={tmp / 'profile'}", "--dump-dom", page.as_uri()],
            capture_output=True, timeout=120)
        dom = proc.stdout.decode("utf-8", errors="replace")
    except Exception as e:
        notes.append(f"ข้ามการวาดผัง Mermaid: เรียกเบราว์เซอร์ไม่สำเร็จ ({e})")
        return
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    m = re.search(r'<div id="result">(.*?)</div>', dom, re.S)
    if not m:
        notes.append("ข้ามการวาดผัง Mermaid: เบราว์เซอร์ไม่ส่งผลกลับ สาเหตุที่พบบ่อยคือไม่มีอินเทอร์เน็ต หรือ proxy ขององค์กรกันการโหลด cdn.jsdelivr.net "
                     "ให้ตรวจผังด้วยคำเตือนเรื่องเครื่องหมายคำพูดข้างต้น และเปิดดูในโปรแกรมที่แสดง Mermaid ได้แทน")
        return
    result = html.unescape(m.group(1))
    if result == "NOLIB":
        notes.append("ข้ามการวาดผัง Mermaid: โหลดตัววาดผังจากอินเทอร์เน็ตไม่สำเร็จ")
        return
    ok = 0
    for item in result.split("||"):
        key, _, status = item.partition("=")
        if not re.fullmatch(r"d\d+", key) or int(key[1:]) >= len(blocks):
            continue  # ชิ้นที่แยกผิดเพราะข้อความผิดพลาดของ Mermaid มี || อยู่ข้างใน
        n = int(key[1:])
        rel, start, _ = blocks[n]
        if status == "ok":
            ok += 1
        else:
            err(rel, start, f"ผัง Mermaid วาดไม่ได้: {status[4:]}")
    notes.append(f"วาดผัง Mermaid ได้ {ok} จาก {len(blocks)} ผัง")


# ---------- หลัก ----------

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if len(args) != 1 or flags - {"--render"}:
        print(__doc__)
        return 2
    root = Path(args[0]).resolve()
    if not root.is_dir():
        print(f"ไม่พบโฟลเดอร์: {root}")
        return 2

    check_structure(root)
    check_numbering(root)

    paths = []
    if (root / "README.md").is_file():
        paths.append(root / "README.md")
    for sub in ("docs", "plans"):
        if (root / sub).is_dir():
            paths.extend(sorted(p for p in (root / sub).rglob("*.md") if p.is_file()))
    docs = [Doc(p, root) for p in paths]

    check_fences(docs)
    check_file_refs(root, docs)
    check_section_refs(root, docs)
    check_readme_lists_files(root, docs)
    check_invented_codes(docs)
    check_leftovers(docs)
    check_empty_sections(docs)
    check_pending_marks(docs)
    check_decision_summary(docs)
    if "--render" in flags:
        render_mermaid(docs)

    total_lines = sum(len(d.lines) for d in docs)
    total_mermaid = sum(len(d.mermaid) for d in docs)
    print(f"ตรวจ {len(docs)} ไฟล์ {total_lines} บรรทัด ผัง Mermaid {total_mermaid} ผัง")
    for n in notes:
        print(f"  หมายเหตุ: {n}")

    counts = actual_counts(docs)
    if counts:
        print("\nจำนวนที่นับได้จริง (ใช้เทียบกับตัวเลขที่สรุปไว้ในเอกสาร)")
        for c in counts:
            print(f"  {c}")

    def show(title, items):
        print(f"\n{title} ({len(items)})")
        for f, line, msg in sorted(items):
            loc = f"{f}:{line}" if line else f
            print(f"  {loc}  {msg}")

    show("ผิดพลาด", errors)
    show("เตือน", warnings)
    print("\nผล: " + ("ไม่ผ่าน ต้องแก้รายการระดับผิดพลาด" if errors else "ผ่านการตรวจด้วยสคริปต์"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
