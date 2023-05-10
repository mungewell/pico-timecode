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
# GP15 - User Key 'A'
# GP17 - User Key 'B'
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
     

def OLED_display_thread(tc, rc):
    tc.acquire()
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
    OLED.show()

    OLED.text(format,0,4,OLED.white)
    OLED.text("Pico-Timecode",0,28,OLED.white)
    if mode > 0:
        OLED.text("Waiting to Jam",2,50,OLED.white)
    else:
        OLED.text("'B' to start",0,50,OLED.white)
        
    OLED.show()
    utime.sleep(5)

    # Allocate appropriate StateMachines, and their pins
    sm = []
    sm_freq = int(fps * 80 * 16)

    # Note: we always want the 'sync' SM to be first in the list.
    if mode > 1:
        '''
        sm.append(rp2.StateMachine(3, auto_start, freq=sm_freq))
        '''
        sm.append(rp2.StateMachine(3, start_from_pin, freq=sm_freq,
                           jmp_pin=machine.Pin(21)))        # Sync from RX LTC
    else:
        sm.append(rp2.StateMachine(3, start_from_pin, freq=sm_freq,
                           jmp_pin=machine.Pin(17)))        # OLED User key-0

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

    # Start up threads
    _thread.start_new_thread(pico_timecode_thread, (tc, rc, sm))

    while True:
        if mode > 0:
            # Check whether we have jam'ed yet
            tc.acquire()
            mode = tc.mode
            tc.release()

        OLED.fill(0x0000)
        OLED.text(format,0,4,OLED.white)

        if mode:
            OLED.text("RX:" + rc.to_ascii(),0,28,OLED.white)
            if mode > 1:
                OLED.text("Waiting to Jam",2,50,OLED.white)
        else:
            OLED.text("TX:" + tc.to_ascii(),0,28,OLED.white)
        OLED.show()

        utime.sleep(0.1)


#---------------------------------------------

if __name__ == "__main__":
    # set up starting values...
    tc = timecode()
    rc = timecode()

    fps = tc.fps
    tc.mode = 2			# Force TX to Jam from RX

    OLED_display_thread(tc, rc)

