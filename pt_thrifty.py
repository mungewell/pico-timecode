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
# GP13 - AMP_CS, low for disable
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

# implement a Digi-Slate with Pico, swiches/buttons
# and 2x I2C LED modules:
#
# Pin10 / GP14 - I2C1_SDA
# Pin11 / GP15 - I2C1_CLK
# Pin14 / GP28 - Clapper Switch, short-circuit to GND when 'open'
# Pin15 / GP29 - Rotation Switch, short-circuit to GND when 'inverted'

# We need to install the following modules
# ---
# https://github.com/aleppax/upyftsconf
# https://github.com/m-lundberg/simple-pid
# https://github.com/jrullan/micropython_neotimer
# https://github.com/jrullan/micropython_statemachine
# https://github.com/smittytone/HT16K33-Python

from libs import config
from libs.pid import *
from libs.neotimer import *
from libs.statemachine import *
from libs.ht16k33segment import HT16K33Segment
from libs.ht16k33segment14 import HT16K33Segment14

import pico_timecode as pt

from machine import Pin,freq,reset,mem32,ADC,I2C
from utime import sleep, ticks_ms
from neopixel import NeoPixel
from os import uname
import _thread
import utime
import rp2
import gc

# Set up (extra) globals
high_output_level = 0       # MIC level

powersave = False
menu_active = False
slate_HM = False
slate_SF = False

thrifty_new_fps = 0
thrifty_current_fps = 0
thrifty_calibration = 0.0
thrifty_synced = 0

thrifty_available_fps_df = [
        [30,     False,  (255, 0,   0  ), 0b11, "30.00"],      # Red
        [30,     True,   (255, 0,   255), 0b10, "30.00"],      # Purple
        [29.97,  False,  (255, 255, 0  ), 0b11, "29.97"],      # Yellow
        [29.97,  True,   (255, 128, 0  ), 0b10, "29.97"],      # Orange
        [25,     False,  (0,   255, 0  ), 0b01, "25.00"],      # Green
        [24,     False,  (0,   0,   255), 0b00, "24.00"],      # Blue
        [23.98,  False,  (0,   128, 128), 0b00, "23.98"],      # Cyan
        ]

# Pico2 uses different addressing
if uname().machine[23:] == 'RP2040':
    IO_BANK0_BASE = 0x40014000
else:
    IO_BANK0_BASE = 0x40028000
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
    pt.eng.mode = mode

    sm_freq = int(pt.eng.tc.fps + 0.1) * 80 * 32

    if pt.eng.mode > pt.MONITOR:
        pt.eng.sm.append(rp2.StateMachine(pt.SM_START, pt.start_from_sync, freq=sm_freq,
                           in_base=Pin(21),
                           jmp_pin=Pin(21)))        # RX Decoding
    else:
        pt.eng.sm.append(rp2.StateMachine(pt.SM_START, pt.auto_start, freq=sm_freq,
                           jmp_pin=Pin(21)))

    # TX State Machines
    if pt._hasUsbDevice:
        pt.eng.sm.append(rp2.StateMachine(pt.SM_BLINK, pt.shift_led_irq_4x, freq=sm_freq,
                               jmp_pin=Pin(3),          # Qtr_Clk on GPIO3
                               out_base=Pin(2)))        # LED on GPIO2
    else:
        pt.eng.sm.append(rp2.StateMachine(pt.SM_BLINK, pt.shift_led_irq_1x, freq=sm_freq,
                               jmp_pin=Pin(3),          # Qtr_Clk on GPIO3
                               out_base=Pin(2)))        # LED on GPIO2

    pt.eng.sm.append(rp2.StateMachine(pt.SM_BUFFER, pt.buffer_out, freq=sm_freq,
                           out_base=Pin(22)))       # Output of 'raw' bitstream

    # always run differential outs (MIC level)
    # outs can be static, and not interfer with incoming/RX LTC for jam'ing
    # force pins 9 & 10 to mux to GPIO, muxing will be changed later
    mem32[IO_BANK0_BASE + 0x04c] = (mem32[IO_BANK0_BASE + 0x04c] & 0xFFFFFFE0) + 0x5
    mem32[IO_BANK0_BASE + 0x054] = (mem32[IO_BANK0_BASE + 0x054] & 0xFFFFFFE0) + 0x5

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
            print("0x%8.8x : 0x%8.8x = 0x%8.8x" % (base + offset, mem32[base + offset], mem32[base + offset + 4]))
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

