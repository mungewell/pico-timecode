# Pico-Timcode for Raspberry-Pi Pico
# (c) 2023-05-05 Simon Wood <simon@mungewell.org>
#
# https://github.com/mungewell/pico-timecode

import _thread
import rp2

from machine import Timer, Pin, mem32, disable_irq, enable_irq, freq, lightsleep
from micropython import schedule, alloc_emergency_exception_buf
from utime import sleep, ticks_us
from gc import collect
from os import uname

# remember to do install lib to device
# 'mpremote mip install usb-device-midi'
import usb.device
from usb.device.midi import MIDIInterface

alloc_emergency_exception_buf(100)

VERSION="v2.1+"

# set up Globals
eng = None
stop = False

tx_raw = 0
rx_ticks = 0
rx_ticks_us = 0
tx_ticks_us = 0

core_dis = [0, 0]

# Constants for run mode
HALTED  = -1
RUN     = 0
MONITOR = 1
JAM     = 64

# Constants for StateMachines
# PIO block 1
SM_START    = 0
SM_BLINK    = 1
SM_BUFFER   = 2
SM_ENCODE   = 3
# PIO block 2
SM_TX_RAW   = 4
SM_SYNC     = 5
SM_DECODE   = 6

irq_callbacks = [None]*8

#---------------------------------------------

@rp2.asm_pio(autopull=True, autopush=True)

def auto_start():
    set(x, 0)
    nop()
    nop()
    irq(clear, 4)                   # immediately trigger Sync
                                    # --
    label("wait_for_low")           # loop length 4 clocks
    jmp(x_dec, "null1")
    label("null1")
    jmp(pin, "wait_for_low") [2]
                                    # --
    label("triggered")              # section length 4 clocks
                                    # capture count when pin goes low
    mov(isr, x)                     # mov X into ISR
    push()
    jmp(x_dec, "wait_for_high") [1]
                                    # --
    wrap_target()
    label("wait_for_high")          # loop length 4 clocks
    jmp(x_dec, "null2")
    label("null2")
    jmp(pin, "wait_for_low") [2]
    wrap()


@rp2.asm_pio(autopull=True, autopush=True)

def start_from_sync():
    set(x, 0)
    wait(0, pin, 0)                 # Wait for pin to go low
    wait(1, pin, 0)                 # Wait for pin to go high

    irq(clear, 4)                   # Trigger Sync
                                    # --
    label("wait_for_low")           # loop length 4 clocks
    jmp(x_dec, "null1")
    label("null1")
    jmp(pin, "wait_for_low") [2]
                                    # --
    label("triggered")              # section length 4 clocks
                                    # capture count when pin goes low
    mov(isr, x)                     # mov X into ISR
    push()
    jmp(x_dec, "wait_for_high") [1]
                                    # --
    wrap_target()
    label("wait_for_high")          # loop length 4 clocks
    jmp(x_dec, "null2")
    label("null2")
    jmp(pin, "wait_for_low") [2]
    wrap()

@rp2.asm_pio(out_init=(rp2.PIO.OUT_HIGH,)*2,
        autopull=True, out_shiftdir=rp2.PIO.SHIFT_RIGHT)

def shift_led2():
    irq(block, 4)                   # Wait for sync'ed start
                                    # ---
    wrap_target()
    irq(rel(0))                     # set IRQ for tx_ticks_us monitoring
    out(x, 6)

    label("next")
    out(pins, 2)                    # LEDs are bit-shifted pattern
                                    # each loop should be 256 cycles
                                    # representing each of the bytes in packet
    set(y, 7) [4]
    jmp(x_dec, "delay")

    pull() [27]
    jmp(y_dec, "delay")

    label("delay")
    jmp(y_dec, "delay") [30]        # 8 * 31 = 248 + 8 = 256 cycles
    jmp(x_not_y, "next")
    wrap()

@rp2.asm_pio(out_init=(rp2.PIO.OUT_HIGH,)*2, autopull=True,
             fifo_join=rp2.PIO.JOIN_TX, out_shiftdir=rp2.PIO.SHIFT_RIGHT)

def shift_led_mtc():
    irq(block, 4)                   # Wait for sync'ed start
                                    # ---
    wrap_target()
    out(x, 6)                       # nominally 20 per frame

    label("next")
    out(pins, 2)                    # LEDs are bit-shifted pattern
                                    # each loop should be 127 cycles
                                    # representing each of the bytes in packet
    jmp(pin, "skip_irq")
    irq(rel(0)) [4]                 # set IRQ clocking quarter frame MTC data
                                    # _extra_ cycles 4x per frame = 20cycles

    label("skip_irq")
    set(y, 3) [2]
    jmp(x_dec, "delay")

    pull() [27]                     # last sub-frame only
    jmp(y_dec, "delay")

    label("delay")
    jmp(y_dec, "delay") [29]        # 4 * 30 = 120 + 8 = 127 cycles
    jmp(x_not_y, "next")
    wrap()

@rp2.asm_pio(out_init=rp2.PIO.OUT_LOW)

def encode_dmc():
    irq(block, 4)                   # Wait for Sync'ed start

    wrap_target()
    label("toggle-0")
    mov(pins, invert(pins)) [14]    # Always toogle pin at start of cycle, "0" or "1"

    jmp(pin, "toggle-1")            # Check output of SM-1 buffer, jump if 1

    jmp("toggle-0") [15]

    label("toggle-1")
    mov(pins, invert(pins)) [15]    # Toggle pin to signal '1'
    wrap()

# 'how-to' example for differential output
@rp2.asm_pio(out_init=(rp2.PIO.OUT_HIGH, rp2.PIO.OUT_LOW))

