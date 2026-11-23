import os
import sqlite3
from datetime import date, datetime
from flask import Flask, flash, redirect, render_template, request, url_for

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-kunci-keuangan-lokal")

# Simpan database di folder yang sama dengan app.py agar tidak bergantung pada working directory saat dijalankan.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "keuangan.db")

JENIS_VALID = {"pemasukan", "pengeluaran"}
TIPE_AKUN_VALID = ("Tunai", "E-Wallet", "Bank", "Lainnya")
KATEGORI_BAWAAN = (
    "Makan",
    "Minum",
    "Jajan",
    "Transportasi",
    "Tagihan",
    "Belanja",
    "Hiburan",
    "Gaji",
    "Uang Saku",
    "Lainnya",
)
AKUN_AWAL = (
    ("Cash", "Tunai", 0),
    ("DANA", "E-Wallet", 0),
    ("GoPay", "E-Wallet", 0),
    ("ShopeePay", "E-Wallet", 0),
)


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meta (
                kunci TEXT PRIMARY KEY,
                nilai TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS akun (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nama TEXT NOT NULL UNIQUE COLLATE NOCASE,
                tipe TEXT NOT NULL,
                saldo_awal INTEGER NOT NULL DEFAULT 0 CHECK (saldo_awal >= 0),
                dibuat_pada TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS anggaran (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bulan TEXT NOT NULL,
                kategori TEXT NOT NULL COLLATE NOCASE,
                batas_nominal INTEGER NOT NULL CHECK (batas_nominal > 0),
                catatan TEXT DEFAULT '',
                dibuat_pada TEXT NOT NULL,
                UNIQUE (bulan, kategori)
            )
            """
        )
        # Nominal disimpan sebagai INTEGER Rupiah untuk menghindari galat pembulatan floating-point.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS transaksi (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tanggal TEXT NOT NULL,
                jenis TEXT NOT NULL CHECK (jenis IN ('pemasukan', 'pengeluaran')),
                akun_id INTEGER REFERENCES akun(id) ON DELETE RESTRICT,
                kategori TEXT NOT NULL,
                nominal INTEGER NOT NULL CHECK (nominal > 0),
                catatan TEXT DEFAULT '',
                dibuat_pada TEXT NOT NULL
            )
            """
        )

        # Migrasi ringan jika pengguna menimpa proyek versi sebelumnya yang belum memiliki kolom akun_id.
        kolom_transaksi = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(transaksi)").fetchall()
        }
        if "akun_id" not in kolom_transaksi:
            conn.execute(
                "ALTER TABLE transaksi ADD COLUMN akun_id INTEGER REFERENCES akun(id) ON DELETE RESTRICT"
            )

        # Seed akun bawaan hanya sekali saat database pertama kali dibuat agar akun yang dihapus pengguna tidak muncul lagi.
        sudah_seed = conn.execute(
            "SELECT 1 FROM meta WHERE kunci = 'akun_awal_dibuat'"
        ).fetchone()
        if not sudah_seed:
            sekarang = datetime.now().isoformat(timespec="seconds")
            for nama, tipe, saldo_awal in AKUN_AWAL:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO akun (nama, tipe, saldo_awal, dibuat_pada)
                    VALUES (?, ?, ?, ?)
                    """,
                    (nama, tipe, saldo_awal, sekarang),
                )
            conn.execute(
                "INSERT INTO meta (kunci, nilai) VALUES ('akun_awal_dibuat', '1')"
            )


@app.template_filter("rupiah")
def format_rupiah(angka):
    nilai = int(angka or 0)
    tanda = "-" if nilai < 0 else ""
    terformat = f"{abs(nilai):,}".replace(",", ".")
    return f"{tanda}Rp {terformat}"


def bulan_sekarang():
    return date.today().strftime("%Y-%m")


def validasi_bulan(nilai):
    try:
        datetime.strptime(nilai, "%Y-%m")
        return True
    except ValueError:
        return False


def ambil_daftar_akun(conn):
    return conn.execute(
        """
        SELECT
            a.id,
            a.nama,
            a.tipe,
            a.saldo_awal,
            COALESCE(SUM(CASE WHEN t.jenis = 'pemasukan' THEN t.nominal ELSE 0 END), 0) AS total_masuk,
            COALESCE(SUM(CASE WHEN t.jenis = 'pengeluaran' THEN t.nominal ELSE 0 END), 0) AS total_keluar,
            COUNT(t.id) AS jumlah_transaksi,
            a.saldo_awal
                + COALESCE(SUM(CASE WHEN t.jenis = 'pemasukan' THEN t.nominal ELSE 0 END), 0)
                - COALESCE(SUM(CASE WHEN t.jenis = 'pengeluaran' THEN t.nominal ELSE 0 END), 0) AS saldo_sekarang
        FROM akun a
        LEFT JOIN transaksi t ON t.akun_id = a.id
        GROUP BY a.id
        ORDER BY a.nama COLLATE NOCASE ASC
        """
    ).fetchall()


def ambil_saran_kategori(conn):
    baris = conn.execute(
        """
        SELECT DISTINCT kategori FROM anggaran
        UNION
        SELECT DISTINCT kategori FROM transaksi
        """
    ).fetchall()
    gabungan = list(KATEGORI_BAWAAN)
    tercatat_lower = {k.lower() for k in gabungan}
    for row in baris:
        nama = (row["kategori"] or "").strip()
        if nama and nama.lower() not in tercatat_lower:
            gabungan.append(nama)
            tercatat_lower.add(nama.lower())
    return gabungan


def validasi_form_transaksi(form, conn):
    errors = []
    tanggal = form.get("tanggal", "").strip()
    jenis = form.get("jenis", "").strip().lower()
    akun_id_mentah = form.get("akun_id", "").strip()
    kategori = form.get("kategori", "").strip()
    nominal_mentah = form.get("nominal", "").strip()
    catatan = form.get("catatan", "").strip()

    try:
        datetime.strptime(tanggal, "%Y-%m-%d")
    except ValueError:
        errors.append("Tanggal harus menggunakan format YYYY-MM-DD yang valid.")

    if jenis not in JENIS_VALID:
        errors.append("Pilih jenis transaksi antara pemasukan atau pengeluaran.")

    akun_id = None
    if not akun_id_mentah:
        errors.append("Akun wajib dipilih.")
    else:
        try:
            akun_id = int(akun_id_mentah)
            ada_akun = conn.execute(
                "SELECT 1 FROM akun WHERE id = ?", (akun_id,)
            ).fetchone()
            if not ada_akun:
                errors.append("Akun yang dipilih tidak ditemukan.")
        except ValueError:
            errors.append("Pilihan akun tidak valid.")

    if not kategori:
        errors.append("Kategori wajib diisi.")
    elif len(kategori) > 60:
        errors.append("Kategori maksimal 60 karakter.")

    nominal = 0
    try:
        nominal = int(nominal_mentah)
        if nominal <= 0:
            errors.append("Nominal harus lebih besar dari 0.")
    except ValueError:
        errors.append("Nominal harus berupa angka bulat tanpa titik atau koma.")

    if len(catatan) > 200:
        errors.append("Catatan maksimal 200 karakter.")

    data = {
        "tanggal": tanggal,
        "jenis": jenis,
        "akun_id": akun_id_mentah if errors else akun_id,
        "kategori": kategori,
        "nominal": nominal_mentah if errors else nominal,
        "catatan": catatan,
    }
    return data, errors


def validasi_form_akun(form):
    errors = []
    nama = form.get("nama", "").strip()
    tipe = form.get("tipe", "").strip()
    saldo_mentah = form.get("saldo_awal", "0").strip()

    if not nama:
        errors.append("Nama akun wajib diisi.")
    elif len(nama) > 50:
        errors.append("Nama akun maksimal 50 karakter.")

    if tipe not in TIPE_AKUN_VALID:
        errors.append("Pilih tipe akun yang tersedia.")

    saldo_awal = 0
    try:
        saldo_awal = int(saldo_mentah)
        if saldo_awal < 0:
            errors.append("Saldo awal tidak boleh kurang dari 0.")
    except ValueError:
        errors.append("Saldo awal harus berupa angka bulat tanpa titik atau koma.")

    data = {
        "nama": nama,
        "tipe": tipe if tipe in TIPE_AKUN_VALID else TIPE_AKUN_VALID[0],
        "saldo_awal": saldo_mentah if errors else saldo_awal,
    }
    return data, errors


def validasi_form_anggaran(form):
    errors = []
    bulan = form.get("bulan", "").strip()
    kategori = form.get("kategori", "").strip()
    batas_mentah = form.get("batas_nominal", "").strip()
    catatan = form.get("catatan", "").strip()

    if not validasi_bulan(bulan):
        errors.append("Bulan anggaran harus menggunakan format YYYY-MM yang valid.")

    if not kategori:
        errors.append("Nama kategori anggaran wajib diisi.")
    elif len(kategori) > 60:
        errors.append("Nama kategori maksimal 60 karakter.")

    batas_nominal = 0
    try:
        batas_nominal = int(batas_mentah)
        if batas_nominal <= 0:
            errors.append("Batas anggaran harus lebih besar dari 0.")
    except ValueError:
        errors.append("Batas anggaran harus berupa angka bulat tanpa titik atau koma.")

    if len(catatan) > 200:
        errors.append("Catatan maksimal 200 karakter.")

    data = {
        "bulan": bulan,
        "kategori": kategori,
        "batas_nominal": batas_mentah if errors else batas_nominal,
        "catatan": catatan,
    }
    return data, errors


# ==================== TRANSAKSI ====================


@app.route("/")
def index():
    filter_jenis = request.args.get("jenis", "semua").strip().lower()
    filter_akun = request.args.get("akun", "semua").strip()
    filter_bulan = request.args.get("bulan", "").strip()
    kata_kunci = request.args.get("q", "").strip()

    if filter_jenis not in {"semua", "pemasukan", "pengeluaran"}:
        filter_jenis = "semua"

    if filter_bulan and not validasi_bulan(filter_bulan):
        filter_bulan = ""

    akun_filter_id = None
    if filter_akun != "semua":
        try:
            akun_filter_id = int(filter_akun)
        except ValueError:
            filter_akun = "semua"

    transaksi_list = []
    akun_list = []
    ringkasan = {"saldo_total": 0, "pemasukan": 0, "pengeluaran": 0}
    total_semua = 0
    db_error = None

    try:
        with get_db() as conn:
            akun_list = ambil_daftar_akun(conn)
            total_saldo_awal = sum(a["saldo_awal"] for a in akun_list)

            baris_global = conn.execute(
                """
                SELECT
                    COALESCE(SUM(CASE WHEN jenis = 'pemasukan' THEN nominal ELSE 0 END), 0) AS total_masuk,
                    COALESCE(SUM(CASE WHEN jenis = 'pengeluaran' THEN nominal ELSE 0 END), 0) AS total_keluar,
                    COUNT(*) AS jumlah_total
                FROM transaksi
                """
            ).fetchone()

            total_semua = baris_global["jumlah_total"]
            saldo_total = (
                total_saldo_awal
                + baris_global["total_masuk"]
                - baris_global["total_keluar"]
            )

            kondisi = []
            parameter = []

            if filter_jenis != "semua":
                kondisi.append("t.jenis = ?")
                parameter.append(filter_jenis)

            if akun_filter_id is not None:
                kondisi.append("t.akun_id = ?")
                parameter.append(akun_filter_id)

            if filter_bulan:
                kondisi.append("substr(t.tanggal, 1, 7) = ?")
                parameter.append(filter_bulan)

            if kata_kunci:
                kondisi.append(
                    "(LOWER(t.kategori) LIKE ? OR LOWER(t.catatan) LIKE ? OR LOWER(COALESCE(a.nama, '')) LIKE ?)"
                )
                pola = f"%{kata_kunci.lower()}%"
                parameter.extend([pola, pola, pola])

            where_sql = f"WHERE {' AND '.join(kondisi)}" if kondisi else ""

            transaksi_list = conn.execute(
                f"""
                SELECT
                    t.id,
                    t.tanggal,
                    t.jenis,
                    t.akun_id,
                    COALESCE(a.nama, 'Tanpa Akun') AS nama_akun,
                    t.kategori,
                    t.nominal,
                    t.catatan
                FROM transaksi t
                LEFT JOIN akun a ON a.id = t.akun_id
                {where_sql}
                ORDER BY t.tanggal DESC, t.id DESC
                """,
                parameter,
            ).fetchall()

            # Ringkasan pemasukan & pengeluaran mengikuti filter bulan jika aktif agar sesuai dengan daftar yang dilihat.
            if filter_bulan:
                baris_arus = conn.execute(
                    """
                    SELECT
                        COALESCE(SUM(CASE WHEN jenis = 'pemasukan' THEN nominal ELSE 0 END), 0) AS total_masuk,
                        COALESCE(SUM(CASE WHEN jenis = 'pengeluaran' THEN nominal ELSE 0 END), 0) AS total_keluar
                    FROM transaksi
                    WHERE substr(tanggal, 1, 7) = ?
                    """,
                    (filter_bulan,),
                ).fetchone()
                masuk_tampil = baris_arus["total_masuk"]
                keluar_tampil = baris_arus["total_keluar"]
            else:
                masuk_tampil = baris_global["total_masuk"]
                keluar_tampil = baris_global["total_keluar"]

            ringkasan = {
                "saldo_total": saldo_total,
                "pemasukan": masuk_tampil,
                "pengeluaran": keluar_tampil,
            }
    except sqlite3.Error as exc:
        db_error = f"Gagal membaca data dari database: {exc}"

    return render_template(
        "index.html",
        active_nav="transaksi",
        transaksi_list=transaksi_list,
        akun_list=akun_list,
        ringkasan=ringkasan,
        total_semua=total_semua,
        filter_jenis=filter_jenis,
        filter_akun=filter_akun,
        filter_bulan=filter_bulan,
        kata_kunci=kata_kunci,
        db_error=db_error,
    )


@app.route("/tambah", methods=["GET", "POST"])
@app.route("/transaksi/tambah", methods=["GET", "POST"])
def tambah_transaksi():
    errors = []
    akun_list = []
    saran_kategori = list(KATEGORI_BAWAAN)

    try:
        with get_db() as conn:
            akun_list = ambil_daftar_akun(conn)
            saran_kategori = ambil_saran_kategori(conn)

            default_akun_id = str(akun_list[0]["id"]) if akun_list else ""
            data = {
                "tanggal": date.today().isoformat(),
                "jenis": "pengeluaran",
                "akun_id": default_akun_id,
                "kategori": "",
                "nominal": "",
                "catatan": "",
            }

            if request.method == "POST":
                data, errors = validasi_form_transaksi(request.form, conn)
                if not errors:
                    conn.execute(
                        """
                        INSERT INTO transaksi (tanggal, jenis, akun_id, kategori, nominal, catatan, dibuat_pada)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            data["tanggal"],
                            data["jenis"],
                            data["akun_id"],
                            data["kategori"],
                            data["nominal"],
                            data["catatan"],
                            datetime.now().isoformat(timespec="seconds"),
                        ),
                    )
                    flash("Transaksi berhasil ditambahkan.", "success")
                    return redirect(url_for("index"))
    except sqlite3.Error as exc:
        errors.append(f"Gagal memproses database: {exc}")
        data = {
            "tanggal": date.today().isoformat(),
            "jenis": "pengeluaran",
            "akun_id": "",
            "kategori": "",
            "nominal": "",
            "catatan": "",
        }

    return render_template(
        "form_transaksi.html",
        active_nav="transaksi",
        mode="tambah",
        data=data,
        akun_list=akun_list,
        saran_kategori=saran_kategori,
        errors=errors,
    )


