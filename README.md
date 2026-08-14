# Vaultix (Windows)

Aplikasi desktop untuk mendaftarkan folder pilihan, lalu mengelola dua
aksi independen terhadapnya: **Sembunyikan** dan **Kunci** — keduanya bisa
diulang-ulang kapan saja, tidak sekali pakai.

## Konsep Utama

1. **Daftar Folder** — folder yang Anda tambahkan satu per satu lewat
   "+ Tambah Folder" akan selalu ada di daftar ini, apa pun statusnya.
   Menambah folder ke daftar TIDAK langsung menyembunyikan/mengunci apa
   pun — itu aksi terpisah yang Anda pilih sendiri setelahnya.
2. **Checkbox seleksi** — setiap baris punya kotak centang (☐/☑) di
   kolom paling kiri. Centang folder yang ingin dikenai aksi, atau pakai
   tombol **Pilih Semua** / **Batal Pilih Semua** di atas tabel.
3. **Sembunyikan / Tampilkan** (tanpa password) — folder diberi/dihapus
   atribut Windows `Hidden + System`, sehingga hilang/muncul lagi dari
   File Explorer. Sifatnya cuma kerapian tampilan, cepat, reversible
   kapan saja.
4. **Kunci / Buka Kunci** (butuh password master) — folder diberi/dihapus
   izin **Deny (Modify)** lewat `icacls` (NTFS permission) untuk akun
   Windows Anda saat ini, sehingga folder benar-benar tidak bisa dibuka
   ("Access is denied") sampai dibuka lagi lewat Vaultix dengan password
   yang benar. Ini proteksi akses sungguhan, bukan sekadar sembunyi —
   tapi tetap **bukan enkripsi**: isi file tidak diubah sama sekali.
5. **Kunci & Sembunyikan** / **Buka Kunci & Tampilkan** — tombol gabungan
   untuk melakukan keduanya sekaligus dengan satu kali password. Urutan
   internalnya penting dan sudah ditangani otomatis: menyembunyikan dulu
   baru mengunci (karena izin kunci turut memblokir hak ubah atribut),
   dan sebaliknya saat membuka.

Password disimpan sebagai hash (PBKDF2-HMAC-SHA256 + salt) di
`%USERPROFILE%\.vaultix\config.json`, bukan sebagai teks biasa.

## Riwayat Perbaikan Penting

- **Izin kunci diganti dari Full Control ke Modify** — versi awal memakai
  `icacls ... /deny user:F` yang ikut memblokir hak mengubah izin folder
  itu sendiri (self-lockout risk). Sekarang pakai `M` (Modify), yang
  tetap memblokir akses isi folder tapi tidak memblokir hak membuka
  kunci lagi.
- **Urutan Sembunyikan sebelum Kunci** — pada aksi gabungan, folder
  disembunyikan dulu baru dikunci (dan sebaliknya saat membuka), karena
  izin kunci (Modify) turut memblokir hak "Write Attributes" yang
  dibutuhkan untuk mengubah status sembunyi.
- **Progress bar** — setiap aksi berjalan di background thread dengan
  jendela progress bar, supaya UI tidak macet saat memproses folder
  besar (operasi `icacls` rekursif bisa memakan waktu).
- **Dialog password bertema** — dialog buat/verifikasi/ganti password
  sudah pakai tampilan CustomTkinter, ikut mode Light/Dark, bukan lagi
  jendela Tkinter polos.
- **UI dirapikan** — jendela diperlebar, tombol aksi disusun bertumpuk
  vertikal per kategori supaya tidak ada teks tombol yang terpotong.
- **Seleksi via checkbox** — menggantikan seleksi Ctrl/Shift-klik baris.
  Ada tombol Pilih Semua / Batal Pilih Semua.
