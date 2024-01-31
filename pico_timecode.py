# Pico-Timcode for Raspberry-Pi Pico
# (c) 2023-05-05 Simon Wood <simon@mungewell.org>
#
# https://github.com/mungewell/pico-timecode

import machine
import _thread
import utime
import rp2

import micropython
micropython.alloc_emergency_exception_buf(100)

from machine import Timer
from micropython import schedule

# set up Globals
eng = None
stop = False

tx_ticks_us = 0
rx_ticks_us = 0

core_dis = [0, 0]

#---------------------------------------------

@rp2.asm_pio()

def auto_start():
    irq(clear, 4)                   # just Trigger Sync...

    label("halt")
    jmp("halt") [31]


@rp2.asm_pio()

def start_from_pin():
    label("high")
    jmp(pin, "high")                # Wait for pin to go low first...

    wrap_target()
    jmp(pin, "start")               # Check pin, jump if 1
    wrap()

    label("start")
    irq(clear, 4)                   # Trigger Sync

    label("halt")
    jmp("halt") [31]


@rp2.asm_pio(set_init=rp2.PIO.OUT_LOW, autopull=True, out_shiftdir=rp2.PIO.SHIFT_RIGHT)

def blink_led():
    out(x, 16)                      # first cycle lenght may be slightly
                                    # different so LED is exactly timed...
    irq(block, 4)                   # Wait for sync'ed start

    label("read_new")
    wrap_target()
    irq(rel(0))                     # set IRQ for ticks_us monitoring
    out(y, 16)                      # Read pulse duration from FIFO

    jmp(not_y, "led_off")           # Do we turn LED on?
    set(pins, 1)
    jmp("cont")
    label("led_off")
    nop() [1]                       # this section 3 cycles

    label("cont")
    jmp(y_dec, "still_on")          # Does LED stay on?
    set(pins, 0) [1]
    jmp("cont2")
    label("still_on")
    nop() [2]                       # this section 4 cycles
        
    label("cont2")
    jmp(x_dec, "cont")              # Loop, so it is 80 * 32 =  2560 cycles
                                    # X = 510 -> 2 + 3 + (510 * (4 +1)) + 5 cycles
    out(x, 16) [4]
    wrap()


@rp2.asm_pio(out_init=rp2.PIO.OUT_LOW)
    
def encode_dmc():
    irq(block, 4)                   # Wait for Sync'ed start
    
    wrap_target()
    label("toggle-0")
    mov(pins, invert(pins)) [14]    # Always toogle pin at start of cycle, "0" or "1"

    jmp(pin, "toggle-1")            # Check output of SM-1 buffer, jump if 1

    nop() [14]                      # Wait out rest of cycle, "0"
    jmp("toggle-0")
    
    label("toggle-1")
    mov(pins, invert(pins)) [15]    # Toggle pin to signal '1'
    wrap()


@rp2.asm_pio(out_init=rp2.PIO.OUT_LOW, autopull=True,
             fifo_join=rp2.PIO.JOIN_TX, out_shiftdir=rp2.PIO.SHIFT_RIGHT)

def buffer_out():
    irq(block, 4)                   # Wait for Sync'ed start
    
    label("start")
    out(pins, 1) [30]
    
    jmp(not_osre, "start")
                                    # UNDERFLOW - when Python fails to fill FIFOs
    irq(rel(0))                     # set IRQ to warn other StateMachines
    wrap_target()
    set(pins, 0)
    wrap()


@rp2.asm_pio(set_init=(rp2.PIO.OUT_LOW,)*2)

