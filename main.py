# Pico-Timcode for Raspberry-Pi Pico
# (c) 2023-05-08 Simon Wood <simon@mungewell.org>
#
# https://github.com/mungewell/pico-timecode

# implement a basic UI on hardware with Pico-OLED-1.3
#
# Pico-OLED-1.3 is connected as follows:
# GP6  - I2C_SDA
# GP7  - I2C_CLK
# GP8  - OLED_DC
# GP9  - CS
# GP10 - OLED_CLK
# GP11 - OLED_DIN
# GP13 - RESET
# GP15 - User key 'A'
# GP17 - User key 'B'
#
# GP25 - Onboard LED
#
# We'll allocate the following to the PIO blocks
#
# GP18 - RX: LTC_INPUT  (physical connection)
# GP19 - RX: raw/decoded LTC input (debug)
# GP20 - ditto - Hack to accomodate running out of memory
# GP21 - RX: sync from LTC input (debug)
#
# GP22 - TX: raw LTC bitstream output (debug)
# GP13 - TX: LTC_OUTPUT (physical connection)
#
# In the future we will also use:
#
# GP14 - OUT_DET (reserved)
# GP16 - IN_DET (reserved)
# GP26 - BLINK_LED (reserved)
# (this will enable both Pico and off board LED simulataneously)
#
# We will also use the I2C bus to 'talk' to other devices...
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

# Requires modified lib
# https://github.com/mungewell/pico-oled-1.3-driver/tree/pico_timecode

from libs.PicoOled13 import *
from libs.ht16k33segment import HT16K33Segment

# Special font, for display the TX'ed timecode in a particular way
from libs.fonts import TimecodeFont
from framebuf import FrameBuffer, MONO_HMSB

import pico_timecode as pt

import machine
import _thread
import utime
import rp2
import gc

# Set up (extra) globals
menu = None
powersave = False
zoom = False
monitor = False
calibrate = False
menu_hidden = True

def add_more_state_machines():
    sm_freq = int(pt.eng.tc.fps * 80 * 32)

    # TX State Machines
    pt.eng.sm.append(rp2.StateMachine(1, pt.blink_led, freq=sm_freq,
                               set_base=machine.Pin(25)))       # LED on Pico board + GPIO26/27/28
    pt.eng.sm.append(rp2.StateMachine(2, pt.buffer_out, freq=sm_freq,
                               out_base=machine.Pin(22)))       # Output of 'raw' bitstream
    pt.eng.sm.append(rp2.StateMachine(3, pt.encode_dmc, freq=sm_freq,
                               jmp_pin=machine.Pin(22),
                               in_base=machine.Pin(13),         # same as pin as out
                               out_base=machine.Pin(13)))       # Encoded LTC Output

    # RX State Machines
    pt.eng.sm.append(rp2.StateMachine(4, pt.decode_dmc, freq=sm_freq,
                               jmp_pin=machine.Pin(18),         # LTC Input ...
                               in_base=machine.Pin(18),         # ... from 'other' device
                               set_base=machine.Pin(19)))       # Decoded LTC Input
    pt.eng.sm.append(rp2.StateMachine(5, pt.sync_and_read, freq=sm_freq,
                               jmp_pin=machine.Pin(19),
                               in_base=machine.Pin(19),
                               out_base=machine.Pin(21),
                               set_base=machine.Pin(21)))       # 'sync' from RX bitstream

    # correct clock dividers
    pt.eng.frig_clocks(pt.eng.tc.fps)

    # set up IRQ handler
    for m in pt.eng.sm:
        m.irq(handler=pt.irq_handler, hard=True)

#---------------------------------------------
# Class for performing rolling averages

