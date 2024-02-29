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
from libs.PicoOled13 import *

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
zoom = False
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
    global menu_hidden, menu_hidden2

    if pt.eng.is_running():
        pt.stop = True
        while pt.eng.is_running():
            utime.sleep(0.1)

        # Also stop any Monitor/Jam
        pt.eng.mode = 0
    else:
        menu_hidden = True
        menu_hidden2 = False

        pt.eng.tc.acquire()
        fps = pt.eng.tc.fps
        pt.eng.tc.release()

        pt.eng.sm = []
        pt.eng.sm.append(rp2.StateMachine(0, pt.auto_start, freq=int(pt.eng.tc.fps * 80 * 32)))
        add_more_state_machines()

        _thread.start_new_thread(pt.pico_timecode_thread, (pt.eng, lambda: pt.stop))


def callback_monitor():
    global menu_hidden, menu_hidden2

    menu_hidden = True
    menu_hidden2 = False

    if pt.eng.mode == 0:
        pt.eng.mode = 1
    elif pt.eng.mode == 1:
        pt.eng.mode = 0


def callback_jam():
    global menu_hidden, menu_hidden2

    menu_hidden = True
    menu_hidden2 = False

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

    pt.eng.mode = 64
    _thread.start_new_thread(pt.pico_timecode_thread, (pt.eng, lambda: pt.stop))


def callback_fps_df(set):
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


def callback_setting_zoom(set):
    global zoom

    if set=="Yes":
        zoom = True
    else:
        zoom = False


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

