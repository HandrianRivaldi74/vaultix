# Vaultix (Windows)

Aplikasi desktop untuk mendaftarkan folder pilihan, lalu mengelola dua
aksi independen terhadapnya: **Sembunyikan** dan **Kunci** — keduanya bisa
diulang-ulang kapan saja, tidak sekali pakai.

## Konsep Utama

1. **Daftar Folder** — folder yang Anda tambahkan (bisa banyak sekaligus)
   akan selalu ada di daftar ini, apa pun statusnya. Menambah folder ke
   daftar TIDAK langsung menyembunyikan/mengunci apa pun — itu aksi
   terpisah yang Anda pilih sendiri setelahnya.
2. **Sembunyikan / Tampilkan** (tanpa password) — folder diberi/dihapus
   atribut Windows `Hidden + System`, sehingga hilang/muncul lagi dari
   File Explorer. Sifatnya cuma kerapian tampilan, cepat, reversible
   kapan saja.
3. **Kunci / Buka Kunci** (butuh password master) — folder diberi/dihapus
   izin **Deny** lewat `icacls` (NTFS permission) untuk akun Windows Anda
   saat ini, sehingga folder benar-benar tidak bisa dibuka ("Access is
   denied") sampai dibuka lagi lewat Vaultix dengan password yang benar.
   Ini proteksi akses sungguhan, bukan sekadar sembunyi — tapi tetap
   **bukan enkripsi**: isi file tidak diubah sama sekali.
4. **Kunci & Sembunyikan** / **Buka Semua** — tombol gabungan untuk
   melakukan keduanya sekaligus dengan satu kali password.

Password disimpan sebagai hash (PBKDF2-HMAC-SHA256 + salt) di
`%USERPROFILE%\.vaultix\config.json`, bukan sebagai teks biasa.

## Yang Baru di Versi Ini

- Tampilan dibangun ulang pakai **CustomTkinter** (tema modern, ada mode
  **Light / Dark / System** yang bisa diganti dari pojok kanan atas dan
  tersimpan otomatis untuk sesi berikutnya).
- Semua jendela (utama & dialog) otomatis **center di layar**.
- Folder di daftar tidak lagi "hilang kegunaannya" setelah dibuka — semua
  aksi (sembunyikan/tampilkan/kunci/buka kunci) bisa diulang kapan saja
  selama folder masih ada di daftar.
- Fitur **Riwayat Akses Folder** tetap ada (baca data yang sudah dicatat
  Windows, tanpa proses background).

## Perbaikan Penting (baca kalau upgrade dari versi sebelumnya)

Versi sebelumnya memakai izin **Full Control ("F")** saat mengunci folder
lewat `icacls`. Ini bug: Full Control mencakup hak mengubah izin folder
itu sendiri (WRITE_DAC), jadi begitu di-deny, akun Anda ikut kehilangan
hak untuk membuka kunci lagi — bisa memicu `Access is denied` bahkan
lewat tombol "Buka Kunci" di aplikasi.

Versi ini sudah diperbaiki untuk memakai izin **Modify ("M")** saja saat
mengunci — tetap memblokir baca/tulis/hapus isi folder, tapi tidak
memblokir hak mengubah izin, sehingga folder selalu bisa dibuka kunci
lagi lewat aplikasi.

**Kalau ada folder yang sudah terlanjur terkunci dengan versi lama** dan
tombol "Buka Kunci" gagal dengan `Access is denied`, perbaiki manual lewat
Command Prompt **yang dijalankan sebagai Administrator**:

```
icacls "C:\path\folder" /remove:d %USERNAME%
```

Kalau masih gagal juga, sebagai jalan terakhir:

```
takeown /F "C:\path\folder" /R /D Y
icacls "C:\path\folder" /reset /T
```



## Perbaikan Penting #2 (urutan Sembunyikan & Kunci + progress bar)

Izin **"Modify"** yang dipakai untuk mengunci ternyata juga mencakup hak
**Write Attributes** (hak yang dibutuhkan untuk mengubah atribut
Hidden/System). Karena itu, tombol gabungan **"Kunci & Sembunyikan"**
sekarang menjalankan **Sembunyikan dulu, baru Kunci** — kalau dibalik,
langkah menyembunyikan akan gagal karena hak mengubah atribut sudah lebih
dulu diblokir oleh kunci. Sebaliknya, **"Buka Semua"** menjalankan Buka
Kunci dulu, baru Tampilkan.

Setiap aksi (Sembunyikan/Tampilkan/Kunci/Buka Kunci/Gabungan) sekarang
juga menampilkan **progress bar** dan berjalan di background thread,
supaya jendela aplikasi tidak tampak macet saat memproses folder
berukuran besar (operasi `icacls` pada folder besar bisa memakan waktu
karena diterapkan ke semua isi folder secara rekursif).

**Kalau ada folder yang sudah terlanjur dalam kondisi "Terkunci" tapi
tidak "Tersembunyi"** (akibat bug urutan versi sebelumnya): klik **"Buka
Kunci"** dulu, lalu klik **"Sembunyikan"**, lalu klik **"Kunci"** lagi
kalau memang ingin folder itu tersembunyi sekaligus terkunci.

## Perbaikan Penting #3 (dialog password kini ikut tema)

Dialog buat password, verifikasi, dan ganti password sebelumnya masih
memakai jendela Tkinter bawaan yang polos (tidak ikut mode Light/Dark).
Sekarang sudah diganti dengan dialog custom bergaya CustomTkinter yang
konsisten dengan tampilan Light/Dark aplikasi.

## Perbaikan Penting #4 (rapikan UI + fitur Tandai Folder)

- Jendela utama diperlebar dan panel tombol aksi disusun ulang (bertumpuk
  vertikal per kategori) supaya teks tombol seperti "Kunci & Sembunyikan"
  tidak lagi terpotong di tampilan Dark mode.
- Tombol gabungan "Buka Semua" diganti nama jadi **"Buka Kunci &
  Tampilkan"** agar lebih jelas maksudnya (membuka kunci dan menampilkan
  folder sekaligus dalam satu klik + satu password).
- Fitur baru: **Tandai Folder** — pilih satu atau beberapa folder di
  daftar, klik "🏷 Tandai Folder", lalu beri label bebas (misalnya
  "Penting", "Kerja", "Pribadi"). Tanda ini muncul sebagai kolom
  tersendiri di tabel dan murni untuk membantu Anda mengorganisir daftar
  folder — tidak memengaruhi status sembunyi/kunci.

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
3. Pilih satu/beberapa folder di daftar (Ctrl/Shift-klik untuk memilih
   lebih dari satu), lalu pilih aksi yang diinginkan:
   - **Sembunyikan / Tampilkan** — tanpa password.
   - **Kunci / Buka Kunci** — minta password master.
   - **Kunci & Sembunyikan** / **Buka Kunci & Tampilkan** — gabungan keduanya.
4. Klik **"🏷 Tandai Folder"** untuk memberi label bebas pada folder
   terpilih (misalnya "Penting", "Kerja") agar mudah dikenali di daftar.
5. Ganti tema Light/Dark/System dari dropdown di pojok kanan atas.
6. **Riwayat Akses Folder** untuk melihat file/folder yang baru-baru ini
   dibuka di perangkat (berdasarkan data bawaan Windows).
7. **Hapus dari Daftar** hanya bisa dilakukan untuk folder yang sudah
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
