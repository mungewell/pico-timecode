# Testing Pico-Timecode with stock Pico's

The scripts `main.py` and `pico_timecode.py` can be run on stock/non-modified Pico's, this might
be helpful to those wanting to play a little before building the audio interface circuits.

Each Pico can both send and receive LTC, however in this test we'll use one as a 'sender' and
one as a 'receiver (and monitor the difference in the time codes)'.

The LTC output/input is regular 3.3V logic so connect the following pins, and a common ground
between the boards. 

From 'sender':
```
# Pin 17 = GP13 - TX: LTC_OUTPUT (physical connection)
```
To 'receiver':
```
# Pin 24 = GP18 - RX: LTC_INPUT  (physical connection)
```

Install the scripts (and library directory) on each Pico, then adjust the `libs/config.py`
file on the 'receiver' to enable the monitor function.
```
setting = {
    'framerate' : ['30', ['30', '29.97', '25', '24.98', '24', '23.98']],
    'dropframe' : ['No', ['No', 'Yes']],
    'tc_start'  : "01000000",
    'output'    : ['Line', ['Mic', 'Line']],
    'flashframe': ['11', ['Off', '0', '11']],
    'userbits'  : ['Name', ['Name', 'Digits', 'Date']],
    'powersave' : ['No', ['No', 'Yes']],
    'zoom'      : ['No', ['No', 'Yes']],
    'monitor'   : ['Yes', ['No', 'Yes']],
    'calibrate' : ['No', ['No', 'Once', 'Always']],
    'ub_name'   : "PI-C",
    'ub_digits' : "00000000",
    'ub_date'   : "Y74-M01-D01+0000",
```

The LED on the Pico's will flash once per second, to represent the passing time. By default I 
set this for frame 11 to match the UltraSync One's operation, but it can be changed in the 
`libs/config.py` file.

## Out of Sync

When you turn on both Pico's, after a short delay you will see the LED flashing... but they 
will likely not be synchonized!

We need to instruct the 'receiver' to 'Jam' to the incoming LTC (albeit 3.3V logic signal).

When the Pico boots it checks (what would be) Button-B, if this is held low it will enter
the 'Jam' process. During 'Jam' the LED is continuously on, and it will then start flashing
in sync with the 'sender' board.
```
# Pin 22 = GP17 - User key 'B'
```

## UART data

In order to aid testing, during 'monitor mode' the Pico will output information on it's
LTC input and how it compares to it's own LTC output. _This information is for debug, it
is not delivered in a timely manner._

A serial terminal (or Thonny) will give something like:
```
Pico-Timecode
www.github.com/mungewell/pico-timecode
01:00:12:01 -0.0002933331 0.02522542 0 26.58253
01:00:13:02 6.666687e-05 0.011475 0 27.28474
01:00:14:03 -5.333312e-05 0.007575238 0 27.51881
01:00:15:00 0.0004266668 0.005864775 0 27.63585
01:00:16:00 3.666617e-05 0.004790071 0 27.70607
01:00:17:01 0.0009366665 0.004065087 0 27.98696
01:00:18:01 0.0004566666 0.003559204 0 27.89333
```

Note: During 'calibration' (when matching XTALs freq) there is more information such as
PID state.


[Demo Video](https://youtu.be/miWlGS6fJNI)
[Demo2 Video](https://www.youtube.com/watch?v=WEdSII-7nx4)


## So how good is it?

*Time will still tell...*

Given my interest (nee obsession) with TimeCode, I have already aquired some specialised test equipment. I
will measure the accuracy of the Pico modules and post results soon.

My approach to validating the Pico-Timecode code is to 'Jam' to incoming LTC and then 'free-run' the
output LTC. Using my test equipment I can monitor the LTC value from my source, a black-burst 
video generator feeding into a Sync-IO (which generates the reference Timecode), as well as from 
the 'Pico-Timecode' device.

_The Ultra-Sync One can also generate a black burst, reference video._

![Test Equipment](https://github.com/mungewell/pico-timecode/blob/main/docs/pics/test_equipment.png)

Evertz #2 (top in my rack) will also tell me the phase difference between the VITC (embedded in Video) and 
the LTC received back from the Pico (or other) Timecode device. The Evertz also output the TC and phase 
information to a video signal, daisy chaining this you can get an image of both the reference and Pico's
timecode - captured in an instant...

![Test Rack](https://github.com/mungewell/pico-timecode/blob/main/docs/pics/test_rack.jpg)

I have already confirmed that the stock XTAL on the Pico is not temperature stable (at all), so this 
needs to be replaced with a TCXO. I am in the process of validating both the calibration process and
the 'TCXO' susceptability to temprature variations - using a modified toaster oven. :-)
