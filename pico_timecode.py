# Pico-Timcode for Raspberry-Pi Pico
# (c) 2023-05-05 Simon Wood <simon@mungewell.org>
#
# https://github.com/mungewell/pico-timecode

import machine
import _thread
import utime
import rp2
import gc

import micropython
micropython.alloc_emergency_exception_buf(100)

from machine import Timer
from micropython import schedule

VERSION="v2.1+"

# set up Globals
eng = None
stop = False

tx_raw = 0
tx_offset = 0
tx_ticks_us = 0
rx_ticks_us = 0
sl_ticks_us = 0

core_dis = [0, 0]

# Constants for run mode
HALTED  = -1
RUN     = 0
MONITOR = 1
JAM     = 64

#---------------------------------------------

@rp2.asm_pio(autopull=True, autopush=True)

def auto_start():
    nop()
    nop()

    irq(clear, 4)                   # immediately Trigger Sync...
    irq(rel(0))                     # set IRQ for sl_ticks_us monitoring

    label("halt")
    out(x, 32)                      # will 'block' waiting for TX timecode
    in_(x, 32)                      # and then write back into FIFO
    jmp("halt") [31]


@rp2.asm_pio(autopull=True, autopush=True)

def start_from_pin():
    label("high")
    jmp(pin, "high")                # Wait for pin to go low first...

    wrap_target()
    jmp(pin, "start")               # Check pin, jump if 1
    wrap()

    label("start")
    irq(clear, 4)                   # Trigger Sync
    irq(rel(0))                     # set IRQ for sl_ticks_us monitoring

    label("halt")
    out(x, 32)                      # will 'block' waiting for TX timecode
    in_(x, 32)                      # and then write back into FIFO
    jmp("halt") [31]


@rp2.asm_pio(set_init=(rp2.PIO.OUT_HIGH,)*2, autopull=True, out_shiftdir=rp2.PIO.SHIFT_RIGHT)

def blink_led():
    out(x, 16)                      # first cycle length may be slightly
                                    # different so LED is exactly timed...
    irq(block, 4)                   # Wait for sync'ed start

    label("read_new")
    wrap_target()
    irq(rel(0))                     # set IRQ for tx_ticks_us monitoring
    out(y, 16)                      # Read pulse duration from FIFO

    jmp(not_y, "led_off")           # Do we turn LED on?
    set(pins, 0b11)
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


#-------------------------------------------------------
# handler for IRQs

