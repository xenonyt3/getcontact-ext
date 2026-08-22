# Panduan `engine.py`

`engine.py` adalah lapisan inti aplikasi. File ini menangani penyimpanan akun,
normalisasi nomor, komunikasi ke layanan, serta alur verifikasi WhatsApp.
Antarmuka seperti Tkinter, web, CLI, atau aplikasi lain seharusnya memakai
fungsi-fungsi publik di file ini, bukan menyalin logika protokol ke UI.

Dokumen ini ditujukan untuk orang yang baru meng-clone repo atau ingin membuat
UI baru di atas engine yang sama.

> **Batas penggunaan.** Gunakan hanya kredensial dan nomor yang berhak Anda
> kelola/periksa. Jangan menampilkan token atau `finalKey` di log, screenshot,
> telemetry, maupun pesan error yang dikirim ke layanan lain.

## Struktur proyek

| Berkas | Tanggung jawab |
| --- | --- |
| `engine.py` | Logika API, kredensial, validasi nomor, dan verifikasi. Tidak bergantung pada UI. |
| `gtc_ui.py` | Implementasi desktop Tkinter yang memakai `engine`. Contoh integrasi referensi. |
| `gtc.py` | Compatibility import untuk kode lama yang masih melakukan `import gtc`. |
| `requirements.txt` | Dependensi Python runtime. |

## Menjalankan setelah clone

Gunakan Python 3.9 atau lebih baru. Buat virtual environment bila perlu, lalu
pasang dependensinya:

```bash
python -m pip install -r requirements.txt
python gtc_ui.py
```

Sebelum lookup pertama kali, tambahkan akun melalui **Kelola akun**. UI dapat
menambahkan kredensial manual milik pengguna atau memulai verifikasi WhatsApp.

## Arsitektur dan alur

```text
UI / CLI / aplikasi lain
        │ memanggil fungsi publik
        ▼
engine.py ── penyimpanan lokal credentials.json
        │
        ├── lookup profil/tag dan subscription
        └── registrasi/verifikasi WhatsApp
```

`engine.py` melakukan request jaringan secara sinkron. Pada UI berbasis event
(Tkinter, Qt, browser bridge, dan sejenisnya), panggil fungsi jaringan dari
worker/background task, lalu kembalikan hasilnya ke thread UI. `gtc_ui.py`
memakai `threading.Thread` dan `queue.Queue` sebagai contoh pola tersebut.

## API publik untuk integrasi

### Lookup dan kuota

| Fungsi | Parameter penting | Hasil |
| --- | --- | --- |
| `normalize_phone(raw)` | Nomor pengguna | Nomor E.164. `08…` menjadi `+628…`, `62…` menjadi `+62…`. |
| `api_search(credential, phone, source)` | `source`: `"profile"` atau `"tags"` | Respons `dict` mentah yang telah didekripsi. |
| `api_subscription(credential)` | Kredensial akun aktif | Respons subscription/usage sebagai `dict`. |
| `dig(obj, path, default=None)` | Contoh path: `"result.profile"` | Mengambil nilai nested dengan aman tanpa `KeyError`. |

Pemetaan endpoint lookup adalah:

| Nilai `source` | Data yang diharapkan | Lokasi respons umum |
| --- | --- | --- |
| `"profile"` | Detail profil nomor | `result.profile` |
| `"tags"` | Daftar tag nomor | `result.tags` |

Untuk kuota, gunakan `result.subscriptionInfo.usage.search` dan
`result.subscriptionInfo.usage.numberDetail`. Masing-masing umumnya memiliki
`remainingCount` dan `limit`; tanggal perpanjangan ada di
`result.subscriptionInfo.renewDate`. Perlakukan field respons sebagai data yang
dapat berubah: tampilkan fallback bila suatu field tidak ada.

Contoh alur lookup minimal:

```python
import engine

account, credential = engine.get_cred(engine.load_store(), None)
phone = engine.normalize_phone("0812xxxxxxx")
response = engine.api_search(credential, phone, "profile")
profile = engine.dig(response, "result.profile") or {}
```

### Penyimpanan dan pemilihan akun

| Fungsi | Kegunaan |
| --- | --- |
| `load_store()` / `save_store(store)` | Membaca/menyimpan database JSON lokal. |
| `get_cred(store, name)` | Mengambil `(nama_akun, credential)`. Berikan `None` untuk akun aktif. |
| `add_credential(...)` | Menyimpan kredensial milik pengguna dan menjadikannya aktif. |
| `use_credential(name)` | Mengubah akun aktif. |
| `remove_credential(name)` | Menghapus akun; bila itu akun aktif, engine memilih akun berikutnya jika ada. |

