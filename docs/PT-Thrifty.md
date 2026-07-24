# The PT-Thrifty is the 'lowest cost Timecode'.

PT-Thrifty has a single 3.5mm jack, from power on this outputs LTC
timecode. At different times of operation the 3.5mm jack becomes an
input, so that the PT-Thrifty can be 'Jam-Synced' from another LTC 
device.

User interface is a single button and a RGB LED, with some actions
requiring connecting/dis-connecting the 3.5mm jack.

There is a 2nd LED next to the 3.5mm jack. This provides a 1PPS flash
from the internal LTC timers at the start of Frame-0. As this is driven
directly its timing is exact, but some commercial units (ie: UltraSync)
choose to blink on Frame-11 for some reason... this can be configured through the `libs/config.py' file.

PT-Thifty is designed to be _LOW COST_, so it does not include battery
or other features that are technically possible (although these can 
be added to your DIY-ed version). The unit needs to be (and remain) externally powered for the duration 
of the shoot. If power is lost the unit will need to be Jam-Synced again.

Note: the 'Info' RGB will double-flash if the unit has previously been Jam-Synced.

[Video Demo](https://youtube.com/shorts/oo_elmEAXs4?feature=share)

# Map of the UI

In pictorial form the UI looks like:

![Map](pics/PT_Thrifty_UI.png)


## Run, and Info

When initially powered up, the PT-Thrifty will start outputing LTC
timecode - this is NOT schronized with anything, just a free running
clock/counter.

Upon power up the RGB LED will flash a colour to inform the user what 
FPS and DropFrame configuration is active:

- Red 	= 30.00fps, non-drop
- Purple	= 30.00fps, drop-frame
- Yellow	= 29.97fps, non-drop
- Orange	= 29.97fps, drop-frame
- Green	= 25.00fps, non-drop
- Blue	= 24.00fps, non-drop
- Cyan	= 23.98fps, non-drop

Note: Some Pico boards use 'RGB' and some use 'GRB', this is configurable and should be changed if the sequence of FPS colours is not as above.

At any time during 'Run' the user can press the button to be reminded
of the current configuration ('Info').

To 'Jam' to another unit, disconnect the 3.5mm jack and press-and-hold 
the button until the LED becomes a steady colour. Then connect the 3.5mm
jack from the 'Master LTC', the LED will flash the colour and then become
solid white when the 'Jam' is complete.

Note: once solid white is displayed the internal time is set, but the LTC 
is not output until the 3.5mm jack is disconnected, and then the connection 
to camera/recorder is remade. Then PT-Thrift will then output LTC.


## Output Level

PT-Thrifty has two output levels: by default 'Mic' (~80mV pk-pk) and, optionally, 'Line' (1V pk-pk).

In order to change the output level the unit must be power-cycled. The
output level is toggled 'Mic' <-> 'Line' when the button is held during 
booting, and the current level is indicated during 'Info'.

The 'Info' flash is longer (0.3s vs 0.1s) when the output level is 'Line'. *The 2nd 'Jam-Synced' flash will remain at 0.1s duration, as a comparison aid.*


## Changing FPS and Drop-Frame configuration

As part of the 'Jam' process the FPS and Drop-Frame configuration can
be changed. From 'Run', hold the button until LED is solid colour and then
each press of the button will cycle through the available configurations.

When the desired configuration is reached, connect the 3.5mm jack to 
start jamming with the new configuration. This new configuration will be 
remembered for the next time PT-Thrifty powers on.

Note: During this 'Pre-Jam' selection, the internal LTC time is still
correct. If a user enters this state and changes their mind they can 
long-press the button to return to the 'Run' state, without loosing LTC synchronization. 

Note: If the unit enters the 'Jam' mode (flashing Colour), then the 
internal **LTC time is lost** and the unit will NOT be Synchronised until
it is Jam-Synced with an external unit.


## Calibration

Typicaly the TCXO module inside PT-Thrifty is accurate to under +/-0.5
frames of drift over a 12 hour period.

However if the user has DIY-ed a board, they may be using the stock/passive
XTAL that the RP2040 boards normally have. This will be inaccurate, but
can be 'Calibrated' to match a more accurate source such as commercial
LTC device.

A calibrated DIY/XTAL unit is only accurate for a few hours, and is likely 
temperature dependant. If the user goes between a warm and cold ambient 
temperature, the LTC will drift away faster from the correct value.

Once the 'Jam' has completed, shown by solid white LED, a button press
will enter 'Follow' mode. During 'Follow' the LTC produced by PT-Thrifty
will be adjusted to match incoming LTC.

Disconnect the 3.5mm jack to return to 'Run' mode.

Note: during 'Follow' the rate of the incoming LTC is tracked, PT-Thrifty does not update if there is a "jump change" in the incoming LTC.

Note: as the LTC is not output at this time, this is just informative
and the relationship of RX vs. TX LTC will be relayed to the serial port.

A long-press will enter 'Calibrate' mode, which like 'Follow', the internal 
LTC rate will be adjusted to match incoming LTC rate. This is indicated with 
alternate White/Colour flashing.

After ~10mins of processing, the new calibration value will be stored and 
the unit will return to 'Follow' mode. Disconnect the 3.5mm jack to 
return to 'Run' mode.

Note: To clear a calibration the user needs to enter 'Calibration' mode, 
but then disconnect the 3.5mm jack BEFORE it completes the calibration 
process.

