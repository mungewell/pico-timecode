
# Proof of Concept

Why am doing this? Primarily because it's a fun challenge. I've been interested in Timecode for a while
and the PIO blocks on the Pico make it very possible...

The `ltc_freerun.py` script is a proof-of-concept, and outputs a counting LTC stream. Connecting this to
PC audios input (via a resistor divider to reduce level), confirms that the LTC content in the audio 
can be decoded.

In the following screen shot the top trace is the 'raw' bitstream, and the lower is the encoded LTC stream.

![First Decode](first_decode.PNG)

# Build Your Own

My intent is that the project could be used to build your own devices. The proof-of-concept script(s) can 
just be dropped onto a 'bare-bones' Pico.

If you want a more fleshed out solution, you could look at ['PiShop'](https://www.pishop.ca) (my go-to
supplier up here in Canada-land).

For example:
[Pico](https://www.pishop.ca/product/raspberry-pi-pico-h/),
[Display](https://www.pishop.ca/product/1-3inch-oled-display-module-for-raspberry-pi-pico-64-128-spi-i2c/),
[Charger](https://www.pishop.ca/product/lipo-shim-for-pico/),
[Battery](https://www.pishop.ca/product/lithium-ion-polymer-battery-3-7v-900mah/)

There needs to be some electronics to 'buffer' the audio signal in-to/out-from the Pico. My intent is to
create a small PCB to do this. The above display is cheap and has both a SPI and I2C interface, it's
connections are as follows:

![OLED PinOut](pico-1.3-oled.png)

Which leaves the 'South' end of the Pico usable for LTC connections. My code uses separate PIO blocks and
each has it's own input/output pins. Once designed my LTC interface card will need to buffer audio and 
connect into the Pico.


If you do use my code for a personal project, drop me an email/picture.
If you make a device to sell, please send me an sample to test.

# LTC Information

LTC is an audio signal, which contains information about the progression of time and some other
infomation. This signal helps synchronise multiple recordings, for example a multi-camera shoot could
record LTC on each camera and match together with a LTC audio track recorder on sound equipment.

Timecode can also be embedded in a video signal (VITC or HDMI Timecode), or as meta-data in audio 
(BWAV) files.

Technically Timecode can be run/scrubbed both backwards and forwards, but this project is only interested
in replicating an accurate clock, real-time in the forward direction.

To find out more about the structure of the LTC packet:
[Wikipedia](https://en.wikipedia.org/wiki/Linear_timecode)

## Note on accuracy/precision

The **whole** purpose of the time-code system it to be time precise, this is not normally something that
you'd expect from a Python script - let alone one running on a micro-controller.

The Pi Pico is different as it has a number of small (cycle precise) PIO engines. This code implements 
the LTC processing with multiple PIO engines. These each handle small chunks of the process and are 
synchronised with interupts between the PIO blocks.

The MicroPython script *only* needs to (pre-)compute the data for the LTC, and place it in a FIFO ahead of
when it is actually required. It will also run the 'UI', sending data to the screen and sensing buttons.

All the PIOs are set to clock at the same speed (16x LTC bit clock), whilst there may be some jitter 
in the clocks (due to fractional dividing) this should not be a problem.

The Pico is normally clocked from a 'cheap' 12.0MHz crystal. Whilst this may not be the 'worlds best' 
crystal, it can also be replaced with a better one if need be.

See: [https://github.com/dorsic/PicoPET](https://github.com/dorsic/PicoPET)


## So how good is it?

*Time will tell...*

Given my interest (nee obsession) with TimeCode, I have already aquired some specialised test equipment. I
will measure the accuracy of the Pico modules and post results soon.

My approach will be to get the code to a point where it will 'Jam' to incoming LTC and then 'free-run' it's
output LTC. Using my test equipment I can monitor the LTC value from my source, as well as from the 
'Pico-Timecode' device.

![Test Equipment](test_equipment.png)

Evertz #2 will also tell me the phase difference between the VITC (embedded in Video) and the LTC.

![Test Equipment](test_equipment2.png)
