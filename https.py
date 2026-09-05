#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Read-only HTTPS static file server for Python 2.7 and Python 3."""
from __future__ import print_function

import argparse
import os
import posixpath
import socket
import ssl
import sys
import threading
import time
import webbrowser

try:
    from http.server import HTTPServer, SimpleHTTPRequestHandler
    from socketserver import ThreadingMixIn
    from urllib.parse import unquote, urlsplit
except ImportError:
    from BaseHTTPServer import HTTPServer
    from SimpleHTTPServer import SimpleHTTPRequestHandler
    from SocketServer import ThreadingMixIn
    from urllib import unquote
    from urlparse import urlsplit

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CERT = os.path.join(SCRIPT_DIR, "cert.pem")
DEFAULT_KEY = os.path.join(SCRIPT_DIR, "key.pem")
ALLOWED_METHODS = "GET, HEAD, OPTIONS"


try:
    text_type = unicode
    binary_type = str
except NameError:
    text_type = str
    binary_type = bytes


MESSAGES = {
    "en": {
        "description": "Read-only HTTPS static file server",
        "help_help": "show this help message and exit",
        "port_help": "port to listen on (default: 8443)",
        "directory_help": "directory to serve (default: script directory)",
        "bind_help": "address to bind (default: 0.0.0.0)",
        "cert_help": "TLS certificate file",
        "key_help": "TLS private key file",
        "no_open_help": "do not open the local URL in a browser",
        "no_pause_help": "never wait for Enter after a fatal error",
        "lang_help": "console language: auto, zh, or en (auto uses Chinese only at UTC+08:00)",
        "port_invalid": "port must be between 1 and 65535",
        "not_directory": "Not a directory: {0}",
        "cert_missing": "HTTPS requires certificate files. Missing: {0}",
        "cert_unreadable": "HTTPS requires readable certificate files; cannot read {0}: {1}",
        "cert_unusable": "HTTPS requires usable certificate files. Could not load {0} and {1}: {2}",
        "bind_denied": "Permission denied binding to {0}; choose a non-privileged port or check local security policy",
        "bind_used": "Address {0} is already in use; choose another port",
        "bind_failed": "Could not bind to {0}: {1}",
        "started": "HTTPS server started",
        "directory": "Directory: {0}",
        "local_url": "Local URL: {0}",
        "lan_url": "LAN access: {0}",
        "lan_hint": "LAN access: use this computer's LAN IP with port {0}",
        "trust_warning": "Other devices must trust the bundled certificate to avoid warnings.",
        "browser_failed": "Warning: could not open browser: {0}",
        "browser_manual": "Open this URL manually: {0}",
        "browser_none": "no browser accepted the URL",
        "stopped": "Server stopped.",
        "error": "Error: {0}",
        "pause": "Press Enter to close this window...",
        "error_title": "HTTPS server error"
    },
    "zh": {
        "description": u"只读 HTTPS 静态文件服务器",
        "help_help": u"显示此帮助信息并退出",
        "port_help": u"监听端口（默认：8443）",
        "directory_help": u"要提供的目录（默认：脚本目录）",
        "bind_help": u"绑定地址（默认：0.0.0.0）",
        "cert_help": u"TLS 证书文件",
        "key_help": u"TLS 私钥文件",
        "no_open_help": u"不在浏览器中打开本地网址",
        "no_pause_help": u"发生致命错误后不等待按 Enter 键",
        "lang_help": u"控制台语言：auto、zh 或 en（auto 仅在 UTC+08:00 时使用中文）",
        "port_invalid": u"端口必须在 1 到 65535 之间",
        "not_directory": u"不是目录：{0}",
        "cert_missing": u"HTTPS 需要证书文件。缺少：{0}",
        "cert_unreadable": u"HTTPS 需要可读取的证书文件；无法读取 {0}：{1}",
        "cert_unusable": u"HTTPS 需要有效的证书文件。无法加载 {0} 和 {1}：{2}",
        "bind_denied": u"没有权限绑定到 {0}；请选择非特权端口或检查本地安全策略",
        "bind_used": u"地址 {0} 已被占用；请选择其他端口",
        "bind_failed": u"无法绑定到 {0}：{1}",
        "started": u"HTTPS 服务器已启动",
        "directory": u"目录：{0}",
        "local_url": u"本机网址：{0}",
        "lan_url": u"局域网访问：{0}",
        "lan_hint": u"局域网访问：请使用此计算机的局域网 IP 和端口 {0}",
        "trust_warning": u"其他设备必须信任随附的证书，以避免浏览器警告。",
        "browser_failed": u"警告：无法打开浏览器：{0}",
        "browser_manual": u"请手动打开此网址：{0}",
        "browser_none": u"没有浏览器接受该网址",
        "stopped": u"服务器已停止。",
        "error": u"错误：{0}",
        "pause": u"按 Enter 键关闭此窗口...",
        "error_title": u"HTTPS 服务器错误"
    }
}


