# Pico-Thrifty for WaveShare Pico-Zero
# (c) 2025-11-23 Simon Wood <simon@mungewell.org>
#
# https://github.com/mungewell/pico-timecode

# pt-Thrifty, the lowest cost timecode generator
#
# GP13 - User key 'A'
# GP27 - Detect from 3.5mm connector
#
# GP02 - Onboard LED
# GP03 - Onboard LED2 or Midi Qtr_Clock
#
# We'll allocate the following to the PIO blocks
#
# GP11 - RX: LTC_INPUT  (physical connection)
# GP19 - RX: raw/decoded LTC input (debug)
# GP20 - ditto - Hack to accomodate running out of memory
# GP21 - RX: sync from LTC input (debug)
#
# GP22 - TX: raw LTC bitstream output (debug)
# GP9  - TX: LTC_OUTPUT (physical connection)
# GP10 - nTX: LTC_OUTPUT (physical connection)
#

# We need to install the following modules
# ---
# https://github.com/aleppax/upyftsconf
# https://github.com/m-lundberg/simple-pid
# https://github.com/jrullan/micropython_neotimer
# https://github.com/jrullan/micropython_statemachine

from libs import config
from libs.pid import *
from libs.neotimer import *
from libs.statemachine import *

import pico_timecode as pt

from machine import Pin,freq,reset,mem32, ADC
from utime import sleep, ticks_ms
from neopixel import NeoPixel
import _thread
import utime
import rp2
import gc

# Set up (extra) globals
high_output_level = True

thrifty_new_fps = 0
thrifty_current_fps = 0
thrifty_calibration = 0.0

thrifty_available_fps_df = [
        [30,     False,  (0, 255, 0),   0b11],      # Red
        [30,     True,   (0, 255, 255), 0b10],      # Purple
        [29.97,  False,  (255, 255, 0), 0b11],      # Yellow
        [29.97,  True,   (128, 255, 0), 0b10],      # Orange
        [25,     False,  (255, 0, 0),   0b01],      # Green
        [24,     False,  (0, 0, 255),   0b00],      # Blue
        [23.98,  False,  (128, 0, 128), 0b00],      # Cyan
        ]

# ----------------------

