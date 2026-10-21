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

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
NUM_TOKEN = re.compile(r"^(\d+(?:\.\d+)*)\.?(?=\s|$|:)")
LIST_NUM = re.compile(r"^\s*[-*]\s+(\d+(?:\.\d+)+)\s")


class Doc:
    def __init__(self, path, root):
        self.path = path
        self.rel = path.relative_to(root).as_posix()
        self.text = path.read_text(encoding="utf-8", errors="replace")
        self.lines = self.text.splitlines()
        self.in_code = []          # แต่ละบรรทัดอยู่ในบล็อกโค้ดหรือไม่
        self.headings = []         # (line_no, level, text)
        self.mermaid = []          # (start_line, code)
        self.fence_count = 0
        self._scan()

    def _scan(self):
        inside = False
        lang = ""
        buf = []
        start = 0
        for i, line in enumerate(self.lines, 1):
            if FENCE.match(line):
                self.fence_count += 1
                if not inside:
                    inside = True
                    lang = line.strip().lstrip("`~").strip().lower()
                    buf = []
                    start = i
                else:
                    if lang == "mermaid":
                        self.mermaid.append((start, "\n".join(buf)))
                    inside = False
                    lang = ""
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
    elif not list(docs.glob("*.md")):
        err("docs/", 0, "โฟลเดอร์ docs/ ไม่มีไฟล์ .md")
    if not plans.is_dir():
        err("plans/", 0, "ไม่พบโฟลเดอร์ plans/")
    elif not list(plans.glob("*.md")):
        err("plans/", 0, "โฟลเดอร์ plans/ ไม่มีไฟล์ .md")


def check_numbering(root):
    docs = root / "docs"
    if not docs.is_dir():
        return
    seen = {}
    for p in sorted(docs.glob("*.md")):
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


# ข้อความใน [] () {} ของ flowchart ที่มีวงเล็บ ทับ หรือโคลอน แต่ไม่ได้ครอบด้วยเครื่องหมายคำพูด จะทำให้ผังวาดไม่ได้
LABEL = re.compile(r"[A-Za-z0-9_\u0E00-\u0E7F]+\s*(\[\(|\[/|\[\\|\[\[|\(\(|\{\{|\[|\(|\{|>)\s*"
                   r"(\"[^\"\n]*\"|[^\]\)\}\n\"]*?)\s*(\)\]|/\]|\\\]|\]\]|\)\)|\}\}|\]|\)|\})")
EDGE_LABEL = re.compile(r"\|([^|\n]*)\|")


def check_mermaid_labels(d, start, code):
    first = next((l.strip() for l in code.splitlines() if l.strip() and not l.strip().startswith("%%")), "").lower()
    if not (first.startswith("flowchart") or first.startswith("graph")):
        return
    for k, line in enumerate(code.splitlines()):
        texts = [m.group(2) for m in LABEL.finditer(line)] + [m.group(1) for m in EDGE_LABEL.finditer(line)]
        for t in texts:
            t = re.sub(r"<br\s*/?>", " ", t).strip()  # <br/> คือการขึ้นบรรทัดใหม่ของ Mermaid ใช้ได้โดยไม่ต้องครอบ
            if not t or (t.startswith('"') and t.endswith('"')):
                continue
            if re.search(r"[()/:]", t):
                warn(d.rel, start + 1 + k, f"ข้อความในผัง Mermaid มีวงเล็บ ทับ หรือโคลอน แต่ไม่ได้ครอบด้วยเครื่องหมายคำพูด: {t[:40]}")
                break


def check_fences(docs):
    known = ("flowchart", "graph", "sequencediagram", "statediagram", "erdiagram", "classdiagram",
             "gantt", "journey", "pie", "mindmap", "timeline", "quadrantchart", "gitgraph",
             "c4context", "requirementdiagram", "block-beta", "sankey-beta", "xychart-beta")
    for d in docs:
        if d.fence_count % 2 != 0:
            err(d.rel, 0, "บล็อกโค้ด (```) เปิดปิดไม่ครบคู่")
        for start, code in d.mermaid:
            first = next((l.strip() for l in code.splitlines() if l.strip() and not l.strip().startswith("%%")), "")
            if not first:
                err(d.rel, start, "ผัง Mermaid ว่างเปล่า")
            elif not first.lower().replace(" ", "").startswith(known):
                warn(d.rel, start, f"บรรทัดแรกของผัง Mermaid ไม่ใช่ชนิดผังที่รู้จัก: {first[:40]}")
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
SECTION_REF = re.compile(r"((?:[A-Za-z0-9_.-]+/)*\d\d-[a-z0-9-]+\.md)" + WRAP + r"(?:\]\([^)\s]*\))?" + WRAP + r"[\s(:,;\u2013\u2014-]*(?:ข้อ|หัวข้อ|หัวข้อที่|section)\s+", re.IGNORECASE)
DECISION_REF = re.compile(r"(?:ประเด็นที่|(?<![A-Za-z0-9_])Decisions?)\s+#?(?=\d)", re.IGNORECASE)
CLOSE_LINE = re.compile(r"^\*\*[^*\n]*(?:ประเด็นที่ต้องปิด|Decisions to close)[^*\n]*:\*\*\s*(.*)$", re.IGNORECASE)
FLOW_WORD = r"(?<![A-Za-z0-9_])Flow"
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
    return re.match(r"\s*(?:$|[,;:()\]}.]|และ|and\b|ถึง|to\b|[-–](?=\s*\d))", text[pos:]) is not None


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
    if not flows:
        err("docs/", 0, "ไม่พบไฟล์ลำดับการทำงาน (ชื่อไฟล์ต้องมีคำว่า flows) จึงตรวจการอ้าง Flow ไม่ได้")
    if not decisions:
        err("docs/", 0, "ไม่พบไฟล์ประเด็นตัดสินใจ (ชื่อไฟล์ต้องมีคำว่า decisions) จึงตรวจการอ้างประเด็นไม่ได้")

    for d in docs:
        if is_source_doc(d):
            continue  # บันทึกข้อความของลูกค้าตามเดิม เลข Flow หรือประเด็นในนั้นเป็นของลูกค้า
        for i, line in d.prose_lines():
            for m in SECTION_REF.finditer(line):
                target = by_name.get(Path(m.group(1)).name)
                if not target:
                    continue  # ไฟล์ไม่มีอยู่ ถูกรายงานแล้วในการตรวจไฟล์
                have = target.referable_numbers()
                for n in parse_numbers(line[m.end():]):
                    if n not in have:
                        err(d.rel, i, f"อ้างถึง {target.path.name} ข้อ {n} แต่ไฟล์นั้นไม่มีหัวข้อเลข {n}")
            if flows:
                for m in re.finditer(FLOW_WORD + r"\s+(?=\d)", line):
                    for n in parse_numbers(line[m.end():]):
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
        for p in sorted(folder.glob("*.md")):
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
        hs = d.headings
        for idx, (line_no, level, text) in enumerate(hs):
            end = hs[idx + 1][0] - 1 if idx + 1 < len(hs) else len(d.lines)
            body = [l for l in d.lines[line_no:end] if l.strip() and l.strip() != "---"]
            if body:
                continue
            if idx + 1 < len(hs) and hs[idx + 1][1] > level:
                continue  # หัวข้อที่มีแต่หัวข้อย่อย ถือว่ามีเนื้อหา
            if line_no <= 3:
                continue  # ชื่อเอกสารและชื่อรองที่ต้นไฟล์
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
        if not is_decisions(d):
            continue
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
        if is_decisions(d):
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
            paths.extend(sorted((root / sub).rglob("*.md")))
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
