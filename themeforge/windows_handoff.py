"""Windows recipient dialog using standard controls and the system dialog manager."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import struct

from .handoff_verification import verify_handoff


# Dialog units scale with the dialog's font. A label immediately before an
# edit control supplies the control's accessible name through the Windows proxy.
_FIELDS = (
    (110, 120, "Evidence CSV path", "&Evidence CSV…", "Choose linked evidence CSV"),
    (111, 121, "Transcript path", "&Transcript…", "Choose transcript revision"),
    (112, 122, "Codebook CSV path", "&Codebook CSV…", "Choose shared codebook CSV"),
)
_VERIFY, _CLOSE, _RESULT = 1, 2, 130  # IDOK and IDCANCEL use normal Enter/Escape behavior.
_LAUNCH = 140
_BUTTON, _EDIT, _STATIC = 0x80, 0x81, 0x82
_TABSTOP = 0x00010000
_WS_CHILD_VISIBLE = 0x50000000
_CLIENTEDGE = 0x200
_WIDTH, _HEIGHT = 380, 250
_INSTRUCTIONS = ("Choose a linked evidence CSV for one transcript, the transcript revision you hold "
                 "and the shared codebook used for the export.")


def _controls():
    yield 100, _STATIC, _INSTRUCTIONS, 0, 0, (8, 6, 364, 22)
    for i, (field, browse, label, action, _) in enumerate(_FIELDS):
        y = 34 + 20 * i
        yield 101 + i, _STATIC, label, 0, 0, (8, y + 2, 74, 12)
        yield field, _EDIT, "", _TABSTOP | 0x80, _CLIENTEDGE, (83, y, 210, 14)
        yield browse, _BUTTON, action, _TABSTOP, 0, (299, y - 1, 73, 16)
    yield _VERIFY, _BUTTON, "&Verify files", _TABSTOP | 1, 0, (8, 95, 73, 16)
    yield 104, _STATIC, "Verification &result", 0, 0, (8, 117, 364, 12)
    # ES_READONLY | ES_MULTILINE | ES_AUTOVSCROLL | WS_VSCROLL
    yield _RESULT, _EDIT, "", _TABSTOP | 0x00200844, _CLIENTEDGE, (8, 130, 364, 87)
    yield _CLOSE, _BUTTON, "C&lose", _TABSTOP, 0, (307, 225, 65, 16)


def _wide(value: str) -> bytes:
    return (value + "\0").encode("utf-16-le")


def _dialog_template(style, title, width, height, controls, point_size=9) -> bytes:
    controls = list(controls)
    data = bytearray(struct.pack("<IIHhhhh", style, 0, len(controls), 0, 0, width, height))
    data.extend(bytes(4) + _wide(title) + struct.pack("<H", point_size) + _wide("Segoe UI"))
    for ident, kind, title, style, exstyle, bounds in controls:
        data.extend(b"\0" * (-len(data) % 4))  # DLGITEMTEMPLATE must start on a DWORD boundary.
        data.extend(struct.pack("<IIhhhhHHH", _WS_CHILD_VISIBLE | style, exstyle, *bounds, ident, 0xffff, kind))
        data.extend(_wide(title) + b"\0\0")
    return bytes(data)


class _OpenFileName(C.Structure):
    _fields_ = [
        ("lStructSize", W.DWORD), ("hwndOwner", W.HWND), ("hInstance", W.HINSTANCE),
        ("lpstrFilter", W.LPCWSTR), ("lpstrCustomFilter", W.LPWSTR), ("nMaxCustFilter", W.DWORD),
        ("nFilterIndex", W.DWORD), ("lpstrFile", W.LPWSTR), ("nMaxFile", W.DWORD),
        ("lpstrFileTitle", W.LPWSTR), ("nMaxFileTitle", W.DWORD), ("lpstrInitialDir", W.LPCWSTR),
        ("lpstrTitle", W.LPCWSTR), ("Flags", W.DWORD), ("nFileOffset", W.WORD),
        ("nFileExtension", W.WORD), ("lpstrDefExt", W.LPCWSTR), ("lCustData", W.LPARAM),
        ("lpfnHook", C.c_void_p), ("lpTemplateName", W.LPCWSTR), ("pvReserved", C.c_void_p),
        ("dwReserved", W.DWORD), ("FlagsEx", W.DWORD),
    ]


class _MinMaxInfo(C.Structure):
    _fields_ = [(name, W.POINT) for name in ("reserved", "max_size", "max_position", "min_track", "max_track")]


def _function(dll, name, result, *arguments):
    function = getattr(dll, name)
    function.argtypes = arguments
    function.restype = result
    return function


_user = C.WinDLL("user32", use_last_error=True)
_common = C.WinDLL("comdlg32", use_last_error=True)
_DialogProc = C.WINFUNCTYPE(C.c_ssize_t, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
_dialog_box = _function(_user, "DialogBoxIndirectParamW", C.c_ssize_t, W.HINSTANCE, C.c_void_p, W.HWND,
                        _DialogProc, W.LPARAM)
_end_dialog = _function(_user, "EndDialog", W.BOOL, W.HWND, C.c_ssize_t)
_item = _function(_user, "GetDlgItem", W.HWND, W.HWND, C.c_int)
_get_length = _function(_user, "GetWindowTextLengthW", C.c_int, W.HWND)
_get_text = _function(_user, "GetWindowTextW", C.c_int, W.HWND, W.LPWSTR, C.c_int)
_set_text = _function(_user, "SetDlgItemTextW", W.BOOL, W.HWND, C.c_int, W.LPCWSTR)
_send = _function(_user, "SendMessageW", W.LPARAM, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
_rect = _function(_user, "GetWindowRect", W.BOOL, W.HWND, C.POINTER(W.RECT))
_client = _function(_user, "GetClientRect", W.BOOL, W.HWND, C.POINTER(W.RECT))
_map = _function(_user, "MapDialogRect", W.BOOL, W.HWND, C.POINTER(W.RECT))
_move = _function(_user, "MoveWindow", W.BOOL, W.HWND, C.c_int, C.c_int, C.c_int, C.c_int, W.BOOL)
_open = _function(_common, "GetOpenFileNameW", W.BOOL, C.POINTER(_OpenFileName))
_picker_error = _function(_common, "CommDlgExtendedError", W.DWORD)
_module = _function(C.WinDLL("kernel32"), "GetModuleHandleW", W.HMODULE, W.LPCWSTR)


_create_dialog = _function(_user, "CreateDialogIndirectParamW", W.HWND, W.HINSTANCE, C.c_void_p, W.HWND,
                           _DialogProc, W.LPARAM)
_destroy = _function(_user, "DestroyWindow", W.BOOL, W.HWND)
_set_focus = _function(_user, "SetFocus", W.HWND, W.HWND)
_key_state = _function(_user, "GetKeyState", C.c_short, C.c_int)
_controls_dll = C.WinDLL("comctl32")
_ButtonProc = C.WINFUNCTYPE(W.LPARAM, W.HWND, W.UINT, W.WPARAM, W.LPARAM, C.c_size_t, C.c_size_t)
_subclass = _function(_controls_dll, "SetWindowSubclass", W.BOOL, W.HWND, _ButtonProc, C.c_size_t, C.c_size_t)
_remove_subclass = _function(_controls_dll, "RemoveWindowSubclass", W.BOOL, W.HWND, _ButtonProc, C.c_size_t)
_default_subclass = _function(_controls_dll, "DefSubclassProc", W.LPARAM, W.HWND, W.UINT, W.WPARAM, W.LPARAM)


def _path_value(hwnd, ident):
    field = _item(hwnd, ident)
    value = C.create_unicode_buffer(_get_length(field) + 1)
    _get_text(field, value, len(value))
    return value.value


def _choose_path(hwnd, field, title):
    buffer = C.create_unicode_buffer(32768)
    picker = _OpenFileName()
    picker.lStructSize = C.sizeof(picker)
    picker.hwndOwner = hwnd
    picker.lpstrTitle = title
    types = ("Transcript files\0*.txt;*.md;*.csv;*.docx;*.rtf;*.pdf" if field == 111 else "CSV files\0*.csv")
    picker.lpstrFilter = types + "\0All files\0*.*\0\0"
    picker.nFilterIndex = 1
    picker.lpstrFile = C.cast(buffer, W.LPWSTR)
    picker.nMaxFile = len(buffer)
    # Explorer dialog, existing file and path, no working-directory change or Recent Files entry.
    picker.Flags = 0x0208180C
    if _open(C.byref(picker)):
        return buffer.value
    error = _picker_error()
    if error:
        raise OSError(f"Windows could not open the file chooser (0x{error:04X}).")
    return None


def _result(hwnd, text, *, focus):
    # WM_SETTEXT uses a NUL-terminated string. Preserve the rest of a report
    # containing an embedded NUL, which can occur in an imported review reason.
    if "\0" in text:
        text = text.replace("\0", "[U+0000]") + "\n\nDisplay note: A null character is shown as [U+0000]."
    # Multiline EDIT uses CRLF. The verifier's source text and hashes stay untouched.
    _set_text(hwnd, _RESULT, text.replace("\r\n", "\n").replace("\n", "\r\n"))
    if focus:
        field = _item(hwnd, _RESULT)
        _send(hwnd, 0x0028, field, 1)  # WM_NEXTDLGCTL updates keyboard focus in the dialog manager.
        _send(field, 0x00B1, 0, 0)  # EM_SETSEL
        _send(field, 0x00B7, 0, 0)  # EM_SCROLLCARET


def _resize(hwnd, dx, dy):
    for ident, kind, _, _, _, (x, y, width, height) in _controls():
        r = W.RECT(x, y, x + width, y + height)
        _map(hwnd, C.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        if kind == _EDIT or ident == 100:
            w += dx
        if ident == _RESULT:
            h += dy
        if kind == _BUTTON and ident != _VERIFY:
            r.left += dx
        if ident == _CLOSE:
            r.top += dy
        _move(_item(hwnd, ident), r.left, r.top, w, h, True)


def show_windows_handoff(parent_hwnd: int, verifier=verify_handoff) -> None:
    """Run the modal recipient check. A zero owner also works for standalone use."""
    initial_client = W.RECT()
    minimum = W.RECT()
    initialized = False
    errors = []

    @_DialogProc
    def dialog_proc(hwnd, message, wparam, lparam):
        nonlocal initialized
        try:
            if message == 0x0110:  # WM_INITDIALOG
                _client(hwnd, C.byref(initial_client))
                _rect(hwnd, C.byref(minimum))
                initialized = True
                return True  # Dialog manager focuses the first path.
            if message == 0x0024 and initialized:  # WM_GETMINMAXINFO
                sizes = C.cast(lparam, C.POINTER(_MinMaxInfo)).contents
                sizes.min_track = W.POINT(minimum.right - minimum.left, minimum.bottom - minimum.top)
                return True
            if message == 0x0005 and initialized and wparam != 1:  # WM_SIZE, except minimized
                _resize(hwnd, (lparam & 0xffff) - initial_client.right,
                        ((lparam >> 16) & 0xffff) - initial_client.bottom)
                return True
            if message == 0x0010:  # WM_CLOSE
                _end_dialog(hwnd, _CLOSE)
                return True
            if message != 0x0111:  # WM_COMMAND
                return False
            ident, notification = wparam & 0xffff, wparam >> 16
            if notification == 0x0300 and any(ident == field for field, *_ in _FIELDS):  # EN_CHANGE
                _result(hwnd, "Choose Verify files to check this selection.", focus=False)
                return True
            if notification != 0:
                return False
            if ident == _CLOSE:
                _end_dialog(hwnd, _CLOSE)
                return True
            if ident == _VERIFY:
                values = [_path_value(hwnd, field) for field, *_ in _FIELDS]
                if not all(values):
                    message_text = "Verification needs all three files. Choose the evidence CSV, transcript and codebook CSV."
                else:
                    try:
                        message_text = verifier(*(Path(value) for value in values))
                    except (OSError, ValueError) as exc:
                        message_text = f"Verification failed.\n\n{exc}"
                _result(hwnd, message_text, focus=True)
                return True
            for field, browse, _, _, title in _FIELDS:
                if ident == browse:
                    try:
                        chosen = _choose_path(hwnd, field, title)
                    except OSError as exc:
                        _result(hwnd, str(exc), focus=True)
                    else:
                        if chosen:
                            _set_text(hwnd, field, chosen)
                    return True
            return False
        except Exception as exc:
            # ctypes cannot propagate callback exceptions through the Windows message loop.
            errors.append(exc)
            _end_dialog(hwnd, _CLOSE)
            return True

    # WS_POPUP | WS_CAPTION | WS_SYSMENU | WS_THICKFRAME and DS_MODALFRAME | DS_CENTER | DS_SETFONT.
    template = C.create_string_buffer(_dialog_template(0x80CC08C0, "Verify handoff", _WIDTH, _HEIGHT, _controls()))
    answer = _dialog_box(_module(None), template, parent_hwnd, dialog_proc, 0)
    if errors:
        raise errors[0]
    if answer == -1:
        raise C.WinError(C.get_last_error())


def mount_handoff_button(frame, activate) -> None:
    mount_workspace_button(frame, activate, "Verify handoff…", _LAUNCH)


def mount_workspace_button(frame, activate, label: str, control_id: int) -> None:
    """Host the workspace's native push button in a dedicated Tk toolbar frame."""
    # A modeless child dialog receives the button's notifications on the Tk UI thread.
    # DS_CONTROL | DS_SETFONT supplies the font and dialog-unit sizing.
    width, height = 66, 19
    controls = [(control_id, _BUTTON, label, _TABSTOP, 0, (0, 0, width, height))]
    template = C.create_string_buffer(_dialog_template(_WS_CHILD_VISIBLE | 0x440, "", width, height, controls, 10))
    host = None
    pending = None
    timer = None

    def dispatch():
        nonlocal pending, timer
        action, pending = pending, None
        timer = None
        try:
            if action == "activate":
                activate()
            elif action:
                target = frame.tk_focusPrev() if action == "previous" else frame.tk_focusNext()
                _set_focus(frame.winfo_id())
                target.focus_set()
        finally:
            if host:
                timer = frame.after(50, dispatch)

    # Win32 callbacks can run inside a Tcl event dispatch. Entering Tk from
    # that stack can corrupt the Python/Tcl callback state. Deliver requests
    # on Tk's own loop, where opening the modal and changing Tk focus are safe.
    @_DialogProc
    def host_proc(hwnd, message, wparam, lparam):
        nonlocal pending, host
        if message == 0x0111 and wparam == control_id and lparam:  # WM_COMMAND, BN_CLICKED
            pending = "activate"
            return True
        if message == 0x0082:  # WM_NCDESTROY
            host = None
        return False

    @_ButtonProc
    def button_proc(hwnd, message, key, lparam, subclass_id, _data):
        nonlocal pending
        # Tk owns the outer message loop. Bridge only traversal and Return on this
        # one button; the system button handles Space, clicks, drawing and UIA.
        if message == 0x0100:  # WM_KEYDOWN
            if key == 0x09:  # Tab
                pending = "previous" if _key_state(0x10) < 0 else "next"
                return 0
            if key == 0x0D:  # Return
                if not lparam & (1 << 30):
                    _send(hwnd, 0x00F5, 0, 0)  # BM_CLICK
                return 0
        if message == 0x0102 and key in (0x09, 0x0D):  # WM_CHAR
            return 0
        if message == 0x0082:  # WM_NCDESTROY
            _remove_subclass(hwnd, button_proc, subclass_id)
        return _default_subclass(hwnd, message, key, lparam)

    host = _create_dialog(_module(None), template, frame.winfo_id(), host_proc, 0)
    if not host:
        raise C.WinError(C.get_last_error())
    button = _item(host, control_id)
    if not _subclass(button, button_proc, control_id, 0):
        _destroy(host)
        raise OSError("Windows could not initialize keyboard navigation for the workspace button.")

    def resize(event):
        if host:
            _move(host, 0, 0, event.width, event.height, True)
            _move(button, 0, 0, event.width, event.height, True)

    def destroy(_event):
        if timer is not None:
            frame.after_cancel(timer)
        if host:
            _destroy(host)
        frame._native_button_callbacks = None

    # ctypes callbacks must stay alive until DestroyWindow finishes with both HWNDs.
    frame._native_button_callbacks = (host_proc, button_proc)
    frame.bind("<Destroy>", destroy, add="+")
    frame.bind("<Configure>", resize, add="+")
    frame.bind("<FocusIn>", lambda _event: _set_focus(button) if host else None, add="+")
    bounds = W.RECT()
    _client(host, C.byref(bounds))
    frame.configure(width=bounds.right, height=bounds.bottom, takefocus=True)
    timer = frame.after(50, dispatch)