def decode_dmc():
    label("previously_low")
    wait(1, pin, 0)         # Line going high
    irq(clear, 5)           # trigger sync engine, and wait til 3/4s mark
    nop()[18]

    jmp(pin, "staying_high")
    set(pins, 3)            # Second transition detected (data is `1`)
    jmp("previously_low")   # Wait for next bit...

    label("staying_high")
    set(pins, 0)            # Line still high, no centre transition (data is `0`)
                            # |
                            # | fall through... a few cycles early
                            # V
    wrap_target()
    label("previously_high")
    wait(0, pin, 0)         # Line going Low
    irq(clear, 5)           # trigger sync engine, and wait til 3/4s mark
    nop()[18]

    jmp(pin, "going_high")
    set(pins, 0)            # Line still low, no centre transition (data is `0`)
    jmp("previously_low")   # Wait for next bit...

    label("going_high")
    set(pins, 3)            # Second transition detected (therfore data is `1`)
    wrap()


@rp2.asm_pio(set_init=rp2.PIO.OUT_HIGH, out_init=rp2.PIO.OUT_LOW,
             autopull=True, autopush=True, in_shiftdir=rp2.PIO.SHIFT_RIGHT)

def sync_and_read():
    out(y, 32)              # Read the expected sync word to Y

    wrap_target()
    label("find_sync")
    mov(isr, x)             # force X value back into ISR, clears counter
    irq(block, 5)           # wait for input, databit ready
                            # Note: the following sample is from previous bit
    in_(pins, 2)            # Double clock input (ie duplicate bits)
    #in_(pins, 1)            # auto-push will NOT trigger...
    mov(x, isr)[10]

    jmp(x_not_y, "find_sync")
    set(pins, 0)

    set(x, 31)[8]#[14]           # Read in the next 32 bits
    mov(isr, null)          # clear ISR
    irq(rel(0))             # set IRQ for ticks_us monitoring
    set(pins, 0)            # signal 'data section' start

    label("next_bit")
    in_(pins, 1)			# 1st should be 32 cycles after last IRQ
    jmp(x_dec, "next_bit")[30]

    set(x, 30)              # Read in the next 31 bits

    label("next_bit2")
    in_(pins, 1)
    jmp(x_dec, "next_bit2")[30]

    set(pins, 1)
    in_(pins, 1)			# Read last bit
    wrap()


#-------------------------------------------------------
# handler for IRQs

def irq_handler(m):
    global eng, stop
    global tx_ticks_us, rx_ticks_us
    global core_dis

    core_dis[machine.mem32[0xd0000000]] = machine.disable_irq()
    ticks = utime.ticks_us()

    if m==eng.sm[1]:
        tx_ticks_us = ticks

    if m==eng.sm[5]:
        rx_ticks_us = ticks

    if m==eng.sm[2]:
        # Buffer Underflow
        stop = 1
        eng.mode = -1

    machine.enable_irq(core_dis[machine.mem32[0xd0000000]])

def timer_sched(timer):
    schedule(timer_re_init, timer)

def timer_re_init(timer):
    global eng

    period = 0
    lock = eng.dlock.acquire(0)

    if lock:
        # Got lock, change dividers
        integer = (machine.mem32[0x502000c8] >> 16) & 0xFFFF
        fraction = (machine.mem32[0x502000c8] >> 8) & 0xFF

        if (eng.duty > 0 and not eng.off_stage) or (eng.duty < 0 and eng.off_stage):
            # Inc divider
            if fraction == 0xFF:
                fraction = 0
                integer += 1
            else:
                fraction += 1
        else:
            # Dec divider
            if fraction == 0:
                fraction = 0xFF
                integer -= 1
            else:
                fraction -= 1

        # Set dividers for all PIO machines
        for base in [0x50200000, 0x50300000]:
            for offset in [0x0c8, 0x0e0, 0x0f8, 0x110]:
                machine.mem32[base + offset] = (integer << 16) + (fraction << 8)

        eng.dlock.release()

        if eng.off_stage:
            # Starting 'on' stage
            eng.off_stage = False
            period = int(eng.period * (abs(eng.duty) % 1))
        else:
            # Starting 'off' stage
            eng.off_stage = True
            period = int(eng.period * (1-(abs(eng.duty) % 1)))

    if period < 50:
        period = 50

    eng.timer.init(period=period, mode=Timer.ONE_SHOT, callback=timer_sched)

