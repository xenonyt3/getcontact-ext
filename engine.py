"""Core engine for the desktop GetContact client.

This module owns protocol, credential, and API operations. It deliberately has
no command-line parsing, terminal output, or UI dependencies.
Core research by @mfajarb, 
re-engineered, fixed by @falihputraaaa / @prnce______ (2nd)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import random
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


GTC_BASE = "https://pbssrv-centralevents.com"
VFK_BASE = "https://api.verifykit.com"
HMAC_KEY = "31426764382a642f3a6665497235466f3d236d5d785b722b4c657457442a495b494524324866782a2364292478587a78662d7a7b7578593f71703e2b7e365762"
VFK_HMAC_KEY = "3452235d713252604a35562d325f765238695738485863672a705e6841544d3c7e6e45463028266f372b544e596f3829236b392825262e534a7e774f37653932"
VFK_CLIENT_KEY = "bhvbd7ced119dc6ad6a0b35bd3cf836555d6f71930d9e5a405f32105c790d"
VFK_FINAL_KEY = "bd48d8c25293cfb537619cc93ae3d6e372eb2ddfffff4ab0eb000777144c7bfa"

APP_VERSION = "8.4.0"
ANDROID_OS = "android 9"
LANG = "en_US"
COUNTRY = "id"
DEVICE_NAME = "SM-G977N"
TIME_ZONE = "Asia/Bangkok"
BUNDLE_ID = "app.source.getcontact"
CARRIER = ("510", "Indosat Ooredoo", "01")
DH_P = 900719898367
DH_G = 7

CONFIG_DIR = Path(os.environ.get("GTC_CONFIG_DIR", Path.home() / ".config" / "gtc"))
CRED_FILE = CONFIG_DIR / "credentials.json"


class GtcError(Exception):
    """An expected error returned by the service or local credential store."""


@dataclass(frozen=True)
class PendingCredential:
    """Credential material created while a WhatsApp verification is in progress."""

    phone: str
    client_device_id: str
    final_key: str
    token: str
    reference: str
    deeplink: str
    verification_code: str | None


ProgressCallback = Callable[[str], None]


def _sig(ts: str, message: str, key_hex: str) -> str:
    mac = hmac.new(bytes.fromhex(key_hex), f"{ts}-{message}".encode(), hashlib.sha256)
    return base64.b64encode(mac.digest()).decode()


def _pad(data: bytes) -> bytes:
    padding = 16 - len(data) % 16
    return data + bytes([padding]) * padding


def encrypt(data: str, key_hex: str) -> str:
    encryptor = Cipher(algorithms.AES(bytes.fromhex(key_hex)), modes.ECB()).encryptor()
    return base64.b64encode(encryptor.update(_pad(data.encode())) + encryptor.finalize()).decode()


def decrypt(data: str, key_hex: str) -> str:
    decryptor = Cipher(algorithms.AES(bytes.fromhex(key_hex)), modes.ECB()).decryptor()
    output = decryptor.update(base64.b64decode(data)) + decryptor.finalize()
    return output[:-output[-1]].decode()


def dh_keypair() -> tuple[int, int]:
    private_key = secrets.randbelow(10**8 - 10**6) + 10**6
    return private_key, pow(DH_G, private_key, DH_P)


def dh_final_key(private_key: int, server_public_key: int) -> str:
    return hashlib.sha256(str(pow(int(server_public_key), private_key, DH_P)).encode()).hexdigest()


def _ts() -> str:
    return str(int(time.time() * 1000))


def new_device_id() -> str:
    return secrets.token_hex(8)


def _post(url: str, body: str, headers: dict, timeout: int = 25) -> requests.Response:
    return requests.post(url, data=body, headers=headers, timeout=timeout)


def gtc_call(endpoint: str, payload: dict, *, token: str, final_key: str,
             device_id: str, encrypted: bool = True) -> tuple[int, dict]:
    """Send a request to a GetContact endpoint and return its decoded body."""
    raw = json.dumps(payload, ensure_ascii=False)
    timestamp = _ts()
    headers = {
        "Content-Type": "application/json",
        "x-os": ANDROID_OS,
        "x-app-version": APP_VERSION,
        "x-client-device-id": device_id,
        "x-lang": LANG,
        "x-req-timestamp": timestamp,
        "x-country-code": COUNTRY,
        "x-encrypted": "1" if encrypted else "0",
        "x-req-signature": _sig(timestamp, raw, HMAC_KEY),
    }
    if token:
        headers["x-token"] = token
    body = json.dumps({"data": encrypt(raw, final_key)}) if encrypted else raw
    response = _post(GTC_BASE + endpoint, body, headers)
    try:
        parsed = response.json()
    except ValueError as exc:
        raise GtcError(f"{endpoint}: non-JSON response (HTTP {response.status_code})") from exc
    if encrypted and "data" in parsed:
        parsed = json.loads(decrypt(parsed["data"], final_key))
    return response.status_code, parsed


def vfk_call(endpoint: str, payload: dict, device_id: str) -> tuple[int, dict]:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    timestamp = _ts()
    headers = {
        "Content-Type": "application/json",
        "X-VFK-Client-Device-Id": device_id,
        "X-VFK-Client-Key": VFK_CLIENT_KEY,
        "X-VFK-Sdk-Version": "0.11.4",
        "X-VFK-Os": "android 9.0",
        "X-VFK-App-Version": "8.16.0",  # Override khusus VerifyKit
        "X-VFK-Encrypted": "1",
        "X-VFK-Lang": "in_ID",
        "X-VFK-Req-Timestamp": timestamp,
        "X-VFK-Req-Signature": _sig(timestamp, raw, VFK_HMAC_KEY),
    }
    encrypted_body = json.dumps({"data": encrypt(raw, VFK_FINAL_KEY)}, separators=(",", ":"))
    response = _post(VFK_BASE + endpoint, encrypted_body, headers)
    try:
        parsed = response.json()
    except ValueError as exc:
        raise GtcError(f"{endpoint}: non-JSON response (HTTP {response.status_code})") from exc
    if "data" in parsed:
        parsed = json.loads(decrypt(parsed["data"], VFK_FINAL_KEY))
    return response.status_code, parsed  # PASTIKAN BARIS INI ADA!

def dig(obj: object, path: str, default=None):
    for key in path.split("."):
        if not isinstance(obj, dict) or key not in obj:
            return default
        obj = obj[key]
    return obj


def load_store() -> dict:
    if not CRED_FILE.exists():
        return {"active": None, "credentials": {}}
    try:
        return json.loads(CRED_FILE.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GtcError(f"Cannot read credentials: {exc}") from exc


def save_store(store: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CRED_FILE.write_text(json.dumps(store, indent=2), "utf-8")
    try:
        os.chmod(CRED_FILE, 0o600)
    except OSError:
        pass


def get_cred(store: dict, name: str | None) -> tuple[str, dict]:
    name = name or store.get("active")
    credentials = store.get("credentials", {})
    if not name or name not in credentials:
        raise GtcError("No active credential. Add or select an account first.")
    return name, credentials[name]


def normalize_phone(raw: str) -> str:
    phone = re.sub(r"[^\d+]", "", raw.strip())
    if phone.startswith("+"):
        return phone
    if phone.startswith("0"):
        return "+62" + phone[1:]
    if phone.startswith("62"):
        return "+" + phone
    raise GtcError(f"Invalid phone number: {raw}")


def api_search(credential: dict, phone: str, source: str) -> dict:
    endpoint = "/v2.8/number-detail" if source == "tags" else "/v2.8/search"
    payload = {
        "countryCode": COUNTRY,
        "phoneNumber": phone,
        "source": "profile" if source == "tags" else "search",
        "token": credential["token"],
    }
    status, body = gtc_call(endpoint, payload, token=credential["token"],
                            final_key=credential["finalKey"],
                            device_id=credential["clientDeviceId"])
    meta_status = dig(body, "meta.httpStatusCode")
    if status != 200 or meta_status != 200:
        raise GtcError(f"HTTP {status}/{meta_status}: {dig(body, 'meta.errorMessage', 'unknown error')}")
    return body


def api_subscription(credential: dict) -> dict:
    status, body = gtc_call("/v2.8/subscription", {"token": credential["token"]},
                            token=credential["token"], final_key=credential["finalKey"],
                            device_id=credential["clientDeviceId"])
    if status != 200:
        raise GtcError(f"HTTP {status}: {dig(body, 'meta.errorMessage', 'unknown error')}")
    return body


def _expect_status(status: int, expected: int, endpoint: str, body: dict) -> None:
    if status == expected:
        return
    detail = (dig(body, "meta.errorMessage") or dig(body, "message") or
              dig(body, "error") or "")
    suffix = f": {detail}" if detail else ""
    raise GtcError(f"{endpoint} gagal: HTTP {status}{suffix}")


def _notify(progress: ProgressCallback | None, message: str) -> None:
    if progress:
        progress(message)


def start_whatsapp_credential(raw_phone: str, *,
                              on_credential: Callable[[dict], None] | None = None,
                              progress: ProgressCallback | None = None) -> PendingCredential:
    """Begin the WhatsApp verification flow and return its pending state.

    The values sent to ``on_credential`` are deliberately *not* stored yet. They
    only become an active account after :func:`complete_whatsapp_credential`.
    """
    phone = normalize_phone(raw_phone)
    device_id = new_device_id()
    private_key, public_key = dh_keypair()
    _notify(progress, "Mendaftarkan perangkat…")

    register_body = {
        "carrierCountryCode": CARRIER[0], "carrierName": CARRIER[1],
        "carrierNetworkCode": CARRIER[2], "countryCode": COUNTRY, "deepLink": None,
        "deviceName": DEVICE_NAME, "deviceType": "Android", "email": None,
        "notificationToken": "", "oldToken": None, "peerKey": public_key,
        "timeZone": TIME_ZONE, "token": "",
    }
    raw = json.dumps(register_body, ensure_ascii=False)
    timestamp = _ts()
    response = _post(GTC_BASE + "/v2.8/register", raw, {
        "Content-Type": "application/json", "x-os": ANDROID_OS,
        "x-app-version": APP_VERSION, "x-client-device-id": device_id, "x-lang": LANG,
        "x-req-timestamp": timestamp, "x-country-code": COUNTRY, "x-encrypted": "0",
        "x-req-signature": _sig(timestamp, raw, HMAC_KEY),
    })
    try:
        registered = response.json()
    except ValueError as exc:
        raise GtcError(f"Registrasi perangkat gagal: HTTP {response.status_code}") from exc
    _expect_status(response.status_code, 201, "Registrasi perangkat", registered)

    token = dig(registered, "result.token")
    server_key = dig(registered, "result.serverKey")
    if not token or not server_key:
        raise GtcError("Registrasi perangkat tidak mengembalikan token atau serverKey.")
    final_key = dh_final_key(private_key, server_key)
    common = dict(token=token, final_key=final_key, device_id=device_id)
    if on_credential:
        on_credential({
            "phoneNumber": phone,
            "clientDeviceId": device_id,
            "finalKey": final_key,
            "token": token,
        })

    base = {
        "carrierCountryCode": CARRIER[0], "carrierName": CARRIER[1],
        "carrierNetworkCode": CARRIER[2], "countryCode": COUNTRY,
        "deviceName": DEVICE_NAME, "notificationToken": "", "timeZone": TIME_ZONE,
        "token": token,
    }
    steps = [
        ("/v2.8/init-basic", base, 201),
        ("/v2.8/ad-settings", {"source": "init", "token": token}, 200),
        ("/v2.8/init-intro", {**base, "hasRouting": False}, 201),
        ("/v2.8/email-code-validate/start", {
            "email": f"user{random.randint(10**7, 10**8 - 1)}@gmail.com",
            "fullName": f"User{random.randint(1000, 999999)}", "token": token,
        }, 200),
        ("/v2.8/country", {"countryCode": COUNTRY.upper(), "token": token}, 200),
        ("/v2.8/validation-start", {
            "app": "verifykit", "countryCode": COUNTRY,
            "notificationToken": "", "token": token,
        }, 200),
    ]
    for endpoint, payload, expected in steps:
        status, body = gtc_call(endpoint, payload, **common)
        _expect_status(status, expected, endpoint, body)
        _notify(progress, f"Selesai {endpoint}")

    _notify(progress, "Menghubungkan verifikasi WhatsApp…")
    
    # Gunakan input user apa adanya (misal "0812...")
    outside_phone = raw_phone.strip() 
    
    # 1. INIT
    status, body = vfk_call("/v2.0/init", {
        "isCallPermissionGranted": True,
        "countryCode": "ID",  # HURUF BESAR
        "deviceName": DEVICE_NAME,
        "installedApps": '{"whatsapp":0,"telegram":0,"viber":0}',  # WAJIB STRING (diapit kutip satu)!
        "outsideCountryCode": "ID",
        "outsidePhoneNumber": outside_phone,
        "timezone": TIME_ZONE,
        "bundleId": BUNDLE_ID,
    }, device_id)
    _expect_status(status, 200, "VerifyKit init", body)
    
    # 2. COUNTRY
    status, body = vfk_call("/v2.0/country", {
        "countryCode": "ID",
        "bundleId": BUNDLE_ID,
    }, device_id)
    _expect_status(status, 200, "VerifyKit country", body)
    
    # 3. START
    status, body = vfk_call("/v2.0/start", {
        "countryCode": "ID",
        "mcc": CARRIER[0],  # "510"
        "mnc": CARRIER[2],  # "01"
        "phoneNumber": phone,  # Format E.164: "+628..."
        "app": "whatsapp", 
        "bundleId": BUNDLE_ID,
    }, device_id)
    _expect_status(status, 200, "VerifyKit start", body)

    # Ekstrak hasil
    deeplink = dig(body, "result.deeplink")
    reference = dig(body, "result.reference")
    if not deeplink or not reference:
        raise GtcError("VerifyKit tidak mengembalikan tautan atau reference WhatsApp.")
    
    code_matches = re.findall(r"\*(.*?)\*", urllib.parse.unquote(deeplink))
    verification_code = next((code for code in code_matches
                              if re.fullmatch(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+", code)), None)
    
    _notify(progress, "Kirim pesan WhatsApp dari nomor tersebut, lalu lanjutkan verifikasi.")
    return PendingCredential(phone, device_id, final_key, token, reference, deeplink, verification_code)


def complete_whatsapp_credential(pending: PendingCredential, *, name: str | None = None,
                                 description: str | None = None) -> tuple[str, dict]:
    """Finish a pending WhatsApp verification and persist the verified account."""
    status, body = vfk_call("/v2.0/check", {
        "reference": pending.reference, "bundleId": BUNDLE_ID,
    }, pending.client_device_id)
    _expect_status(status, 200, "VerifyKit check", body)
    session_id = dig(body, "result.sessionId")
    if not session_id:
        raise GtcError("Verifikasi WhatsApp belum selesai. Pastikan pesan sudah terkirim, lalu coba lagi.")
    status, body = gtc_call("/v2.8/verifykit-result", {
        "sessionId": session_id, "token": pending.token,
    }, token=pending.token, final_key=pending.final_key, device_id=pending.client_device_id)
    _expect_status(status, 200, "GetContact verification", body)
    validation_date = dig(body, "result.validationDate")
    if not validation_date:
        raise GtcError("GetContact tidak mengonfirmasi verifikasi WhatsApp.")

    account_name = name or pending.phone
    credential = {
        "description": description or f"Dibuat {validation_date}",
        "phoneNumber": pending.phone,
        "clientDeviceId": pending.client_device_id,
        "finalKey": pending.final_key,
        "token": pending.token,
        "validationDate": validation_date,
    }
    store = load_store()
    store.setdefault("credentials", {})[account_name] = credential
    store["active"] = account_name
    save_store(store)
    return account_name, credential


def add_credential(name: str, *, token: str, final_key: str, device_id: str | None = None,
                   phone: str = "", description: str = "") -> dict:
    """Save a credential supplied by the account owner and make it active."""
    if not name.strip() or not token.strip() or not final_key.strip():
        raise GtcError("Nama, token, dan finalKey wajib diisi.")
    credential = {
        "description": description.strip(), "phoneNumber": phone.strip(),
        "clientDeviceId": (device_id or new_device_id()).strip(),
        "finalKey": final_key.strip(), "token": token.strip(),
    }
    store = load_store()
    store.setdefault("credentials", {})[name.strip()] = credential
    store["active"] = name.strip()
    save_store(store)
    return credential


def use_credential(name: str) -> None:
    store = load_store()
    if name not in store.get("credentials", {}):
        raise GtcError(f"Akun tidak ditemukan: {name}")
    store["active"] = name
    save_store(store)


def remove_credential(name: str) -> None:
    store = load_store()
    if store.get("credentials", {}).pop(name, None) is None:
        raise GtcError(f"Akun tidak ditemukan: {name}")
    if store.get("active") == name:
        store["active"] = next(iter(store["credentials"]), None)
    save_store(store)


def show_captcha(image_base64: str) -> Path:
    path = Path(tempfile.gettempdir()) / f"gtc_captcha_{int(time.time())}.jpg"
    path.write_bytes(base64.b64decode(image_base64))
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass
    return path
