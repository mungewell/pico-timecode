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
    global sm, stop
    global tx_ticks_us, rx_ticks_us
    global core_dis

    core_dis[machine.mem32[0xd0000000]] = machine.disable_irq()

    ticks = utime.ticks_us()
    if m==sm[1]:
        tx_ticks_us = ticks
        machine.enable_irq(core_dis[machine.mem32[0xd0000000]])
        return

    if m==sm[5]:
        rx_ticks_us = ticks
        machine.enable_irq(core_dis[machine.mem32[0xd0000000]])
        return

    if m==sm[2]:
        # Buffer Underflow
        stop = 1

        tc.acquire()
        tc.mode = -1
        tc.release()

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


def add_more_state_machines(sm_freq):
    global sm

    # TX State Machines
    sm.append(rp2.StateMachine(1, blink_led, freq=sm_freq,
                               set_base=machine.Pin(25)))       # LED on Pico board + GPIO26
    sm.append(rp2.StateMachine(2, buffer_out, freq=sm_freq,
                               out_base=machine.Pin(22)))       # Output of 'raw' bitstream
    sm.append(rp2.StateMachine(3, encode_dmc, freq=sm_freq,
                               jmp_pin=machine.Pin(22),
                               in_base=machine.Pin(13),         # same as pin as out
                               out_base=machine.Pin(13)))       # Encoded LTC Output

    # RX State Machines
    sm.append(rp2.StateMachine(4, decode_dmc, freq=sm_freq*2,
                               jmp_pin=machine.Pin(18),         # LTC Input ...
                               in_base=machine.Pin(18),         # ... from 'other' device
                               set_base=machine.Pin(19)))       # Decoded LTC Input
    sm.append(rp2.StateMachine(5, sync_and_read, freq=sm_freq*2,
                               jmp_pin=machine.Pin(19),
                               in_base=machine.Pin(19),
                               out_base=machine.Pin(21),
                               set_base=machine.Pin(21)))       # 'sync' from RX bitstream

    # set up IRQ handler
    for m in sm:
        m.irq(handler=irq_handler, hard=True)


def callback_stop_start():
    global tc, rc, sm
    global stop, stopped
    global menu_hidden, menu_hidden2

    if not stop:
        stop = True
        utime.sleep(1)

        # Also stop any Monitor/Jam
        tc.acquire()
        tc.mode = 0
        tc.release()
    else:
        menu_hidden = True
        menu_hidden2 = False

        tc.acquire()
        fps = tc.fps
        tc.release()

        sm = []
        sm_freq = int(fps * 80 * 16)
        sm.append(rp2.StateMachine(0, auto_start, freq=sm_freq))
        add_more_state_machines(sm_freq)

        stop = False
        _thread.start_new_thread(pico_timecode_thread, (tc, rc, sm, lambda: stop))


def callback_monitor():
    global tc, rc, sm, stop
    global menu_hidden, menu_hidden2

    menu_hidden = True
    menu_hidden2 = False

    tc.acquire()
    if tc.mode == 0:
        tc.mode = 1
    elif tc.mode == 1:
        tc.mode = 0
    tc.release()


def callback_jam():
    global tc, rc, sm
    global stop, stopped
    global menu_hidden, menu_hidden2

    menu_hidden = True
    menu_hidden2 = False

    # Turn off Jam if already enabled
    tc.acquire()
    '''
    mode = tc.mode
    if mode > 1:
        tc.mode = 0
        tc.release()
        return
    '''

    fps = tc.fps
    tc.mode = 64
    tc.release()

    stop = True
    utime.sleep(1)

    sm = []
    sm_freq = int(fps * 80 * 16)
    sm.append(rp2.StateMachine(0, start_from_pin, freq=sm_freq,
                               jmp_pin=machine.Pin(21)))        # Sync from RX LTC
    add_more_state_machines(sm_freq)

    stop = False
    _thread.start_new_thread(pico_timecode_thread, (tc, rc, sm, lambda: stop))


def callback_fps_df(set):
    global tc, rc, sm, stop

    tc.acquire()
    fps = tc.fps
    df = tc.df
    tc.release()

    if set=="Yes":
        df = True
    elif set == "No":
        df = False
    else:
        fps = float(set)

    tc.set_fps_df(fps, df)


def callback_exit():
    global menu_hidden

    menu_hidden = True


def callback_check_stopped():
    global stop

    if stop:
        return True
    else:
        return False


def callback_check_running():
    global stop

    if stop:
        return False
    else:
        return True

#---------------------------------------------

