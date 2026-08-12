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
import xml.etree.ElementTree as ET

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
    data.setdefault("activity_log", [])
    data.setdefault("pin_enabled", False)
    data.setdefault("pin_hash", None)
    data.setdefault("pin_salt", None)
    data.setdefault("auto_lock_enabled", False)
    data.setdefault("auto_lock_minutes", 5)
    data.setdefault("auto_lock_triggers", {
        "inactive": True, "lock": True, "focus_loss": False,
    })
    return data


def save_config(config):
    ensure_app_dir()
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


MAX_LOG_ENTRIES = 500


def log_event(config, message: str):
    """Catat satu kejadian ke Security Activity Log lalu simpan langsung."""
    entry = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "event": message}
    config.setdefault("activity_log", []).append(entry)
    if len(config["activity_log"]) > MAX_LOG_ENTRIES:
        config["activity_log"] = config["activity_log"][-MAX_LOG_ENTRIES:]
    save_config(config)


def hash_password(password: str, salt: bytes) -> str:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return dk.hex()


def evaluate_password_strength(password: str) -> str:
    length = len(password)
    variety = sum([
        any(c.islower() for c in password),
        any(c.isupper() for c in password),
        any(c.isdigit() for c in password),
        any(not c.isalnum() for c in password),
    ])
    if length >= 12 and variety >= 3:
        return "Kuat"
    if length >= 8 and variety >= 2:
        return "Sedang"
    return "Lemah"


def set_master_password(config, password: str):
    salt = secrets.token_bytes(16)
    config["salt"] = salt.hex()
    config["password_hash"] = hash_password(password, salt)
    config["password_strength"] = evaluate_password_strength(password)
    save_config(config)


def verify_password(config, password: str) -> bool:
    if not config.get("salt") or not config.get("password_hash"):
        return False
    salt = bytes.fromhex(config["salt"])
    return hash_password(password, salt) == config["password_hash"]


def set_pin(config, pin: str):
    salt = secrets.token_bytes(16)
    config["pin_salt"] = salt.hex()
    config["pin_hash"] = hash_password(pin, salt)
    config["pin_enabled"] = True
    save_config(config)


def verify_pin(config, pin: str) -> bool:
    if not config.get("pin_enabled") or not config.get("pin_hash") or not config.get("pin_salt"):
        return False
    salt = bytes.fromhex(config["pin_salt"])
    return hash_password(pin, salt) == config["pin_hash"]


def disable_pin(config):
    config["pin_enabled"] = False
    config["pin_hash"] = None
    config["pin_salt"] = None
    save_config(config)


# ---------------------------------------------------------------------------
# Auto Lock: deteksi idle time (Windows API GetLastInputInfo)
# ---------------------------------------------------------------------------

class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def get_idle_seconds() -> float:
    if not IS_WINDOWS:
        return 0.0
    lii = _LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
        return 0.0
    millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
    return millis / 1000.0


def is_session_locked() -> bool:
    """Deteksi 'komputer dikunci' dengan cara AMAN (polling biasa, tanpa
    menyentuh message loop Windows): cek apakah jendela foreground saat
    ini milik proses LogonUI.exe - itu proses yang menampilkan layar kunci
    Windows. Tidak akurat 100% di semua skenario, tapi tidak berisiko
    merusak memori seperti pendekatan WNDPROC subclassing sebelumnya."""
    if not IS_WINDOWS:
        return False
    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return False
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return False
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        hproc = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not hproc:
            return False
        try:
            buf = ctypes.create_unicode_buffer(260)
            size = ctypes.c_ulong(260)
            ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(size))
            if not ok:
                return False
            return os.path.basename(buf.value).lower() == "logonui.exe"
        finally:
            ctypes.windll.kernel32.CloseHandle(hproc)
    except Exception:
        return False


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
# Riwayat Lengkap (Windows Object Access Auditing + Security Event Log)
# ---------------------------------------------------------------------------
#
# Berbeda dari riwayat ringan di atas, mode ini benar-benar mencatat SETIAP
# percobaan akses (baca/tulis/hapus, berhasil/ditolak) ke folder yang
# diaudit - termasuk lewat Command Prompt/PowerShell/aplikasi apa pun.
# Konsekuensinya: butuh hak Administrator, harus diaktifkan manual per
# folder, dan bisa menghasilkan banyak entri log kalau folder sering
# diakses.

def is_admin() -> bool:
    if not IS_WINDOWS:
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def enable_object_access_audit_policy():
    """Aktifkan kategori audit 'File System' secara sistem (sekali saja,
    berlaku untuk seluruh komputer). Butuh Administrator."""
    result = subprocess.run(
        ["auditpol", "/set", "/subcategory:File System", "/success:enable", "/failure:enable"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "auditpol gagal").strip())


def set_folder_audit(path: str, enable: bool):
    """Pasang/lepas SACL audit pada folder tertentu untuk grup Everyone,
    mencatat baca/tulis/hapus, sukses maupun gagal."""
    if enable:
        args = ["icacls", path, "/setaudit", "Everyone:(OI)(CI)(S,F)(RD,WD,DE)"]
    else:
        args = ["icacls", path, "/removeaudit", "Everyone"]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "icacls /setaudit gagal").strip())


