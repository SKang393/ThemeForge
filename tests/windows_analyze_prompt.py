"""Read and answer Tk's native Windows Analyze message box on the test's GUI thread."""
import ctypes
from ctypes import wintypes


def answer_analyze_prompt(open_prompt, accept, screen):
    user = ctypes.WinDLL("user32", use_last_error=True)
    for function in (user.GetActiveWindow, user.GetFocus, user.FindWindowExW):
        function.restype = wintypes.HWND
    user.SetTimer.restype = ctypes.c_size_t
    timer_proc = ctypes.WINFUNCTYPE(None, wintypes.HWND, wintypes.UINT, ctypes.c_size_t, wintypes.DWORD)
    observed = []
    errors = []

    def window_text(hwnd):
        buffer = ctypes.create_unicode_buffer(1024)
        user.GetWindowTextW(wintypes.HWND(hwnd), buffer, len(buffer))
        return buffer.value

    @timer_proc
    def inspect(_hwnd, _message, _timer, _time):
        # A native modal pumps Windows messages while the Tk button invocation is blocked.
        dialog = wintypes.HWND(user.GetActiveWindow())
        window_class = ctypes.create_unicode_buffer(64)
        user.GetClassNameW(dialog, window_class, len(window_class))
        if window_class.value != "#32770" or observed or errors:
            return
        response = 2  # IDCANCEL on an unexpected prompt or inspection failure.
        try:
            title = window_text(dialog.value)
            focus = window_text(user.GetFocus()).replace("&", "")
            labels = []
            child = None
            while child := user.FindWindowExW(dialog, wintypes.HWND(child), "Static", None):
                labels.append(window_text(child))
            rect = wintypes.RECT()
            if not user.GetWindowRect(dialog, ctypes.byref(rect)):
                raise ctypes.WinError(ctypes.get_last_error())
            observed.append((title, focus, " ".join(" ".join(labels).split()), rect))
            if title == "Start a new review pass?" and accept:
                response = 1  # IDOK
        except Exception as exc:
            errors.append(exc)
        finally:
            user.PostMessageW(dialog, 0x0111, response, 0)  # WM_COMMAND

    timer = user.SetTimer(None, 0, 100, inspect)
    if not timer:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        open_prompt()
    finally:
        user.KillTimer(None, ctypes.c_size_t(timer))
    if errors:
        raise errors[0]
    assert len(observed) == 1, "Analyze did not show its native confirmation"
    title, focus, text, rect = observed[0]
    assert title == "Start a new review pass?", title
    assert focus == "Cancel", f"Initially focused control: {focus!r}"
    for phrase in ("fresh pass for all coders", "Earlier decisions stay in Rigor tools → Audit log",
                   "supported evidence table will be empty until passages are supported again",
                   "Choose Cancel to continue this pass in Quote review", "Choose OK to run Analyze"):
        assert phrase in text, text
    assert 0 <= rect.left < rect.right <= screen[0] and 0 <= rect.top < rect.bottom <= screen[1], tuple(screen)
    print(f"Windows Analyze prompt on {screen[0]}x{screen[1]} at "
          f"({rect.left},{rect.top})–({rect.right},{rect.bottom}); focus {focus}, chose {'OK' if accept else 'Cancel'}.")
