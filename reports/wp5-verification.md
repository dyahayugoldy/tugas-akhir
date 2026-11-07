# Verifikasi WP5

Tanggal pemeriksaan: 7 November 2026. Status: selesai untuk implementasi tiga
model, interface bersama, acceptance satu epoch, checkpoint reload, dan kontrak
kausal. Eksperimen komparatif resmi multi-seed tetap WP6; evaluasi test tetap WP7.

## Hasil implementasi

Satu framework PyTorch 2.9.1+cpu digunakan untuk MoEBS-Transformer, N-Space
Transformer, dan TFT. `ForecastAdapter` menyediakan `fit`, `predict`, `save`,
`load`, `metadata`, serta forward yang menolak label/metadata sebagai input.
`ModelData` memakai loader WP4 dan mengikat checksum manifest, schema, vocabulary
train, scaler, panjang encoder dan horizon. Tidak ada fit preprocessing tambahan.

MoEBS mengoptimasi Transformer encoder-decoder menggunakan gerak predator/prey,
archive Pareto dan grid leader/crowding. N-Space mengikuti L'Heureux dkk. (2022)
dengan contextual fusion, proyeksi linear terlatih, dan encoder-decoder. TFT
memuat static context GRN, VSN static/encoder/decoder, recurrent encoder/decoder,
gating, static enrichment dan interpretable attention dengan shared V.

Konfigurasi terversi: `configs/models.wp5.v1.json`. Rujukan, aturan tiap komponen,
kontrak input, adaptasi paper, perlakuan nol, batas interpretasi dan rekomendasi
WP6 tersedia di [docs/wp5-models.md](../docs/wp5-models.md).

## Input dan cohort acceptance

Manifest bersama tetap
`shared_split-window-v1_seed-42_20261107T010824Z_3cba52aa`.
Setiap kandidat dilatih pada seluruh 13.663 sampel train dan divalidasi pada
seluruh 1.587 sampel validation. Encoder 12 minggu, horizon 1 minggu, kalender,
target kuantitas pcs, kombinasi seri, batas split, preprocessing, serta mask
availability identik pada ketiga model. Seri hash hanya metadata; kolom bisnis
sensitif dan fitur dengan availability yang belum terbukti tidak ditambahkan.

Cohort privat final:
`artifacts/runs/wp5-cohort_20261107T092245Z_78732058.json`.
Berkas ini mencatat checksum ketiga manifest, checksum split, digest seluruh
sampel/tensor train/validation dan empat evaluasi per model yang setara.

| Model | Run acceptance yang diverifikasi |
| --- | --- |
| MoEBS-Transformer | `moebs_transformer_models-wp5-v1_seed-42_20261107t091938328740z_1760ec9395e3` |
| N-Space Transformer | `nspace_transformer_models-wp5-v1_seed-42_20261107t092026322386z_7aa8fe2efb7d` |
| TFT | `tft_models-wp5-v1_seed-42_20261107t092104727135z_57fa02ca0ca5` |

Ketiga run memakai seed 42, CPU float32, dua thread, batch 256, AdamW, gradient
clipping, dan satu epoch penuh per kandidat tanpa membuang batch terakhir.
Empat kandidat per model menghasilkan total 12 trial selesai dan nol trial gagal
pada cohort final. Model lain memakai random search pada ruang dan batas evaluasi
yang sama dengan MoEBS. Batas per trial 180 detik dan per search 900 detik; MoEBS
juga dibatasi populasi 2 dan satu iterasi. Budget ini untuk acceptance, bukan
klaim optimasi telah konvergen.

Setiap model menghasilkan 1.587 prediksi validation berurutan. Prediksi setelah
reload identik secara bit-for-bit terhadap prediksi checkpoint sebelum reload.
Jumlah prediksi test oleh model adalah **nol**. Validator WP4 membaca tensor test
untuk pemeriksaan integritas/kesetaraan loader saja; test tidak digunakan dalam
fit, objective, pemilihan kandidat atau checkpoint, maupun metrik WP5.

## Bukti waktu dan metadata

Nilai berikut membuktikan pencatatan resource berjalan, bukan benchmark final
atau peringkat efisiensi. Training terpilih mencakup fit dan validation selection;
total trial menjumlahkan waktu training empat kandidat. Search juga mencakup
overhead evaluasi dan penyimpanan. Inferensi mencakup adapter conversion/forward
pada 1.587 sampel validation, di luar pembangunan fitur dan penulisan hasil.
Sebagian unit test sintetis berjalan bersamaan dengan acceptance sehingga angka
ini tidak dipakai untuk klaim efisiensi antar-model.

| Model | Parameter kandidat terpilih | Training terpilih (s) | Total training trial (s) | Search (s) | Inferensi validation (s) |
| --- | ---: | ---: | ---: | ---: | ---: |
| MoEBS-Transformer | 44.841 | 8,942 | 41,051 | 45,074 | 0,198 |
| N-Space Transformer | 43.977 | 11,320 | 35,416 | 36,536 | 0,221 |
| TFT | 158.016 | 15,806 | 94,824 | 99,372 | 0,380 |

Runtime terverifikasi adalah Python 3.12.2, PyTorch 2.9.1+cpu, NumPy 2.2.6,
pandas 2.3.3, openpyxl 3.1.5 dan jsonschema 4.25.1. CUDA tidak tersedia pada
runtime terpasang. Versi seluruh paket dan metadata CPU/perangkat tersimpan pada
setiap manifest; `pip check` tidak menemukan konflik dependensi.

