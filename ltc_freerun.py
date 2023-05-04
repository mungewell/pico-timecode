# FreeRun LTC test for Raspberry-Pi Pico
# (c) 2023-05-03 Simon Wood <simon@mungewell.org>
#
# https://en.wikipedia.org/wiki/Linear_timecode

import machine
import _thread
import utime
import rp2

@rp2.asm_pio(out_init=rp2.PIO.OUT_LOW)
    
def encode_dmc():
    set(pins, 0)
    irq(block, 4)					# Wait for Sync
    
    wrap_target()
    label("toggle-0")
    mov(pins, invert(pins)) [2]		# Always toogle pin at start of cycle, "0" or "1"

    jmp(pin, "toggle-1")			# Check output of SM-1 buffer, jump if 1

    nop() [2]						# Wait out rest of cycle, "0"
    jmp("toggle-0")
    
    label("toggle-1")
    mov(pins, invert(pins)) [3]		# Toggle for signal '1'
    wrap()

@rp2.asm_pio(out_init=rp2.PIO.OUT_LOW, autopull=True,
             fifo_join=rp2.PIO.JOIN_TX, out_shiftdir=rp2.PIO.SHIFT_RIGHT)

def buffer_out():
    irq(clear, 4)					# Trigger Sync
    
    label("start")
    out(pins, 1) [6]
    
    jmp(not_osre, "start")
                                    # UNDERFLOW - when Python fails to fill FIFOs
    irq(1)							# set IRQ-1 to stop other StateMachines
    wrap_target()
    set(pins, 0)
    wrap()


@rp2.asm_pio(set_init=rp2.PIO.OUT_LOW, autopull=True,
             fifo_join=rp2.PIO.JOIN_TX)

def blink_led():
    set(pins, 0)
    out(x, 32)						# set first cycle is slightly short
                                    # so LED is exactly timed
    irq(block, 4) [2]				# Wait for sync

    label("read_new")
    wrap_target()
    out(y, 32)						# Read pulse duration from FIFO

    jmp(not_y, "led_off")			# is LED on this frame
    set(pins, 1) [1]
    jmp("cont")
    label("led_off")
    nop() [2]						# this section 4 cycles

    label("cont")
    jmp(y_dec, "still_on")			# Does LED stay on?
    set(pins, 0) [1]
    jmp("cont2")
    label("still_on")
    nop() [2]						# this section 4 cycles
        
    label("cont2")
    jmp(x_dec, "cont")				# Loop, so it is 80 * 8 =  640 cycles
                                    # X = 126 loops -> 1 + 4 + 630 + 5 cycles
    out(x, 32) [4]
    wrap()

#-------------------------------------------------------

# handler for IRQs 
def handler(flags):
    global still_running, sm0, sm1, sm2
    
    if flags & 512:
        #print("FIFO underflow - Stopping State Machines")
        sm0.active(0)
        sm1.active(0)
        sm2.active(0)
        still_running = False

# parity check, count 1's in byte
def bp(b):
    c = 0
    for i in range(8):
        c += (b >> i) & 1

    return(c)

