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

from Pico_OLED import *
from pico_timecode import *

import machine
import _thread
import utime
import rp2

def OLED_add_state_machines(sm, sm_freq):
    # TX State Machines
    sm.append(rp2.StateMachine(0, blink_led, freq=sm_freq,
                               set_base=machine.Pin(25)))       # LED on Pico board + GPIO26
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


def OLED_display_thread(tc, rc):
    menu = 0
    menudisplay = [None, "Stop/Start", "Monitor", "Jam"]

    keyA = Pin(15,Pin.IN,Pin.PULL_UP)
    keyB = Pin(17,Pin.IN,Pin.PULL_UP)
    keyread = 0
    keyseen = 0

    tc.acquire()
    fps = tc.fps
    mode = tc.mode
    format = "FPS: "+ str(tc.fps)
    if tc.fps != 25:
        if tc.df:
            format += " DF"
        else:
            format += " NDF"
    tc.release()

    OLED = OLED_1inch3()
    OLED.fill(0x0000) 
    OLED.text("Pico-Timecode",0,28,OLED.white)
    OLED.show()
    utime.sleep(2)

    if mode > 0:
        OLED.text("Waiting to Jam",2,50,OLED.white)
    else:
        OLED.text("'B' to start",0,50,OLED.white)
        

    # Allocate appropriate StateMachines, and their pins
    sm = []
    sm_freq = int(fps * 80 * 16)
    sm.append(rp2.StateMachine(3, auto_start, freq=sm_freq))

    OLED_add_state_machines(sm, sm_freq)    

    # Start up threads
    stop = False
    _thread.start_new_thread(pico_timecode_thread, (tc, rc, sm, lambda: stop))

    while True:
        if not keyread:
            if keyA.value()==0:
                keyread=1
            elif keyB.value()==0:
                keyread=2
        else:
            keyseen=1
            if keyread==1 and keyA.value()!=0:
                keyread=0
                keyseen=0
            if keyread==2 and keyB.value()!=0:
                keyread=0
                keyseen=0

        # change menu on 'A' press
        if keyread==1 and not keyseen:
            menu += 1
            if menu >= len(menudisplay):
                menu=0

        # action menu item on 'B' press
        if keyread==2 and not keyseen:
            if menu==1:
                if stop == False:
                    # Stop/Start
                    stop = True

                    tc.acquire()
                    tc.mode = 0
                    tc.release()
                    mode = 0
                    menu = 0
                else:
                    sm = []
                    sm.append(rp2.StateMachine(3, auto_start, freq=sm_freq))
                    OLED_add_state_machines(sm, sm_freq)

                    stop = False
                    _thread.start_new_thread(pico_timecode_thread, (tc, rc, sm, lambda: stop))
                    menu = 0

            if menu==2:
                # Monitor incoming LTC only
                tc.acquire()
                if mode == 0:
                    tc.mode = 1
                    mode = 1
                    menu = 0
                elif mode == 1:
                    tc.mode = 0
                    mode = 0
                    menu = 0
                tc.release()

            if menu==3:
                # Jam to incoming LTC
                stop = True
                utime.sleep(1)

                tc.acquire()
                tc.mode = 2
                tc.release()

                sm = []
                sm.append(rp2.StateMachine(3, start_from_pin, freq=sm_freq,
                           jmp_pin=machine.Pin(21)))        # Sync from RX LTC
                OLED_add_state_machines(sm, sm_freq)

                stop = False
                _thread.start_new_thread(pico_timecode_thread, (tc, rc, sm, lambda: stop))
                menu = 0
                mode = 2

        if mode > 0:
            # Check whether we have jam'ed yet
            tc.acquire()
            mode = tc.mode
            tc.release()

        OLED.fill(0x0000)
        if menu:
            OLED.text(">" + menudisplay[menu],0,2,OLED.white)
        else:
            #OLED.text("USR:" + tc.user_to_ascii(),0,2,OLED.white)
            OLED.text("Menu:" ,0,2,OLED.white)

        if mode:
            OLED.text("RX:" + rc.to_ascii(),0,22,OLED.white)
            if mode > 1:
                OLED.text("Waiting to Jam",0,12,OLED.white)

        OLED.text(format,0,40,OLED.white)
        OLED.text("TX:" + tc.to_ascii(),0,52,OLED.white)
        OLED.show()

        utime.sleep(0.1)


#---------------------------------------------

if __name__ == "__main__":
    # set up starting values...
    tc = timecode()
    rc = timecode()

    OLED_display_thread(tc, rc)

