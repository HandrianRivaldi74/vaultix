# Vaultix (Windows)

Aplikasi desktop untuk mendaftarkan folder pilihan, lalu mengelola dua
aksi independen terhadapnya: **Sembunyikan** dan **Kunci** — keduanya bisa
diulang-ulang kapan saja, tidak sekali pakai.

## Konsep Utama

1. **Daftar Folder** — folder yang Anda tambahkan (bisa banyak sekaligus)
   akan selalu ada di daftar ini, apa pun statusnya. Menambah folder ke
   daftar TIDAK langsung menyembunyikan/mengunci apa pun — itu aksi
   terpisah yang Anda pilih sendiri setelahnya.
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
2. Klik **"+ Tambah Folder"** untuk mendaftarkan satu atau beberapa
   folder sekaligus ke daftar utama.
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
