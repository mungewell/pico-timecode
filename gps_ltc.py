##### DMA EXAMPLE #####

import pico_timecode as pt
import _thread

from machine import Pin, mem32, mem16, freq, UART, ADC
from machine import Timer, disable_irq, enable_irq
from array import array
from utime import sleep
from os import uname
import rp2


##### CONFIGURATION DATA #####

GPIO_TRIGGER = 6                # 1PPS/Trigger input pin
GPIO_TOGGLE = 7                 # Alternates every trigger

# if using button for trigger, set pull-up resistor
#keyA = Pin(12,Pin.IN,Pin.PULL_UP)

# setup GPS to output message(s)
#uart = UART(0, baudrate=115200, tx=Pin(0), rx=Pin(1), timeout=50)
uart = UART(1, baudrate=115200, tx=Pin(4), rx=Pin(5), timeout=50)
uart.init(115200, bits=8, parity=None, stop=1)

print(uname().machine)
if uname().machine[-6:] == 'RP2350':
    TIMERBASE = 0x400b0000      # Timer0
    #TIMERBASE = 0x400b8000      # Timer1
else: # rp2040
    TIMERBASE = 0x40054000

##### PIO PROGRAMS #####

@rp2.asm_pio(out_init=rp2.PIO.OUT_LOW)
def pio_trigger_from_gpio():
    wrap_target()

    label("wait_for_high")
    jmp(pin, "pin_is_high")
    wrap()

    label("pin_is_high")
    push(noblock)
    mov(pins, invert(pins))     # Toggle output pin to signal trigger has occured

    label("wait_for_low")
    jmp(pin, "wait_for_low")
    jmp("wait_for_high")

@rp2.asm_pio()
def pio_relay():
    wrap_target()

    pull(block)[31]             # Pull 32-bit from TX-FIFO to ISR
    mov(isr, osr)[31]           # copy ISR to OSR
    push(block)[31]             # Push 32-bit from OSR to RX-FIFO
    
    wrap()

#---------------------------------------------
# Class(es) for making main code more elegant/readable

def inspect_sm_id(sm_object):
    # This might vary depending on the MicroPython version and memory layout
    baddr = bytes(array('O', [sm_object]))
    addr = int.from_bytes(baddr, 'little')
    # The offset (e.g., +10) is specific to certain MicroPython builds
    sm_id = mem16[addr + 10]
    return sm_id

class DMA_PIO_Trigger():
    def __init__(self, state_machine=None, enable=True, trigger=False):
        self.dma = rp2.DMA()
        self.ctrl = None

        self.pack_ctrl(state_machine, enable)

    def pack_ctrl(self, treq_state_machine, enable=True, chain_to=0):
        sm_id = inspect_sm_id(treq_state_machine)
        if sm_id > 7:
            treq = 20 + sm_id - 8           # DREQ_PIO2_RX0 + offset from statemachine 'id'
        if sm_id > 3:
            treq = 12 + sm_id - 4           # DREQ_PIO1_RX0 + offset from statemachine 'id'
        else:
            treq = 4 + sm_id                # DREQ_PIO0_RX1 + offset from statemachine 'id'

        self.ctrl = self.dma.pack_ctrl(
            treq_sel = treq,
            inc_read = False,
            inc_write = False,
            high_pri = True,
            chain_to = chain_to,
            enable = enable)

    def chain_to(self, channel):
        ctrl_dict = self.dma.unpack_ctrl(self.ctrl)
        ctrl_dict['chain_to'] = channel
        self.ctrl = self.dma.pack_ctrl(**ctrl_dict)

    def enable(self, enable=True):
        ctrl_dict = self.dma.unpack_ctrl(self.ctrl)
        ctrl_dict['enable'] = enable
        self.ctrl = self.dma.pack_ctrl(**ctrl_dict)

    def config(self, target_state_machine, trigger=False):
        self.dma.config(
            read = target_state_machine,
            write = target_state_machine,
            count = 1,
            ctrl = self.ctrl,
            trigger = trigger)