#---------------------------------------------

class timecode(object):

    fps = 30
    df = False      # Drop-Frame

    # Timecode - starting value
    hh = 0
    mm = 0
    ss = 0
    ff = 0

    # Colour Frame flag
    cf = False

    # Clock flag
    bgf1 = False

    # User bits - format depends on BF2 and BF0
    bgf0 = True     # 4 ASCII characters
    bgf2 = False

    uf1 = 0x0       # 'PICO'
    uf2 = 0x5
    uf3 = 0x9
    uf4 = 0x4
    uf5 = 0x3
    uf6 = 0x4
    uf7 = 0xF
    uf8 = 0x4

    # Lock for multithreading
    lock = _thread.allocate_lock()

    def acquire(self):
        self.lock.acquire()

    def release(self):
        self.lock.release()

    def validate_for_drop_frame(self, reverse=False):
        self.acquire()
        if not reverse and self.df and self.ss == 0 and \
                (self.ff == 0 or self.ff == 1):
            if self.mm % 10 != 0:
                self.ff += (2 - self.ff)
        if reverse and self.df and self.ss == 0 and \
                (self.ff == 0 or self.ff == 1):
            if self.mm % 10 != 0:
                if self.hh == 0:
                    self.hh = 23
                else:
                    self.hh -= 1
                self.mm = 59                    # only happens on mm==0
                self.ss = 59
                self.ff = self.fps - 1
        self.release()

    def from_ascii(self, start="00:00:00:00"):
        # Example "00:00:00:00"
        #          hh mm ss ff
        #          01234567890

        # convert ASCII to 'raw' BCD array
        time = [x - 0x30 for x in bytes(start, "utf-8")]

        self.acquire()
        self.hh = (time[0]*10) + time[1]
        self.mm = (time[3]*10) + time[4]
        self.ss = (time[6]*10) + time[7]
        self.ff = (time[9]*10) + time[10]
        if time[8] == 11:
            self.df = True
        else:
            self.df = False
        self.release()

        if self.df:
            self.validate_for_drop_frame()
    
    def to_ascii(self):
        self.acquire()
        time = [int(self.hh/10), (self.hh % 10), 10,
                int(self.mm/10), (self.mm % 10), 10,
                int(self.ss/10), (self.ss % 10), 10 + self.df,
                int(self.ff/10), (self.ff % 10)]
        self.release()

        new = ""
        for x in time:
            new += chr(x + 0x30)
        return(new)

    def from_raw(self, raw=0):
        self.acquire()
        self.df = (raw & 0x80000000) >> 31
        self.hh = (raw & 0x7F000000) >> 24
        self.mm = (raw & 0x00FF0000) >> 16
        self.ss = (raw & 0x0000FF00) >> 8
        self.ff = (raw & 0x000000FF)
        self.release()

    def to_raw(self):
        self.acquire()
        raw = (self.df << 31) + (self.hh << 24) + (self.mm << 16) + (self.ss << 8) + self.ff
        self.release()

        return raw

    def set_fps_df(self, fps=25, df=False):
        # should probably validate FPS/DF combo

        self.acquire()
        self.fps = fps
        self.df = df
        self.release()

        if self.df:
            self.validate_for_drop_frame()

        return True

    def next_frame(self):
        self.acquire()
        self.ff += 1
        if self.ff >= self.fps:
            self.ff = 0
            self.ss += 1
            if self.ss >= 60:
                self.ss = 0
                self.mm += 1
                if self.mm >= 60:
                    self.mm = 0
                    self.hh += 1
                    if self.hh >= 24:
                        self.hh = 0
        self.release()

        if self.df:
            self.validate_for_drop_frame()

    def prev_frame(self):
        self.acquire()
        self.ff -= 1
        if self.ff < 0:
            self.ff = self.fps - 1
            self.ss -= 1
            if self.ss < 0:
                self.ss = 59
                self.mm -= 1
                if self.mm < 0:
                    self.mm = 59
                    self.hh -= 1
                    if self.hh < 0:
                        self.hh = 23
        self.release()

        if self.df:
            self.validate_for_drop_frame(True)

    # parity check, count 1's in 32-bit word
    def lp(self, b):
        c = 0
        for i in range(32):
            c += (b >> i) & 1

        return(c)

    def to_ltc_packet(self, send_sync=False):
        f27 = False
        f43 = False
        f59 = False

        self.acquire()
        if self.fps == 25:
            f27 = self.bgf0
            f43 = self.bgf2
        else:
            f43 = self.bgf0
            f59 = self.bgf2

        p = []
        p.append((self.uf2 << 12) + (self.cf  << 11) + (self.df << 10) +
                ((int(self.ff/10) & 0x3) << 8) +
                (self.uf1 << 4) + (self.ff % 10) +
                (self.uf4 << 28) + (f27 << 27) +
                ((int(self.ss/10) & 0x7) << 24) +
                (self.uf3 << 20) + ((self.ss % 10) << 16))

        p.append((self.uf6 << 12) + (f43 << 11) +
                ((int(self.mm/10) & 0x7) << 8) +
                (self.uf5 << 4) + (self.mm % 10) +
                (self.uf8 << 28) + (f59 << 27) + (self.bgf1 << 26) +
                ((int(self.hh/10) & 0x3) << 24) +
                (self.uf7 << 20) + ((self.hh % 10) << 16))

        # polarity correction
        count = 13
        for i in p:
            count += self.lp(i)

        if count & 1:
            if self.fps == 25:
                p[1] += (True << 27)    # f59
            else:
                p[0] += (True << 27)    # f27
        self.release()

        if send_sync:
            # We want to send 'whole' 32bit words to FIFO, so add 2x Sync
            s = []
            s.append(((p[0] & 0x0000FFFF) << 16) + 0xBFFC)
            s.append(((p[1] & 0x0000FFFF) << 16) + ((p[0] & 0xFFFF0000) >> 16))
            s.append((0xBFFC << 16)              + ((p[1] & 0xFFFF0000) >> 16))

            return s
        else:
            return p

    def from_ltc_packet(self, p):
        if len(p) != 2:
            return False

        # reject if parity is not 1, note we are not including Sync word
        '''
        c = self.lp(p[0])
        c+= self.lp(p[1])
        if not c & 1:
            return False
        '''

        self.acquire()
        self.df = ((p[0] >> 10) & 0x01)
        self.ff = (((p[0] >>  8) & 0x3) * 10) + (p[0] & 0xF)
        self.ss = (((p[0] >> 24) & 0x7) * 10) + ((p[0] >> 16) & 0xF)
        self.mm = (((p[1] >>  8) & 0x7) * 10) + (p[1] & 0xF)
        self.hh = (((p[1] >> 24) & 0x3) * 10) + ((p[1] >> 16) & 0xF)

        if self.ff > self.fps:
            self.fps = self.ff
        self.release()
        
        return True