# force TX outputs differential high/low
tx1 = Pin(9,Pin.OUT)
tx1.value(1)
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
    global menu_active

    if menu.execute_once:
        #print("menu run")
        if RGB:
            RGB[0] = (0, 0, 0)
            RGB.write()

        # force pins 9 & 10 to mux to PIO
        mem32[IO_BANK0_BASE + 0x04c] = (mem32[IO_BANK0_BASE + 0x04c] & 0xFFFFFFE0) + 0x6
        mem32[IO_BANK0_BASE + 0x054] = (mem32[IO_BANK0_BASE + 0x054] & 0xFFFFFFE0) + 0x6
        if high_output_level:
            # pin 10 : force muxing to use GPIO block (ie force low)
            mem32[IO_BANK0_BASE + 0x054] = (mem32[IO_BANK0_BASE + 0x054] & 0xFFFFFFE0) + 0x5

            print("HIGH level selected")
        else:
            print("MIC level selected")

        # turn of RX amp
        amp_cs.value(1)

        timerH.start()

        calTimer = None

        # allow slate counter to run
        menu_active = False

    if pt.eng.mode == pt.MONITOR:
        pt.eng.mode = pt.RUN

def menu_info_logic():
    global menu_active

    if menu.execute_once:
        #print("menu info")

        # prevent 7-seg counter running
        menu_active = True
        if slate_SF:
            slate_show_fps_df(thrifty_current_fps)
            timerS.start()

        for i in range(1 if thrifty_synced == 0 else 2):
            if RGB:
                RGB[0] = thrifty_available_fps_df[thrifty_current_fps][2]
                RGB.write()

            if high_output_level:
                sleep(0.3)
            else:
                sleep(0.1)

            if RGB:
                RGB[0] = (0, 0, 0)
                RGB.write()
            sleep(0.1)

def menu_select_logic():
    global thrifty_current_fps, thrifty_new_fps
    global menu_active

    if menu.execute_once:
        # prevent 7-seg counter running
        menu_active = True
        if slate_SF:
            slate_show_fps_df(thrifty_current_fps, True)
            timerS.start()

        #print("menu select")
        if RGB:
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

        if slate_SF:
            slate_show_fps_df(thrifty_new_fps, True)
            timerS.start()

        if RGB:
            RGB[0] = thrifty_available_fps_df[thrifty_new_fps][2]
            RGB.write()

def menu_jam_logic():
    global thrifty_current_fps, thrifty_new_fps, thrifty_synced
    global disp_asc

    if menu.execute_once:
        #print("menu jam")
        thrifty_current_fps = thrifty_new_fps
        thrifty_synced = 0

        # Enable Amp, LTC receive
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

        timerC.start()

    # ~1/2sec ticks to flash LED
    now = ticks_ms() >> 9
    if RGB:
        if now & 1:
            RGB[0] = thrifty_available_fps_df[thrifty_current_fps][2]
            RGB.write()
        else:
            RGB[0] = (0, 0, 0)
            RGB.write()

    if pt.eng.mode == pt.MONITOR:
        menu.force_transition_to(menu_complete_state)

def menu_cancel_jam_logic():
    global disp_asc

    if menu.execute_once:
        #print("menu cancel")
        if RGB:
            RGB[0] = (0, 0, 0)
            RGB.write()

        if pt.eng.is_running():
            pt.stop = True
            while pt.eng.is_running():
                print("stopping")
                sleep(0.1)

        print("JAM cancelled")

        disp_asc = "--:--:--:--"
        start_state_machines(pt.RUN)

        timerC.start()

    menu.force_transition_to(menu_info_state)
    '''
    # force reset of whole device
    reset()
    '''

