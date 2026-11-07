# Command WP6 per kombinasi model dan seed

Status 7 November 2026: kesembilan final run sudah selesai dan cohort WP6 telah
disahkan; lihat [verifikasi akhir WP6](../reports/wp6-verification.md). Command
di bawah tetap tersedia untuk reproduksi. Menjalankannya membuat run baru;
tidak otomatis mengganti anggota cohort yang sudah disahkan.

Jalankan dari root proyek melalui PowerShell. Setiap command `--train-one`
menjalankan tepat satu final training dari inisialisasi baru, menggunakan
hyperparameter dari manifest tuning yang disebutkan secara eksplisit.
Tunggu command selesai dan mencetak `WP6 final run verified` sebelum memilih
command berikutnya. Jangan gunakan `--config` untuk kebutuhan ini karena opsi
tersebut menjalankan tuning dan seluruh kombinasi sekaligus.

Tetapkan tiga path berikut sekali di terminal yang sama:

```powershell
$moebsTuning = 'artifacts/runs/moebs_transformer_models-wp6-v1_seed-2026_tuning_20261107t104004730909z_c9293bdb3347/run_manifest.json'
$nspaceTuning = 'artifacts/runs/nspace_transformer_models-wp6-v1_seed-2026_tuning_20261107t104710646287z_9517aecde0ee/run_manifest.json'
$tftTuning = 'artifacts/runs/tft_models-wp6-v1_seed-2026_tuning_20261107t112249030004z_6f533530b639/run_manifest.json'
```

1. MoEBS, seed 42 (sudah ada run final terverifikasi; jalankan hanya jika ingin run baru):

```powershell
.\.venv\Scripts\python.exe -B -m src.models.experiment --train-one moebs_transformer --seed 42 --tuning-manifest $moebsTuning
```

2. MoEBS, seed 123:

```powershell
.\.venv\Scripts\python.exe -B -m src.models.experiment --train-one moebs_transformer --seed 123 --tuning-manifest $moebsTuning
```

3. MoEBS, seed 2026:

```powershell
.\.venv\Scripts\python.exe -B -m src.models.experiment --train-one moebs_transformer --seed 2026 --tuning-manifest $moebsTuning
```

4. N-Space, seed 42:

```powershell
.\.venv\Scripts\python.exe -B -m src.models.experiment --train-one nspace_transformer --seed 42 --tuning-manifest $nspaceTuning
```

5. N-Space, seed 123:

```powershell
.\.venv\Scripts\python.exe -B -m src.models.experiment --train-one nspace_transformer --seed 123 --tuning-manifest $nspaceTuning
```

6. N-Space, seed 2026:

```powershell
.\.venv\Scripts\python.exe -B -m src.models.experiment --train-one nspace_transformer --seed 2026 --tuning-manifest $nspaceTuning
```

7. TFT, seed 42:

```powershell
.\.venv\Scripts\python.exe -B -m src.models.experiment --train-one tft --seed 42 --tuning-manifest $tftTuning
```

8. TFT, seed 123:

```powershell
.\.venv\Scripts\python.exe -B -m src.models.experiment --train-one tft --seed 123 --tuning-manifest $tftTuning
```

9. TFT, seed 2026:

```powershell
.\.venv\Scripts\python.exe -B -m src.models.experiment --train-one tft --seed 2026 --tuning-manifest $tftTuning
```

Hasil tiap command disimpan dalam direktori unik di `artifacts/runs/`.
Menjalankan ulang kombinasi yang sama membuat run baru, tanpa menimpa run lama.
Command memeriksa checksum manifest tuning, kesamaan konfigurasi dan binding
data sebelum training. Semua kombinasi tetap memakai maksimum 100 epoch,
patience 10, dan pemulihan checkpoint dengan RMSE validation terendah.

Pemeriksaan satu run yang sudah selesai:

```powershell
.\.venv\Scripts\python.exe -B -m src.models.experiment --verify 'artifacts/runs/<run_id>/run_manifest.json'
```

Sembilan command ini tidak otomatis membentuk cohort baru. Cohort resmi yang
sudah disahkan beserta audit provenance/waktu tuning TFT dan batas klaim
efisiensi tercantum pada [verifikasi akhir WP6](../reports/wp6-verification.md).
