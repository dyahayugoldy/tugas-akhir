# Task Breakdown

Dokumen ini memecah penelitian menjadi pekerjaan yang dapat diverifikasi. Urutan penting karena model tidak boleh dilatih sebelum kontrak data, target, dan split dibekukan.

## Indikator status

Status di bawah diaudit hingga 7 November 2026 berdasarkan artefak yang ada di repositori. Status `✅ Selesai` hanya dipakai setelah seluruh kriteria pada bagian **Selesai bila** dapat dibuktikan.

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

**Status: ✅ Selesai (diverifikasi 5 November 2026).** Tiga workbook SAP historis tahun 2023, 2024, dan 2025 telah diverifikasi, diharmonisasi menjadi transaksi kanonis privat, dan dibekukan melalui manifest data. Seluruh baris dipertahankan dengan status kualitas `valid`, `flagged`, atau `rejected`; tidak ada imputasi maupun penghapusan transaksi saat WP1.

**Cakupan sumber aktif:**

| Tahun sumber | Berkas lokal aktif | Kolom sumber | Peran dalam WP1 |
| --- | --- | ---: | --- |
| 2023 | `dataset/2023.XLSX` | 34 | Audit, harmonisasi, dan provenance `source_year=2023` |
| 2024 | `dataset/2024.XLSX` | 34 | Audit, harmonisasi, dan provenance `source_year=2024` |
| 2025 | `dataset/2025.XLSX` | 34 | Audit, harmonisasi, dan provenance `source_year=2025` |

Nama berkas sumber, checksum, dan manifest aktif harus selalu diambil dari konfigurasi versi (`configs/current-data.json` dan konfigurasi pembersihan aktif), bukan di-hard-code pada modul. Jika nama sumber resmi berubah, konfigurasi, dokumentasi, dan manifest harus diperbarui bersama sebelum pipeline dijalankan ulang.

**Bukti audit awal yang tersedia:**

- Profil numerik, kategorikal, temporal, nilai kategorikal, serta kandidat outlier IQR telah dibuat sebagai artefak privat.
- Tidak ditemukan baris duplikat identik pada ketiga dataset ketika seluruh kolom sumber dibandingkan.
- Kolom numerik `Order Quantity (Item)`, `Net Price`, dan `Net Value (Item)` tidak memiliki sel kosong pada 2023, 2024, maupun 2025.
- Terdapat lima kolom yang seluruh nilainya kosong pada ketiga dataset: `Reason for Rejection`, `Delivery Status Item Description`, `SKUCODE`, `Setcode`, dan `WH Code`. Kebijakan pembersihan aktif mempertahankan semua kolom pada artefak turunan awal; kolom tersebut ditandai sebagai kandidat dikeluarkan dari proyeksi kanonis/feature contract setelah keputusan yang terdokumentasi. Berkas mentah tetap read-only.
- Audit tanggal menemukan 11 baris pada dataset 2023 dengan `CRD Date` tahun 2026–2030; semuanya memiliki `CRD Date` lebih dari dua tahun setelah `KP Date`. Temuan ini adalah kandidat anomali, bukan alasan otomatis untuk menghapus baris. Arti bisnis dan waktu ketersediaan `CRD Date` harus dikonfirmasi sebelum aturan directional maupun absolute-gap dibekukan atau kolom dipakai sebagai fitur model.
- Run `data-harmonization-v1_20261105T122845Z_8adc4a76` menghasilkan `schema_report`, `data_quality_report`, transaksi kanonis berprovenance, dan `data_manifest` di `artifacts/data/canonical/`. Manifest aktif dicatat dalam `configs/current-data.json`; artefak rinci tetap privat dan tidak dilacak Git.

**Tujuan:** membangun dataset transaksi kanonis yang benar sebelum forecasting.

