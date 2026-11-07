# Architecture

## Prinsip arsitektur

Arsitektur ini dirancang untuk eksperimen peramalan yang adil dan dapat direproduksi. Ketiga model berbagi data kanonis, panel mingguan, split kronologis, target, horizon, dan evaluator yang sama. Perbedaan model dibatasi pada representasi serta komponen arsitekturnya.

```text
SAP XLSX tahunan
  -> ingest dan schema audit
  -> transaksi kanonis + quality report
  -> filter bisnis WP2 + feature contract + pemeriksaan kelayakan
  -> panel mingguan per series
  -> chronological split + window builder
  -> model adapters: MoEBS | N-Space | TFT
  -> standardized predictions + run manifests
  -> metrics, timing, comparison report
```

Tidak ada komponen deployment, API SAP, dashboard operasional, atau write-back ke ERP dalam ruang lingkup penelitian.

## Lapisan dan tanggung jawab

| Lapisan | Tanggung jawab | Artefak utama |
| --- | --- | --- |
| Source | Membaca tiga ekstraksi SAP tanpa memodifikasi sumber | File XLSX read-only, checksum |
| Data quality | Validasi skema, tipe, tanggal, duplikasi, missingness, currency | `data_quality_report`, `data_manifest` |
| Canonical data | Menyatukan nama/tipe kolom dan provenance per tahun | `transactions_canonical` |
| Business selection/feature | Filter bisnis seragam, target, kategori, fitur kalender, leakage policy | transaksi terfilter, `selection_report`, `feature_contract`, `panel_weekly` |
| Split/window | Membuat split kalender dan sample tensor bebas leakage | `split_manifest`, window index |
| Model | Melatih arsitektur dengan interface dan logging seragam | checkpoint, config efektif |
| Evaluation | Menghitung metrik, timing, prediksi, analisis error | predictions, metrics, comparison table |
| Reporting | Menghasilkan tabel/figure teragregasi dan aman dibagikan | `reports/` |

## Kontrak sumber dan harmonisasi

### Berkas sumber

| Atribut | 2023 | 2024 | 2025 |
| --- | ---: | ---: | ---: |
| Nama berkas | `2023.XLSX` | `2024.XLSX` | `2025.XLSX` |
| Transaksi menurut sempro | 22.459 | 60.052 | 35.655 |
| Jumlah kolom | 34 | 34 | 34 |
| Penanda waktu | `KP Date`, `CRD Date` | `KP Date`, `CRD Date` | `KP Date`, `CRD Date` |

Kolom inti lintas tahun mencakup identitas transaksi/item, `Country Key`, `KP Date`, `CRD Date`, `Order Quantity (Item)`, `Net Price`, `Net Value (Item)`, `Season`, `Document Currency`, `Material`, dan beberapa atribut organisasi/produk. Jika matriks skema menemukan kolom yang tidak tersedia pada suatu tahun, nilainya harus tetap tercatat sebagai `null` dalam skema kanonis, bukan diisi dengan nilai rekaan.

Setiap transaksi kanonis minimal membawa:

```text
source_file, source_year, transaction_key, item_key,
kp_date, crd_date, country_key, material, category_or_brand_key,
order_quantity, net_price, net_value, document_currency,
season, sales_office, sales_group, delivery_status, ...
```

Header sumber tidak memiliki kolom bernama `Brand`. Pada 6 November 2026 pengguna mengonfirmasi `Sales group` sebagai kolom brand; kontrak v3 menggunakan `sales_group` yang dinormalisasi sebagai brand key langsung tanpa membuat kategori dari kolom lain.

WP2 memetakan `Sales group`/`sales_group` sebagai kode buyer untuk eksklusi dan, sesuai konfirmasi pengguna 6 November 2026, sebagai kode brand untuk identitas seri. `configs/business-selection.v3.json` mempertahankan filter baris IDR, empat buyer yang dikecualikan, negara ID, dan harga numerik nol, serta kategori eksplisit untuk negara kosong. Kontrak aktif adalah `configs/feature-contract.v3.json`; v1/v2 beserta artefaknya tetap menjadi riwayat keputusan. Pipeline `src/data/business_selection.py` membaca manifest kanonis WP1, memverifikasi checksum sebelum/sesudah, dan menyimpan hasil serta audit alasan eksklusi pada direktori run privat yang baru.