def start_state_machines(mode=pt.RUN):
    global thrifty_calibration

    if pt.eng.is_running():
        pt.stop = True
        while pt.eng.is_running():
            sleep(0.1)

    # Force Garbage collection
    gc.collect()

    # apply any calibration
    try:
        setting = config.calibration[str(thrifty_available_fps_df[thrifty_current_fps][0])]
        thrifty_calibration = float(setting)
    except:
        pass

    pt.eng.calval = thrifty_calibration

    # restart...
    try:
        setting = config.setting['tc_start']
        if setting[2] == ":":
            pt.eng.tc.from_ascii(setting, True)
        else:
            pt.eng.tc.from_ascii(setting, False)
    except:
        pt.eng.tc.from_ascii("00:00:00:00")

    pt.eng.tc.set_fps_df(thrifty_available_fps_df[thrifty_current_fps][0],
                         thrifty_available_fps_df[thrifty_current_fps][1])
    pt.eng.sm = []
    sm_freq = int(pt.eng.tc.fps + 0.1) * 80 * 32

    pt.eng.mode = mode
    if mode > pt.RUN:
        pt.eng.sm.append(rp2.StateMachine(pt.SM_START, pt.start_from_sync, freq=sm_freq,
                           in_base=Pin(21),
                           jmp_pin=Pin(21)))        # RX Decoding
    else:
        pt.eng.sm.append(rp2.StateMachine(pt.SM_START, pt.auto_start, freq=sm_freq,
                           jmp_pin=Pin(21)))        # RX Decoding

    # TX State Machines
    pt.eng.sm.append(rp2.StateMachine(pt.SM_BLINK, pt.shift_led_mtc, freq=sm_freq,
                           jmp_pin=Pin(3),
                           out_base=Pin(2)))       # LED on GPIO2/3
    pt.eng.sm.append(rp2.StateMachine(pt.SM_BUFFER, pt.buffer_out, freq=sm_freq,
                           out_base=Pin(22)))       # Output of 'raw' bitstream

    if high_output_level:
        tx2.value(0)
        pt.eng.sm.append(rp2.StateMachine(pt.SM_ENCODE, pt.encode_dmc, freq=sm_freq,
                           jmp_pin=Pin(22),
                           in_base=Pin(9),         # same as pin as out
                           out_base=Pin(9)))       # Encoded LTC Output
    else:
        pt.eng.sm.append(rp2.StateMachine(pt.SM_ENCODE, pt.encode_dmc2, freq=sm_freq,
                           jmp_pin=Pin(22),
                           in_base=Pin(9),         # same as pin as out
                           out_base=Pin(9)))       # Encoded LTC Output

    pt.eng.sm.append(rp2.StateMachine(pt.SM_TX_RAW, pt.tx_raw_value, freq=sm_freq))

    # RX State Machines
    pt.eng.sm.append(rp2.StateMachine(pt.SM_SYNC, pt.sync_and_read, freq=sm_freq,
                               jmp_pin=Pin(19),
                               in_base=Pin(19),
                               out_base=Pin(21),
                               set_base=Pin(21)))       # 'sync' from RX bitstream
    pt.eng.sm.append(rp2.StateMachine(pt.SM_DECODE, pt.decode_dmc, freq=sm_freq,
                               jmp_pin=Pin(11),         # LTC Input ...
                               in_base=Pin(11),         # ... from 'other' device
                               set_base=Pin(19)))       # Decoded LTC Input

    '''
    # DEBUG: check the PIO code space/addresses
    for base in [0x50200000, 0x50300000]:
        for offset in [0x0d4, 0x0ec, 0x104, 0x11c]:
            print("0x%8.8x : 0x%2.2x" % (base + offset, mem32[base + offset]))
    '''

    # correct clock dividers
    pt.eng.config_clocks(pt.eng.tc.fps)

    # set up IRQ handler
    for m in pt.eng.sm:
        m.irq(handler=pt.irq_handler, hard=True)

    if pt._hasUsbDevice:
        # set up MTC engine
        pt.mtc = pt.MTC()
        pt.mtc.init()

    pt.stop = False
    _thread.start_new_thread(pt.pico_timecode_thread, (pt.eng, lambda: pt.stop))

# ----------------------

rgb = Pin(16,Pin.OUT)
RGB = NeoPixel(rgb,3)

# The (only) button
keyA = Pin(12,Pin.IN,Pin.PULL_UP)
timerA = Neotimer(50)
timerB = Neotimer(500)
timerC = Neotimer(2000)
timerH = Neotimer(2000)

# Connector Detect, ie 3.5mm in socket
keyD = Pin(27,Pin.IN,Pin.PULL_UP)
timerD = Neotimer(50)

# chip enable for Amp, low = on
amp_cs = Pin(13,Pin.OUT)
amp_cs.value(1)

# force TX outputs low
tx1 = Pin(9,Pin.OUT)
tx1.value(0)
tx2 = Pin(10,Pin.OUT)
tx2.value(0)


def keyA_debounce(state):
    if timerA.debounce_signal(keyA.value()==state):
        return True
    else:
        return False

def keyA_debounce_low():
    return keyA_debounce(0)
def keyA_debounce_high():
    return keyA_debounce(1)

def keyD_debounce(state):
    if timerD.debounce_signal(keyD.value()==state):
        return True
    else:
        return False

def keyD_debounce_low():
    return keyD_debounce(0)
def keyD_debounce_high():
    return keyD_debounce(1)

def timerC_hold():
    if timerC.hold_signal(keyA.value()==0):
        return True
    else:
        return False

