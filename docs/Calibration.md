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
the RX monitor [display](https://github.com/mungewell/pico-timecode/blob/main/docs/Display.md).

## Acceptable drift/accuracy

I've heard it said that the industry accepted drift would be in the '1 frame in 8hrs' region. But what does that mean?

I _think_ that it means that the output Timecode should be closest to the _True_ time for at least 8hrs, so a drift of
not more than 1/2 the frame period - ie a 'phase' of less that +/-0.5.

So what can we achieve? We know that the stock XTAL on the Pico is, lets say, cost optimized... in the following plot 
this particular board (selected at random, but likely others will be different), only 'holds' +/-0.5 for 1hour 15mins. 
By the end of the 8hrs it is inaccurate by almost 4 frames.

If we were to calibrate at the temperature we are woking at it would be __much__ better, but slight variations in 
temp can send the _phase wild_.

The other two plots are boards with a modified TCXO (Temperature Compensated XTAL Osscillator), and they perform 
__much__ better - even the uncalibrated on is still within the requirement!

![XTAL comparison](https://github.com/mungewell/pico-timecode/blob/main/docs/pics/XTAL_comparison.png)

For reference: 8hrs is '8 * 3600 * 30fps = 864000 frames', so for accuracy we'd want '864000.5 / 864000 = 1.000000579' 
(around +/-0.6 ppm).

We could also question how accurate the reference (UltraSync One) is, as if that drifts as well then it would affect the
measurement for the Pico's. Another note is that being open-source, our project provides the tools to make these 
comparisons, the commercial offerings are not so transparent.

## Calibration to perfect

Since we can measure, can we compensate? Sure...

As mentioned above the PIO clock divider is fine grained, with a fractional part. This, however, is not detailed 
enough and we need to further tweak the clock frequency. We do this by modulating the divider, dithering between two 
divider values (Div-A and Div-B) so that the 'apparent divider' is more precise (when veiwed over a longer period of time).

So we have a calibration in two parts; one part which is the _offset_ from nominal (in fractional ticks of the PIO divider 
value) and the other is a _duty_ factor for how much time is spent at Div-A vs Div-B. The code combines this into a 
single number, with integer for the _offset_ and fractional for the _duty_.

This is implemented with two timers, Timer1 controls the duty cycle and Timer2 controls the period. At the start of the
cycle the divider is set to the integer _offset_, and when Timer1 triggers the divider is increased or decreased.

Once Timer2 completes, it reset the divider and reconfigures both timers (potentially with differ values, as used 
during the calibration process).

![Timer diagram showing Duty cycle](https://github.com/mungewell/pico-timecode/blob/main/docs/pics/dither_divider.png)

As the system has to operate with difference frame rates, the simplest solution is to have a calibration values for each. 
These are stored within the `config.py` script, and automatically loaded when the frame rate is selected.

## Real world implementation

All this is fine in theory, but how is it done in the real world?

The user selects the 'Calibration' option to be 'Once' or 'Always', and then initiates a 'Jam' to the external device. Once the Pico-Timecode has
jam'ed it will enter the calibration process, which takes approximately 10 minutes. During this time it monitors the _phase_ of the external device
and actively adjusts the calibration to hold this _phase_ at zero.

It does this in two stages, firstly with a dithering period of 1s and then with the user specified period (nominally 10s). After this it takes an
average of the recently measured calibration values, and writes it to the `config.py` value.

This value will be used in future to compensate for an inaccurate XTAL, and to produce a 'perfect' LTC stream... _said in jest_.

"The world is an imperfect place, screws fall out all the time"... measurement of the phase is somewhat noisy, and if the calibration process is 
run multiple times (with the test scripts) we get slight different values. Evaluating this _spread_ can tell us a bit about the process and
let us assess multiple values to hand configure an even better calibration value.

![Plot of successive calibration cycles](https://github.com/mungewell/pico-timecode/blob/main/docs/pics/cal_ttyACM0_anot.png)

With testing the stock Pico boards, at a single temperature (more on that later), the _spread_ of the calibration values is somewhere in the
0.05 to 0.08 range. With some math, we can see that this is actually __very__ precise and gives us sub-1PPM adjustment of the XTAL.

![Spreadsheet assessing the spread in compensation values](https://github.com/mungewell/pico-timecode/blob/main/docs/pics/calibration_accuracy.png)

For these two units 'P-05' is a stock XTAL, and 'T-02' is modified with a TCXO module. For a static temp, on the bench test cycle
both units calibrated with similar accuracy. The 'spread' of the (multiple/concequtive) calibrations is quite silimar ~0.08 units.
which I believe is down to the noise in how the system measures the phase of the RX LTC stream.

This spread (of ~0.08) roughly equates to a variance +/-96ppb (+/-0.096ppm), which would be excellent.

However the stock XTAL shows it's weakness when the temperature changes, in a test where the temperature increased by ~10'C
during the calibrations cycles, the 'spread' for the stock XTAL increase to over 0.8! It is clear from the following plot
that the calibration is very much affected by changing temperature.

![Changing temp with stock XTAL](https://github.com/mungewell/pico-timecode/blob/main/docs/pics/temp_vs_cal_ttyACM0.png)

Even a few degrees change can push the calibration value significantly - meaning that if we had previous calibrated at a 
different temp, then the resultant LTC stream will be fast/slow and eventually the reported time/frame will be inaccurate.