- [x] Daftarkan tiga sumber 2023–2025 pada konfigurasi aktif dan baca XLSX secara read-only; setiap run wajib memverifikasi checksum sumber sebelum dan sesudah pemrosesan.
- [x] Buat profil awal per workbook/sheet untuk duplikasi identik, missingness, tipe awal, nilai kategorikal, nilai numerik, tanggal, dan kandidat outlier. Artefak detail tetap privat di `artifacts/`.
- [x] Buat matriks skema lintas tahun: nama kolom asli, nama normalisasi, tipe sumber, tipe kanonis, keberadaan per tahun, serta alasan setiap perbedaan skema meskipun ketiga workbook masing-masing memiliki 34 kolom.
- [x] Tetapkan aturan harmonisasi yang terversi untuk setiap kolom: nama baku, parser/koersi tipe, nilai `null` yang sah, dan status `required`, `optional`, atau `excluded_pending_review`. Jika suatu kolom tidak tersedia pada salah satu tahun, nilainya harus menjadi `null` eksplisit dalam skema kanonis, tidak diisi nilai rekaan.
- [x] Normalisasi kolom dan tipe data ke transaksi kanonis; tambahkan `source_file`, `source_year`, `source_sheet`, dan penanda/nomor baris sumber yang hanya disimpan pada artefak privat untuk audit lineage.
- [x] Validasi per sumber dan gabungan: jumlah baris sebelum/sesudah, tanggal minimum/maksimum, tanggal kosong/tidak valid, duplikasi intra- dan lintas-tahun, nilai negatif/nol/tidak wajar, serta distribusi `Document Currency`.
- [x] Bekukan kebijakan tanggal `KP Date`/`CRD Date`: dokumentasikan arti bisnis, parser, nilai invalid, dan apakah kandidat selisih tanggal dipertahankan, ditandai, atau ditolak. Audit exploratory tidak boleh menghapus transaksi tanpa aturan yang telah disetujui.
- [x] Pertahankan seluruh kolom pada artefak pembersihan awal sesuai kebijakan aktif. Untuk lima kolom yang 100% kosong, catat bukti dan keputusan terpisah apakah dikecualikan dari proyeksi kanonis dan feature contract; jangan mengubah berkas mentah atau menghapus diam-diam.
- [x] Buat `data_quality_report`, `schema_report`, dan `data_manifest` berisi checksum sumber, statistik audit per tahun, pemetaan skema, keputusan pembersihan, versi skema, dan lokasi artefak turunan.

**Selesai bila:** ketiga sumber 2023–2025 tercakup dalam satu manifest yang dapat direproduksi; setiap transaksi memiliki status valid/ditandai/ditolak serta alasan yang dapat diaudit; matriks skema tiga workbook berisi 34 kolom dan aturan `null` telah diuji; transaksi kanonis membawa provenance sumber; dan tidak ada pembersihan yang hanya terjadi diam-diam di notebook.

## WP2 - Keputusan bisnis dan feature contract

**Status: ✅ Selesai (diverifikasi 6 November 2026).** Pipeline filter, pemetaan buyer dan brand, konfigurasi versi, `feature_contract` untuk 34 kolom, EDA target privat, dan manifest bersama sudah tersedia. Pengguna mengonfirmasi `Sales group` sebagai kolom brand; seri ditetapkan sebagai kombinasi `country_key` dan `sales_group`. Run aktif `business-selection-v3_20261106T075504Z_052ff4d7` mempertahankan 163.016 dari 180.246 baris; 17.230 baris unik dikeluarkan. Transaksi dan target identik dengan v2, termasuk kuantitas pecahan dan 4.928 baris negara unknown. Tidak ada brand kosong atau quality hold setelah filter; manifest menetapkan `ready_for_panel=true` tanpa blocker. Seluruh 35 pengujian unittest lulus. Bukti dan perintah reproduksi tersedia di [reports/wp2-verification.md](reports/wp2-verification.md); implementasi panel tetap menjadi pekerjaan WP3.

**Tujuan:** memastikan definisi peramalan dapat dijawab oleh data yang tersedia pada origin prediksi.

**Keputusan bisnis yang menjadi acuan WP2:**

