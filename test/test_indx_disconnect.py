from klippy.extras.indx.disconnect import IndxMcuDisconnect


class _FakeMutex:
    def __init__(self, held=False):
        self._held = held

    def test(self):
        return self._held


class _FakeReactor:
    NOW = 0.0
    NEVER = 9e99

    def __init__(self):
        self.timers = []

    def monotonic(self):
        return 0.0

    def register_timer(self, callback, waketime=None):
        self.timers.append((callback, waketime))
        return object()

    def update_timer(self, timer, waketime):
        pass


class _FakeGCode:
    def __init__(self, mutex_held=False):
        self.messages = []
        self._mutex = _FakeMutex(mutex_held)

    def get_mutex(self):
        return self._mutex

    def respond_info(self, msg):
        self.messages.append(msg)


class _FakePauseResume:
    def __init__(self, sd_active=True, paused=False):
        self._sd_active = sd_active
        self.is_paused = paused
        self.pause_command_sent = False
        self.pause_calls = 0
        self.block_resume_reason = None

    def is_sd_active(self):
        return self._sd_active

    def send_pause_command(self):
        self.pause_calls += 1
        self.pause_command_sent = True


class _FakeGCodeMove:
    def __init__(self):
        self.absolute_coord = True
        self.absolute_extrude = True
        self.base_position = [0.0, 0.0, 0.0, 0.0]
        self.last_position = [10.0, 20.0, 0.4, 5.0]
        self.homing_position = [0.0, 0.0, 0.0, 0.0]
        self.speed = 100.0
        self.speed_factor = 1.0 / 60.0
        self.extrude_factor = 1.0
        self.saved_states = {}


class _FakeHeater:
    def __init__(self):
        self.last_report = ("stale",)
        self.cmd_set_control_params = None


class _FakeMcu:
    def __init__(self):
        self.non_critical_disconnected = False
        self.enabled_non_critical = False

    def get_name(self):
        return "indxmcu"

    def get_non_critical_disconnect_event_name(self):
        return "danger:non_critical_mcu_indxmcu:disconnected"

    def get_non_critical_reconnect_event_name(self):
        return "danger:non_critical_mcu_indxmcu:reconnected"

    def enable_non_critical(self):
        self.enabled_non_critical = True


class _FakePrinter:
    def __init__(self, pause_resume, gcode, gcode_move, reactor):
        self._objects = {
            "pause_resume": pause_resume,
            "gcode": gcode,
            "gcode_move": gcode_move,
            "idle_timeout": type(
                "Idle",
                (),
                {"get_status": lambda self, t: {"state": "Printing"}},
            )(),
        }
        self.event_handlers = {}
        self._reactor = reactor

    def get_reactor(self):
        return self._reactor

    def lookup_object(self, name, default=None):
        return self._objects.get(name, default)

    def load_object(self, config, name):
        return self._objects[name]

    def register_event_handler(self, event, callback):
        self.event_handlers.setdefault(event, []).append(callback)


class _FakeConfig:
    def __init__(self, printer):
        self._printer = printer

    def get_printer(self):
        return self._printer

    def getboolean(self, name, default):
        return default

    def getfloat(self, name, default, **kwargs):
        return default


class _FakeToolboard:
    def __init__(self, printer, mcu, heater):
        self.printer = printer
        self.mcu = mcu
        self.heater = heater


def _make_handler(sd_active=True, paused=False, mutex_held=False):
    reactor = _FakeReactor()
    pause_resume = _FakePauseResume(sd_active=sd_active, paused=paused)
    gcode = _FakeGCode(mutex_held=mutex_held)
    gcode_move = _FakeGCodeMove()
    printer = _FakePrinter(pause_resume, gcode, gcode_move, reactor)
    mcu = _FakeMcu()
    heater = _FakeHeater()
    toolboard = _FakeToolboard(printer, mcu, heater)
    handler = IndxMcuDisconnect(toolboard, _FakeConfig(printer))
    return handler, printer, pause_resume, gcode_move, mcu, heater, gcode


def test_ready_enables_non_critical_mcu():
    handler, printer, *_rest = _make_handler()
    mcu = handler.mcu
    ready = printer.event_handlers["klippy:ready"]
    for cb in ready:
        cb()
    assert mcu.enabled_non_critical is True


def test_disconnect_pauses_without_retract_macro():
    handler, printer, pause_resume, gcode_move, mcu, heater, gcode = (
        _make_handler()
    )
    disconnect = printer.event_handlers[
        mcu.get_non_critical_disconnect_event_name()
    ]
    for cb in disconnect:
        cb()
    assert pause_resume.pause_calls == 1
    assert pause_resume.is_paused is True
    assert pause_resume.block_resume_reason is not None
    assert "PAUSE_STATE" in gcode_move.saved_states
    assert gcode_move.saved_states["PAUSE_STATE"]["last_position"][2] == 0.4
    assert heater.last_report is None
    assert handler._waiting_for_reconnect is True
    assert gcode.messages == []


def test_disconnect_skips_pause_when_idle():
    handler, printer, pause_resume, gcode_move, mcu, heater, gcode = (
        _make_handler(sd_active=False)
    )
    printer._objects["idle_timeout"] = type(
        "Idle", (), {"get_status": lambda self, t: {"state": "Idle"}}
    )()
    disconnect = printer.event_handlers[
        mcu.get_non_critical_disconnect_event_name()
    ]
    for cb in disconnect:
        cb()
    assert pause_resume.pause_calls == 0
    assert pause_resume.is_paused is False
    assert "PAUSE_STATE" not in gcode_move.saved_states


def test_reconnect_asks_for_manual_resume():
    handler, printer, pause_resume, gcode_move, mcu, heater, gcode = (
        _make_handler()
    )
    for cb in printer.event_handlers[
        mcu.get_non_critical_disconnect_event_name()
    ]:
        cb()
    pause_resume.is_paused = True
    for cb in printer.event_handlers[
        mcu.get_non_critical_reconnect_event_name()
    ]:
        cb()
    assert pause_resume.block_resume_reason is None
    assert any("press Resume" in msg for msg in gcode.messages)
    assert handler._waiting_for_reconnect is False