- **Tambah folder kembali ke default satu per satu** — sempat ada dialog
  staging & browser multi-folder custom, tapi atas permintaan dibalikkan
  ke perilaku sederhana: klik "+ Tambah Folder" langsung membuka dialog
  pilih folder bawaan Windows (satu folder per klik). Untuk menambahkan
  beberapa folder, klik tombolnya berkali-kali.

## Perbaikan Penting #6 (Riwayat Akses Folder jadi 2 mode)

"Riwayat Akses Folder" sekarang punya 2 tab:

**Tab 1 - Riwayat Ringan (Bawaan Windows)**
Sama seperti sebelumnya: baca Recent Items & registry MRU. Tanpa admin,
tanpa setup, tapi cakupannya terbatas dan tidak ada info user/proses.

**Tab 2 - Riwayat Lengkap (Audit Log)**
Menggunakan Windows Object Access Auditing (fitur keamanan bawaan
Windows) + Security Event Log:
- Mencatat SETIAP percobaan akses (baca/tulis/hapus, sukses/gagal) ke
  folder yang auditnya diaktifkan - termasuk lewat Command
  Prompt/PowerShell/aplikasi apa pun, bukan cuma Explorer.
- Menampilkan waktu presisi, nama user Windows, nama proses, dan jenis
  akses.