def menu_complete_logic():
    global thrifty_synced

    if menu.execute_once:
        #print("menu complete")
        if RGB:
            RGB[0] = (127, 127, 127)
            RGB.write()

        if slate_SF:
            slate_SF.set_blink_rate(0)

        thrifty_synced = 1

def menu_follow_logic():
    global calTimer

    # PID will 'follow' RX LTC, keeping TX aligned

    if menu.execute_once:
        #print("menu follow")

        calTimer = None

    # ~1/2sec ticks
    now = ticks_ms() >> 9
    if RGB:
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
    if RGB:
        if now & 1:
            RGB[0] = thrifty_available_fps_df[thrifty_current_fps][2]
            RGB.write()
        else:
            RGB[0] = (127, 127, 127)
            RGB.write()

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

#---------------------------------------------
# Class for 'GRB' NeoPixel as used on WaveShare boards
# defines different R,G,B order

class GRB_NeoPixel(NeoPixel):
    ORDER = (0, 1, 2, 3)

#---------------------------------------------
# Class to overload HT16K33Segment14
# modify 'render' to double up characters for reduced flicker on ECBUYING display
# https://github.com/smittytone/HT16K33-Python/issues/28

class HT16K33Segment14_dbl(HT16K33Segment14):
    def _render(self):
        """
        Write the display buffer out to I2C
        """
        buffer = bytearray(len(self.buffer) + 1)
        buffer[1:] = self.buffer[:8]
        buffer[9:] = self.buffer[:8]

        buffer[0] = 0x00
        self.i2c.writeto(self.address, bytes(buffer))

# ----------------------

def slate_show_fps_df(index, blink=False):
    global slate_HM, slate_SF, powersave

    if powersave:
        powersave = False

        if slate_SF:
            if slate_HM:
                slate_HM.power_on()
            slate_SF.power_on()

    asc = str(thrifty_available_fps_df[index][4])

    for i in range(4):
        slate_SF.set_character(asc[i+(1 if i>1 else 0)], \
                i, has_dot=(True if i==1 else False))

    extend_glyph = 0
    if len(slate_SF.CHARSET) > 19:
        # include segment '7' on ECBUYING 14-segment
        extend_glyph = 0x80

    if thrifty_available_fps_df[index][1]:
        '''
        F = 0 4 5 6   = 0x71
        P = 0 1 4 5 6 = 0x73
        S = 0 2 3 5 6 = 0x6D

        d = 1 2 3 4 6 = 0x5e
        f = 0 4 5 6   = 0x71
        '''
        # overwrite last digits with 'df'
        slate_SF.set_glyph(0x5e + extend_glyph, 2)
        slate_SF.set_glyph(0x71 + extend_glyph, 3)

    if slate_HM:
        slate_HM.set_glyph(0x71 + extend_glyph, 0)
        slate_HM.set_glyph(0x73 + extend_glyph, 1)
        slate_HM.set_glyph(0x6d + extend_glyph, 2)
        slate_HM.set_glyph(0x00, 3)
        slate_HM.draw()

    #slate_SF.set_colon(False)
    slate_SF.draw()
    if blink:
        slate_SF.set_blink_rate(2)
    else:
        slate_SF.set_blink_rate(0)
    if slate_HM:
        slate_HM.set_blink_rate(0)

