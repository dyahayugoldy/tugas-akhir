# Verifikasi WP2

Tanggal pemeriksaan: 6 November 2026. Status: selesai. Filter dan kontrak telah dijalankan; satuan pcs, kategori negara unknown, serta brand dari `Sales group` sudah dikonfirmasi dan diterapkan pada kontrak v3. Input WP2 layak dipanelkan; implementasi panel tetap menjadi pekerjaan WP3.

## Sumber dan reproduksi

Input: manifest WP1 `data-harmonization-v1_20261105T122845Z_8adc4a76` yang tercatat pada `configs/current-data.json`. Konfigurasi aktif: `configs/business-selection.v3.json` dan `configs/feature-contract.v3.json`. Run WP2 aktif: `business-selection-v3_20261106T075504Z_052ff4d7`. Konfigurasi dan artefak v1/v2 tetap disimpan sebagai riwayat keputusan sebelumnya.

```powershell
python -m src.data.business_selection --config configs/business-selection.v3.json
python -m unittest discover -s tests -v
python -c "import runpy; ns=runpy.run_path('tests/test_date_gap_outliers.py'); ns['test_date_gap_business_rule_boundaries_and_missing_dates'](); print('Date-gap regression: PASS')"
```

Setiap eksekusi menghasilkan direktori run baru di `artifacts/data/business_selected/`; tidak menimpa run lama atau mengubah pointer aktif secara otomatis. Setelah memverifikasi run, tautkan manifestnya pada `active_business_data_manifest` beserta laporan, EDA, kontrak, dan status kelayakannya di `configs/current-data.json`. `active_data_manifest` tetap menunjuk input kanonis WP1.

## Hasil filter

| Tahun sumber | Sebelum | Dikeluarkan unik | Dipertahankan |
| --- | ---: | ---: | ---: |
| 2023 | 77.077 | 5.893 | 71.184 |
| 2024 | 59.287 | 5.997 | 53.290 |
| 2025 | 43.882 | 5.340 | 38.542 |
| Gabungan | 180.246 | 17.230 | 163.016 |

| Aturan | Baris cocok sebelum filter, termasuk overlap | Baris cocok setelah filter |
| --- | ---: | ---: |
| Mata uang IDR | 1.238 | 0 |
| Buyer yang dikecualikan pada `sales_group` | 594 | 0 |
| Negara ID | 13.387 | 0 |
| Harga numerik nol | 3.199 | 0 |

Sebanyak 1.178 baris memenuhi lebih dari satu aturan. Total eksklusi memakai union baris, bukan penjumlahan hitungan per aturan. Kombinasi alasan dan lineage setiap baris tersedia hanya pada artefak privat. Kolom kode dinormalisasi dengan trim spasi dan kapitalisasi; literal `NA` tetap diperlakukan sebagai kode, bukan missing. Harga diparse menggunakan desimal finite sehingga `0`/`0.00` identik, sedangkan kosong, teks invalid, dan nonfinite bukan nol.

## Target dan kelayakan

Target utama adalah `order_quantity_item` dalam pcs berdasarkan konfirmasi pengguna, dengan formula penjumlahan per seri-minggu setelah definisi seri tervalidasi. Sebelum filter terdapat 326 kuantitas nol, tanpa kuantitas hilang/invalid atau negatif. Sesudah filter tidak ditemukan kuantitas hilang/invalid, nol, atau negatif; 450 baris mempunyai kuantitas pecahan yang dipertahankan tanpa pembulatan. Kebijakan tetap mengizinkan nol aktual; hilangnya seluruh nol pada run ini adalah hasil empat aturan bisnis, bukan filter tambahan atas target.

Tidak ditemukan duplikasi identik setelah filter berdasarkan 34 kolom kanonis; tidak ada deduplikasi tambahan. Semua baris hasil filter memiliki `KP Date` valid dan status kualitas WP1 `valid`. Sesuai keputusan pengguna, 4.928 baris tanpa `country_key` dipertahankan sebagai kategori `__UNKNOWN_COUNTRY__`: 1.961 pada 2023, 1.551 pada 2024, dan 1.416 pada 2025. Indikator audit `wp2_country_was_missing` mencatat asal kosong. Token ini berbeda dari `__UNKNOWN__` untuk kategori baru pada encoder. Mapping tidak mengambil informasi dari baris/tahun lain, menolak benturan token sumber, dan tidak mengubah data mentah atau kanonis WP1. Hasil v2 memiliki nol quality hold.

Pemeriksaan buyer menemukan nol `Sales group` kosong pada masing-masing tahun 2023, 2024, dan 2025, baik pada 180.246 baris sebelum filter maupun 163.016 baris sesudah filter. Statistik sebelum/sesudah per tahun tersedia di `selection_report.json` pada bagian `buyer_missingness`.

`target_eda.json` privat mencatat satuan pcs, missingness, nol/negatif, kuantitas pecahan, kuantil, volatilitas, perubahan mingguan, dan minggu tidak teramati. Diagnostik minggu menggunakan nilai pcs tanpa pembulatan dan tetap bersifat diagnostik per negara, termasuk kategori unknown; panel final negara × brand belum dibangun. Minggu kosong pada EDA WP2 belum diisi nol. Keputusan pengguna 6 November 2026 menetapkan minggu tanpa transaksi sebagai permintaan nol untuk regularisasi WP3, bukan imputasi nilai transaksi yang invalid. Tidak ada pemilihan fitur berdasarkan korelasi atau hasil holdout, fitting scaler/encoder, imputasi statistik, penalaan, maupun pelatihan model.