def encode_dmc2():
    irq(block, 4)                   # Wait for Sync'ed start

    wrap_target()
    label("toggle-0")
    mov(pins, invert(pins)) [14]    # Always toogle pin at start of cycle, "0" or "1"

    jmp(pin, "toggle-1")            # Check output of SM-1 buffer, jump if 1

    jmp("toggle-0") [15]

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
    irq(clear, 5) [19]      # trigger sync engine, and wait til 3/4s mark

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
    irq(clear, 5) [19]      # trigger sync engine, and wait til 3/4s mark

    jmp(pin, "going_high")
    set(pins, 0)            # Line still low, no centre transition (data is `0`)
    jmp("previously_low")   # Wait for next bit...

    label("going_high")
    set(pins, 3)            # Second transition detected (therfore data is `1`)
    wrap()


@rp2.asm_pio(set_init=rp2.PIO.OUT_LOW, out_init=rp2.PIO.OUT_LOW,
             autopull=True, autopush=True, in_shiftdir=rp2.PIO.SHIFT_RIGHT)

def sync_and_read():
    out(y, 32)              # Read the expected sync word to Y

    wrap_target()
    label("find_sync")
    mov(isr, x)             # force X value back into ISR, clears counter
    irq(block, 5)           # wait for input, databit ready
                            # Note: the following sample is from previous bit
    in_(pins, 2)            # Double clock input (ie duplicate bits)
    mov(x, isr)[10]

    jmp(x_not_y, "find_sync")
    set(pins, 0)

    set(x, 31)[8]           # Read in the next 32 bits
    mov(isr, null)          # clear ISR
    irq(rel(0))             # set IRQ for rx_ticks_us monitoring
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

@rp2.asm_pio(autopull=True, autopush=True)

def tx_raw_value():
    wrap_target()
    out(x, 32)                      # will 'block' waiting for TX timecode
    in_(x, 32)                      # and then write back into FIFO
    wrap()

#-------------------------------------------------------
# handler for IRQs

def irq_handler(m):
    global eng, stop
    global tx_raw, rx_ticks_us, tx_ticks_us
    global core_dis

    core_dis[mem32[0xd0000000]] = disable_irq()
    ticks = ticks_us()

    if m==eng.sm[SM_BLINK]:
        if eng.sm[SM_TX_RAW].rx_fifo():
            tx_raw = eng.sm[SM_TX_RAW].get()
        tx_ticks_us = ticks

    if m==eng.sm[SM_SYNC]:
        rx_ticks_us = ticks

    if m==eng.sm[SM_BUFFER]:
        # Buffer Underflow
        stop = 1
        eng.mode = HALTED

    # check/schedule any registered callbacks
    for i in range(len(eng.sm)):
        if irq_callbacks[i] and m==eng.sm[i]:
            schedule(irq_callbacks[i], i)

    enable_irq(core_dis[mem32[0xd0000000]])


def timer_sched(timer):
    schedule(timer_re_init, timer)

def timer_re_init(timer):
    global eng

    # do not re-init if we are stopping/stopped
    if eng.is_running() == False:
        return

    # if timer1 exists it means we are dithering
    # between two values
    if timer == eng.timer1:
        if eng.calval > 0:
            eng.dec_divider()
        else:
            eng.inc_divider()

        eng.timer1.deinit()
        eng.timer1 = None

    if timer == eng.timer2:
        if eng.timer1:
            # timer1 should completed first
            print("!!!")
            eng.timer1.deinit()
            eng.timer1 = None

        eng.timer2.deinit()
        eng.timer2 = None
        eng.micro_adjust(eng.next_calval)

    if timer == eng.timer3:
        # This should NEVER occur, it means previous timers were missed.
        print("!!!!!")
        if eng.timer1:
            eng.timer1.deinit()
        if eng.timer2:
            eng.timer2.deinit()

        eng.timer1 = None
        eng.timer2 = None
        eng.micro_adjust(eng.next_calval)

#---------------------------------------------

# https://web.archive.org/web/20240000000000*/http://www.barney-wol.net/time/timecode.html
# lookup this text in array, index is value used in TC

tzs = [ \
    "+0000","-0100","-0200","-0300","-0400","-0500","-0600","-0700","-0800","-0900", \
    "-0030","-0130","-0230","-0330","-0430","-0530", \
    "-1000","-1100","-1200","+1300","+1200","+1100","+1000","+0900","+0800","+0700", \
    "-0630","-0730","-0830","-0930","-1030","-1130", \
    "+0600","+0500","+0400","+0300","+0200","+0100","Undef","Undef","TP-03","TP-02", \
    "+1130","+1030","+0930","+0830","+0730","+0630", \
    "TP-01","TP-00","+1245","Undef","Undef","Undef","Undef","Undef","+XXXX","Undef", \
    "+0530","+0430","+0330","+0230","+0130","+0030"]