| Aspek | Keputusan |
| --- | --- |
| `KP Date` | Tanggal dibuatnya Kartu Produksi (KP). Tetap menjadi kolom waktu utama sesuai kontrak data awal. |
| `CRD Date` | Tanggal keberangkatan ekspor. Arti bisnis sudah ditetapkan; ketersediaannya pada origin prediksi tetap harus divalidasi sebelum dipakai sebagai fitur. |
| Mata uang | Keluarkan seluruh baris dengan `Document Currency = IDR` dari dataset analisis/model. |
| Buyer | Gunakan kolom sumber `Sales group` (`sales_group` pada skema kanonis) sebagai kode buyer sesuai konfirmasi bisnis. Keluarkan seluruh baris dengan kode buyer `PEB`, `KWK`, `LNS`, dan `CPC` pada kolom tersebut. |
| Brand | Konfirmasi pengguna 6 November 2026 menetapkan `Sales group` sebagai kolom brand. Gunakan `sales_group` yang di-trim dan dikapitalisasi sebagai brand key langsung; identitas seri adalah kombinasi negara dan brand. Peran kolom untuk filter buyer tetap berlaku. |
| Negara tujuan | Keluarkan seluruh baris dengan `Country Key = ID` dari seluruh sumber tahun 2023–2025. Negara kosong dipertahankan sebagai kategori `__UNKNOWN_COUNTRY__` dengan indikator audit asal kosong; negara lainnya tetap dipertahankan. |
| Target utama | Gunakan `Order Quantity (Item)` (`order_quantity_item` pada skema kanonis) dalam pcs, dengan agregasi penjumlahan per seri-minggu setelah seri tervalidasi. Kuantitas pecahan tetap dipertahankan tanpa pembulatan. `Net Value (Item)` tidak menjadi target utama. |
| Harga nol | Keluarkan seluruh baris dengan `Net Price` bernilai numerik nol, termasuk representasi `0` dan `0.00`. |

**Tugas dan verifikasi:**

- [x] Catat definisi bisnis `KP Date` dan `CRD Date`, pilihan target utama, serta aturan eksklusi di atas.
- [x] Tetapkan pemetaan buyer: kolom sumber `Sales group` → kolom kanonis `sales_group` → peran bisnis kode buyer. Konfirmasi tambahan pengguna 6 November 2026 juga menetapkan kolom yang sama sebagai brand key.
- [x] Terapkan dan uji pemetaan buyer dari `sales_group` pada seluruh sumber 2023–2025, termasuk normalisasi kode, kebijakan nilai kosong, serta eksklusi `PEB`, `KWK`, `LNS`, dan `CPC`; simpan pemetaan dalam konfigurasi versi dan `feature_contract`.
- [x] Simpan keputusan tanggal, target pcs, brand, dan seluruh aturan filter dalam `configs/business-selection.v3.json` serta `configs/feature-contract.v3.json`. Kode kategorikal di-trim dan dikapitalisasi; harga diparse sebagai desimal finite. Nilai kosong/tidak valid dipertahankan dengan quality hold, kecuali negara kosong yang menjadi kategori unknown berdasarkan keputusan pengguna; tidak ada nilai transaksi hilang yang dianggap nol secara otomatis. Versi v1/v2 tetap disimpan untuk audit.
- [x] Terapkan satu aturan eksklusi yang sama pada transaksi kanonis dari ketiga sumber sebelum EDA WP3, agregasi, dan split. Baris dikeluarkan bila memenuhi setidaknya satu aturan mata uang, buyer, negara, atau harga nol. Hasil disimpan sebagai artefak turunan baru yang dipakai bersama oleh ketiga model; berkas mentah dan artefak audit WP1 tetap dipertahankan.
- [x] Buat laporan filter per tahun dan gabungan: jumlah baris sebelum/sesudah, jumlah yang cocok dengan setiap aturan, overlap antaraturan, serta total baris unik yang dikeluarkan agar tidak terhitung ganda. Alasan eksklusi dan lineage tersimpan pada artefak privat; laporan yang dilacak Git hanya memuat ringkasan agregat yang aman.
- [x] Verifikasi hasil filter tidak memuat mata uang `IDR`, buyer yang dikecualikan, negara `ID`, maupun `Net Price = 0`; pengujian mencakup representasi harga `0`/`0.00`, normalisasi kode, nilai kosong/invalid, dan overlap. Checksum sumber, manifest input/output, serta pembacaan ulang hasil membuktikan integritas artefak.
- [x] Lakukan EDA deskriptif `Order Quantity (Item)` sebelum/sesudah filter: missingness, nol/negatif, kuantitas pecahan, volatilitas mingguan, dan sparsity diagnostik per negara. Statistik rinci tetap privat dan tidak dipakai untuk fitting atau penalaan; diagnostik ini belum merupakan panel seri final.
- [x] Konfirmasikan satuan pcs dan bekukan formula penjumlahan target per `series_id`-minggu setelah seri tervalidasi. Hasil filter memiliki 450 kuantitas pecahan yang tetap dipertahankan tanpa pembulatan. Konversi IDR ke USD tidak diperlukan untuk target kuantitas; fitur moneter dikecualikan sambil menunggu kontrak mata uang dan ketersediaannya.
- [x] Terapkan keputusan pengguna untuk mempertahankan 4.928 baris tanpa `Country Key` sebagai `__UNKNOWN_COUNTRY__`, dengan indikator audit `wp2_country_was_missing`. Token ini berbeda dari token encoder `__UNKNOWN__` untuk kategori baru. Tidak ada quality hold negara kosong atau penghapusan tambahan; data mentah/kanonis tetap utuh.
- [x] Verifikasi `Sales group` kosong: 0 baris pada masing-masing tahun 2023, 2024, dan 2025, baik sebelum maupun sesudah filter; statistik tersimpan pada `selection_report.json`.
- [x] Dokumentasikan waktu ketersediaan setiap fitur dan kelompokkan menjadi static categorical, known future, observed past, dan forbidden/leakage. `CRD Date` dilarang sebagai fitur sampai tersedia bukti pada origin; definisi tanggal tidak menetapkan ambang penghapusan selisih tanggal. Tidak ada fit scaler/encoder atau imputasi statistik pada WP2.
- [x] Validasi field atau aturan derivasi brand. Pengguna mengonfirmasi `Sales group` pada 6 November 2026; kontrak v3 menetapkan mapping langsung ke `sales_group`, normalisasi, dan peran static categorical untuk identitas seri. Nilai kosong tetap quality hold.
- [x] Tutup keputusan fallback. `country_only` tidak dipilih karena mapping brand telah dikonfirmasi. Fallback ini berarti menjumlahkan seluruh brand per negara-minggu; detail per brand hilang dan unit analisis berbeda dari seri negara × brand.