# note: safety measure : can only trigger 'H' if 3.5mm is unplugged
def timerH_hold():
    if timerH.hold_signal(keyA.value()==0) and timerD.debounce_signal(keyD.value()==1):
        return True
    else:
        return False

# ----------------------

menu = StateMachine()

def menu_run_logic():
    global calTimer

    if menu.execute_once:
        #print("menu run")
        RGB[0] = (0, 0, 0)
        RGB.write()

        # pins 9 & 10 : force muxing to use PIO block(s)
        mem32[0x40014000 + 0x04c] = (mem32[0x40014000 + 0x04c] & 0xFFFFFFE0) + 0x6
        if not high_output_level:
            print("MIC level selected")
            mem32[0x40014000 + 0x054] = (mem32[0x40014000 + 0x054] & 0xFFFFFFE0) + 0x6

        # turn of RX amp
        amp_cs.value(1)

        timerH.start()

        calTimer = None

    if pt.eng.mode == pt.MONITOR:
        pt.eng.mode = pt.RUN

def menu_info_logic():
    if menu.execute_once:
        #print("menu info")

        for i in range(1 if thrifty_calibration == 0.0 else 2):
            RGB[0] = thrifty_available_fps_df[thrifty_current_fps][2]
            RGB.write()

            if high_output_level:
                sleep(0.1)
            else:
                sleep(0.3)

            RGB[0] = (0, 0, 0)
            RGB.write()
            sleep(0.1)

def menu_select_logic():
    global thrifty_current_fps, thrifty_new_fps

    if menu.execute_once:
        #print("menu select")
        RGB[0] = thrifty_available_fps_df[thrifty_current_fps][2]
        RGB.write()

        timerB.start()
        timerC.start()

        thrifty_new_fps = thrifty_current_fps

    # advance FPS selection, any change is cancelled with long press
    if timerB.debounce_signal(keyA.value()==0):
        thrifty_new_fps += 1
        if thrifty_new_fps >= len(thrifty_available_fps_df):
            thrifty_new_fps = 0

        RGB[0] = thrifty_available_fps_df[thrifty_new_fps][2]
        RGB.write()

def menu_jam_logic():
    global thrifty_current_fps, thrifty_new_fps
    global disp_asc

    if menu.execute_once:
        #print("menu jam")
        thrifty_current_fps = thrifty_new_fps
        amp_cs.value(0)

        if pt.eng.is_running():
            pt.stop = True
            while pt.eng.is_running():
                print("stopping")
                sleep(0.1)

        # Update config with current fps/df selection
        try:
            setting = config.setting['framerate']
            setting[0] = str(thrifty_available_fps_df[thrifty_current_fps][0])
            config.set('setting', 'framerate', setting)

            setting = config.setting['dropframe']
            setting[0] = ("Yes" if thrifty_available_fps_df[thrifty_current_fps][1] else "No")
            config.set('setting', 'dropframe', setting)
        except:
            pass

        #slate_set_fps_df(index=slate_new_fps_df)
        disp_asc = "--:--:--:--"
        start_state_machines(pt.JAM)

        # pins 9 & 10 : force muxing to GPIO (disable LTC output)
        mem32[0x40014000 + 0x04c] = (mem32[0x40014000 + 0x04c] & 0xFFFFFFE0) + 0x5
        mem32[0x40014000 + 0x054] = (mem32[0x40014000 + 0x054] & 0xFFFFFFE0) + 0x5

        timerC.start()

    # ~1/2sec ticks to flash LED
    now = ticks_ms() >> 9
    if now & 1:
        RGB[0] = thrifty_available_fps_df[thrifty_current_fps][2]
        RGB.write()
    else:
        RGB[0] = (0, 0, 0)
        RGB.write()

    if pt.eng.mode == pt.MONITOR:
        menu.force_transition_to(menu_complete_state)

