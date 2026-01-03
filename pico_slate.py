# Pico-Timcode for Raspberry-Pi Pico
# (c) 2023-05-08 Simon Wood <simon@mungewell.org>
#
# https://github.com/mungewell/pico-timecode

# implement a basic UI on hardware with Pico-OLED-1.3
#
# Pico-OLED-1.3 is connected as follows:
# GP6  - I2C_SDA
# GP7  - I2C_CLK
# GP8  - OLED_DC        (OLED not used on pico-slate)
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

from libs.neotimer import *
from libs.ht16k33segment import HT16K33Segment
from libs.ht16k33segment14 import HT16K33Segment14

import pico_timecode as pt

from machine import Pin,freq,reset,mem32,I2C
from utime import sleep
import _thread
import utime
import rp2
import gc

# Set up (extra) globals
powersave = False
menu_active = False
slate_HM = False
slate_SF = False

def start_state_machines(mode=pt.RUN):
    if pt.eng.is_running():
        pt.stop = True
        while pt.eng.is_running():
            sleep(0.1)

    # Force Garbage collection
    gc.collect()

    # restart...
    pt.eng.tc.from_ascii("00:00:00:00")
    pt.eng.sm = []
    sm_freq = int(pt.eng.tc.fps + 0.1) * 80 * 32

    pt.eng.mode = mode
    if mode > pt.RUN:
        pt.eng.sm.append(rp2.StateMachine(pt.SM_START, pt.start_from_sync, freq=sm_freq,
                           in_base=Pin(21),
                           jmp_pin=Pin(21)))        # RX Decoding
    else:
        pt.eng.sm.append(rp2.StateMachine(pt.SM_START, pt.auto_start, freq=sm_freq,
                           jmp_pin=Pin(21)))        # RX Decoding

    # TX State Machines
    pt.eng.sm.append(rp2.StateMachine(pt.SM_BLINK, pt.shift_led_irq, freq=sm_freq,
                               jmp_pin=Pin(27),
                               out_base=Pin(26)))       # LED on GPIO26
    pt.eng.sm.append(rp2.StateMachine(pt.SM_BUFFER, pt.buffer_out, freq=sm_freq,
                               out_base=Pin(22)))       # Output of 'raw' bitstream
    pt.eng.sm.append(rp2.StateMachine(pt.SM_ENCODE, pt.encode_dmc, freq=sm_freq,
                               jmp_pin=Pin(22),
                               in_base=Pin(13),         # same as pin as out
                               out_base=Pin(13)))       # Encoded LTC Output

    pt.eng.sm.append(rp2.StateMachine(pt.SM_TX_RAW, pt.tx_raw_value, freq=sm_freq))

    # RX State Machines
    pt.eng.sm.append(rp2.StateMachine(pt.SM_SYNC, pt.sync_and_read, freq=sm_freq,
                               jmp_pin=Pin(19),
                               in_base=Pin(19),
                               out_base=Pin(21),
                               set_base=Pin(21)))       # 'sync' from RX bitstream
    pt.eng.sm.append(rp2.StateMachine(pt.SM_DECODE, pt.decode_dmc, freq=sm_freq,
                               jmp_pin=Pin(18),         # LTC Input ...
                               in_base=Pin(18),         # ... from 'other' device
                               set_base=Pin(19)))       # Decoded LTC Input

    '''
    # DEBUG: check the PIO code space/addresses
    for base in [0x50200000, 0x50300000]:
        for offset in [0x0d4, 0x0ec, 0x104, 0x11c]:
            print("0x%8.8x : 0x%2.2x" % (base + offset, mem32[base + offset]))
    '''

    # correct clock dividers
    pt.eng.config_clocks(pt.eng.tc.fps)

    # set up IRQ handler
    for m in pt.eng.sm:
        m.irq(handler=pt.irq_handler, hard=True)

    pt.stop = False
    _thread.start_new_thread(pt.pico_timecode_thread, (pt.eng, lambda: pt.stop))

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

#---------------------------------------------

