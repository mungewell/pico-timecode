
Why am doing this? Primarily because it's a fun challenge. I've been interested in Timecode for a while
and the PIO blocks on the Pico make it very possible...

# DIY Timecode made real (cheap).

The Rev-1 PCBs have been assembled and tested, although the project can run on a un-modified Pi Pico -
optionally with a [display](docs/Display.md).

The primary concern would be the accuracy/stability of the XTAL, testing so far shows that the stock
XTAL on the Pico is not temperature stable enough. A few degrees of temperate change is enough to throw
the [calibration](https://github.com/mungewell/pico-timecode/blob/main/docs/Calibration.md) off, which
will result in the timecode eventually drifting off.

![Rev-1 Board Assembled](docs/pics/first_board.jpg)

I have designed the first revision of hardware to be flexible, with the intent of being used
in multiple ways and with different 'Pico Boards'. The [schematic](hardware/output/schematic.pdf)
will show you how simple it is.

There is a fair bit of testing, and we'll need to select the optimum components. But did I say
that it WORKS!!! :-)

![Render of Rev1](hardware/output/rev1-render.png)

The code now contains a 'calibrate' mode, where the incoming RX LTC is monitored and the XTAL
frequencies are adjusted to match. The stock XTAL can be used in the short term for testing/etc,
but a replacement TCXO is/would be better.

I am qualifying a number of replacement candiates.

[Demo Video - Rev 1](https://www.youtube.com/watch?v=2LLGX8mJC4A)

The `main.py` scripts has a menu which can be used to control the device, and to navigate the settings. 
The incoming LTC is now validated before Jam is performed, and the RX monitor has indicator bar to 
show the relative timing between RX and TX.

This 'code' is seven files; upload all seven if you have the same hardware.

`PicoOled13.py` is library of screen functions, `umenu.py` is menuing library, `neotimer.py` is timer
library, `pid.py` is a PID controller and `config.py` holds the settings for the unit.

`pico_timecode.py` and `main.py` combine to make the GUI app.

The first 5 are from other projects, which I use permissively under their own licenses:

- https://github.com/samveen/pico-oled-1.3-driver (*)
- https://github.com/plugowski/umenu
- https://github.com/jrullan/micropython_neotimer
- https://github.com/m-lundberg/simple-pid
- https://github.com/aleppax/upyftsconf

(*) actually using my port, as some changes are not yet accepted upstream

I created a sub-directory for the 'libs' to clarify that they are not really part of this project.

![Save to Pico](docs/pics/save_to_pico.PNG)

The `pico_timecode.py` script is also self contained for use without a display, ie can be used on 
its own on a 'bare' Pico board.

# Build Your Own

My intent is that the project could be used to build your own devices. The proof-of-concept script(s) can 
just be dropped onto a 'bare-bones' Pico.

There's some [DIY suggestions](docs/DIY.md).

If you do use my code for a personal project, drop me an email/picture.
If you make a device to sell, please send me a sample to test (and promote).

# How it works

It's fair to say that this task should be far above a $1 MCU (chip).

All of the LTC decoding is done in the PIO blocks, each has it's own task. Communincation
between the PIO is via their in/out pins, and with interrupts. 

The `pico_timecode.py` script just needs to monitor the FIFOs, to keep them feed or emptied.

The `main.py` forms the user interface/application, and controls the OLED screen

There's an indepth description on the [workings](docs/how_it_works.md).

## So how good is it?

*Time will still tell...*

Given my interest (nee obsession) with TimeCode, I have already aquired some specialised test equipment.
I have also purchased an UltraSync One to use as a reference, and see how well Pico-Timecode can
interoperate.

My approach will be to get the code to a point where it will 'Jam' to incoming LTC and then 'free-run' it's
output LTC. The code itself has the ability to monitor the RX LTC, however the display is not fast enough
to display every frame (this does NOT affect the output though, as that's running with the PIO block with
TC frames fed from different core/thread).

For more details see [testing](docs/testing.md).