def menu_cancel_jam_logic():
    if menu.execute_once:
        #print("menu cancel")
        RGB[0] = (0, 0, 0)
        RGB.write()

        if pt.eng.is_running():
            pt.stop = True
            while pt.eng.is_running():
                print("stopping")
                sleep(0.1)

        print("JAM cancelled")
        start_state_machines(pt.RUN)
        timerC.start()

    menu.force_transition_to(menu_info_state)
    '''
    # force reset of whole device
    reset()
    '''

def menu_complete_logic():
    if menu.execute_once:
    #    print("menu complete")
        RGB[0] = (127, 127, 127)
        RGB.write()

def menu_follow_logic():
    global calTimer

    # PID will 'follow' RX LTC, keeping TX aligned

    if menu.execute_once:
        #print("menu follow")
        calTimer = None

    # ~1/2sec ticks
    now = ticks_ms() >> 9
    if now & 1:
        RGB[0] = (127, 127, 127)
        RGB.write()
    else:
        RGB[0] = (0, 0, 0)
        RGB.write()

def menu_cal_logic():
    # PID will 'follow' RX LTC, and after a time-out will
    # store a new calibration so subsequent 'free-run'are
    # at the correct rate - calibrate individually for each FPS.

    global thrifty_current_fps, thrifty_new_fps
    global thrifty_calibration, calTimer

    if menu.execute_once:
        #print("menu calibrate")
        timerC.start()

        # erase existing calibration
        thrifty_calibration = 0.0

        # deregister to prevent 'uncaught exception in IRQ handler'? :-(
        pt.irq_callbacks[pt.SM_BLINK] = None
        sleep(0.1)
        config.set('calibration', pt.eng.tc.fps, thrifty_calibration)
        pt.irq_callbacks[pt.SM_BLINK] = thrifty_display_callback

        calTimer = Neotimer(3 * 60 * 1000) # 3mins
        calTimer.start()

    # ~1/2sec ticks
    now = ticks_ms() >> 9
    if now & 1:
        RGB[0] = thrifty_available_fps_df[thrifty_current_fps][2]
        RGB.write()
    else:
        RGB[0] = (127, 127, 127)
        RGB.write()

    # store new value after we deem calibration is complete
    # note: user can cancel calibration by removing 3.5mm
    '''
    if True:
        thrifty_calibration = 1.0
        menu.force_transition_to(menu_follow_state)
    '''

def menu_init():
    global menu, menu_info_state, menu_jam_state
    global menu_complete_state, menu_follow_state

    # Initilize states
    menu_info_state = menu.add_state(menu_info_logic)       # created first, entry point
    menu_run_state = menu.add_state(menu_run_logic)
    menu_select_state = menu.add_state(menu_select_logic)
    menu_jam_state = menu.add_state(menu_jam_logic)
    menu_cancel_jam_state = menu.add_state(menu_cancel_jam_logic)
    menu_complete_state = menu.add_state(menu_complete_logic)
    menu_follow_state = menu.add_state(menu_follow_logic)
    menu_cal_state = menu.add_state(menu_cal_logic)

    # add transitions
    menu_run_state.attach_transition(keyA_debounce_low, menu_info_state)
    menu_info_state.attach_transition(keyA_debounce_high, menu_run_state)

    menu_run_state.attach_transition(timerH_hold, menu_select_state)
    menu_info_state.attach_transition(timerH_hold, menu_select_state)
    menu_select_state.attach_transition(timerC_hold, menu_run_state)

    menu_select_state.attach_transition(keyD_debounce_low, menu_jam_state)
    menu_jam_state.attach_transition(timerC_hold, menu_cancel_jam_state)

    menu_complete_state.attach_transition(keyD_debounce_high, menu_run_state)
    menu_complete_state.attach_transition(keyA_debounce_low, menu_follow_state)

    menu_follow_state.attach_transition(keyD_debounce_high, menu_run_state)
    menu_follow_state.attach_transition(timerC_hold, menu_cal_state)

    menu_cal_state.attach_transition(keyD_debounce_high, menu_run_state)

#---------------------------------------------
# Class for using the internal temp sensor