class timecode(object):
    def __init__(self):
        self.fps = 30.0
        self.df = False      # Drop-Frame

        # Timecode - starting value
        self.hh = 0
        self.mm = 0
        self.ss = 0
        self.ff = 0

        # Colour Frame flag
        self.cf = False

        # Clock flag
        self.bgf1 = False

        # User bits - format depends on BF2 and BF0
        self.bgf0 = True     # 4 ASCII characters
        self.bgf2 = False

        self.uf1 = 0x0       # 'PICO'
        self.uf2 = 0x5
        self.uf3 = 0x9
        self.uf4 = 0x4
        self.uf5 = 0x3
        self.uf6 = 0x4
        self.uf7 = 0xF
        self.uf8 = 0x4

        # Lock for multithreading
        self.lock = _thread.allocate_lock()

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
                self.ff = int(self.fps + 0.1) - 1
        self.release()

    def from_ascii(self, start="00:00:00:00", sep=True):
        # Example "00:00:00:00"
        #          hh mm ss ff
        #          01234567890

        # convert ASCII to 'raw' BCD array
        time = [x - 0x30 for x in bytes(start, "utf-8")]

        self.acquire()
        if sep == True:
            # only change DF if separators are given
            self.df = False

            self.hh = (time[0]*10) + time[1]
            self.mm = (time[3]*10) + time[4]
            self.ss = (time[6]*10) + time[7]
            self.ff = (time[9]*10) + time[10]

            if time[8] != 10:
                self.df = True
        else:
            self.hh = (time[0]*10) + time[1]
            self.mm = (time[2]*10) + time[3]
            self.ss = (time[4]*10) + time[5]
            self.ff = (time[6]*10) + time[7]
        self.release()

        if self.df:
            self.validate_for_drop_frame()
    
    def to_ascii(self, sep=True):
        self.acquire()
        if sep == True:
            time = [int(self.hh/10), (self.hh % 10), 10,
                    int(self.mm/10), (self.mm % 10), 10,
                    int(self.ss/10), (self.ss % 10),
                    (-2 if self.df == True else 10),    # use '.' for DF
                    int(self.ff/10), (self.ff % 10)]
        else:
            time = [int(self.hh/10), (self.hh % 10),
                    int(self.mm/10), (self.mm % 10),
                    int(self.ss/10), (self.ss % 10),
                    int(self.ff/10), (self.ff % 10)]
        self.release()

        new = ""
        for x in time:
            new += chr(x + 0x30)
        return(new)

    def from_raw(self, raw=0):
        self.acquire()
        self.df = (raw & 0x00000080) >> 7
        self.hh = (raw & 0x1F000000) >> 24
        self.mm = (raw & 0x003F0000) >> 16
        self.ss = (raw & 0x00003F00) >> 8
        self.ff = (raw & 0x0000001F)
        self.release()

    def to_raw(self):
        self.acquire()
        raw = (self.df << 7) + (self.hh << 24) + (self.mm << 16) + (self.ss << 8) + self.ff
        self.release()

        return raw

    def set_fps_df(self, fps=25.0, df=False):
        # should probably validate FPS/DF combo

        self.acquire()
        self.fps = fps
        self.df = df
        self.release()

        if self.df:
            self.validate_for_drop_frame()

        return True

    def next_frame(self, repeats=1):
        while repeats:
            repeats -= 1

            self.acquire()
            self.ff += 1
            if self.ff >= int(self.fps + 0.1):
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

    def prev_frame(self, repeats=1):
        while repeats:
            repeats -= 1

            self.acquire()
            self.ff -= 1
            if self.ff < 0:
                self.ff = int(self.fps + 0.1) - 1
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

    def to_ltc_packet(self, send_sync=False, release=True):
        f27 = False
        f43 = False
        f59 = False

        self.acquire()
        if self.fps == 25.0:
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
            if self.fps == 25.0:
                p[1] += (True << 27)    # f59
            else:
                p[0] += (True << 27)    # f27

        if release:
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

    def from_ltc_packet(self, p, acquire=True):
        if len(p) != 2:
            if not acquire:
                # assume previously aquired
                self.release()
            return False

        # reject if parity is not 1, note we are not including Sync word
        '''
        c = self.lp(p[0])
        c+= self.lp(p[1])
        if not c & 1:
            return False
        '''

        if acquire:
            self.acquire()
        self.df = ((p[0] >> 10) & 0x01)
        self.ff = (((p[0] >>  8) & 0x3) * 10) + (p[0] & 0xF)
        self.ss = (((p[0] >> 24) & 0x7) * 10) + ((p[0] >> 16) & 0xF)
        self.mm = (((p[1] >>  8) & 0x7) * 10) + (p[1] & 0xF)
        self.hh = (((p[1] >> 24) & 0x3) * 10) + ((p[1] >> 16) & 0xF)

        if self.fps == 25.0:
            self.bgf0 = (p[0] >> 27) & 0x01 # f27
            self.bgf2 = (p[1] >> 11) & 0x01 # f43
        else:
            self.bgf0 = (p[1] >> 11) & 0x01 # f43
            self.bgf2 = (p[1] >> 27) & 0x01 # f59

        self.bgf1 = (p[1] >> 26) & 0x01

        self.uf1 = ((p[0] >>  4) & 0x0F)
        self.uf2 = ((p[0] >> 12) & 0x0F)
        self.uf3 = ((p[0] >> 20) & 0x0F)
        self.uf4 = ((p[0] >> 28) & 0x0F)

        self.uf5 = ((p[1] >>  4) & 0x0F)
        self.uf6 = ((p[1] >> 12) & 0x0F)
        self.uf7 = ((p[1] >> 20) & 0x0F)
        self.uf8 = ((p[1] >> 28) & 0x0F)

        self.release()
        if self.ff >= int(self.fps):
            return False
        return True

    def user_to_ascii(self):
        new = ""
        if self.bgf1==True:
            # TC is referenced to real time
            new += "*"

        if self.bgf0==True and self.bgf2==True:
            return("Page/Line NA")

        self.acquire()
        if self.bgf0==False and self.bgf2==False:
            # Userbits are BCD/Hex
            dehex = [0x30,0x31,0x32,0x33,0x34,0x35,0x36,0x37, \
                    0x38,0x39,0x41,0x42,0x43,0x44,0x45,0x46]
            user = [dehex[self.uf8], dehex[self.uf7], \
                    dehex[self.uf6], dehex[self.uf5], \
                    dehex[self.uf4], dehex[self.uf3], \
                    dehex[self.uf2], dehex[self.uf1]]
        elif self.bgf0==False and self.bgf2==True:
            # Userbits are Date/Timezone
            user = [0x59, 0x30+self.uf6, 0x30+self.uf5, 0x2D, \
                    0x4D, 0x30+self.uf4, 0x30+self.uf3, 0x2D, \
                    0x44, 0x30+self.uf2, 0x30+self.uf1]
        else:
            # Userbits are ASCII
            user = [(self.uf2 << 4) + self.uf1,
                    (self.uf4 << 4) + self.uf3,
                    (self.uf6 << 4) + self.uf5,
                    (self.uf8 << 4) + self.uf7]

        for x in user:
            new += chr(x)

        if self.bgf0==False and self.bgf2==True:
            i = (self.uf8 << 4) + self.uf7
            if i < len(tzs):
                new += tzs[i]
            else:
                new += tzs[0]

        self.release()
        return(new)

    def user_from_ascii(self, asc="PICO"):
        user = [x for x in bytes(asc+"    ", "utf-8")]

        self.acquire()
        self.bgf0 = True
        self.bgf2 = False
        self.uf1 = (user[0] >> 0) & 0x0F
        self.uf2 = (user[0] >> 4) & 0x0F
        self.uf3 = (user[1] >> 0) & 0x0F
        self.uf4 = (user[1] >> 4) & 0x0F
        self.uf5 = (user[2] >> 0) & 0x0F
        self.uf6 = (user[2] >> 4) & 0x0F
        self.uf7 = (user[3] >> 0) & 0x0F
        self.uf8 = (user[3] >> 4) & 0x0F
        self.release()

        return True

    def user_from_bcd_hex(self, bcd="00000000"):
        user = []
        for x in bytes(bcd + "00000000", "utf-8"):
            if (x >= 0x30) and (x < 0x3A):
                user.append(x - 0x30)
            if (x >= 0x41) and (x < 0x47):
                user.append(x - 0x37)
            if (x >= 0x61) and (x < 0x67):
                user.append(x - 0x57)

        self.acquire()
        self.bgf0 = False
        self.bgf2 = False
        self.uf1 = (user[7] & 0x0F)
        self.uf2 = (user[6] & 0x0F)
        self.uf3 = (user[5] & 0x0F)
        self.uf4 = (user[4] & 0x0F)
        self.uf5 = (user[3] & 0x0F)
        self.uf6 = (user[2] & 0x0F)
        self.uf7 = (user[1] & 0x0F)
        self.uf8 = (user[0] & 0x0F)
        self.release()

        return True

    def user_from_date(self, date="Y74-M01-D01+0000"):
        # Example "Y00-M00-D00+0000"
        #          Yyy Mmm Dddzzzzz
        #          0123456789012345
        user = [x-0x30 for x in bytes(date, "utf-8")]

        self.acquire()
        self.bgf0 = False
        self.bgf2 = True
        self.uf1 = user[10] # DD
        self.uf2 = user[9]
        self.uf3 = user[6]  # MM
        self.uf4 = user[5]
        self.uf5 = user[2]  # YY
        self.uf6 = user[1]

        self.uf7 = 0
        self.uf8 = 0
        for i in range(len(tzs)):
            if date[11:] == tzs[i]:
                self.uf7 = i & 0x0F
                self.uf8 = (i>>4) & 0xFF
                break
        self.release()

        return True


