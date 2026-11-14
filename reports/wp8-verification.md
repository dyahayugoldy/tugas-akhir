# WP8: keputusan komparatif

Tanggal: 13 November 2026 (Asia/Jakarta). Status: **selesai**. WP8 memakai paket evaluasi test WP7 yang telah disegel, tanpa membangun ulang data, melatih ulang model, memilih ulang checkpoint, atau mengubah prediksi.

Keputusan untuk data dan protokol ini adalah **rekomendasi bersyarat kepada MoEBS-Transformer sebagai kandidat utama operasional**. Rekomendasi tersebut berasal dari efisiensi yang konsisten lebih rendah dan kepemimpinan rata-rata pada MAE, RMSE, dan R². Ia bukan klaim pemenang akurasi tunggal: variasi tiga seed dan trade-off antar-metrik tidak mendukung klaim tersebut.

## Bukti dan keterlacakan

Sumber tunggalnya adalah paket privat WP7 `artifacts/runs/wp7-evaluation_20261109t040256z_04861126/evaluation.json`, dengan SHA256 `170618cec788f9f45e249f2bb90eeb27af35d62cf18a890ccac7cbb8e9e4d394`. Runner `src.evaluation.comparative_decision` memverifikasi ulang checksum paket, sembilan manifest evaluasi, checkpoint, prediksi, metrik, kontrak split, dan kesamaan digest test sebelum menghitung keputusan WP8.

Paket keputusan privat yang sah adalah `artifacts/runs/wp8-comparison_20261113t043206z_47086301/decision.json`. Ia mengikat source snapshot, konfigurasi `configs/comparison.wp8.v1.json`, dan bukti WP7 melalui checksum. Artefak ini menyimpan analisis detail yang tidak dipublikasikan. Tabel agregat aman untuk pelaporan ada pada `comparison_metrics.csv` dan `comparison_compute.csv`. Tidak ada nilai transaksi, prediksi individual, label negara/brand, pelanggan, PO, atau bobot per sampel di direktori `reports/`.

Semua model menerima 3.187 observasi test yang sama untuk setiap seed. Horizon adalah satu minggu dan split test mencakup 1 September--28 Desember 2025. Ketiga model memakai tiga seed yang sama: 42, 123, dan 2026. Nilai setelah tanda ± adalah standar deviasi sampel antar-seed (`ddof=1`).

## Akurasi holdout

| Model | MAE (pcs) | RMSE (pcs) | MAPE (%) | SMAPE (%) | R² |
| --- | ---: | ---: | ---: | ---: | ---: |
| MoEBS-Transformer | 161,44 ± 26,49 | 968,27 ± 7,74 | 2.129,67 ± 579,60 | 192,53 ± 1,25 | 0,02745 ± 0,01556 |
| N-Space Transformer | 179,21 ± 41,95 | 981,40 ± 14,04 | 2.114,14 ± 1.203,13 | 191,38 ± 0,41 | 0,00079 ± 0,02847 |
| TFT | 218,30 ± 23,29 | 976,34 ± 7,54 | 3.405,82 ± 519,76 | 191,79 ± 1,09 | 0,01118 ± 0,01530 |

MoEBS memiliki rerata terbaik untuk MAE, RMSE, dan R². N-Space memiliki rerata terbaik untuk MAPE dan SMAPE. Namun, MAPE hanya menghitung 485 dari 3.187 observasi per seed (15,2181%) karena aktual nol dikecualikan. SMAPE mendekati 200% karena mayoritas observasi bernilai nol dan prediksi tidak diklip. Kedua metrik persentase tersebut tidak boleh menjadi dasar tunggal pemilihan.

WP8 menerapkan aturan diagnostik yang telah dibekukan: keunggulan rerata suatu pasangan disebut stabil hanya bila selisih rerata melebihi satu standar deviasi gabungan antar-seed. Pada metrik utama, tidak ada model yang stabil unggul atas semua lawan. MoEBS hanya memiliki keunggulan MAE yang melewati ambang terhadap TFT; perbandingannya dengan N-Space serta seluruh pasangan RMSE, SMAPE, dan R² tetap berada dalam variasi seed. Oleh sebab itu, tidak ada pemenang akurasi tunggal dan tidak ada uji signifikansi yang diklaim.

R² rerata seluruh model sangat dekat dengan nol; tiga run memiliki R² negatif. Hasil ini membatasi klaim kemampuan menjelaskan variasi permintaan dan perlu disampaikan bersama metrik error absolut.

