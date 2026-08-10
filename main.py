"""
Vaultix
=======
Aplikasi desktop untuk Windows: mendaftarkan folder pilihan, lalu memilih
aksi terhadap folder tersebut secara terpisah dan bisa diulang-ulang:

- SEMBUNYIKAN  : folder diberi atribut Hidden+System (hilang dari Explorer).
                 Tidak butuh password - sifatnya cuma kerapian tampilan,
                 bisa ditampilkan lagi kapan saja.
- KUNCI        : folder diberi izin NTFS "Deny" untuk user saat ini lewat
                 `icacls`, sehingga folder benar-benar tidak bisa dibuka
                 (Access Denied) sampai dibuka kembali lewat aplikasi ini
                 dengan password master. Ini lebih kuat daripada sekadar
                 menyembunyikan, walau tetap BUKAN enkripsi isi file.
- KUNCI & SEMBUNYIKAN : melakukan keduanya sekaligus.

Kedua aksi ini independen dan reversible - folder tetap ada di "Daftar
Folder" apa pun statusnya, dan bisa dikunci/disembunyikan ulang kapan saja.

Password disimpan sebagai hash (PBKDF2-HMAC-SHA256 + salt), tidak pernah
sebagai teks biasa.
"""

import ctypes
import glob
import hashlib
import json
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

try:
    import winreg
except ImportError:
    winreg = None

try:
    import win32com.client
    HAS_WIN32COM = True
except ImportError:
    HAS_WIN32COM = False

try:
    import customtkinter as ctk
    HAS_CTK = True
except ImportError:
    HAS_CTK = False

APP_DIR = os.path.join(os.path.expanduser("~"), ".vaultix")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_NORMAL = 0x80

IS_WINDOWS = sys.platform.startswith("win")
CURRENT_USER = os.environ.get("USERNAME", "")


# ---------------------------------------------------------------------------
# Penyimpanan konfigurasi
# ---------------------------------------------------------------------------

def ensure_app_dir():
    os.makedirs(APP_DIR, exist_ok=True)


def load_config():
    ensure_app_dir()
    if not os.path.exists(CONFIG_PATH):
        return {"salt": None, "password_hash": None, "folders": [], "theme": "System"}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Migrasi dari format lama (locked_folders) ke format baru (folders)
    if "folders" not in data and "locked_folders" in data:
        migrated = []
        for entry in data.get("locked_folders", []):
            migrated.append(
                {
                    "path": entry["path"],
                    "hidden": entry.get("locked", True),
                    "locked": False,
                }
            )
        data["folders"] = migrated
    data.setdefault("folders", [])
    data.setdefault("theme", "System")
    for entry in data["folders"]:
        entry.setdefault("tag", "")
    return data


def save_config(config):
    ensure_app_dir()
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def hash_password(password: str, salt: bytes) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return dk.hex()


def set_master_password(config, password: str):
    salt = secrets.token_bytes(16)
    config["salt"] = salt.hex()
    config["password_hash"] = hash_password(password, salt)
    save_config(config)


def verify_password(config, password: str) -> bool:
    if not config.get("salt") or not config.get("password_hash"):
        return False
    salt = bytes.fromhex(config["salt"])
    return hash_password(password, salt) == config["password_hash"]


# ---------------------------------------------------------------------------
# Aksi: SEMBUNYIKAN (atribut folder)
# ---------------------------------------------------------------------------

def set_folder_hidden(path: str, hidden: bool):
    if not IS_WINDOWS:
        raise RuntimeError("Fitur ini hanya berjalan di Windows.")
    attrs = (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM) if hidden else FILE_ATTRIBUTE_NORMAL
    ok = ctypes.windll.kernel32.SetFileAttributesW(path, attrs)
    if not ok:
        raise ctypes.WinError()


# ---------------------------------------------------------------------------
# Aksi: KUNCI (izin NTFS lewat icacls - blokir akses sungguhan)
# ---------------------------------------------------------------------------

