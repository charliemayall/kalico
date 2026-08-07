#ifndef __INDX_COIL_DRIVER_H__
#define __INDX_COIL_DRIVER_H__

extern "C" {
#include "board/internal.h"
#include <stdint.h>
}

// Latched outcome of a nozzle-presence ringdown probe.
enum class ringdown_status : uint8_t {
    idle = 0,
    running = 1,
    valid = 2,
    not_enough_peaks = 3,
    interval_bad = 4,
    aborted = 5,
    overvoltage = 6,
    timeout = 7,
};

enum class nozzle_presence : uint8_t {
    unknown = 0,
    present = 1,
    absent = 2,
};

struct coil_driver {
    // Maximum number of fired cycles per 512-cycle frame (0 = no limit).
    uint32_t duty_limit{0};

    void
    shutdown_();

    void
    set_duty(float duty);

    void
    set_cycle_limit(uint32_t limit);

    // time_on_first is the (shorter) ON time used for the first fired cycle of
    // every burst
    void
    set_timings(float time_on, float time_off, float time_on_first);

    // Cumulative number of overvoltage trips since boot.
    uint32_t
    get_ov_count();

    uint32_t
    get_total_charge();

    // Start driver tuning procedure, to find timing parameters.
    void
    start_tune(bool want_details);

    // True while a tuning session is in progress.
    bool
    tune_active();

    // Advance the tuning state machine one step.
    void
    tune_step();

    // Send the latched tuning outcome (status/error/found timings) to the host.
    void
    report_tune_status();

    // Send the drive timings currently in effect to the host.
    void
    report_params();

    // --- Nozzle presence via LC ringdown ---

    // Configure continuous probe, heat gate, and debug tuning knobs.
    // Decays are unitless ratios (Prusa-style). Periods are milliseconds.
    // excite_scale multiplies the soft-start ON ticks for the probe pulse
    // (clamped to <= 1.0 so the pulse cannot exceed coil_time_on_first).
    // zero_margin_v is the ADC floor (volts) used to find resonance start.
    // abort_on_ov is accepted from the host but OV always aborts the probe.
    void
    set_ringdown_params(bool enable, bool heat_gate, float present_decay,
                        float absent_decay, uint32_t idle_ms, uint32_t heat_ms,
                        float excite_scale, float zero_margin_v, bool abort_on_ov,
                        uint32_t min_interval, uint32_t max_interval);

    // Start a oneshot ringdown probe. Fails closed if tune is active or coil
    // timings are not set. report=true sends indx_nozzle_presence when done.
    void
    start_ringdown(bool report);

    bool
    ringdown_active();

    // Advance the ringdown state machine; also starts background probes when
    // enabled. heating=true selects the longer recheck period.
    void
    ringdown_step(bool heating);

    // Last latched presence classification (unknown until a valid probe).
    nozzle_presence
    get_nozzle_presence();

    // Send latched ringdown outcome to the host.
    void
    report_ringdown_status();
};

coil_driver
coil_driver_create();

#endif
