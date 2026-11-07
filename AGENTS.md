# AGENTS.md

Panduan ini berlaku untuk setiap kontribusi pada proyek tugas akhir:

> Analisis Komparatif Model Transformer untuk Prediksi Penjualan Ekspor Multivariat dengan Studi Kasus Data SAP Perusahaan XYZ

Proyek ini adalah eksperimen komparatif kuantitatif, bukan integrasi ke SAP atau aplikasi produksi. Sumber ruang lingkup utama adalah `docs/sempro.pdf`.

## Tujuan kerja

Implementasikan dan bandingkan tiga arsitektur pada data historis SAP yang sama:

1. MoEBS-Transformer.
2. N-Space Transformer.
3. Temporal Fusion Transformer (TFT).

Keluaran akhir harus dapat menjawab model mana yang paling sesuai berdasarkan akurasi, efisiensi komputasi, dan interpretabilitas yang tersedia. Jangan menyimpulkan bahwa Transformer tertentu unggul sebelum eksperimen selesai.

## Aturan eksperimen yang tidak boleh berubah

- Gunakan satu pipeline data kanonis, satu definisi target, satu granularity, satu horizon, dan batas waktu split yang sama untuk ketiga model.
- Split harus kronologis. Data masa depan tidak boleh dipakai untuk imputasi, normalisasi, encoding, pemilihan fitur, atau penalaan model pada masa lalu.
- Simpan konfigurasi, seed, versi paket, perangkat keras, waktu pelatihan, waktu inferensi, prediksi, dan metrik setiap run.
- Laporkan metrik MAE, RMSE, MAPE, SMAPE, dan R^2. Istilah `R` di sempro diperlakukan sebagai coefficient of determination, sehingga implementasi harus menggunakan dan menamai `r2` secara eksplisit.
- Ukur MAPE hanya pada observasi dengan aktual bukan nol dan laporkan jumlah atau proporsi observasi yang dievaluasi. Gunakan epsilon terdokumentasi untuk SMAPE.
- Model boleh mempunyai preprocessing internal yang diperlukan arsitektur, tetapi tidak boleh menerima informasi masa depan atau memperoleh data/split yang berbeda.
- MoEBS mendapat anggaran optimasi yang ditetapkan di konfigurasi. Bila model lain dituning, semua model harus mendapat batas sumber daya yang setara atau perbedaan anggaran harus dilaporkan sebagai keterbatasan.

## Data dan kerahasiaan

Tiga sumber SAP yang tersedia adalah:

- `dataset/2023.XLSX`
- `dataset/2024.XLSX`
- `dataset/2025.XLSX`

Data mentah SAP adalah data bisnis terbatas. Jangan mengunggahnya ke repositori publik, notebook pihak ketiga, issue tracker, atau layanan eksternal tanpa izin eksplisit. Jangan menulis nama perusahaan, pelanggan, nomor PO, atau nilai transaksi mentah pada README, log, gambar, maupun hasil yang dilacak Git.

Simpan data mentah sebagai read-only. Semua hasil transformasi harus dibuat sebagai artefak turunan yang dapat direproduksi. Bila kurs IDR ke USD diperlukan, simpan sumber, tanggal akses, definisi kurs, dan berkas kurs sebagai artefak versi; jangan mengonversi dengan angka yang tidak terdokumentasi.

## Kontrak data awal

