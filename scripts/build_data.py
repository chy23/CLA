#!/usr/bin/env python3
"""
115學年-1 課堂形成性評量試題解析與圖片擷取引擎
掃描 sources/{chinese,math,social}/ 內的 .docx / .pdf / .md / .json 檔案，
自動擷取圖片並輸出標準 JSON 至 data/，供靜態評量網站讀取。
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    from PIL import Image, ImageOps
except ImportError:
    Image = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR = ROOT / "sources"
ASSETS_IMG_DIR = ROOT / "assets" / "images"
DATA_DIR = ROOT / "data"

SUBJECTS = [
    ("chinese", "國語", "115學年-1 每週時事國語形成性評量"),
    ("math", "數學", "115學年-1 每週時事數學形成性評量"),
    ("social", "社會", "115學年-1 每週時事社會形成性評量"),
]

CN_NUM_MAP = {
    "零": 0, "〇": 0,
    "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
}


def parse_cn_or_int(raw: str) -> Optional[int]:
    s = raw.strip()
    if s.isdigit():
        return int(s)
    if not s:
        return None
    if s == "十":
        return 10
    if "十" in s:
        parts = s.split("十", 1)
        tens = 1 if parts[0] == "" else CN_NUM_MAP.get(parts[0], 0)
        ones = 0 if parts[1] == "" else CN_NUM_MAP.get(parts[1], 0)
        val = tens * 10 + ones
        return val if val > 0 else None
    val = 0
    for ch in s:
        if ch in CN_NUM_MAP:
            val = val * 10 + CN_NUM_MAP[ch]
        else:
            return None
    return val if val > 0 else None


def normalize_tf_answer(raw: str) -> str:
    s = raw.strip().strip("（）()。. ")
    if s in ("○", "O", "o", "〇", "正確", "對", "V", "✓"):
        return "○"
    if s in ("✕", "×", "X", "x", "✗", "✘", "錯誤", "錯"):
        return "✕"
    return s


def infer_section_type(title: str) -> str:
    if "是非" in title:
        return "true_false"
    if "單選" in title or "選擇" in title:
        return "choice"
    if "填空" in title or "填充" in title:
        return "fill_blank"
    if "計算" in title:
        return "calculation"
    if "應用" in title or "探究" in title or "情境" in title or "問答" in title or "思辨" in title:
        return "applied"
    return "applied"


def split_score_info(text: str) -> Tuple[str, str]:
    m = re.match(r"^(.*?)(?:[（(]([^()（）]+)[）)])?\s*$", text.strip())
    if not m:
        return text.strip(), ""
    return m.group(1).strip(), (m.group(2) or "").strip()


def extract_points_from_score_info(score_info: str) -> Optional[int]:
    m = re.search(r"每題\s*(\d+)\s*分", score_info)
    return int(m.group(1)) if m else None


def parse_rubric_entries(lines: List[str]) -> List[Dict[str, str]]:
    """將評分規準文字解析為結構化的 [{score, text}] 列表。"""
    entries: List[Dict[str, str]] = []
    for raw in lines:
        line = re.sub(r"^[●•\-－]\s*", "", raw.strip())
        if not line or line in ("評分規準：", "評分規準:", "評分要點：", "評分要點:"):
            continue
        line = re.sub(r"^評分(?:規準|要點)[：:]\s*", "", line)
        # 若單行以分號隔開多項（如國語第11週「評分要點：...（4分）；...（6分）」）
        parts = [p.strip("；;。. ") for p in re.split(r"[；;]", line) if p.strip("；;。. ")]
        for part in parts:
            # 型式一：「8分：說明...」
            m1 = re.match(r"^(\d+(?:～\d+|-\d+)?\s*分)\s*[：:]\s*(.+)$", part)
            if m1:
                entries.append({"score": m1.group(1).replace(" ", ""), "text": m1.group(2).strip()})
                continue
            # 型式二：「說明文字（6 分）」
            m2 = re.match(r"^(.+?)[（(](\d+(?:～\d+|-\d+)?\s*分)[）)]\s*$", part)
            if m2:
                entries.append({"score": m2.group(2).replace(" ", ""), "text": m2.group(1).strip("：: ")})
                continue
            entries.append({"score": "", "text": part})
    return entries


def split_options_from_stem(stem: str) -> Tuple[str, List[Dict[str, str]]]:
    """僅從題幹（答案之前）擷取 (A)(B)(C)(D) 選項。"""
    pattern = re.compile(r"\(([A-D])\)\s*")
    matches = list(pattern.finditer(stem))
    if len(matches) < 2:
        return stem.strip(), []
    # 只取最後一組連續 A..D 避免題幹前面偶有 (A) 干擾
    start_idx = 0
    for idx in range(len(matches)):
        if matches[idx].group(1) == "A":
            start_idx = idx
    selected = matches[start_idx:]
    keys = [m.group(1) for m in selected]
    if "A" not in keys or "B" not in keys:
        return stem.strip(), []
    prompt = stem[: selected[0].start()].strip()
    options: List[Dict[str, str]] = []
    for i, m in enumerate(selected):
        end = selected[i + 1].start() if i + 1 < len(selected) else len(stem)
        opt_text = stem[m.end() : end].strip()
        options.append({"key": m.group(1), "text": opt_text})
    return prompt, options


def split_subquestions_and_material(stem: str) -> Tuple[str, str, List[Dict[str, Any]], List[str], List[Dict[str, str]], str]:
    """
    從非選擇題／應用題題幹中抽出：
    - material（【歷史情境故事】/【生活情境】等）
    - stances（立場甲/立場乙、傳統派/創新派）
    - bulletPrompts（企劃提案欄位）
    - prompt（主指導語）
    - subQuestions（(1) ... (2) ...）
    - stancePrompt（立場比較後之「請回答：...」提問）
    """
    text = stem.strip()
    material = ""
    stances: List[Dict[str, str]] = []
    bullet_prompts: List[str] = []
    stance_prompt = ""

    # 抽取【材料】區塊（僅在題幹中出現於請回答/子題之前）
    mat_markers = [
        "【歷史情境故事】",
        "【歷史與現代情境】",
        "【生活情境】",
        "【時事與探究材料】",
        "【真實生活思辨】",
    ]
    for marker in mat_markers:
        if marker in text:
            idx = text.find(marker)
            before = text[:idx].strip()
            after = text[idx + len(marker) :].strip()
            # 找材料結束點：換行後的「請...」或「身為...」或「(1)」
            split_m = re.search(
                r"(?:\n+|\s+)(?=(?:請(?:閱讀|根據|結合|回答|為|運用)|身為關心|你所就讀|\(1\)))",
                after,
            )
            if split_m:
                material = f"{marker} {after[:split_m.start()].strip()}"
                rest = after[split_m.end() :].strip()
                text = (before + ("\n" if before and rest else "") + rest).strip()
            else:
                material = f"{marker} {after}"
                text = before
            break

    # 抽出條列（立場對比 vs 一般欄位條列）
    before_stance_lines: List[str] = []
    after_stance_lines: List[str] = []
    seen_stance = False
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        bm = re.match(r"^[●•]\s*(.+)$", s)
        if bm:
            content = bm.group(1).strip()
            sm = re.match(r"^((?:立場[甲乙丙丁]|傳統派|創新派)(?:[（(][^）)]+[）)])?)\s*[：:]\s*(.+)$", content)
            if sm:
                seen_stance = True
                stances.append({"label": sm.group(1).strip(), "text": sm.group(2).strip()})
            else:
                bullet_prompts.append(content)
        else:
            if seen_stance:
                after_stance_lines.append(s)
            else:
                before_stance_lines.append(s)

    text = "\n".join(before_stance_lines).strip()
    stance_prompt = "\n".join(after_stance_lines).strip()

    # 切分 (1) (2) (3) 子題（容許緊接在 ： 或 ） 之後，如「回答下列問題：(1)...（8分）(2)...」）
    sub_matches = list(re.finditer(r"(?<![A-Za-z0-9])\((\d)\)\s*", text))
    sub_questions: List[Dict[str, Any]] = []
    if sub_matches:
        nums = [int(m.group(1)) for m in sub_matches]
        if nums[0] == 1:
            main_prompt = text[: sub_matches[0].start()].strip()
            for i, m in enumerate(sub_matches):
                end = sub_matches[i + 1].start() if i + 1 < len(sub_matches) else len(text)
                sub_raw = text[m.end() : end].strip()
                pt_m = re.search(r"[（(](\d+)\s*分[）)]\s*$", sub_raw)
                sub_pts = int(pt_m.group(1)) if pt_m else None
                sub_questions.append(
                    {
                        "label": f"({m.group(1)})",
                        "text": sub_raw,
                        "points": sub_pts,
                    }
                )
            text = main_prompt

    return text.strip(), material.strip(), sub_questions, bullet_prompts, stances, stance_prompt


def render_pdf_figure_to_png(pdf_path: Path, out_png: Path, dpi: int = 180) -> bool:
    """
    將單頁圖檔 PDF 轉為高解析 PNG，並自動裁切多餘白邊（保留適度留白）。
    """
    if fitz is None:
        return False
    try:
        doc = fitz.open(str(pdf_path))
        if len(doc) == 0:
            return False
        page = doc[0]
        zoom = max(1.0, dpi / 72.0)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        out_png.parent.mkdir(parents=True, exist_ok=True)
        if Image is not None and ImageOps is not None:
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            mask = ImageOps.invert(img.convert("L")).point(lambda v: 255 if v > 12 else 0)
            bbox = mask.getbbox()
            if bbox:
                pad = 16
                left = max(0, bbox[0] - pad)
                top = max(0, bbox[1] - pad)
                right = min(img.width, bbox[2] + pad)
                bottom = min(img.height, bbox[3] + pad)
                img = img.crop((left, top, right, bottom))
            img.save(str(out_png), format="PNG", optimize=True)
        else:
            pix.save(str(out_png))
        return out_png.exists() and out_png.stat().st_size > 0
    except Exception:
        return False


def sync_local_images(subject: str) -> Dict[str, str]:
    """
    掃描 sources/{subject}/images/ 與 assets/images/{subject}/，
    將 sources 內的圖檔（含圖檔 PDF）同步／轉檔到 assets/images/{subject}/，並建立多重索引便於配圖自動對應。
    回傳 {匹配鍵: 相對網站路徑}。
    """
    src_img_dir = SOURCES_DIR / subject / "images"
    dst_img_dir = ASSETS_IMG_DIR / subject
    dst_img_dir.mkdir(parents=True, exist_ok=True)

    valid_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
    if src_img_dir.exists():
        for p in sorted(src_img_dir.iterdir()):
            if not p.is_file() or p.name.startswith("."):
                continue
            suf = p.suffix.lower()
            if suf in valid_exts:
                target = dst_img_dir / p.name
                shutil.copy2(p, target)
            elif suf == ".pdf":
                target_png = dst_img_dir / f"{p.stem}.png"
                if render_pdf_figure_to_png(p, target_png):
                    print(f"  [圖檔 PDF 轉圖] {p.name} -> {target_png.name}")

    mapping: Dict[str, str] = {}
    if dst_img_dir.exists():
        for p in sorted(dst_img_dir.iterdir()):
            if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in valid_exts:
                rel = f"assets/images/{subject}/{p.name}"
                mapping[p.name] = rel
                mapping[p.stem] = rel
                # 正規化支援 第11週_圖一 / w11_fig1 / 第十一週_第7題 / w11_q7 互換
                m_fig = re.match(r"^(?:第\s*([一二三四五六七八九十\d]+)\s*週[_-]圖([一二三四五\d]+)|w(\d+)[_-]fig(\d+))", p.stem, re.IGNORECASE)
                if m_fig:
                    if m_fig.group(1):
                        wk = parse_cn_or_int(m_fig.group(1))
                        fig_n = parse_cn_or_int(m_fig.group(2)) or m_fig.group(2)
                    else:
                        wk = int(m_fig.group(3))
                        fig_n = int(m_fig.group(4))
                    if wk is not None:
                        mapping[f"__wk_{wk}_fig_{fig_n}__"] = rel
                m_q = re.match(r"^(?:第\s*([一二三四五六七八九十\d]+)\s*週[_-]第\s*(\d+)\s*題|w(\d+)[_-]q(\d+))", p.stem, re.IGNORECASE)
                if m_q:
                    if m_q.group(1):
                        wk = parse_cn_or_int(m_q.group(1))
                        qn = int(m_q.group(2))
                    else:
                        wk = int(m_q.group(3))
                        qn = int(m_q.group(4))
                    if wk is not None:
                        mapping[f"__wk_{wk}_q_{qn}__"] = rel
    return mapping


def resolve_local_figure_src(
    caption: str,
    week_num: int,
    fig_idx_in_week: int,
    local_map: Dict[str, str],
    q_display_num: Optional[int] = None,
) -> str:
    clean = re.sub(r"^(?:圖片[：:]\s*)?查看配圖[：:]\s*", "", caption).strip()
    if clean in local_map:
        return local_map[clean]
    for stem, rel in local_map.items():
        if not stem.startswith("__") and stem in clean:
            return rel
    if q_display_num is not None:
        q_key = f"__wk_{week_num}_q_{q_display_num}__"
        if q_key in local_map:
            return local_map[q_key]
    key = f"__wk_{week_num}_fig_{fig_idx_in_week}__"
    if key in local_map:
        return local_map[key]
    return ""


def make_drive_urls(raw_url: str) -> str:
    if not raw_url:
        return ""
    return raw_url


def check_choice_consistency(subject_label: str, week_num: int, q_num: int, answer: str, explanation: str, warnings: List[str]) -> None:
    if not answer or not explanation:
        return
    m = re.search(r"故選\s*[（(]([A-D])[）)]", explanation)
    if m and m.group(1) != answer:
        warnings.append(
            f"[{subject_label} 第{week_num}週 第{q_num}題] 答案為 ({answer})，但解析末句寫「故選 ({m.group(1)})」（已保留原文）。"
        )
    # 檢查常見解析與答案顯著不一致提示（例如社會第11週第2、3題內容檢核）
    if subject_label == "社會" and week_num == 11 and q_num == 2 and answer == "D" and "耕者有其田" in explanation:
        warnings.append(
            "[社會 第11週 第2題] 原檔答案標示為 (D)「農地重劃」，惟解析與選項內容對應 (C)「耕者有其田」，建議教師於原始 DOCX 複核。"
        )
    if subject_label == "社會" and week_num == 11 and q_num == 3 and answer == "A" and "改良土地" in explanation:
        warnings.append(
            "[社會 第11週 第3題] 原檔答案標示為 (A)，惟解析說明農民改良土地與增加投入對應選項 (B)，建議教師於原始 DOCX 複核。"
        )


# ==============================================================================
# DOCX 解析器（適用於社會科及一般 Word 試題檔）
# ==============================================================================
def parse_docx(path: Path, subject: str, subject_label: str, local_map: Dict[str, str], warnings: List[str]) -> Dict[str, Any]:
    ns = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    }
    with zipfile.ZipFile(path) as z:
        rels_xml = z.read("word/_rels/document.xml.rels")
        rels_root = ET.fromstring(rels_xml)
        rels: Dict[str, Tuple[str, str]] = {}
        for el in rels_root:
            rels[el.attrib["Id"]] = (el.attrib.get("Target", ""), el.attrib.get("Type", ""))

        # 若 DOCX 內有 word/media/ 內嵌圖片，自動匯出
        embedded_media_map: Dict[str, str] = {}
        for name in z.namelist():
            if name.startswith("word/media/"):
                fname = f"docx_{path.stem}_{Path(name).name}"
                out_path = ASSETS_IMG_DIR / subject / fname
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(z.read(name))
                embedded_media_map[name] = f"assets/images/{subject}/{fname}"

        doc_root = ET.fromstring(z.read("word/document.xml"))
        body = doc_root.find("w:body", ns)

    # 依序抽出結構化段落紀錄
    items: List[Dict[str, Any]] = []
    for child in body:
        tag = child.tag.split("}")[-1]
        if tag != "p":
            continue
        text = "".join(t.text or "" for t in child.findall(".//w:t", ns)).strip()
        links: List[Dict[str, str]] = []
        for hl in child.findall(".//w:hyperlink", ns):
            rid = hl.attrib.get(f"{{{ns['r']}}}id", "")
            ltxt = "".join(t.text or "" for t in hl.findall(".//w:t", ns)).strip()
            target, _ = rels.get(rid, ("", ""))
            if target:
                links.append({"text": ltxt, "url": target})
        images: List[str] = []
        for blip in child.findall(".//a:blip", ns):
            rid = blip.attrib.get(f"{{{ns['r']}}}embed", "")
            target, _ = rels.get(rid, ("", ""))
            if target:
                media_key = f"word/{target.lstrip('/')}"
                if media_key in embedded_media_map:
                    images.append(embedded_media_map[media_key])
        if text or links or images:
            items.append({"text": text, "links": links, "images": images})

    doc_title = ""
    doc_subtitle = ""
    meta_notes: List[Dict[str, Any]] = []
    weeks: List[Dict[str, Any]] = []

    current_note: Optional[Dict[str, Any]] = None
    in_toc = False
    current_week: Optional[Dict[str, Any]] = None
    current_section: Optional[Dict[str, Any]] = None
    current_q: Optional[Dict[str, Any]] = None
    in_answer_mode = False
    in_rubric_mode = False
    stem_chunks: List[str] = []
    ans_chunks: List[str] = []
    exp_chunks: List[str] = []
    rubric_lines: List[str] = []
    week_fig_counter = 0
    skipped_empty_by_week: Dict[int, List[int]] = {}

    def finalize_question() -> None:
        nonlocal current_q, stem_chunks, ans_chunks, exp_chunks, rubric_lines
        nonlocal in_answer_mode, in_rubric_mode
        if current_q is None or current_week is None:
            return
        stem = "\n".join(c for c in stem_chunks if c).strip()
        ans_raw = "\n".join(c for c in ans_chunks if c).strip()
        exp_raw = "\n".join(c for c in exp_chunks if c).strip()

        # 過濾原檔留白／待補佔位題（如無題目且無答案／選項）
        stem_stripped = re.sub(r"^題目?[：:]?\s*", "", stem).strip()
        if len(stem_stripped) < 3 and not ans_raw and not current_q["figures"]:
            src_num = current_q.get("sourceNumber", current_q["number"])
            skipped_empty_by_week.setdefault(current_week["weekNumber"], []).append(src_num)
            if current_week["questions"] and current_week["questions"][-1] is current_q:
                current_week["questions"].pop()
            current_q = None
            stem_chunks = []
            ans_chunks = []
            exp_chunks = []
            rubric_lines = []
            in_answer_mode = False
            in_rubric_mode = False
            return

        # 處理數學 DOCX「題目：【標籤】內文」格式
        if not current_q["tag"]:
            m_tag = re.match(r"^【(.+?)】\s*(.*)$", stem, re.DOTALL)
            if m_tag:
                current_q["tag"] = m_tag.group(1).strip()
                stem = m_tag.group(2).strip()

        q_type = current_q["type"]
        if q_type == "choice":
            prompt, options = split_options_from_stem(stem)
            if not options:
                q_type = "applied"
                prompt, material, sub_qs, bullets, stances, stance_prompt = split_subquestions_and_material(stem)
                current_q["type"] = q_type
                current_q["prompt"] = prompt
                current_q["material"] = material
                current_q["subQuestions"] = sub_qs
                current_q["bulletPrompts"] = bullets
                current_q["stances"] = stances
                current_q["stancePrompt"] = stance_prompt
            else:
                current_q["prompt"] = prompt
                current_q["options"] = options
        elif q_type == "true_false":
            current_q["prompt"] = re.sub(r"[（(][\s\u3000]*[）)]\s*$", "", stem).strip()
        else:
            prompt, material, sub_qs, bullets, stances, stance_prompt = split_subquestions_and_material(stem)
            current_q["prompt"] = prompt
            current_q["material"] = material
            current_q["subQuestions"] = sub_qs
            current_q["bulletPrompts"] = bullets
            current_q["stances"] = stances
            current_q["stancePrompt"] = stance_prompt

        # 拆解答案與無「解析：」前綴之數學算式／說明區塊
        steps_list: List[str] = []
        ans_lines = [ln.strip() for ln in ans_raw.splitlines() if ln.strip()]

        if current_q["type"] == "choice":
            if ans_lines:
                first_line = ans_lines[0]
                m_ans = re.match(r"^[（(]?([A-D])[）)]?\s*(.*)$", first_line)
                if m_ans:
                    current_q["answer"] = m_ans.group(1)
                    inline_exp = m_ans.group(2).strip("。 ")
                    tail_lines = ([inline_exp] if inline_exp else []) + ans_lines[1:]
                    if tail_lines and not exp_raw:
                        exp_raw = "\n".join(tail_lines)
                        if len(tail_lines) > 1 or any(op in exp_raw for op in ("=", "×", "÷", "≈")):
                            steps_list = tail_lines
                else:
                    current_q["answerText"] = ans_raw
        elif current_q["type"] == "true_false":
            if ans_lines:
                first_line = ans_lines[0]
                m_tf = re.match(r"^([○◯O×✕X✗✘])[。.]?\s*(.*)$", first_line)
                if m_tf:
                    current_q["answer"] = normalize_tf_answer(m_tf.group(1))
                    inline_exp = m_tf.group(2).strip()
                    tail_lines = ([inline_exp] if inline_exp else []) + ans_lines[1:]
                    if tail_lines and not exp_raw:
                        exp_raw = "\n".join(tail_lines)
                else:
                    current_q["answer"] = normalize_tf_answer(first_line)
                    if len(ans_lines) > 1 and not exp_raw:
                        exp_raw = "\n".join(ans_lines[1:])
        else:
            # 應用題：若有明確「解析：」則整個 ans_raw 皆為答案；若無「解析：」（數學格式），首段以 (1)(2)... 開頭為精簡答案，後續無 (N) 開頭段落為計算步驟與解析
            if not exp_raw and len(ans_lines) > 1 and any(re.match(r"^\(\d+\)", ln) for ln in ans_lines):
                short_ans: List[str] = []
                calc_lines: List[str] = []
                in_calc = False
                for ln in ans_lines:
                    if not in_calc and re.match(r"^\(\d+\)", ln):
                        short_ans.append(ln)
                    else:
                        in_calc = True
                        calc_lines.append(ln)
                if short_ans and calc_lines:
                    current_q["answerText"] = "\n".join(short_ans)
                    steps_list = calc_lines
                    exp_raw = "\n".join(calc_lines)
                else:
                    current_q["answerText"] = ans_raw
            else:
                current_q["answerText"] = ans_raw

        current_q["steps"] = steps_list
        current_q["explanation"] = exp_raw
        current_q["rubric"] = parse_rubric_entries(rubric_lines)

        if current_q["type"] == "choice":
            check_choice_consistency(
                subject_label,
                current_week["weekNumber"],
                current_q["number"],
                current_q["answer"],
                current_q["explanation"],
                warnings,
            )

        current_q = None
        stem_chunks = []
        ans_chunks = []
        exp_chunks = []
        rubric_lines = []
        in_answer_mode = False
        in_rubric_mode = False

    SECTION_CN_LABELS = ["壹", "貳", "參", "肆", "伍", "陸"]

    def ensure_section(default_type: str = "choice", force_new_for_type: bool = False) -> Dict[str, Any]:
        nonlocal current_section
        assert current_week is not None
        if current_section is not None:
            if not force_new_for_type or current_section["defaultType"] == default_type:
                return current_section
        sec_idx = len(current_week["sections"]) + 1
        title_map = {
            "true_false": "是非題",
            "choice": "選擇題",
            "applied": "應用題",
            "calculation": "計算題",
            "fill_blank": "填充題",
        }
        sec_label = SECTION_CN_LABELS[sec_idx - 1] if sec_idx <= len(SECTION_CN_LABELS) else str(sec_idx)
        sec = {
            "id": f"w{current_week['weekNumber']}_sec{sec_idx}",
            "label": sec_label,
            "title": title_map.get(default_type, "應用與探究情境題"),
            "scoreInfo": "",
            "defaultType": default_type,
        }
        current_week["sections"].append(sec)
        current_section = sec
        return sec

    for item in items:
        txt = item["text"]
        links = item["links"]
        imgs = item["images"]

        # 檔首標題與著作權準則說明（第一週開始前）
        if current_week is None:
            m_wk_check = re.match(r"^第\s*([一二三四五六七八九十\d]+)\s*週\s*[：:]\s*(.+)$", txt)
            if not m_wk_check:
                if not doc_title and txt:
                    doc_title = re.sub(r"^#\s*", "", txt).strip()
                elif not doc_subtitle and txt and not txt.startswith("【") and not txt.startswith("#") and "週次目錄" not in txt:
                    doc_subtitle = txt
                elif txt.startswith("【") and txt.endswith("】"):
                    current_note = {"title": txt.strip("【】"), "items": []}
                    meta_notes.append(current_note)
                    in_toc = False
                elif "週次目錄" in txt:
                    in_toc = True
                    current_note = None
                elif not in_toc and current_note is not None and txt:
                    current_note["items"].append(txt)
                continue

        # 週次標題
        m_wk = re.match(r"^第\s*([一二三四五六七八九十\d]+)\s*週\s*[：:]\s*(.+)$", txt)
        if m_wk:
            finalize_question()
            wk_num = parse_cn_or_int(m_wk.group(1))
            if wk_num is None:
                continue
            current_week = {
                "weekNumber": wk_num,
                "weekLabel": f"第 {wk_num} 週",
                "title": m_wk.group(2).strip(),
                "schedule": "",
                "goals": "",
                "sections": [],
                "contexts": [],
                "questions": [],
            }
            weeks.append(current_week)
            current_section = None
            week_fig_counter = 0
            continue

        if current_week is None:
            continue

        # 教學進度／教學單元 與 核心情境綱要／評量目標／學習重點（可能出現在週首或題與題之間）
        if re.match(r"^(?:教學進度|教學單元)[：:]", txt):
            finalize_question()
            current_week["schedule"] = re.sub(r"^(?:教學進度|教學單元)[：:]\s*", "", txt).strip()
            continue
        if re.match(r"^(?:核心情境綱要|評量目標|學習重點)[：:]", txt):
            finalize_question()
            current_week["goals"] = re.sub(r"^(?:核心情境綱要|評量目標|學習重點)[：:]\s*", "", txt).strip()
            continue

        # 大題標題（壹、貳、參...）
        m_sec = re.match(r"^([壹貳參肆伍陸])、\s*(.+)$", txt)
        if m_sec:
            finalize_question()
            sec_title, score_info = split_score_info(m_sec.group(2))
            sec_idx = len(current_week["sections"]) + 1
            sec = {
                "id": f"w{current_week['weekNumber']}_sec{sec_idx}",
                "label": m_sec.group(1),
                "title": sec_title,
                "scoreInfo": score_info,
                "defaultType": infer_section_type(sec_title),
            }
            current_week["sections"].append(sec)
            current_section = sec
            continue

        # 新題目開頭：1.【...】 或 題目 1：... 或 第 1 題（是非題）
        m_q1 = re.match(r"^(\d+)\.\s*【(.+?)】(?:[（(](\d+)\s*分[）)])?(.*)$", txt)
        m_q2 = re.match(r"^題目\s*(\d+)\s*[：:]\s*(.+?)(?:[（(](\d+)\s*分[）)])?$", txt)
        m_q_math = re.match(r"^第\s*(\d+)\s*題\s*[（(](是非題|選擇題|單選題|應用題|計算題|填充題|填空題)[）)]\s*$", txt)
        if m_q1 or m_q2 or m_q_math:
            finalize_question()
            if m_q1:
                raw_num = int(m_q1.group(1))
                q_tag = m_q1.group(2).strip()
                q_pts = int(m_q1.group(3)) if m_q1.group(3) else None
                inline_rest = m_q1.group(4).strip()
                sec = ensure_section("choice")
                q_type = sec["defaultType"]
            elif m_q2:
                raw_num = int(m_q2.group(1))
                q_tag = m_q2.group(2).strip()
                q_pts = int(m_q2.group(3)) if m_q2.group(3) else None
                inline_rest = ""
                sec = ensure_section("applied")
                q_type = sec["defaultType"]
            else:
                raw_num = int(m_q_math.group(1))  # type: ignore[union-attr]
                type_label = m_q_math.group(2).strip()  # type: ignore[union-attr]
                q_tag = ""
                q_pts = None
                inline_rest = ""
                q_type = infer_section_type(type_label)
                sec = ensure_section(q_type, force_new_for_type=True)

            if q_pts is None:
                q_pts = extract_points_from_score_info(sec["scoreInfo"])
            q_num = len(current_week["questions"]) + 1
            current_q = {
                "id": f"{subject}_w{current_week['weekNumber']}_q{q_num}",
                "number": q_num,
                "displayNumber": str(raw_num) if m_q_math else str(q_num),
                "sourceNumber": raw_num,
                "sectionId": sec["id"],
                "contextId": None,
                "type": q_type,
                "points": q_pts,
                "tag": q_tag,
                "prompt": "",
                "material": "",
                "subQuestions": [],
                "bulletPrompts": [],
                "stances": [],
                "stancePrompt": "",
                "figures": [],
                "options": [],
                "answer": "",
                "answerText": "",
                "steps": [],
                "explanation": "",
                "rubric": [],
            }
            current_week["questions"].append(current_q)
            if inline_rest:
                stem_chunks.append(inline_rest)
            continue

        if current_q is None:
            continue

        # 題目內配圖連結與圖片來源說明（支援「查看配圖：」與「圖片：查看配圖：」）
        m_fig = re.match(r"^(?:圖片[：:]\s*)?查看配圖[：:]\s*(.*)$", txt)
        if m_fig or (not txt.startswith("題目") and links and any("drive.google.com" in l["url"] for l in links)):
            week_fig_counter += 1
            caption = (m_fig.group(1).strip() if m_fig else txt) or f"第{current_week['weekNumber']}週圖{week_fig_counter}"
            drive_links = [l["url"] for l in links if "drive.google.com" in l["url"]]
            drive_url = drive_links[0] if drive_links else (links[0]["url"] if links else "")
            local_src = resolve_local_figure_src(
                caption,
                current_week["weekNumber"],
                week_fig_counter,
                local_map,
                q_display_num=current_q.get("sourceNumber"),
            )
            current_q["figures"].append(
                {
                    "id": f"w{current_week['weekNumber']}_fig{week_fig_counter}",
                    "caption": caption,
                    "src": local_src,
                    "driveUrl": make_drive_urls(drive_url),
                    "sourceNote": "",
                }
            )
            continue

        if imgs:
            for img_src in imgs:
                week_fig_counter += 1
                current_q["figures"].append(
                    {
                        "id": f"w{current_week['weekNumber']}_fig{week_fig_counter}",
                        "caption": f"第 {current_week['weekNumber']} 週配圖 {week_fig_counter}",
                        "src": img_src,
                        "driveUrl": "",
                        "sourceNote": "",
                    }
                )
            if not txt:
                continue

        if txt.startswith("圖片來源與查證說明："):
            note = txt[len("圖片來源與查證說明：") :].strip()
            if current_q["figures"]:
                current_q["figures"][-1]["sourceNote"] = note
            continue

        # 答案 / 解析 / 評分規準區塊
        if txt.startswith("答案：") or txt.startswith("答案:"):
            in_answer_mode = True
            in_rubric_mode = False
            rest_ans = re.sub(r"^答案[：:]\s*", "", txt)
            if rest_ans:
                ans_chunks.append(rest_ans)
            continue

        if txt.startswith("解析：") or txt.startswith("解析:"):
            in_answer_mode = False
            in_rubric_mode = False
            exp_chunks.append(re.sub(r"^解析[：:]\s*", "", txt))
            continue

        if txt in ("評分規準：", "評分規準:") or txt.startswith("評分規準："):
            in_answer_mode = False
            in_rubric_mode = True
            rest = re.sub(r"^評分規準[：:]\s*", "", txt)
            if rest:
                rubric_lines.append(rest)
            continue

        if in_rubric_mode:
            rubric_lines.append(txt)
        elif in_answer_mode:
            ans_chunks.append(txt)
        else:
            clean_stem_line = re.sub(r"^題目[：:]\s*", "", txt)
            stem_chunks.append(clean_stem_line)

    finalize_question()

    # 若有略過空白待補佔位題，印出提醒並重新整理每週連續 number（保留原始 displayNumber）
    for wk in weeks:
        for idx, q in enumerate(wk["questions"], start=1):
            q["number"] = idx
            q["id"] = f"{subject}_w{wk['weekNumber']}_q{idx}"
        skipped_nums = skipped_empty_by_week.get(wk["weekNumber"])
        if skipped_nums:
            nums_str = "、".join(str(n) for n in skipped_nums)
            warnings.append(
                f"[{subject_label} 第{wk['weekNumber']}週] 略過原檔空白待補題目：第 {nums_str} 題（保留實有題 {len(wk['questions'])} 題）。"
            )

    return {
        "subject": subject,
        "subjectLabel": subject_label,
        "title": doc_title or f"115學年-1 每週時事{subject_label}試題",
        "subtitle": doc_subtitle,
        "sourceFiles": [path.name],
        "updatedAt": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "metaNotes": meta_notes,
        "weeks": weeks,
    }


# ==============================================================================
# PDF 解析器（適用於國語科及未來 PDF 試題檔）
# ==============================================================================
HARD_BREAK_RE = re.compile(
    r"^(?:"
    r"第\s*[一二三四五六七八九十\d]+\s*週\s*[：:]"
    r"|各週目錄與試題導覽"
    r"|教學進度[：:]"
    r"|評量目標[：:]"
    r"|核心情境綱要[：:]"
    r"|班級[：:]"
    r"|[壹貳參肆伍陸]、"
    r"|【情境[一二三四五六七八九十\d]+[：:]"
    r"|【答案與(?:解析|評分規準)】"
    r"|(?:捷運導覽摺頁節錄|資訊閱讀站|文化局宣導文宣節錄|時事焦點|展板摘錄|藝文快訊)[：:]"
    r"|(?:\d+\.\s*){1,2}[（(]\s*[）)]"
    r"|題目\s*\d+\s*[：:]"
    r"|\d+\.\s*【"
    r"|\([A-D]\)"
    r"|\(\d+\)"
    r"|[●•]\s+"
    r"|-\s+"
    r"|請回答[：:]?"
    r"|請(?:觀察|仔細觀察|閱讀|根據).+[：:]$"
    r"|評分(?:要點|規準)[：:]?"
    r"|(?:參考)?範例[：:]?"
    r"|答案[：:]|解析[：:]"
    r"|__PDF_IMAGE__"
    r")"
)


def extract_pdf_stream(pdf_path: Path, subject: str) -> List[Dict[str, Any]]:
    """
    利用 PyMuPDF 依頁內閱讀順序抽取文字行與內嵌插圖，並智慧合併軟換行。
    """
    if fitz is None:
        raise RuntimeError("需要 PyMuPDF (fitz) 才能解析 PDF 檔案，請先安裝 pymupdf。")

    doc = fitz.open(str(pdf_path))
    raw_entries: List[Dict[str, Any]] = []
    img_dir = ASSETS_IMG_DIR / subject
    img_dir.mkdir(parents=True, exist_ok=True)
    raw_img_idx = 0

    for page_idx, page in enumerate(doc):
        page_dict = page.get_text("dict")
        blocks = sorted(page_dict.get("blocks", []), key=lambda b: (round(b["bbox"][1], 1), round(b["bbox"][0], 1)))
        for b in blocks:
            if b.get("type") == 1:
                w = b.get("width", 0)
                h = b.get("height", 0)
                img_bytes = b.get("image")
                ext = b.get("ext", "png").lower()
                if ext == "jpeg":
                    ext = "jpg"
                # 過濾極細分隔線（例如第 12、14 頁的 453x3 橫線）
                if w < 80 or h < 40 or not img_bytes:
                    continue
                raw_img_idx += 1
                temp_name = f"pdf_{pdf_path.stem}_p{page_idx + 1}_{raw_img_idx}.{ext}"
                out_file = img_dir / temp_name
                out_file.write_bytes(img_bytes)
                raw_entries.append(
                    {
                        "kind": "image",
                        "temp_path": out_file,
                        "ext": ext,
                        "width": w,
                        "height": h,
                        "text": f"__PDF_IMAGE__:{temp_name}",
                        "x0": b["bbox"][0],
                        "x1": b["bbox"][2],
                    }
                )
            elif b.get("type") == 0:
                for line in b.get("lines", []):
                    txt = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
                    if not txt:
                        continue
                    raw_entries.append(
                        {
                            "kind": "text",
                            "text": txt,
                            "x0": line["bbox"][0],
                            "x1": line["bbox"][2],
                        }
                    )

    # 合併軟換行
    merged: List[Dict[str, Any]] = []
    for ent in raw_entries:
        if ent["kind"] == "image":
            merged.append(ent)
            continue
        txt = ent["text"]
        if not merged or merged[-1]["kind"] == "image":
            merged.append(ent)
            continue

        prev = merged[-1]
        prev_txt = prev["text"]

        # 若本行為明確新段落／題目／選項／條列，不合併
        if HARD_BREAK_RE.match(txt):
            merged.append(ent)
            continue

        # 單行題標題或單行引言結尾，不把下一行吞進來
        if re.match(r"^(?:\d+\.\s*){1,2}[（(]\s*[）)]\s*\S+$", prev_txt) and not prev_txt.endswith(("：", ":", "，", "、")):
            merged.append(ent)
            continue
        if re.match(r"^題目\s*\d+\s*[：:]", prev_txt):
            merged.append(ent)
            continue
        if re.match(r"^(?:第\s*[一二三四五六七八九十\d]+\s*週\s*[：:]|[壹貳參肆伍陸]、|【情境|各週目錄與試題導覽|班級[：:]|請回答[：:]?$|參考範例[：:]?$)", prev_txt):
            merged.append(ent)
            continue
        if prev_txt.startswith("教學進度：") or prev_txt.startswith("評量目標：") or prev_txt.startswith("核心情境綱要："):
            # 評量目標／教學進度若跨行（未到下一標頭），合併
            if not txt.startswith(("班級：", "評量目標：", "教學進度：")):
                joiner = " " if (prev_txt[-1].isascii() and prev_txt[-1].isalnum() and txt[0].isascii() and txt[0].isalnum()) else ""
                prev["text"] = prev_txt + joiner + txt
                prev["x1"] = ent["x1"]
                continue
            merged.append(ent)
            continue
        if re.match(r"^(?:捷運導覽摺頁節錄|資訊閱讀站|文化局宣導文宣節錄|時事焦點|展板摘錄|藝文快訊)[：:]", prev_txt):
            merged.append(ent)
            continue

        # 短行結尾（如段落末句 `x1 < 460` 且句末標點結束），視為真正段落換行
        if prev["x1"] < 460 and prev_txt[-1] in "。！？：）」』" and not prev_txt.endswith("："):
            # 特殊例外：題幹內第一句結尾後通常接「請選出...」/「請問：」仍屬同一段落或視情況保留換行
            if txt.startswith(("請", "這", "依", "根", "作", "他", "她", "（", "(")):
                # 如果是 (作答時...) 或 題幹問句，保留換行讓排版更清楚；除非是跨行詞彙
                merged.append(ent)
                continue
            merged.append(ent)
            continue

        joiner = " " if (prev_txt[-1].isascii() and prev_txt[-1].isalnum() and txt[0].isascii() and txt[0].isalnum()) else ""
        prev["text"] = prev_txt + joiner + txt
        prev["x1"] = ent["x1"]

    return merged


def parse_pdf(path: Path, subject: str, subject_label: str, local_map: Dict[str, str], warnings: List[str]) -> Dict[str, Any]:
    entries = extract_pdf_stream(path, subject)

    doc_title = ""
    weeks: List[Dict[str, Any]] = []
    current_week: Optional[Dict[str, Any]] = None
    current_section: Optional[Dict[str, Any]] = None
    current_context: Optional[Dict[str, Any]] = None
    current_q: Optional[Dict[str, Any]] = None

    in_toc_block = False
    in_context_body = False
    in_answer_mode = False
    in_rubric_mode = False

    ctx_intro_lines: List[str] = []
    ctx_excerpt_lines: List[str] = []
    ctx_in_excerpt = False
    ctx_range: Optional[Tuple[int, int]] = None

    stem_chunks: List[str] = []
    ans_chunks: List[str] = []
    rubric_lines: List[str] = []
    week_fig_counter = 0

    def finalize_context() -> None:
        nonlocal current_context, in_context_body, ctx_intro_lines, ctx_excerpt_lines, ctx_in_excerpt, ctx_range
        if current_context is None or not in_context_body:
            return
        current_context["intro"] = "\n".join(ctx_intro_lines).strip()
        current_context["excerpt"] = "\n".join(ctx_excerpt_lines).strip()
        in_context_body = False
        ctx_intro_lines = []
        ctx_excerpt_lines = []
        ctx_in_excerpt = False

    def finalize_question() -> None:
        nonlocal current_q, stem_chunks, ans_chunks, rubric_lines, in_answer_mode, in_rubric_mode
        if current_q is None:
            return

        stem = "\n".join(c for c in stem_chunks if c).strip()
        ans_raw = "\n".join(c for c in ans_chunks if c).strip()
        q_type = current_q["type"]

        if q_type == "choice":
            prompt, options = split_options_from_stem(stem)
            current_q["prompt"] = prompt
            current_q["options"] = options
        elif q_type == "true_false":
            current_q["prompt"] = stem
        else:
            prompt, material, sub_qs, bullets, stances, stance_prompt = split_subquestions_and_material(stem)
            current_q["prompt"] = prompt
            current_q["material"] = material
            current_q["subQuestions"] = sub_qs
            current_q["bulletPrompts"] = bullets
            current_q["stances"] = stances
            current_q["stancePrompt"] = stance_prompt

        if q_type == "choice":
            m_ans = re.match(r"^[（(]?([A-D])[）)]?\s*(?:[。.]\s*)?(.*)$", ans_raw, re.DOTALL)
            if m_ans:
                current_q["answer"] = m_ans.group(1)
                current_q["explanation"] = m_ans.group(2).strip()
            else:
                current_q["answerText"] = ans_raw
        elif q_type == "true_false":
            m_tf = re.match(r"^([○◯O×✕X✗✘])[。.]?\s*(.*)$", ans_raw, re.DOTALL)
            if m_tf:
                current_q["answer"] = normalize_tf_answer(m_tf.group(1))
                current_q["explanation"] = m_tf.group(2).strip()
            else:
                current_q["answer"] = normalize_tf_answer(ans_raw)
        else:
            # 應用題答案中可能包含內嵌「評分要點：...」或「評分規準：...」
            clean_ans_lines: List[str] = []
            extra_rubric: List[str] = []
            hit_rubric_inline = False
            for ln in ans_raw.splitlines():
                s = ln.strip()
                if re.match(r"^[●•\-－]?\s*評分(?:要點|規準)[：:]?", s):
                    hit_rubric_inline = True
                    extra_rubric.append(s)
                elif hit_rubric_inline:
                    extra_rubric.append(s)
                else:
                    s_clean = re.sub(r"^-\s+(?=參考範例[：:])", "", s)
                    clean_ans_lines.append(s_clean)
            current_q["answerText"] = "\n".join(clean_ans_lines).strip()
            all_rubric_lines = extra_rubric + rubric_lines
            current_q["rubric"] = parse_rubric_entries(all_rubric_lines)

        if q_type == "choice" and current_week is not None:
            check_choice_consistency(
                subject_label,
                current_week["weekNumber"],
                current_q["number"],
                current_q["answer"],
                current_q["explanation"],
                warnings,
            )

        current_q = None
        stem_chunks = []
        ans_chunks = []
        rubric_lines = []
        in_answer_mode = False
        in_rubric_mode = False

    for ent in entries:
        # 圖片區塊處理
        if ent["kind"] == "image":
            temp_path: Path = ent["temp_path"]
            if current_week is not None:
                week_fig_counter += 1
                final_name = f"w{current_week['weekNumber']}_fig{week_fig_counter}.{ent['ext']}"
                final_path = temp_path.parent / final_name
                if final_path.exists():
                    final_path.unlink()
                temp_path.rename(final_path)
                fig_obj = {
                    "id": f"w{current_week['weekNumber']}_fig{week_fig_counter}",
                    "caption": (current_context["title"].strip("【】") if current_context else f"第 {current_week['weekNumber']} 週配圖"),
                    "src": f"assets/images/{subject}/{final_name}",
                    "driveUrl": "",
                    "sourceNote": "",
                }
                if in_context_body and current_context is not None:
                    current_context["figures"].append(fig_obj)
                elif current_q is not None:
                    current_q["figures"].append(fig_obj)
                elif current_context is not None:
                    current_context["figures"].append(fig_obj)
            else:
                temp_path.unlink(missing_ok=True)
            continue

        txt = ent["text"].strip()
        if not txt:
            continue

        # 忽略頁首目錄與班級座號列
        if txt == "各週目錄與試題導覽":
            in_toc_block = True
            continue
        if in_toc_block:
            if re.match(r"^[●•]\s*【第\s*\d+\s*週", txt):
                continue
            in_toc_block = False
        if txt.startswith("班級："):
            continue

        # 檔首標題
        if current_week is None:
            m_wk_head = re.match(r"^第\s*([一二三四五六七八九十\d]+)\s*週\s*[：:]\s*(.+)$", txt)
            if not m_wk_head:
                if not doc_title:
                    doc_title = txt
                continue

        # 週次標題
        m_wk = re.match(r"^第\s*([一二三四五六七八九十\d]+)\s*週\s*[：:]\s*(.+)$", txt)
        if m_wk:
            finalize_question()
            finalize_context()
            wk_num = parse_cn_or_int(m_wk.group(1))
            if wk_num is None:
                continue
            current_week = {
                "weekNumber": wk_num,
                "weekLabel": f"第 {wk_num} 週",
                "title": m_wk.group(2).strip(),
                "schedule": "",
                "goals": "",
                "sections": [],
                "contexts": [],
                "questions": [],
            }
            weeks.append(current_week)
            current_section = None
            current_context = None
            ctx_range = None
            week_fig_counter = 0
            continue

        if current_week is None:
            continue

        # 教學進度與評量目標
        if current_q is None and txt.startswith("教學進度："):
            current_week["schedule"] = txt[len("教學進度：") :].strip()
            continue
        if current_q is None and (txt.startswith("評量目標：") or txt.startswith("核心情境綱要：")):
            current_week["goals"] = re.sub(r"^(?:評量目標|核心情境綱要)：\s*", "", txt).strip()
            continue

        # 大題標題
        m_sec = re.match(r"^([壹貳參肆伍陸])、\s*(.+)$", txt)
        if m_sec:
            finalize_question()
            finalize_context()
            current_context = None
            ctx_range = None
            sec_title, score_info = split_score_info(m_sec.group(2))
            sec_idx = len(current_week["sections"]) + 1
            sec = {
                "id": f"w{current_week['weekNumber']}_sec{sec_idx}",
                "label": m_sec.group(1),
                "title": sec_title,
                "scoreInfo": score_info,
                "defaultType": infer_section_type(sec_title),
            }
            current_week["sections"].append(sec)
            current_section = sec
            continue

        # 情境區塊標題
        m_ctx = re.match(r"^【情境[一二三四五六七八九十\d]+[：:].+?】$", txt)
        if m_ctx:
            finalize_question()
            finalize_context()
            ctx_idx = len(current_week["contexts"]) + 1
            current_context = {
                "id": f"w{current_week['weekNumber']}_ctx{ctx_idx}",
                "sectionId": current_section["id"] if current_section else "",
                "title": txt,
                "intro": "",
                "excerptTitle": "",
                "excerpt": "",
                "figures": [],
                "_assigned_count": 0,
                "_expected_count": None,
            }
            current_week["contexts"].append(current_context)
            in_context_body = True
            ctx_in_excerpt = False
            ctx_intro_lines = []
            ctx_excerpt_lines = []
            ctx_range = None
            continue

        # 題目開頭（含修復 Google Docs PDF `7. 1. （ ）...`、`4. （ ）...`、`題目 7：...`）
        m_q_mc = re.match(r"^(?:\d+\.\s*){1,2}[（(]\s*[）)]\s*(.+)$", txt)
        m_q_app = re.match(r"^題目\s*(\d+)\s*[：:]\s*(.+?)(?:[（(](\d+)\s*分[）)])?$", txt)
        m_q_dx = re.match(r"^(\d+)\.\s*【(.+?)】(?:[（(](\d+)\s*分[）)])?(.*)$", txt)

        if m_q_mc or m_q_app or m_q_dx:
            finalize_question()
            if in_context_body:
                finalize_context()

            if current_section is None:
                sec = {
                    "id": f"w{current_week['weekNumber']}_sec1",
                    "label": "壹",
                    "title": "試題評量",
                    "scoreInfo": "",
                    "defaultType": "choice",
                }
                current_week["sections"].append(sec)
                current_section = sec

            q_pts: Optional[int] = None
            inline_rest = ""
            q_type = current_section["defaultType"]
            if m_q_mc:
                raw_after_paren = m_q_mc.group(1).strip()
                if q_type == "true_false" and len(raw_after_paren) > 16:
                    q_tag = "是非判斷"
                    inline_rest = raw_after_paren
                else:
                    q_tag = raw_after_paren
            elif m_q_app:
                q_tag = m_q_app.group(2).strip()
                q_pts = int(m_q_app.group(3)) if m_q_app.group(3) else None
            else:
                q_tag = m_q_dx.group(2).strip()  # type: ignore[union-attr]
                q_pts = int(m_q_dx.group(3)) if m_q_dx.group(3) else None  # type: ignore[union-attr]
                inline_rest = m_q_dx.group(4).strip()  # type: ignore[union-attr]

            if q_pts is None:
                q_pts = extract_points_from_score_info(current_section["scoreInfo"])

            q_num = len(current_week["questions"]) + 1

            # 判斷題目是否隸屬於當前情境
            assigned_ctx_id: Optional[str] = None
            if current_context is not None and current_context["sectionId"] == current_section["id"]:
                exp_cnt = current_context.get("_expected_count")
                if exp_cnt is not None:
                    if current_context["_assigned_count"] < exp_cnt:
                        assigned_ctx_id = current_context["id"]
                        current_context["_assigned_count"] += 1
                elif current_context["figures"]:
                    # 含插圖的情境三僅在題目初期（或最多前 2 題讀圖題）掛載，避免後續獨立企劃與思辨題重複顯示圖片
                    if current_context["_assigned_count"] < 2:
                        assigned_ctx_id = current_context["id"]
                        current_context["_assigned_count"] += 1

            current_q = {
                "id": f"{subject}_w{current_week['weekNumber']}_q{q_num}",
                "number": q_num,
                "displayNumber": str(q_num),
                "sectionId": current_section["id"],
                "contextId": assigned_ctx_id,
                "type": q_type,
                "points": q_pts,
                "tag": q_tag,
                "prompt": "",
                "material": "",
                "subQuestions": [],
                "bulletPrompts": [],
                "stances": [],
                "stancePrompt": "",
                "figures": [],
                "options": [],
                "answer": "",
                "answerText": "",
                "steps": [],
                "explanation": "",
                "rubric": [],
            }
            current_week["questions"].append(current_q)
            if inline_rest:
                stem_chunks.append(inline_rest)
            continue

        # 情境內容累積
        if in_context_body and current_context is not None:
            m_rng = re.search(r"回答第\s*(\d+)\s*[～~-]\s*(\d+)\s*題|判斷第\s*(\d+)\s*[～~-]\s*(\d+)\s*題", txt)
            if m_rng:
                a = int(m_rng.group(1) or m_rng.group(3))
                b = int(m_rng.group(2) or m_rng.group(4))
                current_context["_expected_count"] = (b - a + 1)

            if re.match(r"^(?:捷運導覽摺頁節錄|資訊閱讀站|文化局宣導文宣節錄|時事焦點|展板摘錄|藝文快訊)[：:]", txt):
                current_context["excerptTitle"] = txt
                ctx_in_excerpt = True
            elif ctx_in_excerpt:
                ctx_excerpt_lines.append(txt)
            else:
                ctx_intro_lines.append(txt)
            continue

        if current_q is None:
            continue

        # 題目答案／解析／評分規準
        if txt.startswith("【答案與解析】：") or txt.startswith("【答案與評分規準】：") or txt.startswith("【答案與解析】") or txt.startswith("【答案與評分規準】"):
            in_answer_mode = True
            in_rubric_mode = False
            rest = re.sub(r"^【答案與(?:解析|評分規準)】[：:]?\s*", "", txt)
            if rest:
                ans_chunks.append(rest)
            continue

        if re.match(r"^[●•\-－]?\s*評分(?:規準|要點)[：:]?", txt):
            in_answer_mode = False
            in_rubric_mode = True
            rest = re.sub(r"^[●•\-－]?\s*評分(?:規準|要點)[：:]?\s*", "", txt)
            if rest:
                rubric_lines.append(rest)
            continue

        if in_rubric_mode:
            rubric_lines.append(txt)
        elif in_answer_mode:
            ans_chunks.append(txt)
        else:
            stem_chunks.append(txt)

    finalize_question()
    finalize_context()

    # 清理暫存欄位與二度確認含圖情境只綁定確實參考圖表的題目
    for wk in weeks:
        ctx_by_id = {c["id"]: c for c in wk["contexts"]}
        for c in wk["contexts"]:
            c.pop("_assigned_count", None)
            c.pop("_expected_count", None)
        for q in wk["questions"]:
            if q["contextId"] and q["contextId"] in ctx_by_id:
                ctx = ctx_by_id[q["contextId"]]
                if ctx.get("figures") and not ctx.get("excerpt"):
                    # 確保沒提及圖片/現場的題目不會誤掛配圖
                    combined_txt = q["prompt"] + " " + " ".join(s["text"] for s in q["subQuestions"])
                    if not any(k in combined_txt for k in ("圖", "現場", "舞台", "舞臺", "觀眾", "造型", "造形")):
                        q["contextId"] = None

    return {
        "subject": subject,
        "subjectLabel": subject_label,
        "title": doc_title or f"115學年-1 每週時事{subject_label}試題",
        "subtitle": "",
        "sourceFiles": [path.name],
        "updatedAt": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "metaNotes": [],
        "weeks": weeks,
    }


# ==============================================================================
# Markdown 解析器（適用於數學科或未來任何 .md 試題檔）
# ==============================================================================
def parse_markdown(path: Path, subject: str, subject_label: str, local_map: Dict[str, str], warnings: List[str]) -> Dict[str, Any]:
    lines = path.read_text(encoding="utf-8").splitlines()
    doc_title = ""
    weeks: List[Dict[str, Any]] = []
    current_week: Optional[Dict[str, Any]] = None
    current_section: Optional[Dict[str, Any]] = None
    current_context: Optional[Dict[str, Any]] = None
    current_q: Optional[Dict[str, Any]] = None

    mode = "meta"  # meta | context | stem | answer | steps | explanation | rubric
    ctx_lines: List[str] = []
    stem_lines: List[str] = []
    ans_lines: List[str] = []
    step_lines: List[str] = []
    exp_lines: List[str] = []
    rubric_lines: List[str] = []
    week_fig_counter = 0

    def finalize_context() -> None:
        nonlocal current_context, ctx_lines
        if current_context is not None and ctx_lines:
            current_context["excerpt"] = "\n".join(ctx_lines).strip()
            ctx_lines = []

    def finalize_question() -> None:
        nonlocal current_q, stem_lines, ans_lines, step_lines, exp_lines, rubric_lines, mode
        if current_q is None:
            return
        stem = "\n".join(stem_lines).strip()
        ans_raw = "\n".join(ans_lines).strip()
        exp_raw = "\n".join(exp_lines).strip()

        if current_q["type"] == "choice":
            prompt, options = split_options_from_stem(stem)
            current_q["prompt"] = prompt
            current_q["options"] = options
            m_ans = re.match(r"^[（(]?([A-D])[）)]?\s*(.*)$", ans_raw, re.DOTALL)
            if m_ans:
                current_q["answer"] = m_ans.group(1)
                if m_ans.group(2).strip() and not exp_raw:
                    exp_raw = m_ans.group(2).strip()
            else:
                current_q["answerText"] = ans_raw
        elif current_q["type"] == "true_false":
            current_q["prompt"] = stem
            current_q["answer"] = normalize_tf_answer(ans_raw)
        else:
            prompt, material, sub_qs, bullets, stances, stance_prompt = split_subquestions_and_material(stem)
            current_q["prompt"] = prompt
            current_q["material"] = material
            current_q["subQuestions"] = sub_qs
            current_q["bulletPrompts"] = bullets
            current_q["stances"] = stances
            current_q["stancePrompt"] = stance_prompt
            current_q["answerText"] = ans_raw

        clean_steps = [re.sub(r"^(?:\d+\.\s*|[-•]\s*)", "", s.strip()) for s in step_lines if s.strip()]
        current_q["steps"] = clean_steps
        current_q["explanation"] = exp_raw
        current_q["rubric"] = parse_rubric_entries(rubric_lines)

        if current_q["type"] == "choice" and current_week is not None:
            check_choice_consistency(subject_label, current_week["weekNumber"], current_q["number"], current_q["answer"], exp_raw, warnings)

        current_q = None
        stem_lines = []
        ans_lines = []
        step_lines = []
        exp_lines = []
        rubric_lines = []
        mode = "stem"

    for raw in lines:
        s = raw.strip()

        if s.startswith("# ") and current_week is None:
            doc_title = s[2:].strip()
            continue

        m_wk = re.match(r"^##\s*第\s*([一二三四五六七八九十\d]+)\s*週\s*[：:]\s*(.+)$", s)
        if m_wk:
            finalize_question()
            finalize_context()
            wk_num = parse_cn_or_int(m_wk.group(1))
            if wk_num is None:
                continue
            current_week = {
                "weekNumber": wk_num,
                "weekLabel": f"第 {wk_num} 週",
                "title": m_wk.group(2).strip(),
                "schedule": "",
                "goals": "",
                "sections": [],
                "contexts": [],
                "questions": [],
            }
            weeks.append(current_week)
            current_section = None
            current_context = None
            week_fig_counter = 0
            mode = "meta"
            continue

        if current_week is None:
            continue

        if current_q is None and re.match(r"^-?\s*教學進度[：:]", s):
            current_week["schedule"] = re.sub(r"^-?\s*教學進度[：:]\s*", "", s)
            continue
        if current_q is None and re.match(r"^-?\s*(?:評量目標|核心情境綱要)[：:]", s):
            current_week["goals"] = re.sub(r"^-?\s*(?:評量目標|核心情境綱要)[：:]\s*", "", s)
            continue

        m_sec = re.match(r"^###\s*([壹貳參肆伍陸])、\s*(.+)$", s)
        if m_sec:
            finalize_question()
            finalize_context()
            current_context = None
            sec_title, score_info = split_score_info(m_sec.group(2))
            sec_idx = len(current_week["sections"]) + 1
            current_section = {
                "id": f"w{current_week['weekNumber']}_sec{sec_idx}",
                "label": m_sec.group(1),
                "title": sec_title,
                "scoreInfo": score_info,
                "defaultType": infer_section_type(sec_title),
            }
            current_week["sections"].append(current_section)
            mode = "meta"
            continue

        m_ctx = re.match(r"^####\s*(【.+】)$", s)
        if m_ctx:
            finalize_question()
            finalize_context()
            ctx_idx = len(current_week["contexts"]) + 1
            current_context = {
                "id": f"w{current_week['weekNumber']}_ctx{ctx_idx}",
                "sectionId": current_section["id"] if current_section else "",
                "title": m_ctx.group(1),
                "intro": "",
                "excerptTitle": "",
                "excerpt": "",
                "figures": [],
            }
            current_week["contexts"].append(current_context)
            mode = "context"
            continue

        m_q = re.match(r"^(\d+)\.\s*【(.+?)】(?:[（(](\d+)\s*分[）)])?(.*)$", s)
        if m_q:
            finalize_question()
            if mode == "context":
                finalize_context()
            if current_section is None:
                current_section = {
                    "id": f"w{current_week['weekNumber']}_sec1",
                    "label": "壹",
                    "title": "試題評量",
                    "scoreInfo": "",
                    "defaultType": "applied",
                }
                current_week["sections"].append(current_section)

            q_pts = int(m_q.group(3)) if m_q.group(3) else extract_points_from_score_info(current_section["scoreInfo"])
            q_num = len(current_week["questions"]) + 1
            ctx_id = current_context["id"] if (current_context and current_context["sectionId"] == current_section["id"]) else None
            current_q = {
                "id": f"{subject}_w{current_week['weekNumber']}_q{q_num}",
                "number": q_num,
                "displayNumber": str(q_num),
                "sectionId": current_section["id"],
                "contextId": ctx_id,
                "type": current_section["defaultType"],
                "points": q_pts,
                "tag": m_q.group(2).strip(),
                "prompt": "",
                "material": "",
                "subQuestions": [],
                "bulletPrompts": [],
                "stances": [],
                "stancePrompt": "",
                "figures": [],
                "options": [],
                "answer": "",
                "answerText": "",
                "steps": [],
                "explanation": "",
                "rubric": [],
            }
            current_week["questions"].append(current_q)
            if m_q.group(4).strip():
                stem_lines.append(m_q.group(4).strip())
            mode = "stem"
            continue

        # Markdown 圖片語法 ![說明](路徑)
        m_img = re.match(r"^!\[(.*?)\]\((.+?)\)$", s)
        if m_img and current_week is not None:
            week_fig_counter += 1
            img_ref = m_img.group(2).strip()
            img_name = Path(img_ref).name
            src = local_map.get(img_name) or local_map.get(Path(img_ref).stem) or ""
            fig_obj = {
                "id": f"w{current_week['weekNumber']}_fig{week_fig_counter}",
                "caption": m_img.group(1).strip() or f"第 {current_week['weekNumber']} 週配圖",
                "src": src,
                "driveUrl": "",
                "sourceNote": "",
            }
            if mode == "context" and current_context is not None:
                current_context["figures"].append(fig_obj)
            elif current_q is not None:
                current_q["figures"].append(fig_obj)
            continue

        if mode == "context":
            if s:
                ctx_lines.append(s)
            continue

        if current_q is None:
            continue

        if re.match(r"^答案[：:]", s):
            mode = "answer"
            rest = re.sub(r"^答案[：:]\s*", "", s)
            if rest:
                ans_lines.append(rest)
        elif re.match(r"^步驟[：:]", s):
            mode = "steps"
            rest = re.sub(r"^步驟[：:]\s*", "", s)
            if rest:
                step_lines.append(rest)
        elif re.match(r"^解析[：:]", s):
            mode = "explanation"
            rest = re.sub(r"^解析[：:]\s*", "", s)
            if rest:
                exp_lines.append(rest)
        elif re.match(r"^評分(?:規準|要點)[：:]", s):
            mode = "rubric"
            rest = re.sub(r"^評分(?:規準|要點)[：:]\s*", "", s)
            if rest:
                rubric_lines.append(rest)
        else:
            if not s:
                continue
            if mode == "answer":
                ans_lines.append(s)
            elif mode == "steps":
                step_lines.append(s)
            elif mode == "explanation":
                exp_lines.append(s)
            elif mode == "rubric":
                rubric_lines.append(s)
            else:
                stem_lines.append(s)

    finalize_question()
    finalize_context()

    return {
        "subject": subject,
        "subjectLabel": subject_label,
        "title": doc_title or f"115學年-1 每週時事{subject_label}試題",
        "subtitle": "",
        "sourceFiles": [path.name],
        "updatedAt": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "metaNotes": [],
        "weeks": weeks,
    }


def merge_subject_docs(subject: str, subject_label: str, default_title: str, docs: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not docs:
        return {
            "subject": subject,
            "subjectLabel": subject_label,
            "title": default_title,
            "subtitle": "尚未匯入試題檔案，請將 .docx / .pdf / .md 放入對應科目資料夾後執行更新。",
            "sourceFiles": [],
            "updatedAt": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
            "metaNotes": [],
            "weeks": [],
        }

    if len(docs) == 1:
        return docs[0]

    merged_weeks: Dict[int, Dict[str, Any]] = {}
    source_files: List[str] = []
    meta_notes: List[Dict[str, Any]] = []
    title = docs[0].get("title") or default_title
    subtitle = docs[0].get("subtitle") or ""

    for d in docs:
        source_files.extend(d.get("sourceFiles", []))
        meta_notes.extend(d.get("metaNotes", []))
        for wk in d.get("weeks", []):
            merged_weeks[wk["weekNumber"]] = wk

    sorted_weeks = [merged_weeks[k] for k in sorted(merged_weeks.keys())]
    return {
        "subject": subject,
        "subjectLabel": subject_label,
        "title": title,
        "subtitle": subtitle,
        "sourceFiles": source_files,
        "updatedAt": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "metaNotes": meta_notes,
        "weeks": sorted_weeks,
    }


def build_all() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    warnings: List[str] = []
    manifest_subjects: Dict[str, Any] = {}

    print("============================================================")
    print("  115學年-1 形成性評量試題建置工具 (CLA Build Engine)")
    print("============================================================")

    for subject, label, default_title in SUBJECTS:
        subj_dir = SOURCES_DIR / subject
        subj_dir.mkdir(parents=True, exist_ok=True)
        local_map = sync_local_images(subject)

        files = sorted(
            p for p in subj_dir.iterdir()
            if p.is_file()
            and not p.name.startswith(("_", ".", "~$"))
            and p.suffix.lower() in {".docx", ".pdf", ".md", ".json"}
        )

        parsed_docs: List[Dict[str, Any]] = []
        for f in files:
            ext = f.suffix.lower()
            try:
                if ext == ".docx":
                    parsed_docs.append(parse_docx(f, subject, label, local_map, warnings))
                elif ext == ".pdf":
                    parsed_docs.append(parse_pdf(f, subject, label, local_map, warnings))
                elif ext == ".md":
                    parsed_docs.append(parse_markdown(f, subject, label, local_map, warnings))
                elif ext == ".json":
                    data = json.loads(f.read_text(encoding="utf-8"))
                    data.setdefault("sourceFiles", [f.name])
                    parsed_docs.append(data)
            except Exception as exc:
                warnings.append(f"[{label}] 解析檔案 {f.name} 失敗：{exc}")
                raise

        merged = merge_subject_docs(subject, label, default_title, parsed_docs)
        out_json = DATA_DIR / f"{subject}.json"
        out_json.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

        weeks = merged.get("weeks", [])
        total_q = sum(len(w.get("questions", [])) for w in weeks)
        manifest_subjects[subject] = {
            "subject": subject,
            "label": label,
            "title": merged.get("title", default_title),
            "totalWeeks": len(weeks),
            "totalQuestions": total_q,
            "sourceFiles": merged.get("sourceFiles", []),
            "updatedAt": merged.get("updatedAt"),
            "weeks": [
                {
                    "weekNumber": w["weekNumber"],
                    "weekLabel": w["weekLabel"],
                    "title": w["title"],
                    "schedule": w.get("schedule", ""),
                    "questionCount": len(w.get("questions", [])),
                }
                for w in weeks
            ],
        }

        week_summary = ", ".join(f"第{w['weekNumber']}週({len(w['questions'])}題)" for w in weeks) or "尚無試題"
        print(f"  [{label}] 來源檔：{len(files)} 份 | 週次：{len(weeks)} | 總題數：{total_q} | {week_summary}")

    manifest = {
        "generatedAt": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "subjects": manifest_subjects,
    }
    (DATA_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    if warnings:
        print("\n[提醒與資料檢核]")
        for w in warnings:
            print(f"  ! {w}")
    print("\n已完成資料建置：data/*.json 與 assets/images/ 已同步更新。")
    return 0


if __name__ == "__main__":
    sys.exit(build_all())