**Selesai bila:** konfigurasi versi dan `feature_contract` menetapkan definisi tanggal, pemetaan `Sales group`/`sales_group` sebagai buyer, target kuantitas beserta satuan/agregasinya, seluruh aturan eksklusi, serta transformasi dan ketersediaan waktu setiap kolom. Artefak turunan dan laporan sebelum/sesudah membuktikan pemetaan buyer dan filter diterapkan pada seluruh sumber 2023–2025, pengujian eksklusi lulus, dan satu manifest hasil filter menjadi acuan ketiga model.

## WP3 - EDA, agregasi, dan dataset time series

**Status: ✅ Selesai (diverifikasi 6 November 2026).** Run aktif `panel-weekly-v1_20261106T091154Z_6030bb53` menghasilkan 23.812 baris panel untuk 229 seri negara × brand dalam 156 minggu lengkap, tanpa minggu ganda. Sebanyak 19.969 minggu-seri tanpa transaksi diisi nol (83,86% panel), sedangkan 283 transaksi pada minggu parsial akhir tahun tetap tersimpan di audit dan tidak masuk panel. Agregasi desimal mempertahankan target tanpa pembulatan. Kalender, lag 1/2/4/13 minggu, registry waktu diketahui, dan masking per origin tersedia. Desain input 12 minggu serta horizon 1 minggu dipilih/dibekukan tanpa holdout; dua run menghasilkan checksum identik pada delapan artefak deterministik utama. Seluruh 48 pengujian unittest lulus. Bukti, reproduksi, dan asumsi cakupan/capture tersedia di [reports/wp3-verification.md](reports/wp3-verification.md). Split final dan fitting preprocessing tetap menjadi pekerjaan WP4.

**Tujuan:** mengubah item transaksi menjadi panel time series yang siap dilatih.