#---------------------------------------------

class engine(object):
    def __init__(self):
        self.mode = RUN
        self.flashframe = 0
        self.flashtime = 0  # 'raw' TC
        self.dlock = _thread.allocate_lock()

        self.tc = timecode()
        self.rc = timecode()
        self.sm = None

        self.calval = 0
        self.next_calval = 0

        self.period = 10000 # 10s, can be update by client
        self.timer1 = None
        self.timer2 = None
        self.timer3 = None

        # state of running (ie whether being used for output)
        self.stopped = True
        self.powersave = False
        self.ps_en0 = 0
        self.ps_en1 = 0

    def is_stopped(self):
        return self.stopped

    def is_running(self):
        return not self.stopped

    def set_stopped(self, s=True):
        global stop

        self.stopped = s
        if s:
            self.powersave = False
            self.tc.bgf1 = False

            if self.timer1:
                self.timer1.deinit()
                self.timer1 = None
            if self.timer2:
                self.timer2.deinit()
                self.timer2 = None
            if self.timer3:
                self.timer3.deinit()
                self.timer3 = None

            stop = False
            self.asserted = False

            self.calval = 0
            self.next_calval = 0

    def set_powersave(self, p=True, ps_en0=0, ps_en1=0):
        self.ps_en0 = ps_en0
        self.ps_en1 = ps_en1
        self.powersave = p

    def get_powersave(self):
        return self.powersave

    def config_clocks(self, fps, calval=0):
        if calval == 0:
            calval = self.calval

        # optimal divider computed for CPU clock at 120MHz
        if fps == 30.00:
            new_div = 0x061a8000
        elif fps == 29.97:
            new_div = 0x061c1000
        elif fps == 25.00:
            new_div = 0x07530000
        elif fps == 24.98:
            new_div = 0x0754e000
        elif fps == 24.00:
            new_div = 0x07a12000
        elif fps == 23.98:
            new_div = 0x07a31400
        else:
            return

        # apply divider offset, from calibration value
        new_div -= int(calval) << 8

        # Set dividers for all PIO machines
        self.dlock.acquire()
        '''
        for base in [0x50200000, 0x50300000]:
            for offset in [0x0c8, 0x0e0, 0x0f8, 0x110]:
                mem32[base + offset] = new_div
        '''
        mem32[0x502000c8] = new_div
        mem32[0x502000e0] = new_div
        mem32[0x502000f8] = new_div
        mem32[0x50200110] = new_div
        mem32[0x503000c8] = new_div
        mem32[0x503000e0] = new_div
        mem32[0x503000f8] = new_div
        mem32[0x50300110] = new_div

        self.dlock.release()

    def inc_divider(self):
        # increasing divider -> slower clock
        self.dlock.acquire()
        new_div = mem32[0x502000c8] + 0x0100

        # Set dividers for all PIO machines
        '''
        for base in [0x50200000, 0x50300000]:
            for offset in [0x0c8, 0x0e0, 0x0f8, 0x110]:
                mem32[base + offset] = (integer << 16) + (fraction << 8)
        '''
        mem32[0x502000c8] = new_div
        mem32[0x502000e0] = new_div
        mem32[0x502000f8] = new_div
        mem32[0x50200110] = new_div
        mem32[0x503000c8] = new_div
        mem32[0x503000e0] = new_div
        mem32[0x503000f8] = new_div
        mem32[0x50300110] = new_div

        self.dlock.release()

    def dec_divider(self):
        # decreasing divider -> faster clock
        self.dlock.acquire()
        new_div = mem32[0x502000c8] - 0x0100

        # Set dividers for all PIO machines
        '''
        for base in [0x50200000, 0x50300000]:
            for offset in [0x0c8, 0x0e0, 0x0f8, 0x110]:
                machine.mem32[base + offset] = (integer << 16) + (fraction << 8)
        '''
        mem32[0x502000c8] = new_div
        mem32[0x502000e0] = new_div
        mem32[0x502000f8] = new_div
        mem32[0x50200110] = new_div
        mem32[0x503000c8] = new_div
        mem32[0x503000e0] = new_div
        mem32[0x503000f8] = new_div
        mem32[0x50300110] = new_div

        self.dlock.release()

    def micro_adjust(self, calval, period=0):
        if self.stopped:
            if eng.timer1:
                eng.timer1.deinit()
                eng.timer1 = None
            if eng.timer2:
                eng.timer2.deinit()
                eng.timer2 = None
            if eng.timer3:
                eng.timer3.deinit()
                eng.timer3 = None
            return

        self.next_calval = calval
        if period > 0:
            # in ms, only change if specified
            self.period = period

        if self.timer1 == None and self.timer2 == None:
            self.calval = calval

            # re-init clock dividers
            self.config_clocks(self.tc.fps, calval)

            # re-init timers
            if eng.timer2:
                eng.timer2.deinit()
            self.timer2 = Timer()
            self.timer2.init(period=self.period, mode=Timer.ONE_SHOT, callback=timer_sched)

            # safety timer - triggers 2s after timer2, if timer2 fails
            if eng.timer3:
                eng.timer3.deinit()
            self.timer3 = Timer()
            self.timer3.init(period=self.period + 2000, mode=Timer.ONE_SHOT, callback=timer_sched)

            # are we dithering between two clock values?
            part = int(self.period * (abs(calval) % 1))
            if part > 0:
                if eng.timer1:
                    eng.timer1.deinit()
                self.timer1 = Timer()
                self.timer1.init(period=self.period - part, mode=Timer.ONE_SHOT, callback=timer_sched)


    def set_flashtime(self, ft):
        self.dlock.acquire()
        self.flashtime = (ft.df << 7) + (ft.hh << 24) + (ft.mm << 16) + (ft.ss << 8) + ft.ff
        self.dlock.release()