class DMA_PIO_Timestamp(DMA_PIO_Trigger):
    def pack_ctrl(self, treq_state_machine=None, enable=True, chain_to=0):
        if treq_state_machine:
            sm_id = inspect_sm_id(treq_state_machine)
            if sm_id > 7:
                treq = 16 + sm_id - 8   # DREQ_PIO2_TX0 + offset from statemachine 'id'
            if sm_id > 3:
                treq = 8 + sm_id - 4    # DREQ_PIO1_TX0 + offset from statemachine 'id'
            else:
                treq = 0 + sm_id        # DREQ_PIO0_TX0 + offset from statemachine 'id'
        else:
            treq = 0x3f                 # 'as-fast-as-possible'

        self.ctrl = self.dma.pack_ctrl(
            treq_sel = treq,
            inc_write = False,
            inc_read = False,
            high_pri = True,
            chain_to = chain_to,
            enable = enable)

    def config(self, target_state_machine, trigger=False):
        self.dma.config(
            read = TIMERBASE + 0x28,    # Address of TIMERAWL Register (u-sec counter)
            write = target_state_machine,
            count = 1,
            ctrl = self.ctrl,
            trigger = trigger)

class DMA_PIO_Timestamp64(DMA_PIO_Trigger):
    def pack_ctrl(self, treq_state_machine=None, enable=True, chain_to=0):
        if treq_state_machine:
            sm_id = inspect_sm_id(treq_state_machine)
            if sm_id > 7:
                treq = 16 + sm_id - 8   # DREQ_PIO2_TX0 + offset from statemachine 'id'
            if sm_id > 3:
                treq = 8 + sm_id - 4    # DREQ_PIO1_TX0 + offset from statemachine 'id'
            else:
                treq = 0 + sm_id        # DREQ_PIO0_TX0 + offset from statemachine 'id'
        else:
            treq = 0x3f                 # 'as-fast-as-possible'

        self.ctrl = self.dma.pack_ctrl(
            treq_sel = treq,
            inc_write = False,
            inc_read = True,            # Using ring size, so we loop back to read TIMEHR 
            ring_size = 4,              # add 3..0
            ring_sel = False,           # ring affects the read address
            high_pri = True,
            chain_to = chain_to,
            enable = enable)

    def config(self, target_state_machine, trigger=False):
        self.dma.config(
            read = TIMERBASE + 0x0c,    # Address of TIMELR Register (u-sec counter)
                                        # this also latches TIMEHR, which will be our 4th read
            write = target_state_machine,
            count = 4,
            ctrl = self.ctrl,
            trigger = trigger)

#-------------------------------------------------------