class engine(object):
    mode = 0        # -1=Halted, 0=FreeRun, 1=Monitor RX, 2>=Jam to RX

    tc = timecode()
    rc = timecode()
    sm = None

    duty = 0.5
    prev_duty = 0
    dlock = _thread.allocate_lock()

    timer = None
    off_stage = False

    # state of running (ie whether being used for output)
    stopped = True

    def is_stopped(self):
        return self.stopped

    def is_running(self):
        return not self.stopped

    def set_stopped(self, s=True):
        global stop

        self.stopped = s
        if s:
            stop = False
            self.timer = None
            self.asserted = False

            rduty = 0
            rcache = []

    def frig_clocks(self, fps):
        # divider computed for CPU clock at 120MHz
        if fps == 29.97:
            new_div = 0x061c1000
        elif fps == 23.976:
            new_div = 0x07a31400
        else:
            return

        # Set dividers for all PIO machines
        for base in [0x50200000, 0x50300000]:
            for offset in [0x0c8, 0x0e0, 0x0f8, 0x110]:
                machine.mem32[base + offset] = new_div

    def inc_divider(self):
        # increasing divider -> slower clock
        integer = (machine.mem32[0x502000c8] >> 16) & 0xFFFF
        fraction = (machine.mem32[0x502000c8] >> 8) & 0xFF

        if fraction == 0xFF:
            fraction = 0
            integer += 1
        else:
            fraction += 1

        # Set dividers for all PIO machines
        for base in [0x50200000, 0x50300000]:
            for offset in [0x0c8, 0x0e0, 0x0f8, 0x110]:
                machine.mem32[base + offset] = (integer << 16) + (fraction << 8)

    def dec_divider(self):
        # decreasing divider -> faster clock
        integer = (machine.mem32[0x502000c8] >> 16) & 0xFFFF
        fraction = (machine.mem32[0x502000c8] >> 8) & 0xFF

        if fraction == 0:
            fraction = 0xFF
            integer -= 1
        else:
            fraction -= 1

        # Set dividers for all PIO machines
        for base in [0x50200000, 0x50300000]:
            for offset in [0x0c8, 0x0e0, 0x0f8, 0x110]:
                machine.mem32[base + offset] = (integer << 16) + (fraction << 8)

    def crude_adjust(self, duty):
        dif = int(duty) - int(self.prev_duty)
        self.prev_duty = duty

        if dif==0:
            return

        self.dlock.acquire()
        for i in range(abs(dif)):
            if dif > 0:
                self.dec_divider()
            else:
                self.inc_divider()
        self.dlock.release()

    def micro_adjust(self, duty, period=60000): # period in ms
        if self.stopped:
            return

        self.duty = duty
        self.period = period
        self.crude_adjust(duty)

        if self.timer == None:
            self.off_stage = False
            period = int(self.period * (abs(duty) % 1))
            if period == 0:
                return

            # For 'on_stage'
            self.dlock.acquire()
            if duty > 0:
                self.dec_divider()
            else:
                self.inc_divider()
            self.dlock.release()

            # start up new hardware timer
            self.timer = Timer()
            self.timer.init(period=period, mode=Timer.ONE_SHOT, callback=timer_sched)
            return



