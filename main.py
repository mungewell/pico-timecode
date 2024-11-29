# Pico-Timcode for Raspberry-Pi Pico
# (c) 2023-05-08 Simon Wood <simon@mungewell.org>
#
# https://github.com/mungewell/pico-timecode

# Basic UI implemented on hardware with 'Pico-OLED-1.3'
#
# Pico-OLED-1.3 is connected as follows:
# Pin9  / GP6  - I2C_SDA (not actually used)
# Pin10 / GP7  - I2C_CLK (not actually used)
# Pin11 / GP8  - OLED_DC
# Pin12 / GP9  - CS
# Pin14 / GP10 - OLED_CLK
# Pin15 / GP11 - OLED_DIN
# Pin17 / GP13 - RESET
# Pin20 / GP15 - User key 'A'
# Pin22 / GP17 - User key 'B'
#
# GP25 - Onboard LED
#
# We'll allocate the following to the PIO blocks:
#
# GP18 - RX: LTC_INPUT  (physical connection)
# GP19 - RX: raw/decoded LTC input (debug)
# GP20 - ditto - Hack to accomodate running out of memory
# GP21 - RX: sync from LTC input (debug)
#
# Pin29 / GP22 - TX: raw LTC bitstream output (debug)
# Pin17 / GP13 - TX: LTC_OUTPUT (physical connection)
#
# In PCB Rev1 we will also use:
#
# Pin19 / GP14 - OUT_DET (shorted to GND when J1 is connected)
# Pin21 / GP16 - IN_DET (shorted to GND when J2 is connected)
# Pin32 / GP26 - BLINK_LED (additional LED on front of PCB, near J1)
#
# For controlling the Output Amp:
#
# Pin7  / GP5  - ENABLE (fly wire as PCB error)
# Pin14 / GP10 - Shared with OLED_CLK
# Pin15 / GP11 - Shared with OLED_DIN
#
# In the future we may also use the I2C bus to 'talk' to other devices...
#

# We need to install the following modules
# ---
# https://github.com/aleppax/upyftsconf
# https://github.com/m-lundberg/simple-pid
# https://github.com/plugowski/umenu
# https://github.com/jrullan/micropython_neotimer
# https://github.com/mungewell/pico-oled-1.3-driver/tree/pico_timecode

from libs import config
from libs.pid import *
from libs.umenu import *
from libs.neotimer import *
from libs.lowpower import *

# Requires modified lib
# https://github.com/mungewell/pico-oled-1.3-driver/tree/pico_timecode

from libs.PicoOled13 import *

# Special font, for display the TX'ed timecode in a particular way
from libs.fonts import TimecodeFont
from framebuf import FrameBuffer, MONO_HMSB

import pico_timecode as pt

from machine import Pin,SPI,ADC,freq,reset
import _thread
import utime
import rp2
import gc

# Set up (extra) globals
outamp = None
menu = None
powersave = False
zoom = False
monitor = False
calibrate = False
menu_hidden = True
displayfps = None

def add_more_state_machines():
    sm_freq = int(pt.eng.tc.fps * 80 * 32)

    # TX State Machines
    pt.eng.sm.append(rp2.StateMachine(1, pt.blink_led, freq=sm_freq,
                               set_base=Pin(25)))       # LED on Pico board + GPIO26/27/28
    pt.eng.sm.append(rp2.StateMachine(2, pt.buffer_out, freq=sm_freq,
                               out_base=Pin(22)))       # Output of 'raw' bitstream
    pt.eng.sm.append(rp2.StateMachine(3, pt.encode_dmc, freq=sm_freq,
                               jmp_pin=Pin(22),
                               in_base=Pin(13),         # same as pin as out
                               out_base=Pin(13)))       # Encoded LTC Output

    # RX State Machines
    pt.eng.sm.append(rp2.StateMachine(4, pt.decode_dmc, freq=sm_freq,
                               jmp_pin=Pin(18),         # LTC Input ...
                               in_base=Pin(18),         # ... from 'other' device
                               set_base=Pin(19)))       # Decoded LTC Input
    pt.eng.sm.append(rp2.StateMachine(5, pt.sync_and_read, freq=sm_freq,
                               jmp_pin=Pin(19),
                               in_base=Pin(19),
                               out_base=Pin(21),
                               set_base=Pin(21)))       # 'sync' from RX bitstream

    # correct clock dividers
    pt.eng.config_clocks(pt.eng.tc.fps)

    # set up IRQ handler
    for m in pt.eng.sm:
        m.irq(handler=pt.irq_handler, hard=True)

