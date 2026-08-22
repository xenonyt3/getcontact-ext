#!/usr/bin/env python3
"""Desktop interface for GetContact profile/tag lookups and local accounts."""

from __future__ import annotations

import json
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import engine


class LookupApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("GetContact Desktop")
        self.minsize(720, 480)
        self.geometry("800x580")
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.busy = False
        self.phone = tk.StringVar()
        self.account = tk.StringVar()
        self.status = tk.StringVar(value="Siap")
        self._build()
        self.refresh_active_account()
        self.after(100, self._drain_events)

    def _build(self) -> None:
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(4, weight=1)
        ttk.Label(root, text="GetContact Desktop", font=("Segoe UI", 18, "bold")).grid(row=0, column=0, sticky="w")
        account_row = ttk.Frame(root)
        account_row.grid(row=1, column=0, sticky="ew", pady=(4, 12))
        account_row.columnconfigure(0, weight=1)
        ttk.Label(account_row, textvariable=self.account).grid(row=0, column=0, sticky="w")
        self.quota_button = ttk.Button(account_row, text="Cek Kuota", command=self.check_quota)
        self.quota_button.grid(row=0, column=1, padx=(0, 8))
        ttk.Button(account_row, text="Kelola akun", command=self.open_accounts).grid(row=0, column=2)
        ttk.Label(root, text="Masukkan nomor, lalu pilih Profil atau Tag.").grid(row=2, column=0, sticky="w", pady=(0, 10))
        form = ttk.Frame(root)
        form.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        form.columnconfigure(0, weight=1)
        entry = ttk.Entry(form, textvariable=self.phone, font=("Segoe UI", 11))
        entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        entry.bind("<Return>", lambda _event: self.lookup("profile"))
        self.profile_button = ttk.Button(form, text="Cari Profil", command=lambda: self.lookup("profile"))
        self.profile_button.grid(row=0, column=1, padx=(0, 8))
        self.tags_button = ttk.Button(form, text="Cari Tag", command=lambda: self.lookup("tags"))
        self.tags_button.grid(row=0, column=2)
        self.output = tk.Text(root, wrap="word", font=("Consolas", 10), state="disabled")
        self.output.grid(row=4, column=0, sticky="nsew")
        ttk.Label(root, textvariable=self.status).grid(row=5, column=0, sticky="e", pady=(10, 0))
        entry.focus_set()

    def refresh_active_account(self) -> None:
        try:
            store = engine.load_store()
            name = store.get("active")
            credential = store.get("credentials", {}).get(name or "", {})
            phone = credential.get("phoneNumber", "")
            self.account.set(f"Akun aktif: {name or '-'}{f' ({phone})' if phone else ''}")
        except engine.GtcError as exc:
            self.account.set(f"Kredensial tidak dapat dibaca: {exc}")

    def open_accounts(self) -> None:
        AccountDialog(self)

    def lookup(self, source: str) -> None:
        raw_phone = self.phone.get().strip()
        if not raw_phone:
            messagebox.showwarning("Nomor diperlukan", "Masukkan nomor telepon terlebih dahulu.", parent=self)
            return
        if self.busy:
            return
        self.busy = True
        self.status.set("Mencari…")
        self.profile_button.configure(state="disabled")
        self.tags_button.configure(state="disabled")
        self.quota_button.configure(state="disabled")

        def worker() -> None:
            try:
                account, credential = engine.get_cred(engine.load_store(), None)
                phone = engine.normalize_phone(raw_phone)
                result = engine.api_search(credential, phone, source)
                self.events.put(("result", self.format_result(account, phone, source, result)))
            except Exception as exc:
                self.events.put(("error", f"{type(exc).__name__}: {exc}"))
            finally:
                self.events.put(("done", None))

        threading.Thread(target=worker, daemon=True).start()

    def check_quota(self) -> None:
        """Retrieve and display the active account's subscription allowance."""
        if self.busy:
            return
        self.busy = True
        self.status.set("Memeriksa kuota…")
        self.profile_button.configure(state="disabled")
        self.tags_button.configure(state="disabled")
        self.quota_button.configure(state="disabled")

        def worker() -> None:
            try:
                account, credential = engine.get_cred(engine.load_store(), None)
                result = engine.api_subscription(credential)
                self.events.put(("result", self.format_quota(account, result)))
            except Exception as exc:
                self.events.put(("error", f"{type(exc).__name__}: {exc}"))
            finally:
                self.events.put(("done", None))

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def format_result(account: str, phone: str, source: str, body: dict) -> str:
        lines = [f"[{account}] {phone}", ""]
        if source == "profile":
            profile = engine.dig(body, "result.profile") or {}
            for key in ("displayName", "name", "surname", "phoneNumber", "displayNumber", "tagCount", "email"):
                if key in profile:
                    lines.append(f"{key:<14}: {profile.get(key) or '-'}")
            if profile:
                return "\n".join(lines)
        else:
            tags = engine.dig(body, "result.tags") or []
            if tags:
                lines.append(f"Tag ({len(tags)}):")
                lines.extend(f"- {tag.get('tag', '-')}  x{tag.get('count', '')}" for tag in tags)
                return "\n".join(lines)
        lines.append(json.dumps(body, ensure_ascii=False, indent=2))
        return "\n".join(lines)

    @staticmethod
    def format_quota(account: str, body: dict) -> str:
        usage = engine.dig(body, "result.subscriptionInfo.usage") or {}
        lines = [f"Kuota akun [{account}]", ""]
        for key, label in (("search", "Pencarian profil"), ("numberDetail", "Pencarian tag")):
            quota = usage.get(key) or {}
            remaining = quota.get("remainingCount", "?")
            limit = quota.get("limit", "?")
            lines.append(f"{label:<19}: {remaining}/{limit} tersisa")
        renew_date = engine.dig(body, "result.subscriptionInfo.renewDate", "-")
        lines.append(f"Tanggal perpanjangan: {renew_date}")
        return "\n".join(lines)

    def _drain_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "result":
                    self._write(str(value))
                elif kind == "error":
                    self._write(f"ERROR: {value}")
                elif kind == "done":
                    self.busy = False
                    self.status.set("Selesai")
                    self.profile_button.configure(state="normal")
                    self.tags_button.configure(state="normal")
                    self.quota_button.configure(state="normal")
        except queue.Empty:
            pass
        self.after(100, self._drain_events)

    def _write(self, text: str) -> None:
        self.output.configure(state="normal")
        self.output.insert("end", text.rstrip() + "\n\n")
        self.output.see("end")
        self.output.configure(state="disabled")


