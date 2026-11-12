* tarik raw data setiap tahun dengan metode yang sama
* list numerik dan kategori
* tampilkan kemungkinan masing2 value yang ada di masing2 kolom yang sifatnya kategori
* untuk kolom yang sifatnya numerik cek outlier
* hapus data duplikat yang identik sama dari setiap row
* hapus kolom yang kosong (kalo ada)
* ~~cek setiap kolom yang kosong jika jumlah kolom yang kosong kurang dari 0.5% hapus~~
* ~~jika ada data kosong pada nilai numerik, terdapat outlier yang jauh dari q1\&q3 maka ambil median dari kolom, jika outlier dekat dengan q1\&q3 pakai mean~~
* jangan hapus outlier
* file dataset diambil dari KP Date berdasarkan tahun
* format tanggal tidak konsisten dd/mm/yyyy dan mm/dd/yyyy
* jika pada format tanggal KP DATE > 1tahun atau < 2tahun dari CRD Date maka masuk ke data outlier,  tampilkan dalam diagram box plot
* jika tanggal CRD date lebih dari 2tahun dan kurang dari 1 tahun dari tahun KP Date maka masuk ke data outlier

&#x20;

selaraskan task WP2 dengan poin berikut ini 

* KP Date adalah Kartu Produksi di mana tanggal yang tertera adalah tanggal dibuatnya Kartu Produksi, sedangkan CRD Date adalah tanggal keberangkatan ekspor
* keluarkan data yang mempunyai nilai IDR pada Document Currency
* hapus buyer PEB, KWK, LNS, CPC
* Keluarkan country ID dari seluruh dataset
* gunakan Order Quantity sebagai target utama
* hapus semua baris yang mempunyai Net Price dengan nilai 0 atau 0.00
* petakan kolom sales group menjadi pemetaan buyer



WP3 

* mapping brand dengan kolom sales costumer 
* jelaskan mengenai fallback country\_only
* tetapkan minggu tanpa transaksi sebagai permintaan nol