@app.route("/edit/<int:transaksi_id>", methods=["GET", "POST"])
@app.route("/transaksi/edit/<int:transaksi_id>", methods=["GET", "POST"])
def edit_transaksi(transaksi_id):
    errors = []
    akun_list = []
    saran_kategori = list(KATEGORI_BAWAAN)

    try:
        with get_db() as conn:
            baris = conn.execute(
                """
                SELECT id, tanggal, jenis, akun_id, kategori, nominal, catatan
                FROM transaksi
                WHERE id = ?
                """,
                (transaksi_id,),
            ).fetchone()

            if baris is None:
                flash("Data transaksi tidak ditemukan.", "error")
                return redirect(url_for("index"))

            akun_list = ambil_daftar_akun(conn)
            saran_kategori = ambil_saran_kategori(conn)
            data = dict(baris)
            data["akun_id"] = "" if data["akun_id"] is None else str(data["akun_id"])

            if request.method == "POST":
                data, errors = validasi_form_transaksi(request.form, conn)
                data["id"] = transaksi_id
                if not errors:
                    conn.execute(
                        """
                        UPDATE transaksi
                        SET tanggal = ?, jenis = ?, akun_id = ?, kategori = ?, nominal = ?, catatan = ?
                        WHERE id = ?
                        """,
                        (
                            data["tanggal"],
                            data["jenis"],
                            data["akun_id"],
                            data["kategori"],
                            data["nominal"],
                            data["catatan"],
                            transaksi_id,
                        ),
                    )
                    flash("Perubahan transaksi berhasil disimpan.", "success")
                    return redirect(url_for("index"))
    except sqlite3.Error as exc:
        flash(f"Gagal memuat atau memperbarui transaksi: {exc}", "error")
        return redirect(url_for("index"))

    return render_template(
        "form_transaksi.html",
        active_nav="transaksi",
        mode="edit",
        data=data,
        akun_list=akun_list,
        saran_kategori=saran_kategori,
        errors=errors,
    )


