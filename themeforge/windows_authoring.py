"""Windows coding review controls for the open workspace."""
from __future__ import annotations

import ctypes as C
from collections import deque
from ctypes import wintypes as W

from . import windows_handoff as win


_CLOSE = 2  # IDCANCEL also handles Escape.
_IMPORT, _BOOK, _OPEN_PROJECT, _SAVE, _ANALYZE, _PASSAGE, _NEXT_CODE = range(201, 208)
_ANALYST, _SOURCE, _SPEAKER, _EXCERPT, _CODE, _DEFINITION, _REASON, _STATUS = range(210, 218)
_SUPPORT, _DECLINE, _EXPORT = range(220, 223)
_WIDTH, _HEIGHT = 396, 346
_WAKE = 0x8001  # An application message wakes GetMessage after a synchronous UIA invocation.

_ACTIONS = {
    _IMPORT: "import_source", _BOOK: "import_codebook", _OPEN_PROJECT: "open_project",
    _SAVE: "save_project", _ANALYZE: "analyze", _PASSAGE: "next_passage", _NEXT_CODE: "next_code",
    _ANALYST: "set_analyst", _SUPPORT: "support", _DECLINE: "decline", _EXPORT: "export",
}
_WORKSPACE_ACTIONS = {_IMPORT, _BOOK, _OPEN_PROJECT, _SAVE, _ANALYZE, _EXPORT}
_READ_ONLY = 0x0800
_MULTILINE = 0x00200044  # WS_VSCROLL | ES_AUTOVSCROLL | ES_MULTILINE


def _controls():
    def button(ident, label, x, y, width):
        return ident, win._BUTTON, label, win._TABSTOP, 0, (x, y, width, 16)

    def label(ident, title, x, y, width):
        return ident, win._STATIC, title, 0, 0, (x, y, width, 10)

    def field(ident, x, y, width, height=14, *, multiline=False, editable=False):
        style = _MULTILINE if multiline else 0x80  # ES_AUTOHSCROLL on a single line.
        if not editable:
            style |= _READ_ONLY
        elif multiline:
            style |= 0x1000  # ES_WANTRETURN accepts a paragraph break in a reason.
        return ident, win._EDIT, "", style | win._TABSTOP, win._CLIENTEDGE, (x, y, width, height)

    yield button(_IMPORT, "Import &transcript…", 7, 6, 90)
    yield button(_BOOK, "Import &codebook…", 102, 6, 90)
    yield button(_OPEN_PROJECT, "&Open project…", 197, 6, 93)
    yield button(_SAVE, "&Save project…", 295, 6, 94)
    yield label(301, "Anal&yst", 7, 32, 58)
    yield field(_ANALYST, 72, 29, 224, editable=True)
    yield button(_ANALYZE, "&Analyze", 302, 28, 87)
    yield label(302, "Source and location", 7, 52, 64)
    yield field(_SOURCE, 72, 49, 317)
    yield label(303, "Speaker", 7, 72, 60)
    yield field(_SPEAKER, 72, 69, 224)
    yield button(_PASSAGE, "&Next passage", 302, 68, 87)
    yield label(304, "Excer&pt", 7, 87, 120)
    yield field(_EXCERPT, 7, 98, 382, 32, multiline=True)
    yield label(305, "Code", 7, 139, 64)
    yield field(_CODE, 72, 136, 224)
    yield button(_NEXT_CODE, "Ne&xt code", 302, 135, 87)
    yield label(306, "Definition and criteria", 7, 155, 250)
    yield field(_DEFINITION, 7, 166, 382, 43, multiline=True)
    yield label(307, "Decision &reason", 7, 213, 240)
    yield field(_REASON, 7, 224, 382, 26, multiline=True, editable=True)
    yield button(_SUPPORT, "S&upport", 7, 254, 65)
    yield button(_DECLINE, "&Decline", 77, 254, 65)
    yield label(308, "Review status", 7, 274, 240)
    yield field(_STATUS, 7, 285, 382, 38, multiline=True)
    yield button(_EXPORT, "&Export scoped evidence…", 7, 326, 140)
    yield button(_CLOSE, "C&lose", 322, 326, 67)


