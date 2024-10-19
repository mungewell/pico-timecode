# Calibration

## Clock sources

Effectively this project is _just_ a counter, it counts passing time in frames, seconds, minutes and hours. It takes this, addd in some meta data and encodes
it into a LTC stream that can be recorded onto tape, or decoded by other devices.

What make it special is the fact that this counter needs to keep very accurate time, this (of course) comes down to the accuracy of the XTAL. To put it bluntly
the stock XTAL is... let's just say 'budget optimized'. It has two big problems, first is the accuracy of the 12MHz output and then second is the stability over
time/temperaure.

The frequency of the XTAL is multiplied up to a CPU clock of 120MHz, and then this is divided down (according to the specified frame rate) so that the PIO
blocks are clocked 32 times for each bit in the LTC stream (80bits per frame).

![Computation of the PIO clock frequencies](https://github.com/mungewell/pico-timecode/blob/main/docs/pics/clockdiv_values.png)

The PIO clocks are generated with a divider ratio, this is made from an integer (whole number) and fractional part, as you can see from above calcs picking 
the right CPU clock allows precise selection of the dividers to get 'perfect' PIO clocks in most cases. If the XTAL was perfectly 12MHz we'd be _golden_, 
however life isn't that helpful...

## Monitoring a reference device

In order to measure/understand whether our XTAL is good or bad we need a way to measure it, and we do this against a reference device (which could also 
have accuracy issues). We have a system for reading the contents of the RX LTC stream, but the resolution of this data is that helpful. But we also 
have the ability to measure the timing of the structure of the frame, the RX decode causes an interrupt when the start of the LTC frame is detected.

We take the timing of the TX frame and compare it to the timing of the RX frame, and call this _phase_. If the frames perfectly align the _phase_ would 
be 0, and as the RX frames are early (upto 1/2 frame) the phase changes to -0.5 and if late to +0.5. This is used to display the bargraph shown on
the RX monitor [display](https://github.com/mungewell/pico-timecode/blob/main/docs/DISPLAY.md).

## Calibration to perfect

Since we can measure, can we compensate? Sure...

As mentioned above the PIO clock divider is fine grained, with a fractional part. This, however, is not detailed enough and we need to further tweak
the clock frequency. We do this by modulating the divider, dithering between two divider values (Div-A and Div-B) so that the 'apparent divider' is 
more precise (when veiwed over a longer period of time).

So we have a calibration in two parts; one part which is the _offset_ from nominal (in fractional ticks of the PIO divider value) and the other is a
_duty_ factor for how much time is spent at Div-A vs Div-B. The code combines this into a single number, with integer for the _offset_ and fractional
for the _duty_.

As the system has to operate with difference frame rates, the simplest solution is to have a calibration value for each. These are stored within
the `config.py` script, and automatically loaded when the frame rate is selected.