- Unit sumber adalah item transaksi penjualan.
- Kolom waktu utama adalah `KP Date`, yaitu tanggal dibuatnya Kartu Produksi. `CRD Date` adalah tanggal keberangkatan ekspor; penggunaannya sebagai fitur dilarang sampai ketersediaannya pada origin prediksi terbukti.
- Target utama yang telah ditetapkan adalah `Order Quantity (Item)` dengan satuan pcs berdasarkan konfirmasi bisnis. Agregasi menggunakan penjumlahan per seri-minggu setelah definisi seri tervalidasi; pertahankan kuantitas pecahan tanpa pembulatan.
- `Country Key` kosong dipertahankan sebagai kategori negara `__UNKNOWN_COUNTRY__` sesuai keputusan bisnis, disertai indikator audit asal kosong. Bedakan kategori ini dari token encoder untuk kategori baru pada validation/test; data mentah dan kanonis WP1 tetap utuh.
- `Sales group` (`sales_group` pada skema kanonis) digunakan sebagai kode buyer untuk filter dan sebagai kode brand berdasarkan konfirmasi pengguna 6 November 2026. Pemetaan brand langsung memakai kode yang di-trim dan dikapitalisasi; seri mingguan menggunakan kombinasi `country_key` dan `sales_group`.
- Dataset analisis/model mengecualikan baris dengan `Document Currency = IDR`, buyer `PEB`/`KWK`/`LNS`/`CPC` pada `Sales group`, `Country Key = ID`, atau `Net Price` bernilai numerik nol (termasuk `0.00`). Terapkan pada seluruh sumber melalui konfigurasi versi; simpan alasan dan statistik eksklusi pada artefak turunan tanpa mengubah data mentah.
- Sempro mengusulkan agregasi mingguan per kombinasi negara tujuan dan merek karena seri harian terlalu sparse.
- Dataset tidak memiliki kolom bernama `Brand` secara eksplisit. Pengguna telah memvalidasi `Sales group` sebagai kolom brand pada 6 November 2026; jangan memakai `Search Term`, `Search term 1`, atau turunan `Material` sebagai merek tanpa validasi tambahan.
- Minggu tanpa transaksi setelah filter bisnis ditetapkan sebagai permintaan nol (0 pcs) berdasarkan keputusan pengguna 6 November 2026. Terapkan pada minggu dengan cakupan sumber lengkap untuk seri aktif dan simpan indikator nol hasil regularisasi. Nilai transaksi yang hilang/invalid tetap mengikuti quality hold; kebijakan aktivasi seri dan minggu parsial wajib diaudit pada WP3 tanpa informasi masa depan.
- `Delivery Status`, `Reason for Rejection`, dan status operasional lain berpotensi merupakan informasi pasca-transaksi. Perlakukan sebagai leakage sampai terbukti tersedia pada saat origin prediksi.
- Ketiga file 2023, 2024, dan 2025 masing-masing memiliki 34 kolom. Harmonisasi skema—termasuk nama, tipe, urutan, dan makna kolom—tetap wajib dicatat dan diuji.

## Struktur proyek yang dituju

```text
dataset/                 # Input SAP lokal, tidak dilacak Git
docs/                    # Proposal dan referensi internal
configs/                 # Konfigurasi data, fitur, model, dan eksperimen
src/
  data/                  # Ingest, validasi, harmonisasi, agregasi
  features/              # Feature contract dan pembentukan window
  models/                # MoEBS, N-Space, TFT dengan interface seragam
  evaluation/            # Metrik, prediksi, tabel perbandingan
  utils/                 # Seed, logging, manifest, serialisasi
tests/                   # Unit, contract, dan regression tests
artifacts/               # Data turunan dan checkpoint, tidak dilacak Git
reports/                 # Tabel/figures agregat yang aman dibagikan
```

Direktori di atas adalah rancangan target. Jangan membuat modul kosong hanya demi memenuhi struktur; buat ketika tahap implementasi membutuhkannya.

## Konvensi implementasi

- Gunakan Python dan satu framework deep learning utama untuk seluruh model bila memungkinkan. Implementasi harus dapat dijalankan kembali dari konfigurasi tanpa langkah manual notebook.
- Pisahkan data mentah, data terharmonisasi, data teragregasi, feature tensor, checkpoint, dan hasil evaluasi. Jangan menimpa artefak antar run.
- Nama run harus memuat model, versi konfigurasi, seed, dan timestamp atau run id yang unik.
- Gunakan `float32` untuk tensor kecuali ada alasan numerik yang dicatat. Fit scaler/encoder hanya pada subset train lalu terapkan ke validation/test.
- Tangani kategori baru pada validation/test dengan token `unknown` yang konsisten, bukan dengan fit ulang encoder.
- Semua nilai hilang, duplikasi, transaksi tanpa tanggal, nilai negatif, nilai nol, dan mata uang harus memiliki kebijakan eksplisit beserta statistik sebelum/sesudah.
- Jangan hard-code rentang waktu, kolom, kurs, horizon, atau hyperparameter di source. Masukkan ke konfigurasi versi.

## Kriteria kualitas

Sebelum menganggap suatu tahap selesai, pastikan:

1. Ada artefak atau test yang membuktikan hasilnya.
2. Tidak ada leakage lintas batas train/validation/test.
3. Semua model memakai manifest data dan split yang identik.
4. Hasil dapat direproduksi dari seed dan konfigurasi yang tersimpan.
5. Ringkasan yang dibagikan tidak membocorkan detail bisnis sensitif.

Rujuk [task-breakdown.md](task-breakdown.md) untuk urutan pekerjaan dan [ARCHITECTURE.md](ARCHITECTURE.md) untuk kontrak antar komponen.