_get_message = win._function(win._user, "GetMessageW", C.c_int, C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT)
_dialog_message = win._function(win._user, "IsDialogMessageW", W.BOOL, W.HWND, C.POINTER(W.MSG))
_translate = win._function(win._user, "TranslateMessage", W.BOOL, C.POINTER(W.MSG))
_dispatch = win._function(win._user, "DispatchMessageW", W.LPARAM, C.POINTER(W.MSG))
_post = win._function(win._user, "PostMessageW", W.BOOL, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
_post_quit = win._function(win._user, "PostQuitMessage", None, C.c_int)
_show = win._function(win._user, "ShowWindow", W.BOOL, W.HWND, C.c_int)
_active = win._function(win._user, "SetActiveWindow", W.HWND, W.HWND)
_enable = win._function(win._user, "EnableWindow", W.BOOL, W.HWND, W.BOOL)
_enabled = win._function(win._user, "IsWindowEnabled", W.BOOL, W.HWND)
_exists = win._function(win._user, "IsWindow", W.BOOL, W.HWND)
_focused = win._function(win._user, "GetFocus", W.HWND)


def _resize(hwnd, dx, dy):
    excerpt_growth, definition_growth = dy // 3, dy // 3
    for ident, kind, _, _, _, (x, y, w, h) in _controls():
        box = W.RECT(x, y, x + w, y + h)
        win._map(hwnd, C.byref(box))
        width, height = box.right - box.left, box.bottom - box.top
        if kind == win._EDIT:
            width += dx
        elif ident in (_SAVE, _ANALYZE, _PASSAGE, _NEXT_CODE, _CLOSE):
            box.left += dx
        if y >= 135:
            box.top += excerpt_growth
        if y >= 213:
            box.top += definition_growth
        if y >= 326:
            box.top += dy - excerpt_growth - definition_growth
        if ident == _EXCERPT:
            height += excerpt_growth
        elif ident == _DEFINITION:
            height += definition_growth
        elif ident == _STATUS:
            height += dy - excerpt_growth - definition_growth
        win._move(win._item(hwnd, ident), box.left, box.top, width, height, True)


def _display(hwnd, ident, value):
    # Windows EDIT uses a NUL-terminated string and CRLF for display.
    win._set_text(hwnd, ident, value.replace("\0", "[U+0000]").replace("\r\n", "\n").replace("\n", "\r\n"))


def show_windows_authoring(parent_hwnd: int, controller) -> None:
    """Review the current workspace. Controller operations run outside Win32 callbacks."""
    if not parent_hwnd or not _exists(parent_hwnd):
        raise ValueError("Coding review requires an open workspace.")
    pending = deque()
    initial, minimum = W.RECT(), W.RECT()
    errors = []
    rendering = False
    accepting = False
    reason_analyst = None

    @win._DialogProc
    def window_proc(hwnd, message, wparam, lparam):
        nonlocal reason_analyst
        try:
            if message == 0x0024 and minimum.right:  # WM_GETMINMAXINFO
                info = C.cast(lparam, C.POINTER(win._MinMaxInfo)).contents
                info.min_track = W.POINT(minimum.right - minimum.left, minimum.bottom - minimum.top)
                return True
            if message == 0x0005 and initial.right and wparam != 1:  # WM_SIZE
                _resize(hwnd, (lparam & 0xffff) - initial.right, (lparam >> 16 & 0xffff) - initial.bottom)
                return True
            if message == 0x0082:  # WM_NCDESTROY, including owner teardown.
                _post(None, _WAKE, 0, 0)
                return False
            if not accepting:
                return False
            if message == 0x0010:  # WM_CLOSE
                ident = _CLOSE
            elif message == 0x0111:  # WM_COMMAND
                ident, notice = wparam & 0xffff, wparam >> 16
                if ident == _REASON and notice == 0x0300 and not rendering:  # EN_CHANGE
                    reason_analyst = win._path_value(hwnd, _ANALYST).strip()
                    return False
                if ident == _ANALYST:
                    if notice == 0x0200 and reason_analyst == "":  # EN_KILLFOCUS after naming an analyst
                        reason_analyst = win._path_value(hwnd, _ANALYST).strip()
                    if rendering or notice != 0x0300:
                        return False
                elif notice or (ident != _CLOSE and ident not in _ACTIONS):
                    return False
            else:
                return False
            if not (ident == _ANALYST and pending and pending[-1] == _ANALYST):
                pending.append(ident)
                _post(hwnd, _WAKE, 0, 0)
            return True
        except Exception as exc:
            errors.append(exc)
            _post(hwnd, _WAKE, 0, 0)
            return True

    template = C.create_string_buffer(win._dialog_template(0x80CC08C0, "Coding review", _WIDTH, _HEIGHT, _controls()))
    previous_focus = _focused()
    owner_enabled = bool(_enabled(parent_hwnd))
    hwnd = win._create_dialog(win._module(None), template, parent_hwnd, window_proc, 0)
    if not hwnd:
        raise C.WinError(C.get_last_error())
    state = {}

    def render(new, *, clear_reason=False, error=""):
        nonlocal state, rendering, reason_analyst
        rendering = True
        try:
            # UIA can set a multiline EDIT using WM_SETTEXT without EN_CHANGE.
            if not win._get_length(win._item(hwnd, _REASON)):
                reason_analyst = None
            elif reason_analyst is None:
                reason_analyst = state.get("analyst", "").strip()
            different_analyst = bool(reason_analyst and reason_analyst != new["analyst"].strip())
            if clear_reason or different_analyst or any(new.get(k) != state.get(k) for k in ("target", "code")):
                win._set_text(hwnd, _REASON, "")
                reason_analyst = None
            if new.get("analyst") != state.get("analyst"):
                if win._path_value(hwnd, _ANALYST) != new["analyst"]:
                    _display(hwnd, _ANALYST, new["analyst"])
            for ident, name in ((_SOURCE, "source"), (_SPEAKER, "speaker"), (_EXCERPT, "excerpt"),
                                (_CODE, "code"), (_DEFINITION, "definition")):
                if new.get(name) != state.get(name):
                    _display(hwnd, ident, new[name])
            status = "\n\n".join(x for x in (new["review"], error or new["status"]) if x)
            if different_analyst:
                status += "\nDraft reason cleared because the analyst changed."
            if any("\0" in value for value in new.values()):
                status += "\nA null character is displayed as [U+0000]."
            _display(hwnd, _STATUS, status)
            state = new
        finally:
            rendering = False

    try:
        win._client(hwnd, C.byref(initial))
        win._rect(hwnd, C.byref(minimum))
        for ident in (_ANALYST, _REASON):
            win._send(win._item(hwnd, ident), 0x00C5, 0, 0)  # EM_SETLIMITTEXT, system maximum.
        render(controller.snapshot())
        if owner_enabled:
            _enable(parent_hwnd, False)
        _show(hwnd, 5)
        _active(hwnd)
        win._send(hwnd, 0x0028, win._item(hwnd, _IMPORT), 1)  # WM_NEXTDLGCTL
        accepting = True
        msg = W.MSG()
        while _exists(hwnd):
            got = _get_message(C.byref(msg), None, 0, 0)
            if got == -1:
                raise C.WinError(C.get_last_error())
            if got == 0:
                _post_quit(msg.wParam)
                break
            if not _dialog_message(hwnd, C.byref(msg)):
                _translate(C.byref(msg))
                _dispatch(C.byref(msg))
            if errors:
                raise errors[0]
            while pending and _exists(hwnd):
                ident = pending.popleft()
                if ident == _CLOSE:
                    return
                analyst = win._path_value(hwnd, _ANALYST)
                reason = win._path_value(hwnd, _REASON).replace("\r\n", "\n")
                suspended = ident in _WORKSPACE_ACTIONS
                if suspended:
                    accepting = False
                    _show(hwnd, 0)
                    if owner_enabled:
                        _enable(parent_hwnd, True)
                        _active(parent_hwnd)
                try:
                    recorded = controller.perform(_ACTIONS[ident], analyst=analyst, reason=reason)
                    render(controller.snapshot(), clear_reason=bool(recorded) and ident in (_SUPPORT, _DECLINE))
                except (ValueError, OSError) as exc:
                    render(controller.snapshot(), error=str(exc))
                finally:
                    if suspended and _exists(hwnd):
                        if owner_enabled:
                            _enable(parent_hwnd, False)
                        _show(hwnd, 5)
                        _active(hwnd)
                        win._send(hwnd, 0x0028, win._item(hwnd, ident), 1)
                        accepting = True
                if ident in (_SUPPORT, _DECLINE):
                    result_field = win._item(hwnd, _STATUS)
                    win._send(hwnd, 0x0028, result_field, 1)  # WM_NEXTDLGCTL
                    win._send(result_field, 0x00B1, 0, 0)  # EM_SETSEL
                    win._send(result_field, 0x00B7, 0, 0)  # EM_SCROLLCARET
    finally:
        accepting = False
        if owner_enabled and _exists(parent_hwnd):
            _enable(parent_hwnd, True)
        if _exists(hwnd):
            win._destroy(hwnd)
        if _exists(parent_hwnd):
            _active(parent_hwnd)
            if previous_focus and _exists(previous_focus):
                win._set_focus(previous_focus)