def unicode_text(value):
    if isinstance(value, text_type):
        return value
    if isinstance(value, binary_type):
        try:
            return value.decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            return value.decode("utf-8", "replace")
    try:
        return text_type(value)
    except Exception:
        return text_type(repr(value))


def ascii_text(value):
    value = unicode_text(value)
    return "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in value)


def message(lang, key, *values):
    template = MESSAGES.get(lang, MESSAGES["en"])[key]
    return template.format(*tuple(unicode_text(value) for value in values))


def current_utc_offset_seconds(timestamp=None):
    """Return the current local UTC offset, including the active DST rule."""
    if timestamp is None:
        timestamp = time.time()
    local = time.localtime(timestamp)
    west = time.altzone if local.tm_isdst > 0 and time.daylight else time.timezone
    return -int(west)


def automatic_language(offset_seconds=None):
    if offset_seconds is None:
        offset_seconds = current_utc_offset_seconds()
    return "zh" if offset_seconds == 8 * 60 * 60 else "en"


def prescan_language(argv):
    selected = "auto"
    for index, item in enumerate(argv):
        if item == "--lang" and index + 1 < len(argv):
            selected = argv[index + 1]
        elif item.startswith("--lang="):
            selected = item.split("=", 1)[1]
    return automatic_language() if selected not in ("zh", "en") else selected