- [x] Buat EDA agregat privat: transaksi/target per bulan dan minggu, mata uang, negara, brand tervalidasi, missingness, panjang seri, serta sparsity sebelum/sesudah. Ringkasan aman ada pada laporan WP3.
- [x] Agregasikan transaksi per `series_id`-minggu Senin-Minggu, dengan identitas tuple `country_key` dan `sales_group` sebagai brand. Hash tuple stabil tidak menggantikan vocabulary encoder train-only.
- [x] Tetapkan minggu tanpa transaksi setelah filter bisnis sebagai permintaan nol (0 pcs), sesuai keputusan pengguna 6 November 2026; kebijakan tersimpan pada `calendar_regularization` di kontrak v3.
- [x] Buat kalender mingguan lengkap di dalam cakupan tahunan yang dideklarasikan dan diaudit. Seri dimulai pada minggu transaksi pertama dan dilanjutkan sampai minggu lengkap terakhir; tidak ada pemotongan memakai transaksi terakhir. Simpan indikator audit `wp3_zero_filled`, statistik nol, dan agregat minggu parsial secara terpisah. Asumsi kelengkapan ekstraksi tercatat karena log ekstraksi tidak tersedia.
- [x] Buat fitur kalender dan lag dengan tanggal sumber serta waktu ketersediaan; accessor mem-mask lag terhadap origin asli dan menolak seri yang belum diketahui. Label target dan kolom operasional/moneter tidak masuk tabel fitur. Asumsi delay capture 0 hari tercatat, tanpa klaim verifikasi operasional.
- [x] Tetapkan panjang input 12 minggu dari kandidat 8/12/26/52 berdasarkan dukungan window periode pengembangan usulan train saja, dengan retensi minimal 80% terhadap kandidat terpendek. Horizon 1 minggu mengikuti target periode berikutnya; konfigurasi dan keputusan efektif disimpan.
- [x] Pertahankan seri pendek dan seri baru; syarat histori diperiksa per origin pada WP4, tanpa filter panjang yang melihat masa depan. Minimum histori encoder beserta lag adalah 25 minggu. Kategori baru nanti memakai encoder train-only dan token unknown; WP3 tidak melakukan fitting kategori atau scaler.

**Selesai bila:** panel memiliki satu baris per `series_id`-minggu, tidak ada minggu ganda, dan statistik sparsity tersedia sebelum dan sesudah regularisasi kalender.

## WP4 - Split dan pembentukan window bebas leakage

**Status: ✅ Selesai (diverifikasi 7 November 2026).** Konfigurasi `split-window.v1.json` membekukan seluruh minggu lengkap Januari 2023-Desember 2025 sesuai keputusan pengguna. Train: 2 Januari 2023-29 Juni 2025; validation: 30 Juni-31 Agustus 2025; test: 1 September-28 Desember 2025. Pipeline menghasilkan 13.663/1.587/3.187 window dengan encoder 12 minggu, horizon 1 minggu, preprocessing train-only, mask origin, dan indeks immutable bersama. Seluruh 60 unittest lulus, termasuk 12 pengujian WP4. Loader NumPy untuk ketiga konsumen menghasilkan urutan sampel, nilai tensor, dan jumlah sampel identik; adapter framework dan model tetap pekerjaan WP5. Bukti, reproduksi, deviasi sempro, dan keterbatasan tersedia di [reports/wp4-verification.md](reports/wp4-verification.md).

**Tujuan:** menghasilkan contoh train, validation, dan test yang sepenuhnya kronologis.

- [x] Audit cakupan WP3 dan finalkan batas Senin-Minggu dalam konfigurasi. Pengguna memilih cakupan sumber Januari-Desember pada ketiga tahun; perpanjangan awal train dan akhir test dicatat sebagai deviasi sempro. Jumlah minggu/sampel tiap tahun tidak disamakan secara paksa.
- [x] Sertakan empat minggu lengkap 1-28 Desember 2025. Minggu mulai 29 Desember tetap berada di audit parsial WP3. Pertahankan keterbatasan kelengkapan ekstraksi tahunan dan ketersediaan retrospektif tanpa timestamp capture/revisi.
- [x] Fit scaler target global, kalender per field, dan vocabulary hanya pada baris train unik yang tersedia pada cutoff. Lag memakai scaler target bersama; tidak ada imputasi statistik atau pemilihan fitur dari holdout. Kategori baru memakai `__UNKNOWN__=0`, berbeda dari kategori negara kosong.
- [x] Bentuk sliding window dengan sample ID stabil, origin, rentang encoder/target, dan waktu maksimum ketersediaan input. Seri pendek/baru diputuskan per origin; simpan statistik eksklusi tanpa membuang seri dari panel.
- [x] Buktikan label horizon suatu sampel tidak masuk input sampel tersebut. Evaluasi rolling-origin mengizinkan aktual validation/test yang sudah tersedia menjadi histori origin berikutnya dengan preprocessing tetap; tidak mengizinkan fit/training pada holdout. Rumusan sebelumnya yang melarang seluruh target holdout muncul pada fitur window mana pun diperjelas agar konsisten dengan protokol rolling-origin.
- [x] Simpan panel split, indeks window, tensor per split, preprocessing, kontrak fitur, source snapshot, checksum, dan manifest pada run privat unik. Tiga konsumen memakai file identik dan tidak membuang batch terakhir; persamaan sample ID, tensor, label, serta jumlah sampel diverifikasi.

