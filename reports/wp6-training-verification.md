# Verifikasi training final WP6

Tanggal pemeriksaan: 7 November 2026 (Asia/Jakarta).
Status: **training final 9/9 selesai dan terverifikasi**. Audit penutupan juga
telah selesai; rujuk [verifikasi akhir WP6](wp6-verification.md) untuk cohort
resmi, koreksi provenance resume dan keterbatasan resource.

## Eksekusi

Ketiga run MoEBS sudah tersedia ketika permintaan melanjutkan training diterima.
Ketiganya diverifikasi dan dipertahankan. Enam command `--train-one` kemudian
dijalankan berurutan: N-Space seed 42, 123, 2026, diikuti TFT seed 42, 123, 2026.
Run berikutnya hanya dimulai setelah proses sebelumnya keluar dengan kode 0
dan mencetak `WP6 final run verified`. Tidak ada run final yang ditimpa.

Seluruh run menggunakan 13.663 window train dan 1.587 window validation dari
manifest `shared_split-window-v1_seed-42_20261107T010824Z_3cba52aa`, maksimum
100 epoch, patience 10, min_delta 0, AdamW/MSE, CPU float32, dua thread dan batch
256. Pemilihan checkpoint berdasarkan RMSE validation minimum. Setiap run
dimulai dari model dan optimizer baru dengan hyperparameter hasil tuning yang
dibekukan untuk arsitektur tersebut.

## Ringkasan run

Waktu di bawah adalah catatan aktual fit termasuk validation/checkpoint
selection, bukan total biaya tuning atau peringkat efisiensi resmi.

| Model | Seed | Epoch selesai | Epoch terbaik | Waktu fit (detik) |
| --- | ---: | ---: | ---: | ---: |
| MoEBS-Transformer | 42 | 14 | 4 | 64,245 |
| MoEBS-Transformer | 123 | 29 | 19 | 117,776 |
| MoEBS-Transformer | 2026 | 12 | 2 | 49,008 |
| N-Space Transformer | 42 | 12 | 2 | 100,800 |
| N-Space Transformer | 123 | 17 | 7 | 184,678 |
| N-Space Transformer | 2026 | 14 | 4 | 187,710 |
| TFT | 42 | 13 | 3 | 244,151 |
| TFT | 123 | 22 | 12 | 437,723 |
| TFT | 2026 | 12 | 2 | 479,483 |

Semua run berhenti sesuai patience 10 dan memulihkan checkpoint terbaik.
Setiap folder run memuat `checkpoint.pt`, `metrics.json`, `predictions.csv`,
`config.resolved.json`, `verification.json` dan `run_manifest.json`.

## Pemeriksaan akhir

Pemeriksaan ulang dilakukan setelah seluruh proses training berhenti:

- Sembilan pasangan model/seed tepat, tanpa pasangan hilang atau ganda.
- Seluruh manifest dan checksum artefak lolos validator WP6.
- ID/SHA256 split serta digest data train/validation sama untuk kesembilan run.
- Arsitektur yang dipilih identik di seluruh seed untuk model yang sama.
- Seluruh epoch memproses semua sampel train/validation; tidak ada batch akhir dibuang.
- `best_epoch` sama dengan posisi RMSE validation minimum; jumlah epoch selesai
  sesuai aturan early stopping.
- Kesembilan checkpoint dimuat ulang dan kembali menghasilkan prediksi
  validation yang cocok dengan CSV tersimpan (rtol 1e-12, atol 1e-9 untuk
  roundtrip CSV), dengan urutan sample ID identik.
- MAE, RMSE, MAPE, SMAPE dan r2 dihitung ulang dan cocok dengan `metrics.json`;
  RMSE checkpoint cocok dengan minimum riwayat epoch.
- Prediksi test oleh model tetap nol. Pemeriksaan integritas WP4 dapat membaca
  tensor test; tidak ada fit, seleksi, atau evaluasi model menggunakan test.

Bukti privat yang mengikat path dan SHA256 kesembilan manifest:
`artifacts/runs/wp6-final-grid-verification_20261107T141459Z.json`.
Berkas ini merupakan verifikasi grid final training, bukan sertifikasi cohort
tuning/final WP6 secara keseluruhan.

## Penutupan audit

Audit resume, pengujian penutupan, rekonsiliasi biaya dan pengesahan cohort
telah dilakukan pada [verifikasi akhir WP6](wp6-verification.md). Artefak donor
menunjukkan trial TFT keempat selesai lalu diulang pada sesi lanjutan dengan
prediksi identik. Biaya retry dan keterbatasan pengukuran diungkapkan dalam
laporan akhir; manifest lama dipertahankan. WP6 selesai dengan keterbatasan
tersebut, sedangkan evaluasi test tetap WP7.

Review pengungkapan: laporan hanya memuat metode, ID manifest, hitungan sampel,
seed, epoch dan waktu komputasi. Tidak ada identitas bisnis, transaksi, scaler,
bobot model, atau prediksi rinci yang disalin ke laporan.
