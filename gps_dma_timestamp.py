##### DMA EXAMPLE #####

from machine import Pin, mem32, mem16, freq, UART, ADC
from utime import sleep
from array import array
from os import uname
import rp2

##### CONFIGURATION DATA #####

TIMESTAMP64 = True

GPIO_TRIGGER = 6                # 1PPS/Trigger input pin
GPIO_TOGGLE = 7                 # Alternates every trigger

# if using button for trigger, remember set pull-up resistor
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

@rp2.asm_pio()
def pio_relay():
    wrap_target()

    pull(block)                 # Pull 32-bit from TX-FIFO to ISR
    mov(isr, osr)               # copy ISR to OSR
    push(block)                 # Push 32-bit from OSR to RX-FIFO
    
    wrap()

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

#---------------------------------------------
# Class for performing rolling averages

class Rolling:
    def __init__(self, size=5):
        self.max = size
        self.data = []
        for i in range(size):
            self.data.append([0.0, 0])

        self.dsum = 0.0

        self.enter = 0
        self.exit = 0
        self.size = 0

    def store(self, data, mark=0):
        if self.size == self.max:
            self.dsum -= self.data[self.exit][0]
            self.exit = (self.exit + 1) % self.max

        self.data[self.enter][0] = data
        self.data[self.enter][1] = mark
        self.dsum += data

        self.enter = (self.enter + 1) % self.max
        if self.size < self.max:
            self.size += 1

    def read(self):
        if self.size > 0:
            return(self.dsum/self.size)

    def store_read(self, data, mark=0):
        self.store(data, mark)
        return(self.read())

    def purge(self, mark):
        while self.size and self.data[self.exit][1] < mark:
            self.dsum -= self.data[self.exit][0]
            self.data[self.exit][0] = None
            self.exit = (self.exit + 1) % self.max
            self.size -= 1

#---------------------------------------------
# Class for using the internal temp sensor

class Temperature:
    def __init__(self, ref=3.3):
        self.ref = ref
        self.sensor = ADC(4)

    def read(self):
        adc_value = self.sensor.read_u16()
        volt = (self.ref/65536) * adc_value

        return(27-(volt-0.706)/0.001721)

#--------------------------------------------- 
# Class for measuring VSYS voltage
 
class Battery: 
    def __init__(self, ref=3.3 * 3): 
        self.ref = ref 
        self.sensor = ADC(29) 
 
    def read(self): 
        adc_value = self.sensor.read_u16() 
        return((self.ref/65536) * adc_value)
 
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

#---------------------------------------------

##### Initialization #####

toggle = Pin(GPIO_TOGGLE,Pin.IN)

volts = Battery()
temp = Temperature()
temps = Rolling(30)

# Set up the state machines to run at CPU clock rate, as fast as possible
sm = []
sm.append(rp2.StateMachine(0, pio_trigger_from_gpio, freq=-1,
                           out_base=Pin(GPIO_TOGGLE),
                           in_base=Pin(GPIO_TOGGLE),
                           jmp_pin=Pin(GPIO_TRIGGER)))
sm.append(rp2.StateMachine(1, pio_relay, freq=-1))

for m in sm:
    m.active(1)

# Allocate DMA channel(s)
dma_trigger = DMA_PIO_Trigger(sm[0])
if TIMESTAMP64:
    dma_timestamp = DMA_PIO_Timestamp64(sm[1])
else:
    dma_timestamp = DMA_PIO_Timestamp(sm[1])

dma_trigger.chain_to(dma_timestamp.dma.channel)
dma_timestamp.chain_to(dma_trigger.dma.channel)
dma_timestamp.config(sm[1])

dma_trigger.config(sm[0], True)

##### MAIN PROGRAM #####

uart.write(b"\r\n")
uart.write(b"setTimingSystem, auto\r\n")
sleep(0.2)
uart.write(b"setPPSParameters, sec1, Low2High, 0.00, UTC, 60, 5.000\r\n")
sleep(0.2)
uart.write(b"setNMEAOutput, Stream1, COM1, GGA, sec1\r\n")
#uart.write(b"setNMEAOutput, Stream1, COM1, ZDA, sec1\r\n")

'''
# quick test to ensure GPS is responding
while True:
    u = uart.readline()
    if u:
        print(u)
'''

print("Starting test, waiting for trigger...")

# combine reports into a single line
# monitor the 'toggle' pin to align Timestamp with GPS message
# as undetermined which gets processed first.
seen = False
timestamp = [[None,None], [None,None]]

while True:
    if TIMESTAMP64:
        while sm[1].rx_fifo() > 3:
            # read out 4x 32bits, first and last are relevant.
            a = []
            while sm[1].rx_fifo():
                a.append(sm[1].get())
                #print("0x%8.8x" % a[-1])
            
            '''
            print("Timestamp: 0x%16.16x %d" % ((a[3]<<32)|a[0], toggle.value()))
            '''
            seen = False
            if toggle.value():
                timestamp[1][0] = (a[3]<<32)|a[0]
                timestamp[0][0] = None
                timestamp[0][1] = None
            else:
                timestamp[0][0] = (a[3]<<32)|a[0]
                timestamp[1][0] = None
                timestamp[1][1] = None
    else:
        if sm[1].rx_fifo():
            a = sm[1].get()

            '''
            print("Timestamp: 0x%8.8x %d" % (a, toggle.value()))
            '''
            seen = False
            if toggle.value():
                timestamp[1][0] = a
                timestamp[0][0] = None
                timestamp[0][1] = None
            else:
                timestamp[0][0] = a
                timestamp[1][0] = None
                timestamp[1][1] = None

    u = uart.readline()
    if u:
        v = u.split(b",")
        if (v[0][0:3] == b"$GP" or v[0][0:3] == b"$GN") and \
                    (v[0][3:] == b"GGA" or v[0][3:] == b"ZDA") :
            '''
            print(v[1], toggle.value())
            '''
            seen = False
            if toggle.value():
                timestamp[1][1] = v[1]
                timestamp[0][0] = None
                timestamp[0][1] = None
            else:
                timestamp[0][1] = v[1]
                timestamp[1][0] = None
                timestamp[1][1] = None

    # Combine reports into a single line
    t = toggle.value()
    if not seen and timestamp[t][0] and timestamp[t][1]:
        if TIMESTAMP64:
            print("Timestamp for %s is 0x%16.16x (%d us, %f v, %f 'c)" % \
                    (timestamp[t][1], timestamp[t][0], timestamp[t][0],
                    volts.read(), temps.store_read(temp.read())))
        else:
            print("Timestamp for %s is 0x%8.8x (%d us, %f v, %f 'c)" % \
                    (timestamp[t][1], timestamp[t][0], timestamp[t][0],
                    volts.read(), temps.store_read(temp.read())))
        seen = True
        timestamp[t][0] = None