**Selesai bila:** test contract membuktikan batas waktu, scaler train-only, dan kesetaraan sample count pada tiga dataloader.

## WP5 - Implementasi model dengan interface seragam

**Status: ✅ Selesai (diverifikasi 7 November 2026).** Ketiga arsitektur PyTorch memakai `ForecastAdapter` dan manifest WP4 yang sama. Acceptance menjalankan empat kandidat per model, masing-masing satu epoch penuh pada 13.663 sampel train; 1.587 prediksi validation per model identik setelah checkpoint reload. Seluruh 72 test lulus, termasuk 12 test WP5. Bukti dan batas klaim tersedia di [reports/wp5-verification.md](reports/wp5-verification.md); acuan/adaptasi tercatat pada [docs/wp5-models.md](docs/wp5-models.md). Acceptance seed tunggal bukan eksperimen komparatif resmi; test belum diprediksi.

**Tujuan:** membuat tiga implementasi dapat dilatih dan dievaluasi dengan kontrak yang sama.

- [x] Definisikan interface bersama: `fit`, `predict`, `save`, `load`, dan metadata arsitektur.
- [x] Implementasikan MoEBS-Transformer dengan objective, ruang pencarian, batas jumlah evaluasi, seed, dan kriteria berhenti yang terdokumentasi.
- [x] Implementasikan N-Space Transformer dengan contextual module, N-space transformation, serta encoder-decoder sesuai acuan yang dipilih.
- [x] Implementasikan TFT dengan static/known/observed covariate contract, variable selection network, recurrent encoder/decoder, gating, dan attention.
- [x] Tambahkan unit test untuk bentuk tensor, masking, kategori unknown, horizon lebih dari satu, dan checkpoint reload.
- [x] Catat setiap deviasi dari paper acuan beserta alasan adaptasi ke data SAP.

**Selesai bila:** ketiga model dapat melakukan forward pass, satu epoch, checkpoint reload, dan prediksi pada batch yang sama tanpa kebocoran data.

## WP6 - Protokol training yang adil

**Status: ✅ Selesai dengan keterbatasan resource yang dilaporkan (7 November 2026).** Cohort mengikat 3 hasil tuning dan 9 final run dari seed 42/123/2026. Seluruh checkpoint, manifest, data/split, pemilihan kandidat, early stopping, prediksi validation dan metrik telah diaudit. Provenance resume TFT dan seluruh fit retry yang tercatat direkonsiliasi tanpa mengubah manifest lama. Anggaran seleksi 4 kandidat/model sebanding; total compute fisik berbeda akibat retry dan diungkapkan sebagai keterbatasan. Seluruh 83 test lulus. Bukti, cohort resmi dan perintah verifikasi ada di [reports/wp6-verification.md](reports/wp6-verification.md). Test belum diprediksi oleh model; evaluasi tetap WP7.

**Tujuan:** mencegah perbedaan hasil akibat perlakuan eksperimen yang tidak setara.

- [x] Bekukan seed 42/123/2026, CPU float32, dua thread, batch 256, maksimum 100 epoch, patience 10, dan budget penalaan pada konfigurasi WP6.
- [x] Selesaikan sembilan final run dari inisialisasi baru: tiga model dengan tiga seed yang sama; seluruh checkpoint terverifikasi.
- [x] Gunakan validation saja untuk pemilihan checkpoint dan hyperparameter; evaluasi test tetap WP7.
- [x] Catat wall-clock training, hardware, jumlah parameter, dan waktu inferensi pada jumlah sampel validation yang sama. Peak memory CPU tidak tersedia; biaya retry dan keterbatasan pengukuran dilaporkan.
- [x] Simpan konfigurasi efektif, riwayat epoch, checkpoint terbaik, dan `run_manifest` per run; sahkan cohort beserta audit provenance dan resource.

**Selesai bila:** setiap model memiliki jumlah run dan definisi resource yang sebanding atau deviasinya dijustifikasi dalam laporan.

## WP7 - Evaluasi dan analisis error

**Status: ⬜ Belum dimulai untuk evaluasi test.** WP5 menyediakan metrik bersama MAE/RMSE/MAPE/SMAPE/`r2` beserta prediksi validation acceptance. Prediction artifact dan analisis error test belum dibuat; WP7 menggunakan model hasil protokol resmi WP6.

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
