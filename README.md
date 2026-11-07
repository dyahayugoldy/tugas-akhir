# Prediksi Penjualan Ekspor SAP dengan Transformer

Repositori ini menyiapkan implementasi penelitian tugas akhir berjudul **Analisis Komparatif Model Transformer untuk Prediksi Penjualan Ekspor Multivariat dengan Studi Kasus Data SAP Perusahaan XYZ**.

Status saat ini: WP0-WP6 selesai dan diverifikasi. WP6 menghasilkan tiga hasil tuning dan sembilan checkpoint final (3 model × seed 42/123/2026), dengan maksimum 100 epoch dan patience 10. Cohort dan audit resume/resource telah disahkan dengan keterbatasan biaya retry yang dilaporkan; total compute aktual tidak diklaim identik. Evaluasi test belum dilakukan dan tetap menjadi WP7. Bukti tersedia di [reports/wp6-verification.md](reports/wp6-verification.md), dengan lineage data/model pada [reports/wp4-verification.md](reports/wp4-verification.md) dan [reports/wp5-verification.md](reports/wp5-verification.md).

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
| `2023.XLSX` | 22.459 | 34 kolom |
| `2024.XLSX` | 60.052 | 34 kolom |
| `2025.XLSX` | 35.655 | 34 kolom |
| Total | 118.166 | Perlu harmonisasi lintas tahun |

Sempro mendokumentasikan rentang sumber 15 Juli 2023 sampai 31 Desember 2025, dengan rentang efektif Desember 2023 sampai Desember 2025 setelah pemeriksaan awal. Sebanyak 303 transaksi dilaporkan tidak mempunyai tanggal valid. Seluruh angka ini harus diaudit kembali oleh pipeline sebelum dipakai sebagai dasar eksperimen.

Kolom yang relevan antara lain `KP Date`, `CRD Date`, `Order Quantity (Item)`, `Net Price`, `Net Value (Item)`, `Country Key`, `Season`, `Document Currency`, `Material`, `Search Term`, dan status transaksi. Ketiga workbook memiliki header 34 kolom yang identik; pemetaan nama, tipe, dan status kualitasnya dibekukan dalam konfigurasi harmonisasi WP1.

### Catatan kualitas data

- Pengguna mengonfirmasi `Sales group` sebagai brand pada 6 November 2026. Seri memakai kombinasi `country_key` dan `sales_group`; `Search Term` tidak dipakai sebagai brand.
- Target adalah `Order Quantity (Item)` dalam pcs. Baris mata uang IDR dikeluarkan melalui filter bisnis bersama; fitur moneter dikecualikan, sehingga tidak ada konversi kurs pada pipeline aktif.
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

Keputusan pengguna 7 November 2026 mempertahankan cakupan Januari-Desember pada ketiga sumber tahunan. Konfigurasi WP4 `configs/split-window.v1.json` menggunakan semua minggu lengkap berikut, sebagai deviasi terdokumentasi dari usulan sempro:

| Subset | Rentang minggu utuh (termasuk hari terakhir) |
| --- | --- |
| Train | 2 Januari 2023 - 29 Juni 2025 |
| Validation | 30 Juni - 31 Agustus 2025 |
| Test | 1 September - 28 Desember 2025 |

Minggu 30 Juni-6 Juli masuk validation tanpa membelah minggu. Empat minggu lengkap Desember 2025 disertakan; minggu 29 Desember-4 Januari dikecualikan karena melewati cakupan sumber. Minggu lintas tahun internal tetap utuh setelah sumber digabungkan. Kelengkapan seluruh transaksi tetap merupakan asumsi cakupan tahunan karena log ekstraksi tidak tersedia; ini bukan bukti bahwa Desember tidak lengkap. Cakupan bulan yang sama tidak menjamin jumlah minggu atau sampel tiap tahun sama.

Protokol evaluasi adalah rolling-origin satu minggu dengan preprocessing tetap. Aktual validation/test boleh menjadi histori pada origin berikutnya setelah tersedia, tetapi label horizon suatu sampel tidak boleh masuk input sampel tersebut. Tidak ada fit ulang pada holdout.

## Alur kerja yang dituju

```text
XLSX SAP -> validasi + harmonisasi -> filter bisnis + feature contract
         -> EDA + panel mingguan negara-brand + kalender/lag kausal
         -> split kronologis + preprocessing train-only + sliding window
         -> training 3 model -> prediksi holdout
         -> metrik + waktu komputasi -> analisis komparatif
```

Setiap model harus menerima manifest data, target, split, horizon, dan protokol evaluasi yang sama. Perbedaan hanya boleh berasal dari arsitektur dan preprocessing internal yang memang disyaratkan oleh model.