class AccountDialog(tk.Toplevel):
    def __init__(self, app: LookupApp) -> None:
        super().__init__(app)
        self.app = app
        self.title("Kelola akun")
        self.geometry("820x540")
        self.minsize(700, 440)
        self.transient(app)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._build()
        self.refresh()

    def _build(self) -> None:
        top = ttk.Frame(self, padding=(14, 14, 14, 8))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(0, weight=1)
        ttk.Label(top, text="Akun tersimpan", font=("Segoe UI", 13, "bold")).grid(row=0, column=0, sticky="w")
        ttk.Button(top, text="Tambah dari WhatsApp", command=self.create_whatsapp).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(top, text="Tambah manual", command=self.add_manual).grid(row=0, column=2, padx=(8, 0))
        body = ttk.Panedwindow(self, orient="vertical")
        body.grid(row=1, column=0, sticky="nsew", padx=14, pady=(0, 8))
        table_frame = ttk.Frame(body)
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(table_frame, columns=("phone", "description"), show="headings", height=8)
        self.table.heading("phone", text="Nomor")
        self.table.heading("description", text="Keterangan")
        self.table.column("phone", width=160, anchor="w")
        self.table.column("description", width=440, anchor="w")
        self.table.grid(row=0, column=0, sticky="nsew")
        self.table.bind("<<TreeviewSelect>>", lambda _event: self.show_selected())
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scroll.set)
        body.add(table_frame, weight=1)
        detail_frame = ttk.Labelframe(body, text="Detail kredensial", padding=8)
        detail_frame.columnconfigure(0, weight=1)
        detail_frame.rowconfigure(0, weight=1)
        self.detail = tk.Text(detail_frame, height=9, wrap="word", font=("Consolas", 9), state="disabled")
        self.detail.grid(row=0, column=0, sticky="nsew")
        body.add(detail_frame, weight=1)
        buttons = ttk.Frame(self, padding=(14, 0, 14, 14))
        buttons.grid(row=2, column=0, sticky="ew")
        ttk.Button(buttons, text="Jadikan aktif", command=self.use_selected).pack(side="left")
        ttk.Button(buttons, text="Hapus", command=self.remove_selected).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Tutup", command=self.destroy).pack(side="right")

    def selected_name(self) -> str | None:
        selected = self.table.selection()
        return selected[0] if selected else None

    def refresh(self, select: str | None = None) -> None:
        for item in self.table.get_children():
            self.table.delete(item)
        try:
            store = engine.load_store()
        except engine.GtcError as exc:
            messagebox.showerror("Kredensial", str(exc), parent=self)
            return
        active = store.get("active")
        for name, credential in store.get("credentials", {}).items():
            description = credential.get("description", "")
            if name == active:
                description = f"Aktif — {description}".rstrip()
            self.table.insert("", "end", iid=name, values=(credential.get("phoneNumber", ""), description))
        wanted = select or active
        if wanted and self.table.exists(wanted):
            self.table.selection_set(wanted)
            self.table.focus(wanted)
        self.show_selected()
        self.app.refresh_active_account()

    def show_selected(self) -> None:
        name = self.selected_name()
        text = "Pilih akun untuk melihat detail."
        if name:
            credential = engine.load_store().get("credentials", {}).get(name, {})
            text = "\n".join((
                f"name           : {name}",
                f"phoneNumber    : {credential.get('phoneNumber', '')}",
                f"clientDeviceId : {credential.get('clientDeviceId', '')}",
                f"finalKey       : {credential.get('finalKey', '')}",
                f"token          : {credential.get('token', '')}",
                f"validationDate : {credential.get('validationDate', '')}",
            ))
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", text)
        self.detail.configure(state="disabled")

    def create_whatsapp(self) -> None:
        CredentialWizard(self)

    def add_manual(self) -> None:
        ManualCredentialDialog(self)

    def use_selected(self) -> None:
        name = self.selected_name()
        if not name:
            return
        try:
            engine.use_credential(name)
        except engine.GtcError as exc:
            messagebox.showerror("Gagal memilih akun", str(exc), parent=self)
            return
        self.refresh(name)

    def remove_selected(self) -> None:
        name = self.selected_name()
        if not name:
            return
        if not messagebox.askyesno("Hapus akun", f"Hapus kredensial '{name}' dari komputer ini?", parent=self):
            return
        try:
            engine.remove_credential(name)
        except engine.GtcError as exc:
            messagebox.showerror("Gagal menghapus akun", str(exc), parent=self)
            return
        self.refresh()