class Rolling:
    def __init__(self, size=5):
        self.max = size
        self.data = []
        for i in range(size):
            self.data.append([0.0, 0.0])

        self.dsum = 0.0

        self.enter = 0
        self.exit = 0
        self.size = 0

    def store(self, data, mark=0.0):
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

    def store_read(self, data, mark=0.0):
        self.store(data, mark)
        return(self.read())

    def purge(self, mark):
        while self.size and self.data[self.exit][1] < mark:
            self.dsum -= self.data[self.exit][0]
            self.exit = (self.exit + 1) % self.max
            self.size -= 1

#---------------------------------------------
# Class for using the internal temp sensor
# 3v3 to be connected to vREF (pin 35)

class Temperature:
    def __init__(self, ref=3.3):
        self.ref = ref
        self.sensor = machine.ADC(4)

    def read(self):
        adc_value = self.sensor.read_u16()
        volt = (self.ref/65536) * adc_value

        return(27-(volt-0.706)/0.001721)

#---------------------------------------------

def callback_stop_start():
    #global eng, stop
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


def callback_monitor():
    global menu_hidden, monitor

    menu_hidden = True

    if pt.eng.mode == pt.RUN:
        pt.eng.mode = pt.MONITOR
        monitor = True
    elif pt.eng.mode == pt.MONITOR:
        pt.eng.mode = pt.RUN
        callback_setting_monitor(config.setting['monitor'][0])


def callback_jam():
    global menu_hidden, monitor

    menu_hidden = True

    if pt.eng.is_running():
        pt.stop = True
        while pt.eng.is_running():
            utime.sleep(0.1)

    # Force Garbage collection
    gc.collect()

    # Turn off Jam if already enabled
    pt.eng.sm = []
    pt.eng.sm.append(rp2.StateMachine(0, pt.start_from_pin, freq=int(pt.eng.tc.fps * 80 * 32),
                               jmp_pin=machine.Pin(21)))        # Sync from RX LTC
    add_more_state_machines()

    pt.eng.mode = pt.JAM
    _thread.start_new_thread(pt.pico_timecode_thread, (pt.eng, lambda: pt.stop))


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


def callback_setting_powersave(set):
    global powersave

    if set=="Yes":
        powersave = True
    else:
        powersave = False

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


def callback_setting_userbits(set):
    if set=="Text":
        pt.eng.tc.user_from_ascii(config.setting['ub_ascii'])
    elif set=="Digits":
        pt.eng.tc.user_from_bcd_hex(config.setting['ub_bcd'])
    else:
        pt.eng.tc.user_from_date(config.setting['ub_date'])


def callback_setting_save():
    global menu

    for j in menu.current_screen._visible_items[0].parent._visible_items:
        try:
            config.set('setting', j.name, [j.items[j.selected], j.items])
        except AttributeError:
            pass


def callback_exit():
    global menu_hidden

    menu_hidden = True

#---------------------------------------------