`configs/current-data.json` mempertahankan `active_data_manifest` untuk WP1 dan mencatat `active_business_data_manifest` untuk hasil WP2 bersama `business_data_ready_for_panel`. Semua model harus memakai manifest WP2 yang sama setelah pemeriksaan kelayakan terpenuhi. Baris yang lolos empat filter tetapi memiliki nilai wajib hilang/tidak valid atau numerik negatif tetap disimpan dengan `wp2_quality_holds`; baris ini perlu keputusan sebelum pembuatan panel. Duplikasi hanya dilaporkan, tanpa penghapusan tambahan.

Pengecualian yang telah disetujui: `country_key` kosong menjadi `__UNKNOWN_COUNTRY__`, dengan indikator audit `wp2_country_was_missing`; barisnya tetap dipertahankan tanpa hold negara kosong. Token ini adalah kategori negara yang tidak diketahui, terpisah dari `__UNKNOWN__` untuk kategori baru di encoder. Pipeline menolak benturan token dengan nilai sumber yang sudah terisi. Tidak ada fitting atau inferensi negara dari baris lain.

## Kontrak waktu dan target

### Granularity

Rancangan sempro memilih agregasi mingguan per kombinasi negara tujuan dan merek karena deret harian banyak yang sparse. Bentuk kanonisnya:

```text
series_id = kombinasi (country_key, sales_group)
time_idx  = week_start_date
row       = satu series_id pada satu minggu
```

Pipeline harus menghasilkan kalender mingguan lengkap untuk setiap seri aktif. Keputusan pengguna 6 November 2026 menetapkan minggu tanpa transaksi setelah filter bisnis sebagai permintaan nol (0 pcs), tercatat pada `calendar_regularization` di kontrak v3. Nol ini berlaku pada minggu dengan cakupan sumber lengkap; nilai transaksi hilang/invalid tetap memerlukan quality hold. Panel wajib menyimpan indikator audit `wp3_zero_filled` serta statistik sparsity sebelum/sesudah. Aturan aktivasi/penutupan seri dan minggu parsial harus ditetapkan melalui audit WP3 tanpa menggunakan informasi masa depan.

Fallback `country_only` berarti setiap seri hanya mewakili satu negara: kuantitas seluruh brand di negara tersebut dijumlahkan per minggu. Jumlah seri berkurang dan panel dapat menjadi kurang sparse, tetapi detail per brand hilang. Pilihan ini mengubah unit analisis dari negara × brand menjadi negara dan harus dicatat sebagai deviasi dari sempro bila digunakan. Kontrak v3 menetapkannya tidak dipilih karena mapping brand `sales_group` telah dikonfirmasi; ketiga model tetap memakai seri negara × brand yang sama.

### Target

Keputusan bisnis WP2 menetapkan target utama berikut dalam `configs/feature-contract.v3.json`:

```text
target_name: order_quantity_item
target_unit: pcs
aggregation: sum
aggregation_gate: common_quantity_unit_pcs_confirmed
```

Target kuantitas tidak memerlukan konversi mata uang dan seluruh baris IDR telah dikecualikan. `Net Price` dan `Net Value (Item)` dipertahankan untuk audit tetapi dikecualikan dari fitur model sampai kontrak mata uang dan ketersediaan waktunya disetujui. Tidak ada kurs yang diterapkan. Satuan pcs dan brand `sales_group` telah dikonfirmasi; kuantitas pecahan tetap dipertahankan tanpa pembulatan. Kelayakan input WP2 untuk panel tidak berarti implementasi panel, split, dan audit ketersediaan pada origin telah selesai.

### Ketersediaan fitur pada origin prediksi

| Kelas fitur | Contoh | Aturan |
| --- | --- | --- |
| Static categorical | negara, brand/kategori tervalidasi | Boleh jika stabil untuk seri |
| Known future | kalender minggu, musim yang direncanakan | Boleh hanya bila benar-benar diketahui sebelum horizon |
| Observed past | lag target, lag harga, lag kuantitas | Hanya nilai sampai origin |
| Unknown future | realisasi harga, order, nilai penjualan | Tidak boleh dipakai untuk target horizon |
| Suspect leakage | delivery status, rejection reason, status pemenuhan | Dilarang sampai dibuktikan tersedia saat prediksi |