## Efisiensi komputasi

| Model | Parameter | Training WP6 final (s) | Inferensi test (s) |
| --- | ---: | ---: | ---: |
| MoEBS-Transformer | 44.841 | 77,01 ± 36,12 | 0,14934 ± 0,00659 |
| N-Space Transformer | 136.521 | 157,73 ± 49,33 | 0,24972 ± 0,00934 |
| TFT | 157.496 | 387,12 ± 125,56 | 0,66884 ± 0,11247 |

MoEBS konsisten memiliki parameter paling sedikit, waktu final training paling rendah, dan waktu inferensi test paling rendah. Training adalah wall-clock final fit WP6, terpisah dari data preparation, pencarian, dan retry historis. Waktu inferensi adalah median lima repetisi pada 3.187 sampel, CPU `float32`, dua thread, dan tidak mencakup feature building, inverse scaling, penulisan, atau perhitungan metrik. Biaya tuning/retry historis tidak identik dan tidak dipakai untuk menyatakan benchmark hardware universal.

## Interpretabilitas TFT

TFT dievaluasi ulang pada test melalui checkpoint WP7 yang sudah dibekukan, tanpa fit. Bobot variable-selection encoder dan decoder dirata-ratakan per seed, lalu urutan fiturnya dibandingkan dengan Spearman untuk semua pasangan seed. Ambang publikasi adalah Spearman minimal 0,8 pada seluruh pasangan.

Kedua kelompok gagal memenuhi ambang: Spearman encoder adalah -0,226; 0,037; dan 0,051, sedangkan decoder 0,324; -0,352; dan -0,165. Attention temporal juga gagal pada dua dari tiga pasangan dan tidak diperlakukan sebagai feature importance. Karena itu, tidak ada ranking atau bobot feature importance TFT yang dipublikasikan. Bobot selection menggambarkan pembobotan model, bukan hubungan kausal.

Dengan bukti saat ini, interpretabilitas TFT belum cukup stabil untuk menjadi alasan memilihnya dibanding dua model lain.

## Jawaban tujuan penelitian

1. Ketiga arsitektur telah dibandingkan pada data, target, horizon, split, dan evaluator holdout yang sama.
2. Akurasinya menunjukkan trade-off dan variasi seed; MoEBS memimpin rerata MAE/RMSE/R², sedangkan N-Space memimpin rerata MAPE/SMAPE. Tidak ada klaim superioritas akurasi tunggal.
3. Untuk penggunaan yang membutuhkan trade-off akurasi rerata dan biaya komputasi, gunakan MoEBS-Transformer sebagai kandidat utama bersyarat. N-Space dan TFT tetap menjadi pembanding sah, bukan model yang gagal.

## Ancaman validitas dan batas rekomendasi

- Cakupan ekstraksi tahunan tidak memiliki log independen; target tersedia memakai proxy retrospektif dengan delay nol hari karena tidak ada timestamp capture/revisi historis.
- Panel diregularisasi dengan minggu tanpa transaksi sebagai nol sesuai keputusan bisnis. Test sangat sparse, sehingga metrik persentase harus dibaca bersama coverage MAPE dan error pada aktual nol/non-nol.
- Test hanya 17 minggu dan variasi tiga seed bukan confidence interval ataupun uji signifikansi. Validation sembilan minggu juga membatasi tuning dan checkpoint selection.
- Evaluasi hanya mencakup window yang memiliki histori cukup; cold start penuh tidak dievaluasi. Kategori baru memakai token unknown dari preprocessing train-only.
- Fitur dibatasi pada kontrak availability: variabel moneter, status operasional, dan field yang belum terbukti tersedia pada origin tidak dipakai. Brand memakai `sales_group` sesuai keputusan bisnis terdokumentasi.
- Tidak ada baseline operasional tambahan pada studi ini, dan adaptasi implementasi berbeda dari domain paper acuan. Rekomendasi berlaku untuk konfigurasi penelitian ini, bukan untuk semua data penjualan.

## Reproduksi

```powershell
.\.venv\Scripts\python.exe -B -m src.evaluation.comparative_decision --verify artifacts/runs/wp8-comparison_20261113t043206z_47086301/decision.json
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -B -m src.utils.git_protection
```

Review pengungkapan: Codex, 13 November 2026. Laporan ini hanya memuat metrik, hitungan, waktu, parameter, metodologi, dan simpulan agregat. Data SAP mentah, identitas bisnis, prediksi rinci, checkpoint, dan bobot per sampel tetap privat.
