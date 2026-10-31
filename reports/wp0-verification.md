# Verifikasi WP0

Tanggal audit: 31 Oktober 2026 (Asia/Jakarta).

Status: **selesai**. Verifikasi menggunakan Python 3.12.2, jsonschema 4.25.1,
PowerShell, dan Git for Windows. Seluruh fixture pengujian bersifat sintetis.

| Kriteria | Bukti |
| --- | --- |
| Struktur proyek sesuai kebutuhan WP0 | `configs/`, `src/utils/`, `tests/`, `reports/`, dan direktori run lokal di `artifacts/` tersedia. |
| Data, artefak, checkpoint, log, dan kredensial terlindungi dari tracking biasa | `python -m src.utils.git_protection` lulus; `git status --ignored --short` menampilkan `dataset/` dan `artifacts/` sebagai ignored. Pemeriksaan seluruh index tidak menemukan path terproteksi. |
| Forced-add ditolak hook | Test menjalankan commit di repositori sementara: file sintetis terproteksi ditolak, sedangkan laporan aman dan `.env.example` diterima. Index repositori penelitian tidak diubah oleh test. |
| Format manifest lengkap | Skema v1 mewajibkan identitas run, konfigurasi efektif, seed, commit dan dirty flag, data/split/feature ID, timestamp UTC, paket, perangkat keras, timing, dan path artefak. |
| Dummy run menghasilkan manifest valid | Generator berhasil; CLI validator memeriksa skema, tanggal, isolasi path, keberadaan file, dan kesesuaian konfigurasi tersimpan. |
| Tidak menimpa run dan dapat direproduksi | Run ID memuat model/config/seed/timestamp/suffix unik; pembuatan eksklusif dan checksum sintetis untuk seed yang sama diuji. |
| Kebijakan hasil agregat | `docs/reproducibility.md` menetapkan pemisahan artefak; `reports/README.md` menetapkan pencatatan review anonimisasi sebelum publikasi. |

Hasil pengujian: `python -m unittest discover -s tests -v` menjalankan **13 test,
seluruhnya lulus**. Cakupannya meliputi metadata wajib, tanggal tidak valid,
konfigurasi sintetis, larangan akses dataset, artefak hilang, path traversal,
path lintas run, perbedaan konfigurasi, larangan overwrite, reproducibility seed,
aturan ignore, index terproteksi, dan eksekusi hook Git yang sebenarnya.

Contoh artefak penerimaan lokal yang berhasil divalidasi:

```text
artifacts/runs/tft_wp0-v1_seed-42_20261031t052805610052z_357d8341321f/
  config.resolved.json
  dummy_output.json
  run_manifest.json
```

Direktori tersebut diabaikan Git. Checksum dan jumlah sampel dummy bersifat
sintetis; tidak ada pembacaan XLSX SAP, training, checkpoint, atau metrik model.
Manifest mencatat `source_dirty: true` karena implementasi masih berupa perubahan
lokal. Sebelum eksperimen resmi, commit source/config yang telah direview agar
revision menunjuk implementasi yang dapat dipulihkan. WP6 tetap bertanggung jawab
atas runner training dan pencatatan perangkat/timing model yang sebenarnya.

Perintah reproduksi dari root repositori:

```powershell
python -m pip install -r requirements.txt
git config core.hooksPath .githooks
python -m unittest discover -s tests -v
python -m src.utils.manifest dummy --config configs/dummy-run.v1.json
python -m src.utils.git_protection
git status --ignored --short
```

Gunakan path manifest yang dicetak generator untuk validasi ulang:

```powershell
python -m src.utils.manifest validate artifacts/runs/<run_id>/run_manifest.json
```

Wrapper `scripts/verify-git-data-protection.sh` juga telah dijalankan melalui shell
bawaan Git for Windows dan lulus. Pemeriksaan ini berlaku pada ignore rules serta
index saat ini; tidak menyatakan bahwa seluruh riwayat Git atau remote telah
diaudit. Kebijakan remediasi riwayat tetap mengikuti `docs/reproducibility.md`.