#-------------------------------------------------------

def pico_timecode_thread(eng, stop):
    global tx_raw, rx_ticks
    global debug

    debug = Pin(28,Pin.OUT)
    debug.off()

    eng.set_stopped(False)
    
    # Pre-load 'SYNC' word into RX decoder - only needed once
    # needs to be bit doubled 0xBFFC -> 0xCFFFFFF0
    eng.sm[SM_SYNC].put(0xCFFFFFF0)

    # Set up Blink/LED timing
    # 1st LED on for 6 (~10ms) of 20 sub-divisions
    # plus 4 sub-divions of 'extra sync'
    # needs to be '00000_00000_00001_11111__1111->'
    #
    # 2nd LED out is used to trigger MTC quarter packets
    # plus 4 sub-divions of 'extra sync'
    # needs to be '11110_11110_11110_11110__1110->'
    #
    # combined for the 24 sub-divisions, split across 32words
    # '10101001111111110111111101->'
    # '10101010101010101000101010100010'
    eng.sm[SM_BLINK].put((0b10101001111111110111111101 << 6) + 23)
    eng.sm[SM_BLINK].put((0b10101010101010101000101010100010))
    send_sync = True        # send 1st packet with sync header

    # Ensure Timecodes are using same fps/df settings
    eng.tc.acquire()
    fps = eng.tc.fps
    df = eng.tc.df
    eng.tc.release()

    eng.rc.set_fps_df(fps, df)

    scratch = timecode()
    scratch.set_fps_df(fps, df)

    # Start StateMachines (except 'SM_START')
    startup_complete = False
    for m in range(SM_BLINK, SM_TX_RAW + 1):
        eng.sm[m].active(1)
        sleep(0.005)

    if eng.mode >= MONITOR:
        eng.sm[SM_SYNC].active(1)
        sleep(0.005)
        eng.sm[SM_DECODE].active(1)

    # Fine adjustment of the PIO clocks to compensate for XTAL inaccuracies
    # -1 -> +1 : +ve = faster clock, -ve = slower clock
    eng.micro_adjust(eng.calval)

    # Defines used later in 'lightsleep()'
    if uname().machine[23:] == 'RP2040':
        # RP2040
        CLOCKS_SLEEP_EN0_CLK_SYS_PIO0_BITS = 0x00001000
        CLOCKS_SLEEP_EN0_CLK_SYS_PIO1_BITS = 0x00002000

        CLOCKS_SLEEP_EN1_CLK_SYS_UART0_BITS = 0x00000080
        CLOCKS_SLEEP_EN1_CLK_SYS_UART1_BITS = 0x00000200

        CLOCKS_SLEEP_EN1_CLK_PERI_UART0_BITS = 0x00000040
        CLOCKS_SLEEP_EN1_CLK_PERI_UART1_BITS = 0x00000100

        CLOCKS_SLEEP_EN0 = CLOCKS_SLEEP_EN0_CLK_SYS_PIO1_BITS | CLOCKS_SLEEP_EN0_CLK_SYS_PIO0_BITS
        CLOCKS_SLEEP_EN1 = CLOCKS_SLEEP_EN1_CLK_SYS_UART0_BITS | CLOCKS_SLEEP_EN1_CLK_PERI_UART0_BITS
    else:
        # RP2350 - to be tested...
        CLOCKS_SLEEP_EN0_CLK_SYS_PIO0_BITS = 0x00040000
        CLOCKS_SLEEP_EN0_CLK_SYS_PIO1_BITS = 0x00080000
        CLOCKS_SLEEP_EN0_CLK_SYS_PIO2_BITS = 0x00100000

        CLOCKS_SLEEP_EN1_CLK_SYS_UART0_BITS = 0x00800000
        CLOCKS_SLEEP_EN1_CLK_SYS_UART1_BITS = 0x02000000

        CLOCKS_SLEEP_EN1_CLK_PERI_UART0_BITS = 0x00400000
        CLOCKS_SLEEP_EN1_CLK_PERI_UART1_BITS = 0x01000000

        CLOCKS_SLEEP_EN0 = None
        CLOCKS_SLEEP_EN1 = None


    # Main Loop, service FIFOs and increasing counter
    while not stop():
        # Empty RX FIFOs as they fill
        # wait for both to be available
        while eng.sm[SM_SYNC].rx_fifo() >= 2:
            if eng.sm[SM_START].rx_fifo():
                rx_ticks = eng.sm[SM_START].get()

            p = []
            eng.rc.acquire()
            p.append(eng.sm[SM_SYNC].get())
            p.append(eng.sm[SM_SYNC].get())
            eng.rc.from_ltc_packet(p, False)

            if eng.mode > MONITOR:
                # should perform some basic validation:
                s = scratch.to_raw()
                r = eng.rc.to_raw()
                fail = False

                # check DF flags match
                if ((r & 0x00000080) >> 7) != df:
                    fail = True

                # check packets are counting correctly
                if s!=0:
                    if s!=r:
                        fail = True

                if r!=0:
                    scratch.from_raw(r)
                    scratch.next_frame()
                else:
                    fail = True

                if fail:
                    eng.mode = JAM      # Start process again
                else:
                    eng.mode -= 1

                if eng.mode == MONITOR:
                    # Jam to 'next' RX timecode
                    s = eng.rc.to_raw()
                    eng.tc.from_raw(s)
                    eng.tc.next_frame(2)

                    # clone Userbit Clock flag
                    eng.tc.bgf1 = eng.rc.bgf1

        # Wait for TX FIFO to be empty enough to accept more
        #while eng.mode <= MONITOR and eng.sm[2].tx_fifo() < (7 - send_sync):
        while eng.mode <= MONITOR and eng.sm[SM_BUFFER].tx_fifo() < (7 - send_sync):
            eng.sm[SM_TX_RAW].put(eng.tc.to_raw())

            for w in eng.tc.to_ltc_packet(send_sync, False):
                eng.sm[SM_BUFFER].put(w)
            eng.tc.release()
            send_sync = (send_sync + 1) & 1

            # Calculate next frame value
            eng.tc.next_frame()

            # Does the LED flash for the next frame?
            # 1st LED on for 6 (~10ms) of 20 sub-divisions
            # needs to be '00000_00000_00001_11111->'
            #
            # 2nd LED out is used to trigger MTC quarter packets
            # needs to be '11110_11110_11110_11110->'
            #
            # combined for the 20 sub-divisions, split across 32words
            # '10101010101010101010101010001010'
            # '10100010101010011111111101->'
            eng.tc.acquire()
            if eng.flashframe >= 0:
                if eng.tc.ff == eng.flashframe:
                    eng.sm[SM_BLINK].put((0b10100010101010011111111101 << 6) + 19)
                    eng.sm[SM_BLINK].put((0b10101010101010101010101010001010))
                else:
                    eng.sm[SM_BLINK].put((0b10100010101010001010101000 << 6) + 19)
                    eng.sm[SM_BLINK].put((0b10101010101010101010101010001010))
            else:
                if eng.tc.to_raw() == eng.flashtime:
                    eng.sm[SM_BLINK].put((0b10100010101010011111111101 << 6) + 19)
                    eng.sm[SM_BLINK].put((0b10101010101010101010101010001010))
                else:
                    eng.sm[SM_BLINK].put((0b10100010101010001010101000 << 6) + 19)
                    eng.sm[SM_BLINK].put((0b10101010101010101010101010001010))
            eng.tc.release()

            # Complete start-up sequence
            if not startup_complete:
                # enable 'Start' machine last, so it can synchronise others...
                eng.sm[SM_START].active(1)
                startup_complete = True


        if eng.powersave and eng.sm[SM_BUFFER].tx_fifo() > 5:
            # requires special build microPython with ability to control CLKs
            # lightsleep for longer than a frame is possible, with FIFOs, but
            # may cause IRQs to merged and thus corrupt reporting.

            debug.on()
            try:
                lightsleep(30, eng.ps_en0 | CLOCKS_SLEEP_EN0, eng.ps_en1 | CLOCKS_SLEEP_EN1)
            except:
                eng.set_powersave(False)
            debug.off()

            '''
            # DEMO - automatically exit powersave when TC is 30s
            if (eng.tc.to_raw() & 0x00003F00) == 0x000001E00:
                eng.set_powersave(False)
            '''

    # Stop all StateMachines
    for m in range(len(eng.sm)):
        eng.sm[m].active(0)

    rp2.PIO(0).remove_program()
    rp2.PIO(1).remove_program()

    Pin(25, Pin.OUT, value=0)
    Pin(26, Pin.OUT, value=0)

    eng.set_stopped(True)

    # Ensure timers are cleared
    eng.micro_adjust(eng.calval)

    # Force Garbage collection
    collect()