def apply_calibration():
    global displayfps

    period = None
    try:
        period = config.calibration['period']
    except:
        pass
    if period != None:
        try:
            pt.eng.micro_adjust(config.calibration[displayfps], period * 1000) # in ms
        except:
            pass


#---------------------------------------------
# Class for Custom Editing of Userbits/Name

class EditString(CustomItem, CallbackItem):

    def __init__(self, title, string, callback, \
                    alphabet=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"], \
                    selected=None, visible=None):
        super().__init__(title, visible=visible)
        self.callback = callback
        self.selected = None

        self.value = string
        self.alphabet = alphabet
        self.pos = 0

        self.items = []
        for i in range(len(string)):
            v = 0
            for j in range(len(self.alphabet)):
                if string[i] == self.alphabet[j]:
                    v = j
            self.items.append(v)

    def down(self):
        self.pos +=1
        if self.pos >= len(self.items):
            self.pos = -2
        self.draw()

    def select(self):
        if self.pos == -2:
            string = ""
            for i in range(len(self.items)):
                string += self.alphabet[self.items[i]]
            self.value = string
            return self.parent
        elif self.pos == -1:
            return self.parent
        else:
            self.items[self.pos] += 1
            if self.items[self.pos] >= len(self.alphabet):
                self.items[self.pos] = 0
        return self

    def draw(self):
        self.display.fill(0)

        for i in range(len(self.items)):
            self.display.text(self.alphabet[self.items[i]], 10*i, 15 if i == self.pos else 20, 1)

        if self.pos == -2:
            self.display.text("SAVE", 100, 40)
        else:
            self.display.text("save", 100, 40)

        if self.pos == -1:
            self.display.text("CANCEL", 0, 40)
        else:
            self.display.text("cancel", 0, 40)
        self.display.show()

    @property
    def value(self):
        return self._value

    @value.setter
    def value(self, value):
        self._value = value
        self._call_callable(self.callback, self._value)


# Make menus loop back to first item (single button navigation)
class MenuLoop(Menu):
    def move(self, direction: int = 1):
        if direction > 1 and type(self.current_screen) is not ValueItem and \
                    type(self.current_screen) is not EditString:
            if self.current_screen.selected + 1 == self.current_screen.count():
                self.current_screen.selected = 0
                return

        self.current_screen.up() if direction < 0 else self.current_screen.down()
        self.draw()

#---------------------------------------------
# Class for controlling MCP6S91 programable Amp
# (as used on official PCB)

class MCP6S91():
    GAIN_ADDR = b"\x40"
    GAINVALS = (1, 2, 4, 5, 8, 10, 16, 32)

    def __init__(self):
        self.cs = Pin(5, Pin.OUT)
        self.cs.value(1)

        self.spi = SPI(0, baudrate=10000, polarity=0, phase=0, bits=8,
                  firstbit=SPI.MSB, sck=Pin(6), mosi=Pin(7))

        self.power = False
        self.psu = Pin(23,Pin.OUT, value=1)

        self.powerdown(False)

    def gain(self, value):
        try:
            gainval = MCP6S91.GAINVALS.index(value)
        except ValueError:
            raise ValueError('MCP6S91 invalid gain {}'.format(value))

        self.cs.value(0)
        self.spi.write(MCP6S91.GAIN_ADDR)
        self.spi.write(gainval.to_bytes(1,"little"))
        self.cs.value(1)

    def powerdown(self, powerdown=True):
        if powerdown:
            self.cs.value(0)
            self.spi.write(b"\x01\x00")     # Power Down
            self.cs.value(1)

            self.power = False
            self.psu.value(0)
        else:
            self.cs.value(0)
            self.spi.write(b"\x00\x00")     # NOP/Power Up
            self.cs.value(1)

            self.power = True
            self.psu.value(1)

#---------------------------------------------
# Class for performing rolling averages

class Rolling:
    def __init__(self, size=5):
        self.max = size
        self.data = []
        for i in range(size):
            self.data.append([0.0, 0])

        self.dsum = 0.0

        self.enter = 0
        self.exit = 0
        self.size = 0

    def store(self, data, mark=0):
        if self.size == self.max:
            self.dsum -= self.data[self.exit][0]
            self.exit = (self.exit + 1) % self.max

        self.data[self.enter][0] = data
        self.data[self.enter][1] = mark
        self.dsum += data

        self.enter = (self.enter + 1) % self.max
        if self.size < self.max:
            self.size += 1

    def read(self):
        if self.size > 0:
            return(self.dsum/self.size)

    def store_read(self, data, mark=0):
        self.store(data, mark)
        return(self.read())

    def purge(self, mark):
        while self.size and self.data[self.exit][1] < mark:
            self.dsum -= self.data[self.exit][0]
            self.data[self.exit][0] = None
            self.exit = (self.exit + 1) % self.max
            self.size -= 1

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
# Class for measuring VSYS voltage
 
class Battery: 
    def __init__(self, ref=3.3 * 3): 
        self.ref = ref 
        self.sensor = ADC(29) 
 
    def read(self): 
        adc_value = self.sensor.read_u16() 
        return((self.ref/65536) * adc_value)
 
#---------------------------------------------

def callback_stop_start():
    global menu_hidden

    if pt.eng.is_running():
        pt.stop = True
        while pt.eng.is_running():
            utime.sleep(0.1)

        # Also stop any Monitor/Jam
        pt.eng.mode = pt.RUN
    else:
        menu_hidden = True

        pt.eng.sm = []
        pt.eng.sm.append(rp2.StateMachine(0, pt.auto_start, freq=int(pt.eng.tc.fps * 80 * 32)))
        add_more_state_machines()

        _thread.start_new_thread(pt.pico_timecode_thread, (pt.eng, lambda: pt.stop))

        # apply previously saved calibration value
        apply_calibration()


def callback_monitor():
    global menu_hidden, monitor

    menu_hidden = True

    if pt.eng.is_running():
        if pt.eng.mode == pt.RUN:
            pt.eng.mode = pt.MONITOR
            monitor = True
        elif pt.eng.mode == pt.MONITOR:
            pt.eng.mode = pt.RUN
            monitor = False
    else:
        callback_setting_monitor(config.setting['automon'][0])
        if monitor:
            pt.eng.mode = pt.MONITOR
        else:
            pt.eng.mode = pt.RUN


def callback_jam():
    global menu_hidden, monitor

    menu_hidden = True

    if pt.eng.is_running():
        pt.stop = True
        while pt.eng.is_running():
            utime.sleep(0.1)

    # Force Garbage collection
    gc.collect()


    # Reconfigure PIOs
    pt.eng.sm = []
    pt.eng.sm.append(rp2.StateMachine(0, pt.start_from_pin, freq=int(pt.eng.tc.fps * 80 * 32),
                               jmp_pin=Pin(21)))        # Sync from RX LTC
    add_more_state_machines()

    pt.eng.mode = pt.JAM
    callback_setting_monitor(config.setting['automon'][0])
    _thread.start_new_thread(pt.pico_timecode_thread, (pt.eng, lambda: pt.stop))

    # apply previously saved calibration value
    apply_calibration()


def callback_fps_df(set):
    # need to read before changing either FPS or DF
    pt.eng.tc.acquire()
    fps = pt.eng.tc.fps
    df = pt.eng.tc.df
    pt.eng.tc.release()

    if set=="Yes":
        df = True
    elif set == "No":
        df = False
    else:
        fps = float(set)

    pt.eng.tc.set_fps_df(fps, df)


def callback_tc_start(set):
    if not pt.eng.is_running():
        if set[2] == ":":
            pt.eng.tc.from_ascii(set, True)
        else:
            pt.eng.tc.from_ascii(set, False)


def callback_setting_output(set):
    global outamp

    if set=="Mic":
        outamp.gain(1)
    elif set=="Line":
        outamp.gain(10)
    else:
        outamp.gain(int(set))

def callback_setting_powersave(set):
    global powersave

    if set=="Off":
        powersave = 0
    elif set=="Screen":
        powersave = 1
    else:
        powersave = 2

def callback_setting_zoom(set):
    global zoom

    if set=="Yes":
        zoom = True
    else:
        zoom = False


def callback_setting_monitor(set):
    global monitor

    if set=="Yes":
        monitor = True
    else:
        monitor = False


def callback_setting_calibrate(set):
    global calibrate

    if set=="Always":
        calibrate = 2
    elif set=="Once":
        calibrate = 1
    else:
        calibrate = 0


def callback_setting_flashframe(set):
    if set=="Off":
        pt.eng.flashframe = -1
    else:
        pt.eng.flashframe = int(set)


def callback_userbits_userbits(set):
    if set=="Name":
        pt.eng.tc.user_from_ascii(config.userbits['ub_name'])
    elif set=="Digits":
        pt.eng.tc.user_from_bcd_hex(config.userbits['ub_digits'])
    else:
        pt.eng.tc.user_from_date(config.userbits['ub_date'])

def callback_userbits_ub_name(set):
    if set != config.userbits['ub_name']:
        config.set('userbits', 'ub_name', set)
        callback_userbits_userbits(config.userbits['userbits'][0])

def callback_userbits_ub_digits(set):
    if set != config.userbits['ub_digits']:
        config.set('userbits', 'ub_digits', set)
        callback_userbits_userbits(config.userbits['userbits'][0])

def callback_setting_save():
    global menu, menu_hidden

    menu_hidden = True
    for j in menu.current_screen._visible_items[0].parent._visible_items:
        try:
            config.set('setting', j.name, [j.items[j.selected], j.items])
        except AttributeError:
            pass

def callback_power_off():
    global keyA, keyB
    global OLED, outamp

    # Power off everything
    pt.stop = True
    while pt.eng.is_running():
        utime.sleep(0.1)

    if OLED:
        OLED.fill(0x0000)
        OLED.show()
        OLED.poweroff()

    outamp.powerdown()
    Pin(23, Pin.OUT, value=0)

    print("Power Off")

    # Set minimal CPU/USB freq to save power
    freq(18000000, 18000000)

    # Ensure buttons are not currently pressed
    while keyA.value()==0 or keyB.value()==0:
        utime.sleep(0.1)

    # do deepsleep() for minumum current, wake with either Key
    dormant_until_pins([15,17], False, False)
    reset()

def callback_exit():
    global menu_hidden

    menu_hidden = True

#---------------------------------------------

def OLED_display_thread(mode=pt.RUN):
    global OLED, menu, menu_hidden, monitor, displayfps
    global powersave, zoom, calibrate
    global keyA, keyB
    global outamp

    pt.eng = pt.engine()
    pt.eng.mode = mode
    pt.eng.set_stopped(True)

    # Output Amp
    outamp = MCP6S91()
    detIn  = Pin(16,Pin.IN,Pin.PULL_UP)
    detOut = Pin(14,Pin.IN,Pin.PULL_UP)

    # Force PWM mode on PSU, for cleaner 3V3
    psu = Pin(23,Pin.OUT, value=1)

    # apply saved settings
    callback_fps_df(config.setting['framerate'][0])
    callback_fps_df(config.setting['dropframe'][0])

    callback_setting_output(config.setting['output'][0])
    callback_setting_flashframe(config.setting['flashframe'][0])
    callback_tc_start(config.setting['tc_start'])
    callback_setting_powersave(config.setting['powersave'][0])
    callback_setting_zoom(config.setting['zoom'][0])
    callback_setting_monitor(config.setting['automon'][0])      # Monitor after Jam
    callback_setting_calibrate(config.setting['calibrate'][0])

    callback_userbits_userbits(config.userbits['userbits'][0])

    keyA = Pin(15,Pin.IN,Pin.PULL_UP)
    keyB = Pin(17,Pin.IN,Pin.PULL_UP)
    timerA = Neotimer(50)
    timerB = Neotimer(50)
    timerH = Neotimer(3000)
    timerP = Neotimer(30000)
    timerP.start()

    # Internal temp sensor
    sensor = Temperature()
    temp_avg = Rolling()

    # Battery voltage
    batTimer = Neotimer(10000)      # 10s period
    bat_raw = Battery()
    bat_avg = Rolling(6)            # avergage over 1min
    bat_avg.store(bat_raw.read())
    batWarn = Neotimer(1000)

    # Check which mode we start in
    startmode = config.hwconfig['startmode'][0]
    if startmode == 'Jam':
        pt.eng.mode = pt.JAM
    elif startmode == 'Monitor':
        pt.eng.mode = pt.MONITOR
        monitor = True
    else:
        pt.eng.mode = pt.RUN

    # alternatively, automatically Jam if booted with 'B' pressed
    if keyB.value() == 0:
        pt.eng.mode = pt.JAM

    # Initilize the display and menu
    display = config.hwconfig['display'][0]
    OLED = False
    timecode_fb = []
    if display != "None":
        # load font into FB
        for i in range(len(TimecodeFont)):
            timecode_fb.append(FrameBuffer(TimecodeFont[i], 16, 16, MONO_HMSB))

    if display == 'Pico1.3':
        OLED = OLED_1inch3_SPI()

    if OLED:
        OLED.fill(0x0000)
        OLED.text("Pico-Timecode " + pt.VERSION,64,0,OLED.white,0,2)
        OLED.text("www.github.com/",0,24,OLED.white,0,0)
        OLED.text("mungewell/",64,36,OLED.white,0,2)
        OLED.text("pico-timecode",128,48,OLED.white,0,1)
        OLED.show()

        utime.sleep(2)
        OLED.fill(0x0000)
        OLED.show()

        menu = MenuLoop(OLED, 5, 10)
        menu.set_screen(MenuScreen('A=Skip, B=Select')
            .add(CallbackItem("Exit", callback_exit, return_parent=True))
            .add(CallbackItem("Start/Stop Monitor", callback_monitor, visible=pt.eng.is_running))
            .add(CallbackItem("Jam/Sync RX", callback_jam))

            .add(ConfirmItem("Stop TX", callback_stop_start, "Confirm?", ('Yes', 'No'), \
                              visible=pt.eng.is_running))
            .add(CallbackItem("Start TX", callback_stop_start, visible=pt.eng.is_stopped))
            .add(SubMenuItem("TC Settings", visible=pt.eng.is_stopped)
                .add(EnumItem("framerate", config.setting['framerate'][1], callback_fps_df, \
                    selected=config.setting['framerate'][1].index(config.setting['framerate'][0])))
                .add(EnumItem("dropframe", config.setting['dropframe'][1], callback_fps_df, \
                    selected=config.setting['dropframe'][1].index(config.setting['dropframe'][0])))
                .add(EditString('tc_start', config.setting['tc_start'], callback_tc_start))
                .add(ConfirmItem("Save as Default", callback_setting_save, "Confirm?", ('Yes', 'No'))))

            .add(SubMenuItem("Unit Settings")
                .add(EnumItem("output", config.setting['output'][1], callback_setting_output, \
                    selected=config.setting['output'][1].index(config.setting['output'][0])))
                .add(EnumItem("flashframe", config.setting['flashframe'][1], callback_setting_flashframe, \
                    selected=config.setting['flashframe'][1].index(config.setting['flashframe'][0])))
                .add(EnumItem("powersave", config.setting['powersave'][1], callback_setting_powersave, \
                    selected=config.setting['powersave'][1].index(config.setting['powersave'][0])))
                .add(EnumItem("zoom", config.setting['zoom'][1], callback_setting_zoom, \
                    selected=config.setting['zoom'][1].index(config.setting['zoom'][0])))
                .add(EnumItem("automon", config.setting['automon'][1], callback_setting_monitor, \
                    selected=config.setting['automon'][1].index(config.setting['automon'][0])))
                .add(EnumItem("calibrate", config.setting['calibrate'][1], callback_setting_calibrate, \
                    selected=config.setting['calibrate'][1].index(config.setting['calibrate'][0])))
                .add(ConfirmItem("Save as Default", callback_setting_save, "Confirm?", ('Yes', 'No'))))

            .add(SubMenuItem("User Bits")
                .add(EnumItem("userbits", config.userbits['userbits'][1], callback_userbits_userbits, \
                    selected=config.userbits['userbits'][1].index(config.userbits['userbits'][0])))
                .add(EditString('ub_name', config.userbits['ub_name'], callback_userbits_ub_name, \
                    alphabet=[" ", "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", \
                        "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z", \
                        "0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "+", "-", "*", "_"]))
                .add(EditString('ub_digits', config.userbits['ub_digits'], callback_userbits_ub_digits, \
                    alphabet=["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "A", "B", "C", "D", "E", "F"])))

            .add(ConfirmItem("Power Off", callback_power_off, "Confirm?", ('Yes', 'No'), \
                              visible=pt.eng.is_stopped))
        )

    # Reduce the CPU clock, for better computation of PIO freqs
    if freq() != 120000000:
        freq(120000000)

    # Allocate appropriate StateMachines, and their pins
    pt.eng.sm = []
    if pt.eng.mode > pt.MONITOR:
        pt.eng.sm.append(rp2.StateMachine(0, pt.start_from_pin, freq=int(pt.eng.tc.fps * 80 * 32),
                                   jmp_pin=Pin(21)))        # Sync from RX LTC
    else:
        pt.eng.sm.append(rp2.StateMachine(0, pt.auto_start, freq=int(pt.eng.tc.fps * 80 * 32)))
    add_more_state_machines()

    # Start up threads
    _thread.start_new_thread(pt.pico_timecode_thread, (pt.eng, lambda: pt.stop))

    while True:
        disp = pt.timecode()
        disp.set_fps_df(pt.eng.tc.fps, pt.eng.tc.df)

        displayfps = "{:.2f}".format(disp.fps) + ("-DF" if disp.df == True else "")
        cycle_us = (1000000.0 / disp.fps)

        if menu_hidden == True:
            if OLED:
                OLED.fill(0x0000)
                OLED.text("A=Menu" ,0,2,OLED.white)
                OLED.text(displayfps,128,2,OLED.white,1,1)
                OLED.show()
            else:
                print("Format:", displayfps)

        tx_asc="--------"
        tx_ticks = 0
        tx_ub = ""
        rx_asc="--:--:--:--"
        rx_ub = ""

        monTimer = None
        cal_after_jam = 0
        powersave_active = False

        pid = PID(500, 20, 0.0, setpoint=0)
        pid.auto_mode = False
        pid.sample_time = 1
        pid.output_limits = (-50.0, 50.0)

        # apply previously saved calibration value
        apply_calibration()

        period = 10
        try:
            period = config.calibration['period']
        except:
            pass

        phase = Rolling(30 * period)  	# sized for max fps, but really
                                        # we only get ~4fps with RX/CAL mode
        adj_avg = Rolling(120)          # average over 2 minutes

        while True:
            # Monitor battery every 1s and eval
            if batTimer.repeat_execution():
                if (powersave_active and powersave > 1):
                    # ADCs currently 'stall' in hardware powersave
                    # temporarily exit to make reading
                    pt.eng.set_powersave(False)
                    utime.sleep(0.1)
                    bat_avg.store(bat_raw.read())
                    pt.eng.set_powersave(True)
                else:
                    bat_avg.store(bat_raw.read())

                #print(disp.to_ascii(), bat_avg.read())

                # Dead Battery - turn off Pico, wake with buttons
                if bat_avg.read() < 2.5 and batWarn.started:
                    callback_power_off()

                # Warn user battery is low
                if bat_avg.read() < 3.2:
                    if not batWarn.started:
                        batWarn.start()

            if OLED and menu_hidden == False:
                if timerA.debounce_signal(keyA.value()==0):
                    menu.move(2)        # Requires patched umenu to work
                if timerB.debounce_signal(keyB.value()==0):
                    menu.click()
                timerP.start()
                menu.draw()

                # Clear screen after Menu Exits
                if menu_hidden == True:
                    OLED.fill(0x0000)
                    OLED.text("A=Menu" ,0,2,OLED.white)
                    OLED.text(displayfps,128,2,OLED.white,1,1)
                    OLED.show()

                    tx_asc="--------"
                    tx_ticks = 0
                    tx_ub = ""
                    rx_ub = ""
                    timerP.start()
            else:
                if timerA.debounce_signal(keyA.value()==0) or \
                        timerB.debounce_signal(keyB.value()==0):
                    if powersave_active == True:
                        if pt.eng.get_powersave():
                            pt.eng.set_powersave(False)
                        powersave_active = False
                        if OLED:
                            OLED.poweron()
                        timerP.start()

                        print("Exiting PowerSave")

                    elif OLED and keyA.value()==0:
                        # enter the Menu...
                        menu.reset()
                        menu_hidden = False
                        timerP.stop()

                # Hold B for 3s to (re)start jam
                if pt.eng.mode <= pt.MONITOR and timerH.hold_signal(keyB.value()==0) and \
                        not powersave_active and detIn.value() == 0:
                    callback_jam()

                # Check whether to enter power save mode
                if pt.eng.mode == pt.RUN:
                    if powersave_active == False and powersave > 0:
                        if timerP.finished():
                            print("Entering PowerSave")
                            utime.sleep(0.1)

                            if powersave > 1:
                                pt.eng.set_powersave(True)
                            powersave_active = True
                            if OLED:
                                OLED.poweroff()
                            timerP.stop()

                    # If power save is active, we don't update the screen
                    if powersave_active == True:
                        utime.sleep(0.1)
                        if powersave > 1:
                            powersave_active = pt.eng.get_powersave()
                            if not powersave_active:
                                # hardware exited, disable hardware powersave option
                                powersave = 1
                                timerP.start()

                        # Low Battery - disable powersave so we can notify on screen
                        if bat_avg.read() < 3.0:
                            if pt.eng.get_powersave():
                                pt.eng.set_powersave(False)
                            powersave_active = False
                            powersave = 0

                        if powersave_active:
                            continue
                        else:
                            if OLED:
                                OLED.poweron()
                            timerP.start()

                            print("Powersave Exited")

                # Attempt to align display with the TX timing
                if pt.eng.mode == pt.RUN:
                    t1 = pt.tx_ticks_us

                    if tx_ticks == t1:
                        ticks = utime.ticks_us()

                        # we will stall if < 5ms until next expected frame
                        d = cycle_us - utime.ticks_diff(ticks, t1)
                        if d > 0 and d < 5000:
                            while d > 0:
                                ticks = utime.ticks_us()
                                d = cycle_us - utime.ticks_diff(ticks, t1)
                        else:
                            utime.sleep(0.001)
                            continue

                # Figure out what TX frame to display
                while True:
                    t1 = pt.tx_ticks_us
                    raw = pt.tx_raw
                    t2 = pt.tx_ticks_us

                    if t1==t2:
                        disp.from_raw(raw)
                        break

                # Figure out what RX frame to display
                if pt.eng.mode > pt.RUN:
                    while True:
                        r1 = pt.rx_ticks_us
                        rf1 = pt.eng.sm[5].rx_fifo()
                        g = pt.eng.rc.to_raw()
                        rf2 = pt.eng.sm[5].rx_fifo()
                        r2 = pt.rx_ticks_us

                        t2 = pt.tx_ticks_us
                        if r1==r2 and rf1==rf2:
                            break

                # Draw the main TC counter
                # check which characters of the TC have changed
                asc = disp.to_ascii(False)
                if tx_asc != asc:
                    if OLED:
                        for c in range(len(asc)):
                            if asc[c]!=tx_asc[c]:
                                break
                        for i in range(7,(c&6)-1,-1):
                            # blit in reverse order, offsetting to hide ':'
                            OLED.blit(timecode_fb[int(asc[i])],
                                (16*i)-(4 if i&1 else 0), 48)

                        # Drop Frame, convert ":" to "."
                        if disp.df:
                            OLED.fill_rect(96,52,4,4,OLED.black)

                        # blank left most ':'
                        if c < 2:
                            OLED.fill_rect(0,48,4,16,OLED.black)

                        OLED.show(49 ,64, c*16)
                    elif pt.eng.mode == pt.RUN:     # don't flood monitor/calibration prints
                        print(disp.to_ascii()) #, utime.ticks_diff(t1, tx_ticks))

                    tx_asc = asc
                    tx_ticks = t1

                    # update Userbits display
                    ub = pt.eng.tc.user_to_ascii()
                    if tx_ub != ub:
                        if OLED:
                            OLED.fill_rect(0,38,128,8,OLED.black)
                            OLED.text(ub,64,38,OLED.white,1,2)
                            OLED.show(38,46)
                        tx_ub = ub


                if pt.eng.mode > pt.RUN:
                    # every code left in FIFO, means that we have outdated TC
                    disp.from_raw(g)
                    for i in range(int(rf1/2)):
                        disp.next_frame()

                    # Show RX Timecode
                    asc = disp.to_ascii()
                    if rx_asc != asc:
                        if OLED:
                            OLED.text(asc,64,22,OLED.white,1,2)
                            OLED.show(22,32)
                        rx_asc = asc

                    # Show RX Userbits
                    ub = pt.eng.rc.user_to_ascii()
                    if rx_ub != ub:
                        if OLED:
                            OLED.fill_rect(0,12,128,8,OLED.black)
                            OLED.text(ub,64,12,OLED.white,1,2)
                            OLED.show(12,20)
                        rx_ub = ub

                    # Draw an error bar to represent timing phase between TX and RX
                    # Positive Delta = TX is ahead of RX, bar is shown to the right
                    # and should increase 'duty' to slow down it's bit-clock
                    now = utime.time()
                    if pt.eng.mode == pt.MONITOR:
                        d = utime.ticks_diff(r1, t2) / cycle_us

                        # RX is offset by ~2/3 bit
                        d -= 2.0/ (3 * 80)

                        # correct delta, if not adjacent frame
                        if d > -2 and d <= 0:
                            while d < -0.5:
                                d += 1.0
                        elif d < 2 and d >= 0:
                            while d > 0.5:
                                d -= 1.0

                        # Rolling average
                        if d >= -0.5 and d <= 0.5:
                            phase.store(d, now)

                        # Check if it's the first received frame
                        if monTimer == None:
                            if cal_after_jam > 0:
                                # wait 1m
                                monTimer = Neotimer(60000)
                                monTimer.start()
                            else:
                                # wait 1s
                                monTimer = Neotimer(1000)
                                monTimer.start()

                        elif monTimer.finished():
                            if cal_after_jam > 0:
                                if pid.auto_mode == False:
                                    pid.set_auto_mode(True, last_output=pt.eng.calval)
                                    monTimer = Neotimer(1000)

                                # we'll start calibration with 1s period for 400s, then 
                                # switch to specified period for more accurate calibration
                                if cal_after_jam < 340:
                                    phase.purge(now - 1)
                                    adjust = pid(phase.read())
                                    pt.eng.micro_adjust(adjust, 1000)
                                else:
                                    phase.purge(now - period)
                                    adjust = pid(phase.read())
                                    pt.eng.micro_adjust(adjust, period * 1000)

                                print(disp.to_ascii(), d, phase.read(), pt.eng.calval, \
                                      temp_avg.store_read(sensor.read()), \
                                      adj_avg.store_read(adjust), \
                                      pt.eng.tc.user_to_ascii(), \
                                      pid.components)

                                # stop calibration after 10mins and save calculated value
                                cal_after_jam += 1
                                if cal_after_jam > 540:
                                    new_cal_value = adj_avg.read()
                                    pt.eng.micro_adjust(new_cal_value, period * 1000)

                                    # Purge everything, to clean up memory!
                                    phase.purge(now)
                                    adj_avg.purge(1)
                                    gc.collect()

                                    config.set('calibration', displayfps, new_cal_value)
                                    config.set('calibration', 'period', period)

                                    if calibrate == 1:
                                        callback_setting_calibrate("No")

                                    cal_after_jam = 0
                                    pid.auto_mode = False

                            else:
                                print(disp.to_ascii(), d, phase.read(), pt.eng.calval, \
                                      temp_avg.store_read(sensor.read()))

                            monTimer.start()

                        if OLED:
                            if pt.eng.mode == pt.MONITOR and cal_after_jam > 0:
                                # CAL = Sync'ed to RX and calibrating XTAL
                                OLED.text("CAL ",0,22,OLED.white)
                            else:
                                OLED.text("RX  ",0,22,OLED.white)

                            OLED.vline(64, 33, 2, OLED.white)
                            if zoom == True:
                                length = int(1280 * d)
                                OLED.vline(0, 32, 4, OLED.black)
                                OLED.vline(127, 32, 4, OLED.black)
                            else:
                                length = int(128 * d)

                                # markers at side to indicate full view
                                # -1/2 to +1/2 a frame is displayed
                                OLED.vline(0, 32, 4, OLED.white)
                                OLED.vline(127, 32, 4, OLED.white)

                            if d > 0:
                                OLED.hline(64, 33, length, OLED.white)
                                OLED.hline(64, 34, length, OLED.white)
                            else:
                                OLED.hline(64+length, 33, -length, OLED.white)
                                OLED.hline(64+length, 34, -length, OLED.white)

                    if pt.eng.mode > pt.MONITOR:
                        if OLED:
                            OLED.text("Jam ",0,22,OLED.white)

                            # Draw a line representing time until Jam complete
                            OLED.vline(0, 32, 4, OLED.white)
                            OLED.hline(0, 33, pt.eng.mode * 2, OLED.white)
                            OLED.hline(0, 34, pt.eng.mode * 2, OLED.white)

                        cal_after_jam = calibrate

                    if pt.eng.mode > pt.RUN:
                        if OLED:
                            # Show RX bar
                            OLED.show(33,36)

                            # clear bar ready for next frame
                            OLED.hline(1, 33, 127, OLED.black)
                            OLED.hline(1, 34, 127, OLED.black)

                        if not (monitor or cal_after_jam) \
                               and pt.eng.mode == pt.MONITOR:
                            if OLED:
                                OLED.fill_rect(0,12,128,24,OLED.black)
                                OLED.show()
                            pt.eng.mode = pt.RUN
                    else:
                        monitor = False

                        # catch if user has cancelled jam/calibrate
                        cal_after_jam = 0
                        pid.auto_mode = False

                        # Purge everything, to clean up memory!
                        phase.purge(now)
                        adj_avg.purge(1)
                        gc.collect()

            if batWarn.finished():
                if bat_avg.read() < 3.2:
                    if OLED:
                        OLED.fill_rect(0,38,128,10, \
                                (OLED.white if not pt.tx_raw & 0x00000100 else OLED.black))
                        OLED.text("Battery Low",64,38, \
                                (OLED.white if pt.tx_raw & 0x00000100 else OLED.black),1,2)
                        OLED.show(38, 46)
                    else:
                        print("Battery Low")
                    batWarn.start()
                else:
                    batWarn.stop()
                    tx_ub = ""

            if pt.eng.mode == pt.HALTED:
                if OLED:
                    OLED.fill_rect(0,51,128,10,OLED.black)
                    OLED.text("Underflow Error",64,53,OLED.white,1,2)
                    OLED.show(49 ,64)
                else:
                    print("HALTED")
                pt.stop = True

            if pt.eng.is_stopped():
                break

#---------------------------------------------

if __name__ == "__main__":
    print("Pico-Timecode " + pt.VERSION)
    print("www.github.com/mungewell/pico-timecode")
    utime.sleep(2)

    OLED_display_thread()
