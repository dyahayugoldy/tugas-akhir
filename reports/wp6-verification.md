# Verifikasi akhir WP6

Tanggal: 7 November 2026 (Asia/Jakarta).
Status: **selesai dengan keterbatasan resource yang dilaporkan**. Tiga hasil
tuning, dua belas checkpoint kandidat, dan sembilan checkpoint final telah
diaudit. Evaluasi test tetap menjadi pekerjaan WP7; jumlah prediksi test nol.

## Cohort yang disahkan

Cohort privat yang menjadi rujukan resmi:
`artifacts/runs/wp6-closure_20261107T143831Z_6dc939f2/cohort.json`.
Plan keanggotaannya adalah `configs/cohort.wp6.v1.json`; setiap anggota diikat
oleh path dan SHA256 manifest, bukan dipilih otomatis dari run terbaru atau
berdasarkan skor yang paling baik. Ketiga run tuning yang dipakai adalah:

- MoEBS: `moebs_transformer_models-wp6-v1_seed-2026_tuning_20261107t104004730909z_c9293bdb3347`.
- N-Space: `nspace_transformer_models-wp6-v1_seed-2026_tuning_20261107t104710646287z_9517aecde0ee`.
- TFT: `tft_models-wp6-v1_seed-2026_tuning_20261107t112249030004z_6f533530b639`.

Masing-masing terhubung dengan final run seed 42, 123, dan 2026. Daftar lengkap
sembilan manifest final beserta checksum tersimpan di plan dan berkas cohort.
Riwayat epoch dan waktu final per seed tersedia di
[laporan training](wp6-training-verification.md).

## Kontrak yang terbukti

Semua anggota memakai manifest split
`shared_split-window-v1_seed-42_20261107T010824Z_3cba52aa`, dengan 13.663 window
train dan 1.587 window validation; encoder 12 minggu, horizon 1 minggu, dan
target kuantitas pcs. Pemeriksaan ulang WP4 atas checksum output, ancestor,
indeks, schema dan ketiga konsumen juga lulus. Pemeriksaan integritas ini dapat
membaca tensor test; model WP6 hanya menerima train/validation.

Konfigurasi `models.wp6.v1.json` menetapkan CPU float32, dua thread, batch 256,
AdamW/MSE, gradient clipping, shuffle dengan seed, maksimum 100 epoch,
min_delta 0 dan patience 10. Semua sampel diproses setiap epoch. Bobot terbaik
dipulihkan berdasarkan RMSE validation. Ketiga model memakai empat kandidat
seleksi, ruang parameter dan batas 3.600 detik/fit serta 14.400 detik/search
yang sama. Hyperparameter terpilih dibekukan sebelum final run; semua final
run memakai model dan optimizer baru. Tidak ada training pada validation atau
test dan tidak ada refit scaler/encoder.

Auditor memuat ulang 12 checkpoint trial dan 12 checkpoint tingkat run
(3 tuning terpilih + 9 final), serta 4 checkpoint donor TFT. Prediksi disamakan
dengan NPZ/CSV yang tersimpan, termasuk urutan sample ID. Metrik MAE, RMSE,
MAPE, SMAPE dan r2 dihitung ulang pada pcs; seluruh nilai cocok. Coverage MAPE,
epsilon SMAPE dan perlakuan r2 tidak terdefinisi turut diperiksa. Auditor
memeriksa seluruh urutan early stopping, parameter optimizer final, checksum
source snapshot, serta 24 dependency source inti yang identik antaranggota.
Versi wrapper `experiment.py` yang berbeda tetap tercatat pada snapshot asli;
source model, adapter, pencarian, data dan metrik inti tidak berubah.

Replay deterministik MoEBS/random search memakai objective validation yang
tercatat dan kembali menghasilkan urutan kandidat, Pareto set, dan kandidat
terpilih yang sama. Inferensi validation setiap final run menggunakan satu
warm-up dan lima repetisi pada jumlah sampel yang sama. Ini bukan benchmark
inferensi test WP7.

## Koreksi audit resume TFT

Donor TFT adalah
`tft_models-wp6-v1_seed-2026_tuning_20261107t105738704984z_5fe4332a7fcf`.
Penelusuran artefak menunjukkan keempat trial donor ternyata selesai. Dugaan
sebelumnya bahwa host memutus trial keempat tidak sesuai dengan artefak akhir.
Run MoEBS kedua yang pernah diberi catatan terputus juga ternyata menyelesaikan
keempat trial dan manifestnya. Catatan awal dipertahankan sebagai riwayat,
bukan dianggap sebagai fakta penutupan.

Pada resume TFT, trial 0-2 disalin identik byte-per-byte untuk checkpoint,
checksum checkpoint, training record dan prediksi. Trial 3 dihitung ulang.
Seluruh kandidat, objective, urutan sample dan prediksi donor/resume identik;
kandidat terpilih tetap trial 1. Tidak ada kandidat baru atau pergantian
pemenang karena resume. Seluruh berkas donor diikat SHA256 pada `audit.json`.

