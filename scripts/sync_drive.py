#!/usr/bin/env python3
"""
Google 雲端資料夾自動同步引擎（搭配 GitHub Actions 或本機執行）
- --phase pre ：將 cloud_sync.json 或環境變數設定的 Google Drive 公開資料夾下載至暫存區，
                自動分類試題檔 (.docx/.pdf/.md) 與配圖／圖檔 PDF 至 sources/{subject}/。
- --phase post：掃描 data/{subject}.json 中尚無本地圖檔的 Google Drive 配圖連結，
                直接依 File ID 下載（支援圖片與單頁圖檔 PDF 自動轉 PNG），寫入 assets/images/ 與 JSON。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_data  # noqa: E402

try:
    import gdown
except ImportError:
    gdown = None

CONFIG_PATH = ROOT / "cloud_sync.json"
TMP_ROOT = ROOT / ".drive_tmp"
SOURCES_DIR = ROOT / "sources"
DATA_DIR = ROOT / "data"
ASSETS_IMG_DIR = ROOT / "assets" / "images"

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"}
DOC_EXTS = {".docx", ".pdf", ".md", ".json"}
FIGURE_NAME_HINTS = ("圖一", "圖二", "圖三", "圖四", "圖五", "示意圖", "紀錄表", "變遷圖", "架構圖", "花圃", "步道", "生活圈圖", "分工圖", "俯視圖", "面積圖", "進程")


def load_config() -> Dict[str, Any]:
    cfg: Dict[str, Any] = {
        "enabled": False,
        "drive_folder_url": "",
        "subjects": {
            "chinese": {"folder_url": "", "keywords": ["國語", "chinese"]},
            "math": {"folder_url": "", "keywords": ["數學", "math"]},
            "social": {"folder_url": "", "keywords": ["社會", "social"]},
        },
        "fetch_embedded_drive_figures": True,
        "pdf_image_dpi": 180,
    }
    if CONFIG_PATH.exists():
        loaded = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        cfg.update(loaded)
    # 允許透過 GitHub Actions Variables / Secrets 覆蓋總資料夾連結
    env_root = os.environ.get("DRIVE_FOLDER_URL", "").strip()
    if env_root:
        cfg["drive_folder_url"] = env_root
        cfg["enabled"] = True
    for subj in ("chinese", "math", "social"):
        env_subj = os.environ.get(f"DRIVE_FOLDER_{subj.upper()}", "").strip()
        if env_subj:
            cfg["subjects"].setdefault(subj, {})["folder_url"] = env_subj
            cfg["enabled"] = True
    return cfg


def extract_drive_file_id(url: str) -> Optional[str]:
    if not url:
        return None
    for pat in (
        r"/file/d/([a-zA-Z0-9_-]+)",
        r"/d/([a-zA-Z0-9_-]+)",
        r"[?&]id=([a-zA-Z0-9_-]+)",
    ):
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


def classify_subject(rel_path: Path, cfg: Dict[str, Any]) -> Optional[str]:
    joined = "/".join(rel_path.parts).lower()
    for subj, info in cfg.get("subjects", {}).items():
        for kw in info.get("keywords", []):
            if kw.lower() in joined:
                return subj
    return None


def is_figure_file(rel_path: Path) -> bool:
    suf = rel_path.suffix.lower()
    if suf in IMAGE_EXTS:
        return True
    if suf == ".pdf":
        parts_lower = [p.lower() for p in rel_path.parts[:-1]]
        if any(k in parts_lower for k in ("images", "image", "figures", "fig", "配圖", "圖檔", "圖片")):
            return True
        stem = rel_path.stem
        if any(h in stem for h in FIGURE_NAME_HINTS) or re.search(r"第\s*\d+\s*題|圖[一二三四五\d]+|w\d+[_-](?:fig|q)\d+", stem):
            return True
    return False


def download_drive_folder(folder_url: str, target_dir: Path) -> List[Path]:
    if gdown is None:
        print("  ! 未安裝 gdown，略過雲端資料夾下載。")
        return []
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        files = gdown.download_folder(url=folder_url, output=str(target_dir), quiet=False, use_cookies=False, remaining_ok=True)
        return [Path(f) for f in (files or []) if f]
    except Exception as exc:
        print(f"  ! 下載雲端資料夾失敗（請確認資料夾已設為「知道連結的任何人皆可檢視」）：{exc}")
        return []


def run_pre_sync(cfg: Dict[str, Any]) -> None:
    print("============================================================")
    print("  Google 雲端資料夾前置同步 (Pre-Sync)")
    print("============================================================")
    if not cfg.get("enabled"):
        print("  尚未掛載雲端資料夾（cloud_sync.json enabled=false），沿用現有本地 sources/ 檔案。")
        return

    if TMP_ROOT.exists():
        shutil.rmtree(TMP_ROOT)
    TMP_ROOT.mkdir(parents=True, exist_ok=True)

    synced_docs = 0
    synced_figs = 0

    # 1. 各科獨立資料夾（若各自有填 folder_url）
    for subj in ("chinese", "math", "social"):
        url = (cfg.get("subjects", {}).get(subj, {}).get("folder_url") or "").strip()
        if not url:
            continue
        subj_tmp = TMP_ROOT / subj
        download_drive_folder(url, subj_tmp)
        for p in subj_tmp.rglob("*"):
            if not p.is_file() or p.name.startswith((".", "_", "~$")):
                continue
            rel = p.relative_to(subj_tmp)
            if is_figure_file(rel):
                dst = SOURCES_DIR / subj / "images" / p.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
                synced_figs += 1
            elif p.suffix.lower() in DOC_EXTS:
                dst = SOURCES_DIR / subj / p.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
                synced_docs += 1

    # 2. 共用總資料夾（依子資料夾或檔名自動分流國語／數學／社會與圖檔）
    root_url = (cfg.get("drive_folder_url") or "").strip()
    if root_url:
        root_tmp = TMP_ROOT / "shared_root"
        download_drive_folder(root_url, root_tmp)
        for p in root_tmp.rglob("*"):
            if not p.is_file() or p.name.startswith((".", "_", "~$")):
                continue
            rel = p.relative_to(root_tmp)
            subj = classify_subject(rel, cfg)
            if not subj:
                continue
            if is_figure_file(rel):
                dst = SOURCES_DIR / subj / "images" / p.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
                synced_figs += 1
            elif p.suffix.lower() in DOC_EXTS:
                dst = SOURCES_DIR / subj / p.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
                synced_docs += 1

    if TMP_ROOT.exists():
        shutil.rmtree(TMP_ROOT)
    print(f"  前置同步完成：試題檔 {synced_docs} 份、配圖／圖檔 PDF {synced_figs} 份。")


def detect_magic_ext(file_path: Path) -> str:
    head = file_path.read_bytes()[:16]
    if head.startswith(b"%PDF"):
        return ".pdf"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if head.lstrip().startswith((b"<html", b"<!DOCTYPE", b"<!doctype")):
        return ".html"
    return ""


def fetch_drive_figure(file_id: str, subject: str, fig_id: str, dpi: int) -> Optional[str]:
    if gdown is None:
        return None
    TMP_ROOT.mkdir(parents=True, exist_ok=True)
    raw_tmp = TMP_ROOT / f"{subject}_{fig_id}.bin"
    try:
        out = gdown.download(id=file_id, output=str(raw_tmp), quiet=True, use_cookies=False)
        if not out or not raw_tmp.exists() or raw_tmp.stat().st_size == 0:
            return None
        ext = detect_magic_ext(raw_tmp)
        if not ext or ext == ".html":
            # 私人權限阻擋會回傳 Google 登入 HTML，安全略過
            return None

        dst_dir = ASSETS_IMG_DIR / subject
        dst_dir.mkdir(parents=True, exist_ok=True)
        if ext == ".pdf":
            out_png = dst_dir / f"{fig_id}.png"
            if build_data.render_pdf_figure_to_png(raw_tmp, out_png, dpi=dpi):
                return f"assets/images/{subject}/{out_png.name}"
            return None

        out_img = dst_dir / f"{fig_id}{ext}"
        shutil.copy2(raw_tmp, out_img)
        return f"assets/images/{subject}/{out_img.name}"
    except Exception:
        return None
    finally:
        raw_tmp.unlink(missing_ok=True)


def run_post_sync(cfg: Dict[str, Any]) -> None:
    print("============================================================")
    print("  Google 雲端配圖／PDF 補抓 (Post-Sync)")
    print("============================================================")
    if not cfg.get("enabled") or not cfg.get("fetch_embedded_drive_figures", True):
        print("  未啟用雲端同步，略過嵌入式 Google Drive 配圖補抓。")
        return
    if gdown is None:
        print("  ! 未安裝 gdown，略過配圖補抓。")
        return

    dpi = int(cfg.get("pdf_image_dpi", 180))
    fetched_total = 0
    skipped_private = 0

    for subj in ("chinese", "math", "social"):
        json_path = DATA_DIR / f"{subj}.json"
        if not json_path.exists():
            continue
        data = json.loads(json_path.read_text(encoding="utf-8"))
        changed = False
        for wk in data.get("weeks", []):
            targets: List[Dict[str, Any]] = []
            for ctx in wk.get("contexts", []):
                targets.extend(ctx.get("figures", []))
            for q in wk.get("questions", []):
                targets.extend(q.get("figures", []))

            for fig in targets:
                if fig.get("src") or not fig.get("driveUrl"):
                    continue
                fid = extract_drive_file_id(fig["driveUrl"])
                if not fid:
                    continue
                rel_src = fetch_drive_figure(fid, subj, fig["id"], dpi=dpi)
                if rel_src:
                    fig["src"] = rel_src
                    changed = True
                    fetched_total += 1
                else:
                    skipped_private += 1

        if changed:
            json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    if TMP_ROOT.exists():
        shutil.rmtree(TMP_ROOT)
    print(f"  配圖補抓完成：成功轉換內嵌 {fetched_total} 張、仍為私人權限維持原連結 {skipped_private} 張。")


def main() -> int:
    parser = argparse.ArgumentParser(description="Google 雲端資料夾與 PDF 配圖自動同步工具")
    parser.add_argument("--phase", choices=["pre", "post"], required=True)
    args = parser.parse_args()

    cfg = load_config()
    if args.phase == "pre":
        run_pre_sync(cfg)
    else:
        run_post_sync(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
