# Verifikasi WP4

Tanggal pemeriksaan: 7 November 2026. Status: selesai untuk split, preprocessing train-only, window tensor, dan kontrak loader bersama. Implementasi model/framework adapter serta training tetap pekerjaan WP5-WP6.

## Input, keputusan kalender, dan split final

Input bersama adalah manifest WP3 `panel-weekly-v1_20261106T091154Z_6030bb53`, dengan 23.812 baris panel dari 229 seri dan 156 minggu lengkap. Konfigurasi `configs/split-window.v1.json` membekukan keputusan pengguna 7 November 2026 untuk memakai cakupan Januari-Desember pada ketiga sumber tahunan. Data mentah tetap utuh dan hanya dibaca untuk checksum; transformasi memakai artefak WP3 privat.

| Split | Rentang kalender lengkap | Minggu kalender | Baris panel | Window eligible |
| --- | --- | ---: | ---: | ---: |
| Train | 2 Januari 2023-29 Juni 2025 | 130 | 18.238 | 13.663 |
| Validation | 30 Juni-31 Agustus 2025 | 9 | 1.843 | 1.587 |
| Test | 1 September-28 Desember 2025 | 17 | 3.731 | 3.187 |

Rentang memakai seluruh minggu Senin-Minggu tanpa overlap atau pemecahan minggu. Minggu 30 Juni-6 Juli masuk validation seluruhnya. Empat minggu lengkap 1-28 Desember 2025 masuk test; minggu 29 Desember 2025-4 Januari 2026 tetap berada pada audit parsial WP3 karena sumber berakhir 31 Desember. Minggu lintas tahun internal dapat digunakan karena sumber digabungkan sebelum agregasi. Tidak ada kesimpulan bahwa seluruh Desember tidak lengkap.

Awal train Januari 2023 menggantikan usulan Desember 2023, dan akhir test Desember 2025 menggantikan usulan November 2025. Ini deviasi dari sempro yang disimpan dalam konfigurasi dan perlu dibawa ke Bab III. Penggunaan cakupan bulan yang sama tidak menyamakan jumlah minggu, seri aktif, atau sample tiap tahun; split tetap kronologis, tanpa mengacak data atau membagi ulang tiga workbook menjadi tiga dataset model.

## Histori dan aturan origin

Encoder tetap 12 minggu, lag target 1/2/4/13 minggu, dan horizon 1 minggu, mengikuti keputusan WP3. Tidak ada pemilihan ulang panjang input berdasarkan holdout. Periode desain WP3 Desember 2023-Juni 2025 seluruhnya berada dalam train final.

Seri memerlukan 25 minggu histori agar 12 baris encoder beserta lag lengkap tersedia. Data sejak Januari 2023 menjadi konteks; label train pertama berada pada minggu 26 Juni-2 Juli 2023. Tidak ada konteks sebelum awal eksperimen. Syarat histori dan registry `known_at` dievaluasi setiap origin, tanpa memakai panjang akhir seri sebagai filter. Seri pendek/baru tetap ada di panel dan alasan ketidaklayakan window disimpan per split di artefak privat. Total 18.437 window eligible; baris panel lain tetap tercatat sebagai histori atau origin yang belum memenuhi syarat.

Setiap window menyimpan sample ID stabil, seri, origin Senin sebelum horizon, rentang encoder dan target hingga hari akhir minggu, waktu label tersedia, serta waktu ketersediaan maksimum input. Semua encoder target dan lag yang digunakan tersedia paling lambat pada origin asli. Label seluruh horizon harus berada pada split yang sama. Kalender masa depan berasal dari tanggal, bukan realisasi transaksi; indikator zero-fill, status operasional, harga, PO, dan metadata audit tidak masuk input model.

Evaluasi bersifat **rolling origin** dengan preprocessing tetap. Aktual validation/test yang telah tersedia boleh menjadi histori origin berikutnya. Aktual pada horizon suatu window tidak boleh masuk input window tersebut; data holdout juga tidak dipakai untuk fit ulang preprocessing, pelatihan, atau penalaan. Larangan pada rumusan WP4 lama yang menyebut target holdout tidak boleh muncul pada fitur window mana pun diperjelas menjadi larangan kebocoran horizon pada origin asli, agar konsisten dengan evaluasi bergulir.

## Preprocessing dan tensor bersama

Statistik preprocessing di-fit pada 18.238 baris panel train unik, bukan pada window yang mengulang baris. Hanya baris yang sudah tersedia pada cutoff 30 Juni 2025 yang dipakai. Target menggunakan standardisasi global mean/std populasi; lag dan target historis memakai scaler target yang sama. Kalender menggunakan scaler per field dari baris train yang sama. Fitur konstan memakai scale 1. Statistik dihitung float64; tensor numerik float32, kategori int64, dan mask bool. Kuantitas desimal asli tetap tersedia pada panel WP3 tanpa pembulatan; representasi tensor mengikuti keterbatasan presisi float32.

Vocabulary masing-masing static field hanya dari train yang telah diketahui. `__UNKNOWN__=0` menangani kategori baru, sedangkan `__UNKNOWN_COUNTRY__` tetap kategori bisnis tersendiri. Tidak ada fitting vocabulary dari registry masa depan, imputasi statistik, atau seleksi fitur berbasis holdout. Lag decoder yang belum tersedia bernilai 0 dalam ruang standardisasi dengan mask false; nilai itu tidak diperlakukan sebagai permintaan nol.

