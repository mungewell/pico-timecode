# Pico-Timcode for Raspberry-Pi Pico
# (c) 2023-05-05 Simon Wood <simon@mungewell.org>
#
# https://en.wikipedia.org/wiki/Linear_timecode

import machine
import _thread
import utime
import rp2

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
    out(x, 32)                      # first cycle lenght may be slightly
                                    # different so LED is exactly timed...
    irq(block, 4)                   # Wait for sync'ed start

    label("read_new")
    wrap_target()
    out(y, 32)                      # Read pulse duration from FIFO

    jmp(not_y, "led_off")           # Do we turn LED on?
    set(pins, 1) [1]
    jmp("cont")
    label("led_off")
    nop() [2]                       # this section 4 cycles

    label("cont")
    jmp(y_dec, "still_on")          # Does LED stay on?
    set(pins, 0) [1]
    jmp("cont2")
    label("still_on")
    nop() [2]                       # this section 4 cycles
        
    label("cont2")
    jmp(x_dec, "cont")              # Loop, so it is 80 * 16 =  1280 cycles
                                    # X = 254  -> 1 + 4 + (254 * (4 +1)) + 5 cycles
    out(x, 32) [4]
    wrap()


@rp2.asm_pio(out_init=rp2.PIO.OUT_LOW)
    
def encode_dmc():
    irq(block, 4)                   # Wait for Sync'ed start
    
    wrap_target()
    label("toggle-0")
    mov(pins, invert(pins)) [6]     # Always toogle pin at start of cycle, "0" or "1"

    jmp(pin, "toggle-1")            # Check output of SM-1 buffer, jump if 1

    nop() [6]                       # Wait out rest of cycle, "0"
    jmp("toggle-0")
    
    label("toggle-1")
    mov(pins, invert(pins)) [7]     # Toggle pin to signal '1'
    wrap()


@rp2.asm_pio(out_init=rp2.PIO.OUT_LOW, autopull=True,
             fifo_join=rp2.PIO.JOIN_TX, out_shiftdir=rp2.PIO.SHIFT_RIGHT)

def buffer_out():
    irq(block, 4)                   # Wait for Sync'ed start
    
    label("start")
    out(pins, 1) [14]
    
    jmp(not_osre, "start")
                                    # UNDERFLOW - when Python fails to fill FIFOs
    irq(1)                          # set IRQ-1 to stop other StateMachines
    wrap_target()
    set(pins, 0)
    wrap()


@rp2.asm_pio(set_init=rp2.PIO.OUT_LOW)

def decode_dmc():
    label("initial_high")
    wait(1, pin, 0)
    irq(clear, 5)[10]        # trigger sync engine, and wait til 3/4s mark

    jmp(pin, "high_0")
    label("high_1")
    set(pins, 1)            # Second transition detected (a `1` data symbol)
    jmp("initial_high")
    label("high_0")
    set(pins, 0)            # Line still high, no centre transition (data is `0`)
                            # fall through... a few cycles early
    wrap_target()
    label("initial_low")
    wait(0, pin, 0)
    irq(clear, 5)[10]        # trigger sync engine, and wait til 3/4s mark

    jmp(pin, "low_1")
    label("low_0")
    set(pins, 0)            # Line still low, no centre transition (data is `0`)
    jmp("initial_high")
    label("low_1")
    set(pins, 1)            # Second transition detected (data is `1`)

    wrap()


@rp2.asm_pio(set_init=rp2.PIO.OUT_HIGH, out_init=rp2.PIO.OUT_LOW,
             autopull=True, autopush=True, in_shiftdir=rp2.PIO.SHIFT_RIGHT)

def sync_and_read():
    out(y, 32)              # Read the expected sync word to Y

    wrap_target()
    set(x, 0)

    label("find_sync")
    mov(isr, x)             # force X value back into ISR, clears counter
    irq(block, 5)           # wait for input, databit ready
    set(pins, 1)[3]         # signal 'header section' start

    in_(pins, 1)            # Double clock input (ie duplicate bits)
    in_(pins, 1)            # auto-push will NOT trigger...
    mov(x, isr)

    jmp(x_not_y, "find_sync")

    set(x, 31)[10]          # Read in the next 31 bits
    mov(isr, null)          # clear ISR
    set(pins, 0)            # signal 'data section' start

    label("next_bit")
    in_(pins, 1)
    jmp(x_dec, "next_bit")[14]

    set(x, 31)              # Read in the next 31 bits

    label("next_bit2")
    in_(pins, 1)
    jmp(x_dec, "next_bit2")[13]

    wrap()


