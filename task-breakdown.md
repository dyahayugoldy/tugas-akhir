# Task Breakdown

Dokumen ini memecah penelitian menjadi pekerjaan yang dapat diverifikasi. Urutan penting karena model tidak boleh dilatih sebelum kontrak data, target, dan split dibekukan.

## Indikator status

Status di bawah diaudit pada 31 Oktober 2026 berdasarkan artefak yang ada di repositori. Status `✅ Selesai` hanya dipakai setelah seluruh kriteria pada bagian **Selesai bila** dapat dibuktikan.

| Indikator | Arti |
| --- | --- |
| ⬜ Belum dimulai | Belum ada implementasi atau artefak pembuktian untuk paket kerja. |
| 🔄 Sebagian selesai | Ada pekerjaan atau artefak awal, tetapi belum memenuhi seluruh kriteria selesai. |
| ✅ Selesai | Seluruh kriteria selesai telah dipenuhi dan bukti dapat diperiksa. |

## Keterlacakan tujuan

| Tujuan penelitian | Paket kerja pembuktian |
| --- | --- |
| Mengimplementasikan MoEBS-Transformer, N-Space Transformer, dan TFT | WP5 dan WP6 |
| Membandingkan akurasi dengan MAE, RMSE, MAPE, SMAPE, dan R^2 | WP7 dan WP8 |
| Menentukan model paling sesuai untuk data SAP | WP8 dan WP9 |
| Menilai efisiensi komputasi | WP6, WP7, dan WP8 |

## WP0 - Reproduksibilitas dan tata kelola

**Status: ✅ Selesai (diverifikasi 31 Oktober 2026).** Dummy run sintetis berhasil membuat manifest dan lolos validasi skema serta keberadaan artefak. Seluruh 13 pengujian WP0 lulus, termasuk larangan akses dataset, seed yang dapat direproduksi, isolasi artefak, dan hook Git yang menolak forced-add data terproteksi. `dataset/` dan `artifacts/` diabaikan Git; tidak ada path terproteksi pada index. Bukti dan perintah reproduksi ada di [reports/wp0-verification.md](reports/wp0-verification.md).

**Tujuan:** menyiapkan fondasi agar data sensitif dan eksperimen terkelola dengan baik.

- [x] Buat struktur `configs/`, `src/`, `tests/`, `artifacts/`, dan `reports/` saat implementasi dimulai. Modul utilitas, test, dan laporan WP0 mengisi struktur sesuai kebutuhan.
- [x] Tambahkan aturan ignore untuk data mentah, data turunan, checkpoint, log rinci, dan kredensial. Pemeriksaan lintas platform dan hook tersedia melalui `python -m src.utils.git_protection`.
- [x] Tetapkan format run manifest: commit/version source, data manifest id, konfigurasi, seed, paket, GPU/CPU, dan timestamp. Skema v1, generator, serta validator tersedia di `configs/` dan `src/utils/manifest.py`.
- [x] Tetapkan kebijakan penamaan artefak dan mekanisme penyimpanan hasil agregat yang aman dibagikan. Lihat `docs/reproducibility.md` dan `reports/README.md`.

**Selesai bila:** satu run dummy dapat membuat manifest tanpa data SAP bocor ke Git.

## WP1 - Audit dan harmonisasi data SAP

**Status: ⬜ Belum dimulai.** Belum ada modul ingest/audit, laporan kualitas data, atau data manifest yang dapat menjadi bukti penyelesaian.

**Tujuan:** membangun dataset transaksi kanonis yang benar sebelum forecasting.

- Baca tiga XLSX tanpa mengubah berkas sumber.
- Bandingkan nama, tipe, dan kelengkapan kolom setiap tahun.
- Normalisasi nama kolom dan tipe data; tambahkan `source_file` dan `source_year` untuk audit.
- Validasi jumlah baris, tanggal minimum/maksimum, nilai tanggal kosong/tidak valid, duplikasi, nilai numerik tidak wajar, dan distribusi currency.
- Dokumentasikan perbedaan 34 kolom pada 2023-2024 versus 27 kolom pada 2025, termasuk aturan pengisian `null` yang sah.
- Buat `data_quality_report` dan `data_manifest` berisi checksum sumber, statistik audit, skema versi, serta keputusan pembersihan.

**Selesai bila:** seluruh transaksi memiliki status valid/ditolak yang dapat dijelaskan, dan tidak ada pembersihan yang hanya terjadi diam-diam di notebook.

## WP2 - Keputusan bisnis dan feature contract

**Status: ⬜ Belum dimulai.** Belum ada hasil EDA terdokumentasi, keputusan target, validasi brand, atau feature contract.

**Tujuan:** memastikan definisi peramalan dapat dijawab oleh data yang tersedia pada origin prediksi.

