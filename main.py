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
# https://www.waveshare.com/wiki/Pico-OLED-1.3

from libs import config
from libs.pid import *
from libs.umenu import *
from libs.neotimer import *
from libs.Pico_OLED import *

import pico_timecode as pt

import machine
import _thread
import utime
import rp2

# Set up (extra) globals
zoom = False
calibrate = False
menu_hidden = True
menu_hidden2 = False

'''
def irq_handler(m):
    #global eng, stop
    #global tx_ticks_us, rx_ticks_us
    global core_dis

    core_dis[machine.mem32[0xd0000000]] = machine.disable_irq()

    ticks = utime.ticks_us()
    if m==pt.eng.sm[1]:
        pt.tx_ticks_us = ticks
        machine.enable_irq(core_dis[machine.mem32[0xd0000000]])
        return

    if m==pt.eng.sm[5]:
        pt.rx_ticks_us = ticks
        machine.enable_irq(core_dis[machine.mem32[0xd0000000]])
        return

    if m==pt.eng.sm[2]:
        # Buffer Underflow
        pt.eng.stopped = True
        pt.eng.mode = -1

        menu_hidden = True
        menu_hidden2 = False

    machine.enable_irq(core_dis[machine.mem32[0xd0000000]])
'''


class NoShowScreen(OLED_1inch3):
    def show(self, start=0):
        # This allows us to superimpose a running 'TC' on the menu
        line = 0
        if start==0:
            return
        elif start > 1:
            line = start

        self.write_cmd(0xb0)
        for page in range(line,64):
            self.column = 63 - page
            self.write_cmd(0x00 + (self.column & 0x0f))
            self.write_cmd(0x10 + (self.column >> 4))
            for num in range(0,16):
                self.write_data(self.buffer[page*16+num])


def add_more_state_machines():
    sm_freq = int(pt.eng.tc.fps * 80 * 32)

    # TX State Machines
    pt.eng.sm.append(rp2.StateMachine(1, pt.blink_led, freq=sm_freq,
                               set_base=machine.Pin(25)))       # LED on Pico board + GPIO26
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
    #global eng, stop
    global menu_hidden, menu_hidden2

    menu_hidden = True
    menu_hidden2 = False

    if pt.eng.mode == 0:
        pt.eng.mode = 1
    elif pt.eng.mode == 1:
        pt.eng.mode = 0


def callback_jam():
    #global eng, stop
    global menu_hidden, menu_hidden2

    menu_hidden = True
    menu_hidden2 = False

    if pt.eng.is_running():
        pt.stop = True
        while pt.eng.is_running():
            utime.sleep(0.1)

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


def callback_zoom(set):
    global zoom

    if set=="Yes":
        zoom = True
    else:
        zoom = False


def callback_calibrate(set):
    global calibrate

    if set=="Yes":
        calibrate = True
    else:
        calibrate = False


def callback_save_config():
    global zoom, calibrate

    pt.eng.tc.acquire()
    fps = pt.eng.tc.fps
    df = pt.eng.tc.df
    pt.eng.tc.release()

    config.set('setting', 'fps', fps)
    config.set('setting', 'df', df)
    config.set('setting', 'zoom', zoom)
    config.set('setting', 'calibrate', calibrate)


def callback_exit():
    global menu_hidden

    menu_hidden = True

#---------------------------------------------

