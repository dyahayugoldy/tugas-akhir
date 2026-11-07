# Verifikasi WP3

Tanggal pemeriksaan: 6 November 2026. Status: selesai untuk EDA, panel mingguan, dan fitur kausal di bawah asumsi ketersediaan retrospektif yang dicatat. Split, vocabulary/scaler, window tensor, dan training belum dibuat.

## Sumber dan reproduksi

Input bersama: manifest WP2 `business-selection-v3_20261106T075504Z_052ff4d7`, ditunjuk oleh `configs/current-data.json`. Konfigurasi: `configs/panel-weekly.v1.json` dan kontrak induk `configs/feature-contract.v3.json`. Run panel aktif: `panel-weekly-v1_20261106T091154Z_6030bb53`; manifest dan seluruh detail berada di direktori privat `artifacts/data/weekly_panel/`.

```powershell
python -m src.data.weekly_panel --config configs/panel-weekly.v1.json
python -m unittest discover -s tests -v
python -m src.utils.git_protection
```

Setiap eksekusi membuat direktori run baru, tanpa menimpa artefak atau mengubah pointer aktif otomatis. Setelah verifikasi, pointer panel/fitur/registry/laporan/kontrak efektif ditetapkan pada `configs/current-data.json`; pointer WP1 dan WP2 tetap tersedia. Runner menyimpan konfigurasi, snapshot pointer dan empat berkas source dependency, checksum input/output, seed tidak berlaku untuk transformasi deterministik, versi Python/pandas/NumPy, CPU/arsitektur, commit/dirty flag, dan waktu proses.

## Panel dan audit kalender

Seri adalah tuple negara × brand: `country_key` dan `sales_group` yang dikonfirmasi pengguna. ID merupakan SHA-256 serialisasi tuple, sehingga delimiter dalam kode tidak mencampur seri dan penambahan kategori masa depan tidak mengubah ID lama. Registry berisi identitas serta tanggal pertama diketahui; registry tidak difit menjadi vocabulary.

| Pemeriksaan | Hasil |
| --- | ---: |
| Transaksi input WP2 | 163.016 |
| Seri negara × brand | 229 |
| Minggu lengkap global | 156 |
| Baris seri-minggu teramati sebelum regularisasi | 3.843 |
| Baris panel sesudah regularisasi | 23.812 |
| Baris hasil zero-fill | 19.969 |
| Proporsi nol sesudah regularisasi | 83,86% |
| Proporsi nol pada agregat teramati sebelum regularisasi | 0% |
| Minggu-seri hilang sesudah regularisasi | 0 |
| Kunci seri-minggu ganda | 0 |
| Transaksi tercakup panel | 162.733 |
| Transaksi minggu parsial yang disimpan terpisah | 283 |
| Seri pendek yang tetap dipertahankan | 30 |
| Seri pertama diketahui setelah periode pengembangan | 33 |

Panel mencakup minggu mulai 2 Januari 2023 sampai 22 Desember 2025 (akhir minggu 28 Desember). Minggu mulai 29 Desember 2025 melampaui batas sumber 31 Desember, sehingga 26 agregat seri-minggu yang memuat 283 transaksi disimpan pada `aggregated/observed_weekly.csv.gz` dengan penanda cakupan parsial, tanpa dipakai sebagai label minggu penuh. Panel dan audit parsial mempartisi seluruh transaksi input.

Seri dimulai pada minggu transaksi pertamanya. Setelah aktif, seri dipertahankan sampai akhir kalender lengkap; tidak ada nol sebelum aktivasi dan tidak ada penutupan otomatis berdasarkan transaksi terakhir. Ini mempertahankan nol di akhir seri tanpa memilih panjang seri memakai masa depan. Indikator `wp3_zero_filled` dan jumlah transaksi adalah metadata audit, bukan fitur model.

Kuantitas dijumlahkan menggunakan Decimal dengan pemeriksaan arithmetic rounding. Total agregat teramati sama persis dengan total input; total panel sama persis dengan transaksi pada minggu lengkap. Seluruh 450 kuantitas input pecahan tetap utuh; jumlah sel pecahan pada panel menjadi 56 karena penjumlahan dapat menghasilkan bilangan bulat. Tidak ada pembulatan, imputasi nilai transaksi invalid, kurs, deduplikasi tambahan, atau perubahan input WP2/mentah.

EDA detail menyimpan transaksi/target per bulan dan minggu, distribusi mata uang/negara/brand, missingness seluruh kolom, kuantil panjang seri, audit coverage, dan rekonsiliasi target. Identitas kategori dan total kuantitas rinci hanya berada pada artefak privat.

## Fitur dan panjang input

Kalender memuat tahun kalender, tahun/minggu ISO, bulan, kuartal, dan sin/cos minggu/bulan. Semua berasal dari minggu target. Lag target 1/2/4/13 minggu di-shift dalam seri yang kalendernya sudah regular; setiap lag membawa minggu sumber, waktu tersedia, dan mask. Lag awal yang belum ada tetap null, tanpa backward fill. Tabel fitur tidak memuat label target, harga, status operasional, maupun identifier transaksi.