`KP Date` adalah tanggal dibuatnya Kartu Produksi dan menjadi indeks waktu utama. `CRD Date` adalah tanggal keberangkatan ekspor; artinya sudah dikonfirmasi tetapi waktu ketersediaannya belum terbukti, sehingga dikecualikan dari fitur. `configs/feature-contract.v3.json` mengklasifikasikan seluruh 34 kolom dan kolom provenance. Negara dan brand `sales_group` adalah static categorical untuk identitas seri; kalender merupakan known future; lag target hanya observed past dari data yang tersedia sampai origin. Tidak ada fitting scaler/encoder, imputasi statistik, split, atau penalaan model pada WP2.

## Pembentukan data model

1. Ingest membuat transaksi kanonis dan menandai baris invalid.
2. Seleksi bisnis WP2 menerapkan filter yang sama untuk seluruh tahun, kategori negara unknown yang disetujui, serta target pcs, dan menghasilkan satu manifest bersama dengan pemeriksaan quality holds.
3. WP3 menjumlahkan kuantitas desimal tanpa pembulatan per kombinasi negara-brand-minggu dan menyimpan agregat teramati terpisah dari panel kalender lengkap.
4. Seri aktif sejak minggu transaksi pertama, tanpa baris sebelum aktivasi. Setelah aktif, minggu tanpa transaksi dipertahankan sebagai nol sampai akhir cakupan observasi; transaksi terakhir tidak dianggap bukti penutupan seri. Minggu parsial disimpan sebagai agregat audit dan dikecualikan dari panel.
5. Feature builder menambahkan kalender dan lag dengan tanggal sumber serta waktu ketersediaan. Tabel fitur tidak membawa label target; indikator zero-fill, jumlah transaksi, dan tanggal sumber adalah metadata audit.
6. WP4 membatasi waktu secara kronologis. Vocabulary/scaler fit hanya pada train; kategori baru memakai token `unknown`. Hash `series_id` WP3 adalah identitas tuple yang stabil, bukan encoding kategori yang telah difit.
7. Window builder membentuk pasangan `encoder_window -> forecast_horizon`, menyimpan origin timestamp, dan memanggil `features_at_origin(features, origin, lags, registry)` dengan origin asli untuk menutup lag horizon yang belum tersedia dan seri yang belum dikenal.

Usulan sempro adalah train Desember 2023-Juni 2025, validation Juli-Agustus 2025, dan test September-November 2025. Keputusan pengguna 7 November 2026 mempertahankan Januari-Desember pada setiap sumber tahunan. WP4 mencatat deviasi pada `configs/split-window.v1.json`: train 2 Januari 2023-29 Juni 2025, validation 30 Juni-31 Agustus 2025, test 1 September-28 Desember 2025. Batas selalu Senin-Minggu dan target seluruh horizon harus berada dalam satu subset. Minggu 30 Juni-6 Juli masuk validation; empat minggu lengkap Desember masuk test, sedangkan minggu mulai 29 Desember tetap audit parsial WP3. Klaim kelengkapan transaksi tetap dibatasi asumsi cakupan sumber tahunan tanpa log ekstraksi.

### Artefak WP3 dan batas interpretasi

`python -m src.data.weekly_panel --config configs/panel-weekly.v1.json` menghasilkan run baru di `artifacts/data/weekly_panel/`. Direktori `aggregated/`, `panel/`, dan `features/` memisahkan agregat teramati, label panel/registry, dan kalender/lag. Run menyimpan EDA privat, laporan regularisasi, keputusan input/horizon, kontrak fitur efektif, snapshot konfigurasi/pointer/source, dan checksum input/output. Pointer WP2 tetap tersedia; `active_panel_manifest` dipakai tahap split.

Panjang input dipilih dari dukungan window pada periode pengembangan usulan train saja (4 Desember 2023-29 Juni 2025), dengan mempertahankan minimal 80% jumlah window kandidat terpendek. Hasil v1 adalah encoder 12 minggu dan horizon 1 minggu; kandidat 8/12/26/52 serta perhitungannya disimpan di `window_design.json`. Lag 1/2/4/13 minggu memerlukan 25 minggu histori untuk encoder dengan seluruh lag terisi; seri pendek tetap disimpan dan kelayakan ditentukan ulang per origin pada WP4. Tidak ada pemilihan berdasarkan test atau fitting scaler/encoder pada WP3.

