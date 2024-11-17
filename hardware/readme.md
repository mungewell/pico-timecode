
Work is starting on PCB Rev2, for a while these files may be incomplete. You can access the
Rev1 PCB here:

https://github.com/mungewell/pico-timecode/releases/tag/PCB_Rev1


Ideas for future:

- 2x button on the bottom side, for replicating A & B when used with RP2040-LCD-0.96

- pull down 0R on U2/5 nCS, to allow 3rd party to wire line if SD0 is also used for other devices.

- add battery holder connections/through holes

- connections for Digi-Slate (extra) 8x 7-seg LED.

    This lib looks very interesting:
    https://github.com/smittytone/HT16K33-Python

    display count when switch is open, then hold for 2s once closed, then turn off
    possibly display UB, though could only do Digits/BCD.

    would need I2C (GP0, 1) and GPIO (GP2), plus GND/3.3v/VSYS