slate_current_fps_df = 0

slate_available_fps_df = [
        "30.00",
        "30.00-df",
        "29.97",
        "29.97-df",
        "25.00",
        "24.00",
        "23.98",
        ]

def slate_set_fps_df(fps=0, df=False, index=0):
    global disp, slate_current_fps_df

    while True:
        if not fps:
            asc = slate_available_fps_df[index]
            fps = float(asc[0:5])
            if len(asc) > 5:
                df = True
        else:
            asc = "{:.2f}".format(fps) + ("-df" if df == True else "")

        if asc in slate_available_fps_df:
            break

        fps = 30.00
        df = False

    pt.eng.tc.set_fps_df(fps, df)
    disp.set_fps_df(fps, df)

    slate_current_fps_df = slate_available_fps_df.index(asc)


def slate_show_fps_df(fps_df):
    global slate_HM, slate_SF

    if fps_df >= len(slate_available_fps_df):
        fps_df = 0

    asc = slate_available_fps_df[fps_df]

    for i in range(4):
        if slate_HM:
            slate_HM.set_character(asc[i+(1 if i>1 else 0)], \
                    i, has_dot=False) #(True if i==1 else False))
            slate_SF.set_character(" ", i)
        else:
            slate_SF.set_character(asc[i+(1 if i>1 else 0)], \
                    i, has_dot=(True if i==1 else False))

    if len(asc) > 5:
        '''
        - = 6         = 0x40
        d = 1 2 3 4 6 = 0x5e
        f = 0 4 5 6   = 0x71
        '''
        extend_glyph = 0
        if len(slate_SF.CHARSET) > 19:
            # include segment '7' on ECBUYING 14-segment
            extend_glyph = 0x80

        if slate_HM:
            slate_SF.set_glyph(0x40 + extend_glyph, 0)
            slate_SF.set_glyph(0x5e + extend_glyph, 1)
            slate_SF.set_glyph(0x71 + extend_glyph, 2)
        else:
            # overwrite last digits
            slate_SF.set_glyph(0x5e + extend_glyph, 2)
            slate_SF.set_glyph(0x71 + extend_glyph, 3)

    if slate_HM:
        slate_HM.draw()
    #slate_SF.set_colon(False)
    slate_SF.draw()

    return fps_df