#---------------------------------------------

# parity check, count 1's in byte
def bp(b):
    c = 0
    for i in range(8):
        c += (b >> i) & 1

    return(c)


class timecode(object):
    fps = 25
    df = False      # Drop-Frame

    # Timecode - starting value
    hh = 0
    mm = 0
    ss = 0
    ff = 0

    # User bits - format depends on BF2 and BF0
    bgf0 = True     # 4 ASCII characters
    bgf1 = False
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

    def validate_for_drop_frame(self):
        self.acquire()
        if self.df and self.ss == 0 and \
                (self.ff == 0 or self.ff == 1):
            if self.mm % 10 != 0:
                self.ff += (2 - self.ff)
        self.release()

    def from_ascii(self, start=b"00:00:00:00"):
        # Example "00:00:00:00"
        #          hh mm ss ff
        #          01234567890

        # convert ASCII to 'raw' BCD array
        time = [x - 0x30 for x in start]

        self.acquire()
        self.hh = (time[0]*10) + time[1]
        self.mm = (time[3]*10) + time[4]
        self.ss = (time[6]*10) + time[7]
        self.ff = (time[9]*10) + time[10]
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

    def set_fps(self, fps=25, df=False):
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

    def build_packet_bytes(self):
        cf = False
        f27 = False
        f43 = False
        f58 = False
        f59 = False

        self.acquire()
        f58 = self.bgf1
        if self.fps == 25:
            f27 = self.bgf0
            f43 = self.bgf2
        else:
            f43 = self.bgf0
            f59 = self.bgf2

        b = []
        b.append((self.uf1 << 4) + (self.ff % 10))
        b.append((self.uf2 << 4) + (cf  << 3) + (self.df << 2)+ (int(self.ff/10) & 0xF))
        b.append((self.uf3 << 4) + (self.ss % 10))
        b.append((self.uf4 << 4) + (f27 << 3)                 + (int(self.ss/10) & 0xF))
        b.append((self.uf5 << 4) + (self.mm % 10))
        b.append((self.uf6 << 4) + (f43 << 3)                 + (int(self.mm/10) & 0xF))
        b.append((self.uf7 << 4) + (self.hh % 10))
        b.append((self.uf8 << 4) + (f59 << 3) + (f58 << 2)    + (int(self.hh/10) & 0xF))
       
        # polarity correction
        count = 13
        for i in b:
            count += bp(i)

        if count & 1:
            if self.fps == 25:
                b[7] = (self.uf8 << 4) + (True << 3) + (f58 << 2) + (int(self.hh/10) & 0xF)
            else:
                b[4] = (self.uf4 << 4) + (True << 3)              + (int(self.ss/10) & 0xF)
        self.release()
        
        return b

#-------------------------------------------------------

# handler for IRQs 
def handler(flags, sm):    
    if flags & 512:
        # FIFO underflow - Stopping State Machines
        for m in sm:
            m.active(0)
        still_running = False

