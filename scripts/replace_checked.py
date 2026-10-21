#!/usr/bin/env python3
"""แก้ข้อความหลายจุดหลายไฟล์ในชุดเอกสาร โดยตรวจจำนวนจุดที่แก้ก่อนเขียนไฟล์

ใช้เมื่อต้องเปลี่ยนคำ ตัวเลข หรือประโยคเดียวกันในหลายไฟล์ เช่น หลังรีวิว หรือเมื่อผู้ใช้สั่งเปลี่ยนคำที่ใช้
ถ้ารายการใดหาไม่เจอ หรือจำนวนจุดไม่ตรงกับที่ระบุ สคริปต์จะไม่เขียนไฟล์ใดเลย เพื่อไม่ให้แก้ไปครึ่งเดียว

วิธีใช้:
    python3 <โฟลเดอร์สกิล>/scripts/replace_checked.py <โฟลเดอร์โครงการ> <ไฟล์รายการแก้.json> [--dry-run]

ไฟล์รายการแก้เป็น JSON แบบ UTF-8 เป็นรายการของ
    {"file": "docs/03-flows.md", "find": "ข้อความเดิม", "replace": "ข้อความใหม่", "count": 2}

    file     ตำแหน่งไฟล์จากโฟลเดอร์โครงการ หรือ "*" หมายถึง README.md และทุกไฟล์ .md ใน docs/ และ plans/
             ยกเว้นไฟล์ 00- ซึ่งเป็นบันทึกข้อมูลต้นทางตามที่ได้รับ
    find     ข้อความเดิม ต้องตรงทุกตัวอักษร ถ้ามีหลายบรรทัด ตัวขึ้นบรรทัดต้องตรงกับไฟล์ (ไฟล์ CRLF ใช้ \r\n)
    replace  ข้อความใหม่
    count    จำนวนจุดที่ต้องพบ ถ้าไม่ระบุ: ไฟล์เดียวต้องพบ 1 จุดพอดี ส่วน "*" ต้องพบอย่างน้อย 1 จุดรวมทุกไฟล์

รายการถูกทำตามลำดับ รายการหลังเห็นผลของรายการก่อน
--dry-run แสดงว่าจะแก้อะไรโดยไม่เขียนไฟล์

ไฟล์รายการแก้เป็นไฟล์ชั่วคราว ให้วางไว้นอกโฟลเดอร์โครงการ เช่น โฟลเดอร์ชั่วคราวของระบบ เพื่อไม่ให้ติดไปกับชุดเอกสาร

รหัสออก: 0 = สำเร็จ, 1 = มีรายการที่ไม่ตรง ไม่ได้เขียนไฟล์, 2 = เรียกใช้ผิด
"""
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def all_docs(root):
    files = []
    if (root / "README.md").is_file():
        files.append(root / "README.md")
    for sub in ("docs", "plans"):
        if (root / sub).is_dir():
            files.extend(sorted(p for p in (root / sub).rglob("*.md") if p.is_file()))
    # ไฟล์ 00- เป็นบันทึกข้อมูลต้นทางตามที่ได้รับ ไม่เปลี่ยนคำตามไฟล์อื่น ถ้าต้องแก้ให้ระบุชื่อไฟล์ตรง ๆ
    return [f for f in files if not f.name.startswith("00-")]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if len(args) != 2 or flags - {"--dry-run"}:
        print(__doc__)
        return 2
    root = Path(args[0]).resolve()
    try:
        edits = json.loads(Path(args[1]).read_text(encoding="utf-8"))
    except Exception as e:
        print(f"อ่านไฟล์รายการแก้ไม่ได้: {e}")
        return 2
    if not isinstance(edits, list):
        print("ไฟล์รายการแก้ต้องเป็นรายการ (list)")
        return 2

    content = {}  # path -> ข้อความปัจจุบันในหน่วยความจำ

    def load(p):
        if p not in content:
            with open(p, encoding="utf-8", newline="") as f:  # คงรูปแบบขึ้นบรรทัดเดิมของไฟล์
                content[p] = f.read()
        return content[p]

    problems = []
    report = []
    for n, e in enumerate(edits, 1):
        if not isinstance(e, dict):
            problems.append(f"รายการที่ {n}: ต้องเป็น object ที่มี file, find และ replace")
            continue
        find = e.get("find", "")
        repl = e.get("replace")
        target = e.get("file", "")
        if not isinstance(find, str) or not isinstance(repl, str) or not isinstance(target, str) or not find or not target:
            problems.append(f"รายการที่ {n}: ต้องมี file, find และ replace เป็นข้อความ และ find กับ file ต้องไม่ว่าง")
            continue
        if target == "*":
            paths = all_docs(root)
        else:
            p = (root / target).resolve()
            if root not in p.parents:
                problems.append(f"รายการที่ {n}: ไฟล์ {target} อยู่นอกโฟลเดอร์โครงการ")
                continue
            if not p.is_file():
                problems.append(f"รายการที่ {n}: ไม่พบไฟล์ {target}")
                continue
            paths = [p]
        hits = {p: load(p).count(find) for p in paths}
        total = sum(hits.values())
        expected = e.get("count")
        label = find if len(find) <= 40 else find[:37] + "..."
        if expected is not None and (type(expected) is not int or expected < 1):
            problems.append(f"รายการที่ {n}: count ต้องเป็นจำนวนเต็มตั้งแต่ 1 ({target})")
            continue
        if expected is None:
            ok = total >= 1 if target == "*" else total == 1
            want = "อย่างน้อย 1" if target == "*" else "1"
        else:
            ok = total == expected
            want = str(expected)
        if not ok:
            problems.append(f"รายการที่ {n}: \"{label}\" พบ {total} จุด แต่ต้องการ {want} จุด ({target})")
            continue
        for p, c in hits.items():
            if c:
                content[p] = content[p].replace(find, repl)
                report.append(f"รายการที่ {n}: {p.relative_to(root).as_posix()} {c} จุด  \"{label}\"")

    if problems:
        print("ไม่ได้เขียนไฟล์ใด เพราะมีรายการที่ไม่ตรง")
        for line in problems:
            print("  " + line)
        return 1
    print("จุดที่จะแก้" if "--dry-run" in flags else "จุดที่แก้")
    for line in report:
        print("  " + line)
    if "--dry-run" in flags:
        print("\nทดลองเท่านั้น ไม่ได้เขียนไฟล์")
        return 0
    changed = 0
    for p, text in content.items():
        with open(p, encoding="utf-8", newline="") as f:
            original = f.read()
        if text != original:
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            changed += 1
    print(f"\nเขียนไฟล์แล้ว {changed} ไฟล์ จาก {len(edits)} รายการแก้")
    return 0


if __name__ == "__main__":
    sys.exit(main())