def irq_handler(m):
    global eng, stop
    global tx_raw, tx_ticks_us, rx_ticks_us, sl_ticks_us
    global core_dis

    core_dis[machine.mem32[0xd0000000]] = machine.disable_irq()
    ticks = utime.ticks_us()

    if m==eng.sm[0]:
        sl_ticks_us = ticks

    if m==eng.sm[1]:
        if eng.sm[0].rx_fifo() > 0:
            tx_raw = eng.sm[0].get()

        tx_ticks_us = ticks

    if m==eng.sm[5]:
        rx_ticks_us = ticks

    if m==eng.sm[2]:
        # Buffer Underflow
        stop = 1
        eng.mode = HALTED

    machine.enable_irq(core_dis[machine.mem32[0xd0000000]])


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

        eng.timer1 = None

    if timer == eng.timer2:
        if eng.timer1:
            # timer1 should completed first
            print("!!!")
            eng.timer1.deinit()
            eng.timer1 = None

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

    def next_frame(self):
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

    def prev_frame(self):
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

        if self.ff >= int(self.fps):
            #self.fps = self.ff          # Can we infer fps, which might be float??
            if acquire:
                self.release()
            return False

        self.release()
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
        # divider computed for CPU clock at 120MHz
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
                machine.mem32[base + offset] = new_div
        '''
        machine.mem32[0x502000c8] = new_div
        machine.mem32[0x502000e0] = new_div
        machine.mem32[0x502000f8] = new_div
        machine.mem32[0x50200110] = new_div
        machine.mem32[0x503000c8] = new_div
        machine.mem32[0x503000e0] = new_div
        machine.mem32[0x503000f8] = new_div
        machine.mem32[0x50300110] = new_div

        self.dlock.release()

    def inc_divider(self):
        # increasing divider -> slower clock
        self.dlock.acquire()
        new_div = machine.mem32[0x502000c8] + 0x0100

        # Set dividers for all PIO machines
        '''
        for base in [0x50200000, 0x50300000]:
            for offset in [0x0c8, 0x0e0, 0x0f8, 0x110]:
                machine.mem32[base + offset] = (integer << 16) + (fraction << 8)
        '''
        machine.mem32[0x502000c8] = new_div
        machine.mem32[0x502000e0] = new_div
        machine.mem32[0x502000f8] = new_div
        machine.mem32[0x50200110] = new_div
        machine.mem32[0x503000c8] = new_div
        machine.mem32[0x503000e0] = new_div
        machine.mem32[0x503000f8] = new_div
        machine.mem32[0x50300110] = new_div

        self.dlock.release()

    def dec_divider(self):
        # decreasing divider -> faster clock
        self.dlock.acquire()
        new_div = machine.mem32[0x502000c8] - 0x0100

        # Set dividers for all PIO machines
        '''
        for base in [0x50200000, 0x50300000]:
            for offset in [0x0c8, 0x0e0, 0x0f8, 0x110]:
                machine.mem32[base + offset] = (integer << 16) + (fraction << 8)
        '''
        machine.mem32[0x502000c8] = new_div
        machine.mem32[0x502000e0] = new_div
        machine.mem32[0x502000f8] = new_div
        machine.mem32[0x50200110] = new_div
        machine.mem32[0x503000c8] = new_div
        machine.mem32[0x503000e0] = new_div
        machine.mem32[0x503000f8] = new_div
        machine.mem32[0x50300110] = new_div

        self.dlock.release()

    def micro_adjust(self, calval, period=0):
        if self.stopped:
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
                self.timer1 = Timer()
                self.timer1.init(period=self.period - part, mode=Timer.ONE_SHOT, callback=timer_sched)


    def set_flashtime(self, ft):
        self.dlock.acquire()
        self.flashtime = (ft.df << 7) + (ft.hh << 24) + (ft.mm << 16) + (ft.ss << 8) + ft.ff
        self.dlock.release()

#-------------------------------------------------------

def pico_timecode_thread(eng, stop):
    global tx_raw

    debug = machine.Pin(28,machine.Pin.OUT)
    debug.off()

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
    eng.micro_adjust(eng.calval)

    # Loop, increasing frame count each time
    while not stop():
        # Empty RX FIFO as it fills
        if eng.sm[5].rx_fifo() >= 2:
            p = []
            eng.rc.acquire()
            p.append(eng.sm[5].get())
            p.append(eng.sm[5].get())
            if len(p) == 2:
                eng.rc.from_ltc_packet(p, False)

            if eng.mode > MONITOR:
                # should perform some basic validation:
                g = gc.to_raw()
                r = eng.rc.to_raw()
                fail = False

                # check DF flags match
                if ((r & 0x00000080) >> 7) != df:
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
                    eng.mode = JAM      # Start process again
                else:
                    eng.mode -= 1

                if eng.mode == MONITOR:
                    # Jam to 'next' RX timecode
                    g = eng.rc.to_raw()
                    eng.tc.from_raw(g)
                    eng.tc.next_frame()
                    eng.tc.next_frame()

                    # clone Userbit Clock flag
                    eng.tc.bgf1 = eng.rc.bgf1

        # Wait for TX FIFO to be empty enough to accept more
        while eng.mode <= MONITOR and eng.sm[2].tx_fifo() < (7 - send_sync):
            if eng.sm[0].tx_fifo() < 3:
                eng.sm[0].put(eng.tc.to_raw())
            for w in eng.tc.to_ltc_packet(send_sync, False):
                eng.sm[2].put(w)
            eng.tc.release()
            send_sync = (send_sync + 1) & 1

            # Calculate next frame value
            eng.tc.next_frame()

            # Does the LED flash for the next frame?
            eng.tc.acquire()
            if eng.flashframe >= 0:
                if eng.tc.ff == eng.flashframe:
                    eng.sm[1].put((210 << 16)+ 509) # '209' duration of flash
                else:
                    eng.sm[1].put(509)              # '509' is complete cycle length
            else:
                if eng.tc.to_raw() == eng.flashtime:
                    eng.sm[1].put((210 << 16)+ 509) # '209' duration of flash
                else:
                    eng.sm[1].put(509)              # '509' is complete cycle length
            eng.tc.release()

            if not start_sent:
                # enable 'Start' machine last, so others can synchronise to it
                eng.sm[0].active(1)
                start_sent = True

        if eng.powersave and eng.sm[2].tx_fifo() > 5:
            # requires special build microPython with ability to control CLKs
            # lightsleep for longer than a frame is possible, with FIFOs, but
            # may cause IRQs to merged and thus corrupt reporting.

            # RP2040
            CLOCKS_SLEEP_EN0_CLK_SYS_PIO0_BITS = 0x00001000
            CLOCKS_SLEEP_EN0_CLK_SYS_PIO1_BITS = 0x00002000

            CLOCKS_SLEEP_EN1_CLK_SYS_UART0_BITS = 0x00000080
            CLOCKS_SLEEP_EN1_CLK_SYS_UART1_BITS = 0x00000200

            CLOCKS_SLEEP_EN1_CLK_PERI_UART0_BITS = 0x00000040
            CLOCKS_SLEEP_EN1_CLK_PERI_UART1_BITS = 0x00000100

            '''
            # RP2350
            CLOCKS_SLEEP_EN0_CLK_SYS_PIO0_BITS = 0x00040000
            CLOCKS_SLEEP_EN0_CLK_SYS_PIO1_BITS = 0x00080000
            CLOCKS_SLEEP_EN0_CLK_SYS_PIO2_BITS = 0x00100000
            '''

            debug.on()
            try:
                machine.lightsleep(30, eng.ps_en0 | CLOCKS_SLEEP_EN0_CLK_SYS_PIO1_BITS | CLOCKS_SLEEP_EN0_CLK_SYS_PIO0_BITS,
                                   eng.ps_en1 | CLOCKS_SLEEP_EN1_CLK_SYS_UART0_BITS | CLOCKS_SLEEP_EN1_CLK_PERI_UART0_BITS)
            except:
                eng.set_powersave(False)
            debug.off()

            '''
            # DEMO - automatically exit when TC is 30s
            if (eng.tc.to_raw() & 0x00003F00) == 0x000001E00:
                eng.set_powersave(False)
            '''

    # Stop the StateMachines
    for m in range(len(eng.sm)):
        eng.sm[m].active(0)

    rp2.PIO(0).remove_program()
    rp2.PIO(1).remove_program()

    machine.Pin(25, machine.Pin.OUT, value=0)
    machine.Pin(26, machine.Pin.OUT, value=0)

    eng.set_stopped(True)

#-------------------------------------------------------

def ascii_display_thread(mode = RUN):
    global eng, stop

    eng = engine()
    eng.mode = mode
    eng.set_stopped(True)

    # alternatively, automatically Jam if booted with 'B' pressed
    keyB = machine.Pin(17,machine.Pin.IN,machine.Pin.PULL_UP)
    if keyB.value() == 0:
        eng.mode = JAM

    # Reduce the CPU clock, for better computation of PIO freqs
    if machine.freq() != 120000000:
        machine.freq(120000000)

    # Allocate appropriate StateMachines, and their pins
    eng.sm = []
    sm_freq = int(eng.tc.fps * 80 * 32)

    # Note: we always want the 'sync' SM to be first in the list.
    if eng.mode > MONITOR:
        # We will only start after a trigger pin goes high
        eng.sm.append(rp2.StateMachine(0, start_from_pin, freq=sm_freq,
                           jmp_pin=machine.Pin(21)))        # RX Decoding
    else:
        eng.sm.append(rp2.StateMachine(0, auto_start, freq=sm_freq))

    # TX State Machines
    eng.sm.append(rp2.StateMachine(1, blink_led, freq=sm_freq,
                           set_base=machine.Pin(25)))       # LED on Pico board + GPIO26/27/28
    eng.sm.append(rp2.StateMachine(2, buffer_out, freq=sm_freq,
                           out_base=machine.Pin(22)))       # Output of 'raw' bitstream
    eng.sm.append(rp2.StateMachine(3, encode_dmc, freq=sm_freq,
                           jmp_pin=machine.Pin(22),
                           in_base=machine.Pin(13),         # same as pin as out
                           out_base=machine.Pin(13)))       # Encoded LTC Output

    # RX State Machines - note DEMO Mode
    if eng.mode > MONITOR:
        eng.sm.append(rp2.StateMachine(4, decode_dmc, freq=sm_freq,
                           jmp_pin=machine.Pin(18),
                           in_base=machine.Pin(18),
                           set_base=machine.Pin(19)))       # Decoded LTC Input
    else:
        eng.sm.append(rp2.StateMachine(4, decode_dmc, freq=sm_freq,
                           jmp_pin=machine.Pin(13),         # DEMO MODE - read from self/tx
                           in_base=machine.Pin(13),         # for real operation change 13 -> 18
                           set_base=machine.Pin(19)))       # Decoded LTC Input

    eng.sm.append(rp2.StateMachine(5, sync_and_read, freq=sm_freq,
                           jmp_pin=machine.Pin(19),
                           in_base=machine.Pin(19),
                           out_base=machine.Pin(21),
                           set_base=machine.Pin(21)))       # 'sync' from RX bitstream

    # correct clock dividers for 29.98 and 23.976
    eng.config_clocks(eng.tc.fps)

    # set up IRQ handler
    for m in eng.sm:
        m.irq(handler=irq_handler, hard=True)

    # Start up threads
    stop = False
    _thread.start_new_thread(pico_timecode_thread, (eng, lambda: stop))

    disp = timecode()
    disp.set_fps_df(eng.tc.fps, eng.tc.df)
    cycle_us = (1000000.0 / disp.fps)

    disp_asc="--:--:--:--"
    disp_ticks = 0
    disp_loop = 0

    while True:
        if eng.mode > RUN:
            if eng.mode > MONITOR:
                print("Jamming:", eng.mode)
            else:
                # Fall back to RUN if we previously initiated JAM
                if mode == JAM:
                    eng.mode = RUN

            print("RX:", eng.rc.to_ascii())
            utime.sleep(0.1)

        if eng.mode == RUN:
            t1 = tx_ticks_us
            if disp_ticks == t1:
                # 5ms before next expected frame arrives we will stall
                # intently looking for the moment it happens...
                d = cycle_us - utime.ticks_diff(utime.ticks_us(), t1)
                if d > -1000 and d < 5000:
                    while d > -1000:
                        d = cycle_us - utime.ticks_diff(utime.ticks_us(), t1)
                        if disp_ticks != tx_ticks_us:
                            break
                else:
                    if disp_loop == 0:
                        # Force garbage collection at a time that's not busy
                        gc.collect()
                    disp_loop += 1
                    utime.sleep(0.001)
                    continue

            # Figure out what TX frame to display
            while True:
                t1 = tx_ticks_us
                raw = tx_raw
                t2 = tx_ticks_us

                if t1==t2:
                    disp.from_raw(raw)
                    break

            asc = disp.to_ascii()
            if disp_asc != asc:
                print(asc)
                disp_asc = asc
                disp_ticks = t1
                disp_loop = 0

            '''
            # DEMO - Enable Power-Save every minute, at 10s on TC
            if (eng.tc.to_raw() & 0x00003F00) == 0x00000A00:
                print("Entering powersave")
                utime.sleep(0.1)

                eng.set_powersave(True)

                while eng.get_powersave():
                    utime.sleep(0.1)

                print("Exited powersave")
            '''

        if eng.mode == HALTED:
            eng.set_powersave(False)
            print("Underflow Error")
            break

#---------------------------------------------

if __name__ == "__main__":
    print("Pico-Timecode " + VERSION)
    print("www.github.com/mungewell/pico-timecode")
    utime.sleep(2)

    ascii_display_thread()#RUN/MONITOR/JAM)       # Note: DEMO Mode(s) above
