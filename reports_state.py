"""
StockCenter 報表狀態模型與唯讀掃描模組 (reports_state.py)

本模組提供：
1. reports_state.json 的規格驗證、模型定義與安全原子寫入（損壞時安全失敗）。
2. reports/ 目錄的純函式唯讀掃描器（零副作用，不移動/刪除/寫入任何檔案）。
3. 「從現況建立初始狀態」的預覽產生器（完整標示多 HTML、跨資料夾重複、英文名稱缺漏與 Tombstone 衝突）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# 股票代號正規式：2 至 6 碼英數字
STOCK_CODE_RE = re.compile(r"^[0-9A-Za-z]{2,6}$")
# UTC ISO-8601 時間正規式：結尾必須為大寫 Z，秒數後可選 1 至 6 位微秒小數
UTC_ISO8601_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")

# 檔名解析正規式：支援代號_名稱(市場)
TRACKED_FILENAME_RE = re.compile(r"^([0-9A-Za-z]{2,6})_(.+?)\((TW|TWO|IDX)\)")
# 一般前綴正規式：{code}_*
CODE_PREFIX_RE = re.compile(r"^([0-9A-Za-z]{2,6})_(.+)$")
# 中文字元判定正規式
CHINESE_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")
# 非法資料夾字元
FORBIDDEN_FOLDER_CHARS = set('\\:*?"<>|')


def _get_utc_now_iso() -> str:
    """取得標準 ISO-8601 UTC 時間字串 (微秒 6 位 + Z)。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _is_valid_iso8601(dt_str: Any) -> bool:
    """
    驗證是否為嚴格合法的 UTC ISO-8601 時間字串。
    必須以大寫 'Z' 結尾，不接受時區偏移（例如 +08:00）或無時區標記字串。
    """
    if not isinstance(dt_str, str):
        return False
    if not UTC_ISO8601_RE.match(dt_str):
        return False
    # 正規式通過後以 datetime.fromisoformat 驗證年月日分秒的合法性（如閏年、月份天數）
    cleaned = dt_str[:-1] + "+00:00"
    try:
        datetime.fromisoformat(cleaned)
        return True
    except (ValueError, TypeError):
        return False


def normalize_folder_path(folder: str) -> str:
    """
    正規化相對資料夾路徑為 POSIX 風格。
    根目錄以空字串 "" 表示，去除前後斜線，不允許 ".." 或非法字元。
    """
    if folder is None:
        return ""
    cleaned = str(folder).strip().replace("\\", "/").strip("/")
    if not cleaned or cleaned == ".":
        return ""
    parts = [p.strip() for p in cleaned.split("/") if p.strip()]
    for part in parts:
        if part in (".", ".."):
            raise ValueError(f"資料夾路徑包含無效導航元件: '{part}'")
        if any(ch in part for ch in FORBIDDEN_FOLDER_CHARS):
            raise ValueError(f"資料夾名稱包含非法字元: '{part}'")
    return "/".join(parts)


def create_empty_state() -> Dict[str, Any]:
    """建立一份標準初始空的狀態模型物件。"""
    return {
        "version": 1,
        "updatedAt": _get_utc_now_iso(),
        "stocks": {},
    }


def validate_state(state: Dict[str, Any]) -> None:
    """
    驗證狀態檔資料結構是否完全符合架構約束。
    不合規則時拋出 ValueError。
    """
    if not isinstance(state, dict):
        raise ValueError("狀態資料根節點必須為 JSON 物件 (dict)")

    version = state.get("version")
    if version != 1:
        raise ValueError(f"不支援的狀態版本: {version}，預期為 1")

    updated_at = state.get("updatedAt")
    if not _is_valid_iso8601(updated_at):
        raise ValueError(f"狀態檔 updatedAt 格式不合法，必須為以 'Z' 結尾的 UTC ISO-8601: {updated_at}")

    stocks = state.get("stocks")
    if not isinstance(stocks, dict):
        raise ValueError("狀態檔 'stocks' 節點必須為字典物件")

    for code, item in stocks.items():
        if not isinstance(code, str) or not STOCK_CODE_RE.match(code) or code != code.upper():
            raise ValueError(f"股票代號主鍵不合法（必須為 2-6 碼大寫英數字）: '{code}'")

        if not isinstance(item, dict):
            raise ValueError(f"股票 '{code}' 的記錄必須為字典物件")

        # 必要欄位檢查
        for field in ("name", "folder", "updatedAt", "deletedAt"):
            if field not in item:
                raise ValueError(f"股票 '{code}' 缺少必要欄位: '{field}'")

        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"股票 '{code}' 名稱必須為非空字串")

        folder = item.get("folder")
        if not isinstance(folder, str):
            raise ValueError(f"股票 '{code}' 資料夾必須為字串")
        # 驗證 folder 正規化
        norm_folder = normalize_folder_path(folder)
        if norm_folder != folder:
            raise ValueError(f"股票 '{code}' 資料夾未符合 POSIX 正規格式: '{folder}' -> '{norm_folder}'")

        item_updated = item.get("updatedAt")
        if not _is_valid_iso8601(item_updated):
            raise ValueError(f"股票 '{code}' updatedAt 格式不合法: {item_updated}")

        deleted_at = item.get("deletedAt")
        if deleted_at is not None and not _is_valid_iso8601(deleted_at):
            raise ValueError(f"股票 '{code}' deletedAt 必須為 null 或以 'Z' 結尾的 UTC ISO-8601: {deleted_at}")


def load_state(file_path: Path | str) -> Dict[str, Any]:
    """
    讀取並驗證狀態檔。
    - 檔案不存在時回傳空狀態（不自動寫檔）。
    - JSON 損壞或結構不合法時拋出 ValueError（安全失敗，絕不覆蓋原檔）。
    """
    path = Path(file_path)
    if not path.is_file():
        return create_empty_state()

    try:
        raw_text = path.read_text(encoding="utf-8")
    except Exception as e:
        raise ValueError(f"無法讀取狀態檔 {path}: {e}")

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"狀態檔 JSON 語法損壞 ({path}): {e}")

    validate_state(data)
    return data


