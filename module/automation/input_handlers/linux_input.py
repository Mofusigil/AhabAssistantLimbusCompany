"""Linux 前台输入：X11 使用 pyautogui，Wayland 使用授权的桌面 portal。

游戏窗口仍经 XWayland 定位和截图。Wayland 下鼠标移动、按钮、滚轮和
键盘全部使用同一个 RemoteDesktop session，避免不同坐标空间混用。
"""

import os
import random
import time
from typing import overload

import pyperclip
from Xlib import display as xdisplay

from module.config import cfg
from module.logger import log
from module.platform_compat import is_wayland_session
from utils.singletonmeta import SingletonMeta

from ...game_and_screen import screen
from . import AbstractInput
from .scroll_swipe import build_windows_scroll_swipe_plan
from .wayland_portal import portal_input

if os.environ.get("DISPLAY"):
    import pyautogui

    pyautogui.FAILSAFE = False
else:
    pyautogui = None


def _use_pyautogui_mouse() -> bool:
    return bool(os.environ.get("DISPLAY")) and not is_wayland_session()


_DISPLAY = None


def _query_pointer() -> tuple[int, int]:
    global _DISPLAY
    if _DISPLAY is None:
        _DISPLAY = xdisplay.Display()
    pointer = _DISPLAY.screen().root.query_pointer()
    return int(pointer.root_x), int(pointer.root_y)


def _abs_move(x: int, y: int) -> bool:
    portal_input.move(int(x), int(y))
    return True


def _left_press() -> None:
    portal_input.button(True)


def _left_release() -> None:
    time.sleep(0.02)
    portal_input.button(False)


def _left_click() -> None:
    _left_press()
    try:
        time.sleep(0.02)
    finally:
        _left_release()