def gps_ltc_thread(eng, stop):
    global tx_raw, rx_ticks
    global quarters
    global debug

    debug = Pin(28,Pin.OUT)
    debug.off()

    eng.set_stopped(False)
    pt.quarters = 0
    
    send_sync = True        # send 1st packet with sync header

    # Set up Blink/LED timing
    # 1st LED on for 10 (~16ms) of 20 sub-divisions
    # plus 4 sub-divions of 'extra sync'
    # needs to be '00000_00000_11111_11111__1111->'
    #
    # 2nd LED out is used to trigger MTC quarter packets
    # plus 4 sub-divions of 'extra sync'
    # needs to be '11110_11110_11110_11110__1111->'
    #
    # combined for the 24 sub-divisions, split across 32words
    # '10101001111111110111111101->'
    # '10101010101010101000101010100010'

    BLINK_LED = 0b01010101010101010101 << 6         # ~16ms flash

    if pt._hasUsbDevice:
        # 4x IRQs per frame, long first frame
        eng.sm[pt.SM_BLINK].put((0b101010001010101000_11111111 << 6) + 23)
        eng.sm[pt.SM_BLINK].put( 0b101010101010_10101000101010100010)

        # normally...
        BLINK_IRQ1 = (0b10100010101010001010101000 << 6) + 19
        BLINK_IRQ2 =  0b10101010101010101010_101010001010
    else:
        # 1x IRQs per frame, long first frame
        eng.sm[pt.SM_BLINK].put((0b101010101010101000_11111111 << 6) + 23)
        eng.sm[pt.SM_BLINK].put( 0b101010101010_10101010101010101010)

        # normally...
        BLINK_IRQ1 = (0b10101010101010101010101000 << 6) + 19
        BLINK_IRQ2 =  0b10101010101010101010_101010101010

    # Ensure Timecodes are using same fps/df settings
    eng.tc.acquire()
    fps = eng.tc.fps
    df = eng.tc.df
    eng.tc.release()

    eng.rc.set_fps_df(fps, df)

    scratch = pt.timecode()
    scratch.set_fps_df(fps, df)

    # Start StateMachines
    for m in eng.sm:
        m.active(1)

    # Fine adjustment of the PIO clocks to compensate for XTAL inaccuracies
    # -1 -> +1 : +ve = faster clock, -ve = slower clock
    eng.micro_adjust(eng.calval)

    # Main Loop, service FIFOs and increasing counter
    while True: #not stop():
        # Wait for TX FIFO to be empty enough to accept next packet
        while eng.sm[pt.SM_BUFFER].tx_fifo() < (6 - send_sync):
            #eng.sm[SM_TX_RAW].put(eng.tc.to_raw())      # 1 word into FIFO

            for w in eng.tc.to_ltc_packet(send_sync, False):
                eng.sm[pt.SM_BUFFER].put(w)                # 2 or 3 words into FIFO
            eng.tc.release()
            send_sync = not send_sync

            # Calculate next frame value
            eng.tc.next_frame()

            # Does the LED flash for the next frame?
            if eng.flashframe >= 0:
                if eng.tc.ff == eng.flashframe:
                    eng.sm[pt.SM_BLINK].put(BLINK_IRQ1 | BLINK_LED)
                    eng.sm[pt.SM_BLINK].put(BLINK_IRQ2)    # 2 words into FIFO
                else:
                    eng.sm[pt.SM_BLINK].put(BLINK_IRQ1)
                    eng.sm[pt.SM_BLINK].put(BLINK_IRQ2)
            else:
                if eng.tc.to_raw() == eng.flashtime:
                    eng.sm[pt.SM_BLINK].put(BLINK_IRQ1 | BLINK_LED)
                    eng.sm[pt.SM_BLINK].put(BLINK_IRQ2)
                else:
                    eng.sm[pt.SM_BLINK].put(BLINK_IRQ1)
                    eng.sm[pt.SM_BLINK].put(BLINK_IRQ2)

            '''
            # Complete start-up sequence
            if not startup_complete:
                # enable 'Start' machine last, so it can synchronise others...
                eng.sm[SM_START].active(1)
                startup_complete = True
            '''



    # Stop all StateMachines, disable IRQs and empty RX FIFOs
    for m in eng.sm:
        m.active(0)
        m.irq(handler=None)
        while m.rx_fifo():
            m.get()

    # Bug 18646 workaround - specify StateMachines implicitly by name
    rp2.PIO(0).remove_program(auto_start)
    rp2.PIO(0).remove_program(start_from_sync)

    '''
    rp2.PIO(0).remove_program()
    rp2.PIO(1).remove_program()

    # 'Purge' statemachines to ensure TX FIFOs are empty
    for i in range(8):
        sm = rp2.StateMachine(i, tx_fifo_purge, freq=1_000_000)
        sm.active(1)
        sleep(0.01)
        sm.active(0)

    rp2.PIO(0).remove_program()
    rp2.PIO(1).remove_program()
    '''

    eng.set_stopped(True)

    # Ensure timers are cleared
    eng.micro_adjust(eng.calval)

    # Force Garbage collection
    #print("Available memory (bytes):", mem_free())
    collect()
    #print(mem_info())

#-------------------------------------------------------

class gps_ltc_engine(pt.engine):
    def write_divider(self, new_div):
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
        '''
        # do not affect the 2nd PIO Block
        mem32[0x503000c8] = new_div
        mem32[0x503000e0] = new_div
        mem32[0x503000f8] = new_div
        mem32[0x50300110] = new_div
        '''

        self.dlock.release()

#---------------------------------------------