# Penghapusan dibatasi pada metode POST agar tidak terpicu oleh tautan biasa atau prefetch browser.
@app.route("/hapus/<int:transaksi_id>", methods=["POST"])
@app.route("/transaksi/hapus/<int:transaksi_id>", methods=["POST"])
def hapus_transaksi(transaksi_id):
    try:
        with get_db() as conn:
            cursor = conn.execute("DELETE FROM transaksi WHERE id = ?", (transaksi_id,))
        if cursor.rowcount == 0:
            flash("Transaksi yang ingin dihapus tidak ditemukan.", "error")
        else:
            flash("Transaksi berhasil dihapus.", "success")
    except sqlite3.Error as exc:
        flash(f"Gagal menghapus transaksi: {exc}", "error")

    return redirect(url_for("index"))


# ==================== AKUN ====================


@app.route("/akun")
def daftar_akun():
    akun_list = []
    total_saldo = 0
    db_error = None

    try:
        with get_db() as conn:
            akun_list = ambil_daftar_akun(conn)
            total_saldo = sum(a["saldo_sekarang"] for a in akun_list)
    except sqlite3.Error as exc:
        db_error = f"Gagal membaca daftar akun: {exc}"

    return render_template(
        "akun.html",
        active_nav="akun",
        akun_list=akun_list,
        total_saldo=total_saldo,
        db_error=db_error,
    )