class CredentialWizard(tk.Toplevel):
    def __init__(self, accounts: AccountDialog) -> None:
        super().__init__(accounts)
        self.accounts = accounts
        self.title("Tambah akun melalui WhatsApp")
        self.geometry("780x560")
        self.minsize(660, 500)
        self.transient(accounts)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.pending: engine.PendingCredential | None = None
        self.busy = False
        self.phone = tk.StringVar()
        self.name = tk.StringVar()
        self.status = tk.StringVar(value="Masukkan nomor WhatsApp milik Anda.")
        self.credential_vars = {key: tk.StringVar(value="Belum dibuat") for key in ("clientDeviceId", "finalKey", "token")}
        self._build()
        self.after(100, self._drain_events)

    def _build(self) -> None:
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(5, weight=1)
        ttk.Label(root, text="Verifikasi akun melalui WhatsApp", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(root, text="Nomor WhatsApp").grid(row=1, column=0, sticky="w", pady=(12, 4))
        ttk.Entry(root, textvariable=self.phone).grid(row=1, column=1, sticky="ew", pady=(12, 4))
        ttk.Label(root, text="Nama akun (opsional)").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Entry(root, textvariable=self.name).grid(row=2, column=1, sticky="ew", pady=4)
        credential_box = ttk.Labelframe(root, text="Kredensial sesi (rahasia)", padding=8)
        credential_box.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(10, 6))
        credential_box.columnconfigure(1, weight=1)
        for row, key in enumerate(("clientDeviceId", "finalKey", "token")):
            ttk.Label(credential_box, text=key).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=2)
            ttk.Entry(credential_box, textvariable=self.credential_vars[key], state="readonly").grid(row=row, column=1, sticky="ew", pady=2)
        ttk.Label(root, textvariable=self.status, wraplength=700).grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 8))
        self.instructions = tk.Text(root, height=9, wrap="word", font=("Consolas", 9), state="disabled")
        self.instructions.grid(row=5, column=0, columnspan=2, sticky="nsew")
        buttons = ttk.Frame(root)
        buttons.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.start_button = ttk.Button(buttons, text="Mulai verifikasi", command=self.start)
        self.start_button.pack(side="left")
        self.complete_button = ttk.Button(buttons, text="Saya sudah mengirim pesan WA", command=self.complete, state="disabled")
        self.complete_button.pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Tutup", command=self.destroy).pack(side="right")

    def _set_instructions(self, text: str) -> None:
        self.instructions.configure(state="normal")
        self.instructions.delete("1.0", "end")
        self.instructions.insert("1.0", text)
        self.instructions.configure(state="disabled")

    def _append_log(self, text: str) -> None:
        """Append verification progress to the wizard's visible text area."""
        self.instructions.configure(state="normal")
        self.instructions.insert("end", text.rstrip() + "\n\n")
        self.instructions.see("end")
        self.instructions.configure(state="disabled")

    def start(self) -> None:
        if self.busy:
            return
        raw_phone = self.phone.get().strip()
        if not raw_phone:
            messagebox.showwarning("Nomor diperlukan", "Masukkan nomor WhatsApp terlebih dahulu.", parent=self)
            return
        self.busy = True
        self.start_button.configure(state="disabled")
        self.status.set("Membuat perangkat dan memulai verifikasi…")
        self._set_instructions("Memulai verifikasi WhatsApp…\n")

        def worker() -> None:
            try:
                pending = engine.start_whatsapp_credential(
                    raw_phone,
                    on_credential=lambda credential: self.events.put(("credential", credential)),
                    progress=lambda message: self.events.put(("progress", message)),
                )
                self.events.put(("pending", pending))
            except Exception as exc:
                self.events.put(("error", str(exc)))
            finally:
                self.events.put(("ready", None))

        threading.Thread(target=worker, daemon=True).start()

    def complete(self) -> None:
        if self.busy or not self.pending:
            return
        self.busy = True
        self.complete_button.configure(state="disabled")
        self.status.set("Memeriksa hasil verifikasi WhatsApp…")
        self._append_log("Memeriksa hasil verifikasi WhatsApp…")

        def worker() -> None:
            try:
                account, _ = engine.complete_whatsapp_credential(self.pending, name=self.name.get().strip() or None)
                self.events.put(("completed", account))
            except Exception as exc:
                self.events.put(("error", str(exc)))
            finally:
                self.events.put(("ready", None))

        threading.Thread(target=worker, daemon=True).start()

    def _drain_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "credential":
                    credential = value
                    for key in self.credential_vars:
                        self.credential_vars[key].set(str(credential[key]))
                    self.status.set("Kredensial awal dibuat. Menyambungkan WhatsApp…")
                    self._append_log("Kredensial sesi awal dibuat.")
                elif kind == "progress":
                    self.status.set(str(value))
                    self._append_log(str(value))
                elif kind == "pending":
                    self.pending = value
                    code = self.pending.verification_code or "(lihat pesan pada tautan)"
                    self._append_log(
                        "Tautan WhatsApp siap. Buka atau salin tautan berikut, lalu kirim pesan yang sudah diisi.\n\n"
                        f"Tautan: {self.pending.deeplink}\n\nKode: {code}\n\n"
                        "Setelah pesan mendapat dua centang, klik ‘Saya sudah mengirim pesan WA’."
                    )
                    self.status.set("Tautan WhatsApp siap. Kredensial belum tersimpan sampai verifikasi sukses.")
                    self.complete_button.configure(state="normal")
                elif kind == "completed":
                    self._append_log(f"Berhasil: akun '{value}' telah diverifikasi dan disimpan.")
                    self.accounts.refresh(str(value))
                    messagebox.showinfo("Berhasil", f"Akun '{value}' telah diverifikasi dan disimpan.", parent=self)
                    self.destroy()
                elif kind == "error":
                    self.status.set(f"Gagal: {value}")
                    self._append_log(f"GAGAL: {value}")
                    messagebox.showerror("Verifikasi gagal", str(value), parent=self)
                elif kind == "ready":
                    self.busy = False
                    if not self.pending:
                        self.start_button.configure(state="normal")
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(100, self._drain_events)