## Dokumen proyek

- [AGENTS.md](AGENTS.md): aturan kerja, data governance, dan kontrak eksperimen.
- [task-breakdown.md](task-breakdown.md): pekerjaan terurut, output, dan kriteria penerimaan.
- [ARCHITECTURE.md](ARCHITECTURE.md): rancangan data, fitur, training, evaluasi, dan artefak.
- `docs/sempro.pdf`: sumber utama ruang lingkup dan metodologi proposal.

## Keputusan yang harus dibekukan sebelum training

1. Sudah ditetapkan: target `Order Quantity (Item)` dalam pcs, dijumlahkan tanpa pembulatan per seri-minggu.
2. Sudah dikonfirmasi: `Sales group` adalah kolom brand; seri memakai kombinasi `country_key` dan `sales_group`.
3. Konversi kurs tidak diperlukan untuk target kuantitas. Baris IDR difilter; fitur moneter masih dikecualikan.
4. WP3 menetapkan input 12 minggu, horizon 1 minggu, dan minggu tanpa transaksi sebagai nol. Seri pendek/baru tetap disimpan dengan kelayakan per origin; asumsi coverage dan capture didokumentasikan.
5. WP4 membekukan rentang kalender di atas, encoder 12 minggu, horizon 1 minggu, scaler/vocabulary train-only, dan indeks sampel bersama.
6. Protokol penalaan hyperparameter yang adil, termasuk anggaran pencarian MoEBS.

Untuk memverifikasi WP0 dari root repositori (Python 3.12):

```powershell
python -m pip install -r requirements.txt
git config core.hooksPath .githooks
python -m unittest discover -s tests -v
python -m src.utils.manifest dummy --config configs/dummy-run.v1.json
python -m src.utils.git_protection
```

Dummy run hanya memakai metadata sintetis dan tidak membaca XLSX SAP atau melatih model. Detail manifest, kebijakan artefak, dan langkah validasi ada di [docs/reproducibility.md](docs/reproducibility.md). Artefak data tetap privat pada `artifacts/data/`; checkpoint dan hasil acceptance WP5 tersimpan privat di `artifacts/runs/`.

Untuk membuat ulang artefak WP1 dari manifest pembersihan aktif, jalankan perintah berikut dari root repositori. Perintah membuat direktori artefak baru dan tidak menimpa maupun mengubah workbook sumber.

```powershell
python -m src.data.harmonize_workbooks --config configs/data-harmonization.v1.json
```

Untuk membuat ulang panel WP3 dari manifest WP2 aktif:

```powershell
python -m src.data.weekly_panel --config configs/panel-weekly.v1.json
```

Perintah membuat run baru di `artifacts/data/weekly_panel/` tanpa mengubah pointer aktif otomatis. Panel, registry, fitur, EDA, dan manifest aktif dicatat pada `configs/current-data.json`. Hasil dan batas asumsi ketersediaan retrospektif tersedia di [reports/wp3-verification.md](reports/wp3-verification.md).

Untuk membuat ulang split dan window WP4 dari panel aktif:

```powershell
python -m src.features.split_windows --config configs/split-window.v1.json
```

Perintah membuat run privat unik di `artifacts/data/windowed/` dan tidak mengganti pointer aktif otomatis. `split_manifest.json` mengikat indeks, tensor NumPy, preprocessing, konfigurasi, kontrak fitur, checksum, dan hasil verifikasi tiga konsumen. `WindowDataset` menyediakan batch input, label, dan metadata terpisah untuk MoEBS, N-Space, dan TFT; adapter PyTorch WP5 memakai artefak ini langsung.

## Model dan acceptance WP5

Ketiga arsitektur menyediakan `fit`, `predict`, `save`, `load`, dan metadata melalui `ForecastAdapter`. Konfigurasi `configs/models.wp5.v1.json` menetapkan CPU float32, seed 42, empat kandidat per model, satu epoch per kandidat, dan batas waktu bersama. MoEBS memakai Pareto search; N-Space/TFT memakai random search dengan budget yang sama. Pemilihan kandidat/checkpoint hanya memakai validation. Ini pemeriksaan implementasi, belum hasil akhir perbandingan.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install torch==2.9.1 --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m src.models.acceptance --config configs/models.wp5.v1.json
```

Runner membuat run unik, checkpoint, prediksi/metrics validation, seluruh trial, source snapshot, manifest dengan checksum, dan satu berkas cohort yang mengikat ketiga run. Test tidak diprediksi pada WP5. Rincian acuan, adaptasi kausal, batas interpretasi, dan rekomendasi resource/seed WP6 ada di [docs/wp5-models.md](docs/wp5-models.md).