@app.route("/akun/tambah", methods=["GET", "POST"])
def tambah_akun():
    data = {"nama": "", "tipe": "E-Wallet", "saldo_awal": "0"}
    errors = []

    if request.method == "POST":
        data, errors = validasi_form_akun(request.form)
        if not errors:
            try:
                with get_db() as conn:
                    conn.execute(
                        """
                        INSERT INTO akun (nama, tipe, saldo_awal, dibuat_pada)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            data["nama"],
                            data["tipe"],
                            data["saldo_awal"],
                            datetime.now().isoformat(timespec="seconds"),
                        ),
                    )
                flash(f"Akun {data['nama']} berhasil ditambahkan.", "success")
                return redirect(url_for("daftar_akun"))
            except sqlite3.IntegrityError:
                errors.append("Nama akun sudah digunakan. Gunakan nama yang berbeda.")
            except sqlite3.Error as exc:
                errors.append(f"Gagal menyimpan akun ke database: {exc}")

    return render_template(
        "form_akun.html",
        active_nav="akun",
        mode="tambah",
        data=data,
        tipe_pilihan=TIPE_AKUN_VALID,
        errors=errors,
    )


@app.route("/akun/edit/<int:akun_id>", methods=["GET", "POST"])
def edit_akun(akun_id):
    errors = []

    try:
        with get_db() as conn:
            baris = conn.execute(
                "SELECT id, nama, tipe, saldo_awal FROM akun WHERE id = ?",
                (akun_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        flash(f"Gagal memuat data akun: {exc}", "error")
        return redirect(url_for("daftar_akun"))

    if baris is None:
        flash("Data akun tidak ditemukan.", "error")
        return redirect(url_for("daftar_akun"))

    data = dict(baris)

    if request.method == "POST":
        data, errors = validasi_form_akun(request.form)
        data["id"] = akun_id
        if not errors:
            try:
                with get_db() as conn:
                    conn.execute(
                        """
                        UPDATE akun
                        SET nama = ?, tipe = ?, saldo_awal = ?
                        WHERE id = ?
                        """,
                        (data["nama"], data["tipe"], data["saldo_awal"], akun_id),
                    )
                flash("Perubahan akun berhasil disimpan.", "success")
                return redirect(url_for("daftar_akun"))
            except sqlite3.IntegrityError:
                errors.append("Nama akun sudah digunakan oleh akun lain.")
            except sqlite3.Error as exc:
                errors.append(f"Gagal memperbarui akun: {exc}")

    return render_template(
        "form_akun.html",
        active_nav="akun",
        mode="edit",
        data=data,
        tipe_pilihan=TIPE_AKUN_VALID,
        errors=errors,
    )


@app.route("/akun/hapus/<int:akun_id>", methods=["POST"])
def hapus_akun(akun_id):
    try:
        with get_db() as conn:
            jumlah_terkait = conn.execute(
                "SELECT COUNT(*) AS jumlah FROM transaksi WHERE akun_id = ?",
                (akun_id,),
            ).fetchone()["jumlah"]

            if jumlah_terkait > 0:
                flash(
                    f"Akun tidak dapat dihapus karena masih digunakan pada {jumlah_terkait} transaksi. Pindahkan atau hapus transaksi terkait terlebih dahulu.",
                    "error",
                )
                return redirect(url_for("daftar_akun"))

            cursor = conn.execute("DELETE FROM akun WHERE id = ?", (akun_id,))
            if cursor.rowcount == 0:
                flash("Akun yang ingin dihapus tidak ditemukan.", "error")
            else:
                flash("Akun berhasil dihapus.", "success")
    except sqlite3.Error as exc:
        flash(f"Gagal menghapus akun: {exc}", "error")

    return redirect(url_for("daftar_akun"))


# ==================== ANGGARAN ====================


@app.route("/anggaran")
def daftar_anggaran():
    bulan = request.args.get("bulan", "").strip()
    if not validasi_bulan(bulan):
        bulan = bulan_sekarang()

    anggaran_list = []
    ringkasan = {"total_batas": 0, "total_terpakai": 0, "total_sisa": 0}
    db_error = None

    try:
        with get_db() as conn:
            baris_list = conn.execute(
                """
                SELECT
                    g.id,
                    g.bulan,
                    g.kategori,
                    g.batas_nominal,
                    g.catatan,
                    COALESCE(
                        (
                            SELECT SUM(t.nominal)
                            FROM transaksi t
                            WHERE t.jenis = 'pengeluaran'
                              AND substr(t.tanggal, 1, 7) = g.bulan
                              AND LOWER(TRIM(t.kategori)) = LOWER(TRIM(g.kategori))
                        ),
                        0
                    ) AS terpakai
                FROM anggaran g
                WHERE g.bulan = ?
                ORDER BY g.kategori COLLATE NOCASE ASC
                """,
                (bulan,),
            ).fetchall()

            for row in baris_list:
                batas = row["batas_nominal"]
                terpakai = row["terpakai"]
                sisa = batas - terpakai
                persen = round((terpakai / batas) * 100) if batas > 0 else 0
                persen_bar = min(persen, 100)

                if terpakai > batas:
                    status = "lebih"
                    label_status = "Melebihi Batas"
                elif persen >= 80:
                    status = "waspada"
                    label_status = "Mendekati Batas"
                else:
                    status = "aman"
                    label_status = "Aman"

                anggaran_list.append(
                    {
                        "id": row["id"],
                        "bulan": row["bulan"],
                        "kategori": row["kategori"],
                        "batas_nominal": batas,
                        "terpakai": terpakai,
                        "sisa": sisa,
                        "persen": persen,
                        "persen_bar": persen_bar,
                        "status": status,
                        "label_status": label_status,
                        "catatan": row["catatan"],
                    }
                )

            total_batas = sum(item["batas_nominal"] for item in anggaran_list)
            total_terpakai = sum(item["terpakai"] for item in anggaran_list)
            ringkasan = {
                "total_batas": total_batas,
                "total_terpakai": total_terpakai,
                "total_sisa": total_batas - total_terpakai,
            }
    except sqlite3.Error as exc:
        db_error = f"Gagal membaca data anggaran: {exc}"

    return render_template(
        "anggaran.html",
        active_nav="anggaran",
        bulan=bulan,
        anggaran_list=anggaran_list,
        ringkasan=ringkasan,
        db_error=db_error,
    )


@app.route("/anggaran/tambah", methods=["GET", "POST"])
def tambah_anggaran():
    bulan_awal = request.args.get("bulan", "").strip()
    if not validasi_bulan(bulan_awal):
        bulan_awal = bulan_sekarang()

    data = {
        "bulan": bulan_awal,
        "kategori": "",
        "batas_nominal": "",
        "catatan": "",
    }
    errors = []

    if request.method == "POST":
        data, errors = validasi_form_anggaran(request.form)
        if not errors:
            try:
                with get_db() as conn:
                    conn.execute(
                        """
                        INSERT INTO anggaran (bulan, kategori, batas_nominal, catatan, dibuat_pada)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            data["bulan"],
                            data["kategori"],
                            data["batas_nominal"],
                            data["catatan"],
                            datetime.now().isoformat(timespec="seconds"),
                        ),
                    )
                flash(
                    f"Anggaran {data['kategori']} untuk bulan {data['bulan']} berhasil ditambahkan.",
                    "success",
                )
                return redirect(url_for("daftar_anggaran", bulan=data["bulan"]))
            except sqlite3.IntegrityError:
                errors.append(
                    "Anggaran untuk kategori dan bulan tersebut sudah ada. Edit anggaran yang ada jika ingin mengubah batasnya."
                )
            except sqlite3.Error as exc:
                errors.append(f"Gagal menyimpan anggaran ke database: {exc}")

    return render_template(
        "form_anggaran.html",
        active_nav="anggaran",
        mode="tambah",
        data=data,
        saran_kategori=KATEGORI_BAWAAN,
        errors=errors,
    )