| Tensor input | Bentuk per sampel |
| --- | --- |
| Encoder observed: target historis + 4 lag | 12 × 5 |
| Encoder known: kalender | 12 × 9 |
| Decoder known: kalender | 1 × 9 |
| Decoder lag + mask | 1 × 4 |
| Static categorical: negara dan brand | 2 |

Label `y` dalam pcs dan `y_scaled` terpisah dari dictionary input. Sample ID, series ID, dan origin dikembalikan sebagai metadata. ID seri hash tidak dijadikan vocabulary embedding. NPZ tidak memakai pickle; urutan fitur, bentuk, dan dtype disimpan pada `tensor_schema.json`.

`WindowDataset` memberikan batch NumPy yang sama bagi `moebs_transformer`, `nspace_transformer`, dan `tft`. Verifikasi membaca file yang telah disimpan, memeriksa checksum, mencocokkan sample ID/seri/origin terhadap indeks, memeriksa schema/dtype, dan membandingkan digest seluruh batch pada tiga konsumen. Semua split mempunyai jumlah, urutan sampel, input, label, dan metadata identik. Batch terakhir tidak dibuang. Bukti ini adalah kontrak loader data WP4; belum merupakan bukti forward pass, adapter framework, atau training tiga model.

## Artefak, reproduksi, dan pengujian

Run aktif: `shared_split-window-v1_seed-42_20261107T010824Z_3cba52aa`, ditunjuk `active_split_manifest` pada `configs/current-data.json` dengan `split_ready_for_models=true`. Run pembanding saat verifikasi adalah `shared_split-window-v1_seed-42_20261107T010521Z_88e8480a`. Sepuluh artefak data deterministik identik checksum pada kedua run: dua indeks, tiga file tensor, preprocessing, schema tensor, kontrak fitur, laporan split, dan verifikasi loader. Dua run sintetis dengan source final juga menghasilkan checksum identik pada artefak utama. Run pembanding kemudian dihapus atas permintaan pengguna; run aktif dan dependensinya tetap dipertahankan, sesuai [catatan pembersihan](preprocessing-cleanup.md).

Setiap run baru dibuat tanpa menimpa run lama dan menyimpan konfigurasi efektif, snapshot pointer, tujuh snapshot source dependency, checksum input/output, seed 42, versi paket, hardware, waktu proses, commit/dirty flag, serta asumsi coverage/availability. Transformasi deterministik tidak menggunakan random; seed tersedia untuk shuffle loader yang eksplisit. Waktu proses WP4 adalah persiapan data dan tidak boleh dicampur ke waktu training/inferensi model WP6. Manifest baru disegel `ready_for_models=true` setelah verifikasi tiga konsumen; penggantian pointer aktif dilakukan setelah seluruh pengujian dan validasi berhasil.

```powershell
python -m src.features.split_windows --config configs/split-window.v1.json
python -m src.features.window_dataset --manifest artifacts/data/windowed/shared_split-window-v1_seed-42_20261107T010824Z_3cba52aa/split_manifest.json --verify-inputs
python -m unittest discover -s tests -v
python -m src.utils.git_protection
git diff --check
```

Seluruh **60 unittest lulus**, termasuk 12 pengujian WP4 dengan data sintetis. Pengujian mencakup batas split dan horizon, bentuk/dtype/mask, target pecahan dan inverse scaling, fitur konstan, cold start per origin, kategori unknown, perubahan masa depan yang tidak mengubah train/scaler, mutasi label horizon yang tidak mengubah inputnya sendiri, pemakaian histori holdout hanya setelah tersedia, horizon multistep dengan origin tetap, capture delay, penolakan split overlap/gap/cakupan parsial, target invalid/negatif, fitur atau checksum yang diubah, output publik, reproducibility, seed shuffle, dan batch terakhir. Validasi run aktual juga memverifikasi ulang seluruh checksum output dan ancestor/sumber.

Pointer aktif adalah berkas mutable yang berubah saat aktivasi; checksum pointer historis tidak dipaksakan terhadap pointer terbaru. Snapshot pointer immutable dalam run tetap diverifikasi. Semua input lain tetap diperiksa terhadap checksum yang dipin.

## Batas interpretasi dan kelanjutan

Kelengkapan sumber tahunan tetap asumsi yang dideklarasikan, karena log ekstraksi tidak tersedia. Adanya transaksi di setiap bulan dan endpoint tidak membuktikan kelengkapan setiap transaksi. Waktu target tersedia memakai proxy retrospektif Senin setelah akhir minggu dengan delay 0 hari; snapshot tahunan tidak dapat merekonstruksi transaksi backdated atau revisi. Kesimpulan eksperimen harus membawa keterbatasan ini.

WP5 wajib memakai satu `active_split_manifest` yang sama untuk ketiga model. Framework adapter boleh mengubah bentuk representasi, tetapi tidak boleh membuat ulang split, fit preprocessing dari holdout, mengubah horizon, atau membuang sampel. Training resmi dimulai setelah implementasi model dan protokol resource WP5-WP6 siap; WP4 tidak melatih model atau menentukan model pemenang.

Review pengungkapan: Codex, 7 November 2026. Laporan hanya memuat aturan metodologis, rentang kalender, hitungan gabungan, dan bentuk tensor; tidak memuat identitas negara/brand bisnis, pelanggan, PO, nilai transaksi, atau parameter scaler privat. Artefak rinci tetap pada `artifacts/` yang diabaikan Git. Ringkasan layak dilacak lokal tanpa mengotorisasi publikasi sumber.