class Handler(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def translate_path(self, path):
        path = unquote(urlsplit(path).path)
        trailing = path.endswith("/")
        result = self.server.serve_directory
        for word in posixpath.normpath(path).split("/"):
            drive, word = os.path.splitdrive(word)
            head, word = os.path.split(word)
            if word and word not in (os.curdir, os.pardir):
                result = os.path.join(result, word)
        return result + (os.sep if trailing else "")

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        SimpleHTTPRequestHandler.end_headers(self)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Allow", ALLOWED_METHODS)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def reject_write(self):
        self.close_connection = True
        self.send_response(405, "Method Not Allowed")
        self.send_header("Allow", ALLOWED_METHODS)
        self.send_header("Connection", "close")
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_POST = reject_write
    do_PUT = reject_write
    do_DELETE = reject_write
    do_PATCH = reject_write
    do_TRACE = reject_write
    do_CONNECT = reject_write

    def log_message(self, fmt, *args):
        SimpleHTTPRequestHandler.log_message(
            self, ascii_text(fmt), *tuple(ascii_text(item) for item in args))


class ThreadingServer(ThreadingMixIn, HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class IPv6ThreadingServer(ThreadingServer):
    address_family = socket.AF_INET6


def server_class(bind):
    try:
        info = socket.getaddrinfo(bind, None, 0, socket.SOCK_STREAM)
        if info and info[0][0] == socket.AF_INET6:
            return IPv6ThreadingServer
    except socket.gaierror:
        pass
    return ThreadingServer


def ssl_context():
    if not hasattr(ssl, "SSLContext"):
        return None
    protocol = getattr(ssl, "PROTOCOL_TLS_SERVER",
                       getattr(ssl, "PROTOCOL_TLS", ssl.PROTOCOL_SSLv23))
    return ssl.SSLContext(protocol)


def ensure_certificate_files(cert_file, key_file, lang="en"):
    missing = []
    if not os.path.isfile(cert_file):
        missing.append(cert_file)
    if not os.path.isfile(key_file):
        missing.append(key_file)
    if missing:
        raise RuntimeError(message(lang, "cert_missing", ", ".join(missing)))
    for path in (cert_file, key_file):
        try:
            handle = open(path, "rb")
            handle.close()
        except (IOError, OSError) as exc:
            raise RuntimeError(message(lang, "cert_unreadable", path, exc))


def wrap_socket(httpd, cert_file, key_file, lang="en"):
    try:
        context = ssl_context()
        if context is not None and hasattr(context, "load_cert_chain"):
            context.load_cert_chain(cert_file, key_file)
            httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
        else:
            httpd.socket = ssl.wrap_socket(
                httpd.socket, certfile=cert_file, keyfile=key_file,
                server_side=True, ssl_version=ssl.PROTOCOL_SSLv23)
    except Exception as exc:
        raise RuntimeError(message(lang, "cert_unusable", cert_file, key_file, exc))


def socket_error_message(exc, bind, port, lang="en"):
    error_number = getattr(exc, "errno", None)
    if error_number is None and getattr(exc, "args", None):
        error_number = exc.args[0]
    address = "%s:%s" % (bind, port)
    if error_number in (13, 10013):
        return message(lang, "bind_denied", address)
    if error_number in (48, 98, 10048):
        return message(lang, "bind_used", address)
    return message(lang, "bind_failed", address, exc)


def write_line(stream, value):
    """Write Unicode when possible, with safe legacy-console fallbacks."""
    if stream is None:
        return False
    value = unicode_text(value) + u"\n"
    try:
        stream.write(value)
        stream.flush()
        return True
    except Exception:
        pass
    try:
        encoding = getattr(stream, "encoding", None) or "ascii"
        data = value.encode(encoding, "replace")
        if sys.version_info[0] >= 3 and hasattr(stream, "buffer"):
            stream.buffer.write(data)
        else:
            stream.write(data)
        stream.flush()
        return True
    except Exception:
        try:
            stream.write(ascii_text(value))
            stream.flush()
            return True
        except Exception:
            return False


def local_lan_address():
    """Best-effort LAN address discovery without sending application data."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 9))
        return probe.getsockname()[0]
    except socket.error:
        try:
            return socket.gethostbyname(socket.gethostname())
        except socket.error:
            return None
    finally:
        probe.close()


def display_host(host):
    if ":" in host and not host.startswith("["):
        return "[" + host + "]"
    return host


def open_browser(url, lang="en"):
    """Open a trusted, locally constructed URL using stdlib-only fallbacks."""
    errors = []
    try:
        if webbrowser.open(url, new=2):
            return True
    except Exception as exc:
        errors.append(exc)

    if os.name == "nt" and hasattr(os, "startfile"):
        try:
            os.startfile(url)
            return True
        except Exception as exc:
            errors.append(exc)

    try:
        controller = webbrowser.get()
        if controller.open(url, new=2):
            return True
    except Exception as exc:
        errors.append(exc)

    detail = errors[-1] if errors else message(lang, "browser_none")
    write_line(sys.stderr, message(lang, "browser_failed", detail))
    write_line(sys.stderr, message(lang, "browser_manual", url))
    return False


def delayed_browser_open(url, lang="en", delay=0.25):
    time.sleep(delay)
    open_browser(url, lang)


def start_browser_thread(url, lang="en"):
    thread = threading.Thread(target=delayed_browser_open, args=(url, lang))
    thread.daemon = True
    thread.start()
    return thread


def run(args):
    lang = args.lang
    directory = os.path.abspath(args.directory)
    cert_file = os.path.abspath(args.cert)
    key_file = os.path.abspath(args.key)
    if not os.path.isdir(directory):
        raise RuntimeError(message(lang, "not_directory", directory))
    ensure_certificate_files(cert_file, key_file, lang)
    try:
        httpd = server_class(args.bind)((args.bind, args.port), Handler)
    except (socket.error, socket.gaierror) as exc:
        raise RuntimeError(socket_error_message(exc, args.bind, args.port, lang))
    httpd.serve_directory = directory
    try:
        # Do not announce readiness or schedule a browser until the listening
        # socket has successfully become a TLS socket.
        wrap_socket(httpd, cert_file, key_file, lang)
        port = httpd.server_address[1]
        wildcard = args.bind in ("0.0.0.0", "::", "")
        local_host = "::1" if args.bind == "::" else "127.0.0.1"
        open_host = local_host if wildcard else args.bind
        url = "https://%s:%s/" % (display_host(open_host), port)

        write_line(sys.stdout, message(lang, "started"))
        write_line(sys.stdout, message(lang, "directory", directory))
        write_line(sys.stdout, message(lang, "local_url", url))
        if wildcard:
            lan_host = local_lan_address()
            if lan_host:
                lan_url = "https://%s:%s/" % (display_host(lan_host), port)
                write_line(sys.stdout, message(lang, "lan_url", lan_url))
            else:
                write_line(sys.stdout, message(lang, "lan_hint", port))
            write_line(sys.stdout, message(lang, "trust_warning"))

        # The delay runs in a daemon thread.  By the time it expires,
        # serve_forever below is already processing connections.
        if not args.no_open:
            start_browser_thread(url, lang)
        httpd.serve_forever()
    except KeyboardInterrupt:
        write_line(sys.stdout, message(lang, "stopped"))
    finally:
        httpd.server_close()


def parse_args(argv=None):
    actual_argv = list(sys.argv[1:] if argv is None else argv)
    lang = prescan_language(actual_argv)
    parser = argparse.ArgumentParser(add_help=False,
                                     description=message(lang, "description"))
    parser.add_argument("-h", "--help", action="help",
                        help=message(lang, "help_help"))
    parser.add_argument("-p", "--port", type=int, default=8443,
                        help=message(lang, "port_help"))
    parser.add_argument("-d", "--directory", default=SCRIPT_DIR,
                        help=message(lang, "directory_help"))
    parser.add_argument("-b", "--bind", default="0.0.0.0",
                        help=message(lang, "bind_help"))
    parser.add_argument("--cert", default=DEFAULT_CERT,
                        help=message(lang, "cert_help"))
    parser.add_argument("--key", default=DEFAULT_KEY,
                        help=message(lang, "key_help"))
    parser.add_argument("--lang", choices=("auto", "zh", "en"), default="auto",
                        help=message(lang, "lang_help"))
    parser.add_argument("--no-open", action="store_true",
                        help=message(lang, "no_open_help"))
    parser.add_argument("--no-pause", action="store_true",
                        help=message(lang, "no_pause_help"))
    args = parser.parse_args(actual_argv)
    args.lang = automatic_language() if args.lang == "auto" else args.lang
    if not 1 <= args.port <= 65535:
        parser.error(message(args.lang, "port_invalid"))
    return args


def show_windows_error(value, lang="en"):
    """Show a fatal error when pythonw provides no console streams."""
    if os.name != "nt":
        return False
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None, unicode_text(value), message(lang, "error_title"), 0x10)
        return True
    except Exception:
        return False


def stdin_is_interactive():
    if sys.stdin is None:
        return False
    try:
        return bool(sys.stdin.isatty())
    except Exception:
        return False


def pause_after_fatal_error(no_pause, lang="en"):
    if no_pause or os.name != "nt" or not stdin_is_interactive():
        return
    write_line(sys.stdout, message(lang, "pause"))
    try:
        input_function = raw_input
    except NameError:
        input_function = input
    try:
        input_function()
    except (EOFError, KeyboardInterrupt):
        pass


def report_fatal_error(exc, no_pause=False, lang="en"):
    rendered = message(lang, "error", exc)
    visible = write_line(sys.stderr, rendered)
    if not visible and sys.stdout is not sys.stderr:
        visible = write_line(sys.stdout, rendered)
    if not visible:
        show_windows_error(rendered, lang)
    pause_after_fatal_error(no_pause, lang)


def main(argv=None):
    args = parse_args(argv)
    try:
        run(args)
        return 0
    except Exception as exc:
        report_fatal_error(exc, args.no_pause, args.lang)
        return 1


if __name__ == "__main__":
    sys.exit(main())

