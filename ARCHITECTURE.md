# Architecture

## Prinsip arsitektur

Arsitektur ini dirancang untuk eksperimen peramalan yang adil dan dapat direproduksi. Ketiga model berbagi data kanonis, panel mingguan, split kronologis, target, horizon, dan evaluator yang sama. Perbedaan model dibatasi pada representasi serta komponen arsitekturnya.

```text
SAP XLSX tahunan
  -> ingest dan schema audit
  -> transaksi kanonis + quality report
  -> konversi IDR ke USD + feature contract
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
| Feature | Konversi mata uang, target, kategori, fitur kalender, leakage policy | `feature_contract`, `panel_weekly` |
| Split/window | Membuat split kalender dan sample tensor bebas leakage | `split_manifest`, window index |
| Model | Melatih arsitektur dengan interface dan logging seragam | checkpoint, config efektif |
| Evaluation | Menghitung metrik, timing, prediksi, analisis error | predictions, metrics, comparison table |
| Reporting | Menghasilkan tabel/figure teragregasi dan aman dibagikan | `reports/` |

## Kontrak sumber dan harmonisasi

### Berkas sumber

| Atribut | 2023 | 2024 | 2025 |
| --- | ---: | ---: | ---: |
| Nama berkas | `VAL 2023.XLSX` | `VAL 2024.XLSX` | `VAL 2025.XLSX` |
| Transaksi menurut sempro | 22.459 | 60.052 | 35.655 |
| Jumlah kolom | 34 | 34 | 27 |
| Penanda waktu | `KP Date`, `CRD Date` | `KP Date`, `CRD Date` | `KP Date`, `CRD Date` |

Kolom inti lintas tahun mencakup identitas transaksi/item, `Country Key`, `KP Date`, `CRD Date`, `Order Quantity (Item)`, `Net Price`, `Net Value (Item)`, `Season`, `Document Currency`, `Material`, dan beberapa atribut organisasi/produk. Kolom yang hilang pada 2025 harus tetap tercatat sebagai `null` dalam skema kanonis, bukan diisi dengan nilai rekaan.

Setiap transaksi kanonis minimal membawa:

```text
source_file, source_year, transaction_key, item_key,
kp_date, crd_date, country_key, material, category_or_brand_key,
order_quantity, net_price, net_value, document_currency,
season, sales_office, sales_group, delivery_status, ...
```

`category_or_brand_key` tidak boleh diisi sampai rule mapping brand disetujui. Sempro menyebut brand, tetapi header sumber tidak memiliki kolom bernama `Brand`.

## Kontrak waktu dan target

### Granularity

Rancangan sempro memilih agregasi mingguan per kombinasi negara tujuan dan merek karena deret harian banyak yang sparse. Bentuk kanonisnya:

```text
series_id = country_key + validated_brand_or_category_key
time_idx  = week_start_date
row       = satu series_id pada satu minggu
```

Pipeline harus menghasilkan kalender mingguan lengkap untuk setiap seri aktif dan menyimpan kebijakan apakah minggu tanpa transaksi berarti nol atau missing. Kebijakan ini perlu berbasis definisi bisnis, bukan sekadar kebutuhan tensor.

### Target

Target bersifat konfigurabel sampai EDA membekukan keputusan:

```text
target_name: order_quantity_usd_independent | net_value_usd | approved_other
target_unit: item | USD
aggregation: sum
```

Jika target atau fitur menggunakan `Net Value (Item)`, konversi setiap transaksi IDR ke USD harus terjadi sebelum agregasi. Tabel kurs harus memiliki tanggal, pasangan mata uang, sumber, metode pengisian hari non-trading, versi, dan checksum.

### Ketersediaan fitur pada origin prediksi

| Kelas fitur | Contoh | Aturan |
| --- | --- | --- |
| Static categorical | negara, brand/kategori tervalidasi | Boleh jika stabil untuk seri |
| Known future | kalender minggu, musim yang direncanakan | Boleh hanya bila benar-benar diketahui sebelum horizon |
| Observed past | lag target, lag harga, lag kuantitas | Hanya nilai sampai origin |
| Unknown future | realisasi harga, order, nilai penjualan | Tidak boleh dipakai untuk target horizon |
| Suspect leakage | delivery status, rejection reason, status pemenuhan | Dilarang sampai dibuktikan tersedia saat prediksi |

`CRD Date` adalah kandidat fitur yang harus diklasifikasikan setelah arti bisnis dan waktu ketersediaannya tervalidasi.

## Pembentukan data model

1. Ingest membuat transaksi kanonis dan menandai baris invalid.
2. Validasi currency dan konversi IDR ke USD menghasilkan nilai ekonomi satuan.
3. Transformasi kategori memakai vocabulary yang fit pada train saja; kategori baru memakai token `unknown`.
4. Agregasi membuat panel mingguan per `series_id`.
5. Feature builder menambahkan kalender dan lag yang hanya memanfaatkan masa lalu.
6. Split builder membatasi waktu secara kronologis.
7. Window builder membentuk pasangan `encoder_window -> forecast_horizon` dan menyimpan origin timestamp.

Usulan split dari sempro adalah train Desember 2023-Juni 2025, validation Juli-Agustus 2025, dan test September-November 2025. Desember 2025 hanya boleh masuk setelah audit memastikan bulan lengkap. Juli 2023 yang tidak representatif dapat dikeluarkan sesuai justifikasi sempro. Batas ini disimpan sebagai konfigurasi dan `split_manifest`, bukan hard-code.

## Interface model umum

Semua adapter model harus mengimplementasikan kontrak logis berikut:

```text
fit(train_data, validation_data, run_config) -> trained_model, run_manifest
predict(windowed_data) -> predictions
save(path) / load(path)
metadata() -> architecture, parameter_count, effective_config
```

Input umum memuat tensor encoder target/observed covariates, known covariates, static covariates, `series_id`, mask, dan target horizon. Adapter bertanggung jawab mengubah bentuk tensor umum ke kebutuhan internal tanpa mengubah sample, split, atau horizon.

### MoEBS-Transformer

Komponen: Transformer forecasting core dan optimasi Multiple Objectives Escaping Bird Search. Konfigurasi wajib menyimpan objective, ruang hyperparameter, populasi, jumlah iterasi/evaluasi, seed, batas waktu, kandidat terpilih, dan nilai validation untuk seluruh kandidat. Objective tidak boleh melihat test set.

### N-Space Transformer

Komponen: contextual module, N-space transformation, dan encoder-decoder Transformer. Mapping embedding/kategori serta dimensi N-space harus berasal dari train dan dicatat sebagai bagian konfigurasi efektif.

### Temporal Fusion Transformer

Komponen: static covariate encoder, variable selection network, gated residual components, recurrent encoder/decoder, dan temporal attention. Bentuk pembagian static, known, dan observed covariates harus mengacu langsung pada feature contract. Feature importance hanya ditafsirkan dari run holdout yang sah dan stabil antar seed.

## Training dan eksperimen

```text
experiment config
  -> resolve data_manifest + split_manifest
  -> build identical windows
  -> train one model for each seed
  -> select checkpoint from validation
  -> infer once on sealed test set
  -> write predictions and metrics
```

`experiment config` minimal memuat model, target, series definition, date split, input length, horizon, feature version, seed, batch size, optimizer, learning rate, epoch limit, early-stopping policy, precision, hardware metadata, dan tuning budget.

Fairness berarti dataset/split/evaluator sama, bukan seluruh arsitektur harus memiliki jumlah parameter atau preprocessing internal identik. Jika MoEBS memakai budget optimasi, batas pencarian model lain dan total compute harus dicatat agar pembaca dapat menilai trade-off secara jujur.

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
  data/<manifest_id>/
    quality_report.json
    schema_report.json
    feature_contract.json
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
