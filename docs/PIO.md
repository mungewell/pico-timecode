
# How it works - PIO in detail

All of the LTC decoding is done in the PIO blocks, each has it's own task. Communincation
between the PIO is via their in/out pins, and with interrupts. 

The microPython script needs to monitor the FIFOs, to keep them feed or emptied.

```
    # Start-up/Trigger
    sm.append(rp2.StateMachine(0, start_from_pin, freq=sm_freq,
                               jmp_pin=machine.Pin(21)))        # Sync from RX LTC

    # TX State Machines
    sm.append(rp2.StateMachine(1, blink_led, freq=sm_freq,
                               set_base=machine.Pin(25)))       # LED on Pico board + GPIO26
    sm.append(rp2.StateMachine(2, buffer_out, freq=sm_freq,
                               out_base=machine.Pin(20)))       # Output of 'raw' bitstream
    sm.append(rp2.StateMachine(3, encode_dmc, freq=sm_freq,
                               jmp_pin=machine.Pin(20),
                               in_base=machine.Pin(13),         # same as pin as out
                               out_base=machine.Pin(13)))       # Encoded LTC Output

    # RX State Machines
    sm.append(rp2.StateMachine(4, decode_dmc, freq=sm_freq,
                               jmp_pin=machine.Pin(18),         # LTC Input ...
                               in_base=machine.Pin(18),         # ... from 'other' device
                               set_base=machine.Pin(19)))       # Decoded LTC Input
    sm.append(rp2.StateMachine(5, sync_and_read, freq=sm_freq,
                               jmp_pin=machine.Pin(19),
                               in_base=machine.Pin(19),
                               out_base=machine.Pin(21),
                               set_base=machine.Pin(21)))       # 'sync' from RX bitstream
```

## start_from_pin

Triggers start up, either automatically or from a pin. This sends IRQ to all of the TX
machines so that they start in unison. All of the PIO run at the same clock rate, which
(at preset) is 16x the LTC bit clock.

Although they run at the same clock speed, the RX machines are not nessecarily synchronised 
with the TX machines.

## blink_led

The FIFO for this PIO is used to determine whether or not to flash the LED. The low 16bits
is a count representing the whole LTC frame, and the upper 16bits is a count of how long
(if at all) the LED blinks for.

This PIO code loops precisely every frame. The very first cycle is slightly longer to align 
the blink with start of the following frames - as for the very first frame we send Sync word 
before data.


## buffer_out

The FIFO for this PIO contains the bit data for the LTC frame, it is precomputed by the Python
code and pushed into the FIFO as alternatively two and three 32bit words.

The PIO code just plays out this 'raw' bit stream, with the rate determined by the division of
the CPU clock.


## encode_dmc

Takes the 'raw' bit stream and 'modulates' it into LTC stream (Differential Machester Encoding).

## decode_dmc

Receives the LTC stream (from the 'other device') and 'demodulates' it into a raw stream.
Uses a IRQ to signal the start of each bit, helping the reader keep sync.

## sync_and_read 

Takes the 'raw' bit stream, and processes in 2 halves... firstly uses a shift like arrangement
to clock the data into the ISR and then compares value with the Sync word. 

Value of Sync word is pre-loaded to Y via the FIFO. As the PIO does not have math functions, it 
**double-clocks** into the ISR, and can then use the `jmp(X != Y)` function to evaluate Sync word. 

When a Sync is found, it then clocks data portion into the ISR and pushes into the RX FIFO, as
two 32bit words. *It does not send Sync word to FIFO.*

It also sends a sync pulse on it's output pin, this is used to trigger the TX machine(s) when 
we are Jamming to received LTC.


