import os
import platform
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from yt_dlp import YoutubeDL


class StopDownloadRequested(Exception):
    pass


class DownloaderApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("YouTube Downloader (yt-dlp)")
        self.root.geometry("860x560")
        self.root.minsize(820, 520)

        self.event_queue: queue.Queue[tuple[str, dict]] = queue.Queue()
        self.download_thread: threading.Thread | None = None
        self.is_downloading = False
        self.stop_requested = threading.Event()
        self.installing_ffmpeg = False

        self.mode_single = tk.BooleanVar(value=True)
        self.mode_playlist = tk.BooleanVar(value=False)
        self.mode_file = tk.BooleanVar(value=False)

        self.single_url = tk.StringVar()
        self.playlist_url = tk.StringVar()
        self.file_path = tk.StringVar()
        self.dest_path = tk.StringVar(value=os.path.expanduser("~"))
        self.quality = tk.StringVar(value="best")

        self.status_text = tk.StringVar(value="Idle")
        self.size_text = tk.StringVar(value="Downloaded: 0 MB")
        self.eta_text = tk.StringVar(value="ETA: --:--")
        self.progress_var = tk.DoubleVar(value=0.0)

        self._build_ui()
        self._apply_mode("single")
        self._refresh_ffmpeg_warning()
        self.root.after(120, self._process_events)

    def _build_ui(self) -> None:
        root_frame = ttk.Frame(self.root, padding=16)
        root_frame.pack(fill=tk.BOTH, expand=True)

        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")

        title = ttk.Label(
            root_frame,
            text="YouTube Downloader",
            font=("Segoe UI", 20, "bold"),
        )
        title.pack(anchor=tk.W)

        subtitle = ttk.Label(
            root_frame,
            text="Safe and clean UI for single videos, playlists, and link files using yt-dlp.",
            font=("Segoe UI", 10),
        )
        subtitle.pack(anchor=tk.W, pady=(2, 14))

        self.ffmpeg_warning_frame = ttk.Frame(root_frame, padding=(10, 8))
        self.ffmpeg_warning_label = ttk.Label(
            self.ffmpeg_warning_frame,
            text="",
            foreground="#a55b00",
            font=("Segoe UI", 10, "bold"),
        )
        self.ffmpeg_warning_label.pack(side=tk.LEFT)
        self.ffmpeg_install_button = ttk.Button(
            self.ffmpeg_warning_frame,
            text="Install",
            command=self._install_ffmpeg,
        )
        self.ffmpeg_install_button.pack(side=tk.RIGHT)

        mode_frame = ttk.LabelFrame(root_frame, text="Mode", padding=10)
        mode_frame.pack(fill=tk.X)

        ttk.Checkbutton(
            mode_frame,
            text="Single Video",
            variable=self.mode_single,
            command=lambda: self._on_mode_toggle("single"),
        ).grid(row=0, column=0, padx=(0, 18), sticky=tk.W)

        ttk.Checkbutton(
            mode_frame,
            text="Playlist",
            variable=self.mode_playlist,
            command=lambda: self._on_mode_toggle("playlist"),
        ).grid(row=0, column=1, padx=(0, 18), sticky=tk.W)

        ttk.Checkbutton(
            mode_frame,
            text="Videos From File",
            variable=self.mode_file,
            command=lambda: self._on_mode_toggle("file"),
        ).grid(row=0, column=2, sticky=tk.W)

        input_frame = ttk.LabelFrame(root_frame, text="Inputs", padding=12)
        input_frame.pack(fill=tk.X, pady=(12, 0))
        input_frame.columnconfigure(1, weight=1)

        self.single_url_row = self._build_entry_row(
            input_frame,
            row=0,
            label="Video URL:",
            variable=self.single_url,
        )

        self.playlist_url_row = self._build_entry_row(
            input_frame,
            row=1,
            label="Playlist URL:",
            variable=self.playlist_url,
        )

        self.file_row = self._build_entry_row(
            input_frame,
            row=2,
            label="Links TXT File:",
            variable=self.file_path,
            button_text="Browse",
            command=self._browse_file,
        )

        self.dest_row = self._build_entry_row(
            input_frame,
            row=3,
            label="Destination Folder:",
            variable=self.dest_path,
            button_text="Browse",
            command=self._browse_dest,
        )

        ttk.Label(input_frame, text="Quality:").grid(row=4, column=0, sticky=tk.W, pady=(10, 0))
        self.quality_combo = ttk.Combobox(
            input_frame,
            textvariable=self.quality,
            state="readonly",
            values=["best", "1080p", "720p", "480p", "360p", "worst", "audio only"],
        )
        self.quality_combo.grid(row=4, column=1, sticky=tk.EW, pady=(10, 0), padx=(8, 8))
        self.quality_combo.current(0)

        button_frame = ttk.Frame(root_frame)
        button_frame.pack(fill=tk.X, pady=(14, 0))

        self.download_button = ttk.Button(button_frame, text="Download", command=self._start_download)
        self.download_button.pack(side=tk.LEFT)

        self.stop_button = ttk.Button(button_frame, text="Stop", command=self._stop_download, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=(8, 0))

        progress_frame = ttk.LabelFrame(root_frame, text="Progress", padding=12)
        progress_frame.pack(fill=tk.BOTH, expand=True, pady=(14, 0))

        self.progress_bar = ttk.Progressbar(
            progress_frame,
            variable=self.progress_var,
            maximum=100,
            mode="determinate",
        )
        self.progress_bar.pack(fill=tk.X)

        ttk.Label(progress_frame, textvariable=self.status_text, font=("Segoe UI", 10, "bold")).pack(
            anchor=tk.W, pady=(10, 2)
        )
        ttk.Label(progress_frame, textvariable=self.size_text).pack(anchor=tk.W)
        ttk.Label(progress_frame, textvariable=self.eta_text).pack(anchor=tk.W)

        note = ttk.Label(
            progress_frame,
            text="Fail-safe checks: input validation, UI lock during download, error handling, and safe worker thread.",
            foreground="#444",
        )
        note.pack(anchor=tk.W, pady=(12, 0))

    def _build_entry_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        variable: tk.StringVar,
        button_text: str | None = None,
        command=None,
    ) -> tuple[ttk.Entry, ttk.Button | None]:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, pady=(8, 0))
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky=tk.EW, padx=(8, 8), pady=(8, 0))
        btn = None
        if button_text and command:
            btn = ttk.Button(parent, text=button_text, command=command)
            btn.grid(row=row, column=2, sticky=tk.W, pady=(8, 0))
        return (entry, btn)

    def _on_mode_toggle(self, clicked_mode: str) -> None:
        # Keep checkbox UX while enforcing one active mode for safety and clarity.
        if clicked_mode == "single":
            new_value = self.mode_single.get()
            self.mode_single.set(new_value)
            self.mode_playlist.set(False)
            self.mode_file.set(False)
            if not new_value:
                self.mode_single.set(True)
            self._apply_mode("single")

        elif clicked_mode == "playlist":
            new_value = self.mode_playlist.get()
            self.mode_playlist.set(new_value)
            self.mode_single.set(False)
            self.mode_file.set(False)
            if not new_value:
                self.mode_playlist.set(True)
            self._apply_mode("playlist")

        elif clicked_mode == "file":
            new_value = self.mode_file.get()
            self.mode_file.set(new_value)
            self.mode_single.set(False)
            self.mode_playlist.set(False)
            if not new_value:
                self.mode_file.set(True)
            self._apply_mode("file")

    def _apply_mode(self, mode: str) -> None:
        single_state = tk.NORMAL if mode == "single" else tk.DISABLED
        playlist_state = tk.NORMAL if mode == "playlist" else tk.DISABLED
        file_state = tk.NORMAL if mode == "file" else tk.DISABLED

        self.single_url_row[0].configure(state=single_state)
        self.playlist_url_row[0].configure(state=playlist_state)
        self.file_row[0].configure(state=file_state)
        if self.file_row[1] is not None:
            self.file_row[1].configure(state=file_state)

    def _browse_file(self) -> None:
        file_selected = filedialog.askopenfilename(
            title="Select TXT file with video links",
            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")],
        )
        if file_selected:
            self.file_path.set(file_selected)

    def _browse_dest(self) -> None:
        folder = filedialog.askdirectory(title="Select destination folder")
        if folder:
            self.dest_path.set(folder)

    def _active_mode(self) -> str:
        if self.mode_playlist.get():
            return "playlist"
        if self.mode_file.get():
            return "file"
        return "single"

    def _start_download(self) -> None:
        if self.is_downloading:
            messagebox.showwarning("Busy", "A download is already running.")
            return

        if not self._is_ffmpeg_installed():
            proceed = messagebox.askyesno(
                "FFmpeg Missing",
                "ffmpeg is not installed. click install to install it.\n\n"
                "You can continue, but some formats may fail or download at lower quality. Continue?",
            )
            if not proceed:
                return

        mode = self._active_mode()
        destination = self.dest_path.get().strip()

        if not destination:
            messagebox.showerror("Input Error", "Please select a destination folder.")
            return

        if not os.path.isdir(destination):
            try:
                os.makedirs(destination, exist_ok=True)
            except OSError as exc:
                messagebox.showerror("Path Error", f"Could not create destination folder:\n{exc}")
                return

        url_or_list: str | list[str]
        if mode == "single":
            video_url = self.single_url.get().strip()
            if not self._is_valid_http(video_url):
                messagebox.showerror("Input Error", "Please enter a valid video URL.")
                return
            url_or_list = video_url

        elif mode == "playlist":
            playlist_url = self.playlist_url.get().strip()
            if not self._is_valid_http(playlist_url):
                messagebox.showerror("Input Error", "Please enter a valid playlist URL.")
                return
            url_or_list = playlist_url

        else:
            txt_path = self.file_path.get().strip()
            if not txt_path:
                messagebox.showerror("Input Error", "Please select a TXT file with links.")
                return
            if not os.path.isfile(txt_path):
                messagebox.showerror("Input Error", "The selected TXT file does not exist.")
                return
            try:
                with open(txt_path, "r", encoding="utf-8") as f:
                    lines = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
            except OSError as exc:
                messagebox.showerror("File Error", f"Could not read TXT file:\n{exc}")
                return

            valid_links = [link for link in lines if self._is_valid_http(link)]
            if not valid_links:
                messagebox.showerror("Input Error", "No valid URLs found in TXT file.")
                return
            url_or_list = valid_links

        self._set_ui_busy(True)
        self.stop_requested.clear()
        self.progress_var.set(0)
        self.status_text.set("Starting download...")
        self.size_text.set("Downloaded: 0 MB")
        self.eta_text.set("ETA: --:--")

        self.download_thread = threading.Thread(
            target=self._run_download,
            args=(url_or_list, destination, mode, self.quality.get()),
            daemon=True,
        )
        self.download_thread.start()

    def _stop_download(self) -> None:
        # Fail-safe soft stop: mark a stop request and exit gracefully.
        if self.is_downloading:
            self.stop_requested.set()
            self.status_text.set("Stop requested. Finishing current item...")
            self.eta_text.set("ETA: --:--")
            self.stop_button.configure(state=tk.DISABLED)

    def _run_download(self, source: str | list[str], destination: str, mode: str, quality: str) -> None:
        def hook(data: dict) -> None:
            if self.stop_requested.is_set():
                raise StopDownloadRequested()

            status = data.get("status", "")
            if status == "downloading":
                downloaded = data.get("downloaded_bytes", 0)
                total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                eta = data.get("eta")
                speed = data.get("speed")
                filename = os.path.basename(data.get("filename", ""))

                progress = (downloaded / total * 100.0) if total else 0.0
                payload = {
                    "progress": progress,
                    "downloaded": self._format_bytes(downloaded),
                    "eta": self._format_eta(eta),
                    "speed": self._format_speed(speed),
                    "filename": filename,
                }
                self.event_queue.put(("progress", payload))

            elif status == "finished":
                payload = {"filename": os.path.basename(data.get("filename", ""))}
                self.event_queue.put(("item_finished", payload))

        ydl_opts = {
            "outtmpl": os.path.join(destination, "%(title)s.%(ext)s"),
            "noplaylist": mode == "single",
            "ignoreerrors": True,
            "progress_hooks": [hook],
            "quiet": True,
            "no_warnings": True,
            "format": self._quality_to_format(quality),
            "concurrent_fragment_downloads": 4,
            "retries": 10,
            "continuedl": True,
            "overwrites": False,
        }

        try:
            with YoutubeDL(ydl_opts) as ydl:
                if isinstance(source, list):
                    for link in source:
                        if self.stop_requested.is_set():
                            break
                        ydl.download([link])
                        if self.stop_requested.is_set():
                            break
                else:
                    ydl.download([source])
            if self.stop_requested.is_set():
                self.event_queue.put(("stopped", {"message": "Download stopped by user."}))
            else:
                self.event_queue.put(("done", {"message": "Download task completed."}))
        except Exception as exc:
            if isinstance(exc, StopDownloadRequested):
                self.event_queue.put(("stopped", {"message": "Download stopped by user."}))
            else:
                self.event_queue.put(("error", {"message": str(exc)}))

    def _install_ffmpeg(self) -> None:
        if self.installing_ffmpeg:
            messagebox.showinfo("FFmpeg", "Installation is already in progress.")
            return
        if self._is_ffmpeg_installed():
            self._refresh_ffmpeg_warning()
            messagebox.showinfo("FFmpeg", "FFmpeg is already installed.")
            return

        confirm = messagebox.askyesno(
            "Install FFmpeg",
            "ffmpeg is not installed. click install to install it.\n\nProceed with automatic installation?",
        )
        if not confirm:
            return

        self.installing_ffmpeg = True
        self.ffmpeg_install_button.configure(state=tk.DISABLED, text="Installing...")
        thread = threading.Thread(target=self._run_ffmpeg_install, daemon=True)
        thread.start()

    def _run_ffmpeg_install(self) -> None:
        system_name = platform.system().lower()
        commands = self._ffmpeg_install_commands(system_name)

        attempted: list[str] = []
        for cmd in commands:
            attempted.append(cmd)
            try:
                result = subprocess.run(
                    cmd,
                    check=False,
                    capture_output=True,
                    text=True,
                    shell=True,
                )
            except OSError:
                continue

            if result.returncode == 0:
                if self._is_ffmpeg_installed():
                    self.event_queue.put(("ffmpeg_installed", {}))
                    return

        self.event_queue.put(
            (
                "ffmpeg_install_failed",
                {
                    "system": system_name,
                    "attempted": attempted,
                },
            )
        )

    @staticmethod
    def _ffmpeg_install_commands(system_name: str) -> list[str]:
        if system_name == "windows":
            return [
                "winget install -e --id Gyan.FFmpeg",
                "choco install ffmpeg -y",
                "scoop install ffmpeg",
            ]
        if system_name == "darwin":
            return ["brew install ffmpeg"]

        # Linux fallbacks for common distros/package managers.
        return [
            "sudo apt update && sudo apt install -y ffmpeg",
            "sudo dnf install -y ffmpeg",
            "sudo yum install -y ffmpeg",
            "sudo pacman -S --noconfirm ffmpeg",
            "sudo zypper install -y ffmpeg",
            "sudo apk add ffmpeg",
        ]

    @staticmethod
    def _is_ffmpeg_installed() -> bool:
        return shutil.which("ffmpeg") is not None

    def _refresh_ffmpeg_warning(self) -> None:
        if self._is_ffmpeg_installed():
            self.ffmpeg_warning_frame.pack_forget()
            return

        self.ffmpeg_warning_label.configure(
            text="ffmpeg is not installed. click install to install it."
        )
        self.ffmpeg_warning_frame.pack(fill=tk.X, pady=(0, 10))

    def _process_events(self) -> None:
        while True:
            try:
                event, payload = self.event_queue.get_nowait()
            except queue.Empty:
                break

            if event == "progress":
                progress = float(payload.get("progress", 0.0))
                self.progress_var.set(max(0.0, min(100.0, progress)))
                filename = payload.get("filename", "")
                speed = payload.get("speed", "")
                label = f"Downloading: {filename}" if filename else "Downloading..."
                if speed:
                    label += f" ({speed})"
                self.status_text.set(label)
                self.size_text.set(f"Downloaded: {payload.get('downloaded', '0 MB')}")
                self.eta_text.set(f"ETA: {payload.get('eta', '--:--')}")

            elif event == "item_finished":
                filename = payload.get("filename")
                self.status_text.set(f"Finished: {filename}" if filename else "Item finished")
                self.eta_text.set("ETA: --:--")

            elif event == "done":
                self.progress_var.set(100.0)
                self.status_text.set(payload.get("message", "Done"))
                self.eta_text.set("ETA: 00:00")
                self._set_ui_busy(False)
                messagebox.showinfo("Download", "Download finished.")

            elif event == "stopped":
                self.status_text.set(payload.get("message", "Stopped"))
                self.eta_text.set("ETA: --:--")
                self._set_ui_busy(False)
                messagebox.showinfo("Download", "Download stopped.")

            elif event == "error":
                self.status_text.set("Error")
                self._set_ui_busy(False)
                messagebox.showerror("Download Error", payload.get("message", "Unknown error"))

            elif event == "ffmpeg_installed":
                self.installing_ffmpeg = False
                self.ffmpeg_install_button.configure(state=tk.NORMAL, text="Install")
                self._refresh_ffmpeg_warning()
                messagebox.showinfo("FFmpeg", "FFmpeg installed successfully. You can now download/merge best quality formats.")

            elif event == "ffmpeg_install_failed":
                self.installing_ffmpeg = False
                self.ffmpeg_install_button.configure(state=tk.NORMAL, text="Install")
                self._refresh_ffmpeg_warning()
                system_name = payload.get("system", "unknown")
                attempted = payload.get("attempted", [])
                attempted_text = "\n".join(attempted) if attempted else "No command could be started."
                messagebox.showwarning(
                    "FFmpeg Install Failed",
                    "Automatic FFmpeg installation failed.\n"
                    f"OS detected: {system_name}\n\n"
                    "Attempted commands:\n"
                    f"{attempted_text}\n\n"
                    "Please install FFmpeg manually and restart this app.",
                )

        self.root.after(120, self._process_events)

    def _set_ui_busy(self, busy: bool) -> None:
        self.is_downloading = busy
        self.download_button.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.stop_button.configure(state=tk.NORMAL if busy else tk.DISABLED)

        mode = self._active_mode()
        self._apply_mode(mode)

        if busy:
            self.single_url_row[0].configure(state=tk.DISABLED)
            self.playlist_url_row[0].configure(state=tk.DISABLED)
            self.file_row[0].configure(state=tk.DISABLED)
            if self.file_row[1] is not None:
                self.file_row[1].configure(state=tk.DISABLED)
            self.dest_row[0].configure(state=tk.DISABLED)
            if self.dest_row[1] is not None:
                self.dest_row[1].configure(state=tk.DISABLED)
            self.quality_combo.configure(state=tk.DISABLED)
        else:
            self.dest_row[0].configure(state=tk.NORMAL)
            if self.dest_row[1] is not None:
                self.dest_row[1].configure(state=tk.NORMAL)
            self.quality_combo.configure(state="readonly")

    @staticmethod
    def _is_valid_http(text: str) -> bool:
        return text.startswith("http://") or text.startswith("https://")

    @staticmethod
    def _quality_to_format(quality: str) -> str:
        mapping = {
            "best": "bestvideo+bestaudio/best",
            "1080p": "bestvideo[height<=1080]+bestaudio/best[height<=1080]",
            "720p": "bestvideo[height<=720]+bestaudio/best[height<=720]",
            "480p": "bestvideo[height<=480]+bestaudio/best[height<=480]",
            "360p": "bestvideo[height<=360]+bestaudio/best[height<=360]",
            "worst": "worst",
            "audio only": "bestaudio/best",
        }
        return mapping.get(quality, "bestvideo+bestaudio/best")

    @staticmethod
    def _format_bytes(num_bytes: int | float) -> str:
        size = float(num_bytes)
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024.0 or unit == "TB":
                if unit == "B":
                    return f"{int(size)} {unit}"
                return f"{size:.2f} {unit}"
            size /= 1024.0
        return "0 B"

    @staticmethod
    def _format_eta(eta_seconds: int | float | None) -> str:
        if eta_seconds is None:
            return "--:--"
        seconds = max(0, int(eta_seconds))
        minutes, sec = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{sec:02d}"
        return f"{minutes:02d}:{sec:02d}"

    @staticmethod
    def _format_speed(speed: int | float | None) -> str:
        if speed is None:
            return ""
        return f"{DownloaderApp._format_bytes(speed)}/s"


if __name__ == "__main__":
    root = tk.Tk()
    app = DownloaderApp(root)
    root.mainloop()