- Konfirmasikan apakah `KP Date` adalah timestamp operasional yang dipakai untuk forecasting dan pahami arti `CRD Date`.
- Validasi field atau aturan derivasi brand. Jangan memakai proksi brand sebelum pemilik data menyetujuinya.
- Bandingkan kandidat target `Order Quantity (Item)` dan `Net Value (Item)` melalui EDA: missingness, nilai nol/negatif, volatilitas, sparsity, dan relevansi bisnis.
- Bekukan target utama, satuan, dan formula agregasinya. Bila nilai penjualan dipilih, konversi IDR ke USD dengan tabel kurs historis yang terversi dan dapat diaudit.
- Kelompokkan fitur menjadi static categorical, known future, observed past, dan forbidden/leakage.
- Tentukan fallback bila brand tidak bisa dipetakan: misalnya seri per negara x kategori produk tervalidasi, atau pengubahan rancangan dengan persetujuan pembimbing.

**Selesai bila:** `feature_contract` menjelaskan setiap kolom, transformasi, ketersediaan waktunya, serta alasan masuk/keluar dari model.

## WP3 - EDA, agregasi, dan dataset time series

**Status: ⬜ Belum dimulai.** Belum ada artefak EDA agregat, panel mingguan, maupun aturan regularisasi kalender yang dapat diaudit.

**Tujuan:** mengubah item transaksi menjadi panel time series yang siap dilatih.

- Buat EDA agregat yang aman: transaksi per bulan/minggu, target, mata uang, negara, kategori tervalidasi, dan missingness.
- Agregasikan transaksi ke minggu yang konsisten per `series_id`, dengan `series_id` dibentuk dari negara tujuan dan brand/kategori yang tervalidasi.
- Buat kalender mingguan lengkap. Masukkan nol hanya bila definisi bisnis menyatakan tidak ada transaksi berarti permintaan nol; dokumentasikan kebijakan seri sebelum/selesai aktif.
- Buat fitur kalender dan fitur lag dari data yang tersedia sampai origin prediksi.
- Tetapkan panjang encoder/input dan horizon forecast melalui EDA serta konfigurasi, bukan konstanta source.
- Tangani seri terlalu pendek dan kategori baru dengan aturan eksplisit.

**Selesai bila:** panel memiliki satu baris per `series_id`-minggu, tidak ada minggu ganda, dan statistik sparsity tersedia sebelum dan sesudah regularisasi kalender.

## WP4 - Split dan pembentukan window bebas leakage

**Status: ⬜ Belum dimulai.** Batas split masih berupa usulan sempro; belum ada konfigurasi final, split manifest, atau test leakage.

**Tujuan:** menghasilkan contoh train, validation, dan test yang sepenuhnya kronologis.

- Audit kelengkapan periode dan finalkan batas kalender. Mulai dari usulan sempro: train Des 2023-Jun 2025, validation Jul-Agu 2025, test Sep-Nov 2025.
- Putuskan dengan bukti apakah Desember 2025 dapat digunakan atau dikeluarkan.
- Fit scaler, vocabulary/encoder, imputasi statistik, dan pemilihan fitur menggunakan train saja.
- Bentuk sliding window dengan penanda origin, encoder range, dan target range.
- Pastikan target window validation/test tidak muncul pada fitur window mana pun.
- Simpan daftar index/panel split sebagai artefak immutable yang dipakai ketiga model.

**Selesai bila:** test contract membuktikan batas waktu, scaler train-only, dan kesetaraan sample count pada tiga dataloader.

## WP5 - Implementasi model dengan interface seragam

**Status: ⬜ Belum dimulai.** Belum ada implementasi model, interface bersama, maupun unit test model di repositori.

**Tujuan:** membuat tiga implementasi dapat dilatih dan dievaluasi dengan kontrak yang sama.

- Definisikan interface bersama: `fit`, `predict`, `save`, `load`, dan metadata arsitektur.
- Implementasikan MoEBS-Transformer dengan objective, ruang pencarian, batas jumlah evaluasi, seed, dan kriteria berhenti yang terdokumentasi.
- Implementasikan N-Space Transformer dengan contextual module, N-space transformation, serta encoder-decoder sesuai acuan yang dipilih.
- Implementasikan TFT dengan static/known/observed covariate contract, variable selection network, recurrent encoder/decoder, gating, dan attention.
- Tambahkan unit test untuk bentuk tensor, masking, kategori unknown, horizon lebih dari satu, dan checkpoint reload.
- Catat setiap deviasi dari paper acuan beserta alasan adaptasi ke data SAP.

**Selesai bila:** ketiga model dapat melakukan forward pass, satu epoch, checkpoint reload, dan prediksi pada batch yang sama tanpa kebocoran data.