def slate_display_thread(init_mode=pt.RUN):
    global disp, slate_current_fps_df
    global disp_asc, slate_open
    global powersave, menu_active
    global slate_HM, slate_SF, timerS
    global debug

    pt.eng = pt.engine()
    pt.eng.mode = init_mode
    pt.eng.set_stopped(True)

    keyA = Pin(15,Pin.IN,Pin.PULL_UP)
    keyB = Pin(17,Pin.IN,Pin.PULL_UP)
    timerA = Neotimer(50)
    timerB = Neotimer(50)
    timerHA = Neotimer(3000)
    timerHB = Neotimer(3000)

    # automatically Jam if booted with 'B' pressed
    if keyB.value() == 0:
        pt.eng.mode=pt.JAM

    debug = Pin(28,Pin.OUT)
    debug.off()

    # Configure Digi-Slate controls
    keyC = Pin(4,Pin.IN,Pin.PULL_UP)
    keyR = Pin(5,Pin.IN,Pin.PULL_UP)
    timerC = Neotimer(15)
    timerR = Neotimer(50)
    timerS = Neotimer(1000)

    # Display is made from 2x 4-character I2C modules
    # note: left module is mounted up-side-down
    slate_R = None
    slate_L = None
    try:
        '''
        # Adafruit 7-segment
        i2c = I2C(1, scl=Pin(3), sda=Pin(2), freq=1_200_000)
        slate_R = HT16K33Segment(i2c, i2c_address=0x70)
        slate_L = HT16K33Segment(i2c, i2c_address=0x71)
        '''
        # ECBUYING 14-segment
        i2c = I2C(1, scl=Pin(3), sda=Pin(2), freq=1_200_000)
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

    '''
    slate_HM.set_brightness(1)
    slate_SF.set_brightness(1)
    '''

    disp_asc = "--------"
    for i in range(4):
        if slate_HM:
            slate_HM.set_character(disp_asc[i], i)
        slate_SF.set_character(disp_asc[i+4], i)

    if slate_HM:
        slate_HM.draw()
    slate_SF.draw()
    timerS.start()

    # Reduce the CPU clock, for better computation of PIO freqs
    if machine.freq() != 180000000:
        machine.freq(180000000)

    # load PIO blocks, and start pico_timecode thread
    start_state_machines(pt.eng.mode)

    disp = pt.timecode()
    slate_set_fps_df(pt.eng.tc.fps, pt.eng.tc.df)
    slate_new_fps_df = slate_current_fps_df

    slate_open = False
    slate_rotated = False

    # register callbacks, functions to display TX data ASAP
    pt.irq_callbacks[pt.SM_BLINK] = slate_display_callback

    while not timerS.finished():
        sleep(0.1)

    if slate_HM:
        slate_HM.clear()
        slate_HM.draw()
    slate_SF.clear()
    slate_SF.draw()

    while True:
        if pt.eng.mode == pt.HALTED:
            for i in range(4):
                if slate_HM:
                    slate_HM.set_character("-", i)
                    slate_HM.draw()
                    slate_HM.set_blink_rate(2)

                slate_SF.set_character("-", i)
                slate_SF.draw()
                slate_SF.set_blink_rate(2)
            pt.stop = True

        '''
        if pt.eng.is_stopped():
            break
        '''

        if pt.eng.mode > pt.RUN:
            # Fall back to 'RUN' mode (outputing TX value) after 'JAM'
            # unless we initially requested 'MONITOR'
            # note: you can force JAM by holding key-B whilst booting
            if pt.eng.mode == pt.MONITOR and init_mode != pt.MONITOR:
                pt.eng.mode = pt.RUN

        # Check for clapper closing
        if slate_open and timerC.debounce_signal(keyC.value()==1):
            if menu_active:
                print("Menu cancelled")
                menu_active = 0

                if slate_HM:
                    slate_HM.clear()
                    slate_HM.draw()
                    slate_HM.set_blink_rate(0)

                slate_SF.clear()
                slate_SF.draw()
                slate_SF.set_blink_rate(0)

            slate_open = False
            timerS.start()

        # Once clapper has closed and timer expired, enter powersave
        '''
        if not slate_open and timerS.finished() and not powersave:
            if slate_HM:
                slate_HM.power_off()
            slate_SF.power_off()

            pt.irq_callbacks[pt.SM_BLINK] = None
            print("Entering powersave")
            sleep(0.1)

            pt.eng.set_powersave(True)
            powersave = True
        '''

        # Display FPS on slate when clapper is first lifted
        if not slate_open and timerC.debounce_signal(keyC.value()==0):
            if powersave:
                print("Exiting powersave")
                pt.eng.set_powersave(False)

                pt.irq_callbacks[pt.SM_BLINK] = slate_display_callback
                powersave = False

                if slate_HM:
                    slate_HM.power_on()
                slate_SF.power_on()

            slate_open = True
            timerS.start()

            slate_show_fps_df(slate_current_fps_df)

        # Powersave prevents functions below...
        if powersave and pt.eng.get_powersave():
            sleep(0.1)
            continue

        # Closed slate prevents functions below...
        if not slate_open:
            continue

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

        # Menu: Changing FPS/DF
        # note: cancel by closing clapper
        if menu_active:
            slate_new_fps_df = slate_show_fps_df(slate_new_fps_df)

            if slate_HM:
                slate_HM.set_blink_rate(2)
            slate_SF.set_blink_rate(2)
            sleep(0.25)

            # change with A key
            if timerA.debounce_signal(keyA.value()==0):
                slate_new_fps_df += 1

            # confirm with B key
            if timerB.debounce_signal(keyB.value()==0):
                if slate_HM:
                    slate_HM.set_blink_rate(0)
                slate_SF.set_blink_rate(0)

                menu_active = False
                print("Menu de-activated")

                if slate_current_fps_df != slate_new_fps_df:
                    if pt.eng.is_running():
                        pt.stop = True
                        while pt.eng.is_running():
                            print("stopping")
                            sleep(0.1)

                    slate_set_fps_df(index=slate_new_fps_df)
                
                    print("restarting", pt.eng.tc.fps)
                    start_state_machines(init_mode)

        # Active menu prevents function below...
        if menu_active:
            continue

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
                force_dp = False
                extend_glyph = 0
                if len(slate_SF.CHARSET) > 19:
                    # include segment '7' on ECBUYING
                    extend_glyph = 0x80

                if slate_HM:
                    slate_HM.set_glyph(0x6D + extend_glyph, 0)
                    slate_HM.set_glyph(0x6E + extend_glyph, 1)
                    slate_HM.set_glyph(0x54 + extend_glyph, 2)
                    slate_HM.set_glyph(0x58 + extend_glyph, 3)
                    slate_HM.draw()
                else:
                    # indicate Sync with all decimal points lit
                    force_dp = True

                # only display the SS:FF digits
                for i in range(4):
                    slate_SF.set_character(asc[4+i], i,
                                has_dot=(True if i==1 else force_dp))
                #slate_SF.set_colon(True)
                slate_SF.draw()

                # also print to console
                phase = ((4294967295 - pt.rx_ticks + 188) % 640) - 320
                if phase < -32:
                    # RX is ahead/earlier than TX
                    phases = ((" "*10) + ":" + ("+"*int(abs(phase/32))) + (" "*10)) [:21]
                elif phase > 32:
                    # RX is behind/later than TX
                    phases = ((" "*10) + ("-"*int(abs(phase/32))) + ":" + (" "*10)) [-21:]
                else:
                    phases = "          :          "

                if pt.eng.mode > pt.MONITOR:
                    print("Jamming:", pt.eng.mode)

                print("RX: %s (%4d %21s)" % (pt.eng.rc.to_ascii(), phase, phases))
                disp_asc = asc

        # Hold A for 3s select a different FPS/DF
        # note: cancel by closing clapper
        if timerHA.hold_signal(keyA.value()==0):
            print("Menu activated")
            slate_new_fps_df = slate_current_fps_df
            menu_active = True

        # Hold B for 3s to jam external LTC
        # note: this will stop LTC generation as PIO blocks need to be restarted
        if pt.eng.mode == pt.RUN and timerHB.hold_signal(keyB.value()==0):
            start_state_machines(pt.JAM)


