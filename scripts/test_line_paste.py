"""測試 WM_CHAR 在游標中間位置插入文字 — LINE / Anti / Chrome"""
import sys, time, ctypes, ctypes.wintypes, comtypes
import uiautomation as auto
sys.stdout.reconfigure(encoding='utf-8')
comtypes.CoInitialize()
user32 = ctypes.windll.user32

WM_CHAR = 0x0102
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1
VK_BACK = 0x08
VK_LCONTROL = 0xA2
VK_A = 0x41
VK_HOME = 0x24
VK_RIGHT = 0x27

class KEYBDINPUT(ctypes.Structure):
    _fields_ = (('wVk', ctypes.c_ushort), ('wScan', ctypes.c_ushort),
                ('dwFlags', ctypes.c_ulong), ('time', ctypes.c_ulong),
                ('dwExtraInfo', ctypes.c_void_p))
class MOUSEINPUT(ctypes.Structure):
    _fields_ = (('dx', ctypes.c_long), ('dy', ctypes.c_long),
                ('mouseData', ctypes.c_ulong), ('dwFlags', ctypes.c_ulong),
                ('time', ctypes.c_ulong), ('dwExtraInfo', ctypes.c_void_p))
class HARDWAREINPUT(ctypes.Structure):
    _fields_ = (('uMsg', ctypes.c_ulong), ('wParamL', ctypes.c_ushort),
                ('wParamH', ctypes.c_ushort))
class INPUTUNION(ctypes.Union):
    _fields_ = (('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('hi', HARDWAREINPUT))
class INPUT(ctypes.Structure):
    _anonymous_ = ('u',)
    _fields_ = (('type', ctypes.c_ulong), ('u', INPUTUNION))

def send_unicode(text):
    data = text.encode('utf-16-le', 'surrogatepass')
    units = [data[i] | (data[i+1] << 8) for i in range(0, len(data), 2)]
    n = len(units) * 2
    inputs = (INPUT * n)()
    for idx, unit in enumerate(units):
        inputs[idx*2].type = INPUT_KEYBOARD
        inputs[idx*2].ki.wScan = unit
        inputs[idx*2].ki.dwFlags = KEYEVENTF_UNICODE
        inputs[idx*2+1].type = INPUT_KEYBOARD
        inputs[idx*2+1].ki.wScan = unit
        inputs[idx*2+1].ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
    user32.SendInput(n, inputs, ctypes.sizeof(INPUT))

def post_wm_char(text):
    hwnd = user32.GetForegroundWindow()
    for ch in text:
        cp = ord(ch)
        if cp > 0xFFFF:
            hi = 0xD800 + ((cp - 0x10000) >> 10)
            lo = 0xDC00 + ((cp - 0x10000) & 0x3FF)
            user32.PostMessageW(hwnd, WM_CHAR, hi, 0)
            user32.PostMessageW(hwnd, WM_CHAR, lo, 0)
        else:
            user32.PostMessageW(hwnd, WM_CHAR, cp, 0)

def key_tap(vk):
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.05)

def clear():
    user32.keybd_event(VK_LCONTROL, 0, 0, 0)
    time.sleep(0.02)
    user32.keybd_event(VK_A, 0, 0, 0)
    user32.keybd_event(VK_A, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.02)
    user32.keybd_event(VK_LCONTROL, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.1)
    user32.keybd_event(VK_BACK, 0, 0, 0)
    user32.keybd_event(VK_BACK, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.3)

def move_cursor_to_middle(text_len):
    """Home 移到開頭，然後右箭頭走到中間"""
    key_tap(VK_HOME)
    time.sleep(0.1)
    mid = text_len // 2
    for _ in range(mid):
        key_tap(VK_RIGHT)
    time.sleep(0.1)
    return mid

def read_focused():
    try:
        f = auto.GetFocusedControl()
        if not f:
            return "(no focus)"
        vp = getattr(f, "GetValuePattern", None)
        if callable(vp):
            try:
                p = vp()
                if p and p.Value is not None:
                    return p.Value
            except:
                pass
        return "(unreadable)"
    except:
        return "(error)"

def get_fg_proc():
    hwnd = user32.GetForegroundWindow()
    pid = ctypes.wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    buf = ctypes.create_unicode_buffer(260)
    h = ctypes.windll.kernel32.OpenProcess(0x0410, False, pid.value)
    if h:
        ctypes.windll.psapi.GetModuleFileNameExW(h, None, buf, 260)
        ctypes.windll.kernel32.CloseHandle(h)
    return buf.value.split("\\")[-1] if buf.value else "unknown"

INITIAL = "你好世界測試文字"  # 8 字
INSERT = "【插入】"  # 4 字
# 游標移到 4 個字後（中間），插入後預期：你好世界【插入】測試文字
EXPECTED = INITIAL[:4] + INSERT + INITIAL[4:]

def run_test(app_name):
    print(f"\n{'='*60}")
    print(f"測試: {app_name} — 游標在中間插入")
    print(f"請在 7 秒內點擊 {app_name} 的輸入框...")
    for i in range(7, 0, -1):
        print(f"  {i}...")
        time.sleep(1)

    proc = get_fg_proc()
    print(f"  前景: {proc}")
    print(f"  初始文字: {INITIAL!r}")
    print(f"  插入文字: {INSERT!r}")
    print(f"  預期結果: {EXPECTED!r}")

    # 先清空輸入段1
    clear()
    time.sleep(0.2)

    # 用 SendInput 輸入初始文字
    send_unicode(INITIAL)
    time.sleep(0.5)
    after_init = read_focused()
    print(f"  輸入後: {after_init!r}")

    # 移動游標到中間（第4字後）
    mid = move_cursor_to_middle(len(INITIAL))
    print(f"  游標移到位置 {mid}")
    time.sleep(0.3)

    # 用 WM_CHAR 在中間插入
    post_wm_char(INSERT)
    time.sleep(0.5)
    result = read_focused()
    ok = result == EXPECTED
    tag = "PASS" if ok else "FAIL"
    print(f"  結果: {result!r}")
    print(f"  [{tag}]")
    if not ok:
        print(f"  預期: {EXPECTED!r}")

    clear()
    return ok

ok = run_test("Anti")
print(f"\nAnti: {'PASS' if ok else 'FAIL'}")
