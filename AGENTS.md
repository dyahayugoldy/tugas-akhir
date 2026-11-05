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
- Kolom waktu utama adalah `KP Date`; `CRD Date` harus dipahami secara bisnis sebelum dipakai sebagai fitur.
- Kandidat target adalah `Order Quantity (Item)` dan `Net Value (Item)` setelah harmonisasi mata uang. Pilih satu target utama setelah EDA dan catat keputusan itu pada konfigurasi serta laporan.
- Sempro mengusulkan agregasi mingguan per kombinasi negara tujuan dan merek karena seri harian terlalu sparse.
- Dataset tidak memiliki kolom bernama `Brand` secara eksplisit. Jangan menyamakan `Search Term`, `Search term 1`, `Sales group`, atau turunan `Material` dengan merek tanpa data dictionary atau validasi pemilik data.
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