## WP6 - Protokol training yang adil

**Status: ⬜ Belum dimulai.** Utilitas manifest dan dummy run tersedia dari WP0; belum ada runner training, konfigurasi resource/seed eksperimen, manifest hasil training, atau checkpoint.

**Tujuan:** mencegah perbedaan hasil akibat perlakuan eksperimen yang tidak setara.

- Tetapkan seed list, hardware, precision, batch policy, maximum epoch, early stopping, dan budget penalaan.
- Jalankan minimal beberapa seed yang sama untuk setiap model; gunakan seed tunggal hanya bila keterbatasan komputasi dicatat.
- Gunakan validation saja untuk pemilihan checkpoint dan hyperparameter, bukan test.
- Catat wall-clock training, GPU/CPU, memori puncak jika tersedia, jumlah parameter, dan waktu inferensi pada jumlah sample tetap.
- Simpan konfigurasi efektif, log, checkpoint terbaik, dan `run_manifest` per run.

**Selesai bila:** setiap model memiliki jumlah run dan definisi resource yang sebanding atau deviasinya dijustifikasi dalam laporan.

## WP7 - Evaluasi dan analisis error

**Status: ⬜ Belum dimulai.** Belum ada prediction artifact, implementasi metrik, atau analisis error test.

**Tujuan:** menghasilkan hasil kuantitatif yang dapat dibandingkan langsung.

- Hasilkan prediksi test untuk horizon yang sama dan satuan yang sama dengan aktual.
- Hitung MAE, RMSE, MAPE, SMAPE, dan R^2 dengan formula serta perlakuan nilai nol yang dibekukan.
- Laporkan nilai per seed dan ringkasan mean, standar deviasi, atau interval yang sesuai.
- Uraikan error berdasarkan horizon, negara tujuan, brand/kategori, dan periode bila ukuran sampel memadai dan aman dibagikan.
- Lakukan pemeriksaan residual, prediksi negatif bila target non-negatif, outlier, dan kegagalan pada seri sparse.
- Ukur waktu inferensi terpisah dari preprocessing dan training.

**Selesai bila:** semua angka pada tabel perbandingan dapat ditelusuri ke prediction artifact dan run manifest.

## WP8 - Keputusan komparatif

**Status: ⬜ Belum dimulai.** Belum ada hasil eksperimen tiga model yang dapat dibandingkan atau tabel keputusan final.

**Tujuan:** membuat rekomendasi yang menjawab rumusan masalah, bukan sekadar memilih nilai metrik terkecil.

- Bandingkan akurasi, variasi antar seed, waktu komputasi, jumlah parameter, dan interpretabilitas.
- Jangan menyatakan pemenang tunggal jika perbedaan metrik berada dalam variasi eksperimen atau trade-off terlalu besar.
- Untuk TFT, ekspor ringkasan feature importance hanya bila hasil stabil dan tidak mengungkap data bisnis sensitif.
- Bedakan klaim yang didukung test holdout dari hipotesis atau hasil validation.
- Catat ancaman validitas: 25 bulan efektif, sparsity, field brand, kurs, kategori baru, dan perbedaan domain dengan paper acuan.

**Selesai bila:** tabel final dan narasi menjawab tiga rumusan masalah dengan bukti yang dapat direproduksi.

## WP9 - Pelaporan tugas akhir

**Status: ⬜ Belum dimulai.** Belum ada Bab IV/V atau laporan hasil yang dapat ditautkan ke artefak eksperimen.

**Tujuan:** menghubungkan hasil teknis ke struktur sempro dan rekomendasi praktis.

- Perbarui Bab III hanya bila keputusan akhir berbeda dari rancangan sempro, dengan alasan dan bukti audit.
- Susun Bab IV: profil data yang dianonimkan, konfigurasi eksperimen, tabel metrik, efisiensi, analisis error, dan interpretasi.
- Susun Bab V: jawaban tujuan, rekomendasi model, keterbatasan, dan saran studi lanjut.
- Pastikan judul, istilah model, metrik, dan rentang data konsisten pada abstrak, tabel, gambar, kode, dan lampiran.

**Selesai bila:** setiap klaim hasil mempunyai artefak eksperimen yang dapat dirujuk dan tidak ada data sensitif yang terungkap.

## Dependensi utama

```text
WP0 -> WP1 -> WP2 -> WP3 -> WP4 -> WP5 -> WP6 -> WP7 -> WP8 -> WP9
                         ^
                         | keputusan target, brand, kurs, horizon, dan batas split
```

WP5 dapat mulai mempelajari paper acuan setelah WP0, tetapi training resmi tidak boleh dimulai sebelum WP4 selesai.