if __name__ == "__main__":
    # Set up the state machines to run at CPU speed
    freq(180_000_000)
    sm = []

    pt.eng = pt.engine()
    pt.eng.mode = pt.RUN
    pt.eng.set_stopped(True)

    # Set the CPU clock, for better computation of PIO freqs
    if machine.freq() != 180000000:
        machine.freq(180000000)

    pt.eng.sm = []
    sm_freq = int(pt.eng.tc.fps + 0.1) * 80 * 32

    # Startup State Machine
    pt.eng.sm.append(rp2.StateMachine(pt.SM_START, pt.start_from_sync, freq=sm_freq,
                               in_base=Pin(GPIO_TRIGGER),
                               jmp_pin=Pin(GPIO_TRIGGER)))  # GPS 1PPS

    # TX State Machines
    if pt._hasUsbDevice:
        pt.eng.sm.append(rp2.StateMachine(pt.SM_BLINK, pt.shift_led_irq_4x, freq=sm_freq,
                               jmp_pin=Pin(3),          # Qtr_Clk on GPIO3
                               out_base=Pin(2)))        # LED on GPIO2
    else:
        pt.eng.sm.append(rp2.StateMachine(pt.SM_BLINK, pt.shift_led_irq_1x, freq=sm_freq,
                               jmp_pin=Pin(3),          # Qtr_Clk on GPIO3
                               out_base=Pin(2)))        # LED on GPIO2

    pt.eng.sm.append(rp2.StateMachine(pt.SM_BUFFER, pt.buffer_out, freq=sm_freq,
                               out_base=Pin(22)))       # Output of 'raw' bitstream

    pt.eng.sm.append(rp2.StateMachine(pt.SM_ENCODE, pt.encode_dmc2, freq=sm_freq,
                               jmp_pin=Pin(22),
                               in_base=Pin(9),         # same as pin as out
                               out_base=Pin(9)))       # Encoded LTC Output

    #pt.eng.sm.append(rp2.StateMachine(pt.SM_TX_RAW, pt.tx_raw_value, freq=sm_freq))

    # correct clock dividers
    pt.eng.config_clocks(pt.eng.tc.fps)

    # no interrupt for now...
    '''
    # set up IRQ handler
    for m in pt.eng.sm:
        #m.irq(handler=pt.irq_handler, hard=True)
        m.irq(handler=gps_ltc_irq_handler, hard=True)
    '''

    if pt._hasUsbDevice:
        # set up MTC engine
        pt.mtc = pt.MTC()
        pt.mtc.init()

    pt.stop = False
    #_thread.start_new_thread(pt.pico_timecode_thread, (pt.eng, lambda: pt.stop))
    _thread.start_new_thread(gps_ltc_thread, (pt.eng, lambda: pt.stop))

    # timestamp PIOs allocated to 2nd PIO Block, run at CPU speed
    sm.append(rp2.StateMachine(4, pio_trigger_from_gpio, freq=-1,
                               out_base=Pin(GPIO_TOGGLE),
                               in_base=Pin(GPIO_TOGGLE),
                               jmp_pin=Pin(GPIO_TRIGGER)))
    sm.append(rp2.StateMachine(5, pio_relay, freq=-1))

    sm.append(rp2.StateMachine(6, pio_trigger_from_gpio, freq=-1,
                               out_base=Pin(5),
                               in_base=Pin(5),
                               jmp_pin=Pin(2)))
    sm.append(rp2.StateMachine(7, pio_relay, freq=-1))

    for m in sm:
        m.active(1)

    # Allocate DMA channel(s)
    dma_trigger_a = DMA_PIO_Trigger(sm[0])
    dma_timestamp_a = DMA_PIO_Timestamp64(sm[1])

    dma_trigger_b = DMA_PIO_Trigger(sm[2])
    dma_timestamp_b = DMA_PIO_Timestamp(sm[3])

    dma_trigger_a.chain_to(dma_timestamp_a.dma.channel)
    dma_timestamp_a.chain_to(dma_trigger_a.dma.channel)
    dma_timestamp_a.config(sm[1])

    dma_trigger_b.chain_to(dma_timestamp_b.dma.channel)
    dma_timestamp_b.chain_to(dma_trigger_b.dma.channel)
    dma_timestamp_b.config(sm[3])

    dma_trigger_a.config(sm[0], True)
    dma_trigger_b.config(sm[2], True)

    while True:
        while sm[1].rx_fifo() > 3:
            # read out 4x 32bits, first and last are relevant.
            a = []
            while sm[1].rx_fifo():
                a.append(sm[1].get())
                #print("A 0x%8.8x" % a[-1])
            
            print("Timestamp-GPS: 0x%16.16x %d" % ((a[3]<<32)|a[0], (a[3]<<32)|a[0]))

        while sm[3].rx_fifo():
            # read out 4x 32bits, first and last are relevant.
            b = sm[3].get()
            
            print("Timestamp-LTC:         0x%8.8x %d" % (b, b))
