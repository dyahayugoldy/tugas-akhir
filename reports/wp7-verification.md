# Verifikasi WP7: evaluasi test dan analisis error

Tanggal: 9 November 2026 (Asia/Jakarta).
Status: **selesai untuk sembilan final checkpoint WP6**. Terdapat 3.187 prediksi
test per run, total 28.683 baris prediksi dari tiga model dan tiga seed.
Seluruh prediksi, metrik, analisis error dan ringkasan telah diverifikasi ulang.
Keputusan komparatif akurasi, efisiensi dan interpretabilitas tetap WP8.

## Artefak resmi dan kontrak

Paket privat:
`artifacts/runs/wp7-evaluation_20261109t040256z_04861126/evaluation.json`.
SHA256 paket:
`170618cec788f9f45e249f2bb90eeb27af35d62cf18a890ccac7cbb8e9e4d394`.

Paket mengikat sembilan manifest evaluasi dengan path dan SHA256, serta tabel
`per-seed.csv` dan `model-summary.csv`. Setiap manifest evaluasi menyimpan
snapshot manifest WP6, salinan checkpoint identik byte, konfigurasi efektif,
source, environment, hardware, prediksi, metrik, analisis error, dan checksum.
Pasangan model/seed pada tabel di bawah dipetakan secara unik ke run melalui
`per-seed.csv` dan daftar `runs` pada paket resmi.

Input tetap cohort
`artifacts/runs/wp6-closure_20261107T143831Z_6dc939f2/cohort.json`, yang dipin
sebelum prediksi test oleh `configs/evaluation.wp7.v1.json`. Split tetap
`shared_split-window-v1_seed-42_20261107T010824Z_3cba52aa`: encoder 12 minggu,
horizon 1 minggu, target kuantitas pcs, test 1 September-28 Desember 2025
(17 minggu lengkap). Semua model/seed menerima sampel dan digest tensor test
yang identik. Scaler dan vocabulary tetap hasil train WP4.

Evaluasi memakai rolling origin sesuai WP4: histori holdout yang telah tersedia
boleh masuk window origin berikutnya. Label horizon sendiri terpisah dari input.
Tidak ada fitting, pemilihan checkpoint, tuning, kalibrasi, clipping, pembulatan,
atau penggantian split dalam WP7. Penyesuaian berdasarkan hasil test tidak
dilakukan. Salinan checkpoint evaluasi cocok dengan checkpoint final resmi WP6.

Tiga XLSX sumber telah diubah atributnya menjadi read-only; checksum sebelum
dan sesudah identik. Pemeriksaan ancestor WP4 serta cohort WP6 juga lulus.
Perubahan atribut tidak memerlukan pembangunan data atau training ulang.

## Metrik global

Mean dan standar deviasi sampel (ddof=1) dihitung atas tiga seed 42/123/2026,
bukan dari prediksi yang dirata-ratakan. MAE/RMSE dalam pcs, MAPE/SMAPE dalam
persen, dan `r2` adalah coefficient of determination.

| Model | MAE mean ± SD | RMSE mean ± SD | MAPE mean ± SD (%) | SMAPE mean ± SD (%) | r2 mean ± SD |
| --- | ---: | ---: | ---: | ---: | ---: |
| MoEBS-Transformer | 161,44 ± 26,49 | 968,27 ± 7,74 | 2.129,67 ± 579,60 | 192,53 ± 1,25 | 0,02745 ± 0,01556 |
| N-Space Transformer | 179,21 ± 41,95 | 981,40 ± 14,04 | 2.114,14 ± 1.203,13 | 191,38 ± 0,41 | 0,00079 ± 0,02847 |
| TFT | 218,30 ± 23,29 | 976,34 ± 7,54 | 3.405,82 ± 519,76 | 191,79 ± 1,09 | 0,01118 ± 0,01530 |

Metrik per seed dibulatkan untuk tampilan; presisi penuh tersedia pada
`per-seed.csv` privat:

| Model | Seed | MAE (pcs) | RMSE (pcs) | MAPE (%) | SMAPE (%) | r2 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| MoEBS | 42 | 166,603 | 961,308 | 2.353,752 | 191,727 | 0,041420 |
| MoEBS | 123 | 132,741 | 966,898 | 1.471,488 | 193,973 | 0,030239 |
| MoEBS | 2026 | 184,969 | 976,601 | 2.563,780 | 191,897 | 0,010680 |
| N-Space | 42 | 227,534 | 965,213 | 3.499,177 | 191,713 | 0,033616 |
| N-Space | 123 | 152,134 | 990,241 | 1.328,006 | 191,504 | -0,017150 |
| N-Space | 2026 | 157,949 | 988,758 | 1.515,222 | 190,919 | -0,014106 |
| TFT | 42 | 192,515 | 973,241 | 2.821,352 | 191,210 | 0,017476 |
| TFT | 123 | 237,794 | 970,837 | 3.816,154 | 193,050 | 0,022322 |
| TFT | 2026 | 224,593 | 984,931 | 3.579,962 | 191,114 | -0,006270 |