def lock_folder_access(path: str):
    if not IS_WINDOWS:
        raise RuntimeError("Fitur ini hanya berjalan di Windows.")
    user = CURRENT_USER or os.getlogin()
    # PENTING: pakai "M" (Modify), BUKAN "F" (Full Control).
    # Full Control mencakup hak WRITE_DAC/WRITE_OWNER (hak mengubah izin
    # folder itu sendiri). Kalau itu ikut di-deny, akun yang sama tidak
    # akan bisa lagi menghapus entri deny ini di kemudian hari - self
    # lockout permanen. "Modify" tetap memblokir baca/tulis/hapus isi
    # folder, tapi tidak memblokir hak mengubah izin, sehingga folder
    # selalu bisa dibuka kunci lagi lewat aplikasi ini.
    result = subprocess.run(
        ["icacls", path, "/deny", f"{user}:(OI)(CI)M"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "icacls gagal").strip())


def unlock_folder_access(path: str):
    if not IS_WINDOWS:
        raise RuntimeError("Fitur ini hanya berjalan di Windows.")
    user = CURRENT_USER or os.getlogin()
    result = subprocess.run(
        ["icacls", path, "/remove:d", user],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "icacls gagal").strip())


# ---------------------------------------------------------------------------
# Riwayat akses folder/file (baca dari data yang sudah dicatat Windows)
# ---------------------------------------------------------------------------

def get_recent_items():
    results = []
    if not IS_WINDOWS:
        return results
    recent_dir = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Recent")
    if not os.path.isdir(recent_dir) or not HAS_WIN32COM:
        return results
    shell = win32com.client.Dispatch("WScript.Shell")
    for lnk_path in glob.glob(os.path.join(recent_dir, "*.lnk")):
        try:
            shortcut = shell.CreateShortCut(lnk_path)
            target = shortcut.TargetPath
            if not target:
                continue
            mtime = os.path.getmtime(lnk_path)
            results.append(
                {
                    "name": os.path.basename(target.rstrip("\\/")) or target,
                    "path": target,
                    "is_folder": os.path.isdir(target),
                    "time": datetime.fromtimestamp(mtime),
                    "source": "Recent Items",
                }
            )
        except Exception:
            continue
    return results


def _extract_readable_utf16(data: bytes, min_len=4):
    pattern = re.compile(rb"(?:[\x20-\x7e]\x00){%d,}" % min_len)
    found = []
    for m in pattern.finditer(data):
        try:
            text = m.group(0).decode("utf-16-le", errors="ignore").strip()
            if text:
                found.append(text)
        except Exception:
            continue
    return found


def get_recent_folders_registry():
    results = []
    if not IS_WINDOWS or winreg is None:
        return results
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\RecentDocs\Folder"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            i = 0
            while True:
                try:
                    name, value, vtype = winreg.EnumValue(key, i)
                except OSError:
                    break
                i += 1
                if name == "MRUListEx" or not isinstance(value, (bytes, bytearray)):
                    continue
                strings = _extract_readable_utf16(bytes(value))
                if not strings:
                    continue
                best_guess = max(strings, key=len)
                results.append(
                    {
                        "name": best_guess,
                        "path": best_guess,
                        "is_folder": True,
                        "time": None,
                        "source": "Registry (MRU, urutan saja)",
                    }
                )
    except FileNotFoundError:
        pass
    except OSError:
        pass
    return results


def get_folder_access_history():
    items = get_recent_items() + get_recent_folders_registry()
    items.sort(key=lambda x: x["time"] or datetime.min, reverse=True)
    return items


# ---------------------------------------------------------------------------
# Util UI
# ---------------------------------------------------------------------------

def center_window(win, width, height):
    win.update_idletasks()
    screen_w = win.winfo_screenwidth()
    screen_h = win.winfo_screenheight()
    x = (screen_w - width) // 2
    y = (screen_h - height) // 2
    win.geometry(f"{width}x{height}+{x}+{y}")


def status_text(entry):
    hide_part = "🙈 Tersembunyi" if entry.get("hidden") else "👁 Terlihat"
    lock_part = "🔒 Terkunci" if entry.get("locked") else "🔓 Terbuka"
    return hide_part, lock_part


# ---------------------------------------------------------------------------
# Aplikasi utama
# ---------------------------------------------------------------------------

class VaultixApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Vaultix")
        self.config_data = load_config()

        ctk.set_appearance_mode(self.config_data.get("theme", "System"))

        WIDTH, HEIGHT = 900, 580
        self.geometry(f"{WIDTH}x{HEIGHT}")
        self.minsize(WIDTH, HEIGHT)
        center_window(self, WIDTH, HEIGHT)

        if not self.config_data.get("password_hash"):
            self.first_run_setup()

        self.build_ui()
        self.apply_treeview_style()
        self.refresh_list()

    # -- dialog password bertema (menggantikan simpledialog polos) ---------
    def ask_password_dialog(self, title, prompt, confirm=False):
        """Dialog input password ala CustomTkinter yang ikut tema Light/Dark.
        Kalau confirm=True, ada kolom ulangi password + validasi panjang
        minimal & kecocokan. Mengembalikan password (str) atau None kalau
        dibatalkan."""
        box = ctk.CTkToplevel(self)
        box.title(title)
        W, H = (380, 260) if confirm else (380, 190)
        box.geometry(f"{W}x{H}")
        center_window(box, W, H)
        box.transient(self)
        box.grab_set()
        box.resizable(False, False)

        result = {"value": None}

        ctk.CTkLabel(box, text=prompt, wraplength=340, justify="left").pack(padx=20, pady=(20, 8))
        entry1 = ctk.CTkEntry(box, show="*", width=300, placeholder_text="Password")
        entry1.pack(padx=20, pady=4)
        entry1.focus_set()

        entry2 = None
        if confirm:
            entry2 = ctk.CTkEntry(box, show="*", width=300, placeholder_text="Ulangi password")
            entry2.pack(padx=20, pady=4)

        error_var = tk.StringVar(value="")
        ctk.CTkLabel(box, textvariable=error_var, text_color="#e05555", wraplength=340).pack(pady=(6, 0))

        def submit(event=None):
            pw1 = entry1.get()
            if not pw1:
                error_var.set("Password tidak boleh kosong.")
                return
            if confirm:
                if len(pw1) < 4:
                    error_var.set("Password minimal 4 karakter.")
                    return
                if pw1 != entry2.get():
                    error_var.set("Password tidak cocok, coba lagi.")
                    return
            result["value"] = pw1
            box.destroy()

        def cancel():
            box.destroy()

        entry1.bind("<Return>", submit)
        if entry2:
            entry2.bind("<Return>", submit)

        btn_frame = ctk.CTkFrame(box, fg_color="transparent")
        btn_frame.pack(pady=15)
        ctk.CTkButton(btn_frame, text="OK", command=submit, width=100).pack(side="left", padx=5)
        ctk.CTkButton(
            btn_frame, text="Batal", command=cancel, width=100,
            fg_color="gray40", hover_color="gray30",
        ).pack(side="left", padx=5)

        box.protocol("WM_DELETE_WINDOW", cancel)
        box.wait_window()
        return result["value"]

    # -- setup password pertama kali -------------------------------------
    def first_run_setup(self):
        messagebox.showinfo(
            "Selamat datang",
            "Ini adalah pertama kali Anda menjalankan Vaultix.\n"
            "Silakan buat password master terlebih dahulu.",
        )
        pw = self.ask_password_dialog(
            "Buat Password Master", "Buat password master (minimal 4 karakter):", confirm=True
        )
        if pw is None:
            sys.exit(0)
        set_master_password(self.config_data, pw)
        messagebox.showinfo("Sukses", "Password master berhasil dibuat.")

    # -- UI ---------------------------------------------------------------
    def build_ui(self):
        top = ctk.CTkFrame(self, corner_radius=0)
        top.pack(fill="x")

        ctk.CTkLabel(
            top, text="🗄  Vaultix", font=ctk.CTkFont(size=20, weight="bold")
        ).pack(side="left", padx=15, pady=12)

        self.theme_menu = ctk.CTkOptionMenu(
            top, values=["System", "Light", "Dark"], command=self.on_theme_change, width=110
        )
        self.theme_menu.set(self.config_data.get("theme", "System"))
        self.theme_menu.pack(side="right", padx=15, pady=12)

        ctk.CTkLabel(top, text="Tema:").pack(side="right", pady=12)

        # -- daftar folder --
        list_frame = ctk.CTkFrame(self)
        list_frame.pack(fill="both", expand=True, padx=15, pady=(10, 5))

        ctk.CTkLabel(
            list_frame, text="Daftar Folder", font=ctk.CTkFont(size=14, weight="bold")
        ).pack(anchor="w", padx=10, pady=(10, 0))

        tree_container = ctk.CTkFrame(list_frame, fg_color="transparent")
        tree_container.pack(fill="both", expand=True, padx=10, pady=10)

        columns = ("path", "hidden", "locked", "tag")
        self.tree = ttk.Treeview(tree_container, columns=columns, show="headings", selectmode="extended", height=10)
        self.tree.heading("path", text="Folder")
        self.tree.heading("hidden", text="Status Tampilan")
        self.tree.heading("locked", text="Status Kunci")
        self.tree.heading("tag", text="Tanda")
        self.tree.column("path", width=300)
        self.tree.column("hidden", width=130, anchor="center")
        self.tree.column("locked", width=130, anchor="center")
        self.tree.column("tag", width=140, anchor="center")
        self.tree.pack(side="left", fill="both", expand=True)

        sb = ttk.Scrollbar(tree_container, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)

        # -- tombol kelola daftar --
        manage_frame = ctk.CTkFrame(self, fg_color="transparent")
        manage_frame.pack(fill="x", padx=15, pady=(0, 5))
        ctk.CTkButton(manage_frame, text="+ Tambah Folder", width=140, command=self.open_add_dialog).pack(side="left", padx=(0, 8))
        ctk.CTkButton(manage_frame, text="🏷 Tandai Folder", width=140, command=self.open_tag_dialog).pack(side="left", padx=8)
        ctk.CTkButton(manage_frame, text="Hapus dari Daftar", width=140, command=self.remove_selected, fg_color="#8a3b3b", hover_color="#6e2f2f").pack(side="left", padx=8)
        ctk.CTkButton(manage_frame, text="Riwayat Akses Folder", width=160, command=self.show_access_history).pack(side="left", padx=8)
        ctk.CTkButton(manage_frame, text="Ganti Password", width=140, command=self.change_password).pack(side="right")

        # -- tombol aksi --
        action_frame = ctk.CTkFrame(self)
        action_frame.pack(fill="x", padx=15, pady=(5, 15))

        visibility_box = ctk.CTkFrame(action_frame, fg_color="transparent")
        visibility_box.pack(side="left", expand=True, fill="both", padx=15, pady=12)
        ctk.CTkLabel(visibility_box, text="Sembunyikan (tanpa password)", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w")
        ctk.CTkButton(visibility_box, text="Sembunyikan", width=220, command=lambda: self.action_hide(True)).pack(fill="x", pady=(8, 4))
        ctk.CTkButton(visibility_box, text="Tampilkan", width=220, command=lambda: self.action_hide(False)).pack(fill="x")

        sep1 = ttk.Separator(action_frame, orient="vertical")
        sep1.pack(side="left", fill="y", pady=12)

        lock_box = ctk.CTkFrame(action_frame, fg_color="transparent")
        lock_box.pack(side="left", expand=True, fill="both", padx=15, pady=12)
        ctk.CTkLabel(lock_box, text="Kunci (butuh password)", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w")
        ctk.CTkButton(lock_box, text="Kunci", width=220, command=lambda: self.action_lock(True)).pack(fill="x", pady=(8, 4))
        ctk.CTkButton(lock_box, text="Buka Kunci", width=220, command=lambda: self.action_lock(False)).pack(fill="x")

        sep2 = ttk.Separator(action_frame, orient="vertical")
        sep2.pack(side="left", fill="y", pady=12)

        combo_box = ctk.CTkFrame(action_frame, fg_color="transparent")
        combo_box.pack(side="left", expand=True, fill="both", padx=15, pady=12)
        ctk.CTkLabel(combo_box, text="Gabungan (butuh password)", font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w")
        ctk.CTkButton(combo_box, text="Kunci & Sembunyikan", width=220, command=self.action_lock_and_hide).pack(fill="x", pady=(8, 4))
        ctk.CTkButton(combo_box, text="Buka Kunci & Tampilkan", width=220, command=self.action_unlock_and_unhide).pack(fill="x")

    # -- theme --------------------------------------------------------------
    def on_theme_change(self, value):
        ctk.set_appearance_mode(value)
        self.config_data["theme"] = value
        save_config(self.config_data)
        self.apply_treeview_style()

    def apply_treeview_style(self):
        mode = ctk.get_appearance_mode()  # "Light" atau "Dark"
        style = ttk.Style()
        style.theme_use("clam")
        if mode == "Dark":
            bg, fg, field_bg, sel = "#2b2b2b", "#e8e8e8", "#242424", "#1f6aa5"
        else:
            bg, fg, field_bg, sel = "#f2f2f2", "#1a1a1a", "#ffffff", "#3a7ebf"
        style.configure("Treeview", background=field_bg, fieldbackground=field_bg, foreground=fg, rowheight=26, borderwidth=0)
        style.configure("Treeview.Heading", background=bg, foreground=fg, borderwidth=0)
        style.map("Treeview", background=[("selected", sel)], foreground=[("selected", "#ffffff")])

    # -- list -----------------------------------------------------------------
    def refresh_list(self):
        for row in self.tree.get_children():
            self.tree.delete(row)
        for entry in self.config_data.get("folders", []):
            hide_part, lock_part = status_text(entry)
            tag_part = entry.get("tag") or "-"
            self.tree.insert("", tk.END, iid=entry["path"], values=(entry["path"], hide_part, lock_part, tag_part))

    def get_selected_entries(self):
        paths = self.tree.selection()
        if not paths:
            messagebox.showwarning("Info", "Pilih satu atau beberapa folder di daftar terlebih dahulu.")
            return []
        folders = self.config_data.get("folders", [])
        return [e for e in folders if e["path"] in paths]

    def ask_master_password(self, title="Verifikasi Password") -> bool:
        pw = self.ask_password_dialog(title, "Masukkan password master:")
        if pw is None:
            return False
        if not verify_password(self.config_data, pw):
            messagebox.showerror("Error", "Password salah.")
            return False
        return True

    def show_result_summary(self, success, failed, verb_success, verb_failed):
        summary = f"{len(success)} folder {verb_success}."
        if failed:
            detail = "\n".join(f"- {p}: {err}" for p, err in failed)
            summary += f"\n\n{len(failed)} folder GAGAL {verb_failed}:\n{detail}"
            messagebox.showwarning("Selesai (sebagian gagal)", summary)
        else:
            messagebox.showinfo("Sukses", summary)

    def run_bulk_action(self, entries, worker, title, verb_success, verb_failed):
        """Jalankan `worker(entry)` untuk setiap entry di background thread
        sambil menampilkan progress bar, supaya UI tidak macet saat memproses
        folder besar (icacls/atribut pada folder besar bisa memakan waktu)."""
        progress = ctk.CTkToplevel(self)
        progress.title(title)
        W, H = 440, 150
        progress.geometry(f"{W}x{H}")
        center_window(progress, W, H)
        progress.transient(self)
        progress.grab_set()
        progress.protocol("WM_DELETE_WINDOW", lambda: None)  # cegah ditutup paksa saat proses

        status_var = tk.StringVar(value=f"Memproses 0 dari {len(entries)} folder...")
        ctk.CTkLabel(progress, textvariable=status_var, wraplength=400, justify="left").pack(pady=(20, 10), padx=20)
        bar = ctk.CTkProgressBar(progress, mode="indeterminate")
        bar.pack(fill="x", padx=20, pady=5)
        bar.start()
        ctk.CTkLabel(
            progress, text="Folder besar bisa memakan waktu beberapa saat, mohon tunggu.",
            text_color="#888888",
        ).pack(pady=(5, 0))

        result_queue = queue.Queue()

        def worker_thread():
            success, failed = [], []
            for i, entry in enumerate(entries, 1):
                result_queue.put(("progress", i, entry["path"]))
                try:
                    worker(entry)
                    success.append(entry["path"])
                except Exception as e:
                    failed.append((entry["path"], str(e)))
            result_queue.put(("done", success, failed))

        threading.Thread(target=worker_thread, daemon=True).start()

        def poll():
            try:
                while True:
                    item = result_queue.get_nowait()
                    if item[0] == "progress":
                        _, i, path = item
                        status_var.set(f"Memproses {i} dari {len(entries)}:\n{path}")
                    elif item[0] == "done":
                        _, success, failed = item
                        bar.stop()
                        progress.grab_release()
                        progress.destroy()
                        save_config(self.config_data)
                        self.refresh_list()
                        self.show_result_summary(success, failed, verb_success, verb_failed)
                        return
            except queue.Empty:
                pass
            progress.after(100, poll)

        progress.after(100, poll)

    # -- tambah folder (bisa banyak sekaligus) --------------------------------
    def open_add_dialog(self):
        existing = {e["path"] for e in self.config_data.get("folders", [])}

        dialog = ctk.CTkToplevel(self)
        dialog.title("Tambah Folder")
        W, H = 480, 380
        dialog.geometry(f"{W}x{H}")
        center_window(dialog, W, H)
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(
            dialog,
            text="Tambahkan satu atau beberapa folder ke daftar.\n"
                 "Folder hanya akan didaftarkan dulu (belum disembunyikan\n"
                 "atau dikunci) - pilih aksinya setelah muncul di daftar utama.",
            justify="left",
        ).pack(anchor="w", padx=15, pady=(15, 5))

        list_container = ctk.CTkFrame(dialog, fg_color="transparent")
        list_container.pack(fill="both", expand=True, padx=15)

        staging = tk.Listbox(list_container, selectmode=tk.EXTENDED)
        staging.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(list_container, orient="vertical", command=staging.yview)
        sb.pack(side="right", fill="y")
        staging.config(yscrollcommand=sb.set)

        staged_paths = []

        def add_folder():
            folder = filedialog.askdirectory(title="Pilih folder (bisa dipanggil berkali-kali)", parent=dialog)
            if not folder:
                return
            folder = os.path.normpath(folder)
            if folder in existing or folder in staged_paths:
                messagebox.showwarning("Info", "Folder ini sudah ada di daftar.", parent=dialog)
                return
            staged_paths.append(folder)
            staging.insert(tk.END, folder)

        def remove_staged():
            for idx in reversed(list(staging.curselection())):
                del staged_paths[idx]
                staging.delete(idx)

        def confirm_add():
            if not staged_paths:
                messagebox.showwarning("Info", "Belum ada folder yang ditambahkan.", parent=dialog)
                return
            for p in staged_paths:
                self.config_data.setdefault("folders", []).append({"path": p, "hidden": False, "locked": False, "tag": ""})
            save_config(self.config_data)
            self.refresh_list()
            dialog.destroy()

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill="x", padx=15, pady=15)
        ctk.CTkButton(btn_frame, text="+ Tambah Folder", command=add_folder).pack(side="left")
        ctk.CTkButton(btn_frame, text="Hapus dari Daftar Ini", command=remove_staged).pack(side="left", padx=8)
        ctk.CTkButton(btn_frame, text="Simpan ke Daftar Utama", command=confirm_add).pack(side="right")

    # -- aksi: sembunyikan / tampilkan (tanpa password) -----------------------
    def action_hide(self, hide: bool):
        entries = self.get_selected_entries()
        if not entries:
            return
        entries = [e for e in entries if e.get("hidden", False) != hide]
        if not entries:
            messagebox.showinfo("Info", "Semua folder terpilih sudah dalam status tampilan yang diminta.")
            return

        def worker(entry):
            set_folder_hidden(entry["path"], hide)
            entry["hidden"] = hide

        title = "Menyembunyikan Folder..." if hide else "Menampilkan Folder..."
        self.run_bulk_action(entries, worker, title, "berhasil diubah statusnya", "diubah")

    # -- aksi: kunci / buka kunci (butuh password) -----------------------------
    def action_lock(self, lock: bool):
        entries = self.get_selected_entries()
        if not entries:
            return
        entries = [e for e in entries if e.get("locked", False) != lock]
        if not entries:
            messagebox.showinfo("Info", "Semua folder terpilih sudah dalam status kunci yang diminta.")
            return
        title = "Konfirmasi Password untuk Mengunci" if lock else "Konfirmasi Password untuk Membuka Kunci"
        if not self.ask_master_password(title):
            return

        def worker(entry):
            if lock:
                lock_folder_access(entry["path"])
            else:
                unlock_folder_access(entry["path"])
            entry["locked"] = lock

        progress_title = "Mengunci Folder..." if lock else "Membuka Kunci Folder..."
        self.run_bulk_action(entries, worker, progress_title, "berhasil diubah status kuncinya", "diubah")

    # -- aksi gabungan ---------------------------------------------------------
    def action_lock_and_hide(self):
        entries = self.get_selected_entries()
        if not entries:
            return
        if not self.ask_master_password("Konfirmasi Password untuk Mengunci & Menyembunyikan"):
            return

        def worker(entry):
            # PENTING: sembunyikan dulu, baru kunci - bukan sebaliknya.
            # Izin "Modify" yang dipakai untuk mengunci turut memblokir hak
            # mengubah atribut folder (Write Attributes), jadi kalau folder
            # sudah dikunci lebih dulu, langkah menyembunyikan akan gagal
            # Access Denied.
            if not entry.get("hidden"):
                set_folder_hidden(entry["path"], True)
                entry["hidden"] = True
            if not entry.get("locked"):
                lock_folder_access(entry["path"])
                entry["locked"] = True

        self.run_bulk_action(
            entries, worker, "Mengunci & Menyembunyikan...",
            "berhasil dikunci & disembunyikan", "diproses",
        )

    def action_unlock_and_unhide(self):
        entries = self.get_selected_entries()
        if not entries:
            return
        if not self.ask_master_password("Konfirmasi Password untuk Membuka Semua"):
            return

        def worker(entry):
            # Urutan sebaliknya: buka kunci dulu (mengembalikan hak Write
            # Attributes), baru tampilkan kembali.
            if entry.get("locked"):
                unlock_folder_access(entry["path"])
                entry["locked"] = False
            if entry.get("hidden"):
                set_folder_hidden(entry["path"], False)
                entry["hidden"] = False

        self.run_bulk_action(
            entries, worker, "Membuka Semua...",
            "berhasil dibuka semua", "dibuka",
        )

    # -- tandai folder -----------------------------------------------------
    def open_tag_dialog(self):
        entries = self.get_selected_entries()
        if not entries:
            return

        box = ctk.CTkToplevel(self)
        box.title("Tandai Folder")
        W, H = 420, 240
        box.geometry(f"{W}x{H}")
        center_window(box, W, H)
        box.transient(self)
        box.grab_set()
        box.resizable(False, False)

        if len(entries) == 1:
            info_text = f"Beri tanda/label untuk:\n{entries[0]['path']}"
        else:
            info_text = f"Beri tanda/label yang sama untuk {len(entries)} folder terpilih."
        ctk.CTkLabel(box, text=info_text, wraplength=380, justify="left").pack(padx=20, pady=(20, 10))

        default_value = entries[0].get("tag", "") if len(entries) == 1 else ""
        entry = ctk.CTkEntry(box, width=340, placeholder_text="Contoh: Penting, Kerja, Pribadi")
        entry.pack(padx=20, pady=4)
        if default_value:
            entry.insert(0, default_value)
        entry.focus_set()

        def save():
            value = entry.get().strip()
            for e in entries:
                e["tag"] = value
            save_config(self.config_data)
            self.refresh_list()
            box.destroy()

        def clear_tag():
            for e in entries:
                e["tag"] = ""
            save_config(self.config_data)
            self.refresh_list()
            box.destroy()

        entry.bind("<Return>", lambda ev: save())

        btn_frame = ctk.CTkFrame(box, fg_color="transparent")
        btn_frame.pack(pady=18)
        ctk.CTkButton(btn_frame, text="Simpan", width=110, command=save).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="Hapus Tanda", width=110, fg_color="gray40", hover_color="gray30", command=clear_tag).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="Batal", width=110, fg_color="gray40", hover_color="gray30", command=box.destroy).pack(side="left", padx=5)

    # -- hapus dari daftar ------------------------------------------------------
    def remove_selected(self):
        entries = self.get_selected_entries()
        if not entries:
            return
        blocked = [e["path"] for e in entries if e.get("hidden") or e.get("locked")]
        if blocked:
            messagebox.showwarning(
                "Info",
                "Folder berikut masih tersembunyi/terkunci, buka dulu sebelum dihapus dari daftar:\n"
                + "\n".join(blocked),
            )
            return
        if not messagebox.askyesno("Konfirmasi", "Hapus folder terpilih dari daftar? (folder itu sendiri tidak akan dihapus)"):
            return
        remove_paths = {e["path"] for e in entries}
        self.config_data["folders"] = [e for e in self.config_data["folders"] if e["path"] not in remove_paths]
        save_config(self.config_data)
        self.refresh_list()

    # -- riwayat akses folder -------------------------------------------
    def show_access_history(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Riwayat Akses Folder")
        W, H = 760, 440
        dialog.geometry(f"{W}x{H}")
        center_window(dialog, W, H)
        dialog.transient(self)

        ctk.CTkLabel(
            dialog,
            text="Menampilkan folder/file yang baru-baru ini dibuka, berdasarkan data\n"
                 "yang sudah dicatat Windows sendiri (Recent Items & registry MRU).\n"
                 "Tidak mencakup akses lewat Command Prompt/PowerShell atau aplikasi\n"
                 "yang tidak memakai dialog Explorer standar.",
            justify="left",
        ).pack(anchor="w", padx=15, pady=(15, 5))

        columns = ("time", "name", "path", "type", "source")
        tree = ttk.Treeview(dialog, columns=columns, show="headings", height=13)
        tree.heading("time", text="Waktu")
        tree.heading("name", text="Nama")
        tree.heading("path", text="Lokasi")
        tree.heading("type", text="Tipe")
        tree.heading("source", text="Sumber")
        tree.column("time", width=130)
        tree.column("name", width=140)
        tree.column("path", width=260)
        tree.column("type", width=60, anchor="center")
        tree.column("source", width=140)
        tree.pack(fill="both", expand=True, padx=15)

        status_var = tk.StringVar(value="")
        ctk.CTkLabel(dialog, textvariable=status_var, text_color="#888888").pack(anchor="w", padx=15, pady=(6, 0))

        def refresh():
            for row in tree.get_children():
                tree.delete(row)
            if not IS_WINDOWS:
                status_var.set("Fitur ini hanya berfungsi di Windows.")
                return
            if not HAS_WIN32COM:
                status_var.set(
                    "Catatan: modul 'pywin32' belum terinstall, 'Recent Items' tidak bisa dibaca "
                    "(jalankan: pip install pywin32). Data registry MRU tetap tampil."
                )
            else:
                status_var.set("")
            items = get_folder_access_history()
            for item in items:
                waktu = item["time"].strftime("%Y-%m-%d %H:%M:%S") if item["time"] else "-"
                tipe = "Folder" if item["is_folder"] else "File"
                tree.insert("", tk.END, values=(waktu, item["name"], item["path"], tipe, item["source"]))
            if not items:
                status_var.set((status_var.get() + " Belum ada riwayat yang ditemukan.").strip())

        ctk.CTkButton(dialog, text="Refresh", command=refresh).pack(anchor="e", padx=15, pady=10)
        refresh()

    # -- ganti password -----------------------------------------------------
    def change_password(self):
        if not self.ask_master_password("Verifikasi Password Lama"):
            return
        pw = self.ask_password_dialog(
            "Password Baru", "Masukkan password master baru (minimal 4 karakter):", confirm=True
        )
        if pw is None:
            return
        set_master_password(self.config_data, pw)
        messagebox.showinfo("Sukses", "Password master berhasil diganti.")


def main():
    if not HAS_CTK:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Modul belum terinstall",
            "Vaultix butuh modul 'customtkinter' untuk tampilannya.\n\n"
            "Jalankan perintah ini di Command Prompt lalu buka lagi aplikasinya:\n"
            "pip install customtkinter",
        )
        sys.exit(1)

    app = VaultixApp()
    app.mainloop()


if __name__ == "__main__":
    main()