def pico_timecode_thread(tc, rc, sm):
    sync_sent = False
    start_sent = False
    
    # Loop, increasing frame count each time
    while still_running:

        # Empty RX FIFO as it fills
        if sm[5].rx_fifo() > 0:
            sm[5].get()
            #print("0x%x" % sm[5].get())

        # Wait for TX FIFO to be empty enough
        if sm[2].tx_fifo() < 5:
            # build LTC frame
            b = tc.build_packet_bytes()

            # We want to send 'whole' 32bit words...
            if sync_sent:
                sm[2].put(b[0] + (b[1] << 8) + (b[2] << 16) + (b[3] << 24))
                sm[2].put(b[4] + (b[5] << 8) + (b[6] << 16) + (b[7] << 24))
                sync_sent = False
            else:
                sm[2].put(0xBFFC             + (b[0] << 16) + (b[1] << 24))
                sm[2].put(b[2] + (b[3] << 8) + (b[4] << 16) + (b[5] << 24))
                sm[2].put(b[6] + (b[7] << 8) + (0xBFFC << 16))
                sync_sent = True

            # Does the LED flash for this frame?
            tc.acquire()
            # Send values as 32bit
            if not start_sent:
                sm[1].put(304)          # 1st cycle 'extra sync' correction
            else:
                sm[1].put(253)          # '253' is extact loop length
                
            if tc.ff == 0:
                sm[1].put(210)          # Y - duration of flash
            else:
                sm[1].put(0)
            '''
            # Send values as 16bit
            if not start_sent:
                sm[1].put(0x0000007C)
            else:
                if tc.ff == 0:
                    sm[1].put(0x0010007F)
                    #               ^^^^ X - duration of loop
                    #           ^^^^     Y - duration on flash
                else:
                    sm[1].put(0x0000007D)
            '''
            tc.release()

            # Pre-load 'SYNC' word into RX decoder - only needed once
            if not start_sent:
                # needs to be bit doubled
                # 0xBFFC -> 0xCFFFFFF0
                sm[5].put(3489660912)
                utime.sleep(0.005)

            # We can start StateMachines now that they have some data queued to send
            if not start_sent:
                for m in range(len(sm)-1):
                    sm[m+1].active(1)
                    utime.sleep(0.005)
                    
                # start 'Start' machine last, so others can synchronise
                sm[0].active(1)
                start_sent = True
                
                '''
                # Wait to start handler, as its interfers with statemachine-sync
                utime.sleep(0.01)
                rp2.PIO(0).irq(lambda pio: handler(pio.irq().flags()))
                '''

            # Calculate next frame value
            tc.next_frame()
            
        utime.sleep(0.001)


def ascii_display_thread(tc, rc):
    while still_running:
        print(tc.to_ascii())
        
        utime.sleep(0.1)


#---------------------------------------------

if __name__ == "__main__":
    print("Starting")
    # set up starting values...
    tc = timecode()
    rc = timecode()
    
    tc.acquire()
    fps = tc.fps
    tc.release()
    
    print(tc.to_ascii(), fps, "fps")

    # Allocate appropriate StateMachines, and their pins
    sm = []

    # always want the 'start' SM to be first in the list.
    sm.append(rp2.StateMachine(3, auto_start, freq=80 * fps * 16))
    '''
    sm.append(rp2.StateMachine(3, start_from_pin, freq=80 * fps * 16,
                           jmp_pin=machine.Pin(18)))
    '''

    # TX State Machines
    sm.append(rp2.StateMachine(0, blink_led, freq=80 * fps * 16,
                           set_base=machine.Pin(25)))       # LED on Pico board
    sm.append(rp2.StateMachine(1, buffer_out, freq=80 * fps * 16,
                           out_base=machine.Pin(17)))       # Output of 'raw' bitstream
    sm.append(rp2.StateMachine(2, encode_dmc, freq=80 * fps * 16,
                           jmp_pin=machine.Pin(17),
                           in_base=machine.Pin(19),         # same as Output
                           out_base=machine.Pin(19)))       # Encoded LTC Output

    # RX State Machines
    sm.append(rp2.StateMachine(4, decode_dmc, freq=80 * fps * 16,
                           jmp_pin=machine.Pin(19),
                           in_base=machine.Pin(19),
                           set_base=machine.Pin(16)))       # Decoded LTC Input
    sm.append(rp2.StateMachine(5, sync_and_read, freq=80 * fps * 16,
                           jmp_pin=machine.Pin(16),
                           in_base=machine.Pin(16),
                           out_base=machine.Pin(18),
                           set_base=machine.Pin(18)))       # 'raw' bitstream Input

    # Start up threads
    still_running = True
    _thread.start_new_thread(pico_timecode_thread, (tc, rc, sm))
    ascii_display_thread(tc, rc)

    print("Threads Completed")
