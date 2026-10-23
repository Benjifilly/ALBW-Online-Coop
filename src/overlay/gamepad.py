"""SDL controller input, shared by PlayStation, Switch and Xbox pads.

Only the companion reads these states; it never sends synthetic input to Azahar.
"""
import os
import time

BUTTONS = ("south", "east", "west", "north", "back", "guide", "start",
           "leftstick", "rightstick", "leftshoulder", "rightshoulder",
           "up", "down", "left", "right", "misc", "paddle1", "paddle2",
           "paddle3", "paddle4", "touchpad")
DEFAULT_BINDING = ["leftstick", "rightstick"]
VALID_BUTTONS = set(BUTTONS) | {"lefttrigger", "righttrigger"}


def binding_label(binding, device_name=""):
    name = device_name.lower()
    switch = any(t in name for t in ("switch", "nintendo", "joy-con"))
    labels = dict(zip(BUTTONS, ("Croix / B" if switch else "Croix / A", "Rond / A" if switch else "Rond / B",
        "Carré / Y" if switch else "Carré / X", "Triangle / X" if switch else "Triangle / Y",
        "−" if switch else "Share", "Home / PS", "+" if switch else "Options",
        "L3", "R3", "L" if switch else "L1", "R" if switch else "R1",
        "↑", "↓", "←", "→", "Capture", "Palette 1", "Palette 2", "Palette 3", "Palette 4", "Pavé tactile")))
    labels.update(lefttrigger="ZL" if switch else "L2", righttrigger="ZR" if switch else "R2")
    return " + ".join(labels.get(key, key) for key in binding) if binding else "Désactivé"


class ShortcutState:
    """Edges are per controller, so buttons from two pads never form a chord."""
    def __init__(self, binding=None):
        self.binding = list(binding if binding is not None else DEFAULT_BINDING)
        self.previous = {}
        self.recording = False
        self.deadline = 0
        self.candidate = frozenset()
        self.capture_id = None
        self.wait_release = False

    def begin_capture(self, now):
        self.recording = True
        self.deadline = now + 12
        self.candidate = frozenset()
        self.capture_id = None
        self.wait_release = True

    def cancel_capture(self):
        self.recording = False

    def update(self, devices, active, now):
        """Return (shortcut pressed, captured binding, capture timed out)."""
        pressed, captured, timed_out = False, None, False
        keys = {key: frozenset(value) & VALID_BUTTONS for key, value in devices.items()}
        if self.recording:
            if now >= self.deadline or not active:
                self.recording = False
                timed_out = True
            elif self.wait_release:
                self.wait_release = any(keys.values())
            else:
                if self.capture_id is None:
                    self.capture_id = next((key for key, value in keys.items() if value), None)
                if self.capture_id is not None:
                    if self.capture_id not in keys:
                        self.recording = False
                        timed_out = True
                    else:
                        down = keys[self.capture_id]
                        if len(down) > len(self.candidate):
                            self.candidate = down
                        if not down and self.candidate:
                            captured = sorted(self.candidate)
                            self.binding = captured
                            self.recording = False
        elif active and self.binding:
            chord = set(self.binding)
            pressed = any(chord <= down and not chord <= self.previous.get(key, down)
                          for key, down in keys.items())
        self.previous = keys
        return pressed, captured, timed_out


class SDLGamepads:
    def __init__(self):
        self.devices = {}
        self.error = ""
        self._next_scan = 0
        self.pg = self.api = None
        try:
            os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
            os.environ["SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"] = "1"
            import pygame
            from pygame._sdl2 import controller
            self.pg, self.api = pygame, controller
            # No SDL window or audio device is created: Qt owns the interface.
            pygame.display.init()
            pygame.joystick.init()
            controller.init()
        except Exception as exc:
            self.error = str(exc)

    def read(self):
        result = {}
        if not self.api or self.error:
            return result
        try:
            self.pg.event.pump()
            self.pg.event.clear()
            now = time.monotonic()
            for ident, pad in list(self.devices.items()):
                if not pad.attached():
                    pad.quit()
                    del self.devices[ident]
            if now >= self._next_scan:
                self._next_scan = now + 1.5
                for index in range(self.api.get_count()):
                    if self.api.is_controller(index):
                        joystick = self.pg.joystick.Joystick(index)
                        ident = joystick.get_instance_id()
                        if ident not in self.devices:
                            self.devices[ident] = self.api.Controller(index)
            for ident, pad in self.devices.items():
                buttons = {name for index, name in enumerate(BUTTONS) if pad.get_button(index)}
                if pad.get_axis(4) > 16000:
                    buttons.add("lefttrigger")
                if pad.get_axis(5) > 16000:
                    buttons.add("righttrigger")
                result[ident] = (pad.name, frozenset(buttons))
        except self.pg.error:
            # Bluetooth disconnects can happen between attached() and get_button().
            self._next_scan = 0
        return result

    def close(self):
        for pad in self.devices.values():
            pad.quit()
        self.devices.clear()
        if self.api:
            self.api.quit()
            self.pg.joystick.quit()
            self.pg.display.quit()