def get_audit_events(limit=300):
    """Baca event Object Access (Event ID 4663) dari Security Event Log."""
    if not IS_WINDOWS:
        return []
    cmd = [
        "wevtutil", "qe", "Security",
        "/q:*[System[(EventID=4663)]]",
        "/f:RenderedXml", f"/c:{limit}", "/rd:true",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, errors="ignore")
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or result.stdout or "Gagal membaca Security Event Log").strip()
            + "\n\nPastikan aplikasi dijalankan sebagai Administrator."
        )

    xml_text = "<Events>" + result.stdout.replace('<?xml version="1.0" encoding="UTF-8"?>', "") + "</Events>"
    events = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return events

    ns = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}
    for ev in root.findall("e:Event", ns):
        time_el = ev.find(".//e:TimeCreated", ns)
        entry = {"time": time_el.get("SystemTime") if time_el is not None else "-"}
        for d in ev.findall(".//e:EventData/e:Data", ns):
            name = d.get("Name")
            if name:
                entry[name] = (d.text or "").strip()
        events.append(entry)
    return events


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
        self.checked_paths = set()

        ctk.set_appearance_mode(self.config_data.get("theme", "System"))

        WIDTH, HEIGHT = 1020, 620
        self.geometry(f"{WIDTH}x{HEIGHT}")
        self.minsize(WIDTH, HEIGHT)
        center_window(self, WIDTH, HEIGHT)

        if not self.config_data.get("password_hash"):
            self.first_run_setup()

        self.build_ui()
        self.apply_treeview_style()
        self.refresh_list()
        log_event(self.config_data, "Aplikasi dibuka")

        # -- Auto Lock: mulai monitor idle, fokus, dan status lock (polling aman) --
        self.bind("<FocusOut>", self._on_focus_out)
        self._last_lock_state = False
        self._poll_idle()

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
        log_event(self.config_data, "Password master dibuat")
        messagebox.showinfo("Sukses", "Password master berhasil dibuat.")

    # -- UI ---------------------------------------------------------------
    def build_ui(self):
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        # -- sidebar --
        sidebar = ctk.CTkFrame(self, width=190, corner_radius=0)
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.grid_propagate(False)

        ctk.CTkLabel(sidebar, text="🗄 Vaultix", font=ctk.CTkFont(size=20, weight="bold")).pack(padx=20, pady=(25, 20), anchor="w")

        self.nav_buttons = {}
        nav_items = [
            ("folders", "📁 Folders"),
            ("dashboard", "📊 Dashboard"),
            ("activity", "🛡 Activity"),
            ("settings", "⚙ Settings"),
        ]
        for key, label in nav_items:
            btn = ctk.CTkButton(
                sidebar, text=label, anchor="w", width=160, height=38,
                fg_color="transparent",
                command=lambda k=key: self.show_view(k),
            )
            btn.pack(padx=15, pady=4)
            self.nav_buttons[key] = btn

        ctk.CTkLabel(sidebar, text="").pack(expand=True, fill="both")  # spacer

        ctk.CTkLabel(sidebar, text="Tema", text_color="#888888").pack(padx=20, anchor="w")
        self.theme_menu = ctk.CTkOptionMenu(
            sidebar, values=["System", "Light", "Dark"], command=self.on_theme_change, width=160
        )
        self.theme_menu.set(self.config_data.get("theme", "System"))
        self.theme_menu.pack(padx=15, pady=(2, 20))

        # -- area konten (4 view ditumpuk, ditukar lewat show_view) --
        content_container = ctk.CTkFrame(self, fg_color="transparent")
        content_container.grid(row=0, column=1, sticky="nsew")
        content_container.grid_rowconfigure(0, weight=1)
        content_container.grid_columnconfigure(0, weight=1)

        self.views = {}
        for key, _ in nav_items:
            frame = ctk.CTkFrame(content_container, fg_color="transparent")
            frame.grid(row=0, column=0, sticky="nsew")
            self.views[key] = frame

        self._build_folders_view(self.views["folders"])
        self._build_dashboard_view(self.views["dashboard"])
        self._build_activity_view(self.views["activity"])
        self._build_settings_view(self.views["settings"])

        self.show_view("folders")

    def show_view(self, key):
        self.views[key].tkraise()
        for k, btn in self.nav_buttons.items():
            btn.configure(fg_color=("gray75", "gray25") if k == key else "transparent")
        if key == "dashboard":
            self._build_dashboard_view(self.views["dashboard"])
        elif key == "activity":
            self.refresh_activity_log()

    # -- view: daftar folder + aksi -----------------------------------------
    def _build_folders_view(self, parent):
        parent.grid_rowconfigure(1, weight=1)
        parent.grid_columnconfigure(0, weight=1)

        toolbar = ctk.CTkFrame(parent, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 10))
        ctk.CTkLabel(toolbar, text="Daftar Folder", font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")
        ctk.CTkButton(toolbar, text="+ Tambah Folder", width=140, command=self.open_add_dialog).pack(side="left", padx=(20, 8))
        ctk.CTkButton(
            toolbar, text="Hapus dari Daftar", width=140, fg_color="#8a3b3b", hover_color="#6e2f2f",
            command=self.remove_selected,
        ).pack(side="left")
        ctk.CTkButton(toolbar, text="Pilih Semua", width=110, command=self.select_all).pack(side="right", padx=(6, 0))
        ctk.CTkButton(
            toolbar, text="Batal Pilih Semua", width=130, fg_color="gray40", hover_color="gray30",
            command=self.deselect_all,
        ).pack(side="right")

        list_frame = ctk.CTkFrame(parent)
        list_frame.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 10))

        tree_container = ctk.CTkFrame(list_frame, fg_color="transparent")
        tree_container.pack(fill="both", expand=True, padx=10, pady=10)

        columns = ("check", "path", "hidden", "locked")
        self.tree = ttk.Treeview(tree_container, columns=columns, show="headings", selectmode="none", height=10)
        self.tree.heading("check", text="✓")
        self.tree.heading("path", text="Folder")
        self.tree.heading("hidden", text="Status Tampilan")
        self.tree.heading("locked", text="Status Kunci")
        self.tree.column("check", width=40, anchor="center")
        self.tree.column("path", width=320)
        self.tree.column("hidden", width=140, anchor="center")
        self.tree.column("locked", width=140, anchor="center")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Button-1>", self.on_tree_click)

        sb = ttk.Scrollbar(tree_container, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)

        # -- tombol aksi --
        action_frame = ctk.CTkFrame(parent)
        action_frame.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 20))

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

    # -- view: pengaturan -----------------------------------------------------
    def _build_settings_view(self, parent):
        ctk.CTkLabel(parent, text="⚙ Settings", font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w", padx=25, pady=(20, 15))

        def setting_row(title, desc, btn_text, command, danger=False):
            box = ctk.CTkFrame(parent)
            box.pack(fill="x", padx=25, pady=6)
            inner = ctk.CTkFrame(box, fg_color="transparent")
            inner.pack(fill="x", padx=15, pady=12)
            text_col = ctk.CTkFrame(inner, fg_color="transparent")
            text_col.pack(side="left", fill="x", expand=True)
            ctk.CTkLabel(text_col, text=title, font=ctk.CTkFont(size=13, weight="bold"), anchor="w").pack(anchor="w")
            ctk.CTkLabel(text_col, text=desc, text_color="#888888", anchor="w", wraplength=460, justify="left").pack(anchor="w")
            kwargs = {"fg_color": "#8a3b3b", "hover_color": "#6e2f2f"} if danger else {}
            ctk.CTkButton(inner, text=btn_text, width=140, command=command, **kwargs).pack(side="right")

        setting_row(
            "Password Master", "Ganti password master yang dipakai untuk semua verifikasi.",
            "Ganti Password", self.change_password,
        )
        setting_row(
            "PIN", "Atur PIN 4-8 digit sebagai alternatif password master untuk verifikasi cepat.",
            "Atur PIN", self.open_pin_settings,
        )
        setting_row(
            "Riwayat Akses Folder", "Lihat folder/file yang baru-baru ini dibuka di perangkat ini (mode ringan & audit log lengkap).",
            "Buka", self.show_access_history,
        )

        # -- Auto Lock (punya switch on/off, bukan cuma tombol) --
        box = ctk.CTkFrame(parent)
        box.pack(fill="x", padx=25, pady=6)
        inner = ctk.CTkFrame(box, fg_color="transparent")
        inner.pack(fill="x", padx=15, pady=12)
        text_col = ctk.CTkFrame(inner, fg_color="transparent")
        text_col.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(text_col, text="Auto Lock", font=ctk.CTkFont(size=13, weight="bold"), anchor="w").pack(anchor="w")
        self.auto_lock_desc_var = tk.StringVar()
        ctk.CTkLabel(
            text_col, textvariable=self.auto_lock_desc_var, text_color="#888888",
            anchor="w", wraplength=440, justify="left",
        ).pack(anchor="w")
        self._update_auto_lock_desc()

        btn_col = ctk.CTkFrame(inner, fg_color="transparent")
        btn_col.pack(side="right")
        self.auto_lock_switch_var = tk.BooleanVar(value=self.config_data.get("auto_lock_enabled", False))
        ctk.CTkSwitch(
            btn_col, text="", variable=self.auto_lock_switch_var, command=self._toggle_auto_lock, width=40,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_col, text="Konfigurasi", width=110, command=self.open_auto_lock_settings).pack(side="left")

    # -- auto lock ------------------------------------------------------------
    def _trigger_auto_lock(self, reason: str):
        if not self.config_data.get("auto_lock_enabled"):
            return
        triggers = self.config_data.get("auto_lock_triggers", {})
        if not triggers.get(reason, False):
            return

        folders = self.config_data.get("folders", [])
        unlocked = [e for e in folders if not e.get("locked")]
        if not unlocked:
            return

        for entry in unlocked:
            try:
                if not entry.get("hidden"):
                    set_folder_hidden(entry["path"], True)
                    entry["hidden"] = True
                lock_folder_access(entry["path"])
                entry["locked"] = True
            except Exception:
                continue  # folder tertentu boleh gagal, lanjut ke folder lain

        reason_text = {
            "inactive": "tidak ada aktivitas", "lock": "komputer dikunci",
            "sleep": "komputer sleep", "logout": "user logout",
            "focus_loss": "aplikasi kehilangan fokus",
        }.get(reason, reason)
        log_event(self.config_data, f"Vault otomatis dikunci (alasan: {reason_text})")
        if hasattr(self, "tree"):
            self.refresh_list()

    def _poll_idle(self):
        try:
            if IS_WINDOWS and self.config_data.get("auto_lock_enabled"):
                triggers = self.config_data.get("auto_lock_triggers", {})

                if triggers.get("inactive"):
                    threshold = self.config_data.get("auto_lock_minutes", 5) * 60
                    if get_idle_seconds() >= threshold:
                        self._trigger_auto_lock("inactive")

                if triggers.get("lock"):
                    locked_now = is_session_locked()
                    if locked_now and not self._last_lock_state:
                        self._trigger_auto_lock("lock")
                    self._last_lock_state = locked_now
        except Exception:
            pass
        self.after(5000, self._poll_idle)  # cek tiap 5 detik, ringan (panggilan API biasa)

    def _on_focus_out(self, event):
        if event.widget != self:
            return
        self.after(150, self._check_focus_lost)

    def _check_focus_lost(self):
        try:
            if self.focus_get() is None:  # None berarti fokus benar-benar keluar dari aplikasi
                self._trigger_auto_lock("focus_loss")
        except Exception:
            pass

    def _update_auto_lock_desc(self):
        enabled = self.config_data.get("auto_lock_enabled", False)
        minutes = self.config_data.get("auto_lock_minutes", 5)
        triggers = self.config_data.get("auto_lock_triggers", {})
        labels = {
            "inactive": "tidak aktif", "lock": "komputer dikunci", "sleep": "komputer sleep",
            "logout": "logout", "focus_loss": "aplikasi kehilangan fokus",
        }
        active = [labels[k] for k in labels if triggers.get(k)]
        if enabled:
            text = f"Aktif — {minutes} menit tidak aktif. Pemicu: {', '.join(active) if active else '(belum ada dipilih)'}"
        else:
            text = "Nonaktif — folder yang sedang terbuka tidak akan dikunci otomatis."
        self.auto_lock_desc_var.set(text)

    def _toggle_auto_lock(self):
        self.config_data["auto_lock_enabled"] = self.auto_lock_switch_var.get()
        save_config(self.config_data)
        log_event(self.config_data, f"Auto Lock {'diaktifkan' if self.config_data['auto_lock_enabled'] else 'dinonaktifkan'}")
        self._update_auto_lock_desc()

    def open_auto_lock_settings(self):
        box = ctk.CTkToplevel(self)
        box.title("Konfigurasi Auto Lock")
        W, H = 440, 460
        box.geometry(f"{W}x{H}")
        center_window(box, W, H)
        box.transient(self)
        box.grab_set()
        box.resizable(False, False)

        ctk.CTkLabel(
            box, text="Kunci & sembunyikan ulang otomatis semua folder yang sedang\nterbuka, kalau kondisi berikut terpenuhi:",
            wraplength=400, justify="left",
        ).pack(padx=20, pady=(20, 10), anchor="w")

        minutes_frame = ctk.CTkFrame(box, fg_color="transparent")
        minutes_frame.pack(fill="x", padx=20, pady=(0, 10))
        ctk.CTkLabel(minutes_frame, text="Tidak ada aktivitas selama:").pack(side="left")
        minutes_entry = ctk.CTkEntry(minutes_frame, width=60)
        minutes_entry.insert(0, str(self.config_data.get("auto_lock_minutes", 5)))
        minutes_entry.pack(side="left", padx=8)
        ctk.CTkLabel(minutes_frame, text="menit").pack(side="left")

        triggers = self.config_data.get("auto_lock_triggers", {})
        trigger_vars = {}
        trigger_labels = [
            ("inactive", "Tidak ada aktivitas (idle) selama durasi di atas"),
            ("lock", "Komputer dikunci (Windows Lock)"),
            ("focus_loss", "Aplikasi Vaultix kehilangan fokus"),
        ]
        for key, label in trigger_labels:
            var = tk.BooleanVar(value=triggers.get(key, False))
            trigger_vars[key] = var
            ctk.CTkCheckBox(box, text=label, variable=var).pack(anchor="w", padx=20, pady=4)

        ctk.CTkLabel(
            box,
            text="Catatan: \"Komputer dikunci\" dideteksi lewat polling ringan "
                 "(cek jendela lock-screen tiap 5 detik) - aman, tidak menyentuh "
                 "message loop Windows. \"Sleep\" dan \"User logout\" belum "
                 "tersedia: cara deteksi paling aman untuk keduanya butuh integrasi "
                 "lebih dalam yang belum siap kami pasang tanpa risiko crash.",
            text_color="#888888", wraplength=400, justify="left",
        ).pack(anchor="w", padx=20, pady=(10, 0))

        def save():
            try:
                minutes = int(minutes_entry.get())
                if minutes < 1:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Error", "Durasi harus angka bulat minimal 1 menit.", parent=box)
                return
            self.config_data["auto_lock_minutes"] = minutes
            updated_triggers = dict(self.config_data.get("auto_lock_triggers", {}))
            updated_triggers.update({k: v.get() for k, v in trigger_vars.items()})
            self.config_data["auto_lock_triggers"] = updated_triggers
            save_config(self.config_data)
            log_event(self.config_data, "Pengaturan Auto Lock diperbarui")
            self._update_auto_lock_desc()
            box.destroy()

        ctk.CTkButton(box, text="Simpan", command=save).pack(pady=15)

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
        folders = self.config_data.get("folders", [])
        valid_paths = {e["path"] for e in folders}
        self.checked_paths &= valid_paths  # buang path yang sudah tidak ada di daftar
        for entry in folders:
            hide_part, lock_part = status_text(entry)
            check_symbol = "☑" if entry["path"] in self.checked_paths else "☐"
            self.tree.insert("", tk.END, iid=entry["path"], values=(check_symbol, entry["path"], hide_part, lock_part))

    def on_tree_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col = self.tree.identify_column(event.x)
        row = self.tree.identify_row(event.y)
        if not row or col != "#1":
            return
        if row in self.checked_paths:
            self.checked_paths.discard(row)
        else:
            self.checked_paths.add(row)
        self.tree.set(row, "check", "☑" if row in self.checked_paths else "☐")

    def select_all(self):
        self.checked_paths = {e["path"] for e in self.config_data.get("folders", [])}
        self.refresh_list()

    def deselect_all(self):
        self.checked_paths = set()
        self.refresh_list()

    def get_selected_entries(self):
        if not self.checked_paths:
            messagebox.showwarning("Info", "Centang satu atau beberapa folder di daftar terlebih dahulu.")
            return []
        folders = self.config_data.get("folders", [])
        return [e for e in folders if e["path"] in self.checked_paths]

    def ask_master_password(self, title="Verifikasi Password") -> bool:
        prompt = "Masukkan password master:"
        if self.config_data.get("pin_enabled"):
            prompt += "\n(atau PIN Anda)"
        pw = self.ask_password_dialog(title, prompt)
        if pw is None:
            return False
        if verify_password(self.config_data, pw):
            return True
        if self.config_data.get("pin_enabled") and verify_pin(self.config_data, pw):
            log_event(self.config_data, "Verifikasi berhasil menggunakan PIN")
            return True
        messagebox.showerror("Error", "Password/PIN salah.")
        log_event(self.config_data, "Percobaan password/PIN salah")
        return False

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

    # -- tambah folder (satu per satu, default) --------------------------------
    def open_add_dialog(self):
        folder = filedialog.askdirectory(title="Pilih folder yang ingin ditambahkan")
        if not folder:
            return
        folder = os.path.normpath(folder)

        existing = {e["path"] for e in self.config_data.get("folders", [])}
        if folder in existing:
            messagebox.showwarning("Info", "Folder ini sudah ada di daftar.")
            return

        self.config_data.setdefault("folders", []).append({"path": folder, "hidden": False, "locked": False})
        save_config(self.config_data)
        self.refresh_list()

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
            log_event(self.config_data, f"Folder {'disembunyikan' if hide else 'ditampilkan'}: {entry['path']}")

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
            log_event(self.config_data, f"Folder {'dikunci' if lock else 'dibuka kuncinya'}: {entry['path']}")

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
            log_event(self.config_data, f"Folder dikunci & disembunyikan: {entry['path']}")

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
            log_event(self.config_data, f"Folder dibuka kunci & ditampilkan: {entry['path']}")

        self.run_bulk_action(
            entries, worker, "Membuka Semua...",
            "berhasil dibuka semua", "dibuka",
        )

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

    # -- dashboard security status ----------------------------------------
    def _build_dashboard_view(self, parent):
        for w in parent.winfo_children():
            w.destroy()

        folders = self.config_data.get("folders", [])
        total = len(folders)
        locked_count = sum(1 for f in folders if f.get("locked"))
        hidden_count = sum(1 for f in folders if f.get("hidden"))

        pw_strength = self.config_data.get("password_strength", "Tidak diketahui")
        pw_color = {"Kuat": "green", "Sedang": "yellow"}.get(pw_strength, "red")

        score = 0
        score += {"Kuat": 2, "Sedang": 1}.get(pw_strength, 0)
        if total > 0 and locked_count == total:
            score += 2
        elif locked_count > 0:
            score += 1
        if score >= 4:
            vault_label, vault_color = "Kuat", "green"
        elif score >= 2:
            vault_label, vault_color = "Sedang", "yellow"
        else:
            vault_label, vault_color = "Lemah", "red"

        def status_row(box, label, value, color, note=None):
            dot = {"green": "🟢", "yellow": "🟡", "red": "🔴"}[color]
            row = ctk.CTkFrame(box, fg_color="transparent")
            row.pack(fill="x", pady=3)
            ctk.CTkLabel(row, text=f"{dot} {label}", anchor="w", width=190, font=ctk.CTkFont(size=13)).pack(side="left")
            ctk.CTkLabel(row, text=value, anchor="w", font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
            if note:
                ctk.CTkLabel(box, text=note, text_color="#888888", wraplength=520, justify="left").pack(anchor="w", padx=(28, 0), pady=(0, 4))

        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.pack(fill="x", padx=25, pady=(20, 10))
        ctk.CTkLabel(header, text="SECURITY STATUS", font=ctk.CTkFont(size=18, weight="bold")).pack(side="left")
        ctk.CTkButton(header, text="Refresh", width=90, command=lambda: self._build_dashboard_view(parent)).pack(side="right")

        status_box = ctk.CTkFrame(parent)
        status_box.pack(fill="x", padx=25, pady=(0, 15))
        inner = ctk.CTkFrame(status_box, fg_color="transparent")
        inner.pack(fill="x", padx=15, pady=15)

        status_row(inner, "Vault Security", vault_label, vault_color)
        status_row(
            inner, "Encryption", "Nonaktif (mode: Kunci NTFS)", "red",
            note="Bukan enkripsi sungguhan - lihat 'Mode Encryption (AES-256)' di roadmap #7, belum tersedia.",
        )
        status_row(inner, "Password", pw_strength, pw_color)
        auto_lock_on = self.config_data.get("auto_lock_enabled", False)
        auto_lock_minutes = self.config_data.get("auto_lock_minutes", 5)
        status_row(
            inner, "Auto Lock",
            f"{auto_lock_minutes} menit" if auto_lock_on else "Nonaktif",
            "green" if auto_lock_on else "red",
        )
        status_row(
            inner, "Clipboard Protection", "Belum direncanakan", "red",
            note="Ada di roadmap #12, belum dikerjakan.",
        )

        ctk.CTkLabel(parent, text="Folder", font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", padx=25, pady=(0, 0))
        ctk.CTkLabel(
            parent, text=f"{locked_count} dari {total} folder terkunci · {hidden_count} dari {total} tersembunyi",
            text_color="#888888",
        ).pack(anchor="w", padx=25, pady=(0, 8))

        list_box = ctk.CTkScrollableFrame(parent)
        list_box.pack(fill="both", expand=True, padx=25, pady=(0, 20))

        if not folders:
            ctk.CTkLabel(list_box, text="Belum ada folder di daftar.", text_color="#888888").pack(anchor="w", pady=10)
        else:
            for entry in folders:
                row = ctk.CTkFrame(list_box, fg_color="transparent")
                row.pack(fill="x", pady=2)
                name = os.path.basename(entry["path"].rstrip("\\/")) or entry["path"]
                icon = "🔒" if entry.get("locked") else "🔓"
                ctk.CTkLabel(row, text=name, anchor="w", width=280).pack(side="left")
                ctk.CTkLabel(row, text=icon, anchor="e").pack(side="left")

    # -- security activity log -------------------------------------------
    def _build_activity_view(self, parent):
        ctk.CTkLabel(
            parent, text="🛡 Aktivitas Keamanan", font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w", padx=25, pady=(20, 5))
        ctk.CTkLabel(
            parent,
            text="Catatan kejadian keamanan Vaultix: password diverifikasi/salah,\n"
                 "folder dikunci/dibuka, dan aksi lainnya. Disimpan lokal di perangkat ini.",
            justify="left", text_color="#888888",
        ).pack(anchor="w", padx=25, pady=(0, 10))

        columns = ("time", "event")
        self.activity_tree = ttk.Treeview(parent, columns=columns, show="headings", height=16)
        self.activity_tree.heading("time", text="Waktu")
        self.activity_tree.heading("event", text="Kejadian")
        self.activity_tree.column("time", width=160)
        self.activity_tree.column("event", width=500)
        self.activity_tree.pack(fill="both", expand=True, padx=25)

        def clear_log():
            if not messagebox.askyesno("Konfirmasi", "Hapus seluruh riwayat aktivitas keamanan?"):
                return
            self.config_data["activity_log"] = []
            save_config(self.config_data)
            self.refresh_activity_log()

        btn_row = ctk.CTkFrame(parent, fg_color="transparent")
        btn_row.pack(fill="x", padx=25, pady=15)
        ctk.CTkButton(btn_row, text="Refresh", width=90, command=self.refresh_activity_log).pack(side="right")
        ctk.CTkButton(
            btn_row, text="Bersihkan Log", width=120, fg_color="gray40", hover_color="gray30", command=clear_log,
        ).pack(side="right", padx=8)

    def refresh_activity_log(self):
        for row in self.activity_tree.get_children():
            self.activity_tree.delete(row)
        entries = list(reversed(self.config_data.get("activity_log", [])))
        for entry in entries:
            self.activity_tree.insert("", tk.END, values=(entry["time"], entry["event"]))

    # -- riwayat akses folder -------------------------------------------
    def show_access_history(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Riwayat Akses Folder")
        W, H = 820, 560
        dialog.geometry(f"{W}x{H}")
        center_window(dialog, W, H)
        dialog.transient(self)

        tabs = ctk.CTkTabview(dialog, width=W - 30, height=H - 30)
        tabs.pack(fill="both", expand=True, padx=10, pady=10)
        tab_light = tabs.add("Riwayat Ringan (Bawaan Windows)")
        tab_full = tabs.add("Riwayat Lengkap (Audit Log)")

        self._build_light_history_tab(tab_light)
        self._build_full_history_tab(tab_full)

    def _build_light_history_tab(self, tab):
        ctk.CTkLabel(
            tab,
            text="Menampilkan folder/file yang baru-baru ini dibuka, berdasarkan data\n"
                 "yang sudah dicatat Windows sendiri (Recent Items & registry MRU).\n"
                 "Ringan (tanpa admin, tanpa setup), tapi TIDAK mencakup akses lewat\n"
                 "Command Prompt/PowerShell atau aplikasi non-Explorer, dan tidak\n"
                 "mencatat siapa/proses apa yang mengakses.",
            justify="left",
        ).pack(anchor="w", padx=10, pady=(10, 5))

        columns = ("time", "name", "path", "type", "source")
        tree = ttk.Treeview(tab, columns=columns, show="headings", height=13)
        tree.heading("time", text="Waktu")
        tree.heading("name", text="Nama")
        tree.heading("path", text="Lokasi")
        tree.heading("type", text="Tipe")
        tree.heading("source", text="Sumber")
        tree.column("time", width=130)
        tree.column("name", width=140)
        tree.column("path", width=250)
        tree.column("type", width=60, anchor="center")
        tree.column("source", width=140)
        tree.pack(fill="both", expand=True, padx=10)

        status_var = tk.StringVar(value="")
        ctk.CTkLabel(tab, textvariable=status_var, text_color="#888888").pack(anchor="w", padx=10, pady=(6, 0))

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

        ctk.CTkButton(tab, text="Refresh", command=refresh).pack(anchor="e", padx=10, pady=10)
        refresh()

    def _build_full_history_tab(self, tab):
        admin_ok = is_admin()
        warn_text = (
            "Mode ini mencatat SETIAP percobaan akses (baca/tulis/hapus, berhasil\n"
            "atau ditolak) ke folder yang Anda aktifkan auditnya - termasuk lewat\n"
            "Command Prompt/PowerShell/aplikasi apa pun. Butuh Administrator, harus\n"
            "diaktifkan manual per folder, dan bisa menghasilkan banyak entri log\n"
            "kalau folder sering diakses."
        )
        if not admin_ok:
            warn_text += "\n\n⚠ Aplikasi TIDAK sedang berjalan sebagai Administrator - fitur di tab ini tidak akan berfungsi. Tutup aplikasi, klik kanan main.py/Vaultix.exe, pilih \"Run as administrator\"."

        ctk.CTkLabel(tab, text=warn_text, justify="left", text_color=("#8a5b00" if admin_ok else "#c0392b")).pack(
            anchor="w", padx=10, pady=(10, 5)
        )

        control_row = ctk.CTkFrame(tab, fg_color="transparent")
        control_row.pack(fill="x", padx=10, pady=(0, 5))

        def on_enable():
            entries = self.get_selected_entries()
            if not entries:
                return
            if not admin_ok:
                messagebox.showerror("Butuh Administrator", "Jalankan Vaultix sebagai Administrator dulu.")
                return
            try:
                enable_object_access_audit_policy()
            except Exception as e:
                messagebox.showerror("Gagal", f"Tidak bisa mengaktifkan kebijakan audit:\n{e}")
                return
            success, failed = [], []
            for entry in entries:
                try:
                    set_folder_audit(entry["path"], True)
                    success.append(entry["path"])
                except Exception as e:
                    failed.append((entry["path"], str(e)))
            self.show_result_summary(success, failed, "berhasil diaktifkan auditnya", "diaktifkan")

        def on_disable():
            entries = self.get_selected_entries()
            if not entries:
                return
            if not admin_ok:
                messagebox.showerror("Butuh Administrator", "Jalankan Vaultix sebagai Administrator dulu.")
                return
            success, failed = [], []
            for entry in entries:
                try:
                    set_folder_audit(entry["path"], False)
                    success.append(entry["path"])
                except Exception as e:
                    failed.append((entry["path"], str(e)))
            self.show_result_summary(success, failed, "berhasil dinonaktifkan auditnya", "dinonaktifkan")

        ctk.CTkButton(control_row, text="Aktifkan Audit untuk Folder Tercentang", width=260, command=on_enable).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            control_row, text="Nonaktifkan Audit untuk Folder Tercentang", width=260,
            fg_color="gray40", hover_color="gray30", command=on_disable,
        ).pack(side="left")

        columns = ("time", "user", "path", "access", "process", "result")
        tree = ttk.Treeview(tab, columns=columns, show="headings", height=11)
        headings = {
            "time": "Waktu", "user": "User", "path": "Folder/File",
            "access": "Jenis Akses", "process": "Proses", "result": "Hasil",
        }
        widths = {"time": 130, "user": 100, "path": 240, "access": 140, "process": 130, "result": 70}
        for col in columns:
            tree.heading(col, text=headings[col])
            tree.column(col, width=widths[col], anchor="center" if col in ("result",) else "w")
        tree.pack(fill="both", expand=True, padx=10, pady=(8, 0))

        status_var = tk.StringVar(value="Klik \"Refresh Log Audit\" untuk memuat data (hanya menampilkan folder yang sudah diaktifkan auditnya).")
        ctk.CTkLabel(tab, textvariable=status_var, text_color="#888888", wraplength=760, justify="left").pack(anchor="w", padx=10, pady=(6, 0))

        def refresh_log():
            for row in tree.get_children():
                tree.delete(row)
            if not admin_ok:
                status_var.set("Butuh Administrator untuk membaca Security Event Log.")
                return
            try:
                events = get_audit_events()
            except Exception as e:
                status_var.set(f"Gagal membaca log: {e}")
                return
            audited_paths = [e["path"] for e in self.config_data.get("folders", [])]
            shown = 0
            for ev in events:
                obj_name = ev.get("ObjectName", "")
                if audited_paths and not any(obj_name.lower().startswith(p.lower()) for p in audited_paths):
                    continue
                waktu = ev.get("time", "-")
                user = ev.get("SubjectUserName", "-")
                access = ev.get("AccessList", "-").strip().replace("\r\n", " ").replace("\t", " ")
                process = ev.get("ProcessName", "-")
                tree.insert("", tk.END, values=(waktu, user, obj_name, access, process, "Event"))
                shown += 1
            status_var.set(f"Menampilkan {shown} kejadian (dari folder yang sudah diaktifkan auditnya).")

        ctk.CTkButton(tab, text="Refresh Log Audit", command=refresh_log).pack(anchor="e", padx=10, pady=10)

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
        log_event(self.config_data, "Password master diganti")
        messagebox.showinfo("Sukses", "Password master berhasil diganti.")

    # -- atur PIN -----------------------------------------------------------
    def open_pin_settings(self):
        if not self.ask_master_password("Verifikasi Password Master"):
            return

        box = ctk.CTkToplevel(self)
        box.title("Atur PIN")
        W, H = 400, 300
        box.geometry(f"{W}x{H}")
        center_window(box, W, H)
        box.transient(self)
        box.grab_set()
        box.resizable(False, False)

        status = "Aktif" if self.config_data.get("pin_enabled") else "Nonaktif"
        ctk.CTkLabel(box, text=f"Status PIN saat ini: {status}", font=ctk.CTkFont(weight="bold")).pack(padx=20, pady=(20, 10))
        ctk.CTkLabel(
            box, text="PIN adalah alternatif password yang lebih pendek (4-8 digit\nangka) untuk verifikasi cepat kunci/buka kunci folder.",
            wraplength=360, justify="left", text_color="#888888",
        ).pack(padx=20, pady=(0, 10))

        entry1 = ctk.CTkEntry(box, show="*", width=300, placeholder_text="PIN baru (4-8 digit angka)")
        entry1.pack(padx=20, pady=4)
        entry2 = ctk.CTkEntry(box, show="*", width=300, placeholder_text="Ulangi PIN baru")
        entry2.pack(padx=20, pady=4)

        error_var = tk.StringVar(value="")
        ctk.CTkLabel(box, textvariable=error_var, text_color="#e05555", wraplength=360).pack(pady=(6, 0))

        def save_pin():
            pin1 = entry1.get().strip()
            pin2 = entry2.get().strip()
            if not pin1.isdigit() or not (4 <= len(pin1) <= 8):
                error_var.set("PIN harus berupa angka, panjang 4-8 digit.")
                return
            if pin1 != pin2:
                error_var.set("PIN tidak cocok, coba lagi.")
                return
            set_pin(self.config_data, pin1)
            log_event(self.config_data, "PIN diaktifkan/diperbarui")
            messagebox.showinfo("Sukses", "PIN berhasil disimpan.", parent=box)
            box.destroy()

        def remove_pin():
            if not self.config_data.get("pin_enabled"):
                messagebox.showinfo("Info", "PIN memang belum aktif.", parent=box)
                return
            if not messagebox.askyesno("Konfirmasi", "Nonaktifkan PIN? Anda hanya bisa memakai password master setelah ini.", parent=box):
                return
            disable_pin(self.config_data)
            log_event(self.config_data, "PIN dinonaktifkan")
            messagebox.showinfo("Sukses", "PIN dinonaktifkan.", parent=box)
            box.destroy()

        btn_frame = ctk.CTkFrame(box, fg_color="transparent")
        btn_frame.pack(pady=15)
        ctk.CTkButton(btn_frame, text="Simpan PIN", width=110, command=save_pin).pack(side="left", padx=5)
        ctk.CTkButton(
            btn_frame, text="Nonaktifkan PIN", width=130, fg_color="gray40", hover_color="gray30", command=remove_pin,
        ).pack(side="left", padx=5)


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