Seluruh run memakai 3.187 observasi yang sama. MAPE hanya mengevaluasi **485
aktual bukan nol (15,2181%)**; 2.702 observasi lain bernilai nol. Observasi nol
tetap masuk MAE, RMSE, SMAPE, dan r2. Epsilon SMAPE tetap 0,000001. MAPE tidak
dibatasi ke 100%; angka yang tinggi dipertahankan sesuai formula. SMAPE untuk
aktual nol dengan prediksi bukan nol bernilai 200%, sehingga dominasi minggu nol
penting saat membaca angka mendekati 200%. MAPE tidak mengukur error pada
84,7819% minggu yang bernilai nol dan sensitif terhadap denominator aktual kecil.

Mean r2 mendekati nol pada ketiga model, dan tiga run memiliki r2 negatif.
Ini menunjukkan kemampuan menjelaskan variasi target test secara global masih
rendah. R2 di sini menggunakan mean aktual test sebagai referensi matematis;
tidak ada model baseline operasional tambahan yang dilatih atau dibandingkan.
Urutan metrik tidak dijadikan kesimpulan pemenang tunggal. SD antar seed hanya
dispersi eksperimen, bukan confidence interval atau uji signifikansi.

## Analisis error

Residual didefinisikan sebagai prediksi dikurangi aktual. Mean residual antar
seed adalah -12,23 pcs untuk MoEBS, +19,29 pcs untuk N-Space, dan +67,29 pcs
untuk TFT. Mean ini dapat menutupi underprediction dan overprediction yang
saling meniadakan; distribusi dan kuantil residual lengkap tersimpan privat.
Korelasi lag-one yang dipool pada pasangan residual minggu bersebelahan berada
antara -0,0056 dan 0,0738 pada sembilan run. Nilai tersebut tidak membuktikan
residual independen atau normal karena dependensi antar seri/komposisi sampel.

| Model | Prediksi negatif seed 42 / 123 / 2026 | MAE mean pada aktual nol (pcs) | MAE mean pada aktual bukan nol (pcs) | MAE mean seri sparse train (pcs) |
| --- | ---: | ---: | ---: | ---: |
| MoEBS | 369 / 1.504 / 581 | 78,86 | 621,49 | 90,50 |
| N-Space | 199 / 19 / 18 | 97,53 | 634,24 | 109,43 |
| TFT | 243 / 263 / 123 | 140,89 | 649,58 | 144,20 |

Prediksi negatif tetap masuk seluruh metrik; jumlahnya menunjukkan keluaran
unconstrained belum selalu sesuai domain permintaan non-negatif. Clipping tidak
diterapkan setelah melihat test. Setiap run mengevaluasi 485 minggu permintaan
bukan nol dari 111 seri serta 2.702 minggu nol dari 197 seri; kedua populasi
seri dapat beririsan.

Seri sparse ditetapkan sebelum test sebagai seri dengan minimal 80% minggu
train bernilai nol pada panel unik yang tersedia saat cutoff train. Kelompok
ini mencakup 2.476 window dari 149 seri. Kelompok train lainnya mencakup 708
window dari 47 seri. Tiga window seri tanpa histori train disimpan tetapi
metrik kelompok kecilnya disupresi. Status sparse tidak memakai distribusi test.
Perbedaan besaran target antar kelompok membatasi perbandingan MAE lintas
kelompok; MAE sparse yang lebih kecil tidak membuktikan masalah sparsity teratasi.

Diagnostik juga mencakup horizon, negara, brand (sales_group), bulan target,
seri dan target di atas kuantil 0,99 train. Ambang, source dan cutoff
diagnostik tercatat privat, hanya dihitung dari train dan tidak memfilter test.
Kelompok memerlukan minimal 20 observasi dan tiga seri untuk metriknya.
Kelompok per-seri memerlukan 20 observasi; tidak ada seri individual yang
memenuhi ambang pada test 17 minggu. Pada setiap run, 19 kelompok negara,
22 kelompok brand, empat bulan dan satu horizon memenuhi syarat. Label kategori
dan angkanya tetap privat. Empat observasi di atas ambang tail train tidak
diterbitkan sebagai metrik kelompok karena terlalu kecil; seluruhnya tetap
masuk metrik global dan daftar error terbesar privat. Tidak ada identitas atau
nilai observasi individual pada laporan ini.