def OLED_display_thread(mode=pt.RUN):
    global menu, menu_hidden
    global powersave, zoom, calibrate

    pt.eng = pt.engine()
    pt.eng.mode = mode
    pt.eng.set_stopped(True)

    # apply saved settings
    callback_fps_df(config.setting['framerate'][0])
    callback_fps_df(config.setting['dropframe'][0])

    callback_setting_flashframe(config.setting['flashframe'][0])
    callback_setting_userbits(config.setting['userbits'][0])
    callback_setting_powersave(config.setting['powersave'][0])
    callback_setting_zoom(config.setting['zoom'][0])
    callback_setting_monitor(config.setting['monitor'][0])
    callback_setting_calibrate(config.setting['calibrate'][0])

    keyA = Pin(15,Pin.IN,Pin.PULL_UP)
    keyB = Pin(17,Pin.IN,Pin.PULL_UP)
    timerA = Neotimer(50)
    timerB = Neotimer(50)
    timerH = Neotimer(3000)

    # Internal temp sensor
    sensor = Temperature()
    temp_avg = Rolling()

    # automatically Jam if booted with 'B' pressed
    if keyB.value() == 0:
        pt.eng.mode=pt.JAM

    # Configure Digi-Slate
    debug = Pin(27,Pin.OUT)
    debug.off()

    keyC = Pin(4,Pin.IN,Pin.PULL_UP)
    keyR = Pin(5,Pin.IN,Pin.PULL_UP)
    timerC = Neotimer(15)
    timerR = Neotimer(50)
    timerS = Neotimer(1000)

    slate_rotated = False
    slate_open = 0

    slate_L = HT16K33Segment(machine.I2C(1, scl=Pin(3), sda=Pin(2)), \
            i2c_address=0x70)
    slate_R = HT16K33Segment(machine.I2C(1, scl=Pin(3), sda=Pin(2)), \
            i2c_address=0x71)

    slate_HM = slate_L
    slate_HM.set_brightness(5)

    slate_SF = slate_R
    slate_SF.set_brightness(5)
    slate_SF.rotate()

    # Disply unit 'name', but not all characters supported...
    try:
        for i in range(4):
            slate_HM.set_character(config.setting['ub_ascii'][i], i)
            slate_SF.set_character(" ", i)
    except AssertionError:
        for i in range(4):
            slate_HM.set_character("-", i)
            slate_SF.set_character("-", i)
    slate_HM.draw()
    slate_SF.draw()
    timerS.start()

    # load font into FB
    timecode_fb = []
    for i in range(len(TimecodeFont)):
        timecode_fb.append(FrameBuffer(TimecodeFont[i], 16, 16, MONO_HMSB))

    OLED = OLED_1inch3_SPI()
    OLED.fill(0x0000)
    OLED.text("Pico-Timecode",64,0,OLED.white,0,2)
    OLED.text("www.github.com/",0,24,OLED.white,0,0)
    OLED.text("mungewell/",64,36,OLED.white,0,2)
    OLED.text("pico-timecode",128,48,OLED.white,0,1)
    OLED.show()

    utime.sleep(2)
    OLED.fill(0x0000)
    OLED.show()

    menu = Menu(OLED, 5, 10)
    menu.set_screen(MenuScreen('A=Skip, B=Select')
        .add(CallbackItem("Exit", callback_exit, return_parent=True))
        .add(CallbackItem("Start TX", callback_stop_start, visible=pt.eng.is_stopped))
        .add(CallbackItem("Start/Stop Monitor", callback_monitor, visible=pt.eng.is_running))
        .add(CallbackItem("Jam/Sync RX", callback_jam))
        .add(ConfirmItem("Stop TX", callback_stop_start, "Confirm?", ('Yes', 'No'), \
                          visible=pt.eng.is_running))

        .add(SubMenuItem("TC Settings", visible=pt.eng.is_stopped)
            .add(EnumItem("framerate", config.setting['framerate'][1], callback_fps_df, \
                selected=config.setting['framerate'][1].index(config.setting['framerate'][0])))
            .add(EnumItem("dropframe", config.setting['dropframe'][1], callback_fps_df, \
                selected=config.setting['dropframe'][1].index(config.setting['dropframe'][0])))
            .add(ConfirmItem("Save as Default", callback_setting_save, "Confirm?", ('Yes', 'No'))))

        .add(SubMenuItem("Unit Settings")
            .add(EnumItem("flashframe", config.setting['flashframe'][1], callback_setting_flashframe, \
                selected=config.setting['flashframe'][1].index(config.setting['flashframe'][0])))
            .add(EnumItem("userbits", config.setting['userbits'][1], callback_setting_userbits, \
                selected=config.setting['userbits'][1].index(config.setting['userbits'][0])))
            .add(EnumItem("powersave", config.setting['powersave'][1], callback_setting_powersave, \
                selected=config.setting['powersave'][1].index(config.setting['powersave'][0])))
            .add(EnumItem("zoom", config.setting['zoom'][1], callback_setting_zoom, \
                selected=config.setting['zoom'][1].index(config.setting['zoom'][0])))
            .add(EnumItem("monitor", config.setting['monitor'][1], callback_setting_monitor, \
                selected=config.setting['monitor'][1].index(config.setting['monitor'][0])))
            .add(EnumItem("calibrate", config.setting['calibrate'][1], callback_setting_calibrate, \
                selected=config.setting['calibrate'][1].index(config.setting['calibrate'][0])))
            .add(ConfirmItem("Save as Default", callback_setting_save, "Confirm?", ('Yes', 'No'))))
    )

    # Reduce the CPU clock, for better computation of PIO freqs
    if machine.freq() != 120000000:
        machine.freq(120000000)

    # Allocate appropriate StateMachines, and their pins
    pt.eng.sm = []
    if pt.eng.mode > pt.MONITOR:
        pt.eng.sm.append(rp2.StateMachine(0, pt.start_from_pin, freq=int(pt.eng.tc.fps * 80 * 32),
                                   jmp_pin=machine.Pin(21)))        # Sync from RX LTC
    else:
        pt.eng.sm.append(rp2.StateMachine(0, pt.auto_start, freq=int(pt.eng.tc.fps * 80 * 32)))
    add_more_state_machines()

    # Start up threads
    _thread.start_new_thread(pt.pico_timecode_thread, (pt.eng, lambda: pt.stop))

    while True:
        dc = pt.timecode()
        dc.set_fps_df(pt.eng.tc.fps, pt.eng.tc.df)

        format = "{:.2f}".format(dc.fps) + ("-DF" if dc.df == True else "")
        cycle_us = (1000000.0 / dc.fps)

        if menu_hidden == True:
            OLED.fill(0x0000)
            OLED.text("A=Menu" ,0,2,OLED.white)
            OLED.text(format,128,2,OLED.white,1,1)
            OLED.show()

        tx_asc="--------"
        tx_ticks = 0
        tx_ub = ""
        rx_ub = ""

        sync_after_jam = 0
        jam_started = False
        powersave_active = False
        last_button = utime.time()

        next_mon = None
        next_mon_raw = 0

        pid = PID(500, 12.5, 0.0, setpoint=0)
        pid.auto_mode = False
        pid.sample_time = 1
        pid.output_limits = (-50.0, 50.0)

        # apply previously saved calibration value
        period = 10
        try:
            period = config.calibration['period']
        except:
            pass
        try:
            pt.eng.micro_adjust(config.calibration[format], period)
        except:
            pass
        phase = Rolling(30 * period)  	# sized for max fps, but really
                                        # we only get ~4fps with RX/CAL mode
        adj_avg = Rolling(240)          # average over 4 minutes

        while True:
            now = utime.time()
            if menu_hidden == False:
                if timerA.debounce_signal(keyA.value()==0):
                    menu.move(2)        # Requires patched umenu to work
                if timerB.debounce_signal(keyB.value()==0):
                    menu.click()
                last_button = now
                menu.draw()

                # Clear screen after Menu Exits
                if menu_hidden == True:
                    OLED.fill(0x0000)
                    OLED.text("A=Menu" ,0,2,OLED.white)
                    OLED.text(format,128,2,OLED.white,1,1)
                    OLED.show()

                    tx_asc="--------"
                    tx_ticks = 0
                    tx_ub = ""
                    rx_ub = ""
            else:
                if timerA.debounce_signal(keyA.value()==0):
                    if powersave_active == True:
                        OLED.on()
                        powersave_active = False
                    last_button = now

                    # enter the Menu...
                    menu.reset()
                    menu_hidden = False

                if timerB.debounce_signal(keyB.value()==0):
                    if powersave_active == True:
                        OLED.on()
                        powersave_active = False
                    last_button = now

                # Hold B for 3s to (re)start jam
                if pt.eng.mode <= pt.MONITOR and timerH.hold_signal(keyB.value()==0) \
                        and not powersave_active:
                    callback_jam()

                # Debug - freeze screen
                while pt.eng.mode == pt.MONITOR and timerB.debounce_signal(keyB.value()==0):
                    utime.sleep(1)

                # Check whether to enter power save mode
                if pt.eng.mode == pt.RUN:
                    if powersave_active == False and powersave == True:
                        if (now - 30) > last_button:
                            print("Entering PowerSave")
                            OLED.off()
                            powersave_active = True

                    # If power save is active, we don't update the screen
                    if powersave_active == True:
                        continue

                # Check for slate rotation
                if slate_rotated == False and timerR.debounce_signal(keyR.value()==0):
                    slate_HM = slate_R
                    slate_HM.rotate()
                    slate_HM.set_colon(False)
                    slate_SF = slate_L
                    slate_SF.rotate()
                    slate_rotated = True
                elif slate_rotated == True and timerR.debounce_signal(keyR.value()==1):
                    slate_HM = slate_L
                    slate_HM.rotate()
                    slate_HM.set_colon(False)
                    slate_SF = slate_R
                    slate_SF.rotate()
                    slate_rotated = False

                # Display FPS on slate, when clapper is lifted
                if slate_open < 1 and timerC.debounce_signal(keyC.value()==0):
                    pt.sl_ticks_us = 0

                    # resuse PIO to accurately time clapper
                    if pt.eng.mode == pt.RUN:
                        pt.eng.sm[0].active(0)
                        pt.eng.sm[0] = rp2.StateMachine(0, pt.clapper_from_pin, \
                                freq=int(pt.eng.tc.fps * 80 * 32),
                                jmp_pin=machine.Pin(4))        # Sync from clapper
                        pt.eng.sm[0].active(1)

                    slate_open = 1
                    timerS.start()
                    for i in range(4):
                        slate_HM.set_character(format[i+(1 if i>1 else 0)], \
                                i, has_dot=(True if i==1 else False))
                        slate_SF.set_character(" ", i)
                    if len(format) > 5:
                        '''
                        - = 6         = 0x40
                        d = 1 2 3 4 6 = 0x5e
                        f = 0 4 5 6   = 0x71
                        '''
                        slate_SF.set_glyph(0x40, 0)
                        slate_SF.set_glyph(0x5e, 1)
                        slate_SF.set_glyph(0x71, 2)

                    slate_HM.draw()
                    slate_SF.set_colon(False)
                    slate_SF.draw()

                # Check for clapper closing
                if slate_open == 1 and timerC.debounce_signal(keyC.value()==1):
                    slate_open = 0
                    timerS.start()

                # Attempt to align display with the TX timing
                t1 = pt.tx_ticks_us
                if pt.eng.mode == pt.RUN:
                    if tx_ticks == t1:
                        continue

                # Draw the main TC counter
                debug.on()
                while True:
                    t1 = pt.tx_ticks_us
                    offset = pt.tx_offset
                    raw = pt.tx_raw
                    t2 = pt.tx_ticks_us

                    if t1==t2:
                        dc.from_raw(raw)
                        break

                # correct read TC value, for frames queued in FIFO
                dc.prev_frame()
                for i in range(offset):
                    dc.prev_frame()
                asc = dc.to_ascii(False)

                debug.off()

                # check which characters of the TC have changed
                if tx_asc != asc:
                    # update Digi-Slate
                    if slate_open == 0 and timerS.finished():
                        slate_open = -1
                        slate_HM.clear()
                        slate_HM.draw()
                        slate_SF.clear()
                        slate_SF.draw()
                    elif slate_open == 1 and timerS.finished():
                        for i in range(4):
                            if sync_after_jam == 0:
                                slate_HM.set_character(asc[i], i, \
                                        has_dot=(True if i&1 else False))
                            slate_SF.set_character(asc[4+i], i)
                        slate_HM.draw()
                        slate_SF.set_colon(True)
                        slate_SF.draw()
                    elif slate_open == 0:
                        # slate just closed, maybe update display
                        if pt.sl_ticks_us != 0:
                            d = utime.ticks_diff(pt.tx_ticks_us, pt.sl_ticks_us) / cycle_us 
                            #print("slate closed", pt.tx_ticks_us, pt.sl_ticks_us, d)
                            if d < 0.0:
                                # Correct as Slate closed in previous frame
                                for i in range(4):
                                    slate_HM.set_character(asc[i], i, \
                                        has_dot=(True if i&1 else False))
                                    slate_SF.set_character(asc[4+i], i)
                                slate_HM.draw()
                                #slate_SF.set_colon(True)
                                slate_SF.set_colon(False)
                                slate_SF.draw()

                            pt.sl_ticks_us = 0

                    # update OLED display
                    for c in range(len(asc)):
                        if asc[c]!=tx_asc[c]:
                            break
                    for i in range(7,(c&6)-1,-1):
                        # blit in reverse order, offsetting to hide ':'
                        OLED.blit(timecode_fb[int(asc[i])],
                            (16*i)-(4 if i&1 else 0), 48)

                    # blank left most ':'
                    if c < 2:
                        OLED.rect(0,48,4,16,OLED.black,True)

                    tx_asc = asc
                    tx_ticks = t1
                    OLED.show(49 ,64, c*16)

                    # update Userbits display
                    ub = pt.eng.tc.user_to_ascii()
                    if tx_ub != ub:
                        OLED.fill_rect(0,38,128,8,OLED.black)
                        OLED.text(ub,64,38,OLED.white,1,2)
                        OLED.show(38,46)
                        tx_ub = ub

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
                            dc.from_raw(g)
                            break

                    # every code left in FIFO, means that we have outdated TC
                    dc.next_frame()
                    for i in range(int(rf1/2)):
                        dc.next_frame()

                    # Draw an error bar to represent timing phase between TX and RX
                    # Positive Delta = TX is ahead of RX, bar is shown to the right
                    # and should increase 'duty' to slow down it's bit-clock
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

                        # Check if first received frame
                        if next_mon==None:
                            next_mon = pt.timecode()

                            if sync_after_jam > 0:
                                # wait ~1m
                                next_mon.from_raw(g | 0x0000FFFF)
                                next_mon.next_frame()
                                next_mon.from_raw(next_mon.to_raw() + (g & 0x0000FF00))
                            else:
                                # wait ~1s
                                next_mon.from_raw(g | 0x000000FF)
                                next_mon.next_frame()

                            next_mon_raw = next_mon.to_raw() & 0xFFFFFF00

                        elif g & 0xFFFFFF00 == next_mon_raw:
                            # Then update PID every 1s (or so)
                            if sync_after_jam > 0:
                                if pid.auto_mode == False:
                                    pid.set_auto_mode(True, last_output=pt.eng.duty)

                                phase.purge(now - period)
                                adjust = pid(phase.read())

                                pt.eng.micro_adjust(adjust, period * 1000)

                                print(dc.to_ascii(), d, phase.read(), pt.eng.duty, \
                                      temp_avg.store_read(sensor.read()), \
                                      adj_avg.store_read(adjust), \
                                      pt.eng.tc.user_to_ascii(), \
                                      pid.components)

                                # stop calibration after 15mins and save calculated value
                                if jam_started and (now - 900) > jam_started:
                                    pt.eng.micro_adjust(adj_avg.read(), period * 1000)

                                    config.set('calibration', format, adj_avg.read())
                                    config.set('calibration', 'period', period)

                                    if calibrate == 1:
                                        callback_setting_calibrate("No")

                                    sync_after_jam = 0
                                    jam_started = False
                                    pid.auto_mode = False

                            else:
                                print(dc.to_ascii(), d, phase.read(), pt.eng.duty, \
                                      temp_avg.store_read(sensor.read()))

                            # Current frame + ~1s
                            next_mon.from_raw(g | 0x000000FF)
                            next_mon.next_frame()
                            next_mon_raw = next_mon.to_raw() & 0xFFFFFF00


                        if pt.eng.mode == pt.MONITOR and sync_after_jam > 0:
                            # CAL = Sync'ed to RX and calibrating XTAL
                            OLED.text("CAL ",0,22,OLED.white)

                            # update Digi-Slate
                            '''
                            C = 0 3 4 5     = 0x39
                            A = 0 1 2 4 5 6 = 0x77
                            L = 3 4 5       = 0x38
                            '''
                            slate_HM.set_glyph(0x39, 0)
                            slate_HM.set_glyph(0x77, 1)
                            slate_HM.set_glyph(0x38, 2)
                            slate_HM.set_glyph(0, 3)
                        else:
                            OLED.text("RX  ",0,22,OLED.white)

                            # update Digi-Slate
                            '''
                            S = 0 2 3 5 6 = 0x6D
                            Y = 1 2 3 5 6 = 0x6E
                            n = 2 4 6     = 0x54
                            c = 3 4 6     = 0x58
                            '''
                            slate_HM.set_glyph(0x6D, 0)
                            slate_HM.set_glyph(0x6E, 1)
                            slate_HM.set_glyph(0x54, 2)
                            slate_HM.set_glyph(0x58, 3)

                        OLED.vline(64, 33, 2, OLED.white)
                        if zoom == True:
                            length = int(1280 * d)
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
                        OLED.text("Jam ",0,22,OLED.white)

                        # update Digi-Slate
                        '''
                        S = 0 2 3 5 6 = 0x6D
                        Y = 1 2 3 5 6 = 0x6E
                        n = 2 4 6     = 0x54
                        c = 3 4 6     = 0x58
                        '''
                        slate_HM.set_glyph(0x6D, 0)
                        slate_HM.set_glyph(0x6E, 1)
                        slate_HM.set_glyph(0x54, 2)
                        slate_HM.set_glyph(0x58, 3)

                        # Draw a line representing time until Jam complete
                        OLED.vline(0, 32, 4, OLED.white)
                        OLED.hline(0, 33, pt.eng.mode * 2, OLED.white)
                        OLED.hline(0, 34, pt.eng.mode * 2, OLED.white)

                        sync_after_jam = calibrate
                        jam_started = utime.time()

                    if pt.eng.mode > pt.RUN:
                        # Show RX Userbits
                        ub = pt.eng.rc.user_to_ascii()
                        if rx_ub != ub:
                            OLED.fill_rect(0,12,128,8,OLED.black)
                            OLED.text(ub,64,12,OLED.white,1,2)
                            OLED.show(12,20)
                            rx_ub = ub

                        # Show RX Timecode and bar
                        OLED.text(dc.to_ascii(),64,22,OLED.white,1,2)
                        OLED.show(22,36)

                        # update Digi-Slate
                        rx_asc = dc.to_ascii(False)
                        for i in range(4):
                            slate_SF.set_character(rx_asc[4+i], i)
                        slate_HM.draw()
                        slate_SF.set_colon(True)
                        slate_SF.draw()

                        # clear for next frame
                        OLED.fill_rect(0,22,128,15,OLED.black)

                        if not monitor and not sync_after_jam \
                               and pt.eng.mode == pt.MONITOR:
                            OLED.fill_rect(0,12,128,10,OLED.black)
                            OLED.show()
                            pt.eng.mode = pt.RUN
                    else:
                        # catch if user has cancelled jam/calibrate
                        sync_after_jam = 0
                        jam_started = False


            if pt.eng.mode == pt.HALTED:
                for i in range(4):
                    slate_HM.set_character("-", i)
                    slate_SF.set_character("-", i)
                slate_HM.draw()
                slate_SF.draw()

                OLED.rect(0,51,128,10,OLED.black,True)
                OLED.text("Underflow Error",64,53,OLED.white,1,2)
                OLED.show(49 ,64)
                pt.stop = True

            if pt.eng.is_stopped():
                break

#---------------------------------------------

if __name__ == "__main__":
    OLED_display_thread()