Kontrak mengklasifikasikan seluruh 34 kolom beserta transformasi, ketersediaan waktu, dan alasan penggunaan/pengecualian. `KP Date` berarti tanggal dibuatnya Kartu Produksi; `CRD Date` berarti tanggal keberangkatan ekspor dan dilarang sebagai fitur selama waktu ketersediaannya belum terbukti. Berdasarkan konfirmasi pengguna 6 November 2026, `sales_group` digunakan sebagai buyer untuk filter dan brand key untuk identitas seri. Mapping brand langsung memakai kode yang di-trim dan dikapitalisasi, tanpa inferensi dari tahun lain atau penggantian nama bisnis. Negara dan brand menjadi static categorical; fitur moneter dan status operasional dikecualikan; kalender adalah known future, lag target hanya observed past. Vocabulary/scaler nantinya harus fit pada train saja dengan token unknown konsisten.

Definisi seri dibekukan sebagai kombinasi `country_key` dan `sales_group`. Fallback `country_only` berarti menjumlahkan seluruh brand dalam satu negara per minggu. Fallback dapat mengurangi sparsity dan jumlah seri, tetapi menghilangkan detail brand dan mengubah unit analisis sempro; opsi tersebut tidak dipilih karena brand telah dikonfirmasi.

Manifest v3 menetapkan `ready_for_panel=true` tanpa blocker. Tidak ada `sales_group` kosong setelah filter atau quality hold. `calendar_regularization` pada kontrak v3 menetapkan minggu tanpa transaksi setelah filter sebagai 0 pcs pada minggu dengan cakupan sumber lengkap untuk seri aktif, dengan indikator audit `wp3_zero_filled`. Aktivasi seri, minggu parsial, dan penerapan zero-fill harus diaudit/diimplementasikan pada WP3 tanpa informasi masa depan. Tidak ada panel, model, atau split final; seluruh model nantinya memakai manifest WP2 yang sama. Batas waktu split/horizon tetap menjadi pekerjaan WP3–WP4.

## Pembuktian dan kerahasiaan

Seluruh 35 pengujian unittest lulus, termasuk delapan pengujian WP2. Satu regression test tanggal yang berbentuk fungsi juga dijalankan langsung dan lulus. Pengujian WP2 menggunakan data sintetis, mencakup normalisasi, empat filter dan overlap, harga nol vs invalid, kategori `NA`, quality holds, kelengkapan kontrak, checksum tampering, penolakan output publik, pembacaan ulang artefak, serta kesetaraan hasil pada run berulang. Pengujian v2 membuktikan satuan pcs, pemeliharaan kuantitas pecahan, mapping negara kosong tanpa penghapusan, indikator asal kosong, pemeliharaan kode negara valid, serta penolakan benturan token. Pengujian integrasi v3 membuktikan manifest layak dipanelkan saat brand valid dan tetap menahan brand kosong dengan quality hold; keputusan nol tersimpan sebagai kebijakan WP3 yang belum diterapkan pada panel.

Pipeline memverifikasi checksum input kanonis, cleaning manifest, dan tiga sumber mentah terhadap manifest, kemudian memastikan seluruh input tetap sama setelah pemrosesan. Hasil terfilter dibaca ulang dan dibandingkan persis dengan hasil di memori. Manifest mencatat checksum output, kode, commit sumber, konfigurasi, versi lingkungan, waktu proses, serta alasan seed tidak berlaku pada seleksi deterministik. Snapshot pointer dan konfigurasi harmonisasi ikut disimpan pada run aktif.

Dua eksekusi awal v1 (`business-selection-v1_20261105T141438Z_29af49a9` dan `business-selection-v1_20261105T141712Z_6c16f7ac`) menghasilkan checksum identik untuk transaksi terfilter, ledger seleksi, laporan filter, dan EDA. Perbandingan v2 terhadap v1 membuktikan jumlah/lineage baris dan seluruh nilai target tetap sama, seluruh kode negara terisi tetap sama, serta hanya negara kosong yang menjadi token unknown. Perbandingan artefak v3 terhadap v2 `business-selection-v2_20261105T143150Z_76206da8` membuktikan seluruh transaksi terfilter identik, termasuk target, kuantitas pecahan, negara unknown, dan lineage. Seluruh checksum output run v3 telah diverifikasi kembali; perubahan v3 berada pada kontrak dan kelayakan panel. `git diff --check` dan pemeriksaan `python -m src.utils.git_protection` lulus; direktori hasil WP2 terbukti diabaikan Git.

Data baris, identifier bisnis, transaksi, statistik numerik terperinci, dan ledger eksklusi hanya berada pada `artifacts/` yang diabaikan Git. Laporan ini memuat agregat gabungan/tahunan dan kebijakan; tidak memuat nama buyer/brand, PO, kode negara tujuan selain aturan eksklusi, atau nilai transaksi. Review pengungkapan: Codex, 6 November 2026; keputusan: ringkasan agregat ini layak dilacak secara lokal, tanpa mengotorisasi publikasi atau unggahan data.
