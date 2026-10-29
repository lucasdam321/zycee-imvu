"""Interface desktop para procurar malhas locais e exportar arquivos OBJ."""

from __future__ import annotations

import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Callable

from .cache import associate, export_product, scan_cache
from .converter import convert_file


class Application:
    """Executa a interface; leitura do cache e conversão ficam fora do loop Tk."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("IMVU → OBJ — arquivos locais")
        root.geometry("920x720")
        root.minsize(700, 570)
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.busy = False
        self.buttons: list[ttk.Button] = []

        appdata = Path(os.environ.get("APPDATA", str(Path.home() / "AppData/Roaming")))
        self.cache = tk.StringVar(value=str(appdata / "IMVU"))
        self.pid = tk.StringVar(value="74667074")
        self.source = tk.StringVar()
        self.output = tk.StringVar(value=str(Path.home() / "IMVU-OBJ"))
        self.scale = tk.StringVar(value="1")
        self.center = tk.BooleanVar(value=True)

        frame = ttk.Frame(root, padding=16)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(12, weight=1)

        ttk.Label(frame, text="Extrair malhas locais para o Blender", font=("Segoe UI", 15, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )
        ttk.Label(
            frame,
            text="Selecione o cache do IMVU Classic ou um arquivo de malha. O cache original é apenas lido.",
            wraplength=820,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 12))

        self._path_row(frame, 2, "Pasta de cache:", self.cache, self._pick_cache)
        ttk.Label(frame, text="PID do produto:").grid(row=3, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=self.pid, width=24).grid(row=3, column=1, sticky="w", pady=6)
        self._path_row(frame, 4, "Arquivo direto:", self.source, self._pick_source)
        self._path_row(frame, 5, "Pasta de saída:", self.output, self._pick_output)

        options = ttk.Frame(frame)
        options.grid(row=6, column=0, columnspan=3, sticky="w", pady=8)
        ttk.Checkbutton(options, text="Centralizar geometria na origem", variable=self.center).pack(side="left")
        ttk.Label(options, text="Escala:").pack(side="left", padx=(25, 6))
        ttk.Entry(options, textvariable=self.scale, width=10).pack(side="left")

        actions = ttk.Frame(frame)
        actions.grid(row=7, column=0, columnspan=3, sticky="w", pady=(8, 6))
        for column, (text, command) in enumerate((
            ("Buscar PID no cache", self._scan),
            ("Exportar produto por PID", self._export),
            ("Converter arquivo direto", self._convert),
        )):
            button = ttk.Button(actions, text=text, command=command)
            button.grid(row=0, column=column, sticky="w", padx=(0, 8))
            self.buttons.append(button)
        associate_button = ttk.Button(actions, text="Associar arquivo ao PID", command=self._associate)
        associate_button.grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.buttons.append(associate_button)

        ttk.Label(
            frame,
            text=(
                "A busca exige uma associação verificável entre arquivo e PID. Na conversão direta, "
                "o PID informado apenas rotula a saída e não confirma a origem da malha."
            ),
            wraplength=820,
        ).grid(row=8, column=0, columnspan=3, sticky="w", pady=(6, 5))
        ttk.Label(
            frame,
            text="Saída: geometria estática, normais e UVs. Texturas, esqueleto e animações não são recuperados automaticamente.",
            wraplength=820,
        ).grid(row=9, column=0, columnspan=3, sticky="w", pady=(0, 8))

        self.status = tk.StringVar(value="Pronto para ler os arquivos locais.")
        ttk.Label(frame, textvariable=self.status).grid(row=10, column=0, columnspan=3, sticky="w")
        self.progress = ttk.Progressbar(frame, mode="indeterminate")
        self.progress.grid(row=11, column=0, columnspan=3, sticky="ew", pady=6)
        self.log = scrolledtext.ScrolledText(frame, height=16, wrap="word", state="disabled")
        self.log.grid(row=12, column=0, columnspan=3, sticky="nsew")
        self._append("No Blender: Arquivo → Importar → Wavefront (.obj). Use a tecla Home para enquadrar a malha.")
        root.protocol("WM_DELETE_WINDOW", self._close)
        root.after(100, self._poll)

    def _path_row(
        self, frame: ttk.Frame, row: int, label: str, variable: tk.StringVar, command: Callable[[], None]
    ) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=6, padx=(0, 10))
        ttk.Entry(frame, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=6)
        button = ttk.Button(frame, text="Selecionar…", command=command)
        button.grid(row=row, column=2, padx=(8, 0), pady=6)
        self.buttons.append(button)

    def _pick_cache(self) -> None:
        selected = filedialog.askdirectory(parent=self.root, title="Selecione a pasta do IMVU Classic")
        if selected:
            self.cache.set(selected)

    def _pick_source(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self.root,
            title="Selecione uma malha ou arquivo do cache (inclusive sem extensão)",
            filetypes=[("Todos os arquivos", "*"), ("Cal3D", "*.cmf *.xmf"), ("ZIP", "*.zip")],
        )
        if selected:
            self.source.set(selected)

    def _pick_output(self) -> None:
        selected = filedialog.askdirectory(parent=self.root, title="Selecione a pasta de saída")
        if selected:
            self.output.set(selected)

    def _options(self) -> tuple[Path, bool, float]:
        import math

        output = self.output.get().strip()
        if not output:
            raise ValueError("Selecione uma pasta para salvar os arquivos OBJ.")
        try:
            scale = float(self.scale.get().strip().replace(",", "."))
        except ValueError as exc:
            raise ValueError("A escala deve ser um número positivo; por exemplo, 1 ou 0,01.") from exc
        if not math.isfinite(scale) or scale <= 0:
            raise ValueError("A escala deve ser um número positivo e finito.")
        return Path(output).expanduser(), self.center.get(), scale

    def _required_pid(self) -> str:
        pid = self.pid.get().strip()
        if not pid or not pid.isascii() or not pid.isdigit() or int(pid) <= 0:
            raise ValueError("Informe um PID numérico positivo.")
        return pid

    def _cache_path(self) -> Path:
        value = self.cache.get().strip()
        if not value:
            raise ValueError("Selecione a pasta de cache do IMVU Classic.")
        return Path(value).expanduser()

    def _scan(self) -> None:
        try:
            cache, pid = self._cache_path(), self._required_pid()
        except ValueError as exc:
            messagebox.showerror("Verifique os campos", str(exc), parent=self.root)
            return

        def work() -> None:
            report = scan_cache(cache, pid)
            self.events.put(("log", f"Arquivos examinados: {report.scanned_files}. Correspondências: {len(report.matches)}."))
            for candidate in report.matches:
                path = str(candidate.source)
                if candidate.member:
                    path += f" :: {candidate.member}"
                self.events.put(("log", f"[{candidate.format}] {path}\n  Associação: {candidate.evidence}"))
            if report.unassigned:
                self.events.put(("log", f"Malhas sem associação confirmada a um PID: {len(report.unassigned)}."))
                self.events.put(("log", "Essas malhas podem ser convertidas individualmente; não serão atribuídas ao PID por suposição."))
                for candidate in report.unassigned[:10]:
                    member = f" :: {candidate.member}" if candidate.member else ""
                    self.events.put(("log", f"  Sem PID: {candidate.source}{member}"))
                if len(report.unassigned) > 10:
                    self.events.put(("log", "  Exibidas somente as primeiras 10 malhas sem PID."))
            for warning in report.warnings:
                self.events.put(("log", f"Aviso: {warning}"))
            if not report.matches:
                self.events.put(("log", f"Nenhum arquivo associado ao PID {pid} foi localizado. Nenhum produto foi exportado."))

        self._start("Buscando arquivos no cache…", work)

    def _export(self) -> None:
        try:
            cache, pid = self._cache_path(), self._required_pid()
            output, center, scale = self._options()
        except ValueError as exc:
            messagebox.showerror("Verifique os campos", str(exc), parent=self.root)
            return

        def work() -> None:
            paths = export_product(cache, pid, output, center=center, scale=scale)
            for path in paths:
                self.events.put(("log", f"Salvo: {path}"))
            self.events.put(("log", f"Exportação concluída para o PID {pid}."))

        self._start("Exportando o produto…", work)

    def _convert(self) -> None:
        try:
            source = self.source.get().strip()
            if not source:
                raise ValueError("Selecione um arquivo no campo Arquivo direto.")
            output, center, scale = self._options()
            source_path = Path(source).expanduser()
            pid = self.pid.get().strip() or None
            if pid:
                pid = self._required_pid()
        except ValueError as exc:
            messagebox.showerror("Verifique os campos", str(exc), parent=self.root)
            return

        def work() -> None:
            paths = convert_file(source_path, output, pid=pid, center=center, scale=scale)
            for path in paths:
                self.events.put(("log", f"Salvo: {path}"))
            self.events.put(("log", "Conversão direta concluída. O PID digitado é apenas um rótulo de saída."))

        self._start("Convertendo o arquivo selecionado…", work)

    def _associate(self) -> None:
        try:
            source = self.source.get().strip()
            if not source:
                raise ValueError("Selecione o arquivo que deseja associar no campo Arquivo direto.")
            source_path = Path(source).expanduser()
            if not source_path.is_file():
                raise ValueError("O arquivo selecionado não existe ou não pode ser lido.")
            pid = self._required_pid()
        except (ValueError, OSError) as exc:
            messagebox.showerror("Verifique os campos", str(exc), parent=self.root)
            return

        index_path = Path.home() / "IMVU-OBJ" / "product-index.json"
        confirmed = messagebox.askyesno(
            "Confirmar associação ao PID",
            (
                f"Você sabe que este arquivo pertence ao produto {pid}?\n\n"
                f"{source_path}\n\n"
                "Esta associação é uma informação fornecida por você, não uma identificação automática. "
                "O hash do arquivo será registrado no índice local:\n"
                f"{index_path}\n\n"
                "O arquivo original e o cache não serão alterados. Confirmar?"
            ),
            parent=self.root,
            icon="question",
        )
        if not confirmed:
            return

        def work() -> None:
            associate(source_path, pid)
            self.events.put(("log", f"Associação informada por você registrada: {source_path} → PID {pid}."))
            self.events.put(("log", f"Índice local: {index_path}"))
            self.events.put(("log", "Agora a busca por PID reconhecerá o conteúdo deste arquivo, mesmo que seu nome mude."))

        self._start("Registrando associação ao PID…", work)

    def _start(self, label: str, work: Callable[[], None]) -> None:
        if self.busy:
            return
        self.busy = True
        for button in self.buttons:
            button.state(["disabled"])
        self.status.set(label)
        self._append("\n" + label)
        self.progress.start(12)

        def worker() -> None:
            try:
                work()
            except Exception as exc:
                # O erro fica visível; o thread principal decide como mostrá-lo.
                self.events.put(("error", f"{type(exc).__name__}: {exc}"))
            finally:
                self.events.put(("done", ""))

        threading.Thread(target=worker, daemon=True).start()

    def _append(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll(self) -> None:
        while True:
            try:
                event, value = self.events.get_nowait()
            except queue.Empty:
                break
            if event == "log":
                self._append(value)
            elif event == "error":
                self._append("Erro: " + value)
                messagebox.showerror("Não foi possível concluir", value, parent=self.root)
            elif event == "done":
                self.busy = False
                self.progress.stop()
                self.status.set("Operação finalizada. Consulte o resultado abaixo.")
                for button in self.buttons:
                    button.state(["!disabled"])
        self.root.after(100, self._poll)

    def _close(self) -> None:
        if self.busy:
            messagebox.showinfo(
                "Operação em andamento", "Aguarde a leitura ou exportação terminar antes de fechar.", parent=self.root
            )
            return
        self.root.destroy()


def main() -> None:
    """Abre a interface desktop. Requer Python com suporte a Tk."""
    root = tk.Tk()
    Application(root)
    root.mainloop()