#-------------------------------------------------------

class MTC(MIDIInterface):
    count = 0

    def send_sysex(self, p):
        # start of SysEx packet
        if len(p) > 3:
            w = self._tx.pend_write()
            if len(w) < 4:
                return False  # TX buffer is full. TODO: block here?

            w[0] = 0x4  # _CIN_SYSEX_START
            w[1] = p[0]
            w[2] = p[1]
            w[3] = p[2]
            self._tx.finish_write(4)
            self._tx_xfer()

            p = p[3:]

        '''
        # add some checks/code for really short packets???
        # _CIN_SYSEX_END_3BYTE
        # _CIN_SYSEX_END_2BYTE
        '''

        # play out til end
        while p:
            if len(p) > 2:
                w = self._tx.pend_write()
                if len(w) < 4:
                    return False  # TX buffer is full. TODO: block here?

                w[0] = 0x7  # _CIN_SYSEX_END_3BYTE
                w[1] = p[0]
                w[2] = p[1]
                w[3] = p[2]
                self._tx.finish_write(4)
                self._tx_xfer()

                p = p[3:]
            elif len(p) > 1:
                w = self._tx.pend_write()
                if len(w) < 4:
                    return False  # TX buffer is full. TODO: block here?

                w[0] = 0x6  # _CIN_SYSEX_END_2BYTE
                w[1] = p[0]
                w[2] = p[1]
                w[3] = 0
                self._tx.finish_write(4)
                self._tx_xfer()

                p = p[2:]
            else:
                w = self._tx.pend_write()
                if len(w) < 4:
                    return False  # TX buffer is full. TODO: block here?

                w[0] = 0x5  # _CIN_SYSEX_END_1BYTE
                w[1] = p[0]
                w[2] = 0
                w[3] = 0
                self._tx.finish_write(4)
                self._tx_xfer()

                p = p[1:]

        return True


    def send_long_mtc(self):
        global tx_raw

        p = bytearray(b"\xF0\x7F\x7F\x01\x01")
        p.append(((tx_raw & 0x1F000000) >> 24) + (0b11 << 5))     # hour + '30fps'
        p.append(( tx_raw & 0x003F0000) >> 16)                    # minutes
        p.append(( tx_raw & 0x00003F00) >> 8)                     # seconds
        p.append(  tx_raw & 0x0000003F)                           # frames
        p.append(0xF7)

        return self.send_sysex(p)


    def send_quarter_mtc(self):
        global tx_raw

        # send directly as time critical
        w = self._tx.pend_write()
        if len(w) < 4:
            return False  # TX buffer is full. TODO: block here?

        # assemble packet
        w[0] = 0x6 # _CIN_SYSEX_END_2BYTE
        w[1] = 0xF1

        # figure the right packet to send
        if not self.count & 0x4:
            if not self.count & 0x2:
                if not self.count & 0x1:
                    w[2] = (tx_raw & 0x0000000F)                              # 0x0_ low frame
                else:
                    w[2] = (((tx_raw & 0x00000010) >> 4) + 0x10)              # 0x1_ high frame
            else:
                if not self.count & 0x1:
                    w[2] = (((tx_raw & 0x00000F00) >> 8) + 0x20)              # 0x2_ low second
                else:
                    w[2] = (((tx_raw & 0x00003000) >> 12) + 0x30)             # 0x3_ high second
        else:
            if not self.count & 0x2:
                if not self.count & 0x1:
                    w[2] = (((tx_raw & 0x000F0000) >> 16) + 0x40)             # 0x4_ low minute
                else:
                    w[2] = (((tx_raw & 0x00300000) >> 20) + 0x50)             # 0x5_ high minute
            else:
                if not self.count & 0x1:
                    w[2] = (((tx_raw & 0x0F000000) >> 24) + 0x60)             # 0x6_ low hour
                else:
                    w[2] = (((tx_raw & 0x10000000) >> 28) + 0x70 + 0b0110)    # 0x7_ high hour + '30fps'

        # finish assembling and send
        w[3] = 0
        self._tx.finish_write(4)
        self._tx_xfer()

        self.count += 1
        return True