## Waktu dan resource

| Model | Parameter | Training WP6 mean ± SD (s) | Inferensi test median per run, mean ± SD (s) |
| --- | ---: | ---: | ---: |
| MoEBS | 44.841 | 77,010 ± 36,118 | 0,14934 ± 0,00659 |
| N-Space | 136.521 | 157,729 ± 49,325 | 0,24972 ± 0,00934 |
| TFT | 157.496 | 387,119 ± 125,562 | 0,66884 ± 0,11247 |

Inferensi menggunakan CPU float32, dua thread, batch 256 dan seluruh 3.187
sampel per run. Satu warm-up dilanjutkan lima repetisi terukur yang menghasilkan
prediksi identik; angka tabel merata-ratakan median kelima repetisi tiap seed.
Timing mengikuti cakupan adapter: konversi batch, forward dan pengumpulan
output. Loading, pembangunan fitur, inverse scaling, penulisan dan evaluasi
metrik berada di luar interval tersebut. Lingkungan dan versi paket tersimpan
pada setiap manifest. Angka training adalah riwayat final fit WP6 yang ditautkan;
WP7 mencatat waktu fitting baru nol.

Beban host dan kondisi termal tidak dikendalikan; peak memory CPU tidak tersedia.
Angka ini merupakan pengukuran lokal dengan protokol sama, bukan benchmark
hardware terkontrol. Biaya tuning/retry WP6 tetap mengikuti rekonsiliasi di
[verifikasi WP6](wp6-verification.md), bukan ditambahkan ke waktu inferensi.

## Pengujian, reproduksi dan batas validitas

Seluruh **87 test lulus**: 83 test terdahulu dan empat test WP7. Test WP7
mencakup grid sembilan model/seed, horizon dua, urutan dan jumlah prediksi,
round-trip checkpoint/CSV/metrik, penolakan artefak berubah, larangan memanggil
fit, kebijakan prediksi tanpa clipping, nilai nol dan r2 tidak terdefinisi,
prediksi negatif, supresi kelompok kecil, dan threshold diagnostik train-only.
Fixture integrasi menggunakan checkpoint sintetis yang tidak dilatih; data SAP
tidak dipakai pada test. Audit artefak resmi memuat ulang checkpoint nyata dan
menghitung ulang hasil secara deterministik.

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -B -m src.evaluation.test_evaluation --verify artifacts/runs/wp7-evaluation_20261109t040256z_04861126/evaluation.json
.\.venv\Scripts\python.exe -B -m src.utils.git_protection
```

Perintah eksekusi baru, bila diperlukan untuk reproduksi konfigurasi yang sama,
adalah `python -B -m src.evaluation.test_evaluation --config configs/evaluation.wp7.v1.json`.
Perintah tersebut membuat ID dan artefak baru; prediksi deterministik harus sama,
sedangkan waktu dan ID run akan berbeda. Protokol lengkap ada di
[docs/wp7-evaluation-protocol.md](../docs/wp7-evaluation-protocol.md).

Batas validitas WP3/WP4 tetap berlaku: kelengkapan cakupan tahunan tidak terbukti
dengan log ekstraksi independen, dan timestamp capture/revisi historis tidak
tersedia. Ketersediaan target memakai proxy retrospektif delay nol. Pengujian
meliputi window eligible dengan histori cukup, sehingga bukan penilaian cold-start
menyeluruh. Validation hanya sembilan minggu dan dipakai untuk tuning serta
checkpoint selection; variasi tiga seed tidak mencakup ketidakpastian pemilihan
split. Precision label mengikuti tensor float32 WP4; desimal asli tetap pada
panel privat. Keterbatasan resource historis WP6 juga tetap dilaporkan.

Review pengungkapan: Codex, 9 November 2026. Sumber laporan adalah paket resmi
WP7, metrik per-seed/global, dan analisis error privat. Hanya metode, waktu,
parameter, hitungan dan metrik agregat yang disertakan. Tidak ada nama perusahaan,
pelanggan, PO, label kategori bisnis, prediksi atau nilai transaksi individual.
Data rinci, checkpoint dan source snapshot tetap di `artifacts/` yang diabaikan
Git. Pemeriksaan Git data protection dan whitespace pada perubahan tahap ini
lulus.