class Temperature:
    def __init__(self, ref=3.3):
        self.ref = ref
        self.sensor = ADC(4)

    def read(self):
        adc_value = self.sensor.read_u16()
        volt = (self.ref/65536) * adc_value

        return(27-(volt-0.706)/0.001721)

# ----------------------

def thrifty_display_thread():
    global disp, slate_current_fps_df
    global disp_asc, slate_open
    global amp_cs, high_output_level
    global thrifty_calibration, calTimer
    global thrifty_current_fps

    pt.eng = pt.engine()
    pt.eng.mode = pt.RUN
    pt.eng.set_stopped(True)

    menu_init()

    # Internal temp sensor
    sensor = Temperature()

    # Set the CPU clock, for better computation of PIO freqs
    if machine.freq() != 180000000:
        machine.freq(180000000)

    # Read Line/MIC level from config, toggle if booted with 'A' pressed
    try:
        setting = config.setting['output']
        high_output_level = setting[1].index(setting[0])
    except:
        pass

    if keyA.value() == 0:
        high_output_level = not high_output_level
        try:
            setting = config.setting['output']
            setting[0] = setting[1][high_output_level]
            config.set('setting', 'output', setting)
        except:
            pass

    # Load/set the flashframe from config
    try:
        setting = config.setting['flashframe']
        if setting[0]=="Off":
            pt.eng.flashframe = -1
        else:
            pt.eng.flashframe = int(setting[0])
    except:
        pass

    # Load userbits from config
    try:
        userbits = config.userbits['userbits']
        if userbits[0]=="Name":
            pt.eng.tc.user_from_ascii(config.userbits['ub_name'])
        elif userbits[0]=="Digits":
            pt.eng.tc.user_from_bcd_hex(config.userbits['ub_digits'])
        else:   # Date
            pt.eng.tc.user_from_date(config.userbits['ub_date'])
    except:
        pass

    # Update 'current' with config's fps/df selection
    try:
        setting = config.setting['framerate']
        for i in range(len(thrifty_available_fps_df)):
            # find first matching fps
            if setting[0] == str(thrifty_available_fps_df[i][0]):
                thrifty_current_fps = i
                break

        setting = config.setting['dropframe']
        if setting[0] == "Yes":
            if thrifty_available_fps_df[i][0] == thrifty_available_fps_df[i+1][0]:
                # check for repeated fps, else reject
                thrifty_current_fps += 1
            else:
                # clear illegal combination
                setting[0] = "No"
                config.set('setting', 'dropframe', setting)
    except:
        pass

    # apply any calibration
    try:
        setting = config.calibration[str(thrifty_available_fps_df[thrifty_current_fps][0])]
        thrifty_calibration = float(setting)
    except:
        pass

    # load PIO blocks, and start pico_timecode thread
    start_state_machines(pt.eng.mode)

    disp = pt.timecode()
    disp_asc = "--:--:--:--"

    monTimer = None
    calTimer = None

    period = 1
    try:
        period = config.calibration['period']
    except:
        pass

    pt.eng.micro_adjust(thrifty_calibration, period * 1000) # period in ms

    # register callbacks, functions to display TX data ASAP
    pt.irq_callbacks[pt.SM_BLINK] = thrifty_display_callback

    while True:
        if pt.eng.mode == pt.HALTED:
            pt.stop = True

        '''
        if pt.eng.is_stopped():
            break
        '''

        menu.run()

        # Async display of external LTC during jam/monitoring
        if pt.eng.mode > pt.RUN:
            asc = pt.eng.rc.to_ascii(False)

            if disp_asc != asc:
                disp_asc = asc

                if pt.eng.mode > pt.MONITOR:
                    print("Jamming:", pt.eng.mode)

                if monTimer == None:
                    # Display data every second
                    monTimer = Neotimer(1000 - (1000/pt.eng.tc.fps))
                    monTimer.start()
                    pid = None
                elif monTimer.repeat_execution():
                    phase = ((4294967295 - pt.rx_ticks + 188) % 640) - 320
                    if phase < -32:
                        # RX is ahead/earlier than TX
                        phases = ((" "*10) + ":" + ("+"*int(abs(phase/32))) + (" "*10)) [:21]
                    elif phase > 32:
                        # RX is behind/later than TX
                        phases = ((" "*10) + ("-"*int(abs(phase/32))) + ":" + (" "*10)) [-21:]
                    else:
                        phases = "          :          "

                    if menu.state_list[menu.active_state_index] == menu_follow_state \
                            or calTimer:

                        if not pid:
                            pid = PID(12.5, 0.25, 0.1, setpoint=0)

                            pid.sample_time = 1
                            pid.output_limits = (-500.0, 500.0)
                            pid.set_auto_mode(True, last_output=pt.eng.calval)

                            zcount = 0
                            zmax = 0

                        # count when we are 'exact', and for how long
                        if phase == 0.0:
                            zcount += 1
                        else:
                            if zcount > zmax:
                                zmax = zcount
                            zcount = 0

                        print("RX: %s (%4d %21s) %2.2f" % (pt.eng.rc.to_ascii(),
                                phase, phases, sensor.read()),
                                pid.components, zcount, zmax)

                        adjust = pid(phase)
                        pt.eng.micro_adjust(adjust, 1000)

                        if calTimer and calTimer.finished():
                            if pid.Ki > 0.005:
                                pid.Ki = pid.Ki / 2

                                print("Calibration extended", pid.Ki)
                                calTimer = Neotimer(2 * 60 * 1000) # 2mins
                                calTimer.start()
                            else:
                                new_cal_value = pid(phase)

                                # deregister to prevent 'uncaught exception in IRQ handler'? :-(
                                pt.irq_callbacks[pt.SM_BLINK] = None
                                sleep(0.1)

                                print("Calibration complete, writing to config file")
                                config.set('calibration', 'period', period)
                                config.set('calibration', pt.eng.tc.fps, new_cal_value)
                                pt.irq_callbacks[pt.SM_BLINK] = thrifty_display_callback

                                thrifty_calibration = new_cal_value
                                menu.force_transition_to(menu_follow_state)
                    else:
                        print("RX: %s (%4d %21s) %2.2f" % (pt.eng.rc.to_ascii(),
                                phase, phases, sensor.read()))
        else:
            if monTimer:
                monTimer = None
                pt.eng.micro_adjust(thrifty_calibration, 10000) # period in ms


