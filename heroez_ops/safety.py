"""Small safety checks shared by the upload and fetch paths."""
from __future__ import annotations

import io
import ipaddress
import socket
import zipfile
from urllib.parse import quote, urlsplit

MAX_UNCOMPRESSED = 60 * 1024 * 1024
MAX_RATIO = 200
MAX_ENTRIES = 5000


class UnsafeFile(ValueError):
    pass


def check_zip(data: bytes) -> None:
    """Refuse .docx/.xlsx files that would expand to far more than they are (zip bombs)."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos = z.infolist()
    except zipfile.BadZipFile as e:
        raise UnsafeFile("File rusak atau bukan dokumen Office.") from e
    if len(infos) > MAX_ENTRIES:
        raise UnsafeFile("Dokumen berisi terlalu banyak bagian.")
    total = sum(i.file_size for i in infos)
    if total > MAX_UNCOMPRESSED or (len(data) and total / max(len(data), 1) > MAX_RATIO):
        raise UnsafeFile("Dokumen terlalu besar saat dibuka.")
    for i in infos:
        name = i.filename
        if name.startswith(("/", "\\")) or ".." in name.split("/"):
            raise UnsafeFile("Dokumen berisi path yang tidak wajar.")


def is_public_https(url: str) -> tuple[bool, str]:
    """True if the URL is https and its host resolves only to public addresses."""
    try:
        p = urlsplit(url)
    except ValueError:
        return False, "alamat tidak valid"
    if p.scheme != "https" or not p.hostname:
        return False, "alamat harus diawali https://"
    if p.username or p.password:
        return False, "alamat tidak boleh berisi nama pengguna"
    try:
        infos = socket.getaddrinfo(p.hostname, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False, "nama situs tidak ditemukan"
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False, "alamat mengarah ke jaringan internal"
    return True, ""


def content_disposition(filename: str) -> str:
    """attachment; filename for any script, per RFC 5987."""
    ascii_name = filename.encode("ascii", "ignore").decode() or "download"
    ascii_name = ascii_name.replace('"', "")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
