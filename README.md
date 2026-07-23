
# What is 'Pico-Timecode'?

'Pico-Timecode' is an Open-Source solution for LTC Timecode, using the RP2040's PIO blocks to count time divisions and render the LTC waveform. It works with all common frame rates, with/with-out drop frame operation. It also has the ability to read LTC from an external device, and sync to it.

_LTC Timecode is used in the TV/Movie industry as a way to synchronize video and audio recordings, which offers improvements in workflow and (most importantly) decreases overall editing time._

This technology has been implemented in 2 custom PCBs; firstly the (now renamed) 'pt-papa' board based around the Raspberry Pico board, with Waveshare OLED screen module and input/output connectors.

!['pt-papa' board](https://github.com/mungewell/pico-timecode/blob/main/docs/pics/first_board.jpg)

Secondly with the 'pt-thrifty' board based around the Waveshare RP2040-Zero. 'pt-thrifty' aims to be the **lowest cost timecode device** (less than $25 per device) to make LTC accessible to DIY/budget film-makers, and thus it uses a minimum of components. It has a UI with single button and RGB LED, the control of the device is achieved by navigating through a [map](https://github.com/mungewell/pico-timecode/blob/main/docs/pics/PT_Thrifty_UI.png) of states. 

!['pt-thrifty' board](https://github.com/mungewell/pico-timecode/blob/main/docs/pics/PT_Thrifty_PCB.JPG)

There are some demonstrations of 'pt-thrifty' here:
https://www.youtube.com/playlist?list=PL1t1GwpUNc-VbEAXxscaxrPQlrt16c4yX

# DIY yours now...

As the project is Open-Source, it's intended to be customized for other implementations.

The code as it is can be run on any RP2040 board, though may need customization for particular use cases. The easiest/fastest route to try it out is to use the PT_Thrifty '.UF2' on the releases page, these can be run on a 'naked' PICO and will even sync LTC between multi Picos - albeit it at TTL level, you will need at least a mininal amount of interface circuitry to record the signal on your camera or audio recorder.

I recomend replacing the XTAL on the Pico with a TCXO, but once 'calibrated' even the XTAL will hold time OK for a few hours.

# Why?

Why am doing this? Primarily because it's a fun challenge. I've been interested in Timecode for a while and the PIO blocks on the Pico are very powerfull. I am debating whether to offer pre-built hardware for purchase, at very reasonable costs.

You can follow the project on Instagram:

https://www.instagram.com/picotimecode/