def slate_display_callback(sm=None):
    global disp, disp_asc, slate_open
    global slate_HM, slate_SF, timerS
    global menu_active
    global debug

    if sm == pt.SM_BLINK:
        if pt.eng.mode == pt.RUN:
            # sync to 0th quarter (inc has happened)
            # send previously written frame
            if ((pt.quarters == 1) or not pt._hasUsbDevice) and \
                    not menu_active and slate_open == 1 and timerS.finished():
                debug.on()
                slate_SF.draw()
                if slate_HM:
                    slate_HM.draw()
                debug.off()

            # Figure out what TX frame to display
            disp.from_raw(pt.tx_raw)
            asc = disp.to_ascii()

            if disp_asc != asc:
                # print to console
                print("TX: %s" % asc)
                disp_asc = asc

                if not menu_active and slate_open == 1 and timerS.finished():
                    # pre-write values for next frame
                    disp.next_frame()
                    asc = disp.to_ascii(False)
                    for i in range(4):
                        if slate_HM:
                            slate_HM.set_character(asc[i], i,
                                    has_dot=False)
                        slate_SF.set_character(asc[4+i], i,
                                has_dot=(True if i==1 else False))


#---------------------------------------------

if __name__ == "__main__":
    print("Pico-Slate, using:")
    print("Pico-Timecode " + pt.VERSION)
    print("www.github.com/mungewell/pico-timecode")
    sleep(2)

    slate_display_thread()
