from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

if not sys.platform.startswith("linux"):
    pytest.skip("Linux isolated X11 backend", allow_module_level=True)

from module import platform_compat
from module.automation.input_handlers import linux_input, wayland_backend
from module.game_and_screen import game as game_module
from module.game_and_screen import x11_handle as x11_module
from module.game_and_screen.x11_handle import X11Handle


def test_is_wayland_session_false_when_x11_explicit_even_with_wayland_display(monkeypatch) -> None:
    monkeypatch.setattr(platform_compat, "IS_LINUX", True)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")

    assert platform_compat.is_wayland_session() is False


def test_x11_handle_get_atom_value_skips_missing_atom() -> None:
    handle = object.__new__(X11Handle)
    win = SimpleNamespace(get_full_property=Mock(side_effect=AssertionError("不应在 atom=0 时查询属性")))
    handle._disp = SimpleNamespace(intern_atom=Mock(return_value=0))

    assert handle._get_atom_value(win, "_NET_FRAME_EXTENTS") is None
    win.get_full_property.assert_not_called()


def test_x11_handle_is_active_falls_back_to_input_focus() -> None:
    handle = object.__new__(X11Handle)
    handle._hwnd = 42
    root = SimpleNamespace(
        id=1,
        get_full_property=Mock(return_value=None),
    )
    win = SimpleNamespace(get_attributes=Mock())
    disp = SimpleNamespace(
        intern_atom=Mock(return_value=0),
        screen=Mock(return_value=SimpleNamespace(root=root)),
        get_input_focus=Mock(return_value=SimpleNamespace(focus=SimpleNamespace(id=42))),
        create_resource_object=Mock(return_value=win),
    )
    handle._disp = disp

    assert handle.isActive is True

    disp.get_input_focus.return_value = SimpleNamespace(focus=SimpleNamespace(id=99))
    assert handle.isActive is False


def test_x11_handle_set_foreground_sets_input_focus() -> None:
    handle = object.__new__(X11Handle)
    handle._hwnd = 42
    root = SimpleNamespace(id=1, send_event=Mock())
    win = SimpleNamespace(
        get_attributes=Mock(),
        configure=Mock(),
        set_input_focus=Mock(),
    )
    disp = SimpleNamespace(
        intern_atom=Mock(return_value=0),
        screen=Mock(return_value=SimpleNamespace(root=root)),
        create_resource_object=Mock(return_value=win),
        flush=Mock(),
    )
    handle._disp = disp

    handle.setForeground()

    win.configure.assert_called_once_with(stack_mode=x11_module.X.Above)
    win.set_input_focus.assert_called_once_with(x11_module.X.RevertToPointerRoot, x11_module.X.CurrentTime)
    disp.flush.assert_called()


def test_x11_handle_configure_window_clamps_bounds_and_restores_initial_size(monkeypatch) -> None:
    handle = object.__new__(X11Handle)
    handle._hwnd = 42
    handle._content_offset_cache = {}
    monkeypatch.setattr(x11_module, "cfg", SimpleNamespace(set_windows=True, set_win_size=1080))
    monkeypatch.setattr(x11_module.time, "sleep", lambda _sec: None)

    monkeypatch.setattr(handle, "restore", Mock())
    monkeypatch.setattr(handle, "setForeground", Mock())
    monkeypatch.setattr(handle, "set_topmost", Mock())
    monkeypatch.setattr(handle, "monitor_size", Mock(return_value=(1960, 1160)))
    # 初始已为 1920x1080，但位置偏出右边界，后续模拟始终未匹配到主界面
    monkeypatch.setattr(handle, "rect", Mock(return_value=(100, 100, 2020, 1180)))
    monkeypatch.setattr(handle, "width", Mock(return_value=1920))
    monkeypatch.setattr(handle, "height", Mock(return_value=1080))
    set_pos = Mock()
    set_size = Mock()
    monkeypatch.setattr(handle, "set_window_pos", set_pos)
    monkeypatch.setattr(handle, "set_window_size", set_size)
    monkeypatch.setattr(handle, "capture_window_image", Mock(return_value=object()))
    # 避开直接访问 _dpy
    monkeypatch.setattr(X11Handle, "isMinimized", property(lambda self: False))
    monkeypatch.setattr(X11Handle, "isActive", property(lambda self: True))

    handle.configure_window()

    set_pos.assert_called_with(0, 0)
    # 优先尝试 1080，未命中结束时恢复初始精确尺寸 1920x1080
    assert set_size.call_args_list[0].args == (1920, 1080)
    assert set_size.call_args_list[-1].args == (1920, 1080)