#-------------------------------------------------------

def ascii_display_thread(init_mode = RUN):
    global eng, stop
    global tx_raw, rx_ticks
    global irq_callbacks
    global disp, disp_asc
    global mtc

    eng = engine()
    eng.mode = init_mode
    eng.set_stopped(True)

    # alternatively, automatically Jam if booted with 'B' pressed
    keyA = Pin(15,Pin.IN,Pin.PULL_UP)
    keyB = Pin(17,Pin.IN,Pin.PULL_UP)
    if keyB.value() == 0:
        eng.mode = JAM

    # Reduce the CPU clock, for better computation of PIO freqs
    if freq() != 120000000:
        freq(120000000)

    # Allocate appropriate StateMachines, and their pins
    eng.sm = []
    sm_freq = int(eng.tc.fps + 0.1) * 80 * 32

    # Note: we always want the 'sync' SM to be first in the list.
    if eng.mode > MONITOR:
        # We will only start after a trigger pin goes high
        eng.sm.append(rp2.StateMachine(SM_START, start_from_sync, freq=sm_freq,
                           in_base=Pin(21),
                           jmp_pin=Pin(21)))        # RX Decoding
    else:
        eng.sm.append(rp2.StateMachine(SM_START, auto_start, freq=sm_freq,
                           jmp_pin=Pin(21)))        # RX Decoding

    # TX State Machines
    eng.sm.append(rp2.StateMachine(SM_BLINK, shift_led_mtc, freq=sm_freq,
                           jmp_pin=Pin(26),
                           out_base=Pin(25)))       # LED on Pico board + GPIO26/27/28
    eng.sm.append(rp2.StateMachine(SM_BUFFER, buffer_out, freq=sm_freq,
                           out_base=Pin(22)))       # Output of 'raw' bitstream
    eng.sm.append(rp2.StateMachine(SM_ENCODE, encode_dmc, freq=sm_freq,
                           jmp_pin=Pin(22),
                           in_base=Pin(13),         # same as pin as out
                           out_base=Pin(13)))       # Encoded LTC Output

    eng.sm.append(rp2.StateMachine(SM_TX_RAW, tx_raw_value, freq=sm_freq))

    # RX State Machines - note DEMO Mode
    eng.sm.append(rp2.StateMachine(SM_SYNC, sync_and_read, freq=sm_freq,
                           jmp_pin=Pin(19),
                           in_base=Pin(19),
                           out_base=Pin(21),
                           set_base=Pin(21)))       # 'sync' from RX bitstream

    if eng.mode > MONITOR:
        eng.sm.append(rp2.StateMachine(SM_DECODE, decode_dmc, freq=sm_freq,
                           jmp_pin=Pin(18),
                           in_base=Pin(18),
                           set_base=Pin(19)))       # Decoded LTC Input
    else:
        eng.sm.append(rp2.StateMachine(SM_DECODE, decode_dmc, freq=sm_freq,
                           jmp_pin=Pin(13),         # DEMO MODE - read from self/tx
                           in_base=Pin(13),         # for real operation change 13 -> 18
                           set_base=Pin(19)))       # Decoded LTC Input


    '''
    # DEBUG: check the PIO code space/addresses
    for base in [0x50200000, 0x50300000]:
        for offset in [0x0d4, 0x0ec, 0x104, 0x11c]:
            print("0x%8.8x : 0x%2.2x" % (base + offset, mem32[base + offset]))
    '''

    # correct clock dividers
    eng.config_clocks(eng.tc.fps)

    # set up IRQ handler
    for m in eng.sm:
        m.irq(handler=irq_handler, hard=True)

    # set up MTC engine
    mtc = MTC()

    # Remove builtin_driver=True if you don't want the MicroPython serial REPL available.
    usb.device.get().init(mtc, builtin_driver=True)
    sleep(2)

    # Start up threads
    stop = False
    _thread.start_new_thread(pico_timecode_thread, (eng, lambda: stop))

    disp = timecode()
    disp.set_fps_df(eng.tc.fps, eng.tc.df)

    disp_asc = "--:--:--:--"

    # register callbacks, functions to display TX data ASAP
    irq_callbacks[SM_BLINK] = ascii_display_callback

    while True:
        sleep(0.01)

        if eng.mode == HALTED:
            eng.set_powersave(False)
            print("Underflow Error")
            break

        if eng.mode > RUN:
            if eng.mode > MONITOR:
                print("Jamming:", eng.mode)
                sleep(0.01)

            # Async - display RX whenever we notice value has changed
            asc = eng.rc.to_ascii()
            if disp_asc != asc:
                phase = ((4294967295 - rx_ticks + 188) % 640) - 320
                if phase < -32:
                    # RX is ahead/earlier than TX
                    phases = ((" "*10) + ":" + ("+"*int(abs(phase/32))) + (" "*10)) [:21]
                elif phase > 32:
                    # RX is behind/later than TX
                    phases = ((" "*10) + ("-"*int(abs(phase/32))) + ":" + (" "*10)) [-21:]
                else:
                    phases = "          :          "

                print("RX: %s (%4d %21s)" % (asc, phase, phases))
                disp_asc = asc

            # Fall back to 'RUN' mode (outputing TX value) after 'JAM'
            # unless we initially requested 'MONITOR'
            # note: you can force JAM by holding key-B whilst booting
            if eng.mode == MONITOR and init_mode != MONITOR:
                eng.mode = RUN


        '''
        # DEMO - Enter Power-Save every minute, at 10s on TX
        # note: Timecode generator is still running, but
        #       USB coms may be interrupted (you can use UART)
        if eng.mode == RUN:
            if (eng.tc.to_raw() & 0x00003F00) == 0x00000A00:
                irq_callbacks[SM_BLINK] = None
                print("Entering powersave")
                sleep(0.1)

                eng.set_powersave(True)

                while eng.get_powersave():
                    sleep(0.1)

                print("Exited powersave")
                irq_callbacks[SM_BLINK] = ascii_display_callback
        '''

def ascii_display_callback(sm=None):
    global eng
    global tx_raw
    global disp, disp_asc
    global debug
    global mtc

    if sm == SM_BLINK:
        if eng.mode == RUN:
            # Figure out what TX frame to display
            disp.from_raw(tx_raw)
            asc = disp.to_ascii()

            if disp_asc == "--:--:--:--":
                # MTC long packet, first frame only
                if mtc.is_open():
                    mtc.send_long_mtc()           # 'seek' to position

                print("TX: %s" % asc)
            else:
                # MTC quarter packets
                if mtc.is_open():
                    mtc.send_quarter_mtc()

                debug.toggle()

            disp_asc = asc


#---------------------------------------------

if __name__ == "__main__":
    print("Pico-Timecode " + VERSION)
    print("www.github.com/mungewell/pico-timecode")
    print("MTC enabled (will loose USB-UART connection)")
    sleep(2)

    ascii_display_thread()#RUN/MONITOR/JAM)       # Note: DEMO Mode(s) above