def thrifty_display_thread():
    global disp, slate_current_fps_df
    global disp_asc, slate_open
    global amp_cs, high_output_level
    global thrifty_calibration, calTimer
    global thrifty_current_fps
    global rgb, RGB
    global slate_HM, slate_SF, timerS, powersave

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

    # Config the type of NeoPixel
    # note: different RGB order seen on 'WaveShare' boards
    RGB = False
    rgb = Pin(16,Pin.OUT)
    try:
        neo = config.pt_thrifty['neopixel'][0]
        if neo == "RGB":
            RGB = NeoPixel(rgb,3)
        elif neo == "GRB":
            RGB = GRB_NeoPixel(rgb,3)
    except:
        pass

    # Configure Digi-Slate controls
    keyC = Pin(28,Pin.IN,Pin.PULL_UP)
    keyR = Pin(29,Pin.IN,Pin.PULL_UP)
    timerC = Neotimer(15)
    timerR = Neotimer(50)
    timerS = Neotimer(1000)

    # Display is made from 2x 4-character I2C modules
    # note: left module is mounted up-side-down
    slate_R = None
    slate_L = None

    # preferred display, supports ASCII
    setting = None
    try:
        setting = config.pt_thrifty['7seg'][0]
    except:
        pass

    try:
        if setting=="HT16K33Segment":
            # Adafruit 7-segment
            i2c = I2C(1, scl=Pin(15), sda=Pin(14), freq=1_200_000)
            slate_R = HT16K33Segment(i2c, i2c_address=0x70)
            slate_L = HT16K33Segment(i2c, i2c_address=0x71)
        elif setting=="HT16K33Segment14":
            # ECBUYING 14-segment
            i2c = I2C(1, scl=Pin(15), sda=Pin(14), freq=1_200_000)
            slate_R = HT16K33Segment14_dbl(i2c, i2c_address=0x70, board=HT16K33Segment14.ECBUYING_054)
            slate_L = HT16K33Segment14_dbl(i2c, i2c_address=0x71, board=HT16K33Segment14.ECBUYING_054)
    except OSError as e:
        if e.args[0] == 5: # Errno 5 is EIO
            print("One or more 7-seg/14-seg displays not found")
        else:
            raise e

    slate_SF = slate_R
    if slate_L:
        slate_HM = slate_L
        slate_HM.rotate()

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
    #disp_asc = "--:--:--:--"
    disp_asc = "--------"
    if slate_SF:
        for i in range(4):
            if slate_HM:
                slate_HM.set_character(disp_asc[i], i)
            slate_SF.set_character(disp_asc[i+4], i)

        if slate_HM:
            slate_HM.draw()
        slate_SF.draw()
    timerS.start()

    monTimer = None
    calTimer = None

    period = 1
    try:
        period = config.calibration['period']
    except:
        pass

    pt.eng.micro_adjust(thrifty_calibration, period * 1000) # period in ms

    slate_open = False
    slate_rotated = False

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

        # Check for slate rotation
        # rotation only possible with 2x displays
        if slate_HM:
            if not slate_rotated and timerR.debounce_signal(keyR.value()==0):
                slate_HM = slate_R
                slate_HM.rotate()
                slate_HM.set_colon(False)
                slate_SF = slate_L
                slate_SF.rotate()
                slate_rotated = True
            elif slate_rotated and timerR.debounce_signal(keyR.value()==1):
                slate_HM = slate_L
                slate_HM.rotate()
                slate_HM.set_colon(False)
                slate_SF = slate_R
                slate_SF.rotate()
                slate_rotated = False

        # Check for clapper closing
        if slate_open and timerC.debounce_signal(keyC.value()==1) and not menu_active:
            slate_open = False
            timerS.start()

            # 'LED blur' workaround, freeze TC display for 4 frames
            if slate_HM:
                slate_HM.set_character("-", 0)
                slate_HM.draw()
            sleep(4/pt.eng.tc.fps)

            # display user bits, if possible
            '''
            C = 0 3 4 5     = 0x39
            L = 3 4 5       = 0x38
            A = 0 1 2 4 5 6 = 0x77
            P = 0 1 4 5 6   = 0x73
            - = 6           = 0x40
            '''
            if slate_SF:
                if len(slate_SF.CHARSET) > 19:
                    # include segment '7' on ECBUYING 14-segment
                    clap = [0xC0,0xC0,0x39,0x38,0xF7,0xF3,0xC0,0xC0]
                else:
                    clap = [0x40,0x40,0x39,0x38,0x77,0x73,0x40,0x40]

                ub = None
                try:
                    if config.userbits['userbits'][0] == "Name":
                        ub = "  " + config.userbits['ub_name'] + "      "
                    elif config.userbits['userbits'][0] == "Digits":
                        ub = config.userbits['ub_digits'] + "        "
                except:
                    pass

                if ub:
                    # best effort to display Userbits
                    try:
                        for i in range(4):
                            if slate_HM:
                                slate_HM.set_character(ub[i], i)
                                slate_SF.set_character(ub[i+4], i)
                            else:
                                slate_SF.set_character(ub[i+2], i)
                        clap = None
                    except:
                        pass

                if clap:
                    # Unable to display User-Bits
                    for i in range(4):
                        if slate_HM:
                            slate_HM.set_glyph(clap[i], i)
                            slate_SF.set_glyph(clap[i+4], i)
                        else:
                            slate_SF.set_glyph(clap[i+2], i)

                if slate_HM:
                    slate_HM.draw()
                slate_SF.draw()

        # Once clapper has closed and timer expired, enter powersave
        if not slate_open and timerS.finished() and \
                not menu_active and not powersave:
            if slate_SF:
                if slate_HM:
                    slate_HM.power_off()
                slate_SF.power_off()

            '''
            pt.irq_callbacks[pt.SM_BLINK] = None
            print("Entering powersave")
            sleep(0.1)

            pt.eng.set_powersave(True)
            '''
            powersave = True

        # Display FPS on slate when clapper is first lifted
        if not slate_open and timerC.debounce_signal(keyC.value()==0):
            '''
            if powersave:
                print("Exiting powersave")
                pt.eng.set_powersave(False)

                pt.irq_callbacks[pt.SM_BLINK] = slate_display_callback
                powersave = False

                if slate_SF:
                    if slate_HM:
                        slate_HM.power_on()
                    slate_SF.power_on()
            '''

            slate_open = True
            timerS.start()

            if slate_SF:
                slate_show_fps_df(thrifty_current_fps)

        # Async display of external LTC during jam/monitoring
        if pt.eng.mode > pt.RUN:
            asc = pt.eng.rc.to_ascii(False)

            if disp_asc != asc:
                # update Digi-Slate
                '''
                S = 0 2 3 5 6 = 0x6D
                Y = 1 2 3 5 6 = 0x6E
                n = 2 4 6     = 0x54
                c = 3 4 6     = 0x58
                '''
                if slate_SF:
                    force_dp = False
                    if slate_HM:
                        if len(slate_SF.CHARSET) > 19:
                            slate_HM.set_character("S", 0)
                            slate_HM.set_character("Y", 1)
                            slate_HM.set_character("N", 2)
                            slate_HM.set_character("C", 3)
                        else:
                            # 7-seg
                            slate_HM.set_glyph(0x6D, 0)
                            slate_HM.set_glyph(0x6E, 1)
                            slate_HM.set_glyph(0x54, 2)
                            slate_HM.set_glyph(0x58, 3)
                        slate_HM.draw()
                    else:
                        # indicate Sync with all decimal points lit
                        force_dp = True

                    # only display the SS:FF digits
                    if slate_SF:
                        for i in range(4):
                            slate_SF.set_character(asc[4+i], i,
                                        has_dot=(True if i==1 else force_dp))
                        #slate_SF.set_colon(True)
                        slate_SF.draw()

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
        # sync to 0th quarter (inc has happened)
        # send previously written frame
        if slate_SF and ((pt.quarters == 1) or not pt._hasUsbDevice) and \
                not menu_active and timerS.finished() and slate_open == 1:
            #debug.on()
            slate_SF.draw()
            if slate_HM:
                slate_HM.draw()
            #debug.off()

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

                if slate_SF and not menu_active and timerS.finished() and slate_open == 1:
                    # pre-write values for next frame
                    disp.next_frame()
                    asc = disp.to_ascii(False)
                    for i in range(4):
                        slate_SF.set_character(asc[4+i], i,
                                has_dot=(True if i==1 else False))
                        if slate_HM and slate_open == 1:
                            slate_HM.set_character(asc[i], i,
                                    has_dot=False)

#---------------------------------------------

if __name__ == "__main__":
    print("pt-Thrifty uses...")
    print("Pico-Timecode" + pt.VERSION)
    print("www.github.com/mungewell/pico-timecode")
    if pt._hasUsbDevice:
        print("MTC enabled (will loose USB-UART connection)")
    sleep(2)

    thrifty_display_thread()