def test_linux_input_prepare_focuses_window_in_x11(monkeypatch) -> None:
    handler = object.__new__(linux_input.LinuxInput)
    monkeypatch.setattr(linux_input, "pyautogui", SimpleNamespace())
    handle = SimpleNamespace(setForeground=Mock(), input_monitors=Mock(return_value=[{"name": "eDP-1", "rect": (0, 0, 1920, 1080)}]))
    monkeypatch.setattr(linux_input.screen, "handle", handle)

    # Wayland 下返回 portal_backend
    portal = SimpleNamespace(prepare=Mock())
    monkeypatch.setattr(wayland_backend, "portal_backend", portal)
    monkeypatch.setattr(linux_input, "is_wayland_session", lambda: True)
    assert handler.backend is portal

    # X11 模式下 prepare() 会置前聚焦
    monkeypatch.setattr(linux_input, "is_wayland_session", lambda: False)
    handler.prepare()
    handle.setForeground.assert_called_once_with()


def test_game_start_uses_launch_script_when_configured(monkeypatch, tmp_path) -> None:
    script = tmp_path / "launch_limbus_isolated.sh"
    script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    monkeypatch.setattr(game_module, "IS_LINUX", True)
    monkeypatch.setattr(game_module, "IS_WINDOWS", False)
    monkeypatch.setattr(game_module, "sleep", lambda _sec: None)
    monkeypatch.setattr(
        game_module,
        "cfg",
        SimpleNamespace(game_path="/nonexistent/LimbusCompany.exe", game_process_name="LimbusCompany.exe"),
    )

    start_detached = Mock()
    open_uri = Mock()
    monkeypatch.setattr(game_module, "start_detached", start_detached)
    monkeypatch.setattr(game_module, "open_uri", open_uri)

    game = object.__new__(game_module.Game)
    game.game_path = "/nonexistent/LimbusCompany.exe"
    game.game_url = "steam://rungameid/1973530"
    game.log = SimpleNamespace(debug=Mock(), info=Mock(), error=Mock())
    game.process_name = "LimbusCompany.exe"
    game.game_path_exists = True
    game.last_check_time = None
    game.check_in_short_time = 0
    monkeypatch.setattr(game, "check_game_alive", lambda: False)

    monkeypatch.setenv("AALC_GAME_LAUNCH_SCRIPT", str(script))
    assert game.start_game() is True
    start_detached.assert_called_once_with([str(script)])
    open_uri.assert_not_called()

    start_detached.reset_mock()
    monkeypatch.delenv("AALC_GAME_LAUNCH_SCRIPT")
    assert game.start_game() is True
    open_uri.assert_called_once_with("steam://rungameid/1973530")
    start_detached.assert_not_called()


def test_x11_handle_resolve_content_offset_crops_assets_border(monkeypatch) -> None:
    import cv2
    from PIL import Image

    monkeypatch.setattr(x11_module, "cfg", SimpleNamespace(set_win_size=1080))
    tpl_raw = cv2.imread("assets/images/default/share/home/drive_assets.png", cv2.IMREAD_GRAYSCALE)
    assert tpl_raw is not None
    scaled = cv2.resize(tpl_raw, (1920, 1080), interpolation=cv2.INTER_AREA)
    img = Image.fromarray(scaled)

    handle = object.__new__(X11Handle)
    handle._content_offset_cache = {}
    assert handle._resolve_content_offset(1920, 1080, img) == 0
    assert handle._content_offset_cache.get((1920, 1080)) == 0

