import logging

# Default hop is small: lift off the part without a long travel while
# the toolboard (and therefore the extruder) is gone.
DEFAULT_Z_HOP = 5.0
DEFAULT_Z_HOP_SPEED = 15.0


class IndxMcuDisconnect:
    def __init__(self, toolboard, config):
        self.toolboard = toolboard
        self.printer = toolboard.printer
        self.reactor = self.printer.get_reactor()
        self.mcu = toolboard.mcu
        self.pause_on_disconnect = config.getboolean(
            "pause_on_disconnect", True
        )
        self.disconnect_z_hop = config.getfloat(
            "disconnect_z_hop", DEFAULT_Z_HOP, minval=0.0
        )
        self.disconnect_z_hop_speed = config.getfloat(
            "disconnect_z_hop_speed", DEFAULT_Z_HOP_SPEED, above=0.0
        )
        self._pause_timer = None
        self._waiting_for_reconnect = False
        if not self.pause_on_disconnect:
            return
        self.printer.load_object(config, "pause_resume")
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler(
            self.mcu.get_non_critical_disconnect_event_name(),
            self._handle_disconnect,
        )
        self.printer.register_event_handler(
            self.mcu.get_non_critical_reconnect_event_name(),
            self._handle_reconnect,
        )

    def _handle_ready(self):
        try:
            self.mcu.enable_non_critical()
        except Exception:
            logging.exception(
                "INDX: unable to mark MCU '%s' non-critical",
                self.mcu.get_name(),
            )
            return
        logging.info(
            "INDX: MCU '%s' disconnects will pause the print instead of"
            " shutting down",
            self.mcu.get_name(),
        )

    def _should_pause(self, pause_resume):
        if pause_resume.is_paused:
            return False
        if pause_resume.is_sd_active():
            return True
        idle = self.printer.lookup_object("idle_timeout", None)
        if idle is None:
            return False
        eventtime = self.reactor.monotonic()
        return idle.get_status(eventtime).get("state") == "Printing"

    def _handle_disconnect(self):
        self._waiting_for_reconnect = True
        self.toolboard.heater.last_report = None
        pause_resume = self.printer.lookup_object("pause_resume")
        if self._should_pause(pause_resume):
            # Stop new G-code immediately. Do not run PAUSE: that macro
            # typically retracts, and the extruder lives on this MCU.
            pause_resume.send_pause_command()
            pause_resume.is_paused = True
            pause_resume.block_resume_reason = (
                "INDX toolboard is disconnected. Wait for reconnect before"
                " Resume."
            )
            self._save_pause_state()
        if self._pause_timer is None:
            self._pause_timer = self.reactor.register_timer(
                self._after_disconnect, self.reactor.NOW
            )
        else:
            self.reactor.update_timer(self._pause_timer, self.reactor.NOW)

    def _save_pause_state(self):
        gcode_move = self.printer.lookup_object("gcode_move", None)
        if gcode_move is None:
            return
        gcode_move.saved_states["PAUSE_STATE"] = {
            "absolute_coord": gcode_move.absolute_coord,
            "absolute_extrude": gcode_move.absolute_extrude,
            "base_position": list(gcode_move.base_position),
            "last_position": list(gcode_move.last_position),
            "homing_position": list(gcode_move.homing_position),
            "speed": gcode_move.speed,
            "speed_factor": gcode_move.speed_factor,
            "extrude_factor": gcode_move.extrude_factor,
        }

    def _after_disconnect(self, eventtime):
        gcode = self.printer.lookup_object("gcode")
        if gcode.get_mutex().test():
            return eventtime + 0.05
        pause_resume = self.printer.lookup_object("pause_resume", None)
        if pause_resume is not None and pause_resume.is_paused:
            gcode.respond_info(
                "INDX toolboard disconnected - print paused. Waiting for"
                " reconnect; press Resume after it returns and the nozzle"
                " is back at temperature."
            )
        else:
            gcode.respond_info(
                "INDX toolboard disconnected. Waiting for reconnect."
            )
        if (
            self.disconnect_z_hop > 0.0
            and pause_resume is not None
            and pause_resume.is_paused
        ):
            try:
                self._z_hop()
            except Exception:
                logging.exception("INDX: Z hop after MCU disconnect failed")
        return self.reactor.NEVER

    def _z_hop(self):
        toolhead = self.printer.lookup_object("toolhead", None)
        if toolhead is None:
            return
        pos = toolhead.get_position()
        if len(pos) < 3:
            return
        new_z = pos[2] + self.disconnect_z_hop
        kin = toolhead.get_kinematics()
        limits = getattr(kin, "limits", None)
        if limits is not None and len(limits) >= 3:
            z_min, z_max = limits[2]
            if z_min > z_max:
                return
            new_z = min(new_z, z_max)
        if new_z <= pos[2] + 0.01:
            return
        toolhead.manual_move([None, None, new_z], self.disconnect_z_hop_speed)

    def _handle_reconnect(self):
        heater = self.toolboard.heater
        heater.last_report = None
        try:
            if getattr(heater, "cmd_set_control_params", None) is not None:
                heater.apply_pid_params()
        except Exception:
            logging.exception("INDX: failed to restore PID after reconnect")
        gcode = self.printer.lookup_object("gcode")
        pause_resume = self.printer.lookup_object("pause_resume", None)
        if pause_resume is not None:
            pause_resume.block_resume_reason = None
        if self._waiting_for_reconnect and pause_resume is not None:
            if pause_resume.is_paused:
                gcode.respond_info(
                    "INDX toolboard reconnected. Wait for the nozzle to"
                    " recover, then press Resume."
                )
            else:
                gcode.respond_info("INDX toolboard reconnected.")
        self._waiting_for_reconnect = False
