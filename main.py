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
# GP13 - RX: LTC_INPUT  (physical connection)
# GP18 - TX: LTC_OUTPUT (physical connection)
# GP19 - RX: raw/decoded LTC input (debug)
# GP20 - TX: raw LTC bitstream output (debug)
# GP21 - RX: sync from LTC input (debug)
#
# In the future we will also use:
#
# GP14 - IN_DET (reserved)
# GP16 - OUT_DET (reserved)
# GP26 - BLINK_LED (reserved)
# (this will enable both Pico and off board LED simulataneously)
#
# We will also use the I2C bus to 'talk' to other devices...
#

from umenu import *
from Pico_OLED import *
from pico_timecode import *

import machine
import _thread
import utime
import rp2

# set up starting values...
sm = []
stop = False
tc = timecode()
rc = timecode()
menu_hidden = True


class NoShowScreen(OLED_1inch3):
    def show(self, really=False):
        # This allows us to superimpose a running 'TC' on the menu
        if not really:
            return

        self.write_cmd(0xb0)
        for page in range(0,64):
            self.column = 63 - page
            self.write_cmd(0x00 + (self.column & 0x0f))
            self.write_cmd(0x10 + (self.column >> 4))
            for num in range(0,16):
                self.write_data(self.buffer[page*16+num])


def add_state_machines(sm, sm_freq):
    # TX State Machines
    sm.append(rp2.StateMachine(0, blink_led, freq=sm_freq,
                               set_base=machine.Pin(25)))       # LED on Pico board + GPIO26
    sm[-1].irq(tx_phase_handler)
    sm.append(rp2.StateMachine(1, buffer_out, freq=sm_freq,
                               out_base=machine.Pin(20)))       # Output of 'raw' bitstream
    sm.append(rp2.StateMachine(2, encode_dmc, freq=sm_freq,
                               jmp_pin=machine.Pin(20),
                               in_base=machine.Pin(18),         # same as pin as out
                               out_base=machine.Pin(18)))       # Encoded LTC Output

    # RX State Machines
    sm.append(rp2.StateMachine(4, decode_dmc, freq=sm_freq,
                               jmp_pin=machine.Pin(13),         # Input from 'other' device
                               in_base=machine.Pin(13),         # Input from 'other' device
                               set_base=machine.Pin(19)))       # Decoded LTC Input
    sm.append(rp2.StateMachine(5, sync_and_read, freq=sm_freq,
                               jmp_pin=machine.Pin(19),
                               in_base=machine.Pin(19),
                               out_base=machine.Pin(21),
                               set_base=machine.Pin(21)))       # 'sync' from RX bitstream
    sm[-1].irq(rx_phase_handler)


def callback_stop_start():
    global tc, rc, sm, stop

    if stop == False:
        stop = True

        tc.acquire()
        tc.mode = 0
        tc.release()
    else:
        tc.acquire()
        fps = tc.fps
        tc.release()

        sm = []
        sm_freq = int(fps * 80 * 16)
        sm.append(rp2.StateMachine(3, auto_start, freq=sm_freq))
        add_state_machines(sm, sm_freq)

        stop = False
        _thread.start_new_thread(pico_timecode_thread, (tc, rc, sm, lambda: stop))


def callback_monitor():
    global tc, rc, sm, stop
    global menu_hidden
    menu_hidden = True

    # Monitor incoming LTC only
    tc.acquire()
    if tc.mode == 0:
        tc.mode = 1
    elif tc.mode == 1:
        tc.mode = 0
    tc.release()


def callback_jam():
    global tc, rc, sm, stop
    global menu_hidden
    menu_hidden = True

    stop = True
    utime.sleep(1)

    tc.acquire()
    fps = tc.fps
    tc.mode = 2
    tc.release()

    sm = []
    sm_freq = int(fps * 80 * 16)
    sm.append(rp2.StateMachine(3, start_from_pin, freq=sm_freq,
                               jmp_pin=machine.Pin(21)))        # Sync from RX LTC
    add_state_machines(sm, sm_freq)
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

    # if change is accepted, stop TX
    if tc.set_fps_df(fps, df):
        stop = True
        tc.acquire()
        tc.mode = 0
        tc.release()


def callback_exit():
    global menu_hidden
    menu_hidden = True


def callback_check_stopped():
    global tc, rc, sm, stop

    if stop:
        return True
    else:
        return False


#---------------------------------------------

if __name__ == "__main__":
    '''
    global tc, rc, sm, stop
    global menu_hidden
    global rx_phase, tx_phase
    '''

    keyA = Pin(15,Pin.IN,Pin.PULL_UP)
    keyB = Pin(17,Pin.IN,Pin.PULL_UP)

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
    OLED.show(True)
    utime.sleep(2)

    menu = Menu(OLED, 4, 10)
    menu.set_screen(MenuScreen('Main Menu')
        .add(CallbackItem("Exit", callback_exit, return_parent=True))
        .add(SubMenuItem('TX Setting', visible=callback_check_stopped)
             .add(EnumItem("FPS", ["25", "29.97", "30", "24", "23.976"], callback_fps_df))
             .add(EnumItem("Drop Frame", ["Yes", "No"], callback_fps_df)))
        .add(CallbackItem("Monitor RX", callback_monitor, return_parent=True))
        .add(CallbackItem("Jam RX", callback_jam, return_parent=True))
        .add(ConfirmItem("Stop/Start TX", callback_stop_start, "Confirm?", ('Yes', 'No')))
    )

    # Allocate appropriate StateMachines, and their pins
    sm = []
    sm_freq = int(fps * 80 * 16)
    sm.append(rp2.StateMachine(3, auto_start, freq=sm_freq))
    add_state_machines(sm, sm_freq)    

    # Start up threads
    stop = False
    _thread.start_new_thread(pico_timecode_thread, (tc, rc, sm, lambda: stop))

    while True:
        tc.acquire()
        fps = tc.fps
        format = "FPS: "+ str(tc.fps)
        if tc.fps != 25 and tc.fps != 24:
            if tc.df:
                format += " DF"
            else:
                format += " NDF"
        tc.release()

        while True:
            if menu_hidden == False:
                if keyA.value()==0:
                    menu.move(2)        # Requires patched umenu to work
                if keyB.value()==0:
                    menu.click()
                menu.draw()

                # Clear screen after Menu Exits
                if menu_hidden == True:
                    OLED.fill(0x0000)
                    OLED.show(True)
            else:
                if keyA.value()==0:
                    menu.reset()
                    menu_hidden = False


            if menu_hidden:
                # Only show the following when menu is not active
                OLED.fill(0x0000)
                OLED.text("Menu:" ,0,2,OLED.white)

                tc.acquire()
                mode = tc.mode
                tc.release()

                if mode:
                    OLED.text("RX:" + rc.to_ascii(),0,22,OLED.white)
                    if mode > 1:
                        OLED.text("Waiting to Jam",0,12,OLED.white)

                OLED.text(format,0,40,OLED.white)

            # Always show the TX timecode, 'cos that's important!
            OLED.text("TX:" + tc.to_ascii(),0,54,OLED.white)
            OLED.show(True)

            if not stop:
                utime.sleep(0.1)
            else:
                break