def save_state(state: Dict[str, Any], file_path: Path | str) -> None:
    """
    原子寫入狀態檔。
    - 寫入前嚴格驗證 schema，驗證失敗直接中斷，不碰磁碟。
    - 寫入同目錄之暫存檔 (.tmp)，並以 replace 進行原子替換。
    """
    validate_state(state)
    path = Path(file_path)
    if not path.is_absolute():
        path = Path.cwd() / path

    temp_path = path.with_suffix(".tmp")
    json_bytes = (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    try:
        with open(temp_path, "wb") as f:
            f.write(json_bytes)
            f.flush()
            os.fsync(f.fileno())
        temp_path.replace(path)
    except Exception as e:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass
        raise IOError(f"原子寫入狀態檔失敗 ({path}): {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 唯讀檔案掃描器（零副作用）
# ─────────────────────────────────────────────────────────────────────────────

def _has_chinese(text: str) -> bool:
    return bool(CHINESE_CHAR_RE.search(text or ""))


def _extract_stock_info_from_filename(filename: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    從檔名萃取 (code, raw_name, market)。
    """
    # 優先嘗試標準檔名：3324_雙鴻(TWO).html 或 3324_雙鴻(TWO)_chart.png
    base = filename
    for ext in (".html", ".md", ".png"):
        if base.lower().endswith(ext):
            base = base[:-len(ext)]
            break

    m = TRACKED_FILENAME_RE.match(filename)
    if m:
        return m.group(1), m.group(2), m.group(3)

    # 嘗試前綴：2455_全新_4階段技術分析報告
    m2 = CODE_PREFIX_RE.match(base)
    if m2:
        code = m2.group(1)
        rest = m2.group(2)
        # 去除常見尾綴如 _chart, _4階段技術分析報告
        clean_name = re.sub(r"(_chart|_4階段技術分析報告|\(TW\)|\(TWO\)|\(IDX\)).*$", "", rest)
        return code, clean_name if clean_name else None, None

    return None, None, None


def scan_reports_directory(
    reports_dir: Path | str,
    stock_name_dict: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    唯讀掃描 reports/ 目錄下的所有個股檔案，按股票代號歸納完整群組。
    保證不對檔案進行任何寫入、搬移、刪除或修改 mtime（零副作用）。
    
    回傳字典：code -> StockGroupInfo
    """
    rep_path = Path(reports_dir).resolve()
    if not rep_path.is_dir():
        return {}

    name_dict = stock_name_dict or {}
    results: Dict[str, Dict[str, Any]] = {}

    for root, _dirs, files in os.walk(rep_path):
        current_dir = Path(root)
        try:
            rel_folder = current_dir.relative_to(rep_path).as_posix()
        except ValueError:
            rel_folder = ""
        if rel_folder == ".":
            rel_folder = ""

        for filename in files:
            # 排除系統/臨時檔
            if filename.startswith("._") or filename == ".DS_Store" or filename.endswith(".tmp"):
                continue

            lower_name = filename.lower()
            ext = ""
            if lower_name.endswith(".html"):
                ext = "html"
            elif lower_name.endswith(".md"):
                ext = "md"
            elif lower_name.endswith("_chart.png"):
                ext = "png"
            else:
                continue

            code, raw_name, market = _extract_stock_info_from_filename(filename)
            if not code or not (2 <= len(code) <= 6):
                continue

            file_path = current_dir / filename
            try:
                stat = file_path.stat()
                mtime = stat.st_mtime
                size = stat.st_size
            except OSError:
                mtime = 0.0
                size = 0

            rel_file_path = f"{rel_folder}/{filename}" if rel_folder else filename

            file_info = {
                "code": code,
                "name": filename,
                "relPath": rel_file_path,
                "folder": rel_folder,
                "ext": ext,
                "mtime": mtime,
                "size": size,
                "rawName": raw_name,
                "market": market,
            }

            if code not in results:
                results[code] = {
                    "code": code,
                    "folders": set(),
                    "files": [],
                    "htmlFiles": [],
                    "mdFiles": [],
                    "chartFiles": [],
                    "nameCandidates": set(),
                }

            group = results[code]
            group["folders"].add(rel_folder)
            group["files"].append(file_info)
            if ext == "html":
                group["htmlFiles"].append(file_info)
            elif ext == "md":
                group["mdFiles"].append(file_info)
            elif ext == "png":
                group["chartFiles"].append(file_info)

            if raw_name:
                group["nameCandidates"].add(raw_name)

    # 後處理：排序與名稱解析
    final_output: Dict[str, Dict[str, Any]] = {}
    for code, group in sorted(results.items()):
        folders_list = sorted(list(group["folders"]))
        # 依照 mtime 倒序排列檔案
        files_sorted = sorted(group["files"], key=lambda f: f["mtime"], reverse=True)
        html_sorted = sorted(group["htmlFiles"], key=lambda f: f["mtime"], reverse=True)

        # 決定名稱：優先順序 1. 字典中的中文名 2. 候選名稱中有中文者 3. 檔名中的英文候選
        dict_name = name_dict.get(code)
        resolved_name = None
        has_chinese_name = False

        if dict_name and dict_name.strip():
            resolved_name = dict_name.strip()
            has_chinese_name = _has_chinese(resolved_name)
        else:
            chinese_candidates = [n for n in group["nameCandidates"] if _has_chinese(n)]
            if chinese_candidates:
                resolved_name = sorted(chinese_candidates, key=len)[0]
                has_chinese_name = True
            elif group["nameCandidates"]:
                resolved_name = sorted(list(group["nameCandidates"]))[0]
                has_chinese_name = False
            else:
                resolved_name = code
                has_chinese_name = False

        final_output[code] = {
            "code": code,
            "resolvedName": resolved_name,
            "hasChineseName": has_chinese_name,
            "needsChineseName": not has_chinese_name,
            "folders": folders_list,
            "hasMultipleFolders": len(folders_list) > 1,
            "files": files_sorted,
            "htmlFiles": html_sorted,
            "mdFiles": group["mdFiles"],
            "chartFiles": group["chartFiles"],
            "hasMultipleHtmls": len(html_sorted) > 1,
            "nameCandidates": sorted(list(group["nameCandidates"])),
        }

    return final_output


def _is_canonical_html_variant(file_info: Dict[str, Any]) -> bool:
    """判斷 HTML 是否為無額外尾碼的標準報表名稱。"""
    code = file_info.get("code") or ""
    raw_name = file_info.get("rawName") or ""
    market = file_info.get("market") or ""
    if not code or not raw_name or market not in ("TW", "TWO", "IDX"):
        return False
    return file_info.get("name") == f"{code}_{raw_name}({market}).html"


def generate_duplicate_cleanup_preview(
    reports_dir: Path | str,
    stock_name_dict: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """產生同資料夾重複 HTML 的唯讀隔離預覽，不修改任何檔案。"""
    scan_results = scan_reports_directory(reports_dir, stock_name_dict)
    candidates: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for code, info in scan_results.items():
        html_files = info.get("htmlFiles", [])
        if len(html_files) < 2:
            continue
        if info.get("hasMultipleFolders"):
            skipped.append({
                "code": code,
                "reason": "同代號位於多個資料夾，不能自動選擇隔離版本",
                "files": [f["relPath"] for f in html_files],
            })
            continue

        resolved_name = info.get("resolvedName", "")

        def rank(file_info: Dict[str, Any]) -> Tuple[int, int, int, float, str]:
            raw_name = file_info.get("rawName") or ""
            return (
                1 if _is_canonical_html_variant(file_info) else 0,
                1 if raw_name == resolved_name else 0,
                1 if _has_chinese(raw_name) else 0,
                float(file_info.get("mtime") or 0),
                str(file_info.get("name") or ""),
            )

        primary = max(html_files, key=rank)
        archive_htmls = [f for f in html_files if f["relPath"] != primary["relPath"]]
        archive_files = [f["relPath"] for f in archive_htmls]

        # 只有「標準 HTML」的同名 MD/圖表屬於該變體。處置期間 HTML 沒有
        # 專屬附檔，絕不能連帶搬走標準報表的 MD。
        for html_file in archive_htmls:
            if not _is_canonical_html_variant(html_file):
                continue
            raw_name = html_file.get("rawName") or ""
            prefix = f"{code}_{raw_name}_"
            for attached in info.get("mdFiles", []) + info.get("chartFiles", []):
                if attached.get("folder") == html_file.get("folder") and attached.get("name", "").startswith(prefix):
                    archive_files.append(attached["relPath"])

        archive_files = sorted(set(archive_files))
        candidates.append({
            "code": code,
            "folder": primary.get("folder", ""),
            "name": resolved_name,
            "keepFile": primary["relPath"],
            "keepReason": "保留標準命名、名稱優先符合中文名稱字典的 HTML 報表",
            "archiveFiles": archive_files,
        })

    candidates.sort(key=lambda item: item["code"])
    return {
        "candidates": candidates,
        "skipped": skipped,
        "summary": {
            "groups": len(candidates),
            "filesToArchive": sum(len(item["archiveFiles"]) for item in candidates),
            "skippedGroups": len(skipped),
        },
        "isReadOnlyPreview": True,
    }


def archive_duplicate_variants_transactional(
    reports_dir: Path | str,
    candidates: List[Dict[str, Any]],
    quarantine_root: Path | str,
) -> Dict[str, Any]:
    """將預覽確認的重複變體移至 reports/ 外的可復原隔離區。"""
    reports_p = Path(reports_dir).resolve()
    quarantine_p = Path(quarantine_root).resolve()
    if not reports_p.is_dir() or quarantine_p.is_relative_to(reports_p):
        raise ValueError("報表目錄或隔離區路徑不安全")

    sources: List[Tuple[Path, Path]] = []
    seen_sources: Set[Path] = set()
    session_dir = quarantine_p / f"duplicate_cleanup_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    for candidate in candidates:
        for rel_file in candidate.get("archiveFiles", []):
            if not isinstance(rel_file, str) or not rel_file.lower().endswith((".html", ".md", ".png")):
                raise ValueError("隔離計畫含有不合法檔案")
            source = (reports_p / rel_file).resolve()
            if (not source.is_relative_to(reports_p) or not source.is_file() or source.is_symlink() or source in seen_sources):
                raise ValueError(f"隔離來源檔案不存在、重複或不安全: {rel_file}")
            seen_sources.add(source)
            destination = session_dir / source.relative_to(reports_p)
            if destination.exists():
                raise FileExistsError(f"隔離目標已存在: {destination}")
            sources.append((source, destination))

    if not sources:
        return {"ok": True, "archivedCount": 0, "session": None, "archivedFiles": []}

    moved: List[Tuple[Path, Path]] = []
    try:
        for source, destination in sources:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            moved.append((source, destination))
    except Exception as error:
        rollback_errors = []
        for source, destination in reversed(moved):
            try:
                if destination.exists():
                    source.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(destination), str(source))
            except Exception as rollback_error:
                rollback_errors.append(str(rollback_error))
        if session_dir.exists():
            shutil.rmtree(str(session_dir), ignore_errors=True)
        if rollback_errors:
            raise RuntimeError(f"隔離失敗且回滾不完整: {rollback_errors}") from error
        raise

    return {
        "ok": True,
        "archivedCount": len(moved),
        "session": session_dir,
        "archivedFiles": [str(source.relative_to(reports_p)) for source, _ in moved],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 初始狀態預覽產生器（唯讀，不寫檔）
# ─────────────────────────────────────────────────────────────────────────────

def generate_initial_state_preview(
    reports_dir: Path | str,
    existing_state: Optional[Dict[str, Any]] = None,
    stock_name_dict: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    依現況掃描結果產生「從現況建立初始狀態」的唯讀預覽資料。
    絕對不修改 reports/ 或寫入任何檔案。
    
    回傳預覽結構，包含：
    - proposed_state: 乾淨無衝突時可直接納入的狀態模型
    - conflicts: 必須由使用者裁決或標記的衝突清單
    - warnings: 警告清單（如名稱待補、同資料夾多 HTML 等）
    - summary: 統計數字
    """
    scan_results = scan_reports_directory(reports_dir, stock_name_dict)
    state = existing_state or create_empty_state()
    existing_stocks = state.get("stocks", {})

    proposed_stocks: Dict[str, Any] = {}
    conflicts: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []

    now_iso = _get_utc_now_iso()

    for code, info in scan_results.items():
        existing_rec = existing_stocks.get(code)
        folders = info["folders"]
        resolved_name = info["resolvedName"]

        # 1. 檢查 Tombstone 衝突（狀態檔標記已刪除，但磁碟仍有舊檔）
        if existing_rec and existing_rec.get("deletedAt") is not None:
            conflicts.append({
                "code": code,
                "type": "tombstone_conflict",
                "message": f"股票 '{code}' 在狀態檔中已標記為刪除 (deletedAt: {existing_rec['deletedAt']})，但 reports/ 內仍有實體檔案",
                "existingRecord": existing_rec,
                "filesFound": [f["relPath"] for f in info["files"]],
                "foldersFound": folders,
            })
            # 依 AI_A 規範：原樣保留該筆 tombstone 在 proposedState.stocks，絕不可因舊檔遺失刪除記錄
            proposed_stocks[code] = dict(existing_rec)
            continue

        # 2. 檢查跨資料夾重複衝突
        if info["hasMultipleFolders"]:
            # 提供建議：依最新 HTML 或最新檔案所在資料夾
            suggested_folder = folders[0]
            if info["htmlFiles"]:
                suggested_folder = info["htmlFiles"][0]["folder"]
            conflicts.append({
                "code": code,
                "type": "location_conflict",
                "message": f"股票 '{code}' 出現在多個資料夾: {folders}",
                "suggestedFolder": suggested_folder,
                "folders": folders,
                "files": [f["relPath"] for f in info["files"]],
            })
            # 發生位置衝突時不默默塞入 proposed_stocks，等待決策
            continue

        # 3. 檢查同代號多 HTML 變體警告
        if info["hasMultipleHtmls"]:
            warnings.append({
                "code": code,
                "type": "multiple_html_variants",
                "message": f"股票 '{code}' 在資料夾 '{folders[0]}' 內存在多份 HTML 變體",
                "htmlFiles": [f["name"] for f in info["htmlFiles"]],
            })

        # 4. 檢查缺中文名警告
        if info["needsChineseName"]:
            warnings.append({
                "code": code,
                "type": "missing_chinese_name",
                "message": f"股票 '{code}' 缺少中文名稱，目前以 '{resolved_name}' 作為暫定名稱",
                "currentName": resolved_name,
            })

        # 正常單一位置標的：納入提案
        target_folder = folders[0] if folders else ""
        # 若現有狀態已存在且未刪除，保留其原 folder/name 若一致
        item_name = resolved_name
        if existing_rec and existing_rec.get("deletedAt") is None:
            if existing_rec.get("name"):
                item_name = existing_rec["name"]
            if existing_rec.get("folder") == target_folder:
                target_folder = existing_rec["folder"]

        proposed_stocks[code] = {
            "name": item_name,
            "folder": target_folder,
            "updatedAt": existing_rec.get("updatedAt", now_iso) if existing_rec else now_iso,
            "deletedAt": None,
        }

    # 同步檢查：狀態檔有記錄，但 reports/ 完全沒有任何檔案
    for code, rec in existing_stocks.items():
        if code not in scan_results:
            if rec.get("deletedAt") is None:
                warnings.append({
                    "code": code,
                    "type": "missing_on_disk",
                    "message": f"股票 '{code}' 存在於狀態檔 (folder: '{rec.get('folder')}')，但實體檔案不存在",
                    "record": rec,
                })
            else:
                # 正常已刪除且磁碟無檔案，保留在狀態
                proposed_stocks[code] = dict(rec)

    proposed_state = {
        "version": 1,
        "updatedAt": now_iso,
        "stocks": dict(sorted(proposed_stocks.items())),
    }

    return {
        "proposedState": proposed_state,
        "conflicts": conflicts,
        "warnings": warnings,
        "summary": {
            "totalScanned": len(scan_results),
            "proposedCount": len(proposed_stocks),
            "conflictCount": len(conflicts),
            "warningCount": len(warnings),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 狀態變更交易輔助函式 (階段 2)
# ─────────────────────────────────────────────────────────────────────────────

def record_stock_move(
    state: Dict[str, Any],
    code: str,
    folder: str,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    更新狀態檔中個股的資料夾位置，並將 deletedAt 清為 null。
    """
    now_iso = _get_utc_now_iso()
    stocks = state.setdefault("stocks", {})
    norm_folder = normalize_folder_path(folder)

    existing = stocks.get(code, {})
    stock_name = (name or "").strip() or existing.get("name") or code

    stocks[code] = {
        "name": stock_name,
        "folder": norm_folder,
        "updatedAt": now_iso,
        "deletedAt": None,
    }
    state["updatedAt"] = now_iso
    return state


def record_stock_deletion(
    state: Dict[str, Any],
    code: str,
    name: Optional[str] = None,
    folder: Optional[str] = None,
) -> Dict[str, Any]:
    """
    在狀態檔中標記個股刪除 tombstone (保留 deletedAt 時間戳)，防止跨機舊檔復活。
    - 優先使用明確傳入的來源資料夾 (folder)；未傳入時才使用既有記錄的 folder。
    - 優先使用傳入的 name，未傳入時使用既有記錄的 name 或 code。
    """
    now_iso = _get_utc_now_iso()
    stocks = state.setdefault("stocks", {})

    existing = stocks.get(code, {})
    stock_name = (name or "").strip() or existing.get("name") or code

    if folder is not None:
        stock_folder = normalize_folder_path(folder)
    else:
        stock_folder = existing.get("folder", "")
        if stock_folder:
            stock_folder = normalize_folder_path(stock_folder)

    stocks[code] = {
        "name": stock_name,
        "folder": stock_folder,
        "updatedAt": now_iso,
        "deletedAt": now_iso,
    }
    state["updatedAt"] = now_iso
    return state


def record_folder_rename(
    state: Dict[str, Any],
    old_folder: str,
    new_folder: str,
) -> int:
    """
    當資料夾改名時，同步更新狀態檔中所有落在該資料夾（含子目錄）之未刪除個股的 folder。
    回傳受影響的個股筆數。
    """
    norm_old = normalize_folder_path(old_folder)
    norm_new = normalize_folder_path(new_folder)
    if not norm_old:
        raise ValueError("不可重新命名根目錄")

    now_iso = _get_utc_now_iso()
    stocks = state.get("stocks", {})
    updated_count = 0

    for code, stock in stocks.items():
        if stock.get("deletedAt") is not None:
            continue
        f = stock.get("folder", "")
        if f == norm_old:
            stock["folder"] = norm_new
            stock["updatedAt"] = now_iso
            updated_count += 1
        elif f.startswith(norm_old + "/"):
            sub = f[len(norm_old):]
            stock["folder"] = norm_new + sub
            stock["updatedAt"] = now_iso
            updated_count += 1

    if updated_count > 0:
        state["updatedAt"] = now_iso
    return updated_count


# ─────────────────────────────────────────────────────────────────────────────
# 階段 3C：基準計算與排他首次建立輔助函式
# ─────────────────────────────────────────────────────────────────────────────

def calculate_preview_basis(
    reports_dir: Path | str,
    state_file_path: Path | str,
    stock_name_dict: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    計算初始化預覽的嚴格基準指紋（純唯讀，零副作用）。
    涵蓋：
    1. reports/ 內受追蹤檔案（CODE_* 且 .html/.md/_chart.png）之相對路徑與二進位內容 SHA-256（Canonical 排序）。
    2. reports_state.json 狀態（absent / non_regular_file / sha256:<hash>）。
    3. 參與掃描的名稱字典快照 SHA-256。
    """
    rep_path = Path(reports_dir).resolve()
    state_path = Path(state_file_path)

    # 1. 計算 reports 檔案內容雜湊
    file_records: List[Tuple[str, str]] = []
    if rep_path.is_dir():
        for root, _dirs, files in os.walk(rep_path):
            current_dir = Path(root)
            try:
                rel_folder = current_dir.relative_to(rep_path).as_posix()
            except ValueError:
                rel_folder = ""
            if rel_folder == ".":
                rel_folder = ""

            for filename in files:
                if filename.startswith("._") or filename == ".DS_Store" or filename.endswith(".tmp"):
                    continue
                lower_name = filename.lower()
                if not (lower_name.endswith(".html") or lower_name.endswith(".md") or lower_name.endswith("_chart.png")):
                    continue
                # 確認符合受追蹤個股檔名規則
                code, _, _ = _extract_stock_info_from_filename(filename)
                if not code or not (2 <= len(code) <= 6):
                    continue

                full_file = current_dir / filename
                rel_file = f"{rel_folder}/{filename}" if rel_folder else filename
                try:
                    content_bytes = full_file.read_bytes()
                    f_hash = hashlib.sha256(content_bytes).hexdigest()
                    file_records.append((rel_file, f_hash))
                except OSError:
                    continue

    # Canonical 排序：依 POSIX 相對路徑 UTF-8 遞增排序
    file_records.sort(key=lambda x: x[0].encode("utf-8"))
    if file_records:
        combined_text = "".join(f"{r[0]}:{r[1]}\n" for r in file_records)
        reports_hash = f"sha256:{hashlib.sha256(combined_text.encode('utf-8')).hexdigest()}"
    else:
        reports_hash = f"sha256:{hashlib.sha256(b'').hexdigest()}"

    # 2. 計算狀態檔路徑狀態
    if state_path.is_symlink():
        state_file_status = "non_regular_file"
    elif not state_path.exists():
        state_file_status = "absent"
    elif not state_path.is_file():
        state_file_status = "non_regular_file"
    else:
        try:
            state_bytes = state_path.read_bytes()
            state_file_status = f"sha256:{hashlib.sha256(state_bytes).hexdigest()}"
        except OSError:
            state_file_status = "non_regular_file"

    # 3. 計算名稱字典快照雜湊
    name_dict = stock_name_dict or {}
    clean_dict = {str(k).upper(): str(v) for k, v in name_dict.items() if k and v}
    sorted_dict = dict(sorted(clean_dict.items()))
    dict_bytes = json.dumps(sorted_dict, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    name_dict_hash = f"sha256:{hashlib.sha256(dict_bytes).hexdigest()}"

    # 4. 綜合成指紋
    version = 1
    raw_fingerprint = f"{version}|{reports_hash}|{state_file_status}|{name_dict_hash}".encode("utf-8")
    fingerprint = f"sha256:{hashlib.sha256(raw_fingerprint).hexdigest()}"

    return {
        "version": version,
        "fingerprint": fingerprint,
        "reportsHash": reports_hash,
        "stateFileStatus": state_file_status,
        "nameDictHash": name_dict_hash,
    }


def create_initial_state_exclusive(state: Dict[str, Any], file_path: Path | str) -> None:
    """
    首次建立狀態檔的原子、排他、僅建立操作。
    絕對不覆寫既有檔案。
    流程：
    1. 驗證 state 合法性。
    2. 檢查目標路徑是否為符號連結或已存在（若存在直接拋出 FileExistsError 或 ValueError）。
    3. 在同一目錄下建立私有暫存檔並 fsync。
    4. 使用 os.link 進行原子排他發布；若目標已存在則捕捉 FileExistsError，刪除暫存檔並拋出。
    """
    validate_state(state)
    raw_path = Path(file_path)
    if raw_path.is_symlink():
        raise ValueError("狀態檔路徑為符號連結，拒絕寫入")
    path = raw_path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("狀態檔路徑為符號連結，拒絕寫入")
    if path.exists():
        if not path.is_file():
            raise ValueError("狀態檔路徑不是一般檔案")
        raise FileExistsError(f"狀態檔已存在: {path}")

    # 私有暫存檔：mkstemp 以排他方式建立，避免名稱碰撞或覆寫其他暫存檔。
    json_bytes = (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    fd, temp_name = tempfile.mkstemp(prefix=".tmp_init_", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)

    try:
        with os.fdopen(fd, "wb") as f:
            f.write(json_bytes)
            f.flush()
            os.fsync(f.fileno())

        # 排他發布：os.link 保證只有目的地不存在時才會成功
        try:
            os.link(temp_path, path)
        except FileExistsError:
            raise FileExistsError(f"狀態檔競態已存在: {path}")
        except OSError as error:
            # 不支援 hard link 時不能退回一般建立／覆寫；那會失去「完整內容後
            # 才原子發布」的保證。安全失敗並讓 API 回傳 500。
            raise OSError(f"檔案系統不支援安全的排他發布: {error}") from error
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def get_report_output_destination(
    code: str,
    reports_dir: Path | str,
    state_file_path: Path | str,
) -> Dict[str, Any]:
    """
    依據狀態檔判定報表產生器的輸出目標目錄與安全狀態。
    狀態回傳：
    - 'no_state_file': 狀態檔不存在（回退既有路徑判定行為）
    - 'invalid_state_file': 狀態檔損壞、格式不合法、為資料夾或符號連結（安全失敗）
    - 'tombstone': 個股在狀態檔中已被標記刪除（安全拒絕輸出以防舊檔復活）
    - 'active': 個股在狀態檔中為有效未刪除項目（強制輸出至 reports/<folder>）
    - 'unmanaged': 存在合法狀態檔但該個股尚未受管理（固定輸出至 reports/ 根目錄）
    """
    clean_code = str(code).strip().upper()
    if not STOCK_CODE_RE.match(clean_code):
        return {
            "status": "invalid_state_file",
            "error": f"股票代號格式不合法: '{code}'",
            "target_dir": None,
        }

    state_path = Path(state_file_path)
    reports_p = Path(reports_dir)

    if state_path.is_symlink():
        return {
            "status": "invalid_state_file",
            "error": "狀態檔路徑不可為符號連結 (symlink)",
            "target_dir": None,
        }

    if state_path.is_dir():
        return {
            "status": "invalid_state_file",
            "error": "狀態檔路徑不可為資料夾",
            "target_dir": None,
        }

    if not state_path.exists():
        return {
            "status": "no_state_file",
            "target_dir": None,
        }

    # 讀取並嚴格驗證狀態檔
    try:
        raw_text = state_path.read_text(encoding="utf-8")
        data = json.loads(raw_text)
        validate_state(data)
    except Exception as e:
        return {
            "status": "invalid_state_file",
            "error": f"狀態檔損壞或結構不合法 ({state_path}): {e}",
            "target_dir": None,
        }

    stocks = data.get("stocks", {})
    if clean_code in stocks:
        rec = stocks[clean_code]
        if rec.get("deletedAt") is not None:
            return {
                "status": "tombstone",
                "deletedAt": rec["deletedAt"],
                "stock": rec,
                "target_dir": None,
            }
        folder = rec.get("folder", "")
        target_dir = reports_p / folder if folder else reports_p
        return {
            "status": "active",
            "folder": folder,
            "target_dir": target_dir,
            "stock": rec,
        }

    return {
        "status": "unmanaged",
        "folder": "",
        "target_dir": reports_p,
    }


def update_stock_name_dict_entry(
    dict_path: Path | str,
    code: str,
    chinese_name: str,
) -> Dict[str, str]:
    """
    安全原子更新 stock_name_dict.json 中指定代號的中文名稱。
    - 嚴格驗證代號格式與非空中文字元。
    - 拒絕符號連結與資料夾。
    - 原子寫入並 fsync，失敗時完整回滾，絕不破壞原字典與狀態檔。
    - 絕不建立或改動 reports_state.json。
    """
    clean_code = str(code).strip().upper()
    if not STOCK_CODE_RE.match(clean_code):
        raise ValueError(f"股票代號格式不合法: '{code}'，必須為 2-6 碼英數字")

    clean_name = str(chinese_name).strip()
    if not clean_name:
        raise ValueError("中文名稱不能為空")
    if not _has_chinese(clean_name):
        raise ValueError(f"中文名稱必須包含至少一個中文字元: '{clean_name}'")
    if any(ch in FORBIDDEN_FOLDER_CHARS for ch in clean_name) or any(ord(c) < 32 for c in clean_name):
        raise ValueError(f"中文名稱包含非法字元: '{clean_name}'")

    # 不可先 resolve()，否則 symbolic link 會被展開成一般目標檔而繞過防護。
    path = Path(dict_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    if path.is_symlink():
        raise ValueError(f"字典檔案不可為符號連結 (symlink): {path}")
    if path.is_dir():
        raise ValueError(f"字典路徑不可為資料夾: {path}")

    data: Dict[str, str] = {}
    if path.exists():
        try:
            raw_text = path.read_text(encoding="utf-8")
            loaded = json.loads(raw_text)
            if not isinstance(loaded, dict):
                raise ValueError("字典檔案頂層結構必須為 JSON 物件 (dict)")
            data = {str(k).strip().upper(): str(v).strip() for k, v in loaded.items()}
        except Exception as e:
            raise ValueError(f"字典檔案損壞或格式不合法: {e}")

    data[clean_code] = clean_name

    # 原子寫入同目錄暫存檔
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".tmp_dict_", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    json_bytes = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    try:
        with os.fdopen(fd, "wb") as f:
            f.write(json_bytes)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass

    return data


def merge_reports_states(
    local_state: Dict[str, Any],
    incoming_state: Dict[str, Any],
) -> Dict[str, Any]:
    """
    純函式跨電腦狀態合併（階段 4 授權 008 定案規則）。
    1. 兩份輸入均先嚴格驗證（validate_state），任一損壞或不合法直接拋出 ValueError。
    2. 代號為唯一鍵。單方存在時保留原紀錄。
    3. 雙方 active 且內容相同：保留較新 updatedAt。
    4. 雙方 active 但 folder 或 name 不同：
       - updatedAt 較新者勝出。
       - updatedAt 相等時列入 concurrent_move_conflict，不得靜默選擇。
    5. 一方 active、一方 tombstone：
       - tombstone 的 deletedAt >= active 的 updatedAt：tombstone 勝出（防舊檔復活）。
       - active 的 updatedAt > tombstone 的 deletedAt：列入 delete_active_conflict，不得自動復活或自動刪除。
    6. 雙方 tombstone：
       - deletedAt 較新者勝出。
       - deletedAt 相等但 folder 或 name 不同時列入 concurrent_delete_conflict。
    """
    validate_state(local_state)
    validate_state(incoming_state)

    local_stocks = local_state.get("stocks", {})
    incoming_stocks = incoming_state.get("stocks", {})

    all_codes = sorted(set(local_stocks.keys()) | set(incoming_stocks.keys()))

    merged_stocks: Dict[str, Any] = {}
    item_decisions: Dict[str, Any] = {}
    conflicts: List[Dict[str, Any]] = []

    for code in all_codes:
        has_local = code in local_stocks
        has_incoming = code in incoming_stocks

        if has_local and not has_incoming:
            loc = local_stocks[code]
            merged_stocks[code] = dict(loc)
            item_decisions[code] = {
                "decision": "local_only",
                "source": "local",
                "reason": "僅存在於本機狀態檔",
                "record": dict(loc),
            }
            continue

        if has_incoming and not has_local:
            inc = incoming_stocks[code]
            merged_stocks[code] = dict(inc)
            item_decisions[code] = {
                "decision": "incoming_only",
                "source": "incoming",
                "reason": "僅存在於外來狀態檔",
                "record": dict(inc),
            }
            continue

        # 雙方皆存在
        loc = local_stocks[code]
        inc = incoming_stocks[code]
        loc_is_del = loc.get("deletedAt") is not None
        inc_is_del = inc.get("deletedAt") is not None

        # 情況 A：雙方皆為 Active
        if not loc_is_del and not inc_is_del:
            loc_folder = loc.get("folder", "")
            inc_folder = inc.get("folder", "")
            loc_name = loc.get("name", "")
            inc_name = inc.get("name", "")

            if loc_folder == inc_folder and loc_name == inc_name:
                newer_rec = dict(loc if loc.get("updatedAt", "") >= inc.get("updatedAt", "") else inc)
                merged_stocks[code] = newer_rec
                item_decisions[code] = {
                    "decision": "identical",
                    "source": "both",
                    "reason": "兩端內容完全一致，採用較新時間戳",
                    "record": newer_rec,
                }
            else:
                loc_time = loc.get("updatedAt", "")
                inc_time = inc.get("updatedAt", "")
                if loc_time > inc_time:
                    merged_stocks[code] = dict(loc)
                    item_decisions[code] = {
                        "decision": "local_active_newer",
                        "source": "local",
                        "reason": f"本機 active 更新時間 ({loc_time}) 較新於外來 ({inc_time})",
                        "record": dict(loc),
                    }
                elif inc_time > loc_time:
                    merged_stocks[code] = dict(inc)
                    item_decisions[code] = {
                        "decision": "incoming_active_newer",
                        "source": "incoming",
                        "reason": f"外來 active 更新時間 ({inc_time}) 較新於本機 ({loc_time})",
                        "record": dict(inc),
                    }
                else:
                    conflict_item = {
                        "code": code,
                        "type": "concurrent_move_conflict",
                        "message": f"代號 '{code}' 兩端於相同時間 ({loc_time}) 進行了不同修改：本機='{loc_folder}/{loc_name}' vs 外來='{inc_folder}/{inc_name}'",
                        "localRecord": dict(loc),
                        "incomingRecord": dict(inc),
                    }
                    conflicts.append(conflict_item)
                    item_decisions[code] = {
                        "decision": "conflict",
                        "source": "conflict",
                        "reason": conflict_item["message"],
                        "conflict": conflict_item,
                    }

        # 情況 B：一方 Active，一方 Tombstone
        elif loc_is_del != inc_is_del:
            active_rec = loc if not loc_is_del else inc
            tomb_rec = loc if loc_is_del else inc
            active_source = "local" if not loc_is_del else "incoming"
            tomb_source = "local" if loc_is_del else "incoming"

            active_time = active_rec.get("updatedAt", "")
            tomb_time = tomb_rec.get("deletedAt", "")

            if tomb_time >= active_time:
                merged_stocks[code] = dict(tomb_rec)
                item_decisions[code] = {
                    "decision": f"{tomb_source}_tombstone_supersedes",
                    "source": tomb_source,
                    "reason": f"{tomb_source} tombstone 刪除時間 ({tomb_time}) 晚於或等於 {active_source} active 更新時間 ({active_time})，以防舊檔復活",
                    "record": dict(tomb_rec),
                }
            else:
                conflict_item = {
                    "code": code,
                    "type": "delete_active_conflict",
                    "message": f"代號 '{code}' 發生刪除與後續修改衝突：{active_source} active 更新時間 ({active_time}) 晚於 {tomb_source} tombstone 刪除時間 ({tomb_time})",
                    "activeRecord": dict(active_rec),
                    "tombstoneRecord": dict(tomb_rec),
                    "activeSource": active_source,
                    "tombstoneSource": tomb_source,
                    "localRecord": dict(loc),
                    "incomingRecord": dict(inc),
                }
                conflicts.append(conflict_item)
                item_decisions[code] = {
                    "decision": "conflict",
                    "source": "conflict",
                    "reason": conflict_item["message"],
                    "conflict": conflict_item,
                }

        # 情況 C：雙方皆為 Tombstone
        else:
            loc_del_time = loc.get("deletedAt", "")
            inc_del_time = inc.get("deletedAt", "")

            if loc_del_time > inc_del_time:
                merged_stocks[code] = dict(loc)
                item_decisions[code] = {
                    "decision": "local_tombstone_newer",
                    "source": "local",
                    "reason": f"本機 tombstone 刪除時間 ({loc_del_time}) 較新於外來 ({inc_del_time})",
                    "record": dict(loc),
                }
            elif inc_del_time > loc_del_time:
                merged_stocks[code] = dict(inc)
                item_decisions[code] = {
                    "decision": "incoming_tombstone_newer",
                    "source": "incoming",
                    "reason": f"外來 tombstone 刪除時間 ({inc_del_time}) 較新於本機 ({loc_del_time})",
                    "record": dict(inc),
                }
            else:
                if loc.get("folder") == inc.get("folder") and loc.get("name") == inc.get("name"):
                    merged_stocks[code] = dict(loc)
                    item_decisions[code] = {
                        "decision": "identical_tombstone",
                        "source": "both",
                        "reason": "兩端 tombstone 完全一致",
                        "record": dict(loc),
                    }
                else:
                    conflict_item = {
                        "code": code,
                        "type": "concurrent_delete_conflict",
                        "message": f"代號 '{code}' 兩端 tombstone 刪除時間相同 ({loc_del_time}) 但屬性不同",
                        "localRecord": dict(loc),
                        "incomingRecord": dict(inc),
                    }
                    conflicts.append(conflict_item)
                    item_decisions[code] = {
                        "decision": "conflict",
                        "source": "conflict",
                        "reason": conflict_item["message"],
                        "conflict": conflict_item,
                    }

    latest_time = max(
        local_state.get("updatedAt", _get_utc_now_iso()),
        incoming_state.get("updatedAt", _get_utc_now_iso())
    )

    merged_state = {
        "version": 1,
        "updatedAt": latest_time,
        "stocks": merged_stocks,
    }

    return {
        "mergedState": merged_state,
        "itemDecisions": item_decisions,
        "conflicts": conflicts,
    }


def generate_sync_preview(
    local_state: Dict[str, Any],
    incoming_state: Dict[str, Any],
    reports_dir: Path | str,
    stock_name_dict: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    純唯讀同步整理預覽（階段 4 授權 008 規範）。
    - 執行 merge_reports_states 取得合併狀態與決策。
    - 唯讀掃描 reports_dir。
    - 比對產生 stateChanges、fileActions、conflicts、warnings。
    - 絕不進行磁碟寫入、搬移、刪除或修改（純唯讀）。
    """
    merge_result = merge_reports_states(local_state, incoming_state)

    merged_state = merge_result["mergedState"]
    merged_stocks = merged_state.get("stocks", {})
    item_decisions = merge_result["itemDecisions"]
    conflicts = list(merge_result["conflicts"])
    warnings: List[Dict[str, Any]] = []

    scan_results = scan_reports_directory(reports_dir, stock_name_dict)

    file_actions: List[Dict[str, Any]] = []
    state_changes: List[Dict[str, Any]] = []

    local_stocks = local_state.get("stocks", {})

    # 1. 整理狀態變更 (stateChanges)
    for code, decision in item_decisions.items():
        if decision.get("decision") == "conflict":
            continue
        loc = local_stocks.get(code)
        rec = decision.get("record")
        if not loc:
            state_changes.append({
                "code": code,
                "type": "add",
                "description": f"新增狀態: {rec.get('name')} (資料夾: '{rec.get('folder', '')}')",
                "record": rec,
                "source": decision.get("source"),
            })
        elif loc != rec:
            state_changes.append({
                "code": code,
                "type": "modify",
                "description": f"更新狀態: '{loc.get('folder', '')}' -> '{rec.get('folder', '')}'",
                "oldRecord": loc,
                "newRecord": rec,
                "source": decision.get("source"),
            })

    # 2. 檔案庫整理建議 (fileActions) 與檔案衝突
    all_scanned_codes = set(scan_results.keys())
    all_known_codes = set(merged_stocks.keys())

    for code in sorted(all_scanned_codes | all_known_codes):
        info = scan_results.get(code)
        rec = merged_stocks.get(code)

        # 狀態合併本身有衝突時，任何檔案建議都可能暗示可自動處理；只能保留
        # 衝突資訊，等待階段 5 的人工決策。
        if item_decisions.get(code, {}).get("decision") == "conflict":
            continue

        # 檢查磁碟多資料夾重複衝突
        if info and info.get("hasMultipleFolders"):
            conflicts.append({
                "code": code,
                "type": "location_conflict",
                "message": f"股票 '{code}' 在本機磁碟出現在多個資料夾: {info['folders']}",
                "folders": info["folders"],
                "files": [f["relPath"] for f in info["files"]],
            })
            # 同代號跨資料夾的實體檔案不能同時標記為可搬移，避免 UI 產生
            # 「衝突但似乎可直接套用」的矛盾訊息。
            continue

        # 情況 1: 磁碟上有檔案，但在 mergedState 中完全不存在 -> unmanaged_candidate
        if info and not rec:
            file_actions.append({
                "code": code,
                "action": "unmanaged_candidate",
                "name": info.get("resolvedName", code),
                "currentFolders": info.get("folders", []),
                "files": [f["relPath"] for f in info.get("files", [])],
                "message": f"代號 '{code}' 在合併狀態中未受管理，保留於現況",
            })
            continue

        # 情況 2: 合併狀態有 active 紀錄
        if rec and rec.get("deletedAt") is None:
            target_folder = rec.get("folder", "")
            if not info:
                file_actions.append({
                    "code": code,
                    "action": "missing_on_disk",
                    "name": rec.get("name", code),
                    "targetFolder": target_folder,
                    "message": f"代號 '{code}' 在狀態檔中為有效，但本機磁碟無任何報表檔案",
                })
            else:
                current_folders = info.get("folders", [])
                if current_folders != [target_folder]:
                    file_actions.append({
                        "code": code,
                        "action": "move_candidate",
                        "name": rec.get("name", code),
                        "currentFolders": current_folders,
                        "targetFolder": target_folder,
                        "files": [f["relPath"] for f in info.get("files", [])],
                        "message": f"建議搬移: {current_folders} -> '{target_folder}'",
                    })

        # 情況 3: 合併狀態為 tombstone
        if rec and rec.get("deletedAt") is not None:
            if info:
                file_actions.append({
                    "code": code,
                    "action": "delete_candidate",
                    "name": rec.get("name", code),
                    "currentFolders": info.get("folders", []),
                    "files": [f["relPath"] for f in info.get("files", [])],
                    "deletedAt": rec.get("deletedAt"),
                    "message": f"代號 '{code}' 已在狀態檔標記刪除，但本機仍有舊檔，建議清除",
                })
                warnings.append({
                    "code": code,
                    "type": "tombstone_residual_files",
                    "message": f"代號 '{code}' 已於 {rec.get('deletedAt')} 標記刪除，本機磁碟仍有實體檔案",
                    "files": [f["relPath"] for f in info.get("files", [])],
                })

    state_summary = {
        "keptLocal": sum(1 for d in item_decisions.values() if d.get("source") == "local"),
        "addedFromIncoming": sum(1 for d in item_decisions.values() if d.get("decision") == "incoming_only"),
        "updatedFromIncoming": sum(1 for d in item_decisions.values() if d.get("source") == "incoming" and d.get("decision") != "incoming_only"),
        "conflicts": len(conflicts),
    }
    file_summary = {
        "movesCount": sum(1 for a in file_actions if a["action"] == "move_candidate"),
        "deletionsCount": sum(1 for a in file_actions if a["action"] == "delete_candidate"),
        "unmanagedCount": sum(1 for a in file_actions if a["action"] == "unmanaged_candidate"),
        "missingCount": sum(1 for a in file_actions if a["action"] == "missing_on_disk"),
    }

    return {
        "mergedState": merged_state,
        "stateChanges": state_changes,
        "fileActions": file_actions,
        "conflicts": conflicts,
        "warnings": warnings,
        "itemDecisions": item_decisions,
        "stateSummary": state_summary,
        "fileSummary": file_summary,
        "isReadOnlyPreview": True,
    }


def calculate_sync_preview_basis(
    reports_dir: Path | str,
    state_file_path: Path | str,
    incoming_state: Dict[str, Any],
    stock_name_dict: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """計算同步預覽基準，包含本機輸入與外來狀態內容，供後續確認套用驗證。"""
    validate_state(incoming_state)
    local_basis = calculate_preview_basis(reports_dir, state_file_path, stock_name_dict)
    incoming_bytes = json.dumps(
        incoming_state,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    incoming_hash = f"sha256:{hashlib.sha256(incoming_bytes).hexdigest()}"
    version = 1
    fingerprint_source = f"{version}|{local_basis['fingerprint']}|{incoming_hash}".encode("utf-8")
    fingerprint = f"sha256:{hashlib.sha256(fingerprint_source).hexdigest()}"
    return {
        "version": version,
        "fingerprint": fingerprint,
        "localBasis": local_basis,
        "incomingStateHash": incoming_hash,
    }


class SyncCleanupError(RuntimeError):
    """同步已提交，但交易暫存區清理失敗；保留位置供人工處理。"""

    def __init__(self, backup_dir: Path, cause: Exception):
        self.backup_dir = backup_dir
        self.cause = cause
        super().__init__(f"交易暫存區清理失敗，保留於 {backup_dir}: {cause}")


def execute_sync_plan_transactional(
    reports_dir: Path | str,
    file_actions: List[Dict[str, Any]],
    target_state: Dict[str, Any],
    state_file_path: Path | str,
    temp_dir: Optional[Path | str] = None,
) -> Dict[str, Any]:
    """
    交易式執行同步檔案整理與狀態更新（階段 5 授權）。
    1. 驗證全部搬移與刪除項目。
    2. 建立交易 Journal。
    3. 搬移：搬至目標目錄；記錄 (src, dest)。
    4. 刪除：移至私有暫存備份目錄；記錄 (src, backup)。
    5. 原子寫入狀態主檔 (save_state)。
    6. 失敗時依 Journal 逆序還原所有檔案與狀態，保證零殘留、無資料遺失。
    7. 成功後清理暫存備份目錄。
    """
    reports_p = Path(reports_dir).resolve()
    state_p = Path(state_file_path)
    validate_state(target_state)

    if not reports_p.is_dir():
        raise ValueError("reports 目錄不存在或不是資料夾")
    if state_p.is_symlink() or state_p.is_dir():
        raise ValueError("狀態檔路徑不安全")
    state_p = state_p.resolve()
    if state_p.is_relative_to(reports_p):
        raise ValueError("狀態檔不得位於 reports 目錄內")

    if temp_dir is not None:
        backup_dir = Path(temp_dir).resolve() / f".sync_backup_{uuid.uuid4().hex[:8]}"
    else:
        backup_dir = state_p.parent / f".sync_backup_{uuid.uuid4().hex[:8]}"

    if backup_dir.is_relative_to(reports_p):
        raise ValueError("交易暫存區不得位於 reports 目錄內")

    # 所有可變更項目在任何搬移前完成驗證，避免來源不存在、路徑逃逸或
    # 目標碰撞導致半完成的同步。
    move_plan: List[Tuple[Path, Path]] = []
    delete_plan: List[Path] = []
    seen_sources: Set[Path] = set()
    seen_destinations: Set[Path] = set()
    for action in file_actions:
        action_name = action.get("action")
        if action_name not in ("move_candidate", "delete_candidate"):
            continue
        files = action.get("files")
        if not isinstance(files, list):
            raise ValueError("同步檔案計畫格式不合法")
        if action_name == "move_candidate":
            target_folder = normalize_folder_path(action.get("targetFolder", ""))
            dest_dir = (reports_p / target_folder).resolve()
            if not dest_dir.is_relative_to(reports_p):
                raise ValueError("搬移目標資料夾不安全")
        for rel_file in files:
            if not isinstance(rel_file, str) or not rel_file.strip():
                raise ValueError("同步檔案計畫含有不合法路徑")
            src_file = (reports_p / rel_file).resolve()
            if not src_file.is_relative_to(reports_p) or not src_file.is_file() or src_file.is_symlink():
                raise ValueError(f"同步來源檔案不存在或不安全: {rel_file}")
            if src_file in seen_sources:
                raise ValueError(f"同步計畫重複處理同一檔案: {rel_file}")
            seen_sources.add(src_file)
            if action_name == "move_candidate":
                dest_file = dest_dir / src_file.name
                if dest_file == src_file:
                    continue
                if dest_file.exists() or dest_file in seen_destinations:
                    raise FileExistsError(f"目標檔案已存在，無法搬移: {dest_file.relative_to(reports_p)}")
                seen_destinations.add(dest_file)
                move_plan.append((src_file, dest_file))
            else:
                delete_plan.append(src_file)

    original_state_exists = state_p.exists()
    if original_state_exists and not state_p.is_file():
        raise ValueError("狀態檔路徑不安全")

    backup_dir.mkdir(parents=True, exist_ok=False)
    state_backup = backup_dir / "reports_state.original"
    if original_state_exists:
        shutil.copy2(str(state_p), str(state_backup))

    moved_journal: List[Tuple[Path, Path]] = []
    deleted_journal: List[Tuple[Path, Path]] = []
    created_folders: List[Path] = []
    state_write_attempted = False

    try:
        # 1. 執行搬移 (move_candidate)
        for src_file, dest_file in move_plan:
            dest_dir = dest_file.parent
            if not dest_dir.exists():
                new_dirs = []
                cursor = dest_dir
                while not cursor.exists():
                    new_dirs.append(cursor)
                    cursor = cursor.parent
                dest_dir.mkdir(parents=True, exist_ok=True)
                created_folders.extend(new_dirs)
            shutil.move(str(src_file), str(dest_file))
            moved_journal.append((src_file, dest_file))

        # 2. 執行刪除 (delete_candidate)
        for src_file in delete_plan:
            backup_file = backup_dir / f"{uuid.uuid4().hex[:8]}_{src_file.name}"
            shutil.move(str(src_file), str(backup_file))
            deleted_journal.append((src_file, backup_file))

        # 3. 原子寫入狀態主檔
        state_write_attempted = True
        save_state(target_state, state_p)

        # 4. 全部成功，清理暫存備份
        try:
            shutil.rmtree(str(backup_dir))
        except OSError as cleanup_error:
            raise SyncCleanupError(backup_dir, cleanup_error) from cleanup_error

        return {
            "ok": True,
            "movedCount": len(moved_journal),
            "deletedCount": len(deleted_journal),
            "movedFiles": [str(d.relative_to(reports_p)) for _, d in moved_journal],
            "deletedFiles": [str(s.relative_to(reports_p)) for s, _ in deleted_journal],
        }

    except Exception as e:
        if isinstance(e, SyncCleanupError):
            # 檔案與狀態已經原子提交；清理失敗時不可嘗試回滾部分已清掉的備份。
            raise

        # 回滾 (Rollback)
        rollback_errors = []
        for orig_src, curr_dest in reversed(moved_journal):
            try:
                if curr_dest.exists():
                    orig_src.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(curr_dest), str(orig_src))
            except Exception as re:
                rollback_errors.append(f"還原搬移失敗 ({curr_dest} -> {orig_src}): {re}")

        for orig_src, backup_file in reversed(deleted_journal):
            try:
                if backup_file.exists():
                    orig_src.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(backup_file), str(orig_src))
            except Exception as re:
                rollback_errors.append(f"還原刪除失敗 ({backup_file} -> {orig_src}): {re}")

        if state_write_attempted:
            try:
                if original_state_exists:
                    state_backup.replace(state_p)
                elif state_p.exists():
                    state_p.unlink()
            except Exception as re:
                rollback_errors.append(f"還原狀態檔失敗: {re}")

        for created_folder in reversed(created_folders):
            try:
                if created_folder.exists() and not any(created_folder.iterdir()):
                    created_folder.rmdir()
            except Exception as re:
                rollback_errors.append(f"清理新建資料夾失敗 ({created_folder}): {re}")

        try:
            shutil.rmtree(str(backup_dir))
        except OSError as re:
            rollback_errors.append(f"清理交易暫存區失敗 ({backup_dir}): {re}")

        if rollback_errors:
            raise RuntimeError(f"同步套用失敗且回滾發生錯誤: {e}; 回滾錯誤: {rollback_errors}") from e
        raise e

