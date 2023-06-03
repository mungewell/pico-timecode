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

from pid import *
from umenu import *
from neotimer import *
from Pico_OLED import *
from pico_timecode import *

import machine
import _thread
import utime
import rp2

# Set up (extra) globals
menu_hidden = True
menu_hidden2 = False

def irq_handler(m):
    global eng, stop
    global tx_ticks_us, rx_ticks_us
    global core_dis

    core_dis[machine.mem32[0xd0000000]] = machine.disable_irq()

    ticks = utime.ticks_us()
    if m==eng.sm[1]:
        tx_ticks_us = ticks
        machine.enable_irq(core_dis[machine.mem32[0xd0000000]])
        return

    if m==eng.sm[5]:
        rx_ticks_us = ticks
        machine.enable_irq(core_dis[machine.mem32[0xd0000000]])
        return

    if m==eng.sm[2]:
        # Buffer Underflow
        stop = 1
        eng.mode = -1

        menu_hidden = True
        menu_hidden2 = False

    machine.enable_irq(core_dis[machine.mem32[0xd0000000]])


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


def add_more_state_machines(eng):
    sm_freq = int(eng.tc.fps * 80 * 32)

    # TX State Machines
    eng.sm.append(rp2.StateMachine(1, blink_led, freq=sm_freq,
                               set_base=machine.Pin(25)))       # LED on Pico board + GPIO26
    eng.sm.append(rp2.StateMachine(2, buffer_out, freq=sm_freq,
                               out_base=machine.Pin(22)))       # Output of 'raw' bitstream
    eng.sm.append(rp2.StateMachine(3, encode_dmc, freq=sm_freq,
                               jmp_pin=machine.Pin(22),
                               in_base=machine.Pin(13),         # same as pin as out
                               out_base=machine.Pin(13)))       # Encoded LTC Output

    # RX State Machines
    eng.sm.append(rp2.StateMachine(4, decode_dmc, freq=sm_freq,
                               jmp_pin=machine.Pin(18),         # LTC Input ...
                               in_base=machine.Pin(18),         # ... from 'other' device
                               set_base=machine.Pin(19)))       # Decoded LTC Input
    eng.sm.append(rp2.StateMachine(5, sync_and_read, freq=sm_freq,
                               jmp_pin=machine.Pin(19),
                               in_base=machine.Pin(19),
                               out_base=machine.Pin(21),
                               set_base=machine.Pin(21)))       # 'sync' from RX bitstream

    # correct clock dividers
    eng.frig_clocks(eng.tc.fps)

    # set up IRQ handler
    for m in eng.sm:
        m.irq(handler=irq_handler, hard=True)


def callback_stop_start():
    global eng, stop
    global menu_hidden, menu_hidden2

    if eng.is_running():
        stop = True
        while eng.is_running():
            utime.sleep(0.1)

        # Also stop any Monitor/Jam
        eng.mode = 0
    else:
        menu_hidden = True
        menu_hidden2 = False

        eng.tc.acquire()
        fps = eng.tc.fps
        eng.tc.release()

        eng.sm = []
        eng.sm.append(rp2.StateMachine(0, auto_start, freq=int(eng.tc.fps * 80 * 32)))
        add_more_state_machines(eng)

        stop = False
        _thread.start_new_thread(pico_timecode_thread, (eng, lambda: stop))


def callback_monitor():
    global eng, stop
    global menu_hidden, menu_hidden2

    menu_hidden = True
    menu_hidden2 = False

    if eng.mode == 0:
        eng.mode = 1
    elif eng.mode == 1:
        eng.mode = 0


def callback_jam():
    global eng, stop
    global menu_hidden, menu_hidden2

    menu_hidden = True
    menu_hidden2 = False

    if eng.is_running():
        stop = True
        while eng.is_running():
            utime.sleep(0.1)

    # Turn off Jam if already enabled
    eng.sm = []
    eng.sm.append(rp2.StateMachine(0, start_from_pin, freq=int(eng.tc.fps * 80 * 32),
                               jmp_pin=machine.Pin(21)))        # Sync from RX LTC
    add_more_state_machines(eng)

    eng.mode = 64
    stop = False
    _thread.start_new_thread(pico_timecode_thread, (eng, lambda: stop))


def callback_fps_df(set):
    global eng, stop

    eng.tc.acquire()
    fps = eng.tc.fps
    df = eng.tc.df
    eng.tc.release()

    if set=="Yes":
        df = True
    elif set == "No":
        df = False
    else:
        fps = float(set)

    eng.tc.set_fps_df(fps, df)


def callback_exit():
    global menu_hidden

    menu_hidden = True


#---------------------------------------------