@app.route("/anggaran/edit/<int:anggaran_id>", methods=["GET", "POST"])
def edit_anggaran(anggaran_id):
    errors = []

    try:
        with get_db() as conn:
            baris = conn.execute(
                """
                SELECT id, bulan, kategori, batas_nominal, catatan
                FROM anggaran
                WHERE id = ?
                """,
                (anggaran_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        flash(f"Gagal memuat data anggaran: {exc}", "error")
        return redirect(url_for("daftar_anggaran"))

    if baris is None:
        flash("Data anggaran tidak ditemukan.", "error")
        return redirect(url_for("daftar_anggaran"))

    data = dict(baris)

    if request.method == "POST":
        data, errors = validasi_form_anggaran(request.form)
        data["id"] = anggaran_id
        if not errors:
            try:
                with get_db() as conn:
                    conn.execute(
                        """
                        UPDATE anggaran
                        SET bulan = ?, kategori = ?, batas_nominal = ?, catatan = ?
                        WHERE id = ?
                        """,
                        (
                            data["bulan"],
                            data["kategori"],
                            data["batas_nominal"],
                            data["catatan"],
                            anggaran_id,
                        ),
                    )
                flash("Perubahan anggaran berhasil disimpan.", "success")
                return redirect(url_for("daftar_anggaran", bulan=data["bulan"]))
            except sqlite3.IntegrityError:
                errors.append(
                    "Kategori tersebut sudah memiliki anggaran pada bulan yang sama."
                )
            except sqlite3.Error as exc:
                errors.append(f"Gagal memperbarui anggaran: {exc}")

    return render_template(
        "form_anggaran.html",
        active_nav="anggaran",
        mode="edit",
        data=data,
        saran_kategori=KATEGORI_BAWAAN,
        errors=errors,
    )


@app.route("/anggaran/hapus/<int:anggaran_id>", methods=["POST"])
def hapus_anggaran(anggaran_id):
    bulan_redirect = request.form.get("bulan", "").strip()
    try:
        with get_db() as conn:
            cursor = conn.execute("DELETE FROM anggaran WHERE id = ?", (anggaran_id,))
        if cursor.rowcount == 0:
            flash("Anggaran yang ingin dihapus tidak ditemukan.", "error")
        else:
            flash("Anggaran berhasil dihapus.", "success")
    except sqlite3.Error as exc:
        flash(f"Gagal menghapus anggaran: {exc}", "error")

    if validasi_bulan(bulan_redirect):
        return redirect(url_for("daftar_anggaran", bulan=bulan_redirect))
    return redirect(url_for("daftar_anggaran"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
