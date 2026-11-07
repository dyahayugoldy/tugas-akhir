# Pembersihan hasil preprocessing

Tanggal: 7 November 2026. Dilaksanakan atas permintaan pengguna untuk menghapus dataset preprocessing lama dan mempertahankan hasil terbaru yang diperlukan.

## Hasil

Sebanyak 10 direktori run lama dan 3 workbook preprocessing lama dihapus: total 113 berkas, 80.417.899 byte (sekitar 80 MB). Penghapusan menggunakan daftar target eksplisit di dalam `artifacts/data/`, setelah pemeriksaan bahwa target tidak beririsan dengan run aktif dan tidak mengandung reparse point. Sumber mentah tidak dihapus atau diubah.

| Tahap | Hasil yang dipertahankan |
| --- | --- |
| Cleaning aktif | `clean-v2_20261104T040822Z_a4d0b0f7` |
| Kanonis WP1 | `data-harmonization-v1_20261105T122845Z_8adc4a76` |
| Seleksi bisnis WP2 | `business-selection-v3_20261106T075504Z_052ff4d7` |
| Panel WP3 | `panel-weekly-v1_20261106T091154Z_6030bb53` |
| Window WP4 | `shared_split-window-v1_seed-42_20261107T010824Z_3cba52aa` |

Satu run aktif per tahap dipertahankan karena manifest WP4 dan pipeline reproduksi masih bergantung pada tahapan sebelumnya. Semua pointer aktif pada `configs/current-data.json` tetap berlaku; tidak ada perubahan manifest atau checksum historis.

## Run yang dihapus

- `business-selection-v1_20261105T141438Z_29af49a9`
- `business-selection-v1_20261105T141712Z_6c16f7ac`
- `business-selection-v2_20261105T143150Z_76206da8`
- `clean-v1_20261104T030634Z_125c0a27`
- `drop-missing-cols-v1_20261104T034253Z_51aa5fe7`
- `panel-weekly-v1_20261106T090035Z_c29f700b`
- `panel-weekly-v1_20261106T090426Z_ebc0a1b6`
- `panel-weekly-v1_20261106T090452Z_8e16d65b`
- `panel-weekly-v1_20261106T091118Z_63c4f7aa`
- `shared_split-window-v1_seed-42_20261107T010521Z_88e8480a`

Tiga salinan `2023_cleaned.xlsx`, `2024_cleaned.xlsx`, dan `2025_cleaned.xlsx` pada `clean-v1_20261104T031315Z_9f676a7e` juga dihapus. Manifest dan metadata audit run tersebut tetap dipertahankan, bersama `missing-v1_20261104T033854Z_4925985f`, karena `configs/data-cleaning.v2.json` dan proses restorasi kolom memerlukan bukti duplikasi serta missingness tersebut. Kedua direktori historis ini berisi bukti audit yang masih dibutuhkan, bukan dataset preprocessing aktif tambahan. Profil EDA dan laporan kualitas di luar `artifacts/data/` tetap dipertahankan.

## Verifikasi dan pemulihan

Checksum 58 berkas sumber/artefak aktif dibandingkan sebelum dan sesudah penghapusan dan seluruhnya identik. Validasi WP4 mencakup checksum output dan ancestor/sumber, indeks sampel, schema tensor, serta ketiga konsumen data. Jumlah sampel tetap 13.663 train, 1.587 validation, dan 3.187 test.

Penghapusan bersifat permanen dan tidak menggunakan Recycle Bin. Run historis yang dihapus tidak dapat dipulihkan langsung dari Git karena `artifacts/` tidak dilacak. Dataset turunannya dapat dibangun ulang dari sumber, konfigurasi, dan pipeline yang tersedia; ID run/timestamp baru akan berbeda. Hasil pembandingan dua run pada laporan verifikasi sebelumnya merupakan catatan pemeriksaan sebelum pembersihan, bukan pernyataan bahwa kedua run masih tersimpan.

Review pengungkapan: Codex, 7 November 2026. Laporan memuat ID run, kebijakan retensi, dan hitungan berkas agregat tanpa identitas pelanggan, brand bisnis, PO, atau nilai transaksi.