`features_at_origin(features, origin, lags, registry)` menggunakan origin asli: lag yang baru tersedia setelah origin di-mask, dan seri yang belum diketahui saat origin dikeluarkan. Aturan ini juga wajib dipakai ketika WP4 membentuk horizon lebih dari satu. Static categories hanya berasal dari registry yang diketahui; vocabulary dan token unknown tetap menjadi pekerjaan train-only WP4. `__UNKNOWN_COUNTRY__` tetap kategori negara kosong yang disetujui, berbeda dari token encoder kategori baru.

Desain panjang encoder memakai periode pengembangan usulan train, 4 Desember 2023-29 Juni 2025. Periode ini untuk keputusan desain WP3 dan belum menjadi split final. Kandidat terbesar dipilih selama masih mempertahankan minimal 80% window feasible kandidat terpendek; seluruh lag encoder harus tersedia.

| Encoder (minggu) | Window pengembangan feasible | Retensi terhadap 8 minggu |
| --- | ---: | ---: |
| 8 | 9.628 | 100% |
| 12 | 8.927 | 92,72% |
| 26 | 6.556 | 68,09% |
| 52 | 2.457 | 25,52% |

Pilihan: encoder 12 minggu, lag maksimum 13 minggu, sehingga minimal histori 25 minggu dan minimal 26 minggu lengkap untuk satu contoh berlabel horizon 1. Horizon 1 minggu membekukan tugas periode berikutnya sesuai bagian 3.6.2 proposal; ilustrasi 12 minggu pada bagian teori tidak diperlakukan sebagai konstanta wajib. Kandidat, ambang retensi, periode pengembangan, dan horizon disimpan pada konfigurasi serta `window_design.json`. Tidak ada keputusan berdasarkan akurasi atau statistik target validation/test. Seri pendek tetap tersimpan; kelayakan diputuskan per origin dan identik bagi ketiga model pada WP4.

## Pengujian dan reproduksibilitas

Seluruh 48 unittest lulus, termasuk 13 test WP3 sintetis. Bukti mencakup agregasi pecahan yang tepat, nol di sela/akhir seri, larangan baris sebelum aktivasi, preservasi seri pendek/baru, minggu parsial, tahun ISO lintas tahun kalender, tabrakan delimiter identitas, penolakan target invalid/negatif, quality hold, checksum tampering, penolakan output publik, dan pesan kegagalan roundtrip yang tidak membocorkan nilai privat.

Test kausal memodifikasi kuantitas masa depan dan menambah kategori masa depan: panel/fitur masa lalu serta keputusan panjang input tetap identik. Test origin tetap menutup lag horizon masa depan, memeriksa ketersediaan terlambat, dan mengecualikan seri yang belum diketahui. Tidak ada fitting scaler/encoder/imputer statistik atau akses XLSX asli pada test.

Dua run final, `panel-weekly-v1_20261106T091118Z_63c4f7aa` dan `panel-weekly-v1_20261106T091154Z_6030bb53`, menghasilkan checksum identik pada delapan artefak utama: agregat teramati, panel, registry, fitur, EDA, laporan panel, desain window, dan kontrak fitur. Seluruh 14 checksum output dan empat snapshot source run aktif telah diverifikasi kembali; pembacaan ulang CSV persis sama dengan tabel yang disimpan. Checksum sumber mentah dan input dipantau sebelum/sesudah run.

## Asumsi dan kelanjutan WP4

Cakupan sumber dideklarasikan per tahun 2023-2025. Semua tanggal KP sesuai tahun sumber, setiap bulan memiliki transaksi, dan endpoint tahunan telah diaudit. Bukti ini tidak membuktikan kelengkapan setiap transaksi tanpa log ekstraksi; asumsi cakupan tahunan disimpan pada manifest. Panel mempertahankan seluruh data baru sejak Januari 2023, sedangkan usulan sempro memakai rentang yang berbeda; pemilihan periode eksperimen final tetap menjadi keputusan WP4.

Sumber hanya memiliki tanggal kejadian KP, tanpa timestamp capture atau riwayat revisi. Target mingguan diasumsikan tersedia pada Senin sesudah minggu berakhir dengan delay capture 0 hari. Ini proxy retrospektif yang terdokumentasi, bukan bukti bahwa transaksi backdated/revisi sudah diketahui saat origin historis. WP4 dan laporan eksperimen wajib membawa keterbatasan ini, serta menghormati metadata waktu ketersediaan. Kelengkapan bulan Desember 2025 untuk split final belum diputuskan hanya dari adanya transaksi pada 31 Desember.

Run ditandai `ready_for_split=true`, dengan satu manifest panel bersama bagi ketiga model. WP4 masih perlu membekukan batas minggu train/validation/test, memutuskan penggunaan Desember dan histori sebelum awal train, fit preprocessing pada train saja, dan membuat window index identik.

Review pengungkapan: Codex, 6 November 2026. Laporan ini memuat hitungan gabungan dan aturan metodologis, tanpa nama pelanggan/brand, PO, detail transaksi, atau nilai target bisnis. Detail tetap pada `artifacts/` yang diabaikan Git; ringkasan ini layak dilacak lokal tanpa mengotorisasi publikasi atau unggahan sumber.