def OLED_display_thread(mode = 0):
    #global eng, stop
    global menu_hidden, menu_hidden2
    global zoom, calibrate
    #global tx_ticks_us, rx_ticks_us

    pt.eng = pt.engine()
    pt.eng.mode = mode
    pt.eng.set_stopped(True)

    # apply saved settings
    pt.eng.tc.set_fps_df(config.setting['fps'], config.setting['df'])
    zoom = config.setting['zoom']
    calibrate = config.setting['calibrate']

    keyA = Pin(15,Pin.IN,Pin.PULL_UP)
    keyB = Pin(17,Pin.IN,Pin.PULL_UP)
    timerA = Neotimer(250)
    timerB = Neotimer(250)

    # automatically Jam if booted with 'B' pressed
    if keyB.value() == 0:
        pt.eng.mode=64

    #OLED = OLED_1inch3()
    OLED = NoShowScreen()
    
    OLED.fill(0x0000)
    OLED.text("Pico-Timecode",10,0,OLED.white)
    OLED.text("www.github.com/",0,24,OLED.white)
    OLED.text("mungewell/",10,36,OLED.white)
    OLED.text("pico-timecode",20,48,OLED.white)
    OLED.show(1)
    utime.sleep(2)

    menu = Menu(OLED, 4, 10)
    menu.set_screen(MenuScreen('Main Menu')
        .add(CallbackItem("Exit", callback_exit, return_parent=True))
        .add(CallbackItem("Monitor RX", callback_monitor, visible=pt.eng.is_running))
        .add(SubMenuItem('Settings', visible=pt.eng.is_stopped)
            .add(EnumItem("Framerate", ["30", "29.97", "25", "24", "23.976"], callback_fps_df, \
                selected=[30, 29.97, 25, 24, 23.976].index(config.setting['fps'])))
            .add(EnumItem("Drop Frame", ["No", "Yes"], callback_fps_df, \
                selected=[False, True].index(config.setting['df'])))
            .add(EnumItem("Zoom Display", ["No", "Yes"], callback_zoom, \
                selected=[False, True].index(config.setting['zoom'])))
            .add(EnumItem("Jam+Calibrate", ["No", "Yes"], callback_calibrate, \
                selected=[False, True].index(config.setting['calibrate'])))
            .add(ConfirmItem("Save as Default", callback_save_config, "Confirm?", ('Yes', 'No'))))
        .add(CallbackItem("Jam/Sync RX", callback_jam))
        .add(ConfirmItem("Stop/Start TX", callback_stop_start, "Confirm?", ('Yes', 'No')))
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
        format = "FPS "+ str(pt.eng.tc.fps)
        if pt.eng.tc.fps != 25 and pt.eng.tc.fps != 24:
            format += (" DF" if df == True else " NDF")

        cycle_us = (1000000 / fps)
        gc = pt.timecode()

        cache = []
        sync_after_jam = False
        last_mon = 0

        pid = PID(0.015, 0.00025, 0.0, setpoint=0)
        pid.auto_mode = False
        pid.sample_time = 1
        pid.output_limits = (-50.0, 50.0)

        # apply calibration value
        try:
            pt.eng.micro_adjust(config.calibration[str(fps) + \
                    ("DF" if df == True else "")])
        except:
            pass

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
                    OLED.show(1)
            else:
                if timerA.debounce_signal(keyA.value()==0):
                    menu.reset()
                    menu_hidden = False


            if (menu_hidden and pt.eng.mode>0) or (menu_hidden and not menu_hidden2):
                # Only show the following when menu is not active
                OLED.fill(0x0000)
                OLED.text("<- Menu" ,0,2,OLED.white)

                if pt.eng.mode:
                    # Figure out what RX frame to display
                    while True:
                        r1 = pt.rx_ticks_us
                        t1 = pt.tx_ticks_us
                        f = pt.eng.sm[5].rx_fifo()
                        g = pt.eng.rc.to_raw()
                        r2 = pt.rx_ticks_us
                        n = utime.ticks_us()
                        if r1==r2:
                            gc.from_raw(g)
                            break

                    gc.next_frame()			# TC is ahead due to buffers...

                    # Draw an error bar to represent timing delta between TX and RX
                    # Positive Delta = TX is ahead of RX, bar is shown to the right
                    # and should increase 'duty' to slow down it's bit-clock
                    if pt.eng.mode == 1:
                        d = utime.ticks_diff(r1, t1)

                        # RX is offset by ~2/3 bit
                        d -= cycle_us * 2/ (3 * 80)

                        # correct delta, if not adjacent frame
                        if d > (-2 * cycle_us) and d <= 0:
                            while d < -(cycle_us/2):
                                d += cycle_us
                        elif d < (2 * cycle_us) and d >= 0:
                            while d > (cycle_us/2):
                                d -= cycle_us

                        # Update PID every 1s (or so)
                        if (g & 0xFFFFFF00) != last_mon:
                            if last_mon:
                                if sync_after_jam == True:
                                    if pid.auto_mode == False:
                                        pid.set_auto_mode(True, last_output=pt.eng.duty)

                                    adjust = pid(d)
                                    cache.append(adjust)
                                    if len(cache) > 60:
                                        cache = cache[1:]

                                    pt.eng.micro_adjust(adjust)
                                    print(gc.to_ascii(), d, pt.eng.duty, pid.components)
                                else:
                                    print(gc.to_ascii(), d, pt.eng.duty)

                            last_mon = g & 0xFFFFFF00

                        OLED.vline(64, 32, 4, OLED.white)
                        if zoom == True:
                            length = int(1280 * d/cycle_us)
                        else:
                            # markers at side to indicate full view
                            OLED.vline(0, 32, 4, OLED.white)
                            OLED.vline(127, 32, 4, OLED.white)
                            length = int(128 * d/cycle_us)

                        if d > 0:
                            OLED.hline(64, 33, length, OLED.white)
                            OLED.hline(64, 34, length, OLED.white)
                        else:
                            OLED.hline(64+length, 33, -length, OLED.white)
                            OLED.hline(64+length, 34, -length, OLED.white)

                    if pt.eng.mode > 1:
                        OLED.text("Jamming to:",0,12,OLED.white)

                        # Draw a line representing time until Jam complete
                        OLED.vline(0, 32, 4, OLED.white)
                        OLED.hline(0, 33, pt.eng.mode * 2, OLED.white)
                        OLED.hline(0, 34, pt.eng.mode * 2, OLED.white)

                        sync_after_jam = calibrate
                        #pid.set_auto_mode(True, last_output=eng.duty)

                    if pt.eng.mode == 1 and sync_after_jam:
                        # CX = Sync'ed to RX and calibrating
                        OLED.text("CX  " + gc.to_ascii(),0,22,OLED.white)
                    else:
                        OLED.text("RX  " + gc.to_ascii(),0,22,OLED.white)

                OLED.text(format,0,40,OLED.white)
            else:
                if pt.eng.mode == 0 and sync_after_jam == True:
                    # save calculated value
                    calfps = str(fps)
                    if pt.eng.tc.fps != 25 and pt.eng.tc.fps != 24:
                        calfps += ("DF" if df == True else "")
                    pt.eng.micro_adjust(sum(cache)/len(cache))
                    config.set('calibration', calfps, sum(cache)/len(cache))
                    sync_after_jam = False
                    pid.auto_mode = False

                # clear the lower lines of the screen
                OLED.rect(0,52,128,10,OLED.balck,True)

            if pt.eng.mode < 0:
                OLED.text("Underflow Error",0,54,OLED.white)
            else:
                # Always show the TX timecode, 'cos that's important!
                # since FIFO is in use, our current 'tc' is always ahead
                g = pt.eng.tc.to_raw()
                gc.from_raw(g)
                gc.prev_frame()
                gc.prev_frame()
                gc.prev_frame()

                OLED.text("TX  " + gc.to_ascii(),0,54,OLED.white)

            if menu_hidden and menu_hidden2 and pt.eng.mode==0:
                # Only draw the bottom lines of the screen
                OLED.show(52)
            else:
                OLED.show(1)

            menu_hidden2=menu_hidden

            if pt.eng.is_stopped():
                break

#---------------------------------------------

if __name__ == "__main__":
    OLED_display_thread()