Masing-masing run menyimpan 28 snapshot source/konfigurasi/spesifikasi beserta
checksum, pointer dan split snapshot, kontrak data, konfigurasi efektif terpilih,
seluruh trial dan objective, checkpoint serta SHA256, prediksi validation,
metrik, timing, dan hasil verifikasi. `source_dirty=true` dicatat secara jujur
karena implementasi belum di-commit; snapshot source menyediakan kode yang
dipakai. Checkpoint mendukung inferensi/reload, bukan resume optimizer.

Satu percobaan awal sebelum cohort final berhenti saat serialisasi tanggal
prediksi karena tipe `numpy.str_`; audit `failure.json` tetap dipertahankan pada
run privat tersebut. Konversi ke string standar diperbaiki dan tercakup dalam
test integrasi WP4 ke WP5. Run awal itu tidak termasuk cohort berhasil di atas.

## Pengujian dan integritas

Perintah berikut dijalankan dari root proyek:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -B -m src.models.acceptance --config configs/models.wp5.v1.json
.\.venv\Scripts\python.exe -B -m src.models.acceptance --verify artifacts/runs/<run_id>/run_manifest.json
.\.venv\Scripts\python.exe -B -m src.features.window_dataset --manifest artifacts/data/windowed/shared_split-window-v1_seed-42_20261107T010824Z_3cba52aa/split_manifest.json --verify-inputs
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -B -m src.utils.git_protection
```

Seluruh **72 test lulus**, termasuk 12 test WP5 yang mencakup:

- Forward ketiga model, float32, gradien finite dan unknown embedding yang valid.
- Horizon satu dan tiga langkah, termasuk batch terakhir berukuran satu.
- Mutasi nilai yang tertutup mask tidak mengubah prediksi; mask dapat menutup
  sentinel nonfinite tanpa mengubah informasi yang tersedia.
- Label/metadata ditolak oleh forward; mutasi label tidak memengaruhi inferensi.
- Mutasi konteks decoder masa depan tidak mengubah prediksi horizon sebelumnya.
- Bobot selection/attention TFT ternormalisasi dan attention kausal.
- Satu epoch memakai semua sampel, parameter terbarui, seed dapat direproduksi,
  dan reload menghasilkan prediksi identik dengan sample ID/order tetap.
- Checkpoint/split berbeda, kategori di luar vocabulary, input tidak sah,
  overwrite checkpoint dan checkpoint/artifact yang diubah ditolak.
- Dominasi Pareto, archive capacity, gerak MoEBS, reproducibility kandidat,
  pencatatan trial timeout dan batas jumlah evaluasi.
- Formula metrik, aktual nol, coverage MAPE, epsilon SMAPE dan `r2` konstan null.
- Integrasi WP4 ke WP5 untuk ketiga model pada lineage sintetis lengkap,
  manifest sama, tanpa perubahan split, serta pemeriksaan checksum hasil.

Ketiga manifest acceptance dan semua checksum artefaknya diperiksa ulang setelah
run; checksum manifest pada cohort cocok. Pemeriksaan ulang WP4 atas output,
ancestor/sumber, indeks, schema dan ketiga konsumen lulus. Data mentah dan semua
artefak data aktif tidak ditulis ulang. Git data-protection checks dan
`git diff --check` lulus.

## Adaptasi dan kelanjutan

Objective MoEBS memakai validation RMSE, validation MAE dan jumlah parameter,
bukan test sebagaimana prosedur paper asal. Proyeksi/konteks N-Space tetap ada,
sedangkan decoder memakai target encoder terakhir lalu prediksinya sendiri agar
label masa depan tidak masuk saat training atau inferensi. TFT menggunakan point
head/MSE sebagai adaptasi dari quantile head/loss asli. Adaptasi algoritme untuk
Pareto pair ranking, parameter diskret dan fallback numerik didokumentasikan;
implementasi ini bukan klaim replikasi persis hasil paper.

Input masih terbatas pada fitur yang lolos kontrak availability. Harga/moneter,
SAP Season, currency dan Material tidak otomatis menjadi fitur. Empty-country
business category tetap berbeda dari unknown encoder; unknown embedding yang
belum muncul di train memiliki keterbatasan cold category. Asumsi coverage sumber
tahunan dan availability retrospektif WP3-WP4 tetap berlaku.

WP6 perlu menetapkan protokol resmi pada hardware sasaran, budget hingga
konvergensi dan seed bersama; usulan 42/123/2026 sudah tercatat di konfigurasi.
Validation hanya sembilan minggu kalender dan window saling overlap, sehingga
jumlah window tidak diperlakukan sebagai observasi waktu independen. Pembesaran
tuning tidak boleh didasarkan pada test. Metrik akhir, analisis error test, waktu
inferensi benchmark dan stabilitas interpretabilitas tetap WP6-WP8. WP5 tidak
menentukan model pemenang.

Review pengungkapan: Codex, 7 November 2026. Sumber bukti adalah cohort dan tiga
run privat di atas, hasil test sintetis, serta verifikasi integritas WP4. Laporan
hanya memuat metode, ID run, hitungan sampel, parameter model, versi dan waktu
komputasi. Tidak ada kode negara/brand bisnis, pelanggan, PO, nilai transaksi,
parameter scaler, bobot model atau prediksi rinci yang disalin ke laporan.
Ringkasan ini aman dilacak lokal; publikasi sumber SAP tidak diotorisasi.