def ltc_freerun_thread():
    global still_running, sm0, sm1, sm2
    global processing

    global hh, mm, ss, ff, df
    global fps, bf2, bf0
    global uf1, uf2, uf3, uf4, uf5, uf6, uf7, uf8

    sync_sent = False
    start_sent = False

    # Loop, increasing frame count each time
    while still_running:
        # build LTC frame
        cf = False
        f27 = False
        f43 = False
        f58 = False
        f59 = False

        # Compute as 8bit Bytes
        b1 = (uf1 << 4) + (ff % 10)
        b2 = (uf2 << 4) + (cf  << 3) + (df << 2)  + (int(ff/10) & 0xF)
        b3 = (uf3 << 4) + (ss % 10)
        b4 = (uf4 << 4) + (f27 << 3)              + (int(ss/10) & 0xF)
        b5 = (uf5 << 4) + (mm % 10)
        b6 = (uf6 << 4) + (f43 << 3)              + (int(mm/10) & 0xF)
        b7 = (uf7 << 4) + (hh % 10)
        b8 = (uf8 << 4) + (f59 << 3) + (f58 << 2) + (int(hh/10) & 0xF)
       
        # polarity correction
        count = bp(b1) + bp(b2) + bp(b3) + bp(b4) + bp(b5) + bp(b6) + bp(b7) + bp(b8) + 13
        if count & 1:
            # print("Fix parity")
            if fps == 25:
                b8 = (uf8 << 4) + (True << 3) + (f58 << 2) + (int(hh/10) & 0xF)
            else:
                b4 = (uf4 << 4) + (True << 3)              + (int(ss/10) & 0xF)

        while sm1.tx_fifo() > 5:
            # Wait for FIFO to be empty enough
            utime.sleep(0.01)
            
        # We want to send whole 32bit words, but only have 'next packet data' after looping
        if sync_sent:
            sm1.put(b1 + (b2 << 8) + (b3 << 16) + (b4 << 24))
            sm1.put(b5 + (b6 << 8) + (b7 << 16) + (b8 << 24))
            sync_sent = False
        else:
            sm1.put(0xBFFC         + (b1 << 16) + (b2 << 24))
            sm1.put(b3 + (b4 << 8) + (b5 << 16) + (b6 << 24))
            sm1.put(b7 + (b8 << 8) + (0xBFFC << 16))
            sync_sent = True
            
        # Does the LED flash for this frame?
        if not start_sent:
            sm2.put(124)			# 1st cycle sync correction
        else:
            sm2.put(125)
        if ff == 0:
            sm2.put(100)			# Y - duration on flash
        else:
            sm2.put(0)

        # We can start StateMachines now that they have data queued to send
        if not start_sent:
            sm0.active(1)
            sm2.active(1)
            utime.sleep(0.01)

            sm1.active(1)
            start_sent = True
            
            # Wait to start handler, as its interfers with statemachine-sync
            utime.sleep(0.01)
            rp2.PIO(0).irq(lambda pio: handler(pio.irq().flags()))

        # Calculate next frame value
        processing.acquire()
        ff += 1
        if ff >= fps:
            ff = 0
            ss += 1
            if ss >= 60:
                ss = 0
                mm += 1
                if mm >= 60:
                    mm = 0
                    hh += 1
                    if hh >= 24:
                        hh = 0

            # handle Drop-Frame where applicable
            if df and ss == 0 and ff == 0:
                if mm % 10 != 0:
                    ff += 2
        processing.release()

def ascii_display_thread():
    global processing
    global hh, mm, ss, ff, df
    global still_running
    
    while still_running:
        processing.acquire()
        time = [int(hh/10), (hh % 10), 10 + df,
                int(mm/10), (mm % 10), 10 + df,
                int(ss/10), (ss % 10), 10 + df,
                int(ff/10), (ff % 10)]
        processing.release()
        new = ""
        for x in time:
            new += chr(x + 0x30)
        print(new)

        utime.sleep(0.1)
    
#---------------------------------------------

if __name__ == "__main__":
    # set up starting values...
    
    fps = 25
    df = False      # Drop-Frame

    # user bits - format depends on BF2 and BF0
    bf2 = False
    bf0 = True		# 4 ASCII characters

    uf1 = 0x0		# 'PICO'
    uf2 = 0x5
    uf3 = 0x9
    uf4 = 0x4
    uf5 = 0x3
    uf6 = 0x4
    uf7 = 0xF
    uf8 = 0x4

    # Timecode - starting value
    start = b"00:00:00:00"
    #         hh mm ss ff
    #         01234567890

    #start = b"12:34:56:00"

    # convert ASCII to 'raw' BCD array
    time = [x - 0x30 for x in start]

    hh = (time[0]*10) + time[1]
    mm = (time[3]*10) + time[4]
    ss = (time[6]*10) + time[7]
    ff = (time[9]*10) + time[10]

    # validate for Drop-Frame ??
    if df and ss == 0 and ff == 0:
        if mm % 10 != 0:
            ff += 2

    still_running = True
    processing = _thread.allocate_lock()

    # Allocate appropriate StateMachines, and their pins
    sm0 = rp2.StateMachine(0, encode_dmc, freq=80 * fps * 8,
                           jmp_pin=machine.Pin(19),
                           in_base=machine.Pin(18),
                           out_base=machine.Pin(18))		# Encoded LTC Output
    sm1 = rp2.StateMachine(1, buffer_out, freq=80 * fps * 8,
                           out_base=machine.Pin(19))		# Output of 'raw' bitstream
    sm2 = rp2.StateMachine(2, blink_led, freq=80 * fps * 8,
                           set_base=machine.Pin(25))		# LED on Pico board

    # Start up threads
    _thread.start_new_thread(ltc_freerun_thread, ())
    ascii_display_thread()

    print("Threads Completed")