Lokasi default penyimpanan adalah `~/.config/gtc/credentials.json`. Untuk
pengembangan atau pengujian, set `GTC_CONFIG_DIR` ke folder lain sebelum
aplikasi dijalankan.

Struktur berkasnya:

```json
{
  "active": "nama-akun",
  "credentials": {
    "nama-akun": {
      "description": "opsional",
      "phoneNumber": "+628…",
      "clientDeviceId": "…",
      "finalKey": "RAHASIA",
      "token": "RAHASIA",
      "validationDate": "opsional"
    }
  }
}
```

`token` dan `finalKey` setara materi autentikasi. Jangan commit file ini ke
repo, dan jangan membuat fitur ekspor otomatis tanpa peringatan yang jelas.

### Verifikasi WhatsApp

Alur verifikasi adalah dua tahap dan harus dipertahankan jika UI baru dibuat:

1. Panggil `start_whatsapp_credential(raw_phone, on_credential=..., progress=...)`.
   Fungsi mengembalikan `PendingCredential`, berisi `deeplink`, `reference`,
   dan kredensial sementara. Pada tahap ini akun **belum** disimpan.
2. Minta pengguna membuka tautan WhatsApp, mengirim pesan yang telah disiapkan,
   lalu panggil `complete_whatsapp_credential(pending, name=...)`.
   Hanya setelah berhasil, akun disimpan dan diaktifkan.

`progress` dipanggil beberapa kali selama tahap pertama. Kirim pesannya ke
status/log UI, tetapi jangan masukkan nilai kredensial ke log tersebut.

## Kontrak error dan respons

Fungsi-fungsi engine dapat melempar `engine.GtcError` untuk kesalahan yang
diharapkan: nomor tidak valid, akun belum dipilih, HTTP gagal, atau verifikasi
belum selesai. UI sebaiknya menangkap exception ini dan menampilkan pesan yang
singkat serta aman. Untuk error tak terduga, tampilkan tipe/pesan umum tanpa
mencetak `credential`, header request, atau body terenkripsi.

Contoh pola aman:

```python
try:
    account, credential = engine.get_cred(engine.load_store(), None)
    result = engine.api_subscription(credential)
except engine.GtcError as exc:
    show_error(str(exc))
else:
    render_quota(result)
```

Respons API sengaja dikembalikan sebagai `dict` mentah agar UI dapat memilih
field yang ingin ditampilkan. Karena respons layanan dapat berubah, gunakan
`engine.dig(...)` atau `.get(...)`, bukan indexing langsung untuk field yang
tidak dijamin tersedia.

## Catatan untuk pembuat UI baru

- Import `engine`; jangan memanggil fungsi privat yang diawali `_` dan jangan
  membuat ulang `gtc_call`, enkripsi, atau header request di UI.
- Nonaktifkan aksi yang memakai jaringan selama request aktif agar pengguna
  tidak mengirim request ganda.
- Normalisasi nomor dengan `normalize_phone` sebelum lookup.
- Ambil kredensial aktif tepat sebelum request menggunakan
  `get_cred(load_store(), None)` agar pilihan akun terbaru dipakai.
- Pisahkan tampilan profil, tag, dan kuota. Ketiadaan hasil bukan selalu sama
  dengan kuota habis; error dari engine harus ditampilkan sebagai error.
- `show_captcha(image_base64)` hanya helper untuk menyimpan/membuka gambar
  captcha. Engine saat ini tidak menyediakan alur lengkap untuk mengambil atau
  mengirim jawaban captcha.

### Template prompt untuk membuat UI lain

Salin dan sesuaikan prompt berikut saat meminta agen coding membuat antarmuka
baru:

```text
Buat UI [web/desktop/CLI] untuk project ini dengan engine.py sebagai satu-satunya
lapisan API. Jangan ubah protokol, konstanta, enkripsi, atau penyimpanan
kredensial di engine.py. Implementasikan: pemilihan akun aktif, cari profil,
cari tag, cek kuota, dan penanganan engine.GtcError. Jalankan request jaringan
di background agar UI tidak hang, nonaktifkan tombol selama request berjalan,
normalisasi nomor lewat engine.normalize_phone, serta jangan pernah log atau
tampilkan token/finalKey kecuali pada layar kredensial yang memang diperlukan.
```

## Verifikasi perubahan

Setelah mengubah engine atau UI, lakukan pemeriksaan syntax sederhana:

```bash
python -m py_compile engine.py gtc_ui.py
```

Uji manual minimal: tambah akun sendiri, jadikan aktif, cek kuota, lalu lakukan
satu lookup profil atau tag pada nomor yang memang berhak diuji.
