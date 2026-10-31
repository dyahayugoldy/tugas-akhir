# Prediksi Penjualan Ekspor SAP dengan Transformer

Repositori ini menyiapkan implementasi penelitian tugas akhir berjudul **Analisis Komparatif Model Transformer untuk Prediksi Penjualan Ekspor Multivariat dengan Studi Kasus Data SAP Perusahaan XYZ**.

Status saat ini: fondasi reproduksibilitas dan tata kelola WP0 tersedia beserta dummy run sintetis dan pengujiannya. Pipeline SAP, model, dan hasil eksperimen belum diimplementasikan dalam repositori ini.

## Ringkasan penelitian

Penelitian menguji tiga arsitektur pada kondisi data dan evaluasi yang sama:

| Model | Fokus perbandingan |
| --- | --- |
| MoEBS-Transformer | Transformer dengan penalaan hyperparameter melalui Multiple Objectives Escaping Bird Search |
| N-Space Transformer | Representasi multivariat melalui contextual module dan N-space transformation sebelum encoder-decoder |
| Temporal Fusion Transformer | Integrasi fitur statis/dinamis dan interpretabilitas melalui variable selection network |

Tujuannya adalah menentukan arsitektur yang paling sesuai untuk peramalan penjualan ekspor berbasis SAP, berdasarkan MAE, RMSE, MAPE, SMAPE, R^2, serta waktu pelatihan dan inferensi. Penelitian tidak mencakup integrasi/deployment ke ERP SAP.

## Dataset yang tersedia

Sumber data berada di `dataset/` dan terdiri dari ekstraksi modul Sales & Distribution SAP:

| Berkas | Jumlah transaksi menurut sempro | Skema |
| --- | ---: | --- |
| `VAL 2023.XLSX` | 22.459 | 34 kolom |
| `VAL 2024.XLSX` | 60.052 | 34 kolom |
| `VAL 2025.XLSX` | 35.655 | 27 kolom |
| Total | 118.166 | Perlu harmonisasi lintas tahun |

Sempro mendokumentasikan rentang sumber 15 Juli 2023 sampai 31 Desember 2025, dengan rentang efektif Desember 2023 sampai Desember 2025 setelah pemeriksaan awal. Sebanyak 303 transaksi dilaporkan tidak mempunyai tanggal valid. Seluruh angka ini harus diaudit kembali oleh pipeline sebelum dipakai sebagai dasar eksperimen.

Kolom yang relevan antara lain `KP Date`, `CRD Date`, `Order Quantity (Item)`, `Net Price`, `Net Value (Item)`, `Country Key`, `Season`, `Document Currency`, `Material`, `Search Term`, dan status transaksi. Skema 2025 menghilangkan beberapa kolom yang tersedia pada 2023-2024, termasuk `Remark Item`, `Reason for Rejection`, `Search term 1`, `SKUCODE`, `Setcode`, dan `WH Code`.

### Catatan kualitas data

- `Brand` disebut sebagai fitur kontekstual di sempro, tetapi tidak ditemukan sebagai nama kolom eksplisit. Hubungan `Search Term` atau atribut lain terhadap brand perlu dikonfirmasi dengan data dictionary/perusahaan.
- `Net Value (Item)` dicatat dalam USD dan IDR. Sempro mencatat 117.824 baris USD dan 341 baris IDR; nilai IDR harus dikonversi ke USD dengan kurs historis bertanggal sebelum menjadi target atau fitur numerik bersama.
- Field status atau informasi yang terbentuk setelah transaksi tidak boleh menjadi fitur prediksi tanpa pembuktian bahwa nilainya sudah diketahui pada waktu prediksi.
- Data SAP bersifat sensitif dan tidak boleh dipublikasikan atau dilacak Git.

## Desain eksperimen

Rancangan sempro menggunakan pendekatan kuantitatif dengan eksperimen komparatif. Unit transaksi akan dibersihkan, diharmonisasi, lalu diagregasikan mingguan per kombinasi negara tujuan dan merek yang telah divalidasi, karena banyak seri kategori bersifat jarang pada tingkat harian.

Pembagian data harus kronologis. Usulan sempro adalah:

| Subset | Usulan periode |
| --- | --- |
| Train | Desember 2023 - Juni 2025 |
| Validation | Juli - Agustus 2025 |
| Test | September - November 2025 |
| Dikeluarkan | Juli 2023 yang tidak representatif dan Desember 2025 bila bulan tidak lengkap |

Rentang tersebut adalah konfigurasi awal, bukan asumsi tetap. Pipeline harus memverifikasi kelengkapan data Desember 2025 dan membekukan `data_manifest` sebelum training pertama.

## Alur kerja yang dituju

```text
XLSX SAP -> validasi + harmonisasi -> konversi mata uang -> EDA
         -> agregasi seri mingguan -> feature contract + sliding window
         -> split kronologis -> training 3 model -> prediksi holdout
         -> metrik + waktu komputasi -> analisis komparatif
```

Setiap model harus menerima manifest data, target, split, horizon, dan protokol evaluasi yang sama. Perbedaan hanya boleh berasal dari arsitektur dan preprocessing internal yang memang disyaratkan oleh model.

## Dokumen proyek

- [AGENTS.md](AGENTS.md): aturan kerja, data governance, dan kontrak eksperimen.
- [task-breakdown.md](task-breakdown.md): pekerjaan terurut, output, dan kriteria penerimaan.
- [ARCHITECTURE.md](ARCHITECTURE.md): rancangan data, fitur, training, evaluasi, dan artefak.
- `docs/sempro.pdf`: sumber utama ruang lingkup dan metodologi proposal.

## Keputusan yang harus dibekukan sebelum training

1. Target utama: kuantitas pesanan, nilai penjualan USD, atau target lain hasil EDA.
2. Pemetaan field brand yang tervalidasi untuk agregasi seri.
3. Sumber dan metodologi kurs historis IDR ke USD.
4. Panjang input window, horizon prediksi, dan strategi penanganan seri baru/sangat sparse.
5. Batas final train/validation/test setelah audit kelengkapan tanggal.
6. Protokol penalaan hyperparameter yang adil, termasuk anggaran pencarian MoEBS.

Untuk memverifikasi WP0 dari root repositori (Python 3.12):

```powershell
python -m pip install -r requirements.txt
git config core.hooksPath .githooks
python -m unittest discover -s tests -v
python -m src.utils.manifest dummy --config configs/dummy-run.v1.json
python -m src.utils.git_protection
```

Dummy run hanya memakai metadata sintetis dan tidak membaca XLSX SAP atau melatih model. Detail manifest, kebijakan artefak, dan langkah validasi ada di [docs/reproducibility.md](docs/reproducibility.md). Tahap berikutnya adalah WP1, audit dan harmonisasi data.
