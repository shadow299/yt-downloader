import glob
import os
import platform
import queue
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from yt_dlp import YoutubeDL


# Inherit from KeyboardInterrupt so yt-dlp's `ignoreerrors=True` (which catches
# `Exception`) does not swallow our abort request during playlist iteration.
class StopDownloadRequested(KeyboardInterrupt):
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
        self.output_format = tk.StringVar(value="Best")

        self.status_text = tk.StringVar(value="Idle")

        # One progress row per video; created lazily as downloads emit progress.
        self.item_rows: dict[str, dict] = {}

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
        self.ffmpeg_warning_frame.columnconfigure(0, weight=1)
        self.ffmpeg_warning_label = ttk.Label(
            self.ffmpeg_warning_frame,
            text="",
            foreground="#a55b00",
            font=("Segoe UI", 10, "bold"),
            justify=tk.LEFT,
            wraplength=620,
        )
        self.ffmpeg_warning_label.grid(row=0, column=0, sticky=tk.W)
        self.ffmpeg_install_button = ttk.Button(
            self.ffmpeg_warning_frame,
            text="Install FFmpeg",
            command=self._install_ffmpeg,
        )
        self.ffmpeg_install_button.grid(row=0, column=1, sticky=tk.E, padx=(12, 0))

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
            values=["best", "1080p", "720p", "480p", "360p", "worst"],
        )
        self.quality_combo.grid(row=4, column=1, sticky=tk.EW, pady=(10, 0), padx=(8, 8))
        self.quality_combo.current(0)

        ttk.Label(input_frame, text="Output Format:").grid(row=5, column=0, sticky=tk.W, pady=(10, 0))
        self.format_combo = ttk.Combobox(
            input_frame,
            textvariable=self.output_format,
            state="readonly",
            values=["Best", "MP4", "WebM", "MKV", "Audio Only"],
        )
        self.format_combo.grid(row=5, column=1, sticky=tk.EW, pady=(10, 0), padx=(8, 8))
        self.format_combo.current(0)

        button_frame = ttk.Frame(root_frame)
        button_frame.pack(fill=tk.X, pady=(14, 0))

        self.download_button = ttk.Button(button_frame, text="Download", command=self._start_download)
        self.download_button.pack(side=tk.LEFT)

        self.stop_button = ttk.Button(button_frame, text="Stop", command=self._stop_download, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=(8, 0))

        progress_frame = ttk.LabelFrame(root_frame, text="Progress", padding=12)
        progress_frame.pack(fill=tk.BOTH, expand=True, pady=(14, 0))

        ttk.Label(
            progress_frame,
            textvariable=self.status_text,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor=tk.W, pady=(0, 6))

        canvas_holder = ttk.Frame(progress_frame)
        canvas_holder.pack(fill=tk.BOTH, expand=True)

        self.items_canvas = tk.Canvas(
            canvas_holder,
            borderwidth=0,
            highlightthickness=0,
            background=self.root.cget("background"),
        )
        self.items_scrollbar = ttk.Scrollbar(
            canvas_holder, orient="vertical", command=self.items_canvas.yview
        )
        self.items_canvas.configure(yscrollcommand=self.items_scrollbar.set)

        self.items_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.items_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.items_container = ttk.Frame(self.items_canvas)
        self.items_window_id = self.items_canvas.create_window(
            (0, 0), window=self.items_container, anchor="nw"
        )

        self.items_container.bind(
            "<Configure>",
            lambda _e: self.items_canvas.configure(
                scrollregion=self.items_canvas.bbox("all")
            ),
        )
        self.items_canvas.bind(
            "<Configure>",
            lambda e: self.items_canvas.itemconfigure(self.items_window_id, width=e.width),
        )
        self._bind_mousewheel(self.items_canvas)

        note = ttk.Label(
            progress_frame,
            text="Fail-safe checks: input validation, UI lock during download, error handling, and safe worker thread.",
            foreground="#444",
        )
        note.pack(anchor=tk.W, pady=(12, 0), side=tk.BOTTOM)

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
            action = self._prompt_ffmpeg_missing()
            if action == "install":
                self._install_ffmpeg(skip_confirm=True)
                return
            if action != "continue":
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

        self._clear_item_rows()

        self._set_ui_busy(True)
        self.stop_requested.clear()
        self.status_text.set("Starting download...")

        self.download_thread = threading.Thread(
            target=self._run_download,
            args=(url_or_list, destination, mode, self.quality.get(), self.output_format.get()),
            daemon=True,
        )
        self.download_thread.start()

    def _stop_download(self) -> None:
        # Fail-safe soft stop: mark a stop request and exit gracefully.
        if self.is_downloading:
            self.stop_requested.set()
            self.status_text.set("Stop requested. Finishing current item...")
            self.stop_button.configure(state=tk.DISABLED)

    def _run_download(
        self,
        source: str | list[str],
        destination: str,
        mode: str,
        quality: str,
        output_format: str,
    ) -> None:
        session_files: set[str] = set()

        def hook(data: dict) -> None:
            if self.stop_requested.is_set():
                raise StopDownloadRequested()

            info = data.get("info_dict") or {}
            item_id = info.get("id") or os.path.basename(data.get("filename", "")) or "item"
            title = info.get("title") or os.path.basename(data.get("filename", ""))

            fname = data.get("filename")
            if fname:
                session_files.add(fname)
            tmp = data.get("tmpfilename")
            if tmp:
                session_files.add(tmp)

            status = data.get("status", "")
            if status == "downloading":
                downloaded = data.get("downloaded_bytes", 0)
                total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                eta = data.get("eta")
                speed = data.get("speed")

                progress = (downloaded / total * 100.0) if total else 0.0
                payload = {
                    "item_id": str(item_id),
                    "title": title,
                    "progress": progress,
                    "downloaded": self._format_bytes(downloaded),
                    "total": self._format_bytes(total) if total else "?",
                    "eta": self._format_eta(eta),
                    "speed": self._format_speed(speed),
                }
                self.event_queue.put(("progress", payload))

            elif status == "finished":
                payload = {
                    "item_id": str(item_id),
                    "title": title,
                    "filename": os.path.basename(data.get("filename", "")),
                }
                self.event_queue.put(("item_finished", payload))

        def match_filter(info_dict, *, incomplete=False):
            # Stops new videos in a playlist from being picked up after Stop is pressed.
            if self.stop_requested.is_set():
                return "Aborted by user"
            return None

        ydl_opts = {
            "outtmpl": os.path.join(destination, "%(title)s.%(ext)s"),
            "noplaylist": mode == "single",
            "ignoreerrors": True,
            "progress_hooks": [hook],
            "match_filter": match_filter,
            "quiet": True,
            "no_warnings": True,
            "concurrent_fragment_downloads": 4,
            "retries": 10,
            "continuedl": True,
            "overwrites": False,
        }
        ydl_opts.update(self._build_ydl_format_options(quality, output_format))

        aborted = False
        try:
            with YoutubeDL(ydl_opts) as ydl:
                links = source if isinstance(source, list) else [source]
                for link in links:
                    if self.stop_requested.is_set():
                        aborted = True
                        break
                    try:
                        ydl.download([link])
                    except StopDownloadRequested:
                        aborted = True
                        break
                    if self.stop_requested.is_set():
                        aborted = True
                        break
        except StopDownloadRequested:
            aborted = True
        except Exception as exc:
            if self.stop_requested.is_set():
                aborted = True
            else:
                self._cleanup_partial_files(destination, session_files)
                self.event_queue.put(("error", {"message": str(exc)}))
                return

        if aborted or self.stop_requested.is_set():
            self._cleanup_partial_files(destination, session_files)
            self.event_queue.put(("stopped", {"message": "Download stopped by user."}))
        else:
            self.event_queue.put(("done", {"message": "Download task completed."}))

    @staticmethod
    def _cleanup_partial_files(destination: str, session_files: set[str]) -> None:
        # Remove .part / .ytdl leftovers for files we touched, plus a safety-net sweep
        # of the destination folder for stray fragments.
        candidates: set[str] = set()
        for base in session_files:
            candidates.add(base + ".part")
            candidates.add(base + ".ytdl")
            for match in glob.glob(base + ".f*.part"):
                candidates.add(match)
            for match in glob.glob(base + ".part-Frag*"):
                candidates.add(match)

        if os.path.isdir(destination):
            for pattern in ("*.part", "*.ytdl", "*.part-Frag*"):
                for match in glob.glob(os.path.join(destination, pattern)):
                    candidates.add(match)

        for path in candidates:
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass

    def _install_ffmpeg(self, skip_confirm: bool = False) -> None:
        if self.installing_ffmpeg:
            messagebox.showinfo("FFmpeg", "Installation is already in progress.")
            return
        if self._is_ffmpeg_installed():
            self._refresh_ffmpeg_warning()
            messagebox.showinfo("FFmpeg", "FFmpeg is already installed.")
            return

        if not skip_confirm:
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

    def _prompt_ffmpeg_missing(self) -> str:
        dialog = tk.Toplevel(self.root)
        dialog.title("FFmpeg Missing")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        result: dict[str, str | None] = {"value": None}

        body = ttk.Frame(dialog, padding=16)
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            body,
            text="ffmpeg is not installed. Click install to install it.\n\n"
            "You can continue, but some formats may fail or download at lower quality.",
            justify=tk.LEFT,
            wraplength=420,
        ).pack(anchor=tk.W)

        button_row = ttk.Frame(body)
        button_row.pack(fill=tk.X, pady=(16, 0))

        def choose(value: str) -> None:
            result["value"] = value
            dialog.destroy()

        ttk.Button(button_row, text="Install FFmpeg", command=lambda: choose("install")).pack(side=tk.LEFT)
        ttk.Button(button_row, text="Continue Anyway", command=lambda: choose("continue")).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(button_row, text="Cancel", command=lambda: choose("cancel")).pack(side=tk.LEFT, padx=(8, 0))

        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_reqwidth()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_reqheight()) // 2
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
        dialog.wait_window()

        return str(result["value"] or "cancel")

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
        if self.installing_ffmpeg:
            self.ffmpeg_install_button.configure(state=tk.DISABLED, text="Installing...")
        else:
            self.ffmpeg_install_button.configure(state=tk.NORMAL, text="Install FFmpeg")

        if not self.ffmpeg_warning_frame.winfo_ismapped():
            self.ffmpeg_warning_frame.pack(fill=tk.X, pady=(0, 10))

    def _process_events(self) -> None:
        while True:
            try:
                event, payload = self.event_queue.get_nowait()
            except queue.Empty:
                break

            if event == "progress":
                self._update_item_row(payload)

            elif event == "item_finished":
                self._mark_item_finished(payload)

            elif event == "done":
                self.status_text.set(payload.get("message", "Done"))
                self._set_ui_busy(False)
                messagebox.showinfo("Download", "Download finished.")

            elif event == "stopped":
                self.status_text.set(payload.get("message", "Stopped"))
                self._set_ui_busy(False)
                messagebox.showinfo("Download", "Download stopped.")

            elif event == "error":
                self.status_text.set("Error")
                self._set_ui_busy(False)
                messagebox.showerror("Download Error", payload.get("message", "Unknown error"))

            elif event == "ffmpeg_installed":
                self.installing_ffmpeg = False
                self.ffmpeg_install_button.configure(state=tk.NORMAL, text="Install FFmpeg")
                self._refresh_ffmpeg_warning()
                messagebox.showinfo("FFmpeg", "FFmpeg installed successfully. You can now download/merge best quality formats.")

            elif event == "ffmpeg_install_failed":
                self.installing_ffmpeg = False
                self.ffmpeg_install_button.configure(state=tk.NORMAL, text="Install FFmpeg")
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
            self.format_combo.configure(state=tk.DISABLED)
        else:
            self.dest_row[0].configure(state=tk.NORMAL)
            if self.dest_row[1] is not None:
                self.dest_row[1].configure(state=tk.NORMAL)
            self.quality_combo.configure(state="readonly")
            self.format_combo.configure(state="readonly")

    def _clear_item_rows(self) -> None:
        for row in self.item_rows.values():
            row["frame"].destroy()
        self.item_rows.clear()
        self.items_canvas.yview_moveto(0.0)

    def _get_or_create_item_row(self, item_id: str, title: str) -> dict:
        row = self.item_rows.get(item_id)
        if row is not None:
            return row

        container = ttk.Frame(self.items_container, padding=(4, 6))
        container.pack(fill=tk.X, expand=True)

        title_var = tk.StringVar(value=title or "Preparing...")
        status_var = tk.StringVar(value="Queued")
        progress_var = tk.DoubleVar(value=0.0)

        ttk.Label(container, textvariable=title_var, font=("Segoe UI", 10, "bold")).pack(
            anchor=tk.W
        )
        ttk.Progressbar(
            container,
            variable=progress_var,
            maximum=100,
            mode="determinate",
        ).pack(fill=tk.X, pady=(2, 2))
        ttk.Label(container, textvariable=status_var, foreground="#444").pack(anchor=tk.W)

        ttk.Separator(self.items_container, orient="horizontal").pack(fill=tk.X, pady=(0, 2))

        row = {
            "frame": container,
            "title_var": title_var,
            "status_var": status_var,
            "progress_var": progress_var,
            "finished": False,
        }
        self.item_rows[item_id] = row
        return row

    def _update_item_row(self, payload: dict) -> None:
        item_id = payload.get("item_id") or "item"
        title = payload.get("title") or payload.get("filename") or item_id
        row = self._get_or_create_item_row(item_id, title)
        if row["finished"]:
            return

        row["title_var"].set(title)
        progress = max(0.0, min(100.0, float(payload.get("progress", 0.0))))
        row["progress_var"].set(progress)

        downloaded = payload.get("downloaded", "0 MB")
        total = payload.get("total", "?")
        eta = payload.get("eta", "--:--")
        speed = payload.get("speed", "")
        status = f"{progress:5.1f}%  -  {downloaded} / {total}  -  ETA {eta}"
        if speed:
            status += f"  -  {speed}"
        row["status_var"].set(status)
        self.status_text.set(f"Downloading {len(self.item_rows)} item(s)...")

    def _mark_item_finished(self, payload: dict) -> None:
        item_id = payload.get("item_id") or "item"
        title = payload.get("title") or payload.get("filename") or item_id
        row = self._get_or_create_item_row(item_id, title)
        row["progress_var"].set(100.0)
        row["status_var"].set(f"Finished: {payload.get('filename', title)}")
        row["finished"] = True

    def _bind_mousewheel(self, canvas: tk.Canvas) -> None:
        def on_wheel(event: tk.Event) -> None:
            if getattr(event, "num", None) == 4:
                canvas.yview_scroll(-3, "units")
            elif getattr(event, "num", None) == 5:
                canvas.yview_scroll(3, "units")
            else:
                delta = getattr(event, "delta", 0)
                canvas.yview_scroll(int(-1 * (delta / 120)), "units")

        def bind_all(_e: tk.Event) -> None:
            canvas.bind_all("<MouseWheel>", on_wheel)
            canvas.bind_all("<Button-4>", on_wheel)
            canvas.bind_all("<Button-5>", on_wheel)

        def unbind_all(_e: tk.Event) -> None:
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        canvas.bind("<Enter>", bind_all)
        canvas.bind("<Leave>", unbind_all)

    @staticmethod
    def _is_valid_http(text: str) -> bool:
        return text.startswith("http://") or text.startswith("https://")

    @staticmethod
    def _build_ydl_format_options(quality: str, output_format: str) -> dict:
        # Audio-only: extract audio via yt-dlp's FFmpegExtractAudio postprocessor.
        if output_format == "Audio Only":
            return {
                "format": "bestaudio/best",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }

        height_filters = {
            "best": "",
            "1080p": "[height<=1080]",
            "720p": "[height<=720]",
            "480p": "[height<=480]",
            "360p": "[height<=360]",
        }
        hf = height_filters.get(quality, "")

        if quality == "worst":
            format_str = "worstvideo+worstaudio/worst"
        else:
            format_str = f"bestvideo{hf}+bestaudio/best{hf}" if hf else "bestvideo+bestaudio/best"

        options: dict = {"format": format_str}

        # Force final container via yt-dlp's FFmpegVideoConvertor postprocessor.
        if output_format in {"MP4", "WebM", "MKV"}:
            container = output_format.lower()
            options["merge_output_format"] = container
            options["postprocessors"] = [
                {
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": container,
                }
            ]
        return options

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