Kalender lengkap dibatasi minggu Senin-Minggu di dalam cakupan ekstraksi tahunan yang dideklarasikan. Audit checksum, tahun KP, keberadaan bulan, dan endpoint tidak membuktikan kelengkapan setiap transaksi tanpa log ekstraksi. Snapshot juga tidak memiliki timestamp capture/revisi, sehingga ketersediaan target memakai proxy retrospektif: Senin setelah minggu berakhir, dengan delay 0 hari yang tercatat di konfigurasi. Ini asumsi eksperimen, bukan verifikasi ketersediaan operasional; WP4 dan pelaporan wajib mempertahankan keterbatasan tersebut.

### Kontrak WP4

`python -m src.features.split_windows --config configs/split-window.v1.json` memverifikasi seluruh output WP3 dan checksum ancestor/sumber, merekonstruksi fitur untuk memeriksa lag, lalu membuat run baru privat. Pointer mutable tidak dibandingkan terhadap checksum pointer historis; snapshot pointer di dalam run tetap diverifikasi. Tidak ada histori sebelum awal eksperimen. Histori pada split sebelumnya boleh menjadi konteks bila tersedia pada origin. Semua seri dipertahankan pada panel; kelayakan window ditentukan pada setiap origin melalui registry `known_at`, 12 minggu encoder, dan lag lengkap 1/2/4/13 minggu. Histori minimum adalah 25 minggu, sehingga awal periode dipakai sebagai warm-up dan bukan seluruh baris panel menjadi label.

Target distandardisasi global dengan mean/std populasi pada baris panel train unik yang tersedia pada cutoff train. Lag dan encoder target memakai scaler target yang sama; kalender memakai scaler per field dari baris train yang sama. Statistik dihitung float64 dan tensor disimpan float32; data desimal WP3 tetap tersedia untuk audit. Fitur konstan memakai scale 1. Vocabulary tiap static field hanya dari seri pada baris train yang telah diketahui, dengan `__UNKNOWN__=0`. `__UNKNOWN_COUNTRY__` adalah kategori bisnis terpisah. Tidak ada imputasi statistik atau seleksi fitur berbasis holdout. Lag decoder tidak tersedia diisi 0 pada ruang skala dengan mask false; nol ini tidak berarti permintaan nol.

Tensor bersama memisahkan `encoder_observed` (target historis dan lag), `encoder_known`, `decoder_known`, `decoder_lags`, mask, dan `static_categorical`. Label `y` dalam pcs serta `y_scaled` dikembalikan terpisah dari input oleh `WindowDataset`. Seri hash adalah metadata, bukan vocabulary embedding. `tensor_schema.json` mencatat urutan field, bentuk, dan dtype. `index/window_index.csv.gz` memuat sample ID stabil, seri, origin, rentang encoder/target hingga akhir minggu, serta waktu ketersediaan maksimum input; `index/panel_split.csv.gz` mempertahankan penugasan seluruh baris panel.

Evaluasi menggunakan rolling origin dengan scaler/vocabulary tetap, bukan satu origin tetap untuk seluruh test. Aktual holdout yang sudah tersedia dapat masuk histori origin selanjutnya; label horizon tidak pernah masuk input sampelnya sendiri. Dilarang menggunakan holdout untuk fit ulang, training, atau pemilihan model. Test kontrafaktual memutasi masa depan, kategori baru, dan label pada origin tertentu untuk membuktikan aturan ini; horizon lebih dari satu diuji terhadap origin asli dan batas split.

Tiga konsumen NumPy (`moebs_transformer`, `nspace_transformer`, `tft`) memakai file tensor/indeks yang sama. Verifikasi membandingkan seluruh sample ID berurutan, nilai tensor, label, dan jumlah sampel pada ketiga loader tanpa membuang batch terakhir. Ini kontrak data bersama WP4; integrasi adapter framework, forward pass, dan training tetap WP5-WP6. `split_manifest.json` baru ditandai `ready_for_models=true` setelah pemeriksaan artefak dan ketiga konsumen berhasil. Setiap run menyimpan konfigurasi, seed, environment/hardware, waktu proses, snapshot source, dan checksum; run lama tidak ditimpa. WP5 harus mengikat satu manifest ini pada ketiga model, bukan membuat ulang split sendiri.

## Interface model umum

Semua adapter model harus mengimplementasikan kontrak logis berikut:

```text
fit(train_data, validation_data, run_config) -> trained_model, run_manifest
predict(windowed_data) -> predictions
save(path) / load(path)
metadata() -> architecture, parameter_count, effective_config
```

Input umum memuat tensor encoder target historis/observed covariates, known covariates, static covariates, dan mask. `series_id` hanya metadata; label horizon terpisah dari input forward. Adapter bertanggung jawab mengubah bentuk tensor umum ke kebutuhan internal tanpa mengubah sample, split, atau horizon.

Implementasi WP5 tersedia melalui `src/models/adapter.py`, `networks.py`, dan `search.py`, dengan satu framework PyTorch. `ModelData` mengikat schema, ukuran vocabulary train, scaler, panjang encoder/horizon, ID dan SHA256 manifest WP4. `fit` menerima train/validation dengan binding yang identik dan mengembalikan model serta catatan training; runner merakit catatan tersebut menjadi run manifest. Checkpoint eksklusif menyimpan state dict, konfigurasi efektif, seed, kontrak data dan catatan training; reload memeriksa checksum/kontrak dan mendukung inferensi, bukan resume optimizer.

`python -m src.models.acceptance --config configs/models.wp5.v1.json` menjalankan acceptance satu epoch dengan budget kandidat setara, menyimpan semua trial/prediksi validation, dan memverifikasi reload persis. `run_type=acceptance` membedakan bukti implementasi dari eksperimen resmi. Cohort mengikat tiga run, checksum manifest, digest sampel/tensor dan jumlah evaluasi yang sama. Test hanya diperiksa integritasnya oleh validator WP4, tidak diprediksi oleh model. Spesifikasi dan deviasi paper lengkap tersedia di [docs/wp5-models.md](docs/wp5-models.md).

### MoEBS-Transformer

Komponen: Transformer forecasting core dan optimasi Multiple Objectives Escaping Bird Search. Konfigurasi wajib menyimpan objective, ruang hyperparameter, populasi, jumlah iterasi/evaluasi, seed, batas waktu, kandidat terpilih, dan nilai validation untuk seluruh kandidat. Objective tidak boleh melihat test set.

WP5 mengimplementasikan gerak predator/prey EBS, archive Pareto dan grid crowding/leader. Objective adalah validation RMSE, validation MAE dan jumlah parameter sebagai proxy biaya. Ruang pencarian meliputi model width, head, learning rate dan weight decay. Penggunaan metrik test pada paper asal diganti validation; aturan pasangan, decoding diskret dan fallback numerik didokumentasikan sebagai adaptasi.

### N-Space Transformer

Komponen: contextual module, N-space transformation, dan encoder-decoder Transformer. Mapping embedding/kategori serta dimensi N-space harus berasal dari train dan dicatat sebagai bagian konfigurasi efektif.

Acuan adalah L'Heureux dkk. (2022), tanpa modul Attention Memory dari varian 2026. Proyeksi N-space linear dipelajari bersama encoder-decoder. Decoder dimulai dari target encoder terakhir yang tersedia, lalu memakai prediksinya sendiri dan konteks yang tersedia pada origin; tidak menerima label masa depan, termasuk saat training multi-step.

### Temporal Fusion Transformer

Komponen: static covariate encoder, variable selection network, gated residual components, recurrent encoder/decoder, dan temporal attention. Bentuk pembagian static, known, dan observed covariates harus mengacu langsung pada feature contract. Feature importance hanya ditafsirkan dari run holdout yang sah dan stabil antar seed.

TFT WP5 memakai empat static context GRN, VSN static/encoder/decoder, LSTM encoder/decoder, gated residual dan static enrichment, serta interpretable attention dengan shared V dan rata-rata head. Adaptasi point output/MSE menggantikan quantile head/loss asli. Bobot selection/attention tersedia untuk analisis kemudian; mask availability melekat pada variabel, bukan fitur bisnis baru.

## Training dan eksperimen

```text
experiment config
  -> resolve data_manifest + split_manifest
  -> load identical sealed WP4 windows (do not rebuild splits)
  -> train one model for each seed
  -> select checkpoint from validation
  -> infer once on sealed test set
  -> write predictions and metrics
```

`experiment config` minimal memuat model, target, series definition, date split, input length, horizon, feature version, seed, batch size, optimizer, learning rate, epoch limit, early-stopping policy, precision, hardware metadata, dan tuning budget.