def thrifty_display_callback(sm=None):
    global disp, disp_asc

    if sm == pt.SM_BLINK:
        # MTC quarter packets
        if pt.mtc:
            if pt.mtc.is_open():
                # sync to 0th quarter (inc has happened)
                if pt.quarters==1 and pt.mtc.open_seen==1:
                    pt.mtc.open_seen=2

                if pt.mtc.open_seen==2:
                    pt.mtc.send_quarter_mtc(pt.tx_raw)
            else:
                # reset, ready for being USB attached again
                pt.mtc.open_seen = 0
                pt.mtc.count = 0

        # Figure out what TX frame to display
        disp.from_raw(pt.tx_raw)
        asc = disp.to_ascii()

        if disp_asc != asc:
            # MTC long packet, first frame only
            if pt.mtc and pt.mtc.is_open():
                if not pt.mtc.open_seen:
                    pt.mtc.send_long_mtc(pt.tx_raw)          # 'seek' to position
                    pt.mtc.open_seen = 1

            disp_asc = asc
            if pt.eng.mode == pt.RUN:
                print("TX: %s" % asc)


#---------------------------------------------

if __name__ == "__main__":
    print("pt-Thrifty uses...")
    print("Pico-Timecode" + pt.VERSION)
    print("www.github.com/mungewell/pico-timecode")
    if pt._hasUsbDevice:
        print("MTC enabled (will loose USB-UART connection)")
    sleep(2)

    thrifty_display_thread()
