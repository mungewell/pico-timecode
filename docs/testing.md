
Why am doing this? Primarily because it's a fun challenge. I've been interested in Timecode for a while
and the PIO blocks on the Pico make it very possible...

# Now we're JAM'ing in the real world.

We've moved past the Proof-of-concept stage! Well past....

![Prototype Hardware](docs/pics/prototype_hardware.jpg)

Mk-1 of the audio inteface is built, and I was able to Jam with the LTC from my Sync-IO, and feed
the regenerated LTC to an Evertz 5300 LTC Analyzer. After Jam the LTC is spot on, but (as expected)
'drifted off' as time went by.

The project stalled in the summer, there was a scheduler bug in MicroPython which was causing
occassional lock-ups, and I couldn't figure it out.... anyhow they're smarter than me and the 
`RPI_PICO-20240105-v1.22.1.uf2` release works fine.

I've added some compensation for XTAL frequencies, the device can 'Sync after Jam' to learn the
correction factor required to match RX/incoming LTC. It then remembers this as part of it's config.

Not yet looked at using a more precise/temp compensated XTAL...

[Demo Video](https://youtu.be/miWlGS6fJNI)
[Demo2 Video](https://www.youtube.com/watch?v=WEdSII-7nx4)


The script(s) now has a menu which can be used to control the device, and to navigate the settings. 
The incoming LTC is now validated before Jam is performed, and the RX monitor has indicator bar to 
show the relative timing between RX and TX.

This code is in five files; upload all seven if you have the same hardware.

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

In the following screen shot the top trace is the 'raw' bitstream, and the lower is the encoded 
LTC stream. We will need some interfacing hardware before the TTL level can be fed nicely into other 
hardware. 

# Build Your Own

My intent is that the project could be used to build your own devices. The proof-of-concept script(s) can 
just be dropped onto a 'bare-bones' Pico.

There's some DIY suggestions [here](docs/DIY.md)

If you do use my code for a personal project, drop me an email/picture.
If you make a device to sell, please send me an sample to test.

# How it works

It's fair to say that this task should be far above a $3 MCU.

All of the LTC decoding is done in the PIO blocks, each has it's own task. Communincation
between the PIO is via their in/out pins, and with interrupts. 

The `pico_timecode.py` script just needs to monitor the FIFOs, to keep them feed or emptied.

The `main.py` forms the user interface/application, and controls the OLED screen

There's an indepth description on the workings [here](docs/PIO.md)

## So how good is it?

*Time will still tell...*

Given my interest (nee obsession) with TimeCode, I have already aquired some specialised test equipment. I
will measure the accuracy of the Pico modules and post results soon.

On the above 'first jam' video the two units started well in sync, but after ~20hrs it was clear that the
LEDs had drifted appart - by around 4 frames. This is still pretty good for a 'crappy' crystal. I will 
need to do some investigations as to whether this is coding error, or attributed to some other (fixable) 
issue. Otherwise we'll have to look at compensating somehow, or replacing the crystal with a better one. 

My approach will be to get the code to a point where it will 'Jam' to incoming LTC and then 'free-run' it's
output LTC. Using my test equipment I can monitor the LTC value from my source, as well as from the 
'Pico-Timecode' device.

For more details see [testing](docs/testing.md)

![Test Equipment](docs/pics/test_equipment.png)

Evertz #2 will also tell me the phase difference between the VITC (embedded in Video) and the LTC.

![Test Equipment](docs/pics/test_equipment2.png)
