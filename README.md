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

1. ✅ **Security Activity Log** — selesai (lihat di bawah).
2. ⬜ Dashboard Security Status
3. ⬜ PIN sebagai alternatif password
4. ⬜ Auto Lock berdasarkan aktivitas (idle, focus, lock/sleep/logout)
5. ⬜ Multiple Vault (tiap vault: password/enkripsi/auto-lock/policy sendiri)
6. ⬜ Windows Hello / Fingerprint / Face (lewat UserConsentVerifier)
7. ⬜ Mode Encryption terpisah (AES-256-GCM + Argon2id + salt acak)
8. ⬜ Screenshot/Recording Protection — **catatan penting**: hanya bisa
   melindungi jendela Vaultix sendiri, TIDAK bisa mencegah screenshot
   File Explorer asli. Baru berguna penuh kalau dipasangkan dengan file
   browser custom di dalam aplikasi.
9. ⬜ Decoy Vault — butuh Mode Encryption selesai dulu sebagai fondasi.
10. ⬜ Security Key (FIDO2) — tahap lanjutan/eksperimental.
11. ⬜ Mobile Remote Lock — **butuh infrastruktur terpisah** (backend
    server, API, push notification, aplikasi mobile). Di luar cakupan
    aplikasi desktop lokal ini.

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

## Dependensi

```
pip install customtkinter pywin32
```

- `customtkinter` **wajib** — dipakai untuk seluruh tampilan aplikasi.
- `pywin32` opsional, hanya dipakai fitur "Riwayat Akses Folder" bagian
  Recent Items. Tanpa ini aplikasi tetap jalan normal.

## Menjalankan

```
python main.py
```

(kalau `python` tidak dikenali di PowerShell, coba `py main.py`)

## Membuat file .exe standalone

```
pip install pyinstaller customtkinter pywin32
pyinstaller --onefile --noconsole --name "Vaultix" main.py
```

File hasilnya ada di `dist\Vaultix.exe`.

## Penggunaan

1. Jalankan aplikasi. Pertama kali dibuka, Anda diminta membuat
   **password master**.
2. Klik **"+ Tambah Folder"** untuk membuka dialog pilih folder bawaan
   Windows. Satu folder per klik — ulangi tombolnya untuk menambahkan
   folder lain.
3. **Centang** folder yang ingin diproses di kolom checkbox paling kiri
   (atau klik **"Pilih Semua"** / **"Batal Pilih Semua"**), lalu pilih
   aksi yang diinginkan:
   - **Sembunyikan / Tampilkan** — tanpa password.
   - **Kunci / Buka Kunci** — minta password master.
   - **Kunci & Sembunyikan** / **Buka Kunci & Tampilkan** — gabungan
     keduanya.
4. Ganti tema Light/Dark/System dari dropdown di pojok kanan atas.
5. **Riwayat Akses Folder** untuk melihat file/folder yang baru-baru ini
   dibuka di perangkat (berdasarkan data bawaan Windows).
6. **Hapus dari Daftar** hanya bisa dilakukan untuk folder yang sudah
   dalam kondisi terlihat & tidak terkunci (untuk mencegah folder
   terkunci "hilang jejak" dari daftar).

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
