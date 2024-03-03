
# Build Your Own

My intent is that the project could be used to build your own device(s). The proof-of-concept script(s) can 
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

![OLED PinOut](pics/pico-1.3-oled.png)

Which leaves the 'South' end of the Pico usable for LTC connections. My code uses separate PIO blocks and
each has it's own input/output pins. Once designed my LTC interface card will need to buffer audio and 
connect into the Pico.

# Digi-Slate

One obvious variant would be to build a Digi-Slate, which should actually be pretty easy... There are
spare pins on the Pico (pins 1 tru 7) which can be used for I2C and GPIO. I'll make a pledge to add
appropriate connector to the next PCB.

![Digi-Slate](pics/digi-slate.png)

Adafruit makes a large (1.2inch) 7-segment display and back pack:
[https://www.adafruit.com/product/1264](https://www.adafruit.com/product/1264)

And there's this library which is capable of driving it:
[https://github.com/smittytone/HT16K33-Python](https://github.com/smittytone/HT16K33-Python)


# Other Displays

There are some other Pico like boards, with displays already attached. It really should be trivial
to adapt the code for these, just remember that in-order to achieve high FPS the code should be 
structured to send a few bytes to the display as possible.

If you do use my code for a personal project, drop me an email/picture.
If you make a device to sell, please send me an sample to test.