Fairness berarti dataset/split/evaluator sama, bukan seluruh arsitektur harus memiliki jumlah parameter atau preprocessing internal identik. Jika MoEBS memakai budget optimasi, batas pencarian model lain dan total compute harus dicatat agar pembaca dapat menilai trade-off secara jujur.

WP6 telah ditutup melalui `src/models/cohort.py` dengan plan keanggotaan
`configs/cohort.wp6.v1.json`. Cohort privat mengikat 3 tuning dan 9 final run
dengan checksum, audit checkpoint/prediksi/metrik validation, replay pencarian,
dan rekonsiliasi provenance serta biaya retry. Manifest historis tidak diubah.
Source model/data/evaluator inti sama; perubahan wrapper dicatat pada snapshot.
Empat kandidat seleksi/model sebanding, tetapi compute fisik historis tidak
identik akibat retry. Deviasi serta overhead yang tidak dapat direkonstruksi
diungkapkan pada [reports/wp6-verification.md](reports/wp6-verification.md).
Pembacaan tensor test oleh pemeriksa integritas tidak memberi test ke model;
checkpoint test prediction/evaluation tetap hanya dilakukan pada WP7.

## Evaluasi

Prediction artifact harus memuat paling sedikit:

```text
run_id, model_name, seed, series_id, origin_time, target_time,
actual, prediction, target_unit, horizon_step
```

Evaluator menghasilkan metrik global dan, bila aman serta ukuran sample memadai, metrik per horizon dan kelompok seri:

| Metrik | Aturan |
| --- | --- |
| MAE | Rata-rata error absolut |
| RMSE | Akar rata-rata kuadrat error |
| MAPE | Hanya aktual bukan nol; laporkan coverage |
| SMAPE | Gunakan denominator simetris dan epsilon terversi |
| R^2 | Coefficient of determination; gunakan label `r2` |
| Training time | Wall-clock dari awal fit sampai checkpoint final |
| Inference time | Waktu prediksi pada jumlah sample test yang sama |

Skala normalisasi harus dikembalikan ke satuan target sebelum metrik akhir dihitung. Simpan prediksi per seed agar rerata dan variasi antar run dapat diperiksa.

## Artefak dan provenance

```text
artifacts/
  data/canonical/<manifest_id>/
    canonical_transactions.csv.gz
    quality_report.json
    schema_report.json
    data_manifest.json
    feature_contract.json
    split_manifest.json
  data/business_selected/<manifest_id>/
    selected_transactions.csv.gz
    selection_audit.csv.gz
    selection_report.json
    target_eda.json
    feature_contract.json
    config.resolved.json
    current-data.snapshot.json
    harmonization-config.snapshot.json
    data_manifest.json
  data/windowed/<shared_split_run_id>/
    index/window_index.csv.gz
    index/panel_split.csv.gz
    tensors/train.npz
    tensors/validation.npz
    tensors/test.npz
    preprocessing.json
    tensor_schema.json
    feature_contract.json
    split_report.json
    loader_verification.json
    config.resolved.json
    current-data.snapshot.json
    source/
    split_manifest.json
  runs/<run_id>/
    config.resolved.yaml
    run_manifest.json
    checkpoints/
    predictions.parquet
    metrics.json
    timing.json
reports/
  comparison_metrics.csv
  comparison_compute.csv
  figures/
```

Artefak turunan yang memuat data baris/detail tetap diperlakukan sensitif dan tidak boleh dilacak Git. Hanya tabel agregat serta visualisasi yang telah ditinjau untuk anonimisasi yang boleh masuk `reports/`.

## Test strategy

- **Schema contract test:** nama, tipe, dan kolom wajib tiap sumber.
- **Data quality test:** parser tanggal, currency, duplikasi, serta input kosong.
- **Leakage test:** semua feature time-varying pada contoh hanya berasal dari `time <= origin_time`.
- **Split test:** tidak ada overlap target antar subset dan seluruh model memakai manifest sama.
- **Window test:** bentuk tensor, mask, horizon, dan seri pendek.
- **Metric test:** nilai manual kecil, nilai nol untuk MAPE/SMAPE, dan inverse scaling.
- **Model smoke test:** forward, satu epoch, save/load, dan inference untuk ketiga adapter.
- **Reproducibility test:** konfigurasi dan seed identik menghasilkan artefak yang dapat dibandingkan dalam toleransi terdokumentasi.