Pemisahan biaya yang benar adalah:

- Search sesi resume yang tercatat semula: **655,711 detik**.
- Search empat kandidat cohort setelah memasukkan evaluasi donor trial 0-2:
  **1.500,993 detik**, terdiri dari evaluasi lama yang dipakai ulang dan sesi
  lanjutannya; tanpa waktu jeda di antara sesi.
- Jumlah search sesi donor penuh + resume: **2.222,101 detik**. Ini mencakup
  trial 3 donor dan pengulangannya.
- Biaya fit tambahan untuk trial 3 yang diulang: **650,576 detik**.

Angka `search_seconds` pada manifest lama tidak ditimpa. Koreksi serta hubungan
donor/resume dicatat secara tambahan di audit cohort.

## Akuntansi resource dan batas klaim fairness

| Model | Kandidat seleksi cohort | Fit tuning fisik yang tercatat | Fit kandidat cohort (s) | Search cohort direkonsiliasi (s) | Seluruh fit tuning tercatat termasuk retry (s) |
| --- | ---: | ---: | ---: | ---: | ---: |
| MoEBS-Transformer | 4 | 12 | 419,015 | 424,065 | 1.376,519 |
| N-Space Transformer | 4 | 5 | 625,660 | 626,562 | 765,405 |
| TFT | 4 | 5 | 1.493,770 | 1.500,993 | 2.213,350 |

Fit fisik menghitung kerja nyata; salinan trial TFT 0-2 tidak dihitung dua
kali. Dua run MoEBS dan satu run N-Space lama dikeluarkan dari keanggotaan,
tetapi biaya fit yang tercatat tetap dijumlahkan. Semua trial selesai yang
dikeluarkan terbukti mengulang konfigurasi dan prediksi kandidat cohort,
sehingga tidak memperluas ruang seleksi. Bukti dan checksum run tersebut
tersimpan pada `excluded_runs` dalam audit.

Anggaran seleksi dan aturan final sebanding, tetapi **total compute aktual
tidak identik**. Biaya pekerjaan terputus yang tidak memiliki record lengkap,
startup dan overhead lain tidak dapat direkonstruksi tepat; tabel seluruh fit
tercatat adalah batas bawah biaya historis, bukan total biaya proyek yang
lengkap. Jangan mengklaim seluruh retry bebas biaya atau total compute sama.
Keterbatasan ini memenuhi ketentuan proyek untuk melaporkan deviasi resource.

Peak memory CPU tidak tersedia. Beban sistem, contention dan kondisi termal
tidak dipantau terus-menerus, sehingga waktu aktual bukan benchmark hardware
terkontrol untuk mengklaim pemenang efisiensi. Validation hanya sembilan minggu
kalender; tuning dan checkpoint selection memakai validation yang sama. Seed
final 2026 sama dengan seed tuning, meskipun model diinisialisasi ulang.
Asumsi kelengkapan sumber tahunan dan availability retrospektif WP3/WP4 tetap
berlaku. Sesi donor TFT tidak mempunyai manifest environment lengkap sendiri;
kontrak data, source inti, seed dan runtime deklaratifnya diverifikasi dari
checkpoint dan snapshot donor.

## Pengujian dan reproduksi audit

Seluruh **83 test lulus**, termasuk delapan test penutupan WP6: pemulihan bobot
terbaik setelah patience 10, penolakan early stop prematur/terlambat, replay
search, lineage/byte-copy resume dan akuntansi waktu, penolakan grid tidak
lengkap/config berubah, isolasi path, deteksi perubahan bukti, dan routing
command verifikasi cohort. Tiga test single-run WP6 juga tetap lulus.

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -B -m src.models.experiment --verify-cohort artifacts/runs/wp6-closure_20261107T143831Z_6dc939f2/cohort.json
.\.venv\Scripts\python.exe -B -m src.features.window_dataset --manifest artifacts/data/windowed/shared_split-window-v1_seed-42_20261107T010824Z_3cba52aa/split_manifest.json --verify-inputs
.\.venv\Scripts\python.exe -B -m src.utils.git_protection
git diff --check
```

`src.models.cohort --verify <cohort.json>` melakukan audit penuh yang sama.
`src.models.cohort --plan configs/cohort.wp6.v1.json` membuat paket audit baru
secara eksklusif tanpa training atau menimpa bukti lama. Paket memuat cohort,
snapshot plan, audit, dan snapshot source verifier dengan checksum.

Review pengungkapan: Codex, 7 November 2026. Laporan berasal dari cohort privat,
hasil audit checkpoint/manifest/source dan test sintetis. Hanya metode, ID run,
hitungan sampel/trial, seed dan waktu agregat yang disertakan. Prediksi rinci,
bobot, parameter scaler, kategori bisnis dan transaksi tetap privat. WP6 tidak
menentukan pemenang model; evaluasi test dan analisis perbandingan tetap WP7-WP8.