class ManualCredentialDialog(tk.Toplevel):
    def __init__(self, accounts: AccountDialog) -> None:
        super().__init__(accounts)
        self.accounts = accounts
        self.title("Tambah kredensial manual")
        self.transient(accounts)
        self.resizable(True, False)
        self.values = {key: tk.StringVar() for key in ("name", "phone", "device", "final", "token", "description")}
        self._build()

    def _build(self) -> None:
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        fields = [("Nama akun", "name"), ("Nomor", "phone"), ("Client device ID", "device"), ("Final key", "final"), ("Token", "token"), ("Keterangan", "description")]
        for row, (label, key) in enumerate(fields):
            ttk.Label(root, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
            ttk.Entry(root, textvariable=self.values[key], width=62).grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Label(root, text="Gunakan hanya kredensial akun sendiri.", wraplength=520).grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=(8, 12))
        buttons = ttk.Frame(root)
        buttons.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="ew")
        ttk.Button(buttons, text="Simpan", command=self.save).pack(side="left")
        ttk.Button(buttons, text="Batal", command=self.destroy).pack(side="right")

    def save(self) -> None:
        try:
            engine.add_credential(
                self.values["name"].get(), token=self.values["token"].get(), final_key=self.values["final"].get(),
                device_id=self.values["device"].get(), phone=self.values["phone"].get(), description=self.values["description"].get(),
            )
        except engine.GtcError as exc:
            messagebox.showerror("Tidak dapat menyimpan", str(exc), parent=self)
            return
        name = self.values["name"].get().strip()
        self.accounts.refresh(name)
        self.destroy()


if __name__ == "__main__":
    LookupApp().mainloop()
