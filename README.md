# BukuKas v2 (Flask CRUD Keuangan)

Aplikasi web CRUD pengelolaan keuangan menggunakan Python Flask dan SQLite dengan tiga modul utama:

- **Transaksi**: Catat pemasukan dan pengeluaran yang terhubung ke akun, lengkap dengan filter jenis, akun, bulan, dan pencarian.
- **Akun**: Kelola dompet dan rekening (Cash, DANA, GoPay, ShopeePay, Bank, dll.) beserta perhitungan saldo otomatis dari saldo awal dan transaksi.
- **Anggaran**: Tetapkan batas pengeluaran bulanan per kategori (Makan, Minum, Jajan, dll.) dengan pelacakan realisasi otomatis dari pengeluaran pada kategori dan bulan yang sama.

## Cara Menjalankan

1. Ekstrak folder `keuangan-flask` ke dalam folder proyek Anda.
2. Buka terminal dan masuk ke folder tersebut:
   ```bash
   cd keuangan-flask
   ```
3. Buat dan aktifkan virtual environment:
   ```bash
   python -m venv venv
   ```
   - Windows (PowerShell): `.\venv\Scripts\Activate.ps1`
   - Linux/macOS/Termux: `source venv/bin/activate`
4. Pasang dependensi:
   ```bash
   pip install -r requirements.txt
   ```
5. Jalankan aplikasi:
   ```bash
   python app.py
   ```
6. Buka `http://127.0.0.1:5000` di browser. File database `keuangan.db` akan dibuat otomatis di dalam folder ini.