#-------------------------------------------------------

def pico_timecode_thread(eng, stop):
    eng.set_stopped(False)

    send_sync = True        # send 1st packet with sync
    start_sent = False
    
    # Pre-load 'SYNC' word into RX decoder - only needed once
    # needs to be bit doubled 0xBFFC -> 0xCFFFFFF0
    eng.sm[5].put(3489660912)

    # Set up Blink/LED timing
    eng.sm[1].put(612)          # 1st cycle includes 'extra sync' correction
                            # (80 + 16) * 32 = 3072 cycles

    # Start the StateMachines (except Sync)
    for m in range(1, len(eng.sm)):
        eng.sm[m].active(1)
        utime.sleep(0.005)

    eng.tc.acquire()
    fps = eng.tc.fps
    df = eng.tc.df
    eng.tc.release()

    gc = timecode()
    gc.set_fps_df(fps, df)

    # Fine adjustment of the PIO clocks to compensate for XTAL inaccuracies
    # -1 -> +1 : +ve = faster clock, -ve = slower clock
    eng.micro_adjust(eng.duty, 10000)
    #eng.crude_adjust(eng.duty)

    # Loop, increasing frame count each time
    while not stop():

        # Empty RX FIFO as it fills
        if eng.sm[5].rx_fifo() >= 2:
            p = []
            p.append(eng.sm[5].get())
            p.append(eng.sm[5].get())
            if len(p) == 2:
                eng.rc.from_ltc_packet(p)

            if eng.mode > 1:
                # should perform some basic validation:
                g = gc.to_raw()
                r = eng.rc.to_raw()
                fail = False

                # check DF flags match
                if (r >> 31) != df:
                    fail = True

                # check packets are counting correctly
                if g!=0:
                    if g!=r:
                        fail = True

                if r!=0:
                    gc.from_raw(r)
                    gc.next_frame()
                else:
                    fail = True

                if fail:
                    eng.mode = 64       # Start process again
                else:
                    eng.mode -= 1

                if eng.mode == 1:
                    # Jam to 'next' RX timecode
                    if eng.mode == 1:
                        g = eng.rc.to_raw()
                        eng.tc.from_raw(g)
                        eng.tc.next_frame()
                        eng.tc.next_frame()

        # Wait for TX FIFO to be empty enough to accept more
        if eng.mode < 2 and eng.sm[2].tx_fifo() < 5: # and tc.to_int() < 0x0200:
            for w in eng.tc.to_ltc_packet(send_sync):
                eng.sm[2].put(w)
            send_sync = (send_sync + 1) & 1

            # Calculate next frame value
            eng.tc.next_frame()

            # Does the LED flash for this frame?
            eng.tc.acquire()
            if eng.tc.ff == 0:
                eng.sm[1].put((210 << 16)+ 509) # '209' duration of flash
            else:
                eng.sm[1].put(509)              # '509' is complete cycle length
            eng.tc.release()

            if not start_sent:
                # enable 'Start' machine last, so others can synchronise to it
                eng.sm[0].active(1)
                start_sent = True
            
        utime.sleep(0.001)

    # Stop the StateMachines
    for m in range(len(eng.sm)):
        eng.sm[m].active(0)
        utime.sleep(0.005)

    eng.set_stopped(True)

