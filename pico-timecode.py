# Pico-Timcode for Raspberry-Pi Pico
# (c) 2023-05-05 Simon Wood <simon@mungewell.org>
#
# https://en.wikipedia.org/wiki/Linear_timecode

import machine
import _thread
import utime
import rp2

@rp2.asm_pio(set_init=rp2.PIO.OUT_LOW, autopull=True, out_shiftdir=rp2.PIO.SHIFT_RIGHT)

def blink_led():
    set(pins, 0)
    out(x, 32)                      # first cycle may be slightly different
                                    # so LED is exactly timed...
    irq(block, 4)               # Wait for sync

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
    jmp(x_dec, "cont")              # Loop, so it is 80 * 8 =  640 cycles
                                    # X = 126  -> 1 + 4 + (126 * (4 +1)) + 5 cycles
    out(x, 32) [4]
    wrap()


@rp2.asm_pio(out_init=rp2.PIO.OUT_LOW)
    
def encode_dmc():
    set(pins, 0)
    irq(block, 4)                   # Wait for Sync
    
    wrap_target()
    label("toggle-0")
    mov(pins, invert(pins)) [2]     # Always toogle pin at start of cycle, "0" or "1"

    jmp(pin, "toggle-1")            # Check output of SM-1 buffer, jump if 1

    nop() [2]                       # Wait out rest of cycle, "0"
    jmp("toggle-0")
    
    label("toggle-1")
    mov(pins, invert(pins)) [3]     # Toggle for signal '1'
    wrap()


@rp2.asm_pio(out_init=rp2.PIO.OUT_LOW, autopull=True,
             fifo_join=rp2.PIO.JOIN_TX, out_shiftdir=rp2.PIO.SHIFT_RIGHT)

def buffer_out():
    irq(block, 4)                   # Wait for Sync
    
    label("start")
    out(pins, 1) [6]
    
    jmp(not_osre, "start")
                                    # UNDERFLOW - when Python fails to fill FIFOs
    irq(1)                          # set IRQ-1 to stop other StateMachines
    wrap_target()
    set(pins, 0)
    wrap()


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

def pico_timecode_thread(tc, sm):
    sync_sent = False
    start_sent = False
    
    # Loop, increasing frame count each time
    while still_running:
        # Wait for FIFO to be empty enough
        if sm[1].tx_fifo() < 5:
            # build LTC frame
            b = tc.build_packet_bytes()

            # We want to send 'whole' 32bit words...
            if sync_sent:
                sm[1].put(b[0] + (b[1] << 8) + (b[2] << 16) + (b[3] << 24))
                sm[1].put(b[4] + (b[5] << 8) + (b[6] << 16) + (b[7] << 24))
                sync_sent = False
            else:
                sm[1].put(0xBFFC             + (b[0] << 16) + (b[1] << 24))
                sm[1].put(b[2] + (b[3] << 8) + (b[4] << 16) + (b[5] << 24))
                sm[1].put(b[6] + (b[7] << 8) + (0xBFFC << 16))
                sync_sent = True

            # Does the LED flash for this frame?
            tc.acquire()
            # Send values as 32bit
            if not start_sent:
                sm[0].put(124)#0x00000060)          # 1st cycle sync correction
            else:
                sm[0].put(125)#0x0000007F)          # '126'
                
            if tc.ff == 0:
                sm[0].put(10)#0x00000010)           # Y - duration on flash
            else:
                sm[0].put(0)#0x00000000)
            '''
            # Send values as 16bit
            if not start_sent:
                sm[0].put(0x0000007C)
            else:
                if tc.ff == 0:
                    sm[0].put(0x0010007F)
                    #               ^^^^ X - duration of loop
                    #           ^^^^     Y - duration on flash
                else:
                    sm[0].put(0x0000007D)
            '''
            tc.release()

            # We can start StateMachines now that they have data queued to send
            if not start_sent:
                for m in sm:
                    m.active(1)
                    utime.sleep(0.001)
                
                '''
                # Wait to start handler, as its interfers with statemachine-sync
                utime.sleep(0.01)
                rp2.PIO(0).irq(lambda pio: handler(pio.irq().flags()))
                '''

            # Calculate next frame value
            tc.next_frame()
            
        utime.sleep(0.01)

def ascii_display_thread(tc):
    while still_running:
        print(tc.to_ascii())
        
        utime.sleep(0.1)
    
#---------------------------------------------

if __name__ == "__main__":
    print("Starting")
    # set up starting values...
    tc = timecode()
    
    tc.acquire()
    fps = tc.fps
    tc.release()
    
    print(tc.to_ascii(), fps, "fps")

    # Allocate appropriate StateMachines, and their pins
    sm = []
    sm.append(rp2.StateMachine(0, blink_led, freq=80 * fps * 8,
                           set_base=machine.Pin(25)))       # LED on Pico board
    sm.append(rp2.StateMachine(1, buffer_out, freq=80 * fps * 8,
                           out_base=machine.Pin(19)))       # Output of 'raw' bitstream
    sm.append(rp2.StateMachine(2, encode_dmc, freq=80 * fps * 8,
                           jmp_pin=machine.Pin(19),
                           in_base=machine.Pin(18),
                           out_base=machine.Pin(18)))       # Encoded LTC Output
    sm.append(rp2.StateMachine(3, auto_start, freq=80 * fps * 8))
    '''
    sm.append(rp2.StateMachine(3, start_from_pin, freq=80 * fps * 8,
                           jmp_pin=machine.Pin(13)))
    '''
 
    # Start up threads
    still_running = True
    _thread.start_new_thread(pico_timecode_thread, (tc, sm))
    ascii_display_thread(tc)

    print("Threads Completed")