def OLED_display_thread(mode = 0):
    global menu, menu_hidden
    global zoom, calibrate

    pt.eng = pt.engine()
    pt.eng.mode = mode
    pt.eng.set_stopped(True)

    # apply saved settings
    callback_fps_df(config.setting['framerate'][0])
    callback_fps_df(config.setting['dropframe'][0])

    callback_setting_zoom(config.setting['zoom'][0])
    callback_setting_calibrate(config.setting['calibrate'][0])
    callback_setting_flashframe(config.setting['flashframe'][0])
    callback_setting_userbits(config.setting['userbits'][0])

    keyA = Pin(15,Pin.IN,Pin.PULL_UP)
    keyB = Pin(17,Pin.IN,Pin.PULL_UP)
    timerA = Neotimer(50)
    timerB = Neotimer(50)

    # Internal temp sensor
    sensor = Temperature()
    temp_avg = Rolling()

    # automatically Jam if booted with 'B' pressed
    if keyB.value() == 0:
        pt.eng.mode=64

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
            .add(EnumItem("zoom", config.setting['zoom'][1], callback_setting_zoom, \
                selected=config.setting['zoom'][1].index(config.setting['zoom'][0])))
            .add(EnumItem("calibrate", config.setting['calibrate'][1], callback_setting_calibrate, \
                selected=config.setting['calibrate'][1].index(config.setting['calibrate'][0])))
            .add(EnumItem("flashframe", config.setting['flashframe'][1], callback_setting_flashframe, \
                selected=config.setting['flashframe'][1].index(config.setting['flashframe'][0])))
            .add(EnumItem("userbits", config.setting['userbits'][1], callback_setting_userbits, \
                selected=config.setting['userbits'][1].index(config.setting['userbits'][0])))
            .add(ConfirmItem("Save as Default", callback_setting_save, "Confirm?", ('Yes', 'No'))))
    )

    # Reduce the CPU clock, for better computation of PIO freqs
    machine.freq(120000000)

    # Allocate appropriate StateMachines, and their pins
    pt.eng.sm = []
    pt.eng.sm.append(rp2.StateMachine(0, pt.auto_start, freq=int(pt.eng.tc.fps * 80 * 32)))
    add_more_state_machines()

    # Start up threads
    _thread.start_new_thread(pt.pico_timecode_thread, (pt.eng, lambda: pt.stop))

    while True:
        fps = pt.eng.tc.fps
        df = pt.eng.tc.df
        format = str(pt.eng.tc.fps)
        if pt.eng.tc.fps != 25 and pt.eng.tc.fps != 24:
            format += ("-DF" if df == True else "-NDF")

        cycle_us = (1000000.0 / fps)

        dc = pt.timecode()

        if menu_hidden == True:
            OLED.fill(0x0000)
            OLED.text("<Menu" ,0,2,OLED.white)
            OLED.text(format,128,2,OLED.white,1,1)
            OLED.text(pt.eng.tc.user_to_ascii(), \
                      64,39,OLED.white,1,2)
            OLED.show(0,49)
        pasc = "--------"
        ptus = 0

        sync_after_jam = 0
        jam_started = False
        last_mon = 0

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
            print("Calibration:", config.calibration[format], period)
        except:
            pass
        phase = Rolling(int(fps+1) * period)  	# sized for real fps, but really
                                                # we only get ~4fps with RX/CX mode
        adj_avg = Rolling(240)               	# average over 4 minutes

        while True:
            if menu_hidden == False:
                if timerA.debounce_signal(keyA.value()==0):
                    menu.move(2)        # Requires patched umenu to work
                if timerB.debounce_signal(keyB.value()==0):
                    menu.click()
                menu.draw()

                # Clear screen after Menu Exits
                if menu_hidden == True:
                    OLED.fill(0x0000)
                    OLED.text("<Menu" ,0,2,OLED.white)
                    OLED.text(format,128,2,OLED.white,1,1)
                    OLED.text(pt.eng.tc.user_to_ascii(), \
                              64,39,OLED.white,1,2)
                    OLED.show(0,49)
                    pasc="--------"
                    ptx = 0
            else:
                if timerA.debounce_signal(keyA.value()==0):
                    # enter the Menu...
                    menu.reset()
                    menu_hidden = False

                # Debug - freeze screen
                while pt.eng.mode == 1 and timerB.debounce_signal(keyB.value()==0):
                    utime.sleep(1)

                # Attempt to align display with the TX timing
                t1 = pt.tx_ticks_us
                if pt.eng.mode == 0:
                    if ptus == t1:
                        continue

                # Draw the main TC counter, buffering means value is 2 frames ahead
                while True:
                    t1 = pt.tx_ticks_us
                    tf1 = pt.eng.sm[2].tx_fifo()
                    g = pt.eng.tc.to_raw()
                    tf2 = pt.eng.sm[2].tx_fifo()
                    t2 = pt.tx_ticks_us

                    if t1==t2 and tf1==tf2:
                        dc.from_raw(g)
                        break

                # When filling TX FIFO, we do so if < 5 word pre-loaded
                # then we add add either 2 or 3 32-bit words (depending on sync)
                # So TX FIFO may be:
                # <- 3, 2, 3
                # <- 2, 3, 2

                # correct read TC, for frames queued in FIFO
                dc.prev_frame()
                if tf1 > 3:
                    dc.prev_frame()
                if tf1 > 5:
                    dc.prev_frame()
                asc = dc.to_ascii(False)

                # check which characters of the TC have changed
                if pasc != asc:
                    for c in range(len(asc)):
                        if asc[c]!=pasc[c]:
                            break
                    for i in range(7,(c&6)-1,-1):
                        # blit in reverse order, offsetting to hide ':'
                        OLED.blit(timecode_fb[int(asc[i])],
                            (16*i)-(4 if i&1 else 0), 48)

                    # blank left most ':'
                    if c < 2:
                        OLED.rect(0,48,4,16,OLED.black,True)

                    pasc=asc
                    ptus = t1
                    OLED.show(49 ,64, c*2)

                # Figure out what RX frame to display
                if pt.eng.mode > 0:
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
                    if pt.eng.mode == 1:
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
                        now = utime.time()
                        if d >= -0.5 and d <= 0.5:
                            phase.store(d, now)

                        # Update PID every 1s (or so)
                        if (g & 0xFFFFFF00) != last_mon:
                            if last_mon:
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

                            last_mon = g & 0xFFFFFF00


                        if pt.eng.mode == 1 and sync_after_jam > 0:
                            # CX = Sync'ed to RX and calibrating XTAL
                            OLED.text("CX  ",0,22,OLED.white)
                        else:
                            OLED.text("RX  ",0,22,OLED.white)

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

                    if pt.eng.mode > 1:
                        OLED.text("Jam ",0,22,OLED.white)

                        # Draw a line representing time until Jam complete
                        OLED.vline(0, 32, 4, OLED.white)
                        OLED.hline(0, 33, pt.eng.mode * 2, OLED.white)
                        OLED.hline(0, 34, pt.eng.mode * 2, OLED.white)

                        sync_after_jam = calibrate
                        jam_started = utime.time()

                    if pt.eng.mode > 0:
                        OLED.text(pt.eng.rc.user_to_ascii(), \
                                64,12,OLED.white,1,2)
                        OLED.text(dc.to_ascii(),64,22,OLED.white,1,2)
                        OLED.show(12,36)
                        OLED.fill_rect(0,12,128,36,OLED.black)
                    else:
                        # catch if user has cancelled jam/calibrate
                        sync_after_jam = 0
                        jam_started = False


                        '''
                        # debug - place marker when RX updates, cleared when TX updates
                        OLED.pixel(126 ,62, OLED.white)
                        OLED.show(62, 63, 15)
                        '''

            if pt.eng.mode < 0:
                OLED.rect(0,52,128,10,OLED.black,True)
                OLED.text("Underflow Error",64,54,OLED.white,1,2)
                OLED.show(49 ,64)
                pt.stop = True

            if pt.eng.is_stopped():
                break

#---------------------------------------------

if __name__ == "__main__":
    OLED_display_thread()