#-------------------------------------------------------

def ascii_display_thread(mode = 0):
    global eng, stop

    eng = engine()
    eng.mode = mode
    eng.set_stopped(True)

    # Reduce the CPU clock, for better computation of PIO freqs
    machine.freq(120000000)

    # Allocate appropriate StateMachines, and their pins
    eng.sm = []
    sm_freq = int(eng.tc.fps * 80 * 32)

    # Note: we always want the 'sync' SM to be first in the list.
    if eng.mode > 1:
        # We will only start after a trigger pin goes high
        eng.sm.append(rp2.StateMachine(0, start_from_pin, freq=sm_freq,
                           jmp_pin=machine.Pin(21)))        # RX Decoding
    else:
        eng.sm.append(rp2.StateMachine(0, auto_start, freq=sm_freq))

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
    if eng.mode > 1:
        eng.sm.append(rp2.StateMachine(4, decode_dmc, freq=sm_freq,
                           jmp_pin=machine.Pin(18),
                           in_base=machine.Pin(18),
                           set_base=machine.Pin(19)))   # Decoded LTC Input
    else:
        eng.sm.append(rp2.StateMachine(4, decode_dmc, freq=sm_freq,
                           jmp_pin=machine.Pin(13),     # Test - read from self/tx
                           in_base=machine.Pin(13),     # Test - read from self/tx
                           set_base=machine.Pin(19)))   # Decoded LTC Input

    eng.sm.append(rp2.StateMachine(5, sync_and_read, freq=sm_freq,
                           jmp_pin=machine.Pin(19),
                           in_base=machine.Pin(19),
                           out_base=machine.Pin(21),
                           set_base=machine.Pin(21)))       # 'sync' from RX bitstream

    # correct clock dividers for 29.98 and 23.976
    eng.frig_clocks(eng.tc.fps)

    # set up IRQ handler
    for m in eng.sm:
        m.irq(handler=irq_handler, hard=True)

    # Start up threads
    stop = False
    _thread.start_new_thread(pico_timecode_thread, (eng, lambda: stop))

    while True:
        if eng.mode:
            if eng.mode>1:
                print("Jamming:", eng.mode)
            print("RX:", eng.rc.to_ascii())

        if eng.mode == 0:
            print("TX:", eng.tc.to_ascii())

        if eng.mode < 0:
            print("Underflow Error")
            break

        utime.sleep(0.1)

#---------------------------------------------

if __name__ == "__main__":
    ascii_display_thread(1)