class LinuxInput(AbstractInput, metaclass=SingletonMeta):
    """Linux 仅支持前台操作。"""

    def prepare(self) -> None:
        """任务开始时申请授权，不在应用导入或每次点击时弹出窗口。"""
        if is_wayland_session():
            portal_input.prepare(screen.handle.input_monitors())
            # 授权窗口关闭后重新激活游戏，即使未启用自动调整窗口。
            screen.handle.setForeground()

    @staticmethod
    def _window_is_ready() -> bool:
        """窗口重建或冷启动期间不允许向桌面注入输入。"""
        try:
            left, top, right, bottom = screen.handle.rect(True)
        except Exception as error:
            log.debug(f"读取 Linux 游戏窗口区域失败，跳过输入: {error}")
            return False
        return right > left and bottom > top

    @overload
    def pos_offset(self, x: int, y: int) -> tuple[int, int]: ...
    @overload
    def pos_offset(self, pos: tuple[int, int]) -> tuple[int, int]: ...

    def pos_offset(self, *args) -> tuple[int, int]:
        if len(args) == 2:
            x, y = args
        elif isinstance(args[0], tuple):
            x, y = args[0]
        else:
            raise ValueError("pos_offset 接受两个整数参数或一个包含两个整数的元组")
        real_x, real_y, _, _ = screen.handle.rect(True)
        return x + real_x, y + real_y

    def get_mouse_position(self) -> tuple[int, int]:
        """XWayland 的指针位置仅用于操作后尽力恢复，不用于定位目标。"""
        try:
            if _use_pyautogui_mouse():
                pos = pyautogui.position()
                return int(pos.x), int(pos.y)
            return _query_pointer()
        except Exception:
            log.debug("获取鼠标位置失败，返回 (0, 0)")
            return (0, 0)

    def mouse_click(self, x, y, times=1, move_back=False) -> bool:
        if not self._window_is_ready():
            log.debug("Linux 游戏窗口尚未就绪，跳过鼠标点击")
            return False
        previous = self.get_mouse_position() if move_back else None
        log.debug(f"点击位置:({x},{y})", stacklevel=2)
        x, y = self.pos_offset(x, y)
        for _ in range(times):
            if _use_pyautogui_mouse():
                pyautogui.click(x, y)
            else:
                if not _abs_move(x, y):
                    log.debug(f"Linux 鼠标无法移动到目标位置 ({x}, {y})，跳过点击")
                    return False
                time.sleep(0.05)
                _left_click()
                time.sleep(0.05)
        if previous:
            self.mouse_move(previous)
        self.wait_pause()
        return True

    def _drag_path(self, plan, settle_duration=0, move_back=True) -> None:
        """起点移动成功后再按下，任何失败都保证释放已按下的按钮。"""
        if not self._window_is_ready() or not plan:
            return
        previous = self.get_mouse_position() if move_back else None
        use_pyautogui = _use_pyautogui_mouse()
        if use_pyautogui:
            pyautogui.moveTo(*plan[0][0])
            pyautogui.mouseDown()
        else:
            if not _abs_move(*plan[0][0]):
                return
            time.sleep(0.05)
            _left_press()
        try:
            for point, duration in plan[1:]:
                if use_pyautogui:
                    pyautogui.moveTo(*point, duration=duration)
                else:
                    if not _abs_move(*point):
                        return
                    time.sleep(duration)
            if settle_duration:
                time.sleep(settle_duration)
        finally:
            if use_pyautogui:
                pyautogui.mouseUp()
            else:
                _left_release()
        if previous:
            self.mouse_move(previous)

    def mouse_drag_down(self, x, y, reverse=1, move_back=True) -> None:
        distance = int(300 * cfg.set_win_size / 1080 * reverse)
        if _use_pyautogui_mouse():
            plan = [(self.pos_offset(x, y), 0), (self.pos_offset(x, y + distance), 0.4)]
            self._drag_path(plan, move_back=move_back)
            return
        plan = [(self.pos_offset(x, y + int(distance * step / 8)), 0.05) for step in range(9)]
        self._drag_path(plan, settle_duration=0.2, move_back=move_back)

    def mouse_drag(self, x, y, drag_time=0.1, dx=0, dy=0, move_back=True) -> None:
        if _use_pyautogui_mouse():
            plan = [(self.pos_offset(x, y), 0), (self.pos_offset(x + dx, y + dy), drag_time)]
            self._drag_path(plan, settle_duration=max(0.5, drag_time * 0.3), move_back=move_back)
            return
        steps = max(1, min(60, int(drag_time / 0.02)))
        plan = [
            (self.pos_offset(x + int(dx * step / steps), y + int(dy * step / steps)), drag_time / steps)
            for step in range(steps + 1)
        ]
        self._drag_path(plan, settle_duration=max(0.5, drag_time * 0.3), move_back=move_back)

    def mouse_swipe_for_scroll(self, x, y, duration=0.3, dx=0, dy=0, move_back=True) -> None:
        raw_plan, settle_duration = build_windows_scroll_swipe_plan(x, y, dx, dy, duration)
        plan = [(self.pos_offset(*point), move_duration) for point, move_duration in raw_plan]
        self._drag_path(plan, settle_duration=settle_duration, move_back=move_back)

    def mouse_scroll(self, direction: int = -3) -> bool:
        if not self._window_is_ready():
            return False
        log.debug("鼠标滚动滚轮，远离界面" if direction <= 0 else "鼠标滚动滚轮，拉近界面", stacklevel=2)
        if _use_pyautogui_mouse():
            pyautogui.scroll(direction)
        else:
            portal_input.scroll(direction)
        return True

    def mouse_click_blank(self, coordinate=(1, 1), times=1, move_back=False) -> bool:
        x = coordinate[0] + random.randint(0, 10)
        y = coordinate[1] + random.randint(0, 10)
        return self.mouse_click(x, y, times=times, move_back=move_back)

    def mouse_to_blank(self, coordinate=(1, 1), move_back=False) -> None:
        if not self._window_is_ready():
            return
        previous = self.get_mouse_position() if move_back else None
        log.debug("鼠标移动到空白，避免遮挡", stacklevel=2)
        self.mouse_move(self.pos_offset(*coordinate))
        if previous:
            self.mouse_move(previous)

    def mouse_move(self, coordinate=(1, 1)) -> None:
        """移动到屏幕绝对坐标。"""
        if _use_pyautogui_mouse():
            pyautogui.moveTo(int(coordinate[0]), int(coordinate[1]))
        else:
            _abs_move(int(coordinate[0]), int(coordinate[1]))
        self.wait_pause()

    def mouse_drag_link(self, position: list, drag_time=0.1, move_back=False) -> None:
        plan = [(self.pos_offset(*pos), drag_time) for pos in position]
        self._drag_path(plan, move_back=move_back)

    def key_press(self, key):
        if not self._window_is_ready():
            return
        if _use_pyautogui_mouse():
            return pyautogui.press(key)
        portal_input.hotkey(key)

    def input_text(self, text: str):
        if not text:
            log.warning("未提供要粘贴的文本")
            return
        if not self._window_is_ready():
            return
        try:
            pyperclip.copy(text)
        except Exception:
            if _use_pyautogui_mouse():
                pyautogui.typewrite(text)
            else:
                portal_input.type_text(text)
            return
        if _use_pyautogui_mouse():
            pyautogui.hotkey("ctrl", "v")
        else:
            portal_input.hotkey("ctrl", "v")