def OLED_display_thread(mode = 0):
    global eng, stop
    global menu_hidden, menu_hidden2
    global tx_ticks_us, rx_ticks_us

    eng = engine()
    eng.mode = mode
    eng.set_stopped(True)

    keyA = Pin(15,Pin.IN,Pin.PULL_UP)
    keyB = Pin(17,Pin.IN,Pin.PULL_UP)
    timerA = Neotimer(250)
    timerB = Neotimer(250)

    # automatically Jam if booted with 'B' pressed
    if keyB.value() == 0:
        eng.mode=64

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
        .add(CallbackItem("Monitor RX", callback_monitor, visible=eng.is_running))
        .add(SubMenuItem('TX/FPS Setting', visible=eng.is_stopped)
             .add(EnumItem("Framerate", ["30", "29.97", "25", "24", "23.976"], callback_fps_df))
             .add(EnumItem("Drop Frame", ["No", "Yes"], callback_fps_df)))
        .add(CallbackItem("Jam/Sync RX", callback_jam))
        .add(ConfirmItem("Stop/Start TX", callback_stop_start, "Confirm?", ('Yes', 'No')))
    )

    # Reduce the CPU clock, for better computation of PIO freqs
    machine.freq(120000000)

    # Allocate appropriate StateMachines, and their pins
    eng.sm = []
    eng.sm.append(rp2.StateMachine(0, auto_start, freq=int(eng.tc.fps * 80 * 32)))
    add_more_state_machines(eng)

    # Start up threads
    stop = False
    _thread.start_new_thread(pico_timecode_thread, (eng, lambda: stop))

    while True:
        eng.tc.acquire()
        fps = eng.tc.fps
        format = "FPS "+ str(eng.tc.fps)
        if eng.tc.fps != 25 and eng.tc.fps != 24:
            if eng.tc.df:
                format += " DF"
            else:
                format += " NDF"
        eng.tc.release()

        cycle_us = (1000000 / fps)
        gc = timecode()
        l = 0

        dave = 0
        dcache = []

        pid = PID(-0.001, -0.00001, -0.002, setpoint=0)
        pid.auto_mode = False
        pid.sample_time = 60
        pid.output_limits = (-20.0, 20.0)
        sync_after_jam = False

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


            if (menu_hidden and eng.mode>0) or (menu_hidden and not menu_hidden2):
                # Only show the following when menu is not active
                OLED.fill(0x0000)
                OLED.text("<- Menu" ,0,2,OLED.white)

                if eng.mode:
                    # Figure out what RX frame to display
                    while True:
                        r1 = rx_ticks_us
                        t1 = tx_ticks_us
                        f = eng.sm[5].rx_fifo()
                        g = eng.rc.to_raw()
                        r2 = rx_ticks_us
                        n = utime.ticks_us()
                        if r1==r2:
                            gc.from_raw(g)
                            break

                    gc.next_frame()			# TC is ahead due to buffers...

                    # Draw an error bar to represent timing betwen TX and RX
                    if eng.mode == 1:
                        d = utime.ticks_diff(t1, r1)

                        # RX is offset by ~2/3 bit
                        d += cycle_us * 2/ (3 * 80)

                        # correct delta, if adjacent frame
                        if d > (-2 * cycle_us) and d <= 0:
                            while d < -(cycle_us/2):
                                d += cycle_us
                        elif d < (2 * cycle_us) and d >= 0:
                            while d > (cycle_us/2):
                                d -= cycle_us
                        elif l:
                                sync_after_jam = False
                                pid.auto_mode = False

                        # Average recent delta's to minimise glitches
                        dave += d
                        dcache.append(d)
                        if len(dcache) > 10:
                            dave -= dcache[0]
                            dcache = dcache[1:]

                        # Update PID every 60s or so
                        if (g & 0xFFFF0000) != l:
                            if l:
                                if sync_after_jam == True:
                                    if pid.auto_mode == False:
                                        pid.set_auto_mode(True, last_output=eng.duty)

                                    eng.duty = pid(dave/len(dcache))
                                    print(gc.to_ascii(), dave/len(dcache), eng.duty, pid.components)
                                else:
                                    print(gc.to_ascii(), dave/len(dcache), eng.duty)

                            l = g & 0xFFFF0000

                        OLED.vline(64, 32, 4, OLED.white)

                        length = int(1280 * d/cycle_us)
                        if d > 0:
                            OLED.hline(65, 33, length, OLED.white)
                            OLED.hline(65, 34, length, OLED.white)
                        else:
                            OLED.hline(63+length, 33, -length, OLED.white)
                            OLED.hline(63+length, 34, -length, OLED.white)

                    OLED.text("RX  " + gc.to_ascii(),0,22,OLED.white)
                    if eng.mode > 1:
                        OLED.text("Jamming to:",0,12,OLED.white)

                        # Draw a line representing time until Jam complete
                        OLED.vline(0, 32, 4, OLED.white)
                        OLED.hline(0, 33, eng.mode * 2, OLED.white)
                        OLED.hline(0, 34, eng.mode * 2, OLED.white)

                        sync_after_jam = True
                        #pid.set_auto_mode(True, last_output=eng.duty)

                OLED.text(format,0,40,OLED.white)
            else:
                # clear the lower lines of the screen
                OLED.rect(0,52,128,10,OLED.balck,True)
                sync_after_jam = False
                pid.auto_mode = False

            if eng.mode < 0:
                OLED.text("Underflow Error",0,54,OLED.white)
            else:
                # Always show the TX timecode, 'cos that's important!
                # since FIFO is in use, our current 'tc' is always ahead
                g = eng.tc.to_raw()
                gc.from_raw(g)
                gc.prev_frame()
                gc.prev_frame()
                gc.prev_frame()

                OLED.text("TX  " + gc.to_ascii(),0,54,OLED.white)

            if menu_hidden and menu_hidden2 and eng.mode==0:
                # Only draw the bottom lines of the screen
                OLED.show(52)
            else:
                OLED.show(1)

            menu_hidden2=menu_hidden

            if eng.is_stopped():
                break

#---------------------------------------------

if __name__ == "__main__":
    OLED_display_thread()
