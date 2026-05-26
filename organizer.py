"""
文件整理助手 v2
- 自定义扫描范围
- 按文件夹/按文件两种模式
- 智能整理建议（基于文件类型、日期、目录结构）
- 单文件撤销 + 批量撤销
- 持久化历史记录
"""

import os
import sys
import json
import shutil
import hashlib
import datetime
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from pathlib import Path
from collections import defaultdict
import threading

# ─── 常量 ───
APP_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(APP_DIR, "move_history.json")
SCAN_CACHE_FILE = os.path.join(APP_DIR, "scan_cache.json")

CATEGORY_MAP = {
    "图片": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".svg", ".webp", ".ico", ".tiff", ".psd", ".raw", ".heic"],
    "视频": [".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts"],
    "音频": [".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a", ".opus"],
    "文档": [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".md", ".csv", ".rtf", ".odt", ".ods", ".odp"],
    "压缩包": [".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".tgz"],
    "代码": [".py", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".rb", ".php", ".html", ".css", ".json", ".xml", ".yaml", ".yml", ".toml", ".sh", ".bat", ".ps1"],
    "可执行": [".exe", ".msi", ".dmg", ".app", ".deb", ".rpm", ".apk"],
    "字体": [".ttf", ".otf", ".woff", ".woff2", ".eot"],
    "数据库": [".db", ".sqlite", ".sqlite3", ".mdb", ".accdb"],
    "其他": [],
}


def get_category(ext: str) -> str:
    ext_lower = ext.lower()
    for cat, exts in CATEGORY_MAP.items():
        if ext_lower in exts:
            return cat
    return "其他"


def format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024**2:.1f} MB"
    else:
        return f"{size_bytes / 1024**3:.1f} GB"


def file_fingerprint(path: str) -> str:
    """用路径+大小+修改时间做轻量指纹"""
    try:
        stat = os.stat(path)
        raw = f"{path}|{stat.st_size}|{stat.st_mtime}"
        return hashlib.md5(raw.encode()).hexdigest()[:12]
    except:
        return ""


# ─── 历史记录 ───
def load_history() -> list:
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return []