def OLED_display_thread():
    global tc, rc, sm
    global stop, stopped
    global menu_hidden, menu_hidden2
    global tx_ticks_us, rx_ticks_us

    gc = timecode()

    keyA = Pin(15,Pin.IN,Pin.PULL_UP)
    keyB = Pin(17,Pin.IN,Pin.PULL_UP)
    timerA = Neotimer(250)
    timerB = Neotimer(250)

    tc.acquire()
    fps = tc.fps
    tc.mode=0
    mode = tc.mode
    format = "FPS: "+ str(tc.fps)
    if tc.fps != 25:
        if tc.df:
            format += " DF"
        else:
            format += " NDF"
    tc.release()

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
        .add(CallbackItem("Monitor RX", callback_monitor, visible=callback_check_running))
        .add(SubMenuItem('TX/FPS Setting', visible=callback_check_stopped)
             .add(EnumItem("Framerate", ["30", "29.97", "25", "24", "23.976"], callback_fps_df))
             .add(EnumItem("Drop Frame", ["No", "Yes"], callback_fps_df)))
        .add(CallbackItem("Jam/Sync RX", callback_jam))
        .add(ConfirmItem("Stop/Start TX", callback_stop_start, "Confirm?", ('Yes', 'No')))
    )

    # Allocate appropriate StateMachines, and their pins
    sm = []
    sm_freq = int(fps * 80 * 16)
    sm.append(rp2.StateMachine(0, auto_start, freq=sm_freq))
    add_more_state_machines(sm_freq)

    # Start up threads
    stop = False
    _thread.start_new_thread(pico_timecode_thread, (tc, rc, sm, lambda: stop))

    while True:
        tc.acquire()
        fps = tc.fps
        format = "FPS "+ str(tc.fps)
        if tc.fps != 25 and tc.fps != 24:
            if tc.df:
                format += " DF"
            else:
                format += " NDF"
        tc.release()
        cycle_us = (1000000 / fps)

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


            if (menu_hidden and mode>0) or (menu_hidden and not menu_hidden2):
                # Only show the following when menu is not active
                OLED.fill(0x0000)
                OLED.text("<- Menu" ,0,2,OLED.white)

                tc.acquire()
                mode = tc.mode
                tc.release()

                if mode:
                    # Figure out what RX frame to display
                    while True:
                        r1 = rx_ticks_us
                        t1 = tx_ticks_us
                        f = sm[5].rx_fifo()
                        g = rc.to_int()
                        r2 = rx_ticks_us
                        n = utime.ticks_us()
                        if r1==r2:
                            gc.from_int(g)
                            break

                    gc.next_frame()			# TC is ahead due to buffers...

                    # Draw an error bar to represent timing betwen TX and RX
                    if mode == 1:
                        d = utime.ticks_diff(t1, r1)

                        # RX is offset by ~2/3 bit
                        d += cycle_us * 2/ (3 * 80)

                        # correct delta, if adjacent frame
                        if d > (-2 * cycle_us) and d < 0:
                            while d < -(cycle_us/2):
                                d += cycle_us
                        if d < (2 * cycle_us) and d > 0:
                            while d > (cycle_us/2):
                                d -= cycle_us

                        OLED.vline(64, 32, 4, OLED.white)

                        length = int(1280 * d/cycle_us)
                        if d > 0:
                            OLED.hline(65, 33, length, OLED.white)
                            OLED.hline(65, 34, length, OLED.white)
                        else:
                            OLED.hline(63+length, 33, -length, OLED.white)
                            OLED.hline(63+length, 34, -length, OLED.white)

                    OLED.text("RX  " + gc.to_ascii(),0,22,OLED.white)
                    if mode > 1:
                        OLED.text("Jamming to:",0,12,OLED.white)

                        # Draw a line representing time until Jam complete
                        OLED.vline(0, 32, 4, OLED.white)
                        OLED.hline(0, 33, mode * 2, OLED.white)
                        OLED.hline(0, 34, mode * 2, OLED.white)

                OLED.text(format,0,40,OLED.white)
            else:
                # clear the lower lines of the screen
                OLED.rect(0,52,128,10,OLED.balck,True)

            if mode < 0:
                OLED.text("Underflow Error",0,54,OLED.white)
            else:
                # Always show the TX timecode, 'cos that's important!
                # since FIFO is in use, our current 'tc' is always ahead
                g = tc.to_int()
                gc.from_int(g)
                gc.prev_frame()
                gc.prev_frame()
                gc.prev_frame()

                OLED.text("TX  " + gc.to_ascii(),0,54,OLED.white)

            if menu_hidden and menu_hidden2 and mode==0:
                # Only draw the bottom lines of the screen
                OLED.show(52)
            else:
                OLED.show(1)

            menu_hidden2=menu_hidden

            if stop:
                break


if __name__ == "__main__":
    OLED_display_thread()