- **Wajib dijalankan sebagai Administrator** (klik kanan → "Run as
  administrator"), dan audit harus **diaktifkan manual per folder**
  lewat tombol "Aktifkan Audit untuk Folder Tercentang" di tab ini
  (pakai checkbox di daftar folder utama untuk memilih folder mana).
- Bisa dinonaktifkan lagi lewat "Nonaktifkan Audit untuk Folder
  Tercentang".
- **Perhatian**: folder yang sering diakses akan menghasilkan banyak
  entri log di Security Event Log Windows (bukan cuma di Vaultix) -
  aktifkan hanya untuk folder yang benar-benar perlu dipantau ketat.

## Roadmap Vaultix 1.2

Fitur-fitur besar berikut sedang dikerjakan bertahap, dari yang paling
mudah ke paling kompleks:

1. ✅ **Security Activity Log** — selesai.
2. ✅ **Dashboard Security Status** — selesai.
3. ✅ **PIN sebagai alternatif password** — selesai (lihat di bawah).
4. ✅ **Auto Lock berdasarkan aktivitas** — selesai (lihat di bawah).
5. ✅ **Multiple Vault** — selesai (lihat di bawah).
6. ⏸ **Windows Hello / Fingerprint / Face** — DITUNDA, dinonaktifkan
   sementara. Lihat penjelasan lengkap di bawah.
7. ✅ **Mode Encryption terpisah** — selesai (lihat di bawah).
8. ⬜ Screenshot/Recording Protection — **catatan penting**: hanya bisa
   melindungi jendela Vaultix sendiri, TIDAK bisa mencegah screenshot
   File Explorer asli. Baru berguna penuh kalau dipasangkan dengan file
   browser custom di dalam aplikasi.
9. ⬜ Decoy Vault — butuh Mode Encryption selesai dulu sebagai fondasi.
10. ⬜ Security Key (FIDO2) — tahap lanjutan/eksperimental.
11. ⬜ Mobile Remote Lock — **butuh infrastruktur terpisah** (backend
    server, API, push notification, aplikasi mobile). Di luar cakupan
    aplikasi desktop lokal ini.
12. ⬜ Clipboard Protection — ditambahkan atas konfirmasi Anda, belum
    dikerjakan.

## Fitur Baru: Security Activity Log

Tombol **"🛡 Aktivitas Keamanan"** menampilkan catatan kejadian
keamanan, tersimpan lokal di `config.json` (maksimal 500 entri
terakhir, entri lama otomatis dibuang). Kejadian yang dicatat saat ini:
- Aplikasi dibuka
- Password master dibuat / diganti / diverifikasi (gagal → "Percobaan
  password salah")
- Folder disembunyikan / ditampilkan
- Folder dikunci / dibuka kuncinya
- Aksi gabungan (kunci & sembunyikan / buka kunci & tampilkan)

Bisa dibersihkan lewat tombol "Bersihkan Log" di jendela yang sama.
Log ini akan jadi fondasi untuk fitur Auto Lock dan Dashboard di
tahap berikutnya.

## Fitur Baru: Dashboard Security Status

Tombol **"📊 Dashboard"** menampilkan ringkasan status keamanan.
**Prinsip penting**: dashboard ini menampilkan status **apa adanya** -
tidak berpura-pura menampilkan fitur yang belum ada.

- **Vault Security** — skor komposit dari kekuatan password + rasio
  folder yang terkunci (Kuat/Sedang/Lemah).
- **Encryption** — saat ini selalu "Nonaktif (mode: Kunci NTFS)" karena
  Vaultix belum punya mode enkripsi sungguhan (ada di roadmap #7).
- **Password** — kekuatan password master (Lemah/Sedang/Kuat), dihitung
  dari panjang & variasi karakter saat password dibuat/diganti.
- **Auto Lock** — saat ini selalu "Nonaktif" (roadmap #4, belum
  tersedia).
- **Clipboard Protection** — "Belum direncanakan". Ini muncul di contoh
  dashboard yang Anda berikan tapi belum ada di daftar 11 fitur roadmap
  — beri tahu kalau memang ingin ditambahkan sebagai fitur baru.
- **Folder** — daftar semua folder dengan ikon 🔒/🔓 sesuai statusnya
  (menggantikan konsep "Vaults" bernama di contoh Anda, karena Multiple
  Vault baru ada di roadmap #5).

## Fitur Baru: PIN sebagai Alternatif Password

Tombol **"🔢 Atur PIN"** (butuh verifikasi password master dulu untuk
mengaksesnya). PIN adalah kode 4-8 digit angka yang bisa dipakai
**sebagai pengganti** password master di semua dialog verifikasi
(kunci/buka kunci folder, dll) — password master tetap selalu bisa
dipakai juga, PIN hanya alternatif tambahan yang lebih cepat diketik.

- Disimpan sebagai hash terpisah (PBKDF2-HMAC-SHA256 + salt sendiri),
  sama seperti password — tidak disimpan sebagai teks biasa.
- Bisa dinonaktifkan kapan saja lewat tombol "Nonaktifkan PIN".
- Semua aktivasi/perubahan/nonaktivasi PIN tercatat di Security
  Activity Log.
- **Catatan keamanan**: PIN yang pendek (4 digit) secara matematis
  lebih mudah ditebak daripada password panjang — cocok untuk
  kenyamanan sehari-hari di perangkat pribadi, tapi kalau butuh
  keamanan maksimal, tetap andalkan password master yang kuat.

## Perbaikan Penting #7 (Restrukturisasi UI: layout Sidebar)

Sebelumnya 7 tombol (Tambah Folder, Dashboard, Hapus, Riwayat Akses,
Aktivitas Keamanan, Atur PIN, Ganti Password) numpuk jadi satu baris di
atas daftar folder — makin banyak fitur ditambahkan, makin sesak dan
kaku. Sekarang aplikasi dirombak jadi **layout sidebar** (pola aplikasi
modern), dengan 4 halaman:

- **📁 Folders** — daftar folder + tombol Tambah/Hapus + Pilih Semua di
  satu toolbar rapi, plus panel aksi (Sembunyikan/Kunci/Gabungan) di
  bawahnya. Ini halaman utama/default saat aplikasi dibuka.
- **📊 Dashboard** — Security Status, otomatis refresh setiap kali
  halaman ini dibuka.
- **🛡 Activity** — Security Activity Log, otomatis refresh setiap kali
  halaman ini dibuka.
- **⚙ Settings** — Ganti Password, Atur PIN, dan akses ke Riwayat Akses
  Folder, masing-masing sebagai kartu terpisah dengan deskripsi singkat.

Pemilihan tema Light/Dark/System dipindah ke bagian bawah sidebar (tetap
selalu terlihat di halaman mana pun). Jendela diperlebar untuk
menampung sidebar.

## Fitur Baru: Auto Lock

Kartu **"Auto Lock"** di halaman Settings, dengan switch on/off dan
tombol "Konfigurasi" untuk mengatur durasi & pemicu. Saat aktif dan
kondisi terpenuhi, **semua folder yang sedang terbuka** otomatis
dikunci & disembunyikan lagi (tanpa perlu password, karena ini aksi
otomatis tanpa kehadiran user) — persis seperti aksi "Kunci &
Sembunyikan" tapi berjalan sendiri.

Tiga pemicu yang bisa dicentang (bisa lebih dari satu sekaligus):

| Pemicu | Cara deteksi |
|---|---|
| Tidak ada aktivitas (idle) | Windows API `GetLastInputInfo`, dicek tiap 5 detik |
| Aplikasi kehilangan fokus | Event `<FocusOut>` Tkinter + pengecekan fokus aplikasi |
| Komputer dikunci (Windows Lock) | Polling ringan tiap 5 detik: cek apakah jendela lock-screen (`LogonUI.exe`) sedang di depan |

**"Sleep/hibernate" dan "User logout" belum tersedia** — lihat catatan
hotfix di bawah.

### ⚠ Hotfix: WNDPROC subclassing dicabut (berisiko crash)

Versi awal fitur ini memakai teknik **WNDPROC subclassing** (membajak
pesan Windows `WM_WTSSESSION_CHANGE`/`WM_POWERBROADCAST`/
`WM_QUERYENDSESSION`) untuk mendeteksi lock/sleep/logout. Ternyata ada
bug: nilai kembalian `SetWindowLongPtrW` (sebuah pointer 64-bit) tidak
diberi tipe data yang benar di `ctypes`, sehingga pointer-nya terpotong
dan menyebabkan **access violation** yang membanjiri konsol setiap kali
ada pesan Windows masuk ke jendela aplikasi.

Karena ini menyentuh manajemen memori tingkat rendah dan sudah terbukti
berisiko, teknik itu **dicabut total**, bukan ditambal. Sebagai
gantinya:
- **"Komputer dikunci"** sekarang dideteksi lewat **polling biasa**
  (memeriksa proses jendela foreground tiap 5 detik) — jauh lebih aman
  karena tidak menyentuh message loop Windows sama sekali.
- **"Sleep"** dan **"User logout"** untuk sementara **dihapus dari
  pilihan** sampai ada cara deteksi yang sama amannya dengan cara di
  atas.

Setiap kali Auto Lock benar-benar mengunci folder, tercatat di Security
Activity Log dengan alasan pemicunya (mis. "Vault otomatis dikunci
(alasan: tidak ada aktivitas)").

## Fitur Baru: Multiple Vault

Restrukturisasi arsitektur besar: dari satu daftar folder & satu
password, sekarang aplikasi mendukung **beberapa vault bernama**,
masing-masing punya:
- Password master & PIN sendiri
- Daftar folder sendiri
- Pengaturan Auto Lock sendiri (durasi & pemicu)
- Placeholder pengaturan enkripsi (`encryption_enabled`) - baru aktif
  setelah roadmap #7 (Mode Encryption) selesai

**Migrasi otomatis**: kalau Anda upgrade dari versi sebelumnya,
`config.json` lama otomatis dikonversi jadi satu vault bernama
"Personal" berisi semua data lama (password, PIN, folder, auto-lock) -
tidak ada data yang hilang, tidak perlu setup ulang.

**Cara pakai:**
- **Vault Aktif** di bagian atas sidebar — dropdown untuk berpindah
  antar vault. Halaman Folders/Dashboard/Activity/Settings otomatis
  menampilkan data vault yang sedang aktif.
- **"+ Vault"** — buat vault baru dengan nama & password master sendiri.
- **"Ganti Nama"** — ubah nama vault yang sedang aktif.
- **"Hapus Vault Ini"** (di halaman Settings, butuh verifikasi password)
  — melepas vault dari daftar Vaultix. Folder aslinya tidak dihapus,
  hanya lepas dari pengelolaan aplikasi. Tidak bisa menghapus vault
  terakhir (minimal harus ada 1).
- **Auto Lock berjalan independen per vault** — bahkan vault yang
  sedang tidak ditampilkan tetap dipantau sesuai pengaturannya sendiri
  di latar belakang.
- Security Activity Log tetap **global** (satu log untuk semua vault),
  tapi setiap entri mencantumkan nama vault yang terlibat supaya tetap
  jelas asalnya.

### Perbaikan: satu folder tidak boleh ada di dua vault

Karena kunci/sembunyikan bekerja di **level folder fisik** (atribut
Windows + izin NTFS), folder yang sama tidak bisa "terkunci di satu
vault tapi terbuka di vault lain" — keduanya akan selalu ikut-ikutan
karena secara fisik itu folder yang sama di disk.

- **"+ Tambah Folder"** sekarang mengecek ke SEMUA vault, bukan cuma
  vault aktif — kalau folder sudah terdaftar di vault lain, muncul
  peringatan dan folder tidak ditambahkan lagi.
- Saat aplikasi dibuka, ada pengecekan otomatis untuk folder yang
  sudah kadung terdaftar di lebih dari satu vault (dari sebelum
  validasi ini ada) — kalau ditemukan, muncul peringatan berisi daftar
  foldernya beserta vault mana saja yang memilikinya, supaya bisa
  dibereskan manual (buka status foldernya, lalu hapus dari salah satu
  vault).

## Windows Hello / Fingerprint / Face — DITUNDA (dinonaktifkan sementara)

Sempat dicoba diimplementasikan pakai `UserConsentVerifier` (API
Windows yang otomatis memunculkan PIN Hello/sidik jari/wajah, mana saja
yang sudah terdaftar user). Setelah diuji langsung, ternyata API polos
ini **tidak berfungsi untuk aplikasi desktop klasik** seperti Vaultix
(Python + Tkinter) — selalu gagal dengan error `[WinError -2147019873]
The group or resource is not in the correct state`, bahkan setelah COM
diinisialisasi STA dengan benar.

**Penyebab sebenarnya**: `UserConsentVerifier.RequestVerificationAsync`
versi polos dirancang untuk aplikasi UWP yang punya asosiasi jendela
resmi di Windows Runtime. Aplikasi desktop klasik butuh memakai
interface interop khusus (`IUserConsentVerifierInterop`) yang mengirim
HWND jendela secara eksplisit — ini butuh manipulasi COM/WinRT tingkat
rendah lewat `ctypes` (vtable manual, GUID interface, dsb), jenis kode
yang risikonya setara dengan insiden WNDPROC subclassing yang pernah
menyebabkan crash di fitur Auto Lock.

**Keputusan**: daripada memasang perbaikan berisiko tanpa pengetesan
matang, fitur ini **dimatikan total** lewat flag
`WINDOWS_HELLO_FEATURE_ENABLED = False` di kode. Switch di halaman
Settings tetap terlihat (supaya jelas fiturnya "belum siap", bukan
hilang tanpa penjelasan) tapi selalu nonaktif/abu-abu. Password dan PIN
tetap menjadi metode verifikasi utama dan berfungsi normal.

Kalau di masa depan ada implementasi interop HWND-aware yang sudah
diuji aman, fitur ini bisa diaktifkan kembali cukup dengan mengubah
flag tersebut jadi `True` plus kode interop yang benar.

## Fitur Baru: Mode Encryption (AES-256-GCM + Argon2id)

Menu **terpisah** di sidebar ("🔐 Encryption") — sengaja tidak digabung
dengan mode Sembunyikan/Kunci yang sudah ada, karena mekanismenya
benar-benar berbeda:

| | Sembunyikan/Kunci (NTFS) | Mode Encryption |
|---|---|---|
| Yang diubah | Atribut folder / izin akses | **Isi setiap file**, byte demi byte |
| Kalau disk diambil & dibaca langsung | Isi file tetap bisa dibaca | Isi file **tetap acak**, tidak bisa dibaca |
| Reversible tanpa password | Bisa (manual lewat `attrib`/`icacls`) | **Tidak bisa** - tidak ada backdoor |

**Skema kriptografi:**
- **AES-256-GCM** — AEAD (Authenticated Encryption with Associated
  Data), jadi *authentication tag* otomatis termasuk di setiap file;
  kalau file dirusak/diotak-atik, dekripsi akan gagal jelas (bukan
  data korup diam-diam).
- **Argon2id** — menurunkan kunci AES-256 dari password master, dengan
  parameter time_cost=3, memory_cost=64 MB, parallelism=4 (jauh lebih
  tahan brute-force dibanding hash biasa).
- **Salt (16 byte) dan nonce (12 byte) acak PER FILE**, disimpan di
  header masing-masing file terenkripsi - tidak ada yang dipakai ulang.
- File terenkripsi ditandai ekstensi tambahan `.vaultixenc` dan
  strukturnya: `MAGIC(5 byte) + SALT(16) + NONCE(12) + CIPHERTEXT+TAG`.

**Cara pakai:**
1. Centang folder di halaman **Folders** (folder harus dalam status
   **tidak terkunci** - buka kuncinya dulu kalau masih NTFS-locked).
2. Buka halaman **🔐 Encryption**, klik **"Enkripsi Folder
   Tercentang"**.
3. Masukkan **password master penuh** (bukan PIN/Windows Hello - wajib
   password asli karena dipakai langsung sebagai bahan Argon2id).
4. Semua file di dalam folder (rekursif, termasuk subfolder) dienkripsi
   satu per satu dengan progress bar.
5. Untuk mengembalikan: centang folder yang berstatus "🔐 Terenkripsi",
   klik **"Dekripsi Folder Tercentang"**, masukkan password yang sama.

**⚠ Peringatan penting**: **tidak ada mekanisme "lupa password"** untuk
data terenkripsi. Kalau password vault hilang, file yang terenkripsi
TIDAK BISA dipulihkan oleh siapa pun, termasuk pengembang aplikasi ini.
Simpan password dengan sangat hati-hati sebelum mengenkripsi data
penting.

**Sudah diuji langsung** (bukan cuma ditulis, benar-benar dijalankan):
enkripsi file → isi jadi acak tanpa jejak plaintext → coba dekripsi
dengan password salah → gagal total (sesuai harapan) → dekripsi dengan
password benar → isi pulih 100% sama seperti semula.

## Fitur Baru: Recovery Key (Lupa Password)

Diakses lewat halaman **Settings → Recovery Key → "Buat Sekarang"**
(atau otomatis ditawarkan tepat setelah password pertama kali dibuat).

**Cara kerja:**
- Vaultix membuat kode acak 32 karakter (format `XXXX-XXXX-...` x8),
  ditampilkan **SEKALI SAJA** — Anda harus menyimpannya sendiri (salin,
  atau kirim ke email lewat aplikasi email default Anda).
- Yang disimpan Vaultix hanya **hash-nya** (PBKDF2 + salt), sama seperti
  password — bukan kode aslinya.
- Kalau lupa password: di dialog verifikasi akan muncul tautan **"Lupa
  password? Gunakan Recovery Key"** (hanya muncul kalau vault itu sudah
  punya recovery key). Masukkan kode recovery, kalau benar Anda diminta
  membuat password master baru.

**Kirim ke email — tanpa Vaultix menyimpan kredensial email Anda:**
tombol "Kirim ke Email" membuka **aplikasi email default** di
komputer Anda (lewat `mailto:`) dengan draft sudah terisi kode
recovery-nya — Anda yang meninjau dan mengirim sendiri. Vaultix tidak
pernah menyimpan alamat email, password email, atau kredensial SMTP
apa pun; ini sengaja dihindari karena kalau akun email diretas, itu
akan langsung membocorkan akses recovery vault juga.

**⚠ Batasan penting yang harus dipahami**: Recovery Key **hanya bisa
reset password LOGIN vault** (untuk kunci NTFS, PIN, dll). Recovery Key
**TIDAK BISA** mendekripsi file yang sudah dienkripsi lewat Mode
Encryption dengan password lama — kunci AES-nya diturunkan langsung
dari string password asli lewat Argon2id, jadi tidak ada jalan pintas
tanpa menghancurkan keamanan enkripsinya sendiri. Kalau Anda berencana
memakai Mode Encryption untuk data penting, password aslinya tetap
wajib diingat/disimpan terpisah - recovery key tidak menggantikan itu.

**Sudah diuji langsung**: generate key → verifikasi dengan key benar
(berhasil) → key salah (ditolak) → key dengan huruf kecil/spasi ekstra
(tetap diterima karena dinormalisasi) — semua sesuai harapan.

## Perbaikan Penting #8 (checkbox Encryption + scroll Settings)

- **Halaman Encryption**: kolom centang folder sebelumnya cuma teks
  statis (☑/☐), tidak bisa diklik sama sekali. Diganti jadi checkbox
  sungguhan (`CTkCheckBox`) yang langsung sinkron dua arah dengan
  centang di halaman Folders — centang di salah satu halaman, otomatis
  ikut tercentang juga di halaman satunya.
- **Halaman Settings**: sebelumnya semua kartu (Password, PIN, Recovery
  Key, Windows Hello, Riwayat Akses, Auto Lock, Hapus Vault) dipasang
  langsung tanpa area scroll, jadi kartu-kartu di bagian bawah hilang
  begitu jendela dikecilkan. Sekarang dibungkus dalam area scrollable -
  semua kartu selalu bisa dijangkau dengan scroll berapa pun ukuran
  jendelanya.

## Perbaikan Penting #9 (race condition: Auto Lock vs proses enkripsi/aksi lain)

Ditemukan bug: **Auto Lock berjalan di timer latar belakang** (dicek
tiap 5 detik) dan timer ini **tetap aktif meski ada dialog progress bar
modal** sedang terbuka (mis. saat sedang mengenkripsi folder besar).
Akibatnya, kalau Auto Lock terpicu (misalnya karena idle) di
tengah-tengah proses enkripsi, folder yang sedang diproses ikut dikunci
NTFS — menyebabkan file-file yang belum sempat dienkripsi gagal diakses
dan tertinggal dalam status campur aduk (sebagian `.vaultixenc`,
sebagian masih plaintext).

**Perbaikan**: folder yang sedang diproses aksi apa pun (Sembunyikan,
Kunci, Enkripsi, Dekripsi, dll) sekarang ditandai di
`self.paths_in_progress`, dan Auto Lock **melewati** folder yang sedang
ditandai itu sampai prosesnya benar-benar selesai. Race condition ini
tidak akan terjadi lagi.

**Kalau folder Anda sudah kadung tercampur** (sebagian file
`.vaultixenc`, sebagian belum, status terkunci): buka kunci foldernya
dulu di halaman Folders, lalu jalankan "Dekripsi Folder Tercentang" di
halaman Encryption — ini aman, hanya memproses file `.vaultixenc` yang
sempat kepotong, file yang memang belum sempat dienkripsi tidak
diapa-apakan.

## Dependensi

```
pip install customtkinter pywin32 cryptography argon2-cffi
```

- `customtkinter` **wajib** — dipakai untuk seluruh tampilan aplikasi.
- `cryptography` dan `argon2-cffi` **wajib untuk Mode Encryption** —
  tanpa ini, halaman "🔐 Encryption" menampilkan pesan jelas untuk
  install dulu, sisa aplikasi tetap jalan normal.
- `pywin32` opsional, hanya dipakai fitur "Riwayat Akses Folder" bagian
  Recent Items. Tanpa ini aplikasi tetap jalan normal.
- `winsdk` **tidak diperlukan lagi untuk saat ini** — fitur Windows
  Hello yang memakainya sedang dinonaktifkan (lihat bagian di atas).
  Boleh tetap diinstall kalau sudah terlanjur, tidak masalah.

## Menjalankan

```
python main.py
```

(kalau `python` tidak dikenali di PowerShell, coba `py main.py`)

## Membuat file .exe standalone

```
pip install pyinstaller customtkinter pywin32 cryptography argon2-cffi
pyinstaller --onefile --noconsole --name "Vaultix" main.py
```

File hasilnya ada di `dist\Vaultix.exe`.

## Penggunaan

1. Jalankan aplikasi. Pertama kali dibuka, Anda diminta membuat
   **password master**.
2. Navigasi antar halaman lewat **sidebar kiri**: 📁 Folders (utama),
   🔐 Encryption, 📊 Dashboard, 🛡 Activity, ⚙ Settings.
3. Di halaman **Folders**, klik **"+ Tambah Folder"** untuk membuka
   dialog pilih folder bawaan Windows. Satu folder per klik — ulangi
   tombolnya untuk menambahkan folder lain.
4. **Centang** folder yang ingin diproses di kolom checkbox paling kiri
   (atau klik **"Pilih Semua"** / **"Batal Pilih Semua"**), lalu pilih
   aksi yang diinginkan:
   - **Sembunyikan / Tampilkan** — tanpa password.
   - **Kunci / Buka Kunci** — minta password master (atau PIN kalau
     sudah diaktifkan).
   - **Kunci & Sembunyikan** / **Buka Kunci & Tampilkan** — gabungan
     keduanya.
5. Halaman **🔐 Encryption** — pakai centang folder yang sama untuk
   **Enkripsi/Dekripsi** isi file sungguhan (AES-256-GCM). Folder harus
   tidak terkunci dulu, dan wajib pakai password master penuh (bukan
   PIN). Baca peringatan di halaman itu sebelum dipakai untuk data
   penting.
6. **Hapus dari Daftar** hanya bisa dilakukan untuk folder yang sudah
   dalam kondisi terlihat, tidak terkunci, dan tidak terenkripsi
   (untuk mencegah folder "hilang jejak" dari daftar).
7. Halaman **⚙ Settings** berisi Ganti Password, Atur PIN, dan tombol
   ke Riwayat Akses Folder.
8. Ganti tema Light/Dark/System dari dropdown di bagian bawah sidebar.

## Catatan Keamanan

- Fitur **Kunci** mengubah izin NTFS asli folder Anda (menambah entri
  Deny). Ini aman untuk dibalik lewat tombol "Buka Kunci" di aplikasi,
  tapi kalau Anda lupa password dan ingin membuka manual, gunakan Command
  Prompt (jalankan sebagai Administrator jika perlu):
  ```
  icacls "C:\path\folder" /remove:d %USERNAME%
  ```
  Kalau masih gagal juga, sebagai jalan terakhir:
  ```
  takeown /F "C:\path\folder" /R /D Y
  icacls "C:\path\folder" /reset /T
  ```
- Untuk membalik status **Sembunyikan** secara manual tanpa password:
  ```
  attrib -h -s "C:\path\folder"
  ```
- Tidak ada mekanisme "lupa password" otomatis di aplikasi — simpan
  password master dengan baik.
- Karena ini bukan enkripsi, untuk perlindungan data yang lebih kuat
  (misalnya terhadap orang yang mengakses hard disk secara langsung)
  pertimbangkan solusi enkripsi seperti BitLocker atau VeraCrypt di
  kemudian hari.