def save_history(history: list):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def load_scan_cache() -> dict:
    if os.path.exists(SCAN_CACHE_FILE):
        try:
            with open(SCAN_CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {"files": {}, "paths": [], "time": ""}


def save_scan_cache(cache: dict):
    with open(SCAN_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# ─── 主界面 ───
class FileOrganizerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("文件整理助手 v2")
        self.root.geometry("1200x800")
        self.root.minsize(950, 650)

        self.scan_paths: list[str] = []
        self.scan_results: list[dict] = []
        self.move_mode = tk.StringVar(value="file")
        self.suggestions: list[dict] = []
        self.history = load_history()
        self.scan_cache = load_scan_cache()

        self._build_ui()

    def _build_ui(self):
        # ── 顶部：扫描范围 ──
        frame_top = ttk.LabelFrame(self.root, text="扫描范围", padding=8)
        frame_top.pack(fill=tk.X, padx=10, pady=(10, 5))

        self.path_listbox = tk.Listbox(frame_top, height=3)
        self.path_listbox.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        btn_frame = ttk.Frame(frame_top)
        btn_frame.pack(side=tk.RIGHT)
        ttk.Button(btn_frame, text="添加文件夹", command=self._add_path).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="移除选中", command=self._remove_path).pack(fill=tk.X, pady=2)
        ttk.Button(btn_frame, text="清空", command=self._clear_paths).pack(fill=tk.X, pady=2)

        # ── 模式选择 & 操作按钮 ──
        frame_mode = ttk.Frame(self.root, padding=5)
        frame_mode.pack(fill=tk.X, padx=10)

        ttk.Label(frame_mode, text="移动模式:").pack(side=tk.LEFT, padx=(0, 5))
        ttk.Radiobutton(frame_mode, text="按文件移动", variable=self.move_mode, value="file").pack(side=tk.LEFT, padx=5)
        ttk.Radiobutton(frame_mode, text="按文件夹移动", variable=self.move_mode, value="folder").pack(side=tk.LEFT, padx=5)

        ttk.Button(frame_mode, text="🔍 开始扫描", command=self._start_scan).pack(side=tk.RIGHT, padx=5)
        ttk.Button(frame_mode, text="💡 生成整理建议", command=self._generate_suggestions).pack(side=tk.RIGHT, padx=5)
        ttk.Button(frame_mode, text="🆕 检测新文件", command=self._detect_new_files).pack(side=tk.RIGHT, padx=5)

        # ── 主内容区：左右分栏 ──
        frame_main = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        frame_main.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # 左侧：扫描结果
        frame_left = ttk.LabelFrame(frame_main, text="扫描结果", padding=5)
        frame_main.add(frame_left, weight=3)

        cols = ("name", "ext", "size", "category", "modified", "path")
        col_labels = {"name": "文件名", "ext": "扩展名", "size": "大小", "category": "分类", "modified": "修改时间", "path": "路径"}
        self.tree = ttk.Treeview(frame_left, columns=cols, show="headings", selectmode="extended")
        for c in cols:
            self.tree.heading(c, text=col_labels[c], command=lambda _c=c: self._sort_tree(_c))
            self.tree.column(c, width=100)
        self.tree.column("name", width=180)
        self.tree.column("path", width=300)
        self.tree.column("size", width=70)
        self.tree.column("ext", width=60)
        self.tree.column("category", width=70)

        scrollbar = ttk.Scrollbar(frame_left, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # 右侧：整理建议
        frame_right = ttk.LabelFrame(frame_main, text="整理建议", padding=5)
        frame_main.add(frame_right, weight=2)

        self.suggestion_text = scrolledtext.ScrolledText(frame_right, wrap=tk.WORD, state=tk.DISABLED, font=("Microsoft YaHei", 10))
        self.suggestion_text.pack(fill=tk.BOTH, expand=True)

        # ── 底部按钮栏 ──
        frame_bottom = ttk.Frame(self.root, padding=8)
        frame_bottom.pack(fill=tk.X, padx=10, pady=(0, 10))

        self.status_label = ttk.Label(frame_bottom, text="就绪")
        self.status_label.pack(side=tk.LEFT, padx=(0, 15))

        ttk.Button(frame_bottom, text="📋 查看移动历史", command=self._show_history).pack(side=tk.RIGHT, padx=5)
        ttk.Button(frame_bottom, text="↩️ 撤销选中记录", command=self._undo_selected).pack(side=tk.RIGHT, padx=5)
        ttk.Button(frame_bottom, text="↩️ 撤销上次操作", command=self._undo_last).pack(side=tk.RIGHT, padx=5)
        ttk.Button(frame_bottom, text="✅ 执行移动", command=self._execute_move).pack(side=tk.RIGHT, padx=5)

    # ─── 路径管理 ───
    def _add_path(self):
        path = filedialog.askdirectory(title="选择扫描文件夹")
        if path and path not in self.scan_paths:
            self.scan_paths.append(path)
            self.path_listbox.insert(tk.END, path)

    def _remove_path(self):
        sel = self.path_listbox.curselection()
        if sel:
            idx = sel[0]
            self.path_listbox.delete(idx)
            self.scan_paths.pop(idx)

    def _clear_paths(self):
        self.scan_paths.clear()
        self.path_listbox.delete(0, tk.END)

    # ─── 扫描 ───
    def _start_scan(self):
        if not self.scan_paths:
            messagebox.showwarning("提示", "请先添加扫描路径")
            return

        self.scan_results.clear()
        self.tree.delete(*self.tree.get_children())
        self._set_status("正在扫描...")

        def do_scan():
            count = 0
            for base_path in self.scan_paths:
                for root_dir, dirs, files in os.walk(base_path):
                    dirs[:] = [d for d in dirs if not d.startswith(('.', '__'))]
                    for fname in files:
                        if fname.startswith('.'):
                            continue
                        fpath = os.path.join(root_dir, fname)
                        try:
                            stat = os.stat(fpath)
                            ext = os.path.splitext(fname)[1]
                            rel = os.path.relpath(fpath, base_path)
                            top_dir = rel.split(os.sep)[0] if os.sep in rel else ""
                            item = {
                                "path": fpath,
                                "name": fname,
                                "size": stat.st_size,
                                "ext": ext,
                                "category": get_category(ext),
                                "modified": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                                "mtime": stat.st_mtime,
                                "suggested": "",
                                "depth": rel.count(os.sep),
                                "rel_path": rel,
                                "top_dir": top_dir,
                                "parent_dir": os.path.dirname(rel),
                                "base_path": base_path,
                            }
                            self.scan_results.append(item)
                            count += 1
                        except (PermissionError, OSError):
                            pass

            # 更新缓存
            cache_files = {}
            for item in self.scan_results:
                fp = file_fingerprint(item["path"])
                if fp:
                    cache_files[fp] = {"path": item["path"], "name": item["name"], "size": item["size"], "category": item["category"]}
            self.scan_cache["files"] = cache_files
            self.scan_cache["paths"] = list(self.scan_paths)
            self.scan_cache["time"] = datetime.datetime.now().isoformat()
            save_scan_cache(self.scan_cache)

            self.root.after(0, lambda: self._populate_tree())
            self.root.after(0, lambda: self._set_status(f"扫描完成，共 {count} 个文件"))

        threading.Thread(target=do_scan, daemon=True).start()

    def _populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        for item in self.scan_results:
            self.tree.insert("", tk.END, values=(
                item["name"], item["ext"], format_size(item["size"]),
                item["category"], item["modified"], item["path"]
            ))

    def _sort_tree(self, col):
        """点击列头排序"""
        items = [(self.tree.set(k, col), k) for k in self.tree.get_children("")]
        try:
            items.sort(key=lambda t: float(t[0].replace(" KB", "").replace(" MB", "").replace(" GB", "").replace(" B", "")))
        except ValueError:
            items.sort(key=lambda t: t[0])
        for i, (_, k) in enumerate(items):
            self.tree.move(k, "", i)

    # ─── 检测新文件 ───
    def _detect_new_files(self):
        if not self.scan_paths:
            messagebox.showwarning("提示", "请先添加扫描路径")
            return

        old_fps = set(self.scan_cache.get("files", {}).keys())
        if not old_fps:
            messagebox.showinfo("提示", "没有上次扫描记录，请先执行一次扫描")
            return

        self._set_status("正在检测新文件...")
        self.scan_results.clear()
        self.tree.delete(*self.tree.get_children())

        def do_detect():
            new_files = []
            all_files = []
            for base_path in self.scan_paths:
                for root_dir, dirs, files in os.walk(base_path):
                    dirs[:] = [d for d in dirs if not d.startswith(('.', '__'))]
                    for fname in files:
                        if fname.startswith('.'):
                            continue
                        fpath = os.path.join(root_dir, fname)
                        try:
                            stat = os.stat(fpath)
                            ext = os.path.splitext(fname)[1]
                            fp = file_fingerprint(fpath)
                            rel = os.path.relpath(fpath, base_path)
                            top_dir = rel.split(os.sep)[0] if os.sep in rel else ""
                            item = {
                                "path": fpath,
                                "name": fname,
                                "size": stat.st_size,
                                "ext": ext,
                                "category": get_category(ext),
                                "modified": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                                "mtime": stat.st_mtime,
                                "suggested": "",
                                "depth": rel.count(os.sep),
                                "rel_path": rel,
                                "top_dir": top_dir,
                                "parent_dir": os.path.dirname(rel),
                                "base_path": base_path,
                            }
                            all_files.append(item)
                            if fp not in old_fps:
                                new_files.append(item)
                        except (PermissionError, OSError):
                            pass

            self.scan_results = all_files
            self.root.after(0, lambda: self._populate_tree())
            self.root.after(0, lambda: self._set_status(f"检测完成: {len(all_files)} 个文件，其中 {len(new_files)} 个新文件"))

            if new_files:
                self.root.after(0, lambda: self._auto_suggest_new(new_files))

        threading.Thread(target=do_detect, daemon=True).start()

    def _auto_suggest_new(self, new_files: list[dict]):
        """为新文件自动生成整理建议"""
        self.suggestions.clear()
        groups = defaultdict(list)
        for f in new_files:
            groups[f["category"]].append(f)

        base = self.scan_paths[0]
        for category, files in groups.items():
            target_dir = os.path.join(base, f"新文件_{category}")
            suggestion = {
                "target_dir": target_dir,
                "files": files,
                "description": f"新发现的 {len(files)} 个{category}文件",
                "is_new": True,
            }
            for f in files:
                f["suggested"] = target_dir
            self.suggestions.append(suggestion)

        self._display_suggestions(highlight_new=True)

    # ─── 整理建议 ───
    def _generate_suggestions(self):
        if not self.scan_results:
            messagebox.showwarning("提示", "请先扫描文件")
            return

        self.suggestions.clear()

        if self.move_mode.get() == "file":
            self._suggest_by_file()
        else:
            self._suggest_by_folder()

        self._display_suggestions()

    def _suggest_by_file(self):
        groups = defaultdict(list)
        for item in self.scan_results:
            groups[item["category"]].append(item)

        base = self.scan_paths[0]
        for category, files in groups.items():
            target_dir = os.path.join(base, f"整理_{category}")
            suggestion = {
                "target_dir": target_dir,
                "files": files,
                "description": f"将 {len(files)} 个{category}文件移至 {os.path.basename(target_dir)}/",
            }
            for f in files:
                f["suggested"] = target_dir
            self.suggestions.append(suggestion)

    def _suggest_by_folder(self):
        """按文件夹建议：基于目录结构分组，整个子文件夹作为移动单位"""
        for base in self.scan_paths:
            base_items = [i for i in self.scan_results if i.get("base_path") == base]
            if not base_items:
                continue

            # 按顶层子目录分组
            dir_groups = defaultdict(list)
            loose_files = []
            for item in base_items:
                top = item.get("top_dir", "")
                if top and item["path"] != os.path.join(base, top):
                    dir_groups[top].append(item)
                else:
                    loose_files.append(item)

            # 每个子目录作为一个整体建议
            for dirname, files in dir_groups.items():
                target_dir = os.path.join(base, f"整理_{dirname}")
                cat_count = defaultdict(int)
                for f in files:
                    cat_count[f["category"]] += 1
                composition = "、".join(f"{cat}×{n}" for cat, n in sorted(cat_count.items(), key=lambda x: -x[1]))

                suggestion = {
                    "target_dir": target_dir,
                    "files": files,
                    "description": f"整个文件夹 {dirname}/ 移入整理目录（{composition}）",
                    "is_folder": True,
                    "source_dir": os.path.join(base, dirname),
                }
                for f in files:
                    f["suggested"] = target_dir
                self.suggestions.append(suggestion)

            # 散文件按类型归类
            if loose_files:
                cat_groups = defaultdict(list)
                for f in loose_files:
                    cat_groups[f["category"]].append(f)
                for cat, files in cat_groups.items():
                    target_dir = os.path.join(base, f"散文件_{cat}")
                    suggestion = {
                        "target_dir": target_dir,
                        "files": files,
                        "description": f"根目录下 {len(files)} 个{cat}散文件归类整理",
                        "is_loose": True,
                    }
                    for f in files:
                        f["suggested"] = target_dir
                    self.suggestions.append(suggestion)

    def _display_suggestions(self, highlight_new=False):
        self.suggestion_text.config(state=tk.NORMAL)
        self.suggestion_text.delete("1.0", tk.END)

        total_files = sum(len(s["files"]) for s in self.suggestions)
        total_size = sum(f["size"] for s in self.suggestions for f in s["files"])
        self.suggestion_text.insert(tk.END, f"📊 整理概览\n")
        self.suggestion_text.insert(tk.END, f"共 {total_files} 个文件，总大小 {format_size(total_size)}\n")
        self.suggestion_text.insert(tk.END, f"分为 {len(self.suggestions)} 个类别\n\n")

        for i, s in enumerate(self.suggestions, 1):
            size = sum(f["size"] for f in s["files"])
            tags = ""
            if s.get("is_new"):
                tags = "🆕 "
            if s.get("is_folder"):
                tags += "📂 "
            elif s.get("is_loose"):
                tags += "📎 "

            self.suggestion_text.insert(tk.END, f"{'─' * 40}\n")
            self.suggestion_text.insert(tk.END, f"{tags}{i}. {os.path.basename(s['target_dir'])}\n")
            self.suggestion_text.insert(tk.END, f"   目标: {s['target_dir']}\n")
            self.suggestion_text.insert(tk.END, f"   文件数: {len(s['files'])}  |  总大小: {format_size(size)}\n")
            self.suggestion_text.insert(tk.END, f"   {s['description']}\n\n")

            # 按文件夹模式：显示子目录结构预览
            if s.get("is_folder"):
                subdirs = defaultdict(int)
                for f in s["files"]:
                    parent = f.get("parent_dir", "")
                    subdirs[parent] += 1
                for sd, cnt in list(subdirs.items())[:5]:
                    display = sd if sd else "(根目录)"
                    self.suggestion_text.insert(tk.END, f"     📁 {display}: {cnt} 个文件\n")
                if len(subdirs) > 5:
                    self.suggestion_text.insert(tk.END, f"     ... 还有 {len(subdirs) - 5} 个子目录\n")
            else:
                for f in s["files"][:5]:
                    self.suggestion_text.insert(tk.END, f"     • {f['name']} ({format_size(f['size'])})\n")
                if len(s["files"]) > 5:
                    self.suggestion_text.insert(tk.END, f"     ... 还有 {len(s['files']) - 5} 个文件\n")
            self.suggestion_text.insert(tk.END, "\n")

        self.suggestion_text.config(state=tk.DISABLED)

    # ─── 执行移动 ───
    def _execute_move(self):
        if not self.suggestions:
            messagebox.showwarning("提示", "请先生成整理建议")
            return

        total = sum(len(s["files"]) for s in self.suggestions)
        if not messagebox.askyesno("确认移动", f"即将移动 {total} 个文件，确定继续？"):
            return

        self._set_status("正在移动文件...")
        move_record = []
        errors = []

        def do_move():
            for s in self.suggestions:
                target = s["target_dir"]
                os.makedirs(target, exist_ok=True)
                for f in s["files"]:
                    src = f["path"]
                    dst = os.path.join(target, f["name"])
                    if os.path.exists(dst):
                        base_name, ext = os.path.splitext(f["name"])
                        counter = 1
                        while os.path.exists(dst):
                            dst = os.path.join(target, f"{base_name}_{counter}{ext}")
                            counter += 1
                    try:
                        shutil.move(src, dst)
                        move_record.append({
                            "src": src,
                            "dst": dst,
                            "time": datetime.datetime.now().isoformat(),
                            "size": f["size"],
                            "category": f["category"],
                        })
                    except Exception as e:
                        errors.append(f"{f['name']}: {e}")

            if move_record:
                self.history.append({
                    "id": datetime.datetime.now().strftime("%Y%m%d%H%M%S"),
                    "time": datetime.datetime.now().isoformat(),
                    "count": len(move_record),
                    "moves": move_record,
                })
                save_history(self.history)

            msg = f"移动完成: {len(move_record)} 个文件"
            if errors:
                msg += f"，{len(errors)} 个失败"
            self.root.after(0, lambda: self._set_status(msg))
            if errors:
                self.root.after(0, lambda: messagebox.showwarning("部分失败", "\n".join(errors[:10])))
            else:
                self.root.after(0, lambda: messagebox.showinfo("完成", msg))

            self.scan_results.clear()
            self.suggestions.clear()
            self.root.after(0, lambda: self.tree.delete(*self.tree.get_children()))
            self.root.after(0, lambda: self._clear_suggestion_text())

        threading.Thread(target=do_move, daemon=True).start()

    def _clear_suggestion_text(self):
        self.suggestion_text.config(state=tk.NORMAL)
        self.suggestion_text.delete("1.0", tk.END)
        self.suggestion_text.config(state=tk.DISABLED)

    # ─── 撤销功能 ───
    def _undo_last(self):
        """撤销整批操作"""
        if not self.history:
            messagebox.showinfo("提示", "没有可撤销的操作")
            return

        last = self.history[-1]
        count = last["count"]
        if not messagebox.askyesno("确认撤销", f"撤销上次操作（{count} 个文件移动）？"):
            return

        self._do_undo_batch(last, len(self.history) - 1)

    def _undo_selected(self):
        """打开历史窗口，让用户选择单个或多个文件撤销"""
        if not self.history:
            messagebox.showinfo("提示", "没有可撤销的操作")
            return

        win = tk.Toplevel(self.root)
        win.title("选择要撤销的移动记录")
        win.geometry("850x550")

        # 说明
        ttk.Label(win, text="勾选要撤销的文件移动记录，然后点击「撤销选中」", padding=8).pack(anchor=tk.W)

        # 操作按钮
        btn_bar = ttk.Frame(win, padding=5)
        btn_bar.pack(fill=tk.X)
        ttk.Button(btn_bar, text="全选", command=lambda: self._select_all_undo(tree, True)).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_bar, text="全不选", command=lambda: self._select_all_undo(tree, False)).pack(side=tk.LEFT, padx=3)
        ttk.Button(btn_bar, text="撤销选中", command=lambda: self._do_undo_picked(tree, win)).pack(side=tk.RIGHT, padx=3)

        # 表格
        cols = ("batch", "sel", "name", "from", "to", "time", "category")
        col_labels = {"batch": "批次", "sel": "✓", "name": "文件名", "from": "原位置", "to": "移到", "time": "时间", "category": "分类"}
        tree = ttk.Treeview(win, columns=cols, show="headings", selectmode="none")
        for c in cols:
            tree.heading(c, text=col_labels[c])
            tree.column(c, width=80)
        tree.column("name", width=150)
        tree.column("from", width=220)
        tree.column("to", width=220)
        tree.column("batch", width=40)
        tree.column("sel", width=30)
        tree.column("time", width=100)

        scrollbar = ttk.Scrollbar(win, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=5)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=5, padx=(0, 10))

        # 填充数据 — 用 tag 标记选中状态
        self._undo_check_vars = {}  # iid -> bool
        for bi, record in enumerate(self.history):
            for mi, m in enumerate(record["moves"]):
                iid = f"{bi}_{mi}"
                tree.insert("", tk.END, iid=iid, values=(
                    bi + 1, "☐", os.path.basename(m["src"]),
                    os.path.dirname(m["src"]), m["dst"], m.get("time", ""),
                    m.get("category", ""),
                ))
                self._undo_check_vars[iid] = False

        # 点击切换勾选
        def on_click(event):
            region = tree.identify_region(event.x, event.y)
            if region == "cell":
                col = tree.identify_column(event.x)
                iid = tree.identify_row(event.y)
                if iid and col == "#2":  # sel 列
                    self._undo_check_vars[iid] = not self._undo_check_vars[iid]
                    mark = "☑" if self._undo_check_vars[iid] else "☐"
                    vals = list(tree.item(iid, "values"))
                    vals[1] = mark
                    tree.item(iid, values=vals)

        tree.bind("<ButtonRelease-1>", on_click)

        # 双击整行也切换
        def on_dblclick(event):
            iid = tree.identify_row(event.y)
            if iid:
                self._undo_check_vars[iid] = not self._undo_check_vars[iid]
                mark = "☑" if self._undo_check_vars[iid] else "☐"
                vals = list(tree.item(iid, "values"))
                vals[1] = mark
                tree.item(iid, values=vals)

        tree.bind("<Double-1>", on_dblclick)

    def _select_all_undo(self, tree, state: bool):
        for iid in self._undo_check_vars:
            self._undo_check_vars[iid] = state
            mark = "☑" if state else "☐"
            vals = list(tree.item(iid, "values"))
            vals[1] = mark
            tree.item(iid, values=vals)

    def _do_undo_picked(self, tree, win):
        picked = [iid for iid, v in self._undo_check_vars.items() if v]
        if not picked:
            messagebox.showwarning("提示", "请先勾选要撤销的记录")
            return

        # 按批次分组
        batch_moves = defaultdict(list)  # batch_idx -> [move_indices]
        for iid in picked:
            bi, mi = iid.split("_")
            batch_moves[int(bi)].append(int(mi))

        total = sum(len(v) for v in batch_moves.values())
        if not messagebox.askyesno("确认撤销", f"即将撤销 {total} 个文件的移动，确定？"):
            return

        errors = []
        undone = 0

        for bi in sorted(batch_moves.keys(), reverse=True):
            record = self.history[bi]
            move_indices = batch_moves[bi]
            # 按倒序撤销同一批次内的操作
            for mi in sorted(move_indices, reverse=True):
                m = record["moves"][mi]
                try:
                    src = m["dst"]
                    dst = m["src"]
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.move(src, dst)
                    undone += 1
                except Exception as e:
                    errors.append(f"{os.path.basename(m['dst'])}: {e}")

            # 清理空批次
            remaining = [i for i in range(len(record["moves"])) if i not in move_indices]
            if not remaining:
                self.history.pop(bi)
            else:
                record["moves"] = [record["moves"][i] for i in remaining]
                record["count"] = len(record["moves"])

        save_history(self.history)
        msg = f"撤销完成: {undone} 个文件已恢复"
        if errors:
            msg += f"，{len(errors)} 个失败"
        self._set_status(msg)
        win.destroy()
        if errors:
            messagebox.showwarning("部分失败", "\n".join(errors[:10]))
        else:
            messagebox.showinfo("完成", msg)

    def _do_undo_batch(self, record, idx):
        """撤销整批操作"""
        self._set_status("正在撤销...")
        errors = []

        def do_undo():
            for move in reversed(record["moves"]):
                try:
                    src = move["dst"]
                    dst = move["src"]
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.move(src, dst)
                except Exception as e:
                    errors.append(f"{os.path.basename(move['dst'])}: {e}")

            self.history.pop(idx)
            save_history(self.history)

            msg = f"撤销完成: {record['count'] - len(errors)} 个文件已恢复"
            if errors:
                msg += f"，{len(errors)} 个失败"
            self.root.after(0, lambda: self._set_status(msg))
            if errors:
                self.root.after(0, lambda: messagebox.showwarning("部分失败", "\n".join(errors[:10])))
            else:
                self.root.after(0, lambda: messagebox.showinfo("完成", msg))

        threading.Thread(target=do_undo, daemon=True).start()

    # ─── 历史查看 ───
    def _show_history(self):
        win = tk.Toplevel(self.root)
        win.title("移动历史")
        win.geometry("750x500")

        text = scrolledtext.ScrolledText(win, wrap=tk.WORD, font=("Microsoft YaHei", 10))
        text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        if not self.history:
            text.insert(tk.END, "暂无移动记录")
            text.config(state=tk.DISABLED)
            return

        for i, record in enumerate(reversed(self.history), 1):
            idx = len(self.history) - i
            text.insert(tk.END, f"{'═' * 50}\n")
            text.insert(tk.END, f"📋 批次 #{idx + 1}  |  {record['time']}\n")
            text.insert(tk.END, f"   文件数: {record['count']}\n\n")
            for mi, m in enumerate(record["moves"]):
                text.insert(tk.END, f"   [{mi + 1}] {os.path.basename(m['src'])}\n")
                text.insert(tk.END, f"       {m['src']}\n")
                text.insert(tk.END, f"    →  {m['dst']}\n")
            text.insert(tk.END, "\n")

        text.config(state=tk.DISABLED)

    # ─── 状态栏 ───
    def _set_status(self, msg: str):
        self.status_label.config(text=msg)


def main():
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except:
        pass
    FileOrganizerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
