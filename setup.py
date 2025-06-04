#!/usr/bin/env python3
import sys
import os
import zipfile
import shutil
import json
import stat
import subprocess
import time

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QRectF
from PyQt5.QtGui import QFont, QPixmap, QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel,
    QPushButton, QHBoxLayout, QFrame, QGraphicsDropShadowEffect, QMessageBox,
    QSpacerItem, QSizePolicy, QTextEdit, QScrollArea
)

# --- Circular Progress Bar Widget ---
class CircularProgressBar(QWidget):
    """
    Widget progress bar melengkung kustom.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(150, 150)  # Ukuran progress bar
        self._value = 0
        self._min = 0
        self._max = 100
        self._bar_color = QColor(0, 150, 57)  # Hijau, sesuai dengan tema installer
        self._bg_color = QColor(220, 220, 220) # Abu-abu terang untuk latar belakang
        self._pen_width = 12  # Ketebalan garis progress bar
        self._text_color = QColor(50, 50, 50) # Warna teks di tengah
        self._icon_pixmap = QPixmap() # Atribut baru untuk menyimpan pixmap ikon
        self._show_percentage = False # Atribut baru untuk mengontrol tampilan ikon/persentase

    def setValue(self, value):
        if self._value != value:
            self._value = value
            self.update() # Memaksa widget untuk digambar ulang

    def setRange(self, min_val, max_val):
        self._min = min_val
        self._max = max_val
        self.update()

    def setIconPixmap(self, pixmap: QPixmap):
        """Mengatur QPixmap yang akan digambar di tengah progress bar."""
        self._icon_pixmap = pixmap
        self.update()

    def setShowPercentage(self, show: bool):
        """Mengatur apakah akan menampilkan teks persentase atau ikon di tengah."""
        if self._show_percentage != show:
            self._show_percentage = show
            self.update()

    def setBarColors(self, bg_color: QColor, bar_color: QColor):
        """Mengatur warna latar belakang dan warna progress bar."""
        self._bg_color = bg_color
        self._bar_color = bar_color
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing) # Untuk garis halus

        rect = self.rect()
        side = min(rect.width(), rect.height()) - self._pen_width * 2 # Padding dari tepi
        
        # Buat persegi untuk menggambar busur (di tengah)
        arc_rect = QRectF(
            self._pen_width,
            self._pen_width,
            side,
            side
        )

        # Gambar latar belakang progress bar (lingkaran penuh)
        painter.setPen(QPen(self._bg_color, self._pen_width, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(arc_rect, 90 * 16, 360 * 16) # Mulai dari atas (90 derajat), 360 derajat penuh

        # Hitung sudut untuk progress
        if self._max == self._min: # Hindari pembagian dengan nol
            span_angle = 0
        else:
            span_angle = (self._value - self._min) / (self._max - self._min) * 360
        
        # Gambar progress bar yang melengkung
        painter.setPen(QPen(self._bar_color, self._pen_width, Qt.SolidLine, Qt.RoundCap))
        # Sudut negatif untuk arah searah jarum jam (dari 90 derajat ke bawah)
        painter.drawArc(arc_rect, 90 * 16, -int(span_angle * 16))

        # Gambar ikon atau teks persentase berdasarkan _show_percentage
        if self._show_percentage: # Show percentage only when explicitly asked
            painter.setPen(QPen(self._text_color))
            font = QFont("Arial")
            font.setPointSize(int(side / 5)) # Ukuran font relatif terhadap ukuran widget
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(rect, Qt.AlignCenter, f"{self._value}%")
        elif not self._icon_pixmap.isNull(): # Otherwise, show icon if available
            icon_size = 90 # Ukuran ikon di tengah
            # Hitung posisi untuk menggambar ikon di tengah
            icon_x = (self.width() - icon_size) / 2
            icon_y = (self.height() - icon_size) / 2
            painter.drawPixmap(int(icon_x), int(icon_y), icon_size, icon_size, 
                               self._icon_pixmap.scaled(icon_size, icon_size, Qt.KeepAspectRatio, Qt.SmoothTransformation))

# --- Installer Thread ---
class InstallerThread(QThread):
    """
    Menangani ekstraksi file ZIP secara asinkron ke direktori target.
    Mengeluarkan sinyal untuk pembaruan progres, pesan, dan status penyelesaian.
    Dirancang khusus untuk sistem Linux/Unix-like.
    """
    progress_signal = pyqtSignal(int)
    message_signal = pyqtSignal(str) # Keep this signal for internal logging/debugging if needed, but not connected to GUI
    finished_signal = pyqtSignal(bool)

    def __init__(self, zip_path: str, app_name: str, executable_name: str = None, icon_path_in_zip: str = None, desktop_entry_metadata: dict = None, install_map: list = None):
        super().__init__()
        self.zip_path = zip_path
        self.app_name = app_name
        # executable_name dan icon_path_in_zip diharapkan sebagai path relatif dari $HOME
        self.executable_name = executable_name
        self.icon_path_in_zip = icon_path_in_zip
        self.desktop_entry_metadata = desktop_entry_metadata if desktop_entry_metadata is not None else {}
        self.install_map = install_map if install_map is not None else []
        self.home_dir = os.path.expanduser("~")

        # Direktori temporer untuk ekstraksi semua file ZIP
        # Unik per aplikasi untuk menghindari konflik jika beberapa installer berjalan
        self.temp_extract_dir = os.path.join(self.home_dir, f".{self.app_name.lower().replace(' ', '')}_installer_temp_extract")

        # Direktori temporer khusus untuk ikon yang ditampilkan di GUI (jika perlu)
        # Pastikan ini juga dihapus.
        self.temp_icon_display_dir = os.path.join(self.home_dir, f".{self.app_name.lower().replace(' ', '')}_installer_display_temp_icon")


    def run(self):
        """
        Menjalankan proses instalasi: mengekstrak file ke temp,
        menyalin ke lokasi target, membersihkan, dan membuat entri .desktop.
        """
        try:
            # Memvalidasi keberadaan dan format file ZIP
            if not os.path.exists(self.zip_path) or not zipfile.is_zipfile(self.zip_path):
                self.message_signal.emit("Error: ZIP file ain't valid or just gone, yo.")
                self.finished_signal.emit(False)
                return

            self.message_signal.emit(f"Gettin' a temp spot ready: {self.temp_extract_dir}")
            
            # 1. Bersihkan dan buat direktori ekstraksi sementara
            if os.path.exists(self.temp_extract_dir):
                shutil.rmtree(self.temp_extract_dir)
            os.makedirs(self.temp_extract_dir)

            # 2. Ekstrak SEMUA isi ZIP ke direktori sementara
            with zipfile.ZipFile(self.zip_path, 'r') as zip_ref:
                files = zip_ref.infolist()
                total_files = len(files)
                for i, file_info in enumerate(files):
                    zip_ref.extract(file_info, self.temp_extract_dir)
                    progress = int((i + 1) / total_files * 50) # 50% pertama untuk ekstraksi
                    self.progress_signal.emit(progress)
                    # self.message_signal.emit(f"Unpackin': {file_info.filename}") # Removed text updates for individual files

            # 3. Proses install_map: Salin/pindahkan file dari temp ke tujuan akhir
            current_progress = 50
            total_mappings = len(self.install_map)
            
            if total_mappings == 0:
                 # Fallback: Jika tidak ada install_map, instal semua ke ~/.app/AppName
                 default_app_dir = os.path.join(self.home_dir, ".app", self.app_name)
                 self.message_signal.emit(f"No install map found. Just dumpin' everything to: {default_app_dir}")
                 if os.path.exists(default_app_dir):
                     shutil.rmtree(default_app_dir)
                 shutil.copytree(self.temp_extract_dir, default_app_dir)
                 
                 # Sesuaikan executable_name dan icon_path_in_zip jika menggunakan fallback
                 # Ini penting agar desktop entry nanti menunjuk ke lokasi yang benar
                 if not self.executable_name or not self.executable_name.startswith('.'):
                     self.executable_name = os.path.join(".app", self.app_name, self.executable_name if self.executable_name else self.app_name.lower().replace(" ", ""))
                 if not self.icon_path_in_zip or not self.icon_path_in_zip.startswith('.'):
                     self.icon_path_in_zip = os.path.join(".app", self.app_name, self.icon_path_in_zip if self.icon_path_in_zip else "app_icon.png")


            else:
                for i, mapping in enumerate(self.install_map):
                    source_root_zip = mapping.get("source_root") # Nama folder di dalam ZIP (e.g., "DOTapp")
                    destination_root_template = mapping.get("destination_root") # Path tujuan (e.g., "$HOME/.app")
                    
                    if not source_root_zip or not destination_root_template:
                        self.message_signal.emit(f"Heads up: Map's incomplete ({mapping}). Skippin' it.")
                        continue

                    # Path sumber di dalam direktori temporer
                    full_source_path_in_temp = os.path.join(self.temp_extract_dir, source_root_zip)
                    # Path tujuan akhir (setelah $HOME diganti)
                    full_destination_path = destination_root_template.replace("$HOME", self.home_dir)
                    
                    if os.path.exists(full_source_path_in_temp):
                        # self.message_signal.emit(f"Copyin' '{source_root_zip}' to '{full_destination_path}'...") # Removed this line as requested
                        
                        # Hapus konten tujuan yang ada sebelum menyalin (untuk update/instalasi bersih)
                        if os.path.exists(full_destination_path):
                            self.message_signal.emit(f"Clearin' out old stuff at: {full_destination_path}")
                            try:
                                if os.path.isdir(full_destination_path):
                                    shutil.rmtree(full_destination_path)
                                else: # Jika itu file
                                    os.remove(full_destination_path)
                            except OSError as e:
                                self.message_signal.emit(f"Warning: Couldn't wipe {full_destination_path}: {e}")
                                # Lanjutkan, tapi catat kegagalan

                        # Buat direktori induk tujuan jika belum ada
                        os.makedirs(os.path.dirname(full_destination_path) if os.path.isfile(full_source_path_in_temp) else full_destination_path, exist_ok=True)
                        
                        try:
                            if os.path.isdir(full_source_path_in_temp):
                                shutil.copytree(full_source_path_in_temp, full_destination_path)
                            else: # Ini adalah file tunggal
                                shutil.copy2(full_source_path_in_temp, full_destination_path)
                            
                            # Atur izin executable untuk file yang relevan
                            if os.path.isdir(full_destination_path):
                                for root, dirs, files in os.walk(full_destination_path):
                                    for file in files:
                                        file_path = os.path.join(root, file)
                                        if os.path.islink(file_path): # Lewati symlink
                                            continue
                                        # Heuristik: skrip .sh, nama executable, atau file tanpa ekstensi
                                        if (file.endswith(".sh") or 
                                            file == os.path.basename(self.executable_name) or
                                            (not "." in file and os.path.isfile(file_path) and not stat.S_ISLNK(os.stat(file_path).st_mode))):
                                            st = os.stat(file_path)
                                            if not (st.st_mode & stat.S_IXUSR): # Jika izin eksekusi pengguna belum disetel
                                                os.chmod(file_path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                                                self.message_signal.emit(f"Givin' execute permission to: {file_path}")
                            elif os.path.isfile(full_destination_path): # Jika hanya satu file yang disalin
                                file_path = full_destination_path
                                if not os.path.islink(file_path):
                                    if (os.path.basename(file_path).endswith(".sh") or 
                                        os.path.basename(file_path) == os.path.basename(self.executable_name) or
                                        (not "." in os.path.basename(file_path) and os.path.isfile(file_path) and not stat.S_ISLNK(os.stat(file_path).st_mode))):
                                        st = os.stat(file_path)
                                        if not (st.st_mode & stat.S_IXUSR):
                                            os.chmod(file_path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                                            self.message_signal.emit(f"Givin' execute permission to: {file_path}")

                        except Exception as e:
                            self.message_signal.emit(f"Copy fail from '{source_root_zip}' to '{full_destination_path}': {str(e)}")
                            # Dalam installer yang lebih ketat, ini bisa memicu finished_signal(False) dan menghentikan instalasi.

                    progress = int(current_progress + (i + 1) / total_mappings * 50) # 50% sisanya untuk menyalin
                    self.progress_signal.emit(progress)

            # 4. Buat Entri Desktop
            self._create_desktop_entry()
            self._update_desktop_icon_cache()

            self.message_signal.emit("Boom! Installation Done!")
            self.finished_signal.emit(True)

        except Exception as e:
            self.message_signal.emit(f"Install totally blew up: {str(e)}")
            self.finished_signal.emit(False)
        finally:
            # Selalu membersihkan direktori temporer, baik berhasil maupun gagal
            self._cleanup_temp_files()

    def _create_desktop_entry(self):
        """
        Membuat file .desktop untuk lingkungan desktop Linux agar aplikasi dapat ditemukan.
        Executable dan Icon path diharapkan sebagai path relatif dari $HOME di metadata.json.
        """
        desktop_entry_dir = os.path.join(self.home_dir, ".local", "share", "applications")
        os.makedirs(desktop_entry_dir, exist_ok=True)

        desktop_file_path = os.path.join(desktop_entry_dir, f"{self.app_name.lower().replace(' ', '')}.desktop")
        
        # Resolusi executable_name dan icon_path_in_zip dari path relatif ke path absolut
        resolved_executable_path = os.path.join(self.home_dir, self.executable_name.lstrip('./'))
        resolved_icon_path = os.path.join(self.home_dir, self.icon_path_in_zip.lstrip('./'))

        # Fallback jika executable tidak ditemukan (penting agar desktop entry tidak rusak)
        if not os.path.exists(resolved_executable_path) or not os.path.isfile(resolved_executable_path):
            self.message_signal.emit(f"Heads up: Can't find '{self.executable_name}' at '{resolved_executable_path}'. Desktop entry might glitch on launch.")
            resolved_executable_path = "" # Biarkan kosong atau arahkan ke skrip wrapper yang bisa menampilkan error

        # Fallback jika ikon tidak ditemukan
        if not os.path.exists(resolved_icon_path) or not os.path.isfile(resolved_icon_path):
            self.message_signal.emit(f"Yo, the icon '{self.icon_path_in_zip}' ain't at '{resolved_icon_path}'. Desktop might just show a generic icon.")
            resolved_icon_path = "" # Biarkan kosong atau arahkan ke ikon sistem generik

        terminal_setting = str(self.desktop_entry_metadata.get("Terminal", False)).lower()
        entry_type = self.desktop_entry_metadata.get("Type", "Application")
        categories = self.desktop_entry_metadata.get("Categories", "Utility;")

        desktop_content = f"""[Desktop Entry]
Name={self.app_name}
Comment={self.app_name} Application
Exec={resolved_executable_path}
Icon={resolved_icon_path}
Terminal={terminal_setting}
Type={entry_type}
Categories={categories}
"""
        try:
            with open(desktop_file_path, 'w') as f:
                f.write(desktop_content)
            self.message_signal.emit(f"Whippin' up desktop entry: {desktop_file_path}")
            os.chmod(desktop_file_path, os.stat(desktop_file_path).st_mode | stat.S_IXUSR) # Memberikan izin executable
        except Exception as e:
            self.message_signal.emit(f"Major fail makin' the desktop entry: {str(e)}")

    def _update_desktop_icon_cache(self):
        """
        Mencoba memperbarui cache ikon desktop untuk memastikan entri .desktop baru dikenali.
        """
        self.message_signal.emit("Refreshin' that desktop icon cache...")
        try:
            # Mencoba update-desktop-database terlebih dahulu (lebih umum untuk GNOME/KDE modern)
            subprocess.run(["update-desktop-database", os.path.join(self.home_dir, ".local", "share", "applications")], check=True, capture_output=True)
            self.message_signal.emit("Desktop icon cache? Smooothly updated.")
        except FileNotFoundError:
            # Fallback untuk sistem yang mungkin tidak memiliki update-desktop-database langsung di PATH
            try:
                subprocess.run(["gtk-update-icon-cache", "-f", "-t", os.path.join(self.home_dir, ".local", "share", "icons")], check=True, capture_output=True)
                self.message_signal.emit("GTK icon cache updated (fallback).")
            except FileNotFoundError:
                self.message_signal.emit("Heads up: Can't find 'update-desktop-database' or 'gtk-update-icon-cache'. You might need to bounce out and back in, or manually refresh your desktop cache to see the icon.")
            except subprocess.CalledProcessError as e:
                self.message_signal.emit(f"Whoops, GTK icon cache update kinda busted: {e.stderr.decode().strip()}")
            except Exception as e:
                self.message_signal.emit(f"Random error when refreshin' icon cache: {str(e)}")
        except subprocess.CalledProcessError as e:
            self.message_signal.emit(f"Desktop database update went sideways: {e.stderr.decode().strip()}")
        except Exception as e:
            self.message_signal.emit(f"Unexpected blip refreshin' desktop cache: {str(e)}")

    def _cleanup_temp_files(self):
        """Menghapus direktori sementara yang digunakan untuk ekstraksi dan ikon display."""
        # Clean up main extraction directory
        if os.path.exists(self.temp_extract_dir):
            try:
                max_attempts = 5
                for attempt in range(max_attempts):
                    try:
                        shutil.rmtree(self.temp_extract_dir)
                        self.message_signal.emit(f"Temp directory cleaned up, easy peasy: {self.temp_extract_dir}")
                        break
                    except OSError as e:
                        if attempt < max_attempts - 1:
                            self.message_signal.emit(f"Heads up: Couldn't delete {self.temp_extract_dir} (Attempt {attempt+1}/{max_attempts}): {str(e)}. Retrying...")
                            time.sleep(0.1) # QThread.sleep() is fine, but time.sleep() is simpler here for general Python context
                        else:
                            self.message_signal.emit(f"Fatal error: Couldn't delete temp directory {self.temp_extract_dir} after {max_attempts} tries: {str(e)}")
            except Exception as e: # Tangkap exception lain yang mungkin terjadi
                self.message_signal.emit(f"Generic oopsie cleanin' temp directory {self.temp_extract_dir}: {str(e)}")
        
        # Clean up temporary icon display directory (if it exists and is separate)
        if os.path.exists(self.temp_icon_display_dir):
            try:
                shutil.rmtree(self.temp_icon_display_dir)
                self.message_signal.emit(f"Temp icon display directory wiped clean: {self.temp_icon_display_dir}")
            except Exception as e:
                self.message_signal.emit(f"Problem cleanin' temp icon display directory {self.temp_icon_display_dir}: {str(e)}")


# --- Main Installer Window ---
class CleanModernInstaller(QWidget):
    """
    GUI installer PyQt5 modern, tanpa bingkai, dengan desain bersih.
    Memvalidasi paket masukan dan mengambil metadata sebelum instalasi.
    Dirancang khusus untuk sistem Linux/Unix-like.
    """
    def __init__(self, zip_path: str):
        super().__init__()
        self.zip_path = zip_path
        # Metadata default jika tidak dapat dimuat dari file
        self.metadata = {
            "app_name": "FreedomApp",
            "version": "1.0.0",
            "description": "A powerful application for seamless system integration.",
            "icon_path": ".app/FreedomApp/app_icon.png", # Default relative path for icon
            "executable_name": ".app/FreedomApp/executable", # Default relative path for executable
            "install_map": [], # Default to empty map
            "desktop_entry": {
                "Terminal": False,
                "Type": "Application",
                "Categories": "Utility;"
            }
        }

        # Validasi dan muat metadata di awal
        if not self._load_package_metadata():
            QMessageBox.critical(self, "Installation Error",
                                 "Yo, that package file ain't right or 'metadata.json' went missing. Get us a real app ZIP, fam.")
            sys.exit(1)

        # Mengambil informasi aplikasi dari metadata
        self.app_name = self.metadata.get("app_name", "FreedomApp")
        # executable_name dan icon_path_for_desktop diharapkan sebagai path relatif terhadap $HOME
        self.executable_name = self.metadata.get("executable_name") 
        self.icon_path_for_desktop = self.metadata.get("icon_path") 
        self.desktop_entry_config = self.metadata.get("desktop_entry", {})
        self.install_map = self.metadata.get("install_map", []) # Get the install map

        # Set fallback untuk executable_name dan icon_path_for_desktop jika tidak disediakan dalam metadata
        # Path fallback ini juga harus relatif ke $HOME.
        if not self.executable_name:
            self.executable_name = os.path.join(".app", self.app_name, self.app_name.lower().replace(" ", ""))
            print(f"Peringatan: 'executable_name' tidak ditemukan dalam metadata. Menggunakan default: {self.executable_name}")
        if not self.icon_path_for_desktop:
            self.icon_path_for_desktop = os.path.join(".app", self.app_name, "app_icon.png")
            print(f"Peringatan: 'icon_path' tidak ditemukan dalam metadata. Menggunakan default: {self.icon_path_for_desktop}")


        # Mengatur properti jendela
        self.setWindowTitle(f"{self.app_name} Installer")
        self.setFixedSize(450, 700) # Ukuran jendela seperti smartphone (lebih tinggi, lebih sempit)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        # Inisialisasi komponen UI dan terapkan gaya
        self._init_ui()
        self._apply_main_styles()

        # Untuk fungsi jendela yang dapat diseret
        self._old_pos = None
        self.header.mousePressEvent = self._handle_mouse_press
        self.header.mouseMoveEvent = self._handle_mouse_move

        # Memperbarui label status dan deskripsi awal
        self.desc_text_edit.setText(self.metadata.get("description", "Gettin' things ready for a smooth ride.")) 
        
        # Inisialisasi: Tampilkan ikon, sembunyikan persentase, set warna putih
        self.progress_bar.setShowPercentage(False)
        self.progress_bar.setBarColors(QColor(255, 255, 255), QColor(255, 255, 255)) # White background, white bar initially

    def _load_package_metadata(self) -> bool:
        """
        Mencoba memuat metadata dari 'metadata.json' di dalam file ZIP.
        Mengembalikan True jika berhasil, False jika gagal (misal: bukan ZIP, tidak ada metadata.json).
        """
        if not os.path.exists(self.zip_path):
            return False

        if not zipfile.is_zipfile(self.zip_path):
            return False

        try:
            with zipfile.ZipFile(self.zip_path, 'r') as zip_ref:
                if 'metadata.json' not in zip_ref.namelist():
                    return False

                with zip_ref.open('metadata.json') as metadata_file:
                    self.metadata = json.load(metadata_file)
                
                # Validasi dasar untuk kunci-kunci penting
                if "app_name" not in self.metadata or "version" not in self.metadata:
                    return False

            return True
        except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as e:
            print(f"Error reading metadata from ZIP: {e}")
            return False
        except Exception as e:
            print(f"Unexpected error loading metadata: {e}")
            return False

    def _init_ui(self):
        """Menginisialisasi semua komponen UI dan tata letaknya."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20) # Margin untuk bayangan

        self.main_container = QFrame(self)
        self.main_container.setObjectName("mainContainer")
        container_layout = QVBoxLayout(self.main_container)
        container_layout.setContentsMargins(0, 0, 0, 0)

        self.header = self._create_header()
        container_layout.addWidget(self.header)

        content_layout = self._create_content_layout()
        container_layout.addLayout(content_layout)

        main_layout.addWidget(self.main_container)
        self.setLayout(main_layout)

    def _create_header(self) -> QWidget:
        """Membuat bilah header yang dapat diseret dengan judul dan tombol tutup."""
        header = QWidget()
        header.setFixedHeight(40)
        # Changed header background color to white
        header.setStyleSheet("background-color: #FFFFFF; border-top-left-radius: 15px; border-top-right-radius: 15px;") 
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(15, 0, 5, 0)
        header_layout.setAlignment(Qt.AlignRight)

        header_layout.addStretch() # This will now push the close button to the right

        close_button = QPushButton("✕")
        close_button.setFixedSize(36, 36) # Slightly larger touch target
        close_button.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: black; /* Changed to black for visibility on white header */
                font-size: 16px; /* Slightly larger icon */
                border: none;
                border-radius: 18px; /* Half of size for circular button */
            }
            QPushButton:hover {
                background-color: rgba(0, 0, 0, 0.1); /* Subtle black overlay on hover */
            }
            QPushButton:pressed {
                background-color: rgba(0, 0, 0, 0.2); /* More pronounced black overlay on press */
            }
        """)
        close_button.clicked.connect(self.close)
        header_layout.addWidget(close_button)
        return header

    def _create_content_layout(self) -> QVBoxLayout:
        """Membuat area konten utama dengan ikon, teks, progres, dan tombol (tata letak portrait), 
           disesuaikan dengan desain g2945.png."""
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(40, 30, 40, 30)
        content_layout.setSpacing(15)

        # 1. Title Label (Aplikasi setup) - moved to the top
        self.title_label = self._create_title_label()
        content_layout.addWidget(self.title_label, alignment=Qt.AlignCenter)

        content_layout.addSpacing(20) # Add spacing after title

        # 2. Circular Progress Bar (with icon inside)
        self.progress_bar = self._create_circular_progress_bar()
        content_layout.addWidget(self.progress_bar, alignment=Qt.AlignCenter)

        content_layout.addSpacing(20) # Add spacing after progress bar

        # 3. Install Button - moved to be below the progress bar
        self.install_button = self._create_install_button()
        content_layout.addWidget(self.install_button, alignment=Qt.AlignCenter)

        # 4. Description Text Edit (with scroll area)
        self.desc_text_edit = self._create_description_text_edit()
        
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(self.desc_text_edit)
        scroll_area.setFrameShape(QFrame.NoFrame) 
        
        # --- Adjusted Scrollbar Styling ---
        scroll_area.setStyleSheet("""
            QScrollArea {
                border: none;
                background-color: white; /* Set the scroll area background to white */
                border-radius: 10px; /* Optional: subtle rounded corners for the box */
                padding: 10px; /* Optional: Add some padding inside the scroll area */
            }
            QScrollBar:vertical {
                border: none;
                background: transparent; /* Make track transparent */
                width: 6px; /* Even slimmer scrollbar */
                margin: 0px 0px 0px 0px;
                border-radius: 3px; /* Match width for half-circle caps */
            }
            QScrollBar::handle:vertical {
                background: rgba(103, 58, 183, 150); /* Softer purple, slightly transparent */
                border: none;
                border-radius: 3px; /* Match width for half-circle caps */
                min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                border: none;
                background: none;
            }
            QScrollBar::up-arrow:vertical, QScrollBar::down-arrow:vertical {
                background: none;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: none;
            }
            /* Styling for the corner widget (where horizontal and vertical scrollbars meet) */
            QScrollBar::corner {
                background: transparent;
            }
        """)

        scroll_area.setMinimumHeight(120) 
        scroll_area.setMaximumHeight(180) # Add a maximum height to control its growth
        content_layout.addWidget(scroll_area)


        # 5. Status Label - placed below the description label
        self.status_label = self._create_status_label()
        content_layout.addWidget(self.status_label, alignment=Qt.AlignCenter)
        
        content_layout.addStretch(1) # Add some stretch at the bottom

        return content_layout

    def _get_app_icon_pixmap(self) -> QPixmap:
        """Mengekstrak dan mengembalikan QPixmap ikon aplikasi."""
        pixmap = QPixmap() # Default ke pixmap kosong
        
        # Path direktori temporer khusus untuk menampilkan ikon di GUI
        temp_icon_display_dir = os.path.join(os.path.expanduser("~"), f".{self.app_name.lower().replace(' ', '')}_installer_display_temp_icon")
        os.makedirs(temp_icon_display_dir, exist_ok=True)

        try:
            with zipfile.ZipFile(self.zip_path, 'r') as zip_ref:
                # Menghilangkan awalan "./" jika ada, karena nama di zip tidak punya itu
                zip_internal_icon_path = self.icon_path_for_desktop.lstrip('./')
                
                if zip_internal_icon_path in zip_ref.namelist():
                    temp_icon_path = zip_ref.extract(zip_internal_icon_path, temp_icon_display_dir)
                    pixmap = QPixmap(temp_icon_path)
                else:
                    # Coba cari di root ZIP sebagai fallback jika icon_path_for_zip bukan path lengkap
                    # Misalnya, jika icon_path_for_desktop hanya "app_icon.png"
                    if os.path.basename(zip_internal_icon_path) in zip_ref.namelist():
                        temp_icon_path = zip_ref.extract(os.path.basename(zip_internal_icon_path), temp_icon_display_dir)
                        pixmap = QPixmap(temp_icon_path)
        except Exception as e:
            print(f"Error extracting or loading icon from ZIP for GUI display: {e}")
        
        return pixmap

    def _create_title_label(self) -> QLabel:
        """Membuat dan menata label judul utama."""
        title_label = QLabel(f"{self.app_name} Setup v{self.metadata['version']}")
        title_label.setWordWrap(True)
        title_label.setFont(QFont("Arial", 16, QFont.Bold))
        title_label.setStyleSheet("color: #000000; background-color: transparent;")
        return title_label

    def _create_description_text_edit(self) -> QTextEdit:
        """Membuat dan menata QTextEdit untuk deskripsi dengan scrollbar."""
        text_edit = QTextEdit()
        text_edit.setReadOnly(True) 
        text_edit.setFrameStyle(QFrame.NoFrame) 
        text_edit.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        text_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff) 
        text_edit.setFont(QFont("Arial", 11))
        # Remove default padding from QTextEdit to control it via the layout/margins
        text_edit.setContentsMargins(0, 0, 0, 0) 
        text_edit.setStyleSheet("""
            QTextEdit {
                color: #000000; 
                background-color: white; /* Set the background color to white */
                border: none; /* Ensure no border */
            }
        """)
        return text_edit


    def _create_circular_progress_bar(self) -> CircularProgressBar:
        """Membuat dan menata progress bar melengkung, dengan ikon di dalamnya."""
        progress_bar = CircularProgressBar()
        icon_pixmap = self._get_app_icon_pixmap()
        if not icon_pixmap.isNull():
            progress_bar.setIconPixmap(icon_pixmap)
        return progress_bar

    def _create_status_label(self) -> QLabel:
        """Membuat dan menata label pesan status."""
        status_label = QLabel("") 
        status_label.setFont(QFont("Arial", 10))
        status_label.setStyleSheet("color: #000000; background-color: transparent;")
        status_label.setAlignment(Qt.AlignCenter) 
        return status_label

    def _create_install_button(self) -> QPushButton:
        """Membuat dan menata tombol instal."""
        install_button = QPushButton(f"GET {self.app_name.upper()} ROLLING") 
        install_button.setCursor(Qt.PointingHandCursor)
        install_button.setFont(QFont("Roboto", 14, QFont.Bold)) 
        install_button.setFixedHeight(56) 
        install_button.setContentsMargins(20, 10, 20, 10) 
        install_button.setStyleSheet("""
            QPushButton {
                background-color: #673AB7; /* A vibrant purple, example of "beautiful" */
                color: white; /* Ensure text is white */
                border: none;
                border-radius: 28px; /* Half of height for fully rounded corners */
                padding: 10px 24px; /* More padding */
                text-transform: uppercase; /* Not directly supported by stylesheet, handle in Python */
                box-shadow: 0px 3px 6px rgba(0, 0, 0, 0.16), 0px 3px 6px rgba(0, 0, 0, 0.23); /* Android-like shadow */
                transition: all 0.2s ease-in-out; /* Smooth transition for hover/press */
            }
            QPushButton:hover {
                background-color: #5E35B1; /* Darker purple on hover */
                color: white; /* Ensure text is white */
                box-shadow: 0px 5px 10px rgba(0, 0, 0, 0.2), 0px 5px 10px rgba(0, 0, 0, 0.25); /* More pronounced shadow on hover */
            }
            QPushButton:pressed {
                background-color: #4527A0; /* Even darker purple on press */
                color: white; /* Ensure text is white */
                box-shadow: 0px 1px 3px rgba(0, 0, 0, 0.12), 0px 1px 2px rgba(0, 0, 0, 0.24); /* Flatter shadow on press */
            }
            QPushButton:disabled {
                background-color: #BDBDBD; /* Grey for disabled */
                color: white; /* Keep text white even when disabled */
                box-shadow: none;
            }
            QPushButton[status="installed"] {
                background-color: #4CAF50; /* Green for installed status */
                color: white; /* Ensure text is white */
                box-shadow: 0px 3px 6px rgba(0, 0, 0, 0.16), 0px 3px 6px rgba(0, 0, 0, 0.23);
            }
        """)
        install_button.clicked.connect(self._start_installation)
        return install_button

    def _apply_main_styles(self):
        """Menerapkan gaya kontainer utama dan bayangan ke jendela."""
        self.main_container.setStyleSheet("""
            #mainContainer {
                background-color: #FFFFFF; /* Putih */
                border-radius: 20px;
                border: 1px solid #000000; /* Hitam */
            }
        """)
        shadow_effect = QGraphicsDropShadowEffect(self)
        shadow_effect.setBlurRadius(20)
        shadow_effect.setXOffset(0)
        shadow_effect.setYOffset(5)
        shadow_effect.setColor(QColor(0, 0, 0, 80)) # Hitam dengan opacity 80%
        self.main_container.setGraphicsEffect(shadow_effect)

    def _handle_mouse_press(self, event):
        """Menangani event tekan mouse untuk jendela yang dapat diseret."""
        if event.button() == Qt.LeftButton:
            self._old_pos = event.globalPos()

    def _handle_mouse_move(self, event):
        """Menangani event gerak mouse untuk jendela yang dapat diseret."""
        if event.buttons() == Qt.LeftButton and self._old_pos is not None:
            delta = event.globalPos() - self._old_pos
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self._old_pos = event.globalPos()

    def _start_installation(self):
        """Memulai proses instalasi."""
        self.install_button.setEnabled(False)
        self.status_label.setStyleSheet("color: #EE2A2A; background-color: transparent;") 
        
        # Selama instalasi: tampilkan persentase, set warna abu-abu dan hijau
        self.progress_bar.setShowPercentage(True) 
        self.progress_bar.setBarColors(QColor(220, 220, 220), QColor(0, 150, 57)) # Gray background, green bar
        self.progress_bar.setValue(0) 

        self.thread = InstallerThread(
            self.zip_path, 
            self.app_name, 
            self.executable_name, 
            self.icon_path_for_desktop, 
            self.desktop_entry_config,
            self.install_map 
        )
        self.thread.progress_signal.connect(self.progress_bar.setValue) 
        self.thread.finished_signal.connect(self._installation_finished)
        self.thread.start()

    def _installation_finished(self, success: bool):
        """Update UI based on installation success or failure."""
        if success:
            self.status_label.setText(f"{self.app_name} just got hooked up, fam!")
            self.status_label.setStyleSheet("color: #009639; background-color: transparent;") 
            self.install_button.setText("You're All Set!")
            self.install_button.setProperty("status", "installed")
            self.install_button.setStyleSheet(self.install_button.styleSheet()) 
            self.install_button.setEnabled(False) 
            self.progress_bar.setValue(100) 
            # Setelah selesai: tampilkan ikon, sembunyikan persentase, kembali ke warna putih
            self.progress_bar.setShowPercentage(False)
            self.progress_bar.setBarColors(QColor(255, 255, 255), QColor(255, 255, 255)) # White background, white bar
        else:
            self.status_label.setText(f"Install totally choked. Peep the errors.")
            self.status_label.setStyleSheet("color: #EE2A2A; background-color: transparent;") 
            self.install_button.setText("Try Again, fam")
            self.install_button.setEnabled(True) 
            self.progress_bar.setValue(0)
            # Saat gagal: tampilkan ikon, sembunyikan persentase, kembali ke warna putih
            self.progress_bar.setShowPercentage(False) 
            self.progress_bar.setBarColors(QColor(255, 255, 255), QColor(255, 255, 255)) # White background, white bar


# --- Main Execution ---
if __name__ == "__main__":
    if sys.platform == "darwin" or sys.platform.startswith("win"):
        QMessageBox.critical(None, "OS Not Supported, Dude", "This installer's just for Linux/Unix-like systems, sorry not sorry.")
        sys.exit(1)

    if len(sys.argv) < 2:
        print("Usage: python3 installer.py <path_to_your_app.zip>, yo")
        sys.exit(1)

    zip_file_path = sys.argv[1]
    
    app = QApplication(sys.argv)
    app.setStyle("Fusion") 

    installer_window = CleanModernInstaller(zip_file_path)
    installer_window.show()
    
    sys.exit(app.exec_())
